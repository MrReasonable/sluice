"""Sluice.create_lead(): validation, outcome passthrough (#131 decisions 9-12)."""
import os
import pathlib

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _app(tmp_path):
    return Sluice(Config(), store=Vault(str(tmp_path)))


def test_reports_the_resolvable_slug(tmp_path):
    result = _app(tmp_path).create_lead(
        title="Example Role", company="Example Ltd", url="https://example.invalid/1")
    assert result.outcome == "created"
    assert result.slug == "Example Ltd - Example Role"


def test_collision_reports_merged_and_does_not_overwrite_url(tmp_path):
    """The collision trap (decision 10): two leads sharing company+title (even with
    DIFFERENT urls -- url is not part of vault identity) resolve to the SAME note.

    Outcome is "merged", not "updated": with no location on either side and a
    non-matching url, `same_opportunity` (sluice/core/leads.py) returns UNKNOWN
    (neither url-proof nor a location match), which `Vault._reconcile` maps to
    "merge" -- verified directly against `Vault.upsert`, independent of
    `create_lead`. Both outcomes are pass-through, never-clobber last_seen bumps,
    so the collision trap itself (same slug, incoming url NOT recorded) is
    identical either way -- this test pins the exact string `create_lead` must
    forward verbatim (decision 10), not invent its own."""
    app = _app(tmp_path)
    first = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1")
    second = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/DIFFERENT")
    assert first.outcome == "created"
    assert second.outcome == "merged"
    assert second.slug == first.slug
    note = Vault(str(tmp_path)).read_leads()[0]
    assert note.fm["url"] == "https://example.invalid/1"   # never-clobber: unchanged


