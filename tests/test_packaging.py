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
