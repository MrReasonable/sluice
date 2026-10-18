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


# Both prompts the package serves, each at both privilege levels. This test's NAME is what
# tests/test_renderer_template.py's `covered` mapping certifies as the sweep over the shipped
# `onboard/coach/*.md` files, so the prep prompt is swept HERE rather than beside it: a sibling
# would leave the named sweep never reading rehearse.md.
@pytest.mark.parametrize("assembler", ["assemble_prompt", "assemble_prep_prompt"])
@pytest.mark.parametrize("write", [True, False])
def test_the_assembled_prompt_names_no_preference(assembler, write):
    assert _leaks(getattr(coach, assembler)(write=write)) == []


def test_every_packaged_playbook_is_found_and_used():
    files = {p.name[:-3] for p in resources.files("sluice.onboard.coach").iterdir()
             if p.name.endswith(".md")}
    assert files and files == set(coach.PLAYBOOKS) | set(coach.PREP_PLAYBOOKS)
    full = coach.assemble_prompt()
    assert all(coach.read_playbook(n).strip()[:80] in full for n in coach.PLAYBOOKS)
    # Each prompt carries ITS OWN roster: the prep playbooks reach the prep prompt at both
    # levels and never the setup coach's.
    for write in (True, False):
        prep = coach.assemble_prep_prompt(write=write)
        assert all(coach.read_playbook(n).strip()[:80] in prep for n in coach.PREP_PLAYBOOKS)
    assert not [n for n in coach.PREP_PLAYBOOKS if coach.read_playbook(n).strip()[:80] in full]


@pytest.mark.parametrize("write", [True, False])
def test_the_prep_prompt_carries_no_setup_playbook(write):
    # The other direction of "each prompt carries its own roster": a prep prompt assembled from
    # PLAYBOOKS + PREP_PLAYBOOKS would hand the prep coach every setup rule, setup_save's included.
    prep = coach.assemble_prep_prompt(write=write)
    assert not [n for n in coach.PLAYBOOKS if coach.read_playbook(n).strip()[:80] in prep]


def test_the_sweep_reports_a_planted_role_word():
    def planted(name):
        return coach.read_playbook(name) + ("\nConsider a scrum master role.\n"
                                            if name == "discovery" else "")
    assert _leaks(coach.assemble_prompt(read=planted))


@pytest.mark.parametrize("write", [True, False])
def test_the_sweep_reports_a_planted_role_word_in_the_prep_prompt(write):
    # The prep twin: "discovery" is not a prep playbook, so the plant goes into "rehearse".
    def planted(name):
        return coach.read_playbook(name) + ("\nConsider a scrum master role.\n"
                                            if name == "rehearse" else "")
    assert _leaks(coach.assemble_prep_prompt(write=write, read=planted))


def test_the_prompt_states_the_rules_and_names_every_unit_kind():
    text = coach.assemble_prompt()
    # The two settings-pacing phrases: a real session showed the coach asking two groups in one
    # message and labelling them with the playbook's own numbers ("settings 3 and 4").
    for phrase in ("never the answers", "setup_status", "setup_save",
                   "one group per message", "never show them to the user"):
        assert phrase in text
    from sluice.onboard.review import ROLE_BRIEF_SECTIONS
    from sluice.onboard.plan import PROFILE_HEADINGS
    from sluice.onboard.questions import catalogue
    for name in [q.key for q in catalogue()] + list(ROLE_BRIEF_SECTIONS) + [
            h.lstrip("#").strip() for h in PROFILE_HEADINGS]:
        assert name in text


# An example list is an opinion about what is typical, which no vocabulary can enumerate;
# the playbooks may not use the phrasing that introduces one.
_EXAMPLE_MARKERS = ("e.g.", "for example", "for instance", "such as", "say, a", "like a ",
                    "etc.", "and so on")

# The text-only sweeps read every playbook the package ships, from both rosters.
_ALL_PLAYBOOKS = coach.PLAYBOOKS + coach.PREP_PLAYBOOKS


def test_the_text_only_sweeps_reach_both_rosters():
    # Scope for the two text-only sweeps: a parametrize list that lost the prep roster would
    # leave both green without ever reading rehearse.md.
    assert "rehearse" in _ALL_PLAYBOOKS


