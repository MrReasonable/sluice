# Homebrew Tap Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `brew install MrReasonable/tap/job-sluice` as the fourth publish channel, bumped automatically by the release workflow and verified by installing it before anything is pushed.

**Architecture:** A pure Python script renders a Homebrew formula skeleton from release metadata; a `homebrew` job in `release-please.yml` runs it on a macOS runner, lets `brew update-python-resources` fill the resource tree, then audits, installs and tests the result and pushes to the tap only if all of that passes. The formula lives only in the tap and is never hand-edited.

**Tech Stack:** Python 3.12+ (stdlib only), Homebrew, GitHub Actions, pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-homebrew-channel-design.md`

> **SUPERSEDED IN PART — read this before following any task below.** This plan is the record
> of what was planned, not of what shipped. Review found that holding the tap's `contents: write`
> App token in the environment while `brew install --build-from-source` executes ~50 PyPI sdists'
> build backends exposes it to any compromised transitive build dependency. The single
> `.github/scripts/homebrew_bump.sh` this plan specifies was therefore **split** into
> `.github/scripts/homebrew_verify.sh` (render → `update-python-resources` → `audit` → `install`
> → `test`, **no token in the environment**) and `.github/scripts/homebrew_push.sh` (commit and
> push, token present), with the `create-github-app-token` step moved BETWEEN them in both
> workflows. The branch target also became an explicit, validated `PUSH_TARGET` rather than an
> inference from whether the formula file exists. Wherever a task below says `homebrew_bump.sh`,
> or mints the token before verification, **the shipped code is authoritative** — see
> `.github/workflows/release-please.yml`'s `homebrew` job.

## Global Constraints

- **`scripts/` is production code.** It is under the mutation-testing bar (`.rulesync/rules/CLAUDE.md`) and under `ruff check sluice tests scripts`.
- **The renderer is PURE.** It takes `version`, `sdist_url`, `sha256` and returns text. It reads no files, opens no sockets, and imports only stdlib.
- **No test may import a name from `render_homebrew_formula.py` that it also asserts on.** Expected values are literals restated in the test file. This is the whole non-vacuity mechanism and three review rounds lost it; Task 3 enforces it with a test, not a convention. Importing `render` itself is fine — that is the subject, not the expectation.
- **Every assertion needs a mutation witness**, mutating by MOVING or DELETING, never by ADDING. Run the named test by node id and confirm no sibling catches the mutant.
- **Assert SCOPE on every sweep.** `all([])` is `True` and `set() == set()` passes; a matcher that enumerates nothing must fail loudly.
- **Conventional Commits.** `feat(packaging):`, `test(packaging):`, `docs(packaging):`, `ci(release):`.
- **Run before mutation testing:** `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
- **Exact values, copied from the spec:**
  - Extras: `render`, `google`, `mcp`, `completion`
  - Importable core formulae (depended on AND excluded): `cffi`, `cryptography`, `pillow`, `pydantic`, `rpds-py`
  - Forbidden formulae (never depended on): `click`, `brotli`, `zopfli`, `protobuf`, `httpx`
  - Native formula: `pango`; plus `uses_from_macos "libffi"`
  - Python formula: `python@3.14`
  - License: `MIT`; homepage: `https://github.com/MrReasonable/sluice`

---

### Task 1: The renderer, and the extras pin that proves it

**Files:**
- Create: `scripts/render_homebrew_formula.py`
- Create: `tests/test_homebrew_formula.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `render(*, version: str, sdist_url: str, sha256: str) -> str` in `scripts.render_homebrew_formula`. Tasks 2 and 3 assert on its return value. Also module constants `_SHIPPED_EXTRAS`, `_IMPORTABLE_CORE_FORMULAE`, `_FORBIDDEN_FORMULAE`, `_PYTHON_FORMULA` — **which tests must not import.**

- [ ] **Step 1: Write the failing test**

Create `tests/test_homebrew_formula.py`:

```python
"""Offline pins for the Homebrew formula renderer (#104, PR 6 of 7).

EVERY expected value below is a literal restated HERE by a human. None is imported from
`scripts/render_homebrew_formula.py`. That is not style: the formula is machine-generated, so
if the expectation came from the generator both sides would move together and no assertion
could ever fail. Three review rounds of this design lost that property in three different ways
-- deriving from pyproject, then importing the producer's constant, then enforcing it by a grep
in a checklist. `test_the_expectations_are_not_imported_from_the_renderer` is what holds it now.

Mirrors tests/test_linux_packages_channel.py, the sibling channel's guard: import the script's
FUNCTION, restate its expected OUTPUT independently, compare.
"""
import pathlib
import re
import tomllib

from scripts.render_homebrew_formula import render

ROOT = pathlib.Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"
RENDERER = ROOT / "scripts" / "render_homebrew_formula.py"

# Fixture release metadata. Synthetic and offline -- no network, no real release needed.
FIXTURE = {
    "version": "9.9.9",
    "sdist_url": "https://files.pythonhosted.org/packages/ab/cd/job_sluice-9.9.9.tar.gz",
    "sha256": "0" * 64,
}

# The shipping scope, restated. `scripts/render_homebrew_formula.py` has its own copy; these
# two must agree, and the ONLY way to make them disagree is a human editing one of them.
_EXPECTED_EXTRAS = {"render", "google", "mcp", "completion"}


def _pyproject_extras() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    return set(data["project"]["optional-dependencies"])


def test_the_formula_declares_exactly_the_shipped_extras():
    """EQUALITY against the test's own literal supplies non-vacuity; the subset against
    pyproject catches an extra that does not exist. Same two-step as
    test_the_dockerfile_installs_exactly_the_expected_extras, and for the same reason."""
    formula = render(**FIXTURE)
    match = re.search(r'package_name:\s*"job-sluice\[([a-z,]+)\]"', formula)
    assert match, (
        "could not find `pypi_packages package_name:` with a bracketed extras list in the "
        "rendered formula; every assertion below would pass vacuously on an empty set. "
        f"Rendered:\n{formula}"
    )
    found = set(match.group(1).split(","))
    assert found == _EXPECTED_EXTRAS, (
        f"the formula ships {sorted(found)}, expected {sorted(_EXPECTED_EXTRAS)}"
    )
    declared = _pyproject_extras()
    assert declared, "parsed no extras from pyproject.toml; the subset check below is vacuous"
    assert found <= declared, (
        f"the formula names extras pyproject.toml does not declare: {sorted(found - declared)}"
    )
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_homebrew_formula.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.render_homebrew_formula'`

