"""A truncated calendar window must not read as "nothing of ours".

Both wave-1 reviewers converged here. `list_events`' own docstring states the harm exactly --
"an event of ours sitting off-page makes `sync_event` insert a duplicate, or on a cancel skip
the delete entirely and report `present` while the interview stays in the calendar" -- and
then `list_events` logged the warning and returned the items, so `truncated` never reached
`_find_ours`. Absence still read as "we never created this".

Three distinct causes make `_find_ours` return None, and they are named rather than numbered
here because an earlier draft numbered them and then referred to "the first" and "the second"
meaning something else six lines later:

  NO-DTSTART   -- a cancel carrying only a UID. Fixed by #138.
  SHORT-WINDOW -- the read was truncated and we know it. Fixed here, and this file is its home.
  OUT-OF-WINDOW -- the event exists but sits beyond `calendar_lookahead_days`. Left alone
                   rather than guessed at until #146 gave it a UID-keyed query with no time
                   bound; `tests/test_track_calendar_uid_lookup.py` covers that one.

SHORT-WINDOW and OUT-OF-WINDOW stay distinct, and the tests below are what keep them so. A
short window is "we could not read the whole answer"; an out-of-window event is "we asked the
wrong question". The tag query answers the second and deliberately does not forgive the first,
because its own matching behaviour has never been executed against a live calendar.

Also pinned here: the two helpers used to make the SAME `list_events` call with identical
bounds and no sharing, so raising the page cap turned 2 requests per invite into up to 20.
"""
from datetime import datetime, timezone

from sluice.track.calendar_sync import sync_event
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent


class _TruncatingClient:
    """A client whose window is short and SAYS so, like the real one after #137."""

    def __init__(self, events=(), truncated=True, tag_truncated=False):
        self.events, self.truncated = list(events), truncated
        self.tag_truncated = tag_truncated
        self.calls = 0
        # Counted SEPARATELY from `calls`. The fetch-once test below asserts on the window
        # call, and folding the tag query into the same counter would make that assertion
        # read as a regression the moment #146's lookup was added -- an honest test breaking
        # on an unrelated correct change is how a suite gets its assertions loosened.
        self.tag_calls = 0
        self.deleted, self.inserted, self.updated = [], [], []

    def list_events(self, t0, t1, max_results=2500):
        self.calls += 1
        return list(self.events), self.truncated

    def find_events_by_private_property(self, name, value, max_results=2500):
        self.tag_calls += 1
        return ([e for e in self.events
                 if (e.get("extendedProperties", {}).get("private", {}) or {}).get(name) == value],
                self.tag_truncated)

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
    # And the #146 tag lookup adds exactly ONE more, not one per helper -- the counter existed
    # here with no assertion on it, which is how a second walk would have slipped in unnoticed.
    assert c.tag_calls == 1, f"asked for the tag {c.tag_calls} times"


def test_a_kwargs_shaped_client_is_not_silently_mis_unpacked():
    """This replaces a test that asserted the opposite and was wrong.

    The old design made the pair opt-in (`return_truncated=True`) with a `try/except TypeError`
    fallback for clients predating it, and the test that "proved" compatibility defined its
    fake as `def list_events(self, t0, t1)` -- POSITIONAL-ONLY, the one shape the probe
    actually handles. A `**kwargs` client, which is what two of the three fakes in this repo
    are, SWALLOWS the unknown kwarg instead of raising, so no TypeError fired and a bare list
    reached the tuple unpack: `ValueError: not enough values to unpack` with an empty window,
    and a silent mis-bind of two event dicts to `(events, truncated)` with two.

    The contract is now unconditional, so the probe is gone. What is worth pinning is that a
    `**kwargs` client reaches `sync_event` intact rather than being quietly mis-read.
    """
    class _Kwargs:
        def __init__(self):
            self.inserted = []

        def list_events(self, *a, **k):
            return [], False

        def find_events_by_private_property(self, *a, **k):
            return [], False

        def insert_event(self, body):
            self.inserted.append(body)
            return "x"

    c = _Kwargs()
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics()) == "created"
    assert c.inserted, "the insert must actually have happened"


def test_a_client_MISSING_the_tag_query_fails_loudly_rather_than_skipping_it():
    """Why #146's lookup is a new METHOD and not a new argument to `list_events`.

    The swallowed-kwarg failure above is a property of ARGUMENTS: `**kwargs` absorbs one it
    does not know, so the caller learns nothing. A method name cannot be absorbed that way --
    a client that has not implemented it raises AttributeError at the call, which is the
    failure being visible rather than the lookup being skipped.

    Pinned because the tempting softening is one line: wrapping the call in
    `getattr(client, "find_events_by_private_property", None)` and carrying on when it is
    absent. That would restore the silence exactly -- every fake in this repo would quietly
    stop exercising the fix while its tests went on passing.
    """
    import pytest

    class _NoTagQuery:
        def __init__(self):
            self.inserted = []

        def list_events(self, *a, **k):
            return [], False

        def insert_event(self, body):
            self.inserted.append(body)
            return "x"

    c = _NoTagQuery()
    with pytest.raises(AttributeError, match="find_events_by_private_property"):
        sync_event(c, TrackConfig(), lead_slug="example-lead", ics=_ics())
    assert not c.inserted, "and it must fail BEFORE writing anything"
