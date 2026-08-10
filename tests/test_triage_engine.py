import json
import os
import re
from datetime import datetime
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from sluice.triage.config import TriageConfig
from sluice.core.dossier import DossierCache
from sluice.triage.audit import AuditLog
from sluice.triage.engine import run
import sluice.triage.engine as eng


def _note(v, name, fm_lines):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    open(os.path.join(leads, name), "w").write(
        "---\n" + "\n".join(fm_lines) + "\n---\n# body\n")


class _Backend:
    """Echoes back the actual lead_ids from the batch prompt (as a real judge
    does), so verdicts always match the dossiers the engine built."""
    last_backend = "primary"
    def complete(self, prompt):
        ids = re.findall(r"Dossier \d+ lead_id: (\S+)", prompt)
        return json.dumps([{"lead_id": i, "verdict": "shortlist",
                            "relevance_score": 80} for i in ids])


def _fields(company, role, status="new"):
    # location "remote" matches TriageConfig's neutral default
    # target_locations=["remote"], so these engine-flow tests don't depend
    # on a specific geo preference.
    return [f'company: "{company}"', f'role: "{role}"', 'location: "remote"',
            'salary: ""', 'role_type: "permanent"', 'url: "https://x/y"',
            f"status: {status}", "score: 0", 'glassdoor_rating: ""',
            'culture_flags: ""', 'relevance_notes: ""']


def _cache(tmp_path):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7,
                        fetcher=lambda lead: {"jd": {"markdown": "j"},
                                              "glassdoor": {"rating": "4.0"}},
                        clock=lambda: datetime(2026, 7, 7))


def _blank_fields(role, *, source="ex-board", url="https://x/y", status="new"):
    return ['company: ""', f'role: "{role}"', 'location: "remote"',
           'salary: ""', 'role_type: "permanent"', f'url: "{url}"',
           f'source: "{source}"',
           f"status: {status}", "score: 0", 'glassdoor_rating: ""',
           'culture_flags: ""', 'relevance_notes: ""']


class _RecordingCache(DossierCache):
    """A DossierCache stand-in recording get_or_build calls without touching disk,
    for proving how many fetches a run actually performs.

    Subclasses the real cache rather than duck-typing it so `lead_id` is stamped by
    the PRODUCTION cache_key and cannot drift from it. Faithfulness there is
    load-bearing, not decoration: the real get_or_build always stamps that key, and
    engine.py's enrich pass indexes `note_by_id[d["lead_id"]]` OUTSIDE its own
    try/except -- so a double omitting it raises a KeyError that reads as a bug in
    the code under test rather than as an unfaithful double (tst-003). Nothing
    inherited is ever reached: get_or_build is fully overridden and is the only
    method engine.py and resolve.py call, so the blank dir/None fetcher stay inert."""
    def __init__(self, dossier=None):
        super().__init__("", 0, None)
        self.calls = []
        self._dossier = dossier or {"page_title": "", "structured_data": ""}

    def get_or_build(self, fm):
        self.calls.append(dict(fm))
        return {"lead_id": self.cache_key(fm), **self._dossier}


def _tier1_source(company):
    class _Source:
        def company_from_url(self, url):
            return company
    return _Source()


def _get_source(mapping):
    def _get(sid):
        if sid not in mapping:
            raise KeyError(sid)
        return mapping[sid]
    return _get


def test_pipeline_classifies_and_judges(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _fields("Acme", accept[0].title()))   # keep -> judge
    _note(v, "dir.md", _fields("Beta", reject[0].title()))    # deterministic reject
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cfg.reject_titles = list(reject)
    report = run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    statuses = {n.fm["company"]: n.status for n in v.read_leads()}
    assert statuses["Beta"] == "dismiss"        # deterministic reject
    assert statuses["Acme"] == "shortlist"      # LLM verdict applied
    assert report.counts["dismiss"] >= 1 and report.counts["shortlist"] >= 1
    lines = open(str(tmp_path / "audit.jsonl")).read().strip().splitlines()
    assert any("Director" in l or "reject" in l for l in lines)


