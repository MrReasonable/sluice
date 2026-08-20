"""core/candidate.py: the pure derivations over a CandidateProfile.

Fixtures are synthetic. `Example` names and RFC 2606 domains only -- this
file's values are read by tests/test_fixture_name_neutrality.py's sweep.
"""
import dataclasses
import logging

import pytest

from sluice.core.candidate import age_from_dob, contact_block, full_name, has_any_declared
from sluice.core.protocols import CandidateProfile

# The exact 36 field names, in declaration order. A rename
# (`address_line1` -> `address_line_1`) or a reorder must show up as a readable
# diff here -- ten downstream tasks key the vault note's frontmatter and the
# apply packet off these exact spellings, and a bare len()==36 check is silent
# to a rename.
_EXPECTED_FIELD_NAMES = (
    "forenames", "surname", "email", "mobile", "linkedin",
    "address_line1", "address_line2", "town", "county", "postcode", "country",
    "requires_uk_work_permit", "right_to_work_uk", "currently_employed_by_them",
    "previously_employed_by_them", "referred_by_current_employee",
    "how_heard_default", "how_heard_detail_from_lead_source",
    "gender_identity", "identifies_as_trans", "ethnicity", "religion",
    "sexual_orientation", "preferred_pronouns", "disability", "neurodivergent",
    "open_about_orientation_at_work",
    "date_of_birth", "honorific", "marital_status", "nationality", "dual_nationality",
    "first_language", "served_armed_forces", "caring_responsibility",
    "worked_in_construction",
)


def test_candidate_profile_has_exactly_these_36_fields_in_declaration_order():
    # The literal's own length is an independent claim, not merely an
    # emergent property of the tuple-equality check below -- removing a field
    # from BOTH the dataclass and this literal at once would leave the
    # tuple-equality assertion green with a stale "36" in this test's own name.
    assert len(_EXPECTED_FIELD_NAMES) == 36
    # Scope assertion: a broken dataclasses.fields() call returning [] would
    # make the all()s below vacuously true. dataclasses.fields() accepts the
    # class itself -- no instance needed.
    fields = dataclasses.fields(CandidateProfile)
    assert tuple(f.name for f in fields) == _EXPECTED_FIELD_NAMES, (
        "the field roster or its order changed; update the spec and the packet list"
    )
    assert all(f.default == "" for f in fields)
    assert all(f.type is str or f.type == "str" for f in fields)


def test_full_name_joins_declared_parts_only():
    assert full_name(CandidateProfile(forenames="Ada", surname="Example")) == "Ada Example"
    assert full_name(CandidateProfile(forenames="Ada")) == "Ada"
    assert full_name(CandidateProfile(surname="Example")) == "Example"
    assert full_name(CandidateProfile()) == ""


def test_full_name_leaves_no_stray_whitespace():
    assert full_name(CandidateProfile(forenames="  Ada  ", surname="  Example  ")) == "Ada Example"
    assert full_name(CandidateProfile(forenames="   ")) == ""


def test_full_name_collapses_internal_whitespace_runs():
    # full_name feeds the #99/#100 STRUCTURAL guard (cv/engine.py, see its docstring): that guard
    # case-fold-matches the composed header's last line, so a composer that collapses a whitespace
    # run (models routinely do) must still match, or a gate-clean CV will fail the anchor check and
    # the lead will be binned after its one retry.
    assert full_name(CandidateProfile(forenames="Ada  Grace", surname="Example")) == "Ada Grace Example"


def test_contact_block_emits_bare_values_in_mobile_email_linkedin_order():
    # No labels: the retired `cv.contact` config key (#133/#107) used to illustrate
    # labels ("Phone number: ..."), but those were one user's formatting choice
    # living in a value they could edit. Moving them into core/ would make them an
    # unoverridable shipped preference. A user who wants a label puts it in the
    # field value.
    p = CandidateProfile(mobile="+44 20 7946 0000", email="ada@example.invalid",
                         linkedin="https://example.invalid/in/example/")
    assert contact_block(p) == ("+44 20 7946 0000\n"
                               "ada@example.invalid\n"
                               "https://example.invalid/in/example/")
    assert contact_block(CandidateProfile(mobile="+44 20 7946 0000")) == "+44 20 7946 0000"


def test_contact_block_omits_undeclared_lines_rather_than_emitting_blanks():
    p = CandidateProfile(email="ada@example.invalid")
    assert contact_block(p) == "ada@example.invalid"
    assert "\n" not in contact_block(p)
    assert contact_block(CandidateProfile()) == ""


