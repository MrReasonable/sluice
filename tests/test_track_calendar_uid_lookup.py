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
    lo, hi = (datetime.fromisoformat(b) for b in c.listed[0])
    old = datetime.fromisoformat(_OLD)
    # Parsed, not compared as strings. ISO-8601 sorts lexically only while every value shares
    # one offset, and a premise check that silently stops meaning anything the day a fixture
    # gains a `+01:00` is worse than no premise check.
    assert not (lo <= old <= hi), (
        f"the old event at {_OLD} sits INSIDE the window {lo}..{hi}, so nothing here "
        "exercises the out-of-window lookup")


def test_a_reschedule_beyond_the_lookahead_MOVES_our_event_instead_of_duplicating_it():
    c = FakeGoogleClient(events=[_ours()])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "updated"
    assert not c.inserted, "a second entry for the same interview is the #146 harm itself"
    assert [eid for eid, _ in c.updated] == ["ev1"], "the original must be the thing that moved"


def test_a_cancel_beyond_the_lookahead_DELETES_our_event_instead_of_leaving_it():
    """The other half of the same miss, and the quieter one.

    A duplicate is at least visible. A cancel whose event sits out of window deleted nothing
    and reported `present` -- "we searched a complete window and there was nothing of ours" --
    so the cancelled interview simply stayed in the calendar and the run looked clean."""
    c = FakeGoogleClient(events=[_ours()])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "cancelled"
    assert c.deleted == ["ev1"]