- [ ] **Step 3: Write the minimal renderer**

Create `scripts/render_homebrew_formula.py`:

```python
"""Render the Homebrew formula for job-sluice (#104, PR 6 of 7).

The tap holds no hand-written formula. This renders the skeleton fresh from release metadata
on every bump, and `brew update-python-resources` fills the ~45 resource stanzas afterwards.
Nothing here is edited by hand, which is what makes "the formula lives only in the tap"
coherent rather than merely tidy.

PURE: takes version, sdist url and sha256, returns text. It reads NO files -- not even
pyproject.toml -- so the tests run offline and no ambient file can mask a mutant. The expected
values live in tests/test_homebrew_formula.py as literals a human restated; that test must
never import them from here, because two independent sources is the only thing that makes the
assertions falsifiable at all.
"""

# Shipping scope. tests/test_homebrew_formula.py restates this independently and MUST NOT
# import it -- see this module's docstring. Deriving it from pyproject.toml (the first design)
# collapses the two sources into one and the guard can no longer fail.
_SHIPPED_EXTRAS = ("render", "google", "mcp", "completion")

# homebrew-core formulae whose `install` runs `pip install` against each brewed interpreter,
# so they land in that interpreter's site-packages and a virtualenv created with
# system_site_packages=True (Homebrew's default) can import them. Depending on one removes its
# build from our vendored tree, which is the point: pydantic, cryptography and rpds-py are Rust
# builds and pillow and cffi are C.
#
# A formula whose `install` is `virtualenv_install_with_resources` gets a PRIVATE libexec venv
# and is NOT importable, so `weasyprint` and `fonttools` are deliberately absent here.
#
# These are emitted BOTH as `depends_on` lines and as `exclude_packages`. A name excluded but
# not depended on is an ImportError at runtime; depended on but not excluded vendors a second
# copy.
_IMPORTABLE_CORE_FORMULAE = ("cffi", "cryptography", "pillow", "pydantic", "rpds-py")

# Never depend on these. Two distinct hazards, one tuple because the consequence is identical:
#   - NAME MATCH, different content: `click` is a Kubernetes CLI, `brotli` and `zopfli` are the
#     Google C libraries rather than the Python bindings, `protobuf` is the C++ implementation.
#     `brotli` ships the SAME version string as the Python binding, so a version check would
#     certify it as correct.
#   - NEAR MATCH, no collision at all: our package is `httpx2`; homebrew-core's `httpx` is
#     ProjectDiscovery's Go toolkit. Nothing in our closure is named `httpx`, so a
#     match-based rule cannot reach it -- which is exactly why it is listed by hand.
_FORBIDDEN_FORMULAE = ("click", "brotli", "zopfli", "protobuf", "httpx")

# WeasyPrint's native tree. `pango` pulls cairo, glib and harfbuzz transitively; mirrors
# homebrew-core's own weasyprint formula rather than guessing a wider set.
_NATIVE_FORMULAE = ("pango",)

# THE PAYOFF MECHANISM, and the single most load-bearing line this file emits. Homebrew's
# CPython patches Lib/ctypes/macholib/dyld.py to put HOMEBREW_PREFIX/lib at the head of
# DEFAULT_LIBRARY_FALLBACK, which is what lets WeasyPrint find cairo/pango on macOS with no
# DYLD_FALLBACK_LIBRARY_PATH set. Every other macOS install path needs that variable; this
# channel exists because this line removes the need for it.
#
# It cannot be derived from anything in this repository -- it tracks homebrew-core's default
# CPython, which has no local source of truth. `brew audit --strict` fails on a deprecated or
# missing python@ formula, and the test pins that SOME python@ is named and that its version is
# both at or above pyproject's requires-python floor and among its declared classifiers.
_PYTHON_FORMULA = "python@3.14"

_DESC = "Engineered, config-driven job-hunting pipeline"
_HOMEPAGE = "https://github.com/MrReasonable/sluice"
_LICENSE = "MIT"


def render(*, version: str, sdist_url: str, sha256: str) -> str:
    """The formula skeleton for `version`, as text.

    Keyword-only on purpose: three same-typed strings in a row is exactly the signature where
    a positional swap produces a plausible-looking formula with the wrong digest, and nothing
    downstream would catch it before `brew audit` -- after the release is already public.
    """
    depends = [_PYTHON_FORMULA, *_NATIVE_FORMULAE, *_IMPORTABLE_CORE_FORMULAE]
    depends_lines = "\n".join(f'  depends_on "{name}"' for name in depends)
    excludes = " ".join(_IMPORTABLE_CORE_FORMULAE)
    extras = ",".join(_SHIPPED_EXTRAS)
    return f'''class JobSluice < Formula
  include Language::Python::Virtualenv

  desc "{_DESC}"
  homepage "{_HOMEPAGE}"
  url "{sdist_url}"
  sha256 "{sha256}"
  license "{_LICENSE}"
  version "{version}"

{depends_lines}
  uses_from_macos "libffi"

  pypi_packages package_name: "job-sluice[{extras}]",
                exclude_packages: %w[{excludes}]

  def install
    virtualenv_install_with_resources
  end
end
'''
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_homebrew_formula.py -v`
Expected: PASS

- [ ] **Step 5: Witness the mutation**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
# DELETE one entry from the renderer's shipping scope.
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/render_homebrew_formula.py")
s = p.read_text()
old = '_SHIPPED_EXTRAS = ("render", "google", "mcp", "completion")'
new = '_SHIPPED_EXTRAS = ("render", "google", "completion")'
assert s.count(old) == 1, "anchor did not match; the witness would prove nothing"
p.write_text(s.replace(old, new))
EOF
.venv/bin/python -m pytest tests/test_homebrew_formula.py::test_the_formula_declares_exactly_the_shipped_extras -v
```

Expected: **FAIL** — "the formula ships ['completion', 'google', 'render'], expected ['completion', 'google', 'mcp', 'render']". This is the whole point: the test's literal still names `mcp`, so the two sources disagree.

Restore:

```bash
git checkout scripts/render_homebrew_formula.py
.venv/bin/python -m pytest tests/test_homebrew_formula.py -v   # green again
```

- [ ] **Step 6: Commit**

```bash
git add scripts/render_homebrew_formula.py tests/test_homebrew_formula.py
git commit -m "feat(packaging): render the Homebrew formula from release metadata (#104)"
```

---

### Task 2: Pin the dependency emission in both directions

**Files:**
- Modify: `tests/test_homebrew_formula.py` (append)

**Interfaces:**
- Consumes: `render(...)` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_homebrew_formula.py`:

