"""The dossier fetch waits for a client-rendered page to settle (#228).

WHY THIS EXISTS. `create_tab` awaits `page.goto(waitUntil='domcontentloaded')`, which fires
when the HTML document is parsed. A single-page app has not mounted, let alone fetched and
painted the posting, by that point -- so `document.body.innerText` was read immediately and came
back empty or chrome-only. Measured on a live Ashby posting: the fetcher saw 0 chars where the
vendor's own board API served 3411. `cv run` then composed from the verified bundle with NO job
description, rendered a plausible PDF, and passed the fabrication gate correctly -- the bundle is
sound; there is simply nothing to tailor to. For Ashby and Workday that is EVERY lead on EVERY
run, not an intermittent failure.

The nastier half is the PARTIAL render: a page that paints chrome but not the posting yields a
SHORT NON-EMPTY body, which `jd_arrived` treats as having arrived at the shipped `min_jd_chars`
of 0 -- so it is not flagged at all, and is worse than the empty case that is.

NO SEAM CHANGE. `Fetcher` (core/protocols.py) has exactly four members, and settling needs only
`evaluate`, so nothing here widens the Protocol, touches `core/camofox.py`, or changes the
surface `tests/harness/browser.py` stands in for. That is also what makes it testable offline:
a fake that returns "" then "" then the real text is a client-rendered page, exactly.
"""
import pytest

from sluice.core import urlguard
from sluice.core.app import Sluice, _LD_JSON_JS
from sluice.core.config import Config
from tests.harness.config import FIXTURE_ADDR as GLOBAL_ADDR


BODY_JS = "document.body.innerText"


@pytest.fixture
def role(titles):
    """A synthetic job title from the seeded pool, mirroring test_dossier_guard.py's fixture.

    Generated rather than hardcoded because this repo ships no opinion about which jobs are
    good, and a literal in a test is how one gets in. `titles` comes from tests/conftest.py.
    """
    return titles[0][0]


class _SettlingTab:
    """A fake Fetcher whose body text follows a SCRIPT of successive reads.

    The last entry repeats forever, so a script models "arrives on read 3 and then stays" and
    "never arrives" with the same two lines.
    """

    _UNSET = object()

    def __init__(self, script, landed="https://jobs.invalid/x", body_result=_UNSET):
        self.script = list(script)
        self.landed = landed
        # A malformed `evaluate` ENVELOPE for the body probe, as opposed to a body whose
        # text is merely empty -- the two must not converge, which is what the refusal
        # test at the bottom of this file pins.
        self.body_result = body_result
        self.body_reads = 0
        self.calls = []

    def create_tab(self, url):
        self.calls.append(("create_tab", url))
        return "tab-1"

    def evaluate(self, tid, js):
        if js == "location.href":
            return {"result": self.landed}
        if js == BODY_JS:
            if self.body_result is not _SettlingTab._UNSET:
                self.body_reads += 1
                return self.body_result
            i = min(self.body_reads, len(self.script) - 1)
            self.body_reads += 1
            self.calls.append(("body", self.script[i]))
            return {"result": self.script[i]}
        # document.title and the JSON-LD probe: not what this file is about.
        return {"result": ""}

    def scroll(self, tid, amount):
        pass

    def close_tab(self, tid):
        self.calls.append(("close_tab", tid))


def _fetch(tmp_path, tab, role, *, settle_ms=None):
    """Drive the real dossier fetch closure, recording every sleep it asks for.

    `role` is a PARAMETER rather than a bare name resolved from module scope: without it the
    name binds to the pytest fixture FUNCTION defined above, so every lead built here carried a
    `FixtureFunctionDefinition` where a job title belongs. Nothing asserted on the field, so it
    passed.
    """
    waits = []
    cfg = Config()
    if settle_ms is not None:
        cfg.dossier_settle_ms = settle_ms
    app = Sluice(cfg, fetcher=tab, sleep=waits.append,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    # No assert here on purpose. The bug this parameter fixed -- a bare `role` resolving to
    # the module-scope fixture FUNCTION -- is now structurally impossible, since `role` is a
    # required positional and every caller passes its own fixture value. An assert would read
    # as coverage while only being able to fire if a future test deliberately passed a
    # non-string.
    dossier = cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                             "role": role})
    return dossier, waits


