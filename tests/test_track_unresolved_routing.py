"""What happens to work we could NOT complete, once it leaves `sync_event`.

Wave 1 taught `sync_event` to say `unresolved` and wave 1's review found that saying it was
the easy half. Five of seven reviewers independently traced the same gap: `reconcile` learned
what `unresolved` means in the CANCEL branch and not in the SCHEDULING branch, so a refused
insert advanced the status, booked nothing, wrote no row, and let `seen.add` consume the
message. `failures=0 calendar_added=0` -- indistinguishable from a message carrying no invite.

The unit tests for that path stopped at `sync_event`'s return value, which is precisely why
they could not see it. Everything here drives `engine.run` end to end.
"""
import json
import pathlib
from datetime import datetime, timezone

from sluice.track import engine as E
from sluice.track.config import TrackConfig
from sluice.track.deadletter import EV_TYPE_CALENDAR, EV_TYPE_FAILURE, Entry
from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault

_ICS = (b"BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
        b"DTSTART:20260715T100000Z\r\nSUMMARY:Screen\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")

_INTERVIEW = json.dumps({"lead": "Example Tidal - Analyst", "type": "interview",
                         "confidence": 0.9, "when": None, "links": [], "materials": [],
                         "summary": "invite"})


class _TruncatedClient(OneMsgClient):
    """A client whose calendar window is short and SAYS so, carrying an invite."""

    def __init__(self, truncated=True):
        super().__init__()
        self.truncated = truncated

    def get_message(self, mid):
        msg = super().get_message(mid)
        msg["attachments"] = [{"filename": "invite.ics", "mime": "text/calendar", "data": _ICS}]
        return msg


def _run(client, backend_json=_INTERVIEW, status="applied", seen=None, dl=None, day="10"):
    v, path = _vault(status)
    dl = dl or _dl()
    seen = seen if seen is not None else set()
    rep = E.run(v, TrackConfig(), client, FakeBackend(backend_json), seen=seen, deadletter=dl,
                now_iso=f"2026-07-{day}T12:00:00+00:00")
    return rep, dl, seen, pathlib.Path(path).read_text()


def test_a_refused_insert_is_RECORDED_even_though_the_status_advanced():
    """The critical one. `action` came out as `applied`, which records nothing."""
    rep, dl, seen, note = _run(_TruncatedClient())
    assert rep.calendar_added == 0, "nothing was booked"
    assert "status: interview" in note, "the interview is real, so the advance is right"
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, ("the calendar entry was never created and NOTHING recorded it -- "
                  "seen.add consumed the message and the interview is not in the calendar")
    assert rows[0].proposal == "calendar-unresolved"
    assert "u1" in rows[0].hint, "the hint must name the uid so the entry can be found"


def test_the_refused_insert_hint_does_not_claim_the_entry_exists():
    _rep, dl, _seen, _note = _run(_TruncatedClient())
    hint = [e for e in dl.open_entries() if e.message_id == "m1"][0].hint
    assert "by hand" in hint, hint


def test_a_COMPLETE_window_still_books_and_records_nothing():
    # The quiet path stays quiet: crying wolf on every invite is how a real row gets ignored.
    rep, dl, _seen, _note = _run(_TruncatedClient(truncated=False))
    assert rep.calendar_added == 1
    assert [e for e in dl.open_entries() if e.message_id == "m1"] == []


def test_the_refused_insert_row_is_a_CALENDAR_row_not_a_status_proposal():
    """`clear_lead` fires on any later advance for the same lead, unfiltered by kind."""
    _rep, dl, _seen, _note = _run(_TruncatedClient())
    assert [e for e in dl.open_entries() if e.message_id == "m1"][0].ev_type == EV_TYPE_CALENDAR


