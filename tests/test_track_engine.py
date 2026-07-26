import json, tempfile, pathlib, sqlite3
import pytest
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from sluice.track.config import TrackConfig
from sluice.track import engine as E
from sluice.track.deadletter import DeadLetterDb, Entry
from tests.test_track_google_client import FakeGoogleClient


def _dl():
    return DeadLetterDb(str(pathlib.Path(tempfile.mkdtemp(), "track-seen.db.deadletter.db")))


def _vault(status="applied"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / "Tidemark - Analyst.md").write_text(f'---\ncompany: "Tidemark"\nrole: "Analyst"\nstatus: {status}\n---\n\nBODY\n')
    return Vault(root), str(leads / "Tidemark - Analyst.md")


class OneMsgClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={"m1": {"headers": {"from": "jobs@tidemark.com", "subject": "Interview"},
                                           "body_text": "We'd like to interview you", "thread_id": "t1",
                                           "attachments": []}}, events=[])


class OneMsgRejectClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={"m1": {"headers": {"from": "jobs@tidemark.com", "subject": "Update"},
                                           "body_text": "an update on your application", "thread_id": "t1",
                                           "attachments": []}}, events=[])


class TwoMsgClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "mA": {"headers": {"from": "jobs@tidemark.com", "subject": "Update"},
                   "body_text": "unfortunately not moving forward", "thread_id": "t", "attachments": []},
            "mB": {"headers": {"from": "jobs@tidemark.com", "subject": "Interview"},
                   "body_text": "we would like to interview you", "thread_id": "t", "attachments": []},
        }, events=[])


class SeqBackend:
    def __init__(self, replies): self.replies = list(replies); self.i = 0
    def complete(self, prompt):
        r = self.replies[self.i]; self.i = min(self.i + 1, len(self.replies) - 1); return r


class FakeBackend:
    def __init__(self, reply): self.reply = reply
    def complete(self, prompt): return self.reply


def test_run_auto_advances_and_reports():
    v, path = _vault("applied")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                                 "when": "2026-07-20T10:00", "links": [], "materials": [], "summary": "interview"}))
    seen = set()
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=seen, deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.msgs == 1 and rep.auto == 1
    assert "status: interview" in pathlib.Path(path).read_text()
    assert "m1" in seen


def test_run_skips_seen_and_dry_run_writes_nothing():
    v, path = _vault("applied")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "x"}))
    assert E.run(v, TrackConfig(), OneMsgClient(), be, seen={"m1"}, deadletter=_dl(),
                 now_iso="2026-07-10T12:00:00+00:00").msgs == 0
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00", dry_run=True)
    assert rep.msgs == 1 and "status: applied" in pathlib.Path(path).read_text()


def test_run_resilient_to_bad_message():
    v, _ = _vault("applied")
    class Boom(OneMsgClient):
        def get_message(self, mid): raise RuntimeError("gmail hiccup")
    rep = E.run(v, TrackConfig(), Boom(), FakeBackend("{}"), seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.failures == 1  # did not raise


def test_confirm_never_clobber():
    v, path = _vault("interview")
    assert E.confirm(v, TrackConfig(), "Tidemark - Analyst", "offer", deadletter=_dl())["ok"] is True
    assert "status: offer" in pathlib.Path(path).read_text()
    assert E.confirm(v, TrackConfig(), "Tidemark - Analyst", "phone_screen", deadletter=_dl())["ok"] is False  # backward refused


def test_same_lead_two_messages_no_regression():
    v, path = _vault("applied")
    be = SeqBackend([
        json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.95,
                    "when": None, "links": [], "materials": [], "summary": "rejected"}),
        json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                    "when": "2026-07-20T10:00", "links": [], "materials": [], "summary": "interview"}),
    ])
    E.run(v, TrackConfig(), TwoMsgClient(), be, seen=set(), deadletter=_dl(),
          now_iso="2026-07-10T12:00:00+00:00")
    assert "status: rejected" in pathlib.Path(path).read_text()  # NOT reverted to interview