def test_a_client_rendered_page_is_read_after_it_settles(tmp_path, role):
    """The headline case: empty at domcontentloaded, real text once the SPA mounts.

    Without the settle this returns "" and `cv run` composes blind against the bundle alone.
    """
    tab = _SettlingTab(["", "", "THE REAL JOB DESCRIPTION, at last."])
    dossier, _waits = _fetch(tmp_path, tab, role)
    assert dossier["jd"]["markdown"] == "THE REAL JOB DESCRIPTION, at last."


def test_a_partially_rendered_page_returns_the_settled_text_not_the_first_paint(tmp_path, role):
    """A body that GROWS must be read at its final size, not at its first non-empty one.

    This is the silent case: the short first paint is non-empty, so `jd_arrived` accepts it at
    the shipped floor of 0 and nothing anywhere reports a problem.
    """
    tab = _SettlingTab(["Apply now", "Apply now | Loading",
                        "Apply now | Full posting text, all of it.",
                        "Apply now | Full posting text, all of it."])
    dossier, _waits = _fetch(tmp_path, tab, role)
    assert dossier["jd"]["markdown"] == "Apply now | Full posting text, all of it."


def test_a_stable_page_confirms_once_and_does_not_spend_the_budget(tmp_path, role):
    """A server-rendered page must not pay the whole settle budget on every lead.

    It reads once, confirms the text is unchanged, and stops -- so the cost of this feature on
    the pages that never needed it is one extra `evaluate` and one short wait, not `settle_ms`.
    A settle that always spent its budget would add that to every dossier fetch in a run.
    """
    tab = _SettlingTab(["A COMPLETE SERVER-RENDERED POSTING."])
    dossier, waits = _fetch(tmp_path, tab, role, settle_ms=5000)
    assert dossier["jd"]["markdown"] == "A COMPLETE SERVER-RENDERED POSTING."
    assert tab.body_reads == 2, f"expected one read plus one confirming read, got {tab.body_reads}"
    assert sum(waits) <= 1.0, f"a stable page waited {sum(waits)}s, which every lead now pays"


def test_a_body_that_never_arrives_is_bounded_and_still_reports_empty(tmp_path, role):
    """An SPA that never mounts must give up, not spin -- and must not invent a JD.

    The outcome stays exactly what it is today (an empty JD, which `jd_arrived` refuses as a
    FACT at every floor); only the number of attempts before concluding it changes.
    """
    tab = _SettlingTab([""])
    dossier, waits = _fetch(tmp_path, tab, role, settle_ms=1000)
    assert dossier["jd"]["markdown"] == ""
    assert tab.body_reads <= 12, f"unbounded polling: {tab.body_reads} reads"
    assert sum(waits) <= 1.5, f"waited {sum(waits)}s against a 1000ms budget"


def test_the_settle_budget_is_configurable_and_zero_restores_the_single_read(tmp_path, role):
    """`0` turns the settle OFF, giving byte-identical pre-#228 behaviour: one read, no wait.

    Kept configurable because the right budget is a property of the boards a user actually
    scrapes, and kept honest at 0 because that is the value an operator reaches for to prove
    the settle is what changed a result.
    """
    tab = _SettlingTab(["", "", "arrived too late"])
    dossier, waits = _fetch(tmp_path, tab, role, settle_ms=0)
    assert dossier["jd"]["markdown"] == ""
    assert tab.body_reads == 1, f"settle_ms=0 must read exactly once, got {tab.body_reads}"
    assert waits == [], f"settle_ms=0 must not wait at all, waited {waits}"


@pytest.mark.parametrize("settle_ms", [0, 250, 5000])
@pytest.mark.parametrize("bad", [None, "not-a-dict", {}, {"result": 42}])
def test_an_unreadable_body_still_refuses_at_every_settle_budget(tmp_path, role, settle_ms, bad):
    """Settling must never LAUNDER a malformed envelope into a string.

    `_settle_body` returns its last read exactly as `evaluate` gave it, so the caller's
    `not isinstance(md, str)` refusal still fires. That refusal is what stops a broken browser
    -- a timeout or dropped connection, which `core/camofox.py` swallows into `{"error": ...}`
    -- from being cached as an EMPTY JD, indistinguishable from a posting that genuinely has
    none.

    PARAMETRIZED OVER THE BUDGET because the interesting case is the one the pre-existing
    guard in tests/test_dossier_guard.py cannot reach. Measured: returning `""` instead of
    `None` for a non-dict envelope SURVIVED every test in that file, because with a settle
    budget the loop simply re-reads and gets a non-string again. The laundering was only
    observable with the settle OFF, and nothing exercised that combination.
    """
    tab = _SettlingTab([""], body_result=bad)
    cfg = Config()
    cfg.dossier_settle_ms = settle_ms
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                       "role": role})
    assert str(ei.value) == urlguard.BODY_UNREADABLE
    assert isinstance(ei.value, urlguard.DossierUnavailable)


