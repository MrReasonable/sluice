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


class TwoReceiptClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "r1": {"headers": {"from": "jobs@example.com", "subject": "Thanks for applying"},
                   "body_text": "received", "thread_id": "t", "attachments": []},
            "r2": {"headers": {"from": "jobs@example.com", "subject": "Application received"},
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
