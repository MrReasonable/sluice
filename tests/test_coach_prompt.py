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