```python
# The formulae depended on AND excluded, restated independently of the renderer's own tuple.
# Both directions are asserted below because they fail differently: a name excluded but not
# depended on is an ImportError the moment a user runs the CLI, while one depended on but not
# excluded silently vendors a second copy -- re-adding a Rust build the whole approach exists
# to remove.
_EXPECTED_IMPORTABLE = {"cffi", "cryptography", "pillow", "pydantic", "rpds-py"}
# Emitted as `depends_on` but NOT excluded: the interpreter and the native tree are not Python
# packages, so `exclude_packages` has nothing to say about them.
_EXPECTED_NON_PACKAGE_DEPENDS = {"pango"}


def _depends_on(formula: str) -> set[str]:
    found = set(re.findall(r'^\s*depends_on "([^"]+)"', formula, re.MULTILINE))
    assert found, f"no depends_on lines parsed; every check on them is vacuous:\n{formula}"
    return found


def _exclude_packages(formula: str) -> set[str]:
    match = re.search(r"exclude_packages:\s*%w\[([^\]]*)\]", formula)
    assert match, f"no exclude_packages stanza parsed; checks on it are vacuous:\n{formula}"
    found = set(match.group(1).split())
    assert found, "exclude_packages parsed as empty"
    return found


def test_every_excluded_package_is_also_depended_on():
    """Excluded-but-not-depended-on is an ImportError at runtime: `exclude_packages` tells brew
    not to vendor it, so something else must supply it."""
    formula = render(**FIXTURE)
    excluded = _exclude_packages(formula)
    assert excluded == _EXPECTED_IMPORTABLE, (
        f"excluded {sorted(excluded)}, expected {sorted(_EXPECTED_IMPORTABLE)}"
    )
    assert excluded <= _depends_on(formula), (
        f"excluded but not depended on: {sorted(excluded - _depends_on(formula))}. Nothing "
        f"would supply these at runtime."
    )


def test_every_python_package_depended_on_is_also_excluded():
    """The other direction, which a subset check alone misses: a python package depended on but
    not excluded gets vendored a SECOND time from source -- re-adding a Rust build."""
    formula = render(**FIXTURE)
    depends = _depends_on(formula)
    python_pkg_depends = depends - _EXPECTED_NON_PACKAGE_DEPENDS
    python_pkg_depends = {d for d in python_pkg_depends if not d.startswith("python@")}
    assert python_pkg_depends == _EXPECTED_IMPORTABLE, (
        f"python-package depends_on is {sorted(python_pkg_depends)}, expected "
        f"{sorted(_EXPECTED_IMPORTABLE)}; anything here that is not excluded gets vendored twice"
    )
```

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_homebrew_formula.py -v`
Expected: PASS (Task 1's renderer already emits both from one tuple)

- [ ] **Step 3: Witness the mutation**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/render_homebrew_formula.py")
s = p.read_text()
old = '_IMPORTABLE_CORE_FORMULAE = ("cffi", "cryptography", "pillow", "pydantic", "rpds-py")'
new = '_IMPORTABLE_CORE_FORMULAE = ("cffi", "cryptography", "pillow", "rpds-py")'
assert s.count(old) == 1, "anchor did not match; the witness would prove nothing"
p.write_text(s.replace(old, new))
EOF
.venv/bin/python -m pytest tests/test_homebrew_formula.py -v
```

Expected: **BOTH new tests FAIL.** Dropping `pydantic` removes it from `depends_on` and from `exclude_packages` at once — which is precisely the mutation that silently re-vendors a Rust build and which every earlier version of this guard passed.

Restore: `git checkout scripts/render_homebrew_formula.py`

- [ ] **Step 4: Commit**

```bash
git add tests/test_homebrew_formula.py
git commit -m "test(packaging): pin the formula's dependency emission both ways (#104)"
```

---

### Task 3: Pin the payoff line, the forbidden formulae, and import independence

**Files:**
- Modify: `tests/test_homebrew_formula.py` (append)

**Interfaces:**
- Consumes: `render(...)` from Task 1.
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_homebrew_formula.py`:

```python
# Never depended on. Restated here rather than imported, same as everything else in this file.
# `httpx` is on the list for a DIFFERENT reason from the other four and the distinction matters:
# nothing in our closure is named `httpx` at all (ours is `httpx2`), so a rule phrased as "the
# formula name matches a package we use" would not reach it.
_EXPECTED_FORBIDDEN = {"click", "brotli", "zopfli", "protobuf", "httpx"}

# The only names this module may import from the renderer. `render` is the SUBJECT under test;
# an expectation imported from the producer would make every assertion above unfalsifiable.
_ALLOWED_RENDERER_IMPORTS = {"render"}


