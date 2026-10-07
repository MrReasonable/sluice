"""The career coach's prompt (sluice/onboard/coach/): neutrality, completeness, and the rules.

The playbooks are shipped prose a model reads as instructions, so they carry the same
no-opinion rule as every other string sluice ships: no role, sector, seniority or employer,
and no example list of either. Two vocabularies apply, each matched the way its own guard
matches it -- `questions.NO_TAXONOMY_WORDS` on word boundaries, the judge prompt's hoisted
list as lowercase substrings. Both are smoke tests: no vocabulary enumerates every example,
which is why the example-introducing phrasings are banned outright below.

The planted-word row is the standing witness that the sweep can fire at all: it drives the
SAME assembler the server registers, swapping only its playbook source.
"""
from importlib import resources

import pytest

from sluice.onboard import coach
from sluice.onboard.questions import expresses_a_preference
from tests.test_prompt import FORBIDDEN_ROLE_AND_CULTURE_TERMS


def _leaks(text):
    low = text.lower()
    return expresses_a_preference(text) + [t for t in FORBIDDEN_ROLE_AND_CULTURE_TERMS if t in low]


@pytest.mark.parametrize("focus", ["", "a change of direction"])
@pytest.mark.parametrize("write", [True, False])
def test_the_assembled_prompt_names_no_preference(focus, write):
    assert _leaks(coach.assemble_prompt(focus, write=write)) == []


def test_every_packaged_playbook_is_found_and_used():
    files = {p.name[:-3] for p in resources.files("sluice.onboard.coach").iterdir()
             if p.name.endswith(".md")}
    assert files and files == set(coach.PLAYBOOKS)
    full = coach.assemble_prompt()
    assert all(coach.read_playbook(n).strip()[:80] in full for n in coach.PLAYBOOKS)


def test_the_sweep_reports_a_planted_role_word():
    def planted(name):
        return coach.read_playbook(name) + ("\nConsider a scrum master role.\n"
                                            if name == "discovery" else "")
    assert _leaks(coach.assemble_prompt(read=planted))


def test_the_prompt_states_the_rules_and_names_every_unit_kind():
    text = coach.assemble_prompt()
    for phrase in ("never the answers", "ticks it", "setup_status", "setup_review"):
        assert phrase in text
    from sluice.onboard.review import ROLE_BRIEF_SECTIONS
    from sluice.onboard.plan import PROFILE_HEADINGS
    from sluice.onboard.questions import catalogue
    for name in [q.key for q in catalogue()] + list(ROLE_BRIEF_SECTIONS) + [
            h.lstrip("#").strip() for h in PROFILE_HEADINGS]:
        assert name in text


# An example list is an opinion about what is typical, which no vocabulary can enumerate;
# the playbooks may not use the phrasing that introduces one.
_EXAMPLE_MARKERS = ("e.g.", "for example", "for instance", "such as", "say, a", "like a ")


@pytest.mark.parametrize("name", coach.PLAYBOOKS)
def test_no_playbook_introduces_an_example_list(name):
    low = coach.read_playbook(name).lower()
    assert [m for m in _EXAMPLE_MARKERS if m in low] == []


def test_registered_descriptions_name_no_preference():
    import asyncio
    from sluice.core.config import Config
    from sluice.mcpserver import build_server
    server = build_server(Config(), write=True)
    tools = asyncio.run(server.list_tools())
    prompts = asyncio.run(server.list_prompts())
    texts = [t.description for t in tools if t.name.startswith("setup_")]
    texts += [p.description for p in prompts] + [a.description for p in prompts
                                                 for a in (p.arguments or [])
                                                 if a.description]
    assert len(texts) >= 3 and all(_leaks(t) == [] for t in texts)


def test_a_read_only_server_says_to_restart_with_write():
    assert "--write" in coach.assemble_prompt(write=False)
    assert "--write" not in coach.assemble_prompt(write=True)


