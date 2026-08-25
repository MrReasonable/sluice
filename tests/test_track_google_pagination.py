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
    # deleting the page cap under test gives an INFINITE LOOP, not a failure. In a suite that
    # is otherwise fast, that reads as wedged CI rather than as a caught regression, which
    # is the one outcome a mutation witness must never produce.
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


def _client_with(monkeypatch, *, gmail=None, cal=None,
                 gmail_max_messages=500, calendar_max_events=2500):
    c = RealGoogleClient.__new__(RealGoogleClient)          # no OAuth, no network
    c._gmail = c._cal = None
    # `__new__` skips `__init__`, so the caps have to be set here. Defaulted to the SHIPPED
    # values rather than to something convenient: these tests exist to pin what an install
    # that configures nothing actually does.
    c.gmail_max_messages = gmail_max_messages
    c.calendar_max_events = calendar_max_events
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


# ---- the UID tag query: the request itself is the contract (#146) --------------------------

def test_the_tag_query_sends_the_FILTER_and_no_time_window(monkeypatch):
    """The filter and the ABSENCE of a window -- the two halves of #146's fix that live in the
    request rather than in our own logic.

    Everything above this line tests behaviour we control. This one tests a REQUEST we hand to
    Google, and it is the part of #146 a fake cannot vouch for: `sync_event` reads an empty
    result as "we never created this", so a filter that is malformed or accidentally bounded by
    time fails by returning nothing -- silently reinstating the duplicate insert.

    Pinned against Google's discovery document for calendar/v3 (revision 20260810):
    `privateExtendedProperty` is `propertyName=value` and matches PRIVATE properties only;
    `timeMin`/`timeMax` are optional, `calendarId` being the sole required parameter. A fake
    cannot check the semantics, but it can check that we asked the question we meant to.
    (`singleEvents` is asserted by the sibling below, against the window read it must agree
    with.)
    """
    cal = _CalPaged([{"items": []}])
    _client_with(monkeypatch, cal=cal).find_events_by_private_property("sluice-track-uid", "u1")

    sent = cal.events_ep.calls[0]
    assert sent["privateExtendedProperty"] == "sluice-track-uid=u1", (
        "the filter must be the documented propertyName=value form; anything else is a 400 "
        "or, worse, a query that matches nothing")
    assert "timeMin" not in sent and "timeMax" not in sent, (
        f"a time bound here recreates #146 exactly -- our event moved OUT of one: {sent}")
    assert sent["calendarId"] == "primary"


def test_both_calendar_reads_expand_recurrences_THE_SAME_WAY(monkeypatch):
    """`sync_event` runs one `_find_ours` over both result sets and then calls
    `update_event`/`delete_event` on whatever id it finds.

    So the two reads have to return the same SHAPE of object. If `singleEvents` drifted apart,
    one search would hand back an expanded instance and the other the recurring parent, and the
    same UID would resolve to a different id depending on which search happened to find it --
    with a delete aimed at the parent taking out the whole series. Asserted as an equality
    between the two requests rather than as `is True` twice, because the property that matters
    is that they AGREE."""
    window = _CalPaged([{"items": []}])
    tagged = _CalPaged([{"items": []}])
    _client_with(monkeypatch, cal=window).list_events("2026-01-01T00:00:00+00:00",
                                                      "2026-03-01T00:00:00+00:00")
    _client_with(monkeypatch, cal=tagged).find_events_by_private_property("k", "v")

    win, tag = window.events_ep.calls[0], tagged.events_ep.calls[0]
    assert win["singleEvents"] == tag["singleEvents"], "the two reads disagree"
    # The VALUE as well as the agreement. Equality alone is satisfied by both drifting to
    # False -- and that mutant is exactly the harm this test's own docstring describes, since
    # an unexpanded recurring parent is what a delete would then be aimed at. Executed: with
    # `singleEvents=False` on both reads the whole suite stayed green, so the equality was
    # certifying nothing on its own.
    #
    # It also silently breaks `_foreign_at_start`, which needs recurrences EXPANDED to see the
    # weekly standup sitting at the interview slot; against unexpanded parents it stops
    # suppressing the booking.
    assert win["singleEvents"] is True and tag["singleEvents"] is True, (
        f"both reads must expand recurrences, got window={win['singleEvents']!r} "
        f"tag={tag['singleEvents']!r}")


