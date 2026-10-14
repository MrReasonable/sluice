"""#302: the `triage_blocked` stamp -- what counts as one, where triage writes and clears it,
and how the engine counts a refused `unjudgeable` verdict on a `research` lead."""
import pytest

from sluice.core.leads import blocked_stamp


@pytest.mark.parametrize("value,expected", [
    ("2031-03-14", "2031-03-14"),
    (" 2031-03-14 ", " 2031-03-14 "),   # the RAW value: require_unchanged compares it as stored
    ("", ""),
    ("blocked", ""),                     # a hand-typed word is the person's own text
    ("|", ""),                           # a block scalar reads as its indicator, not blank
    ("2031-03-14\n", ""),                # never a multi-line value, even one strip() would pass
    ("2031-13-40", ""),
])
def test_blocked_stamp_accepts_only_a_one_line_iso_date(value, expected):
    assert blocked_stamp({"triage_blocked": value}) == expected


def test_blocked_stamp_of_a_note_without_the_key_is_blank():
    assert blocked_stamp({}) == ""
    assert blocked_stamp({"triage_blocked": None}) == ""
