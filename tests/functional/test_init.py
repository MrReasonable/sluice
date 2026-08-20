"""`sluice init` through the real `main(argv)`."""
import io
import os

from sluice.core.paths import config_file
from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH, CRITERIA_RELPATH
from sluice.core.vault import Vault

# `run_init` comes from tests/functional/conftest.py, which imports it from
# tests/harness/initdriver.py. Importing it HERE instead shadows each test's parameter of the same
# name and ruff flags all eleven as F811. The suppressed-import style this repo uses elsewhere for
# harness fixtures works only for AUTOUSE ones, which are never named as a parameter.


def test_init_writes_both_artefacts(run_init, tmp_path):
    vault = tmp_path / "notes"
    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert os.path.exists(config_file())
    assert (vault / CRITERIA_RELPATH).exists()
    assert "wrote" in out


def test_the_profile_lands_where_the_judge_reads_it(run_init, tmp_path):
    """Asserted by CALLING read_criteria, not by checking a path."""
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    assert "Judging Profile" in Vault(str(vault)).read_criteria()


def test_a_re_run_clobbers_nothing_and_exits_zero(run_init, tmp_path):
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    (vault / CRITERIA_RELPATH).write_text("MY REAL CRITERIA", encoding="utf-8")
    before = open(config_file(), encoding="utf-8").read()

    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert (vault / CRITERIA_RELPATH).read_text(encoding="utf-8") == "MY REAL CRITERIA"
    assert open(config_file(), encoding="utf-8").read() == before
    assert "exists" in out


def test_no_vault_and_no_terminal_refuses_writing_nothing(run_init, monkeypatch):
    """The autouse `_pin_paths` SETS VAULT_DIR, so without this delenv the test passes for the
    wrong reason -- init would find a vault in the environment and never reach the refusal."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    rc, _out, err = run_init(["init", "--no-input"])
    assert rc == 2
    assert "--vault" in err
    assert not os.path.exists(config_file())


def test_vault_flag_disagreeing_with_the_env_refuses(run_init, tmp_path, monkeypatch):
    """stores/vault.py:_make is env-first, so the seam route would otherwise write to $VAULT_DIR
    while the report names --vault. The two answers contradict each other and only the user knows
    which they meant."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "from-env"))
    rc, _out, err = run_init(["init", "--vault", str(tmp_path / "from-flag"), "--no-input"])
    assert rc == 2
    assert "VAULT_DIR" in err
    assert not os.path.exists(config_file())


def test_an_existing_config_is_kept_and_the_profile_still_scaffolds(run_init, tmp_path):
    dest = config_file()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("# hand written\n")
    vault = tmp_path / "notes"
    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert open(dest, encoding="utf-8").read() == "# hand written\n"
    assert (vault / CRITERIA_RELPATH).exists()
    assert "exists" in out


def test_a_config_that_appears_after_the_check_is_not_truncated(run_init, tmp_path, monkeypatch):
    """The exclusive create is a SECOND line of defence, covering the window between
    `os.path.exists(config_dest)` and the open.

    Without this test that mode is untested: swapping `"x"` for `"w"` leaves every other init test
    GREEN, because the exists() branch means the open is never reached when the config is already
    there -- witnessed. The comment in `cmd_init` asserting never-clobber "is a property of the open,
    not of the check above it" was prose nothing could falsify.

    The racer is injected at the last moment before the open: `os.makedirs` is the call immediately
    preceding it, so the existence check has already run and seen nothing.
    """
    dest = config_file()
    real_makedirs = os.makedirs

    def makedirs_then_race(path, **kw):
        real_makedirs(path, **kw)
        # Anchored to the config's OWN directory. os.makedirs recurses through the module-global
        # name, so the patch is re-entered for every missing parent -- firing on one of those would
        # try to create the config before its directory exists.
        if path == os.path.dirname(dest) and not os.path.exists(dest):
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write("WRITTEN BY A CONCURRENT SHELL\n")

    monkeypatch.setattr(os, "makedirs", makedirs_then_race)
    rc, out, _err = run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 0
    assert open(dest, encoding="utf-8").read() == "WRITTEN BY A CONCURRENT SHELL\n"
    assert "exists" in out


