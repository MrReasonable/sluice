"""core/followups.py (#378): the answers-section grammar, the one awaiting predicate and the
queue the worklist and the triage digest both read."""
from datetime import date, timedelta

import pytest

from sluice.core import followups
from sluice.core.protocols import LeadNote

# A date the host clock cannot produce, so a function reading the real clock instead of the
# `today` it is handed goes red on whatever day the suite runs (tst-001).
TODAY = "2031-03-14"


def _ago(days):
    return (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()


def _note(slug, fm, body=""):
    return LeadNote(ref=slug, slug=slug, fm=fm, body=body, status=fm.get("status", ""))


def test_a_rendered_answer_reads_back():
    body = "# Lead\n\n" + followups.render_answer("Client is Example Ltd", TODAY) + "\n"
    assert followups.answers(body) == f"{TODAY}: Client is Example Ltd"


def test_several_answers_concatenate_in_body_order():
    body = ("# Lead\n\n" + followups.render_answer("first", _ago(11)) + "\n\n## Notes\nmine\n\n"
            + followups.render_answer("second", TODAY) + "\n")
    assert followups.answers(body) == f"{_ago(11)}: first\n\n{TODAY}: second"


def test_an_unclosed_fence_in_one_answer_does_not_swallow_what_follows():
    # inv-002. An open fence runs to the end of the body, so the next heading would read as
    # code: the later answer and the user's own `## Notes` would be folded into this answer's
    # text, and the notes shown to the judge as a reply. Asserted as SECTIONS, not by
    # substring: a swallowed answer is still a substring of the section that swallowed it.
    # Witness: `neutralise` returning `out` without the closing fence.
    body = ("# Lead\n\n" + followups.render_answer("pasted\n```", _ago(1)) + "\n\n"
            + followups.render_answer("SENTINEL-SECOND", TODAY) + "\n\n## Notes\nmine\n")
    assert followups.answers(body) == (f"{_ago(1)}: pasted\n```\n```\n\n"
                                       f"{TODAY}: SENTINEL-SECOND")


def test_tag_lines_are_dropped_and_a_hand_typed_section_reads():
    body = "## Follow-up answers\nSENTINEL hand-typed answer\n"
    assert followups.answers(body) == "SENTINEL hand-typed answer"
    assert "sluice:followup-answer" not in followups.answers(
        followups.render_answer("x", TODAY))


def test_an_answer_that_opens_with_a_fence_does_not_swallow_what_follows():
    # Final review / inv-001: the date prefix sits on the answer's first line, so a fence there
    # is no fence once dated, and the text's own closing fence OPENS one instead. Neutralising
    # the dated line rather than the bare text is what keeps the count right. A pasted email
    # wrapped in a code block is the realistic shape. Witness: neutralise the bare text.
    body = ("# Lead\n\n" + followups.render_answer("```\nquoted email\n```", _ago(1))
            + "\n\n" + followups.render_answer("SENTINEL-SECOND", TODAY)
            + "\n\n## Notes\nmine\n")
    assert followups.answers(body) == (f"{_ago(1)}: ```\nquoted email\n```\n```\n\n"
                                       f"{TODAY}: SENTINEL-SECOND")


def test_a_pasted_heading_is_demoted_so_the_whole_answer_is_read():
    text = "line one\n## Their notes\nline three"
    out = followups.answers(followups.render_answer(text, TODAY))
    assert out == f"{TODAY}: line one\n### Their notes\nline three"


def test_a_heading_with_trailing_text_is_not_the_section():
    assert followups.answers("## Follow-up answers:\nx\n") == ""


def test_the_tag_covers_date_and_text():
    assert followups.answer_tag("no", _ago(11)) != followups.answer_tag("no", TODAY)
    assert followups.answer_tag("no", TODAY) == followups.answer_tag("no", TODAY)
    assert followups.answer_tag("no", TODAY) != followups.answer_tag("yes", TODAY)


@pytest.mark.parametrize("fm, expected", [
    ({"status": "research", "followup_asked": TODAY}, True),
    ({"status": "Research", "followup_asked": TODAY}, True),     # Review Focus 2
    ({"status": "research", "followup_asked": "yes"}, True),     # Review Focus 1
    ({"status": "research", "followup_asked": ""}, False),
    ({"status": "research", "followup_asked": "   "}, False),
    ({"status": "research"}, False),
    ({"status": "new", "followup_asked": TODAY}, False),
    ({"status": "shortlist", "followup_asked": TODAY}, False),
])
def test_awaiting_reply(fm, expected):
    assert followups.awaiting_reply(fm) is expected


def test_build_queue_groups_ages_and_orders_newest_first():
    notes = [
        _note("Example Alpha", {"status": "research", "first_seen": _ago(41),
                      "triage_next": "ask recruiter: client name"}),
        _note("Example Beta", {"status": "research", "first_seen": _ago(2), "triage_next": ""}),
        _note("Example Gamma", {"status": "research", "first_seen": _ago(11),
                        "followup_asked": _ago(7), "triage_next": "ask: band"}),
        _note("Example Northgate", {"status": "research", "first_seen": "nope", "followup_asked": "yes"}),
        _note("Example Tidal", {"status": "shortlist", "first_seen": _ago(11),
                        "followup_asked": _ago(7)}),
    ]
    q = followups.build_queue(notes, TODAY)
    assert [r.slug for r in q.never_asked] == ["Example Beta", "Example Alpha"]
    assert [r.slug for r in q.asked] == ["Example Gamma", "Example Northgate"]
    rows = {r.slug: r for r in q.rows}
    assert rows["Example Alpha"].age_days == 41 and rows["Example Alpha"].next_action == "ask recruiter: client name"
    assert rows["Example Gamma"].waited_days == 7
    assert rows["Example Northgate"].age_days is None and rows["Example Northgate"].waited_days is None
    assert "Example Tidal" not in rows
    assert q.oldest_days == 41


def test_an_empty_queue_has_no_oldest():
    q = followups.build_queue([], TODAY)
    assert q.rows == () and q.oldest_days is None


def test_module_is_pure():
    src = open(followups.__file__, encoding="utf-8").read()
    # The clock is a parameter: a real-clock read here would make every age depend on the
    # host date, which the pinned TODAY above exists to catch.
    for banned in ("import os", "open(", "import sys", "date.today", "datetime.now"):
        assert banned not in src
