"""Guards for the deb/rpm channel (#104, PR 5): nfpm.yaml, the shim, and the staging script.

WHERE THE BOUNDARY IS, stated because it was folklore and folklore is how an assertion got
lost. This module owns the ARTEFACTS -- nfpm.yaml, packaging/job-sluice,
scripts/build_linux_packages.py -- and reads no workflow file. tests/test_release_publish_wiring.py
owns the WORKFLOW, and reaches into nfpm.yaml and the stager only where it must compare the two
sides of an agreement (the packaging output directory). When the workflow probes were moved out
of this module in review, the assertion tying the job to the staging script was dropped rather
than relocated, and the import-set guard here went on proving the script clean while nothing
proved the job ran it. If a guard needs both sides, it belongs in the wiring module and must
compare parsed VALUES, not file text.

Parses YAML rather than text-matching, per tests/test_docker_channel.py's stated rule: parse
when the guard needs YAML's OWN semantics. It does here -- the whole point of this config is
the `overrides:` mapping, whose per-packager nesting is exactly the structure a text match
would have to re-derive by hand, and whose `mode: 0755` is an octal scalar YAML resolves.

No real `nfpm package` runs here. nfpm is a Go binary this suite does not have and would need
the network to fetch, and the suite is deliberately offline. The real build runs in CI's
`linux-packages` job; the packages were additionally installed and exercised for real in
debian:13-slim, ubuntu:24.04 and fedora:41 while this channel was written -- `--version`,
`doctor --offline`, and a rendered PDF on each distro's own WeasyPrint.

WHY THE DEPENDENCY NAMES ARE PINNED AT ALL. They diverge by packager and there is no way to
derive one from the other: yaml is `python3-yaml` on Debian and Ubuntu but `python3-pyyaml` on
Fedora, and WeasyPrint is `weasyprint` on Debian/Ubuntu but `python3-weasyprint` on Fedora.
Each was verified against the real distro index rather than assumed. A wrong name in `depends`
fails the install loudly; a wrong name in `recommends` does NOT -- apt skips an unsatisfiable
Recommends with a note, so the render extra would simply be absent on every install of that
family, which is the silent half and the reason these are pinned rather than trusted.
"""
import pathlib
import re
import tomllib

import yaml

# `scripts/` is a package (`scripts/__init__.py`) and the repo root is on sys.path under pytest,
# so this is a plain import -- the idiom tests/test_guard_rulesync_drift.py documents and the
# other scripts-importing test modules already follow. No sys.path manipulation. (Deliberately
# no COUNT of those modules: a number in prose is a drift surface this repo has been bitten by
# more than once, and the first spelling of this line was already off by one.)
from scripts.build_linux_packages import find_wheel, stage

ROOT = pathlib.Path(__file__).parent.parent
NFPM = ROOT / "nfpm.yaml"
SHIM = ROOT / "packaging" / "job-sluice"
STAGER = ROOT / "scripts" / "build_linux_packages.py"
PYPROJECT = ROOT / "pyproject.toml"

# Where the unpacked wheel lands. Named once here and compared against BOTH sides -- nfpm's
# `dst` and the shim's sys.path line -- rather than hardcoded in each assertion, so the test
# cannot agree with itself while the two files disagree with each other.
LIB_DIR = "/usr/lib/job-sluice"


def _config() -> dict:
    return yaml.safe_load(NFPM.read_text())


def _override(packager: str, key: str) -> list[str]:
    """One packager's dependency list, asserted non-empty.

    Empty is the fail-open case: every membership assertion below is vacuously satisfiable
    against a list that is missing entirely, so `.get(...)` returning None must fail here
    rather than downstream.
    """
    overrides = _config().get("overrides") or {}
    assert packager in overrides, (
        f"nfpm.yaml declares no `{packager}` override. The dependency names diverge by "
        f"packager, so each family needs its own list -- a shared default would be silently "
        f"wrong for whichever one it did not match."
    )
    value = overrides[packager].get(key)
    assert value, f"nfpm.yaml's {packager} override declares no non-empty `{key}`"
    return value


