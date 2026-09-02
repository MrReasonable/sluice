from datetime import datetime, timedelta, timezone
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent, parse_ics
from sluice.track.calendar_sync import _event_body, sync_event
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
    # The premise the absence-assertion below rests on. `uid` is what was fed to `parse_ics`,
    # not what came out (it does `value.strip()`, and RFC 5545 line-unfolding sits upstream);
    # if the value were ever normalised, the substring search would stop matching and the
    # assertion would pass while the UID was being logged -- green for a reason unrelated to
    # the property it claims.
    assert ics.uid == uid, "fixture premise: the UID must survive parse_ics byte-for-byte"
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


def test_a_floating_RESCHEDULE_names_the_entry_it_moved(caplog):
    """The UPDATE arm of the warning, which nothing reached.

    Measured: eight tests execute the `updated` call site and in NONE of them does the guard
    let the warning through, because none has a floating start. So the entry id threaded into
    that call was inert -- deleting it, or the whole call, left the suite green. Every existing
    warning test goes through the CREATE arm.

    A reschedule is where this matters most: the entry already exists, the operator has
    probably already looked at it once, and it has just been moved to an hour sluice guessed.
    """
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T09:00:00+00:00")])
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_floating()) == "updated"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.track.calendar_sync"]
    assert said, "a moved entry booked at a GUESSED hour must not be silent"
    assert any("ev1" in m for m in said), f"the entry that moved is not named: {said}"
    assert any("ASSUMING UTC" in m for m in said), f"the assumption is not stated: {said}"


def test_a_DRY_RUN_reschedule_does_not_say_it_updated_anything(caplog):
    """The create arm hedged for dry runs and the update arm did not, so one preview could
    print "would have created ... a calendar entry" and "updated the calendar entry ev1" in the
    same breath. Nothing was updated -- and the message asks the operator to go and verify an
    hour on an entry sluice never touched."""
    c = FakeGoogleClient(events=[_tagged_event("u1", "2026-07-15T09:00:00+00:00")])
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_floating(),
                          dry_run=True) == "updated"
    assert not c.updated, "a dry run must not write"
    said = " ".join(r.getMessage() for r in caplog.records
                    if r.name == "sluice.track.calendar_sync")
    assert said, "the guessed instant must still be reported in a preview"
    assert "would have updated" in said, (
        f"a preview claims an accomplished write: {said}")


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


def _ours(uid, start_iso, lead_slug="example-lead", event_id="ev1"):
    """An event sluice created: OUR uid tag AND the lead tag `_event_body` writes.

    Distinct from `_tagged_event` above, which omits the lead tag -- the same-slot rule
    is scoped by lead, so a fixture without one could not express the scoping.
    """
    return {"id": event_id, "start": {"dateTime": start_iso},
            "extendedProperties": {"private": {"sluice-track-uid": uid,
                                               "sluice-track-lead": lead_slug}}}


def test_a_second_invite_for_the_same_slot_does_not_double_book_under_a_new_uid():
    """#203: dedup was keyed on the ics UID alone. N messages on one thread carrying N
    distinct UIDs produced N events at ONE slot -- each "ours" under a different
    identity, so `_find_ours` matched none of the others, and `_foreign_at_start` did
    not suppress the insert either because it only fires for UNTAGGED events.

    `calendar_added` reported the inflated count, so the digest and the calendar agreed
    with each other and nothing read as wrong. It stayed hidden because the count is 0
    on most runs and only exceeds 1 on a backlog -- exactly when several messages from
    one thread land in a single pass.
    """
    c = FakeGoogleClient(events=[_ours("u1", "2026-07-15T10:00:00+00:00")])
    out = sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics(uid="u2"))
    assert not c.inserted, "a second UID for one slot booked a duplicate"
    assert out == "present"


