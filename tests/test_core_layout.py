"""core/layout.py::parse_layout -- one row per validation rule in the spec's §4.1.

Mappings are built directly here (the pure parser); tests/test_core_layout_store.py reads
YAML TEXT through the store, the route a user's note takes.
"""
import pytest

from sluice.core.layout import fold_employer, parse_layout
from sluice.core.protocols import SECTION_HEADINGS, CvLayout, LayoutError, LayoutRole


def _role(**over):
    role = {"heading": "Example Alpha", "from": "01/2020", "to": "present"}
    role.update(over)
    return role


def _problems(mapping):
    with pytest.raises(LayoutError) as exc:
        parse_layout(mapping)
    return exc.value.problems


def test_a_minimal_layout_parses_with_every_optional_field_abstaining():
    layout = parse_layout({"roles": [_role()]})
    assert layout == CvLayout(roles=(LayoutRole(
        heading="Example Alpha", start="01/2020", end="present", location="", title="",
        employers=("Example Alpha",), bullets_max=None),))
    assert (layout.skills_max, layout.certificates, layout.education,
            layout.any_role, layout.omitted) == (None, (), (), (), ())


def test_every_field_is_read():
    layout = parse_layout({
        "skills_max": 4,
        "roles": [_role(location="Example Location A", title="SYNTHETIC-TITLE-1",
                        bullets_max=3, employers=["Example Beta", "Example Meridian"])],
        "certificates": ["Example Cert"], "education": ["Example University, BSc"],
        "any_role": ["Example Cartography"], "omitted": ["Example Tidal"],
    })
    role = layout.roles[0]
    assert (role.location, role.title, role.bullets_max, role.employers) == (
        "Example Location A", "SYNTHETIC-TITLE-1", 3, ("Example Beta", "Example Meridian"))
    assert (layout.skills_max, layout.certificates, layout.education,
            layout.any_role, layout.omitted) == (
        4, ("Example Cert",), ("Example University, BSc",), ("Example Cartography",),
        ("Example Tidal",))


def test_a_non_mapping_frontmatter_is_refused():
    assert "frontmatter" in _problems(["roles"])[0]


def test_roles_must_be_a_non_empty_list():
    assert any(p.startswith("roles:") for p in _problems({}))
    assert any(p.startswith("roles:") for p in _problems({"roles": []}))


def test_every_problem_is_reported_at_once():
    problems = _problems({"roles": [_role(**{"from": "13/2020"}), _role(to="soon")],
                          "skills_max": -1})
    assert len(problems) == 3, problems


def test_a_bad_month_names_its_path():
    assert _problems({"roles": [_role(**{"from": "13/2020"})]}) == (
        "roles[0].from: is not MM/YYYY",)


def test_from_equal_to_to_is_accepted_and_from_after_to_is_refused():
    parse_layout({"roles": [_role(**{"from": "03/2020", "to": "03/2020"})]})
    assert _problems({"roles": [_role(**{"from": "04/2020", "to": "03/2020"})]}) == (
        "roles[0]: from is after to",)


def test_present_is_normalised_whatever_its_case():
    assert parse_layout({"roles": [_role(to="PRESENT")]}).roles[0].end == "present"


def test_a_valueless_bullets_max_reads_as_absent_never_zero():
    assert parse_layout({"roles": [_role(bullets_max=None)]}).roles[0].bullets_max is None


def test_zero_is_a_real_cap():
    assert parse_layout({"roles": [_role(bullets_max=0)]}).roles[0].bullets_max == 0


@pytest.mark.parametrize("bad", [True, -1, 2.5, "3"])
def test_a_cap_must_be_a_whole_number(bad):
    assert _problems({"roles": [_role(bullets_max=bad)]})[0].startswith(
        "roles[0].bullets_max:")
    assert _problems({"roles": [_role()], "skills_max": bad})[0].startswith("skills_max:")