def test_proposal_carries_real_confirm_command():
    v, _ = _vault("phone_screen")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.6,
                                 "when": None, "links": [], "materials": [], "summary": "soft"}))
    rep = E.run(v, TrackConfig(), OneMsgRejectClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.open_proposals
    assert "--to rejected" in rep.open_proposals[0].hint
    assert "<status>" not in rep.open_proposals[0].hint


def test_gmail_query_uses_since_iso():
    from sluice.track.engine import _gmail_query
    q = _gmail_query(TrackConfig(), "2026-07-10T12:00:00+00:00", since_iso="2026-07-08T00:00:00+00:00")
    assert "after:2026/07/08" in q


def test_update_proposal_has_no_broken_command():
    v, _ = _vault("applied")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "update", "confidence": 0.8,
                                 "when": None, "links": [], "materials": [], "summary": "under review"}))
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.open_proposals
    assert "<status>" not in rep.open_proposals[0].hint
    assert "review" in rep.open_proposals[0].hint.lower()  # a manual-review note, not a fake command


def test_offer_stage_lead_is_in_flight():
    v, path = _vault("offer")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.95,
                                 "when": None, "links": [], "materials": [], "summary": "withdrawn"}))
    E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=_dl(),
          now_iso="2026-07-10T12:00:00+00:00")
    assert "status: rejected" in pathlib.Path(path).read_text()


def test_unmatched_proposal_has_no_fake_lead_command():
    v, _ = _vault("applied")
    be = FakeBackend(json.dumps({"lead": "Zzz", "type": "rejection", "confidence": 0.6,
                                 "when": None, "links": [], "materials": [], "summary": "soft"}))
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.open_proposals
    assert '--lead "?"' not in rep.open_proposals[0].hint
    assert '--lead "Zzz"' not in rep.open_proposals[0].hint


def test_dry_run_previews_without_writing():
    v, path = _vault("applied")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "interview", "confidence": 0.9,
                                 "when": "2026-07-20T10:00", "links": [], "materials": [], "summary": "iv"}))
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00", dry_run=True)
    assert rep.classified == 1 and rep.auto == 1        # previewed the auto-advance
    assert "status: applied" in pathlib.Path(path).read_text()  # but nothing written


class TwoSoftRejectClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "mA": {"headers": {"from": "jobs@tidemark.com", "subject": "Update"},
                   "body_text": "an update on your application", "thread_id": "t", "attachments": []},
            "mB": {"headers": {"from": "jobs@tidemark.com", "subject": "Update"},
                   "body_text": "an update on your application", "thread_id": "t", "attachments": []},
        }, events=[])


def _soft_reject_backend():
    # low confidence -> reconcile returns `proposed`, not an auto-advance
    return FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.6,
                                   "when": None, "links": [], "materials": [], "summary": "soft"}))


def test_proposal_survives_across_runs_until_dismissed():
    v, _ = _vault("phone_screen")
    dl = _dl()
    seen = set()
    r1 = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
               seen=seen, deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")
    assert r1.open_proposals and r1.open_proposals[0].times_surfaced == 1
    # run 2: the message is in `seen` (skipped), but the dead-letter re-surfaces it, bumped
    r2 = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
               seen=seen, deadletter=dl, now_iso="2026-07-11T12:00:00+00:00")
    assert r2.msgs == 0                                   # message skipped (in seen)
    assert r2.open_proposals and r2.open_proposals[0].times_surfaced == 2
    # dismiss clears it; run 3 shows an empty backlog
    dl.clear_id(r2.open_proposals[0].message_id)
    r3 = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
               seen=seen, deadletter=dl, now_iso="2026-07-12T12:00:00+00:00")
    assert r3.open_proposals == []


def test_times_surfaced_mixed_carried_and_new_in_one_run():
    v, _ = _vault("phone_screen")
    dl = _dl()
    # run 1: mB pre-seen, so only mA is new -> record mA (times_surfaced=1)
    E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
          seen={"mB"}, deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")
    # run 2 (fresh seen): mA carried (in seen), mB now new -> bump mA->2, record mB->1
    r2 = E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
               seen={"mA"}, deadletter=dl, now_iso="2026-07-11T12:00:00+00:00")
    got = {e.message_id: e.times_surfaced for e in r2.open_proposals}
    assert got == {"mA": 2, "mB": 1}