class _NavigatingTab(_SettlingTab):
    """A page that MOVES partway through the settle, like a late client-side redirect.

    `landed_script` is walked one entry per `location.href` read, the last repeating -- so
    ["ok", "ok", "evil"] is a page that passes the initial check, is read once, and has
    navigated by the time the settle looks again.
    """

    def __init__(self, script, landed_script):
        super().__init__(script)
        self.landed_script = list(landed_script)
        self.landed_reads = 0

    def evaluate(self, tid, js):
        if js == "location.href":
            i = min(self.landed_reads, len(self.landed_script) - 1)
            self.landed_reads += 1
            self.calls.append(("landed", self.landed_script[i]))
            return {"result": self.landed_script[i]}
        return super().evaluate(tid, js)


def test_a_page_that_navigates_mid_settle_is_refused(tmp_path, role):
    """The TOCTOU half of #228's settle, and the reason the guard is re-applied per read.

    Before the settle the body was read ONCE, immediately after the landed-url check, so the
    check-to-read window was a single `evaluate`. Settling turned that into a window as wide as
    `dossier_settle_ms`, and the body read at the end of it comes from wherever the page has
    moved to -- an SSRF straight past the #18 guard, opened by the feature that reads more
    slowly. A client-rendered posting is precisely the kind of page that routes after load.

    The refusal must also mean the body never arrives: reaching the bytes and discarding them
    afterwards is not a guard, because the request has already been made.
    """
    tab = _NavigatingTab(
        # Never stabilises, so the settle keeps looking -- which is when the move happens.
        ["", "", "INTERNAL SERVICE RESPONSE"],
        ["https://jobs.invalid/x", "https://jobs.invalid/x", "http://internal.invalid/admin"])
    # Host-AWARE, matching test_dossier_guard.py's redirect test. A resolver that hands every
    # host a globally-routable address defeats this test rather than passing it: the guard
    # judges the RESOLVED address, so the destination has to actually resolve somewhere
    # policy refuses.
    def _resolve(host):
        return ["127.0.0.1"] if host == "internal.invalid" else [GLOBAL_ADDR]

    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None, resolve_host=_resolve)
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                       "role": role})
    assert str(ei.value) == urlguard.LANDED_BLOCKED
    read_after_move = [c for c in tab.calls if c[0] == "body"][2:]
    assert not read_after_move, (
        f"the body was read from the moved-to location before the guard refused: "
        f"{read_after_move}")


def test_every_dossier_probe_is_preceded_by_a_landed_url_check(tmp_path, role):
    """Derived from the call sequence rather than counted by hand.

    THE ROSTER IS THE CLAIM. The first version of this guard watched only
    `document.body.innerText`, which was the roster when the body was the sole JD source. #228
    made the page's JSON-LD a JD source too, and that probe ran AFTER the settle's last check
    with nothing in between -- so a tab that moved late had its metadata read from the new
    location and returned as the JD, unrefused, while this test stayed green. A guard applied
    to SOME reads is the same defect as a guard applied to none, one probe later.

    Every probe whose result reaches the dossier is watched here, and the ordering is derived
    by walking the sequence: a hand-written expected count goes stale the first time the loop's
    shape changes, and this assertion should not.
    """
    tab = _SettlingTab(["", "", "settled at last"])
    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)

    seen = []
    orig = tab.evaluate
    # Every probe that CONTRIBUTES to the dossier, not just the body.
    watched = {BODY_JS: "body", "document.title": "title", _LD_JSON_JS: "ld"}

    def spy(tid, js):
        if js == "location.href":
            seen.append("landed")
        elif js in watched:
            seen.append(watched[js])
        return orig(tid, js)

    tab.evaluate = spy
    cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})

    # ANTI-VACUITY, on BOTH axes: an ordering claim over probes is satisfied trivially by a run
    # that made almost none, and the roster claim is satisfied trivially if a probe never ran.
    assert seen.count("body") >= 3, f"the settle did not actually loop: {seen}"
    assert {"body", "title", "ld"} <= set(seen), (
        f"a dossier-contributing probe never ran, so this guard did not watch it: {seen}")
    assert seen[0] == "landed", f"the first probe must be the guard, not a read: {seen}"
    assert all(seen[i - 1] == "landed" for i, k in enumerate(seen) if k != "landed"), (
        f"a dossier probe was not immediately preceded by a landed-url check: {seen}")


