"""#146. Our own event, found by its UID tag no matter how far it moved.

`_find_ours` searched a window centred on the NEW `ics.start`, so a reschedule moving an
interview by more than `calendar_lookahead_days` (default 45) put our own tagged event
outside it. `sync_event` then answered `created`, INSERTED A DUPLICATE, and orphaned the
original at the old time -- untagged as stale, so the operator's calendar showed the
interview twice and neither entry said which was real. On a cancel the same miss skipped the
delete entirely and left the cancelled interview in the calendar.

The UID is the idempotency key across reschedules -- the module docstring has always said so
-- and the lookup was bounded by a window derived from the value that just changed.
"""
from datetime import datetime, timezone

from sluice.track.calendar_sync import sync_event
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent
from tests.test_track_google_client import FakeGoogleClient

# 78 days after the original, i.e. comfortably past the 45-day default lookahead in both
# directions: the window centred on the new start does not reach back to the old one.
_OLD = "2026-07-15T10:00:00+00:00"
_NEW = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


def _ours(uid="u1", start_iso=_OLD, event_id="ev1"):
    return {"id": event_id, "start": {"dateTime": start_iso},
            "extendedProperties": {"private": {"sluice-track-uid": uid}}}


def _ics(uid="u1", start=None, cancelled=False):
    e = IcsEvent(uid=uid, summary="Screen", start=start or _NEW)
    if cancelled:
        e.method = "CANCEL"
    return e


def test_the_fixture_really_is_out_of_window():
    """The premise every test in this file rests on, asserted rather than assumed.

    If the old event were inside the window the plain `list_events` scan would find it and
    each test below would pass without the tag query existing at all -- green for a reason
    that has nothing to do with #146."""
    c = FakeGoogleClient(events=[_ours()])
    sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics())
    lo, hi = c.listed[0]
    assert not (lo <= _OLD <= hi), (
        f"the old event at {_OLD} sits INSIDE the window {lo}..{hi}, so nothing here "
        "exercises the out-of-window lookup")


def test_a_reschedule_beyond_the_lookahead_MOVES_our_event_instead_of_duplicating_it():
    c = FakeGoogleClient(events=[_ours()])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "updated"
    assert not c.inserted, "a second entry for the same interview is the #146 harm itself"
    assert [eid for eid, _ in c.updated] == ["ev1"], "the original must be the thing that moved"