def _python_floor() -> str:
    """The `>=X.Y` floor from pyproject.toml's requires-python.

    DERIVED, never repeated. The floor appears in two dependency lists here and in
    pyproject.toml; a version bump that updated only pyproject would otherwise leave the
    packages declaring an interpreter the code no longer supports, and the install would
    succeed onto it.
    """
    requires = tomllib.loads(PYPROJECT.read_text())["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(\d+\.\d+)", requires.strip())
    assert match, f"pyproject.toml's requires-python is {requires!r}, not a simple >=X.Y floor"
    return match.group(1)


def test_packages_are_architecture_independent():
    """`all` is what makes this channel two files instead of a build matrix.

    It is only correct because nothing is vendored: `sluice/` is pure Python and both runtime
    dependencies come from the distro. Vendoring pyyaml instead would ship its C extension,
    and the package would silently become specific to an architecture AND a Python minor
    while still declaring itself portable.
    """
    assert _config()["arch"] == "all", (
        f"nfpm.yaml's arch is {_config()['arch']!r}, not 'all' -- it emits `Architecture: all` "
        f"for the deb and `BuildArch: noarch` for the rpm"
    )


def test_the_shared_config_declares_no_dependencies_of_its_own():
    """Names diverge by packager, so a top-level list would apply the wrong one to whichever
    family it did not match. Stated as an absence because that is what the design is: not a
    shared default patched per packager, but no default at all."""
    config = _config()
    for key in ("depends", "recommends"):
        assert key not in config, (
            f"nfpm.yaml declares a top-level `{key}`. Package names diverge by packager "
            f"(python3-yaml vs python3-pyyaml, weasyprint vs python3-weasyprint), so each "
            f"belongs under its own override."
        )


# Each PyPI runtime dependency and how the two families spell it. A TABLE rather than a check
# per dependency, because the test below asserts the table covers pyproject's `dependencies`
# EXACTLY -- so adding a third runtime dependency fails the build here until someone decides
# what it is called on Debian and on Fedora, instead of silently shipping a package that
# declares two of three. `tzdata` is the same word on both.
_DISTRO_SPELLINGS = {
    "pyyaml": ("python3-yaml", "python3-pyyaml"),
    "tzdata": ("tzdata", "tzdata"),
}

# The same shape for the `render` extra, which the packages RECOMMEND rather than depend on.
# `weasyprint` is the binary package name on Debian 13 and Ubuntu 24.04; Fedora uses the
# python3- prefix. jinja2 agrees across all three.
_RENDER_SPELLINGS = {
    "weasyprint": ("weasyprint", "python3-weasyprint"),
    "jinja2": ("python3-jinja2", "python3-jinja2"),
}


def _runtime_dependencies() -> set[str]:
    """pyproject.toml's `[project] dependencies`, reduced to bare distribution names."""
    raw = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    return {re.split(r"[<>=!~;\[ ]", spec)[0].strip().lower() for spec in raw}


