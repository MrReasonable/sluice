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

    # A hard call bound. `list_next` finds its position with `self.pages.index(response)`,
    # and 10_000 identical zero-item pages all compare equal, so the index is always 0 --
    # deleting the page cap under test gives an INFINITE LOOP, not a failure. In a sub-second
    # suite that reads as wedged CI rather than as a caught regression, which is the one
    # outcome a mutation witness must never produce.
    _MAX_CALLS = 400

    def __init__(self, pages, item_key):
        self.pages, self.item_key = pages, item_key
        self.calls = []
        self._next_calls = 0

    def list(self, **kw):
        self.calls.append(kw)
        return _Exec(self.pages[0])

    def list_next(self, previous_request, previous_response):
        self._next_calls += 1
        if self._next_calls > self._MAX_CALLS:
            raise AssertionError(
                f"list_next called {self._next_calls} times -- the pager is not bounded. "
                "Failing fast rather than hanging the suite.")
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
    ids, _trunc = _client_with(monkeypatch, gmail=gmail).search_messages("after:2026/01/01")
    assert ids == ["a", "b", "c", "d"], "a truncated page was read as the complete set"


def test_search_messages_stops_at_the_last_page(monkeypatch):
    gmail = _GmailPaged([{"messages": [{"id": "a"}]}])
    assert _client_with(monkeypatch, gmail=gmail).search_messages("q") == (["a"], False)
    assert len(gmail.messages_ep.calls) == 1, "no nextPageToken means exactly one request"


def test_search_messages_is_bounded_so_a_runaway_cannot_hang_a_run(monkeypatch):
    """A cap is still needed -- just an HONEST one that reports truncation.

    Unbounded paging over a mailbox is its own outage: a cron run that never returns is
    indistinguishable from a hung one. The bound is on TOTAL ids, not per page, and hitting
    it is loud (see the caller's warning) rather than silently short.
    """
    endless = [{"messages": [{"id": f"m{i}"}], "nextPageToken": "t"} for i in range(500)]
    gmail = _GmailPaged(endless)
    ids, _trunc = _client_with(monkeypatch, gmail=gmail).search_messages("q", max_results=10)
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
    got, _trunc = _client_with(monkeypatch, cal=cal).list_events("2026-01-01T00:00:00+00:00",
                                                         "2026-03-01T00:00:00+00:00")
    assert [e["id"] for e in got] == ["e1", "e2"], (
        "our own tagged event sitting on page 2 would read as absent, so sync_event would "
        "insert a duplicate -- or skip a cancel's delete")


def test_list_events_is_bounded_too(monkeypatch):
    endless = [{"items": [{"id": f"e{i}"}], "nextPageToken": "t"} for i in range(500)]
    cal = _CalPaged(endless)
    got, _trunc = _client_with(monkeypatch, cal=cal).list_events("a", "b", max_results=5)
    assert len(got) == 5


@pytest.mark.parametrize("caller", ["search_messages", "list_events"])
def test_the_pager_is_shared_so_the_two_endpoints_cannot_drift(monkeypatch, caller):
    """One helper, two callers -- asserted by OBSERVING the call, not by `hasattr`.

    The previous version's whole body was `assert hasattr(gc, "_paged")`, which stays green
    if both callers hand-roll their own loops beside an unused helper. That is the exact
    state this issue found them in: two loops, one of which quietly dropped `nextPageToken`.
    """
    from sluice.track import google_client as gc

    seen = {}

    def _spy(endpoint, params, item_key, max_results, max_pages=100):
        seen["item_key"] = item_key
        seen["max_results"] = max_results
        return [], False

    monkeypatch.setattr(gc, "_paged", _spy)
    c = _client_with(monkeypatch, gmail=_GmailPaged([{"messages": []}]),
                     cal=_CalPaged([{"items": []}]))
    if caller == "search_messages":
        c.search_messages("q")
        assert seen.get("item_key") == "messages"
    else:
        c.list_events("a", "b")
        assert seen.get("item_key") == "items"
    assert seen.get("max_results"), "the caller must pass its own bound through the pager"


# ---- honesty about what was dropped -------------------------------------------------------