def test_sluice_config_retargets_the_written_config(run_init, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("SLUICE_CONFIG", str(elsewhere))
    rc, out, _err = run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 0 and elsewhere.exists() and str(elsewhere) in out


def test_init_creates_nothing_under_the_state_or_cache_roots(run_init, tmp_path):
    """#80: a stray file under the state root disarms a relocation notice -- a 0-byte seen.db is
    enough. Asserted against the resolver's own roots, not a literal."""
    from sluice.core.paths import resolve
    run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    for kind in ("state", "cache"):
        root = os.path.dirname(resolve(env_var=None, config_value="", kind=kind, name="probe"))
        assert not os.path.exists(root) or os.listdir(root) == []


def test_a_new_vault_directory_is_reported_as_created(run_init, tmp_path):
    rc, out, _err = run_init(["init", "--vault", str(tmp_path / "brand-new"), "--no-input"])
    assert rc == 0 and "created" in out.lower()


def test_a_vault_path_that_is_a_file_refuses(run_init, tmp_path):
    afile = tmp_path / "not-a-dir"
    afile.write_text("x", encoding="utf-8")
    rc, _out, err = run_init(["init", "--vault", str(afile), "--no-input"])
    assert rc == 2 and "not a directory" in err


def test_the_commands_own_report_names_no_exemplar(run_init, tmp_path):
    """The third output channel. `tests/onboard_prose.py` sweeps the rendered artefacts and the
    asker's transcript, but `cmd_init`'s own report -- the `wrote`/`exists` lines, the vault
    sentence, the `Your config will:` notes and the whole `Next:` block -- is printed by the CLI and
    reaches neither. It is swept HERE, where the command actually runs.

    Uses the same shared vocabulary as the unit tier, imported rather than re-listed."""
    from sluice.onboard.questions import expresses_a_preference
    rc, out, err = run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 0 and out.strip()                          # SCOPE: a sweep over nothing passes
    assert "Next:" in out                                   # ...and reached the report's tail
    assert not expresses_a_preference(out + err)


def test_the_profile_is_probed_through_the_store_not_the_filesystem(run_init, tmp_path,
                                                                    monkeypatch):
    """`protocols.py` calls CRITERIA_RELPATH "an opaque DOCUMENT KEY, not a path -- nothing here
    may assume a filesystem", and `cmd_init` used to check it with `os.path.exists`.

    That cannot be witnessed by reverting the code: `vault` is the only registered store and it IS
    a filesystem store, so both forms agree and the whole suite stays green. Measured. So the
    MECHANISM is pinned instead -- the profile path must never be handed to `os.path.exists` --
    which is falsifiable today rather than only when #1 lands a second store.
    """
    vault = tmp_path / "notes"
    probed = os.path.join(str(vault), CRITERIA_RELPATH)
    real_exists = os.path.exists

    def refuse_to_stat_the_profile(path):
        assert str(path) != probed, "cmd_init probed the profile through the filesystem"
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", refuse_to_stat_the_profile)
    rc, _out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert real_exists(probed), "precondition: the profile was actually written"


def _skip_all_questions():
    """One blank per question `cmd_init` will ask, DERIVED from the catalogue.

    `--vault` is a preset, so it is filtered out before `collect` runs. Hardcoding the count made
    the leading blanks eat the board answer and the walk silently collected nothing -- the script
    has to track the catalogue or the test drifts the moment a question is added."""
    from sluice.onboard.questions import catalogue
    return ["" for q in catalogue() if q.key != "vault_dir"]


def _scripted(lines):
    """A TtyAsker driven from a list of answers, with the editor explicitly OFF.

    `editor=None` is load-bearing: `tests/conftest.py` does not scrub `$EDITOR`, so an asker that
    resolved it itself would open the developer's editor mid-suite."""
    import io

    from sluice.onboard.ask import TtyAsker
    return TtyAsker(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=io.StringIO(), editor=None)


def _init(argv, asker):
    """`cmd_init` through its own seam, since `main()` has no way to inject one."""
    from sluice.cli import _build_parser, cmd_init
    from sluice.core.config import load_config
    args = _build_parser().parse_args(argv)
    return cmd_init(args, load_config(), asker=asker)


def test_a_failed_config_write_reports_and_exits_non_zero(run_init, tmp_path, monkeypatch):
    """Hard rule 9. The whole failure arm was unwitnessed: mutating `return 1 if failed else 0` to
    `return 0`, or either `except OSError` body to `pass`, left the full suite green -- after which
    `init` exits 0 having written nothing while printing `wrote` for both artefacts."""
    dest = config_file()
    real_open = open

    def refuse_the_config(path, *a, **kw):
        if str(path) == dest:
            raise OSError(13, "Permission denied")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", refuse_the_config)
    rc, out, err = run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 1
    assert "FAILED" in err and dest in err
    assert f"wrote   {dest}" not in out


def test_both_artefacts_name_the_SAME_vault_when_the_env_carries_a_tilde(run_init, tmp_path,
                                                                        monkeypatch):
    """`cmd_init` expanded `~`; the store seam did not. Measured before the fix:
    `VAULT_DIR='~/probevault' sluice init --no-input` wrote `vault_dir: <HOME>/probevault` into the
    config while the profile landed in a literal `./~/probevault/` under the CWD -- a real directory
    named `~`, in whatever the user happened to be standing in.

    The consequence is silent: triage reads the config's path, finds no profile there, and falls
    back to the shipped default criteria. The user's scaffold is orphaned in a directory they will
    never look in. The --vault/$VAULT_DIR refusal does not cover this -- it guards which VARIABLE
    wins, not how the value is normalized."""
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VAULT_DIR", "~/probevault")

    rc, _out, _err = run_init(["init", "--no-input"])
    assert rc == 0

    from sluice.core.config import load_config
    configured = load_config(config_file()).vault_dir
    assert (home / "probevault" / CRITERIA_RELPATH).exists(), "the profile did not land under $HOME"
    assert not (tmp_path / "~").exists(), "a literal '~' directory was created"
    assert Vault(configured).read_criteria(), \
        "the config names a vault the profile was not written to"


def test_a_second_run_never_reports_gates_it_did_not_write(run_init, tmp_path):
    """The report is derived from ANSWERS, not from what landed.

    Measured: with a config already on disk, a second interactive run asked all 15 preference
    questions, wrote nothing, and then printed "reject titles matching: Widget Wrangler" for a file
    that had never heard of it. rc 0. A user reads that as configuration and stops looking.

    Both halves are fixed here: the config questions are not asked at all when there is nothing to
    write them to, and the notes print only when the config was actually created."""
    dest = config_file()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("# hand written\n")

    vault = tmp_path / "notes"
    script = _skip_all_questions() + [""] + [""] * 5
    rc = _init(["init", "--vault", str(vault)], _scripted(script))

    assert rc == 0
    assert open(dest, encoding="utf-8").read() == "# hand written\n"


def test_an_existing_config_skips_the_questions_that_only_write_to_it(run_init, tmp_path, capsys):
    """The docstring always claimed the preflight resolves both destinations "before a single
    question is asked ... wasted their time". `config_exists` was computed early and not consulted
    until after the interview, so it prevented nothing -- moving that line down left every init
    test green."""
    dest = config_file()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("# hand written\n")

    # ONE answer available: the vault. If any preference question is still asked, the asker runs
    # out of script and this would collect blanks -- so assert on what was ASKED, via the prompts.
    from sluice.onboard.ask import TtyAsker
    out = io.StringIO()
    asker = TtyAsker(stdin=io.StringIO("\n" * 8), stdout=out, editor=None)
    rc = _init(["init", "--vault", str(tmp_path / "notes")], asker)
    shown = out.getvalue()

    assert rc == 0
    assert "Which job titles do you want" not in shown, "a config-only question was still asked"
    assert "Keep only titles containing" not in shown
    captured = capsys.readouterr().out
    assert "skipping the config questions" in captured
    assert "Your config will:" not in captured, "reported gates for a config it did not write"


def test_an_existing_config_also_skips_the_board_walk(run_init, tmp_path):
    """The sibling the round-2 fix left behind. `collect_sources` writes ONLY into
    `plan.config_text`, exactly like the preference questions -- so gating those and not this left
    a second interactive run printing "skipping the config questions" and then immediately asking
    for board ids, search labels and URLs, discarding every answer with rc 0 and no report."""
    dest = config_file()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("# hand written\n")

    from sluice.onboard.ask import TtyAsker
    out = io.StringIO()
    asker = TtyAsker(stdin=io.StringIO("\n" * 8), stdout=out, editor=None)
    rc = _init(["init", "--vault", str(tmp_path / "notes")], asker)
    shown = out.getvalue()

    assert rc == 0
    assert "boards do you want" not in shown, "the board walk ran for a config it will not write"
    assert "search label" not in shown


def test_a_relative_SLUICE_CONFIG_reports_rather_than_crashing(run_init, tmp_path, monkeypatch):
    """`os.path.dirname("sluice.local.yaml")` is `""`, and `os.makedirs("")` raises
    FileNotFoundError. Sitting outside the try, that escaped as an uncaught traceback instead of
    this command's own FAILED report -- reproduced. A relative $SLUICE_CONFIG is a documented way
    to use sluice, so this is an ordinary path, not an edge case."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLUICE_CONFIG", "sluice.local.yaml")
    rc, out, _err = run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    assert rc == 0, "a relative config path must simply work"
    assert (tmp_path / "sluice.local.yaml").exists()
    assert "wrote" in out


def test_a_walked_board_reaches_the_written_config(run_init, tmp_path):
    """The interactive half was deletable with the suite green -- `if interactive:` -> `if False:`
    passed, because every other test uses `--no-input` and the mode was derived from isatty()
    rather than from the injected asker. This drives it through the `asker=` seam."""
    from sluice.core.config import load_config
    from sluice.ingest import sources as registry
    # sorted(), not [0]: registry order follows import order, so an unsorted pick makes this
    # test depend on which plugin happens to load first.
    board = sorted(s.id for s in registry.all_sources())[0]
    # questions ... | board walk: pick, label, url, blank-to-finish | profile: 5 blanks
    rc = _init(["init", "--vault", str(tmp_path / "notes")],
               _scripted(_skip_all_questions()
                         + [board, "Example search", "https://example.invalid/j", ""]
                         + [""] * 5))
    assert rc == 0
    assert load_config(config_file()).sources[board].searches == \
        [["Example search", "https://example.invalid/j"]]


def _asker_that_plants_the_profile(lines, vault):
    """A scripted asker that creates the Judging Profile part-way through the interview.

    That is the ONLY way to reach the `.init-scaffold.md` branch, and getting it wrong is
    instructive: pre-creating the profile makes `cmd_init` skip the interview entirely (correctly --
    there is nothing to ask for), so `profile_answers` stays empty and the branch never runs. The
    branch exists for the race where a profile appears BETWEEN the preflight check and the write --
    a human in Obsidian, or a sync client -- which is what this reproduces.
    """
    from sluice.onboard.ask import TtyAsker

    class _Planting(TtyAsker):
        def ask_prose(self, prompt):
            answer = super().ask_prose(prompt)
            target = vault / CRITERIA_RELPATH
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("MY REAL CRITERIA", encoding="utf-8")
            return answer

    import io
    return _Planting(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=io.StringIO(), editor=None)


def test_prose_typed_against_a_profile_that_appears_mid_interview_is_parked_not_binned(
        run_init, tmp_path):
    """`.init-scaffold.md` -- the one data-loss guard in `cmd_init` -- was structurally unreachable
    from the suite (`grep -rn init-scaffold tests/` returned nothing). A human types five answers,
    a profile appears underneath them, and the original must survive byte-identical while their
    prose lands beside it."""
    vault = tmp_path / "notes"
    script = _skip_all_questions() + [""] + ["Example background prose."] + [""] * 4
    rc = _init(["init", "--vault", str(vault)], _asker_that_plants_the_profile(script, vault))

    assert rc == 0
    assert (vault / CRITERIA_RELPATH).read_text(encoding="utf-8") == "MY REAL CRITERIA"
    spare = vault / CRITERIA_RELPATH.replace(".md", ".init-scaffold.md")
    assert spare.exists() and "Example background prose." in spare.read_text(encoding="utf-8")


def test_a_second_collision_reports_the_loss_rather_than_binning_it(run_init, tmp_path, capsys):
    """The spare write is itself `only_if_absent`, and its "" return was DROPPED -- so a second
    such run discarded the answers with no output and rc 0, directly under a comment saying not
    to."""
    vault = tmp_path / "notes"
    (vault / "Job Applications").mkdir(parents=True)
    (vault / CRITERIA_RELPATH.replace(".md", ".init-scaffold.md")).write_text(
        "FROM AN EARLIER RUN", encoding="utf-8")

    script = _skip_all_questions() + [""] + ["Example background prose."] + [""] * 4
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], _asker_that_plants_the_profile(script, vault))
    err = capsys.readouterr().err

    assert rc == 1, "losing what a human typed must not be reported as success"
    assert "init-scaffold" in err and "NOT saved" in err
    # This branch is reachable ONLY from this collision, so no rendered or transcript sweep can see
    # it -- measured, an exemplar planted here left the whole suite green. Swept where it runs,
    # with the shared vocabulary rather than a second copy.
    from sluice.onboard.questions import expresses_a_preference
    assert not expresses_a_preference(err)
    assert (vault / CRITERIA_RELPATH.replace(".md", ".init-scaffold.md")).read_text(
        encoding="utf-8") == "FROM AN EARLIER RUN", "the earlier run's prose must survive too"


def test_the_written_config_loads_and_abstains(run_init, tmp_path):
    from sluice.core.config import load_config
    from sluice.triage.config import load_triage_config
    run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    path = config_file()
    assert load_config(path).relevance_keep == []
    assert load_triage_config(path).accept_titles == []
    assert load_config(path).lead_ttl_days == 0


# ── Task 7: the Candidate Profile interview, gated on the note it writes ──────────────────────────
#
# The Candidate Profile is the THIRD artefact `cmd_init` writes, and the one deliberate difference
# from the other two: its write is CONDITIONAL on at least one declared answer, because
# `_render_candidate` (unlike `_render_profile`) has no fallback prose to fall back on. Writing an
# all-blank note unconditionally would make the note EXIST (so a re-run's write refuses,
# never-clobber) while `has_any_declared` stayed False FOREVER (so the existence probe never
# reports it as done) -- a deadlock where every later run re-asks, parks the answers in
# `.init-scaffold.md`, and the run after that reports `failed` with the real note still empty.
# `test_no_input_writes_no_candidate_profile_note` and its sequel are what a broken (unconditional)
# write would fail first.

def _skip_all_candidate_questions():
    """One blank per `collect_candidate` prompt. Hardcoding `5` would silently stop tracking
    `_CANDIDATE_PROMPTS` (sluice/onboard/ask.py) the moment a sixth identity field grows a
    question -- the same drift `_skip_all_questions` above already guards against for the
    catalogue."""
    from sluice.onboard.ask import _CANDIDATE_PROMPTS
    return ["" for _ in _CANDIDATE_PROMPTS]


def _seed_candidate_note(vault, fields):
    """Write a Candidate Profile note directly (bypassing `cmd_init`), the way a user editing
    Obsidian by hand -- or a note from an earlier run -- would leave one. `fields` supplies only the
    keys under test; every other CandidateProfile field is simply absent from the frontmatter block,
    which `read_candidate_profile` already treats as blank (tests/test_vault_candidate_profile.py)."""
    dest = vault / CANDIDATE_PROFILE_RELPATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())
    dest.write_text(f"---\n{body}---\n", encoding="utf-8")


def test_no_input_writes_no_candidate_profile_note(run_init, tmp_path):
    """--no-input runs no interview, so there are no declared answers, so nothing is written. This
    is the DEADLOCK case: an unconditional write here would create an all-blank note that EXISTS
    (refusing every later write) while never satisfying `has_any_declared` (so the interview
    re-asks forever) -- see the module comment above."""
    vault = tmp_path / "notes"
    rc, out, _err = run_init(["init", "--vault", str(vault), "--no-input"])
    assert rc == 0
    assert not (vault / CANDIDATE_PROFILE_RELPATH).exists()
    assert "wrote" in out  # precondition: the run did something, not merely nothing at all


def test_a_second_no_input_run_still_writes_none(run_init, tmp_path):
    """Repeats `test_no_input_writes_no_candidate_profile_note` across two runs for idempotency,
    not as a second witness: under --no-input the candidate interview never runs
    (`NoInputAsker.ask_text_plain` always returns ""), so `candidate_answers` is `{}` and the
    write's outer gate is False on every run regardless of anything already on disk. A write-gate
    mutation that made the block unconditional is already caught on the FIRST run by the sibling
    test above -- the very first no-input run would itself create the note. And a wrongly-created
    note would not refuse the second run's write SILENTLY either way: `write_document`'s refusal
    still reaches `skipped.append(candidate_dest)`, which the ordinary report loop prints as
    `exists ... (left alone)` -- nothing about this path is silent, an earlier version of this
    docstring's claim to the contrary."""
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    run_init(["init", "--vault", str(vault), "--no-input"])
    assert not (vault / CANDIDATE_PROFILE_RELPATH).exists()


def test_an_interactive_run_with_every_identity_question_skipped_writes_none(run_init, tmp_path):
    """The interactive twin of the two tests above: a human present at every prompt who declines
    every one of the five identity questions must leave exactly the same nothing behind."""
    vault = tmp_path / "notes"
    script = _skip_all_questions() + [""] + [""] * 5 + _skip_all_candidate_questions()
    rc = _init(["init", "--vault", str(vault)], _scripted(script))
    assert rc == 0
    assert not (vault / CANDIDATE_PROFILE_RELPATH).exists()


def _prompting_asker(lines):
    """A scripted `TtyAsker` whose own transcript is reachable via the returned `out.getvalue()`.

    `_scripted` (above) builds a throwaway `stdout=io.StringIO()` with no handle back to it, which
    is fine for tests that only care what got WRITTEN -- but a prompt the interview skips and one
    it asks-and-discards are indistinguishable from written output alone, and telling those apart
    is the entire point of the three tests below. Mirrors the inline construction
    `test_an_existing_config_skips_the_questions_that_only_write_to_it` already uses for the same
    reason, one layer up (the config questions rather than the candidate ones).
    """
    import io

    from sluice.onboard.ask import TtyAsker
    out = io.StringIO()
    return TtyAsker(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out, editor=None), out


def test_a_populated_candidate_note_is_left_alone_and_the_questions_are_not_re_asked(
        run_init, tmp_path):
    """A migrating user (or a second run) with a real note must not have it touched, and must not
    be re-interviewed for it -- checked against the actual prompt TEXT in the wizard's own
    transcript, not merely against what ended up written."""
    vault = tmp_path / "notes"
    _seed_candidate_note(vault, {"forenames": "Ada", "email": "ada@example.invalid"})
    before = (vault / CANDIDATE_PROFILE_RELPATH).read_bytes()

    script = _skip_all_questions() + [""]  # catalogue + board walk; profile falls to editor=None
    asker, out = _prompting_asker(script)
    rc = _init(["init", "--vault", str(vault)], asker)
    shown = out.getvalue()

    assert rc == 0
    assert (vault / CANDIDATE_PROFILE_RELPATH).read_bytes() == before
    # Positive anchor, not just an absence: this run has no config yet, so the catalogue's OWN
    # first question is expected to fire on the same asker/stream -- proving the transcript is
    # genuinely live rather than resting on a sibling test elsewhere to establish that.
    # cv_employers, not the retired cv_name (#133/#107: cv_name/cv_contact left the
    # catalogue entirely, so cv_employers is now the first question after vault_dir).
    assert "Places you have worked, comma-separated?" in shown
    assert "forename(s)" not in shown, "the interview must be gated, not merely harmless"


def test_a_note_declaring_only_email_still_closes_the_gate(run_init, tmp_path):
    """has_any_declared, not full_name: a user who answered only `email` has a note that exists
    and is useful to `cv run`/`apply prep`. A `full_name`-keyed probe would treat that note as
    though nothing were declared and re-ask forever."""
    vault = tmp_path / "notes"
    _seed_candidate_note(vault, {"email": "ada@example.invalid"})

    script = _skip_all_questions() + [""]
    asker, out = _prompting_asker(script)
    rc = _init(["init", "--vault", str(vault)], asker)
    shown = out.getvalue()

    assert rc == 0
    # cv_employers, not the retired cv_name -- positive anchor, see the sibling test above.
    assert "Places you have worked, comma-separated?" in shown
    assert "forename(s)" not in shown


def test_a_blank_note_is_treated_as_not_existing_and_the_questions_are_asked(
        run_init, tmp_path, capsys):
    """The mirror image of the two tests above, and what actually PROVES `has_any_declared` is the
    probe rather than a bare `bool(store.read_candidate_profile())`: `read_candidate_profile()`
    returns a CandidateProfile OBJECT, so `bool(...)` on it is True the instant the FILE exists --
    even one with every field blank -- which is a different question from "did the user declare
    anything". A note with a fenced-but-empty frontmatter block is exactly that shape: it exists on
    disk, and nothing in it is declared.

    `rc == 1`, not 0: the interview correctly runs (the assertion this test exists for), but the
    primary write then refuses -- the blank note is already sitting at that path -- and the answers
    can only be parked in the spare, never actually reaching the note the user will read. That is a
    real failure to communicate, not a cosmetic one, so it is reported as one. See
    `test_a_hand_started_note_with_no_frontmatter_is_named_as_the_real_blocker` below for the same
    shape without the fence, and for what the FAILED message says."""
    vault = tmp_path / "notes"
    _seed_candidate_note(vault, {})
    seeded = (vault / CANDIDATE_PROFILE_RELPATH).read_text(encoding="utf-8")

    script = (_skip_all_questions() + [""] + [""] * 5
              + ["Ada", "Example", "ada@example.invalid", "", ""])
    asker, out = _prompting_asker(script)
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], asker)
    err = capsys.readouterr().err

    assert rc == 1
    assert "forename(s)" in out.getvalue(), "a blank note must not be read as 'already answered'"
    assert seeded == "---\n---\n", "precondition: the seeded note really was blank"
    assert "declares nothing" in err


