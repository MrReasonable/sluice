"""The post-release smoke check is itself code, so it gets the same treatment as any guard.

`scripts/smoke_installed.py` runs against a PUBLISHED artefact on a clean machine, which is by
definition somewhere this suite is not. That makes it exactly the kind of script that rots
unnoticed: it only ever runs after a release, its failure mode is "reported success", and
nobody looks at it while it is green.

What is testable offline is its JUDGEMENT -- given a described package, does it reach the right
verdict? So each check is driven directly with the state it inspects, rather than by installing
anything. The one thing deliberately NOT asserted here is the happy path against a real install:
that needs a real install, and pretending otherwise with a mock would certify the mock.
"""
import ast
import importlib.util
import inspect
import json
import os
import re
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "smoke_installed.py"


def _load():
    """Import the script by path -- it lives in `scripts/`, which is not a package."""
    spec = importlib.util.spec_from_file_location("smoke_installed", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


smoke = _load()


def _report(_name, _detail):
    """A `report` callback that records nothing -- these tests assert on raises, not output."""


def test_the_source_tree_guard_refuses_a_checkout(tmp_path, monkeypatch):
    """THE check that makes every other one meaningful, so it gets the first test.

    A smoke run executed from a checkout imports the SOURCE, passes everything, and certifies
    a package it never loaded. Simulated by pointing the guard at a `sluice/__init__.py` whose
    parent holds a `pyproject.toml`, which is what distinguishes a checkout from site-packages.
    """
    checkout = tmp_path / "repo"
    (checkout / "sluice").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='job-sluice'\n")
    init = checkout / "sluice" / "__init__.py"
    init.write_text("__version__ = '9.9.9'\n")

    fake = type(sys)("sluice")
    fake.__file__ = str(init)
    monkeypatch.setitem(sys.modules, "sluice", fake)

    with pytest.raises(smoke.SmokeFailure, match="source checkout"):
        smoke.check_not_the_source_tree(_report)


def test_the_source_tree_guard_accepts_an_installed_package(tmp_path, monkeypatch):
    """The other half. A guard that refuses everything is not a guard.

    site-packages has no `pyproject.toml` beside the package, which is the whole
    discriminator. This case also covers the false positive the first version had: a venv
    created INSIDE the working directory is still a real install, and comparing against the
    cwd rejected it.
    """
    site = tmp_path / "venv" / "lib" / "python3.14" / "site-packages"
    (site / "sluice").mkdir(parents=True)
    init = site / "sluice" / "__init__.py"
    init.write_text("__version__ = '2.2.0'\n")

    fake = type(sys)("sluice")
    fake.__file__ = str(init)
    monkeypatch.setitem(sys.modules, "sluice", fake)
    monkeypatch.chdir(tmp_path)          # cwd is an ANCESTOR of the install -- still valid

    smoke.check_not_the_source_tree(_report)   # must not raise


def test_version_disagreement_is_reported_with_every_source_named(monkeypatch):
    """Three sources must agree, and the message must say WHICH disagreed.

    The failure this catches is an artefact assembled from something other than the source it
    claims. A message saying only "version mismatch" would leave the reader to work out
    whether the CLI, the attribute or the metadata is the odd one out.
    """
    fake = type(sys)("sluice")
    fake.__version__ = "2.2.0"
    monkeypatch.setitem(sys.modules, "sluice", fake)
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (0, "job-sluice 1.0.0\n", ""))
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version", lambda _n: "2.2.0")

    with pytest.raises(smoke.SmokeFailure) as exc:
        smoke.check_version(_report, "2.2.0")
    assert "--version" in str(exc.value), "the disagreeing source must be named"
    assert "1.0.0" in str(exc.value), "the wrong value must be shown"


def test_an_empty_source_list_fails_rather_than_passing_quietly(monkeypatch):
    """A package that installs, answers `--version`, and ships no source plugins at all.

    This is the shape that reads as a healthy install while every board is silently gone --
    the same class as the extractor that returns zero rows from a live page.
    """
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (0, "", ""))
    with pytest.raises(smoke.SmokeFailure, match="did not ship or failed to autoload"):
        smoke.check_sources_load(_report, floor=10)


