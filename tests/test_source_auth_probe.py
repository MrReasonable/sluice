"""A source that finds itself logged out must SAY so, not report an empty page.

`detect_drift` gained an "auth" classification on 2026-08-15, and a classification nothing
produces is dead code that quietly never fires -- the same trap as a shared constant no caller
consumes. This wires a real producer.

The 2026-08-15 state, measured on the live browser: LinkedIn's jobs page served 60 real jobs
in LOGGED-OUT markup (`base-card` / `job-search-card`) and zero of the authenticated
`artdeco-entity-lockup` cards the extractor targets. Rows found: 0. Nothing anywhere could
tell that from "no jobs matched today", so it was recorded as a bare zero three times and the
source auto-retired.

An `auth_probe_js` lets a source declare what "logged out" looks like for it. The probe is
evaluated on the same tab as the extractor, so it sees exactly the page the extractor failed
on -- not a second fetch that might land differently.
"""
import pytest
from types import SimpleNamespace

from sluice.ingest.base import BrowserListSource, Ctx, Search
from sluice.core.health import detect_drift


class _Cam:
    """Camofox stand-in. `probe_result` is what the auth probe evaluates to.

    Records `(tid, expr)` and hands out a FRESH tab id per `create_tab`. Both matter: an
    earlier version returned a constant id and recorded only the expression, so
    `test_the_probe_runs_on_the_same_tab_as_the_extractor` could not tell one tab from two --
    verified by making the probe open a second tab and watching the suite stay green.
    """

    def __init__(self, rows, probe_result=False):
        self.rows, self.probe_result = rows, probe_result
        self.evaluated = []      # (tid, expr)
        self.tabs_opened = 0
        self.closed = 0          # every tab opened must be closed, even on a raise

    def create_tab(self, url=""):
        self.tabs_opened += 1
        return f"t{self.tabs_opened}"

    def evaluate(self, tid, expr):
        self.evaluated.append((tid, expr))
        if expr == "location.href":
            return {"result": "http://x"}
        if expr == "AUTHPROBE":
            return {"result": self.probe_result}
        return {"result": self.rows}

    def scroll(self, tid, amount=0):
        return {}

    def close_tab(self, tid):
        self.closed += 1
        return {}


def _src(**kw):
    return BrowserListSource(id="demo", searches_spec=[("A", "http://x")],
                             extractor_js="JS", wait=0, scrolls=0, **kw)


def _ctx(cam):
    # `sleep` injected, as every other Ctx in the suite does it. Ctx defaults it to real
    # `time.sleep`, and the two tests that drive the REAL registered LinkedIn source
    # (wait=4, scrolls=8) therefore slept 8s each -- 41% of the whole suite's wall clock,
    # and the next-slowest test in the repo is 0.78s.
    return Ctx(camofox=cam, sleep=lambda *_: None,
               config=SimpleNamespace(source=lambda i: SimpleNamespace(searches=[])))


def _fetch(cam, **kw):
    src = _src(**kw)
    return src, src.fetch(_ctx(cam), Search("A", "http://x"))


def test_a_source_with_no_probe_behaves_exactly_as_before():
    # The probe is opt-in. A source that declares none must not gain a signal, or every
    # existing source starts reporting auth state it never measured.
    cam = _Cam(rows=[{"title": "T"}])
    src, raw = _fetch(cam)
    assert "AUTHPROBE" not in [e for _tid, e in cam.evaluated]
    assert src.health_hint(raw).get("auth") is None


def test_a_logged_out_page_reports_auth_missing():
    cam = _Cam(rows=[], probe_result=True)
    src, raw = _fetch(cam, auth_probe_js="AUTHPROBE")
    assert src.health_hint(raw)["auth"] == "missing"


def test_a_logged_in_page_reports_no_auth_problem():
    cam = _Cam(rows=[{"title": "T"}], probe_result=False)
    src, raw = _fetch(cam, auth_probe_js="AUTHPROBE")
    assert src.health_hint(raw).get("auth") is None


