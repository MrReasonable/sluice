from datetime import datetime, timedelta, timezone
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent, parse_ics
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
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "created"
    assert c.inserted and c.inserted[0]["extendedProperties"]["private"]["sluice-track-uid"] == "u1"


def test_present_when_same_uid_same_time():
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T10:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "present"
    assert not c.inserted


def test_update_on_reschedule_same_uid_new_time():
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T09:00:00+00:00")])
    new = _ics(start=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=new) == "updated"
    assert c.updated and not c.inserted


def test_match_google_auto_added_by_start_proximity():
    # No sluice tag; Google already added the invite at the same start -> no duplicate,
    # and the foreign event must never be inserted/updated/deleted (safety).
    c = FakeGoogleClient(events=[{"id": "g1", "start": {"dateTime": "2026-07-15T10:10:00+00:00"}}])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "present"
    assert not c.inserted and not c.updated and not c.deleted


def test_foreign_event_never_updated_on_reschedule():
    # An untagged event near the OLD time must not be updated when our ics has a new time.
    c = FakeGoogleClient(events=[{"id": "foreign", "start": {"dateTime": "2026-07-15T10:05:00+00:00"}}])
    new = _ics(start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=new) == "present"
    assert not c.updated and not c.inserted and not c.deleted


def test_foreign_event_never_deleted_on_cancel():
    c = FakeGoogleClient(events=[{"id": "foreign", "start": {"dateTime": "2026-07-15T10:00:00+00:00"}}])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics(cancelled=True)) == "present"
    assert not c.deleted


def test_cancel_removes_matched():
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T10:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics(cancelled=True)) == "cancelled"
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
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=naive) == "present"
    assert not c.updated and not c.inserted


def test_naive_ics_start_still_sends_offset_bearing_window_bounds():
    # The sibling above proves the naive-vs-aware COMPARISON survives; it cannot catch this,
    # because the fake ignores the bounds it is handed. `events.list` requires RFC 3339, so a
    # bound built from a naive datetime serialises as "2026-05-31T10:00:00" -- no offset --
    # and is rejected with HTTP 400, which escapes reconcile and engine.run drops the whole
    # message. Assert on the ARGUMENTS, which is where the defect actually lives.
    cfg = TrackConfig()
    naive = IcsEvent(uid="u1", summary="Screen",
                     start=datetime(2026, 7, 15, 10, 0), end=datetime(2026, 7, 15, 10, 30))
    c = FakeGoogleClient(events=[])
    sync_event(c, cfg, lead_slug="example-lead", ics=naive)
    assert c.listed, "list_events was never called, so the bounds were never exercised"
    for lo, hi in c.listed:
        lo_dt, hi_dt = datetime.fromisoformat(lo), datetime.fromisoformat(hi)
        # utcoffset(), not `tzinfo is not None`: the invariant the API actually needs is that
        # isoformat() EMITS an offset, and a tzinfo whose utcoffset() is None is "aware" by
        # the weaker test while still serialising bare.
        assert lo_dt.utcoffset() is not None, f"timeMin has no UTC offset: {lo}"
        assert hi_dt.utcoffset() is not None, f"timeMax has no UTC offset: {hi}"
        # A window has to be a window. Neither of these is implied by the offset check:
        # swapping the bounds, or collapsing them to zero width, left the suite green.
        assert lo_dt < hi_dt, f"timeMin must precede timeMax, got {lo} > {hi}"
        assert hi_dt - lo_dt == 2 * timedelta(days=cfg.calendar_lookahead_days)


def test_windows_tzid_invite_is_inserted_with_its_real_offset_and_zone():
    # The parse-level tests pin `GMT Standard Time` -> +01:00, but nothing pinned that the
    # resolved zone REACHES the calendar body -- which is where the whole table earns its
    # keep. Deleting _event_body's zone derivation (always stamping "UTC") left the suite
    # green, so this is the test that makes the stated purpose falsifiable.
    ics = parse_ics("BEGIN:VEVENT\r\nUID:u1\r\n"
                    "DTSTART;TZID=GMT Standard Time:20260715T110000\r\nEND:VEVENT")
    c = FakeGoogleClient(events=[])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "created"
    assert c.inserted[0]["start"] == {"dateTime": "2026-07-15T11:00:00+01:00",
                                      "timeZone": "Europe/London"}


def test_floating_start_is_booked_as_utc_but_says_so_loudly(caplog):
    # The fix traded a loud HTTP 400 for a quiet wrong hour: before the bounds were coerced
    # this input could not reach a write at all. It books at the guessed instant now, so the
    # guess has to be audible -- otherwise every signal (no failure, an ordinary-looking
    # entry) says success while the hour is wrong.
    ics = parse_ics("BEGIN:VEVENT\r\nUID:u1\r\n"
                    "DTSTART;TZID=Nowhere/Notreal:20260715T110000\r\nEND:VEVENT")
    c = FakeGoogleClient(events=[])
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "created"
    assert c.inserted[0]["start"]["timeZone"] == "UTC"      # the guess itself is unchanged
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.track.calendar_sync"]
    assert any("Nowhere/Notreal" in m for m in said), f"the unresolved zone is not named: {said}"
    assert any("u1" in m for m in said), f"the uid is not named: {said}"
    assert any("ASSUMING UTC" in m for m in said), f"the assumption is not stated: {said}"


def test_resolved_zone_and_present_outcome_stay_silent(caplog):
    # The warning must not cry wolf. A resolved zone is not a guess, and a `present` outcome
    # wrote nothing at all -- warning on either would train the reader to ignore the line.
    resolved = parse_ics("BEGIN:VEVENT\r\nUID:u1\r\n"
                         "DTSTART;TZID=GMT Standard Time:20260715T110000\r\nEND:VEVENT")
    naive = IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 15, 10, 0))
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        sync_event(FakeGoogleClient(events=[]), TrackConfig(), lead_slug="example-lead", ics=resolved)
        # naive, but an untagged event already covers the slot -> "present", nothing written
        sync_event(FakeGoogleClient(events=[{"id": "g1", "start": {"dateTime": "2026-07-15T10:00:00+00:00"}}]),
                   TrackConfig(), lead_slug="example-lead", ics=naive)
    assert [r.getMessage() for r in caplog.records
            if r.name == "sluice.track.calendar_sync"] == []
