import os
from sluice.core.vault import Vault


def _write_note(vault, name, fm_lines, body="body\n"):
    leads = os.path.join(vault.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n" + body)


def test_read_leads_filters_and_normalizes_status(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "A.md", ['company: "Acme"', 'role: "Analyst"', "status: new"])
    _write_note(v, "B.md", ['company: "Beta"', 'role: "Analyst"', 'status: "dismissed"'])
    _write_note(v, "C.md", ['company: "Gamma"', 'role: "Analyst"', "status: applied"])

    new = v.read_leads({"new"})
    assert [n.fm["company"] for n in new] == ["Acme"]

    # 'dismissed' normalizes to 'dismiss' for filtering
    assert len(v.read_leads({"dismiss"})) == 1
    assert v.read_leads({"dismiss"})[0].status == "dismiss"

    assert len(v.read_leads()) == 3  # no filter -> all


def test_update_fields_sets_values_preserves_body_and_is_idempotent(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "A.md",
                ['company: "Acme"', "status: new", "score: 0",
                 'relevance_notes: ""'],
                body="# Acme\n\nDetailed body text.\n")
    path = v.read_leads()[0].path

    v.update_fields(path, {"status": "dismiss", "score": "20"},
                    append_note="[triage] IC role.", note_tag="[triage]")
    note = v.read_leads()[0]
    assert note.status == "dismiss"
    assert note.fm["score"] == "20"
    assert "[triage] IC role." in note.fm["relevance_notes"]
    assert "Detailed body text." in note.body  # body preserved

    # second identical apply does not duplicate the note annotation
    v.update_fields(path, {"status": "dismiss"},
                    append_note="[triage] IC role.", note_tag="[triage]")
    assert v.read_leads()[0].fm["relevance_notes"].count("[triage]") == 1

    # a key that did not exist gets added
    v.update_fields(path, {"glassdoor_rating": '"3.9"'})
    assert v.read_leads()[0].fm["glassdoor_rating"] == "3.9"


def test_normalize_all_statuses(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "A.md", ['company: "A"', 'status: "dismissed"'])   # value drift
    _write_note(v, "B.md", ['company: "B"', "status: new"])           # already ok
    _write_note(v, "C.md", ['company: "C"', 'status: "Researching"']) # value drift
    _write_note(v, "D.md", ['company: "D"', 'status: "new"'])         # quoting drift

    dry = v.normalize_all_statuses(dry_run=True)
    assert dry["changed"] == 3
    assert v.read_leads()[0].fm["status"] in ("dismissed", "dismiss")  # unwritten

    real = v.normalize_all_statuses(dry_run=False)
    assert real["changed"] == 3
    statuses = sorted(n.status for n in v.read_leads())
    assert statuses == ["dismiss", "new", "new", "research"]
    # canonical form is unquoted, for both value-drift and quoting-drift notes
    dismiss_raw = open(v.read_leads({"dismiss"})[0].path).read()
    assert "status: dismiss" in dismiss_raw and 'status: "dismiss"' not in dismiss_raw
    d_raw = open(os.path.join(v.leads_dir, "D.md")).read()
    assert "status: new" in d_raw and 'status: "new"' not in d_raw


def test_normalize_collapses_consistent_duplicate_status_lines(tmp_path):
    v = Vault(str(tmp_path))
    # legacy corruption: two status lines, same value, mixed quoting
    _write_note(v, "dup.md",
                ['company: "A"', "status: dismiss", "score: 0",
                 'culture_flags: ""', 'status: "dismiss"'])
    summary = v.normalize_all_statuses(dry_run=False)
    assert summary["changed"] == 1
    raw = open(os.path.join(v.leads_dir, "dup.md")).read()
    assert raw.count("status:") == 1                     # collapsed to one line
    assert "status: dismiss" in raw and 'status: "dismiss"' not in raw


def test_normalize_flags_conflicting_status_without_touching(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "conflict.md",
                ['company: "B"', "status: dismiss", 'status: "shortlist"'])
    summary = v.normalize_all_statuses(dry_run=False)
    assert ("conflict.md", ["dismiss", "shortlist"]) in summary["conflicts"]
    raw = open(os.path.join(v.leads_dir, "conflict.md")).read()
    # left untouched: both original lines still present
    assert "status: dismiss" in raw and 'status: "shortlist"' in raw