def test_a_populated_registry_passes(monkeypatch):
    listing = "\n".join(f"src{i}   browser   enabled" for i in range(22))
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (0, listing + "\n", ""))
    smoke.check_sources_load(_report, floor=10)      # must not raise


def test_a_nonzero_exit_is_a_failure_not_an_empty_result(monkeypatch):
    """`_run` never raises, so a crashed CLI returns `(rc, "", stderr)`. Without the explicit
    rc check that would fall through to the count comparison and report the wrong cause --
    "no sources" for a command that never ran."""
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (2, "", "boom"))
    with pytest.raises(smoke.SmokeFailure, match="exited 2"):
        smoke.check_sources_load(_report, floor=10)


def test_every_check_defined_is_registered_in_the_checks_table():
    """`CHECKS` is what `main` iterates AND what it counts, so an unregistered check does not
    run and its absence is invisible: the summary reads `all N checks passed` with a smaller N
    and the script exits 0. Every workflow job in this PR gates on that exit code alone.

    Measured before this test existed: deleting four of the five registry entries left the
    whole suite green, and the script then certified an artefact on the strength of one check.

    BOTH ENDS ARE DERIVED from the module -- the `check_*` functions it defines, and the
    registry's own contents. A hand-listed expectation is wrong within one revision and would
    be edited by the very commit that broke it.
    """
    defined = {name for name, obj in vars(smoke).items()
               if name.startswith("check_")
               and inspect.isfunction(obj)
               and obj.__module__ == smoke.__name__}
    registered = {fn.__name__ for _label, fn in smoke.CHECKS}

    # SCOPE, before the comparison: two empty sets are equal, so the assertion below is
    # satisfied by a sweep that found nothing at all. A floor is also what catches a check
    # being deleted at BOTH ends at once, which set equality alone cannot see.
    assert len(defined) >= 5, (
        f"the sweep found only {sorted(defined)} -- either it stopped seeing the check "
        "functions, or a check was deleted along with its registration")

    assert defined == registered, (
        f"defined but never registered (so never run): {sorted(defined - registered)}; "
        f"registered but not defined here: {sorted(registered - defined)}")
    assert len(smoke.CHECKS) == len(registered), (
        f"a check is registered twice: {[f.__name__ for _l, f in smoke.CHECKS]}")


def test_every_registered_check_has_a_distinct_label():
    """The label is what the reader sees in the summary and what names the failing row. Two
    checks sharing one makes the report ambiguous exactly when it is being read in anger."""
    labels = [label for label, _fn in smoke.CHECKS]
    assert len(set(labels)) == len(labels), f"duplicate label in CHECKS: {labels}"


def test_the_packaged_template_check_refuses_a_missing_template(monkeypatch):
    """Package data is the half of a build a metadata check misses, and the `.deb`/`.rpm`
    unpack a wheel into `/usr/lib/job-sluice` -- a second chance to lose it. Untested until
    now: deleting both refusals left the suite green and the check reported `ok` regardless.
    """
    class _Missing:
        def is_file(self):
            return False

        def __truediv__(self, _other):
            return self

    import importlib.resources
    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _Missing())
    with pytest.raises(smoke.SmokeFailure, match="package data did not survive"):
        smoke.check_packaged_template(_report)


def test_the_packaged_template_check_refuses_an_empty_template(monkeypatch):
    """Read as BYTES rather than merely listed: a zero-byte entry satisfies an existence check
    while breaking the renderer, which is the failure that looks most like success."""
    class _Empty:
        def is_file(self):
            return True

        def read_bytes(self):
            return b"   \n"

        def __truediv__(self, _other):
            return self

    import importlib.resources
    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _Empty())
    with pytest.raises(smoke.SmokeFailure, match="present but empty"):
        smoke.check_packaged_template(_report)


def test_the_packaged_template_check_accepts_a_real_template(monkeypatch):
    """The accept direction -- a check that refuses everything is not a check."""
    class _Good:
        def is_file(self):
            return True

        def read_bytes(self):
            return b"<html>{{ document.name }}</html>"

        def __truediv__(self, _other):
            return self

    import importlib.resources
    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _Good())
    smoke.check_packaged_template(_report)          # must not raise


