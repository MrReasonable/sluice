"""Triage never touches a lead that has entered the application lifecycle.

`status` is one key with two owners: triage owns new/shortlist/research/... and
the tracker owns applied/phone_screen/.../rejected. `triage/apply.py::_guarded`
is the seam that keeps triage's writers off an application-owned lead. This drives
that guard through the real composition root: an `applied` lead is fed to a triage
pass (whose stubbed judge would otherwise shortlist it), and its status and
application provenance must come back untouched.

This closes a real gap: the guard was unwitnessed end to end -- the existing
triage-engine tests only ever seed `new` leads, so nothing exercised the
application-owned branch.
"""
from tests.harness import ScriptedBackend, build_harness, seed_lead_note


def test_triage_leaves_my_application(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url="https://remoteok.example/x",
                      rows=[])
    note_path = seed_lead_note(h.paths["vault"], status="applied", body="Application in flight.")
    before = open(note_path, encoding="utf-8").read()

    # A judge that shortlists everything it is handed -- so if the guard let this
    # applied lead through, its status WOULD be rewritten to shortlist.
    app = h.sluice(ScriptedBackend(default_verdict="shortlist"))
    # statuses=("applied",) forces triage to READ the application-owned lead; it
    # is the guard, not the read filter, that must keep it safe.
    report = app.triage(statuses=("applied",))

    note = h.vault.read_leads()[0]
    assert note.status == "applied"                       # never regressed
    assert "applied_date: 2026-07-01" in open(note_path, encoding="utf-8").read()
    assert open(note_path, encoding="utf-8").read() == before  # byte-for-byte
    # The judge did run and returned a verdict; the guard is what refused to apply it.
    assert report.judged == 1
