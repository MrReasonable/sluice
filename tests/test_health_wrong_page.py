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

from sluice.core.health import _RECOVERABLE, _explained, detect_drift

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
