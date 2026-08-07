"""The version has exactly ONE home, and the CLI can report it (#12).

These outlive the hand-rolled tag guard they were written beside: release-please keeps the
tag and the declared version in sync BY CONSTRUCTION (it writes both), so a guard asserting
they agree became a check that could not fail. What still can fail is the wiring below --
someone re-adding a literal version to pyproject.toml, or the build ceasing to read the
attribute -- and that is what release-please's `extra-files` annotation depends on.
"""
import re
from pathlib import Path

import pytest


def test_pyproject_declares_no_literal_version():
    """`version` was declared in pyproject.toml AND sluice/__init__.py with nothing tying
    them together, so a release could bump one, ship the other, and `pip show sluice` would
    disagree with `sluice --version` in silence.

    pyproject is now `dynamic`, reading the attribute, and release-please updates that one
    file. A literal here reintroduces the second home and the drift with it.
    """
    import tomllib

    raw = (Path(__file__).parent.parent / "pyproject.toml").read_bytes()
    project = tomllib.loads(raw.decode("utf-8"))["project"]
    assert "version" not in project, (
        "pyproject.toml declares a literal version again. It must stay in `dynamic` and be "
        "read from sluice.__version__, or the two drift apart at exactly the moment nobody "
        "re-checks: cutting a release.")
    assert "version" in project.get("dynamic", []), "version is neither literal nor dynamic"


def test_the_version_line_carries_the_release_please_annotation():
    """release-please rewrites the version by finding an ANNOTATED line, not by parsing
    Python. Strip the annotation and it silently stops bumping: the release PR still opens,
    the changelog still updates, and only the shipped version is stale -- which is the
    failure mode this repo keeps engineering out, so it gets a row rather than a comment.
    """
    text = (Path(__file__).parent.parent / "sluice" / "__init__.py").read_text(
        encoding="utf-8")
    version_lines = [ln for ln in text.splitlines() if ln.startswith("__version__")]
    assert len(version_lines) == 1, f"expected exactly one __version__ line, got {version_lines}"
    assert "x-release-please-version" in version_lines[0], (
        "the __version__ line lost its `# x-release-please-version` annotation; "
        "release-please updates the line by that marker and will silently skip it.")


def test_the_installed_metadata_matches_the_declared_attribute():
    """The property the dynamic wiring buys, asserted on the INSTALLED package rather than
    the source -- that is where the two would diverge if the build stopped reading it."""
    import importlib.metadata

    import sluice

    assert importlib.metadata.version("sluice") == sluice.__version__, (
        "installed metadata and sluice.__version__ disagree. If you have just pulled a "
        "release bump, the editable install's dist-info is frozen at install time -- "
        're-run `pip install -e ".[test]"`. Otherwise the build has stopped reading the '
        "attribute, which is the drift the dynamic version exists to prevent.")


def test_cli_version_flag_prints_the_declared_version(capsys):
    """`sluice --version` is what a user pastes into a bug report.

    It must not require a subcommand: the root parser marks the subcommand group
    `required=True`, and argparse's version action fires during parsing and exits before
    that check is reached.

    This pins the PROPERTY, not a placement. Moving the `add_argument` call below
    `add_subparsers` leaves this green and the behaviour identical -- argparse optionals
    are position-independent, so there is no ordering here to assert. Deleting the flag is
    what reds it, with `the following arguments are required: group`.
    """
    import sluice
    from sluice.cli import main

    with pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0
    assert capsys.readouterr().out.strip() == f"sluice {sluice.__version__}"


# ── the release-please config's couplings to THIS repo (#12) ─────────────────────
#
# The schema itself is third-party and unverifiable offline, so it gets no test. These two
# couplings are ours, cheap, and fail SILENTLY -- the release PR still opens and the
# changelog still updates while the shipped version goes stale or backwards.


def _rp_config():
    import json

    return json.loads(
        (Path(__file__).parent.parent / "release-please-config.json").read_text(
            encoding="utf-8"))["packages"]["."]


# What release-please actually rewrites: a line carrying the marker AND a quoted version.
# Matching the marker ALONE swept up every file that merely NAMES it -- the canonical rules
# file documents the mechanism, and rulesync regenerates that prose into CLAUDE.md and
# AGENTS.md, so the first version of this sweep demanded three documentation files be added
# to `extra-files`. Asserted through the shape the tool consumes, not the substring a human
# would grep for.
def _rewritable_marker_line(line: str) -> bool:
    """Would release-please rewrite this line? Pure.

    Both halves required: the marker, and a quoted semver on the SAME line. Prose that
    mentions the marker carries no quoted version beside it, which is what separates a file
    release-please edits from a file that documents that it does.
    """
    return "x-release-please-version" in line and bool(
        re.search(r"""["']\d+\.\d+\.\d+["']""", line))