def test_a_page_that_moves_before_the_json_ld_probe_is_refused(tmp_path, role):
    """The behavioural half of the roster point above, on the probe that was missing one.

    `structured_data` is a JD source since #228, so reading it from a location policy has not
    cleared is the same SSRF as reading the body from one -- and it was reachable, because the
    settle can finish and hand over while the page is still free to navigate.
    """
    tab = _NavigatingTab(
        # A body that settles immediately, so the move lands AFTER the last body-read check
        # and before the metadata probes -- the window this test exists for.
        ["a stable body", "a stable body"],
        ["https://jobs.invalid/x", "https://jobs.invalid/x", "https://jobs.invalid/x",
         "http://internal.invalid/admin"])

    def _resolve(host):
        return ["127.0.0.1"] if host == "internal.invalid" else [GLOBAL_ADDR]

    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None, resolve_host=_resolve)
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})
    assert str(ei.value) == urlguard.LANDED_BLOCKED


def test_a_malformed_body_envelope_refuses_at_once_without_polling(tmp_path, role):
    """A broken probe is a transport failure, not a page that has not painted.

    Polling it cannot help -- the caller refuses it as BODY_UNREADABLE either way -- and doing
    so spent the entire budget first. In an offline suite with an injected no-op sleep that is
    invisible; with the real clock it put ~5 seconds into a single test, which is a browser
    failure being simulated as slowness.
    """
    waits = []
    tab = _SettlingTab([""], body_result="not-a-dict")
    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=waits.append,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})
    assert str(ei.value) == urlguard.BODY_UNREADABLE
    assert tab.body_reads == 1, f"a malformed envelope was polled {tab.body_reads} times"
    assert waits == [], f"a malformed envelope cost {sum(waits)}s of waiting: {waits}"


def test_a_body_that_shrinks_after_first_paint_keeps_the_longest_read(tmp_path, role):
    """A settled page can be OVERLAID, and the last read is then the overlay.

    Measured on a live posting: the JD painted in full, then a cookie banner replaced the body
    text. Returning the LAST read made the shipped default strictly WORSE than
    `dossier_settle_ms: 0`, which had returned the complete posting — a settle that loses a JD
    it already held is worse than no settle at all. The rule is the same one the caller applies
    between body and JSON-LD: prefer whichever yields more text.
    """
    posting = "The complete job description, every word of it. " * 5
    overlay = "We use cookies. Accept all. Manage preferences."
    tab = _SettlingTab([posting, overlay, overlay])
    dossier, _waits = _fetch(tmp_path, tab, role, settle_ms=5000)
    assert dossier["jd"]["markdown"].startswith("The complete job description")
    assert "cookies" not in dossier["jd"]["markdown"]


def test_an_unsettled_body_is_logged_rather_than_passed_off_as_settled(tmp_path, role, caplog):
    """Budget exhaustion is not stability, and the returned value cannot say which it was.

    A page still painting when the budget expires yields truncated mid-render text that reads
    exactly like a settled short posting. Before #228 it read as "" and was honestly reported
    as `dossier_failed`; this is the second loud->quiet conversion the settle introduces, and
    nothing local can separate truncated-but-growing from finished-and-short without a
    judgement about posting length that `min_jd_chars` deliberately does not ship. So the fact
    is RECORDED rather than guessed at.
    """
    # Grows on every read and never repeats, so it can only ever exit by exhaustion.
    script = [f"partially rendered {'x' * n}" for n in range(1, 60)]
    with caplog.at_level("WARNING"):
        dossier, _waits = _fetch(tmp_path, tab := _SettlingTab(script), role, settle_ms=1000)
    assert dossier["jd"]["markdown"].startswith("partially rendered")
    logged = [r.getMessage() for r in caplog.records]
    assert any("never settled" in m for m in logged), f"exhaustion was not logged: {logged}"
    assert tab.body_reads > 1


