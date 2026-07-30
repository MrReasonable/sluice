"""Every string `sluice/onboard/` puts in front of a user or into their files.

A ROSTER plus a completeness guard, the shape `tests/conftest.py` already uses for
`PATH_ENV_VARS`: the roster is hand-listed so the sweep stays legible, and the guard pins it
against what the source actually declares, so a new constant cannot ship unswept.

Discovery alone was rejected: it would sweep `NO_TAXONOMY_WORDS` (the vocabulary itself, which
contains every banned word by construction and would fail always) and `_DEFAULT_CRITERIA`
(imported into `plan`'s namespace, authored elsewhere, governed by its own guard). Both need a
NAMED exemption, and once exemptions exist a bare `dir()` sweep is no simpler than a roster.

This file lands with Task 8 rather than Task 4, because `shipped_prose()` reads all three of
`questions`, `plan` and `ask` -- so it cannot import until the last of them exists.
"""
import inspect

# Module-level string constants that are NOT shipped prose, each with its reason.
_NOT_PROSE = {
    # The banned vocabulary itself. Sweeping it is a guaranteed self-hit.
    ("sluice.onboard.questions", "NO_TAXONOMY_WORDS"),
    # Authored in triage/prompt.py, imported here; governed by
    # test_shipped_prompt_expresses_no_role_or_culture_preference. Exempt on PROVENANCE, not to
    # hide a failure -- measured, it trips zero words in NO_TAXONOMY_WORDS. Re-measure before
    # widening this set: an exemption that would otherwise fire is a suppressed finding.
    ("sluice.onboard.plan", "_DEFAULT_CRITERIA"),
    ("sluice.onboard.plan", "PROFILE_HEADINGS"),   # derived FROM the above
}


def shipped_prose():
    """[(label, text), ...] for every surface a user reads."""
    import sluice.onboard.ask as ask_mod
    import sluice.onboard.plan as plan_mod
    from sluice.onboard.questions import catalogue

    out = []
    for q in catalogue(default_vault="/example/vault"):
        for attr in ("prompt", "hint", "consequence"):
            out.append((f"catalogue[{q.key}].{attr}", getattr(q, attr)))
    out.append(("plan._HEADER", plan_mod._HEADER))
    for section, blurb in plan_mod._SECTION_BLURB.items():
        out.append((f"plan._SECTION_BLURB[{section}]", blurb))
    for heading, (_key, prompt) in plan_mod._PROFILE_PROMPTS.items():
        out.append((f"plan._PROFILE_PROMPTS[{heading}]", prompt))
    for key, prompt in ask_mod._PROFILE_QUESTIONS:
        out.append((f"ask._PROFILE_QUESTIONS[{key}]", prompt))
    return out


def _declared_string_constants():
    """Module-level str / dict-of-str / tuple-of-pairs constants across the package."""
    import sluice.onboard.ask as ask_mod
    import sluice.onboard.plan as plan_mod
    import sluice.onboard.questions as q_mod

    found = set()
    for mod in (ask_mod, plan_mod, q_mod):
        for name, value in vars(mod).items():
            if name.startswith("__") or inspect.ismodule(value) or callable(value):
                continue
            if isinstance(value, (str, dict, tuple, list)) and value:
                found.add((mod.__name__, name))
    return found