def test_the_probe_runs_on_the_same_tab_as_the_extractor():
    """A second fetch could land somewhere else (redirect, A/B split, rate limit), and the
    probe would then describe a different page than the one that yielded nothing.

    Asserted on the TAB IDS, which is the only thing that can distinguish one tab from two.
    An earlier version asserted `"JS" in cam.evaluated and "AUTHPROBE" in cam.evaluated`,
    which is true whether they ran on the same tab or on two -- confirmed by making the probe
    open a second tab and watching the suite stay green.
    """
    cam = _Cam(rows=[], probe_result=True)
    _fetch(cam, auth_probe_js="AUTHPROBE")
    by_expr = {e: tid for tid, e in cam.evaluated}
    assert "JS" in by_expr and "AUTHPROBE" in by_expr, cam.evaluated
    assert by_expr["JS"] == by_expr["AUTHPROBE"], (
        f"probe ran on {by_expr['AUTHPROBE']}, extractor on {by_expr['JS']}")
    assert cam.tabs_opened == 1, f"fetch opened {cam.tabs_opened} tabs; the probe must reuse one"


def test_the_signal_reaches_detect_drift_as_auth_not_zero():
    # The end-to-end point of the whole change: this run must classify as "auth", so the
    # digest names the cause and `_is_dead` does not retire the source.
    cam = _Cam(rows=[], probe_result=True)
    src, raw = _fetch(cam, auth_probe_js="AUTHPROBE")
    hint = src.health_hint(raw)
    signals = {k: v for k, v in hint.items() if k != "markers"}   # what engine.py records
    assert detect_drift("demo", hint["count"], signals, baseline=50) == "auth"


def test_the_linkedin_subclass_runs_the_probe_too():
    """`_LinkedInSource` customises scrolling, so drive the REAL registered source end to end.

    It used to override `fetch` wholesale and shipped without the probe: the registration
    declared one, so everything READ as covered while the copied fetch never evaluated it.
    The override is now a single `_scroll_step`, which removes that class of bug -- but the
    regression pin stays, because "the subclass still honours the contract" is exactly the
    property that silently broke. Enumerate both the base and the subclass, not just the base.
    """
    from sluice.ingest import sources as registry

    src = registry.get("linkedin")
    assert src.auth_probe_js, "linkedin should declare an auth probe"

    class _LiCam(_Cam):
        def evaluate(self, tid, expr):
            self.evaluated.append((tid, expr))
            if expr == "location.href":
                return {"result": "https://example.invalid/jobs/search"}
            if expr == src.auth_probe_js:
                return {"result": True}          # logged-out page
            return {"result": []}                 # extractor finds nothing

    cam = _LiCam(rows=[])
    raw = src.fetch(_ctx(cam), Search("A", "https://example.invalid/jobs/search"))
    assert src.auth_probe_js in [e for _tid, e in cam.evaluated], "the subclass skipped the auth probe"
    assert src.health_hint(raw)["auth"] == "missing"


def _eval_probe(probe_js, *, artdeco, guest):
    """Run LinkedIn's real probe expression against stub DOM counts.

    The probe is JS, so this translates it rather than executing it -- but it translates the
    EXPRESSION under test, parsed out of the source, so an operator change (`&&` -> `||`) is
    reflected. The previous version of this test only grepped for substrings, which is why
    flipping the operator left the whole suite green.
    """
    import re

    op = "and" if "&&" in probe_js else "or"
    m = re.search(r"artdeco-entity-lockup'\)\.length\s*(===|!==|>|<)\s*(\d+)", probe_js)
    g = re.search(r"job-search-card'\)\.length\s*(===|!==|>|<)\s*(\d+)", probe_js)
    assert m and g, f"probe shape not recognised, update this translator: {probe_js}"
    cmp_ = {"===": lambda a, b: a == b, "!==": lambda a, b: a != b,
            ">": lambda a, b: a > b, "<": lambda a, b: a < b}
    left = cmp_[m.group(1)](artdeco, int(m.group(2)))
    right = cmp_[g.group(1)](guest, int(g.group(2)))
    return (left and right) if op == "and" else (left or right)


