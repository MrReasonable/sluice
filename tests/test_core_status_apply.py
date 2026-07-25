from sluice.core import status as S
from sluice.core.status import can_apply


def test_can_apply_true_only_for_shortlist():
    assert can_apply("shortlist") is True
    assert can_apply("shortlisted") is True   # alias
    assert can_apply('"shortlist"') is True    # quoted drift


def test_can_apply_false_for_application_owned_and_others():
    for s in ("applied", "phone_screen", "interview", "offer", "rejected",
              "accepted", "withdrawn",
              "new", "research", "needs_review", "dismiss", ""):
        assert can_apply(s) is False


def test_can_transition_routes_applied_through_can_apply():
    # shortlist -> applied is legal (can_apply), which can_advance would reject.
    assert S.can_transition("shortlist", "applied") is True
    assert S.can_advance("shortlist", "applied") is False  # the reason can_transition exists


def test_can_transition_refuses_applied_from_non_shortlist():
    for src in ("interview", "offer", "applied", "rejected", "new"):
        assert S.can_transition(src, "applied") is False


def test_can_transition_delegates_non_applied_to_can_advance():
    # A non-applied target routes to can_advance unchanged.
    assert S.can_transition("applied", "interview") is True
    assert S.can_transition("offer", "phone_screen") is False  # backward on the ladder
