"""core/tokens.py: the one tokeniser and term matcher the CV gate and doctor share.

Synthetic names only (owner's ruling, tests/test_fixture_name_neutrality.py): an
Examplelang / Examplelangscript pair stands in for a short name inside a longer one.
"""
import pytest

from sluice.core import tokens as T


def test_the_bundle_re_exports_the_one_tokeniser():
    from sluice.cv import bundle
    assert bundle._WORD_RE is T.WORD_RE
    assert bundle.SKILL_TOKEN_RE is T.TOKEN_RULE_RE


def test_sentence_punctuation_splits_segments_and_an_inner_dot_does_not():
    segs = T.segments("Built Example.lang tooling. Health checks ran")
    assert [[t for t, _s, _e in seg] for seg in segs] == [
        ["Built", "Example.lang", "tooling"], ["Health", "checks", "ran"]]


def test_a_whole_term_never_matches_inside_a_longer_token():
    assert T.find_term("We ship Examplelangscript daily", "Examplelang") == []
    assert len(T.find_term("We ship Examplelang daily", "Examplelang")) == 1


def test_matching_is_case_insensitive_by_default():
    assert len(T.find_term("we ship examplelang", "Examplelang")) == 1


def test_case_sensitive_matching_needs_identical_case():
    assert T.find_term("we ship examplelang", "Examplelang", case_sensitive=True) == []


def test_a_lowercase_term_may_accept_a_capitalised_mention_when_asked():
    text = "Coaching sessions ran weekly"
    assert T.find_term(text, "coaching", case_sensitive=True) == []
    assert len(T.find_term(text, "coaching", case_sensitive=True,
                           lower_accepts_capital=True)) == 1


def test_a_capitalised_term_never_matches_a_lowercase_word():
    # The Go / go-live shape: a capitalised tool must not be licensed by an ordinary word.
    assert T.find_term("Led the ex-live cutover", "Ex", case_sensitive=True,
                       lower_accepts_capital=True) == []


def test_a_phrase_never_matches_across_a_sentence_break():
    assert T.find_term("at Example. Zephyr checks ran", "Example Zephyr") == []
    assert len(T.find_term("at Example Zephyr, checks ran", "Example Zephyr")) == 1


def test_a_hyphenated_compound_matches_its_spaced_spelling():
    assert len(T.find_term("a co-founder of it", "co founder")) == 1


def test_a_term_touching_a_non_ascii_letter_or_digit_is_not_whole():
    assert T.find_term("Société Example", "Soci") == []
    assert T.find_term("Widget3０ units", "Widget3") == []


def test_figures_finds_digits_in_any_script_and_normalises_them():
    assert T.figures("grew ５００% in 2024") == {"500", "2024"}
    assert T.figures("grew ٥٠٠%") == {"500"}
    assert T.figures("handled 10⁶ requests") == {"106"}


def test_figures_blanks_only_the_spans_it_is_given():
    text = "Ran Example Widget3 at 30 sites"
    spans = [s for occ in T.find_term(text, "Example Widget3") for s in occ]
    assert T.figures(text, remove=spans) == {"30"}


def test_removing_a_multi_token_tool_never_removes_the_gap_between_its_tokens():
    # A full-width figure between two tokens of a licensed tool must still be scanned.
    text = "Example ５００ Widget"
    spans = [s for occ in T.find_term(text, "Example Widget") for s in occ]
    assert T.figures(text, remove=spans) == {"500"}


def test_a_licensed_name_never_launders_a_longer_figure():
    text = "Ran Widget30 nodes"
    spans = [s for occ in T.find_term(text, "Widget3") for s in occ]
    assert T.figures(text, remove=spans) == {"30"}


def test_tool_items_reads_a_comma_list():
    entry = {"fields": {"Tools": "Examplelang, .Examplenet , Example.lang"}}
    assert T.tool_items(entry) == ["Examplelang", ".Examplenet", "Example.lang"]


def test_tool_items_refuses_a_nameless_item():
    with pytest.raises(ValueError, match="no name"):
        T.tool_items({"fields": {"Tools": "Examplelang, ..."}})


