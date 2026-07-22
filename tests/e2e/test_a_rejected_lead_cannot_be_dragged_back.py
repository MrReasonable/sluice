"""A rejected lead cannot be dragged back onto the ladder.

Never-regress terminal, end to end. A `rejected` lead is a terminal; confirming
it forward to `offer` must be refused (`can_advance` returns False for a move out
of a terminal). This is reachable end to end only through `track_confirm` -- an
email rejection filters to _INFLIGHT and never reaches reconcile for a terminal
lead. The note must come back byte-for-byte unchanged.
"""
import os

from tests.harness import ScriptedBackend, build_harness

_REJECTED_NOTE = """---
base: "[[Job Leads.base]]"
company: "Example Foundry"
role: "Staff Engineer"
location: "Remote"
status: rejected
score: 0
url: "https://remoteok.example/jobs/1"
applied_date: 2026-07-01
ats: example-ats
relevance_notes: ""
---

# Example Foundry - Staff Engineer

Application closed.
"""


def test_a_rejected_lead_cannot_be_dragged_back(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url="https://remoteok.example/x",
                      rows=[])
    leads_dir = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads_dir, exist_ok=True)
    note_path = os.path.join(leads_dir, "Example Foundry - Staff Engineer.md")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(_REJECTED_NOTE)
    before = open(note_path, encoding="utf-8").read()

    app = h.sluice(ScriptedBackend())          # confirm makes no backend call
    slug = h.vault.read_leads()[0].slug
    out = app.track_confirm(lead=slug, to="offer")

    assert out["ok"] is False                                    # terminal refused
    assert h.vault.read_leads()[0].status == "rejected"          # status intact
    assert open(note_path, encoding="utf-8").read() == before    # byte-for-byte
