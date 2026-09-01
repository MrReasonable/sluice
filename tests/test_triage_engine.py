import json
import os
import re
from datetime import datetime

import pytest

from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from sluice.triage.config import TriageConfig
from sluice.core.dossier import DossierCache
from sluice.triage import reverdict
from sluice.triage.audit import AuditLog
from sluice.triage.classify import classify
from sluice.triage.engine import run
from sluice.core.backends import BackendError
from sluice.triage.audit import render_rejected_note
import sluice.triage.engine as eng


def _note(v, name, fm_lines, *, subdir=""):
    # `subdir` files the note in a folder under Job Leads, which the scan walks
    # recursively (#1). That is the only way to seat one FILENAME -- and so one
    # store-issued slug -- on two notes, since a single directory cannot hold two.
    leads = os.path.join(v.dir, "Job Applications", "Job Leads", subdir)
    os.makedirs(leads, exist_ok=True)
    open(os.path.join(leads, name), "w").write(
        "---\n" + "\n".join(fm_lines) + "\n---\n# body\n")


# The judge's per-dossier id line, as the doubles below read it back. The id is the REST
# OF THE LINE: `lead_id` is the note's store-issued slug, and a real slug is a note
# FILENAME -- `Example Ltd - Example Role`, spaces and all. A `(\S+)` here captured only
# the first token, and an unmatched lead_id is dropped SILENTLY by the engine (a
# `continue`), so a double that mis-parsed it would report a run that judged nothing as a
# run with nothing to judge. The vault's `_sanitize` maps the C0 controls out of every name
# it issues, so one LINE is always the whole id.
#
# `[^\n]`, not `.`: `_CompanyKeyedBackend` below compiles this into a pattern it runs with
# re.S, where `.` matches newlines too and a greedy `(.+)` swallowed the rest of the prompt.
# Spelling the exclusion out keeps the id one line whatever flags a caller adds.
_DOSSIER_ID = r"Dossier \d+ lead_id: ([^\n]+)"


class _Backend:
    """Echoes back the actual lead_ids from the batch prompt (as a real judge
    does), so verdicts always match the dossiers the engine built.

    Records every prompt it is handed: what the judge is TOLD is the only place
    some of this feature's effects are observable, and reading it off the real
    prompt keeps that assertion honest about the whole engine -> judge -> slim()
    path rather than a hand-built dossier."""
    last_backend = "primary"

    def __init__(self):
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        ids = re.findall(_DOSSIER_ID, prompt)
        return json.dumps([{"lead_id": i, "verdict": "shortlist",
                            "relevance_score": 80} for i in ids])