def test_rejects_an_unsafe_field_by_name(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(ValueError, match="title"):
        app.create_lead(title='Bad"Title', company="Example Ltd",
                        url="https://example.invalid/1")


def test_rejects_an_embedded_newline_by_name(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(ValueError, match="company"):
        app.create_lead(title="Example Role", company="Bad\nCompany",
                        url="https://example.invalid/1")


def test_rejects_a_non_http_url(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(ValueError, match="url"):
        app.create_lead(title="Example Role", company="Example Ltd", url="ftp://x")


@pytest.mark.parametrize("bad_url", ["httpx://internal", "httpfoo", "http", "http:/x"])
def test_rejects_urls_that_merely_start_with_the_substring_http(tmp_path, bad_url):
    # A bare `.startswith("http")` (the shape this check used to share with
    # apply/select.eligibility) accepts all four of these. is_http_url requires the
    # exact "http://"/"https://" scheme instead (round-2 review finding).
    app = _app(tmp_path)
    with pytest.raises(ValueError, match="url"):
        app.create_lead(title="Example Role", company="Example Ltd", url=bad_url)


def test_requires_url(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(ValueError, match="url"):
        app.create_lead(title="Example Role", company="Example Ltd", url="")


def test_frontmatter_carries_no_search_key(tmp_path):
    app = _app(tmp_path)
    app.create_lead(title="Example Role", company="Example Ltd",
                    url="https://example.invalid/1")
    note = Vault(str(tmp_path)).read_leads()[0]
    text = pathlib.Path(note.ref).read_text()
    assert "search:" not in text


def test_does_not_touch_seen_db(tmp_path):
    app = _app(tmp_path)
    app.create_lead(title="Example Role", company="Example Ltd",
                    url="https://example.invalid/1")
    assert not os.path.exists(tmp_path / "seen.db")
    assert SeenDb(str(tmp_path / "seen.db")).load() == set()


def test_allows_a_blank_location(tmp_path):
    result = _app(tmp_path).create_lead(
        title="Example Role", company="Example Ltd", url="https://example.invalid/1",
        location="")
    assert result.outcome == "created"


def test_refused_returns_no_slug(tmp_path):
    # Both company and role blank -> upsert's own blank-identity gate refuses.
    result = _app(tmp_path).create_lead(title=" ", company=" ",
                                        url="https://example.invalid/1")
    assert result.outcome == "refused"
    assert result.slug == ""


def test_a_fourth_call_at_a_fourth_distinct_location_resolves_its_own_slug_never_a_stale_one(tmp_path):
    """Important #1 of the final whole-branch review: company+title alone can
    resolve to MULTIPLE notes -- reachable via a run of proven-different
    locations, each advancing past the last per Vault._reconcile's "advance"
    verdict (a disjoint, non-remote location pair is proof of a DIFFERENT
    posting, so each call below creates a genuinely separate note rather than
    merging). Before the fix, `notes[0]` over an unfiltered, not-recency-ordered
    read_leads() could return a note THIS call never touched -- reproduced
    directly: a 3rd create_lead call returned the 2nd note's slug, not the 3rd
    (freshly created) one. None of these four calls reuses an earlier url, so
    no note is url-proven -- this exercises `Vault.upsert`'s own candidate
    walk with no url match anywhere in play. See
    test_url_proven_second_call_resolves_the_first_notes_slug_not_a_coincidental_match
    below for the case where a url DOES prove the match (#131 post-final-review:
    create_lead now just reads Vault.upsert's own answer instead of guessing,
    so both cases are correct by construction rather than by two separate
    guessing tiers)."""
    app = _app(tmp_path)
    # LOCATIONS has 3 token-disjoint, non-remote entries; this test needs a 4th,
    # so "Delta" extends the same synthetic naming scheme rather than a real city.
    first = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location=LOCATIONS[0])
    second = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/2", location=LOCATIONS[1])
    third = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/3", location=LOCATIONS[2])
    for r in (first, second, third):
        assert r.outcome == "created"
    fourth = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/4", location="Delta")
    assert fourth.outcome == "created"
    # The bug's exact failure shape: a stale/wrong slug (any of the three
    # PRIOR notes) or an empty one where disambiguation was actually possible.
    assert fourth.slug not in ("", first.slug, second.slug, third.slug)
    note = next(n for n in Vault(str(tmp_path)).read_leads() if n.slug == fourth.slug)
    assert note.fm.get("location") == "Delta"


def test_url_proven_second_call_resolves_the_first_notes_slug_not_a_coincidental_match(tmp_path):
    """The original reproduction that broke TWO of the three prior create_lead fix
    attempts this session (location-only narrowing, a flat same_opportunity filter)
    -- now correct by construction, since create_lead just reads Vault.upsert's own
    answer instead of guessing. NOT the scenario that distinguishes the real fix
    from the THIRD prior attempt (a two-tier url-then-location priority, #131's
    immediately-preceding commit): that strategy gets this exact case right too,
    because the third call's url matches exactly one note here. See
    test_a_second_notes_url_does_not_steal_a_write_that_actually_landed_on_the_first
    below for the scenario that does distinguish it."""
    app = _app(tmp_path)
    first = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location=LOCATIONS[0])
    second = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/2", location=LOCATIONS[1])
    assert first.slug != second.slug

    third = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location=LOCATIONS[1])
    assert third.slug == first.slug, (
        f"expected the url-proven FIRST note's slug {first.slug!r}, got {third.slug!r}")
    # never-clobber: the untouched SECOND note's own fields must survive intact --
    # restores the coverage the deleted two-tier-mechanism test had. The
    # conformance suite's sibling test already proves never-clobber via
    # last_seen at the Vault.upsert layer; this re-proves it here, at the
    # create_lead FACADE specifically, since create_lead's own pass-through
    # could in principle diverge from the store even though today it does not.
    second_note = next(n for n in Vault(str(tmp_path)).read_leads() if n.slug == second.slug)
    assert second_note.fm.get("location") == LOCATIONS[1]
    assert second_note.fm.get("url") == "https://example.invalid/2"


def test_a_second_notes_url_does_not_steal_a_write_that_actually_landed_on_the_first(tmp_path):
    """#131 round-2 review, Important #3: distinguishes the real fix from the THIRD
    prior create_lead attempt (a two-tier url-then-location priority applied to a
    post-hoc `read_leads()` filter, #131's immediately-preceding commit), which the
    scenario above does not. Swap WHICH field matches which note: the incoming
    lead's url matches the SECOND note, but its location coincidentally matches the
    FIRST note, which sits at the bare candidate name the real candidate walk
    checks FIRST -- so the actual write lands on the FIRST note via a
    location-only verdict, and the walk never even reaches the SECOND note. A
    url-then-location priority computed afterward instead finds the SECOND note's
    url a match FIRST (before ever considering location) and reports ITS slug for a
    write that actually touched the FIRST -- verified directly against a faithful
    reimplementation of that exact prior commit's logic before writing this test."""
    app = _app(tmp_path)
    first = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location=LOCATIONS[0])
    second = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/2", location=LOCATIONS[1])
    assert first.slug != second.slug

    # The SECOND note's own url, but the FIRST note's own location.
    third = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/2", location=LOCATIONS[0])
    assert third.slug == first.slug, (
        f"the write actually landed on the FIRST note (matched by location on the "
        f"bare candidate) but create_lead reported {third.slug!r} -- a url-then-"
        f"location strategy applied after the fact would wrongly report the SECOND "
        f"note's slug {second.slug!r} here")
    # never-clobber: the untouched SECOND note's own fields survive intact.
    second_note = next(n for n in Vault(str(tmp_path)).read_leads() if n.slug == second.slug)
    assert second_note.fm.get("location") == LOCATIONS[1]
    assert second_note.fm.get("url") == "https://example.invalid/2"