def test_the_formula_depends_on_a_brewed_python():
    """THE payoff mechanism. Homebrew's CPython patches ctypes' dyld fallback to include the
    Homebrew prefix; that is the entire reason this channel resolves cairo/pango on macOS
    without DYLD_FALLBACK_LIBRARY_PATH. Deleting this line leaves a formula that still builds
    and still passes `--version`, so nothing else here would catch it."""
    formula = render(**FIXTURE)
    match = re.search(r'depends_on "python@(\d+)\.(\d+)"', formula)
    assert match, (
        "the formula names no `depends_on \"python@X.Y\"`. Without a BREWED interpreter the "
        "renderer row goes dead on macOS and this channel has no reason to exist."
    )
    major, minor = int(match.group(1)), int(match.group(2))
    data = tomllib.loads(PYPROJECT.read_text())

    floor = data["project"]["requires-python"]
    floor_match = re.search(r"(\d+)\.(\d+)", floor)
    assert floor_match, f"could not parse a floor from requires-python {floor!r}"
    assert (major, minor) >= (int(floor_match.group(1)), int(floor_match.group(2))), (
        f"the formula depends on python@{major}.{minor}, below requires-python {floor}"
    )

    # The floor alone is one-sided: it accepts a python that does not exist yet. Classifier
    # membership is the upper bound, and pyproject declares one per supported version.
    supported = {
        tuple(int(p) for p in m.groups())
        for c in data["project"]["classifiers"]
        if (m := re.fullmatch(r"Programming Language :: Python :: (\d+)\.(\d+)", c))
    }
    assert supported, "parsed no per-version Python classifiers; the check below is vacuous"
    assert (major, minor) in supported, (
        f"the formula depends on python@{major}.{minor}, which pyproject.toml declares no "
        f"classifier for. Supported: {sorted(supported)}"
    )


def test_no_forbidden_formula_is_depended_on():
    """homebrew-core carries formulae whose names match ours but whose content is different
    software. `brotli` ships the SAME version string as the Python binding, so a version
    comparison would certify the wrong one."""
    formula = render(**FIXTURE)
    hit = _depends_on(formula) & _EXPECTED_FORBIDDEN
    assert not hit, (
        f"the formula depends on {sorted(hit)}, which in homebrew-core is different software "
        f"from the Python package of that name"
    )


def test_the_expectations_are_not_imported_from_the_renderer():
    """The property that makes every other test in this file able to fail.

    A test that imports the value it asserts on compares a constant to itself. This design lost
    that property three times -- deriving the extras from pyproject, then importing the
    renderer's own constant, then writing the rule into a checklist instead of a test.

    Local bindings come from `asname or name`, not from the imported name: a sweep keyed on the
    original walks straight past `from x import _SHIPPED_EXTRAS as _EXPECTED`, which is the
    exact alias hazard CLAUDE.md already records.
    """
    import ast

    imported: set[str] = set()
    for node in ast.walk(ast.parse(pathlib.Path(__file__).read_text())):
        if isinstance(node, ast.ImportFrom) and node.module == "scripts.render_homebrew_formula":
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("scripts.render_homebrew_formula"):
                    imported.add(alias.asname or alias.name)
    # SCOPE first: a sweep matching nothing would make the equality below vacuously true.
    assert imported, (
        "this sweep found no import from scripts.render_homebrew_formula at all, but this "
        "module imports `render`. The matcher is broken, and the equality below proves nothing."
    )
    assert imported == _ALLOWED_RENDERER_IMPORTS, (
        f"this module imports {sorted(imported)} from the renderer; only "
        f"{sorted(_ALLOWED_RENDERER_IMPORTS)} is allowed. An EXPECTATION imported from the "
        f"producer makes the assertion that uses it unfalsifiable -- that defect shipped three "
        f"times in this design's review. Restate the value here instead."
    )
```

- [ ] **Step 2: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_homebrew_formula.py -v`
Expected: PASS (all tests)

- [ ] **Step 3: Witness the payoff-line mutation**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("scripts/render_homebrew_formula.py")
s = p.read_text()
old = "    depends = [_PYTHON_FORMULA, *_NATIVE_FORMULAE, *_IMPORTABLE_CORE_FORMULAE]"
new = "    depends = [*_NATIVE_FORMULAE, *_IMPORTABLE_CORE_FORMULAE]"
assert s.count(old) == 1, "anchor did not match; the witness would prove nothing"
p.write_text(s.replace(old, new))
EOF
.venv/bin/python -m pytest tests/test_homebrew_formula.py::test_the_formula_depends_on_a_brewed_python -v
```

Expected: **FAIL** — "the formula names no `depends_on "python@X.Y"`". Deleting the payoff mechanism is now caught; in every earlier revision of this design it was not.

Restore: `git checkout scripts/render_homebrew_formula.py`

- [ ] **Step 4: Witness the import-independence mutation**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path("tests/test_homebrew_formula.py")
s = p.read_text()
old = "from scripts.render_homebrew_formula import render"
new = "from scripts.render_homebrew_formula import _SHIPPED_EXTRAS as _SNEAK, render"
assert s.count(old) == 1, "anchor did not match; the witness would prove nothing"
p.write_text(s.replace(old, new))
EOF
.venv/bin/python -m pytest tests/test_homebrew_formula.py::test_the_expectations_are_not_imported_from_the_renderer -v
```

Expected: **FAIL** — the sweep reports `_SNEAK`, proving it reads the ALIAS rather than the original name. A sweep keyed on `_SHIPPED_EXTRAS` would have missed this exact spelling.

Restore: `git checkout tests/test_homebrew_formula.py`

- [ ] **Step 5: Run the whole suite and the linters**

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tests/test_homebrew_formula.py
git commit -m "test(packaging): pin the payoff line and the import independence (#104)"
```

---

### Task 4: The `homebrew` job, its pins, and the README row — ONE commit

**Files:**
- Modify: `.github/workflows/release-please.yml` (add a `homebrew` job after `attest-image`)
- Modify: `tests/test_release_publish_wiring.py` (`_RELEASE_PLEASE_JOBS` at :122-123; `_CHANNEL_JOBS` at :1509; add a `_permissions_block` equality test; add helpers to `_MODULE_HELPER_NAMES` at :1453 if any are added)
- Modify: `README.md` (the Homebrew row at :126)

**Interfaces:**
- Consumes: `scripts/render_homebrew_formula.py` from Task 1.
- Produces: a `homebrew` job name that Task 5's dispatch workflow reuses the steps of.

**These MUST be one commit.** `test_every_release_job_is_classified_as_channel_or_infrastructure` (`:1552`) asserts `set(_CHANNEL_JOBS) | _NON_CHANNEL_JOBS == jobs` — a set EQUALITY against the live roster, in both directions. Adding the job without the `_CHANNEL_JOBS` entry fails the "unclassified" arm; adding the entry without the job fails the "named here but absent from the workflow" arm; and `test_...channel_table...` (`:1596`) asserts `shipped == set(_CHANNEL_JOBS.values())`, so the README row must move with them.

- [ ] **Step 1: Read the neighbouring job for the house idiom**

Run: `sed -n '374,458p' .github/workflows/release-please.yml`

Note before writing anything: the SHA-pinned `uses:` refs with trailing `# vX.Y.Z`, `persist-credentials: false`, `ref: ${{ needs.release-please.outputs.sha }}`, and the per-job `permissions:` block. Copy those pins verbatim rather than looking up new versions.

