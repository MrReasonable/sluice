# tests/test_cv_slop.py
from sluice.cv.slop import check_hard, check_phrases, check_text

def test_em_dash_is_error():
    errs, _ = check_text("Led the team \u2014 and shipped.")
    assert errs and errs[0][1] == "EM-DASH"

def test_double_hyphen_is_error():
    errs, _ = check_text("cost-effective -- and fast")
    assert any(l == "DOUBLE-HYPHEN-DASH" for _, l, _ in errs)

def test_en_dash_date_range_is_clean():
    errs, warns = check_text("02/2023–present | Example Location A | Staff Engineer")
    assert errs == []

def test_ai_phrase_is_advisory_warning_not_error():
    errs, warns = check_text("I spearheaded a seamless rollout.")
    assert errs == []
    assert len(warns) >= 2  # spearhead, seamless


def test_check_phrases_reports_the_matched_stem_with_its_line_number():
    # _PHRASE_RE matches the STEM as a literal substring, not a word-form-aware
    # match -- "leveraging" does NOT contain "leverage" (they diverge at the 8th
    # character), so the fixture uses the stem itself rather than an inflection.
    lines = [(4, "- Cut latency by leverage of a cache [e1]")]
    assert [(n, p.lower()) for n, p, _ in check_phrases(lines)] == [(4, "leverage")]


def test_check_phrases_sees_ONLY_the_lines_it_is_given():
    # This function has no opinion about scoping -- it matches whatever it is handed.
    # An employer line handed to it WOULD match, which is precisely why the engine must
    # not hand it one (pinned in Task 13): `SLOP leverage: <employer line>` arrives in
    # the retry under "Fix these and re-emit the FULL CV" and is answerable only by
    # renaming the employer, turning a style rule into fabrication pressure -- the
    # LOCATION-refusal shape CLAUDE.md records as the worst case this codebase shipped.
    employer_line = [(1, "Leverage Partners Ltd")]
    assert check_phrases(employer_line) != [], "no scoping happens in this function"
    assert check_phrases([]) == []


def test_an_allowed_phrase_is_not_reported():
    lines = [(4, "- Leveraged a cache [e1]")]
    assert check_phrases(lines) != []
    assert check_phrases(lines, allow=("leverage",)) == []


def test_allow_matching_is_case_insensitive():
    lines = [(4, "- Leveraged a cache [e1]")]
    assert check_phrases(lines, allow=("LEVERAGE",)) == []


def test_check_hard_still_scans_every_line():
    # HARD is NOT scoped: an em dash in an employer line is always fixable without
    # inventing anything, unlike a phrase.
    assert check_hard("Example Co \u2014 Ltd") != []


def test_check_hard_reports_1_indexed_line_numbers_over_the_whole_document():
    errs = check_hard("line one\nline two \u2014 has an em dash\nline three")
    assert errs[0][0] == 2