def test_dry_run_unions_persisted_and_computed_new_without_recording():
    v, _ = _vault("phone_screen")
    dl = _dl()
    # persist mA via a real run (mB pre-seen so only mA records)
    E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
          seen={"mB"}, deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")
    # dry-run (fresh seen): mA carried (in seen), mB new -> union shows both, records nothing
    r = E.run(v, TrackConfig(), TwoSoftRejectClient(), _soft_reject_backend(),
              seen={"mA"}, deadletter=dl, now_iso="2026-07-11T12:00:00+00:00", dry_run=True)
    ids = sorted(e.message_id for e in r.open_proposals)
    assert ids == ["mA", "mB"]                            # mB appears (computed-new)
    assert [e.message_id for e in dl.open_entries()] == ["mA"]  # ...but was NOT recorded


class BoomRecordDL(DeadLetterDb):
    def record(self, entry):
        raise sqlite3.OperationalError("disk full")


def test_record_failure_skips_seen_so_message_reprocesses():
    v, _ = _vault("phone_screen")
    seen = set()
    rep = E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
                seen=seen, deadletter=BoomRecordDL(_dl().path),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.failures == 1        # the raise was caught per-message
    assert "m1" not in seen         # ...and seen.add was skipped -> re-processes next run
    assert rep.deadletter_error is True  # ...and app.py must hold the lastrun watermark (F3)


class BoomBumpDL(DeadLetterDb):
    def bump_surfaced(self):
        raise sqlite3.OperationalError("disk full")


def test_bump_failure_aborts_run_before_any_save():
    # bump_surfaced() runs OUTSIDE the per-message try (before the loop even
    # starts), so a failure there must propagate out of run() entirely rather
    # than being caught -- app.py never reaches _save_seen/_save_lastrun. This
    # pins the existing fail-safe (no code change; a no-op bump on a missing
    # db would hide the bug, so seed the store first).
    v, _ = _vault("phone_screen")
    dl = _dl()
    _seed(dl, mid="m_old")
    boom = BoomBumpDL(dl.path)
    with pytest.raises(Exception):
        E.run(v, TrackConfig(), OneMsgRejectClient(), _soft_reject_backend(),
              seen=set(), deadletter=boom, now_iso="2026-07-10T12:00:00+00:00")


def _seed(dl, mid="m1", lead="Tidemark - Analyst", candidates=""):
    dl.record(Entry(message_id=mid, lead=lead, candidates=candidates, ev_type="rejection",
                    proposal="soft", hint="h", first_seen="2026-07-10", times_surfaced=1))


def test_confirm_clears_dead_letter_on_success():
    v, _ = _vault("phone_screen")
    dl = _dl(); _seed(dl)
    out = E.confirm(v, TrackConfig(), "Tidemark - Analyst", "interview", deadletter=dl)
    assert out["ok"] is True
    assert dl.open_entries() == []                 # the lead's proposals are resolved


def test_confirm_dry_run_does_not_clear():
    v, _ = _vault("phone_screen")
    dl = _dl(); _seed(dl)
    E.confirm(v, TrackConfig(), "Tidemark - Analyst", "interview", deadletter=dl, dry_run=True)
    assert len(dl.open_entries()) == 1             # a preview clears nothing


def test_confirm_refused_advance_does_not_clear():
    v, _ = _vault("interview")
    dl = _dl(); _seed(dl)
    out = E.confirm(v, TrackConfig(), "Tidemark - Analyst", "phone_screen", deadletter=dl)  # backward
    assert out["ok"] is False
    assert len(dl.open_entries()) == 1             # a refused confirm must NOT delete the row


def test_confirm_returns_conflict_on_vault_conflict(monkeypatch):
    # #16 Task 6: a sustained write-race in update_fields must not escape confirm()
    # as an unhandled traceback -- it becomes a first-class refused outcome. CRITICAL:
    # clear_lead must not run on a conflicted write (that would be #49's silent loss
    # on the clear path), so also assert the dead-letter row survives untouched.
    v, _ = _vault("interview")
    dl = _dl(); _seed(dl)

    def boom(*a, **k):
        raise VaultConflict("x")
    monkeypatch.setattr(v, "update_fields", boom)

    out = E.confirm(v, TrackConfig(), "Tidemark - Analyst", "offer", deadletter=dl)
    assert out == {"ok": False, "reason": "conflict"}
    assert len(dl.open_entries()) == 1             # NOT cleared on a conflicted write


