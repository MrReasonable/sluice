from types import SimpleNamespace

from sluice.ingest.base import (
    BrowserListSource,
    CarouselSource,
    Ctx,
    Search,
    searches_for,
)


class _FakeConfig:
    """Minimal config stand-in: maps source id -> object with a `.searches` list."""

    def __init__(self, overrides):
        self._o = overrides

    def source(self, id):
        return SimpleNamespace(searches=self._o.get(id, []))


def _demo_browser(**kw):
    return BrowserListSource(id="demo", searches_spec=[("Analyst", "http://x")],
                             extractor_js="JS", **kw)


def test_browserlist_parse_maps_rows_to_leads():
    src = _demo_browser()
    raw = {"result": [{"title": "Analyst", "company": "Acme", "link": "http://x/1"}],
           "landed": "http://x"}
    leads = src.parse(raw, Search("Analyst", "http://x"))
    assert leads[0].title == "Analyst"
    assert leads[0].url == "http://x/1"
    assert leads[0].source == "demo"
    assert leads[0].search == "Analyst"


def test_browserlist_parse_skips_titleless_rows():
    src = _demo_browser()
    raw = {"result": [{"title": "", "link": "http://x/1"},
                      {"title": "Analyst", "link": "http://x/2"}]}
    leads = src.parse(raw, Search("Analyst"))
    assert [l.url for l in leads] == ["http://x/2"]


def test_browserlist_extra_overrides_applied():
    src = _demo_browser(extra={"job_type": "contract"})
    leads = src.parse({"result": [{"title": "Analyst", "link": "http://x/1"}]}, Search("Analyst"))
    assert leads[0].job_type == "contract"


def test_searches_builds_search_objects():
    src = _demo_browser()
    s = src.searches()
    assert [x.label for x in s] == ["Analyst"]
    assert s[0].url == "http://x"


def test_searches_for_uses_builtin_without_config():
    src = _demo_browser()
    s = searches_for(src, None)
    assert [(x.label, x.url) for x in s] == [("Analyst", "http://x")]


def test_searches_for_config_override_replaces_builtin():
    src = _demo_browser()
    cfg = _FakeConfig({"demo": [["Mine", "http://y", {"job_type": "perm"}]]})
    s = searches_for(src, cfg)
    assert [(x.label, x.url, x.params) for x in s] == [
        ("Mine", "http://y", {"job_type": "perm"})
    ]


def test_searches_for_empty_override_falls_back_to_builtin():
    src = _demo_browser()
    cfg = _FakeConfig({"demo": []})  # present but empty → built-in wins
    s = searches_for(src, cfg)
    assert [(x.label, x.url) for x in s] == [("Analyst", "http://x")]


def test_per_search_params_override_source_extra():
    # one engine, perm + contract by parameter: a perm search on a contract-default
    # source still tags the lead job_type=perm.
    src = BrowserListSource(id="demo", extractor_js="JS", extra={"job_type": "contract"},
                            searches_spec=[("Contract", "http://x"),
                                           ("Perm", "http://y", {"job_type": "perm"})])
    contract, perm = src.searches()
    c = src.parse({"result": [{"title": "Analyst", "link": "http://x/1"}]}, contract)
    p = src.parse({"result": [{"title": "Analyst", "link": "http://y/1"}]}, perm)
    assert c[0].job_type == "contract"   # source default
    assert p[0].job_type == "perm"       # per-search override


def test_health_hint_reports_count_and_hosts():
    src = _demo_browser()
    raw = {"result": [{"title": "A", "link": "u"}],
           "landed": "https://x.com/a", "requested": "https://x.com/s"}
    hint = src.health_hint(raw)
    assert hint["count"] == 1
    assert hint["landed_host"] == "x.com"
    assert hint["requested_host"] == "x.com"
    assert hint["landed_path"] == "/a"
    assert hint["requested_path"] == "/s"
    assert "degraded" not in hint, "nothing stamped a row -- there is nothing to promote"


def test_health_hint_reports_empty_paths_on_an_empty_url():
    # Unconditional "", mirroring landed_host/requested_host -- a path is a measurement
    # that always exists, not an event that "fired".
    src = _demo_browser()
    hint = src.health_hint({"result": [], "landed": "", "requested": ""})
    assert hint["landed_path"] == "" and hint["requested_path"] == ""


def test_health_hint_paths_carry_no_query_string():
    # #156's `login` drift reason deliberately does not match on query tokens -- two real
    # false positives were measured against it (an ordinary `?q=account+manager` search;
    # a healthy redirect merely gaining `session_id=`). This is the producer half of that
    # decision: `urlparse(...).path` already excludes the query, so a query token can never
    # reach `_login_wall` in the real pipeline, not merely "chosen not to match".
    src = _demo_browser()
    raw = {"result": [],
           "landed": "https://example.invalid/jobs?q=account+manager&session_id=abc123",
           "requested": "https://example.invalid/jobs?q=account+manager"}
    hint = src.health_hint(raw)
    assert hint["landed_path"] == "/jobs" and hint["requested_path"] == "/jobs"