def test_a_reschedule_to_a_new_time_under_a_new_uid_is_still_booked():
    """The other direction, and the reason the rule compares the START INSTANT rather
    than `calendar_match_minutes` proximity: a loose match would suppress a genuine
    reschedule and leave the calendar showing the OLD time, silently. Booking a visible
    second event at the new time is the failure to prefer.
    """
    c = FakeGoogleClient(events=[_ours("u1", "2026-07-15T09:00:00+00:00")])
    new = _ics(uid="u2", start=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=new) == "created"
    assert c.inserted


def test_another_leads_interview_at_the_same_instant_does_not_suppress_the_booking():
    """The rule is scoped by lead. Two applications can genuinely hold the same slot --
    suppressing there would silently drop a real interview, which is the harm this whole
    module is arranged to avoid.
    """
    c = FakeGoogleClient(events=[_ours("u1", "2026-07-15T10:00:00+00:00", lead_slug="other-lead")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics(uid="u2")) == "created"
    assert c.inserted


def _ours_seq(uid, start_iso, seq=None, lead_slug="example-lead", event_id="ev1"):
    """`_ours` plus the revision tag. `seq=None` omits the key entirely, which is what
    every event created before #202 looks like."""
    priv = {"sluice-track-uid": uid, "sluice-track-lead": lead_slug}
    if seq is not None:
        priv["sluice-track-seq"] = str(seq)
    return {"id": event_id, "start": {"dateTime": start_iso},
            "extendedProperties": {"private": priv}}


def _rev(uid="u1", sequence=0, start=None):
    e = IcsEvent(uid=uid, summary="Screen", sequence=sequence,
                 start=start or datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                 end=datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc))
    return e


def test_a_superseded_invite_does_not_overwrite_a_newer_revision():
    """#202 defect 1: an ATS sent two invites on one thread 96 seconds apart, the second
    an `Updated:` carrying the corrected day. Nothing ordered them, so whichever was
    processed last won -- and when that was the OLDER one it rewrote the event back to
    the superseded day. The run reported calendar_added=1, failures=0.

    RFC 5545 makes SEQUENCE the arbiter, and `ics.py` already parses it; nothing used it
    to arbitrate between two VEVENTs sharing a UID. A strictly LOWER revision must never
    overwrite a higher one.
    """
    c = FakeGoogleClient(events=[_ours_seq("u1", "2026-07-18T10:00:00+00:00", seq=1)])
    stale = _rev(sequence=0, start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    out = sync_event(c, TrackConfig(), lead_slug="example-lead", ics=stale)
    assert not c.updated, "a superseded revision rewrote the event to the old day"
    assert out == "present"


def test_a_newer_revision_still_reschedules():
    """The other direction -- the fix must not freeze the event against real updates."""
    c = FakeGoogleClient(events=[_ours_seq("u1", "2026-07-15T10:00:00+00:00", seq=1)])
    newer = _rev(sequence=2, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=newer) == "updated"
    assert c.updated


def test_an_equal_revision_still_reschedules():
    """Equal SEQUENCE applies, deliberately. Plenty of senders never increment it and
    ship every revision as SEQUENCE:0, so refusing on equality would ignore their real
    reschedules -- a silently stale calendar, which is the harm being fixed, not a fix.
    """
    c = FakeGoogleClient(events=[_ours_seq("u1", "2026-07-15T10:00:00+00:00", seq=0)])
    same = _rev(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=same) == "updated"


def test_an_event_created_before_the_revision_tag_existed_still_reschedules():
    """Backward compatibility: every entry already in a user's calendar carries no
    revision tag. Absent must mean "cannot compare, so apply", never "refuse"."""
    c = FakeGoogleClient(events=[_ours_seq("u1", "2026-07-15T10:00:00+00:00", seq=None)])
    newer = _rev(sequence=3, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=newer) == "updated"


def _ours_stamped(uid, start_iso, seq, stamp_iso=None, lead_slug="example-lead",
                  event_id="ev1"):
    priv = {"sluice-track-uid": uid, "sluice-track-lead": lead_slug,
            "sluice-track-seq": str(seq)}
    if stamp_iso is not None:
        priv["sluice-track-stamp"] = stamp_iso
    return {"id": event_id, "start": {"dateTime": start_iso},
            "extendedProperties": {"private": priv}}


def _rev_stamped(uid="u1", sequence=0, start=None, dtstamp=None):
    e = _rev(uid=uid, sequence=sequence, start=start)
    e.dtstamp = dtstamp
    return e


def test_an_equal_sequence_is_broken_by_dtstamp_so_an_older_revision_loses():
    """#202: SEQUENCE alone leaves the non-incrementing sender order-dependent -- plenty
    ship every revision as 0, and then whichever message the search returned last won.
    DTSTAMP is RFC 5545's second arbiter, and an OLDER one must not overwrite a newer.
    """
    c = FakeGoogleClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=0,
                                               stamp_iso="2026-07-10T09:01:00+00:00")])
    stale = _rev_stamped(sequence=0, start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                         dtstamp=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=stale) == "present"
    assert not c.updated, "an older DTSTAMP rewrote the event to the superseded day"