def _asker_that_plants_the_candidate_note(lines, vault):
    """A scripted asker that creates the Candidate Profile part-way through the CANDIDATE
    interview -- the sibling of `_asker_that_plants_the_profile` above, reproducing the same race
    (a human in Obsidian, or a sync client) for the note this task's write block writes.

    Hooks `ask_text_plain` rather than `ask_prose`: that is what `collect_candidate` calls, and
    also what `collect_sources` uses for a board's search label -- harmless here because every
    script below skips the board walk with a single blank, so the only `ask_text_plain` calls that
    ever fire are the five candidate prompts.
    """
    from sluice.onboard.ask import TtyAsker

    class _Planting(TtyAsker):
        def ask_text_plain(self, prompt):
            answer = super().ask_text_plain(prompt)
            target = vault / CANDIDATE_PROFILE_RELPATH
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("---\nforenames: Real\n---\n", encoding="utf-8")
            return answer

    import io
    return _Planting(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=io.StringIO(), editor=None)


def test_a_candidate_write_collision_after_the_interview_parks_the_answers_in_the_spare(
        run_init, tmp_path, capsys):
    """The `.init-scaffold.md` rescue, reproduced for the Candidate Profile the same way
    `test_prose_typed_against_a_profile_that_appears_mid_interview_is_parked_not_binned` reproduces
    it for the Judging Profile: the note appears BETWEEN the preflight probe and the write, so the
    interview still ran (correctly -- nothing was there when it started) and the typed answers must
    not be silently discarded."""
    vault = tmp_path / "notes"
    script = (_skip_all_questions() + [""] + [""] * 5
              + ["Ada", "Example", "ada@example.invalid", "", ""])
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], _asker_that_plants_the_candidate_note(script, vault))
    out = capsys.readouterr().out

    assert rc == 0
    spare = vault / CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
    assert spare.exists()
    assert "Candidate Profile.init-scaffold.md" in out
    assert (vault / CANDIDATE_PROFILE_RELPATH).read_text(encoding="utf-8") == \
        "---\nforenames: Real\n---\n", "the note that won the race must survive untouched"


