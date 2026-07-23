import tempfile, pathlib, re
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from sluice.apply.config import ApplyConfig
from sluice.apply import record as rec


def _lead(fm):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / "Northwind - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")
    return Vault(root)


_SHORTLIST = ('company: "Northwind"\nrole: "Analyst"\nstatus: shortlist\n'
              'url: "https://northwind.example/x"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')


def test_record_flips_shortlist_to_applied_and_stamps():
    v = _lead(_SHORTLIST)
    note = v.read_leads({"shortlist"})[0]
    out = rec.record(v, note, ApplyConfig(), ats="greenhouse", url="https://x/apply")
    assert out["ok"] is True
    text = pathlib.Path(note.ref).read_text()
    assert "status: applied" in text
    assert re.search(r"applied_date: \d{4}-\d\d-\d\d", text)
    assert "ats: greenhouse" in text
    assert "applied_cv: CV_deadbeef.pdf" in text
    assert 'applied_url: "https://x/apply"' in text
    assert "BODY" in text  # body preserved


def test_record_refuses_application_owned():
    v = _lead(_SHORTLIST.replace("status: shortlist", "status: interviewing"))
    # read all (not just shortlist) to get the note
    note = [n for n in v.read_leads() if n.fm["company"] == "Northwind"][0]
    out = rec.record(v, note, ApplyConfig())
    assert out["ok"] is False
    assert "status: applied" not in pathlib.Path(note.ref).read_text()


def test_record_dry_run_writes_nothing():
    v = _lead(_SHORTLIST)
    note = v.read_leads({"shortlist"})[0]
    out = rec.record(v, note, ApplyConfig(), dry_run=True)
    assert out["ok"] is True and out["fields"]["status"] == "applied"
    assert "status: shortlist" in pathlib.Path(note.ref).read_text()  # untouched


def test_record_returns_conflict_on_vault_conflict(monkeypatch):
    # #16 Task 6: a sustained write-race in update_fields must not escape record()
    # as an unhandled traceback -- it becomes a first-class refused outcome, same
    # shape as every other refusal this function returns.
    v = _lead(_SHORTLIST)
    note = v.read_leads({"shortlist"})[0]

    def boom(*a, **k):
        raise VaultConflict("x")
    monkeypatch.setattr(v, "update_fields", boom)

    out = rec.record(v, note, ApplyConfig(), ats="greenhouse", url="https://x/apply")
    assert out == {"ok": False, "reason": "conflict"}
    assert "status: applied" not in pathlib.Path(note.ref).read_text()  # untouched
