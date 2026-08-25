"""Guards for the 2026-08-25 board-drift sweep.

Every failure these pin is the same shape: a board moved, the scraper kept asking
the old question, got nothing, and the nothing was read as the board being dead.
These are cheap string assertions on purpose -- the extractors themselves need a
live DOM, so what is pinned here is the part that silently rotted: which host we
ask, which selector we ask for, and whether a board is switched on at all.
"""
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
        assert (parts.hostname or "").lower().endswith("escapethecity.org")
        assert parts.path.startswith("/search/jobs"), \
            "escape_city must use the path the 302 points at"
        assert not parts.path.startswith("/opportunities")


@pytest.mark.parametrize("source_id", ["escape_city", "bwork", "theorg"])
def test_disabled_boards_say_why_next_to_the_flag(source_id):
    """A retirement is a claim about the world and goes stale. Each must carry its reason
    in the module docstring so the next person can re-probe it rather than inherit it."""
    src = _src(source_id)
    assert src.enabled is False
    mod = __import__(f"sluice.ingest.sources.{source_id}", fromlist=["*"])
    assert mod.__doc__ and "2026-08-25" in mod.__doc__, \
        f"{source_id} retirement has not been re-probed"
