"""core/layout.py: which entry may be cited under which role (#364 spec §4.3, D6)."""
from sluice.core.layout import (Placement, build_slots, employers_of, layout_text,
                                no_citable_slot, place, placement_counts)
from sluice.core.protocols import CvLayout, LayoutRole


def _layout(*roles, any_role=(), omitted=()):
    return CvLayout(roles=tuple(roles), any_role=any_role, omitted=omitted)


def _role(heading, employers=None, bullets_max=None):
    return LayoutRole(heading=heading, start="01/2020", end="present",
                      employers=tuple(employers or (heading,)), bullets_max=bullets_max)


ALPHA = _role("Example Alpha")
GROUP = _role("Example Northgate", employers=["Example Beta", "Example Meridian"], bullets_max=3)


def test_employers_of_takes_the_whole_value_and_each_part_folded():
    assert employers_of("Example Beta / Example Meridian") == {
        "example beta / example meridian", "example beta", "example meridian"}


def test_a_block_list_company_and_an_nbsp_still_match():
    # Review Focus 2: Obsidian writes a list property, which the vault reader joins with
    # ", "; a pasted name can carry a non-breaking space.
    layout = _layout(GROUP)
    assert place(layout, "Example Beta, Example Meridian").reason == "role"
    assert place(layout, "Example\u00a0Beta").roles == frozenset({0})


def test_a_multi_employer_company_matches_through_one_part():
    # Review Focus 5: an entry spanning two employers is citable under a roll-up that
    # names only one of them.
    assert place(_layout(ALPHA, GROUP), "Example Telemetry / Example Meridian").roles == frozenset({1})


def test_the_whole_value_keeps_a_comma_inside_one_employer_matchable():
    layout = _layout(_role("Example, Inc"))
    assert place(layout, "Example, Inc").reason == "role"
    # The role side is never split, so a company merely ending in Inc does not match.
    assert place(layout, "Example Co, Inc").reason == "unmatched"


def test_canonically_equivalent_spellings_match():
    # One employer typed precomposed (NFC) in the layout and decomposed (NFD) in an
    # entry is one name, so one match: the vault's own fold (#205, #299).
    layout = _layout(_role("Example Co Soci\u00e9t\u00e9"))
    assert place(layout, "Example Co Socie\u0301te\u0301").reason == "role"


def test_any_role_makes_an_entry_eligible_everywhere():
    layout = _layout(ALPHA, GROUP, any_role=("Example Cartography",))
    assert place(layout, "Example Cartography") == Placement("any_role", frozenset({0, 1}))


def test_omitted_blank_and_unmatched_are_eligible_nowhere():
    layout = _layout(ALPHA, omitted=("Example Tidal",))
    assert place(layout, "Example Tidal") == Placement("omitted", frozenset())
    assert place(layout, "") == Placement("blank", frozenset())
    assert place(layout, "   ") == Placement("blank", frozenset())
    assert place(layout, "Example Robotics") == Placement("unmatched", frozenset())


def test_a_role_without_employers_scopes_by_its_heading():
    # Built directly with EMPTY employers (the `_role` helper fills them), so the
    # heading fallback in `place` is the only thing that can match.
    layout = _layout(LayoutRole("Example Alpha", "01/2020", "present"))
    assert place(layout, "example alpha").roles == frozenset({0})


def test_build_slots_numbers_roles_in_layout_order_with_their_eligible_ids():
    entries = [{"id": "EA1", "company": "Example Alpha"},
               {"id": "EB1", "company": "Example Beta"},
               {"id": "EN1", "company": "Example Robotics"}]
    slots = build_slots(_layout(ALPHA, GROUP), entries)
    assert [(s.id, s.role.heading, s.eligible, s.budget) for s in slots] == [
        ("R1", "Example Alpha", ("EA1",), None),
        ("R2", "Example Northgate", ("EB1",), 3)]


def test_a_slot_with_no_eligible_entry_has_a_budget_of_zero():
    slots = build_slots(_layout(ALPHA, GROUP), [{"id": "EA1", "company": "Example Alpha"}])
    assert [s.budget for s in slots] == [None, 0]


