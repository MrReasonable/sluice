"""The coarse ingest gate. Its lists are the user's, so the suite supplies its own
synthetic ones rather than asserting on anybody's real role preferences."""
from types import SimpleNamespace

from sluice.core.relevance import is_relevant


def _cfg(keep=(), drop=()):
    return SimpleNamespace(relevance_keep=list(keep), relevance_drop=list(drop))


def test_unconfigured_gate_is_a_pass_through(titles):
    # The important property: shipping no lists must never silently filter on
    # somebody else's taste. Everything survives an unconfigured gate.
    accept, reject = titles
    assert is_relevant(accept[0].title(), _cfg())
    assert is_relevant(reject[0].title(), _cfg())
    assert is_relevant("anything at all", None)


def test_keep_list_admits_only_matching_titles(titles):
    accept, reject = titles
    cfg = _cfg(keep=accept)
    assert is_relevant(accept[0].title(), cfg)
    assert not is_relevant(reject[0].title(), cfg)


def test_drop_list_removes_matching_titles(titles):
    accept, reject = titles
    cfg = _cfg(keep=accept, drop=reject)
    assert is_relevant(accept[0].title(), cfg)
    assert not is_relevant(reject[0].title(), cfg)


def test_drop_wins_over_keep_when_a_title_matches_both(titles):
    accept, _ = titles
    cfg = _cfg(keep=accept, drop=[accept[0]])
    assert not is_relevant(accept[0].title(), cfg)


def test_empty_title_is_not_a_crash():
    assert is_relevant("", _cfg(keep=["x"])) is False
