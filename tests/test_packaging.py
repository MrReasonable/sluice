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

This module has grown past checking only the template: it now pins the whole of
pyproject.toml's PyPI-facing metadata as it lands in a real wheel -- license, readme (as
the long description, and, separately, that its markdown actually resolves off-repo,
since PyPI does not rewrite relative links the way GitHub does), authors (pinned to the
project's own noreply identity), classifiers (DERIVED from CI's own test matrix rather
than hand-listed, so the two cannot silently drift apart), keywords, project URLs, and
the console script. Every POSITIVE assertion below reads a single pristine, unmutated
wheel built ONCE by the module-scoped `pristine_wheel` fixture below, rather than each
positive test calling `_build_wheel` on its own identical, unmutated copy -- sharing one
build keeps this module's total runtime in the single digits (measured ~7-10s across
runs, varying with system load; a per-fixture-addition number here would only go stale
again, so this states the current order of magnitude rather than a precise figure -- see
`git log` on this file for the fix-wave commit that established the shared-fixture
pattern if the exact before/after numbers matter). Every FALSIFY test still builds its
own MUTATED wheel per test, deliberately UNshared: each mutates a different line and
must not see another test's mutation.

It has grown once more with #104: the tests below build a real SDIST and pin both its
root members and the templates it ships, because the PyPI channel makes the sdist public
and permanent -- before it, `build`'s sdist expired with the run artifact in a day and
nothing downstream read it. So this module no longer gates only "the shipped template
reaches a wheel"; it gates a second published artefact whose contents nobody can withdraw,
and it gates the TEMPLATE on that one too. The root-member equality alone cannot: measured,
`exclude sluice/templates/*.html.j2` produced an sdist with ZERO template members while
every packaging test stayed green, because `sluice` is still a root entry either way. The
sdist is what the wheel is built FROM, so a template missing there is missing everywhere.
"""
import dataclasses
import glob
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")
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


def _build_wheel(dest, *, pyproject_text=None, readme_text=None):
    """Copy sluice/ + pyproject + README into `dest`, build a wheel there, return its namelist.

    `pyproject_text` and `readme_text` each default to this repo's own file; either can be
    swapped for a MUTATED copy so a falsify test can build from an altered pyproject.toml
    or an altered README.md without touching the real one on disk -- the same shape,
    extended to a second file for the same reason.
    """
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
    if readme_text is not None:   # override the copy above with a MUTATED README
        with open(f"{dest}/README.md", "w", encoding="utf-8") as f:
            f.write(readme_text)
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


def _read_dist_info(dest, suffix):
    """Read and decode a `.dist-info/<suffix>` member from the wheel `_build_wheel` just
    built in `dest`.

    Shared by `_read_metadata` (suffix="METADATA") and `_read_entry_points`
    (suffix="entry_points.txt") -- both need the identical glob-the-wheel / open-the-zip /
    find-the-member dance, and duplicating it across two functions was two copies that
    could silently drift apart under a future change to either.
    """
    wheels = glob.glob(f"{dest}/out/*.whl")
    assert wheels, f"no wheel found in {dest}/out to read {suffix} from"
    with zipfile.ZipFile(wheels[0]) as zf:
        member = next(n for n in zf.namelist() if n.endswith(f".dist-info/{suffix}"))
        return zf.read(member).decode("utf-8")


def _read_metadata(dest):
    """Read and decode the METADATA file from the wheel `_build_wheel(dest, ...)` just built.

    Independent of `_build_wheel`'s own return value (a bare name list) so that function's
    existing callers and their assertions are untouched by this addition.
    """
    return _read_dist_info(dest, "METADATA")


def _read_entry_points(dest):
    """Read and decode entry_points.txt from the wheel `_build_wheel(dest, ...)` just built."""
    return _read_dist_info(dest, "entry_points.txt")


def _long_description_body(metadata):
    """The long-description BODY of a wheel's METADATA -- everything after the header block.

    Core Metadata (an RFC 822-derived format) separates its header block from the payload
    with the first blank line, and the headers above it are single-line `Key: value`
    pairs with no blank lines of their own -- so partitioning on the first "\\n\\n" lands
    exactly on that boundary. Verified against a real build: the returned body starts with
    README.md's own first line, verbatim.
    """
    _, _, body = metadata.partition("\n\n")
    return body


_RELATIVE_MARKDOWN_LINK_RE = re.compile(r"\]\((?!https?://|#)[^)]*\)")
# CommonMark's OTHER link syntax: a reference-style definition line, e.g. "[usage]:
# docs/USAGE.md", distinct from an inline link's "[usage](docs/USAGE.md)". A guard that
# only matched the inline form would stay green while a reference-style relative link
# reached PyPI's rendered page just as broken -- README.md has none of this form today,
# but nothing stops a future edit from introducing one, and this guard's whole job is to
# catch that before it ships.
_RELATIVE_MARKDOWN_REFERENCE_RE = re.compile(
    r"^\s{0,3}\[[^\]]+\]:\s*(?!https?://|#)\S+", re.MULTILINE)


def _relative_markdown_links(text):
    """Every markdown link TARGET in `text` that is neither an absolute URL nor an in-page
    anchor -- i.e. one that would 404 when this text renders somewhere other than this
    repo's own GitHub page. Covers both inline links (`[text](target)`) and reference-
    style link DEFINITIONS (`[label]: target`). PyPI does not rewrite relative links in
    the long description it renders; they resolve against pypi.org/project/job-sluice/,
    not against this repo, confirmed by a real `twine check --strict` run, which passes
    regardless -- that check validates well-formed markdown, not that every link it
    contains actually resolves from PyPI's own domain.
    """
    return (_RELATIVE_MARKDOWN_LINK_RE.findall(text)
            + _RELATIVE_MARKDOWN_REFERENCE_RE.findall(text))


_MATRIX_PYTHON_VERSIONS_RE = re.compile(r'python-version:\s*\[([^\]]*)\]')


def _extract_python_versions(ci_text):
    """The `"X.Y"` version strings inside `ci_text`'s one bracketed `python-version: [...]`
    list.

    Split out of `_expected_python_classifiers` below so this is directly testable against
    a SYNTHETIC ci.yml-shaped string, without needing a real broken
    `.github/workflows/ci.yml` fixture on disk. Asserts its own result is non-empty: a
    broken inner extraction regex (e.g. an unquoted or single-quoted matrix) must fail
    LOUDLY here, not silently degrade `_expected_python_classifiers` to its one hardcoded
    generic classifier -- which would still satisfy THAT function's own non-empty check
    while having enumerated no real versions at all. That is exactly the "a sweep that
    discovers nothing passes" trap this repo's own culture names as a repeat offender
    (`all([])` is `True`), and it is what a version-count-blind `assert expected` cannot
    catch: see test_extract_python_versions_fails_loudly_on_an_unquoted_matrix below,
    which proves this assertion actually fires rather than merely reading well.
    """
    matches = list(_MATRIX_PYTHON_VERSIONS_RE.finditer(ci_text))
    assert len(matches) == 1, (
        f"expected exactly one bracketed python-version list, found "
        f"{len(matches)} -- this guard cannot say which one is the CI test matrix")
    versions = re.findall(r'"([\d.]+)"', matches[0].group(1))
    assert versions, (
        "no quoted Python version strings found inside the matrix's bracketed list -- "
        "the extraction regex may be broken (e.g. an unquoted or single-quoted matrix), "
        "and returning nothing here would silently degrade the caller to its one "
        "hardcoded generic classifier, which would still pass a non-empty check")
    return versions


def _expected_python_classifiers():
    """The `Programming Language :: Python :: 3.X` classifiers CI's own test matrix implies.

    DERIVED from `.github/workflows/ci.yml`'s `matrix.python-version` list, never hand-
    listed: a hand-listed copy is exactly the shape of drift this guard exists to catch --
    measured, bumping CI's matrix to a 4th Python version and leaving a hand-listed
    constant here untouched left the guard built from it green. Text-matched, not YAML-
    parsed, for the same reason tests/test_ci_wiring.py gives in its own module docstring:
    pyyaml is a guarded optional import in `sluice/`, so a test that needs it can skip
    itself into uselessness on a bare install.

    `python-version: [...]` (bracketed) appears in ci.yml exactly once -- every other
    `python-version:` line in that file sets a single quoted string (`"3.12"`), not a
    list -- so `_extract_python_versions` needs no `matrix:` anchoring to stay
    unambiguous, and it is the one that asserts uniqueness and non-emptiness; this
    function trusts its result.
    """
    with open(CI, encoding="utf-8") as f:
        text = f.read()
    versions = _extract_python_versions(text)
    return ["Classifier: Programming Language :: Python :: 3"] + [
        f"Classifier: Programming Language :: Python :: {v}" for v in versions
    ]


def test_extract_python_versions_fails_loudly_on_an_unquoted_matrix():
    """Proves _extract_python_versions's own non-empty guard actually fires on a plausible
    broken input -- not just that the assertion reads well.

    An unquoted `python-version: [3.12, 3.13, 3.14]` (a plausible outcome of a future
    ci.yml reformat) is still matched by the OUTER bracket regex, but the inner
    `"([\\d.]+)"` extraction -- which requires double-quoted version strings -- matches
    zero versions against it. Without the dedicated non-empty assertion, that would
    silently return `[]` rather than raise, which is exactly the shape that would let
    `_expected_python_classifiers` degrade to its one hardcoded generic classifier.
    """
    unquoted_matrix = "matrix:\n  python-version: [3.12, 3.13, 3.14]\n"
    assert re.findall(r'"([\d.]+)"', unquoted_matrix) == [], (
        "test fixture assumption changed -- this input is no longer version-less under "
        "the inner extraction regex, update this test")
    with pytest.raises(AssertionError, match="no quoted Python version"):
        _extract_python_versions(unquoted_matrix)


LICENSE_EXPRESSION_LINE = 'license = "MIT"\n'
LICENSE_FILES_LINE = 'license-files = ["LICENSE"]\n'
LICENSE_FILES_EMPTY_LINE = 'license-files = []\n'
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
EXTRA_URLS_LINES = (
    'Changelog = "https://github.com/MrReasonable/sluice/blob/main/CHANGELOG.md"\n'
    'Issues = "https://github.com/MrReasonable/sluice/issues"\n'
    'Source = "https://github.com/MrReasonable/sluice"\n'
    'Documentation = "https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md"\n'
)
# TWO distinct representations, deliberately not one constant: pyproject.toml is TOML
# (the value is a quoted string), but a wheel's entry_points.txt is INI-format (the value
# is bare, unquoted) -- reusing one string for both would either fail to match the real
# pyproject.toml line or, worse, get replace()'d into it and silently corrupt it into
# invalid TOML.
CONSOLE_SCRIPT_PYPROJECT_LINE = 'job-sluice = "sluice.cli:main"\n'
CONSOLE_SCRIPT_ENTRY_POINT_LINE = "job-sluice = sluice.cli:main"


@dataclasses.dataclass(frozen=True)
class _PristineWheel:
    namelist: list
    metadata: str
    entry_points: str


@pytest.fixture(scope="module")
def pristine_wheel(tmp_path_factory):
    """The unmutated wheel every POSITIVE assertion in this module reads from, built ONCE.

    Every positive-assertion test in this module used to call `_build_wheel` on its own
    identical, unmutated copy of the tree -- sharing this one build across all of them
    instead keeps the module's total runtime in the single digits (see the module
    docstring above for the current measured range; not repeated here as a precise
    number, since this file keeps growing and a stale one would just be wrong again).
    Every FALSIFY test still builds its own MUTATED wheel per test via `_build_wheel`
    directly; those are deliberately NOT routed through this fixture, since each mutates a
    different line of pyproject.toml or README.md and must not see another test's
    mutation, or share a wheel with one that has already been altered.
    """
    dest = str(tmp_path_factory.mktemp("pristine-wheel"))
    namelist = _build_wheel(dest)
    return _PristineWheel(
        namelist=namelist,
        metadata=_read_metadata(dest),
        entry_points=_read_entry_points(dest),
    )


def test_every_shipped_template_is_in_the_built_wheel(pristine_wheel):
    expected = _expected_templates()
    assert expected, "found no templates to check, so this guard would pass vacuously"
    missing = [t for t in expected if t not in pristine_wheel.namelist]
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


def test_wheel_metadata_carries_the_spdx_license_expression(pristine_wheel):
    metadata = pristine_wheel.metadata
    assert "License-Expression: MIT" in metadata, (
        "pyproject.toml's [project] table should declare license = \"MIT\" (PEP 639 SPDX "
        "form) -- without it, PyPI has no machine-readable license for the package page")
    assert "License-File: LICENSE" in metadata, (
        "the LICENSE file should ship in the sdist/wheel -- via the explicit "
        "license-files = [\"LICENSE\"] declaration below, or via setuptools' own default "
        "auto-discovery glob (LICEN[CS]E*/COPYING*/NOTICE*/AUTHORS*) if that were ever "
        "removed; either way this assertion is what actually matters")


def test_the_license_expression_guard_is_falsified_by_dropping_it(tmp_path):
    """Only license = "MIT" is falsified by DELETION here, not license-files.

    Verified against setuptools' own pyproject_config docs: when license-files is unset,
    setuptools defaults it to the glob ['LICEN[CS]E*', 'COPYING*', 'NOTICE*', 'AUTHORS*']
    and auto-discovers this repo's LICENSE file regardless -- so "License-File: LICENSE"
    stays in the wheel's METADATA even with the explicit license-files = ["LICENSE"] line
    DELETED, and asserting its absence there would assert something false. license-files
    is still declared in pyproject.toml for self-documenting intent and to constrain the
    glob against a future NOTICE/COPYING/AUTHORS file this repo doesn't have today.

    license-files IS falsifiable, though -- by SUBSTITUTION, not deletion. Measured: both
    `license-files = []` (an explicit empty list) and `license-files = ["NOTICE"]` (a
    filename that doesn't exist) make "License-File: LICENSE" disappear from the built
    wheel. That correction matters beyond this docstring having stated the wrong
    mechanism: a typo or an accidental glob narrowing in a future edit could silently ship
    an MIT-licensed public package with NO license text in the wheel at all, and the
    positive assertion above had no falsify partner that could catch it --
    test_the_license_files_field_is_falsified_by_emptying_it below is that partner.
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


def test_the_license_files_field_is_falsified_by_emptying_it(tmp_path):
    """The falsify partner test_the_license_expression_guard_is_falsified_by_dropping_it's
    own docstring says the positive assertion above was missing: license-files IS
    falsifiable, by SUBSTITUTION rather than deletion. Emptying the explicit list is the
    shape a future edit could actually introduce by accident (a typo, or a glob narrowed
    too far), and losing it silently would ship an MIT-licensed public package with no
    license text in the wheel at all -- worth guarding even though this specific mutation
    (an empty list) is not itself a plausible accidental edit on its own; it is the
    simplest one that exercises the same failure mode as a narrowed glob would.
    """
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert LICENSE_FILES_LINE in original, (
        "license-files is not written as this guard expects, so emptying it would "
        "SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(
        dest, pyproject_text=original.replace(LICENSE_FILES_LINE, LICENSE_FILES_EMPTY_LINE))
    metadata = _read_metadata(dest)
    assert "License-File: LICENSE" not in metadata


def test_wheel_metadata_carries_the_readme_as_the_long_description(pristine_wheel):
    metadata = pristine_wheel.metadata
    assert "Description-Content-Type: text/markdown" in metadata, (
        "pyproject.toml's [project] table should declare readme = \"README.md\" -- without "
        "it, the PyPI project page renders with a blank description")
    # DERIVED from the real README.md at test time, not a hardcoded literal copy of its
    # opening sentence: a hardcoded copy cannot catch TRUNCATION (only that some fixed
    # substring survived), and it silently goes stale the moment the opening prose changes
    # for an unrelated reason. The first 120 characters cover the title line plus enough of
    # the first sentence to be a meaningful, drift-proof fingerprint.
    with open(f"{ROOT}/README.md", encoding="utf-8") as f:
        opening = f.read()[:120]
    assert opening in metadata, (
        "the README's own opening content should appear verbatim in the wheel's long "
        "description")


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


def test_wheel_long_description_has_no_relative_markdown_links(pristine_wheel):
    """PyPI does not rewrite relative markdown links in the long description it renders --
    they resolve against pypi.org/project/job-sluice/, not against this repo, and 404. This
    is invisible to `twine check --strict`, confirmed by running it: that check validates
    well-formed markdown, not that every link it contains actually resolves from PyPI's own
    domain. README.md's own repo-relative links (`docs/USAGE.md`, `LICENSE`, ...) are
    rewritten to absolute `https://github.com/MrReasonable/sluice/blob/main/...` URLs
    instead -- GitHub resolves an absolute link to its own repo fine when the README
    renders in-repo, and PyPI resolves the same URL for the same reason: it no longer
    depends on the resolving page's own location. Anchor-only links (`#section`) are
    exempt -- those work wherever the page renders, PyPI included.
    """
    body = _long_description_body(pristine_wheel.metadata)
    assert body, (
        "no long-description body found in METADATA -- the header/body split point may "
        "have moved, and this guard would be scanning nothing")
    bad = _relative_markdown_links(body)
    assert not bad, (
        f"{bad} -- relative markdown link(s) in the long description. PyPI does not "
        f"rewrite these; they 404 against pypi.org/project/job-sluice/. Rewrite each to an "
        f"absolute https://github.com/MrReasonable/sluice/blob/main/<path> URL in README.md.")


def test_the_relative_markdown_link_guard_is_falsified_by_reintroducing_one(tmp_path):
    """The guard above must be FALSIFIABLE, not merely green on today's already-fixed
    README. This reintroduces a relative link into a MUTATED copy of the real README
    content -- never the file on disk -- and confirms the guard catches it. Both link
    SHAPES are exercised: an inline link and a reference-style definition, since
    _relative_markdown_links checks both and a witness that only covers one shape would
    leave the other's detection unproven.
    """
    with open(f"{ROOT}/README.md", encoding="utf-8") as f:
        original_readme = f.read()
    mutated = (
        original_readme
        + "\n\nSee [an example](some/relative/path.md) for details.\n"
        + "\n[a reference]: another/relative/path.md\n"
    )
    found = _relative_markdown_links(mutated)
    assert len(found) >= 2, (
        "the injected fixture lines are not both relative-link-shaped (one inline, one "
        f"reference-style), so this test would SILENTLY NO-OP and pass for the wrong "
        f"reason -- found: {found}")
    dest = str(tmp_path)
    _build_wheel(dest, readme_text=mutated)
    metadata = _read_metadata(dest)
    bad = _relative_markdown_links(_long_description_body(metadata))
    assert len(bad) >= 2, (
        "the guard failed to detect one or both reintroduced relative markdown links "
        f"(inline and reference-style) -- it would silently pass a real regression the "
        f"same way (found: {bad})")


def test_the_readme_content_match_is_falsified_by_substituting_the_readme(tmp_path):
    """test_the_readme_guard_is_falsified_by_dropping_the_readme_field above only proves
    the readme FIELD is required -- dropping it entirely kills BOTH of the positive
    test's assertions (Description-Content-Type and the content match) at once, proving
    nothing about the content-match check specifically. This instead keeps
    readme = "README.md" intact and swaps the README's CONTENT for something sharing none
    of the real file's opening text, proving the content-match assertion -- not just field
    presence -- would catch a truncated or substituted README shipped under an unchanged
    readme field.
    """
    substituted_readme = "Not the real README content at all.\n"
    with open(f"{ROOT}/README.md", encoding="utf-8") as f:
        real_opening = f.read()[:120]
    assert real_opening not in substituted_readme, (
        "the substitute fixture happens to share the real opening text, so this test "
        "would SILENTLY NO-OP and pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, readme_text=substituted_readme)
    metadata = _read_metadata(dest)
    assert real_opening not in metadata, (
        "the content-match guard failed to detect a substituted README -- a truncated or "
        "swapped README under an unchanged readme field would silently ship unnoticed")


def test_wheel_author_identity_is_the_project_noreply_address(pristine_wheel):
    metadata = pristine_wheel.metadata
    assert f"Author-email: MrReasonable <{NOREPLY_AUTHOR_EMAIL}>" in metadata, (
        "pyproject.toml's [project] table should declare authors with the project's "
        "existing noreply commit-trailer identity, not left unset or personal")
    emails = set(_EMAIL_RE.findall(metadata))
    assert emails == {NOREPLY_AUTHOR_EMAIL}, (
        f"unexpected email address(es) in wheel metadata: {emails - {NOREPLY_AUTHOR_EMAIL}}. "
        f"authors ships in every published sdist/wheel, and so does README.md as the long "
        f"description (Task 2) -- only the project's public noreply identity belongs in "
        f"either, so check BOTH pyproject.toml's authors field AND README.md for a leaked "
        f"personal address, not authors alone")


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
    # NOT exact-set equality against {"example.person@example.invalid"}: the whole-METADATA
    # scan also covers README.md's body (Task 2 embeds it as the long description), so an
    # unrelated email-shaped string anywhere in README.md's own prose would make an exact-
    # set assertion fail for a reason that has nothing to do with THIS mutation. What this
    # test actually needs to prove is narrower and isolates it: the noreply address is gone
    # AND the injected leaked one is present, regardless of what else the README may add.
    assert NOREPLY_AUTHOR_EMAIL not in emails and "example.person@example.invalid" in emails, (
        f"the guard should have detected the injected non-noreply email and lost the "
        f"noreply one, but did not (emails found: {emails}) -- it would silently pass a "
        f"real leak the same way")


def test_wheel_metadata_carries_the_ci_matrix_python_classifiers(pristine_wheel):
    # expected is guaranteed non-empty by _extract_python_versions's own assertion --
    # no redundant non-empty check needed here.
    expected = _expected_python_classifiers()
    metadata = pristine_wheel.metadata
    actual = {
        line for line in metadata.splitlines()
        if line.startswith("Classifier: Programming Language :: Python")
    }
    # EXACT-set, not a subset check: a subset check only catches a classifier missing
    # after CI's matrix grows, not a STALE classifier surviving after CI's matrix
    # shrinks -- both are the same "pyproject.toml's classifiers must change with
    # ci.yml's matrix" drift this guard exists to catch, and a subset check silently
    # missed the second half of that claim.
    assert actual == set(expected), (
        f"Python-version classifiers don't match .github/workflows/ci.yml's "
        f"matrix.python-version exactly -- expected {set(expected)}, got {actual}. If "
        f"that matrix changed, pyproject.toml's classifiers must change with it, in "
        f"either direction (missing OR stale entries both count)")
    assert "Classifier: License ::" not in metadata, (
        "a License :: classifier combined with the PEP 639 license = \"MIT\" SPDX "
        "expression (Task 1) is a deprecated combination setuptools >=77 HARD-ERRORS on "
        "(setuptools.errors.InvalidConfigError, confirmed by a real build attempt) -- "
        "the license belongs in License-Expression only")


def test_the_deprecated_license_classifier_guard_is_falsified_by_reintroducing_it(tmp_path):
    """Proves the absence-check above ("Classifier: License ::" not in metadata) is real
    protection, not a check that would pass regardless of what pyproject.toml declares.

    Cannot reintroduce the classifier ALONGSIDE license = "MIT" and still build: verified
    directly (not assumed) that combination is a hard setuptools.errors.InvalidConfigError,
    not a warning -- see the comment above `classifiers` in pyproject.toml. So this
    mutation instead drops the SPDX license line (license-files stays independently valid
    without it, per test_the_license_expression_guard_is_falsified_by_dropping_it above)
    and reintroduces the classifier, proving the classifier alone reaches METADATA when
    nothing stops it -- the discriminating fact the absence-check depends on.
    """
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert LICENSE_EXPRESSION_LINE in original, (
        "the license expression is not written as this guard expects, so stripping it "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    assert CLASSIFIERS_LINES in original, (
        "classifiers are not written as this guard expects, so inserting into them "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    reintroduced_classifiers = CLASSIFIERS_LINES.replace(
        'classifiers = [\n', 'classifiers = [\n    "License :: OSI Approved :: MIT License",\n')
    assert reintroduced_classifiers != CLASSIFIERS_LINES  # the insertion landed
    mutated = original.replace(LICENSE_EXPRESSION_LINE, "").replace(
        CLASSIFIERS_LINES, reintroduced_classifiers)
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=mutated)
    metadata = _read_metadata(dest)
    assert "Classifier: License :: OSI Approved :: MIT License" in metadata, (
        "the guard should have detected the reintroduced deprecated classifier but did "
        "not -- it would silently pass a real regression the same way")


def test_the_classifiers_guard_is_falsified_by_dropping_them(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert CLASSIFIERS_LINES in original, (
        "classifiers are not written as this guard expects, so stripping them "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    # expected is guaranteed non-empty by _extract_python_versions's own assertion.
    expected = _expected_python_classifiers()
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(CLASSIFIERS_LINES, ""))
    metadata = _read_metadata(dest)
    assert not any(c in metadata for c in expected)


def test_wheel_metadata_carries_keywords(pristine_wheel):
    """Split out from test_wheel_metadata_carries_the_ci_matrix_python_classifiers above,
    which originally bundled three unrelated assertions (classifiers present, the
    deprecated License:: classifier absent, keywords present) under one name. This is the
    positive counterpart test_the_keywords_guard_is_falsified_by_dropping_them below
    previously had none of its own.

    Asserts the VALUES, not merely the header's presence -- a bare "Keywords:" in metadata
    check stays green against a typo or a list silently narrowed to one term, which is the
    drift that actually costs PyPI discoverability. Every other field in this module is
    pinned exactly (the classifiers exact-set check above, the Project-URL set below); this
    one was the outlier.
    """
    expected_keywords = re.findall(r'"([^"]+)"', KEYWORDS_LINE)
    assert expected_keywords, (
        "no keywords parsed out of KEYWORDS_LINE, so this guard would pass vacuously")
    expected_line = f"Keywords: {','.join(expected_keywords)}"
    assert expected_line in pristine_wheel.metadata, (
        f"pyproject.toml's keywords should reach METADATA verbatim as '{expected_line}' -- "
        f"without them the package is harder to find via PyPI search, and a silently "
        f"shortened list reads the same as a full one")


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


def test_wheel_metadata_carries_the_full_project_url_set(pristine_wheel):
    metadata = pristine_wheel.metadata
    expected = {
        "Project-URL: Homepage, https://github.com/MrReasonable/sluice",
        "Project-URL: Changelog, https://github.com/MrReasonable/sluice/blob/main/CHANGELOG.md",
        "Project-URL: Issues, https://github.com/MrReasonable/sluice/issues",
        "Project-URL: Source, https://github.com/MrReasonable/sluice",
        "Project-URL: Documentation, https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md",
    }
    missing = {line for line in expected if line not in metadata}
    assert not missing, f"{missing} missing from wheel METADATA's Project-URL set"


def test_the_project_url_guard_is_falsified_by_dropping_the_extra_urls(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert EXTRA_URLS_LINES in original, (
        "the extra project URLs are not written as this guard expects, so stripping "
        "them would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(EXTRA_URLS_LINES, ""))
    metadata = _read_metadata(dest)
    assert "Project-URL: Homepage," in metadata  # unrelated field, still present
    assert "Project-URL: Source," not in metadata
    assert "Project-URL: Changelog," not in metadata
    assert "Project-URL: Issues," not in metadata
    assert "Project-URL: Documentation," not in metadata


def test_wheel_console_script_is_job_sluice(pristine_wheel):
    entry_points = pristine_wheel.entry_points
    assert "[console_scripts]" in entry_points
    assert CONSOLE_SCRIPT_ENTRY_POINT_LINE in entry_points, (
        "the console script must stay named job-sluice -- the PyPI distribution name "
        "renamed from sluice in #103, and a silent rename here would break every "
        "documented install instruction")


def test_the_console_script_guard_is_falsified_by_a_rename(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert CONSOLE_SCRIPT_PYPROJECT_LINE in original, (
        "the console script is not written as this guard expects, so renaming it "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    renamed_pyproject_line = 'job-sluice-renamed = "sluice.cli:main"\n'
    renamed_entry_point_line = "job-sluice-renamed = sluice.cli:main"
    _build_wheel(dest, pyproject_text=original.replace(
        CONSOLE_SCRIPT_PYPROJECT_LINE, renamed_pyproject_line))
    entry_points = _read_entry_points(dest)
    assert CONSOLE_SCRIPT_ENTRY_POINT_LINE not in entry_points
    assert renamed_entry_point_line in entry_points  # the mutation landed, not a no-op


SDIST_ROOT_MEMBERS = {
    "LICENSE", "MANIFEST.in", "PKG-INFO", "README.md",
    "job_sluice.egg-info", "pyproject.toml", "setup.cfg", "sluice",
}


def _tracked_files():
    """Every path `git ls-files` reports for this repo, repo-relative.

    NUL-separated (`-z`), not line-separated: a filename containing a newline would
    otherwise arrive as two entries and both copies would fail. Fails LOUDLY on a non-zero
    exit or an empty result rather than returning `[]` -- a copy built from nothing would
    make every assertion over the resulting tarball vacuous, which is the "a sweep that
    discovers nothing passes" shape this repo treats as a repeat offender.
    """
    proc = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"git ls-files failed:\n{proc.stderr[-2000:]}"
    files = [f for f in proc.stdout.split("\0") if f]
    assert files, (
        "git ls-files reported no tracked files, so the sdist below would be built from an "
        "empty tree and every assertion over its members would pass having looked at nothing")
    return files


def _build_sdist(dest, manifest_text=None):
    """Build a REAL sdist from a COPY of the tree and return its member names.

    A copy, not the real tree, for the same two reasons `_build_wheel`'s docstring gives --
    the build drops `build/` and `.egg-info` beside pyproject.toml, which must not land in
    the repo root -- plus a third specific to this guard: the falsify partner below needs to
    build with a MUTATED MANIFEST.in, and it must not edit the repository's real one to do
    it. Both tests share this helper for exactly that reason. Measured during design review:
    a copy WITHOUT `tests/` ships zero test members whether or not `prune tests` is present,
    so a guard and partner that build from differently-shaped trees prove nothing.

    `docs/` is copied too, for the identical reason: MANIFEST.in's own comment asserts that
    nothing grafts `docs/` and that publishing it would put its partially-covered content on a
    permanent index -- a claim this guard cannot falsify unless a `docs/` actually exists in
    the tree being built. A copy without it would make `graft docs` a no-op (nothing to graft)
    and leave that assertion in MANIFEST.in's comment untested by anything executable, the same
    shape of defect as building without `tests/` above.

    SCOPE. The copy is the TRACKED TREE -- whatever `git ls-files` reports -- rather than a
    hand-listed subset, so the root-entry equality below has the blast radius its wording
    implies. It was a hand-list of three trees plus four root files, and measured, that made
    three real MANIFEST.in changes INVISIBLE: `graft scripts` and `graft .github` each found
    nothing to graft, and `include sluice.yaml.example` named a file the copy did not contain,
    so all three left the equality green while the real tree would have shipped 8, 8 and 1
    extra members respectively. Copying what git tracks removes the enumeration, and with it
    the unanswerable "which tree did we forget?". Verified when it was widened: the root-entry
    set comes out IDENTICAL either way (135 members, 0.6s per build), so this changed the
    guard's reach and not its verdict.

    One property changes with it and is worth naming rather than discovering later: an
    UNTRACKED file under `sluice/` is no longer copied, so this now observes what a clean
    checkout ships rather than what one developer's working tree does. That is the right side
    of the trade -- `release-please.yml`'s `build` job checks out the tagged sha, so a clean
    checkout IS what PyPI receives -- but it is a change, not a no-op.

    `__pycache__` never appears here: git tracks none of it, so the explicit ignore the
    hand-listed `copytree` needed is now structural rather than a rule to remember.
    """
    for rel in _tracked_files():
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            continue   # tracked but deleted in the working tree; nothing to copy
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(src, dst)
    # LAST, and unconditionally: MANIFEST.in is itself tracked, so the loop above has already
    # placed the real one. A falsify partner's MUTATED text must overwrite that copy, not lose
    # to it.
    with open(f"{dest}/MANIFEST.in", "w", encoding="utf-8") as f:
        f.write(manifest_text if manifest_text is not None
                else open(f"{ROOT}/MANIFEST.in", encoding="utf-8").read())
    # timeout=300: same reasoning as `_build_wheel`'s twin above -- the module docstring
    # measures a real build at 0.6s, so a five-minute bound costs nothing on a healthy run
    # and stops a hung build from hanging the whole suite with no output, which is what
    # `subprocess.run` does by default.
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--no-isolation",
         "--outdir", f"{dest}/out"],
        cwd=dest, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"sdist build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    tarballs = glob.glob(f"{dest}/out/*.tar.gz")
    assert tarballs, f"the build reported success but produced no sdist in {dest}/out"
    with tarfile.open(tarballs[0]) as tf:
        return tf.getnames()


def _sdist_root_prefix(names):
    """The sdist's single root directory, derived from its TOP-LEVEL PKG-INFO member.

    An sdist carries TWO members ending in `/PKG-INFO` -- measured against a real build, not
    assumed: `<root>/PKG-INFO` and `<root>/job_sluice.egg-info/PKG-INFO`. This took
    `next(...)` over the member list, i.e. whichever of the two the TAR ORDER happened to
    yield first -- a build-backend detail, not a guarantee. Had it yielded the egg-info copy,
    the derived prefix would have been `<root>/job_sluice.egg-info` and every root-entry
    assertion below would have been reading a different directory than it names.

    So restrict to members at depth ONE and require exactly one: zero means the derivation is
    broken and everything over it is vacuous, two means the root is genuinely ambiguous.
    Neither is a case to paper over by taking the first.
    """
    candidates = sorted({n[: -len("/PKG-INFO")] for n in names
                         if n.endswith("/PKG-INFO") and n.count("/") == 1})
    assert len(candidates) == 1, (
        f"expected exactly one TOP-LEVEL PKG-INFO member to derive the sdist root from, "
        f"found {candidates} -- zero leaves every assertion below vacuous, two leaves the "
        f"root ambiguous")
    return candidates[0]


def _sdist_root_entries(names):
    """The entry names one level below the sdist's single root directory.

    Every member of an sdist is `job_sluice-<version>/<path>`, so "the set of top-level
    entries" is ONE element -- identical whether the tarball is clean or carries 166 test
    modules. An allowlist over that set is exactly-equal and blind. Derive the prefix from
    PKG-INFO's parent and assert BELOW it.
    """
    prefix = _sdist_root_prefix(names)
    return {n[len(prefix) + 1:].split("/", 1)[0] for n in names if n != prefix}


@pytest.fixture(scope="module")
def pristine_sdist(tmp_path_factory):
    """The unmutated sdist's member names, built ONCE and shared by every POSITIVE assertion
    over it -- the same pattern, and the same reason, as `pristine_wheel` above. Every
    FALSIFY test still builds its own MUTATED sdist through `_build_sdist` directly, since
    each mutates a different MANIFEST.in line and must not see another test's mutation.
    """
    return _build_sdist(str(tmp_path_factory.mktemp("pristine-sdist")))


def test_the_sdist_ships_the_package_and_metadata_and_no_tests(pristine_sdist):
    """The sdist becomes PUBLIC AND PERMANENT with the PyPI channel. Before it, `build`'s
    sdist expired with the run artifact in a day.

    `tests/` is pruned rather than shipped because the subset that would ship is USELESS:
    distutils' default `tests/test*.py` glob is non-recursive, so `conftest.py` and the
    fixture packages beside it stay out and the shipped tests cannot run. Shipping a broken
    test tree is worse than shipping either a working one or none.
    """
    names = pristine_sdist
    assert len(names) > 20, "the sdist is implausibly small; the build produced almost nothing"
    assert _sdist_root_entries(names) == SDIST_ROOT_MEMBERS
    assert not [n for n in names if "/tests/" in n], "tests must not ship in the sdist"


def test_the_sdist_ships_every_packaged_template(pristine_sdist):
    """Root MEMBERS are not root CONTENTS, and the difference is the whole reason this exists.

    `_sdist_root_entries` says `sluice` is present; it says nothing about what is inside it.
    Measured: appending `exclude sluice/templates/*.html.j2` to MANIFEST.in produced an sdist
    carrying ZERO template members while all 24 packaging tests stayed green -- `len(names) >
    20` still held at 134, the root-member equality still matched, and the `/tests/` check
    still passed. That is precisely the failure this module opens by describing ("the shipped
    template must reach a WHEEL"), now on the artefact the wheel is BUILT FROM and the one
    #104 makes public and permanent.

    `sluice/renderers/template.py` is the only runtime read of a non-`.py` file in the
    package, so a template that does not ship is a default renderer with nothing to render.

    DERIVED from the tree via `_expected_templates`, never a hardcoded filename: the manifest
    ships `templates/*.html.j2`, so a second template added beside the first must be checked
    too rather than silently unswept.
    """
    expected = _expected_templates()
    assert expected, "found no templates to check, so this guard would pass vacuously"
    prefix = _sdist_root_prefix(pristine_sdist)
    missing = [t for t in expected if f"{prefix}/{t}" not in pristine_sdist]
    assert not missing, (
        f"{missing} missing from the built sdist. The sdist is what the wheel is built FROM "
        f"and what PyPI keeps permanently, so a template absent here is absent everywhere "
        f"downstream of it.")


def test_the_sdist_template_guard_is_falsified_by_excluding_them(tmp_path):
    """The guard above must be FALSIFIABLE, not merely green.

    Built through the SAME helper with only MANIFEST.in mutated, for the reason
    `_build_sdist`'s docstring gives: a guard and a partner observing differently-shaped
    trees prove nothing about each other.

    The last assertion is the point of the whole pair rather than a bonus: with every
    template excluded, the root-member equality is STILL exactly satisfied. That is the blind
    spot in writing, inside the test that closes it -- so a future reader cannot conclude the
    root-member check already covered this.
    """
    original = open(f"{ROOT}/MANIFEST.in", encoding="utf-8").read()
    expected = _expected_templates()
    assert expected, "found no templates to check, so this guard would pass vacuously"
    names = _build_sdist(str(tmp_path),
                         manifest_text=original + "\nexclude sluice/templates/*.html.j2\n")
    prefix = _sdist_root_prefix(names)
    assert f"{prefix}/sluice/renderers/template.py" in names, (
        "the mutation removed more than the templates -- the renderer that reads them is "
        "gone too, so this proves nothing about template packaging specifically")
    assert not [t for t in expected if f"{prefix}/{t}" in names], (
        "excluding the templates did not stop them shipping, so the guard above is not "
        "actually what keeps them in")
    assert _sdist_root_entries(names) == SDIST_ROOT_MEMBERS, (
        "the root-member equality was expected to stay SATISFIED here -- it is blind to a "
        "tree's contents, which is exactly why test_the_sdist_ships_every_packaged_template "
        "exists as a separate assertion. If this now fails, that reasoning has changed and "
        "the sibling's docstring needs revisiting rather than this line relaxing.")


def test_the_sdist_guard_is_falsified_by_dropping_the_prune(tmp_path):
    """The guard above must be FALSIFIABLE, not merely green.

    Built from the SAME helper with only MANIFEST.in mutated -- the guard and its partner
    must observe identically-shaped trees, or the partner proves nothing about the guard.
    """
    original = open(f"{ROOT}/MANIFEST.in", encoding="utf-8").read()
    assert "prune tests" in original, (
        "MANIFEST.in is not written as this guard expects, so dropping the prune would "
        "SILENTLY NO-OP and this test would pass for the wrong reason")
    names = _build_sdist(str(tmp_path), manifest_text=original.replace("prune tests", ""))
    assert [n for n in names if "/tests/" in n], (
        "removing `prune tests` did not make tests ship, so the guard above is not "
        "actually what keeps them out")