- [ ] **Step 2: Add the job**

Append to `.github/workflows/release-please.yml`, after `attest-image`:

```yaml
  homebrew:
    # `needs: pypi` is an ORDERING CONSTRAINT, not style: the formula's `url` is a PyPI sdist,
    # so it cannot resolve until that upload has landed. This is the workflow's first
    # cross-channel dependency.
    needs: [release-please, pypi]
    if: success() && needs.release-please.outputs.release_created == 'true'
    # macOS is not a preference. The payoff this channel exists for -- WeasyPrint resolving
    # cairo/pango with no DYLD_FALLBACK_LIBRARY_PATH -- comes from Homebrew's CPython patching
    # Lib/ctypes/macholib/dyld.py, which is a macOS mechanism. Verifying on Linux would prove
    # nothing about what ships.
    runs-on: macos-latest
    # Justified rather than copied: this job DOES check out, for the renderer script. The tap
    # is NOT checked out -- see the step below.
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ needs.release-please.outputs.sha }}
          persist-credentials: false
      - uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        id: tap-token
        with:
          app-id: ${{ secrets.RELEASE_PLEASE_APP_ID }}
          private-key: ${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: homebrew-tap
          permission-contents: write
      - name: Render the formula and bump the tap
        env:
          VERSION: ${{ needs.release-please.outputs.version }}
          TAP_TOKEN: ${{ steps.tap-token.outputs.token }}
        run: bash .github/scripts/homebrew_bump.sh
```

- [ ] **Step 3: Write the bump script**

Create `.github/scripts/homebrew_bump.sh`. The step order below IS the gate: `brew install` and `brew test` run before the push, and Task 4 Step 6 pins that order.

```bash
#!/usr/bin/env bash
# Bump the job-sluice formula in MrReasonable/homebrew-tap (#104, PR 6 of 7).
#
# THE TAP IS NOT AN actions/checkout. `brew` resolves a formula through the tap directory under
# $(brew --repository)/Library/Taps/, which is OUTSIDE $GITHUB_WORKSPACE -- and actions/checkout
# refuses a path outside the workspace. The two requirements are not merely awkward together;
# they describe a step that cannot exist. So the tap is obtained the way brew obtains one, and
# the token is applied ONLY to the push URL, so no credential is written to .git/config.
#
# `brew tap-new` is NOT used, for two independent reasons, both measured against the installed
# Homebrew (dev-cmd/tap-new.rb:95-99): it unconditionally writes .github/dependabot.yml and
# three workflows -- only `git init` is behind --no-git -- and one of them runs
# `brew bump --open-pr` daily, which would be a SECOND automated writer of a formula this
# design declares machine-owned. Independently, an App token scoped `contents: write` cannot
# push anything under .github/workflows/ at all.
set -euo pipefail

: "${VERSION:?}" "${TAP_TOKEN:?}"

TAP_OWNER="mrreasonable"
TAP_DIR="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap"
FORMULA_REL="Formula/job-sluice.rb"

# The sdist URL and digest for the EXACT released version, from the index that job just
# published to. Never a guessed filename: PyPI's path contains a content hash.
read -r SDIST_URL SDIST_SHA < <(python3 - "$VERSION" <<'PY'
import json, sys, urllib.request
version = sys.argv[1]
with urllib.request.urlopen(f"https://pypi.org/pypi/job-sluice/{version}/json", timeout=60) as r:
    data = json.load(r)
sdists = [u for u in data["urls"] if u["packagetype"] == "sdist"]
if len(sdists) != 1:
    raise SystemExit(f"expected exactly one sdist for {version}, found {len(sdists)}")
print(sdists[0]["url"], sdists[0]["digests"]["sha256"])
PY
)

git clone "https://github.com/${TAP_OWNER}/homebrew-tap.git" "$TAP_DIR" 2>/dev/null || true
mkdir -p "$TAP_DIR/Formula"

# BOOTSTRAP OBSERVABLE: whether the FORMULA exists, not whether the repo has commits. A tap
# carrying only an auto-created README has commits, so a commit-based check would take the
# scratch arm while `update-python-resources` edited nothing.
if [ -f "$TAP_DIR/$FORMULA_REL" ]; then
  TARGET_BRANCH="bump-${VERSION}"
else
  TARGET_BRANCH="$(git -C "$TAP_DIR" symbolic-ref --short HEAD 2>/dev/null || echo main)"
fi

python3 - "$VERSION" "$SDIST_URL" "$SDIST_SHA" "$TAP_DIR/$FORMULA_REL" <<'PY'
import sys, pathlib
sys.path.insert(0, ".")
from scripts.render_homebrew_formula import render
version, url, sha, out = sys.argv[1:5]
pathlib.Path(out).write_text(render(version=version, sdist_url=url, sha256=sha))
PY

# --ignore-main-package-cooldown is REQUIRED, not optional: this job runs minutes after the
# PyPI upload and the resolver otherwise refuses a package that new. Homebrew honours the flag
# for non-official taps only, which ours is.
brew update-python-resources --version "$VERSION" --ignore-main-package-cooldown \
  "${TAP_OWNER}/tap/job-sluice"
brew audit --strict --online "${TAP_OWNER}/tap/job-sluice"
brew install --build-from-source "${TAP_OWNER}/tap/job-sluice"
brew test "${TAP_OWNER}/tap/job-sluice"

# Only now. A green job that pushed an unverified formula would repeat the deb/rpm failure
# exactly: three root-only container runs certified a package no ordinary user could run.
git -C "$TAP_DIR" -c user.name="sluice-release-please[bot]" \
    -c user.email="sluice-release-please[bot]@users.noreply.github.com" \
    checkout -B "$TARGET_BRANCH"
git -C "$TAP_DIR" add "$FORMULA_REL"
git -C "$TAP_DIR" -c user.name="sluice-release-please[bot]" \
    -c user.email="sluice-release-please[bot]@users.noreply.github.com" \
    commit -m "job-sluice ${VERSION}"
git -C "$TAP_DIR" push \
  "https://x-access-token:${TAP_TOKEN}@github.com/${TAP_OWNER}/homebrew-tap.git" \
  "$TARGET_BRANCH"
```