def test_the_tag_query_reads_every_page(monkeypatch):
    """Same harm as the window read, one page further out: our own tagged event sitting on
    page 2 reads as absent, and absence is what makes `sync_event` insert a duplicate."""
    pages = [{"items": [{"id": "e1"}], "nextPageToken": "t1"}, {"items": [{"id": "e2"}]}]
    cal = _CalPaged(pages)
    got, truncated = _client_with(monkeypatch, cal=cal).find_events_by_private_property("k", "v")
    assert [e["id"] for e in got] == ["e1", "e2"]
    assert truncated is False


def test_the_tag_query_is_bounded_and_SAYS_so(monkeypatch):
    """Unbounded is not an option just because the result set should be tiny -- "should be" is
    what a cap exists for. Hitting it must REPORT, which is this method's whole job here;
    `_find_ours_by_tag` passes the flag up and `sync_event` is what turns it into `unresolved`
    rather than a guessed insert, and only when nothing of ours was found."""
    endless = [{"items": [{"id": f"e{i}"}], "nextPageToken": "t"} for i in range(500)]
    cal = _CalPaged(endless)
    got, truncated = _client_with(monkeypatch, cal=cal).find_events_by_private_property(
        "k", "v", max_results=5)
    assert len(got) == 5 and truncated is True


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


def _warns(monkeypatch, caplog, *, gmail=None, cal=None, query="q", tag=None, **kw):
    """The messages `sluice.track.google_client` emitted for one read.

    `tag=(name, value)` drives the UID-tag query instead of the window read. A THIRD mode
    rather than a second calendar one, because the tag query has its own truncation warning
    with its own remedy, and a helper that could only reach two of the three left the newest
    `if truncated:` block with no witness at all -- which is the same gap the two tests below
    were written for."""
    with caplog.at_level("WARNING", logger="sluice.track.google_client"):
        c = _client_with(monkeypatch, gmail=gmail, cal=cal)
        if gmail is not None:
            c.search_messages(query, **kw)
        elif tag is not None:
            c.find_events_by_private_property(*tag, **kw)
        else:
            c.list_events("a", "b", **kw)
    return [r.getMessage() for r in caplog.records
            if r.name == "sluice.track.google_client"]


def test_the_gmail_truncation_WARNING_actually_fires(monkeypatch, caplog):
    # Deleting either caller's `if truncated:` block left the suite green, while the tests'
    # own docstring claimed "hitting it is loud".
    pages = [{"messages": [{"id": f"m{i}"} for i in range(5)], "nextPageToken": "t"},
             {"messages": [{"id": "z"}]}]
    # A query shaped like a real one: `_gmail_query` appends `gmail_extra_query`, which is
    # where the operator's own job-hunt domains and addresses live.
    query = "after:2026/07/10 -category:promotions from:jobs@example-tidal.invalid"
    said = _warns(monkeypatch, caplog, gmail=_GmailPaged(pages), max_results=3, query=query)
    assert said, "a truncated gmail search must say so"
    joined = " ".join(said)
    assert "3" in joined, "the warning should name the cap that was hit"
    # ...and NOT the query. `config.py` sets the rule: a log message travels further (logs,
    # bug reports) than the config file does. Naming the KNOB keeps it actionable without the
    # value -- and putting `%r`/`query` back into the warning was green before this line.
    assert "example-tidal.invalid" not in joined, f"the warning leaked the query: {joined}"
    assert "gmail_extra_query" in joined, "it must still name the knob to narrow"