def test_both_packagers_declare_every_runtime_dependency_from_pyproject():
    """DERIVED from pyproject, not hand-listed -- and that is the whole point of its shape.

    An earlier version of this test named pyyaml directly. It therefore could not see that
    `tzdata`, the second hard runtime dependency, was declared for pip and Docker and dropped
    for this channel: the wheel is unpacked with `zipfile`, so no resolution happens here and
    nothing supplied it. A hand-listed guard is exactly as complete as the day it was written.

    These are `depends`, not `recommends`: sluice's config modules import yaml, and an install
    without it dies on the first command rather than degrading.
    """
    runtime = _runtime_dependencies()
    # SCOPE. Without this the loop below is satisfied by a table covering one dependency out of
    # three -- the failure this test exists to prevent, in the guard rather than the config.
    assert runtime == set(_DISTRO_SPELLINGS), (
        f"pyproject.toml's runtime dependencies are {sorted(runtime)} but this table maps "
        f"{sorted(_DISTRO_SPELLINGS)}. Unmapped: {sorted(runtime - set(_DISTRO_SPELLINGS))}. "
        f"Every runtime dependency needs a deb and an rpm spelling, or the packages declare "
        f"fewer dependencies than the wheel does."
    )
    for packager, index in (("deb", 0), ("rpm", 1)):
        declared = _override(packager, "depends")
        for dist, spellings in sorted(_DISTRO_SPELLINGS.items()):
            assert spellings[index] in declared, (
                f"the {packager} does not declare {spellings[index]!r}, the {packager} spelling "
                f"of pyproject's {dist!r} runtime dependency. Declared: {declared}."
            )
    # The mirror image, and the half that catches a copy-paste between the two blocks: a
    # family-specific spelling must NOT appear in the other's list, where it resolves to
    # nothing. Skipped where both families agree, since there is nothing to confuse.
    for dist, (deb_name, rpm_name) in sorted(_DISTRO_SPELLINGS.items()):
        if deb_name == rpm_name:
            continue
        assert rpm_name not in _override("deb", "depends"), (
            f"the deb declares {rpm_name!r}, which is Fedora's spelling of {dist!r}"
        )
        assert deb_name not in _override("rpm", "depends"), (
            f"the rpm declares {deb_name!r}, which is Debian's spelling of {dist!r}"
        )


def test_the_wheel_tree_carries_no_blanket_mode():
    """The tree entry must NOT set `file_info.mode`, and the absence is the fix.

    An earlier revision set `mode: 0644` here to stop the installed files inheriting the build
    runner's umask. nfpm carries a tree's file_info onto the DIRECTORY entries it synthesises
    too, so every directory under /usr/lib/job-sluice shipped with no search bit -- measured as
    `drw-r--r--` in the built .deb, and on a real install as `ModuleNotFoundError: No module
    named 'sluice'` for an ordinary user, while root (who bypasses directory traversal checks)
    saw a working CLI.

    Modes are normalised in the stager instead, which the test below exercises for real.
    """
    trees = [entry for entry in _config()["contents"] if entry.get("type") == "tree"]
    assert len(trees) == 1, f"expected exactly one `type: tree` entry, got {trees}"
    assert "mode" not in trees[0].get("file_info", {}), (
        "the unpacked-wheel tree must not set a blanket file_info.mode: nfpm applies it to the "
        "directories it synthesises as well, which strips their search bit and makes the "
        "package unusable for every non-root user"
    )


def test_the_stager_normalises_modes_so_directories_stay_traversable(tmp_path):
    """Executed, not asserted from the YAML. This is the regression test for the round-1 fix
    that broke the package for non-root users, so it checks the DIRECTORY case specifically --
    the case a root-only container run cannot see.

    Built through the real `stage()` against a real wheel-shaped zip, under a deliberately
    hostile umask, because the whole point is that the result must not depend on it.
    """
    import os
    import zipfile as zf

    wheel = tmp_path / "job_sluice-0.0.0-py3-none-any.whl"
    with zf.ZipFile(wheel, "w") as archive:
        archive.writestr("sluice/__init__.py", "__version__ = '0.0.0'\n")
        archive.writestr("sluice/core/paths.py", "\n")
        archive.writestr("job_sluice-0.0.0.dist-info/METADATA", "Name: job-sluice\n")

    out = tmp_path / "lib"
    previous = os.umask(0o077)          # the strict umask that motivated the original fix
    try:
        # `stage()` is INSIDE the try, so its own failure is covered by the cleanup below.
        # Previously it ran ahead of the block, and a stage() raising part-way through its
        # chmod loop stranded an unsearchable tree in the shared pytest-of-<user> base --
        # which then warned in later, unrelated sessions. Observed while witnessing a mutant.
        stage(wheel, out)

        directories = [p for p in [out, *out.rglob("*")] if p.is_dir()]
        files = [p for p in out.rglob("*") if p.is_file()]
        # Scope first: an extraction that produced nothing would satisfy every mode check below.
        assert len(directories) >= 3 and len(files) >= 3, (
            f"expected a staged tree with directories and files, got {len(directories)} dir(s) "
            f"and {len(files)} file(s)"
        )
        for directory in directories:
            mode = directory.stat().st_mode & 0o777
            assert mode == 0o755, (
                f"{directory} staged as {mode:04o}, not 0755. Without the search bit nothing "
                f"underneath it can be reached by a non-root user, and `import sluice` fails."
            )
        for path in files:
            mode = path.stat().st_mode & 0o777
            assert mode == 0o644, f"{path} staged as {mode:04o}, not 0644"
    finally:
        # Restore traversable modes before leaving. When this test FAILS it is precisely
        # because the tree carries unsearchable directories, and pytest's tmp_path cleanup
        # then hits PermissionError and warns on every later run in the session -- observed,
        # while witnessing the 0644-directory mutant this test exists to catch. Walked
        # top-down, chmodding each directory before descending into it, since an unsearchable
        # one cannot otherwise be listed.
        os.umask(previous)
        if out.exists():
            out.chmod(0o700)
            for parent, names, _ in os.walk(out):
                for name in names:
                    os.chmod(os.path.join(parent, name), 0o700)


