"""A truncated calendar window must not read as "nothing of ours".

Both wave-1 reviewers converged here. `list_events`' own docstring states the harm exactly --
"an event of ours sitting off-page makes `sync_event` insert a duplicate, or on a cancel skip
the delete entirely and report `present` while the interview stays in the calendar" -- and
then `list_events` logged the warning and returned the items, so `truncated` never reached
`_find_ours`. Absence still read as "we never created this".

#138 fixed ONE of the three causes `_find_ours` returns None for (a cancel with no DTSTART).
This is the second: the window was short and we know it. The third (the event sits outside
`calendar_lookahead_days`) is genuinely indistinguishable from absence without a UID-keyed
query, and is left alone rather than guessed at.

Also pinned here: the two helpers used to make the SAME `list_events` call with identical
bounds and no sharing, so raising the page cap turned 2 requests per invite into up to 20.
"""
from datetime import datetime, timezone

from sluice.track.calendar_sync import sync_event
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent


class _TruncatingClient:
    """A client whose window is short and SAYS so, like the real one after #137."""

    def __init__(self, events=(), truncated=True):
        self.events, self.truncated = list(events), truncated
        self.calls = 0
        self.deleted, self.inserted, self.updated = [], [], []

    def list_events(self, t0, t1, return_truncated=False):
        self.calls += 1
        return (list(self.events), self.truncated) if return_truncated else list(self.events)

    def insert_event(self, body):
        self.inserted.append(body)
        return "new"

    def update_event(self, event_id, body):
        self.updated.append(event_id)
        return event_id

    def delete_event(self, event_id):
        self.deleted.append(event_id)


def _ics(cancelled=False):
    e = IcsEvent(uid="u1", summary="Screen",
                 start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    if cancelled:
        e.method = "CANCEL"
    return e


def test_a_cancel_over_a_TRUNCATED_window_is_unresolved_not_present():
    """The reviewer traced this end to end: `present`, `action="calendar"` matching no branch,
    no dead-letter row, and `seen.add` consuming the message -- the cancelled interview left
    in the calendar with every trace gone."""
    c = _TruncatingClient(events=[], truncated=True)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "unresolved"
    assert not c.deleted


def test_a_cancel_over_a_COMPLETE_window_is_still_present():
    # The honest "we looked properly and found nothing" case keeps its quiet answer.
    c = _TruncatingClient(events=[], truncated=False)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "present"


def test_an_INSERT_over_a_truncated_window_is_unresolved_rather_than_a_duplicate():
    """The other half of the same harm: our event may be off-page, so inserting would
    duplicate it. Refusing and surfacing beats silently double-booking."""
    c = _TruncatingClient(events=[], truncated=True)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "unresolved"
    assert not c.inserted, "must not create a second entry it cannot rule out"


def test_a_complete_window_still_inserts():
    c = _TruncatingClient(events=[], truncated=False)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "created"
    assert c.inserted


def test_finding_our_event_wins_even_if_the_window_was_short():
    """Truncation only matters when we found NOTHING. A positive hit is a fact."""
    tagged = {"id": "ev1", "start": {"dateTime": "2026-07-15T10:00:00+00:00"},
              "extendedProperties": {"private": {"sluice-track-uid": "u1"}}}
    c = _TruncatingClient(events=[tagged], truncated=True)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead",
                      ics=_ics(cancelled=True)) == "cancelled"
    assert c.deleted == ["ev1"]


def test_the_window_is_fetched_ONCE_per_sync_not_twice():
    """`_find_ours` and `_foreign_at_start` made the identical call with identical bounds.

    Pre-#137 that was 2 requests per invite; after it, each is a walk of up to 10 pages, so
    20 per invite and up to 400 sequential round trips for a 20-invite run.
    """
    c = _TruncatingClient(events=[], truncated=False)
    sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics())
    assert c.calls == 1, f"fetched the same window {c.calls} times"


def test_a_client_without_the_truncation_kwarg_still_works():
    """The Fetcher-shaped protocol is satisfied by fakes across the suite and by any
    third-party client; a new kwarg must not become a hard requirement."""
    class _Old:
        def __init__(self):
            self.inserted = []

        def list_events(self, t0, t1):
            return []

        def insert_event(self, body):
            self.inserted.append(body)
            return "x"

    c = _Old()
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "created"