def test_confirm_lead_does_not_clear_ambiguous_candidates_entry():
    v, _ = _vault("phone_screen")
    dl = _dl()
    _seed(dl, mid="mAmb", lead="", candidates="Tidemark - Analyst,Other - Role")  # ambiguous: lead=""
    E.confirm(v, TrackConfig(), "Tidemark - Analyst", "interview", deadletter=dl)
    assert len(dl.open_entries()) == 1             # exact-match clear misses it; dismiss --id clears it


def test_auto_advance_clears_dead_letter_for_that_lead():
    v, _ = _vault("applied")
    dl = _dl()
    _seed(dl, mid="m_old", lead="Tidemark - Analyst")   # a pending soft-proposal from an earlier run
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.95,
                                 "when": None, "links": [], "materials": [], "summary": "rejected"}))
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.auto == 1                    # the lead auto-advanced (applied)
    assert dl.open_entries() == []          # ...and its pending proposal was cleared


def test_auto_advance_dry_run_does_not_clear():
    v, _ = _vault("applied")
    dl = _dl()
    _seed(dl, mid="m_old", lead="Tidemark - Analyst")
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.95,
                                 "when": None, "links": [], "materials": [], "summary": "rejected"}))
    E.run(v, TrackConfig(), OneMsgClient(), be, seen=set(), deadletter=dl,
          now_iso="2026-07-10T12:00:00+00:00", dry_run=True)
    assert len(dl.open_entries()) == 1      # a dry-run preview clears nothing


class BoomClearDL(DeadLetterDb):
    def clear_lead(self, slug):
        raise sqlite3.OperationalError("disk full")


def test_clear_failure_holds_watermark():
    # Mirrors test_auto_advance_clears_dead_letter_for_that_lead, but the store's
    # clear_lead raises instead of succeeding: _dl_write must set deadletter_error
    # and re-raise into the per-message `except`, so seen.add is skipped and
    # app.py (per #49) holds the lastrun watermark rather than losing the message.
    v, _ = _vault("applied")
    dl = BoomClearDL(_dl().path)
    _seed(dl, mid="m_old", lead="Tidemark - Analyst")  # record() is inherited and still works;
                                                        # only clear_lead is overridden to fail
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "rejection", "confidence": 0.95,
                                 "when": None, "links": [], "materials": [], "summary": "rejected"}))
    seen = set()
    rep = E.run(v, TrackConfig(), OneMsgClient(), be, seen=seen, deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.auto == 1  # the lead DID auto-advance (action=="applied"), reaching clear_lead
    assert rep.deadletter_error is True
    assert rep.failures == 1
    assert "m1" not in seen


def test_non_deadletter_error_does_not_set_flag():
    # Anti-over-reach pin for _dl_write's scope: a per-message failure that has
    # nothing to do with the dead-letter store (a Gmail get_message hiccup) must
    # NOT set deadletter_error, or a transient Gmail error would wrongly hold the
    # whole watermark alongside genuine dead-letter write failures.
    v, _ = _vault("applied")

    class Boom(OneMsgClient):
        def get_message(self, mid): raise RuntimeError("gmail hiccup")

    rep = E.run(v, TrackConfig(), Boom(), FakeBackend("{}"), seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.failures == 1
    assert rep.deadletter_error is False


def _vault_shortlist(url, status="shortlist"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / "Example - Analyst.md").write_text(
        f'---\ncompany: "Example"\nrole: "Analyst"\nurl: "{url}"\nstatus: {status}\n---\n\nBODY\n')
    return Vault(root), str(leads / "Example - Analyst.md")


# What a delivering server writes for a genuine, aligned DKIM signature on example.com.
# Required for the proof tier: an unauthenticated From domain is a claim, not evidence,
# so without this header these receipts only PROPOSE (see
# test_receipt_without_sender_authentication_does_not_advance).
_AUTH_OK = "mx.example.invalid; dkim=pass header.i=@example.com header.b=Zm9v"


class TwoReceiptClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "r1": {"headers": {"from": "jobs@example.com", "subject": "Thanks for applying",
                               "authentication-results": _AUTH_OK},
                   "body_text": "received", "thread_id": "t", "attachments": []},
            "r2": {"headers": {"from": "jobs@example.com", "subject": "Application received",
                               "authentication-results": _AUTH_OK},
                   "body_text": "received", "thread_id": "t", "attachments": []},
        }, events=[])


