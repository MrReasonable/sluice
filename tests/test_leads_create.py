"""Sluice.create_lead(): validation, outcome passthrough (#131 decisions 9-12)."""
import os
import pathlib

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault


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
    try:
        app.create_lead(title='Bad"Title', company="Example Ltd",
                        url="https://example.invalid/1")
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "title" in str(e)


def test_rejects_an_embedded_newline_by_name(tmp_path):
    app = _app(tmp_path)
    try:
        app.create_lead(title="Example Role", company="Bad\nCompany",
                        url="https://example.invalid/1")
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "company" in str(e)


def test_rejects_a_non_http_url(tmp_path):
    app = _app(tmp_path)
    try:
        app.create_lead(title="Example Role", company="Example Ltd", url="ftp://x")
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "url" in str(e)


def test_requires_url(tmp_path):
    app = _app(tmp_path)
    try:
        app.create_lead(title="Example Role", company="Example Ltd", url="")
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "url" in str(e)


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
    first = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location="London")
    second = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/2", location="New York")
    third = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/3", location="Berlin")
    for r in (first, second, third):
        assert r.outcome == "created"
    fourth = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/4", location="Paris")
    assert fourth.outcome == "created"
    # The bug's exact failure shape: a stale/wrong slug (any of the three
    # PRIOR notes) or an empty one where disambiguation was actually possible.
    assert fourth.slug not in ("", first.slug, second.slug, third.slug)
    note = next(n for n in Vault(str(tmp_path)).read_leads() if n.slug == fourth.slug)
    assert note.fm.get("location") == "Paris"


def test_url_proven_second_call_resolves_the_first_notes_slug_not_a_coincidental_match(tmp_path):
    """The exact scenario that broke every prior create_lead fix attempt this
    session (location-only narrowing, a flat same_opportunity filter, a two-tier
    url-then-location priority) -- now correct by construction, since create_lead
    just reads Vault.upsert's own answer instead of guessing."""
    app = _app(tmp_path)
    first = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location="London")
    second = app.create_lead(title="Example Role", company="Example Ltd",
                             url="https://example.invalid/2", location="Berlin")
    assert first.slug != second.slug

    third = app.create_lead(title="Example Role", company="Example Ltd",
                            url="https://example.invalid/1", location="Berlin")
    assert third.slug == first.slug, (
        f"expected the url-proven FIRST note's slug {first.slug!r}, got {third.slug!r}")