Make it executable: `chmod +x .github/scripts/homebrew_bump.sh`

- [ ] **Step 4: Add the formula's `test do` block to the renderer**

The `brew test` above needs one. Modify `render()` in `scripts/render_homebrew_formula.py`, replacing the `def install` block's closing `end` and the final `end`:

```python
    return f'''class JobSluice < Formula
  include Language::Python::Virtualenv

  desc "{_DESC}"
  homepage "{_HOMEPAGE}"
  url "{sdist_url}"
  sha256 "{sha256}"
  license "{_LICENSE}"
  version "{version}"

{depends_lines}
  uses_from_macos "libffi"

  pypi_packages package_name: "job-sluice[{extras}]",
                exclude_packages: %w[{excludes}]

  def install
    virtualenv_install_with_resources
  end

  test do
    # The ambient environment is NOT clean: SLUICE_CONFIG and VAULT_DIR would point a local
    # `brew test` at the maintainer's real vault and read their live config.
    ENV.delete("SLUICE_CONFIG")
    ENV.delete("VAULT_DIR")
    ENV["HOME"] = testpath
    ENV["XDG_CONFIG_HOME"] = testpath/"config"
    ENV["XDG_STATE_HOME"] = testpath/"state"

    assert_match version.to_s, shell_output("#{{bin}}/job-sluice --version")

    # `doctor --offline` exits 1 on ANY unconfigured machine BY DESIGN -- no vault directory
    # and no `claude` CLI are both DEAD rows, and exit_code returns 1 on any DEAD. Measured.
    # ci.yml records the same fact for the container smoke and asserts the status in NEITHER
    # direction. Asserting success here would fail every release.
    report = shell_output("#{{bin}}/job-sluice doctor --offline", 1)
    assert_match "job-sluice doctor", report

    # THE PAYOFF, POSITIVE rather than a refutation of "dead": core/app.py's
    # `if cv_cfg is not None:` drops the renderer row ENTIRELY on any load_cv_config error,
    # with exit 1 and the banner intact -- so refuting "dead" passes when the row is merely
    # ABSENT. A negative guard that finds nothing is indistinguishable from success.
    # Row format is `f"{{component:12}} {{subject:32}} {{state:9}} ..."` (cli.py:1537).
    assert_match(/renderer\\s+cv\\.renderer\\s+ok/, report)

    # ...and independently of sluice's own output format, so a change to doctor's printing
    # cannot silently retire the check above.
    system libexec/"bin/python", "-c",
           "import weasyprint; weasyprint.HTML(string='<p>x</p>').write_pdf('t.pdf')"
    assert_predicate testpath/"t.pdf", :exist?
  end
end
'''
```

- [ ] **Step 5: Update the classification and roster**

In `tests/test_release_publish_wiring.py`:

```python
# :122-123 -- file order matters; homebrew comes last, after attest-image
_RELEASE_PLEASE_JOBS = ["release-please", "build", "linux-packages", "attest", "pypi",
                        "release-assets", "docker", "attest-image", "homebrew"]

# :1509
_CHANNEL_JOBS = {"pypi": "PyPI", "docker": "Docker", "linux-packages": "deb / rpm",
                 "homebrew": "Homebrew"}
```

In `README.md`, replace the Homebrew row at :126:

```markdown
| Homebrew | shipped | `brew install MrReasonable/tap/job-sluice` |
```

- [ ] **Step 6: Add the job's own pins**

Append to `tests/test_release_publish_wiring.py`:

```python
def test_the_homebrew_job_has_no_elevated_permissions():
    """Every job in this file carries an exact `_permissions_block` equality pin -- a property
    `attest-image`'s own comment asserts and uses to argue that no future job can hold a
    registry credential and an OIDC identity together. A roster entry WITHOUT this pin is
    worse than useless: `_ROSTER_MESSAGE` says extending the list alone restores the blind
    spot it exists to close.

    `contents: read` and nothing else. The cross-repo write is a SCOPED APP TOKEN for a
    different repository, which is why `release-assets` remains the only holder of
    `contents: write` on this workflow's GITHUB_TOKEN.
    """
    assert _permissions_block(RELEASE_PLEASE, "homebrew") == {"contents": "read"}


def test_the_homebrew_job_runs_on_macos():
    """Not a preference. The payoff -- WeasyPrint resolving cairo/pango with no
    DYLD_FALLBACK_LIBRARY_PATH -- comes from Homebrew's CPython patching ctypes' dyld
    fallback, a macOS mechanism. A Linux runner would verify nothing this channel is for."""
    assert "runs-on: macos-latest" in _job_directives(RELEASE_PLEASE, "homebrew")


def test_the_homebrew_job_waits_for_the_pypi_upload():
    """The formula's `url` is a PyPI sdist; it cannot resolve before that upload lands. This
    is the workflow's first cross-channel dependency and it is load-bearing, not stylistic."""
    directives = _job_directives(RELEASE_PLEASE, "homebrew")
    assert re.search(r"needs:\s*\[release-please,\s*pypi\]", directives), directives


def test_the_homebrew_bump_verifies_before_it_pushes():
    """THE GATE, asserted by INDEX ORDER over the script rather than by a condition reference.

    An earlier design pinned that the push referenced the test step's `outcome`. In GitHub
    Actions that reference is only meaningful when the referenced step sets
    `continue-on-error: true`; without it a failing step already ends the job, the reference is
    unreachable, and DELETING it changes nothing -- so both the pin and its witness were
    equivalent mutants.
    """
    script = (ROOT / ".github" / "scripts" / "homebrew_bump.sh").read_text()
    install = script.index("brew install --build-from-source")
    test = script.index("brew test ")
    push = script.index("git -C \"$TAP_DIR\" push")
    assert install < test < push, (
        f"the bump script must install and test the formula BEFORE pushing it; got offsets "
        f"install={install}, test={test}, push={push}. A green job that pushed an unverified "
        f"formula would repeat the deb/rpm failure exactly."
    )


def test_the_homebrew_bump_bypasses_the_release_cooldown():
    """This job runs minutes after the PyPI upload, and `brew update-python-resources` otherwise
    refuses a package that new. Homebrew honours the flag for non-official taps only -- ours is
    non-official, so it applies. Without it the job fails at EVERY release."""
    script = (ROOT / ".github" / "scripts" / "homebrew_bump.sh").read_text()
    assert "--ignore-main-package-cooldown" in script


def test_the_homebrew_bump_never_uses_tap_new():
    """`brew tap-new` unconditionally writes .github/dependabot.yml and three workflows -- only
    `git init` is behind --no-git (dev-cmd/tap-new.rb:95-99). One runs `brew bump --open-pr`
    daily, which would be a SECOND automated writer of a formula this design declares
    machine-owned. Independently, an App token scoped `contents: write` cannot push anything
    under .github/workflows/, so such a bootstrap would fail at the push regardless."""
    script = (ROOT / ".github" / "scripts" / "homebrew_bump.sh").read_text()
    body = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    assert "tap-new" not in body, "the bump script must never call `brew tap-new`"
```

