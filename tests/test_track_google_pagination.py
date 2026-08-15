"""Both Google list calls must read every page, or a truncated one reads as the whole set.

#137. `search_messages` capped at 50 and `list_events` at 250, and both discarded
`nextPageToken`. Neither cap is generous for the windows this code asks for:

- `list_events` spans `2 * calendar_lookahead_days` (default 90 days) with
  `singleEvents=True`, which EXPANDS recurrences -- one daily standup contributes ~90 items
  by itself.
- `search_messages` runs over the Gmail lookback window, and dedup against `seen` happens
  AFTER the fetch, so already-processed messages consume the cap before a new one is reached.

A truncated page is not a smaller answer, it is a WRONG one: `_find_ours` returns None for an
event that exists just off-page, so `sync_event` concludes "we never created this" and inserts
a duplicate -- or, on a cancel, never issues the delete. Both are reported as ordinary success.
"""
import pytest

from sluice.track.google_client import RealGoogleClient


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _PagedList:
    """A Google list endpoint that hands back `pages` in order, recording each request.

    `list_next` is the paging protocol the real client library exposes: given the previous
    request and response it returns the next request, or None at the end. Modelling THAT
    rather than a `pageToken` kwarg matters -- it is what the discovery-built service does.
    """

    def __init__(self, pages, item_key):
        self.pages, self.item_key = pages, item_key
        self.calls = []

    def list(self, **kw):
        self.calls.append(kw)
        return _Exec(self.pages[0])

    def list_next(self, previous_request, previous_response):
        idx = self.pages.index(previous_response)
        if idx + 1 >= len(self.pages):
            return None
        nxt = self.pages[idx + 1]
        self.calls.append({"page": idx + 1})
        return _Exec(nxt)


def _client_with(monkeypatch, *, gmail=None, cal=None):
    c = RealGoogleClient.__new__(RealGoogleClient)          # no OAuth, no network
    c._gmail = c._cal = None
    if gmail is not None:
        monkeypatch.setattr(c, "_gmail_svc", lambda: gmail, raising=False)
    if cal is not None:
        monkeypatch.setattr(c, "_cal_svc", lambda: cal, raising=False)
    return c


# ---- messages ---------------------------------------------------------------------------

class _GmailPaged:
    def __init__(self, pages):
        self.messages_ep = _PagedList(pages, "messages")

    def users(self):
        return self

    def messages(self):
        return self.messages_ep