@pytest.mark.parametrize("name", _ALL_PLAYBOOKS)
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
    # Scope of the original population, asserted on its own: the setup tools by name and a
    # non-empty set of described prompts. The by-name prep texts appended below would otherwise
    # satisfy any count over the combined list, so a `setup_` filter that matched nothing, or a
    # server that registered no prompt, would leave this sweep green over none of the texts it
    # exists for.
    setup = {t.name: t.description for t in tools if t.name.startswith("setup_")}
    assert set(setup) == {"setup_status", "setup_save"} and all(setup.values()), setup
    assert prompts and all(p.description for p in prompts), [p.name for p in prompts]
    # Both prompts' registered descriptions, by name: the interview_prep description is swept
    # because it is among these, so a roster that lost it must go red here, not pass quietly.
    assert {"career_interview", "interview_prep"} <= {p.name for p in prompts}
    texts = list(setup.values())
    texts += [p.description for p in prompts] + [a.description for p in prompts
                                                 for a in (p.arguments or [])
                                                 if a.description]
    # #195: get_lead's interview-prep sentence, by name. It is a returned content_warning
    # rather than a registered description, but it is shipped prose an agent reads beside the
    # coach's, so it carries the same no-opinion rule. Scope: the named text IS the sentence
    # get_lead returns, so the sweep cannot pass over a stale copy.
    from sluice.core.prepbrief import TAGGED_HEADING
    from sluice.mcpserver import _GET_LEAD_CONTENT_WARNING, _GET_LEAD_PREP_BRIEF_NOTE
    assert TAGGED_HEADING in _GET_LEAD_PREP_BRIEF_NOTE
    assert _GET_LEAD_PREP_BRIEF_NOTE in _GET_LEAD_CONTENT_WARNING
    texts.append(_GET_LEAD_PREP_BRIEF_NOTE)
    # #195: the prep_brief tool's registered description and the four warnings its replies
    # carry, each by name. Scope: each is found and non-empty, so a renamed tool or a constant
    # emptied out cannot leave this sweep passing over nothing.
    from sluice import mcpserver
    described = {t.name: t.description for t in tools}
    prep = {"prep_brief": described.get("prep_brief") or "",
            **{name: getattr(mcpserver, name, "") for name in (
                "_PREP_JD_WARNING", "_PREP_LEAD_WARNING", "_PREP_ENTRIES_WARNING",
                "_PREP_SUGGESTIONS_WARNING")}}
    assert all(prep.values()), [name for name, text in prep.items() if not text]
    texts += list(prep.values())
    assert [t for t in texts if _leaks(t)] == []


def test_a_read_only_server_says_to_restart_with_write():
    assert "--write" in coach.assemble_prompt(write=False)
    assert "--write" not in coach.assemble_prompt(write=True)


def test_the_read_only_prep_prompt_gives_the_stories_add_command():
    # A read-only server registers no propose_evidence, so naming it would send the coach after
    # a tool that is not there. The user gets the command to add the story themselves instead.
    text = coach.assemble_prep_prompt(write=False)
    assert "propose_evidence" not in text
    assert "job-sluice stories add" in text and coach.PREP_READ_ONLY_NOTE in text
    assert coach.PREP_WRITE_NOTE not in text


def test_the_write_level_prep_prompt_offers_propose_evidence_and_says_where_it_lands():
    # At --write the coach may queue a story, and must say what that does and does not do:
    # proposed in the user's own words, it lands in the inbox, and it reaches a brief only once
    # the user verifies it. Each clause is pinned, so a note that only said "save it with
    # propose_evidence" goes red.
    text = coach.assemble_prep_prompt(write=True)
    assert "propose_evidence" in text and coach.PREP_WRITE_NOTE in text
    assert coach.PREP_READ_ONLY_NOTE not in text
    for clause in ("in the user's own words", "`_inbox/`", "`job-sluice stories verify`"):
        assert clause in coach.PREP_WRITE_NOTE, clause


def test_both_prep_notes_capture_a_story_not_in_the_entries_never_judging_its_worth():
    # The coach holds no opinion about answer content, so what makes a story one to capture is
    # only that the user's entries do not hold it, at either level: a trigger on a story "worth
    # keeping" would have the read-only coach decide which of the user's stories matter.
    for note in (coach.PREP_READ_ONLY_NOTE, coach.PREP_WRITE_NOTE):
        assert "a story that is not in their entries" in note, note
        assert "worth" not in note.lower(), note


