# Packaging: pyproject.toml Metadata (PR 1 of #104) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `pyproject.toml` metadata every downstream packaging channel needs (`license`,
`readme`, `authors`, `classifiers`, `keywords`, the full `[project.urls]` set), each field paired
with an assert-then-falsify guard test in `tests/test_packaging.py`, so PR 1 of the #104
packaging sequence is complete and every later PR (PyPI, Docker, deb/rpm, Homebrew) can build on
metadata that is both present and tested.

**Architecture:** No new modules. Every change is a `[project]`-table addition to the existing
`pyproject.toml`, plus new test functions appended to the existing `tests/test_packaging.py`
using its established idiom: build a real wheel with `python -m build --no-isolation` (offline,
~0.6s per build, verified in that file's own docstring), inspect the built artifact's
`.dist-info/METADATA` and `.dist-info/entry_points.txt`, then rebuild with the property
deliberately stripped or corrupted and assert the guard now fails for the right reason.

**Tech Stack:** Python 3.12+, setuptools >=83.0.0 (already pinned in `[build-system]` and the
`test` extra), PEP 639 SPDX license expressions (the modern, non-deprecated form — verified
against PyPA/setuptools documentation before writing this plan, not assumed).

## Global Constraints

- No personal data in `sluice/` or `tests/` — this is a public repo. The `authors` field ships in
  every published sdist/wheel, so it is pinned to the project's existing noreply identity
  (`MrReasonable <4990954+MrReasonable@users.noreply.github.com>`, the same one used in every
  commit trailer in this repo's git history) and never a personal email address. Any synthetic
  "bad" email used in a falsification fixture uses the `example.invalid` TLD (IANA-reserved,
  guaranteed never real), matching this repo's established neutrality convention.
- Every new test in `tests/test_packaging.py` follows the file's own established idiom: a
  positive assertion against a real built wheel, plus a paired falsification test that mutates
  `pyproject.toml`, rebuilds, and asserts the guard actually fires — never a bare positive check.
- `python -m pytest` stays fast, offline, and green. No test may touch the network; `_build_wheel`
  already runs `--no-isolation` for exactly this reason (see the module docstring in
  `tests/test_packaging.py`).
- `ruff check sluice tests scripts` stays clean after every task.
- Conventional Commits for every commit message (`type[(scope)]: description`).
- Target Python versions for `classifiers` are 3.12, 3.13, 3.14 — read directly from
  `.github/workflows/ci.yml`'s `matrix.python-version`, not assumed from prose.
- License is expressed the modern PEP 639 way (`license = "MIT"` + `license-files = ["LICENSE"]`
  under `[project]`) — **not** a `License :: OSI Approved :: MIT License` classifier, which
  setuptools >=77 (this repo pins >=83) now treats as deprecated and warns loudly about if it's
  combined with the SPDX `license` field. The resulting wheel METADATA field is
  `License-Expression: MIT`, not the older `License:` field — this is a real correction to
  issue #104's own text (which predates this verification and says "assert ... METADATA carries
  `License`"), confirmed against `setuptools.pypa.io`'s migration guide and the Python Packaging
  User Guide's core-metadata spec before writing this plan.

---

### Task 1: `license` + `license-files`, and the shared `_read_metadata` test helper

**Files:**
- Modify: `pyproject.toml` (add to the `[project]` table, after `dependencies`)
- Modify: `tests/test_packaging.py` (add a helper + two new tests)

**Interfaces:**
- Consumes: `_build_wheel(dest, *, pyproject_text=None)` (existing, returns `list[str]` of wheel
  archive member names) and `ROOT` (existing, absolute path to the repo root) — both already
  defined at the top of `tests/test_packaging.py`. Do not change either's signature or behavior;
  the two existing tests in that file call `_build_wheel` and depend on it returning a plain name
  list.
- Produces: `_read_metadata(dest: str) -> str` — reads and decodes the `.dist-info/METADATA` file
  from whichever wheel was just built into `{dest}/out/*.whl` by a prior `_build_wheel(dest, ...)`
  call in the same test. Every later task in this plan calls `_build_wheel(dest, ...)` followed by
  `_read_metadata(dest)` on the same `dest`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_packaging.py`, directly below the existing `_build_wheel` function:

```python
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


LICENSE_LINES = 'license = "MIT"\nlicense-files = ["LICENSE"]\n'


def test_wheel_metadata_carries_the_spdx_license_expression(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    metadata = _read_metadata(dest)
    assert "License-Expression: MIT" in metadata, (
        "pyproject.toml's [project] table should declare license = \"MIT\" (PEP 639 SPDX "
        "form) -- without it, PyPI has no machine-readable license for the package page")
    assert "License-File: LICENSE" in metadata, (
        "license-files = [\"LICENSE\"] should be declared so the LICENSE file itself ships "
        "in the sdist/wheel, not just a reference to its name")


def test_the_license_guard_is_falsified_by_dropping_the_license_fields(tmp_path):
    with open(f"{ROOT}/pyproject.toml", encoding="utf-8") as f:
        original = f.read()
    assert LICENSE_LINES in original, (
        "the license fields are not written as this guard expects, so stripping them "
        "would SILENTLY NO-OP and this test would pass for the wrong reason")
    dest = str(tmp_path)
    _build_wheel(dest, pyproject_text=original.replace(LICENSE_LINES, ""))
    metadata = _read_metadata(dest)
    assert "License-Expression:" not in metadata
    assert "License-File:" not in metadata
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -k license -v`
Expected: both new tests FAIL — `test_wheel_metadata_carries_the_spdx_license_expression` fails
its first assertion (no `License-Expression:` line yet), and
`test_the_license_guard_is_falsified_by_dropping_the_license_fields` fails its own `assert
LICENSE_LINES in original` (the fields don't exist in `pyproject.toml` yet, so the guard's setup
assertion is what fails, not the property it's meant to check — the correct failing shape at this
point).

- [ ] **Step 3: Add the license fields to pyproject.toml**

In `pyproject.toml`, in the `[project]` table, immediately after the `dependencies = ["pyyaml"]`
line, add:

```toml
# PEP 639 SPDX license expression -- the modern, non-deprecated form. setuptools >=77 (this
# repo pins >=83 in [build-system] and the test extra) warns loudly if a `License ::`
# classifier is combined with this field, so no such classifier is added below. Emits
# `License-Expression: MIT` in the built wheel's METADATA (Core Metadata 2.4), not the
# older `License:` field.
license = "MIT"
license-files = ["LICENSE"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -k license -v`
Expected: both PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest`
Expected: all tests pass (no regressions in the two pre-existing `test_packaging.py` tests).

Run: `ruff check sluice tests scripts`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "$(cat <<'EOF'
feat(packaging): declare the MIT license via PEP 639 SPDX expression

Adds license and license-files to pyproject.toml so the built wheel's
METADATA carries a machine-readable License-Expression and ships the
LICENSE file itself, guarded by an assert-then-falsify test pair.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 2: `readme`

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `_build_wheel`, `_read_metadata(dest) -> str` (from Task 1), `ROOT`.
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_packaging.py`:

```python
README_LINE = 'readme = "README.md"\n'


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -k readme -v`
Expected: `test_wheel_metadata_carries_the_readme_as_the_long_description` fails (no
`Description-Content-Type:` line yet); the falsify test fails its own setup assertion (`readme`
field doesn't exist yet), the same correct-shape failure as Task 1's Step 2.

- [ ] **Step 3: Add the readme field to pyproject.toml**

In `pyproject.toml`, in the `[project]` table, immediately after the `license-files` line added
in Task 1, add:

```toml
# setuptools auto-derives Description-Content-Type: text/markdown from the .md extension --
# no separate content-type declaration needed. Without this, PyPI's project page renders
# with a blank description.
readme = "README.md"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -k readme -v`
Expected: both PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest`
Run: `ruff check sluice tests scripts`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "$(cat <<'EOF'
feat(packaging): declare README.md as the PyPI long description

Adds readme to pyproject.toml so the built wheel's METADATA carries
the project's real description instead of rendering blank on PyPI,
guarded by an assert-then-falsify test pair.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 3: `authors`, pinned to the project's public noreply identity

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `_build_wheel`, `_read_metadata(dest) -> str`, `ROOT`.
- Produces: `NOREPLY_AUTHOR_EMAIL` constant (string), reused by no later task in this plan but
  kept as a named constant rather than a repeated literal for a future PR that might need it.

This is the field the design spec at
`docs/superpowers/specs/2026-08-09-packaging-distribution-sequencing-design.md` (lines 50-56)
specifically calls out: `authors` ships in every published sdist/wheel, so it must never carry a
personal email address, and the guard must be assert-then-falsify, not a bare positive check —
this task's two tests are exactly that pair.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_packaging.py`. This needs `re` — add `import re` to the existing import block
at the top of the file, alongside the existing `import glob` etc.:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -k author -v`
Expected: `test_wheel_author_identity_is_the_project_noreply_address` fails (no `Author-email:`
line yet, `pyproject.toml` has no `authors` field at all); the falsify test fails its own setup
assertion (`AUTHORS_LINES` isn't in `pyproject.toml` yet), the same correct-shape failure as the
prior two tasks.

- [ ] **Step 3: Add the authors field to pyproject.toml**

In `pyproject.toml`, in the `[project]` table, immediately after the `readme` line added in
Task 2, add:

```toml
# Ships in every published sdist/wheel, so this is pinned to the same noreply identity every
# commit trailer in this repo's git history already uses -- never a personal email address.
authors = [
    {name = "MrReasonable", email = "4990954+MrReasonable@users.noreply.github.com"},
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -k author -v`
Expected: both PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest`
Run: `ruff check sluice tests scripts`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "$(cat <<'EOF'
feat(packaging): declare authors pinned to the project noreply identity

Adds authors to pyproject.toml using the same noreply address already
used in every commit trailer, guarded by an assert-then-falsify test
pair that injects a personal-looking email and confirms the guard
would catch a real leak.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 4: `classifiers` + `keywords`

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `_build_wheel`, `_read_metadata(dest) -> str`, `ROOT`.
- Produces: `EXPECTED_PYTHON_CLASSIFIERS` constant, not consumed elsewhere in this plan.

Python-version classifiers must match the CI matrix exactly (`.github/workflows/ci.yml`,
`matrix.python-version: ["3.12", "3.13", "3.14"]`) rather than being hand-guessed, so a future
CI matrix bump has a test that visibly reminds an implementer to update `pyproject.toml` too.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_packaging.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -k "classifiers or keywords" -v`
Expected: `test_wheel_metadata_carries_the_ci_matrix_python_classifiers` fails on the first
`missing` assertion (no classifiers declared yet); both falsify tests fail their own setup
assertions (neither field exists in `pyproject.toml` yet).

- [ ] **Step 3: Add classifiers and keywords to pyproject.toml**

In `pyproject.toml`, in the `[project]` table, immediately after the `authors` block added in
Task 3, add:

```toml
# Python-version entries must match .github/workflows/ci.yml's matrix.python-version exactly
# (tests/test_packaging.py::test_wheel_metadata_carries_the_ci_matrix_python_classifiers pins
# this) -- a CI matrix bump with no corresponding update here goes silently stale on PyPI.
# No "License :: OSI Approved :: MIT License" classifier: combined with the PEP 639
# license = "MIT" SPDX expression above, setuptools >=77 treats that pairing as deprecated.
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Topic :: Office/Business",
    "Topic :: Utilities",
]
keywords = ["job-search", "job-hunting", "cli", "cv", "resume", "automation"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -k "classifiers or keywords" -v`
Expected: all three PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest`
Run: `ruff check sluice tests scripts`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "$(cat <<'EOF'
feat(packaging): add PyPI classifiers and keywords

Pins Python-version classifiers to the CI matrix exactly and adds
discovery keywords, guarded by assert-then-falsify tests. No License
:: classifier is added since it would form a deprecated combination
with the PEP 639 SPDX license expression from the prior commit.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 5: The remaining `[project.urls]` entries + a console-script regression guard

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `_build_wheel`, `ROOT`.
- Produces: `_read_entry_points(dest) -> str`, not consumed by any later task in this plan.

Issue #104 asks for `Changelog`/`Issues`/`Source`/`Documentation` URLs alongside the existing
`Homepage`, and separately asks that the console script name (`job-sluice`) be pinned by a guard
test — that script entry already exists and works (`[project.scripts]`, unchanged by this plan);
this task only adds the regression guard #104 calls for, so a future rename is caught rather than
silently shipped.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_packaging.py`:

```python
def _read_entry_points(dest):
    """Read and decode entry_points.txt from the wheel `_build_wheel(dest, ...)` just built."""
    wheels = glob.glob(f"{dest}/out/*.whl")
    assert wheels, f"no wheel found in {dest}/out to read entry points from"
    with zipfile.ZipFile(wheels[0]) as zf:
        ep_name = next(n for n in zf.namelist() if n.endswith(".dist-info/entry_points.txt"))
        return zf.read(ep_name).decode("utf-8")


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


def test_wheel_metadata_carries_the_full_project_url_set(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    metadata = _read_metadata(dest)
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
    assert "Project-URL: Changelog," not in metadata
    assert "Project-URL: Issues," not in metadata
    assert "Project-URL: Documentation," not in metadata


def test_wheel_console_script_is_job_sluice(tmp_path):
    dest = str(tmp_path)
    _build_wheel(dest)
    entry_points = _read_entry_points(dest)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -k "project_url or console_script" -v`
Expected: `test_wheel_metadata_carries_the_full_project_url_set` fails on `missing` (only Homepage
exists so far); its falsify counterpart fails its own setup assertion (the extra URLs don't exist
yet). `test_wheel_console_script_is_job_sluice` and
`test_the_console_script_guard_is_falsified_by_a_rename` should both currently PASS, since
`job-sluice = "sluice.cli:main"` already exists in `[project.scripts]` from #103 — these two are
pure regression guards, not new behavior; confirm they pass before touching `pyproject.toml` in
this step, so the later full-suite run isn't the first time you see them exercised.

- [ ] **Step 3: Add the remaining project URLs to pyproject.toml**

In `pyproject.toml`, replace the existing `[project.urls]` table:

```toml
[project.urls]
Homepage = "https://github.com/MrReasonable/sluice"
```

with:

```toml
[project.urls]
Homepage = "https://github.com/MrReasonable/sluice"
Changelog = "https://github.com/MrReasonable/sluice/blob/main/CHANGELOG.md"
Issues = "https://github.com/MrReasonable/sluice/issues"
Source = "https://github.com/MrReasonable/sluice"
Documentation = "https://github.com/MrReasonable/sluice/blob/main/docs/USAGE.md"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -k "project_url or console_script" -v`
Expected: all four PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest`
Run: `ruff check sluice tests scripts`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "$(cat <<'EOF'
feat(packaging): add Changelog/Issues/Source/Documentation project URLs

Rounds out [project.urls] beyond Homepage and adds a regression guard
pinning the console script name to job-sluice, both via
assert-then-falsify test pairs. Completes PR 1 of #104's packaging
sequence -- every field docs/superpowers/specs/2026-08-09-packaging-
distribution-sequencing-design.md's PR 1 row calls for is now present
and tested.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 6: Final verification against the real PyPI upload checks

**Files:** none modified — verification only.

**Interfaces:** none.

This closes the loop against issue #104's own `Verification` section
(`python -m build && python -m twine check --strict dist/*`), which exercises `twine`'s real
metadata validation rather than only this plan's own hand-written assertions.

- [ ] **Step 1: Build a real sdist and wheel from the actual repo root**

Run: `python -m build`
Expected: succeeds, producing `dist/*.tar.gz` and `dist/*.whl`.

- [ ] **Step 2: Run twine's strict metadata check**

Run: `python -m twine check --strict dist/*` (install `twine` first if not already present:
`pip install twine` — it is intentionally not added to any `pyproject.toml` extra, since it is a
one-off verification tool for this task, not something `tests/` or CI needs to import)
Expected: `Checking dist/*: PASSED` for both files, with no warnings. If `twine` reports a
warning about the long description or any metadata field, treat it as a real finding — the
purpose of this task is to catch anything this plan's own tests didn't.

- [ ] **Step 3: Inspect the built wheel's metadata by hand**

Run: `python -m zipfile -l dist/*.whl | grep dist-info`
Expected: a `<name>-<version>.dist-info/` directory containing `METADATA`, `entry_points.txt`,
`LICENSE` (from `license-files`), and `RECORD`.

Run: `unzip -p dist/*.whl '*.dist-info/METADATA' | head -30`
Expected: manually confirm `License-Expression: MIT`, `Author-email:` with only the noreply
address, all four `Classifier: Programming Language :: Python :: 3.1{2,3,4}` lines, and every
`Project-URL:` line added in Task 5.

- [ ] **Step 4: Clean up the verification artifacts**

Run: `rm -rf dist/ build/ *.egg-info`
Expected: repo working tree is clean again — `git status` shows no untracked files from this
task (these directories should already be `.gitignore`d; if `git status` shows them as untracked
instead of ignored, that's a separate finding to raise, not something this task fixes).

- [ ] **Step 5: Confirm no commit needed**

This task is verification-only, per its own Files/Interfaces sections above — nothing to commit.
If Step 2 or Step 3 surfaces a real gap, that becomes a new task inserted before this one, with
its own failing test, not a hand-fix applied here without a test.

---

## Definition of Done

- All five tasks' commits are on the branch, each with its own assert-then-falsify test pair.
- `python -m pytest` passes in full (existing suite plus 13 new tests: 2 from Task 1, 2 from
  Task 2, 2 from Task 3, 3 from Task 4 [one positive check plus separate classifiers and
  keywords falsify tests], 4 from Task 5).
- `ruff check sluice tests scripts` is clean.
- `python -m build && python -m twine check --strict dist/*` reports `PASSED` with no warnings
  (Task 6).
- `pyproject.toml`'s `[project]` table carries `license`, `license-files`, `readme`, `authors`,
  `classifiers`, `keywords`, and `[project.urls]` carries `Homepage`, `Changelog`, `Issues`,
  `Source`, `Documentation` — every field issue #104's "1. pyproject.toml metadata" section and
  the approved design spec's PR 1 row call for.
- Ready for `/review-pr` per this project's standing pre-push cadence, then PR 2 (the publish
  workflow skeleton) can begin.
