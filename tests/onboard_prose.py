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
import os
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
    # The 36 CandidateProfile field names, in declaration order -- identifiers used as frontmatter
    # KEYS (and, transitively, already covered as literal text wherever `rendered:candidate_text`
    # is swept below), never prose a user reads as guidance. Same shape as `_ESCAPES` above: a
    # vocabulary table, not shipped wording.
    ("sluice.onboard.plan", "_CANDIDATE_FIELD_ORDER"),
    # answer key -> CandidateProfile field name. A lookup table, not prose -- same `_ESCAPES` shape
    # as the entry above, but NOT for the same reason: this table's KEYS (`cv_forenames` etc.) are
    # `collect_candidate`'s internal answer-dict keys, never written into `candidate_text` or any
    # other rendered artefact, so there is no transitive-coverage argument here, only the
    # vocabulary-table one.
    ("sluice.onboard.plan", "_CANDIDATE_KEY_BY_ANSWER"),
    # `LEADS_VIEW_TEXT` is swept as the ARTEFACT it renders into (`rendered:view_text`, in
    # `rendered_artefacts()` above), so it is exempt HERE the way the other rendered constants
    # are -- from the constant-level roster, not from the taxonomy check.
    #
    # It was previously exempt with no such coverage, on the argument that
    # `test_leads_view.py` guards it. That was half true and the wrong half: that guard checks
    # FILTERS and currency tokens and says in its own comment that a place name in a view's
    # NAME would pass. Measured, a role-and-culture phrase planted as a tab name left the whole
    # suite green, in bytes `init` writes into a stranger's vault.
    ("sluice.onboard.plan", "LEADS_VIEW_TEXT"),
    # `EVIDENCE_KINDS` (core/protocols.py) is a registry of relpaths and frontmatter FIELD NAMES --
    # "Company", "Proficiency", "Signal Value" and the like -- same shape as
    # `_CANDIDATE_FIELD_ORDER` above: identifiers a store reads as keys, never prose a user reads
    # as guidance. It is imported at module scope into both evidence modules (`commands.py` needs
    # it to derive `add`'s per-kind flags; `wizard.py` needs it to loop the capture prompts over
    # every kind), so `vars()` sees it as a local name in each and both tuples are listed here.
    ("sluice.evidence.commands", "EVIDENCE_KINDS"),
    ("sluice.evidence.wizard", "EVIDENCE_KINDS"),
}

# The one place the sweep's own fixture values live, so the rendered arm exercises the WALKED
# branch of _render_sources rather than only its commented-example branch.
_SOURCES_FIXTURE = {"example_source": {
    "enabled": True, "searches": [["Example search", "https://example.invalid/jobs"]]}}


def rendered_artefacts():
    """[(label, text), ...] for the FOUR files `sluice init` writes (Task 6 added the Candidate
    Profile note; #240 added the Obsidian Bases view).

    NOTHING IS STRIPPED, and BOTH arms of every branch are rendered. Two holes lived here:

    - The profile used to have `DEFAULT_CRITERIA`'s prose removed before the sweep, on the grounds
      that it has its own guard in triage. It does not: that guard's vocabulary is disjoint from
      `NO_TAXONOMY_WORDS`. Measured -- planting a role-and-culture phrase into `core/criteria.py`
      left the FULL SUITE green while the written profile carried it to the judge as authoritative
      criteria. This feature CHANGED the stakes of that prose: it is now bytes in a stranger's
      vault, not merely a fallback string.
    - `sources` used to be rendered only non-empty, so `_render_sources`' commented-example arm --
      the DEFAULT path, taken by every `--no-input` run and every user who skips the board
      question -- was never swept at all.

    `candidate_text` has no such second arm to miss: every one of its 36 fields is ALWAYS present
    (answered or present-but-empty), so unlike `_render_sources` there is only one structural shape
    to render, and the unanswered `walked` call already exercises it.
    """
    from sluice.onboard.plan import build_plan

    walked = build_plan({}, sources=_SOURCES_FIXTURE)
    default = build_plan({})
    return [("rendered:config_text(sources walked)", walked.config_text),
            ("rendered:config_text(sources skipped -- the DEFAULT path)", default.config_text),
            ("rendered:profile_text", walked.profile_text),
            ("rendered:candidate_text", walked.candidate_text),
            # The FOURTH artefact (#240). It went into `_NOT_PROSE` on the grounds that
            # `test_leads_view.py` guards it, and that was only half true: that guard checks
            # FILTERS and currency tokens and says in its own comment that a place name in a
            # view's NAME would pass. Measured -- planting a role-and-culture phrase as a tab
            # name left the FULL SUITE green, in bytes `init` writes into a stranger's vault.
            # The taxonomy sweep is the check that reads names.
            ("rendered:view_text", walked.view_text)]


