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
import os

from tests.harness import ScriptedBackend, build_harness

_APPLIED_NOTE = """---
base: "[[Job Leads.base]]"
company: "Example Foundry"
role: "Staff Engineer"
location: "Remote"
status: applied
score: 0
url: "https://remoteok.example/jobs/1"
applied_date: 2026-07-01
ats: greenhouse
relevance_notes: ""
---

# Example Foundry - Staff Engineer

Application in flight.
"""


def test_triage_leaves_an_application_owned_lead_untouched(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url="https://remoteok.example/x",
                      rows=[])
    leads_dir = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads_dir, exist_ok=True)
    note_path = os.path.join(leads_dir, "Example Foundry - Staff Engineer.md")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(_APPLIED_NOTE)
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
