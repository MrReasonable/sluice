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
    #
    # Reports `foreign`, not `present`. The SAFETY property is unchanged and is what the
    # `not c.inserted` line pins; what changed is the report. `present` is defined as "we
    # searched a complete window and there was nothing of ours", and this is the opposite --
    # we found something and deliberately left it alone. Conflating the two meant an interview
    # was never booked, the status advanced anyway, and `seen.add` consumed the message.
    c = FakeGoogleClient(events=[{"id": "g1", "start": {"dateTime": "2026-07-15T10:10:00+00:00"}}])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "foreign"
    assert not c.inserted and not c.updated and not c.deleted


def test_foreign_event_never_updated_on_reschedule():
    # An untagged event near the OLD time must not be updated when our ics has a new time.
    c = FakeGoogleClient(events=[{"id": "foreign", "start": {"dateTime": "2026-07-15T10:05:00+00:00"}}])
    new = _ics(start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=new) == "foreign"
    assert not c.updated and not c.inserted and not c.deleted


def test_foreign_event_never_deleted_on_cancel():
    # Safety unchanged: we never delete an event we did not create. But the operator's
    # calendar still shows a cancelled interview -- routinely the recruiter's own invite,
    # which Google auto-adds from the mail -- so `foreign` routes it to a human instead of
    # the quiet `present` that told them nothing.
    c = FakeGoogleClient(events=[{"id": "foreign", "start": {"dateTime": "2026-07-15T10:00:00+00:00"}}])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics(cancelled=True)) == "foreign"
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
    # because the fake does not VALIDATE the bounds it is handed. (It does read them now --
    # `FakeGoogleClient.list_events` filters on them since #146 -- but reading a bound is not
    # checking it carries an offset, and no fake can reject what only Google rejects.)
    # `events.list` requires RFC 3339, so a bound built from a naive datetime serialises as
    # "2026-05-31T10:00:00" -- no offset -- and is rejected with HTTP 400, which escapes
    # reconcile and engine.run drops the whole message. Assert on the ARGUMENTS, which is where
    # the defect actually lives.
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
    # A UID shaped like one off a real invite -- these are counterparty-supplied and often
    # carry the sender's domain, which is why the warning names the calendar ENTRY instead.
    uid = "uid-4b2a@mail.example-tidal.invalid"
    ics = parse_ics(f"BEGIN:VEVENT\r\nUID:{uid}\r\n"
                    "DTSTART;TZID=Nowhere/Notreal:20260715T110000\r\nEND:VEVENT")
    c = FakeGoogleClient(events=[])
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "created"
    assert c.inserted[0]["start"]["timeZone"] == "UTC"      # the guess itself is unchanged
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.track.calendar_sync"]
    assert any("Nowhere/Notreal" in m for m in said), f"the unresolved zone is not named: {said}"
    assert any("ASSUMING UTC" in m for m in said), f"the assumption is not stated: {said}"
    # The ENTRY, by the id Google just returned. This used to pin the ics UID, on the reasoning
    # that a warning telling you to go and verify an hour must say WHICH entry -- which is
    # right, and an event id serves it better: it is what finds the entry in the calendar UI or
    # the API, while the UID identifies the invite and cannot be searched for by hand. Swapping
    # it also stops a domain-bearing UID reaching a log, the rule `search_messages` keeps its
    # own query out of the log for.
    assert any("ev-new" in m for m in said), f"the entry to verify is not named: {said}"
    assert not any(uid in m for m in said), f"the warning leaked the inbound invite id: {said}"


def _floating(uid="u1"):
    return IcsEvent(uid=uid, summary="Screen", start=datetime(2026, 7, 15, 11, 0))


def test_configured_zone_is_what_a_floating_start_is_booked_in():
    # The default UTC is right for nobody in particular. A zone-less invite sitting in your
    # inbox is far likelier to be in your local time, so the assumption is configurable --
    # and setting it is what makes the guess actually correct.
    cfg = TrackConfig(calendar_assumed_timezone="Europe/Berlin")
    c = FakeGoogleClient(events=[])
    assert sync_event(c, cfg, lead_slug="example-lead", ics=_floating()) == "created"
    assert c.inserted[0]["start"] == {"dateTime": "2026-07-15T11:00:00", "timeZone": "Europe/Berlin"}


