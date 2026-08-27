"""Guards for the 2026-08-25 board-drift sweep.

Every failure these pin is the same shape: a board moved, the scraper kept asking
the old question, got nothing, and the nothing was read as the board being dead.
These are cheap string assertions on purpose -- the extractors themselves need a
live DOM, so what is pinned here is the part that silently rotted: which host we
ask, which selector we ask for, and whether a board is switched on at all.
"""
import re
from datetime import date
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
_REPROBE_FLOOR = date(2026, 8, 25)
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

# The date must sit on a line that SAYS it is a re-probe. Without this the check accepts any
# recent date anywhere in the docstring -- a note about when the board changed its layout
# would satisfy a guard that is supposed to assert someone went and looked. The window is
# narrow today so almost any date in range really is a re-probe date, but the floor is fixed
# and the window widens every day, so the hole grows on its own.
#
# Matched case-insensitively against the LINE, not adjacent to the date, because the shipped
# docstrings put the marker before the date ("retirement CONFIRMED 2026-08-27") and after it
# ("Re-probed 2026-08-27 on the DOMAIN"). `verified` earns its place the same way: escape_city
# records its check as "(verified live 2026-08-25)", which asserts someone went and looked
# just as much as the other three do.
#
# Kept deliberately small, and the test is what decides membership: every entry is a word that
# asserts a CHECK HAPPENED. A word that merely describes a STATE -- "disabled", "retired" --
# must never be added, because the original inherited notes all contain those, and adding one
# would let the very belief this guard exists to expire satisfy it again.
_REPROBE_MARKERS = ("re-probed", "reprobed", "confirmed", "upheld", "verified")


def _reprobe_dates(docstring: str) -> list:
    """Every VALID calendar date on a line that claims a re-probe.

    Three holes this closes, all three measured against the previous tuple comparison rather
    than reasoned about: `(2026, 99, 99) >= (2026, 8, 25)` is `True`, so an impossible date
    from a typo passed; `2099-01-01` passed and would keep passing for ever; and a date on an
    unrelated line passed with no re-probe having happened at all.
    """
    found = []
    for line in (docstring or "").splitlines():
        if not any(marker in line.lower() for marker in _REPROBE_MARKERS):
            continue
        for year, month, day in _ISO_DATE.findall(line):
            try:
                found.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue      # 2026-99-99 and friends are not dates, so not evidence
    return found


def test_the_disabled_roster_is_not_vacuous():
    # `@parametrize` over an empty list runs ZERO tests and reports green, so a registry
    # that failed to load would silently certify nothing -- the "a sweep that discovers
    # nothing passes" shape. Assert the SCOPE before trusting any verdict over it.
    assert sources.all_sources(), "registry empty -- the re-probe guard would certify nothing"
    assert _disabled_source_ids(), \
        "no disabled sources found -- the re-probe guard has nothing to check"


@pytest.mark.parametrize("docstring,why", [
    ("retirement CONFIRMED 2026-99-99",
     "an impossible calendar date -- the previous tuple comparison ranked (2026,99,99) above "
     "the floor, so a typo silently satisfied the guard"),
    ("re-probed 2099-01-01",
     "a FUTURE date, which records nothing anyone did and would satisfy this for ever"),
    ("the board changed its layout on 2026-08-26",
     "a date on a line that claims no re-probe -- a note about the board is not evidence "
     "that someone went and looked at it"),
    ("retirement CONFIRMED 2026-07-07",
     "a real re-probe marker, but dated BEFORE the floor -- that is inherited belief"),
    ("", "no date at all"),
])
def test_a_docstring_that_records_no_completed_reprobe_is_rejected(docstring, why):
    """The holes in the first version of this guard, each one measured against it.

    Every row here PASSED before: `_ISO_DATE.findall` scanned the whole docstring and the
    verdict was a tuple comparison, which accepts impossible dates, future dates and dates
    that have nothing to do with a re-probe.
    """
    today = date.today()
    dates = [d for d in _reprobe_dates(docstring) if _REPROBE_FLOOR <= d <= today]
    assert not dates, f"a docstring carrying {why} was accepted as a completed re-probe"


def test_a_real_reprobe_line_is_still_accepted():
    # The other half: a guard that rejects everything is not a guard. Both shipped
    # phrasings -- marker before the date, and marker after it -- must pass.
    today = date.today()
    for docstring in ("Hired. RETIRED 2026-07-07, retirement CONFIRMED 2026-08-27.",
                      "Re-probed 2026-08-27 on the DOMAIN, not a search path.",
                      "reads 12/12 of them with salary (verified live 2026-08-25)."):
        dates = [d for d in _reprobe_dates(docstring) if _REPROBE_FLOOR <= d <= today]
        assert dates, f"a genuine re-probe line was rejected: {docstring!r}"


@pytest.mark.parametrize("source_id", _disabled_source_ids())
def test_disabled_boards_say_why_next_to_the_flag(source_id):
    """A retirement is a claim about the world and goes stale. Each must carry its reason
    in the module docstring so the next person can re-probe it rather than inherit it."""
    src = _src(source_id)
    assert src.enabled is False
    mod = __import__(f"sluice.ingest.sources.{source_id}", fromlist=["*"])
    # Bounded at BOTH ends. The floor is the discipline; `today` is the other half, because a
    # future date is not a record of something someone did -- `2099-01-01` would otherwise
    # satisfy this for ever, which is the same "a claim nobody has to revisit" failure the
    # whole guard exists to prevent.
    today = date.today()
    dates = [d for d in _reprobe_dates(mod.__doc__) if _REPROBE_FLOOR <= d <= today]
    assert dates, (
        f"{source_id} retirement has not been re-probed: its docstring carries no valid "
        f"calendar date between {_REPROBE_FLOOR.isoformat()} and today on a line naming a "
        f"re-probe ({'/'.join(_REPROBE_MARKERS)}), so the reason there is inherited rather "
        f"than checked"
    )