- [ ] **Step 7: Run the suite**

```bash
.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
```

Expected: all green. If `_MODULE_HELPER_NAMES` (:1453) fails, you added a module-level helper — add its name to that set and give it `path` as a first, required parameter.

- [ ] **Step 8: Witness the gate mutation**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path(".github/scripts/homebrew_bump.sh")
s = p.read_text()
push = 'git -C "$TAP_DIR" push \\\n  "https://x-access-token:${TAP_TOKEN}@github.com/${TAP_OWNER}/homebrew-tap.git" \\\n  "$TARGET_BRANCH"\n'
assert s.count(push) == 1, "anchor did not match; the witness would prove nothing"
s = s.replace(push, "")
s = s.replace("brew install --build-from-source", push + "brew install --build-from-source")
p.write_text(s)
EOF
.venv/bin/python -m pytest tests/test_release_publish_wiring.py::test_the_homebrew_bump_verifies_before_it_pushes -v
```

Expected: **FAIL** — the push now precedes install and test. Restore: `git checkout .github/scripts/homebrew_bump.sh`

- [ ] **Step 9: Commit — one commit, all of it**

```bash
git add .github/workflows/release-please.yml .github/scripts/homebrew_bump.sh \
        scripts/render_homebrew_formula.py tests/test_release_publish_wiring.py README.md
git commit -m "feat(packaging): bump a Homebrew tap formula on release (#104)"
```

---

### Task 5: The whole-chain dispatch workflow and its pins

**Files:**
- Create: `.github/workflows/homebrew-dry-run.yml`
- Modify: `tests/test_release_publish_wiring.py` (add a `HOMEBREW_DRY_RUN` Path constant beside `RELEASE_PLEASE`/`TESTPYPI` at :39-40, plus pins)

**Interfaces:**
- Consumes: `.github/scripts/homebrew_bump.sh` from Task 4.
- Produces: nothing.

**Nothing forces these pins.** Verified: no test globs `.github/workflows`, and the two existing references are per-file `Path` constants rather than an asserted set. Adding a fourth workflow breaks nothing and is simply invisible to the suite — and this is the file holding the cross-repo write token, so the pins must be written deliberately.

- [ ] **Step 1: Create the workflow**

```yaml
name: Homebrew dry run

