"""No test may reach a repository through git's inherited location variables.

`git rebase -x` (and any git hook) exports GIT_DIR to the command it runs, so a test that runs
`git init` or `git add` in its own tmp folder acts on whatever repository GIT_DIR names. Measured
2026-10-10: a per-commit `rebase -x` run of this suite staged a test's `a.txt` in the worktree's
index and set `core.bare = true` in the shared config, which broke the main checkout.
`tests/conftest.py::_no_inherited_git_location` removes those variables for every test; this runs a
real git-using test in a child pytest with GIT_DIR aimed at a throwaway repository and proves the
throwaway repository is untouched.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROBE = ("tests/test_no_leaked_files.py::"
          "test_the_windows_gate_catches_every_separator_form_through_git")


def test_a_git_using_test_never_touches_the_repository_git_dir_names(tmp_path):
    # A NON-bare decoy: under a leaked GIT_DIR the probe's `git add` then SUCCEEDS against it
    # and lands in its index, so the index check below is what fails, not a crash before it.
    decoy = tmp_path / "decoy" / ".git"
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(decoy.parent)], check=True, env=clean)
    config_before = (decoy / "config").read_bytes()
    env = {**clean, "GIT_DIR": str(decoy)}
    child = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _PROBE],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert not (decoy / "index").exists(), "a test staged a file in the repository GIT_DIR names"
    assert (decoy / "config").read_bytes() == config_before
    assert child.returncode == 0, child.stdout[-2000:] + child.stderr[-2000:]