def test_confirm_to_applied_from_shortlist_and_refused_otherwise():
    v, path = _vault_shortlist("https://example.com/careers/1")
    res = E.confirm(v, TrackConfig(), "Example - Analyst", "applied", deadletter=_dl())
    assert res["ok"] and res["to"] == "applied"
    assert "status: applied" in pathlib.Path(path).read_text()
    # a non-shortlist lead is refused with its status as the reason
    v2, _ = _vault_shortlist("https://example.com/m", status="interview")
    res2 = E.confirm(v2, TrackConfig(), "Example - Analyst", "applied", deadletter=_dl())
    assert res2["ok"] is False and res2["reason"] == "interview"


def test_two_receipts_same_lead_one_run_advance_once():
    # The second receipt in one run sees the REFLECTED `applied` snapshot -> no-op.
    v, path = _vault_shortlist("https://example.com/careers/1")
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    E.run(v, TrackConfig(), TwoReceiptClient(), be, seen=set(), deadletter=_dl(),
          now_iso="2026-07-25T09:00:00+00:00")
    text = pathlib.Path(path).read_text()
    assert "status: applied" in text
    assert text.count("## Application receipt") == 1


def test_two_receipts_same_lead_one_run_records_no_deadletter_row():
    # Counting evidence sections (above) cannot see this: the second receipt used to be
    # PROPOSED, recording a dead-letter row whose hint is
    # `track confirm --lead "..." --to applied` -- a command confirm() refuses forever
    # (reason: applied). Worse, clear_lead had already run for message 1, so the row was
    # recorded AFTER the clear and re-surfaced on every future run with no way to act on
    # it. reconcile now skips a matched note that can_apply rules out.
    v, path = _vault_shortlist("https://example.com/careers/1")
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    dl = _dl()
    rep = E.run(v, TrackConfig(), TwoReceiptClient(), be, seen=set(), deadletter=dl,
                now_iso="2026-07-25T09:00:00+00:00")
    assert dl.open_entries() == []
    assert rep.auto == 1 and rep.proposed == 0
    assert pathlib.Path(path).read_text().count("## Application receipt") == 1