def test_an_equal_sequence_with_a_newer_dtstamp_still_reschedules():
    """The other direction -- the pair that actually reproduces the reported thread, whose
    two messages were 96 seconds apart."""
    c = FakeGoogleClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=0,
                                               stamp_iso="2026-07-10T09:00:00+00:00")])
    newer = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                         dtstamp=datetime(2026, 7, 10, 9, 1, 36, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=newer) == "updated"


def test_an_equal_sequence_with_no_dtstamp_to_compare_still_reschedules():
    """Neither side carries one, so there is no tie to break and the pre-#202 behaviour
    stands. Absent must never mean refuse."""
    c = FakeGoogleClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=0)])
    same = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=same) == "updated"


def test_sequence_outranks_dtstamp():
    """Order of arbitration, pinned. A higher SEQUENCE wins even carrying an older
    DTSTAMP: SEQUENCE is the sender's explicit statement about which revision supersedes,
    and DTSTAMP only breaks ties it leaves.
    """
    c = FakeGoogleClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=0,
                                               stamp_iso="2026-07-11T09:00:00+00:00")])
    newer_seq = _rev_stamped(sequence=1, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                             dtstamp=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=newer_seq) == "updated"


class _ApplyingClient(FakeGoogleClient):
    """`FakeGoogleClient` that also APPLIES an update to its own store.

    The shared fake records `update_event` and leaves the stored event untouched, which is
    right for a single-call assertion and cannot express a defect that only appears on the
    NEXT call. Applying is the more faithful behaviour -- the real API does -- so this
    narrows the divergence rather than widening it.
    """

    def update_event(self, event_id, body):
        out = super().update_event(event_id, body)
        for i, ev in enumerate(self.events):
            if ev.get("id") == event_id:
                self.events[i] = {**ev, **body, "id": event_id}
        return out


def test_a_same_instant_revision_advances_the_recorded_one():
    """#202 follow-up. The arbiter is only as good as the revision it compares against,
    and that was written on the two arms that CHANGE the start -- never on the arm that
    finds the event already at the right time.

    So an ordinary same-instant revision (a location or title correction) left the tag at
    its old value, and the next genuinely superseded invite then cleared the stale bar.
    """
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=0)])
    same_instant = _rev_stamped(sequence=5, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                                dtstamp=datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc))
    out = sync_event(c, TrackConfig(), lead_slug="example-lead", ics=same_instant)

    assert c.updated, "a newer revision at the same instant was not recorded at all"
    assert out == "updated", "a write that happened must not be reported as nothing to do"
    stored = c.events[0]["extendedProperties"]["private"]
    assert stored["sluice-track-seq"] == "5"
    # Both tags, not just the one: the arbiter reads them together, and a stale stamp
    # beside a fresh sequence is the same staleness this test was written for.
    assert stored["sluice-track-stamp"].startswith("2026-07-12")