def test_the_tag_query_is_NOT_asked_when_the_window_already_found_our_event():
    """One extra round trip per invite, only on the path that needs it.

    The window scan answers the steady-state case -- an invite we have already booked, at a
    time that has not moved -- and asking Google a second question after it has already
    answered would put a request on every invite to fix a case that fires on almost none."""
    c = FakeGoogleClient(events=[_ours(start_iso="2026-10-01T09:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "updated"
    assert c.tag_queries == [], f"asked Google twice for an answer it already had: {c.tag_queries}"


def test_the_tag_query_asks_for_the_KEY_THE_BODY_IS_WRITTEN_WITH():
    """The one drift that would silently un-fix this, pinned on the literal.

    `_event_body` writes the tag, `_uid_of` reads it back and the query filters on it
    server-side. Renaming the constant keeps all three agreeing with each other and agreeing
    with NOTHING already in the operator's calendar -- every event booked by an earlier sluice
    becomes unfindable, and the empty result reads as "we never created this". The published
    string is the contract, so the string is what this asserts."""
    c = FakeGoogleClient(events=[])
    sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics())
    # The KEY SET, not its order -- insertion order is incidental, and pinning it would make
    # this red for a rearrangement that harms nobody while still catching a rename.
    assert set(c.inserted[0]["extendedProperties"]["private"]) == \
        {"sluice-track-uid", "sluice-track-lead"}
    assert c.tag_queries == [("sluice-track-uid", "u1")]


def test_our_own_out_of_window_event_BEATS_a_foreign_event_at_the_new_slot():
    """Which of the two lookups wins, when they disagree.

    The window scan sees only the untagged event now sitting at the rescheduled time -- the
    recruiter's own invite, which Google auto-adds from the mail -- so on its own it answers
    `foreign`: correct about the safety property, and it leaves our real entry stranded at the
    old time forever, because `foreign` writes nothing. Finding OURS has to take precedence,
    and it does: the tag query runs before `_foreign_at_start` is consulted."""
    c = FakeGoogleClient(events=[
        _ours(),                                                        # ours, out of window
        {"id": "recruiter", "start": {"dateTime": "2026-10-01T10:00:00+00:00"}},   # untagged
    ])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "updated"
    assert [eid for eid, _ in c.updated] == ["ev1"]
    assert not c.inserted and not c.deleted, "the foreign event must still never be touched"


class _FilterMatchesNothing(FakeGoogleClient):
    """Google accepting the filter and returning nothing -- the unverifiable failure.

    `privateExtendedProperty` is confirmed to EXIST against the discovery document, but no
    offline test can execute its MATCHING behaviour, and a filter that matched nothing would
    return an empty list rather than an error. This fake is that outcome."""

    def find_events_by_private_property(self, name, value, max_results=2500):
        self.tag_queries.append((name, value))
        return [], False


def test_a_filter_that_MATCHES_NOTHING_leaves_every_pre_existing_outcome_untouched():
    """The property that makes it safe to ship an unexecuted external contract.

    If the tag query silently returns nothing, `sync_event` must behave exactly as it did
    before this lookup existed -- so the worst case of a wrong filter is the bug we already
    ship, never a new one.

    The rows are marked by whether they REACH the broken filter, because a table whose rows
    mostly never touch the thing under test certifies nothing. Measured: `created`,
    `unresolved` and `foreign` go through it (the window found nothing of ours); `updated` and
    `present` are answered by the window scan and are here as controls -- they must not change
    either, and they are the ones that would break if the miss-path guard were removed."""
    cfg = TrackConfig()
    inside = "2026-10-01T09:00:00+00:00"    # within the window centred on the new start

    # -- reaches the filter --------------------------------------------------------------
    nothing = _FilterMatchesNothing(events=[])
    assert sync_event(nothing, cfg, lead_slug="example-lead", ics=_ics()) == "created"
    assert nothing.inserted, "a genuinely new invite must still be booked"
    assert nothing.tag_queries, "this row is only meaningful if it consulted the filter"

    short = _FilterMatchesNothing(events=[], truncated=True)
    assert sync_event(short, cfg, lead_slug="example-lead", ics=_ics()) == "unresolved"
    assert not short.inserted
    assert short.tag_queries

    # The recruiter's own auto-added invite at the new slot, nothing of ours anywhere. Reaches
    # the filter (the window holds no event of ours) and must still refuse to double-book.
    alien = _FilterMatchesNothing(
        events=[{"id": "recruiters-own", "start": {"dateTime": "2026-10-01T10:00:00+00:00"}}])
    assert sync_event(alien, cfg, lead_slug="example-lead", ics=_ics()) == "foreign"
    assert not alien.inserted and not alien.updated and not alien.deleted
    assert alien.tag_queries

    # -- controls: answered by the window, must be unaffected ----------------------------
    moved = _FilterMatchesNothing(events=[_ours(start_iso=inside)])
    assert sync_event(moved, cfg, lead_slug="example-lead", ics=_ics()) == "updated"

    same = _FilterMatchesNothing(events=[_ours(start_iso="2026-10-01T10:00:00+00:00")])
    assert sync_event(same, cfg, lead_slug="example-lead", ics=_ics()) == "present"

    cancel = _FilterMatchesNothing(events=[_ours(start_iso=inside)])
    assert sync_event(cancel, cfg, lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "cancelled"


class _FilterMatchesTooMuch(FakeGoogleClient):
    """Google ignoring the constraint and handing back somebody else's event."""

    def find_events_by_private_property(self, name, value, max_results=2500):
        self.tag_queries.append((name, value))
        return list(self.events), False


def test_the_LOCAL_uid_check_still_decides_when_the_filter_over_matches():
    """The server-side filter narrows; it never adjudicates.

    An over-matching filter is the failure that would actually destroy data -- updating or
    DELETING an event belonging to a different interview, or to the operator's own diary.
    `_find_ours` re-checks `_uid_of(ev) == ics.uid` on everything the query returns, so an
    event can only be acted on after the comparison this repo controls agrees it is ours."""
    c = _FilterMatchesTooMuch(events=[_ours(uid="SOMEONE-ELSES", event_id="not-ours")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "created"
    assert not c.updated and not c.deleted, "a foreign UID was treated as our own event"

    d = _FilterMatchesTooMuch(events=[_ours(uid="SOMEONE-ELSES", event_id="not-ours")])
    assert sync_event(d, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "present"
    assert not d.deleted, "a cancel deleted an event belonging to a different interview"


def test_a_TRUNCATED_tag_query_that_found_nothing_refuses_to_insert():
    """Same discipline the window already had: absence in a short read is not absence.

    Only reachable if more events carry this exact UID tag than one page can hold, which
    should never happen -- but the honest answer costs nothing and the guessed one is a
    duplicate interview."""
    c = FakeGoogleClient(events=[], tag_truncated=True)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "unresolved"
    assert not c.inserted

    d = FakeGoogleClient(events=[], tag_truncated=True)
    assert sync_event(d, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "unresolved"
    assert not d.deleted


def test_a_TRUNCATED_tag_query_that_DID_find_something_still_refuses_to_claim_a_cancel():
    """Deleting what you can see is not the same as removing everything.

    The other truncation tests set an empty calendar, so `mine` is empty and the guard fires
    for a different reason entirely. This is the shape none of them reach: the tag query was
    short AND returned a match. We delete what we found -- that work is real and worth doing --
    but another entry under this UID may be off-page, so `cancelled` would be exactly the
    positive claim of complete removal that this branch refuses to make on partial evidence.

    Note the flag that cannot express this: `truncated` is computed as
    `not mine and tag_unsettled`, deliberately False here BECAUSE something was found, and the
    `if mine:` branch returns before it is read at all."""
    c = FakeGoogleClient(events=[_ours()], tag_truncated=True)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "unresolved"
    assert c.deleted == ["ev1"], "the entry we COULD see must still be removed"


def test_the_row_for_a_PARTIAL_cancel_does_not_claim_nothing_was_deleted():
    """The operator-facing half of the case above, and it was a real defect.

    `sync_event` deleting entries and still answering `unresolved` is new, and the
    `cancel-unresolved` hint said "so nothing was deleted. Check your calendar and remove it by
    hand" -- a false statement in exactly this case, sending the operator after something
    already gone. One reason value covering two situations has to be true of both.

    Drives `engine.run` because the hint is assembled there and a `sync_event` return value
    cannot see it. Asserts the FACT (the row does not claim inaction) rather than the exact
    sentence, so rewording stays free."""
    import json
    import pathlib
    import tempfile

    from sluice.core.vault import Vault
    from sluice.track import engine as E
    from tests.test_track_engine import FakeBackend, OneMsgClient, _dl

    root = pathlib.Path(tempfile.mkdtemp())
    leads = root / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Example Tidal - Analyst.md").write_text(
        '---\ncompany: "Example Tidal"\nrole: "Analyst"\nstatus: interview\n---\n\nBODY\n')
    v = Vault(str(root))

    class _PartialCancel(OneMsgClient):
        """A cancel whose tag query IS truncated and DOES find one of ours."""

        def __init__(self):
            super().__init__()
            self.events = [_ours(start_iso="2026-10-01T10:00:00+00:00")]
            self.tag_truncated = True

        def get_message(self, mid):
            msg = super().get_message(mid)
            msg["attachments"] = [{
                "filename": "invite.ics", "mime": "text/calendar",
                "data": (b"BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                         b"DTSTART:20261001T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")}]
            return msg

    c, dl = _PartialCancel(), _dl()
    E.run(v, TrackConfig(), c,
          FakeBackend(json.dumps({"lead": "Example Tidal - Analyst", "type": "interview",
                                  "confidence": 0.9, "when": None, "links": [],
                                  "materials": [], "summary": "cancelled"})),
          seen=set(), deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")

    assert c.deleted == ["ev1"], "the entry we could see must still have been removed"
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, "an unconfirmed cancel must still leave a durable row"
    hint = rows[0].hint
    assert "nothing was deleted" not in hint, (
        f"the row tells the operator to remove something sluice already removed: {hint}")
    assert "dismiss" in hint, f"the row must still offer a way to close it: {hint}"


def test_a_CLEAN_tag_query_does_not_clear_a_SHORT_window():
    """`truncated` is a one-way ratchet, and this is the test that keeps it one.

    A complete tag query returning nothing is tempting to read as proof that no event of ours
    exists anywhere -- which would license the insert that `sync_event` currently refuses on a
    truncated window. But "complete" there rests on the matching behaviour nobody has
    executed. Reading it as proof converts an unverified contract into a silent duplicate,
    which is the exact harm this change exists to remove."""
    c = FakeGoogleClient(events=[], truncated=True, tag_truncated=False)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "unresolved"
    assert not c.inserted, (
        "the short window was forgiven on the strength of an unexecuted filter -- lift this "
        "only after the query has been run against a live calendar")


# ---- the query must not be asked when the answer could not be trusted ----------------------

def test_an_invite_with_NO_uid_matches_nothing_of_ours():
    """An empty search key must mean ABSTAIN, never match-everything.

    `parse_ics` admits a VEVENT with a DTSTART and no UID -- its last line is
    `ev.uid or ev.start` -- so `_event_body` can write `sluice-track-uid: ""`, and equality
    made `"" == ""` a hit. The +/- lookahead window used to contain that. A query with NO time
    bound does not: it asks Google for every UID-less event sluice ever created, anywhere in
    the calendar.

    The concrete harm, which is why this is not a tidiness test: sluice booked one firm's
    UID-less invite months ago; a DIFFERENT firm's UID-less invite arrives; `sync_event`
    resolves the second onto the first and rewrites its summary, its start and its
    `sluice-track-lead`. The first interview is gone from the calendar and the second was
    never booked as its own entry -- reported as `updated`, counted as a success, with nothing
    routed to a human. On a cancel it DELETES the other firm's live interview."""
    from sluice.track.ics import parse_ics

    theirs = {"id": "other-firms-event",
              "start": {"dateTime": "2026-07-15T10:00:00+00:00"},
              "extendedProperties": {"private": {"sluice-track-uid": "",
                                                 "sluice-track-lead": "other-lead"}}}
    ours = parse_ics("BEGIN:VEVENT\r\nDTSTART:20261001T100000Z\r\nSUMMARY:Screen\r\n"
                     "END:VEVENT")
    assert ours is not None and ours.uid == "", "fixture must be the UID-less invite"

    c = FakeGoogleClient(events=[theirs])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ours) == "created"
    assert not c.updated and not c.deleted, "another lead's event was treated as our own"
    assert c.tag_queries == [], "an empty-value query must never be sent at all"

    d = FakeGoogleClient(events=[theirs])
    ours.method = "CANCEL"
    assert sync_event(d, TrackConfig(), lead_slug="example-lead", ics=ours) == "present"
    assert not d.deleted, "a cancel deleted an event belonging to a different lead"


def test_an_EMPTY_tag_is_read_as_untagged_so_it_still_blocks_a_duplicate():
    """The distinct consequence of `_uid_of` normalising an empty tag to None.

    Abstaining on an absent UID is only half an answer: having refused to recognise our own
    UID-less entry, we must not then cheerfully book a second one on top of it. Reading the
    empty tag as NO tag is what makes `_foreign_at_start` see that entry -- an event whose tag
    is empty really is indistinguishable from an untagged one -- so the slot is already
    occupied and the booking is refused and REPORTED rather than duplicated.

    This is the test that reaches past `_find_ours`' own no-UID guard. With that guard alone,
    `mine` is empty, nothing is treated as foreign, and an insert lands on top of the entry we
    already had. Witnessed: reverting `_uid_of` to return the raw tag reds this with
    `created`."""
    from sluice.track.ics import parse_ics

    already_booked = {"id": "ours-from-a-previous-run",
                      "start": {"dateTime": "2026-10-01T10:00:00+00:00"},
                      "extendedProperties": {"private": {"sluice-track-uid": "",
                                                         "sluice-track-lead": "example-lead"}}}
    ics = parse_ics("BEGIN:VEVENT\r\nDTSTART:20261001T100000Z\r\nSUMMARY:Screen\r\nEND:VEVENT")
    assert ics is not None and ics.uid == ""

    c = FakeGoogleClient(events=[already_booked])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "foreign"
    assert not c.inserted, "booked a second entry on top of one we already had"
    assert not c.updated and not c.deleted


def test_a_CANCEL_removes_EVERY_entry_of_ours_not_just_the_one_in_the_window():
    """The population this whole fix serves is the one most likely to hit this.

    An operator who already suffered #146 has BOTH the orphan at the old time and the duplicate
    at the new one, each carrying this exact tag. The window scan sees only the one at the
    current time. Deleting that and reporting `cancelled` is a positive claim the interview is
    gone while the orphan is still in the calendar -- the #138 conflation exactly, arriving
    through a new door.

    So a cancel asks by tag ALWAYS, even when the window already found something, and removes
    every entry it finds. Removal is the one operation where that is unambiguous: the interview
    is cancelled, so every entry we made for it is stale."""
    orphan = _ours(event_id="orphan-at-old-time")
    dupe = _ours(start_iso="2026-10-01T10:00:00+00:00", event_id="duplicate-at-new-time")

    c = FakeGoogleClient(events=[orphan, dupe])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "cancelled"
    assert sorted(c.deleted) == ["duplicate-at-new-time", "orphan-at-old-time"], (
        f"a cancelled interview was left in the calendar: deleted only {c.deleted}")


def test_a_cancel_asks_by_tag_EVEN_WHEN_the_window_already_found_one():
    """The mechanism behind the test above, pinned separately.

    Restoring the miss-path-only guard on the cancel arm would leave that test green ONLY
    because of the orphan; a reader could reasonably 'tidy' the condition back to `if not mine`
    and this is what says no."""
    c = FakeGoogleClient(events=[_ours(start_iso="2026-10-01T10:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "cancelled"
    assert c.tag_queries == [("sluice-track-uid", "u1")], (
        "a cancel must confirm there is nothing of ours elsewhere before claiming removal")


def test_a_RESCHEDULE_onto_two_entries_of_ours_refuses_to_guess():
    """Removal is unambiguous with several entries; a move is not.

    Both entries here sit outside the window, so the tag query is what finds them -- which is
    the shape a long reschedule actually produces. `events.list` is called with no `orderBy`,
    so "the first" is whatever Google happened to return: updating it moves an arbitrary one
    and silently leaves the other. Only a human can say which is real."""
    both = [_ours(event_id="one-of-two"), _ours(event_id="two-of-two")]
    c = FakeGoogleClient(events=both)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "unresolved"
    assert not c.updated and not c.inserted, "moved an arbitrary one of two candidates"


def test_the_duplicate_warning_names_the_ENTRIES_and_not_the_inbound_uid(caplog):
    """A warning telling you to delete something must say WHICH something.

    Event ids, not the ics UID, and that is better on both axes at once. The operator can act
    on an event id -- it is what identifies an entry in the calendar UI and the API -- whereas
    the UID identifies the invite and cannot be searched for by hand. And an inbound UID is
    counterparty-supplied text that sometimes encodes the sender's domain, which is the leak
    `search_messages` deliberately keeps its query out of the log for: a log line travels
    further than the mailbox does. A Google event id is opaque about who the interview is
    with."""
    uid = "uid-7c11@mail.example-tidal.invalid"
    both = [_ours(uid=uid, event_id="one-of-two"), _ours(uid=uid, event_id="two-of-two")]
    c = FakeGoogleClient(events=both)
    with caplog.at_level("WARNING", logger="sluice.track.calendar_sync"):
        assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                          ics=_ics(uid=uid)) == "unresolved"
    said = " ".join(r.getMessage() for r in caplog.records
                    if r.name == "sluice.track.calendar_sync")
    assert said, "refusing to act must not be silent -- nothing else tells the operator"
    assert "one-of-two" in said and "two-of-two" in said, (
        f"the entries to delete are not named, so the advice cannot be followed: {said}")
    assert uid not in said, f"the warning leaked the inbound invite id: {said}"


def test_a_tag_query_that_RAISES_does_not_fall_through_to_an_insert():
    """The narrow softening that the missing-method test cannot catch.

    `test_a_client_MISSING_the_tag_query_fails_loudly...` catches a `getattr` guard and a bare
    `except Exception`. It does NOT catch `except HttpError: tagged = []`, which looks careful,
    reads as defensive, and silently reinstates #146: a transport blip on the lookup would
    fall through to an insert and duplicate an interview we simply failed to look for.

    Propagating is the accepted cost, and it is the choice #142 already made for this seam --
    a transport error becomes a retryable per-message failure, a dead-letter row and a digest
    line, rather than a guess."""
    import pytest

    class _Boom(FakeGoogleClient):
        def find_events_by_private_property(self, name, value, max_results=2500):
            raise RuntimeError("google said no")

    c = _Boom(events=[])
    with pytest.raises(RuntimeError, match="google said no"):
        sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics())
    assert not c.inserted, "swallowing the error would duplicate an interview"


def test_a_uid_too_long_for_google_still_round_trips():
    """Google SILENTLY TRUNCATES a private-property value past 1024 characters.

    `parse_ics` puts no bound on a UID -- it is `value.strip()` off a third-party invite -- so
    a longer one was written cut short while `_uid_of` and the tag query both searched for the
    whole string. Our own event would be unfindable by window OR tag, and a fresh duplicate
    inserted on every single run, forever: strictly worse than #146, which at least needs a
    long reschedule to fire.

    Truncating on BOTH sides closes the round trip by construction. The echo below is what
    Google would actually hand back on the next run."""
    from sluice.track.calendar_sync import _UID_KEY, _UID_VALUE_MAX

    long_uid = "u" * (_UID_VALUE_MAX + 500)
    first = FakeGoogleClient(events=[])
    sync_event(first, TrackConfig(), lead_slug="example-lead", ics=_ics(uid=long_uid))
    stored = first.inserted[0]["extendedProperties"]["private"][_UID_KEY]
    assert len(stored) == _UID_VALUE_MAX, "we must not hand Google more than it will keep"

    # The echo goes OUT OF WINDOW, at the old time. Placed at the new start it would be found
    # by the window scan and the tag query would never run -- so the test would prove the write
    # side agrees with the window read and say nothing about the query, which is the half that
    # matters here. Measured: with the echo in-window, dropping `_uid_tag` from the QUERY alone
    # left the entire suite green.
    echoed = {"id": "ev1", "start": {"dateTime": _OLD},
              "extendedProperties": {"private": {_UID_KEY: stored}}}
    again = FakeGoogleClient(events=[echoed])
    assert sync_event(again, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(uid=long_uid)) == "updated"
    assert not again.inserted, "a second run duplicated the entry it had just booked"
    assert again.tag_queries == [(_UID_KEY, stored)], (
        "the query must ask for the value Google actually stored, not the full UID -- "
        f"asked for {again.tag_queries}")


def test_the_tag_KEY_fits_inside_googles_own_limit():
    """Google silently DROPS a private-property key over 44 characters -- no error, the tag
    simply never lands, and every lookup then reads as "we never created this". A rename is
    the only way to trip this, and a rename is exactly the change that would not think to
    check."""
    from sluice.track.calendar_sync import _UID_KEY

    assert len(_UID_KEY) <= 44, (
        f"_UID_KEY is {len(_UID_KEY)} chars; Google drops keys over 44 SILENTLY")


# ---- through engine.run, because a return value is not an outcome --------------------------

def _rescheduled_invite_client(uid="u1"):
    """One message carrying a real reschedule: an .ics 78 days out, and our own tagged event
    still sitting at the original time."""
    from tests.test_track_engine import OneMsgClient

    class _Client(OneMsgClient):
        def __init__(self):
            super().__init__()
            self.events = [_ours(uid=uid)]

        def get_message(self, mid):
            msg = super().get_message(mid)
            msg["attachments"] = [{
                "filename": "invite.ics", "mime": "text/calendar",
                "data": (f"BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:{uid}\r\n"
                         "DTSTART:20261001T100000Z\r\nDTEND:20261001T103000Z\r\n"
                         "SUMMARY:Screen\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()}]
            return msg

    return _Client()


def test_a_rescheduled_invite_END_TO_END_moves_the_entry_and_leaves_no_second_one():
    """`sync_event`'s return value is not the thing that hurts anybody.

    A unit test that stops at that return value cannot see a routing gap -- an outcome can be
    correct and still reach no calendar and no human. `tests/test_track_unresolved_routing.py`
    exists for exactly that reason and drives `engine.run` end to end; this is the same
    treatment for #146. It runs the whole path -- Gmail message, .ics parse, classify,
    reconcile, engine.run -- and asserts on the CALLS that reached Google and on the run report
    the operator actually sees.
    """
    import json

    from sluice.track import engine as E
    from tests.test_track_engine import FakeBackend, _dl, _vault

    v, _path = _vault("applied")
    c = _rescheduled_invite_client()
    rep = E.run(v, TrackConfig(), c,
                FakeBackend(json.dumps({"lead": "Example Tidal", "type": "interview",
                                        "confidence": 0.9, "when": None, "links": [],
                                        "materials": [], "summary": "rescheduled"})),
                seen=set(), deadletter=_dl(), now_iso="2026-07-10T12:00:00+00:00")

    assert not c.inserted, "the operator's calendar now shows the interview twice"
    assert [eid for eid, _ in c.updated] == ["ev1"], (
        "the original entry must have MOVED; leaving it behind orphans it at the old time "
        "with nothing marking it stale")
    body = c.updated[0][1]
    assert body["start"]["dateTime"].startswith("2026-10-01T10:00"), body["start"]
    assert rep.calendar_added == 1 and not rep.failures
    # The non-emptiness check FIRST. `all()` over an empty list is True, so without it a run
    # that reconciled nothing at all -- an .ics that failed to parse, a classification that
    # matched no lead -- would satisfy the line below while proving the opposite of what it
    # claims. Same shape as the empty-`caplog` trap that
    # `test_track_calendar_sync.py::test_resolved_zone_and_present_outcome_stay_silent` carries
    # a positive control for: an assertion over an empty collection flatters the code.
    assert rep.results, "nothing was reconciled, so the assertion below would be vacuous"
    assert all(not r.needs_review for r in rep.results), (
        f"a resolved reschedule must not also nag a human: "
        f"{[r.needs_review for r in rep.results if r.needs_review]}")
