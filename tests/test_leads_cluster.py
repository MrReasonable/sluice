"""cluster_duplicates: complete-linkage clustering of duplicate lead notes (#23 §1).

Fixtures are synthetic. Titles are constructed from a faker base plus synthetic
tokens (never hardcoded role strings); companies use a faker base + a synthetic
suffix; LOCATIONS are conftest's Alfa/Bravo/Charlie placeholders.
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
    # Alfa ~ blank ~ Bravo: connected via blank, but Alfa/Bravo DIFFERENT -> not a
    # clique -> no cluster (never bridges two different cities). arc-r2-001.
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