def test_a_whitespace_only_body_is_not_treated_as_settled(tmp_path, role):
    """An unmounted SPA usually yields whitespace, not the empty string.

    The emptiness test strips for that reason, and stripping was pinned by nothing: deleting
    `.strip()` made two whitespace reads compare equal and settle, returning "\\n  \\n" as the
    JD while the real posting was one read away.
    """
    tab = _SettlingTab(["\n  \n", "\n  \n", "THE REAL POSTING, once it mounted."])
    dossier, _waits = _fetch(tmp_path, tab, role, settle_ms=5000)
    assert dossier["jd"]["markdown"] == "THE REAL POSTING, once it mounted."


def test_a_sub_interval_budget_still_polls_once(tmp_path, role):
    """`dossier_settle_ms: 1` asks for a settle; answering with the OFF behaviour would be the
    quiet wrong default this codebase engineers out.

    The `max(1, ...)` floor is stated in two comments and was pinned by nothing — deleting it
    made every budget below one poll interval behave exactly as 0.
    """
    tab = _SettlingTab(["", "arrived on the second read"])
    dossier, _waits = _fetch(tmp_path, tab, role, settle_ms=1)
    assert dossier["jd"]["markdown"] == "arrived on the second read"
    assert tab.body_reads == 2, f"a sub-interval budget must still poll once, got {tab.body_reads}"


def test_a_guard_that_raises_mid_settle_becomes_a_transport_refusal(tmp_path, role):
    """The seam is free to raise, and an unwrapped raise cost the whole dossier.

    `c` is the injected Fetcher, so `evaluate("location.href")` can fail on a browser JS
    error, a timeout or a dropped connection. Left unwrapped that escaped `fetch` as a bare
    RuntimeError, discarding a JD already read from the tab — the harm `_probe`'s isolation
    exists to prevent, reintroduced by moving the guard outside it. It is now the slug that
    already names this condition, so callers see the DossierUnavailable they understand.

    A POLICY refusal must still propagate untouched; `test_a_page_that_navigates_mid_settle_is_
    refused` is that half.
    """
    class _RaisingGuard(_SettlingTab):
        def __init__(self, script, raise_on_landed_read):
            super().__init__(script)
            self.raise_on = raise_on_landed_read
            self.landed_reads = 0

        def evaluate(self, tid, js):
            if js == "location.href":
                self.landed_reads += 1
                if self.landed_reads == self.raise_on:
                    raise RuntimeError("browser connection dropped")
                return {"result": self.landed}
            return super().evaluate(tid, js)

    tab = _RaisingGuard(["", "", "would have arrived"], raise_on_landed_read=3)
    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})
    assert str(ei.value) == urlguard.LANDED_UNREADABLE
    assert isinstance(ei.value, urlguard.DossierUnavailable), (
        "a dropped browser connection is a TRANSPORT failure, not a policy refusal")


def test_a_broken_envelope_mid_settle_keeps_the_jd_already_read(tmp_path, role, caplog):
    """A transport hiccup AFTER a good read must not discard what the fetch already holds.

    Measured before this: a complete posting on read 1 followed by one malformed envelope
    returned the full JD at `dossier_settle_ms: 0` and REFUSED at 5000 — the settle strictly
    worse than off, the same failure the longest-read rule exists to prevent, arriving by a
    different route.

    The FIRST-read case is untouched and still fails closed: with nothing in hand there is no
    JD to protect, and a broken browser must not become a cached empty JD. That is the pairing
    that makes this safe rather than a weakening of BODY_UNREADABLE.
    """
    class _HiccupTab(_SettlingTab):
        def evaluate(self, tid, js):
            if js == BODY_JS:
                self.body_reads += 1
                if self.body_reads == 1:
                    return {"result": "THE COMPLETE POSTING, already in hand."}
                return "not-a-dict"
            return super().evaluate(tid, js)

    tab = _HiccupTab([""])
    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with caplog.at_level("WARNING"):
        dossier = cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                                 "role": role})
    assert dossier["jd"]["markdown"] == "THE COMPLETE POSTING, already in hand."
    assert any("mid-settle" in r.getMessage() for r in caplog.records), (
        "keeping the earlier read must be reported, not silent")