def test_no_citable_slot_needs_a_role_that_wants_bullets():
    nothing_matches = build_slots(_layout(ALPHA), [{"id": "EN1", "company": "Example Robotics"}])
    assert no_citable_slot(nothing_matches)
    headings_only = build_slots(_layout(_role("Example Alpha", bullets_max=0)),
                                [{"id": "EA1", "company": "Example Alpha"}])
    assert not no_citable_slot(headings_only)


def test_placement_counts_tallies_each_reason():
    layout = _layout(ALPHA, omitted=("Example Tidal",))
    entries = [{"id": "A", "company": "Example Alpha"}, {"id": "B", "company": ""},
               {"id": "C", "company": "Example Tidal"}, {"id": "D", "company": "Example X"},
               {"id": "E", "company": "Example Y"}]
    assert placement_counts(layout, entries) == {
        "role": 1, "any_role": 0, "omitted": 1, "blank": 1, "unmatched": 2}


def test_layout_text_carries_every_string_the_composer_is_shown():
    layout = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                        location="Example Location A",
                                        title="SYNTHETIC-TITLE-1",
                                        employers=("Example Beta",)),),
                      certificates=("Example Cert",), education=("Example University",))
    layout = CvLayout(roles=layout.roles, certificates=layout.certificates,
                      education=layout.education, any_role=("Example Cartography",),
                      omitted=("Example Tidal",))
    values = ["Example Alpha", "Example Location A", "SYNTHETIC-TITLE-1", "Example Beta",
              "Example Cert", "Example University", "Example Cartography", "Example Tidal"]
    lines = layout_text(layout).split("\n")
    # One string per line: a value is a whole line, never a fragment of a joined one.
    assert sorted(lines) == sorted(values)


def test_a_role_match_beats_any_role():
    # Precedence is role > omitted > any_role (core/layout.py::place): any_role is the
    # widest grant, so a narrower answer from any part wins.
    layout = _layout(ALPHA, GROUP, any_role=("Example Alpha",))
    assert place(layout, "Example Alpha") == Placement("role", frozenset({0}))


def test_an_omitted_part_beats_an_any_role_part():
    layout = _layout(ALPHA, GROUP, any_role=("Example Cartography",),
                     omitted=("Example Tidal",))
    assert place(layout, "Example Tidal / Example Cartography") == Placement(
        "omitted", frozenset())


def test_a_role_part_beats_an_any_role_part():
    layout = _layout(ALPHA, GROUP, any_role=("Example Cartography",))
    assert place(layout, "Example Alpha / Example Cartography") == Placement(
        "role", frozenset({0}))


def test_an_any_role_part_alone_is_eligible_everywhere():
    layout = _layout(ALPHA, GROUP, any_role=("Example Cartography",),
                     omitted=("Example Tidal",))
    assert place(layout, "Example Robotics / Example Cartography") == Placement(
        "any_role", frozenset({0, 1}))


def test_an_entry_can_match_two_roles():
    layout = _layout(ALPHA, _role("Example Gamma", employers=["Example Alpha"]))
    assert place(layout, "Example Alpha").roles == frozenset({0, 1})


def test_one_any_role_part_of_a_multi_part_company_is_enough():
    layout = _layout(ALPHA, GROUP, any_role=("Example Cartography",))
    assert place(layout, "Example Robotics; Example Cartography") == Placement(
        "any_role", frozenset({0, 1}))


def test_a_role_part_beats_an_omitted_part():
    layout = _layout(ALPHA, omitted=("Example Tidal",))
    assert place(layout, "Example Tidal / Example Alpha") == Placement("role", frozenset({0}))


def test_a_missing_or_none_company_is_blank():
    layout = _layout(ALPHA)
    assert place(layout, None) == Placement("blank", frozenset())
    slots = build_slots(layout, [{"id": "EA1"}])
    assert slots[0].eligible == () and slots[0].budget == 0
    assert placement_counts(layout, [{"id": "EA1"}])["blank"] == 1


def test_no_citable_slot_of_no_slots_is_false():
    assert not no_citable_slot(())


def test_a_heading_only_role_keeps_a_zero_budget_with_an_eligible_entry():
    slots = build_slots(_layout(_role("Example Alpha", bullets_max=0)),
                        [{"id": "EA1", "company": "Example Alpha"}])
    assert slots[0].eligible == ("EA1",) and slots[0].budget == 0
