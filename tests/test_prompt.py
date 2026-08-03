import os
from sluice.core.vault import Vault
from sluice.triage.prompt import (
    SYSTEM_PROMPT, _CRITERIA_RELPATH, build_system_prompt_from,
)


def _write_criteria(vault_dir, text):
    path = os.path.join(vault_dir, _CRITERIA_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _criteria_from_vault(vault_dir):
    """What the ENGINE does: read criteria through the STORE, then compose from text.
    `load_criteria(vault_dir)` used to do both halves inside the judge module; deleting it is
    what this rewrite is for, so the tests below go through the seam instead."""
    return Vault(str(vault_dir)).read_criteria()


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
    # empty vault dir -> no Judging Profile.md -> the store reads "" -> default criteria.
    # The store-side half of this is also pinned by
    # tests/conformance/test_store_contract.py::test_read_criteria_abstains_when_unset.
    assert _criteria_from_vault(tmp_path) == ""
    assert "Who this candidate is" in build_system_prompt_from(_criteria_from_vault(tmp_path))


def test_vault_criteria_overrides_default(tmp_path):
    # Synthetic throughout: a person name and a REAL city here would be personal data in tests/,
    # which the neutrality rule forbids regardless of whose taste they encode.
    _write_criteria(str(tmp_path),
                    "---\nnote: props\n---\n## Who Example Candidate is\n"
                    "WANTS ONLY EXAMPLE ROLES IN EXAMPLE CITY.\n")
    full = build_system_prompt_from(_criteria_from_vault(tmp_path))
    assert "note: props" not in full                  # frontmatter stripped
    assert "WANTS ONLY EXAMPLE ROLES IN EXAMPLE CITY." in full
    assert "You are the batched judgment stage" in full   # scaffold still wraps it
    assert "relevance_score" in full                       # tail still present


def test_empty_vault_file_falls_back(tmp_path):
    _write_criteria(str(tmp_path), "---\nonly: frontmatter\n---\n   \n")
    assert build_system_prompt_from(_criteria_from_vault(tmp_path)) == \
        build_system_prompt_from("")


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


def test_the_prompt_module_no_longer_reaches_a_filesystem():
    """The store-seam refactor moved the judge off `build_system_prompt(vault.dir)` and onto
    `build_system_prompt_from(store.read_criteria())`, because reaching THROUGH the store to a path
    is what put a store-implementation detail on the judge's critical path. The directory-taking
    forms survived as test-only surface, which kept that trap available to the next caller.

    Asserted three ways. The two `hasattr` checks hold name-keyed ground. The IMPORT SET is the
    mechanism check: a module that imports neither `os`, `io`, `pathlib` nor anything else with a
    filesystem cannot reach one, whatever the reaching function is called -- an earlier draft
    asserted `"open(" not in src` while its docstring claimed to catch a reach "named something
    else", and a `pathlib.Path(...).read_text()` helper walked straight past it (witnessed).

    LIMIT, stated because a guard whose reach is assumed is a guard that fails open: this pins
    top-level imports only. A filesystem reach through a function imported from one of the
    allowed modules, or through an import performed inside a function body, is not matched. That
    is a fact about this check, not a claim about the module."""
    import ast
    import sluice.triage.prompt as prompt
    assert not hasattr(prompt, "load_criteria")
    assert not hasattr(prompt, "build_system_prompt")
    assert prompt.SYSTEM_PROMPT, "the baked-in default prompt must survive"
    src = open(prompt.__file__, encoding="utf-8").read()
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {"re", "sluice.core.criteria", "sluice.core.protocols"}, (
        f"triage/prompt.py's imports changed to {sorted(imported)}; it must reach no filesystem")
