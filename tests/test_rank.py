"""core/rank.py (#195): the one JD keyword tokeniser and ranker, shared by cv and prep.

Roles come from the seeded `titles` fixture, never a literal (the spec's Testing rules), so
the role half of `jd_keywords` is checked against the fixture title's own `[a-z]{4,}` words
rather than against a hand-picked title whose words the assertion already knows.
"""
import re

from sluice.core.rank import jd_keywords, matched_keywords, rank

_JD = "Build the DATA pipeline, own data."


def _role_words(role: str) -> list:
    return sorted(set(re.findall(r"[a-z]{4,}", role.lower())))


def test_jd_keywords_are_sorted_unique_lowercased_four_letter_words():
    # `own` and `the` are under four letters; `DATA`/`data` collapse to one lowercased word.
    assert jd_keywords("", _JD) == ["build", "data", "pipeline"]


def test_jd_keywords_join_the_role_words_to_the_jd_words(titles):
    role = titles[0][0]
    assert _role_words(role), f"precondition: fixture title {role!r} carries a 4+ letter word"
    assert jd_keywords(role, None) == _role_words(role)
    assert jd_keywords(role, _JD) == sorted(set(_role_words(role)) | {"build", "data", "pipeline"})


def test_jd_keywords_accept_a_missing_jd(titles):
    role = titles[0][0]
    assert jd_keywords(role, None) == jd_keywords(role, "") == _role_words(role)
    assert jd_keywords("", None) == []


def test_matched_keywords_show_every_keyword_sharing_a_stem():
    """`documenting` and `documented` stem alike, so BOTH show: a brief lists the JD's own
    words, and dropping one of a stem's spellings would hide a word the posting used."""
    entry = {"best_for": "documentation", "category": "", "title": "x"}
    assert matched_keywords(entry, ["documenting", "documented", "unrelated"]) == [
        "documented", "documenting"]


def test_rank_counts_a_shared_stem_once():
    """The shape where the two counting rules DISAGREE (Amendment A3): per stem, A scores 1
    and B scores 2, so B leads; per keyword, A's two spellings would score 2 and tie with B,
    and the stable sort would leave A first."""
    a = {"best_for": "documentation", "category": "", "title": "a"}
    b = {"best_for": "pipeline deploy", "category": "", "title": "b"}
    kws = ["documenting", "documented", "pipeline", "deploy"]
    assert rank([a, b], kws)[0] is b


def test_haystack_parity_across_the_three_fields():
    """`rank` and `matched_keywords` read ONE haystack. Each entry matches the JD in exactly
    one field, a different one each, by one, two and three words; each entry's matched list
    is its own words, and the order is three, two, one.

    Seeded in the order one, two, three, so a ranker scoring everything zero would leave
    the one-word entry in front rather than handing back the right order by accident."""
    kws = jd_keywords("", "We need latency budgets, schema review, monitoring and a pipeline.")
    one = {"best_for": "latency", "category": "", "title": "first"}
    two = {"best_for": "", "category": "schema review", "title": "second"}
    three = {"best_for": "", "category": "", "title": "monitoring pipeline budgets"}
    for word in ("latency", "schema", "review", "monitoring", "pipeline", "budgets"):
        assert word in kws, f"precondition: {word!r} is a JD keyword"

    assert matched_keywords(one, kws) == ["latency"]
    assert matched_keywords(two, kws) == ["review", "schema"]
    assert matched_keywords(three, kws) == ["budgets", "monitoring", "pipeline"]
    assert rank([one, two, three], kws) == [three, two, one]


def test_rank_orders_and_never_excludes():
    a = {"best_for": "", "category": "", "title": "a"}
    b = {"best_for": "pipeline", "category": "", "title": "b"}
    assert rank([a, b], ["pipeline"]) == [b, a]


def test_the_cv_names_still_resolve_to_the_core_functions():
    """`from sluice.cv.bundle import rank` and `cv/engine.py`'s `_jd_keywords` binding keep
    working after the move, and are the SAME functions, not second copies that could drift."""
    from sluice.core import rank as core_rank
    from sluice.cv import bundle, engine

    assert bundle.rank is core_rank.rank
    assert engine._jd_keywords is core_rank.jd_keywords