def test_both_packagers_declare_the_python_floor_from_pyproject():
    floor = _python_floor()
    deb = [d for d in _override("deb", "depends") if d.startswith("python3 ")]
    rpm = [d for d in _override("rpm", "depends") if d.startswith("python3 ")]
    assert deb == [f"python3 (>= {floor})"], (
        f"the deb must depend on python3 (>= {floor}) -- Debian's dependency grammar, with "
        f"parentheses -- derived from pyproject.toml's requires-python. Found {deb}."
    )
    assert rpm == [f"python3 >= {floor}"], (
        f"the rpm must depend on python3 >= {floor} -- rpm's grammar, WITHOUT parentheses, "
        f"which is not Debian's. Found {rpm}."
    )


def test_each_packager_recommends_every_member_of_the_render_extra():
    """The silent half, and DERIVED from pyproject for the same reason `depends` is.

    An unsatisfiable Recommends does not fail an install -- apt notes it and moves on -- so
    naming Fedora's spelling in the deb would leave every Debian-family install without the
    render extra, and nothing would say so. That is the whole reason this channel exists over
    pip, so it is pinned rather than trusted.

    Hand-listing is what let `tzdata` go missing from `depends` for two review rounds: a guard
    that names the packages it already knows about cannot report the one nobody added. This
    reads `[project.optional-dependencies] render` and requires a mapped spelling for each
    member, so growing that extra fails the build here rather than shipping a package that
    recommends two of three.
    """
    render = {re.split(r"[<>=!~;\[ ]", spec)[0].strip().lower()
              for spec in tomllib.loads(PYPROJECT.read_text())
              ["project"]["optional-dependencies"]["render"]}
    assert render == set(_RENDER_SPELLINGS), (
        f"pyproject's `render` extra is {sorted(render)} but this table maps "
        f"{sorted(_RENDER_SPELLINGS)}. Unmapped: {sorted(render - set(_RENDER_SPELLINGS))}. "
        f"Every member needs a deb and an rpm spelling, or the packages recommend fewer than "
        f"the extra installs."
    )
    for packager, index in (("deb", 0), ("rpm", 1)):
        recommends = _override(packager, "recommends")
        for dist, spellings in sorted(_RENDER_SPELLINGS.items()):
            assert spellings[index] in recommends, (
                f"the {packager} does not recommend {spellings[index]!r}, its spelling of the "
                f"render extra's {dist!r}. Recommended: {recommends}."
            )
    assert "python3-weasyprint" not in _override("deb", "recommends"), (
        "python3-weasyprint does not exist on Debian 13 or Ubuntu 24.04; the binary package "
        "is `weasyprint` on both. Naming the Fedora spelling here is an unsatisfiable "
        "Recommends that apt skips silently."
    )


