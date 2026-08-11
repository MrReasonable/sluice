"""Source-plugin tests.

Two layers:
  * test_all_expected_sources_register / test_source_is_well_formed - run offline,
    now: every plugin imports, self-registers, and its parser tolerates an empty
    payload. This is the guard while golden fixtures are still being captured.
  * test_parser_yields_valid_leads - parametrized over whatever golden fixtures
    exist under tests/fixtures/<id>/raw.json. Empty today (fixtures are captured
    from a live Camofox run, once per source), so it simply lights up per source
    as each fixture lands.
"""
import json
import re
from pathlib import Path

import pytest

from sluice.ingest import sources

FIX = Path(__file__).parent / "fixtures"
_FIXTURE_IDS = sorted(p.parent.name for p in FIX.glob("*/raw.json")) if FIX.exists() else []

# Every plugin we expect registered. Technojobs & Jobsearch are intentionally NOT
# ported (dead domains as of 2026-07-07).
EXPECTED_IDS = {
    "cord", "wttj", "jobserve", "workinstartups", "linkedin", "indeed", "reed",
    "cwjobs", "totaljobs", "google", "bayt", "gulftalent", "hackajob",
    "naukrigulf", "weworkremotely", "remoteok", "hired", "theorg", "eighty_k",
    "bwork", "escape_city", "wellfound",
}


def test_all_expected_sources_register():
    got = {s.id for s in sources.all_sources()}
    missing = EXPECTED_IDS - got
    assert not missing, f"sources failed to register: {sorted(missing)}"


@pytest.mark.parametrize("sid", sorted(EXPECTED_IDS))
def test_source_is_well_formed(sid):
    src = sources.get(sid)
    assert src.id == sid
    assert src.searches(), f"{sid} has no searches"
    # parse is pure and must tolerate an empty payload (both raw shapes).
    assert src.parse({"result": [], "jobs": []}, src.searches()[0]) == []


@pytest.mark.parametrize("sid", _FIXTURE_IDS)
def test_parser_yields_valid_leads(sid):
    raw = json.loads((FIX / sid / "raw.json").read_text())
    src = sources.get(sid)
    leads = src.parse(raw, src.searches()[0])
    # google leads legitimately have no url (dedup by title+company); require url
    # for everyone else.
    assert leads, f"{sid} parsed no leads from its fixture"
    assert all(l.title for l in leads)
    if sid != "google":
        assert all(l.url for l in leads)


# company_from_url (#109): a tier-1, free URL-pattern extractor. The URL SHAPES
# below are verified against a real `job-sluice ingest test-source wellfound
# --raw` capture -- real Wellfound company cards link to a BARE `/company/<slug>`
# with no trailing path (end-of-string boundary), and real job-posting cards link
# to `/jobs/<id>-<title-slug>` with NO `/company/` segment at all, so the abstain
# case below uses that shape rather than the plan's illustrative `/role/r/...`
# search-page URL, which is a different (also-abstaining) shape never seen on an
# actual card link.
#
# The HOST is synthetic, though the extractor's own is necessarily real: a source
# plugin has to name the board it scrapes, but `.coderabbit.yaml` binds tests/**
# to synthetic fixtures, and the captured fixtures already follow that -- LinkedIn's
# tests/fixtures/linkedin/raw.json carries example.com links even though
# sources/linkedin.py hardcodes the real domain. Same for the slug ("example-co").
_SYNTHETIC_HOST = "wellfound.invalid"                    # RFC 2606 reserved TLD
_SYNTHETIC_HOST_RE = r"(?:www\.)?wellfound\.invalid"     # ... shaped like the real one


@pytest.fixture
def wellfound(monkeypatch):
    """WellfoundSource with the REAL `_COMPANY_URL_RE`, host swapped for a synthetic
    one.

    The swap is applied to the shipped pattern STRING rather than to a regex written
    out here, so everything these tests exercise -- the slug character class, the
    boundary lookahead, the optional `www.`, and `company_from_url`'s own
    `.replace("-", " ").title()` -- is the production code. A hand-rolled equivalent
    would prove the copy works and stay green on the day the real one stopped.

    It substitutes `wf._HOST_RE`, the production symbol, so this file never spells
    the board's real domain: renaming or inlining that constant makes the lookup
    raise, and the assert catches the quieter failure where it stops appearing in
    the compiled pattern and the swap silently does nothing.
    """
    from sluice.ingest.sources import wellfound as wf
    real = wf._COMPANY_URL_RE.pattern
    patched = real.replace(wf._HOST_RE, _SYNTHETIC_HOST_RE)
    assert patched != real, \
        "no-op host swap: these tests would silently run against the real domain"
    monkeypatch.setattr(wf, "_COMPANY_URL_RE", re.compile(patched))
    return sources.get("wellfound")


