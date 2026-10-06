"""cv/selection.py: what a reply may render (spec §4.4, §6.0, §6.2, D10)."""
from sluice.core.layout import Slot
from sluice.core.protocols import LayoutRole
from sluice.cv.reply import Bullet, Reply
from sluice.cv.selection import (build_pool, cv_name, named_entries, select,
                                 skills_requested, zero_bullet_findings)


def _slot(sid, heading, budget=None, eligible=("EA1",), bullets_max="same"):
    role = LayoutRole(heading, "01/2020", "present",
                      bullets_max=budget if bullets_max == "same" else bullets_max)
    return Slot(sid, role, eligible, budget)


def _bullets(n, prefix="Did"):
    return tuple(Bullet(f"{prefix} thing {i}", ("EA1",)) for i in range(1, n + 1))


def _reply(roles=None, skills=(), malformed=False):
    return Reply("I build reliable systems.", roles or {}, tuple(skills), malformed)


POOL = ("Example Query", "Examplelang", ".Examplenet")


def test_cv_name_prefers_the_label_and_falls_back_to_the_title():
    # The title is the slug `skills add` writes for the typed name.
    assert cv_name({"title": "examplelang", "fields": {"Label": "Examplelang#"}}) == "Examplelang#"
    assert cv_name({"title": "Example Query", "fields": {"Label": "  "}}) == "Example Query"


def test_the_pool_takes_inventory_names_first_then_tools_deduplicated():
    named = [{"title": "Example Query", "fields": {}}]
    experience = [{"fields": {"Tools": "example query, Examplelang"}}]
    assert build_pool(named, experience) == ("Example Query", "Examplelang")


def test_a_decoy_matching_item_never_enters_the_pool():
    experience = [{"fields": {"Tools": "Examplelang, Exampleban"}}]
    assert build_pool([], experience, decoys=("exampleban",)) == ("Examplelang",)


def test_skills_are_requested_only_with_a_pool_and_a_non_zero_cap():
    assert skills_requested(POOL, None) and skills_requested(POOL, 3)
    assert not skills_requested((), None)
    assert not skills_requested(POOL, 0)


def test_a_kept_pick_renders_in_the_pools_spelling_and_a_one_token_difference_drops():
    sel = select(_reply(skills=["example query", "Example Queries"]), (), POOL, None)
    assert sel.skills == ("Example Query",)
    assert sel.skills_dropped == ("'Example Queries': not one of your skills",)


def test_a_pick_with_a_trailing_period_still_keeps():
    # Review Focus 4: "Example Query." is the same skill, and stray whitespace is noise.
    assert select(_reply(skills=["  Example Query. "]), (), POOL, None).skills == (
        "Example Query",)


def test_off_pool_and_duplicate_picks_drop_before_the_cap_is_applied():
    sel = select(_reply(skills=["Example Ghost", "Examplelang", "examplelang",
                                "Example Query"]), (), POOL, 2)
    assert sel.skills == ("Examplelang", "Example Query")
    assert sel.skills_dropped == ("'Example Ghost': not one of your skills",
                                  "'examplelang': listed twice")


def test_picks_beyond_the_cap_drop_and_are_reported():
    sel = select(_reply(skills=list(POOL)), (), POOL, 2)
    assert sel.skills == ("Example Query", "Examplelang")
    assert sel.skills_dropped == ("'.Examplenet': over skills_max (2)",)


def test_a_malformed_skills_list_drops_everything_and_says_so():
    sel = select(_reply(malformed=True), (), POOL, None)
    assert (sel.skills, sel.skills_dropped) == (
        (), ("the skills list was not a list of strings, so none was used",))


def test_skills_max_zero_requests_none_and_reports_nothing():
    sel = select(_reply(skills=["Example Query"]), (), POOL, 0)
    assert (sel.skills, sel.skills_dropped) == ((), ())


def test_an_absent_cap_keeps_every_bullet_and_reports_nothing():
    slots = (_slot("R1", "Example Alpha", budget=None),)
    sel = select(_reply({"R1": _bullets(7)}), slots, (), None)
    assert (len(sel.roles["R1"]), sel.bullets_trimmed) == (7, ())


def test_a_budget_equal_to_the_bullet_count_trims_nothing():
    slots = (_slot("R1", "Example Alpha", budget=3),)
    assert select(_reply({"R1": _bullets(3)}), slots, (), None).bullets_trimmed == ()


def test_one_bullet_over_budget_is_trimmed_keeping_the_first_n():
    slots = (_slot("R1", "Example Alpha", budget=3),)
    sel = select(_reply({"R1": _bullets(4)}), slots, (), None)
    assert [b.text for b in sel.roles["R1"]] == ["Did thing 1", "Did thing 2", "Did thing 3"]
    assert sel.bullets_trimmed == ("R1 (Example Alpha): kept 3 of 4",)


def test_a_zero_budget_keeps_nothing():
    slots = (_slot("R1", "Example Alpha", budget=0),)
    assert select(_reply({"R1": _bullets(2)}), slots, (), None).roles["R1"] == ()


def test_every_slot_has_a_key_even_when_the_reply_left_it_out():
    slots = (_slot("R1", "Example Alpha"), _slot("R2", "Example Beta"))
    assert select(_reply({"R1": _bullets(1)}), slots, (), None).roles["R2"] == ()


def test_no_bullets_in_any_capable_slot_is_a_finding():
    slots = (_slot("R1", "Example Alpha"), _slot("R2", "Example Beta", budget=0))
    sel = select(_reply({"R2": _bullets(2)}), slots, (), None)   # only in a 0-budget slot
    assert zero_bullet_findings(sel, slots) == [
        "REPLY: no bullets in any role that can carry them -- write bullets for the roles "
        "that list citable entries"]


def test_a_headings_only_layout_renders_with_no_bullets_and_no_finding():
    slots = (_slot("R1", "Example Alpha", budget=0),)
    assert zero_bullet_findings(select(_reply(), slots, (), None), slots) == []


def test_the_pool_holds_exactly_the_names_of_kinds_flagged_for_it():
    # Execution-derived sibling of the cited_by_gate test (spec §4.4, D12): offer one
    # verified entry of EVERY kind, each with a distinct sentinel name, through the reader
    # the engine uses, and ask whose names reached the pool.
    from sluice.core.protocols import EVIDENCE_KINDS

    def read_evidence(kind, verified_only=True):
        return [{"title": f"Example Zephyr {kind.title()}", "fields": {}}]

    pool = build_pool(named_entries(read_evidence), [])
    assert pool == tuple(f"Example Zephyr {k.title()}" for k, s in EVIDENCE_KINDS.items()
                         if s.names_in_skills_pool)
    assert pool, "no kind feeds the pool: the sweep would pass vacuously"


def test_the_pool_follows_the_flag_not_the_kind_name(monkeypatch):
    # With only `skills` flagged, a pool_kinds hard-wired to ("skills",) passes the row
    # above. Moving the flag to another kind separates the two; recording verified_only
    # catches a reader that would offer unverified names.
    import dataclasses

    from sluice.core import protocols
    moved = {k: dataclasses.replace(spec, names_in_skills_pool=(k == "stories"))
             for k, spec in protocols.EVIDENCE_KINDS.items()}
    monkeypatch.setattr(protocols, "EVIDENCE_KINDS", moved)
    asked = []

    def read_evidence(kind, verified_only=True):
        asked.append(verified_only)
        return [{"title": f"Example Zephyr {kind.title()}", "fields": {}}]

    assert build_pool(named_entries(read_evidence), []) == ("Example Zephyr Stories",)
    assert asked and all(asked), "the pool read unverified entries"