# Proves the WHOLE chain -- render, update-python-resources, audit, install, test, push --
# against the currently released version, before a real release depends on it. The `pypi` job
# had the same unproven-mechanism problem in PR 3 and it was closed the same way, by
# `testpypi.yml`.
#
# It also BOOTSTRAPS the tap: the tap is empty, and `brew update-python-resources` edits a
# formula rather than creating one, so the first run is what writes Formula/job-sluice.rb. The
# bump script branches on whether that file exists, pushing the default branch when it does not
# and a scratch branch when it does.
#
# `workflow_dispatch` only fires for a file already on the default branch, which is why this
# runs AFTER the PR merges rather than as part of its gate.
on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  dry-run:
    runs-on: macos-latest
    permissions:
      contents: read
    steps:
      - name: Refuse to bump the tap from a non-default branch
        if: github.ref_name != github.event.repository.default_branch
        run: |
          echo "::error::Dispatch this workflow from the default branch -- it pushes to a public tap, so an unmerged branch must not become the tree of record."
          exit 1
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        id: tap-token
        with:
          app-id: ${{ secrets.RELEASE_PLEASE_APP_ID }}
          private-key: ${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: homebrew-tap
          permission-contents: write
      - name: Resolve the current released version
        id: version
        run: |
          python3 - <<'PY' >> "$GITHUB_OUTPUT"
          import json, urllib.request
          with urllib.request.urlopen("https://pypi.org/pypi/job-sluice/json", timeout=60) as r:
              print("version=" + json.load(r)["info"]["version"])
          PY
      - name: Render, verify and bump
        env:
          VERSION: ${{ steps.version.outputs.version }}
          TAP_TOKEN: ${{ steps.tap-token.outputs.token }}
        run: bash .github/scripts/homebrew_bump.sh
```

- [ ] **Step 2: Add the constant and the pins**

In `tests/test_release_publish_wiring.py`, beside the existing constants at :39-40:

```python
HOMEBREW_DRY_RUN = ROOT / ".github" / "workflows" / "homebrew-dry-run.yml"
```

Append:

```python
def test_the_homebrew_dry_run_refuses_a_non_default_branch():
    """It pushes to a PUBLIC tap, so an unmerged branch must not become the tree of record.
    `testpypi.yml` carries the identical guard for the identical reason."""
    text = _text(HOMEBREW_DRY_RUN)
    assert "github.ref_name != github.event.repository.default_branch" in text
    assert "exit 1" in text


def test_the_homebrew_dry_run_has_no_elevated_permissions():
    """This is the file holding the cross-repo write token, and NOTHING forces it to be pinned:
    no test globs .github/workflows, so a fourth workflow file is invisible to the suite unless
    someone writes its pins deliberately."""
    assert _permissions_block(HOMEBREW_DRY_RUN, "dry-run") == {"contents": "read"}


def test_the_homebrew_dry_run_drives_the_same_script_as_the_release_job():
    """One script, two callers. A dry run exercising a DIFFERENT path from the release job
    would prove nothing about the release job -- which is the entire purpose of running it."""
    invocation = "bash .github/scripts/homebrew_bump.sh"
    assert invocation in _text(HOMEBREW_DRY_RUN)
    assert invocation in _job_directives(RELEASE_PLEASE, "homebrew")
```

- [ ] **Step 3: Run the suite and the workflow linter**

```bash
.venv/bin/python -m pytest tests/test_release_publish_wiring.py -v
.venv/bin/python -m pytest
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```

Expected: all green. `zizmor` is what CI's `lint` job runs (`ci.yml:28`) — same invocation.

- [ ] **Step 4: Witness the branch-guard mutation**

```bash
.venv/bin/python - <<'EOF'
import pathlib
p = pathlib.Path(".github/workflows/homebrew-dry-run.yml")
s = p.read_text()
old = "        if: github.ref_name != github.event.repository.default_branch\n"
assert s.count(old) == 1, "anchor did not match; the witness would prove nothing"
p.write_text(s.replace(old, ""))
EOF
.venv/bin/python -m pytest tests/test_release_publish_wiring.py::test_the_homebrew_dry_run_refuses_a_non_default_branch -v
```

Expected: **FAIL**. Restore: `git checkout .github/workflows/homebrew-dry-run.yml`

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/homebrew-dry-run.yml tests/test_release_publish_wiring.py
git commit -m "ci(release): prove the Homebrew chain before a release needs it (#104)"
```

---

### Task 6: The documentation this PR makes false

**Files:**
- Modify: `README.md` (~:114, ~:135, and the macOS rendering note ~:377-385)
- Modify: `docs/TROUBLESHOOTING.md` (~:43)
- Modify: `sluice/renderers/template.py` (~:39)
- Modify: `Dockerfile` (~:21)

**Interfaces:** none.

Grep the CLAIM, not the changed code. `sluice/core/doctor.py` is deliberately **not** in this list: it says only "the `DYLD_FALLBACK_LIBRARY_PATH` note on macOS", which stays accurate.

- [ ] **Step 1: Find every site**

```bash
grep -rn 'DYLD_FALLBACK_LIBRARY_PATH' README.md docs/ sluice/ Dockerfile
grep -n 'still marked \*planned\*\|Rows marked \*planned\*' README.md
grep -n '3\.13-slim' Dockerfile
```

- [ ] **Step 2: Correct README's macOS note**

Replace the "measured rather than assumed" paragraph. The operative variable is the **interpreter**, not the libraries:

````markdown
**macOS, measured rather than assumed:** with cairo/pango/gdk-pixbuf installed via Homebrew,
`import weasyprint` still failed under a non-Homebrew Python until the dynamic linker was told
where to look:

```bash
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
```

The variable that decides this is the **interpreter**, not the libraries. Homebrew's own CPython
patches `ctypes`' library-search fallback to include the Homebrew prefix, so a `brew install`
of job-sluice needs no such export — while a `pip install` under a version-manager Python does.
````

- [ ] **Step 3: Update the two `planned` sentences**

Both go false once no row is planned. Reword to reference the table rather than a state that no longer exists.

- [ ] **Step 4: Narrow `template.py`'s message**

`sluice/renderers/template.py:39` says the loader-path step is needed "even once Homebrew has installed them". Still true for a pip install under a non-Homebrew interpreter, so **narrow** rather than delete — say that a `brew install` of job-sluice does not need it.

- [ ] **Step 5: Add the Homebrew answer to TROUBLESHOOTING**

Beside the export at `docs/TROUBLESHOOTING.md:43`.

- [ ] **Step 6: Fix the Dockerfile comment**

`Dockerfile:21` still names `3.13-slim` while the `FROM` below reads 3.14 — Dependabot rewrote the code and left the prose. **Remove the version from the comment** rather than correcting it: correcting re-rots at 3.15, and the comment's actual point is that the tag lives in the reference rather than a trailing comment.

- [ ] **Step 7: Run the docs guards**

```bash
.venv/bin/python -m pytest tests/test_docs_claims.py tests/test_no_copy_instruction.py -v
.venv/bin/python -m pytest
```

- [ ] **Step 8: Commit**

```bash
git add README.md docs/TROUBLESHOOTING.md sluice/renderers/template.py Dockerfile
git commit -m "docs(packaging): correct the macOS loader-path claim for the tap (#104)"
```

---

## After merge — owner-executed, before the next release

These cannot be in the PR. `workflow_dispatch` only fires for a file already on the default branch, and the rest live in another repository.

1. **Create the `MrReasonable/homebrew-tap` repository, public, with an initial commit** (a plain README is enough — an empty, zero-commit repository has no default branch). `homebrew_verify.sh` resolves the tap's default branch via `git remote set-head origin --auto` against `origin/HEAD`; a genuinely empty repo has nothing for that to resolve, and the script refuses loudly at that point with a message that names no branch and does **not** say "create the tap first" — it reads like a bug in the script rather than a missing prerequisite.
2. **Install the release-please GitHub App on that repository** (the App's own installation settings, or GitHub's Settings → Integrations → GitHub Apps), scoped at least to `homebrew-tap`. Without it, `actions/create-github-app-token` in both the `homebrew` job and `homebrew-dry-run.yml` cannot mint a `contents: write` token for that repo, and the push step fails — *after* the full render/audit/install/test run has already completed.
3. **Dispatch `Homebrew dry run` from `main`.** This bootstraps the tap — writes `Formula/job-sluice.rb` and its default branch — and proves the whole chain. Run it **immediately** after merge: the README's Install cell carries a `brew install` command that fails until the tap holds a formula.
4. **Add a weekly scheduled `brew test` workflow to the tap.** Approach B couples us to homebrew-core, and a `pydantic` or `cryptography` bump breaks *installed users* before it breaks our pipeline. The owner adds this by hand because an App token scoped `contents: write` cannot push under `.github/workflows/`.

## Definition of done

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Plus: every mutation witness above run by node id and confirmed to fail for the stated reason; `/review-pr` run before spending a CodeRabbit slot; CodeRabbit APPROVED on head.
