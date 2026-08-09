"""The shipped template must reach a WHEEL, not merely the source checkout.

CI installs EDITABLE, so `importlib.resources.files("sluice.templates")` reads the
CHECKOUT and returns the file whatever pyproject says. A guard written that way stays
green while every `pip install sluice` ships a default renderer with no template -- the
exact shape of the bug this feature exists to fix, reintroduced by its own fix. Three
plan reviewers reached that independently.

So this builds a REAL wheel and inspects the archive. It builds from a COPY of the tree
for two reasons: the build drops a `build/` directory beside pyproject.toml, which must
not land in the repo root, and a copy lets the second test build a MUTATED pyproject
without touching the real one. Measured 2026-08-06: 0.6s per build, no network --
`--no-isolation` uses the already-installed setuptools, which is why setuptools and
build are in the `test` extra.

NOTE ON HERMETICITY: `conftest.py`'s session fixture blocks `socket.getaddrinfo` in THIS
interpreter, and the build below runs in a SUBPROCESS, which does not inherit that block.
The offline claim above therefore rests on `--no-isolation` (no environment is
provisioned, so nothing is fetched) rather than on the suite's own network guard --
verified by running the build with the machine offline. Left as a note rather than
plumbed through: re-imposing the block in the child would mean an exec wrapper around
`python -m build` for a claim the flag already carries.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DATA = '[tool.setuptools.package-data]\nsluice = ["templates/*.html.j2"]\n'


def _expected_templates():
    """Every template the package-data GLOB will pick up, enumerated from the tree.

    Not one hardcoded name: the manifest ships `templates/*.html.j2`, so a second
    template added beside the first is packaged automatically -- and a guard naming only
    the first would keep passing while the new one silently failed to ship (or shipped
    unchecked). Asserted non-empty by every caller, since a walk that finds nothing
    satisfies a subset check without having looked.
    """
    d = f"{ROOT}/sluice/templates"
    return sorted(f"sluice/templates/{n}" for n in os.listdir(d) if n.endswith(".html.j2"))


def _build_wheel(dest, *, pyproject_text=None):
    """Copy sluice/ + pyproject into `dest`, build a wheel there, return its namelist."""
    shutil.copytree(f"{ROOT}/sluice", f"{dest}/sluice",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if pyproject_text is None:
        with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
            pyproject_text = f.read()
    with open(f"{dest}/pyproject.toml", "w", encoding="utf-8") as f:
        f.write(pyproject_text)
    for named in ("LICENSE", "README.md"):   # pyproject metadata references these
        if os.path.exists(f"{ROOT}/{named}"):
            shutil.copy(f"{ROOT}/{named}", dest)
    # timeout=300: the module docstring measures a real build at 0.6s, so a five-minute
    # bound costs nothing on a healthy run and stops a hung build from hanging the whole
    # suite with no output -- `subprocess.run` has no timeout by default.
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", f"{dest}/out"],
        cwd=dest, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"wheel build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    wheels = glob.glob(f"{dest}/out/*.whl")
    assert wheels, f"the build reported success but produced no wheel in {dest}/out"
    with zipfile.ZipFile(wheels[0]) as zf:
        return zf.namelist()


def _read_metadata(dest):
    """Read and decode the METADATA file from the wheel `_build_wheel(dest, ...)` just built.

    Independent of `_build_wheel`'s own return value (a bare name list) so that function's
    existing two callers and their assertions are untouched by this addition.
    """
    wheels = glob.glob(f"{dest}/out/*.whl")
    assert wheels, f"no wheel found in {dest}/out to read metadata from"
    with zipfile.ZipFile(wheels[0]) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        return zf.read(meta_name).decode("utf-8")


LICENSE_EXPRESSION_LINE = 'license = "MIT"\n'
README_LINE = 'readme = "README.md"\n'
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NOREPLY_AUTHOR_EMAIL = "4990954+MrReasonable@users.noreply.github.com"
AUTHORS_LINES = (
    'authors = [\n'
    '    {name = "MrReasonable", email = "4990954+MrReasonable@users.noreply.github.com"},\n'
    ']\n'
)
# A synthetic, never-real personal email for the falsification fixture below. example.invalid
# is IANA-reserved and guaranteed to never resolve to anyone -- this repo's established
# convention for a fixture that must look personal without being real.
_LEAKED_AUTHORS_LINES = (
    'authors = [\n'
    '    {name = "Example Person", email = "example.person@example.invalid"},\n'
    ']\n'
)

EXPECTED_PYTHON_CLASSIFIERS = [
    "Classifier: Programming Language :: Python :: 3",
    "Classifier: Programming Language :: Python :: 3.12",
    "Classifier: Programming Language :: Python :: 3.13",
    "Classifier: Programming Language :: Python :: 3.14",
]
CLASSIFIERS_LINES = (
    'classifiers = [\n'
    '    "Development Status :: 4 - Beta",\n'
    '    "Environment :: Console",\n'
    '    "Intended Audience :: Developers",\n'
    '    "Operating System :: OS Independent",\n'
    '    "Programming Language :: Python :: 3",\n'
    '    "Programming Language :: Python :: 3.12",\n'
    '    "Programming Language :: Python :: 3.13",\n'
    '    "Programming Language :: Python :: 3.14",\n'
    '    "Topic :: Office/Business",\n'
    '    "Topic :: Utilities",\n'
    ']\n'
)
KEYWORDS_LINE = (
    'keywords = ["job-search", "job-hunting", "cli", "cv", "resume", "automation"]\n'
)


def test_wheel_metadata_carries_the_spdx_license_expression(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    metadata = _read_metadata(dest)
    assert "License-Expression: MIT" in metadata, (
        "pyproject.toml's [project] table should declare license = \"MIT\" (PEP 639 SPDX "
        "form) -- without it, PyPI has no machine-readable license for the package page")
    assert "License-File: LICENSE" in metadata, (
        "the LICENSE file should ship in the sdist/wheel -- via the explicit "
        "license-files = [\"LICENSE\"] declaration below, or via setuptools' own default "
        "auto-discovery glob (LICEN[CS]E*/COPYING*/NOTICE*/AUTHORS*) if that were ever "
        "removed; either way this assertion is what actually matters")


def test_the_license_expression_guard_is_falsified_by_dropping_it(tmp_path):
    """Only license = "MIT" is falsified here, not license-files.

    Verified against setuptools' own pyproject_config docs: when license-files is
    unset, setuptools defaults it to the glob ['LICEN[CS]E*', 'COPYING*', 'NOTICE*',
    'AUTHORS*'] and auto-discovers this repo's LICENSE file regardless -- so
    "License-File: LICENSE" stays in the wheel's METADATA even with the explicit
    license-files = ["LICENSE"] line removed, and asserting its absence there would
    assert something false. license-files is still declared in pyproject.toml (see
    Step 3) for self-documenting intent and to constrain the glob against a future
    NOTICE/COPYING/AUTHORS file this repo doesn't have today -- it just isn't
    independently falsifiable by this mechanism, and this test says so rather than
    silently asserting a property that isn't real.
    """
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert LICENSE_EXPRESSION_LINE in original, (
        "the license expression is not written as this guard expects, so stripping it "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(LICENSE_EXPRESSION_LINE, ""))
    metadata = _read_metadata(dest)
    assert "License-Expression:" not in metadata


def test_every_shipped_template_is_in_the_built_wheel(tmp_path):
    expected = _expected_templates()
    assert expected, "found no templates to check, so this guard would pass vacuously"
    names = _build_wheel(str(tmp_path))
    missing = [t for t in expected if t not in names]
    assert not missing, (
        f"{missing} missing from the built wheel. `packages.find` selects PACKAGES, "
        f"not data: without [tool.setuptools.package-data] every `pip install sluice` "
        f"ships the default renderer with no template for it to render.")


def test_the_wheel_guard_is_falsified_by_dropping_package_data(tmp_path):
    """The guard above must be FALSIFIABLE, not merely green.

    A `package-data` table that silently stopped matching -- a renamed directory, a
    changed suffix -- would otherwise leave the guard green for the wrong reason. This
    strips the table and asserts the template STOPS shipping while the package itself
    still does: the discriminating detail is that `sluice/templates/__init__.py` is
    present either way, because `packages.find` selects the package and never its data.
    """
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert PKG_DATA in original, (
        "the package-data table is not written as this guard expects, so stripping it "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    expected = _expected_templates()
    assert expected, "found no templates to check, so this guard would pass vacuously"
    names = _build_wheel(str(tmp_path), pyproject_text=original.replace(PKG_DATA, ""))
    assert "sluice/templates/__init__.py" in names   # the PACKAGE still ships...
    assert not [t for t in expected if t in names]   # ...its DATA does not


def test_wheel_metadata_carries_the_readme_as_the_long_description(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    metadata = _read_metadata(dest)
    assert "Description-Content-Type: text/markdown" in metadata, (
        "pyproject.toml's [project] table should declare readme = \"README.md\" -- without "
        "it, the PyPI project page renders with a blank description")
    assert "Sluice is an engineered, config-driven job-hunting pipeline" in metadata, (
        "the README's own opening line should appear in the wheel's long description")


def test_the_readme_guard_is_falsified_by_dropping_the_readme_field(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert README_LINE in original, (
        "the readme field is not written as this guard expects, so stripping it "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(README_LINE, ""))
    metadata = _read_metadata(dest)
    assert "Description-Content-Type:" not in metadata


def test_wheel_author_identity_is_the_project_noreply_address(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    metadata = _read_metadata(dest)
    assert f"Author-email: MrReasonable <{NOREPLY_AUTHOR_EMAIL}>" in metadata, (
        "pyproject.toml's [project] table should declare authors with the project's "
        "existing noreply commit-trailer identity, not left unset or personal")
    emails = set(_EMAIL_RE.findall(metadata))
    assert emails == {NOREPLY_AUTHOR_EMAIL}, (
        f"unexpected email address(es) in wheel metadata: {emails - {NOREPLY_AUTHOR_EMAIL}}. "
        f"authors ships in every published sdist/wheel -- only the project's public noreply "
        f"identity belongs there, never a personal address")


def test_the_author_identity_guard_is_falsified_by_a_personal_email(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert AUTHORS_LINES in original, (
        "the authors field is not written as this guard expects, so mutating it "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(AUTHORS_LINES, _LEAKED_AUTHORS_LINES))
    metadata = _read_metadata(dest)
    emails = set(_EMAIL_RE.findall(metadata))
    assert emails == {"example.person@example.invalid"}, (
        "the guard should have detected the injected non-noreply email but did not -- "
        "it would silently pass a real leak the same way")


def test_wheel_metadata_carries_the_ci_matrix_python_classifiers(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    metadata = _read_metadata(dest)
    missing = [c for c in EXPECTED_PYTHON_CLASSIFIERS if c not in metadata]
    assert not missing, (
        f"{missing} missing from wheel METADATA. These must match "
        f".github/workflows/ci.yml's matrix.python-version exactly -- if that matrix "
        f"changes, this list (and pyproject.toml's classifiers) must change with it")
    assert "Classifier: License ::" not in metadata, (
        "a License :: classifier combined with the PEP 639 license = \"MIT\" SPDX "
        "expression (Task 1) is a deprecated combination setuptools >=77 warns about -- "
        "the license belongs in License-Expression only")
    assert "Keywords:" in metadata, (
        "pyproject.toml's [project] table should declare keywords -- without them the "
        "package is harder to find via PyPI search")


def test_the_classifiers_guard_is_falsified_by_dropping_them(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert CLASSIFIERS_LINES in original, (
        "classifiers are not written as this guard expects, so stripping them "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(CLASSIFIERS_LINES, ""))
    metadata = _read_metadata(dest)
    assert not any(c in metadata for c in EXPECTED_PYTHON_CLASSIFIERS)


def test_the_keywords_guard_is_falsified_by_dropping_them(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert KEYWORDS_LINE in original, (
        "keywords are not written as this guard expects, so stripping them "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(KEYWORDS_LINE, ""))
    metadata = _read_metadata(dest)
    assert "Keywords:" not in metadata
