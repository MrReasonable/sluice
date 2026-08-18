"""A scraper's dominant failure mode is not crashing -- it is SUCCEEDING at reading the
wrong page, and every signal `core/health.py` had before #156 reported that as healthy.
`detect_drift` classified only on a bare count and a host pair; nothing compared the
SHAPE of what came back.

Four incidents share this: (1) a results-page fallback that cannot see a company yielded
~25 rows with `company: ""` and a nav link ingested as a vacancy, and health recorded a
healthy count; (2) logged-out markup rendered a full page of jobs the extractor's
authenticated-only selectors could not see, reporting an indistinguishable-from-empty
zero; (3) an auth redirect (`/jobs` -> `/login?redirect=%2F`) was invisible because drift
compared HOST only, and the host never changed; (4) the same board returned a constant,
low, non-zero count for five weeks -- a flat zero would have tripped auto-retire, a
stable 5 tripped nothing.

This file is the #156 narrative and is deliberately separate from
`test_health_explained_zero.py`, whose module docstring is specifically the 2026-08-15
wrong-Camofox-profile incident -- a different root cause, and folding this in would make
that docstring lie about what the file covers.
"""
import pathlib
import re

import pytest

from sluice.core.health import (
    _LOGIN_SEGMENTS,
    _RECOVERABLE,
    _explained,
    _login_segment,
    detect_drift,
)
from sluice.ingest import sources as registry

# ---- fallback: a row the extractor's own degraded path stamped ------------------------


def test_a_stamped_fallback_row_reports_fallback_not_a_healthy_count():
    # Incident 1: ~25 rows, a marker on every one, a count that looks perfectly normal.
    assert detect_drift("s", 25, {"degraded": "anchor-fallback"}, 25) == "fallback"


def test_an_ordinary_run_with_no_marker_reports_nothing():
    assert detect_drift("s", 25, {}, 25) is None


def test_a_zero_yield_run_never_reports_fallback():
    # count>0 phenomenon only: a fallback that produced NOTHING is a zero, not a fallback --
    # there are no rows to have been degraded.
    assert detect_drift("s", 0, {"degraded": "anchor-fallback"}, 25) == "zero"


def test_fallback_outranks_drop():
    # Direct producer evidence outranks an inferred cause: when a count collapse and a
    # stamped fallback coincide, the fallback names the actionable one.
    assert detect_drift("s", 3, {"degraded": "anchor-fallback"}, 100) == "fallback"


def test_redirect_and_blocked_still_outrank_fallback():
    # `_explained`'s reasons are checked first in `detect_drift` -- a page we know we did
    # not even land on correctly is a stronger diagnosis than a row-level marker on it.
    assert detect_drift(
        "s", 25, {"degraded": "anchor-fallback",
                 "requested_host": "a.invalid", "landed_host": "b.invalid"}, 25
    ) == "redirect"
    assert detect_drift("s", 25, {"degraded": "anchor-fallback", "blocked": True}, 25) == "blocked"


def test_fallback_is_neither_explained_nor_recoverable():
    # A count>0 phenomenon must never defer retirement or persist across searches as an
    # explanation -- both would be the "reason that fires benignly buys a dead source
    # unlimited time" hazard `_explained`'s own docstring warns about.
    assert _explained({"degraded": "anchor-fallback"}) is None
    assert "fallback" not in _RECOVERABLE


# ---- login: a landed URL betraying an auth wall, on a host comparison too weak to see it ---


def test_a_jobs_path_landing_on_login_reports_login():
    # Incident 3: `/jobs?query=...` -> `/login?redirect=%2F`, five weeks, `drift=None`
    # throughout because the OLD comparison was host-only and the host never changed.
    assert detect_drift(
        "s", 0, {"requested_path": "/jobs", "landed_path": "/login"}, 10
    ) == "login"


def test_a_login_wall_that_still_returns_rows_reports_login_too():
    # Incidents 3 and 4 are the SAME event: the same board also returned a constant 5 rows
    # off that login page for the rest of the five weeks. `login` must survive the count>0
    # arm or it misses exactly the incident it exists to name.
    assert detect_drift(
        "s", 5, {"requested_path": "/jobs", "landed_path": "/login"}, 5
    ) == "login"


def test_a_source_that_asked_for_a_login_path_reports_nothing():
    # DOES NOT FIRE: the "absent from requested" half. A source configured to search a
    # login-shaped path is immune by construction, not by luck.
    assert detect_drift(
        "s", 0, {"requested_path": "/login", "landed_path": "/login"}, 10
    ) == "zero"


