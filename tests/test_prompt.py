import os
from sluice.triage.prompt import (
    SYSTEM_PROMPT, build_system_prompt, load_criteria, _CRITERIA_RELPATH,
)


def _write_criteria(vault_dir, text):
    path = os.path.join(vault_dir, _CRITERIA_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_default_prompt_has_mechanics_but_no_opinion():
    p = SYSTEM_PROMPT
    assert "\u2014" not in p                               # no em dashes (slop rule)
    # mechanics present: the scaffold must still teach the task and output schema
    assert "shortlist" in p and "research" in p and "dismiss" in p
    assert "relevance_score" in p


# The privacy invariant. The shipped prompt is public; a candidate's target roles,
# anti-targets and culture preferences are not. They belong in the vault Judging
# Profile, and this test fails the moment somebody bakes one back into the code.
def test_shipped_prompt_expresses_no_role_or_culture_preference():
    p = SYSTEM_PROMPT.lower()
    forbidden = [
        # target/anti-target role shapes
        "engineering manager", "software engineering manager", "development manager",
        "team lead", "tech lead", "technical lead", "scrum master", "agile coach",
        "head of engineering", "vp engineering", "manager-of-managers",
        # a specific culture rubric
        "transformation-shaped", "dora", "kanban", "wip limits", "retros are sacred",
    ]
    leaked = [t for t in forbidden if t in p]
    assert not leaked, (
        "the shipped judge prompt names a role or culture preference: "
        f"{leaked}. Those are personal and belong in the vault Judging Profile.")


def test_missing_vault_file_falls_back_to_default(tmp_path):
    # empty vault dir -> no Judging Profile.md -> default criteria
    assert load_criteria(str(tmp_path)) == load_criteria(None)
    assert "Who this candidate is" in build_system_prompt(str(tmp_path))


def test_vault_criteria_overrides_default(tmp_path):
    _write_criteria(str(tmp_path),
                    "---\nnote: props\n---\n## Who Alex is\nWANTS ONLY RUST ROLES IN BERLIN.\n")
    crit = load_criteria(str(tmp_path))
    assert "WANTS ONLY RUST ROLES IN BERLIN." in crit
    assert "note: props" not in crit                  # frontmatter stripped
    full = build_system_prompt(str(tmp_path))
    assert "WANTS ONLY RUST ROLES IN BERLIN." in full
    assert "You are the batched judgment stage" in full   # scaffold still wraps it
    assert "relevance_score" in full                       # tail still present


def test_empty_vault_file_falls_back(tmp_path):
    _write_criteria(str(tmp_path), "---\nonly: frontmatter\n---\n   \n")
    assert load_criteria(str(tmp_path)) == load_criteria(None)


# ── build_system_prompt_from: the form the ENGINE actually calls ──────────────
# triage/engine.py now calls build_system_prompt_from(store.read_criteria()) rather than
# build_system_prompt(vault.dir) -- reaching through the store to a filesystem path is what
# put a store-implementation detail on the judge's critical path. Every existing test in
# this file exercises the OLD form, so the live path had zero coverage.

def test_build_system_prompt_from_uses_the_supplied_criteria():
    from sluice.triage.prompt import build_system_prompt_from
    out = build_system_prompt_from("I want roles doing X. I refuse roles doing Y.")
    assert "I refuse roles doing Y" in out


def test_build_system_prompt_from_abstains_when_criteria_are_absent():
    """Empty criteria must fall back to the shipped default, which states only that
    nothing is configured and declines to invent an opinion. A store that returns "" must
    not produce a prompt with an EMPTY criteria block: the judge would then score every
    lead against nothing."""
    from sluice.triage.prompt import _DEFAULT_CRITERIA, build_system_prompt_from
    for empty in ("", "   ", None):
        out = build_system_prompt_from(empty)
        assert _DEFAULT_CRITERIA in out, "empty criteria must fall back to the neutral default"


def test_build_system_prompt_from_strips_frontmatter():
    from sluice.triage.prompt import build_system_prompt_from
    out = build_system_prompt_from("---\ntitle: Judging Profile\n---\nI want roles doing X.")
    assert "I want roles doing X" in out
    assert "title: Judging Profile" not in out
