import json
from datetime import date
from sluice.triage.audit import AuditLog, render_rejected_note
from sluice.core.vault import Vault


def test_append_writes_one_json_line_per_entry(tmp_path):
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    log.append({"slug": "a", "stage": "classify", "decision": "reject",
                "reason": "IC role", "ts": "2026-07-07"})
    log.append({"slug": "b", "stage": "judge", "verdict": "shortlist",
                "ts": "2026-07-07"})
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["reason"] == "IC role"


def test_read_recent_filters_by_age(tmp_path):
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    log.append({"slug": "old", "ts": "2026-01-01"})
    log.append({"slug": "new", "ts": "2026-07-07"})
    recent = log.read_recent(30, clock=lambda: date(2026, 7, 8))
    assert [e["slug"] for e in recent] == ["new"]


def test_render_rejected_note_groups_rejects(tmp_path):
    v = Vault(str(tmp_path))
    entries = [
        {"slug": "a", "company": "Acme", "role": "Director", "url": "u1",
         "stage": "classify", "decision": "reject", "reason": "m-of-m",
         "score": 0, "ts": "2026-07-07"},
        {"slug": "b", "company": "Beta", "role": "Analyst", "url": "u2",
         "stage": "judge", "verdict": "dismiss", "reason": "weak fit",
         "score": 20, "ts": "2026-07-07"},
        {"slug": "c", "company": "Gamma", "role": "Analyst", "url": "u3",
         "stage": "judge", "verdict": "shortlist", "score": 80, "ts": "2026-07-07"},
    ]
    path = render_rejected_note(v, entries, "Job Applications/Rejected Leads Audit.md")
    text = open(path, encoding="utf-8").read()
    assert "Acme" in text and "Beta" in text
    assert "Gamma" not in text          # shortlist is not a reject
    assert "\u2014" not in text         # no em dashes