def test_an_unknown_role_key_is_refused_with_a_hint():
    assert _problems({"roles": [_role(bullet_max=3)]}) == (
        "roles[0].bullet_max: unknown key -- did you mean bullets_max?",)


def test_a_per_role_key_at_the_top_level_is_refused_by_name():
    assert _problems({"roles": [_role()], "bullets_max": 3}) == (
        "bullets_max: belongs inside a role, under roles:",)


@pytest.mark.parametrize("key,meant", [("skill_max", "skills_max"), ("anyrole", "any_role"),
                                       ("role", "roles"), ("certifications", "certificates")])
def test_a_near_miss_top_level_key_is_refused(key, meant):
    assert _problems({"roles": [_role()], key: 1}) == (
        f"{key}: unknown key -- did you mean {meant}?",)


@pytest.mark.parametrize("key", ["tags", "base", "aliases", "description", "notes"])
def test_obsidian_and_user_metadata_at_the_top_level_is_ignored(key):
    parse_layout({"roles": [_role()], key: "anything"})


def test_a_scalar_where_a_list_belongs_is_refused():
    assert _problems({"roles": [_role()], "certificates": "Example Cert"}) == (
        "certificates: must be a list -- put each item on its own '- ' line",)


def test_a_valueless_list_key_reads_as_absent():
    assert parse_layout({"roles": [_role()], "education": None}).education == ()


@pytest.mark.parametrize("field", ["heading", "location", "title"])
def test_a_pipe_is_refused_in_meta_fields(field):
    assert _problems({"roles": [_role(**{field: "Example | Alpha"})]})[0].startswith(
        f"roles[0].{field}: '|'")


def test_a_heading_equal_to_a_section_heading_is_refused():
    # Derived from the one heading tuple, in another case: the fold is what is under test.
    heading = SECTION_HEADINGS[1].title()
    assert _problems({"roles": [_role(heading=heading)]}) == (
        "roles[0].heading: equals a CV section heading",)


def test_a_line_break_or_control_character_is_refused_in_any_string():
    assert _problems({"roles": [_role(title="SYNTHETIC\nTITLE")]})[0].startswith(
        "roles[0].title: contains a line break")
    assert _problems({"roles": [_role()], "education": ["Example\x0bUniversity"]})[0] \
        .startswith("education[0]: contains a line break")


def test_a_documented_placeholder_left_anywhere_is_refused_at_its_path():
    assert _problems({"roles": [_role(location="<location>")]}) == (
        "roles[0].location: still holds the example placeholder <location>",)
    assert _problems({"roles": [_role(**{"from": "<MM/YYYY>"})]}) == (
        "roles[0].from: still holds the example placeholder <MM/YYYY>",)


def test_a_company_both_omitted_and_in_a_role_is_refused():
    # The same employer in another case is one name after the fold. Bound to a name rather
    # than written inline, so a text sweep of list literals sees no `.casefold()` item.
    folded = ["Example Beta".casefold()]
    assert _problems({"roles": [_role(employers=["Example Beta"])], "omitted": folded}) == (
        "omitted[0]: also listed under any_role or a role's employers",)


def test_an_empty_employers_list_is_refused():
    assert _problems({"roles": [_role(employers=[])]}) == (
        "roles[0].employers: must not be empty",)


def test_a_missing_or_blank_heading_is_refused():
    headless = {"from": "01/2020", "to": "present"}
    assert _problems({"roles": [headless]}) == ("roles[0].heading: required",)
    assert _problems({"roles": [_role(heading="  ")]}) == ("roles[0].heading: must not be blank",)


def test_a_role_that_is_not_a_mapping_is_refused():
    assert _problems({"roles": ["Example Alpha"]}) == (
        "roles[0]: must be a mapping (heading:, from:, to:, ...)",)


def test_a_non_text_location_or_title_is_refused():
    assert _problems({"roles": [_role(location=7)]}) == (
        "roles[0].location: must be text, found int",)
    assert _problems({"roles": [_role(title=True)]}) == (
        "roles[0].title: must be text, found bool",)