def test_the_offline_commands_check_fails_when_a_command_exits_nonzero(monkeypatch):
    """Untested until now: deleting the only `rc != 0` arm left the suite green, and the check
    then reported `--help and list-sources --health both exit 0` for an install where neither
    command exists.

    A non-zero exit here means argparse could not even build its tree -- `cli.py` imports the
    evidence command package inside `_build_parser`, so a missing module fails on EVERY
    invocation rather than at first use.
    """
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (127, "", "command not found"))
    with pytest.raises(smoke.SmokeFailure, match="on a clean machine"):
        smoke.check_offline_commands(_report)


def test_the_offline_commands_check_passes_when_both_exit_zero(monkeypatch):
    """The accept direction, and it also pins that BOTH commands are actually invoked: a check
    that returned after the first would still satisfy the refusal test above."""
    seen = []

    def _fake(args, **_kw):
        seen.append(tuple(args))
        return 0, "", ""

    monkeypatch.setattr(smoke, "_run", _fake)
    smoke.check_offline_commands(_report)           # must not raise
    assert len(seen) == 2, f"both offline commands must run, got {seen}"
    assert any("--help" in a for a in seen), seen
    assert any("--health" in a for a in seen), seen


def test_the_version_check_accepts_full_agreement(monkeypatch):
    """The accept direction for the three-source comparison. Without it, a `check_version`
    hardwired to raise would satisfy the disagreement test beside it."""
    fake = type(sys)("sluice")
    fake.__version__ = "2.2.0"
    monkeypatch.setitem(sys.modules, "sluice", fake)
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (0, "job-sluice 2.2.0\n", ""))
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version", lambda _n: "2.2.0")

    smoke.check_version(_report, "2.2.0")           # must not raise


@pytest.mark.parametrize("attr,dist,cli,odd", [
    ("1.0.0", "2.2.0", "2.2.0", "sluice.__version__"),
    ("2.2.0", "1.0.0", "2.2.0", "dist metadata"),
    ("2.2.0", "2.2.0", "1.0.0", "--version"),
])
def test_each_version_source_is_compared_not_just_one(monkeypatch, attr, dist, cli, odd):
    """All THREE sources must be live. The original test drifted only the CLI, so a comparison
    that ignored the attribute or the metadata entirely would have passed it -- and the whole
    point of reading three is that a stale dist-info disagrees with the attribute it was built
    from, which this repo has already been bitten by.
    """
    fake = type(sys)("sluice")
    fake.__version__ = attr
    monkeypatch.setitem(sys.modules, "sluice", fake)
    monkeypatch.setattr(smoke, "_run", lambda *a, **k: (0, f"job-sluice {cli}\n", ""))
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version", lambda _n: dist)

    with pytest.raises(smoke.SmokeFailure) as exc:
        smoke.check_version(_report, "2.2.0")
    assert odd in str(exc.value), f"{odd} disagreed but was not named: {exc.value}"