def test_the_linkedin_probe_needs_BOTH_halves():
    """Guest markup present AND authenticated markup absent.

    Either half alone is a false positive: guest cards can co-exist with authenticated ones
    during a LinkedIn A/B, and "no artdeco cards" is also what a genuinely empty result set
    looks like. So the probe must be a conjunction, and this asserts the truth table rather
    than the spelling -- an earlier version grepped for substrings and stayed green when the
    `&&` was flipped to `||`.
    """
    from sluice.ingest import sources as registry

    probe = registry.get("linkedin").auth_probe_js
    assert "artdeco-entity-lockup" in probe and "base-card" in probe
    # The measured logged-out state: no authenticated cards, guest cards present.
    assert _eval_probe(probe, artdeco=0, guest=60) is True
    # Logged IN: authenticated cards present. Not a login failure.
    assert _eval_probe(probe, artdeco=25, guest=0) is False
    # Genuinely empty result set: neither kind of card. NOT a login failure -- this is the
    # half a bare "artdeco absent" test would wrongly report.
    assert _eval_probe(probe, artdeco=0, guest=0) is False
    # Both rendered (an A/B split). Authenticated markup is present, so we are logged in.
    assert _eval_probe(probe, artdeco=25, guest=60) is False


def test_a_probe_that_errors_does_not_claim_the_user_is_logged_out():
    # Fail QUIET on the probe specifically: asserting "logged out" off a broken probe would
    # suppress the retirement of a genuinely dead source, which is the opposite failure.
    class _Boom(_Cam):
        def evaluate(self, tid, expr):
            if expr == "AUTHPROBE":
                return {"error": "boom"}
            return super().evaluate(tid, expr)

    cam = _Boom(rows=[])
    src, raw = _fetch(cam, auth_probe_js="AUTHPROBE")
    assert src.health_hint(raw).get("auth") is None


# ---- the PRODUCER side ------------------------------------------------------------------
# Every test above this line drives `detect_drift` with a hand-written signal. That checks the
# classifier and says nothing about the code that EMITS it -- which is how six mutations to
# the emitting side all survived. Each test below deletes a producer, not a classifier.

def test_a_dead_browser_is_reported_as_fetch_error_not_a_bare_zero():
    """`create_tab` returning None is the single most common "could not read".

    `fetch` has always recorded {"error": "no-tab"}; `health_hint` built a fresh dict from
    three keys and dropped it, so the clearest explanation in the system reached the
    classifier as an unexplained zero -- and one Camofox outage retired ALL sources at once.
    """
    class _Down(_Cam):
        def create_tab(self, url=""):
            return None

    src = _src()
    raw = src.fetch(_ctx(_Down(rows=[])), Search("A", "http://x"))
    hint = src.health_hint(raw)
    assert hint["fetch_error"] == "no-tab"
    signals = {k: v for k, v in hint.items() if k != "markers"}
    assert detect_drift("demo", hint["count"], signals, baseline=50) == "unreachable"


def test_a_probe_that_errors_is_SURFACED_and_not_read_as_logged_out():
    """The stub returns BOTH `result: True` and an error, deliberately.

    An earlier version returned only `{"error": ...}` with no `result` key -- so
    `bool(probe.get("result"))` was False whether or not the guard existed, and the test
    passed with the guard deleted. A test that cannot distinguish the fix from its absence
    is worse than none.
    """
    class _BadProbe(_Cam):
        def evaluate(self, tid, expr):
            self.evaluated.append((tid, expr))
            if expr == "location.href":
                return {"result": "http://x"}
            if expr == "AUTHPROBE":
                return {"result": True, "error": "SyntaxError"}
            return {"result": self.rows}

    src, raw = None, None
    cam = _BadProbe(rows=[])
    src = _src(auth_probe_js="AUTHPROBE")
    raw = src.fetch(_ctx(cam), Search("A", "http://x"))
    hint = src.health_hint(raw)
    assert hint.get("auth") is None, "an errored probe must not be read as logged-out"
    assert hint["auth_probe_error"] == "SyntaxError", "a silently broken guard is the bug"
    # Reported, but NOT an explanation: it must not defer retirement.
    signals = {k: v for k, v in hint.items() if k != "markers"}
    assert detect_drift("demo", hint["count"], signals, baseline=50) == "zero"