def test_tool_items_refuses_a_digit_led_token():
    with pytest.raises(ValueError, match="must begin with a letter"):
        T.tool_items({"fields": {"Tools": "Examplestandard 9001"}})


@pytest.mark.parametrize("decoy", ["Examplelang#", ".Examplenet", "Example.lang",
                                   "Example Zephyr", "co founder"])
def test_the_decoy_validator_accepts_what_the_matcher_can_see(decoy):
    assert T.decoy_problem(decoy) is None


@pytest.mark.parametrize("decoy", ["", "Example-Zephyr", "100%", "Ph.D.",
                                   "Examplé", "日本"])
def test_the_decoy_validator_refuses_what_the_matcher_would_drop(decoy):
    assert T.decoy_problem(decoy)


def test_validate_decoys_names_the_position_and_never_echoes_the_value():
    with pytest.raises(ValueError) as exc:
        T.validate_decoys(["Example Zephyr", "Example-Secret"])
    assert "entry 2" in str(exc.value)
    assert "Example-Secret" not in str(exc.value)


def test_blanking_a_span_never_joins_the_digit_runs_either_side_of_it():
    assert T.figures("12x34", remove=[(2, 3)]) == {"12", "34"}


def test_tool_items_accepts_a_yaml_list_and_validates_each_element():
    entry = {"fields": {"Tools": [" Examplelang ", "", ".Examplenet"]}}
    assert T.tool_items(entry) == ["Examplelang", ".Examplenet"]
    with pytest.raises(ValueError, match="must begin with a letter"):
        T.tool_items({"fields": {"Tools": ["Examplestandard 9001"]}})


@pytest.mark.parametrize("value", [7, {"a": 1}, [1, "x"]])
def test_tool_items_refuses_a_non_text_value_with_a_value_error_naming_the_field(value):
    with pytest.raises(ValueError, match="Tools"):
        T.tool_items({"fields": {"Tools": value}})


def test_tool_items_with_no_fields_or_a_blank_value_is_empty():
    assert T.tool_items({}) == []
    assert T.tool_items({"fields": {"Tools": "  "}}) == []


def test_validate_decoys_accepts_none_and_empty():
    T.validate_decoys(None)
    T.validate_decoys([])


def test_decoy_problem_on_a_non_string_is_a_problem_not_a_crash():
    assert T.decoy_problem(None)
    assert T.decoy_problem(5)


@pytest.mark.parametrize("text,want", [
    ("Grew revenue 8.3x", {"8.3"}),
    ("to 3,8 million", {"38"}),
    ("worth 3,000 units", {"3000"}),
    ("worth 3000 units", {"3000"}),
    ("a 2.5 ratio", {"2.5"}),
    ("grew to 40. Then 7, then 9.", {"40", "7", "9"}),
    ("v1.2.3 shipped", {"1.2.3"}),
    ("grew \uff13.\uff15 fold", {"3.5"}),
])
def test_figures_joins_a_digit_run_across_single_separators(text, want):
    assert T.figures(text) == want


def test_a_blanked_span_never_joins_figures_across_a_separator():
    assert T.figures("12.x.34", remove=[(3, 4)]) == {"12", "34"}


@pytest.mark.parametrize("text", ["Ran Example;Zephyr checks", "Ran Example:Zephyr checks",
                                  "Ran Example!Zephyr checks"])
def test_sentence_punctuation_without_whitespace_does_not_break_a_phrase(text):
    assert len(T.find_term(text, "Example Zephyr")) == 1


@pytest.mark.parametrize("text", ["Ran Example. Zephyr checks", "Ran Example; Zephyr checks",
                                  "Ran Example.\nZephyr checks"])
def test_sentence_punctuation_before_whitespace_still_breaks_a_phrase(text):
    assert T.find_term(text, "Example Zephyr") == []