def test_neither_packager_recommends_the_c_libraries_instead_of_weasyprint():
    """#104's scope note said to recommend the cairo/pango runtime libraries. That is one
    layer too low, and this pins the correction: weasyprint's own Depends already pull
    libpango-1.0-0, libpangoft2-1.0-0 and shared-mime-info (verified against Debian 13's
    index), so naming those here WITHOUT weasyprint installs the C libraries and still leaves
    `import weasyprint` failing.
    """
    for packager in ("deb", "rpm"):
        recommends = _override(packager, "recommends")
        libs = [r for r in recommends if r.startswith(("libcairo", "libpango", "libgdk", "libffi"))]
        assert not libs, (
            f"the {packager} recommends C libraries directly: {libs}. Recommend the "
            f"WeasyPrint package instead and let its own dependencies pull them."
        )


def test_the_wheel_lands_where_the_shim_looks_for_it():
    """The one place these two files can silently disagree. nfpm copies the unpacked wheel to
    a `dst`; the shim inserts a hardcoded path at the front. If they drift, the package installs
    and every invocation dies on ImportError -- a failure that appears only after release.
    """
    contents = _config()["contents"]
    trees = [entry for entry in contents if entry.get("type") == "tree"]
    assert len(trees) == 1, f"expected exactly one `type: tree` entry in contents, got {trees}"
    assert trees[0]["dst"] == LIB_DIR
    assert f'sys.path.insert(0, "{LIB_DIR}")' in SHIM.read_text(), (
        f"packaging/job-sluice must insert {LIB_DIR!r} at position 0 -- the same path nfpm.yaml "
        f"installs the unpacked wheel to, and FIRST so that a leftover `pip install job-sluice` "
        f"in site-packages cannot win over the code this package shipped"
    )


def test_the_shim_is_installed_executable_at_usr_bin():
    contents = _config()["contents"]
    shims = [entry for entry in contents if entry["dst"] == "/usr/bin/job-sluice"]
    assert len(shims) == 1, f"expected exactly one /usr/bin/job-sluice entry, got {shims}"
    # 0755 in YAML 1.1 is octal, so safe_load yields 493. Asserted as 0o755 so the intent is
    # legible rather than as the decimal it happens to resolve to.
    assert shims[0]["file_info"]["mode"] == 0o755, (
        "the shim must be installed executable; without the mode it lands 0644 and "
        "`job-sluice` is not runnable at all"
    )


def test_the_shim_names_the_interpreter_the_package_depends_on():
    """`/usr/bin/env python3` would resolve against the invoking user's PATH, so a pyenv,
    conda or asdf shim earlier on it would run this CLI on an interpreter nothing declared --
    possibly below the floor the package's own Depends guarantees.
    """
    first = SHIM.read_text().splitlines()[0]
    assert first == "#!/usr/bin/python3", (
        f"the shim's shebang is {first!r}; it must name /usr/bin/python3 directly, not go "
        f"through env"
    )


def _executable_source(path: pathlib.Path) -> str:
    """`path`'s source with comments and docstrings removed.

    A forbidden-pattern sweep must read what EXECUTES. Scanning the raw text instead matches
    the prose that EXPLAINS the ban -- this module's first draft failed exactly that way, on
    the staging script's own header sentence saying it must never `pip install` -- and the
    tempting fix is to reword the comment, which trades a correct explanation for a green
    test and leaves the sweep just as unable to tell code from commentary.

    `ast.unparse` drops comments on its own; docstrings survive it as ordinary string
    expressions, so they are removed explicitly first.
    """
    import ast

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


