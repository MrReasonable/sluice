"""Guards for the 2026-08-25 board-drift sweep.

Every failure these pin is the same shape: a board moved, the scraper kept asking
the old question, got nothing, and the nothing was read as the board being dead.
These are cheap string assertions on purpose -- the extractors themselves need a
live DOM, so what is pinned here is the part that silently rotted: which host we
ask, which selector we ask for, and whether a board is switched on at all.
"""
import re
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from sluice.ingest import base as sources_base
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


# The sweep that established the re-probe discipline. A retirement dated on or after this has
# been checked against the live world at least once; anything older is inherited belief. A
# FLOOR rather than a pinned string, so a later re-probe supersedes an earlier one without the
# test being edited to match.
_REPROBE_FLOOR = date(2026, 8, 25)


def _reprobe_date(src):
    """The source's DECLARED re-probe date, or None if absent/unparseable.

    Reads the `reprobed` field (#207 ask 4 -- the rule belongs in the source contract). This
    replaced a scan of the module docstring, and the reason is worth keeping: deciding from
    PROSE whether a line asserts that a check happened is a natural-language question, and
    every tightening of it acquired a new hole. Measured, in order, against each version that
    shipped before the next:

      - a bare tuple comparison accepted `2026-99-99`, because (2026, 99, 99) sorts above the
        floor, and `2099-01-01`, which would have satisfied it for ever
      - restricting to lines carrying a marker word accepted `unverified`, since it CONTAINS
        `verified` -- the negation of the rule satisfying the rule
      - word-bounding the markers still accepted `not verified`, `never confirmed`, `no longer
        verified` and `yet to be re-probed`

    That set is unbounded. A declared date cannot be negated: it is either a date or it is not.
    `sluice/ingest/base.py`'s `validate_reprobed` rejects a malformed one at construction, so
    by the time this reads the field the only open questions are presence and recency.
    """
    raw = getattr(src, "reprobed", "") or ""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def test_the_disabled_roster_is_not_vacuous():
    # `@parametrize` over an empty list runs ZERO tests and reports green, so a registry
    # that failed to load would silently certify nothing -- the "a sweep that discovers
    # nothing passes" shape. Assert the SCOPE before trusting any verdict over it.
    assert sources.all_sources(), "registry empty -- the re-probe guard would certify nothing"
    assert _disabled_source_ids(), \
        "no disabled sources found -- the re-probe guard has nothing to check"


@pytest.mark.parametrize("value,why", [
    ("2026-99-99", "an impossible calendar date -- an earlier tuple comparison ranked "
                   "(2026,99,99) above the floor, so a typo satisfied the guard"),
    ("2026-07-07", "a real date, but BEFORE the floor -- that is inherited belief"),
    ("", "no date at all"),
    ("unverified", "prose where a date belongs"),
    ("not verified 2026-08-26", "a NEGATED claim -- the whole reason this reads a field "
                                "rather than sniffing the docstring for marker words"),
])
def test_a_source_that_records_no_completed_reprobe_is_rejected(value, why):
    """Every row here defeated some earlier version of this guard.

    The last two are the point: as prose they each passed a marker-based check, and as a
    `reprobed` value they are simply not dates. That is the whole argument for the field.
    """
    src = SimpleNamespace(reprobed=value)
    parsed = _reprobe_date(src)
    assert parsed is None or not (_REPROBE_FLOOR <= parsed <= date.today()), (
        f"a source declaring {value!r} was accepted as re-probed, but it is {why}")


def test_a_real_declared_reprobe_is_accepted():
    # The other half: a guard that rejects everything is not a guard.
    assert _reprobe_date(SimpleNamespace(reprobed="2026-08-27")) == date(2026, 8, 27)