def terminal_transcript():
    """Everything the asker PRINTS, captured by driving it rather than by listing literals.

    The rendered-artefact sweep cannot reach terminal prose, because it never lands in a file --
    measured: an exemplar in `collect_sources`' inline prompt stayed green against the rendered arm.
    Driving the collectors with a scripted stdin and sweeping the captured stdout is the same
    principle applied to the other output channel: these are the bytes the user actually sees.
    """
    import io

    from sluice.onboard.ask import (MissingAnswer, NoInputAsker, TtyAsker, collect,
                                    collect_profile, collect_sources)
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
    # The ERROR paths are prose a user reads at the moment they are most confused, and driving only
    # the happy path never prints them. A bad int, a bad URL and an unknown board id each re-ask.
    err = io.StringIO()
    bad = TtyAsker(stdin=io.StringIO("notanumber\n90\n"), stdout=err)
    bad.ask(next(q for q in questions if q.key == "lead_ttl_days"))
    TtyAsker(stdin=io.StringIO("nonsense\nhttps://example.invalid/j\n"), stdout=err).ask_url("url?")
    TtyAsker(stdin=io.StringIO("no-such-board\n\n"), stdout=err).ask_ids("boards?", ["example_a"])
    # parse_int's OTHER two arms (the #75 yes/no word, and the negative) and parse_choice's message
    # are each reached only by a specific bad answer, so each needs its own line.
    for bad, key in (("yes", "lead_ttl_days"), ("-1", "lead_ttl_days")):
        TtyAsker(stdin=io.StringIO(f"{bad}\n90\n"), stdout=err).ask(
            next(q for q in questions if q.key == key))
    TtyAsker(stdin=io.StringIO("not-a-backend\nanthropic\n"), stdout=err).ask(
        next(q for q in questions if q.key == "primary_backend"))
    try:
        NoInputAsker(presets={}).ask(next(q for q in questions if q.key == "vault_dir"))
    except MissingAnswer as exc:                       # printed on every --no-input run w/o --vault
        print(exc, file=err)

    return [("terminal:asker transcript", out.getvalue()),
            ("terminal:asker error paths", err.getvalue())]


def _render_help(argv):
    import contextlib
    import io

    from sluice.cli import _build_parser

    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        _build_parser().parse_args(argv)
    return buf.getvalue()


def cli_help_text():
    """Argparse help -- shipped prose the functional sweep cannot see, because that captures a
    RUN's output and never the parser's.

    BOTH parsers. `sluice init --help` renders the CHILD, and a string passed to
    `add_parser("init", help=...)` appears only in the PARENT's subcommand listing -- so an earlier
    version of this helper could not see the single string it was written for. Measured:
    "scaffold a config" is absent from the child and present in the parent.
    """
    return [("cli:sluice --help (the subcommand listing)", _render_help(["--help"])),
            ("cli:sluice init --help", _render_help(["init", "--help"]))]