def test_wellfound_company_from_url_confident_match(wellfound):
    assert wellfound.company_from_url(
        f"https://{_SYNTHETIC_HOST}/company/example-co") == "Example Co"


def test_wellfound_company_from_url_keeps_a_numeric_disambiguation_suffix(wellfound):
    # A real observed Wellfound shape: two companies wanting the same slug get a
    # numeric suffix. Asserted against what `.replace("-", " ").title()` MEASURABLY
    # does with a digit rather than what it looks like it should -- `str.title()`
    # treats a digit as a word character, so it passes through unchanged here but
    # would upper-case a letter FOLLOWING one ("example-co-2b" -> "Example Co 2B").
    # Naming the boundary matters because the suffix is part of the identity: an
    # extractor that dropped it would resolve two different companies to one name.
    assert wellfound.company_from_url(
        f"https://{_SYNTHETIC_HOST}/company/example-co-1") == "Example Co 1"


def test_wellfound_company_from_url_accepts_the_www_host(wellfound):
    # `(?:www\.)?` is part of the swapped-in host group, so this doubles as the pin
    # that the substitution preserved the real pattern's optional-www branch rather
    # than flattening it.
    assert wellfound.company_from_url(
        f"https://www.{_SYNTHETIC_HOST}/company/example-co") == "Example Co"


def test_wellfound_company_from_url_abstains_without_a_company_segment(wellfound):
    assert wellfound.company_from_url(
        f"https://{_SYNTHETIC_HOST}/jobs/2837465-staff-engineer") is None


def test_wellfound_company_from_url_abstains_on_an_empty_url(wellfound):
    assert wellfound.company_from_url("") is None


@pytest.mark.parametrize("suffix", [".example", "_llc", "%20ltd"])
def test_wellfound_company_from_url_abstains_when_the_slug_does_not_end_at_a_boundary(
        wellfound, suffix):
    # `[a-z0-9-]+` stops at the first character it cannot consume, and without the
    # trailing `(?=[/?#]|$)` the match SUCCEEDS there regardless of what follows --
    # so a path segment that merely STARTS like a slug yielded a confident
    # "Example Co" for a URL that names something else entirely. Tier 1 writes its
    # answer to the lead as proven, so a shape the real capture never showed must
    # abstain and leave the work to tier 2, not guess where the slug ended.
    assert wellfound.company_from_url(
        f"https://{_SYNTHETIC_HOST}/company/example-co{suffix}") is None


@pytest.mark.parametrize("boundary", ["/", "/jobs/2837465-staff-engineer", "?ref=x", "#top"])
def test_wellfound_company_from_url_matches_at_every_real_boundary(wellfound, boundary):
    # The paired positive control: the assertions above must be rejecting the
    # non-boundary suffix specifically, not everything that follows a slug.
    assert wellfound.company_from_url(
        f"https://{_SYNTHETIC_HOST}/company/example-co{boundary}") == "Example Co"


def test_the_wellfound_extractor_under_test_is_the_shipped_one(wellfound):
    """The fixture's own control: `company_from_url` must be reading the attribute the
    fixture patched. Every other way this could break is already loud -- a renamed or
    inlined constant makes `monkeypatch.setattr` raise, a no-op host swap trips the
    fixture's own assert, and a regex read from somewhere else fails to match the
    synthetic URLs above -- so this only pins the last quiet one.
    """
    from sluice.ingest.sources import wellfound as wf
    assert _SYNTHETIC_HOST.replace(".", r"\.") in wf._COMPANY_URL_RE.pattern
    # Deliberately NOT asserted by running the extractor over a URL at the board's
    # real domain: keeping that out of this file's fixtures is the point of the swap.