def test_an_unrelated_later_advance_does_NOT_delete_the_calendar_row():
    """The row says "remove it from your calendar by hand". A rejection email arriving weeks
    later advanced the same lead, `clear_lead` deleted every row for that lead, and the
    message was already in `seen` -- so the instruction was gone and never re-derived."""
    dl = _dl()
    dl.record(Entry(message_id="m9", lead="Example Tidal - Analyst", candidates="",
                    ev_type=EV_TYPE_CALENDAR, proposal="cancel-unresolved",
                    hint="remove it by hand", first_seen="2026-07-10", times_surfaced=1))
    assert dl.clear_lead("Example Tidal - Analyst") == 0, "a calendar row is not a status proposal"
    assert [e.message_id for e in dl.open_entries()] == ["m9"]


def test_clear_lead_STILL_clears_an_ordinary_status_proposal():
    # The behaviour the filter must not break.
    dl = _dl()
    dl.record(Entry(message_id="m8", lead="Example Tidal - Analyst", candidates="",
                    ev_type="interview", proposal="interview", hint="confirm",
                    first_seen="2026-07-10", times_surfaced=1))
    assert dl.clear_lead("Example Tidal - Analyst") == 1
    assert dl.open_entries() == []


# ---- a failure row must not outlive its own resolution -----------------------------------