def test_no_llm_skips_judge(tmp_path):
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _fields("Acme", "Banker"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, TriageConfig(), _Backend(), _cache(tmp_path), audit,
                 statuses=("new",), no_llm=True)
    assert report.judged == 0
    assert v.read_leads()[0].status == "new"    # kept, not judged


class _CapturingBackend:
    last_backend = "primary"
    def __init__(self):
        self.prompts = []
    def complete(self, prompt):
        self.prompts.append(prompt)
        ids = re.findall(r"Dossier \d+ lead_id: (\S+)", prompt)
        return json.dumps([{"lead_id": i, "verdict": "research",
                            "relevance_score": 65} for i in ids])


def test_judge_prompt_is_composed_from_vault_criteria(tmp_path):
    v = Vault(str(tmp_path / "vault"))
    # the candidate's editable criteria live in the vault; the judge must use them.
    prof = os.path.join(v.dir, "Job Applications", "Judging Profile.md")
    os.makedirs(os.path.dirname(prof), exist_ok=True)
    open(prof, "w").write("## Who Alex is\nSENTINEL_ONLY_WANTS_HAIKU_ROLES\n")
    _note(v, "acme.md", _fields("Acme", "Banker"))
    be = _CapturingBackend()
    run(v, TriageConfig(), be, _cache(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
        statuses=("new",))
    assert be.prompts, "backend was never called"
    assert "SENTINEL_ONLY_WANTS_HAIKU_ROLES" in be.prompts[0]   # vault criteria used


def test_dry_run_writes_nothing(tmp_path):
    v = Vault(str(tmp_path / "vault"))
    _note(v, "dir.md", _fields("Beta", "Banker"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    run(v, TriageConfig(), _Backend(), _cache(tmp_path), audit,
        statuses=("new",), dry_run=True)
    assert v.read_leads()[0].status == "new"    # unchanged
    assert not os.path.exists(str(tmp_path / "audit.jsonl"))


def test_triage_classify_conflict_is_counted_and_batch_continues(tmp_path, titles, monkeypatch):
    # #16 Task 6: a VaultConflict at the classify-pass apply site (engine.py:56)
    # must not abort the batch -- it is counted in report.failures and the
    # conflicted lead is left untouched, while the next lead still gets applied.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "aaa.md", _fields("Example Conflict Co", reject[0].title()))  # sorts first -> conflicts
    _note(v, "bbb.md", _fields("Beta", reject[0].title()))   # sorts second -> survivor
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)

    real = eng.apply_classification
    calls = {"n": 0}

    def flaky(vault, note, decision, reason):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(note.ref)
        return real(vault, note, decision, reason)
    monkeypatch.setattr(eng, "apply_classification", flaky)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    assert any("apply" in f for f in report.failures)   # the conflict was recorded
    statuses = {n.fm["company"]: n.status for n in v.read_leads()}
    assert statuses["Example Conflict Co"] == "new"       # conflicted lead left in its prior state
    assert statuses["Beta"] == "dismiss"    # survivor still applied (batch continued)


def test_triage_judge_conflict_is_counted_and_batch_continues(tmp_path, titles, monkeypatch):
    # Symmetric to the classify-pass test above, targeting the judge-pass apply
    # site (engine.py:92).
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "aaa.md", _fields("Example Conflict Co", accept[0].title()))  # sorts first -> conflicts
    _note(v, "bbb.md", _fields("Beta", accept[0].title()))   # sorts second -> survivor
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    real = eng.apply_verdict
    calls = {"n": 0}

    def flaky(vault, note, verdict, dossier):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(note.ref)
        return real(vault, note, verdict, dossier)
    monkeypatch.setattr(eng, "apply_verdict", flaky)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    assert any("apply" in f for f in report.failures)   # the conflict was recorded
    statuses = {n.fm["company"]: n.status for n in v.read_leads()}
    assert statuses["Example Conflict Co"] == "new"          # conflicted lead left in its prior state
    assert statuses["Beta"] == "shortlist"     # survivor still applied (_Backend's verdict)


def test_resolution_never_attempted_for_a_lead_classify_would_reject_anyway(tmp_path, titles):
    # Proves the arch-001/rev-002 cost-neutrality fix: gating resolution on
    # decision == "needs_review" means classify()'s existing free title reject
    # (which runs BEFORE the blank-company branch) short-circuits resolution
    # entirely for a lead that was never going to reach it.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(reject[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",), get_source=_get_source({}))

    assert cache.calls == []
    assert v.read_leads()[0].status == "dismiss"


def test_tier1_resolution_reclassifies_to_dismiss_on_reject_companies(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_companies = ["resolved co"]
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",),
           get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "dismiss"
    assert cache.calls == []          # tier 1 hit, tier 2 never reached


def test_tier2_resolution_reclassifies_to_dismiss_on_reject_companies(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_companies = ["resolved co"]
    cfg.company_resolve_fetch = True
    dossier = {"page_title": "", "structured_data":
              '{"@type": "JobPosting", "hiringOrganization": {"name": "Resolved Co"}}'}
    cache = _RecordingCache(dossier=dossier)

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",),
           get_source=_get_source({}))     # unknown source -> tier 1 abstains

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "dismiss"
    assert len(cache.calls) == 1


def test_no_llm_leaves_a_tier1_miss_lead_unresolved(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",),
           no_llm=True, get_source=_get_source({}))

    after = v.read_leads()[0]
    assert after.fm["company"] == ""
    assert after.status == "needs_review"
    assert cache.calls == []


def test_company_resolve_fetch_off_by_default_leaves_a_lead_unresolved(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()          # company_resolve_fetch left at its False default
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",), get_source=_get_source({}))

    after = v.read_leads()[0]
    assert after.fm["company"] == ""
    assert after.status == "needs_review"
    assert cache.calls == []


def test_company_write_vault_conflict_leaves_the_original_needs_review_decision(tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def flaky(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(ref)
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", flaky)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == ""               # the company write never landed
    assert after.status == "needs_review"           # classify()'s original decision stands
    assert any("company-resolve" in f for f in report.failures)


def test_company_write_require_status_abstain_leaves_the_original_needs_review_decision(tmp_path, titles, monkeypatch):
    # Mirrors #9's own require_status race test construction: the lead advances
    # into the application lifecycle between read_leads() and the company write.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"status": "applied"})    # a receipt lands first
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.status == "applied"
    assert after.fm["company"] == ""
    assert any("company-resolve" in f for f in report.failures)


def test_company_write_already_current_self_heals_without_a_misleading_claim(tmp_path, titles, monkeypatch):
    # rev2-001: a concurrent resolution computes the identical company first, so
    # THIS run's write is a genuine no-op (require_status still passes; the value
    # already matched). Same behaviour as the require_status-abstain case: decision
    # stays needs_review this run, self-healing on the next once company reads
    # back non-blank.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"company": '"Resolved Co"'})   # a concurrent resolution wrote first
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"       # the concurrent write survives
    assert any("company-resolve" in f for f in report.failures)
    assert after.status == "needs_review"     # this run's write was a no-op; the eventual
                                              # apply_classification write below still lands


def test_company_write_never_overwrites_a_company_a_human_typed_mid_run(tmp_path, titles, monkeypatch):
    # The "is it blank?" decision is made from the read_leads() SNAPSHOT, but the write
    # happens after a tier-2 page fetch -- a real page load, seconds. A human editing the
    # note in Obsidian inside that window has their typed company silently replaced by the
    # scraped one, with no signal. Same argument Task 6 makes for `status`, applied to the
    # field this feature actually writes, and closed the same way: a FRESH re-read inside
    # the CAS transform (require_blank), never the caller's stale snapshot.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"company": '"Human Typed Co"'})   # a human edits it in Obsidian
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    # A DIFFERENT value from the one this run resolved: the guard must refuse on
    # presence, not merely no-op on an identical value the way rev2-001 does.
    assert after.fm["company"] == "Human Typed Co"
    assert any("company-resolve" in f for f in report.failures)


def test_a_backslash_in_a_resolved_company_does_not_kill_the_batch(tmp_path, titles):
    # resolve.py's docstring promises "one source's bug on one unanticipated URL shape
    # must not crash the whole triage run". A backslash is not a VaultConflict, so
    # engine.py's `except VaultConflict` cannot catch it: _set_fm substitutes the literal
    # through re.sub, which interprets escapes in the REPLACEMENT template -- so a scraped
    # `Foo\Bar Ltd` raises re.PatternError ("bad escape \B") mid-batch and every lead after
    # it is silently never processed, while the ones before it are already written. (Other
    # backslash sequences corrupt silently instead of raising: `\n` in a replacement
    # template becomes a real newline, breaking the frontmatter.) The guard is resolve.py's
    # _safe, which must reject a backslash for the same reason it rejects a quote.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "aaa.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/1"))
    _note(v, "bbb.md", _fields("Survivor Co", reject[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)
    cfg.company_resolve_fetch = True

    eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
            get_source=_get_source({"ex-board": _tier1_source("Foo\\Bar Ltd")}))

    statuses = {n.fm["company"]: n.status for n in v.read_leads()}
    assert statuses[""] == "needs_review"          # abstained rather than writing it
    assert statuses["Survivor Co"] == "dismiss"    # the rest of the batch still ran


def test_apply_classification_race_produces_no_persisted_audit_entry(tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "race.md", _fields("Race Co", reject[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)

    real = eng.apply_classification
    def racer(vault, note, decision, reason):
        vault.update_fields(note.ref, {"status": "applied"})    # a receipt lands first
        return real(vault, note, decision, reason)
    monkeypatch.setattr(eng, "apply_classification", racer)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()[0]
    assert after.status == "applied"                       # the vault clobber is stopped
    assert report.counts["skipped"] >= 1
    assert any("apply-race" in f for f in report.failures)
    assert audit.read_recent(30) == []                      # no false persisted entry


def test_apply_verdict_race_produces_no_persisted_audit_entry(tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "race.md", _fields("Race Co", accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    real = eng.apply_verdict
    def racer(vault, note, verdict, dossier):
        vault.update_fields(note.ref, {"status": "applied"})
        return real(vault, note, verdict, dossier)
    monkeypatch.setattr(eng, "apply_verdict", racer)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()[0]
    assert after.status == "applied"
    assert report.counts["skipped"] >= 1
    assert any("apply-race" in f for f in report.failures)
    assert audit.read_recent(30) == []


def test_dry_run_resolution_keep_count_is_accurate_but_reject_bucket_is_not(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "keep.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/1"))
    _note(v, "rej.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/2"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_companies = ["blocked co"]
    cfg.company_resolve_fetch = True

    class _UrlKeyedSource:
        def company_from_url(self, url):
            return {"https://x/1": "Resolved Co", "https://x/2": "Blocked Co"}.get(url)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     dry_run=True, get_source=_get_source({"ex-board": _UrlKeyedSource()}))

    assert report.counts["keep"] == 1
    assert report.counts["skipped"] >= 1
    for note in v.read_leads():
        assert note.fm["company"] == ""      # nothing actually written under dry_run


def test_tier2_resolution_and_the_later_judge_share_one_fetch(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/1"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    calls = []
    def fetcher(lead):
        calls.append(lead.get("url"))
        return {"jd": {"markdown": "j"}, "glassdoor": {}, "page_title": "",
                "structured_data": '{"@type": "JobPosting", '
                                   '"hiringOrganization": {"name": "Resolved Co"}}'}
    real_cache = DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                              clock=lambda: datetime(2026, 7, 7))

    eng.run(v, cfg, _Backend(), real_cache, audit, statuses=("new",),
           get_source=_get_source({}))     # unknown source -> tier 1 abstains, tier 2 runs

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "shortlist"           # resolved, then judged and shortlisted
    assert len(calls) == 1                       # classify-pass fetch reused by enrich/judge
