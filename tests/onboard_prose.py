"""Every surface `sluice/onboard/` puts in front of a user or into their files.

**The RENDERED ARTEFACTS are the load-bearing half.** An earlier version of this file swept only
module-level constants, and its docstring claimed to cover everything. It did not: every string
literal inside a function body was invisible to it, so a taxonomy word planted in
`plan._render_profile`'s inline preamble -- text written into a stranger's Obsidian vault and handed
to the judge as authoritative criteria -- left the FULL SUITE green. Three reviewers found that
independently, and it was the third round of the same enumeration failure on this feature.

Sweeping `build_plan(...).config_text` and `.profile_text` fixes it at the root: those are the bytes
the user actually receives, so the sweep cannot go stale as literals move in and out of function
bodies. The constant roster is KEPT alongside, because a per-constant label points at the offending
line, which a whole-artefact match cannot. Terminal prose that never reaches a file -- the asker's
prompts, `cmd_init`'s report -- is not renderable, so it stays enumerated here, and the completeness
guard below is what stops a new one shipping unswept.

Discovery is `pkgutil.iter_modules`, not three hand-named modules: the previous hand-list meant a new
sixth module would ship entirely unswept, and `set` was missing from the type tuple so `_BOOL_WORDS`
evaded it.
"""
import importlib
import inspect
import pkgutil

# Module-level constants that are NOT shipped prose, each with its reason.
_NOT_PROSE = {
    # The banned vocabulary itself. Sweeping it is a guaranteed self-hit.
    ("sluice.onboard.questions", "NO_TAXONOMY_WORDS"),
    # Yes/no words for parse_int's guard -- a parser vocabulary, never shown to anyone.
    ("sluice.onboard.questions", "_BOOL_WORDS"),
    # The YAML escape table: five (raw, escaped) pairs of punctuation.
    ("sluice.onboard.emit", "_ESCAPES"),
    # Authored in core/criteria.py and imported here; governed by
    # test_shipped_prompt_expresses_no_role_or_culture_preference. Exempt on PROVENANCE, not to
    # hide a failure -- measured, it trips zero words in NO_TAXONOMY_WORDS. Re-measure before
    # widening this set: an exemption that would otherwise fire is a suppressed finding.
    ("sluice.onboard.plan", "DEFAULT_CRITERIA"),
    ("sluice.onboard.plan", "PROFILE_HEADINGS"),   # derived FROM the above
}

# The one place the sweep's own fixture values live, so the rendered arm exercises the WALKED
# branch of _render_sources rather than only its commented-example branch.
_SOURCES_FIXTURE = {"example_source": {
    "enabled": True, "searches": [["Example search", "https://example.invalid/jobs"]]}}


def rendered_artefacts():
    """[(label, text), ...] for the two files `sluice init` writes.

    The profile carries `_DEFAULT_CRITERIA`'s prose verbatim -- that IS the round-1 Critical fix --
    and that prose has its own guard in triage, so it is stripped here. What remains is what THIS
    package authored around it. `test_the_rendered_sweep_covers_something` asserts the remainder is
    non-empty, because a strip that removed everything would leave a sweep over nothing, which
    passes.
    """
    from sluice.onboard.plan import build_plan, default_sections

    plan = build_plan({}, config_dest="/example/config.yaml", profile_dest="/example/profile.md",
                      sources=_SOURCES_FIXTURE)
    authored = plan.profile_text
    for body in default_sections().values():
        authored = authored.replace(body, "")
    return [("rendered:config_text", plan.config_text),
            ("rendered:profile_text(minus the shipped default prose)", authored)]


def terminal_transcript():
    """Everything the asker PRINTS, captured by driving it rather than by listing literals.

    The rendered-artefact sweep cannot reach terminal prose, because it never lands in a file --
    measured: an exemplar in `collect_sources`' inline prompt stayed green against the rendered arm.
    Driving the collectors with a scripted stdin and sweeping the captured stdout is the same
    principle applied to the other output channel: these are the bytes the user actually sees.
    """
    import io

    from sluice.onboard.ask import TtyAsker, collect, collect_profile, collect_sources
    from sluice.onboard.questions import catalogue

    out = io.StringIO()
    questions = catalogue(default_vault="/example/vault")
    # Blank every answer: the prompt, its hint and its bracket line are printed before the read, so
    # a skipped question still emits its full prose.
    collect(TtyAsker(stdin=io.StringIO("\n" * (len(questions) + 4)), stdout=out), questions)
    collect_profile(TtyAsker(stdin=io.StringIO("\n" * 8), stdout=out, editor=None))
    # A board IS picked, so the per-source label/URL prompts are reached too, not just `ask_ids`.
    collect_sources(
        TtyAsker(stdin=io.StringIO("example_source\nExample search\nhttps://example.invalid/j\n\n"),
                 stdout=out),
        ["example_source", "other_source"])
    return [("terminal:asker transcript", out.getvalue())]


def shipped_prose():
    """[(label, text), ...] for every surface a user reads."""
    import sluice.onboard.ask as ask_mod
    import sluice.onboard.plan as plan_mod
    from sluice.onboard.questions import catalogue

    out = list(rendered_artefacts()) + list(terminal_transcript())
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


def _package_modules():
    """Every module in `sluice.onboard`, DISCOVERED. A hand-list meant a sixth module would ship
    entirely unswept -- the same enumeration failure this file exists to close."""
    import sluice.onboard
    return [importlib.import_module(f"sluice.onboard.{m.name}")
            for m in pkgutil.iter_modules(sluice.onboard.__path__)]


def _declared_string_constants():
    """Module-level str / dict / tuple / list / set constants across the package."""
    found = set()
    for mod in _package_modules():
        for name, value in vars(mod).items():
            if name.startswith("__") or inspect.ismodule(value) or callable(value):
                continue
            if isinstance(value, (str, dict, tuple, list, set, frozenset)) and value:
                found.add((mod.__name__, name))
    return found
