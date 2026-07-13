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