def test_the_superseded_invite_still_loses_after_a_same_instant_revision():
    """The regression in full, three calls, because the defect only exists ACROSS calls.

    seq=0 booked -> seq=5 at the same instant -> seq=1 carrying the stale day. Before the
    fix the middle step left the tag at 0, so the last step passed `1 >= 0` and moved the
    appointment back to a day that had already passed, reporting `updated`.
    """
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=0)])
    sync_event(c, TrackConfig(), lead_slug="example-lead",
               ics=_rev_stamped(sequence=5, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)))
    c.updated.clear()

    superseded = _rev_stamped(sequence=1, start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    out = sync_event(c, TrackConfig(), lead_slug="example-lead", ics=superseded)

    assert out == "present"
    assert not c.updated, "a superseded invite moved the appointment to the stale day"
    assert c.events[0]["start"]["dateTime"].startswith("2026-07-18")


def test_an_unchanged_reinvite_with_nothing_to_compare_writes_nothing():
    """The control for the uncomparable path: neither side carries a DTSTAMP, so there is
    no revision evidence at all and the unmoved instant settles it.

    Named for what it pins. It used to claim it showed that "an unchanged re-send would
    otherwise rewrite the event on every run", which describes a guard the code does not
    have -- see `test_a_reinvite_carrying_a_fresh_dtstamp_does_write` below, and
    `sync_event`'s own ACCEPTED COST note. Asserting a `present` that comes from the
    uncomparable path while explaining it as an equality check is the prose-versus-code
    gap this repo keeps finding.
    """
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=5)])
    same = _rev_stamped(sequence=5, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=same) == "present"
    assert not c.updated


def test_a_readable_zero_sequence_is_still_arbitrated():
    """The control that keeps the fix from swallowing the guard: a genuine SEQUENCE:0 is
    still compared, and still loses to a recorded revision 3."""
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=3)])
    ics = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert ics.sequence_unreadable is False
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "present"
    assert not c.updated


def test_an_unreadable_sequence_still_loses_to_an_older_dtstamp():
    """Round 2. Round 1 made an unreadable SEQUENCE abstain -- and abstained BEFORE the
    DTSTAMP arbiter, so a tie that was perfectly breakable went unbroken. A late copy of
    the original invite (`SEQUENCE:0.0`, older DTSTAMP) then read as "cannot compare",
    fell through to `moved`, and moved the corrected interview back to the old day.

    Closing a silent-loss hole opened a silent-clobber one, in the direction this whole
    arm exists to prevent. Unreadable means "skip the SEQUENCE compare", not "skip the
    arbitration".
    """
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-16T14:00:00+00:00", seq=2,
                                              stamp_iso="2026-07-10T09:00:00+00:00")])
    stale = _rev_stamped(sequence=0, start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                         dtstamp=datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc))
    stale.sequence_unreadable = True

    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=stale) == "present"
    assert not c.updated, "a superseded invite with a mangled SEQUENCE moved the interview"


def test_an_unreadable_sequence_with_a_newer_dtstamp_still_applies():
    """The other direction: the tiebreak must still be able to say YES."""
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=2,
                                              stamp_iso="2026-07-09T09:00:00+00:00")])
    newer = _rev_stamped(sequence=0, start=datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc),
                         dtstamp=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))
    newer.sequence_unreadable = True
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=newer) == "updated"


