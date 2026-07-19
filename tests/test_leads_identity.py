"""same_opportunity — the pure identity verdict for #5's write-path split.

Locations here are synthetic tokens (aaa/bbb/...), never real places: they exercise
the overlap / disjoint / empty branches of _compare_locations directly.
"""
from sluice.core.leads import DIFFERENT, SAME, UNKNOWN, Lead, same_opportunity


def _lead(**kw):
    base = dict(source="b", search="s", title="Analyst", company="Acme",
                url="https://a/1", location="aaa")
    base.update(kw)
    return Lead(**base)


def test_matching_nonempty_urls_are_proof_of_same():
    # A matching non-empty url proves SAME even when the locations disagree.
    fm = {"url": "https://a/1?utm=x", "location": "bbb"}
    assert same_opportunity(fm, _lead(url="https://a/1?utm=x", location="aaa"), frozenset()) == SAME


def test_empty_urls_are_never_proof_the_google_trap():
    # Two url-less leads (google carries url:"") must NOT match on empty urls -> defer to location.
    fm = {"url": "", "location": "bbb"}
    assert same_opportunity(fm, _lead(url="", location="aaa"), frozenset()) == DIFFERENT


def test_defers_to_location_when_urls_do_not_prove():
    # Different urls are not proof, so the verdict is the location comparison.
    fm_overlap = {"url": "https://a/2", "location": "aaa bbb"}
    assert same_opportunity(fm_overlap, _lead(url="https://a/1", location="aaa ccc"), frozenset()) == SAME
    fm_disjoint = {"url": "https://a/2", "location": "bbb"}
    assert same_opportunity(fm_disjoint, _lead(url="https://a/1", location="aaa"), frozenset()) == DIFFERENT


def test_absent_location_is_unknown_never_splits():
    fm = {"url": "https://a/2", "location": ""}
    assert same_opportunity(fm, _lead(url="https://a/1", location="aaa"), frozenset()) == UNKNOWN


def test_noise_word_flips_a_verdict():
    # aaa vs bbb is DIFFERENT; adding 'aaa' to noise empties one side -> UNKNOWN (merge).
    fm = {"url": "", "location": "aaa"}
    assert same_opportunity(fm, _lead(url="", location="bbb"), frozenset()) == DIFFERENT
    assert same_opportunity(fm, _lead(url="", location="bbb"), frozenset({"aaa"})) == UNKNOWN
