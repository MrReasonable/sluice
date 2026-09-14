"""The composer's framing-only TRIAGE NOTES section (#329): its shape, where it sits, and that an
empty framing leaves the prompt exactly as it was."""
import pytest

from sluice.core.backends import Completion
from sluice.cv import compose as C
from tests.conftest import FRAMING_CONCERNS, FRAMING_FLAGS

_NAME = "Example Candidate"
_FLAGS = ", ".join(FRAMING_FLAGS)
_CONCERNS = "; ".join(FRAMING_CONCERNS)


def _prompt(**kw):
    return C.build_prompt("BUNDLE", "JD", "Co", "Role", name=_NAME, **kw)


@pytest.mark.parametrize("flags,concerns,expected", [
    (_FLAGS, _CONCERNS, (f"culture flags: {_FLAGS}", f"concerns: {_CONCERNS}")),
    (_FLAGS, "", (f"culture flags: {_FLAGS}",)),
    ("", _CONCERNS, (f"concerns: {_CONCERNS}",)),
    ("   ", _CONCERNS, (f"concerns: {_CONCERNS}",)),
    ("", "", ()),
    (None, 5, ()),
])
def test_framing_lines(flags, concerns, expected):
    assert C.framing_lines(flags, concerns) == expected


def test_an_empty_framing_leaves_the_prompt_byte_identical():
    assert _prompt(triage_framing=()) == _prompt()


@pytest.mark.parametrize("skills", [False, True])
def test_framing_adds_exactly_its_rule_and_its_section(skills):
    """Standing, not a one-off measurement: remove the rule's own lines and the section block
    from a framed render, and what is left must be the unframed render, line for line. A
    placeholder that fails to collapse, or a stray blank line, shows up here."""
    framing = C.framing_lines(_FLAGS, _CONCERNS)
    base = _prompt(skills_requested=skills).splitlines()
    remaining = _prompt(skills_requested=skills, triage_framing=framing).splitlines()
    for line in C._TRIAGE_FRAMING_PROMPT_RULE.strip("\n").splitlines():
        remaining.remove(line)
    section = [C._TRIAGE_FRAMING_PROMPT_HEADER, *[f"- {line}" for line in framing], ""]
    start = remaining.index(C._TRIAGE_FRAMING_PROMPT_HEADER)
    assert remaining[start:start + len(section)] == section
    del remaining[start:start + len(section)]
    assert remaining == base


def test_the_section_sits_after_the_jd_and_outside_the_source_bundle():
    p = _prompt(triage_framing=C.framing_lines(_FLAGS, _CONCERNS))
    assert (p.index("=== THE ROLE (JD) ===") < p.index(C._TRIAGE_FRAMING_PROMPT_HEADER)
            < p.index("=== SOURCE BUNDLE"))


def test_compose_forwards_the_framing_into_the_prompt_it_sends():
    # `compose()` passes its arguments to `build_prompt` one by one; a forgotten forward is
    # exactly the shape that leaves the section out of what the backend receives.
    class _Backend:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return Completion("CV")

    be = _Backend()
    C.compose(be, "BUNDLE", "JD", "Co", "Role", name=_NAME,
              triage_framing=C.framing_lines("", _CONCERNS))
    assert C._TRIAGE_FRAMING_PROMPT_HEADER in be.prompts[0]
    assert f"- concerns: {_CONCERNS}" in be.prompts[0]


def test_the_shipped_framing_text_models_nothing_it_forbids():
    shipped = [C._TRIAGE_FRAMING_PROMPT_RULE, C._TRIAGE_FRAMING_PROMPT_HEADER,
               *C._TRIAGE_FRAMING_PROMPT_LABELS]
    assert not any("--" in text for text in shipped), "the CV bans a double hyphen"
    assert not any("auditing" in text.lower() for text in shipped), "the CV test doubles route on it"
    assert "never state, paraphrase or allude to anything in it" in C._TRIAGE_FRAMING_PROMPT_RULE