def test_an_unorderable_reschedule_reaches_a_human_instead_of_being_guessed():
    """Nothing can order these -- the invite's SEQUENCE is unreadable and neither side
    carries a DTSTAMP -- and the time has MOVED, so applying and refusing are both a guess
    with a real cost. `unorderable` is the third answer: write nothing AND say so, which
    `reconcile` already routes to a human.

    Scoped to an UNREADABLE sequence. An event that simply carries no recorded revision
    is the ordinary pre-#202 case -- every entry booked before this branch -- and must
    still apply, or the first reschedule of every legacy event goes to the dead-letter.

    This fixture replaces a round-1 test that asserted `updated` on exactly it, under the
    rule "unreadable means cannot compare, so apply". That rule was wrong in the other
    direction -- it let a superseded invite clobber a corrected time (see the DTSTAMP test
    above) -- so the two could not both stand on the same input. The property that test was
    written for survives here and is what the assertions below pin: a real reschedule is
    never SILENTLY discarded. It is no longer applied on a guess; it is surfaced.
    """
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=3)])
    ics = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    ics.sequence_unreadable = True

    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "unorderable"
    assert not c.updated and not c.inserted


def test_a_legacy_event_with_no_recorded_revision_still_reschedules():
    """The control for the scoping above: no tag on the event, readable sequence on the
    invite -- ordinary, benign, and it must still apply."""
    c = _ApplyingClient(events=[_ours_seq("u1", "2026-07-15T10:00:00+00:00", seq=None)])
    ics = _rev_stamped(sequence=4, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "updated"


def test_the_private_accessor_survives_a_null_extended_properties_block():
    """`_private`'s docstring claims each of the three levels "can be absent or None on a
    real Google payload". `{"extendedProperties": None}` raised AttributeError, so the
    claim was false for the middle level -- and four readers now share it."""
    from sluice.track.calendar_sync import _private
    assert _private({}) == {}
    assert _private({"extendedProperties": None}) == {}
    assert _private({"extendedProperties": {"private": None}}) == {}
    assert _private({"extendedProperties": {"private": {"a": "1"}}}) == {"a": "1"}


def test_a_naive_dtstamp_is_read_as_utc_not_as_the_configured_zone():
    """RFC 5545 §3.8.7.2 specifies DTSTAMP in UTC, and iTIP REQUIRES it. So a DTSTAMP
    arriving without a `Z` is malformed, and UTC is the only reading with a basis --
    unlike DTSTART, where a floating value is legal and `calendar_assumed_timezone` is
    the right guess.

    Reading it in the configured zone instead made the SAME pair of revisions order one
    way under `UTC` and the other under `Asia/Dubai`: one direction discards a real
    reschedule, the other lets a superseded invite move a corrected interview. The verdict
    must not move with the config at all.
    """
    outcomes = set()
    for zone in ("UTC", "Asia/Dubai", "America/New_York"):
        cfg = TrackConfig()
        cfg.calendar_assumed_timezone = zone
        c = _ApplyingClient(events=[_ours_stamped(
            "u1", "2026-07-18T10:00:00+00:00", seq=0,
            stamp_iso="2026-07-10T09:00:00")])           # NAIVE, as a sloppy sender sends it
        older = _rev_stamped(sequence=0,
                             start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                             dtstamp=datetime(2026, 7, 10, 7, 0, tzinfo=timezone.utc))
        outcomes.add(sync_event(c, cfg, lead_slug="example-lead", ics=older))
    assert outcomes == {"present"}, (
        f"the revision tiebreak moved with calendar_assumed_timezone: {outcomes}")


def test_an_identical_redelivery_is_recognised_as_the_same_revision():
    """A redelivery of one invite carries the same SEQUENCE and the same DTSTAMP, so it
    is the SAME revision -- not merely uncomparable.

    This row does NOT close the deleted-equality-arm hole, though an earlier version of
    this docstring claimed it did: on an unmoved start, "same revision" and "older
    revision" both come out `present`, so it passes either way. The two rows that DO see
    that arm are the moved-start pair below and beside it.
    """
    stamp = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=0,
                                              stamp_iso=stamp.isoformat())])
    again = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                         dtstamp=stamp)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=again) == "present"
    assert not c.updated