def test_the_calendar_truncation_WARNING_actually_fires(monkeypatch, caplog):
    pages = [{"items": [{"id": f"e{i}"} for i in range(5)], "nextPageToken": "t"},
             {"items": [{"id": "z"}]}]
    said = _warns(monkeypatch, caplog, cal=_CalPaged(pages), max_results=3)
    assert said, "a truncated calendar window must say so"


def test_the_TAG_QUERY_truncation_WARNING_actually_fires(monkeypatch, caplog):
    """Third caller, third `if truncated:` block, and it had no witness at all.

    Deleting the whole warning from `find_events_by_private_property` left the entire suite
    green -- the same gap, in the same shape, that the two tests above were written to close
    for the other two callers. `test_the_tag_query_is_bounded_and_SAYS_so` asserts the RETURNED
    flag, which is a different thing from the operator being told.
    """
    pages = [{"items": [{"id": f"e{i}"} for i in range(5)], "nextPageToken": "t"},
             {"items": [{"id": "z"}]}]
    # A UID shaped like one off a real invite: this value is parsed from an inbound .ics, so
    # it is the same class of untrusted, mailbox-derived string as the gmail query above.
    uid = "uid-9f3c@mail.example-tidal.invalid"
    said = _warns(monkeypatch, caplog, cal=_CalPaged(pages),
                  tag=("sluice-track-uid", uid), max_results=3)
    assert said, "a truncated tag query must say so"
    joined = " ".join(said)
    assert "calendar_max_events" in joined, (
        "it must name the knob that can actually help -- and NOT calendar_lookahead_days, "
        f"which cannot affect a query with no window: {joined}")
    assert "calendar_lookahead_days" not in joined, (
        f"the remedy here is not the window knob: {joined}")
    # Same rule as its gmail sibling, and it was green with `%r`/`value` in the warning.
    assert uid not in joined, f"the warning leaked the UID off an inbound invite: {joined}"


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


# ---- search_truncated: the only artefact of #137 a user ever sees --------------------------

