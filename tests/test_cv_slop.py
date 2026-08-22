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
    # check_phrases reports the STEM ("leverage"), not whatever inflected text was
    # actually matched -- see test_check_phrases_catches_the_e_dropped_ing_form_of_
    # stems_ending_in_e below for the inflection-aware matching itself. This fixture
    # uses the bare stem because that is the plainest input that pins the (lineno,
    # stem, snippet) shape without also exercising the -ing rule.
    lines = [(4, "- Cut latency by leverage of a cache [e1]")]
    assert [(n, p.lower()) for n, p, _ in check_phrases(lines)] == [(4, "leverage")]


def test_check_phrases_catches_the_e_dropped_ing_form_of_stems_ending_in_e():
    # Measured against the shipped list while reviewing Task 12 (#167, Task 17):
    # matching was a literal substring test, so a stem ENDING IN 'e' matched every
    # OTHER inflection ("leveraged" contains "leverage") but never its own gerund --
    # English drops the terminal 'e' before adding '-ing' ("leverage" -> "leveraging",
    # never "leverageing"), so "leverage" is not a substring of "leveraging". A stem
    # NOT ending in 'e' needs no such rule: its '-ing' form already contains it as a
    # literal substring ("foster" in "fostering" already matched before this fix,
    # unaffected by it) -- which is also why "needle-mov" and "game-chang" are
    # deliberately truncated rather than spelled with a trailing 'e'.
    #
    # Every stem in _PHRASES ending in 'e' whose gerund is a real, meaningful word:
    # "cutting-edge" and "wealth of experience" also end in 'e' but have no natural
    # '-ing' form, so they are not pinned here (the rule still applies to them
    # mechanically; it is simply a no-op).
    for lineno, ing_word, expected_stem in [
        (1, "leveraging", "leverage"),
        (2, "delving", "delve"),
        (3, "elevating", "elevate"),
        (4, "streamlining", "streamline"),
        (5, "underscoring", "underscore"),
    ]:
        lines = [(lineno, f"- Was {ing_word} the platform [e1]")]
        found = [(n, p) for n, p, _ in check_phrases(lines)]
        assert found == [(lineno, expected_stem)], (
            f"{ing_word!r} did not resolve to stem {expected_stem!r}: {found}")


def test_ing_form_allow_suppression_is_keyed_on_the_reported_stem():
    # Reporting the STEM (not the matched text) is what makes slop_allow's own
    # membership validation (cv/config.py, Task 11: entries must be in _PHRASES,
    # i.e. STEMS) correctly suppress every inflection of an allowed stem, including
    # the '-ing' form this task's fix newly catches. Reporting the matched text
    # instead would mean `slop_allow: ["leverage"]` fails to suppress a "leveraging"
    # hit, because "leveraging" != "leverage".
    lines = [(4, "- Leveraging a shared cache [e1]")]
    assert check_phrases(lines) != []
    assert check_phrases(lines, allow=("leverage",)) == []


def test_drove_is_now_a_recognized_stem():
    # #167's own complaint: compose.py's prose banned `drove` while _PHRASES never
    # enforced it -- banned in prose, unchecked in code. Low-risk to add here: the
    # STYLE tier only HOLDS (never blocks), and cv.style_hold is off by default.
    lines = [(1, "- Drove adoption across three teams [e1]")]
    assert [(n, p) for n, p, _ in check_phrases(lines)] == [(1, "drove")]


def test_check_phrases_sees_ONLY_the_lines_it_is_given():
    # This function has no opinion about scoping -- it matches whatever it is handed.
    # An employer line handed to it WOULD match, which is precisely why the engine must
    # not hand it one (pinned in Task 13): `SLOP leverage: <employer line>` arrives in
    # the retry under "Fix these and re-emit the FULL CV" and is answerable only by
    # renaming the employer, turning a style rule into fabrication pressure -- the
    # LOCATION-refusal shape CLAUDE.md records as the worst case this codebase shipped.
    employer_line = [(1, "Example Leverage Partners")]
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
