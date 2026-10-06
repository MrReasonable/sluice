"""Doctor's rows for the structured CV (#364/#365/#368 spec §9.1, D14, §8, §6.6), each in
each state, asserted through the DEFAULT printed view -- a row's state alone is not what a
user reads."""
import pytest

from sluice.core.doctor import (DEAD, DEGRADED, OK, SETUP, ComponentCheck, DoctorReport,
                                classify_attribution, classify_cv_eligibility,
                                classify_cv_layout, classify_decoys, classify_tools)
from sluice.core.protocols import CV_LAYOUT_RELPATH, CvLayout, LayoutError, LayoutRole

LAYOUT = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                    employers=("Example Alpha",)),),
                  omitted=("Example Tidal",))


def _entry(company="Example Alpha", tools="", skills=False):
    # Keywords, not dict literals keyed by the field names: the fixture-name sweep reads such
    # a literal's value as a skill NAME, and these are a parameter and a presence flag.
    return {"title": "SYNTHETIC-ENTRY", "company": company, "fields": dict(Tools=tools),
            "legacy": dict(Skills=skills)}


def _printed(capsys, *rows, strict=False):
    from sluice.cli import _print_doctor_verdict
    report = DoctorReport(checks=[], components=list(rows))
    _print_doctor_verdict(report, offline=True, strict=strict,
                          exit_code=report.exit_code(strict=strict))
    return " ".join(capsys.readouterr().out.split())


# --- the CV Layout row -------------------------------------------------------------------

def test_a_parsed_layout_is_ok_with_its_role_count():
    row = classify_cv_layout(LAYOUT)
    assert (row.subject, row.state, row.detail) == ("cv_layout", OK, "1 role")


def test_an_absent_layout_is_setup_blocking_cv_and_says_where(capsys):
    row = classify_cv_layout(None)
    assert (row.state, row.blocks) == (SETUP, ("cv",))
    out = _printed(capsys, row)
    assert "Still to set up:" in out and CV_LAYOUT_RELPATH in out


def test_a_malformed_layout_is_dead_and_lists_every_problem(capsys):
    row = classify_cv_layout(None, LayoutError(["roles[0].from: required",
                                                "roles[0].to: required"]))
    assert (row.state, row.blocks) == (DEAD, ("cv",))
    out = _printed(capsys, row)
    assert "Not working:" in out
    assert "roles[0].from: required" in out and "roles[0].to: required" in out


@pytest.mark.parametrize("exc,strerror", [
    (PermissionError(13, "Permission denied", "/abs/vault/x"), "Permission denied"),
    (IsADirectoryError(21, "Is a directory", "/abs/vault/x"), "Is a directory"),
])
def test_an_unreadable_layout_is_dead_never_absent_and_names_no_path(exc, strerror):
    row = classify_cv_layout(None, exc)
    assert (row.state, row.blocks) == (DEAD, ("cv",))
    assert "could not be read" in row.detail and strerror in row.detail
    assert "/abs" not in row.detail


# --- an unusable Tools item, which the gate cannot use -------------------------------------

def test_an_unusable_tools_item_is_dead_and_counted_never_named(capsys):
    rows = classify_tools([_entry(tools="Examplelang"), _entry(tools="9001 Examplestandard")])
    assert [(r.state, r.blocks) for r in rows] == [(DEAD, ("cv",))]
    out = _printed(capsys, *rows)
    assert "1 verified entry" in out and "job-sluice experience list" in out
    assert "9001" not in out and "SYNTHETIC-ENTRY" not in out


def test_usable_tools_draw_no_row():
    assert classify_tools([_entry(tools="Examplelang, .Examplenet"), _entry()]) == []


# --- entries no CV can cite (D6, D14) -------------------------------------------------------

def test_unmatched_and_companyless_entries_are_warned_about_by_default(capsys):
    rows = classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Robotics"),
                                            _entry("Example Robotics"), _entry("")])
    assert [(r.subject, r.state, r.blocks, r.warn_by_default) for r in rows] == [
        ("cv_layout (not on your CV)", DEGRADED, (), True),
        ("cv_layout (no company)", DEGRADED, (), True)]
    out = _printed(capsys, *rows)
    assert "Worth a look" in out
    assert "2 verified experience entries" in out and "1 verified experience entry" in out
    assert "Example Robotics" not in out
    assert "more degraded" not in out, "a listed warning must not also be counted as quiet"


def test_the_eligibility_wording_agrees_with_the_count():
    one = classify_cv_eligibility(LAYOUT, [_entry("Example Robotics"), _entry("")])
    assert all("can cite it " in r.detail for r in one)
    many = classify_cv_eligibility(LAYOUT, [_entry("Example Robotics")] * 2 + [_entry("")] * 2)
    assert all("can cite them " in r.detail for r in many)


def test_an_omitted_or_matched_entry_draws_nothing():
    assert classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Tidal")]) == []


# --- the attribution check going dark (spec §6.6) ---------------------------------------

def test_an_upgraded_vault_with_no_tools_is_warned_about():
    [row] = classify_attribution([_entry(skills=True), _entry()])
    assert (row.state, row.blocks, row.warn_by_default) == (DEGRADED, (), True)
    assert "1 still carries the retired Skills:" in row.detail


def test_a_vault_with_neither_field_is_an_unconfigured_install_and_draws_nothing():
    assert classify_attribution([_entry(), _entry()]) == []


def test_any_declared_tools_turns_the_check_on_and_draws_nothing():
    assert classify_attribution([_entry(skills=True), _entry(tools="Examplelang")]) == []


# --- a decoy contradicting the user's own data (spec §8) ------------------------------------

@pytest.mark.parametrize("decoys,entries,names,layout", [
    (["examplelang"], [_entry(tools="Examplelang")], [], None),
    (["Example Query"], [], ["Example Query"], None),
    (["example tidal"], [], [], LAYOUT),
])
def test_a_decoy_matching_the_users_own_data_is_warned_about_by_position(
        decoys, entries, names, layout):
    [row] = classify_decoys(["Example Zephyr"] + decoys, entries, names, layout)
    assert (row.state, row.blocks, row.warn_by_default) == (DEGRADED, (), True)
    assert "entry 2" in row.detail
    assert decoys[0] not in row.detail and "Example Zephyr" not in row.detail


def test_several_matching_decoys_read_as_a_plural_by_position():
    [row] = classify_decoys(["examplelang", "Example Zephyr", "example tidal"],
                            [_entry(tools="Examplelang")], [], LAYOUT)
    assert "entries 1 and 3 match" in row.detail


def test_a_decoy_inside_a_longer_token_is_no_contradiction():
    assert classify_decoys(["examplelang"], [_entry(tools="Examplelangscript")], [],
                           None) == []


# --- the flag itself ------------------------------------------------------------------

def test_warn_by_default_is_refused_on_a_row_that_is_not_degraded():
    with pytest.raises(ValueError, match="warn_by_default"):
        ComponentCheck("store", "x", OK, "fine", warn_by_default=True)


def test_a_warning_row_blocks_nothing_and_strict_still_fails_on_it(capsys):
    [row] = classify_attribution([_entry(skills=True)])
    report = DoctorReport(checks=[], components=[row])
    assert report.verdict().buckets["cv"] == report.verdict().buckets["ingest"]
    assert (report.exit_code(), report.exit_code(strict=True)) == (0, 1)
    out = _printed(capsys, row, strict=True)
    assert out.count("cv attribution check") == 1, "listed once, not twice, under --strict"
