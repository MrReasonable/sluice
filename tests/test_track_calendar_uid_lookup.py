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
    assert list(c.inserted[0]["extendedProperties"]["private"]) == \
        ["sluice-track-uid", "sluice-track-lead"]
    assert c.tag_queries == [("sluice-track-uid", "u1")]


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
    ship, never a new one. Each row is an outcome the window scan reaches on its own."""
    cfg = TrackConfig()
    inside = "2026-10-01T09:00:00+00:00"    # within the window centred on the new start

    nothing = _FilterMatchesNothing(events=[])
    assert sync_event(nothing, cfg, lead_slug="example-lead", ics=_ics()) == "created"
    assert nothing.inserted, "a genuinely new invite must still be booked"

    moved = _FilterMatchesNothing(events=[_ours(start_iso=inside)])
    assert sync_event(moved, cfg, lead_slug="example-lead", ics=_ics()) == "updated"

    same = _FilterMatchesNothing(events=[_ours(start_iso="2026-10-01T10:00:00+00:00")])
    assert sync_event(same, cfg, lead_slug="example-lead", ics=_ics()) == "present"

    short = _FilterMatchesNothing(events=[], truncated=True)
    assert sync_event(short, cfg, lead_slug="example-lead", ics=_ics()) == "unresolved"
    assert not short.inserted

    cancel = _FilterMatchesNothing(events=[_ours(start_iso=inside)])
    assert sync_event(cancel, cfg, lead_slug="example-lead", ics=_ics(cancelled=True)) == "cancelled"


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