@pytest.mark.parametrize("landed", [
    "/author/jane-doe",              # contains "auth" but is not an auth wall
    "/challenges/search",            # hackajob is a coding-challenge platform
    "/accountant-jobs",              # naukrigulf/reed-shaped healthy URL
    "/registered-nurse-jobs",        # "register" as a job-title prefix, not a wall
])
def test_the_vocabulary_matches_whole_segments_not_substrings(landed):
    # DOES NOT FIRE. Naive substring matching would put each of these behind a permanent
    # `login`/non-recoverable-but-reported reason -- a real board doing real business.
    assert detect_drift(
        "s", 0, {"requested_path": "/jobs", "landed_path": landed}, 10
    ) == "zero"


@pytest.mark.parametrize("landed", [
    "/authwall",                          # LinkedIn's actual logged-out target
    "/cdn-cgi/challenge-platform/h/g",    # a real Cloudflare interstitial
    "/users/sign_in",                     # Devise, underscore-separated
])
def test_the_boundary_rule_still_catches_real_interstitials(landed):
    # MUST FIRE. Exact-segment matching would miss all three -- proof the prefix+boundary
    # rule is doing real work, not just excluding false positives.
    assert detect_drift(
        "s", 0, {"requested_path": "/jobs", "landed_path": landed}, 10
    ) == "login"


def test_a_failed_read_cannot_manufacture_a_login():
    # `base.py` refuses to echo the request as `landed` on a failed evaluate (landed == "").
    # An empty path has no segments and must not be misread as a wall.
    assert detect_drift(
        "s", 0, {"requested_path": "/jobs", "landed_path": ""}, 10
    ) == "zero"


def test_login_outranks_blocked_but_loses_to_redirect():
    # The precedence decision: a host-changing hop that ALSO lands on a login path is more
    # likely a genuine relocation than a login wall, so `redirect` -- itself not
    # recoverable -- gets first say.
    assert detect_drift(
        "s", 0, {"requested_host": "a.invalid", "landed_host": "b.invalid",
                "requested_path": "/jobs", "landed_path": "/login"}, 10
    ) == "redirect"
    assert detect_drift(
        "s", 0, {"blocked": True, "requested_path": "/jobs", "landed_path": "/login"}, 10
    ) == "login"


def test_an_unreachable_browser_still_outranks_login():
    # If we never got a tab, we did not read a login page either -- `unreachable` is the
    # clearest "could not read" in the system and must win over an inference from a URL
    # that only exists because the read itself failed to overwrite it.
    assert detect_drift(
        "s", 0, {"fetch_error": "no-tab", "requested_path": "/jobs", "landed_path": "/login"}, 10
    ) == "unreachable"


def test_login_is_NOT_recoverable():
    # THE reversal from the first design pass. `_is_dead` short-circuits on `count == 0`,
    # so membership here is irrelevant to incidents 3/4 (count 5) -- it matters only for a
    # ZERO-count login wall, and there a permanently paywalled board must retire exactly
    # like a relocated one (`redirect`), not live forever like an expired session (`auth`).
    assert "login" not in _RECOVERABLE


def test_three_login_walled_zeros_DO_retire(tmp_path):
    from sluice.core.health import HealthStore

    h = HealthStore(str(tmp_path / "h.json"))
    for _ in range(3):
        h.record("s", 0, {"requested_path": "/jobs", "landed_path": "/login"})
    assert h.should_retire("s") is True


def test_a_login_walled_source_still_shows_an_explained_streak(tmp_path):
    # `should_retire` DOES fire, but the streak counter still gives an operator visibility
    # before that third run lands -- the same durable signal `auth`/`blocked` already get.
    from sluice.core.health import HealthStore

    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 0, {"requested_path": "/jobs", "landed_path": "/login"})
    h.record("s", 0, {"requested_path": "/jobs", "landed_path": "/login"})
    assert h.explained_streak("s") == ("login", 2)


def test_the_login_vocabulary_is_pinned():
    # SCOPE + a deliberate-change gate: widening this set weakens auto-retire (a false
    # match keeps a dead board alive forever, per `_RECOVERABLE`'s docstring), so it must
    # be a decision made in a diff review, not a drive-by edit.
    assert _LOGIN_SEGMENTS == frozenset({
        "login", "signin", "sign-in", "signon", "logon",
        "auth", "authwall", "authenticate", "oauth", "sso",
        "session", "sessions",
        "challenge", "captcha", "verify", "register", "onboarding", "2fa", "mfa",
    })


