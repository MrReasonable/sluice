from sluice.core.status import normalize, can_advance


def test_interviewing_aliases_to_interview():
    assert normalize("interviewing") == "interview"
    assert normalize("Interviewing") == "interview"


def test_can_advance_forward_on_ladder():
    assert can_advance("applied", "phone_screen") is True
    assert can_advance("applied", "interview") is True
    assert can_advance("phone_screen", "offer") is True
    assert can_advance("interviewing", "offer") is True  # alias -> interview < offer


def test_can_advance_to_terminal_from_live():
    for cur in ("applied", "phone_screen", "interview"):
        assert can_advance(cur, "rejected") is True
        assert can_advance(cur, "withdrawn") is True
    assert can_advance("offer", "accepted") is True


def test_can_advance_refuses_backward_terminal_and_triage():
    assert can_advance("interview", "applied") is False       # backward
    assert can_advance("offer", "phone_screen") is False      # backward
    assert can_advance("rejected", "interview") is False      # out of terminal
    assert can_advance("accepted", "offer") is False          # out of terminal
    assert can_advance("shortlist", "applied") is False       # triage-owned source
    assert can_advance("applied", "shortlist") is False       # non-application target
    assert can_advance("applied", "applied") is False         # not a forward move