def test_an_equal_revision_with_equal_dtstamps_still_reschedules_a_moved_start():
    """The `0` arm's DISCRIMINATING row, and the policy it encodes.

    A readable SEQUENCE that ties, with matching DTSTAMPs, still applies when the start
    has moved -- the deliberate choice for senders who never increment, stated at the
    refusal above. Distinct from the unreadable case beside it, which surfaces instead.

    `test_an_identical_redelivery_is_recognised_as_the_same_revision` cannot pin this: on
    an UNMOVED start, "same revision" and "older revision" both come out `present`, so it
    passes whether the equality arm exists or not. Moving the start is what makes the two
    disagree -- same-revision applies, older-revision refuses.
    """
    stamp = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=0,
                                              stamp_iso=stamp.isoformat())])
    moved = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                         dtstamp=stamp)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=moved) == "updated"
    assert c.updated


def test_an_unreadable_sequence_with_equal_dtstamps_and_a_moved_start_is_unorderable():
    """Equal DTSTAMPs do not ORDER two invites -- they say the objects were created at the
    same moment, which for two DIFFERENT start times is contradictory rather than
    conclusive. With the SEQUENCE unreadable there is nothing else to appeal to, so this
    is the case the `unorderable` arm's own docstring describes and it must take it.

    Distinct from an equal READABLE sequence, which still applies: there the sender
    stated a revision and simply never increments it, which is a known habit rather than
    contradictory evidence.
    """
    stamp = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-15T10:00:00+00:00", seq=0,
                                              stamp_iso=stamp.isoformat())])
    ics = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                       dtstamp=stamp)
    ics.sequence_unreadable = True
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "unorderable"
    assert not c.updated


def test_a_reinvite_carrying_a_fresh_dtstamp_does_write():
    """The other half, pinning the cost `sync_event` documents rather than contradicting
    it. RFC 5545 makes DTSTAMP the iCalendar OBJECT's creation time, so a re-send of an
    unchanged event carries the same SEQUENCE and a NEW stamp; that reads as strictly
    newer and writes. Deliberate -- leaving the recorded stamp behind lets a stale invite
    whose own stamp sits between the two read as newer and move the appointment.
    """
    c = _ApplyingClient(events=[_ours_stamped(
        "u1", "2026-07-18T10:00:00+00:00", seq=0, stamp_iso="2026-07-10T09:00:00+00:00")])
    resend = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                          dtstamp=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=resend) == "updated"
    assert c.updated


def test_the_dtstamp_we_book_from_is_persisted_so_the_next_run_can_arbitrate():
    """PRODUCER side. Every other DTSTAMP test hand-plants `stamp_iso=` into its fixture,
    so all of them pinned the READER and none pinned that sluice writes the tag it later
    reads -- deleting the write left the whole suite green while reopening #202 in full.

    Builds the stored event from `_event_body` itself, the way a real booking does, then
    puts the late original invite through: same SEQUENCE, older DTSTAMP, the old day.
    """
    cfg = TrackConfig()
    good = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                        dtstamp=datetime(2026, 7, 10, 9, 1, 36, tzinfo=timezone.utc))
    body = _event_body(cfg, "example-lead", good)
    private = body["extendedProperties"]["private"]
    assert "sluice-track-stamp" in private, (
        "a booking that carried a DTSTAMP did not record it, so the next run has nothing "
        "to arbitrate against")

    stored = {"id": "ev1", **body}
    c = _ApplyingClient(events=[stored])
    late = _rev_stamped(sequence=0, start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                        dtstamp=datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc))
    assert sync_event(c, cfg, lead_slug="example-lead", ics=late) == "present"
    assert not c.updated, "the superseded original moved the interview back"


