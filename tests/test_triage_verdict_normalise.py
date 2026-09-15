"""`triage/apply.py::normalise_verdict` and `normalise_fields` (#329).

`triage/judge.py::parse_verdicts` checks that a reply is a JSON array and nothing about each
verdict, so every field below arrives however the model chose to spell it. Measured before this
existed: a bare-string list field was joined character by character into the note, and a
wrong-typed field raised out of the engine and ended the whole triage run.
"""
import pytest

from sluice.triage.apply import normalise_fields, normalise_verdict
from tests.conftest import FRAMING_CONCERNS, FRAMING_FLAGS

_BASE = {"lead_id": "example-foundry-analyst", "verdict": "shortlist", "relevance_score": 80,
         "fit_reasoning": "ok", "concerns": [], "culture_flags": [],
         "recommended_next_action": "tailor CV"}


def _with(**over):
    return {**_BASE, **over}


def _without(key):
    return {k: v for k, v in _BASE.items() if k != key}


@pytest.mark.parametrize("field,value,expected", [
    ("relevance_score", "high", 0),
    ("relevance_score", True, 0),          # bool subclasses int; checked first, never scored 1
    ("relevance_score", [80], 0),
    ("relevance_score", "80", 80),         # pin: already accepted before the fix
    ("relevance_score", 80.0, 80),         # pin: already accepted before the fix
    ("relevance_score", float("nan"), 0),  # int() raises ValueError on NaN
    ("relevance_score", float("inf"), 0),  # int() raises OverflowError on Infinity
    ("relevance_score", float("-inf"), 0),
    ("fit_reasoning", 7, ""),
    ("fit_reasoning", ["a"], ""),
    ("recommended_next_action", 5, ""),
    ("concerns", FRAMING_CONCERNS[0], [FRAMING_CONCERNS[0]]),
    ("concerns", [1], []),
    ("concerns", {FRAMING_CONCERNS[0]: 1}, []),
    ("concerns", [FRAMING_CONCERNS[0], 2, None], [FRAMING_CONCERNS[0]]),
    ("culture_flags", [FRAMING_FLAGS[0], 'negative: UN"SAFE'], [FRAMING_FLAGS[0]]),
])
def test_a_wrong_typed_field_is_repaired_and_nothing_else_moves(field, value, expected):
    out, why = normalise_verdict(_with(**{field: value}))
    assert why == ""
    assert out[field] == expected
    assert {k: v for k, v in out.items() if k != field} == _without(field)


@pytest.mark.parametrize("raw,reason", [
    (FRAMING_CONCERNS[0], "not a JSON object"),
    (5, "not a JSON object"),
    (_without("lead_id"), "no usable lead_id"),
    (_with(lead_id=""), "no usable lead_id"),
    (_with(lead_id="   "), "no usable lead_id"),
    (_with(lead_id=5), "no usable lead_id"),
    (_with(lead_id=[1]), "no usable lead_id"),
    (_without("verdict"), "no usable verdict field"),
    (_with(verdict=None), "no usable verdict field"),
    (_with(verdict=5), "no usable verdict field"),
    (_with(verdict=["shortlist"]), "no usable verdict field"),
    (_with(verdict="  "), "no usable verdict field"),
])
def test_an_unusable_verdict_is_rejected_with_its_reason(raw, reason):
    assert normalise_verdict(raw) == (None, reason)


def test_a_verdict_string_outside_the_vocabulary_is_left_for_clamp_verdict():
    # Clamping stays `clamp_verdict`'s job, applied where the status is written. Only a
    # verdict that is not a usable STRING is rejected here.
    out, why = normalise_verdict(_with(verdict="maybe"))
    assert why == "" and out["verdict"] == "maybe"


def test_normalise_fields_does_not_require_a_lead_id():
    out, why = normalise_fields(_without("lead_id"), "some-slug")
    assert why == ""
    assert "lead_id" not in out


def test_a_padded_lead_id_normalises_to_the_stripped_id():
    # #329: the `.strip()` check accepted a padded id, but the UNSTRIPPED value used to ride
    # through to the caller, which then fails to match the id's own note.
    out, why = normalise_verdict(_with(lead_id="  example-foundry-analyst  "))
    assert why == ""
    assert out["lead_id"] == "example-foundry-analyst"
    # The existing idempotence property: a second pass over the stripped output changes
    # nothing.
    second, why2 = normalise_verdict(out)
    assert why2 == "" and second == out


def _apply_records(caplog):
    return [r for r in caplog.records if r.name == "sluice.triage.apply"]


def test_a_second_pass_changes_nothing_and_logs_nothing(caplog):
    with caplog.at_level("WARNING"):
        first, _ = normalise_verdict(_with(concerns=[FRAMING_CONCERNS[0], 2],
                                           relevance_score="high"))
        # Positive control: the first pass DID log, so an empty second pass means something.
        assert _apply_records(caplog), "the first pass logged nothing; this row is vacuous"
        # caplog keeps every record of the test's call phase, the first pass's included.
        caplog.clear()
        second, why = normalise_verdict(first)
    assert why == "" and second == first
    assert _apply_records(caplog) == []


def test_an_unusable_score_is_logged_by_field_and_lead_never_by_value(caplog):
    with caplog.at_level("WARNING"):
        normalise_verdict(_with(relevance_score="UNSAFE-SCORE-TOKEN"))
    said = [r.getMessage() for r in _apply_records(caplog)]
    assert said, "the score drop was not logged"
    assert all("relevance_score" in m and "example-foundry-analyst" in m for m in said)
    assert not any("UNSAFE-SCORE-TOKEN" in m for m in said)


def test_a_dropped_item_is_logged_by_field_and_lead_never_by_value(caplog):
    with caplog.at_level("WARNING"):
        normalise_verdict(_with(concerns=[FRAMING_CONCERNS[0], 'UNSAFE"TOKEN']))
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.triage.apply"]
    assert said, "the dropped item was not logged"
    assert all("concerns" in m and "example-foundry-analyst" in m for m in said)
    assert not any("UNSAFE" in m for m in said)