def cli_refusals(tmp_path):
    """Every message `cmd_init` writes to STDERR.

    These are prose a user reads at the moment they are most confused, and they reach neither the
    rendered artefacts, the asker transcript, nor the functional sweep -- which captures a
    SUCCESSFUL run. Driven for real rather than enumerated, so the sweep cannot drift from the
    branches it means to cover.
    """
    import contextlib
    import io
    import os

    from sluice.cli import main

    os.makedirs(tmp_path, exist_ok=True)   # callers may pass a subdirectory that does not exist yet
    out = io.StringIO()

    def run(argv, **env):
        """Run with an EXPLICIT environment. Each refusal needs its own: the ambient VAULT_DIR that
        `tests/conftest.py` sets would send every case down the --vault/$VAULT_DIR disagreement
        branch, so two of the three messages would never be printed and this sweep would silently
        cover one arm. Measured -- the transcript carried the same refusal twice."""
        saved = {k: os.environ.get(k) for k in ("VAULT_DIR", "SLUICE_CONFIG")}
        os.environ.pop("VAULT_DIR", None)
        os.environ.update(env)
        try:
            with contextlib.suppress(SystemExit), contextlib.redirect_stderr(out):
                main(argv)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    vault = os.path.join(tmp_path, "notes")
    afile = os.path.join(tmp_path, "not-a-dir")
    with open(afile, "w", encoding="utf-8") as fh:
        fh.write("x")

    run(["init", "--vault", vault, "--no-input"],
        VAULT_DIR=os.path.join(tmp_path, "elsewhere"))   # --vault vs $VAULT_DIR
    run(["init", "--no-input"])                          # no vault anywhere
    run(["init", "--vault", afile, "--no-input"])        # the vault path is a file

    return [("cli:cmd_init refusals (stderr)", out.getvalue())]


def cli_reports(tmp_path):
    """`cmd_init`'s SUCCESS-path report, on the branches a single happy run never reaches.

    An AST sweep of every literal in the package found four still uncovered: the second-run notice,
    the `.init-scaffold.md` data-loss message, `using the existing vault at` (the COMMON repeat
    run), and the no-vault-directory stderr. Confirmed by mutation -- planting an exemplar in the
    "existing vault" line left the whole suite green.

    Driven rather than enumerated: a first run creates, a second run re-uses, so both arms print.
    """
    import contextlib
    import io
    import os

    from sluice.cli import main

    os.makedirs(tmp_path, exist_ok=True)
    out = io.StringIO()
    vault = os.path.join(tmp_path, "notes")
    saved = {k: os.environ.get(k) for k in ("VAULT_DIR", "SLUICE_CONFIG")}
    os.environ.pop("VAULT_DIR", None)
    os.environ["SLUICE_CONFIG"] = os.path.join(tmp_path, "c.yaml")
    try:
        # BOTH streams. The third vault-report arm writes to stderr, so a stdout-only capture could
        # never see it -- and both runs below succeed, so it also needs a FAILING vault write to be
        # reached at all. Two stacked reasons one line stayed unswept.
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            main(["init", "--vault", vault, "--no-input"])     # creates: "created a new vault"
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            main(["init", "--vault", vault, "--no-input"])     # re-uses: "using the existing vault"

        # ...and the failure arm: makedirs refused, so no vault directory exists to report.
        denied = os.path.join(tmp_path, "denied")
        real_makedirs = os.makedirs

        def refuse_the_vault(path, *a, **kw):
            if str(path) == denied:
                raise OSError(13, "Permission denied")
            return real_makedirs(path, *a, **kw)

        os.makedirs = refuse_the_vault
        try:
            with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(out):
                main(["init", "--vault", denied, "--no-input"])
        finally:
            os.makedirs = real_makedirs
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    return [("cli:cmd_init report (create, re-use and failure arms)", out.getvalue())]