def test_a_broken_envelope_after_a_BLANK_read_still_fails_closed(tmp_path, role):
    """The other arm of the mid-settle rule, and the one that keeps it safe.

    With a blank first read there is no JD in hand, so a later broken envelope must still
    refuse — otherwise a broken browser becomes a cached EMPTY JD, which is the exact
    BODY_UNREADABLE harm the immediate-refusal rule exists for. Without this row, weakening
    the arm to keep the blank text left the whole suite green: the pre-loop check only covers
    a first read that is a NON-STRING, not one that is an empty string.
    """
    class _BlankThenBroken(_SettlingTab):
        def evaluate(self, tid, js):
            if js == BODY_JS:
                self.body_reads += 1
                if self.body_reads == 1:
                    return {"result": "   "}      # a string, so the pre-loop check passes
                return "not-a-dict"
            return super().evaluate(tid, js)

    tab = _BlankThenBroken([""])
    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})
    assert str(ei.value) == urlguard.BODY_UNREADABLE
    assert isinstance(ei.value, urlguard.DossierUnavailable)


class _RaisingBodyTab(_SettlingTab):
    """A tab whose BODY read raises on the nth call — the seam is free to."""

    def __init__(self, raise_on_body_read):
        super().__init__(["THE COMPLETE POSTING, already in hand."])
        self.raise_on = raise_on_body_read

    def evaluate(self, tid, js):
        if js == BODY_JS:
            self.body_reads += 1
            if self.body_reads == self.raise_on:
                raise RuntimeError("browser connection dropped")
            return {"result": self.script[0]}
        return super().evaluate(tid, js)


def _drive(tmp_path, tab, role):
    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    return app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)


def test_a_raising_body_read_mid_settle_keeps_the_jd_already_read(tmp_path, role, caplog):
    """`_check_landed` wrapped its own `evaluate`; the BODY reads did not, and the asymmetry
    was the bug.

    Measured before this: a raise on the second body read escaped `fetch` as a bare
    RuntimeError — losing a complete posting already in hand AND never becoming the
    DossierUnavailable the callers understand.
    """
    cache = _drive(tmp_path, tab := _RaisingBodyTab(raise_on_body_read=2), role)
    with caplog.at_level("WARNING"):
        dossier = cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                                 "role": role})
    assert dossier["jd"]["markdown"] == "THE COMPLETE POSTING, already in hand."
    assert any("mid-settle" in r.getMessage() for r in caplog.records)
    assert tab.body_reads >= 2


def test_a_raising_first_body_read_becomes_a_transport_refusal(tmp_path, role):
    """With nothing in hand it must still fail closed — and as the right exception type.

    A bare browser exception escaping `fetch` is not a refusal any caller recognises; the
    per-lead handlers see an unclassified error rather than BODY_UNREADABLE.
    """
    cache = _drive(tmp_path, _RaisingBodyTab(raise_on_body_read=1), role)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})
    assert str(ei.value) == urlguard.BODY_UNREADABLE
    assert isinstance(ei.value, urlguard.DossierUnavailable)


def test_a_policy_refusal_from_the_guard_still_propagates_unconverted(tmp_path, role):
    """The wrapping must not swallow a REFUSAL. The guard runs outside the try for that
    reason, and this is the row that keeps it there: a page moving to a blocked host during
    the settle must still be LANDED_BLOCKED, not degraded into a body-read failure."""
    tab = _NavigatingTab(
        ["", "", "INTERNAL SERVICE RESPONSE"],
        ["https://jobs.invalid/x", "https://jobs.invalid/x", "http://internal.invalid/admin"])

    def _resolve(host):
        return ["127.0.0.1"] if host == "internal.invalid" else [GLOBAL_ADDR]

    cfg = Config()
    cfg.dossier_settle_ms = 5000
    app = Sluice(cfg, fetcher=tab, sleep=lambda _s: None, resolve_host=_resolve)
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    with pytest.raises(urlguard.DossierBlocked) as ei:
        cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co", "role": role})
    assert str(ei.value) == urlguard.LANDED_BLOCKED