def _is_generated_relpath(rel_parts: tuple) -> bool:
    """True for a path (already made RELATIVE to the repo root) this sweep must skip.

    Pulled out of `_files_carrying_the_marker` as its own pure function so the ancestor-
    directory regression below can assert on it directly, with synthetic `Path` objects,
    rather than needing a real checkout nested under a directory named `build`/`dist` to
    reproduce.

    "build" and "dist" are the DoD's own trap: a repo-root `python -m build` drops
    `build/lib/sluice/__init__.py` (a copy of the real file, marker and all) into a
    directory `.gitignore` already excludes -- so `git status` stays clean while this
    sweep, walking the real filesystem rather than git's index, finds a SECOND
    marker-carrying file `extra-files` never lists and fails permanently with an error
    about release-please that says nothing about wheels. Measured 2026-08-06: running
    the DoD's required `python -m build` from repo root reproduces this exactly, and
    cleaning the stray `build/` afterwards is what makes the suite green again -- skip
    both so a routine, DoD-instructed build cannot arm a permanent trap for whoever runs
    it next.

    `rel_parts` MUST already be relative to the repo root, not `path.parts`: `rglob`
    yields paths PREFIXED with the root, so the bare `.parts` form would walk every
    ANCESTOR of the checkout too -- a repo cloned under a directory literally named
    `build` or `dist` (plausible: both are common names for a parent workspace folder)
    would match on that ancestor and skip the ENTIRE sweep, silently, the same "sweep
    discovers nothing and passes" shape CLAUDE.md names as its own guard-testing failure
    mode. Only `build`/`dist` need root-scoping (`.git`/`.venv`/etc. never legitimately
    sit at the repo root as a real tracked directory, so matching them anywhere is safe).
    """
    if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in rel_parts):
        return True
    return rel_parts[:1] in (("build",), ("dist",))


def _files_carrying_the_marker():
    """Every file with a REWRITABLE marker line, found by WALKING.

    Enumerated rather than hand-listed: naming `sluice/__init__.py` here would make this
    test agree with the config by construction and see nothing when the real file moves.
    """
    root = Path(__file__).parent.parent
    skip = {root / "release-please-config.json", Path(__file__)}
    out = set()
    for path in root.rglob("*"):
        if not path.is_file() or path in skip:
            continue
        if _is_generated_relpath(path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(_rewritable_marker_line(ln) for ln in text.splitlines()):
            out.add(path.relative_to(root).as_posix())
    return out


def test_a_repo_root_named_build_does_not_blind_the_whole_sweep():
    """CodeRabbit's cloud review on PR #97: the sweep used to check `path.parts`
    (every ancestor of the checkout, since `rglob` yields paths PREFIXED with the root)
    rather than the path RELATIVE to the root. A checkout sitting under a directory
    literally named `build` or `dist` -- a plausible parent-workspace name -- would have
    matched on that ancestor and silently excluded the ENTIRE tree, the "sweep discovers
    nothing and passes" failure shape this repo has already been bitten by. `sluice/`
    itself must never be excluded merely for living under such a parent.
    """
    assert not _is_generated_relpath(("sluice", "__init__.py")), (
        "an ordinary tracked file was excluded")
    assert not _is_generated_relpath(("README.md",))
    # The regression: rel_parts is ALREADY relative to root, so a `build`/`dist` ANCESTOR
    # never appears in it at all -- there is nothing here for the old ancestor-matching
    # bug to latch onto, which is exactly what makes the fix correct.
    assert _is_generated_relpath(("build", "lib", "sluice", "__init__.py")), (
        "a real build/ artefact at the repo root was not excluded"
    )
    assert _is_generated_relpath(("dist", "sluice-0.1.0.whl"))
    # And the one case the fix must NOT over-exclude: "build" or "dist" appearing
    # somewhere OTHER than the first path component is an ordinary tracked path, not the
    # generated root directory this sweep exists to skip.
    assert not _is_generated_relpath(("sluice", "cv", "build_bundle_helpers.py")), (
        "a tracked file merely CONTAINING 'build' in its name was wrongly excluded"
    )


def test_extra_files_names_every_file_carrying_the_marker():
    """release-please rewrites a file only if `extra-files` LISTS it AND the line carries
    the marker. The two are independent, so they drift silently: rename or move
    `sluice/__init__.py` and the annotation test stays green while nothing gets bumped.
    """
    marked = _files_carrying_the_marker()
    assert marked, "no file carries the marker: this sweep would pass without checking"
    listed = set(_rp_config().get("extra-files", []))
    assert marked <= listed, (
        f"files carry `x-release-please-version` but are not in `extra-files`: "
        f"{sorted(marked - listed)}. release-please will not rewrite them, so the shipped "
        f"version goes stale while the release PR looks healthy.")


def test_the_manifest_agrees_with_the_declared_version():
    """The manifest is release-please's record of the last released version. If it drifts
    BELOW the declared one, the next release rewrites `__version__` backwards; above, and
    the release is numbered past what was ever shipped. Either way silently.
    """
    import json

    import sluice

    manifest = json.loads(
        (Path(__file__).parent.parent / ".release-please-manifest.json").read_text(
            encoding="utf-8"))
    assert manifest["."] == sluice.__version__, (
        f'.release-please-manifest.json says {manifest["."]}, sluice/__init__.py declares '
        f"{sluice.__version__}. release-please treats the manifest as the last release and "
        f"computes the next version from it, so a drift here moves the shipped version to "
        f"the wrong place with nothing red.")