def test_a_blank_employers_item_is_refused():
    assert _problems({"roles": [_role(employers=["Example Beta", " "])]}) == (
        "roles[0].employers[1]: must not be blank",)


def test_a_missing_from_is_refused():
    fromless = {"heading": "Example Alpha", "to": "present"}
    assert _problems({"roles": [fromless]}) == ("roles[0].from: required",)


def test_an_unknown_role_key_without_a_near_match_is_named_by_path_only():
    problems = _problems({"roles": [_role(zzzzzz=1)]})
    assert problems == ("roles[0]: an unknown key -- a role's keys are "
                        "heading, from, to, location, title, employers, bullets_max",)


def test_an_unknown_role_key_with_a_control_character_is_never_echoed():
    key = "bullets_max\x1b[31m"
    problems = _problems({"roles": [_role(**{key: 1})]})
    assert all("\x1b" not in p and "bullets_max" not in p.split("--")[0] for p in problems)
    assert len(problems) == 1 and problems[0].startswith("roles[0]: an unknown key")


def test_a_long_unknown_role_key_is_not_echoed():
    problems = _problems({"roles": [_role(**{"bullets_max" + "x" * 80: 1})]})
    assert problems[0].startswith("roles[0]: an unknown key")
    assert "xxxx" not in problems[0]


def test_a_near_miss_top_level_key_with_a_control_character_is_not_echoed():
    problems = _problems({"roles": [_role()], "any_role\x1b": 1})
    assert problems == ("top level: an unknown key resembling any_role",)


def test_a_company_both_in_any_role_and_omitted_is_refused():
    folded = ["Example Cartography".casefold()]
    assert _problems({"roles": [_role()], "any_role": ["Example Cartography"],
                      "omitted": folded}) == (
        "omitted[0]: also listed under any_role or a role's employers",)


def test_employer_fold_ignores_no_break_and_doubled_spaces():
    plain = fold_employer("Example Beta")
    assert fold_employer("Example\u00a0Beta") == plain
    assert fold_employer("Example  Beta") == plain
    spaced = ["Example\u00a0Beta"]
    assert _problems({"roles": [_role(employers=["Example  Beta"])], "omitted": spaced}) == (
        "omitted[0]: also listed under any_role or a role's employers",)


def test_the_cap_message_is_exact():
    assert _problems({"roles": [_role()], "skills_max": -1}) == (
        "skills_max: must be a whole number of 0 or more (0 means none; leave the key out "
        "for no cap)",)


def test_the_pipe_message_is_exact():
    assert _problems({"roles": [_role(title="Example | Title")]}) == (
        "roles[0].title: '|' separates the CV's meta-line fields, so it cannot appear here",)


def test_the_control_character_message_is_exact():
    assert _problems({"roles": [_role(title="SYNTHETIC\nTITLE")]}) == (
        "roles[0].title: contains a line break or control character",)


def test_roles_required_message_is_exact():
    assert _problems({}) == (
        "roles: required -- a list with one entry per heading on the CV",)


def test_a_bad_to_names_present():
    assert _problems({"roles": [_role(to="soon")]}) == (
        "roles[0].to: is not MM/YYYY or present",)


def test_present_is_refused_as_a_from():
    assert _problems({"roles": [_role(**{"from": "present"})]}) == (
        "roles[0].from: is not MM/YYYY",)


def test_employers_given_as_a_scalar_is_refused():
    assert _problems({"roles": [_role(employers="Example Beta")]}) == (
        "roles[0].employers: must be a list -- put each item on its own '- ' line",)


@pytest.mark.parametrize("bad", ["00/2020", "13/2020", "01/0000", "01/\u0662\u0660\u0662\u0660",
                                 "1/2020", "01/20201"])
def test_a_date_must_be_a_real_ascii_month_and_year(bad):
    assert _problems({"roles": [_role(**{"from": bad})]}) == (
        "roles[0].from: is not MM/YYYY",)