def test_truncated_is_true_when_the_CAP_dropped_items_even_on_the_last_page(monkeypatch):
    """The loss is `items[:max_results]`, not "were there more pages".

    When the final page carries the total past the cap, the slice throws away items already
    in hand AND `list_next` returns None -- so answering "more pages?" reported `truncated=
    False` while the slice dropped up to a full page (249 items on shipped defaults). That is
    this branch's own
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


def test_a_slice_OVERSHOOT_short_circuits_before_the_advisory_probe(monkeypatch):
    """Renamed to say what it actually covers.

    It was called "a probe failure does not lose the items already collected", but its fixture
    (10 items, cap 5) makes `lost = len(items) > max_results` True before the `try`, so the
    raising `list_next` below is never called and the probe branch is never entered. It was a
    duplicate of the slice-overshoot test wearing the probe test's name -- a false signpost
    that the probe path had two witnesses when it had one
    (`test_the_probe_failure_branch_is_actually_REACHED`).

    What it genuinely pins is worth keeping: when the slice already proves loss, we do not
    spend a round trip asking, and a broken `list_next` cannot affect the answer.
    """
    from sluice.track.google_client import _paged

    class _Raises(_PagedList):
        def list_next(self, previous_request, previous_response):
            raise RuntimeError("transport blew up")

    ep = _Raises([{"items": [{"id": f"e{i}"} for i in range(10)]}], "items")
    items, truncated = _paged(ep, {}, "items", 5)
    assert len(items) == 5, "items already in hand must survive"
    assert truncated is True
    assert ep._next_calls == 0, (
        "the slice already proved loss, so the advisory probe must not be consulted at all")


def test_truncated_is_true_when_the_cap_lands_EXACTLY_and_more_pages_remain(monkeypatch):
    """The core #137 signal, and it was unwitnessed.

    Deleting `lost = endpoint.list_next(...) is not None` left the whole suite green: the
    slice-overshoot case was tested, this one was not. It is the commoner shape -- the cap
    falls on a page boundary and there is simply more behind it.
    """
    from sluice.track.google_client import _paged

    pages = [{"items": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t"},
             {"items": [{"id": "c"}]}]
    ep = _PagedList(pages, "items")
    items, truncated = _paged(ep, {}, "items", 2)      # cap lands exactly on page one
    assert len(items) == 2
    assert truncated is True, "more pages remained and we said the answer was complete"


def test_the_probe_failure_branch_is_actually_REACHED(monkeypatch):
    """An earlier version of this test could not enter the branch it named.

    Its fixture had 10 items with max_results=5, so `lost = len(items) > max_results` was
    already True and the `try` was never executed -- the mutation `except: lost = True` ->
    `lost = False` survived. Reaching it needs the cap hit EXACTLY, so the slice drops
    nothing, plus a raising `list_next`.
    """
    from sluice.track.google_client import _paged

    class _Raises(_PagedList):
        def list_next(self, previous_request, previous_response):
            raise RuntimeError("transport blew up")

    ep = _Raises([{"items": [{"id": "a"}, {"id": "b"}]}], "items")
    items, truncated = _paged(ep, {}, "items", 2)      # exact cap: the try IS entered
    assert len(items) == 2, "items in hand must survive an advisory probe failing"
    assert truncated is True, "an unanswerable probe must assume the worse, honest answer"


def _warns(monkeypatch, caplog, *, gmail=None, cal=None, **kw):
    with caplog.at_level("WARNING", logger="sluice.track.google_client"):
        c = _client_with(monkeypatch, gmail=gmail, cal=cal)
        if gmail is not None:
            c.search_messages("q", **kw)
        else:
            c.list_events("a", "b", **kw)
    return [r.getMessage() for r in caplog.records
            if r.name == "sluice.track.google_client"]


def test_the_gmail_truncation_WARNING_actually_fires(monkeypatch, caplog):
    # Deleting either caller's `if truncated:` block left the suite green, while the tests'
    # own docstring claimed "hitting it is loud".
    pages = [{"messages": [{"id": f"m{i}"} for i in range(5)], "nextPageToken": "t"},
             {"messages": [{"id": "z"}]}]
    said = _warns(monkeypatch, caplog, gmail=_GmailPaged(pages), max_results=3)
    assert said, "a truncated gmail search must say so"
    assert "3" in " ".join(said), "the warning should name the cap that was hit"


def test_the_calendar_truncation_WARNING_actually_fires(monkeypatch, caplog):
    pages = [{"items": [{"id": f"e{i}"} for i in range(5)], "nextPageToken": "t"},
             {"items": [{"id": "z"}]}]
    said = _warns(monkeypatch, caplog, cal=_CalPaged(pages), max_results=3)
    assert said, "a truncated calendar window must say so"


def test_a_complete_read_warns_about_nothing(monkeypatch, caplog):
    # A warning that fires on every run stops being read.
    said = _warns(monkeypatch, caplog, cal=_CalPaged([{"items": [{"id": "e1"}]}]))
    assert said == []


# ---- the #137 headline itself, which nothing pinned ---------------------------------------

def test_search_messages_DEFAULT_cap_is_the_post_137_value(monkeypatch):
    """The change #137 is about, and it was unwitnessed.

    Every other test here passes an explicit cap or uses fewer than ten items, so reverting
    the default from 500 to the pre-#137 50 left the whole suite green. `engine.run` filters
    against `seen` AFTER the fetch, so a small cap is consumed by already-processed ids and
    the unseen ones -- the entire point of the call -- never arrive.
    """
    pages = [{"messages": [{"id": f"m{i}"} for i in range(j * 40, j * 40 + 40)],
              **({"nextPageToken": "t"} if j < 2 else {})} for j in range(3)]
    gmail = _GmailPaged(pages)
    ids, truncated = _client_with(monkeypatch, gmail=gmail).search_messages("q")
    assert len(ids) == 120, (
        f"the default cap truncated a 120-message window to {len(ids)} -- a pre-#137 cap of "
        "50 would silently starve the oldest unprocessed messages")
    assert truncated is False


def test_list_events_DEFAULT_cap_is_the_post_137_value(monkeypatch):
    """Same gap on the calendar side. The window is 2 * calendar_lookahead_days (90 days by
    default) with `singleEvents=True` expanding recurrences, so a low cap is reached easily
    and reads as "our event is not there"."""
    pages = [{"items": [{"id": f"e{i}"} for i in range(j * 250, j * 250 + 250)],
              **({"nextPageToken": "t"} if j < 3 else {})} for j in range(4)]
    cal = _CalPaged(pages)
    got, truncated = _client_with(monkeypatch, cal=cal).list_events("a", "b")
    assert len(got) == 1000, f"the default cap truncated a 1000-event window to {len(got)}"
    assert truncated is False