def test_search_messages_reads_every_page(monkeypatch):
    pages = [{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t1"},
             {"messages": [{"id": "c"}], "nextPageToken": "t2"},
             {"messages": [{"id": "d"}]}]
    gmail = _GmailPaged(pages)
    ids = _client_with(monkeypatch, gmail=gmail).search_messages("after:2026/01/01")
    assert ids == ["a", "b", "c", "d"], "a truncated page was read as the complete set"


def test_search_messages_stops_at_the_last_page(monkeypatch):
    gmail = _GmailPaged([{"messages": [{"id": "a"}]}])
    assert _client_with(monkeypatch, gmail=gmail).search_messages("q") == ["a"]
    assert len(gmail.messages_ep.calls) == 1, "no nextPageToken means exactly one request"


def test_search_messages_is_bounded_so_a_runaway_cannot_hang_a_run(monkeypatch):
    """A cap is still needed -- just an HONEST one that reports truncation.

    Unbounded paging over a mailbox is its own outage: a cron run that never returns is
    indistinguishable from a hung one. The bound is on TOTAL ids, not per page, and hitting
    it is loud (see the caller's warning) rather than silently short.
    """
    endless = [{"messages": [{"id": f"m{i}"}], "nextPageToken": "t"} for i in range(500)]
    gmail = _GmailPaged(endless)
    ids = _client_with(monkeypatch, gmail=gmail).search_messages("q", max_results=10)
    assert len(ids) == 10


# ---- events -----------------------------------------------------------------------------

class _CalPaged:
    def __init__(self, pages):
        self.events_ep = _PagedList(pages, "items")

    def events(self):
        return self.events_ep


def test_list_events_reads_every_page(monkeypatch):
    pages = [{"items": [{"id": "e1"}], "nextPageToken": "t1"},
             {"items": [{"id": "e2"}]}]
    cal = _CalPaged(pages)
    got = _client_with(monkeypatch, cal=cal).list_events("2026-01-01T00:00:00+00:00",
                                                         "2026-03-01T00:00:00+00:00")
    assert [e["id"] for e in got] == ["e1", "e2"], (
        "our own tagged event sitting on page 2 would read as absent, so sync_event would "
        "insert a duplicate -- or skip a cancel's delete")


def test_list_events_is_bounded_too(monkeypatch):
    endless = [{"items": [{"id": f"e{i}"}], "nextPageToken": "t"} for i in range(500)]
    cal = _CalPaged(endless)
    got = _client_with(monkeypatch, cal=cal).list_events("a", "b", max_results=5)
    assert len(got) == 5


@pytest.mark.parametrize("endpoint", ["messages", "events"])
def test_the_pager_is_shared_so_the_two_endpoints_cannot_drift(endpoint):
    """One helper, two callers.

    Two hand-rolled paging loops is how one of them keeps `nextPageToken` and the other
    quietly does not -- which is the state this issue found them in.
    """
    from sluice.track import google_client as gc

    assert hasattr(gc, "_paged"), "expected a single shared pager"


# ---- honesty about what was dropped -------------------------------------------------------

def test_truncated_is_true_when_the_CAP_dropped_items_even_on_the_last_page(monkeypatch):
    """The loss is `items[:max_results]`, not "were there more pages".

    When the final page carries the total past the cap, the slice throws away items already
    in hand AND `list_next` returns None -- so answering "more pages?" reported `truncated=
    False` while 239 calendar events vanished on shipped defaults. That is this branch's own
    bug class, reintroduced inside the fix.
    """
    from sluice.track.google_client import _paged

    ep = _PagedList([{"items": [{"id": f"e{i}"} for i in range(8)]}], "items")
    items, truncated = _paged(ep, {}, "items", 5)
    assert len(items) == 5
    assert truncated is True, "dropped 3 items in hand and said nothing"


def test_truncated_is_false_only_when_nothing_was_lost(monkeypatch):
    from sluice.track.google_client import _paged

    ep = _PagedList([{"items": [{"id": "a"}, {"id": "b"}]}], "items")
    items, truncated = _paged(ep, {}, "items", 5)
    assert items and truncated is False


def test_zero_item_pages_with_a_token_cannot_loop_forever(monkeypatch):
    """The cap counts ITEMS, but the loop condition is `request is not None`.

    A page with zero items and a nextPageToken is legal for both Gmail and Calendar, and never
    grows `len(items)` -- so the item cap is never reached and the walk never ends. That
    defeats the cap's stated purpose verbatim: "a cron run that never returns is
    indistinguishable from a hung one".
    """
    from sluice.track.google_client import _paged

    endless = [{"items": [], "nextPageToken": "t"} for _ in range(10_000)]
    ep = _PagedList(endless, "items")
    items, truncated = _paged(ep, {}, "items", 50)
    assert items == []
    assert truncated is True, "hitting the page bound is a truncation and must be reported"


def test_a_probe_failure_does_not_lose_the_items_already_collected(monkeypatch):
    """`list_next` is only an advisory probe at the cap; losing the payload to it is absurd.

    Assume the worse, honest answer instead: report truncated.
    """
    from sluice.track.google_client import _paged

    class _Raises(_PagedList):
        def list_next(self, previous_request, previous_response):
            raise RuntimeError("transport blew up")

    ep = _Raises([{"items": [{"id": f"e{i}"} for i in range(10)]}], "items")
    items, truncated = _paged(ep, {}, "items", 5)
    assert len(items) == 5, "items already in hand must survive an advisory probe failing"
    assert truncated is True