# Every module the stager may import. An EQUALITY pin, not a denylist, and the shape is the
# point: a denylist of forbidden module names is only as good as the names someone thought to
# forbid, while an allow-list forces any new import -- subprocess, urllib, socket, http, an
# installer library -- to be added here deliberately, in a diff a reviewer reads. Every entry
# is stdlib and none can reach an index.
_STAGER_IMPORTS = {"argparse", "pathlib", "shutil", "sys", "zipfile"}


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Top-level module names `path` imports, read from its AST rather than its text.

    `__import__` and `*.import_module` CALLS are reported as the sentinel `<dynamic>` rather
    than ignored. Reading only `Import`/`ImportFrom` left both invisible, and that gap was
    measured: a stager keeping its five declared imports and adding
    `__import__("urllib.request").request.urlopen(...)` satisfied the equality pin outright.

    THE LIMIT, stated because the previous version of this docstring implied more than the code
    does: this is not a sandbox and does not prove the script cannot reach a network. `eval`,
    `exec`, and `globals()["__builtins__"]["__import__"]` all still pass -- measured. What it
    does prove is that the script's DECLARED dependencies are five stdlib modules and that the
    two ordinary dynamic-import spellings are absent, which is the realistic drift (someone
    reaching for pip because it seemed easier), not an adversary evading a guard in a file they
    would have to get through review to change anyway.
    """
    import ast

    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # `level` is non-zero for a relative import and `module` is None for
            # `from . import x`; neither has a resolvable top-level name to record.
            modules.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                modules.add("<dynamic>")
            elif isinstance(func, ast.Attribute) and func.attr in {"import_module", "__import__"}:
                modules.add("<dynamic>")
    return modules


def test_the_package_is_built_from_the_built_wheel_and_never_from_an_index():
    """The same hard invariant the Dockerfile carries. Installing from PyPI here would race
    the `pypi` job in the same release and either fail outright or silently ship the PREVIOUS
    version under this release's tag.

    PINNED ON THE IMPORT SET, not on forbidden phrases. An earlier version of this test swept
    the comment-stripped source for "pip install", "pip download", "--index-url" and
    "--extra-index-url". It FAILED OPEN, shown by execution rather than argued: a stager
    calling `subprocess.run([sys.executable, "-m", "pip", "install", "--target", ...])` matches
    none of those four strings, because the words are separate list elements and never form a
    contiguous substring. The guard passed on a script that resolves from PyPI -- the exact
    thing it exists to forbid. A phrase sweep catches only the spellings someone imagined; the
    import set is what a resolver actually needs.
    """
    modules = _imported_modules(STAGER)
    assert modules == _STAGER_IMPORTS, (
        f"scripts/build_linux_packages.py imports {sorted(modules)}, expected "
        f"{sorted(_STAGER_IMPORTS)}. Unexpected: {sorted(modules - _STAGER_IMPORTS)}. This "
        f"package is built from the wheel the `build` job produced, never resolved from an "
        f"index -- anything that could fetch or install (subprocess, urllib, http, socket) "
        f"must not appear here."
    )
    # The positive half. Without it the equality above is satisfied by a script that imports
    # the right modules and does nothing at all.
    assert "zipfile.ZipFile" in _executable_source(STAGER), (
        "the staging script must unpack the wheel with the stdlib"
    )


def test_the_stager_refuses_anything_other_than_exactly_one_wheel(tmp_path):
    """Two wheels matter as much as zero: a glob matching both would stage whichever it ended
    on, and the package would carry a version nobody chose. Asserted against the real
    function rather than by reading it -- the count check is the whole value of the script.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    try:
        find_wheel(dist)
        raise AssertionError("find_wheel accepted an empty dist/")
    except SystemExit as exc:
        assert "found 0" in str(exc)

    (dist / "a-1.0-py3-none-any.whl").write_bytes(b"")
    assert find_wheel(dist).name == "a-1.0-py3-none-any.whl"

    (dist / "b-2.0-py3-none-any.whl").write_bytes(b"")
    try:
        find_wheel(dist)
        raise AssertionError("find_wheel accepted two wheels")
    except SystemExit as exc:
        assert "found 2" in str(exc)


def test_nfpm_takes_its_version_from_the_environment():
    """`version: ${VERSION}` -- nfpm expands it from the environment, and the release workflow
    passes release-please's own `version` output, so the package version and the git tag cannot
    disagree. A literal here would ship every release stamped with whatever was typed.
    """
    config = _config()
    assert config["version"] == "${VERSION}", (
        f"nfpm.yaml's version is {config['version']!r}; it must be ${{VERSION}} so the package "
        f"version comes from release-please rather than from this file"
    )
    assert config["name"] == "job-sluice", (
        f"the package name is {config['name']!r}. It must match the PyPI distribution and the "
        f"console script -- see CLAUDE.md on why the distribution is job-sluice, not sluice."
    )