def test_both_the_candidate_note_and_the_spare_occupied_reports_failed(run_init, tmp_path, capsys):
    """The second collision, mirroring `test_a_second_collision_reports_the_loss_rather_than_binning_it`:
    the spare is ALSO occupied (a second such race, or an earlier abandoned run), so there is
    nowhere left to park what was typed and the command must say so loudly rather than exit 0
    having silently dropped it."""
    vault = tmp_path / "notes"
    spare_rel = CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
    spare = vault / spare_rel
    spare.parent.mkdir(parents=True)
    spare.write_text("FROM AN EARLIER RUN", encoding="utf-8")

    script = (_skip_all_questions() + [""] + [""] * 5
              + ["Ada", "Example", "ada@example.invalid", "", ""])
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], _asker_that_plants_the_candidate_note(script, vault))
    err = capsys.readouterr().err

    assert rc == 1, "losing what a human typed must not be reported as success"
    assert spare_rel in err and "NOT saved" in err
    assert spare.read_text(encoding="utf-8") == "FROM AN EARLIER RUN", \
        "the earlier run's spare must survive too"


def test_a_hostile_candidate_answer_is_reprompted_rather_than_crashing(run_init, tmp_path, capsys):
    """`_render_candidate` (onboard/plan.py) raises FrontmatterRoundTripError when an answer
    would corrupt on its way back through the real reader -- deliberately uncaught THERE. A
    surname ending in an apostrophe is the exact shape the round trip cannot survive:
    `_fm_dict`'s reader strips a value's OWN leading/trailing quote characters, so a trailing `'`
    is stripped on read-back even though `scalar()` never treated it as one on write.

    `cmd_init` must not lose the whole interview (every catalogue answer, the board walk, the five
    Judging Profile prompts already collected) to this one bad character, and must not write the
    corrupted value either -- so it re-asks the five candidate questions rather than crashing or
    silently truncating the surname. The script's second attempt answers everything blank, which
    always round-trips, so the retry resolves rather than looping."""
    vault = tmp_path / "notes"
    script = (_skip_all_questions() + [""] + [""] * 5
              # attempt 1: a surname ending in an apostrophe -- the hostile answer
              + ["", "Example'", "", "", ""]
              # attempt 2: give up cleanly, so the retry loop terminates
              + _skip_all_candidate_questions())
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], _scripted(script))
    err = capsys.readouterr().err

    assert rc == 0
    assert "surname" in err and "does not survive" in err
    assert not (vault / CANDIDATE_PROFILE_RELPATH).exists(), \
        "the hostile value must never reach disk, corrupted or otherwise"
    # The central claim this retry loop exists for: one bad candidate answer must not cost the
    # rest of the interview. Both of the other two artefacts were collected in the SAME run and
    # must have actually landed, not merely have avoided crashing.
    assert os.path.exists(config_file())
    assert (vault / CRITERIA_RELPATH).exists()