def _fields(company, role, status="new", *, url="https://x/y"):
    # location "remote" matches TriageConfig's neutral default
    # target_locations=["remote"], so these engine-flow tests don't depend
    # on a specific geo preference.
    #
    # `url` defaults to the one every caller shared before it was a parameter, so two
    # notes built by this helper collide on `DossierCache.cache_key`'s url hash unless a
    # caller says otherwise -- which the two-lead identity tests below rely on in BOTH
    # directions, one taking the default and one overriding it.
    return [f'company: "{company}"', f'role: "{role}"', 'location: "remote"',
            'salary: ""', 'role_type: "permanent"', f'url: "{url}"',
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


def _sentinel_fields(role, *, source="ex-board", url="https://x/y", status="new",
                     sentinel="Unknown"):
    # #151: sibling of _blank_fields seeding a PLACEHOLDER company rather than a blank
    # one -- the legacy/foreign-note shape this task's gate+guard fix exists for.
    # `sentinel` is a parameter (not hardcoded) so a test can pick a different
    # NON_ANSWER_COMPANIES member/casing without a second near-identical helper.
    return [f'company: "{sentinel}"', f'role: "{role}"', 'location: "remote"',
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


class _CacheWhere(DossierCache):
    """A DossierCache stand-in whose JD markdown is chosen PER LEAD, keyed by company --
    for proving the #169 unjudgeable gate in a MIXED batch. `_RecordingCache` above hands
    every lead the same dossier, which cannot express "this one lead's JD never arrived
    while its neighbour's did" -- exactly the case a vacuous `prompts == []` assertion
    would miss, since a run that judged NOTHING would satisfy that too.

    Subclasses DossierCache rather than duck-typing it, matching _RecordingCache's own
    reasoning: `lead_id` is stamped by the real, production `cache_key`, so it cannot
    drift from what get_or_build actually stamps.

    `jd_by_company` is indexed with `[...]`, not `.get(...)`: a company this run never
    configured a JD for is a fixture bug in the CALLING test, and should raise loudly
    rather than silently default to an empty JD that would make every unconfigured lead
    look like it never arrived."""
    def __init__(self, jd_by_company):
        super().__init__("", 0, None)
        self._jd_by_company = jd_by_company

    def get_or_build(self, fm):
        markdown = self._jd_by_company[fm.get("company", "")]
        return {"lead_id": self.cache_key(fm), "jd": {"markdown": markdown},
               "glassdoor": {}, "page_title": "", "structured_data": ""}


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


class _AppliedVerdictBackend:
    """A judge that returns an APPLICATION-OWNED status, exactly like a real model
    hallucinating outside its own vocabulary (triage/prompt.py's `_SCAFFOLD_TAIL` output
    schema asks for shortlist|research|dismiss only). #169: this is the live hole -- `_status.normalize`
    passes an unrecognised verdict through untouched, so this used to write `applied`
    straight from triage, reachable from model output."""
    last_backend = "primary"
    def complete(self, prompt):
        ids = re.findall(_DOSSIER_ID, prompt)
        return json.dumps([{"lead_id": i, "verdict": "applied",
                            "relevance_score": 80} for i in ids])


def test_a_judge_verdict_outside_the_vocabulary_is_clamped_in_the_write_the_counts_and_the_audit(
        tmp_path):
    # counts and the audit row are computed in the engine, OUTSIDE apply_verdict, and
    # both keyed on the RAW model string -- a clamp that fixed only the write would
    # report a verdict that never landed (the #109/#118 bug class this repo has
    # already fixed twice).
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _fields("Acme", "Banker"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, TriageConfig(), _AppliedVerdictBackend(), _cache(tmp_path), audit,
                 statuses=("new",))
    assert report.counts["needs_review"] == 1
    assert report.counts.get("applied", 0) == 0
    assert v.read_leads()[0].status == "needs_review"    # the WRITE is clamped too
    lines = open(str(tmp_path / "audit.jsonl")).read().strip().splitlines()
    last = json.loads(lines[-1])
    assert last["verdict"] == "needs_review"    # the audit reports what actually landed


class _CapturingBackend:
    last_backend = "primary"
    def __init__(self):
        self.prompts = []
    def complete(self, prompt):
        self.prompts.append(prompt)
        ids = re.findall(_DOSSIER_ID, prompt)
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


def test_resolution_never_attempted_for_a_lead_whose_status_triage_does_not_own(
        tmp_path, titles):
    # #120 local pre-push review: the classify-pass gate had no check that the
    # lead's CURRENT status is one triage owns. Under the default `--status
    # new,research` invocation this never matters (both are inside TRIAGE_OWNED)
    # -- but an explicit `--status` sweep that also covers a status OUTSIDE
    # TRIAGE_OWNED (e.g. an application-owned "offer") could otherwise trigger a
    # real page fetch and/or a real LLM call for a write the existing
    # require_status guard below would refuse anyway. A wasted-cost gap, not a
    # safety gap -- but there is no reason to pay for a fetch/call whose result is
    # guaranteed to be discarded.
    accept, _reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "owned.md", _blank_fields(accept[0].title(), url="https://x/owned",
                                       status="needs_review"))
    _note(v, "outside.md", _blank_fields(accept[0].title(), url="https://x/outside",
                                         status="offer"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("needs_review", "offer"),
           get_source=_get_source({}))

    # Only the TRIAGE_OWNED-status lead's resolution is attempted at all: the
    # dossier cache (tier 2's own fetch, and the gate tier 3 sits behind) is never
    # touched for the "offer"-status lead, while it IS touched -- exactly once --
    # for the "needs_review"-status one.
    assert len(cache.calls) == 1
    assert cache.calls[0]["url"] == "https://x/owned"


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
    # THIS run's write abstains at the require_blank guard: the company field is
    # no longer blank after the concurrent write, so require_blank refuses on
    # presence (before any value comparison). Decision stays needs_review this run.
    #
    # It self-heals on a later run that is explicitly pointed at needs_review leads
    # -- NOT on the default status set: `triage run` reads ("new", "research"), so a
    # lead parked on needs_review is not picked up again until someone passes
    # `--status needs_review`. The company is correct in the vault either way; what
    # waits is the re-classification that would act on it.
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
    assert after.status == "needs_review"     # this run's write abstains at require_blank; the
                                              # eventual apply_classification write below still lands


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


def test_sentinel_company_lead_is_resolved_and_rewritten(tmp_path, titles):
    # #151: the load-bearing case this task exists for. A note that already reads
    # "Unknown" -- legacy/foreign data, never something sluice itself writes -- must
    # be picked up by the SAME resolution pass a blank company gets: the gate
    # (`is_placeholder_company`) must fire, and the write guard
    # (`require_blank` + `blank_values=NON_ANSWER_COMPANIES`) must actually let the
    # write land rather than refusing on the sentinel's mere presence.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "sentinel.md", _sentinel_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    # tier 0 must have genuinely ABSTAINED here, not merely have run before tier 1 without
    # anyone checking -- the fixture role text has no " at " clause for _ROLE_AT_COMPANY to
    # match today, but nothing pins that; without this a future change to either the regex
    # or this test's title fixture could make tier 0 silently produce the write instead, and
    # the tier1 assertion below would still pass for the wrong reason (CodeRabbit finding 8).
    assert report.resolved.get("tier0", 0) == 0
    assert report.resolved.get("tier1", 0) == 1     # tier 1 (company_from_url) did the resolving
    assert not any("company-resolve" in f for f in report.failures)


def test_sentinel_company_write_never_overwrites_a_real_company_a_human_typed_mid_run(
        tmp_path, titles, monkeypatch):
    # The race-safety control (test_company_write_never_overwrites_a_company_a_human_
    # typed_mid_run, above, unmodified) starts from a BLANK company. This is the same
    # race starting from a SENTINEL company instead, because `blank_values` widens
    # what require_blank treats as absent, and that widening must not also widen what
    # it treats as a human's REAL answer: "Example Real Co" folds to something outside
    # NON_ANSWER_COMPANIES, so it must still block the write exactly like the blank
    # case does.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "sentinel.md", _sentinel_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"company": '"Example Real Co"'})   # a human edits it in Obsidian
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Example Real Co"
    assert any("company-resolve" in f for f in report.failures)


def test_a_human_typing_unknown_mid_run_does_not_block_the_resolution_write(
        tmp_path, titles, monkeypatch):
    # The deliberate, reasoned exception `blank_values` exists for: require_blank's
    # promise is "never replace a human's ANSWER", and typing "Unknown" back into the
    # company field is not an answer -- it is the same non-answer the note already
    # held. So a human re-typing (or a stray concurrent process re-writing) the
    # identical placeholder mid-run must NOT block this run's resolution from landing,
    # unlike the REAL-company race above.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "sentinel.md", _sentinel_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"company": '"Unknown"'})   # a human re-types the same non-answer
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert not any("company-resolve" in f for f in report.failures)
    # The racer must have actually fired, not silently done nothing (CodeRabbit finding 6):
    # without this, a broken monkeypatch/injection would leave every assertion above passing
    # for the wrong reason -- the write would simply land unraced. Measured: `racer` is
    # invoked exactly twice per note in this run (the company-resolve write, where the
    # injected "Unknown" re-type happens first at calls["n"] == 1, then the later classify
    # status write), so >= 2 proves both the injected write's call site and the real
    # resolution write's call site were genuinely exercised through the intercepted path.
    assert calls["n"] >= 2


def test_a_backslash_in_a_resolved_company_does_not_kill_the_batch(tmp_path, titles):
    # resolve.py's docstring promises "one source's bug on one unanticipated URL shape
    # must not crash the whole triage run", and a scraped `Foo\Bar Ltd` is exactly that
    # shape. The batch-killing arm of this -- _set_fm's re.sub interpreting `\B` as an
    # escape in its REPLACEMENT template and raising re.PatternError, which is not a
    # VaultConflict and so escaped engine.py's `except` -- is now fixed at the vault
    # layer (a callable replacement; see test_vault_rw.py). What is witnessed HERE is
    # the surviving, independent reason resolve.py's guard (`frontmatter_safe`, in
    # core/vault.py) must still reject a backslash: a raw backslash inside the
    # double-quoted YAML scalar the write produces
    # is a YAML escape, so `company: "Foo\Bar Ltd"` is a ScannerError in the candidate's
    # own note reader -- a lead written into a state a human cannot open, which no
    # amount of correctness at the vault layer would prevent.
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
    # #118: this is a genuine no-op (require_status refused, nothing lost), not a
    # failure -- a real content race would raise VaultConflict instead, a separate,
    # already-correctly-reported path (`report.failures.append(f"apply {note.ref}: {e}")`
    # above).
    assert report.failures == []
    assert audit.read_recent(30) == []                      # no false persisted entry


def test_an_application_owned_lead_produces_no_persisted_audit_entry(tmp_path, titles):
    # The PRE-EXISTING skip, which has the identical shape to the race above and so
    # had the identical bug: apply.py's _guarded() refuses the write outright because
    # the lead has already left TRIAGE_OWNED, yet classify() still computed a decision
    # for it, and that decision used to reach the audit log -- a persisted line saying
    # this lead was dismissed on a day nothing was written to it, which
    # render_rejected_note then puts in front of a human.
    #
    # No monkeypatching here, deliberately: unlike the race, this needs no interleaving
    # at all. The lead simply IS `applied` when the run reads it, which is the ordinary
    # state of every lead a human has already acted on, reached by pointing `--status`
    # at it. That is what makes this the commoner of the two.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "done.md", _fields("Applied Co", reject[0].title(), status="applied"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit,
                     statuses=("new", "applied"))

    after = v.read_leads()[0]
    assert after.status == "applied"           # untouched: _guarded() refused the write
    assert report.counts["skipped"] >= 1
    assert audit.read_recent(30) == [], \
        "a decision that _guarded() refused to write must not be persisted as if it had"


def test_an_application_owned_lead_the_judge_saw_produces_no_persisted_audit_entry(tmp_path,
                                                                                   titles):
    # The judge-pass half of the test above, and NOT redundant with it: the two audit
    # sites are separate `if`s, so a fix applied to only one leaves the other writing
    # the same false line. An application-owned lead whose title classify() KEEPS is
    # enriched and judged like any other (the pre-gate reads the title, not the
    # status), and only apply_verdict's _guarded() then refuses the write.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "done.md", _fields("Applied Co", accept[0].title(), status="applied"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit,
                     statuses=("new", "applied"))

    after = v.read_leads()[0]
    assert after.status == "applied"           # untouched: _guarded() refused the write
    assert report.judged == 1                  # the judge really did run on it
    assert report.counts["skipped"] >= 1
    assert audit.read_recent(30) == [], \
        "a verdict that _guarded() refused to write must not be persisted as if it had"


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
    # #118: same reasoning as the classify-pass test above -- a benign no-op, not a
    # failure.
    assert report.failures == []
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

    backend = _Backend()
    eng.run(v, cfg, backend, real_cache, audit, statuses=("new",),
           get_source=_get_source({}))     # unknown source -> tier 1 abstains, tier 2 runs

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "shortlist"           # resolved, then judged and shortlisted
    assert len(calls) == 1                       # classify-pass fetch reused by enrich/judge
    # Sharing the fetch is only half of what has to be true, and on its own it is what
    # BREAKS the other half: the classify-pass fetch builds the cache entry while company
    # is still "", and get_or_build snapshots `company` off the lead at BUILD time. The
    # enrich pass then hits that same entry -- by design, that is what makes the count
    # above 1 -- so without a re-derive the judge is handed the stale blank for the full
    # cache TTL, on the very leads this feature exists to give a company to.
    prompt = backend.prompts[0]
    assert '"company": "Resolved Co"' in prompt
    assert '"company": ""' not in prompt