def test_a_configured_zone_does_not_rewrite_the_same_entry_on_every_run():
    # THE regression this knob could introduce. We book wall-clock 11:00 tagged Europe/Berlin
    # (= 09:00Z). If the later comparison still assumed UTC it would read our own entry as
    # 09:00 and the ics as 11:00, differ by the offset, and issue a real update_event on every
    # single run -- forever, silently, against a live calendar.
    cfg = TrackConfig(calendar_assumed_timezone="Europe/Berlin")
    first = FakeGoogleClient(events=[])
    sync_event(first, cfg, lead_slug="example-lead", ics=_floating())
    # Google echoes the booked entry back as a resolved instant, which is what the next run sees.
    echoed = {"id": "ev1", "start": {"dateTime": "2026-07-15T09:00:00+00:00"},
              "extendedProperties": {"private": {"sluice-track-uid": "u1"}}}
    again = FakeGoogleClient(events=[echoed])
    assert sync_event(again, cfg, lead_slug="example-lead", ics=_floating()) == "present"
    assert not again.updated and not again.inserted


def test_an_aware_start_is_never_restamped_with_the_configured_zone():
    # A `Z`-suffixed DTSTART is UTC by definition -- a fact, not a guess. timezone.utc has no
    # `.key`, so a careless fallback would hand it the configured zone and misbook a genuinely
    # UTC invite by that offset.
    cfg = TrackConfig(calendar_assumed_timezone="Europe/Berlin")
    utc_ics = parse_ics("BEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260715T110000Z\r\nEND:VEVENT")
    c = FakeGoogleClient(events=[])
    sync_event(c, cfg, lead_slug="example-lead", ics=utc_ics)
    assert c.inserted[0]["start"] == {"dateTime": "2026-07-15T11:00:00+00:00", "timeZone": "UTC"}


def test_an_unresolvable_configured_zone_warns_and_falls_back_to_utc(caplog):
    # A typo in config must not start dropping invites -- that is the failure this module was
    # just fixed for. Degrade to the old behaviour and say so.
    from sluice.track import calendar_sync as CS
    CS._resolve_zone.cache_clear()   # the warning is cached to fire once per process
    cfg = TrackConfig(calendar_assumed_timezone="Nowhere/Notreal")
    c = FakeGoogleClient(events=[])
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        sync_event(c, cfg, lead_slug="example-lead", ics=_floating())
    assert c.inserted[0]["start"]["timeZone"] == "UTC"
    assert any("Nowhere/Notreal" in r.getMessage() for r in caplog.records)
    CS._resolve_zone.cache_clear()


def test_the_shipped_default_still_assumes_utc():
    # Neutral by default: the shipped config names no location, and behaviour is identical to
    # before the key existed.
    assert TrackConfig().calendar_assumed_timezone == "UTC"
    c = FakeGoogleClient(events=[])
    sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_floating())
    assert c.inserted[0]["start"]["timeZone"] == "UTC"


def test_resolved_zone_and_present_outcome_stay_silent(caplog):
    # The warning must not cry wolf. A resolved zone is not a guess, and a `present` outcome
    # wrote nothing at all -- warning on either would train the reader to ignore the line.
    #
    # Asserting an EMPTY list is the dangerous shape: `get_logger` sets propagate=False, so if
    # capture were not reaching this logger the list would be empty for the WRONG reason and
    # this test would pass while proving nothing. The positive control at the end is what
    # makes the silence meaningful -- same logger, same capture, and it REQUIRES a record, so
    # a broken capture reds this test instead of flattering it.
    resolved = parse_ics("BEGIN:VEVENT\r\nUID:u1\r\n"
                         "DTSTART;TZID=GMT Standard Time:20260715T110000\r\nEND:VEVENT")
    naive = IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 15, 10, 0))

    def said():
        return [r.getMessage() for r in caplog.records
                if r.name == "sluice.track.calendar_sync"]

    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        sync_event(FakeGoogleClient(events=[]), TrackConfig(), lead_slug="example-lead", ics=resolved)
        # naive, but an untagged event already covers the slot -> "present", nothing written
        sync_event(FakeGoogleClient(events=[{"id": "g1", "start": {"dateTime": "2026-07-15T10:00:00+00:00"}}]),
                   TrackConfig(), lead_slug="example-lead", ics=naive)
        assert said() == [], said()
        # Positive control: a genuinely guessed instant DOES reach the capture.
        sync_event(FakeGoogleClient(events=[]), TrackConfig(), lead_slug="example-lead", ics=naive)
        assert said(), ("capture never reached sluice.track.calendar_sync, so the empty "
                        "assertion above proved nothing")
