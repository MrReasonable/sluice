"""The stemmer is certified against Martin Porter's own published vocabulary rather than
against examples chosen here. A table of cases the author picked certifies nothing."""
import hashlib
import pathlib
import re

import pytest

from sluice.core.stem import stem, stem_all, tokens

_CORPUS = pathlib.Path(__file__).resolve().parent / "data" / "porter_vocabulary.txt"

# What this pin buys, stated exactly: regenerating the expected-stem column FROM `stem()`
# would make the equality test below assert that the code equals itself, and this makes
# that regeneration a VISIBLE act rather than an invisible one inside a 353KB diff. It
# does NOT prevent someone regenerating corpus and digest together -- nothing can; the
# same is true of `_REVIEWED_CORPUS_DIGESTS` in tests/test_fixture_name_neutrality.py,
# the precedent this follows.
_CORPUS_SHA256 = "0cf299cd0cd8a3d95fa782c94f0f292e8da35b6c3f2d18833c8021e4cb79aaff"


def _rows():
    out = []
    for line in _CORPUS.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        word, expected = line.split(" ", 1)
        out.append((word, expected))
    return out


def test_the_corpus_is_the_reference_corpus_unmodified():
    assert hashlib.sha256(_CORPUS.read_bytes()).hexdigest() == _CORPUS_SHA256, (
        "tests/data/porter_vocabulary.txt has changed. It is a VERBATIM third-party "
        "corpus; if you regenerated its stem column from stem(), the equality test "
        "below now certifies that the code equals itself.")


def test_the_corpus_is_present_and_whole():
    """SCOPE, not violations. A corpus that failed to load leaves every assertion over it
    iterating an empty list -- green forever, this repo's `all([])` trap."""
    assert len(_rows()) == 23531


def test_every_corpus_row_is_two_lowercase_words():
    """Structural neutrality on the DATA rows, asserted rather than measured once at
    authoring time. It forecloses an email, URL, absolute path or capitalised identity
    entering tests/ through a data row, with no blocklist to maintain.

    Scoped to data rows deliberately, because `_rows()` skips `#` lines and the header
    legitimately carries a URL. The header is covered by the SHA-256 pin above, not by
    this."""
    bad = [r for r in _rows() if not re.fullmatch(r"[a-z]+ [a-z]*", " ".join(r))]
    assert not bad, f"non-word rows in the corpus: {bad[:5]}"


def test_stem_matches_porters_published_vocabulary():
    wrong = [(w, e, stem(w)) for w, e in _rows() if stem(w) != e]
    assert not wrong, f"{len(wrong)} disagreements with the reference, e.g. {wrong[:5]}"


@pytest.mark.parametrize("a,b", [
    ("documenting", "documentation"), ("documented", "documentation"),
    ("deployments", "deployment"), ("migrated", "migration"),
    ("automating", "automation"), ("testing", "tests"),
])
def test_inflections_of_one_word_share_a_stem(a, b):
    assert stem(a) == stem(b), f"{a!r} and {b!r} must rank the same entry"


@pytest.mark.parametrize("a,b", [
    ("planning", "plant"), ("planning", "plane"), ("commit", "committee"),
    ("management", "mandate"), ("contract", "contrast"), ("release", "relevant"),
    # The two the CURRENT substring match gets wrong: `"java" in "javascript"` is True.
    ("java", "javascript"), ("scala", "scalability"),
])
def test_distinct_words_do_not_share_a_stem(a, b):
    assert stem(a) != stem(b), f"{a!r} and {b!r} must not be conflated"


def test_tokens_splits_on_non_letters_and_lowercases():
    assert tokens("Platform/Docs, 2024 tooling") == ["platform", "docs", "tooling"]


def test_stem_all_is_the_stemmed_token_set():
    assert stem_all("Documenting the deployments") == {stem("documenting"), "the",
                                                       stem("deployments")}