def shipped_prose(tmp_path=None):
    """[(label, text), ...] for every surface a user reads.

    `tmp_path` is optional only because the completeness guard does not need it: the refusal and
    report channels DRIVE `main()`, which needs somewhere to write. Pass one and they are included,
    so a consumer of this function sees the same surfaces the exemplar sweep does rather than a
    subset that silently omits two `cmd_init` branches.
    """
    import sluice.evidence.wizard as wizard_mod
    import sluice.onboard.ask as ask_mod
    import sluice.onboard.plan as plan_mod
    from sluice.onboard.questions import catalogue

    out = list(rendered_artefacts()) + list(terminal_transcript()) + list(cli_help_text())
    if tmp_path is not None:
        out += list(cli_refusals(os.path.join(tmp_path, "refuse")))
        out += list(cli_reports(os.path.join(tmp_path, "report")))
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
    for key, prompt in ask_mod._CANDIDATE_PROMPTS:
        out.append((f"ask._CANDIDATE_PROMPTS[{key}]", prompt))
    # `sluice/evidence/wizard.py`'s own docstring names this: every prompt it shows a user is a
    # module-level constant (Task 8 review, FIX 3) precisely so this roster -- not a driven
    # transcript, the same shape as `ask._CANDIDATE_PROMPTS` above -- can reach it. Listed directly
    # rather than driven because `collect_evidence` is gated on `asker.interactive`, and there is
    # no rendered artefact for a terminal-only prompt to land in.
    #
    # `sluice/evidence/commands.py`'s user-facing print()/error messages are NOT here, and
    # widening `_package_modules()` to discover that module does not change that: those messages
    # are in-body f-strings, the same shape wizard.py's prompts were in before the Task 8 fix
    # above, so there is no module-level constant for this sweep to find. They are no longer
    # UNSWEPT, though (#164 review, L2): they are swept WHERE THEY RUN, by
    # `test_no_command_message_names_a_taxonomy_word` in tests/test_evidence_cli.py, which drives
    # all nine commands through `main` under capsys and runs `expresses_a_preference` over the
    # real output. That is `tests/functional/test_init.py`'s
    # `test_the_commands_own_report_names_no_exemplar` precedent, and it is the better fit for
    # status output than hoisting would be: it also covers the INTERPOLATED halves (a store error
    # message, an entry title, a kind name) that no constant roster can see.
    #
    # That same test also sweeps the ONE user-facing prompt in `sluice/core/app.py`
    # (`verify_evidence_interactive`'s `verify this entry? [y/N]`), which this roster does not
    # reach either and which was undisclosed until the round-2 review found it: `core/` is
    # outside `_package_modules()`'s walk entirely, and widening the walk there would sweep a
    # module of orchestration strings rather than prompts. It is driven instead -- the patched
    # `confirm` records what it was SHOWN and the sweep runs over that text -- which is the same
    # where-it-runs answer, applied to the operation that grants citability.
    out.append(("wizard._INTRO", wizard_mod._INTRO))
    out.append(("wizard._CAPTURE_PROMPT", wizard_mod._CAPTURE_PROMPT))
    out.append(("wizard._NAME_PROMPT", wizard_mod._NAME_PROMPT))
    out.append(("wizard._FIELD_PROMPT", wizard_mod._FIELD_PROMPT))
    out.append(("wizard._BODY_PROMPT_DEFAULT", wizard_mod._BODY_PROMPT_DEFAULT))
    for kind, prompt in wizard_mod._BODY_PROMPT_BY_KIND.items():
        out.append((f"wizard._BODY_PROMPT_BY_KIND[{kind}]", prompt))
    out.append(("wizard._ADD_ANOTHER_PROMPT", wizard_mod._ADD_ANOTHER_PROMPT))
    out.append(("wizard._NOT_CAPTURED_PROMPT", wizard_mod._NOT_CAPTURED_PROMPT))
    return out


def _package_modules():
    """Every module in `sluice.onboard` AND `sluice.evidence`, DISCOVERED. A hand-list meant a
    sixth module would ship entirely unswept -- the same enumeration failure this file exists to
    close -- and hand-adding `sluice.evidence.wizard` as a single named entry point would
    reintroduce that exact regime for a whole second package rather than one module."""
    import sluice.evidence
    import sluice.onboard
    mods = []
    for pkg in (sluice.onboard, sluice.evidence):
        mods += [importlib.import_module(f"{pkg.__name__}.{m.name}")
                 for m in pkgutil.iter_modules(pkg.__path__)]
    return mods


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