def test_the_rehearse_playbook_names_neither_capture_tool():
    # The level-specific capture rule lives in the two constants, never in the playbook, which
    # both levels share: a playbook naming propose_evidence would serve it to a read-only coach.
    text = coach.read_playbook("rehearse")
    assert "propose_evidence" not in text and "stories add" not in text


@pytest.mark.parametrize("write", [True, False])
def test_the_prep_prompt_reads_the_brief_and_finds_the_slug_with_list_leads(write):
    text = coach.assemble_prep_prompt(write=write)
    assert "`prep_brief`" in text and "`list_leads`" in text


def test_the_rehearse_playbook_runs_its_phases_in_order_each_with_an_exit():
    # Spec section 9's six phases, in the session's order, each closed by its own exit criterion.
    text = coach.read_playbook("rehearse")
    phases = ["## Phase 1: Choose the interview", "## Phase 2: Read the brief",
              "## Phase 3: How the interview will run", "## Phase 4: The gaps",
              "## Phase 5: Rehearse", "## Phase 6: Keep what was new"]
    at = [text.index(p) for p in phases]
    assert at == sorted(at), "the phases are out of order"
    sections = text.split("\n## Phase ")[1:]
    assert len(sections) == len(phases)
    assert all("\n## Done when\n" in s for s in sections), "a phase has no exit criterion"


def test_the_rehearse_playbook_states_its_rules():
    # The neutrality rules a rehearsal coach is most likely to break: inferring the format,
    # supplying an example for a gap, judging the content of an answer, and treating an entry the
    # user has not reviewed as settled.
    text = coach.read_playbook("rehearse")
    for phrase in ("Ask it as an open question",
                   "Never supply an example",
                   "only on two things",
                   "never on whether you would have chosen it",
                   "`verified: false`",
                   "never treat it as settled",
                   "an acknowledged gap"):
        assert phrase in text, phrase