def test_dry_run_receipt_advance_does_not_mutate_the_status_snapshot():
    # The in-run status reflection mirrors what was WRITTEN TO DISK. A dry run writes
    # nothing, so mutating the snapshot would make the preview lie about the vault's
    # real state -- reporting the second receipt as a no-op against an `applied` status
    # that exists nowhere. Both receipts therefore preview the advance, and the note is
    # byte-unchanged. (Pins the `not dry_run` guard on the reflection, which no test
    # drove: dropping it left the suite green.)
    v, path = _vault_shortlist("https://example.com/careers/1")
    before = pathlib.Path(path).read_text()
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    rep = E.run(v, TrackConfig(), TwoReceiptClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-25T09:00:00+00:00", dry_run=True)
    assert rep.auto == 2
    assert pathlib.Path(path).read_text() == before


# ── Fix round 1 (adversarial review) ────────────────────────────────────────


class OneReceiptClient(FakeGoogleClient):
    def __init__(self, auth=_AUTH_OK):
        headers = {"from": "jobs@example.com", "subject": "Thanks for applying"}
        if auth is not None:
            headers["authentication-results"] = auth
        super().__init__(messages={
            "r1": {"headers": headers,
                   "body_text": "received", "thread_id": "t", "attachments": []},
        }, events=[])


def test_receipt_proof_advance_regression_guard():
    # Finding 2's fix touches the same "receipt, tier X, action Y" dispatch this single-
    # message proof-grade advance already exercised -- pin that the new "tier none, LLM
    # fallback" branch cannot accidentally intercept a genuine proof-tier advance.
    v, path = _vault_shortlist("https://example.com/careers/1")
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    rep = E.run(v, TrackConfig(), OneReceiptClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    text = pathlib.Path(path).read_text()
    assert rep.auto == 1
    assert "status: applied" in text
    assert text.count("## Application receipt") == 1


def _cfg_with_example_ats():
    """TrackConfig whose ATS denylist also carries a reserved placeholder host, merged in
    exactly as a user's own in-house relay would be. Lets the ATS-relay fixtures below
    exercise the relay path without naming a real vendor."""
    cfg = TrackConfig()
    cfg.ats_relay_domains = {**cfg.ats_relay_domains, "ats.example.invalid": "example-ats"}
    return cfg


def test_receipt_without_sender_authentication_does_not_advance():
    # The whole point of the authentication conjunct, asserted where the WRITE happens
    # rather than only on the matcher's return value: an RFC 5322 `From` domain is free
    # text, so a forged "thanks for applying" from the lead's own domain used to walk
    # straight through the proof tier and mark the lead `applied` -- silently suppressing
    # a real application, irreversibly. Same message as the advance case above with the
    # Authentication-Results header REMOVED: the note must be BYTE-UNCHANGED (no status
    # write, no evidence section) and the signal must survive as a proposal a human
    # confirms, not disappear.
    v, path = _vault_shortlist("https://example.com/careers/1")
    before = pathlib.Path(path).read_text()
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    dl = _dl()
    rep = E.run(v, TrackConfig(), OneReceiptClient(auth=None), be, seen=set(), deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.auto == 0 and rep.proposed == 1
    assert pathlib.Path(path).read_text() == before
    assert len(dl.open_entries()) == 1
    assert '--to applied' in dl.open_entries()[0].hint   # runnable: the lead is still shortlist


def test_receipt_with_unaligned_authentication_does_not_advance():
    # A PASS for a domain the attacker controls is still a pass. Only ALIGNMENT with the
    # sender host makes it evidence about this sender, and the note must stay untouched
    # without it.
    v, path = _vault_shortlist("https://example.com/careers/1")
    before = pathlib.Path(path).read_text()
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    unaligned = "mx.example.invalid; dkim=pass header.i=@attacker.invalid header.b=Zm9v"
    rep = E.run(v, TrackConfig(), OneReceiptClient(auth=unaligned), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.auto == 0 and rep.proposed == 1
    assert pathlib.Path(path).read_text() == before


class CorrobReceiptClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            # An ATS-relay sender (not the lead's own domain) whose body/subject names the
            # company -- corroborated, not proof, so reconcile proposes rather than advances.
            "r1": {"headers": {"from": "jobs@ats.example.invalid", "subject": "Your Example application"},
                   "body_text": "received", "thread_id": "t", "attachments": []},
        }, events=[])


def test_receipt_proposal_carries_real_confirm_command():
    # Finding 1: _PROPOSE_TARGET["receipt"] = "applied" was untested and a reviewer proved it
    # inert (deleting it left the full suite green). A corroborated receipt resolves a real
    # lead_slug (via match_receipt, not the LLM), so its dead-letter hint must be the runnable
    # `--to applied` command, mirroring test_proposal_carries_real_confirm_command for the
    # non-receipt event types.
    v, _ = _vault_shortlist("https://example.com/careers/1")
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    rep = E.run(v, _cfg_with_example_ats(), CorrobReceiptClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.open_proposals
    assert "--to applied" in rep.open_proposals[0].hint
    assert "<status>" not in rep.open_proposals[0].hint


class InFlightReceiptClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            # Sender domain matches nothing (Tidemark carries no `url` at all in `_vault`),
            # so match_receipt (shortlist-only) can never find this lead -- it is already
            # `applied`, past shortlist. The LLM's own guess is the only signal available.
            # The domain itself is arbitrary for that purpose -- example.com (RFC 2606
            # reserved) keeps this branch's new fixtures out of real-firm-name territory.
            "r1": {"headers": {"from": "jobs@example.com", "subject": "Thanks for applying"},
                   "body_text": "received", "thread_id": "t", "attachments": []},
        }, events=[])


def test_receipt_about_inflight_lead_surfaces_without_writing():
    # Finding 2: a receipt about a lead that has already advanced PAST shortlist can never
    # match_receipt-match (it only searches shortlist_by_slug) -- reproduced by the reviewer
    # as a genuine #40-class silent loss (a mislabelled rejection would vanish with the lead
    # stuck at `applied` forever, zero signal anywhere). The LLM's own name resolution
    # (llm_lead_slug) must surface exactly one dead-letter row naming the lead, WITHOUT
    # writing to the note at all -- no status change, no evidence section.
    v, path = _vault("applied")  # Tidemark, url-less, already in-flight
    before = pathlib.Path(path).read_text()
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    dl = _dl()
    rep = E.run(v, TrackConfig(), InFlightReceiptClient(), be, seen=set(), deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")
    entries = dl.open_entries()
    assert len(entries) == 1
    assert entries[0].lead == "Tidemark - Analyst"          # named, not blank/ambiguous
    assert "--to applied" not in entries[0].hint             # can_apply is False -- no fake command
    assert rep.auto == 0 and rep.proposed == 1
    assert pathlib.Path(path).read_text() == before          # byte-unchanged: no write at all


def test_receipt_deadletter_fallback_records_nothing_in_dry_run():
    # The receipt fallback's own `if not dry_run:` guard had no test driving it: replacing
    # it with `if True:` left the suite green, so a `--dry-run` preview would have written
    # a durable dead-letter row. The row must be PREVIEWED (rep.open_proposals) and not
    # persisted, and the note must stay byte-unchanged.
    v, path = _vault("applied")
    before = pathlib.Path(path).read_text()
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    dl = _dl()
    rep = E.run(v, TrackConfig(), InFlightReceiptClient(), be, seen=set(), deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00", dry_run=True)
    assert dl.open_entries() == []                     # nothing durably recorded
    assert len(rep.open_proposals) == 1                # but still previewed
    assert rep.proposed == 1 and rep.auto == 0
    assert pathlib.Path(path).read_text() == before


class UnmatchedReceiptClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "r1": {"headers": {"from": "jobs@example.invalid", "subject": "Receipt"},
                   "body_text": "no useful info here", "thread_id": "t", "attachments": []},
        }, events=[])


def test_receipt_matching_nothing_stays_quiet():
    # Finding 2's other half: an untracked job's ordinary receipt (no shortlist match AND the
    # LLM names no known lead either) must stay quiet -- surfacing every such receipt would
    # be exactly the noise the author's ruling explicitly rejected.
    v, path = _vault("applied")
    before = pathlib.Path(path).read_text()
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    dl = _dl()
    rep = E.run(v, TrackConfig(), UnmatchedReceiptClient(), be, seen=set(), deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")
    assert dl.open_entries() == []
    assert rep.auto == 0 and rep.proposed == 0
    assert pathlib.Path(path).read_text() == before


def _vault_two_inflight_same_company():
    # Two DIFFERENT leads sharing a company name, both already in-flight (past shortlist) --
    # the LLM's own "Tidemark" guess cannot resolve uniquely between them (_resolve_lead's
    # existing ambiguous branch), mirroring test_ambiguous_match_sets_candidates_and_no_slug's
    # vault shape but for the fallback fields (#10 fix-round-2).
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    p1 = leads / "Tidemark - Analyst.md"
    p2 = leads / "Tidemark - Associate.md"
    p1.write_text('---\ncompany: "Tidemark"\nrole: "Analyst"\nstatus: applied\n---\n\nBODY\n')
    p2.write_text('---\ncompany: "Tidemark"\nrole: "Associate"\nstatus: applied\n---\n\nBODY\n')
    return Vault(root), str(p1), str(p2)


def test_receipt_ambiguous_inflight_fallback_surfaces_both_candidates():
    # Fix round 2: an AMBIGUOUS llm fallback is still a known-lead signal (the email
    # demonstrably concerns two real, tracked leads) and must surface too -- only a fallback
    # resolving to NOTHING stays quiet. One dead-letter row naming BOTH candidates, neither
    # note touched at all.
    v, path1, path2 = _vault_two_inflight_same_company()
    before1, before2 = pathlib.Path(path1).read_text(), pathlib.Path(path2).read_text()
    be = FakeBackend(json.dumps({"lead": "Tidemark", "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    dl = _dl()
    rep = E.run(v, TrackConfig(), InFlightReceiptClient(), be, seen=set(), deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")
    entries = dl.open_entries()
    assert len(entries) == 1
    assert set(entries[0].candidates.split(",")) == {"Tidemark - Analyst", "Tidemark - Associate"}
    assert "--to applied" not in entries[0].hint   # both candidates are in-flight -- can_apply False
    assert rep.auto == 0 and rep.proposed == 1
    assert pathlib.Path(path1).read_text() == before1   # neither note written
    assert pathlib.Path(path2).read_text() == before2
