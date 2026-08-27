"""Guards for the 2026-08-25 board-drift sweep.

Every failure these pin is the same shape: a board moved, the scraper kept asking
the old question, got nothing, and the nothing was read as the board being dead.
These are cheap string assertions on purpose -- the extractors themselves need a
live DOM, so what is pinned here is the part that silently rotted: which host we
ask, which selector we ask for, and whether a board is switched on at all.
"""
import re
from urllib.parse import urlparse

import pytest

from sluice.ingest import sources


def _src(source_id):
    return sources.get(source_id)


def test_jobserve_extractor_is_not_keyed_on_the_retired_short_code_pattern():
    """The old extractor scanned every anchor for `^/[A-Za-z0-9]{4,8}$` short codes.

    Jobserve stopped emitting those, so it matched zero anchors on a page serving
    11,440 jobs -- a bare zero with no error, which the runtime auto-retires on.
    """
    js = _src("jobserve").extractor_js
    assert "{4,8}" not in js, "short-code href regex is back; it matches nothing on the live board"
    assert "#joblistingcollection" in js, "extractor must anchor on the row container, not an href shape"


def test_jobserve_reads_the_fields_the_short_code_extractor_left_blank():
    js = _src("jobserve").extractor_js
    assert "summlocation" in js and "summrate" in js


def test_hackajob_is_enabled_and_points_at_the_live_domain():
    """hackajob was retired as invite-only. The domain had simply moved .co -> .com."""
    src = _src("hackajob")
    assert src.enabled is True, "hackajob is live at hackajob.com/jobs; do not re-retire without a probe"
    urls = [u for _, u in src.searches_spec]
    assert urls, "hackajob must ship an example search"
    for u in urls:
        # Parsed, not substring-matched: `"hackajob.com" in url` also passes for
        # https://evil.example/?x=hackajob.com, which is why CodeQL rejects that
        # shape (py/incomplete-url-substring-sanitization). Comparing the parsed
        # host also pins the registrable domain rather than any host ending in it.
        host = (urlparse(u).hostname or "").lower()
        assert host == "hackajob.com", f"expected hackajob.com, got {host!r} in {u!r}"


def test_escape_city_url_follows_the_redirect_the_retirement_note_recorded():
    """The 2026-07-07 note wrote down the new path (/search/jobs) and retired it anyway."""
    src = _src("escape_city")
    for _, u in src.searches_spec:
        parts = urlparse(u)
        # Exact hosts, not endswith: "evilescapethecity.org" ends with the domain too,
        # which is the same CodeQL finding as the hackajob assertion above.
        assert (parts.hostname or "").lower() in {"escapethecity.org",
                                                  "www.escapethecity.org"}
        assert parts.path.startswith("/search/jobs"), \
            "escape_city must use the path the 302 points at"
        assert not parts.path.startswith("/opportunities")


def _disabled_source_ids():
    """Every registered source flagged disabled, DERIVED from the registry.

    This roster used to be hand-listed as ["escape_city", "bwork", "theorg"], and the
    omission it produced is the exact failure this file is about: `hired` was the one
    2026-07-07 retirement never independently re-probed, and being absent from the list
    meant the guard written to catch that could not see it. A hand-list is wrong within
    one revision -- derive it, so a source disabled tomorrow is covered the same day.
    """
    return sorted(s.id for s in sources.all_sources() if not getattr(s, "enabled", True))


# The sweep that established the re-probe discipline. A retirement dated on or after this
# has been checked against the live world at least once; anything older is inherited belief.
# A FLOOR rather than the single pinned string this used to compare against, so a later
# re-probe can supersede an earlier one without the test having to be edited to match.
_REPROBE_FLOOR = (2026, 8, 25)
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def test_the_disabled_roster_is_not_vacuous():
    # `@parametrize` over an empty list runs ZERO tests and reports green, so a registry
    # that failed to load would silently certify nothing -- the "a sweep that discovers
    # nothing passes" shape. Assert the SCOPE before trusting any verdict over it.
    assert sources.all_sources(), "registry empty -- the re-probe guard would certify nothing"
    assert _disabled_source_ids(), \
        "no disabled sources found -- the re-probe guard has nothing to check"


@pytest.mark.parametrize("source_id", _disabled_source_ids())
def test_disabled_boards_say_why_next_to_the_flag(source_id):
    """A retirement is a claim about the world and goes stale. Each must carry its reason
    in the module docstring so the next person can re-probe it rather than inherit it."""
    src = _src(source_id)
    assert src.enabled is False
    mod = __import__(f"sluice.ingest.sources.{source_id}", fromlist=["*"])
    dates = [tuple(int(part) for part in found)
             for found in _ISO_DATE.findall(mod.__doc__ or "")]
    assert any(d >= _REPROBE_FLOOR for d in dates), (
        f"{source_id} retirement has not been re-probed: its docstring carries no date on "
        f"or after {'-'.join(f'{p:02d}' for p in _REPROBE_FLOOR)}, so the reason there is "
        f"inherited rather than checked"
    )