def test_an_unreadable_sequence_is_never_PERSISTED_as_a_fabricated_zero():
    """`sequence_unreadable` stops the coerced 0 being COMPARED. It was still WRITTEN.

    `parse_ics` coerces `SEQUENCE:1.0` to 0 so one mangled line cannot sink an invite.
    Recording that 0 on the event turns a value we distrust into the baseline every later
    invite is judged against: measured, an event at seq=5 took an unreadable-SEQUENCE
    invite (newer DTSTAMP, same instant), had its tag rewritten to `"0"`, and a later
    genuinely superseded `SEQUENCE:3` invite then read as newer off that fabrication and
    moved the appointment to the stale day.

    The stored value is carried forward instead, mirroring `_STAMP_KEY`, which already
    stays absent rather than inventing one.
    """
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=5,
                                              stamp_iso="2026-07-10T09:00:00+00:00")])
    mangled = _rev_stamped(sequence=0, start=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
                           dtstamp=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc))
    mangled.sequence_unreadable = True
    sync_event(c, TrackConfig(), lead_slug="example-lead", ics=mangled)
    assert c.events[0]["extendedProperties"]["private"]["sluice-track-seq"] == "5", (
        "a SEQUENCE we refused to compare was written as the new baseline")

    c.updated.clear()
    superseded = _rev_stamped(sequence=3, start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                              dtstamp=datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=superseded) == "present"
    assert not c.updated, "a superseded invite won against a fabricated revision"


def test_a_readable_sequence_is_still_recorded():
    """The control: only an UNREADABLE sequence is withheld, or the tag never advances
    and every arbitration falls back to the value first booked."""
    c = _ApplyingClient(events=[_ours_stamped("u1", "2026-07-18T10:00:00+00:00", seq=0)])
    good = _rev_stamped(sequence=7, start=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
    sync_event(c, TrackConfig(), lead_slug="example-lead", ics=good)
    assert c.events[0]["extendedProperties"]["private"]["sluice-track-seq"] == "7"


def test_the_same_slot_rule_outranks_foreign_and_truncated():
    """The placement of `_ours_at_start` is called load-bearing in its own comment and
    nothing tested it -- moving the block after both branches below survived the suite.

    Both differing inputs: an untagged event near the slot would answer `foreign`, and a
    short window would answer `unresolved`. Either sends a human a needs_review row about
    calendar work that is in fact already done, because an entry of OURS holds the slot.
    """
    ours = _ours("u1", "2026-07-15T10:00:00+00:00")
    near = {"id": "g1", "start": {"dateTime": "2026-07-15T10:10:00+00:00"}}   # untagged
    c = FakeGoogleClient(events=[ours, near])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics(uid="u2")) == "present"
    assert not c.inserted

    c2 = FakeGoogleClient(events=[ours], truncated=True, tag_truncated=True)
    assert sync_event(c2, TrackConfig(), lead_slug="example-lead", ics=_ics(uid="u2")) == "present"
    assert not c2.inserted


def test_a_cancel_under_a_second_uid_does_not_report_nothing_of_ours():
    """#203 made the cancel arm's `present` untrue.

    The same-slot rule suppresses the insert for a second UID on one thread, so the event
    at that slot carries the FIRST UID. A CANCEL arriving under the second one finds no
    match by UID and falls through to `present` -- "nothing of ours, and nothing else at
    that slot" -- which is now false: an entry of ours is sitting right there, for this
    lead, at this instant. It files no row, so `seen.add` consumes the message and the
    cancelled interview stays in the calendar with nothing anywhere saying so.

    Deleting it is not the answer either: the UID does not match, so we cannot show the
    entry is this invite's. Report it and let a human look.
    """
    ours = _ours("u1", "2026-07-15T10:00:00+00:00")
    c = FakeGoogleClient(events=[ours])
    cancel = _ics(uid="u2", cancelled=True)
    out = sync_event(c, TrackConfig(), lead_slug="example-lead", ics=cancel)
    assert out != "present", "claimed nothing of ours at a slot our own entry holds"
    assert out == "unresolved"
    assert not c.deleted, "deleted an entry this invite cannot be shown to own"


def test_a_cancel_with_genuinely_nothing_at_the_slot_is_still_present():
    """The control -- `present` must keep meaning what it says, or every clean cancel
    starts filing dead-letter rows."""
    c = FakeGoogleClient(events=[])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(uid="u2", cancelled=True)) == "present"