class _FlakyOnce(_TruncatedClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._fail = True

    def get_message(self, mid):
        if self._fail:
            self._fail = False
            raise RuntimeError("transient 503")
        return super().get_message(mid)


def test_a_failure_row_is_cleared_when_the_retry_ADVANCES_the_lead():
    """`applied` records nothing, so the clear used to miss it entirely.

    One transient Gmail 500 left a row that survived its own resolution, re-surfacing every
    run with `times_surfaced` climbing, for a message now in `seen` and never reprocessed.
    `clear_lead` structurally cannot reach it -- the failure Entry is written with `lead=""`.
    """
    dl = _dl()
    client = _FlakyOnce(truncated=False)
    _rep, _dl1, _seen, _note = _run(client, dl=dl, day="10")
    assert [e.ev_type for e in dl.open_entries() if e.message_id == "m1"] == [EV_TYPE_FAILURE]

    _rep2, _dl2, _seen2, note = _run(client, dl=dl, day="11")
    assert "status: interview" in note, "run 2 must actually advance"
    assert [e for e in dl.open_entries() if e.ev_type == EV_TYPE_FAILURE] == [], (
        "the failure row outlived the retry that resolved it")


def test_a_failure_row_is_cleared_when_the_retry_is_pure_NOISE():
    """`skipped` records nothing either -- the second of the two missed outcomes."""
    dl = _dl()

    class _FlakyNoise(OneMsgClient):
        def __init__(self):
            super().__init__()
            self._fail = True

        def get_message(self, mid):
            if self._fail:
                self._fail = False
                raise RuntimeError("transient 503")
            return super().get_message(mid)

    client = _FlakyNoise()
    _run(client, backend_json="{}", dl=dl, day="10")
    assert [e.ev_type for e in dl.open_entries() if e.message_id == "m1"] == [EV_TYPE_FAILURE]

    _run(client, backend_json="{}", dl=dl, day="11")
    assert [e for e in dl.open_entries() if e.ev_type == EV_TYPE_FAILURE] == [], (
        "a message that processed has resolved its failure, whatever the outcome was")


# ---- re-recording a message must not wedge the watermark ---------------------------------

def test_a_DIFFERING_proposal_row_on_re_processing_does_not_stall_the_watermark():
    """`record`'s raise, applied to the engine re-recording its OWN message.

    `_save_seen` runs after the whole run, so any crash between a record and that save leaves
    an open row for an un-`seen` message. Re-processing then re-derives the row, and a
    `proposal` string embedding a model confidence differs between runs -- the routine shape.
    That hit the raise: `deadletter_error` on every run, so `_save_lastrun` never advanced and
    `_gmail_query`'s `after:` widened without bound, unrecoverable without hand-editing sqlite.
    """
    dl = _dl()
    dl.record(Entry(message_id="m1", lead="Example Tidal - Analyst", candidates="",
                    ev_type="interview", proposal="interview (conf 0.55)",
                    hint="stale hint from a run that never saved seen",
                    first_seen="2026-07-09", times_surfaced=4))

    rep, _dl2, _seen, _note = _run(OneMsgClient(), backend_json=json.dumps(
        {"lead": "", "type": "interview", "confidence": 0.55, "when": None, "links": [],
         "materials": [], "summary": "invite"}), dl=dl, day="10")

    assert rep.deadletter_error is False, (
        "the engine re-recording its own message wedged the lastrun watermark permanently")
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert len(rows) == 1, "one message, one row"


def test_replace_PRESERVES_first_seen_and_times_surfaced():
    """An UPDATE, not a delete-and-reinsert.

    `first_seen` is how long a row has been open and `times_surfaced` is how often it has been
    shown; both are the store's own bookkeeping, not the caller's to reset. Resetting them
    would make an old unresolved row look brand new every time it was re-derived.
    """
    dl = _dl()
    dl.record(Entry(message_id="m1", lead="a", candidates="", ev_type="interview",
                    proposal="old", hint="old", first_seen="2026-07-01", times_surfaced=9))
    dl.record(Entry(message_id="m1", lead="b", candidates="", ev_type="offer",
                    proposal="new", hint="new", first_seen="2026-07-20", times_surfaced=1),
              replace=True)
    row = dl.open_entries()[0]
    assert (row.proposal, row.hint, row.ev_type, row.lead) == ("new", "new", "offer", "b")
    assert row.first_seen == "2026-07-01", "first_seen is the store's, not the caller's"
    assert row.times_surfaced == 9, "times_surfaced moves under bump_surfaced only"


def test_replace_is_OPT_IN_so_a_foreign_collision_still_raises():
    """The guard exists for a caller clobbering a row it did not write. Only the engine
    re-recording its own message is entitled to replace."""
    import pytest

    dl = _dl()
    dl.record(Entry(message_id="m1", lead="a", candidates="", ev_type="interview",
                    proposal="p", hint="h", first_seen="2026-07-01", times_surfaced=1))
    with pytest.raises(ValueError) as exc:
        dl.record(Entry(message_id="m1", lead="b", candidates="", ev_type="offer",
                        proposal="q", hint="i", first_seen="2026-07-01", times_surfaced=1))
    assert "m1" in str(exc.value)


# ---- the startless arm ---------------------------------------------------------------------

def test_a_startless_NON_cancel_is_unresolved_rather_than_present():
    """`present` is defined as "we searched a complete window and there was nothing of ours".

    Nothing was searched -- there is no DTSTART to build a window from. Unreachable today
    because `reconcile` guards the only non-cancel call site, so this pins the VALUE rather
    than a live path: the wrong one sits waiting for a third caller.
    """
    from sluice.track.calendar_sync import sync_event
    from sluice.track.ics import IcsEvent
    from tests.test_track_google_client import FakeGoogleClient

    c = FakeGoogleClient(events=[])
    ics = IcsEvent(uid="u1", summary="Screen", start=None)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "unresolved"
    assert c.listed == [], "it must not have searched at all"


def test_a_startless_CANCEL_is_still_unresolved():
    from sluice.track.calendar_sync import sync_event
    from sluice.track.ics import IcsEvent
    from tests.test_track_google_client import FakeGoogleClient

    ics = IcsEvent(uid="u1", summary="Screen", start=None)
    ics.method = "CANCEL"
    assert sync_event(FakeGoogleClient(events=[]), TrackConfig(),
                      lead_slug="example-lead", ics=ics) == "unresolved"


def test_a_real_start_still_reaches_the_calendar():
    # The guard must not swallow the ordinary path.
    from sluice.track.calendar_sync import sync_event
    from sluice.track.ics import IcsEvent
    from tests.test_track_google_client import FakeGoogleClient

    c = FakeGoogleClient(events=[])
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "created"
