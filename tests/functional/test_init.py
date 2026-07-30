"""`sluice init` through the real `main(argv)`."""
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


def test_the_written_config_loads_and_abstains(run_init, tmp_path):
    from sluice.core.config import load_config
    from sluice.triage.config import load_triage_config
    run_init(["init", "--vault", str(tmp_path / "notes"), "--no-input"])
    path = config_file()
    assert load_config(path).relevance_keep == []
    assert load_triage_config(path).accept_titles == []
    assert load_config(path).lead_ttl_days == 0