@pytest.mark.parametrize("dob,today,expected", [
    # date_of_birth fixture value: reviewed, invented.
    ("1990-06-15", "2026-06-15", 36),   # exactly on the birthday
    ("1990-06-15", "2026-06-14", 35),   # day before
    ("1990-06-15", "2026-06-16", 36),   # day after
    ("2000-02-29", "2026-02-28", 25),   # leap-day birth, non-leap year
])
def test_age_from_dob_computes_whole_years(dob, today, expected):
    assert age_from_dob(dob, today) == expected


@pytest.mark.parametrize("dob", ["", "   "])
def test_age_from_dob_abstains_silently_on_a_blank_dob(dob, caplog):
    # rev5-001: "" is the DESIGNED DEFAULT of an optional field. Warning on
    # it would warn on every lead of every run for a user who simply declined to
    # declare a DOB -- exactly how a codebase teaches its users to ignore
    # warnings. Two-directional pair to the malformed-case assertion below:
    # without both, nothing distinguishes silent from warning.
    with caplog.at_level(logging.WARNING):
        assert age_from_dob(dob, "2026-06-15") is None
    assert caplog.records == []


@pytest.mark.parametrize("dob", ["15/06/1990", "1990-13-01", "not-a-date"])
def test_age_from_dob_warns_and_abstains_on_an_unparseable_dob(dob, caplog):
    with caplog.at_level(logging.WARNING):
        assert age_from_dob(dob, "2026-06-15") is None
    assert caplog.records, "the abstain must be audible, not silent"


def test_age_from_dob_abstains_on_a_non_string_dob_rather_than_raising():
    # `dob` comes from a user's vault note and must not crash packet-building
    # even when it is not a plain str -- unlike `today` (below), which is a
    # caller-internal value and gets a harder guard.
    assert age_from_dob(None, "2026-06-15") is None


def test_age_from_dob_raises_on_a_non_string_today_naming_today():
    # rev5-002: the clock trap -- core/app.py's Sluice.staleness's
    # `self._today` is a zero-arg CALLABLE. A caller that passes it unresolved
    # must NOT share `dob`'s abstain path: the bug is in sluice's own caller, on
    # every lead, and a silent abstain would point the operator at the user's
    # vault note instead of at the real bug. Assert the DISCRIMINATING MESSAGE,
    # not the type -- date.fromisoformat(dob) below raises the same TypeError,
    # so asserting the type alone would pass whether or not this guard exists.
    with pytest.raises(TypeError, match="today"):
        age_from_dob("1990-06-15", lambda: "2026-06-15")


def test_age_from_dob_abstains_and_warns_on_a_dob_later_than_today(caplog):
    # rev5-003: an impossible date is DECLARED, not undeclared -- the same
    # category as an unparseable dob (which warns), not rev5-001's silent
    # blank abstain. The return value alone cannot distinguish this branch
    # from that silent blank, which is the whole point of the distinction
    # being drawn, so both must be asserted. This also makes a transposed
    # age_from_dob(today, dob) -- two strs, so no type guard can see it --
    # abstain-and-warn rather than silently report a large negative "age".
    with caplog.at_level(logging.WARNING):
        assert age_from_dob("2026-06-15", "1990-06-15") is None
    assert caplog.records, "a declared-but-impossible dob must be audible, not silent"
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "2026-06-15" not in joined
    assert "date_of_birth" in joined


def test_age_from_dob_warning_never_names_the_raw_dob(caplog):
    # A log is a plausible place for a sensitive value to leak into a bug report.
    with caplog.at_level(logging.WARNING):
        age_from_dob("15/06/1990", "2026-06-15")
    assert caplog.records, "the abstain must be audible, not silent"
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "15/06/1990" not in joined
    assert "date_of_birth" in joined


def test_has_any_declared_is_true_for_a_single_non_identity_field():
    # This predicate is what `cmd_init` (cli.py) gates its write AND its existence probe on --
    # the SAME call, `has_any_declared(parse_candidate_profile(...))`, one fed a file read and one
    # fed freshly-rendered text. A user who answers only `email` produces a note that exists and
    # is useful but whose full_name is blank -- a full_name-based probe would re-ask forever and
    # deadlock the interview.
    assert has_any_declared(CandidateProfile()) is False
    assert has_any_declared(CandidateProfile(email="ada@example.invalid")) is True
    assert has_any_declared(CandidateProfile(ethnicity="SYNTHETIC-ETHNICITY-1")) is True
    assert has_any_declared(CandidateProfile(forenames="   ")) is False