@pytest.mark.parametrize("call,loads", [
    ("c.assemble_prep_prompt(write=True); c.assemble_prep_prompt(write=False)", False),
    # The control: the setup prompt DOES load them (its units list each backend's requirement),
    # so the probe can see a backend import when one happens.
    ("c.assemble_prompt()", True),
])
def test_the_prep_prompt_imports_no_backend(call, loads):
    import importlib.util
    import os
    import pathlib
    import subprocess
    import sys
    # `sluice.core.app` is watched in its own right: the coach docstring and docs/ARCHITECTURE.md
    # say building `interview_prep`'s prompt imports neither core/app.py nor the backends, and
    # importing core/app.py loads neither backend module, so watching only those two would leave
    # an app import in assemble_prep_prompt green.
    watched = ("sluice.core.app", "sluice.core.backends", "sluice.backends")
    assert all(importlib.util.find_spec(m) for m in watched), watched
    # `-c` puts the CURRENT DIRECTORY first on the child's sys.path, ahead of PYTHONPATH, so
    # the child runs from this tree's root and asserts that it imported this tree's coach:
    # started from another checkout, it would otherwise measure that checkout's copy.
    root = str(pathlib.Path(coach.__file__).resolve().parents[3])
    probe = (f"import os, sys, sluice.onboard.coach as c; "
             f"assert os.path.realpath(c.__file__).startswith({root + os.sep!r}), c.__file__; "
             f"{call}; "
             f"print(sorted(m for m in {watched!r} if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         check=True, cwd=root).stdout.strip()
    assert (out != "[]") is loads, f"{call} loaded {out}"


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


def test_the_save_is_never_held_for_a_change_still_to_come():
    # Eval run 4: every value was agreed by coach message 10, then the coach held everything
    # for search addresses the user had yet to fetch and the cap ran out with nothing saved.
    assert "Never hold the save for a change that is optional" in coach.read_playbook("interview")
    assert "Do not hold the save for a change that is optional" in coach.read_playbook("review")
    assert "what is still to come and how to add it" in coach.read_playbook("handoff")


def test_the_review_plays_back_everything_and_saves_only_on_an_explicit_yes():
    # Spec 2026-10-08, The flow: a complete playback grouped by destination, replaced values
    # shown, and a save only on an explicit yes; a change asked for afterwards is played back
    # again.
    text = coach.read_playbook("review")
    for group in ("Your sluice settings", "The Judging Profile", "The Candidate Profile",
                  "The Role Brief", "Searches"):
        assert f"**{group}.**" in text, group
    assert "show both: what is there now" in text
    assert "what leaving it empty means" in text
    assert "Save only when they say yes to that playback." in text
    assert 'A shrug, a maybe, "you decide" or silence is not a yes' in text
    assert "play back again what changed before saving" in text
    assert "**Save only on their explicit yes.**" in coach.read_playbook("persona")


def test_the_review_walks_the_user_through_obsidian_and_invites_corrections():
    text = coach.read_playbook("review")
    steps = ["Install Obsidian", '"Open folder as vault"', "`job-sluice doctor`",
             "`Job Applications/`"]
    at = [text.index(s) for s in steps]
    assert at == sorted(at), "the walkthrough's steps are out of order"
    assert "Invite corrections now and in any later session." in text
    # gen-002: doctor resolves the vault from the terminal's own environment, so the coach
    # names the folder the user agreed to, and qualifies doctor by the server's VAULT_DIR.
    assert "it is the `vault_dir` they agreed to" in text
    assert "the same `VAULT_DIR` the sluice server was registered with" in text
    assert "send its `previous` as the new value" in text
    assert "came back with `not_restorable` cannot be put back through `setup_save`" in text
    assert "`clear: true`" in text and "Do this only when they ask." in text


def test_a_stale_save_is_reread_and_played_back_before_saving_again():
    text = coach.read_playbook("review")
    assert "Call `setup_status` again, play back anything that differs" in text
    assert "keep the `version` it returns" in text


def test_no_playbook_mentions_the_retired_form():
    """The per-change setup form is gone (spec 2026-10-08). The one form left is
    verify_evidence's, which the hand-off names for evidence only."""
    import re
    seen, visited = 0, set()
    for name in _ALL_PLAYBOOKS:
        visited.add(name)
        low = coach.read_playbook(name).lower()
        for word in ("tick", "checkbox", "not_shown", "review form", "unticked"):
            assert word not in low, (name, word)
        forms = [m.start() for m in re.finditer(r"\bforms?\b", low)]
        seen += len(forms)
        assert all("evidence" in low[max(0, i - 200):i] for i in forms), (name, forms)
    # Scope: the hand-off's evidence form is the one mention that must be there, so a matcher
    # that found nothing at all would be the broken one, not a clean sweep. And the loop itself
    # read the prep roster's playbook, not only the constant it iterates.
    assert seen >= 1
    assert "rehearse" in visited


def test_the_interview_probes_gaps_names_tensions_and_does_not_push():
    # Cross-family grading of runs 2-5: gaps were noted but not probed, a contradiction in the
    # user's own goals went unnamed, and research leaned on a different kind of employer.
    iv, rs = coach.read_playbook("interview"), coach.read_playbook("research")
    assert "**Probe a gap; do not only note it.**" in iv
    assert "**Name a tension.**" in iv
    assert "A probe or a named tension is a question, never a verdict." in iv
    assert "Match the research to what the user described" in rs


def test_the_review_playbook_keeps_the_approved_text_and_the_users_requests():
    text = coach.read_playbook("review")
    # Run 6: approved Role Brief text was rewritten unseen, and a requested ordering was not
    # followed; both are stated rules.
    assert "Play back exactly the text you will save." in text
    assert "Do what the user asks about the save, and what you told them you would do." in text


def test_misattributions_are_corrected_and_the_profile_is_drafted_in_one_pass():
    # Run 7: a user credited the coach with advice it never gave and the claim stood; the
    # profile was drafted across three messages; the coach promised "one at a time" and
    # asked several.
    text = coach.assemble_prompt()
    assert "Advice nobody gave must not stand." in text
    assert "draft every section the interview covered in one message" in text
    assert "one at a time" not in text


def _setup_outcome_vocabulary():
    """(save outcomes, per-change outcomes), DERIVED from the code that produces them, so a new
    outcome cannot ship without the playbook naming it. Save: every string literal assigned to
    `report["outcome"]` in `setup_save_step` or `_fresh_or_refusal`, or put under "outcome" in
    one of their dict literals that is not a per-change row. Per change: every "outcome" of a
    row literal (a dict carrying "change") and every value `_SAVE_OUTCOME` maps apply_setup's
    statuses to -- and every status apply_setup can produce must be one of its keys, or a new
    one would reach the save as a KeyError rather than a named outcome."""
    import ast
    import inspect

    from sluice import mcpserver
    from sluice.core import app

    def consts(node):
        """The strings an expression can EVALUATE to: an IfExp's branches, never its test."""
        if isinstance(node, ast.IfExp):
            return consts(node.body) | consts(node.orelse)
        return {node.value} if isinstance(node, ast.Constant) and isinstance(node.value,
                                                                              str) else set()

    step, changes = set(), set(mcpserver._SAVE_OUTCOME.values())
    for fn in (mcpserver.setup_save_step, mcpserver._fresh_or_refusal):
        for node in ast.walk(ast.parse(inspect.getsource(fn).lstrip())):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript):
                tgt = node.targets[0]
                if isinstance(tgt.slice, ast.Constant) and tgt.slice.value == "outcome":
                    step |= consts(node.value)
            elif isinstance(node, ast.Dict):
                pairs = {k.value: v for k, v in zip(node.keys, node.values)
                         if isinstance(k, ast.Constant)}
                if "outcome" in pairs:
                    (changes if "change" in pairs else step).update(consts(pairs["outcome"]))
    statuses = set()
    for fn in (app.Sluice.apply_setup, app.Sluice._apply_config):
        for node in ast.walk(ast.parse(inspect.getsource(fn).lstrip())):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") in
                    ("ArtefactOutcome", "outcome") and node.args):
                statuses |= consts(node.args[0])
    assert statuses and statuses <= set(mcpserver._SAVE_OUTCOME), statuses
    return step - {""}, changes