@pytest.mark.parametrize("text,want", [
    ("Served 2 000 users", {"2000", "2", "000"}),
    ("Served 2,000 users", {"2000"}),
    ("Served 10\u00a0000 users", {"10000"}),
    ("Served 10\u202f000 users", {"10000"}),
    ("Served 10\u2009000 users", {"10000"}),
    ("Served 50,000 users", {"50000"}),
    ("Served 1,000,000 users", {"1000000"}),
    ("Served 3,000.5 users", {"3000.5"}),
    ("Q3 2023", {"3", "2023"}),
    ("in 2019 2020", {"2019", "2020"}),
    ("5 12 345", {"5", "12345", "12", "345"}),
    ("a 2 0000", {"2", "0000"}),
])
def test_figures_groups_thousands_into_one_figure_and_a_space_otherwise_separates(text, want):
    assert T.figures(text) == want


@pytest.mark.parametrize("text,want", [
    ("Served 10 000 users", {"10000", "10", "000"}),
    ("Led 3 100-person teams", {"3100", "3", "100"}),
    ("Served 1 000 000 users", {"1000000", "1", "000"}),
    ("Served 3 100.5 units", {"3100.5", "3", "100.5"}),
    ("In Q3 120 customers", {"3", "120"}),
    ("Served 3,100 users", {"3100"}),
    ("Served 3\u00a0100 users", {"3100"}),
])
def test_an_ascii_space_group_is_read_both_ways_and_other_grouping_once(text, want):
    # "Led 3 100-person teams" is three teams or 3100: the entry side licenses either
    # reading, and cv/reply.py refuses the shape in the model's text. A digit glued to a
    # letter (Q3) is a label's, never the head of a grouped number.
    assert T.figures(text) == want


@pytest.mark.parametrize("text", ["Grew 3X100 units", "Grew \uff13X\uff11\uff10\uff10 units"])
def test_find_term_never_spans_a_name_glued_between_digits(text):
    # figures() turns a blanked one-character span into an ASCII space, which could then
    # group the digits either side; find_term is why check_selection never hands it one.
    assert T.find_term(text, "X") == []
    assert T.find_term(text, "X", case_sensitive=True) == []


@pytest.mark.parametrize("text,want", [
    ("Saved USD3 100 a month", {"3100", "3", "100"}),
    ("In Q3\u00a0120 customers", {"3", "120"}),
    ("In Q3\u2009120 customers", {"3", "120"}),
    ("Grew 1.5 100-node", {"1.5", "100"}),
    ("Grew 3,8 000", {"38", "000"}),
    ("Saved 1,000 000", {"1000000", "1000", "000"}),
])
def test_group_reading_is_one_rule_for_every_separator(text, want):
    # Two or more letters (USD3) are a code on the number and do not exempt it; a lone
    # letter (Q3) is a label whatever the space; a decimal's fraction or a comma decimal
    # never heads a group.
    assert T.figures(text) == want


def test_a_lone_letter_on_a_grouped_number_is_the_accepted_residual():
    # core/tokens.py::group_reading: "x1 000" cannot be told from "Q3 120", so it reads
    # as the two figures 1 and 000 and is not refused (tests/test_cv_reply.py).
    assert T.figures("a multiple of x1 000") == {"1", "000"}


@pytest.mark.parametrize("text,want", [("Saved R10,000", {"10000"}), ("x1,000", {"1000"}),
                                       ("Q3 120", {"3", "120"})])
def test_a_comma_group_is_one_number_whatever_letter_precedes_it(text, want):
    # A comma is never ambiguous, so the lone-letter label exemption is for SPACES only.
    assert T.figures(text) == want


def test_a_very_long_comma_chain_is_read_without_recursing():
    # One group per loop step: a recursive walk back along the chain hit RecursionError
    # on ~1500 groups, under reply.py's size cap, breaking its never-raises contract.
    text = "1" + ",000" * 1500
    assert T.figures(text) == {"1" + "000" * 1500}
    assert T.group_reading(text, len(text) - 4) == "group"


@pytest.mark.parametrize("sep", [",", " "])
def test_a_60k_group_chain_is_read_in_linear_time(sep):
    # figures() runs on vault entry text, which has no size cap: a walk back along the chain
    # from every separator was quadratic. Bound is loose enough for a slow CI host and
    # tight enough that a quadratic pass (seconds at this size) cannot meet it.
    import time
    text = "1" + (sep + "000") * 15000
    start = time.perf_counter()
    got = T.figures(text)
    assert time.perf_counter() - start < 0.5
    assert "1" + "000" * 15000 in got