@pytest.mark.parametrize("source_id", _disabled_source_ids())
def test_disabled_boards_say_why_next_to_the_flag(source_id):
    """A retirement is a claim about the world and goes stale. Each must carry its reason
    in the module docstring so the next person can re-probe it rather than inherit it."""
    src = _src(source_id)
    assert src.enabled is False
    # Bounded at BOTH ends. The floor is the discipline; `today` is the other half, because a
    # future date records nothing anyone did -- `2099-01-01` would otherwise satisfy this for
    # ever, the same "a claim nobody has to revisit" failure the guard exists to prevent.
    probed = _reprobe_date(src)
    assert probed is not None and _REPROBE_FLOOR <= probed <= date.today(), (
        f"{source_id} declares reprobed={getattr(src, 'reprobed', '')!r}, which is not a date "
        f"between {_REPROBE_FLOOR.isoformat()} and today -- so its retirement is inherited "
        f"belief rather than something someone checked"
    )
    # The WHEN is the field; the WHY is still prose, and a date with no reason beside it
    # tells the next person nothing about what to look for.
    mod = __import__(f"sluice.ingest.sources.{source_id}", fromlist=["*"])
    assert (mod.__doc__ or "").strip(), (
        f"{source_id} carries a re-probe date but no docstring saying what was found")


def test_a_malformed_reprobed_date_is_refused_at_construction():
    """FAIL LOUDLY AT CONSTRUCTION, the posture `validate_posting_paths` already sets.

    This is the half of #207 ask 4 that lives in the source CONTRACT rather than in this
    file: a retirement whose recorded check date is `2026-99-99` reads as evidence to a
    human and parses as nothing, so it must not be constructible. Whether a disabled source
    must carry one at all, and whether the date is recent enough to still be believed, stay
    here -- those are policy, and the floor and `today` live in this file.
    """
    for bad in ("2026-99-99", "27/08/2026", "yesterday", "not verified 2026-08-26"):
        with pytest.raises(ValueError, match="reprobed"):
            sources_base.BrowserListSource(
                id="demo", searches_spec=[("A", "https://example.invalid/jobs")],
                extractor_js="(()=>[])()", reprobed=bad)
    # "" is the abstaining default and must stay constructible -- every ENABLED source has it.
    assert sources_base.BrowserListSource(
        id="demo", searches_spec=[("A", "https://example.invalid/jobs")],
        extractor_js="(()=>[])()").reprobed == ""


def test_wttj_posting_regex_is_not_anchored_past_the_locale_segment():
    """The bug this cost, caught on the live board rather than reasoned about.

    WTTJ's posting links are `/en-GB/companies/<co>/jobs/<slug>`. A regex anchored at the path
    start with a lowercase locale class -- `^/[a-z-]+/companies/` -- cannot match `en-GB`,
    because of the uppercase `GB`. It matched ZERO of ten real cards and returned an empty
    page, which is indistinguishable from a board with no results and is the exact
    silent-empty shape this file exists for.

    Static, like its jobserve sibling above: this repo has no JS execution harness and
    `sluice/` is stdlib-only, so the real verification is a live run. A string check still
    catches the specific regression.
    """
    js = _src("wttj").extractor_js
    assert "/companies/" in js, "wttj must match postings by their /companies/<co>/jobs/ shape"
    # COMMENTS STRIPPED FIRST. The extractor's own comment explains this bug and therefore
    # contains the forbidden pattern verbatim, so a check over the raw text is satisfied by
    # the prose describing the defect rather than by the code avoiding it -- the same way a
    # bare-substring `degraded` check in tests/test_health_wrong_page.py was once satisfied by
    # the comment above the marker. Grep the CODE, not the explanation of the code.
    code = re.sub(r"//[^\n]*", "", js)
    assert not re.search(r"\^\\?/\[a-z", code), (
        "wttj's posting regex is anchored at the path start with a lowercase-only class -- "
        "the locale segment is `en-GB` and this matches nothing")


def test_wttj_reads_the_list_view_not_the_retired_carousel():
    """wttj moved off the Otta carousel (2026-08-28). Both surfaces are still live and spell
    the same posting with different URLs, so scraping the old one alongside the new would
    produce duplicate leads that `_norm_url` cannot dedup."""
    src = _src("wttj")
    assert not hasattr(src, "advance_selector"), (
        "wttj is a list source now; an advance selector means the carousel is back")
    for _, url in src.searches_spec:
        host = (urlparse(url).hostname or "").lower()
        assert host == "www.welcometothejungle.com", (
            f"wttj must read the list view on www, got {host!r}")