def test_every_roster_backend_states_its_requirement():
    # Eval run 3: the coach mapped a user's chat subscription to the per-token `anthropic`
    # backend by brand. Each backend in the DEFAULT_MODELS roster gets a requirement line: a
    # per-token one checked against the credential map its factory reads, a key-less one
    # stating its own (an unstated requirement read as "needs nothing" and steered to it).
    from sluice.core import app
    from sluice.core.backends import DEFAULT_MODELS
    text = coach.assemble_prompt()
    keyed = [b for b in DEFAULT_MODELS if b in app._PROVIDER_ENV]
    keyless = [b for b in DEFAULT_MODELS if b not in app._PROVIDER_ENV]
    assert keyed and keyless  # both kinds present, or half of this checks nothing
    for b in keyed:
        var = app._PROVIDER_ENV[b][0]
        assert f"- `{b}`: an API key in the `{var}` environment variable" in text
    lines = {ln.split("`")[1]: ln for ln in text.splitlines() if ln.startswith("- `")}
    for b in keyless:
        assert b in lines and "API key" not in lines[b].split(";", 1)[-1]
        assert len(lines[b]) > len(f"- `{b}`: no API key.")  # says what it DOES need
    assert coach.NO_REQUIREMENT_STATED not in text
    assert "a subscription to a chat service does not include one" in text


def test_a_keyless_backend_without_a_stated_requirement_is_visible(monkeypatch):
    # The guard above fires on the fallback text; this pins that a key-less backend whose
    # factory carries no `requirement` produces exactly that fallback rather than "no API key".
    from sluice.core import app, plugins
    from sluice.core.backends import DEFAULT_MODELS
    import sluice.backends  # noqa: F401
    keyless = next(b for b in sorted(DEFAULT_MODELS) if b not in app._PROVIDER_ENV)
    monkeypatch.setitem(plugins._REGISTRY["backend"], keyless, lambda *a, **k: None)
    assert (keyless, coach.NO_REQUIREMENT_STATED) in coach._backend_requirements()


def test_the_interview_comes_before_the_settings():
    text = coach.read_playbook("interview")
    assert text.index("## Part 1: The interview") < text.index("## Part 2: The settings")


def test_the_form_is_never_held_for_a_change_still_to_come():
    # Eval run 4: every value was agreed by coach message 10, then the coach held the whole form
    # for search addresses the user had yet to fetch and the cap ran out with nothing sent.
    assert "Never hold the form for a change that is optional" in coach.read_playbook("interview")
    assert "Do not wait for a change that is optional" in coach.read_playbook("review")
    assert "what is still to come and how to add it" in coach.read_playbook("handoff")


def test_the_interview_probes_gaps_names_tensions_and_does_not_push():
    # Cross-family grading of runs 2-5: gaps were noted but not probed, a contradiction in the
    # user's own goals went unnamed, and research leaned on a different kind of employer.
    iv, rs = coach.read_playbook("interview"), coach.read_playbook("research")
    assert "**Probe a gap; do not only note it.**" in iv
    assert "**Name a tension.**" in iv
    assert "A probe or a named tension is a question, never a verdict." in iv
    assert "Match the research to what the user described" in rs


def test_the_review_playbook_describes_how_a_form_is_filled():
    # Run 5's forms showed 3 of 17 changes; the rest came back not_shown.
    text = coach.read_playbook("review")
    assert "in the order the user asked for them" in text and "`not_shown`" in text
    assert "A `not_shown` change is not a declined one" in text
    # Run 6: approved Role Brief text was rewritten unseen, and a requested smaller form was not
    # sent; both are now stated rules.
    assert "Send exactly the text the user approved." in text
    assert "Do what the user asks about the form, and what you told them you would do." in text


def test_misattributions_are_corrected_and_the_profile_is_drafted_in_one_pass():
    # Run 7: a user credited the coach with advice it never gave and the claim stood; the
    # profile was drafted across three messages; the coach promised "one at a time" and
    # asked several.
    text = coach.assemble_prompt()
    assert "Advice nobody gave must not stand." in text
    assert "draft every section the interview covered in one message" in text
    assert "one at a time" not in text