class _AskerThatStopsBeingInteractiveMidInterview:
    """Wraps a scripted `TtyAsker` but flips `.interactive` to False on its FIRST
    `ask_text_plain` call. Real askers never do this -- `TtyAsker.interactive` and
    `NoInputAsker.interactive` are both fixed class attributes, constant for the object's whole
    lifetime -- so this is the one shape that can reach the retry loop's `not asker.interactive`
    arm.

    Note WHEN the flip lands, because it is earlier than it looks: `collect_sources` reaches
    `ask_text_plain` first, for a board search label, so `interactive` is already False before
    the first candidate prompt -- every script below skips the board walk with a blank, but a
    blank still goes through `ask_text_plain`. The test reaches the intended arm regardless,
    because `cmd_init` captured `interactive` earlier and only re-reads `asker.interactive`
    inside the `except` block. Stated so the next reader does not build on an ordering that
    does not hold."""

    def __init__(self, inner):
        self._inner = inner
        self.interactive = True

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def ask_text_plain(self, prompt):
        answer = self._inner.ask_text_plain(prompt)
        self.interactive = False
        return answer


def test_the_non_interactive_retry_arm_fails_rather_than_looping_forever(run_init, tmp_path,
                                                                          capsys):
    """The defensive `if not asker.interactive:` branch inside the FrontmatterRoundTripError
    retry loop (sluice/cli.py) is unreachable through `NoInputAsker` today -- it never calls
    `collect_candidate` at all, so `candidate_answers` can never be non-empty while
    `asker.interactive` is False through that asker -- so nothing in the suite exercised the
    branch before this test, despite the code comment asserting what it does. Reachable only via
    `_AskerThatStopsBeingInteractiveMidInterview` above: an asker that answers the hostile surname
    (an interview genuinely ran) and then reports non-interactive by the time the exception is
    caught."""
    vault = tmp_path / "notes"
    script = (_skip_all_questions() + [""] + [""] * 5
              + ["", "Example'", "", "", ""])
    asker = _AskerThatStopsBeingInteractiveMidInterview(_scripted(script))
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], asker)
    err = capsys.readouterr().err

    assert rc == 1
    # The `candidate_dest` prefix, not just the message: every other `failed` entry in cmd_init is
    # `path: message`, and reverting this one back to a bare `str(exc)` (the shape it replaced)
    # leaves both substring checks below green -- both are already present inside the exception's
    # own text -- so the path itself has to be asserted for that regression to be witnessed.
    assert str(vault / CANDIDATE_PROFILE_RELPATH) in err
    assert "surname" in err and "does not survive" in err
    assert not (vault / CANDIDATE_PROFILE_RELPATH).exists(), \
        "a value that failed to round-trip must never reach disk, corrupted or otherwise"