def test_search_truncated_reaches_the_RunReport(monkeypatch):
    """The propagation had zero coverage: `ids, _ = client.search_messages(...)` in
    `engine.run` left the whole suite green."""
    from sluice.track import engine as E
    from sluice.track.config import TrackConfig
    from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault

    class _Capped(OneMsgClient):
        def search_messages(self, query, max_results=500):
            return ["m1"], True

    v, _ = _vault("applied")
    rep = E.run(v, TrackConfig(), _Capped(), FakeBackend("{}"), seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.search_truncated is True


def test_a_complete_search_does_not_set_the_flag():
    from sluice.track import engine as E
    from sluice.track.config import TrackConfig
    from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault

    v, _ = _vault("applied")
    rep = E.run(v, TrackConfig(), OneMsgClient(), FakeBackend("{}"), seen=set(),
                deadletter=_dl(), now_iso="2026-07-10T12:00:00+00:00")
    assert rep.search_truncated is False


def test_a_truncated_search_HOLDS_the_lastrun_watermark(tmp_path, monkeypatch):
    """The decision this branch REVERSED, pinned by driving the real gate.

    I first argued for advancing, on two premises review then falsified by measurement:
    a held window does not cost a bigger fetch (since #137 the cap is a hard TOTAL across
    pages, so 400 matches and 50,000 matches both cost one request), and holding does not
    lose the same messages as advancing -- advancing moves `after:` to TODAY, so every
    starved message leaves the addressable set the instant we advance. Holding keeps them
    queryable, which is the only policy under which narrowing the query recovers them.

    An earlier version of this test asserted a restatement of the gate expression instead of
    running it, which certified nothing.
    """
    from sluice.core import app as A
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    seen_db = str(tmp_path / "track-seen.db")
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    saved = []
    monkeypatch.setattr(A, "_save_lastrun", lambda path, iso: saved.append(iso))

    class _Rep:
        auth_error = False
        deadletter_error = False
        search_truncated = True
        failures = []
        open_proposals = []

    # `app.track` imports the engine INSIDE the method, so patch the module itself.
    import sluice.track.engine as _te
    monkeypatch.setattr(_te, "run", lambda *a, **k: _Rep())
    monkeypatch.setattr(A, "_save_seen", lambda *a, **k: None)
    Sluice(Config()).track(now_iso="2026-07-10T12:00:00+00:00")
    assert saved == [], "a truncated search must HOLD the watermark, not advance past it"


def test_a_complete_search_still_ADVANCES_the_watermark(tmp_path, monkeypatch):
    # The hold must be narrow: an ordinary run has to keep advancing, or every run re-queries
    # a widening window -- the stall this sub-app has been bitten by before.
    from sluice.core import app as A
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    seen_db = str(tmp_path / "track-seen.db")
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    saved = []
    monkeypatch.setattr(A, "_save_lastrun", lambda path, iso: saved.append(iso))

    class _Rep:
        auth_error = False
        deadletter_error = False
        search_truncated = False
        failures = []
        open_proposals = []

    # `app.track` imports the engine INSIDE the method, so patch the module itself.
    import sluice.track.engine as _te
    monkeypatch.setattr(_te, "run", lambda *a, **k: _Rep())
    monkeypatch.setattr(A, "_save_seen", lambda *a, **k: None)
    Sluice(Config()).track(now_iso="2026-07-10T12:00:00+00:00")
    assert saved == ["2026-07-10T12:00:00+00:00"]


# ---- the caps are configurable, and the config actually REACHES the client -----------------

def test_a_configured_cap_changes_what_one_run_reads(monkeypatch):
    """The knob, exercised. A config key nothing threads through is decoration."""
    pages = [{"messages": [{"id": f"m{i}"} for i in range(j * 40, j * 40 + 40)],
              **({"nextPageToken": "t"} if j < 2 else {})} for j in range(3)]
    c = _client_with(monkeypatch, gmail=_GmailPaged(pages), gmail_max_messages=50)
    ids, truncated = c.search_messages("q")
    assert len(ids) == 50 and truncated is True, "the configured cap was ignored"


def test_the_configured_calendar_cap_is_used_too():
    cal_pages = [{"items": [{"id": f"e{i}"} for i in range(j * 250, j * 250 + 250)],
                  **({"nextPageToken": "t"} if j < 3 else {})} for j in range(4)]
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    try:
        c = _client_with(mp, cal=_CalPaged(cal_pages), calendar_max_events=500)
        got, truncated = c.list_events("a", "b")
        assert len(got) == 500 and truncated is True
    finally:
        mp.undo()


def test_the_config_keys_are_THREADED_into_the_client(tmp_path, monkeypatch):
    """`app.py` builds the client. If it forgets a key, the default silently wins and every
    test above still passes -- so assert the wiring, not just the client."""
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    seen_db = str(tmp_path / "track-seen.db")
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text(
        f"track:\n  seen_db: {seen_db}\n  gmail_max_messages: 111\n"
        f"  calendar_max_events: 222\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    built = {}
    import sluice.track.google_client as _gc

    class _Spy:
        def __init__(self, token_path, *, gmail_max_messages, calendar_max_events):
            built["gmail"] = gmail_max_messages
            built["cal"] = calendar_max_events

    monkeypatch.setattr(_gc, "RealGoogleClient", _Spy)

    class _Rep:
        auth_error = deadletter_error = search_truncated = False
        failures = []
        open_proposals = []

    import sluice.track.engine as _te
    monkeypatch.setattr(_te, "run", lambda *a, **k: _Rep())
    import sluice.core.app as A
    monkeypatch.setattr(A, "_save_seen", lambda *a, **k: None)
    monkeypatch.setattr(A, "_save_lastrun", lambda *a, **k: None)

    Sluice(Config()).track(now_iso="2026-07-10T12:00:00+00:00")
    assert built == {"gmail": 111, "cal": 222}, f"config did not reach the client: {built}"