def test_account_is_deliberately_excluded_from_the_vocabulary():
    # `/account/jobs` is a legitimate results page on more than one board -- the one word
    # that produced a plausible-healthy false positive under every matching strategy
    # measured. Its own test, so a future "helpful" re-add is caught explicitly rather
    # than silently passing the frozenset-equality check above by accident.
    assert "account" not in _LOGIN_SEGMENTS
    assert _login_segment("/account/jobs") is None


def test_the_paths_are_NOT_persisted_across_searches():
    # The matched-pair asymmetry, extended to a second pair (see EXPLAINING_SIGNALS'
    # comment in core/health.py). Pairing search 1's requested path with search 3's landed
    # path could invent a login wall that never happened.
    from sluice.core.health import EXPLAINING_SIGNALS

    assert "requested_path" not in EXPLAINING_SIGNALS
    assert "landed_path" not in EXPLAINING_SIGNALS


def test_no_shipped_source_search_url_already_matches_the_vocabulary():
    # If this ever fails, the guard below ("absent from requested") is finally load-bearing
    # for a real source rather than pure forward-insurance -- worth knowing, not a bug.
    from urllib.parse import urlparse

    hits = [s.id for s in registry.all_sources()
            for search in s.searches()
            if search.url and _login_segment(urlparse(search.url).path)]
    assert hits == [], f"a shipped source's own search URL already reads as a login path: {hits}"


# ---- scope guard: every whole-row fallback in a shipped extractor must self-declare ----

_SOURCES_DIR = pathlib.Path(__file__).resolve().parent.parent / "sluice" / "ingest" / "sources"
# The shape of a whole-row fallback branch: "if row count is zero, generate rows some other
# way". Reed's weaker single-field fallback (a link-only cascade) doesn't match this pattern
# and is swept by name below instead -- the two are different degradation classes with
# different detection, not an oversight.
_WHOLE_ROW_FALLBACK = re.compile(r"if\s*\(\s*r\.length\s*===\s*0\s*\)\s*\{")


def _whole_row_fallback_branches():
    """(path, branch_body) for every whole-row fallback found in the extractor sources.

    Swept over every .py file in the directory, not the registry: `_stepstone.py` is
    underscore-prefixed (a shared helper, not a registered source) and invisible to
    `sources.all_sources()`, but its fallback is exactly the one #156 is about.
    """
    branches = []
    for path in sorted(_SOURCES_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in _WHOLE_ROW_FALLBACK.finditer(text):
            # Take a generous slice after the match -- these branches are a few lines of
            # minified-ish JS, not deeply nested, so a fixed window comfortably covers one
            # branch body without needing a real brace-matcher. 1500 chars, not 800: a
            # branch preceded by a long explanatory comment (as `_stepstone.py`'s is) can
            # push the actual `degraded:` stamp past a tighter window and false-negative.
            branches.append((path.name, text[m.end():m.end() + 1500]))
    return branches


def test_the_whole_row_fallback_sweep_finds_something():
    # A guard over a negative property (every fallback is marked) passes vacuously if the
    # sweep matches nothing. Pin that it actually looked at a real fallback branch.
    branches = _whole_row_fallback_branches()
    assert branches, "no whole-row fallback found in sluice/ingest/sources/ -- sweep is vacuous"


_DEGRADED_STAMP = re.compile(r"degraded\s*:\s*['\"]")  # the JS object-key STAMP, not prose


def test_every_whole_row_fallback_stamps_a_degraded_marker():
    # Matches `degraded: '...'` specifically, NOT a bare "degraded" -- a first version of
    # this used `"degraded" not in body`, which stayed green after the marker was deleted
    # from _stepstone.py's pushed row, because the branch's own explanatory COMMENT (right
    # above it, inside the scan window) also contains the word "degraded". Grep the CLAIM,
    # not the prose that describes it.
    unmarked = [name for name, body in _whole_row_fallback_branches()
                if not _DEGRADED_STAMP.search(body)]
    assert not unmarked, (
        f"a whole-row fallback that cannot see a company ships with no `degraded` marker: "
        f"{unmarked} -- it will report as a healthy count forever, exactly incident 1"
    )


def test_reeds_link_only_fallback_also_stamps_a_marker():
    # The weaker, cheap follow-up folded into this same commit: reed's unscoped final link
    # tier (`c.querySelector('a')?.href`, no class/attribute scoping at all) degrades one
    # field rather than the whole row, so it doesn't match the whole-row pattern above and
    # needs its own named witness.
    text = (_SOURCES_DIR / "reed.py").read_text(encoding="utf-8")
    assert "link-fallback" in text, "reed's unscoped final anchor tier lost its marker"