def test_the_review_playbook_names_every_outcome_the_save_returns():
    step, changes = _setup_outcome_vocabulary()
    # Scope: a derivation that found nothing would pass every assertion below.
    assert {"completed", "stale", "config_refused"} <= step
    assert changes == {"written", "set_aside", "failed"}
    text = (resources.files(coach) / "review.md").read_text(encoding="utf-8")
    missing = sorted(o for o in step | changes if f"`{o}`" not in text)
    assert missing == [], f"review.md does not name these setup outcomes: {missing}"


def test_usage_names_every_outcome_setup_save_returns():
    """docs/USAGE.md's setup entry once listed `restart_needed` -- a report FIELD -- as an
    outcome, and left out outcomes the step really returns. Derived from the same vocabulary
    the playbook row above reads, and scoped to that one entry, so another tool's outcome
    named elsewhere in the file cannot satisfy it."""
    import pathlib
    step, changes = _setup_outcome_vocabulary()
    usage = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "USAGE.md").read_text(
        encoding="utf-8")
    start = usage.index("- `setup_save(changes, version)`")
    entry = usage[start:usage.index("\n\n- ", start)]
    missing = sorted(o for o in step | changes if f"`{o}`" not in entry)
    assert missing == [], f"USAGE.md's setup_save entry does not name: {missing}"
    assert "`restart_needed` is a FIELD" in entry


def test_a_list_setting_is_sent_as_a_list_and_played_back_one_item_per_line():
    """The eval's bug: the coach sent a list setting comma-joined, and an item holding a comma
    became several. The playbooks say to send a list, and to play each item back on its own
    line, so the user agrees to the items as they will be saved."""
    review = coach.read_playbook("review")
    assert ("takes its `value` as a JSON list of strings, one item each, never comma-joined"
            in review)
    assert "Play back a list setting's items one per line" in review
    assert "a list for a list setting" in review and "a list stays a list" in review
    assert "comma-separated" not in review
    assert "a list setting's items one line per item" in coach.read_playbook("interview")


def test_the_units_mark_every_list_setting_and_never_ask_for_commas():
    """The units list is derived from the catalogue, whose prompts are `init`'s TTY questions
    asking for one comma-separated line. Shown to the coach that would contradict setup_save,
    so every list setting is marked and the comma instruction is dropped."""
    from sluice.onboard import questions
    full = coach.assemble_prompt()
    units = full[full.index("## What you can propose"):]
    assert coach.LIST_NOTE in units and "comma-separated" not in units
    lists = [q.key for q in questions.catalogue() if questions.is_list(q)]
    assert lists
    for key in lists:
        assert f"- `{key}` (list): " in units, key
    for q in questions.catalogue():
        if q.key not in lists:
            assert f"- `{q.key}` (list)" not in units, q.key


