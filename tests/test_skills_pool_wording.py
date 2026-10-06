"""D12 (#364/#365/#368): what verifying an evidence note buys is said by its kind's own
flags in all three places a user reads it -- `verify`'s own message, doctor's store row and
the MCP propose result -- and never claims what the kind cannot back."""
import pytest

from sluice.core.app import pending_evidence_detail
from sluice.core.doctor import classify_store
from sluice.core.protocols import EVIDENCE_KINDS, verify_outcome

_FACTS = {"vault_exists": True, "criteria_present": True,
          "candidate_name_present": True, "candidate_contact_present": True,
          **{f"{k}_{n}": 1 for k in EVIDENCE_KINDS for n in ("verified", "total")}}


@pytest.mark.parametrize("kind", sorted(EVIDENCE_KINDS))
def test_each_message_claims_exactly_what_the_kinds_flags_grant(kind):
    spec = EVIDENCE_KINDS[kind]
    label = spec.relpath.rsplit("/", 1)[-1]
    [row] = [r for r in classify_store(_FACTS) if r.subject == label]
    for text in (verify_outcome(spec), row.detail, pending_evidence_detail(kind)):
        assert ("citable" in text) == spec.cited_by_gate, (kind, text)
        assert ("skills list" in text) == spec.names_in_skills_pool, (kind, text)


def test_the_kinds_disagree_on_both_flags_so_the_row_above_can_fail():
    assert {s.cited_by_gate for s in EVIDENCE_KINDS.values()} == {True, False}
    assert {s.names_in_skills_pool for s in EVIDENCE_KINDS.values()} == {True, False}


def test_experience_declares_tools_and_keeps_skills_as_legacy_presence_only():
    spec = EVIDENCE_KINDS["experience"]
    assert spec.fields == ("Company", "Category", "Best For", "Metrics", "Tools")
    assert spec.legacy_fields == ("Skills",)


def test_doctor_no_longer_reports_a_baseline_cv():
    assert not [r for r in classify_store(_FACTS) if "baseline" in r.subject]