def test_every_candidate_prompt_key_is_mapped_to_a_profile_field():
    """Guards the invariant the structural fix above (has_any_declared over the RENDERED text)
    no longer depends on for correctness, but that a maintainer adding a sixth candidate question
    could still violate silently in a different way: an answer key `collect_candidate`
    (sluice/onboard/ask.py) collects but `_render_candidate` (sluice/onboard/plan.py) has no
    mapping for is COLLECTED, asked of the user, and then never reaches the rendered note or any
    report -- a silently discarded answer, even though it can no longer cause the write-gate
    deadlock the structural fix closes. Cheap, and it catches anyone who later bypasses the
    structural route by reading the answer dict again instead of the artefact."""
    from sluice.onboard import ask, plan
    prompt_keys = set(dict(ask._CANDIDATE_PROMPTS))
    # SCOPE, not just the subset: `set() <= anything` is True, so an emptied _CANDIDATE_PROMPTS
    # (a refactor that silently drops the tuple's contents, say) would pass this vacuously.
    assert prompt_keys, "the negative check below is meaningless if this enumerated nothing"
    assert prompt_keys <= set(plan._CANDIDATE_KEY_BY_ANSWER)


def test_an_unmapped_candidate_prompt_never_produces_a_write(tmp_path, monkeypatch):
    """Reproduces the deadlock end to end against an unmapped candidate question, the exact shape
    that broke the ANSWER-dict write-gate: `_CANDIDATE_PROMPTS` patched with a sixth question
    (`cv_pronouns`) that `plan._CANDIDATE_KEY_BY_ANSWER` does not map, answered only on that field.
    Under the old gate (`any(candidate_answers.values())`) this reached all three stages of the
    documented deadlock across three runs against the SAME broken code: run 1 wrote an all-blank
    note (`_render_candidate` ignores the unmapped answer, but the gate saw something declared
    anyway); run 2's write then refused (never-clobber) and parked the same blank text in
    `.init-scaffold.md`, but the blocking note itself declares nothing, so the "exists but declares
    nothing sluice can read" diagnostic fires unconditionally and lands run 2 in `failed` too, even
    though the park itself succeeded; and run 3's park ALSO refuses (the spare from run 2 is still
    there), landing in `failed` again with the real note still empty. Reading the gate off
    `plan.candidate_text` itself makes stage 1 impossible: whatever `_render_candidate` did not
    write cannot make `has_any_declared` on the rendered text True, which is what removes stages 2
    and 3 along with it -- neither can be reached without stage 1 first. Three runs, and nothing is
    EVER written -- not merely "not yet".

    The per-run SCRIPT SIZE matters and is not the same on every iteration: config and the Judging
    Profile both land unconditionally on any successful run, so from run 2 onward `config_exists`/
    `profile_exists` are True -- the catalogue narrows to `vault_dir` (a preset, so zero
    questions), the board walk is skipped (`if not config_exists:`), and the profile interview is
    skipped (`if not profile_exists:`). Only the candidate interview still fires on every run,
    because nothing is ever declared to close ITS gate -- that persistence is the exact property
    under test, so runs 2 and 3 get the SHORT six-prompt script, not a copy of run 1's."""
    import sluice.onboard.ask as ask_module

    # No `run_init` fixture here (this test drives cmd_init through `_init`, which needs no
    # browser/vault sandbox of its own) -- but `run_init` is also what deletes $VAULT_DIR the
    # autouse `_pin_paths` fixture sets, and without that this test's `--vault` would disagree
    # with it and refuse before ever reaching the interview.
    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.setattr(ask_module, "_CANDIDATE_PROMPTS",
                        ask_module._CANDIDATE_PROMPTS + (("cv_pronouns", "Pronouns?"),))

    vault = tmp_path / "notes"
    spare = vault / CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
    # run 1: 15 catalogue + 1 board-blank + 5 profile blanks + 6 candidate answers (the last one
    #        "she/her", landing on the newly-patched `cv_pronouns`).
    # runs 2, 3: everything ahead of the candidate interview is already satisfied, so only the 6
    #        candidate prompts remain -- a script the length of run 1's would misfeed "she/her"
    #        to whichever OTHER prompt happens to sit at that offset instead.
    scripts = [
        _skip_all_questions() + [""] + [""] * 5 + ["", "", "", "", "", "she/her"],
        [""] * 5 + ["she/her"],
        [""] * 5 + ["she/her"],
    ]
    for script in scripts:
        rc = _init(["init", "--vault", str(vault)], _scripted(script))
        assert rc == 0
        assert not (vault / CANDIDATE_PROFILE_RELPATH).exists()
        assert not spare.exists()