def test_a_failed_landed_evaluate_does_not_manufacture_no_redirect():
    """Defaulting `landed` to the requested URL fabricates `requested_host == landed_host`.

    That is the absence of the one signal that would have explained the run, invented by the
    code that failed to read it.
    """
    class _NoLanded(_Cam):
        def evaluate(self, tid, expr):
            self.evaluated.append((tid, expr))
            if expr == "location.href":
                return {"error": "evaluate failed"}
            return {"result": self.rows}

    src = _src()
    raw = src.fetch(_ctx(_NoLanded(rows=[])), Search("A", "http://x"))
    assert raw["landed"] == "", "landed must stay empty rather than echo the request"
    assert raw["error"] == "evaluate failed"
    assert src.health_hint(raw)["fetch_error"] == "evaluate failed"


def test_the_linkedin_source_scrolls_the_RESULTS_PANEL():
    """LinkedIn virtualizes its list, so a window scroll loads no more jobs.

    The whole reason the subclass exists. Nothing pinned it, so `_scroll_step` could be
    emptied and the suite stayed green -- the source would silently return only the first
    screenful.
    """
    from sluice.ingest import sources as registry

    src = registry.get("linkedin")
    cam = _Cam(rows=[])
    src.fetch(_ctx(cam), Search("A", "https://example.invalid/jobs/search"))
    scrolled = [e for _tid, e in cam.evaluated if "scrollTop" in e or "scrollBy" in e]
    assert scrolled, "linkedin must scroll the results panel, not the window"
    assert len(scrolled) == src.scrolls, f"expected {src.scrolls} panel scrolls, got {len(scrolled)}"


# ---- protocol CONFORMANCE, not per-class bespoke tests -----------------------------------

def _carousel():
    from sluice.ingest.base import CarouselSource
    return CarouselSource(id="carousel", searches_spec=[("A", "http://x")],
                          read_js="READ", advance_selector=".next", wait=0)


@pytest.mark.parametrize("src", [_src(), _carousel()], ids=lambda s: type(s).__name__)
def test_a_camofox_outage_is_explained_for_EVERY_source_class(src):
    """`health_hint` is a protocol member with two implementations. The first fix landed on
    one of them, so an outage still retired the carousel source after three runs -- the
    identical bug, in the file next door.

    Parameterised over the classes rather than written twice: a third implementation joins
    this test by existing, instead of by someone remembering.
    """
    class _NoTab(_Cam):
        def create_tab(self, url=""):
            return None

    raw = src.fetch(_ctx(_NoTab(rows=[])), Search("A", "http://x"))
    hint = src.health_hint(raw)
    assert hint.get("fetch_error") == "no-tab", f"{type(src).__name__} dropped the fetch error"
    signals = {k: v for k, v in hint.items() if k != "markers"}
    assert detect_drift("demo", hint["count"], signals, baseline=20) == "unreachable"


def _every_registered_source():
    """Every source the registry actually holds -- ENUMERATED, not hand-listed.

    The first version of the test below parameterised over `[_src(), _carousel()]`, the two
    base classes, and its own docstring claimed "a third implementation joins this test by
    existing". It did not: `sources/workinstartups.py` already overrode `health_hint` and was
    invisible to the pair. The fix for a fix-one-instance bug reproduced the bug.

    Driving off the registry is the difference between a test that covers what someone
    remembered and one that covers what is shipped.

    Filtered to classes defined under `sluice.` because the registry is a global that tests
    register into without cleanup (`tests/test_registry.py` leaves a `_Dummy` with no
    `health_hint` behind). "Shipped sources" is the population this sweep is about, and saying
    so explicitly beats depending on which test ran first.
    """
    from sluice.ingest import sources as registry
    return [s for s in registry.all_sources()
            if type(s).__module__.startswith("sluice.")]