def test_the_judge_reads_lead_fields_off_the_note_not_a_stale_cached_snapshot(tmp_path, titles):
    """The same defect one step wider, and the reason engine.py re-derives four fields
    rather than only `company`.

    get_or_build snapshots company/role/location/role_type off the lead at BUILD time,
    and the url-hash cache_key made that snapshot outlive the edits it used to be
    invalidated by: keying on company+role meant a hand edit to either MINTED a new key
    and re-fetched, so the snapshot could not go stale. Keying on the url means an entry
    built before the edit is reused, unchanged, for the rest of its ttl -- and what the
    judge is shown is the only place that is visible.

    Stated against a cache that returns a fully-populated STALE dossier, which is what
    a real cache hit from before an edit looks like."""
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "lead.md", _fields("Current Co", accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    stale = _RecordingCache(dossier={
        "company": "Stale Co", "position": "Stale Role", "location": "stale-city",
        "role_type": "contract", "jd": {"markdown": "j"}, "glassdoor": {},
        "page_title": "", "structured_data": ""})
    backend = _Backend()
    eng.run(v, cfg, backend, stale, audit, statuses=("new",))

    # Key-scoped, not a bare substring sweep: the system prompt's own worked examples
    # contain the word "contract", so `"contract" not in prompt` reddens for a reason
    # that has nothing to do with the dossier -- measured, not guessed at.
    prompt = backend.prompts[0]
    for key, stale_value in (("company", "Stale Co"), ("position", "Stale Role"),
                             ("location", "stale-city"), ("role_type", "contract")):
        assert f'"{key}": "{stale_value}"' not in prompt, \
            f"the judge was shown the cache's stale {key}"
    assert '"company": "Current Co"' in prompt
    assert f'"position": "{accept[0].title()}"' in prompt
    assert '"location": "remote"' in prompt
    assert '"role_type": "permanent"' in prompt


def test_a_blank_note_field_is_shown_blank_not_backfilled_from_a_stale_cache_value(tmp_path, titles):
    """#113: the same staleness bug #109 fixed for `lead_id`, one step further --
    specifically on the correction path a human would take to fix bad data.

    A human clearing a field on the note (e.g. blanking a wrong location for someone
    to refill) must be seen as blank by the judge, not silently backfilled from a
    STALE cached copy of what the field used to say. The `or`-fallback's own
    justification ("so a blank note value falls back rather than blanking a
    populated dossier field") does not hold: every one of these four dossier fields
    IS the note's own value, re-derived -- never a fetched fact -- so what it falls
    back to is a stale copy of the note's OWN prior value, not a complementary
    source of truth."""
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    fields = [ln if not ln.startswith("location:") else 'location: ""'
              for ln in _fields("Current Co", accept[0].title())]
    _note(v, "lead.md", fields)
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    stale = _RecordingCache(dossier={
        "company": "Current Co", "position": accept[0].title(), "location": "stale-city",
        "role_type": "permanent", "jd": {"markdown": "j"}, "glassdoor": {},
        "page_title": "", "structured_data": ""})
    backend = _Backend()
    eng.run(v, cfg, backend, stale, audit, statuses=("new",))

    prompt = backend.prompts[0]
    assert '"location": "stale-city"' not in prompt
    assert '"location": ""' in prompt


class _CompanyKeyedBackend:
    """Verdicts keyed on the COMPANY the dossier carries, not on position in the batch.

    The identity defect the tests below pin is invisible to `_Backend`'s uniform
    "shortlist for everyone": a verdict routed to the wrong note carries the same value
    as the one that belonged there, so every status still lands where it would have.
    Differing per company is what makes a misroute observable at all -- and keying on the
    company TEXT rather than on `lead_id` is deliberate, since `lead_id` is the field
    under test and a double that trusted it could not witness a collision in it."""
    last_backend = "primary"

    def __init__(self, by_company):
        self.by_company = by_company
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        out = []
        for lead_id, blob in re.findall(
                _DOSSIER_ID + r"\n```json\n(.*?)\n```", prompt, re.S):
            company = json.loads(blob).get("company", "")
            out.append({"lead_id": lead_id, "verdict": self.by_company[company],
                        "relevance_score": 70})
        return json.dumps(out)


def test_two_leads_sharing_one_url_each_get_their_own_verdict(tmp_path, titles):
    """Two DIFFERENT leads at one url -- a re-scrape not yet deduped, or a cross-post --
    must not have their verdicts swapped or dropped.

    `DossierCache.cache_key` hashes the url (#109), which is right for its actual job:
    two leads at one page SHOULD share one cache entry rather than fetch the page twice.
    What was wrong is that `get_or_build` then stamped that STORAGE key into the dossier's
    own `lead_id`, and the enrich pass keyed judge-round-trip IDENTITY on it -- so both
    leads were presented to the judge under one id, both verdicts came back wearing it,
    and `note_by_id`/`by_id` resolved both to whichever note was inserted LAST. One lead
    took the other's verdict; the other took none. Nothing raised."""
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    # One url (_fields hardcodes it), two companies. The same accepted title on both so
    # each clears the pre-gate and reaches the judge, which is where the identity is used.
    _note(v, "alpha.md", _fields("Alpha Co", accept[0].title()))
    _note(v, "beta.md", _fields("Beta Co", accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    backend = _CompanyKeyedBackend({"Alpha Co": "dismiss", "Beta Co": "shortlist"})
    report = eng.run(v, cfg, backend, _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()
    assert {n.fm["url"] for n in after} == {"https://x/y"}, \
        "the shared url is this test's premise; _fields no longer supplies one"
    assert {n.fm["company"]: n.status for n in after} == \
        {"Alpha Co": "dismiss", "Beta Co": "shortlist"}
    assert report.judged == 2
    assert report.failures == []


def test_two_leads_with_different_urls_each_get_their_own_verdict(tmp_path, titles):
    """The ordinary case, as a regression check on the fix above: distinct urls, distinct
    cache entries, and distinct slugs. It passed before the identity change and must keep
    passing after it -- a fix that routed by something CONSTANT per run would satisfy the
    collision test above (both notes named in one failure) while breaking every normal
    multi-lead run, and nothing else in this file judges two leads with differing
    verdicts."""
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "alpha.md", _fields("Alpha Co", accept[0].title(), url="https://x/1"))
    _note(v, "beta.md", _fields("Beta Co", accept[0].title(), url="https://x/2"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    backend = _CompanyKeyedBackend({"Alpha Co": "dismiss", "Beta Co": "shortlist"})
    report = eng.run(v, cfg, backend, _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()
    assert {n.fm["url"] for n in after} == {"https://x/1", "https://x/2"}
    assert {n.fm["company"]: n.status for n in after} == \
        {"Alpha Co": "dismiss", "Beta Co": "shortlist"}
    assert report.judged == 2
    assert report.failures == []


def test_two_kept_leads_at_one_slug_are_both_refused_and_reported(tmp_path, titles):
    """The residual non-uniqueness in the identity the fix above moved to.

    `note.slug` is unique BY CONSTRUCTION on a flat store -- one directory cannot hold two
    files at one basename -- but a recursive scan (#1) lets a human seat that name in two
    folders, and then keying on it has the same last-twin-wins defect the url hash had.
    So this takes the verdict every other consumer takes: index_by_slug DROPS both twins
    rather than picking one, and the run says so. Both halves are asserted, because
    dropping in silence is the mirror failure -- a lead gone from the run with nothing a
    human could act on.

    A third, unambiguous lead is judged in the same run: the refusal is scoped to the
    twins and must not cost the batch."""
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "twin.md", _fields("Alpha Co", accept[0].title(), url="https://x/1"),
          subdir="folder-a")
    _note(v, "twin.md", _fields("Beta Co", accept[0].title(), url="https://x/2"),
          subdir="folder-b")
    _note(v, "solo.md", _fields("Gamma Co", accept[0].title(), url="https://x/3"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    backend = _CompanyKeyedBackend({"Alpha Co": "dismiss", "Beta Co": "shortlist",
                                    "Gamma Co": "shortlist"})
    report = eng.run(v, cfg, backend, _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()
    assert len(after) == 3, "the twins are both READ; it is acting on them that is refused"
    assert {n.slug for n in after} == {"twin", "solo"}, \
        "the two twins must really share one store-issued slug"
    statuses = {n.fm["company"]: n.status for n in after}
    # Neither twin judged -- not the wrong one, and not one of them arbitrarily either.
    assert statuses["Alpha Co"] == "new"
    assert statuses["Beta Co"] == "new"
    assert statuses["Gamma Co"] == "shortlist"      # the batch still ran
    assert report.judged == 1

    # Reported, and naming the two FILES rather than the slug they share: repeating the
    # slug names nothing a human can act on, whereas the refs are what they rename.
    assert len(report.failures) == 1, report.failures
    failure = report.failures[0]
    assert "twin" in failure
    for ref in (n.ref for n in after if n.slug == "twin"):
        assert str(ref) in failure, failure


class _MisechoingBackend:
    """A judge that returns a verdict for a lead_id no dossier in the batch carries --
    the shape a real model produces when it paraphrases a prose slug (collapses
    whitespace, swaps a dash) rather than echoing it byte-for-byte, now that lead_id
    is a slug instead of an opaque hash."""
    last_backend = "primary"

    def complete(self, prompt):
        return json.dumps([{"lead_id": "a slug nothing in this batch carries",
                            "verdict": "shortlist", "relevance_score": 80}])


def test_a_verdict_for_an_unmatched_lead_id_is_reported_not_silently_dropped(tmp_path, titles):
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "solo.md", _fields("Solo Co", accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    report = eng.run(v, cfg, _MisechoingBackend(), _cache(tmp_path), audit,
                     statuses=("new",))

    after = v.read_leads()[0]
    assert after.status == "new", "no note matches the mis-echoed id, so none is written"
    assert report.judged == 1, "the verdict was still produced -- only routing it failed"
    assert len(report.failures) == 1, report.failures
    assert "no note matches" in report.failures[0]


# ── tier 3 (#120): engine-level wiring ─────────────────────────────────────────

class _ResolveBackend:
    """A resolve_backend double: `replies` is consumed in order. A BackendError
    INSTANCE is raised, not returned. Exhausting the list raises AssertionError --
    a mis-counted fixture must fail loudly, not silently read as a real abstain."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def complete(self, prompt):
        self.calls.append(prompt)
        if not self._replies:
            raise AssertionError("ResolveBackend: no more scripted replies")
        reply = self._replies.pop(0)
        if isinstance(reply, BackendError):
            raise reply
        return reply


# `jd` carries a real markdown body (#169): these fixtures exist to prove tier-3
# RESOLUTION mechanics, and several of them resolve a lead all the way to a `keep`
# decision that then reaches the enrich+judge block. Without a JD that arrived, the
# unjudgeable gate added above would short-circuit every one of those leads before the
# judge, which is a real behaviour for THAT feature but silently defeats what these
# tests are actually checking (e.g. that the judge backend, not the resolve backend,
# is the one that gets called).
_LLM_DOSSIER = {"page_title": "Senior Engineer | Example Board", "structured_data": "",
                "jd": {"markdown": "A real JD, long enough to clear the unjudgeable floor."}}


def test_tier3_resolution_writes_the_company_and_is_counted_under_its_own_tier(
        tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend(["Resolved Co"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    report = run(v, cfg, _Backend(), cache, audit, statuses=("new",),
                get_source=None, resolve_backend=resolve_backend)

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert report.resolved == {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 1}
    assert report.llm_calls == 1


def test_the_judge_backend_is_never_used_for_company_resolution(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    judge_backend = _Backend()
    resolve_backend = _ResolveBackend(["Resolved Co"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    run(v, cfg, judge_backend, cache, audit, statuses=("new",),
       get_source=None, resolve_backend=resolve_backend)

    assert len(resolve_backend.calls) == 1
    assert resolve_backend.calls[0].startswith("You are the company-name resolution step")
    assert len(judge_backend.prompts) == 1
    assert judge_backend.prompts[0].startswith("You are the batched judgment stage")


def test_tier3_calls_are_counted_even_when_the_model_abstains(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank1.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/1"))
    _note(v, "blank2.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/2"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend(["NONE", "NONE"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    report = run(v, cfg, None, cache, audit, statuses=("new",),
                get_source=None, resolve_backend=resolve_backend)

    assert report.llm_calls == 2
    assert report.resolved == {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0}


def test_a_tier3_resolution_is_audited_and_never_reaches_the_rejected_leads_note(
        tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend(["Resolved Co"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    run(v, cfg, _Backend(), cache, audit, statuses=("new",),
       get_source=None, resolve_backend=resolve_backend)

    entries = audit.read_recent(30)
    resolve_entries = [e for e in entries if e.get("stage") == "resolve"]
    assert len(resolve_entries) == 1
    assert resolve_entries[0]["tier"] == "tier3"
    assert resolve_entries[0]["company"] == "Resolved Co"

    note_body = render_rejected_note(v, entries, cfg.rejected_note)
    assert "Resolved Co" not in note_body


class _EmptyVerdictBackend:
    """A judge backend that IS called but returns no verdicts. Lets a test drive a
    resolved lead all the way through the enrich+judge block -- proving tier 3's own
    audit list is what keeps the note-render trigger quiet, not merely an empty
    `keeps` list -- while guaranteeing nothing from the judge stage ever reaches
    `_audit`."""
    last_backend = "primary"
    def complete(self, prompt):
        return "[]"


def test_a_tier3_resolution_never_triggers_the_rejected_leads_note(tmp_path, titles):
    """#120 whole-branch review: resolve_audit_entries is a SEPARATE list from
    audit_entries specifically so a resolve-only run does not start rewriting
    "Rejected Leads Audit.md" on a run that previously never touched it. The
    existing test above pins the rendered note's CONTENT (resolve entries excluded
    via _is_reject); this pins whether render_rejected_note is CALLED AT ALL --
    merging the two lists back together would survive that content-only check (an
    empty-rejects note still renders fine) but would start writing a file this run
    never wrote before."""
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend(["Resolved Co"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    run(v, cfg, _EmptyVerdictBackend(), cache, audit, statuses=("new",),
       get_source=None, resolve_backend=resolve_backend)

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"          # the resolution really landed
    note_path = os.path.join(v.dir, cfg.rejected_note)
    assert not os.path.exists(note_path), \
        "a resolve-only run must never render the rejected-leads note"


def test_the_circuit_breaker_only_counts_consecutive_errors_not_cumulative(tmp_path, titles):
    """#120 whole-branch review: the breaker resets `_llm_consecutive_errors` to 0
    whenever a tier-3 attempt does NOT end in llm_error (a hit or an abstain both
    reset it). Deleting that reset -- making the counter cumulative instead of
    consecutive -- currently survives the full suite. This interleaves 3 TOTAL
    errors across 5 leads with a real hit and a real abstain breaking up any run of
    3-in-a-row: a cumulative counter would trip the breaker on the 3rd error
    (skipping the 5th lead's attempt entirely), while a correct consecutive counter
    never reaches 3 in a row and every lead gets attempted. The breaker's own
    message says "3 CONSECUTIVE backend errors" -- this is what keeps that claim
    true."""
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    for i in range(5):
        _note(v, f"blank{i}.md", _blank_fields(accept[0].title(), source="ex-board",
                                                url=f"https://x/{i}"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend([
        BackendError("down"), "Example Co", BackendError("down"),
        BackendError("down"), "NONE"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    report = run(v, cfg, _Backend(), cache, audit, statuses=("new",),
                get_source=None, resolve_backend=resolve_backend)

    assert len(resolve_backend.calls) == 5, "every lead must get a tier-3 attempt"
    assert report.llm_calls == 5
    assert not any("tier 3 disabled" in f for f in report.failures), \
        "3 SCATTERED errors must never trip the consecutive-errors breaker"


def test_a_dry_run_tier3_resolution_is_counted_but_writes_neither_company_nor_audit_line(
        tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend(["Resolved Co"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    report = run(v, cfg, _Backend(), cache, audit, statuses=("new",), dry_run=True,
                get_source=None, resolve_backend=resolve_backend)

    assert report.resolved["tier3"] == 1
    assert report.llm_calls == 1
    after = v.read_leads()[0]
    assert after.fm["company"] == ""                    # no vault write under dry_run
    assert not os.path.exists(str(tmp_path / "audit.jsonl"))   # no audit line either


def test_a_tier3_resolution_whose_write_was_refused_is_neither_counted_nor_audited(
        tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    resolve_backend = _ResolveBackend(["Resolved Co"])
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"company": '"Human Typed Co"'})   # a human edits it mid-run
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = run(v, cfg, _Backend(), cache, audit, statuses=("new",),
                get_source=None, resolve_backend=resolve_backend)

    after = v.read_leads()[0]
    assert after.fm["company"] == "Human Typed Co"      # the concurrent write survives
    assert report.resolved == {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0}
    assert any("company-resolve" in f for f in report.failures)
    entries = audit.read_recent(30)
    assert not any(e.get("stage") == "resolve" for e in entries)


def test_the_engine_leaves_tier3_off_when_no_resolve_backend_was_threaded(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True     # the knob is ON...
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    report = run(v, cfg, None, cache, audit, statuses=("new",), no_llm=True,
                get_source=None, resolve_backend=None)   # ...but no backend was built

    assert report.resolved == {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0}
    assert report.llm_calls == 0


def test_the_circuit_breaker_stops_tier3_after_3_consecutive_backend_errors_and_reports_once(
        tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    for i in range(4):
        _note(v, f"blank{i}.md", _blank_fields(accept[0].title(), source="ex-board",
                                                url=f"https://x/{i}"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    # Only 3 replies scripted for 4 candidate leads -- a 4th call would raise
    # AssertionError from the double itself, which IS the proof the breaker
    # stopped calling it rather than merely returning fewer hits.
    resolve_backend = _ResolveBackend([BackendError("down")] * 3)
    cache = _RecordingCache(dossier=_LLM_DOSSIER)

    report = run(v, cfg, None, cache, audit, statuses=("new",),
                get_source=None, resolve_backend=resolve_backend)

    assert len(resolve_backend.calls) == 3
    assert report.llm_calls == 3
    assert sum(1 for f in report.failures if "tier 3 disabled" in f) == 1
    assert report.resolved == {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0}


# ── tier 0 (#151): engine-level wiring ─────────────────────────────────────────

def test_tier0_resolves_a_sentinel_company_note_on_a_fully_zero_config_install(tmp_path):
    # #151: proven end-to-end on exactly the install shape tier 0 exists for --
    # `--no-llm`, no page-visit budget (company_resolve_fetch off), no LLM budget
    # (company_resolve_llm off), and no source registry at all (get_source=None).
    # Nothing about tier 0 needs any of those; only the role text itself does the work.
    v = Vault(str(tmp_path / "vault"))
    _note(v, "sentinel.md",
         _sentinel_fields("Head of Platform Engineering at Example Meridian", source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = False
    cfg.company_resolve_llm = False
    cache = _RecordingCache()

    report = eng.run(v, cfg, None, cache, audit, statuses=("new",),
                     no_llm=True, get_source=None, resolve_backend=None)

    after = v.read_leads()[0]
    assert after.fm["company"] == "Example Meridian"
    # Tier 0 recovers a company from role TEXT but must never REWRITE the role
    # field itself -- it is read-only evidence, not a field this tier owns.
    assert after.fm["role"] == "Head of Platform Engineering at Example Meridian"
    assert report.resolved["tier0"] == 1
    assert report.llm_calls == 0
    assert cache.calls == []   # no page visit and no LLM call -- the role text alone resolved it


# ── unjudgeable (#169): the enrich loop short-circuits a lead whose JD never arrived ──

def test_a_lead_whose_jd_never_arrived_is_marked_unjudgeable_and_never_judged(tmp_path, titles):
    """Asserted on the backend spy's prompt CONTENT in a MIXED batch (one healthy lead,
    one blocked): `prompts == []` would be satisfied by a run that judged NOTHING at
    all, so it proves nothing on its own. The healthy lead must be SHOWN reaching the
    judge in the same run the blocked one does not, or the assertion is vacuous."""
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "good.md", _fields("Good Co", accept[0].title(), url="https://x/1"))
    _note(v, "blocked.md", _fields("Blocked Co", accept[0].title(), url="https://x/2"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Good Co": "A real JD, long enough.", "Blocked Co": ""})
    backend = _Backend()

    report = eng.run(v, cfg, backend, cache, audit, statuses=("new",))

    assert report.counts["unjudgeable"] == 1
    after = {n.fm["company"]: n.status for n in v.read_leads()}
    assert after["Blocked Co"] == "unjudgeable"
    assert after["Good Co"] == "shortlist"   # _Backend shortlists everyone it is shown
    joined = "\n".join(backend.prompts)
    assert "Good Co" in joined, "the healthy lead must still reach the judge"
    assert "Blocked Co" not in joined, "an unjudgeable lead must cost no judge call"


def test_a_dry_run_counts_an_unjudgeable_lead_as_skipped_and_writes_nothing(tmp_path, titles):
    # Mirrors the classify-pass convention (test_dry_run_resolution_keep_count_is_
    # accurate_but_reject_bucket_is_not, above): a dry run reports what WOULD happen,
    # never a write that did not occur, so a would-be `unjudgeable` write is counted
    # `skipped` under dry_run -- exactly like a would-be `dismiss`/`needs_review` write
    # is there. Asserting `counts["unjudgeable"] == 1` here would be a THIRD, competing
    # counting convention inside one function; consistency with the two sibling passes
    # is worth more than a dry-run count that claims a write which never landed.
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blocked.md", _fields("Blocked Co", accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Blocked Co": ""})

    report = eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",), dry_run=True)

    assert report.counts["unjudgeable"] == 0
    assert report.counts["skipped"] == 1
    assert v.read_leads()[0].status == "new"   # dry_run counts and reports, writes nothing


def test_an_application_owned_lead_is_never_marked_unjudgeable(tmp_path, titles):
    # apply_classification's _guarded refuses an application-owned lead outright;
    # never-regress holds unchanged for this new decision exactly as it does for
    # every other one apply_classification already writes. Also pins the fix for a
    # real defect this task's own report flagged and the coordinator confirmed: the
    # COUNT must follow the write outcome too, not just the vault status. `_guarded`
    # refuses here (outcome "skipped"), so counting `unjudgeable` unconditionally
    # would have claimed a status change for a lead that is actually `applied` --
    # the OPPOSITE of its real state.
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "applied.md", _fields("Applied Co", accept[0].title(), status="applied"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Applied Co": ""})

    report = eng.run(v, cfg, _Backend(), cache, audit, statuses=("applied",))

    assert v.read_leads()[0].status == "applied"
    assert report.counts["unjudgeable"] == 0
    assert report.counts["skipped"] == 1


def test_a_shortlisted_lead_is_never_demoted_to_unjudgeable(tmp_path, titles):
    # #169, review round 2: `unjudgeable` records the ABSENCE of a verdict, so it must
    # never overwrite one. `_guarded` does not cover this -- `shortlist` is TRIAGE_OWNED,
    # not application-owned, so the write was permitted and a transient JD-fetch failure
    # during `triage run --status shortlist` demoted a real judge verdict to
    # `unjudgeable`, orphaning the composed CV pointer that stays on the note and leaving
    # cv/apply/track with nothing in the shortlist index and no route back. Exit 0
    # throughout, which is what made it silent.
    #
    # `tailored_cv` is asserted SURVIVING rather than merely present-before: the harm is
    # not just the status, it is a pointer left addressing a lead nobody will look at.
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "short.md",
          _fields("Shortlisted Co", accept[0].title(), status="shortlist")
          + ['tailored_cv: "cv-abc.pdf (2026-08-01)"'])
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Shortlisted Co": ""})   # JD never arrives -> unjudgeable branch

    report = eng.run(v, cfg, _Backend(), cache, audit, statuses=("shortlist",))

    note = v.read_leads()[0]
    assert note.status == "shortlist", "a transient fetch failure demoted a real verdict"
    assert note.fm.get("tailored_cv") == "cv-abc.pdf (2026-08-01)"
    assert report.counts["unjudgeable"] == 0
    assert report.counts["skipped"] == 1


def test_an_unjudgeable_lead_is_re_selected_and_judged_once_its_jd_arrives(tmp_path, titles):
    # THE promise #169 exists for, and it had no test anywhere: every other triage test
    # passes an explicit `statuses=`, so nothing drove the engine over a lead already at
    # `unjudgeable` using the SHIPPED default. Mutating DEFAULT_TRIAGE_STATUSES to
    # ("new","research") -- stranding every such lead forever, the exact harm this feature
    # exists to prevent -- was killed only incidentally, by two health-rate tests that
    # happen to name the constant.
    #
    # No `statuses=` kwarg on purpose. That is the whole point of the test.
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "stuck.md", _fields("Retry Co", accept[0].title(), status="unjudgeable"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Retry Co": "a real job description that arrived this time"})

    report = eng.run(v, cfg, _Backend(), cache, audit)

    assert v.read_leads()[0].status in ("shortlist", "research", "dismiss", "needs_review"), (
        "an unjudgeable lead was never re-selected, so its JD arriving changed nothing")
    assert report.counts.get("unjudgeable", 0) == 0


def test_a_new_lead_is_still_marked_unjudgeable(tmp_path, titles):
    # The other direction of the same guard: narrowing `unjudgeable`'s require_status
    # must not make the decision unreachable. Without this row, refusing EVERY status
    # passes the test above while silently disabling #169 outright.
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "fresh.md", _fields("Fresh Co", accept[0].title(), status="new"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Fresh Co": ""})

    report = eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",))

    assert v.read_leads()[0].status == "unjudgeable"
    assert report.counts["unjudgeable"] == 1


def test_triage_unjudgeable_conflict_is_counted_and_batch_continues(tmp_path, titles, monkeypatch):
    # Symmetric to test_triage_classify_conflict_is_counted_and_batch_continues and
    # test_triage_judge_conflict_is_counted_and_batch_continues above, at the THIRD
    # apply_classification call site engine.py now has: the #169 unjudgeable branch.
    # A VaultConflict there (a human editing the note in Obsidian mid-run) must not
    # abort the whole run -- it is counted in report.failures and the conflicted
    # lead is left untouched, while the next lead's write still lands.
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    # sorts first -> conflicts; identity reused from the classify-pass conflict test above
    _note(v, "aaa.md", _fields("Example Conflict Co", accept[0].title(), url="https://x/1"))
    _note(v, "bbb.md", _fields("Beta", accept[0].title(), url="https://x/2"))   # sorts second -> survivor
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cache = _CacheWhere({"Example Conflict Co": "", "Beta": ""})

    real = eng.apply_classification
    calls = {"n": 0}

    def flaky(vault, note, decision, reason):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(note.ref)
        return real(vault, note, decision, reason)
    monkeypatch.setattr(eng, "apply_classification", flaky)

    report = eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",))

    assert any("apply" in f for f in report.failures)   # the conflict was recorded
    statuses = {n.fm["company"]: n.status for n in v.read_leads()}
    assert statuses["Example Conflict Co"] == "new"       # conflicted lead left in its prior state
    assert statuses["Beta"] == "unjudgeable"    # survivor's write still landed (batch continued)
    assert report.counts["unjudgeable"] == 1     # only the survivor's write is counted


# ── #223 §2.4/§2.5: the observation reaches the note, and a conflict is said ──
#
# The ladder is `observed > declared > assumed > ""`, so an observation always wins on
# the VALUE. What varies is whether the user has to be told: overwriting the tool's own
# guess is the feature working, while overwriting the user's DECLARATION means their
# search's premise was wrong and they have no other way to find that out.


class _JdCache(DossierCache):
    """Serves one JD to every lead, through the real `get_or_build` so the observation
    is computed by production code rather than hand-fed. Subclasses rather than
    duck-types for `_RecordingCache`'s stated reason: `lead_id` must be stamped by the
    production `cache_key`."""
    def __init__(self, tmp_path, markdown):
        super().__init__(str(tmp_path / "dos"), 7,
                         lambda lead: {"jd": {"markdown": markdown}, "glassdoor": {}},
                         clock=lambda: datetime(2026, 7, 7))


def _rt_fields(role, *, role_type, source, status="new"):
    fm = ['company: "Acme"', f'role: "{role}"', 'location: "remote"', 'salary: ""',
          f'role_type: "{role_type}"', 'url: "https://x/y"', f"status: {status}",
          "score: 0", 'glassdoor_rating: ""', 'culture_flags: ""',
          'relevance_notes: ""']
    if source is not None:
        fm.append(f'role_type_source: "{source}"')
    return fm


_CONTRACT_JD = "x" * 400 + " This is a 6-month contract, outside IR35."


def _run_rt(tmp_path, fm_lines, *, jd=_CONTRACT_JD, dry_run=False):
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", fm_lines)
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, TriageConfig(), _Backend(), _JdCache(tmp_path, jd), audit,
                 statuses=("new",), dry_run=dry_run)
    return report, v.read_leads()[0]


def test_an_observation_fills_a_blank_role_type(tmp_path):
    report, note = _run_rt(tmp_path, _rt_fields("Analyst", role_type="", source=""))
    assert note.fm["role_type"] == "contract"
    assert note.fm["role_type_source"] == "observed"
    assert report.observed_role_types["filled"] == 1


def test_an_observation_corrects_the_tools_own_guess_without_a_conflict_line(tmp_path):
    # `assumed` is the search label #223 is about. Correcting it IS the feature, so it
    # is counted rather than announced -- a line per lead here would be most of them.
    report, note = _run_rt(
        tmp_path, _rt_fields("Analyst", role_type="permanent", source="assumed"))
    assert note.fm["role_type"] == "contract"
    assert note.fm["role_type_source"] == "observed"
    assert report.observed_role_types["corrected"] == 1
    assert report.role_type_conflicts == []


def test_a_declaration_the_posting_CONTRADICTS_is_reported_and_KEPT(tmp_path):
    """The user's assertion survives an observation that disagrees with it.

    §2.5 as designed put `observed` above `declared` outright, on the reasoning that the
    posting is ground truth about what the job IS. Three rounds of review then MEASURED
    the observer: 37% recall, and a residual false-positive rate on adverts whose subject
    is contracting. Overriding a declaration on that basis writes a guess over something
    the user actually asserted, permanently -- and stickily, since the next run sees the
    value agreeing with itself and skips.

    So an observation fills a blank and corrects the tool's own `assumed` guess, and a
    contradiction with a `declared` value is SURFACED instead. The user keeps their
    assertion and learns their search's premise may not hold for this posting; deciding
    between the two is theirs, and they are the only party who can.
    """
    report, note = _run_rt(
        tmp_path, _rt_fields("Analyst", role_type="permanent", source="declared"))
    assert note.fm["role_type"] == "permanent"        # kept
    assert note.fm["role_type_source"] == "declared"  # ...and still theirs
    assert report.observed_role_types["conflicted"] == 1
    assert len(report.role_type_conflicts) == 1
    said = report.role_type_conflicts[0]
    assert "permanent" in said and "contract" in said and "declared" in said


def test_a_declaration_the_posting_AGREES_with_is_not_a_conflict(tmp_path):
    report, note = _run_rt(
        tmp_path, _rt_fields("Analyst", role_type="contract", source="declared"))
    assert note.fm["role_type"] == "contract"
    assert report.role_type_conflicts == []
    # The counters too, not just the absence of a conflict line: asserting only what did
    # NOT happen left every bucket unpinned here, and the arm this row takes was filing
    # the lead under `filled` -- a label its own field comment defines as "had nothing".
    assert report.observed_role_types == {"filled": 0, "confirmed": 1, "corrected": 0,
                                          "conflicted": 0}


def test_a_posting_that_states_no_basis_leaves_the_declaration_alone(tmp_path):
    # Abstention must not be mistaken for an observation of "". A JD saying nothing
    # about basis is not evidence that the user's declaration is wrong.
    jd = "x" * 400 + " You will own the deployment pipeline."
    report, note = _run_rt(
        tmp_path, _rt_fields("Analyst", role_type="permanent", source="declared"), jd=jd)
    assert note.fm["role_type"] == "permanent"
    assert note.fm["role_type_source"] == "declared"
    assert report.observed_role_types == {"filled": 0, "confirmed": 0, "corrected": 0,
                                          "conflicted": 0}


def test_a_dry_run_observes_and_reports_but_writes_nothing(tmp_path):
    report, note = _run_rt(
        tmp_path, _rt_fields("Analyst", role_type="permanent", source="declared"),
        dry_run=True)
    assert note.fm["role_type"] == "permanent"
    assert note.fm["role_type_source"] == "declared"
    assert len(report.role_type_conflicts) == 1


def test_the_write_is_refused_once_the_lead_leaves_the_statuses_triage_owns(tmp_path):
    # `require_status=TRIAGE_OWNED`, and the reason is the read->write gap: a JD fetch
    # sits between reading the note and writing this field, and the lead may enter the
    # application lifecycle inside it. Read in under an explicit `--status applied`,
    # which is the only way to select one here.
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _rt_fields("Analyst", role_type="permanent", source="declared",
                                   status="applied"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    run(v, TriageConfig(), _Backend(), _JdCache(tmp_path, _CONTRACT_JD), audit,
        statuses=("applied",))
    assert v.read_leads()[0].fm["role_type"] == "permanent"


def test_a_conflict_is_recorded_in_the_durable_audit_log(tmp_path):
    # The run summary scrolls past; the audit log is what survives it.
    _run_rt(tmp_path, _rt_fields("Analyst", role_type="permanent", source="declared"))
    lines = open(str(tmp_path / "audit.jsonl")).read().strip().splitlines()
    entries = [json.loads(l) for l in lines]
    conflicts = [e for e in entries if e.get("stage") == "role_type"]
    assert len(conflicts) == 1
    # The entry records what the note still SAYS and what the posting read as -- in that
    # order, because the note was not changed. An earlier shape named the observation
    # `role_type` and the note's value `previous_role_type`, which described an override
    # that no longer happens.
    assert conflicts[0]["role_type"] == "permanent"
    assert conflicts[0]["role_type_source"] == "declared"
    assert conflicts[0]["observed_role_type"] == "contract"


def test_a_conflict_never_reaches_the_rejected_leads_note(tmp_path):
    # Same discipline `_resolve_audit` follows: a run that only observed a role_type
    # rejected nothing, so it must not start re-rendering a note about rejections.
    from sluice.triage.audit import _is_reject
    _run_rt(tmp_path, _rt_fields("Analyst", role_type="permanent", source="declared"))
    lines = open(str(tmp_path / "audit.jsonl")).read().strip().splitlines()
    for entry in (json.loads(l) for l in lines):
        if entry.get("stage") == "role_type":
            assert not _is_reject(entry)


# ── #223 §2.1: the mass re-verdict is announced before it is applied ──────────
#
# A note written before this feature carries no `role_type_source` key and reads as
# `assumed`, so the gate stops consulting its `role_type`. On an accumulated vault that
# is not one lead changing verdict -- it is a batch, all at once, on the first run after
# an upgrade. `dismiss` is not in `DEFAULT_TRIAGE_STATUSES`, so a lead dismissed that way
# is not re-selected and the user never sees it again.
#
# So the first run that WOULD apply it prints the affected leads and writes nothing.
# `--dry-run` alone is not sufficient: it requires the user to know to use it.


def _floors():
    cfg = TriageConfig(contract_floor_gbp_day=480, perm_floor_gbp=90_000)
    cfg.accept_titles, cfg.reject_titles = [], []
    return cfg


def _legacy_fields(role="Analyst", *, salary="£45,000", role_type="contract",
                   status="new"):
    # NO `role_type_source` key at all -- a note written before #223 ran. With an
    # UNMARKED salary, so the pre-#223 gate reached its day branch on `role_type` alone:
    # £45,000 clears the 480 day floor (keep) and falls under the 90,000 annual one
    # (reject). The branches disagree, which is what makes this lead affected.
    return ['company: "Acme"', f'role: "{role}"', 'location: "remote"',
            f'salary: "{salary}"', f'role_type: "{role_type}"', 'url: "https://x/y"',
            f"status: {status}", "score: 0", 'glassdoor_rating: ""', 'culture_flags: ""',
            'relevance_notes: ""']


def _run_legacy(tmp_path, fm_lines=None, *, dry_run=False, cfg=None):
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", fm_lines if fm_lines is not None else _legacy_fields())
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, cfg or _floors(), _Backend(), _cache(tmp_path), audit,
                 statuses=("new",), dry_run=dry_run)
    return report, v


def test_the_first_run_names_every_lead_whose_verdict_this_changes(tmp_path):
    report, v = _run_legacy(tmp_path)
    assert len(report.reverdict_pending) == 1
    said = report.reverdict_pending[0]
    # Derived from the store, never spelled out: `_note` seats the note by FILENAME, so
    # its slug is `acme`, not the `company - role` a hand-written expectation reaches
    # for. A user has to be able to find the lead the notice names.
    assert v.read_leads()[0].slug in said
    assert "keep -> reject" in said
    assert "Salary below floor: 45000 < 90000" in said


def test_the_first_run_writes_nothing_at_all(tmp_path):
    report, v = _run_legacy(tmp_path)
    assert v.read_leads()[0].status == "new"           # not dismissed
    assert report.judged == 0                          # and no judge call was paid for
    assert not os.path.exists(str(tmp_path / "audit.jsonl"))


def test_re_invoking_applies_the_change(tmp_path):
    # "unless re-invoked" -- the acknowledgement is the second run, not a flag the user
    # has to discover.
    _run_legacy(tmp_path)
    report, v = _run_legacy(tmp_path)
    assert report.reverdict_pending == []
    assert v.read_leads()[0].status == "dismiss"


def test_a_dry_run_announces_it_but_does_not_consume_the_acknowledgement(tmp_path):
    # `--dry-run` writes nothing, and that has to include the acknowledgement: a user
    # who happens to dry-run first must still get the notice on their real run.
    report, _ = _run_legacy(tmp_path, dry_run=True)
    assert len(report.reverdict_pending) == 1
    report, v = _run_legacy(tmp_path)
    assert len(report.reverdict_pending) == 1
    assert v.read_leads()[0].status == "new"


def test_a_vault_with_nothing_to_re_verdict_is_not_interrupted(tmp_path):
    # A day-marked salary reaches the same branch either way, so this lead's verdict
    # does not move and there is nothing to announce.
    report, v = _run_legacy(tmp_path, _legacy_fields(salary="£600/day"))
    assert report.reverdict_pending == []
    assert v.read_leads()[0].status != "new"           # the run proceeded normally


def test_an_uneventful_run_does_not_spend_the_acknowledgement(tmp_path):
    # The acknowledgement records that a NOTICE WAS SHOWN, never merely that a run
    # happened. Otherwise a user who syncs an old vault in later -- or configures a
    # floor for the first time -- is silently re-verdicted, which is the whole harm.
    _run_legacy(tmp_path, _legacy_fields(salary="£600/day"))
    report, v = _run_legacy(tmp_path)
    assert len(report.reverdict_pending) == 1
    assert v.read_leads()[0].status == "new"


def test_a_lead_rejected_for_a_reason_that_is_not_pay_is_not_affected(tmp_path):
    # Its verdict does not move, because the title reject fires before the pay check
    # ever runs. Counting it would inflate the notice with leads nothing changes for.
    cfg = _floors()
    cfg.reject_titles = ["analyst"]
    report, _ = _run_legacy(tmp_path, cfg=cfg)
    assert report.reverdict_pending == []


def test_a_lead_whose_provenance_is_already_recorded_is_not_affected(tmp_path):
    # Written by post-#223 ingest: the gate consults its role_type either way.
    fm = _legacy_fields() + ['role_type_source: "declared"']
    report, _ = _run_legacy(tmp_path, fm)
    assert report.reverdict_pending == []


# The two populations an APPROXIMATE legacy basis misses, and the reason
# `_legacy_pay_basis` spells the pre-#223 selector out verbatim instead. Both were
# written after a mutant that replaced it with "force this lead's role_type to be
# trusted and re-run the gate" SURVIVED every other row in this file -- the docstring
# asserted the shortcut under-reports, and nothing here could falsify it.
#
# Both flip keep -> reject, which is the direction that costs: `dismiss` is not
# re-selected, so an unannounced one is a lead the user never sees again.

def test_a_value_the_old_substring_test_read_as_contract_is_still_announced(tmp_path):
    # `"contract" in "contract-to-perm"` was True, so the old gate judged this lead on
    # the DAY floor. The closed set folds it to blank, so the new gate judges it as
    # annual. An approximation that forces the stored role_type to be trusted sees
    # `annual` on BOTH sides -- because the fold has already blanked it -- and reports
    # nothing.
    report, _ = _run_legacy(tmp_path, _legacy_fields(role_type="Contract-to-perm"))
    assert len(report.reverdict_pending) == 1
    assert "keep -> reject" in report.reverdict_pending[0]


def test_a_contract_label_beaten_by_an_annual_marker_is_still_announced(tmp_path):
    # The old gate checked `role_type` FIRST, so this lead went to the day floor. §2.3
    # reverses that: the salary's own marker wins, and it says annual. An approximation
    # forcing trust also sees `annual` on both sides, because the marker step runs
    # before the provenance step it is manipulating.
    report, _ = _run_legacy(
        tmp_path, _legacy_fields(salary="£45,000 per annum", role_type="contract"))
    assert len(report.reverdict_pending) == 1
    assert "keep -> reject" in report.reverdict_pending[0]


# ── #223 §2.5: the precedence ladder, in BOTH conflict directions ────────────
#
# Asserting one direction proves nothing about the other: a write-back that always wrote
# `contract`, or one that simply took whatever the JD's first marker said, would satisfy
# a single-direction pair. Each row pins the STORED `role_type`/`role_type_source` after
# the write AND the gate's verdict on the value it stored -- because storing the right
# token while the gate goes on judging the old one is the failure this whole issue is
# about, and neither assertion alone can see it.
#
# Each direction needs its OWN salary, and the asymmetry is not incidental. The lead has
# to SURVIVE the classify pass on its declared value -- §9's residual is that a lead the
# gate dismisses never reaches the dossier build, so it never gets observed at all -- and
# then move when the observation replaces that value. With contract_floor_gbp_day=480 and
# perm_floor_gbp=90_000:
#
#   £300    annual: keep (300 < _MIN_CREDIBLE_SALARY)   day: REJECT (50 <= 300 < 480)
#   £1,200  day:    keep (1200 >= 480)                  annual: REJECT (1000 <= 1200 < 90000)
#
# So a permanent declaration needs £300 and a contract one needs £1,200. An earlier draft
# used £1,200 for both, and the permanent row never reached the dossier: the declared
# value rejected it at the gate, and the test read that as "the write-back did not work".

_PERMANENT_JD = "x" * 400 + " This is a permanent position, 85,000 per annum."


def _declared_note(role_type, salary):
    return ['company: "Acme"', 'role: "Analyst"', 'location: "remote"',
            f'salary: "{salary}"', f'role_type: "{role_type}"',
            'role_type_source: "declared"', 'url: "https://x/y"', "status: new",
            "score: 0", 'glassdoor_rating: ""', 'culture_flags: ""',
            'relevance_notes: ""']


def _observe_over_declaration(tmp_path, *, declared, jd, salary):
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _declared_note(declared, salary))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    # The re-verdict notice is about notes with NO provenance; these have one, so it
    # never fires -- but acknowledging first keeps that fact out of the assertions.
    reverdict.acknowledge(str(tmp_path / 'vault'))
    report = run(v, _floors(), _Backend(), _JdCache(tmp_path, jd), audit,
                 statuses=("new",))
    return report, v.read_leads()[0]


def test_a_contradicting_posting_leaves_the_declared_verdict_standing(tmp_path):
    # The VERDICT half of the same decision, and the reason it matters: had the
    # observation been written, £300 would have moved from "kept as an implausible
    # salary" to "a day rate under the 480 floor" -- a reject the user never asked for,
    # off a 37%-recall lexical guess about prose.
    report, note = _observe_over_declaration(
        tmp_path, declared="permanent", jd=_CONTRACT_JD, salary="£300")
    assert note.fm["role_type"] == "permanent"
    assert report.observed_role_types["conflicted"] == 1
    assert classify(note.fm, _floors())[0] == "keep"


def test_the_other_direction_is_also_reported_and_kept(tmp_path):
    # Both directions, because a rule that only held one way would be satisfied by a
    # write-back that always wrote `permanent`. £1,200 stays a day rate under the user's
    # own declaration rather than being re-judged against the annual floor.
    report, note = _observe_over_declaration(
        tmp_path, declared="contract", jd=_PERMANENT_JD, salary="£1,200")
    assert note.fm["role_type"] == "contract"
    assert report.observed_role_types["conflicted"] == 1
    assert classify(note.fm, _floors())[0] == "keep"


def test_the_verdict_follows_the_STORED_value_not_the_one_that_was_declared(tmp_path):
    # The half a stored-fields-only assertion cannot see. Re-reading the note off disk,
    # rather than the in-memory `note.fm` the run mutated, is what proves the gate on
    # the NEXT run sees the observation -- §2.4's ordering limit means it cannot reach
    # the gate on the run that fetched it.
    _observe_over_declaration(tmp_path, declared="contract", jd=_PERMANENT_JD,
                              salary="£1,200")
    fresh = Vault(str(tmp_path / "vault")).read_leads()[0]
    assert fresh.fm["role_type_source"] == "declared"
    assert classify(fresh.fm, _floors())[0] == "keep"


@pytest.mark.parametrize("salary,basis", [("£2,000 per week", "week"),
                                          ("£1,500 per hour", "hour")])
def test_a_lead_the_old_gate_binned_on_the_annual_floor_is_announced_too(
        tmp_path, salary, basis):
    """The re-verdict notice covers the RECOVERING direction, not only the losing one.

    Before each basis had a floor of its own the basis was not parsed at all, so both of
    these met `perm_floor_gbp` and were rejected -- `£2,000 per week` is about £104k a
    year. They keep now. A user is told, because a lead the old gate dismissed sits at
    `dismiss`, which `DEFAULT_TRIAGE_STATUSES` does not re-select: nothing brings it back
    on its own, so the notice is the only place that pairing is visible.
    """
    report, _ = _run_legacy(
        tmp_path, _legacy_fields(salary=salary, role_type=""))
    assert len(report.reverdict_pending) == 1
    said = report.reverdict_pending[0]
    assert f"now judged as {basis}" in said
    assert "reject -> keep" in said


# ── review round 1: the four defects the reviewers found here ────────────────


def test_the_notice_names_every_affected_lead_even_under_limit(tmp_path):
    """The acknowledgement is ONE global marker, so its scope has to be the vault.

    Found independently by three reviewers. On the narrowed version: run 1 with
    `--limit 1` named one lead and spent the marker; run 2 named nothing and dismissed
    BOTH. `dismiss` is not in `DEFAULT_TRIAGE_STATUSES`, so the lead that was never named
    would not surface again -- the precise harm `triage/reverdict.py`'s own docstring
    claims to prevent.
    """
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _legacy_fields())
    _note(v, "beta.md", _legacy_fields(role="Second Analyst"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, _floors(), _Backend(), _cache(tmp_path), audit,
                 statuses=("new",), limit=1)
    assert len(report.reverdict_pending) == 2, (
        "`--limit` narrowed the notice but not the marker it spends")
    assert {n.status for n in v.read_leads()} == {"new"}


def test_the_notice_names_a_lead_outside_this_runs_status_selection(tmp_path):
    # Same defect through the other door. A first run over `--status new` must not
    # acknowledge on behalf of a `research` lead it never looked at.
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _legacy_fields())
    _note(v, "beta.md", _legacy_fields(role="Second Analyst", status="research"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, _floors(), _Backend(), _cache(tmp_path), audit, statuses=("new",))
    assert len(report.reverdict_pending) == 2


def test_a_marker_that_cannot_be_recorded_does_not_brick_triage(tmp_path, monkeypatch):
    """The livelock. Returning early on the strength of "they will see it again next
    run" is only sound while the marker is what makes the next run different. Against a
    read-only state directory it was not: the notice re-showed and the run returned early
    every time, forever, exiting 0 and looking like an idle run.

    So the run PROCEEDS, having printed the notice. The half of §2.1 that protects the
    user -- that they SEE the affected leads before it lands -- is kept; the half given
    up is "and nothing is written yet". Bricking triage is the worse trade.
    """
    monkeypatch.setattr(reverdict, "acknowledge", lambda *a, **kw: False)
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _legacy_fields())
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, _floors(), _Backend(), _cache(tmp_path), audit, statuses=("new",))
    assert len(report.reverdict_pending) == 1          # still told
    assert v.read_leads()[0].status == "dismiss"       # ...and not stuck
    assert any("could not be recorded" in f for f in report.failures)


def test_a_posting_that_agrees_is_confirmed_rather_than_filled(tmp_path):
    # `filled` is documented as "had nothing". A note that already said `contract` as
    # `declared` had something, and the posting agreeing with it is neither a correction
    # nor a conflict -- only the provenance strengthens.
    report, note = _run_rt(
        tmp_path, _rt_fields("Analyst", role_type="contract", source="declared"))
    assert note.fm["role_type_source"] == "observed"
    assert report.observed_role_types["confirmed"] == 1
    assert report.observed_role_types["filled"] == 0


@pytest.mark.parametrize("status", ["applied", "interview", "rejected", "accepted"])
def test_a_dry_run_does_not_report_a_write_require_status_would_refuse(tmp_path, status):
    # `wrote or dry_run` counted every dry-run observation, skipping the status guard the
    # real run applies. Measured before the fix: on these four statuses the real run
    # reported `conflicted: 0` and the dry run reported `conflicted: 1` plus a conflict
    # line -- a dry run predicting a write the vault refuses.
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _rt_fields("Analyst", role_type="permanent", source="declared",
                                   status=status))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    reverdict.acknowledge(str(tmp_path / 'vault'))
    report = run(v, TriageConfig(), _Backend(), _JdCache(tmp_path, _CONTRACT_JD), audit,
                 statuses=(status,), dry_run=True)
    assert report.observed_role_types["conflicted"] == 0
    assert report.role_type_conflicts == []


def test_the_held_run_and_the_applied_run_are_distinguishable(tmp_path, monkeypatch):
    """`reverdict_pending` is non-empty on BOTH arms, so it cannot also say which was
    taken. The CLI branched on it alone and told the user "WROTE NOTHING" over a run that
    had just dismissed every lead it named."""
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _legacy_fields())
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    held = run(v, _floors(), _Backend(), _cache(tmp_path), audit, statuses=("new",))
    assert held.reverdict_pending and held.reverdict_deferred is True
    assert v.read_leads()[0].status == "new"

    # ...and the arm where the marker could not be recorded, on a fresh vault so the
    # notice fires again.
    monkeypatch.setattr(reverdict, "acknowledge", lambda *a, **kw: False)
    v2 = Vault(str(tmp_path / "vault2"))
    _note(v2, "acme.md", _legacy_fields())
    applied = run(v2, _floors(), _Backend(), _cache(tmp_path), audit, statuses=("new",))
    assert applied.reverdict_pending and applied.reverdict_deferred is False
    assert v2.read_leads()[0].status == "dismiss"


def test_a_dry_run_is_held_rather_than_applied(tmp_path):
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _legacy_fields())
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    report = run(v, _floors(), _Backend(), _cache(tmp_path), audit, statuses=("new",),
                 dry_run=True)
    assert report.reverdict_deferred is True
