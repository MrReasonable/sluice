"""cv/terms.py (#194): the unbundled-term check's candidate rule and report.

Spec: docs/superpowers/specs/2026-10-02-unbundled-term-design.md §3.2-§3.4. Every arm has
a token ONLY that arm admits, so deleting the arm turns exactly its row red. Tokens are
invented and Example-shaped throughout.
"""
import pytest

from sluice.cv.bundle import build_bundle, term_vocabulary
from sluice.cv.terms import candidates, unbundled_terms


# ── arms ──────────────────────────────────────────────────────────────────────
def test_arm_i_an_inner_capital_fires_even_at_sentence_start():
    # Sentence-initial, so arm (iii) cannot be what admits it.
    assert candidates("ExampleQuery runs the reports.") == ["ExampleQuery"]


def test_arm_ii_a_trailing_hash_or_plus_fires_on_a_lowercase_token():
    assert candidates("Wrote services in examplelang# daily.") == ["examplelang#"]
    assert candidates("Wrote services in examplelang+ daily.") == ["examplelang+"]


@pytest.mark.parametrize("line", ["sales + support", "item # here"])
def test_arm_ii_a_bare_symbol_with_no_letter_is_not_a_candidate(line):
    # `_WORD_RE` yields a lone `+`/`#` as a token; with no letter it names nothing, and
    # reporting it would ask the composer to remove punctuation.
    assert candidates(line) == []


def test_arm_ii_keeps_a_doubled_trailing_symbol_on_a_name():
    assert candidates("Wrote services in examplelang++ daily.") == ["examplelang++"]


def test_arm_iii_a_leading_capital_fires_mid_sentence():
    assert candidates("Moved reporting onto Examplequery last year.") == ["Examplequery"]


# ── exclusions ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("line", [
    "Moved reporting onto examplev2 last year.",       # digit, lowercase
    "Moved reporting onto Example2 last year.",        # digit, capitalised
    "Cut latency by +15 points.",                      # leading-symbol figure
    "Ranked #1 in the region.",
])
def test_any_digit_excludes_the_token(line):
    assert candidates(line) == []


@pytest.mark.parametrize("line", [
    "Examplequery runs the reports.",                  # first token of the line
    "Shipped it. Examplequery runs the reports.",      # after a full stop
    "Result: Examplequery runs the reports.",          # after a colon
])
def test_a_sentence_initial_capital_alone_is_not_a_candidate(line):
    assert "Examplequery" not in candidates(line)


def test_a_lowercase_token_is_never_a_candidate():
    assert candidates("moved reporting onto examplequery last year.") == []


def test_a_citation_is_stripped_before_tokenising():
    assert candidates("- Shipped the reports [AB1]") == []
    # Position, not digits: unstripped, `AB1` consumes the sentence-initial slot and the
    # following capital then reads as mid-sentence.
    assert candidates("[AB1] Examplequery runs the reports.") == []
    assert candidates("Shipped it. [AB1] Examplequery runs.") == []


def test_a_leading_dot_name_fires():
    assert candidates("Rebuilt the service on .Example last year.") == [".Example"]


# ── report ────────────────────────────────────────────────────────────────────
def test_a_case_folded_vocabulary_match_suppresses():
    assert unbundled_terms([(1, "Moved onto ExampleQuery today.")],
                           frozenset({"examplequery"})) == []


def test_the_plural_fold_strips_exactly_one_s():
    v = frozenset({"examplequery"})
    assert unbundled_terms([(1, "Ran two Examplequerys nightly.")], v) == []
    assert [t for _ln, t, _s in
            unbundled_terms([(1, "Ran two Examplequeryss nightly.")], v)] == ["Examplequeryss"]


def test_an_unbundled_candidate_is_reported_with_its_line_and_snippet():
    line = "Moved reporting onto Examplequery last year."
    assert unbundled_terms([(7, line)], frozenset()) == [(7, "Examplequery", line[:50])]


def test_a_term_is_reported_once_per_line():
    """Review Focus 3."""
    lines = [(1, "Ran Examplequery and then Examplequery again."),
             (2, "Kept Examplequery running.")]
    assert [(ln, t) for ln, t, _s in unbundled_terms(lines, frozenset())] == [
        (1, "Examplequery"), (2, "Examplequery")]


def test_hyphen_and_possessive_forms_of_a_bundled_name_are_quiet():
    """Review Focus 2: `_WORD_RE` splits on `-` and `'`, so the stem is what is looked up."""
    v = frozenset({"exampleworks"})
    assert unbundled_terms([(1, "Ran the Exampleworks-based pipeline.")], v) == []
    assert unbundled_terms([(1, "Owned the Exampleworks's roadmap.")], v) == []