def test_a_declared_answer_actually_lands_in_the_written_note(run_init, tmp_path):
    """The one case none of the tests above cover: a REAL write. Every other test either asserts
    the note is absent or pre-plants it so the primary write refuses -- `store.write_document`
    never returns a handle for `CANDIDATE_PROFILE_RELPATH` anywhere else in this file, so the
    actual round trip this task exists to guarantee (an answer -> a rendered note -> a note the
    NEXT run reads back and recognises) was unwitnessed. Any change to `_render_candidate`'s field
    mapping, or to what `write_document` actually persists, would have stayed green."""
    vault = tmp_path / "notes"
    script1 = _skip_all_questions() + [""] + [""] * 5 + ["", "RunOneSurname", "", "", ""]
    rc1 = _init(["init", "--vault", str(vault)], _scripted(script1))
    assert rc1 == 0
    note = (vault / CANDIDATE_PROFILE_RELPATH).read_text(encoding="utf-8")
    assert 'surname: "RunOneSurname"' in note

    # A NON-blank, distinguishable script for run 2 -- not an empty one. `TtyAsker.ask_text_plain`
    # prints its prompt UNCONDITIONALLY before reading an answer, so if `collect_candidate` were
    # wrongly invoked here (a `candidate_exists` regression) the prompt text would show up in
    # `out2` regardless of what the script answers -- an EMPTY script would leave `out2` empty
    # either way, making an absence check against it vacuous (it cannot fail for the right reason).
    # The distinguishing surname also means a wrongly-invoked interview would clear the write-gate,
    # attempt the primary write, get refused by never-clobber (run 1's note is already there), and
    # land in the spare -- giving `assert not spare.exists()` below something it could actually
    # witness too, rather than being true either way regardless of what ran.
    asker2, out2 = _prompting_asker(["", "ShouldNeverBeAsked", "", "", ""])
    rc2 = _init(["init", "--vault", str(vault)], asker2)

    assert rc2 == 0
    assert out2.getvalue() == "", \
        "a note that landed must not be re-interviewed for -- nothing should even be PRINTED"
    spare = vault / CANDIDATE_PROFILE_RELPATH.replace(".md", ".init-scaffold.md")
    assert not spare.exists()
    assert (vault / CANDIDATE_PROFILE_RELPATH).read_text(encoding="utf-8") == note, \
        "the landed note must survive run 2 byte-identical"