def test_health_hint_promotes_a_degraded_row_marker_from_a_browserlist_source():
    # #156: a row the extractor's own fallback stamped is direct evidence of degradation,
    # promoted so `detect_drift` can report `fallback` instead of a silently healthy count.
    src = _demo_browser()
    raw = {"result": [{"title": "A", "link": "u", "degraded": "anchor-fallback"}]}
    assert src.health_hint(raw)["degraded"] == "anchor-fallback"


def test_health_hint_promotes_the_FIRST_degraded_marker_only():
    src = _demo_browser()
    raw = {"result": [{"title": "A", "link": "u1", "degraded": "anchor-fallback"},
                      {"title": "B", "link": "u2", "degraded": "link-fallback"}]}
    assert src.health_hint(raw)["degraded"] == "anchor-fallback"


def test_health_hint_promotes_a_degraded_row_marker_from_a_carousel_source():
    src = CarouselSource(id="wttj", read_js="R", advance_selector="[n]",
                         searches_spec=[("Otta", "http://o")])
    raw = {"jobs": [{"title": "A", "link": "u", "degraded": "anchor-fallback"}]}
    assert src.health_hint(raw)["degraded"] == "anchor-fallback"


def test_carousel_health_hint_reports_paths_too():
    src = CarouselSource(id="wttj", read_js="R", advance_selector="[n]",
                         searches_spec=[("Otta", "http://o")])
    raw = {"jobs": [], "landed": "https://example.invalid/login",
           "requested": "https://example.invalid/jobs"}
    hint = src.health_hint(raw)
    assert hint["landed_path"] == "/login" and hint["requested_path"] == "/jobs"


def test_browserlist_fetch_drives_camofox_with_fake():
    calls = []

    class FakeCam:
        def create_tab(self, url=""):
            calls.append(("create_tab", url))
            return "t1"

        def evaluate(self, tid, expr):
            calls.append(("evaluate", expr))
            if expr == "location.href":
                return {"result": "http://x/landed"}
            return {"result": [{"title": "Analyst", "link": "http://x/1"}]}

        def scroll(self, tid, amount):
            calls.append(("scroll", amount))
            return {}

        def close_tab(self, tid):
            calls.append(("close_tab", tid))
            return {}

    ctx = Ctx(camofox=FakeCam(), config=None, sleep=lambda *_: None)
    raw = _demo_browser(scrolls=2).fetch(ctx, Search("Analyst", "http://x"))
    assert raw["result"] == [{"title": "Analyst", "link": "http://x/1"}]
    assert raw["landed"] == "http://x/landed"
    assert raw["requested"] == "http://x"
    assert ("create_tab", "http://x") in calls
    assert calls.count(("scroll", 800)) == 2
    assert ("close_tab", "t1") in calls


def test_browserlist_fetch_returns_empty_when_no_tab():
    class NoTabCam:
        def create_tab(self, url=""):
            return None

    ctx = Ctx(camofox=NoTabCam(), sleep=lambda *_: None)
    raw = _demo_browser().fetch(ctx, Search("Analyst", "http://x"))
    assert raw["result"] == [] and raw["error"] == "no-tab"


def test_carousel_parse_maps_jobs_to_leads():
    src = CarouselSource(id="wttj", read_js="R", advance_selector='[data-testid="next"]',
                         searches_spec=[("Otta", "http://o")])
    raw = {"jobs": [{"title": "Analyst", "company": "Acme", "link": "http://o/1", "salary": "£100k"}]}
    leads = src.parse(raw, Search("Otta", "http://o"))
    assert leads[0].title == "Analyst"
    assert leads[0].salary == "£100k"
    assert leads[0].source == "wttj"


def test_carousel_fetch_walks_until_repeat():
    reads = [
        {"result": {"title": "A", "link": "http://o/1"}},
        {"result": {"title": "B", "link": "http://o/2"}},
        {"result": {"title": "B", "link": "http://o/2"}},  # repeat → stop
    ]

    class FakeCam:
        def __init__(self):
            self.i = 0

        def create_tab(self, url=""):
            return "t1"

        def evaluate(self, tid, expr):
            if expr == "R":
                r = reads[self.i]
                return r
            # advance JS: pretend a next-button exists, advance the read cursor
            self.i += 1
            return {"result": True}

        def close_tab(self, tid):
            return {}

    src = CarouselSource(id="wttj", read_js="R", advance_selector="[n]",
                         searches_spec=[("Otta", "http://o")])
    ctx = Ctx(camofox=FakeCam(), sleep=lambda *_: None)
    raw = src.fetch(ctx, Search("Otta", "http://o"))
    assert [j["title"] for j in raw["jobs"]] == ["A", "B"]