def test_rehearse_names_every_unsupported_requirement_before_asking_which_to_prepare():
    # A coach told only "check the requirements the user chose" can leave every other gap
    # unnamed; the docs promise the coach finds the requirements no story evidences. So the
    # playbook names ALL of them first, then asks which to prepare for, and its exit criterion
    # holds it to that.
    text = coach.read_playbook("rehearse")
    low = " ".join(text.lower().split())
    names_all = low.index("name to the user every requirement it states that no entry")
    asks = low.index("then ask which requirements they want to prepare for")
    assert names_all < asks
    assert "you have named every requirement no entry evidences" in low


def test_both_prep_notes_say_to_give_a_captured_story_its_best_for():
    # A brief ranks a story on its title and Best For, never its body (core/rank.py), so a story
    # captured with a blank Best For is nearly invisible to the next brief. The first live eval
    # run saved two of three stories that way. Each note names its own level's spelling of the
    # field: propose_evidence takes the kind's declared label, the CLI the flag derived from it.
    from sluice.core.protocols import EVIDENCE_KINDS
    assert "Best For" in EVIDENCE_KINDS["stories"].fields
    assert "`Best For`" in coach.PREP_WRITE_NOTE
    assert "`--best-for`" in coach.PREP_READ_ONLY_NOTE
    for note in (coach.PREP_READ_ONLY_NOTE, coach.PREP_WRITE_NOTE):
        assert "to the requirement they told it for" in note, note
        assert "in the posting's own words" in note, note
        assert "in the user's own words when the requirement came from them" in note, note
        assert "never its body" in note, note
    # Best For is the one part of a saved story the model writes, so the user sees it first.
    assert "show them that Best For with the story before they say yes" in coach.PREP_WRITE_NOTE


def test_the_stories_add_command_really_takes_the_best_for_flag_the_note_names():
    # The read-only note sends the user to a flag; a renamed field would leave it naming one the
    # parser rejects.
    from tests.test_docs_claims import _parser_flags
    assert "--best-for" in _parser_flags("stories", "add")


def test_rehearse_answers_whether_a_requirement_matters_with_what_the_posting_says():
    # Asked "do I need these?", the first live run answered only "that's your call", leaving out
    # that the posting listed both under its required heading. What the posting says is a fact
    # the coach can give; the choice stays with the user.
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert "if they ask whether a requirement matters, tell them what the posting itself says" \
        in low
    assert "leave the choice with them" in low
    # The heading was the fact the live run left out; quoting only the posting's own text, and
    # saying so when it has nothing more, keeps the coach from inventing a heading or a quote.
    assert "including the heading it sits under when it has one" in low
    assert "quoting only its own text" in low
    assert "when the posting says nothing more about it, say so" in low


def test_rehearse_keeps_a_guessed_interview_format_a_guess():
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert "if they offer a guess, it stays a guess" in low


def test_rehearse_asks_for_an_example_in_the_requirements_own_words_adding_nothing():
    # The confirmation run's coach asked about "someone from outside your organisation, such as
    # a client, a supplier or a partner": a list of kinds is an example meant to jog memory.
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    # A plain question around the exact quote, since a requirement worded as a noun phrase
    # ("experience with ...") asks nothing on its own; nothing is added to the requirement.
    assert "put the requirement into a plain question word for word, and add nothing to the " \
        "requirement itself" in low
    assert "a list of the kinds of people, places or situations it might cover is an example" \
        in low


def test_rehearse_quotes_the_users_own_words_when_it_points_at_what_they_said():
    # The confirmation run's coach told the user a statement carried a detail it did not.
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert "quote their own words from the conversation" in low
    assert "never credit them with a detail their words do not contain" in low


def test_no_prep_surface_says_verify_reaches_an_entry_outside_the_inbox():
    # `<kind> verify` reviews only `_inbox/`, so wording an unverified entry as "not reviewed
    # through verify" sent the coach to tell users to run a command that does nothing for it,
    # in both live runs. Every surface the coach reads now says where verify looks.
    from sluice import mcpserver
    surfaces = {"rehearse": coach.read_playbook("rehearse"),
                "_PREP_ENTRIES_WARNING": mcpserver._PREP_ENTRIES_WARNING,
                "_GET_LEAD_PREP_BRIEF_NOTE": mcpserver._GET_LEAD_PREP_BRIEF_NOTE}
    for name, text in surfaces.items():
        flat = " ".join(text.split())
        assert "reviewed through `job-sluice <kind> verify`" not in flat, name
        assert "`_inbox/` folder" in flat, name


