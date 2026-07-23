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
