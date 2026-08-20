"""The Candidate Profile note (Task 6): five interview questions in, a frontmatter note out,
verified by re-reading it through the REAL reader before it is ever returned.

The load-bearing property is the round trip, not the render. `core/vault.py`'s `_fm_dict` ends in
`.strip().strip('"').strip("'")` -- EVERY leading and trailing quote character is stripped, not
merely one -- and unescapes NOTHING -- it has no idea what `emit.scalar()` (the same double-quoted,
escape-table emitter `_render_config` already uses for the main config file) may have escaped on
the way out. `full_name` (core/candidate.py) feeds both
`compose()` and the #99/#100 STRUCTURAL header guard in `cv/engine.py`, so a value that is written
one way and read back a DIFFERENT way would compare a corrupted name against itself in that guard,
pass every gate, and ship a wrong name as the PDF's headline. `_render_candidate` refuses instead
of writing: see `FrontmatterRoundTripError`.
"""
import dataclasses

import pytest

from sluice.core.protocols import CandidateProfile
from sluice.core.vault import parse_frontmatter
from sluice.onboard.ask import collect_candidate
from sluice.onboard.plan import FrontmatterRoundTripError, build_plan


class _RecordingAsker:
    """Records every prompt passed to `ask_text_plain` and answers each with "" -- just enough to
    drive `collect_candidate` and inspect what it asked, without asserting on the prompt wording
    itself (that is `tests/onboard_prose.py`'s job, via `ask._CANDIDATE_PROMPTS`)."""

    def __init__(self, sink):
        self._sink = sink

    def ask_text_plain(self, prompt):
        self._sink.append(prompt)
        return ""


def test_the_five_identity_questions_map_onto_frontmatter_keys():
    plan = build_plan({"vault_dir": "/example"}, candidate_answers={
        "cv_forenames": "Ada", "cv_surname": "Example",
        "cv_email": "ada@example.invalid", "cv_mobile": "+44 20 7946 0000",
        "cv_linkedin": "https://example.invalid/in/x"})
    fm = parse_frontmatter(plan.candidate_text)
    assert fm["forenames"] == "Ada"
    assert fm["surname"] == "Example"
    assert fm["email"] == "ada@example.invalid"


def test_all_thirty_six_keys_are_present_even_when_unanswered():
    plan = build_plan({"vault_dir": "/example"}, candidate_answers={"cv_forenames": "Ada"})
    fm = parse_frontmatter(plan.candidate_text)
    known = {f.name for f in dataclasses.fields(CandidateProfile)}
    assert known.issubset(set(fm)), "an unanswered field is present-but-empty, not absent"
    assert fm["surname"] == ""


def test_the_body_carries_prose_and_no_data():
    plan = build_plan({"vault_dir": "/example"}, candidate_answers={"cv_forenames": "Ada"})
    body = plan.candidate_text.split("---", 2)[2]
    assert "Candidate Profile" in body
    assert "Judging Profile" in body       # the backlink
    assert "Ada" not in body               # data lives in frontmatter, never the body


@pytest.mark.parametrize("hostile", [
    "Ada'", '"Ada"', "'Ada'", "Ada\x00Example",
    # Fix round 1 (I1): rendering through emit.scalar() (see _render_candidate's docstring for
    # why bare rendering does not satisfy the NUL case above) WIDENS the refusal set beyond
    # leading/trailing quotes and control characters -- an INTERIOR double quote or backslash now
    # refuses too, because scalar() must escape each one and _fm_dict never undoes that escaping.
    # Neither of these two has a leading/trailing quote or a control character; both are still
    # correctly refused, and the message below has to explain why without naming the wrong cause.
    'Ada "Grace" Example', "Ada\\Example",
    # Fix round 2 (F2): one more member of emit._ESCAPES (five total: \, ", \n, \r, \t) beyond the
    # two above, so this parametrization does not itself read as though `"` and `\` were the whole
    # set -- an interior TAB fails by the identical mechanism (scalar() escapes it to the two
    # literal characters `\t`; _fm_dict reads those back literally rather than restoring a real
    # tab). Not exhaustive on purpose: the guard compares re-parsed against `wanted` for every
    # field, so it already catches every _ESCAPES member whether or not a case names it here.
    "Ada\tExample",
])
def test_a_value_that_does_not_survive_the_round_trip_is_refused_not_written(hostile):
    """_fm_dict ends in .strip().strip('"').strip("'") and unescapes nothing, so a
    lossy round trip corrupts the value and then compares the corrupted value
    against itself in cv/engine.py's #99 guard -- the PDF headline ships wrong with
    every guard green. There is no escaping scheme here: the REAL reader is the
    oracle."""
    with pytest.raises(FrontmatterRoundTripError) as exc:
        build_plan({"vault_dir": "/example"}, candidate_answers={"cv_forenames": hostile})
    assert "forenames" in str(exc.value)
    # Fix round 1 (I1): the message must name what actually offended rather than a fixed sentence
    # ("leading/trailing quotes and control characters are lost") that is false for the two new
    # cases above -- neither has a leading/trailing quote or a control character. Asserting the
    # written value's own repr appears verbatim is what pins that the message shows the user their
    # own input rather than a guess at which characters it thinks are the problem.
    assert repr(hostile) in str(exc.value)


def test_an_ordinary_value_with_an_internal_SINGLE_quote_survives():
    # Only LEADING/TRAILING quote characters are stripped by _fm_dict, so an internal single quote
    # is fine -- refusing it would be over-refusal. NOT true of an internal DOUBLE quote or
    # backslash: scalar() must escape those (see _render_candidate's docstring), and the
    # scalar()-plus-_fm_dict PAIRING refuses them -- see the two new parametrized cases above.
    plan = build_plan({"vault_dir": "/example"},
                      candidate_answers={"cv_forenames": "Ada O'Example"})
    assert parse_frontmatter(plan.candidate_text)["forenames"] == "Ada O'Example"


def test_collect_candidate_asks_exactly_the_five_identity_questions():
    asked = []
    plan_answers = collect_candidate(_RecordingAsker(asked))
    assert len(asked) == 5
    assert set(plan_answers) == {"cv_forenames", "cv_surname", "cv_email",
                                 "cv_mobile", "cv_linkedin"}
