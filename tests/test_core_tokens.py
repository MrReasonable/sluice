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
