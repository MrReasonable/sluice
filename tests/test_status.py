import pytest
from sluice.core import status


@pytest.mark.parametrize("raw,expected", [
    ('"new"', "new"), ("new", "new"), ("New", "new"),
    ("dismissed", "dismiss"), ('"dismiss"', "dismiss"),
    ("Researching", "research"), ("shortlisted", "shortlist"),
    ("needs_review", "needs_review"), ("phone screen", "phone_screen"),
    ("  applied  ", "applied"), ("weird_unknown", "weird_unknown"),
])
def test_normalize_maps_drift_and_passes_unknown(raw, expected):
    assert status.normalize(raw) == expected


def test_ownership_partitions():
    assert status.is_application_owned("applied") is True
    assert status.is_application_owned("dismissed") is False   # triage-owned
    assert status.is_application_owned("new") is False
    assert status.is_canonical("dismiss") is True
    assert status.is_canonical("weird_unknown") is False
    # the two sets are disjoint and both live in CANONICAL
    assert not (set(status.TRIAGE_OWNED) & set(status.APPLICATION_OWNED))
    assert set(status.TRIAGE_OWNED) | set(status.APPLICATION_OWNED) == status.CANONICAL
