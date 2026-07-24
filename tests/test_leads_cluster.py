"""cluster_duplicates: complete-linkage clustering of duplicate lead notes (#23 §1).

Fixtures are synthetic. `_note`'s `role`/`company` defaults are deliberately
generic, non-preference placeholders ("engineer", "foo") chosen to exercise
specific clustering token-set relationships (noise-word drift, prefix
containment, seniority distinction) that a faker-generated string cannot
reliably produce -- they are not faker-derived. LOCATIONS are conftest's
Alfa/Bravo/Charlie placeholders.
"""
from types import SimpleNamespace

from sluice.core.leads import cluster_duplicates
from tests.conftest import LOCATIONS


def _note(slug, *, company="foo", role="engineer", location=""):
    return SimpleNamespace(slug=slug,
                           fm={"company": company, "role": role, "location": location})


def _slugs(clusters):
    return sorted(sorted(n.slug for n in c) for c in clusters)


def test_drifted_title_clusters_only_via_configured_noise():
    a = _note("a", role="engineer remote")
    b = _note("b", role="engineer")
    assert cluster_duplicates([a, b]) == []                       # no noise -> not clustered
    assert _slugs(cluster_duplicates([a, b], title_noise=["remote"])) == [["a", "b"]]


def test_distinct_seniority_never_clusters():
    a = _note("a", role="senior engineer")
    b = _note("b", role="engineer")
    assert cluster_duplicates([a, b], title_noise=["remote", "hybrid"]) == []


def test_prefix_company_never_clusters():
    a = _note("a", company="foo")
    b = _note("b", company="foo industries")
    assert cluster_duplicates([a, b]) == []


def test_same_role_different_city_never_clusters():
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location=LOCATIONS[1])
    assert cluster_duplicates([a, b]) == []


def test_blank_location_clusters_both_orders():
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location="")
    assert _slugs(cluster_duplicates([a, b])) == [["a", "b"]]
    assert _slugs(cluster_duplicates([b, a])) == [["a", "b"]]


def test_positive_two_clique():
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location=LOCATIONS[0])
    assert _slugs(cluster_duplicates([a, b])) == [["a", "b"]]


def test_blank_bridge_yields_no_cluster():
    # Alfa ~ blank ~ Bravo: connected via blank, but Alfa/Bravo DIFFERENT. Each of
    # {Alfa} and {Bravo} is its own size-1 seed (two seeds -> ambiguous), so the
    # blank stays unclustered and neither singleton seed reaches size >= 2 -> no
    # cluster at all (never bridges two different cities). arc-r2-001.
    a = _note("a", location=LOCATIONS[0])
    b = _note("b", location="")
    c = _note("c", location=LOCATIONS[1])
    assert cluster_duplicates([a, b, c]) == []


def test_two_disjoint_cliques_in_one_group():
    a = _note("a", location=LOCATIONS[0])
    a2 = _note("a2", location=LOCATIONS[0])
    b = _note("b", location=LOCATIONS[1])
    b2 = _note("b2", location=LOCATIONS[1])
    assert _slugs(cluster_duplicates([a, a2, b, b2])) == [["a", "a2"], ["b", "b2"]]


def test_subclique_retained_in_four_note_group():
    # Alfa ~ Alfa2 (SAME, a valid size-2 seed) ~ blank ~ Bravo (DIFFERENT from the
    # Alfa pair, its own size-1 seed). OLD behaviour treated {a, a2, blank, b} as
    # ONE connected component and discarded it wholesale because the whole thing
    # wasn't a clique (a~b is DIFFERENT) -- == []. NEW behaviour seeds per KNOWN
    # clique first: {a, a2} and {b} are two SEPARATE seeds, so >= 2 seeds exist and
    # the blank -- compatible with both -- is left unclustered rather than guessed
    # into either. Only the Alfa pair (the one seed that reaches size >= 2) is
    # emitted; the recall this fix restores.
    a = _note("a", location=LOCATIONS[0])
    a2 = _note("a2", location=LOCATIONS[0])
    blank = _note("blank", location="")
    b = _note("b", location=LOCATIONS[1])
    assert _slugs(cluster_duplicates([a, a2, blank, b])) == [["a", "a2"]]
    assert _slugs(cluster_duplicates([b, blank, a2, a])) == [["a", "a2"]]        # order-independent
    # determinism: same input, same output
    assert cluster_duplicates([a, a2, blank, b]) == cluster_duplicates([a, a2, blank, b])


def test_subclique_retained_despite_ambiguous_blank():
    # CodeRabbit's example: {A1,A2 at Alfa} + blank + {B1,B2 at Bravo}. Both cities
    # are size-2 seeds, so the blank is ambiguous between them and stays out -- but
    # BOTH real duplicate pairs survive. The recall gap this fix closes: the old
    # whole-component-must-be-a-clique rule discarded this entire group of five.
    a1 = _note("a1", location=LOCATIONS[0])
    a2 = _note("a2", location=LOCATIONS[0])
    blank = _note("blank", location="")
    b1 = _note("b1", location=LOCATIONS[1])
    b2 = _note("b2", location=LOCATIONS[1])
    assert _slugs(cluster_duplicates([a1, a2, blank, b1, b2])) == [["a1", "a2"], ["b1", "b2"]]
    assert _slugs(cluster_duplicates([b2, b1, blank, a2, a1])) == [["a1", "a2"], ["b1", "b2"]]
    # determinism: same input, same output
    result = cluster_duplicates([a1, a2, blank, b1, b2])
    assert result == cluster_duplicates([a1, a2, blank, b1, b2]) == result


def test_blank_attaches_to_sole_seed():
    # Exactly one KNOWN seed {a, a2}: unambiguous, the blank has nowhere else it
    # could belong, so it joins the seed.
    a = _note("a", location=LOCATIONS[0])
    a2 = _note("a2", location=LOCATIONS[0])
    blank = _note("blank", location="")
    assert _slugs(cluster_duplicates([a, a2, blank])) == [["a", "a2", "blank"]]
    assert _slugs(cluster_duplicates([blank, a2, a])) == [["a", "a2", "blank"]]


def test_all_blank_group_clusters():
    # No KNOWN member at all: two blanks carry no DIFFERENT evidence against each
    # other either, so they form their own clique.
    a = _note("a", location="")
    b = _note("b", location="")
    assert _slugs(cluster_duplicates([a, b])) == [["a", "b"]]
    assert _slugs(cluster_duplicates([b, a])) == [["a", "b"]]


def test_empty_identity_never_clusters():
    # Two notes both missing `company` (or both with a role wholly consumed by
    # configured title-noise) produce EQUAL empty token sets, which would otherwise
    # cluster unrelated notes on zero shared evidence (CodeRabbit round-2, #23).
    a = _note("a", company="")
    b = _note("b", company="")
    assert cluster_duplicates([a, b]) == []

    c = _note("c", role="remote")
    d = _note("d", role="remote")
    assert cluster_duplicates([c, d], title_noise=["remote"]) == []
