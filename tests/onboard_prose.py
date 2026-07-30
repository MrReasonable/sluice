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
}

# The one place the sweep's own fixture values live, so the rendered arm exercises the WALKED
# branch of _render_sources rather than only its commented-example branch.
_SOURCES_FIXTURE = {"example_source": {
    "enabled": True, "searches": [["Example search", "https://example.invalid/jobs"]]}}


def rendered_artefacts():
    """[(label, text), ...] for the two files `sluice init` writes.

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
    """
    from sluice.onboard.plan import build_plan

    walked = build_plan({}, sources=_SOURCES_FIXTURE)
    default = build_plan({})
    return [("rendered:config_text(sources walked)", walked.config_text),
            ("rendered:config_text(sources skipped -- the DEFAULT path)", default.config_text),
            ("rendered:profile_text", walked.profile_text)]


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
