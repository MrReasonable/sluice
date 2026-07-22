"""A rejected lead cannot be dragged back onto the ladder.

Never-regress terminal, end to end. A `rejected` lead is a terminal; confirming
it forward to `offer` must be refused (`can_advance` returns False for a move out
of a terminal). This is reachable end to end only through `track_confirm` -- an
email rejection filters to _INFLIGHT and never reaches reconcile for a terminal
lead. The note must come back byte-for-byte unchanged.
"""
from tests.harness import ScriptedBackend, build_harness, seed_lead_note


def test_a_rejected_lead_cannot_be_dragged_back(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url="https://remoteok.example/x",
                      rows=[])
    note_path = seed_lead_note(h.paths["vault"], status="rejected", body="Application closed.")
    with open(note_path, encoding="utf-8") as f:
        before = f.read()

    app = h.sluice(ScriptedBackend())          # confirm makes no backend call
    slug = h.vault.read_leads()[0].slug
    out = app.track_confirm(lead=slug, to="offer")

    assert out["ok"] is False                                    # terminal refused
    assert h.vault.read_leads()[0].status == "rejected"          # status intact
    with open(note_path, encoding="utf-8") as f:
        after = f.read()
    assert after == before                                       # byte-for-byte