def test_an_empty_vocabulary_is_a_valid_shape_and_reports_every_candidate():
    """Review Focus 4: empty is the right SHAPE, so it must not raise."""
    out = unbundled_terms([(1, "Moved onto Examplequery and ExampleBus.")], frozenset())
    assert [t for _ln, t, _s in out] == ["Examplequery", "ExampleBus"]


def test_a_non_ascii_name_in_the_bundle_is_quiet():
    """Review Focus 5: both sides fragment `Exämple` identically."""
    from tests.conftest import SYNTHETIC_LAYOUT
    v = term_vocabulary(build_bundle([{"title": "t", "company": "", "metrics": "",
                                       "body": "Worked at Exämple."}], [], [], {}),
                        SYNTHETIC_LAYOUT)
    assert unbundled_terms([(1, "Rejoined Exämple later.")], v) == []


@pytest.mark.parametrize("bad", ["examplequery", ["examplequery"], {1, 2}])
def test_a_wrongly_shaped_vocabulary_raises_naming_the_type(bad):
    """A `str` would substring-match and silently suppress every finding; a list is the
    wrong container; a set of non-str is the wrong members. Fail loudly, naming the type
    only -- never the value, which is the user's own vocabulary."""
    with pytest.raises(TypeError, match=r"term_vocabulary"):
        unbundled_terms([(1, "Moved onto Examplequery.")], bad)


# ── anti-vacuity, over the suite's real gate-clean CV ─────────────────────────
def test_the_check_scans_real_lines_finds_candidates_and_reports_nothing_bundled():
    """Roster: tests/test_cv_engine.py::CLEAN_REPLY with its own ENTRIES bundle, read as the
    engine reads a reply (cv/document.py::model_lines). All three clauses are needed: (c)
    alone passes on a sweep that scanned nothing (a) or whose rule admits nothing (b)."""
    import json

    from sluice.core.layout import build_slots
    from sluice.cv.document import model_lines
    from sluice.cv.reply import Bullet
    from sluice.cv.selection import Selection
    from tests.conftest import SYNTHETIC_LAYOUT
    from tests.test_cv_engine import CLEAN_REPLY, ENTRIES
    data = json.loads(CLEAN_REPLY)
    selection = Selection(profile=data["profile"], skills=(), roles={
        slot: tuple(Bullet(b["text"], tuple(b["cites"])) for b in bullets)
        for slot, bullets in data["roles"].items()})
    bundle = build_bundle(ENTRIES, [], [], {"Example Foundry": "EF"})
    lines = model_lines(selection, build_slots(SYNTHETIC_LAYOUT, bundle["entries"]))
    assert lines, "(a) model_lines yielded no lines"
    assert any(candidates(text) for _ln, text in lines), "(b) the rule admits nothing"
    vocab = term_vocabulary(bundle, SYNTHETIC_LAYOUT)
    assert unbundled_terms(lines, vocab) == [], "(c) the fixture's own bundle must cover it"


def test_a_snippet_is_cut_to_fifty_characters_of_the_stripped_line():
    # A synthetic line well past the cut, with surrounding whitespace the strip must drop.
    line = "  Built ExampleQuery pipelines for the quarterly reporting cycle across many teams  "
    assert len(line.strip()) > 50
    out = unbundled_terms([(7, line)], frozenset())
    assert out == [(7, "ExampleQuery", line.strip()[:50])]


def test_the_citation_strip_matches_render_exactly():
    # Moved from tests/test_cv_validate.py with the pattern itself (cv/terms.py is its one
    # user now). The strip must remove exactly what the renderer removes, so the term check
    # sees what a reader sees: a non-id bracket like [500] must survive both. Pin equality.
    from sluice.cv.render import _CITE_RE as _RENDER_CITE_RE
    from sluice.cv.terms import _CITE_RE as _TERMS_CITE_RE
    assert _TERMS_CITE_RE.pattern == _RENDER_CITE_RE.pattern
    assert _TERMS_CITE_RE.flags == _RENDER_CITE_RE.flags
    for s in ("I scaled [ES1] fast", "I scaled [500] users", "count [es1] here",
              "value [AB12] ok", "unicode [ES\u0967] digit", "plain text"):
        assert _TERMS_CITE_RE.sub("", s) == _RENDER_CITE_RE.sub("", s), s
