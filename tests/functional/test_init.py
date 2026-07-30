"""`sluice init` through the real `main(argv)`."""
import io
import os

from sluice.core.paths import config_file
from sluice.core.protocols import CRITERIA_RELPATH
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