def test_the_verify_route_the_coach_gives_for_a_hand_placed_entry_really_works(tmp_path):
    # rehearse.md tells the user to move an unverified note into its kind's `_inbox/` and run
    # `verify`. Measured end to end through the store that command drives: outside the inbox
    # the entry is not pending; moved in, it is, and approving it makes it a verified entry.
    import shutil

    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.core.vault import INBOX_SUBDIR, Vault
    assert "move the note into that folder first, then run it" in " ".join(
        coach.read_playbook("rehearse").split())
    vault = Vault(str(tmp_path))
    folder = tmp_path / EVIDENCE_KINDS["stories"].relpath
    folder.mkdir(parents=True)
    note = folder / "Hand note.md"
    note.write_text("---\nCompany: Example Co\nBest For: b\n---\nA thing I did.\n")
    assert [e["title"] for e in vault.read_pending_evidence("stories")] == []
    (folder / INBOX_SUBDIR).mkdir()
    shutil.move(note, folder / INBOX_SUBDIR / note.name)
    assert [e["title"] for e in vault.read_pending_evidence("stories")] == ["Hand note"]
    text = vault.read_pending_evidence_text("stories", "Hand note")
    # The cost the playbook states: once in the inbox, the note is no longer read into a brief
    # (a brief reads the folder, never the inbox) until it is verified.
    assert "until they verify it, a note in that folder is no longer read into a brief" in " ".join(
        coach.read_playbook("rehearse").lower().split())
    assert [e["title"] for e in vault.read_evidence("stories", verified_only=False)] == []
    assert vault.verify_evidence("stories", "Hand note", today="2026-10-18", reviewed=text)
    assert [e["title"] for e in vault.read_evidence("stories")] == ["Hand note"]



def test_rehearse_accounts_for_every_requirement_one_way_or_the_other_and_never_counts():
    # The re-run's coach dropped one of six requirements from the choice ("any of the five")
    # and called another "touched"; run 2's said "two" of three. Both recur, so the rule is
    # stated: every requirement evidenced by a named entry or a gap, by its words.
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert "account for every one of them, each one way or the other" in low
    assert "never call one partly covered or say a story touches it" in low
    assert "refer to requirements by their words, never by a count" in low


def test_rehearse_compares_no_headings_when_it_quotes_what_the_posting_says():
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert "compare nothing: never set one heading's requirements against another's" in low


def test_rehearse_names_a_detail_added_to_an_entry_and_leaves_the_entry_as_written():
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert "say the entry stays as written until they edit it themselves" in low


def test_the_write_note_says_how_a_saved_name_is_stored(tmp_path):
    # The coach showed a title the store then reduced; the note now says how, measured against
    # the reducer on the inputs where "dropped" and "becomes a hyphen" disagree.
    from sluice.core.vault import evidence_slug
    note = " ".join(coach.PREP_WRITE_NOTE.split())
    assert ("every run of characters other than a plain letter a to z or a digit becomes one "
            "hyphen (an apostrophe, a decimal point and an accented letter included), and it is "
            "cut at 80 characters") in note
    assert evidence_slug("Didn't miss a deadline") == "didn-t-miss-a-deadline"
    assert evidence_slug("Cut costs 3.5%") == "cut-costs-3-5"
    assert evidence_slug("Caf\u00e9 rollout") == "caf-rollout"
    assert len(evidence_slug("a" * 81)) == 80



def test_rehearse_counts_an_entry_as_evidence_only_when_it_says_the_user_did_that_thing():
    # Banning "partly covered" without a direction invites rounding up, which takes a
    # requirement off the gap list on evidence the user's note does not hold.
    low = " ".join(coach.read_playbook("rehearse").lower().split())
    assert ("an entry evidences a requirement only when the entry itself says the user did that "
            "thing; anything less is a gap") in low
    assert "every line the posting lists, under any heading, is a requirement" in low


def test_the_read_only_note_keeps_the_users_own_words_in_the_command():
    note = " ".join(coach.PREP_READ_ONLY_NOTE.split())
    assert ("Any `--name` or `--body` you write into the command is the user's own telling: "
            "never reword it or add a part they did not tell") in note
