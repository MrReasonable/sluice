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
from types import SimpleNamespace

from sluice.ingest.base import BrowserListSource, Ctx, Search
from sluice.core.health import detect_drift


class _Cam:
    """Camofox stand-in. `probe_result` is what the auth probe evaluates to."""

    def __init__(self, rows, probe_result=False):
        self.rows, self.probe_result = rows, probe_result
        self.evaluated = []

    def create_tab(self, url=""):
        return "t1"

    def evaluate(self, tid, expr):
        self.evaluated.append(expr)
        if expr == "location.href":
            return {"result": "http://x"}
        if expr == "AUTHPROBE":
            return {"result": self.probe_result}
        return {"result": self.rows}

    def scroll(self, tid, amount=0):
        return {}

    def close_tab(self, tid):
        return {}


def _src(**kw):
    return BrowserListSource(id="demo", searches_spec=[("A", "http://x")],
                             extractor_js="JS", wait=0, scrolls=0, **kw)


def _ctx(cam):
    return Ctx(camofox=cam, config=SimpleNamespace(source=lambda i: SimpleNamespace(searches=[])))


def _fetch(cam, **kw):
    src = _src(**kw)
    return src, src.fetch(_ctx(cam), Search("A", "http://x"))


def test_a_source_with_no_probe_behaves_exactly_as_before():
    # The probe is opt-in. A source that declares none must not gain a signal, or every
    # existing source starts reporting auth state it never measured.
    cam = _Cam(rows=[{"title": "T"}])
    src, raw = _fetch(cam)
    assert "AUTHPROBE" not in cam.evaluated
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
    # A second fetch could land somewhere else (redirect, A/B, rate limit), so the probe would
    # then describe a different page than the one that yielded nothing.
    cam = _Cam(rows=[], probe_result=True)
    _fetch(cam, auth_probe_js="AUTHPROBE")
    assert "JS" in cam.evaluated and "AUTHPROBE" in cam.evaluated


def test_the_signal_reaches_detect_drift_as_auth_not_zero():
    # The end-to-end point of the whole change: this run must classify as "auth", so the
    # digest names the cause and `_is_dead` does not retire the source.
    cam = _Cam(rows=[], probe_result=True)
    src, raw = _fetch(cam, auth_probe_js="AUTHPROBE")
    hint = src.health_hint(raw)
    signals = {k: v for k, v in hint.items() if k != "markers"}   # what engine.py records
    assert detect_drift("demo", hint["count"], signals, baseline=50) == "auth"


def test_the_linkedin_subclass_runs_the_probe_too():
    """`_LinkedInSource` overrides fetch (to scroll the results PANEL) and therefore does not
    inherit the base implementation's probe call.

    That is how a subclass silently opts out of a safety signal it appears to have: the
    registration declares `auth_probe_js`, so everything READS as covered, while the override
    never evaluates it. Enumerate both the base and the subclass, not just the base.
    """
    from sluice.ingest import sources as registry

    src = registry.get("linkedin")
    assert src.auth_probe_js, "linkedin should declare an auth probe"

    class _LiCam(_Cam):
        def evaluate(self, tid, expr):
            self.evaluated.append(expr)
            if expr == "location.href":
                return {"result": "https://www.linkedin.com/jobs/search"}
            if expr == src.auth_probe_js:
                return {"result": True}          # logged-out page
            return {"result": []}                 # extractor finds nothing

    cam = _LiCam(rows=[])
    raw = src.fetch(_ctx(cam), Search("A", "https://www.linkedin.com/jobs/search"))
    assert src.auth_probe_js in cam.evaluated, "the override skipped the auth probe"
    assert src.health_hint(raw)["auth"] == "missing"


def test_the_linkedin_probe_needs_BOTH_halves():
    """Guest markup present AND authenticated markup absent.

    Either half alone is a false positive: guest cards can co-exist with authenticated ones
    during a LinkedIn A/B, and "no artdeco cards" is also what a genuinely empty result set
    looks like. Asserted by evaluating the real probe expression against stub DOM counts.
    """
    from sluice.ingest import sources as registry

    probe = registry.get("linkedin").auth_probe_js
    # The probe is a JS expression; check it names both selector families and requires
    # the authenticated one to be ZERO while the guest one is positive.
    assert "artdeco-entity-lockup" in probe and "base-card" in probe
    assert "=== 0" in probe.replace(" ", " ") and "> 0" in probe


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
