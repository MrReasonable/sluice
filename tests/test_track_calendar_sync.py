from datetime import datetime, timezone
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent
from sluice.track.calendar_sync import sync_event
from tests.test_track_google_client import FakeGoogleClient


def _ics(uid="u1", start=None, cancelled=False):
    e = IcsEvent(uid=uid, summary="Screen", start=start or datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                 end=datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc))
    if cancelled:
        e.method = "CANCEL"
    return e


def _tagged_event(uid, start_iso, event_id="ev1"):
    return {"id": event_id, "start": {"dateTime": start_iso},
            "extendedProperties": {"private": {"sluice-track-uid": uid}}}


def test_insert_when_absent():
    c = FakeGoogleClient(events=[])
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=_ics()) == "created"
    assert c.inserted and c.inserted[0]["extendedProperties"]["private"]["sluice-track-uid"] == "u1"


def test_present_when_same_uid_same_time():
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T10:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=_ics()) == "present"
    assert not c.inserted


def test_update_on_reschedule_same_uid_new_time():
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T09:00:00+00:00")])
    new = _ics(start=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=new) == "updated"
    assert c.updated and not c.inserted


def test_match_google_auto_added_by_start_proximity():
    # No sluice tag; Google already added the invite at the same start -> no duplicate,
    # and the foreign event must never be inserted/updated/deleted (safety).
    c = FakeGoogleClient(events=[{"id": "g1", "start": {"dateTime": "2026-07-15T10:10:00+00:00"}}])
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=_ics()) == "present"
    assert not c.inserted and not c.updated and not c.deleted


def test_foreign_event_never_updated_on_reschedule():
    # An untagged event near the OLD time must not be updated when our ics has a new time.
    c = FakeGoogleClient(events=[{"id": "foreign", "start": {"dateTime": "2026-07-15T10:05:00+00:00"}}])
    new = _ics(start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=new) == "present"
    assert not c.updated and not c.inserted and not c.deleted


def test_foreign_event_never_deleted_on_cancel():
    c = FakeGoogleClient(events=[{"id": "foreign", "start": {"dateTime": "2026-07-15T10:00:00+00:00"}}])
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=_ics(cancelled=True)) == "present"
    assert not c.deleted


def test_cancel_removes_matched():
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T10:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=_ics(cancelled=True)) == "cancelled"
    assert c.deleted == ["ev1"]


def test_naive_ics_start_no_crash_and_present():
    from datetime import datetime
    # floating-time (naive) DTSTART; an unrelated untagged aware event is iterated first
    # (would crash the proximity subtraction pre-fix), then the matching tagged event.
    naive = IcsEvent(uid="u1", summary="Screen",
                     start=datetime(2026, 7, 15, 10, 0), end=datetime(2026, 7, 15, 10, 30))
    c = FakeGoogleClient(events=[
        {"id": "other", "start": {"dateTime": "2026-07-16T09:00:00+00:00"}},   # untagged, iterated first
        _tagged_event("u1", "2026-07-15T10:00:00+00:00"),                       # same instant as naive->UTC
    ])
    assert sync_event(c, TrackConfig(), lead_slug="flowline", ics=naive) == "present"
    assert not c.updated and not c.inserted