@pytest.mark.parametrize("src", _every_registered_source(), ids=lambda s: s.id)
@pytest.mark.parametrize("raw", [None, [], "boom", 0], ids=repr)
def test_health_hint_tolerates_a_non_dict_raw_for_EVERY_registered_source(src, raw):
    """`health_hint` is a protocol member, and every implementation had the same hole.

    The base classes guarded the COUNT with `isinstance(raw, dict)`, then read
    `raw.get("landed")` on the very next line unguarded, then checked `isinstance` again --
    so the unguarded dereference raised first and the trailing guard was unreachable. The
    `workinstartups` override then dereferenced the CALLER's object after `super()` had
    normalised only its own local, so fixing the bases did not reach it.

    Latent rather than live: no shipped source returns a non-dict today. The value is that
    this now fails for any registered source that grows the hole, including one added later.
    """
    hint = src.health_hint(raw)
    assert hint["count"] == 0
    assert hint["landed_host"] == "" and hint["requested_host"] == ""
    assert "fetch_error" not in hint, "a non-dict carries no error to report"


def test_the_conformance_sweep_actually_sees_the_overriding_sources():
    """Guards the enumeration itself.

    A registry that failed to autoload would make the sweep above vacuous -- zero parameters,
    green suite, nothing covered. Assert it reaches a source that OVERRIDES `health_hint`,
    which is the case the sweep exists for.
    """
    srcs = _every_registered_source()
    assert len(srcs) > 5, f"the registry looks unloaded: {len(srcs)} sources"
    from sluice.ingest.base import BrowserListSource, CarouselSource
    overriders = [s for s in srcs
                  if type(s).health_hint not in (BrowserListSource.health_hint,
                                                 CarouselSource.health_hint)]
    assert overriders, "no overriding health_hint reached the sweep -- it cannot catch one"


# ---- a raised evaluation must not leak the tab ---------------------------------------------

@pytest.fixture
def _no_prefetch_probes(monkeypatch):
    """Neutralise any network pre-check a source runs before opening its tab.

    `workinstartups` HEAD-checks for a 429 first, which `conftest` rightly blocks as DNS.
    Patched across every source module that has one rather than excluding that source, so the
    sweep keeps covering it -- excluding the awkward member is how a sweep quietly stops being
    a sweep.
    """
    import sys

    for src in _every_registered_source():
        mod = sys.modules.get(type(src).__module__)
        if mod is not None and hasattr(mod, "head_rate_limited"):
            monkeypatch.setattr(mod, "head_rate_limited", lambda _url: None)


@pytest.mark.parametrize("src", _every_registered_source(), ids=lambda s: s.id)
def test_a_RAISING_evaluate_still_closes_the_tab(src, _no_prefetch_probes):
    """`_run_source` retries on `Exception`, so a leaked tab is leaked once PER ATTEMPT.

    `Camofox._api` turns its own failures into `{"error": ...}` rather than raising, which is
    why this never showed up in practice -- but nothing guarantees that of the transport
    underneath it, an injected fake, or `sleep`. And the failure mode is the one that produced
    this whole PR: an exhausted Camofox, with every source reporting an unexplained zero.

    Swept over the registry rather than the two classes, because `fetch` is a protocol member
    and this branch has already fixed one implementation and not its twin twice.
    """
    class _Boom(_Cam):
        def evaluate(self, tid, expr):
            raise RuntimeError("transport blew up mid-evaluate")

    cam = _Boom(rows=[])
    with pytest.raises(RuntimeError):
        src.fetch(_ctx(cam), Search("A", "http://x"))
    assert cam.closed == cam.tabs_opened, (
        f"{type(src).__name__} opened {cam.tabs_opened} tab(s) and closed {cam.closed}")


def test_the_ordinary_path_still_closes_exactly_once():
    # The finally must not double-close, which would be a second API call per fetch.
    cam = _Cam(rows=[{"title": "T"}])
    _src().fetch(_ctx(cam), Search("A", "http://x"))
    assert cam.closed == 1 and cam.tabs_opened == 1