def test_a_hand_started_note_with_no_frontmatter_is_named_as_the_real_blocker(
        run_init, tmp_path, capsys):
    """Reproduced against the exact shape a real user is most likely to leave: a Candidate Profile
    note created BY HAND (a title, some prose to fill in later) with no frontmatter fence at all.
    `has_any_declared(store.read_candidate_profile())` on that note is False
    (`test_a_note_with_no_frontmatter_fence_returns_an_all_blank_profile`,
    tests/test_vault_candidate_profile.py), so the probe correctly says "ask again" -- but
    never-clobber refuses the primary write on FILE EXISTENCE, not on declared content, so it
    refuses on EVERY run, not merely once. Unlike the Judging Profile's equivalent trap (which
    needs a genuinely 0-byte file, since `read_criteria` treats ANY body text as declared), this
    needs nothing more than an ordinary note a human started and has not finished.

    `rc == 1`: the answers the user just typed can never reach this note through `cmd_init` again
    -- they will keep parking in the spare on every future run -- so this is reported as a real
    failure, and the message names the REAL note (not the spare) as the thing to fix."""
    vault = tmp_path / "notes"
    dest = vault / CANDIDATE_PROFILE_RELPATH
    dest.parent.mkdir(parents=True)
    dest.write_text("Notes to self: fill this in later.\n", encoding="utf-8")

    script = _skip_all_questions() + [""] + [""] * 5 + ["Ada", "", "", "", ""]
    capsys.readouterr()
    rc = _init(["init", "--vault", str(vault)], _scripted(script))
    err = capsys.readouterr().err

    assert rc == 1, "the answers can never reach the real note, and that must not read as success"
    assert str(dest) in err and "declares nothing" in err
    assert dest.read_text(encoding="utf-8") == "Notes to self: fill this in later.\n", \
        "the note a human started must never be touched"