def test_the_subprocess_environment_is_stripped_of_sluice_and_xdg_state():
    """`_run` must not hand the caller's own install to a check that claims a clean machine.

    `core/paths.py:resolve` consults an explicitly-named env var BEFORE any XDG fallback, so a
    single inherited variable retargets the run: measured, an exported `SLUICE_CONFIG` flipped
    two sources enabled->disabled, and a seeded `SLUICE_HEALTH` moved the reported baseline
    from 0 to 37. CLAUDE.md tells a developer to export `SLUICE_CONFIG`, so that is the normal
    local state and the docstrings promise a clean machine on every run.

    Asserted through a REAL subprocess rather than by reading `_clean_env`, because what
    matters is what the child actually receives.
    """
    probe = ("import os,json;"
             "print(json.dumps({k: os.environ.get(k) for k in "
             "('SLUICE_CONFIG','SLUICE_HEALTH','XDG_STATE_HOME','XDG_CONFIG_HOME','HOME')}))")
    dirty = {**os.environ,
             "SLUICE_CONFIG": "/dev/null/leaked.yaml",
             "SLUICE_HEALTH": "/dev/null/leaked.json",
             "XDG_STATE_HOME": "/dev/null/leakedstate",
             "XDG_CONFIG_HOME": "/dev/null/leakedconfig"}
    with mock.patch.dict(os.environ, dirty, clear=True):
        rc, out, _err = smoke._run([sys.executable, "-c", probe])
    assert rc == 0, out
    got = json.loads(out)
    for key in ("SLUICE_CONFIG", "SLUICE_HEALTH", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        assert got[key] is None, f"{key} reached the subprocess as {got[key]!r}"
    assert got["HOME"] == os.getcwd(), "HOME must be repointed, not merely cleared"


def test_the_environment_sweep_is_by_prefix_not_a_hand_list():
    """A hand-list is wrong within one revision -- `sluice/` reads nine `SLUICE_*` variables
    today. Pinned with names that do not exist, so the test cannot be satisfied by someone
    enumerating the current set."""
    with mock.patch.dict(os.environ, {"SLUICE_NOT_A_REAL_KNOB_YET": "x",
                                      "XDG_NOT_A_REAL_KNOB_YET": "y"}):
        env = smoke._clean_env()
    assert "SLUICE_NOT_A_REAL_KNOB_YET" not in env, "the sweep is keyed on names, not the prefix"
    assert "XDG_NOT_A_REAL_KNOB_YET" not in env, "the sweep is keyed on names, not the prefix"
    assert "PATH" in env, "the sweep must not empty the environment -- the CLI needs PATH"


def test_the_script_is_executable_and_self_contained():
    """It is copied into containers and run by an unprivileged user, so it must not import
    anything outside the standard library -- `pip install job-sluice` does not put this repo's
    test dependencies on the machine.

    Parsed with `ast`, not matched line by line, and the reason is that both text spellings
    fail. Anchored at column 0 the match sees only the four module-level imports and skips
    every function-level one -- which is all the interesting ones, so a third-party import
    added inside a check function kept this guard green (measured). Stripping each line first
    closes that and immediately opens a false positive, because a docstring in the script
    itself wraps onto a line beginning `from "they did not", ...` (also measured: it fails the
    guard on clean code). A walk over the parse tree is blind to indentation and to prose
    alike, and it sees through an alias -- `import x as y` binds `y`, which a name-keyed text
    match walks straight past.
    """
    src = _SCRIPT.read_text()
    assert os.access(_SCRIPT, os.X_OK), "smoke_installed.py must be executable"

    tree = ast.parse(src)
    kinds = (ast.Import, ast.ImportFrom)
    top_level = [n for n in tree.body if isinstance(n, kinds)]
    every = [n for n in ast.walk(tree) if isinstance(n, kinds)]

    # SCOPE, before any assertion about the contents: a sweep that discovers nothing satisfies
    # every claim made over it, and `all([])` is True. What this guard exists to inspect lives
    # BELOW module scope, so the check is that the walk found more than the top-level imports a
    # column-0 matcher already saw -- derived from the tree rather than hand-listing names that
    # rot the moment the script's imports move.
    assert len(every) > len(top_level), (
        f"the import sweep found only the {len(top_level)} module-level imports, so it cannot "
        "see the ones inside check functions -- the exact hole this guard exists to close")

    imported = set()
    for node in every:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        else:
            # A relative import cannot resolve on the target machine anyway: the script is
            # copied in on its own, never as part of a package.
            assert node.level == 0, "smoke_installed.py must not use a relative import"
            imported.add(node.module)

    allowed = {"argparse", "json", "os", "subprocess", "sys", "sluice",
               "importlib.metadata", "importlib.resources"}
    roots = {a.split(".")[0] for a in allowed}
    for name in sorted(imported):
        assert name.split(".")[0] in roots or name in allowed, (
            f"smoke_installed.py imports {name!r}, which is not standard library or sluice -- "
            "it runs on a machine that has only the published package installed")


def test_the_cli_reports_a_nonzero_exit_when_a_check_fails(tmp_path):
    """End to end, through `main`, from a directory that is not an install.

    Run as a SUBPROCESS from `tmp_path` so it behaves exactly as the workflow invokes it.

    The version asked for is one no artefact can ever claim, so at least one check fails
    whatever happens to be installed for this interpreter. The previous spelling passed the
    REAL version and relied on the ambient environment to make the run fail, which is not the
    same test on two machines: `job-sluice` sits in `.venv/bin` but is absent from an
    unactivated shell's PATH, so locally three checks raise `FileNotFoundError` while in CI --
    where the editable install IS on PATH -- those same checks run and fail on their merits.

    WHAT IS ASSERTED is the ACCOUNTING, not a particular row: the reported failure count must
    equal the number of failing rows printed. That is what makes the test independent of which
    checks fail, and it is what the original assertions did not constrain at all -- deleting
    `failures.append(...)` from EITHER except arm in `main` left the whole suite green while
    the script reported a genuinely failed check as passing (a Rule-9 silent failure).
    """
    r = subprocess.run([sys.executable, str(_SCRIPT), "999.999.999", "--channel", "unit"],
                       capture_output=True, text=True, cwd=tmp_path, timeout=120)
    assert r.returncode != 0, f"a failing smoke must exit non-zero, got 0:\n{r.stdout}"
    assert "checks failed" in r.stdout, f"the summary must state the failure count:\n{r.stdout}"

    rows = [ln for ln in r.stdout.splitlines()
            if ln.strip().startswith(("FAIL ", "ERROR "))]
    assert rows, f"the premise of this test is that something fails:\n{r.stdout}"
    m = re.search(r"^(\d+) of (\d+) checks failed", r.stdout, re.M)
    assert m, f"the summary line must state both counts:\n{r.stdout}"
    assert int(m.group(1)) == len(rows), (
        f"reported {m.group(1)} failures but printed {len(rows)} failing rows -- the count and "
        f"the rows come from different lists, so one of them is not being appended to:\n"
        f"{r.stdout}")
    assert int(m.group(2)) == len(smoke.CHECKS), (
        f"the denominator must be the whole registry, got {m.group(2)} for "
        f"{len(smoke.CHECKS)} checks:\n{r.stdout}")


def test_a_failed_check_is_counted_and_reported_not_merely_printed(monkeypatch, capsys):
    """The `SmokeFailure` arm of `main`, driven deterministically.

    The subprocess test above cannot guarantee WHICH arm runs -- that depends on whether
    `job-sluice` is on PATH -- so this pins the raise-and-count path directly. Deleting
    `failures.append(...)` from the `except SmokeFailure` arm makes `main` return 0 while
    printing a FAIL row: success reported for a check that failed.
    """
    def boom(report):
        raise smoke.SmokeFailure("deliberate")

    monkeypatch.setattr(smoke, "CHECKS", (("planted", boom),))
    rc = smoke.main(["9.9.9", "--channel", "unit"])
    out = capsys.readouterr().out
    assert rc == 1, f"a failed check must exit non-zero, got {rc}:\n{out}"
    assert "FAIL  planted: deliberate" in out, f"the failure must be shown:\n{out}"
    assert "1 of 1 checks failed" in out, f"the failure must be COUNTED:\n{out}"


def test_an_unexpected_exception_is_counted_too(monkeypatch, capsys):
    """The other arm. A check raising something unexpected means the package could not even be
    interrogated, which is a finding -- not a reason to carry on and report success."""
    def boom(report):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(smoke, "CHECKS", (("planted", boom),))
    rc = smoke.main(["9.9.9", "--channel", "unit"])
    out = capsys.readouterr().out
    assert rc == 1, f"an erroring check must exit non-zero, got {rc}:\n{out}"
    assert "ERROR planted: RuntimeError: unexpected" in out, f"the type must survive:\n{out}"
    assert "1 of 1 checks failed" in out, f"the error must be COUNTED:\n{out}"


def test_all_checks_passing_exits_zero(monkeypatch, capsys):
    """The accept direction. A gate that fails everything is not a gate, and the two tests
    above would both pass against a `main` hardwired to return 1."""
    monkeypatch.setattr(smoke, "CHECKS", (("planted", lambda report: report("planted", "ok")),))
    rc = smoke.main(["9.9.9", "--channel", "unit"])
    out = capsys.readouterr().out
    assert rc == 0, f"a clean run must exit 0, got {rc}:\n{out}"
    assert "all 1 checks passed" in out, out
