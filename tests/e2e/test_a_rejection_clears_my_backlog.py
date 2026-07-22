"""Resolving a lead clears its follow-up backlog (#49).

Dead-letter durability end to end. Run 1 records a proposal (a matched, LOW-
confidence rejection -> action="proposed" with the lead slug set), so the store
holds a clearable entry -- asserted by `Entry.lead == slug`, the anti-vacuity
precondition (clear_lead on an empty store, or an entry keyed `lead=""`, is a
no-op). Run 2's HIGH-confidence rejection auto-advances the lead to `rejected`
and clears its entry. The two emails carry distinct message ids because the runs
share a persisted `seen` set.
"""
import os

from tests.harness import FakeGoogleClient, ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"

_APPLIED_NOTE = """---
base: "[[Job Leads.base]]"
company: "Example Foundry"
role: "Staff Engineer"
location: "Remote"
status: applied
score: 0
url: "https://remoteok.example/jobs/1"
applied_date: 2026-07-01
ats: example-ats
relevance_notes: ""
---

# Example Foundry - Staff Engineer

Application in flight.
"""

# Run 1: a soft/low-confidence rejection -> proposed (records a dead-letter entry).
# Run 2: a high-confidence rejection -> auto-advance to rejected (clears it).
_PROPOSAL = {"lead": "Example Foundry", "type": "rejection", "confidence": 0.5,
             "when": None, "links": [], "materials": [], "summary": "maybe not moving forward"}
_AUTO_REJECT = {"lead": "Example Foundry", "type": "rejection", "confidence": 0.95,
                "when": None, "links": [], "materials": [], "summary": "not moving forward"}


def _msg(subject, marker):
    return {"headers": {"from": "noreply@example.invalid", "subject": f"{subject} {marker}"},
            "body_text": f"Update on your application. {marker}",
            "thread_id": "th-1", "attachments": []}


def test_a_rejection_clears_my_backlog(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=[])
    leads_dir = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads_dir, exist_ok=True)
    with open(os.path.join(leads_dir, "Example Foundry - Staff Engineer.md"),
              "w", encoding="utf-8") as f:
        f.write(_APPLIED_NOTE)
    slug = h.vault.read_leads()[0].slug

    backend = ScriptedBackend(track_response=[("PROPOSAL-SIGNAL", _PROPOSAL),
                                              ("REJECTION-SIGNAL", _AUTO_REJECT)])
    app = h.sluice(backend)

    # ── run 1: a low-confidence rejection is PROPOSED and dead-lettered ──
    rep1 = app.track(client=FakeGoogleClient({"msg-1": _msg("Following up", "PROPOSAL-SIGNAL")}),
                     now_iso="2026-07-10T00:00:00+00:00")
    assert rep1.proposed == 1
    assert [e.lead for e in rep1.open_proposals] == [slug]   # a CLEARABLE entry exists
    assert h.vault.read_leads()[0].status == "applied"       # proposed never advances

    # ── run 2: a high-confidence rejection auto-advances AND clears the backlog ──
    rep2 = app.track(client=FakeGoogleClient({"msg-2": _msg("Decision", "REJECTION-SIGNAL")}),
                     now_iso="2026-07-15T00:00:00+00:00")
    assert rep2.auto == 1
    assert h.vault.read_leads()[0].status == "rejected"
    assert rep2.open_proposals == []                         # the backlog cleared
