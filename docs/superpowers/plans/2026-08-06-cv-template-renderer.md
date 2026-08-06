# CV Template Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sluice cv run` produce a PDF from a user-supplied Jinja2 template, so a fresh install stops dying at the render step after the LLM spend.

**Architecture:** A pure `parse_cv` turns the composed CV into a `CvDocument`; a new `template` renderer fills a user's Jinja2 template with it and writes the PDF via WeasyPrint; the retired `weasyprint` renderer is removed behind a named migration branch. Sluice owns the content and the contract, the user owns the design.

**Tech Stack:** Python 3.12+, Jinja2 (new, `render` + `test` extras), WeasyPrint (existing `render` extra), setuptools `package-data`.

**THE SPEC IS NORMATIVE:** `docs/superpowers/specs/2026-08-06-cv-template-renderer-design.md`. Where a task says "per §X", read §X and implement what it says. Do **not** re-derive its decisions from this plan — this plan supplies sequencing, test code, and verification, not a second copy of the design. Where this plan and the spec disagree, the spec wins except on the three corrections listed below.

---

## Global Constraints

- `sluice/` is standard-library only. `jinja2` and `weasyprint` are imported **lazily inside the factory** — the registry must populate without the extras installed.
- Every renderer is reached **only past the fabrication gate**. No renderer validates. Nothing in this change touches `cv/validate.py`, `cv/compose.py`'s `_RULES`, or the `script` renderer's behaviour (spec §Out of scope).
- **No personal data** in `sluice/`, `tests/`, or `docs/`. Fixtures use the `Example …` / `example.invalid` family. Do **not** add a bare real place name — use `EXAMPLECITY`. (A bare `LONDON` residual already exists in ~6 test files; do not add to it.)
- **No `pytest.importorskip` in any new test module.** CI installs `[test]`, never `[render]`; an `importorskip` silently skips the test and reads as green. This trap has already cost this repo one live guard.
- **Run `python -m pytest` before EVERY commit, docs commits included.** A previous session committed a spec alongside tests written under a superseded plan: 3 red.
- Conventional commits. `feat(cv):`, `fix(cv):`, `docs:`, `chore(deps):` — release-please reads the subjects.
- Mutate by **MOVING or DELETING, never ADDING**. Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once before any mutation witness. Commit before witnessing; restore from the source string held in memory, never `git checkout`.
- A mutation killed by a **pre-existing** test witnesses nothing. Run the named new test **by node id** and confirm no neighbour catches the mutant.

---

## Measured facts (established 2026-08-06 — do not re-derive)

These were verified by execution today, on this machine, against this checkout. They resolve the spec's stated blocker.

| Fact | Evidence |
| --- | --- |
| **A wheel builds.** The spec's "no modern setuptools offline" blocker is RESOLVED. | `python -m build --wheel` succeeds; `build` 1.5.0, `setuptools` 83.0.0 now installed in `.venv`. |
| **`--no-isolation` builds in 0.6s with no network.** | Timed. This is what makes a real built-artefact test viable inside a hermetic suite. |
| **The current wheel ships ZERO non-`.py` files under `sluice/`.** | `zipfile` namelist over the built wheel: 100 `sluice/` entries, 0 non-`.py`. The spec's packaging prediction, witnessed. |
| **`sluice/templates/__init__.py` alone is NOT enough.** With it present but no `package-data`, the wheel ships `__init__.py` and NOT `cv_plain.html.j2`. Adding `package-data` ships both. | Both directions built and inspected; 1.11s total. **Test #14's mutation witness is therefore already done — reproduce it, don't trust this row.** |
| **`build/` and `dist/` are NOT in `.gitignore`.** A wheel build drops an untracked `build/` in the repo root. | `git check-ignore -v build/` returns nothing; `git status` showed `?? build/`. |
| **`jinja2` is NOT installed.** | `ModuleNotFoundError`. Hence the `test`-extra requirement. |
| **`weasyprint` 69.0 IS installed but fails to import** without `DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"`; with it, imports fine. | Both measured. Set it for any real-PDF step. |
| **`UnknownAdapter` already takes a `hint` kwarg** (`core/plugins.py`). The retired-name message has a natural home; no new exception type is needed. | Read at `core/plugins.py`. |
| **The derived heading set is exactly `{PROFILE, WORK EXPERIENCE, CERTIFICATES, EDUCATION}`.** | Ran the derivation against the real `_RULES`. Test #13 is viable without hand-listing. |

### Three corrections to the spec

1. **The orphaned-`weasyprint` count is SEVEN user-facing sites, not six.** The spec's table misses `CHANGELOG.md:124`, which tells a user to "set `cv.renderer: weasyprint`". **Treatment differs**: a changelog is a historical record of the v0.1.0 release and must **not** be rewritten. Leave line 124 alone and let release-please's new entry carry the migration note (spec §Migration case 3). Verify by grep, not by this count.
2. **Four TEST sites also break** and the spec lists none of them: `tests/test_plugins.py:30`, `tests/test_renderers.py:33,44,60-88`, `tests/test_onboard_questions.py:71-72`, `tests/harness/renderer.py:49` (docstring). Note `sluice/onboard/questions.py` derives its renderer *choices* from the registry (self-healing) but the *hint* and the *test* are hardcoded.
3. **Two more `.rulesync/rules/CLAUDE.md` sites go stale** beyond the spec's `:357-358` and `:405` — line `24` and line `362` both enumerate the `test` extra as "`pytest`, `faker`, `pytest-cov`", which this change adds to.
4. **The `\s*`-eats-newlines effect is narrower than the spec's phrasing.** Measured: strip-then-split and split-then-strip diverge **only when a citation stands alone on its own line**, where strip-first deletes the line entirely (6 lines vs 7). The spec's conclusion (strip per-field, after reading line structure) is still right; the *reason* is this specific case, and test #5 must pin that case rather than a general claim.
5. **The spec's grammar states the wrong dash, and implementing it literally would bin every CV.** Spec §0 writes the meta line as `MM/YYYY-MM/YYYY` with an ASCII hyphen. But `cv/validate.py:89` matches `\d{2}/(\d{4})\s*[–-]` — **en dash (U+2013) or hyphen, with optional surrounding whitespace** — and this repo's own clean fixture (`tests/test_cv_engine.py`'s `CLEAN_CV`) uses the **en dash**: `02/2023–present | Alfa | Staff Engineer`. A `parse_cv` built to the spec's literal grammar raises `CvParseError` on a CV the gate passes, which under Task 5 sends every lead through a pointless retry and then to `skipped-gate` — the feature would compose CVs and bin all of them. **The dash class must match what the gate already accepts, and must be pinned by a drift test**, on the same rule that made the profile citation strip share `render._CITE_RE`: a check that must agree with another engine has to share the pattern, because a comment claiming equality is not a check.

---

## File structure

**Create**
- `sluice/cv/parse.py` — pure `parse_cv(text) -> CvDocument`. All the risk lives here; no I/O.
- `sluice/templates/__init__.py` — empty; makes `packages.find` descend.
- `sluice/templates/cv_plain.html.j2` — the shipped default template.
- `sluice/renderers/template.py` — the `template` seam entry + the retired-`weasyprint` declaration.
- `docs/cv-template-example.html.j2` — worked example, expressions and CSS only.
- `tests/test_cv_parse.py`, `tests/test_renderer_template.py`, `tests/test_packaging.py`, `tests/test_cv_template_config.py`

**Modify**
- `pyproject.toml` — `package-data`, `jinja2` into `render` + `test`, `setuptools`/`build` into `test`
- `.gitignore` — `build/`, `dist/`
- `sluice/cv/config.py` — `renderer` default, new `template` field, migration raise in `load_cv_config`
- `sluice/cv/engine.py` — parse inside the retry loop
- `sluice/core/plugins.py` — retired-name registry
- `sluice/renderers/script.py:32`, `sluice/onboard/questions.py:207`
- `sluice.yaml.example`, `docs/ARCHITECTURE.md:830-835`, `README.md:63`, `.rulesync/rules/CLAUDE.md:24,357-358,362,405`
- `tests/test_plugins.py`, `tests/test_renderers.py`, `tests/test_onboard_questions.py`, `tests/harness/renderer.py`

**Delete**
- `sluice/renderers/weasyprint.py` (its `_escape` knowledge moves into `template.py`'s `autoescape=True` contract — spec §2)

### One design point the spec leaves implicit

`parse_cv` is called **twice, by two callers, for two reasons**, and that is deliberate:
- `cv/engine.py` calls it as a **gate check** inside the retry loop (spec §0) — it discards the result and only cares whether it raised.
- `renderers/template.py` calls it to **get the document** it renders.

The seam signature `render(cv_text, out_dir, *, neutral_name)` is therefore **unchanged**, so `script` is untouched. Parsing twice is free (pure, no I/O) and the two callers cannot disagree because it is one function.

---

## Task 1: Packaging, the shipped template, and the dependency moves

Sequenced first because three reviewers independently found the obvious guard here cannot fail in CI. If this task cannot be made to work, nothing downstream is worth building.

**Files:**
- Create: `sluice/templates/__init__.py`, `sluice/templates/cv_plain.html.j2`, `tests/test_packaging.py`
- Modify: `pyproject.toml`, `.gitignore`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sluice/templates/cv_plain.html.j2` reachable via `importlib.resources.files("sluice.templates")`; `jinja2` importable under `[test]`. The template renders a `CvDocument` (Task 2's shape) — write it against the field names in spec §1, which are fixed as the public contract.

- [ ] **Step 1: Add `build/` and `dist/` to `.gitignore`**

A wheel build drops an untracked `build/` into the repo root (measured). `tests/test_no_leaked_files.py` exists because artefacts leaking into this public repo is a live failure mode here.

```
build/
dist/
```

- [ ] **Step 2: Make the dependency and packaging changes in `pyproject.toml`**

Three edits. Keep the existing comment block above `test` and extend it — do not replace it.

```toml
[project.optional-dependencies]
render = ["weasyprint", "jinja2"]
google = ["google-api-python-client", "google-auth"]
# jinja2 is in BOTH `render` and `test`, deliberately. CI installs only `[test]`, so a
# shipped-template test written as `pytest.importorskip("jinja2")` would SKIP in CI and
# read as green -- the exact trap tests/test_renderers.py records having been hit by, when
# an importorskip("weasyprint") silently disabled the one test pinning citation stripping.
# jinja2 is pure Python with no system libraries, so it belongs here on the same footing as
# faker. WeasyPrint stays OUT: it needs cairo/pango, and is covered by injected fakes.
#
# setuptools + build are here so tests/test_packaging.py can build a REAL wheel offline
# (`--no-isolation`, measured at 0.6s). Dev-time only, never imported by `sluice/`.
test = ["pytest", "faker", "pytest-cov", "jinja2", "setuptools>=83.0.0", "build"]

# `packages.find` selects PACKAGES, not DATA. Without this table a built wheel ships
# sluice/templates/__init__.py and NOT the template beside it -- measured 2026-08-06 --
# so every `pip install sluice` would get the default renderer with no template to render.
[tool.setuptools.package-data]
sluice = ["templates/*.html.j2"]
```

- [ ] **Step 3: Install the new test dependencies**

Run: `pip install -e ".[test]"`
Expected: jinja2, setuptools, build present. Verify: `python -c "import jinja2, build, setuptools; print('ok')"`

- [ ] **Step 4: Write the failing packaging tests**

Create `tests/test_packaging.py`:

```python
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
"""
import glob
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = "sluice/templates/cv_plain.html.j2"
PKG_DATA = '[tool.setuptools.package-data]\nsluice = ["templates/*.html.j2"]\n'


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
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", f"{dest}/out"],
        cwd=dest, capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"wheel build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return zipfile.ZipFile(glob.glob(f"{dest}/out/*.whl")[0]).namelist()


def test_the_shipped_template_is_in_the_built_wheel(tmp_path):
    names = _build_wheel(str(tmp_path))
    assert TEMPLATE in names, (
        f"{TEMPLATE} is missing from the built wheel. `packages.find` selects PACKAGES, "
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
    names = _build_wheel(str(tmp_path), pyproject_text=original.replace(PKG_DATA, ""))
    assert "sluice/templates/__init__.py" in names   # the PACKAGE still ships...
    assert TEMPLATE not in names                     # ...its DATA does not
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `python -m pytest tests/test_packaging.py -v`
Expected: **BOTH fail, for different reasons, and both reasons matter.**
- `test_the_shipped_template_is_in_the_built_wheel` — fails on "is missing from the built wheel": `sluice/templates/` does not exist yet.
- `test_the_wheel_guard_is_falsified_by_dropping_package_data` — fails on `assert "sluice/templates/__init__.py" in names`, **not** vacuously and **not** on the `PKG_DATA in original` premise (Step 2 already added the table). If it fails on the premise assertion instead, Step 2 did not land — go back and check.

- [ ] **Step 6: Create the template package and the shipped template**

`sluice/templates/__init__.py`:

```python
"""Packaged template data for the `template` renderer.

This file exists so `[tool.setuptools.packages.find]` descends into the directory --
`find` (as opposed to `find_namespace`) will not select a directory without one. The
template BESIDE it ships only because `[tool.setuptools.package-data]` names it;
measured 2026-08-06, this __init__ alone puts the package in the wheel and leaves the
.j2 file out. See tests/test_packaging.py.
"""
```

`sluice/templates/cv_plain.html.j2` — implement per spec §3. The binding constraint is the checkable property spec §3 states: **every literal text node is either a `CvDocument` field reference or a heading `cv/compose.py:_RULES` already emits.** Single-column (spec §Known limitations: a two-column grid defeats ATS text extraction). Field names come from spec §1.

```jinja
{# The shipped DEFAULT template. It is not "neutral" -- a template must lay something
   out, so its layout is a shipped opinion and the spec says so. What IS guaranteed, and
   is guarded by test_the_shipped_template_contributes_no_content, is that it contributes
   no CONTENT: every literal text node below is either a CvDocument field or a heading
   the composer already emits. Single-column on purpose: a grid or table produces a PDF
   whose text extracts in the wrong order, and the destination is an ATS upload. #}
<style>
  @page { size: A4; margin: 18mm 16mm; }
  body { font-family: "DejaVu Sans", Helvetica, sans-serif; font-size: 10.5pt;
         line-height: 1.35; }
  h1 { font-size: 16pt; margin: 0 0 2mm; }
  h2 { font-size: 11pt; margin: 5mm 0 1.5mm; text-transform: uppercase;
       border-bottom: 0.4pt solid #000; }
  .contact { white-space: pre-wrap; margin: 0 0 4mm; }
  .role { margin: 0 0 3mm; }
  .company { font-weight: bold; }
  .meta { font-style: italic; }
  ul { margin: 1mm 0 0; padding-left: 5mm; }
  li { margin: 0 0 0.8mm; }
</style>

<h1>{{ document.name }}</h1>
<div class="contact">{{ document.contact }}</div>

<h2>PROFILE</h2>
<p>{{ document.profile }}</p>

<h2>WORK EXPERIENCE</h2>
{% for role in document.work %}
<div class="role">
  <div class="company">{{ role.company }}</div>
  <div class="meta">{{ role.dates }} | {{ role.location }} | {{ role.title }}</div>
  <ul>{% for bullet in role.bullets %}<li>{{ bullet }}</li>{% endfor %}</ul>
</div>
{% endfor %}

{% if document.certificates %}
<h2>CERTIFICATES</h2>
<ul>{% for item in document.certificates %}<li>{{ item }}</li>{% endfor %}</ul>
{% endif %}

{% if document.education %}
<h2>EDUCATION</h2>
<ul>{% for item in document.education %}<li>{{ item }}</li>{% endfor %}</ul>
{% endif %}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_packaging.py -v`
Expected: both PASS.

- [ ] **Step 8: Witness the guard by mutation**

Commit first (see Global Constraints). Then delete the two `package-data` lines from `pyproject.toml`, run `python -m pytest tests/test_packaging.py::test_the_shipped_template_is_in_the_built_wheel -v` by node id, confirm **RED**, and restore from the string you read into memory — not `git checkout`.
Expected: RED with "is missing from the built wheel", then green again after restore.

- [ ] **Step 9: Full suite, lint, commit**

```bash
python -m pytest && ruff check sluice tests scripts
git add pyproject.toml .gitignore sluice/templates tests/test_packaging.py
git commit -m "$(cat <<'EOF'
feat(cv): package a default CV template and prove it reaches a wheel

`packages.find` selects packages, not data, so `sluice/` shipped zero non-.py
files: a template in the source tree would have reached no `pip install`. The
guard is built against a REAL wheel because CI installs editable, where an
importlib.resources check passes whatever the packaging says.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 2: `sluice/cv/parse.py` — the pure parser

**Files:**
- Create: `sluice/cv/parse.py`, `tests/test_cv_parse.py`
- Test: `tests/test_cv_parse.py`

**Interfaces:**
- Consumes: `sluice.cv.render._CITE_RE` (share the exact regex — do not re-declare one; a check that must match what the renderer delivers has to SHARE its strip, and this repo has already paid for restating one).
- Produces:
  - `CvDocument(name: str, contact: str, profile: str, work: list[Role], certificates: list[str], education: list[str])`
  - `Role(company: str, dates: str, location: str, title: str, bullets: list[str])`
  - `parse_cv(text: str) -> CvDocument`, raising `CvParseError`
  - `CvParseError(ValueError)`

Implement the grammar in spec §0 and the contract in spec §1. Do not invent constraints beyond them; in particular **do not validate facts here** — the fabrication gate has already run, and a second weaker gate is a way around the real one.

- [ ] **Step 1: Write the failing parser tests**

Create `tests/test_cv_parse.py`:

```python
"""`parse_cv` -- pure, no I/O, no fact validation.

All the risk in the template-renderer design lives in this function, which is why it is
pure: every case below is table-driven with no fixtures, no subprocess and no PDF.

Fixtures are synthetic and use the `Example ...`/`example.invalid` family. EXAMPLECITY
rather than a real place name: `tests/` is bound by the no-personal-data rule.
"""
import pytest

from sluice.cv.parse import CvDocument, CvParseError, Role, parse_cv

CV = """\
Phone number: +44 20 7946 0000
Email: someone@example.invalid

EXAMPLE PERSON

PROFILE
Engineer with nine years building data pipelines and the teams that run them.

WORK EXPERIENCE

Example Data Co
03/2021-present | EXAMPLECITY | Staff Engineer
- Cut p99 latency to 120ms [ED1]
- Grew the team from 3 to 8 [ED2]

Example Analytics Ltd
01/2018-02/2021 | EXAMPLECITY | Senior Engineer
- Shipped 4 services [EA1]

CERTIFICATES
- Example Cloud Practitioner, 2022

EDUCATION
- Example University, 2010-2013 | BSc Computer Science
"""


def test_parse_reads_every_section():
    doc = parse_cv(CV)
    assert doc.name == "EXAMPLE PERSON"
    assert "someone@example.invalid" in doc.contact
    assert doc.profile.startswith("Engineer with nine years")
    assert len(doc.work) == 2
    assert doc.certificates == ["Example Cloud Practitioner, 2022"]
    assert doc.education == ["Example University, 2010-2013 | BSc Computer Science"]


def test_parse_reads_multiple_roles_and_their_bullets():
    doc = parse_cv(CV)
    first, second = doc.work
    assert first.company == "Example Data Co"
    assert first.dates == "03/2021-present"
    assert first.location == "EXAMPLECITY"
    assert first.title == "Staff Engineer"
    assert first.bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]
    assert second.company == "Example Analytics Ltd"
    assert second.title == "Senior Engineer"
    assert second.bullets == ["Shipped 4 services"]


def test_parse_raises_on_an_unparseable_meta_line():
    """A meta line missing its pipes must RAISE, not be absorbed into a neighbour.

    Refusing is the whole argument of spec section 0: the CV has passed the fabrication
    gate, so its facts are sound and only its shape is in doubt -- but the artefact goes
    to an employer under the user's name, and a date landing where a title belongs is
    wrong in a way the user does not see until after sending.
    """
    broken = CV.replace("03/2021-present | EXAMPLECITY | Staff Engineer",
                        "03/2021-present Staff Engineer")
    assert "03/2021-present Staff Engineer" in broken, "the replace no-opped"
    with pytest.raises(CvParseError, match="meta line"):
        parse_cv(broken)


def test_parse_strips_citations_from_bullets():
    """The [id] tokens are an INTERNAL artefact of the fabrication gate and must never
    reach an employer. Stripping happens INSIDE parse_cv, so no renderer has to remember
    to do it -- the obligation was previously duplicated per-renderer."""
    doc = parse_cv(CV)
    every_bullet = [b for role in doc.work for b in role.bullets]
    assert every_bullet, "no bullets parsed, so this assertion proves nothing"
    for bullet in every_bullet:
        assert "[" not in bullet and "]" not in bullet


def test_parse_preserves_line_structure_while_stripping():
    """Strip PER FIELD, after the line structure has been read -- never over whole text.

    `_CITE_RE`'s leading `\\s*` matches newlines. Measured 2026-08-06: for a CV whose
    citation sits alone on its own line, stripping the whole text first DELETES that
    line (6 lines vs 7), so the parser would be reading a different document from the
    one the composer emitted. Here the stand-alone citation must not become a third,
    empty bullet, and must not swallow the bullet after it.
    """
    wrapped = CV.replace("- Cut p99 latency to 120ms [ED1]",
                         "- Cut p99 latency to 120ms\n[ED1]")
    assert "120ms\n[ED1]" in wrapped, "the replace no-opped"
    doc = parse_cv(wrapped)
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms",
                                   "Grew the team from 3 to 8"]


def test_parse_refuses_a_section_it_does_not_model():
    """User content must not vanish silently from a PDF sent under their name."""
    extra = CV.replace("CERTIFICATES", "PUBLICATIONS\n- Example paper, 2021\n\nCERTIFICATES")
    assert "PUBLICATIONS" in extra, "the replace no-opped"
    with pytest.raises(CvParseError, match="PUBLICATIONS"):
        parse_cv(extra)


@pytest.mark.parametrize("mutation,replacement,field,expected", [
    # A date must not be absorbed into the title.
    ("03/2021-present | EXAMPLECITY | Staff Engineer",
     "03/2021-06/2024 | EXAMPLECITY | Staff Engineer", "dates", "03/2021-06/2024"),
    # A title containing a hyphen must survive intact.
    ("03/2021-present | EXAMPLECITY | Staff Engineer",
     "03/2021-present | EXAMPLECITY | Staff Engineer - Platform", "title",
     "Staff Engineer - Platform"),
    # A multi-word location must not be split.
    ("03/2021-present | EXAMPLECITY | Staff Engineer",
     "03/2021-present | EXAMPLECITY REGION | Staff Engineer", "location",
     "EXAMPLECITY REGION"),
])
def test_parse_does_not_silently_misassign_fields(mutation, replacement, field, expected):
    """Parsing WRONGLY is the harm the refusal argument rests on, and the first draft of
    the spec specified no case for it. A date absorbed into `title`, a bullet swallowed
    as a company, a meta line read as a heading: each raises nothing and produces a wrong
    PDF. Assert the whole field, not merely that it parsed."""
    text = CV.replace(mutation, replacement)
    assert replacement in text, "the replace no-opped"
    doc = parse_cv(text)
    assert getattr(doc.work[0], field) == expected
    # The neighbouring fields must be undisturbed by the mutation.
    assert doc.work[0].company == "Example Data Co"
    assert doc.work[0].bullets == ["Cut p99 latency to 120ms", "Grew the team from 3 to 8"]


@pytest.mark.parametrize("dash", ["-", "–", " – ", " - "])
def test_parse_accepts_every_date_dash_the_gate_accepts(dash):
    """The gate at cv/validate.py:89 matches `\\d{2}/(\\d{4})\\s*[--]` -- EN DASH or
    hyphen, with optional surrounding whitespace -- and this repo's own CLEAN_CV fixture
    uses the EN DASH. A parser that took the spec's literal `MM/YYYY-MM/YYYY` would raise
    on a CV the gate PASSES, sending every lead through a pointless retry and then to
    skipped-gate: the feature would compose CVs and bin all of them.
    """
    text = CV.replace("03/2021-present", f"03/2021{dash}present")
    assert f"03/2021{dash}present" in text, "the replace no-opped"
    doc = parse_cv(text)
    assert doc.work[0].title == "Staff Engineer"
    assert doc.work[0].location == "EXAMPLECITY"


def test_parse_accepts_the_repos_own_gate_clean_fixture():
    """The behavioural drift pin, and the reason it is behavioural: spec Out of scope
    forbids touching the gate, so there is no shared constant to assert on.

    `tests/test_cv_engine.py::test_clean_cv_is_actually_clean` already proves the GATE
    passes CLEAN_CV, and CLEAN_CV's date ranges use the EN DASH. Importing that exact
    fixture here closes the loop from the other end: whatever the gate certifies clean,
    parse_cv must accept. If these two ever disagree the lead is composed, gated, then
    binned -- so a single shared fixture is what keeps them honest.
    """
    from tests.test_cv_engine import CLEAN_CV
    assert "–" in CLEAN_CV, "the fixture no longer exercises the en dash; re-pick one"
    doc = parse_cv(CLEAN_CV)
    assert [r.title for r in doc.work] == [
        "Staff Engineer", "Senior Engineer", "Engineer", "Junior Engineer"]


def test_parse_returns_the_documented_types():
    """CvDocument is the PUBLIC CONTRACT a template author writes against; changing a
    field name is a breaking change for every user template."""
    doc = parse_cv(CV)
    assert isinstance(doc, CvDocument) and isinstance(doc.work[0], Role)
    assert [f for f in ("name", "contact", "profile", "work", "certificates", "education")
            if not hasattr(doc, f)] == []
    assert [f for f in ("company", "dates", "location", "title", "bullets")
            if not hasattr(doc.work[0], f)] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cv_parse.py -v`
Expected: all FAIL with `ModuleNotFoundError: No module named 'sluice.cv.parse'`.

- [ ] **Step 3: Implement `sluice/cv/parse.py`**

Implement the grammar in spec §0. Required properties, each pinned by a test above:
- Dataclasses `CvDocument` and `Role` exactly as spec §1 names them.
- `CvParseError(ValueError)`.
- Read line structure **first**; strip citations **per field** with `sluice.cv.render._CITE_RE` (imported, not re-declared).
- A section header that `CvDocument` does not model raises, naming the header.
- A non-bullet, non-header line inside `WORK EXPERIENCE` is a company; the line after it must match the meta grammar or `CvParseError` naming "meta line".
- Split the meta line on `|` into exactly three parts; `present` is a legal end date.
- **The date-range dash must accept what the gate accepts** — en dash (U+2013) and hyphen, with optional surrounding whitespace, matching `cv/validate.py:89`. Do **not** refactor a shared constant out of `validate.py`: spec §Out of scope forbids touching the gate, and the pin below is behavioural, which is stronger than a shared literal anyway. Cite `validate.py:89` in a comment beside the dash class.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cv_parse.py -v`
Expected: all PASS.

- [ ] **Step 5: Witness two mutations by node id**

Commit first. Then, one at a time — **delete or move, never add**:
1. Delete the unmodelled-section raise → `test_parse_refuses_a_section_it_does_not_model` must go RED **by node id**, and confirm no other test in the file catches it.
2. Move the per-field citation strip to a whole-text strip at the top of `parse_cv` → `test_parse_preserves_line_structure_while_stripping` must go RED.

Restore each from the source string held in memory.

- [ ] **Step 6: Full suite, lint, commit**

```bash
python -m pytest && ruff check sluice tests scripts
git add sluice/cv/parse.py tests/test_cv_parse.py
git commit -m "$(cat <<'EOF'
feat(cv): parse the composed CV into a structured CvDocument

Pure and deterministic: no I/O, and deliberately no fact validation -- the
fabrication gate has already run, and a second weaker gate here would be a way
around the real one. Citations are stripped PER FIELD, after the line structure
has been read: _CITE_RE's leading \s* matches newlines, so a whole-text strip
deletes a stand-alone citation line and the parser reads a different document
from the one the composer emitted.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 3: `sluice/renderers/template.py` — the seam entry

**Files:**
- Create: `sluice/renderers/template.py`, `tests/test_renderer_template.py`
- Test: `tests/test_renderer_template.py`

**Interfaces:**
- Consumes: `parse_cv`, `CvDocument`, `Role` (Task 2); `sluice/templates/cv_plain.html.j2` (Task 1); `RenderError` from `sluice.renderers.script`.
- Produces: `TemplateRenderer` with `render(cv_text, out_dir, *, neutral_name="CV.pdf") -> str`; registered as `template`; `_make(cvcfg)` reading `cvcfg.template`.

Implement per spec §2 and the spec §Failure modes table.

- [ ] **Step 1: Re-verify the autoescape claim before relying on it**

jinja2 was not installed when this plan was written, so the spec's measurement could not be reproduced. Run it now — a contract asserted and not executed is exactly the failure mode this repo keeps paying for:

```bash
python -c "
from jinja2 import select_autoescape
f = select_autoescape()
print('cv_plain.html.j2 ->', f('cv_plain.html.j2'))
print('cv_plain.html    ->', f('cv_plain.html'))
"
```
Expected: `False` then `True`. If it does not reproduce, STOP and re-derive spec §2's contract before continuing.

- [ ] **Step 2: Write the failing renderer tests**

Create `tests/test_renderer_template.py`:

```python
"""The `template` renderer.

NO `pytest.importorskip` ANYWHERE IN THIS FILE. jinja2 is in the `test` extra precisely
so these run in CI; an importorskip would silently skip them and read as green, which is
the trap tests/test_renderers.py records having been hit by once already.

WeasyPrint is NOT imported here -- it needs cairo/pango (and, on macOS, a
DYLD_FALLBACK_LIBRARY_PATH). It is injected as a fake, exactly as the renderer it
replaces was tested.
"""
import os

import pytest

from sluice.renderers.script import RenderError
from sluice.renderers.template import TemplateRenderer

CV = """\
Email: someone@example.invalid

EXAMPLE PERSON

PROFILE
Engineer with nine years building data pipelines.

WORK EXPERIENCE

Example Data Co
03/2021-present | EXAMPLECITY | Staff Engineer
- Cut p99 latency to <200ms [ED1]

CERTIFICATES
- Example Cloud Practitioner, 2022

EDUCATION
- Example University, 2010-2013 | BSc Computer Science
"""


class FakeHTML:
    """Captures the HTML the renderer hands WeasyPrint, and writes a stub PDF."""
    captured = {}

    def __init__(self, string=""):
        FakeHTML.captured["html"] = string

    def write_pdf(self, path, stylesheets=None):
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4 fake")


def _renderer(tmp_path, template_text=None):
    if template_text is None:
        return TemplateRenderer(None, html_module=FakeHTML, css_module=lambda string="": object())
    path = tmp_path / "user.html.j2"
    path.write_text(template_text, encoding="utf-8")
    return TemplateRenderer(str(path), html_module=FakeHTML,
                            css_module=lambda string="": object())


def test_template_renderer_escapes_html_in_a_bullet(tmp_path):
    """autoescape=True is a CONTRACT, not a filename convention.

    `select_autoescape()` suffix-matches .html/.htm/.xml and returns False for the
    conventional .j2 suffix. With autoescape off, this gate-verified bullet renders as an
    unknown HTML element and WeasyPrint DROPS the text -- so the PDF differs from what
    validate() approved, and nobody sees it until after the CV is sent.
    """
    r = _renderer(tmp_path, "{{ document.work[0].bullets[0] }}")
    r.render(CV, str(tmp_path / "out"))
    html = FakeHTML.captured["html"]
    assert "&lt;200ms" in html, "the bullet was not escaped; WeasyPrint will drop it"
    assert "<200ms" not in html


def test_template_renderer_strips_citations_before_writing(tmp_path):
    """The [id] tokens must never reach an employer. parse_cv strips them, so no
    template can reintroduce them however it is written."""
    r = _renderer(tmp_path, "{{ document.work[0].bullets[0] }}")
    r.render(CV, str(tmp_path / "out"))
    assert "[ED1]" not in FakeHTML.captured["html"]
    assert "Cut p99 latency to" in FakeHTML.captured["html"]


def test_missing_template_file_raises_at_construction(tmp_path):
    """At CONSTRUCTION, not at call time -- the whole point of this feature is that a
    render failure stops arriving after the LLM spend."""
    with pytest.raises(RenderError, match="template"):
        TemplateRenderer(str(tmp_path / "nope.html.j2"), html_module=FakeHTML,
                         css_module=lambda string="": object())


def test_a_template_directory_is_refused_at_construction(tmp_path):
    """os.path.exists() is True for a directory -- the same trap `script` already
    documents. isfile, not exists."""
    d = tmp_path / "adir.html.j2"
    d.mkdir()
    with pytest.raises(RenderError, match="template"):
        TemplateRenderer(str(d), html_module=FakeHTML, css_module=lambda string="": object())


def test_the_shipped_template_renders_a_parsed_document(tmp_path):
    """The REAL jinja2 engine against the REAL shipped template. A fake engine cannot
    prove a template renders."""
    r = _renderer(tmp_path)          # None -> the packaged default
    out = r.render(CV, str(tmp_path / "out"))
    assert out.endswith("CV.pdf") and os.path.exists(out)
    html = FakeHTML.captured["html"]
    for expected in ("EXAMPLE PERSON", "Example Data Co", "Staff Engineer",
                     "EXAMPLECITY", "Example Cloud Practitioner, 2022"):
        assert expected in html, f"{expected!r} missing from the rendered CV"


def test_the_shipped_template_contributes_no_content():
    """The shipped template is NOT neutral -- a template must lay something out, so its
    layout is a shipped opinion. The property that IS achievable and mechanically
    checkable is narrower: it contributes no CONTENT of its own.

    The heading set is DERIVED from cv/compose.py's _RULES, never hand-listed, so this
    guard cannot drift from what the composer actually emits.
    """
    import re
    from importlib.resources import files

    from sluice.cv.compose import _RULES

    headings = {ln.strip() for ln in _RULES.splitlines()
                if ln.strip() and ln.strip() == ln.strip().upper()
                and all(c.isalpha() or c.isspace() for c in ln.strip())}
    assert headings, "derived no headings, so this guard would pass vacuously"

    text = files("sluice.templates").joinpath("cv_plain.html.j2").read_text(encoding="utf-8")
    stripped = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    stripped = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", " ", stripped, flags=re.S)
    stripped = re.sub(r"<[^>]*>", " ", stripped, flags=re.S)
    leftover = {tok for tok in (t.strip() for t in stripped.splitlines()) if tok}
    assert leftover <= headings, (
        f"the shipped template contributes content of its own: {sorted(leftover - headings)}")


def test_absent_jinja2_raises_naming_the_extra(monkeypatch, tmp_path):
    """Fail loudly at construction, naming the fix."""
    import builtins
    real_import = builtins.__import__

    def no_jinja(name, *a, **kw):
        if name.startswith("jinja2"):
            raise ImportError("no jinja2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_jinja)
    from sluice.renderers.template import _make

    class Cfg:
        template = ""
    with pytest.raises(RenderError, match=r"sluice\[render\]"):
        _make(Cfg())


def test_absent_weasyprint_raises_naming_the_extra(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_weasy(name, *a, **kw):
        if name.startswith("weasyprint"):
            raise ImportError("no weasyprint")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_weasy)
    from sluice.renderers.template import _make

    class Cfg:
        template = ""
    with pytest.raises(RenderError, match=r"sluice\[render\]"):
        _make(Cfg())


def test_the_renderer_reports_when_it_writes_no_pdf(tmp_path):
    """This renderer's OWN check. cv/render.py's equivalent belongs to the subprocess
    path and does not apply here."""
    class SilentHTML(FakeHTML):
        def write_pdf(self, path, stylesheets=None):
            pass          # writes nothing

    r = TemplateRenderer(None, html_module=SilentHTML,
                         css_module=lambda string="": object())
    with pytest.raises(RenderError, match="no file"):
        r.render(CV, str(tmp_path / "out"))


def test_this_module_never_uses_importorskip():
    """The trap is documented twice and has still recurred once."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_renderer_template.py")
    with open(path, encoding="utf-8") as f:
        body = f.read()
    occurrences = [ln for ln in body.splitlines() if "importorskip" in ln]
    # This docstring-and-comment file mentions the word; only a CALL is forbidden.
    assert not [ln for ln in occurrences if "importorskip(" in ln]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_renderer_template.py -v`
Expected: all FAIL with `ModuleNotFoundError: No module named 'sluice.renderers.template'`.

- [ ] **Step 4: Implement `sluice/renderers/template.py`**

Per spec §2. Required properties:
- `TemplateRenderer(template_path, *, html_module, css_module)`; `template_path=None`/`""` resolves to the packaged default via `importlib.resources`.
- Construction raises `RenderError` (imported from `sluice.renderers.script` — one error type for the seam) when an explicitly named template is not `os.path.isfile`.
- `jinja2.Environment(autoescape=True, ...)`. **Never `select_autoescape()`** — Step 1 measured why.
- `render()` calls `parse_cv(cv_text)`, renders with `document=<CvDocument>`, writes the PDF, and raises `RenderError` if no file appeared.
- `_make(cvcfg)` imports jinja2 and weasyprint **lazily inside the function**, raising `RenderError` naming `pip install 'sluice[render]'`.
- `register("template", _make)` at module scope.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_renderer_template.py -v`
Expected: all PASS.

- [ ] **Step 6: Witness the autoescape contract**

Commit first. Change `autoescape=True` to `autoescape=False` (a MOVE of the value, not an added branch) and run `test_template_renderer_escapes_html_in_a_bullet` **by node id**. Confirm RED, and confirm no other test in the file catches it. Restore from memory.

- [ ] **Step 7: Full suite, lint, commit**

```bash
python -m pytest && ruff check sluice tests scripts
git add sluice/renderers/template.py tests/test_renderer_template.py
git commit -m "$(cat <<'EOF'
feat(cv): add the `template` renderer -- user's Jinja2 template, sluice's content

autoescape=True is a CONTRACT, not a default: select_autoescape() suffix-matches
.html and returns False for the conventional .j2, and with escaping off a
gate-verified bullet reading `<200ms` renders as an unknown element that
WeasyPrint DROPS -- so the PDF would differ from what validate() approved.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 4: Config, the default switch, and the retired `weasyprint` name

The task that flips the default and removes the old renderer. It lands after Task 3 so `template` is registered before anything selects it.

**Files:**
- Modify: `sluice/cv/config.py`, `sluice/core/plugins.py`, `sluice/renderers/template.py`, `sluice/renderers/script.py:32`, `sluice/onboard/questions.py:207`
- Delete: `sluice/renderers/weasyprint.py`
- Modify (tests): `tests/test_renderers.py`, `tests/test_plugins.py`, `tests/test_onboard_questions.py`, `tests/harness/renderer.py`
- Create: `tests/test_cv_template_config.py`

**Interfaces:**
- Consumes: the `template` registration (Task 3).
- Produces: `CvConfig.renderer == "template"`, `CvConfig.template == ""`; `plugins.register_retired(seam, name, hint)`; `load_cv_config` raising on `render_script`-without-`renderer`.

- [ ] **Step 1: Write the failing config and migration tests**

Create `tests/test_cv_template_config.py`:

```python
"""`cv.template` and the two migration refusals (spec: Config, Migration)."""
import pytest

from sluice.core import plugins
from sluice.core.app import Sluice
from sluice.cv.config import CvConfig, load_cv_config


def test_cv_template_default_is_blank():
    """`cv.template` is a `str`, so it is INVISIBLE to
    tests/test_sluice_neutral_defaults.py's list-keyed sweep -- it needs its own named
    guard, exactly as `lead_layout` does for the same reason.

    Blank is load-bearing: a non-empty default is truthy, short-circuits the resolution
    chain, and makes the packaged template unreachable while nothing goes red.
    """
    assert CvConfig().template == ""


def test_the_renderer_default_is_template():
    assert CvConfig().renderer == "template", (
        "the default renderer must be `template`: `script`'s default render_script has "
        "never existed in this repository, so no operator can be relying on it")


def test_render_script_without_an_explicit_renderer_is_refused(tmp_path, monkeypatch):
    """The ONE case that could silently change an operator's output: they set
    render_script and relied on the `script` default. Not auto-detected and not quietly
    reinterpreted -- an implicit coupling between two keys is its own quiet wrong default.
    """
    p = tmp_path / "sluice.yaml"
    p.write_text("cv:\n  render_script: ./my_render.py\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="cv.renderer: script"):
        load_cv_config(str(p))


def test_render_script_with_an_explicit_renderer_is_accepted(tmp_path, monkeypatch):
    """The refusal must be reachable ONLY by the ambiguous case -- otherwise it is a
    guard that refuses everything and proves nothing."""
    p = tmp_path / "sluice.yaml"
    p.write_text("cv:\n  renderer: script\n  render_script: ./my_render.py\n",
                 encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    cfg = load_cv_config(str(p))
    assert cfg.renderer == "script" and cfg.render_script == "./my_render.py"


def test_selecting_the_retired_weasyprint_name_names_template():
    """A BARE registry removal cannot produce this message: `plugins.get`'s unknown-name
    error lists the VALID names and would never mention `template`, so "raises, naming
    template as the replacement" would be an empty promise. The retired name needs a
    deliberate branch."""
    cfg = CvConfig()
    cfg.renderer = "weasyprint"
    with pytest.raises(plugins.UnknownAdapter) as e:
        Sluice(None).renderer(cfg)
    assert "template" in str(e.value), "the migration message does not name the replacement"


def test_a_retired_name_is_not_offered_as_a_choice():
    """`sluice init` derives its renderer choices FROM the registry. A retired name that
    stayed registered would keep being offered -- so retirement must not be implemented
    as a factory that raises."""
    assert "weasyprint" not in Sluice.available("renderer")
    assert "template" in Sluice.available("renderer")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cv_template_config.py -v`
Expected: all FAIL — `template` attribute missing, default still `script`, no refusal, no retired branch.

- [ ] **Step 3: Add the retired-name registry to `sluice/core/plugins.py`**

Generic, small, and mirrors `register`. It must **not** appear in `available()`.

```python
# seam -> {retired name -> migration hint}. A retired name is NOT in the registry, so it
# is never offered as a choice (`sluice init` derives its choices from `available`) --
# but selecting one must say what replaced it. A bare removal cannot: UnknownAdapter
# lists the VALID names and would never mention the replacement.
_RETIRED: dict[str, dict[str, str]] = {}


def register_retired(seam: str, name: str, hint: str) -> None:
    """Record that `name` was removed from `seam`, and what to use instead."""
    _RETIRED.setdefault(seam, {})[name] = hint
```

and in `get`, before raising:

```python
def get(seam: str, name: str):
    impls = _REGISTRY.get(seam, {})
    if name not in impls:
        raise UnknownAdapter(seam, name, impls, hint=_RETIRED.get(seam, {}).get(name, ""))
    return impls[name]
```

- [ ] **Step 4: Declare the retirement in `sluice/renderers/template.py`**

Beside the registration — the replacement declares what it replaces, so the two cannot drift.

```python
from sluice.core import plugins

register("template", _make)
# `weasyprint` was a <pre>-dumping renderer that ignored the CV's structure entirely.
# `template` supersedes it: same WeasyPrint backend, but the composed CV is parsed and
# laid out by the user's own Jinja2 template. Retired rather than silently dropped so a
# config naming it says what to do instead.
plugins.register_retired(
    "renderer", "weasyprint",
    "The bundled `weasyprint` renderer has been replaced by `template`, which renders "
    "your own Jinja2 template. Set cv.renderer: template (and optionally cv.template: "
    "/path/to/your.html.j2; blank uses the packaged default).")
```

- [ ] **Step 5: Update `sluice/cv/config.py`**

- Add `template: str = ""` with a comment stating why blank is load-bearing (spec §Config) and that it is **not** routed through `paths.resolve()` — like `render_script` it names a workspace artefact, one of the deliberate cwd-relative exceptions.
- Change `renderer: str = "script"` → `"template"` and rewrite the stale comment at `:56`.
- In `load_cv_config`, beside the existing `baseline_rel` raise, add the migration refusal:

```python
    # A user who set render_script and NOTHING else was relying on the `script` default,
    # which this release changes to `template`. That is the one case where the new
    # default could silently change an operator's output, so refuse rather than guess --
    # inferring `renderer: script` from the presence of render_script would be an
    # implicit coupling between two keys, which is its own quiet wrong default.
    if "render_script" in data and "renderer" not in data:
        raise ValueError(
            "cv.render_script is set but cv.renderer is not, and the default renderer is "
            "now `template` (it was `script`). Add `cv.renderer: script` to keep using "
            "your render script, or drop cv.render_script to use a Jinja2 template.")
```

- [ ] **Step 6: Delete `sluice/renderers/weasyprint.py` and repair the four test sites**

```bash
git rm sluice/renderers/weasyprint.py
```

Then, in the tests that referenced it:
- `tests/test_renderers.py:44` and `tests/test_plugins.py:30` — replace `weasyprint` with `template` in the seam-membership set. **Keep the line** (spec §Testing) — do not delete it.
- `tests/test_renderers.py:33` — the `script` construction error no longer says `cv.renderer: weasyprint`; assert the new wording from Step 7.
- `tests/test_renderers.py:60-88` (`test_weasyprint_renderer_strips_citations_before_writing`) — **delete it**; its guarantee is now carried by `test_template_renderer_strips_citations_before_writing` in Task 3, which is strictly stronger (parse_cv strips, so no template can reintroduce a citation). Say so in the commit message.
- `tests/test_renderers.py:46-48` — the `cfg.renderer == "script"` assertion and its "switching it would silently change the layout" message are now false; that assertion moves to `tests/test_cv_template_config.py::test_the_renderer_default_is_template`.
- `tests/test_onboard_questions.py:71-72` — `parse_choice("script", "weasyprint")` → `parse_choice("script", "template")`.
- `tests/harness/renderer.py:49` — update the docstring's example set.

- [ ] **Step 7: Update the two in-`sluice/` user-facing messages**

- `sluice/renderers/script.py:29-34` — the construction error must now offer `template`, not `weasyprint`:

```python
            raise RenderError(
                f"renderer 'script': render_script is not a file: '{script}'. "
                f"Set cv.render_script to your WeasyPrint script, or switch to the "
                f"bundled renderer with cv.renderer: template "
                f"(pip install 'sluice[render]')."
            )
```

- `sluice/onboard/questions.py:207` — the hint (the choices self-heal from the registry; the hint does not):

```python
                 hint="template renders your own Jinja2 template: pip install 'sluice[render]'."),
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cv_template_config.py tests/test_renderers.py tests/test_plugins.py tests/test_onboard_questions.py -v`
Expected: all PASS.

- [ ] **Step 9: Confirm no orphaned reference survives**

```bash
grep -rn "weasyprint" --include='*.py' sluice tests | grep -v "sluice\[render\]" | grep -v "^tests/test_renderer_template.py"
```
Expected: only genuine references to the *library* (the lazy import in `template.py`, and the `render` extra text) — no reference to a `weasyprint` *renderer name*. Investigate every remaining line; do not accept a count from this plan.

- [ ] **Step 10: Witness the migration branch**

Commit first. Delete the `plugins.register_retired(...)` call in `template.py` and run `test_selecting_the_retired_weasyprint_name_names_template` **by node id**. Confirm RED (the error lists valid names but never says `template`). Restore from memory.

- [ ] **Step 11: Full suite, lint, commit**

```bash
python -m pytest && ruff check sluice tests scripts
git add -A
git commit -m "$(cat <<'EOF'
feat(cv)!: default the renderer to `template` and retire `weasyprint`

BREAKING CHANGE: cv.renderer defaults to `template` (was `script`), and the
bundled `weasyprint` renderer is removed. `script`'s default render_script has
never existed in this repository, so no operator can have been relying on it;
the one case that could silently change an operator's output -- render_script
set with no explicit renderer -- now RAISES and names the fix. Selecting
`weasyprint` raises naming `template`, via a retired-name branch: a bare
registry removal lists only the valid names and would never mention the
replacement.

test_weasyprint_renderer_strips_citations_before_writing is deleted, not lost:
parse_cv now strips citations, so no template can reintroduce one, and
test_template_renderer_strips_citations_before_writing pins that.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 5: Wire the parse failure into the existing retry

**Files:**
- Modify: `sluice/cv/engine.py` (the `for _ in range(2)` loop, ~line 120-146)
- Test: `tests/test_cv_engine.py`

**Interfaces:**
- Consumes: `parse_cv`, `CvParseError` (Task 2).
- Produces: no signature change. A `CvParseError` on the first attempt appends to `gate_msgs`; still failing after the retry yields `CvResult(..., "skipped-gate")`.

Implement per spec §0. The parse runs **inside** the loop, **immediately after** `validate`, and its failure is appended to `gate_msgs` in the same shape a gate violation is.

- [ ] **Step 1: Write the failing engine test**

Add to `tests/test_cv_engine.py` (match the file's existing fixture/harness style — read its neighbours first):

```python
# An UNPARSEABLE meta line that still PASSES the fabrication gate -- the whole point of
# this wiring. validate() reads only `\d{2}/(\d{4})\s*[--]` after WORK EXPERIENCE, so
# dropping the pipes leaves the years (and every citation) intact and the gate clean.
UNPARSEABLE_CV = CLEAN_CV.replace("02/2023–present | Alfa | Staff Engineer",
                                  "02/2023–present Alfa Staff Engineer")


def test_the_unparseable_fixture_still_passes_the_gate():
    """A PREMISE of both tests below: they claim the engine catches a formatting failure
    the GATE does not. If this fixture ever stops clearing the gate they would pass for
    the wrong reason -- the same trap test_clean_cv_is_actually_clean exists to close."""
    assert "Alfa Staff Engineer" in UNPARSEABLE_CV, "the replace no-opped"
    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
    assert validate(UNPARSEABLE_CV, bundle_text) == []


def test_a_parse_failure_feeds_the_retry_not_the_bin():
    """A CV whose role line wobbles must be RE-COMPOSED, not thrown away.

    The engine already composes up to twice, appending violations to the second prompt.
    Making a parse failure fatal would kill the lead AFTER the LLM spend with no
    recovery -- worse than the status quo, and it re-opens the exact problem this design
    exists to close. The model is being asked to fix its own formatting, which is the
    thing an LLM is reliably good at.
    """
    class TwoShotBackend:
        """First compose returns the unparseable CV; the second returns a clean one."""
        def __init__(self):
            self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            # Mirrors FakeBackend's routing: compose prompts carry "SOURCE BUNDLE"
            # and not "auditing"; audit prompts carry both.
            if not ("SOURCE BUNDLE" in prompt and "auditing" not in prompt):
                return "supported\tx\tSF1"
            self.prompts.append(prompt)
            return UNPARSEABLE_CV if len(self.prompts) == 1 else CLEAN_CV

    be = TwoShotBackend()
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "rendered", "a parse failure binned the lead instead of retrying it"
    assert len(be.prompts) == 2, "the parse failure did not reach the existing retry"
    assert "FORMAT" in be.prompts[1], "the parse error never reached the retry prompt"
    assert rend.rendered == [CLEAN_CV], "the renderer got the unparseable CV"


def test_a_parse_failure_that_survives_the_retry_skips_the_lead():
    """Same outcome as a lead that cannot clear the gate, and the renderer is never
    reached -- a half-parsed CV must never become a PDF sent under the user's name."""
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    be = FakeBackend(UNPARSEABLE_CV)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("FORMAT" in x for x in r.violations)
    assert rend.rendered == [], "an unparseable CV reached the renderer"
    assert v.written == {}
```

**A sixth correction to the spec, which these tests settle.** Spec §0 and the §Failure modes table say the lead is "skipped with an `error` … via per-lead isolation (`engine.py:250-264`)" — but they *also* say the `CvParseError` is "appended to `gate_msgs` in the same shape a gate violation is", and those two cannot both hold: appending to `gate_msgs` returns `skipped-gate` from `run_one` and never raises, so `run_batch`'s `except Exception` arm is never reached. **`skipped-gate` is the right outcome and the tests above pin it**: it reports the reason in `r.violations` where the user can see it, and it works for the single-lead `run_one` path too — `error` via `run_batch`'s isolation would only exist in batch runs, leaving `sluice cv run --lead X` to raise a traceback.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cv_engine.py -k parse -v`
Expected: FAIL — parse is not wired in, so an unparseable CV renders instead of retrying.

- [ ] **Step 3: Wire it in**

In `run_one`'s loop, immediately after the `validate` call and the two STRUCTURAL guards, before `_slop`:

```python
            # Parse INSIDE the retry loop, in the same shape a gate violation takes. A
            # CvParseError raised at RENDER instead would arrive after the LLM spend with
            # no recovery, because this loop is the only retry there is -- and it closes
            # before render. The renderer parses again for its own use; parse_cv is pure,
            # so the two callers cannot disagree.
            try:
                _parse_cv(cv_text)
            except _CvParseError as e:
                violations = violations + [f"FORMAT: {e}"]
```

Import lazily at the top of the function alongside the other cv imports, matching the file's existing style.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_cv_engine.py -v`
Expected: all PASS.

- [ ] **Step 5: Witness it**

Commit first. Delete the `try/except` block and run both new tests **by node id**; confirm RED and confirm no pre-existing engine test catches the mutant. Restore from memory.

- [ ] **Step 6: Full suite, lint, commit**

```bash
python -m pytest && ruff check sluice tests scripts
git add sluice/cv/engine.py tests/test_cv_engine.py
git commit -m "$(cat <<'EOF'
fix(cv): feed a CV parse failure to the existing retry instead of the bin

parse_cv runs inside the compose/validate loop, so a wobbly role line is
re-composed with the error appended to the prompt. Raising at render instead
would kill the lead after the LLM spend with no recovery: this loop is the only
retry there is, and it closes before render.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 6: Documentation, the worked example, and the neutrality sweep

Every site below asserts something this change makes false. Left alone they become a trail of stale claims, and one of them is read by every future agent.

**Files:**
- Create: `docs/cv-template-example.html.j2`
- Modify: `sluice.yaml.example`, `docs/ARCHITECTURE.md:830-835`, `README.md:63`, `.rulesync/rules/CLAUDE.md:24,357-358,362,405`
- Modify: whichever test carries the neutrality sweep, to extend it to `docs/**/*.j2`

- [ ] **Step 1: Write the worked example**

`docs/cv-template-example.html.j2`, per spec §3. **ZERO sample values: expressions and CSS only.** A worked example asked for "real CSS" is exactly the pressure that produces a filled-in specimen with a plausible name and employer — and `docs/` is the same public repo, while every existing neutrality guard is scoped to `sluice/` and `tests/`. Where a value is unavoidable for the CSS to make sense, use the `Example …`/`example.invalid` family.

State in a leading comment that it is single-column on purpose and that a two-column grid or table defeats ATS text extraction (spec §Known limitations).

- [ ] **Step 2: Extend the neutrality sweep to `docs/**/*.j2`**

Find the sweep first — do not assume its location:

```bash
grep -rln "neutrality\|personal data\|no_personal" tests/ | head
```

Extend its file set, and **assert on the SCOPE**: pin that the sweep enumerated the `.j2` files it meant to look at. A discovery loop whose matcher is broken yields an empty set that satisfies every assertion over it, and for a negative guard finding nothing is the success case.

- [ ] **Step 3: Update `sluice.yaml.example`**

Rewrite the renderer block at `:143-153`. Both `cv.renderer` and `cv.template` must appear (hard rule 13). Keep the file's convention: keys COMMENTED, so an unanswered `sluice init` still renders a config field-for-field equal to no config at all.

- [ ] **Step 4: Update `docs/ARCHITECTURE.md:830-835`**

The renderer seam now has `script` and `template`. Note the retired `weasyprint` name and that selecting it raises.

- [ ] **Step 5: Update `README.md`**

Line ~63 ("an external WeasyPrint render script you supply") is now the fallback, not the norm. Add the render prerequisites: `pip install 'sluice[render]'`, the WeasyPrint system libraries (cairo, pango, gdk-pixbuf), and the macOS loader path — **measured**: with the Homebrew libraries installed the import still failed until `DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"` was set.

- [ ] **Step 6: Update `.rulesync/rules/CLAUDE.md` — FOUR sites, not two**

`.rulesync/` is the canonical source and is yours to edit directly. It is also the highest-leverage place to assert something false, since every future agent reads it.

- `:24` — the install comment enumerates "pytest + pytest-cov + faker"; jinja2, setuptools and build are now in `[test]`.
- `:357-358` — the stdlib-only exception list names `renderers/weasyprint.py`, **a file this change deletes**, and omits `jinja2`.
- `:362` — enumerates the `test` extra as "(`pytest`, `faker`, `pytest-cov`)".
- `:405` — the renderer seam's "two self-registering production impls".

Assert nothing untrue and add no module mechanics — that is `docs/ARCHITECTURE.md`'s job, a contract this file states about itself.

- [ ] **Step 7: Regenerate and verify no drift**

```bash
npm ci --ignore-scripts && npm run rulesync
diff <(tail -n +9 .rulesync/rules/CLAUDE.md) CLAUDE.md && echo "no drift"
git status --short          # generated outputs are gitignored; expect no new tracked files
```

- [ ] **Step 8: Full suite, lint, commit**

```bash
python -m pytest && ruff check sluice tests scripts
git add -A
git commit -m "$(cat <<'EOF'
docs: retire the weasyprint renderer from every site that named it

Seven user-facing sites told a user to set `cv.renderer: weasyprint`; all but
CHANGELOG.md:124 are updated in place. That line is left alone deliberately: a
changelog records what was true at v0.1.0, and the migration note belongs in the
new release-please entry rather than in a rewritten history.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Definition of Done

Every line below is a command whose output is the evidence. Do not mark an item done from reasoning.

**Correctness**
- [ ] `python -m pytest` — green, and the count is **above** the 2122 recorded at plan time.
- [ ] `ruff check sluice tests scripts` — clean.
- [ ] All 18 tests named in spec §Testing exist and pass. Check by name, not by count:
      `python -m pytest --collect-only -q | grep -cE "test_parse_|test_template_|test_the_shipped_template|test_missing_template|test_absent_|test_a_parse_failure|test_selecting_the_retired|test_render_script_without|test_cv_template_default"`
- [ ] Every new/changed guard was witnessed RED **by node id**, and for each one you confirmed no pre-existing test in the same file catches the mutant.

**Packaging (the item three reviewers said would silently not work)**
- [ ] A built wheel contains `sluice/templates/cv_plain.html.j2`:
      `python -m build --wheel --no-isolation --outdir /tmp/w && python -c "import zipfile,glob;print([n for n in zipfile.ZipFile(glob.glob('/tmp/w/*.whl')[0]).namelist() if n.endswith('.j2')])"`
- [ ] Deleting the `package-data` table turns `test_the_shipped_template_is_in_the_built_wheel` RED — witnessed, then restored.
- [ ] `git status --porcelain` is empty after a wheel build (`build/`, `dist/` gitignored).

**No stale claims**
- [ ] `grep -rn "cv.renderer: weasyprint" . --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git` returns **only** `CHANGELOG.md:124` (historical, deliberately unchanged) and the spec/plan under `docs/superpowers/`.
- [ ] `grep -rn "weasyprint" sluice/ tests/` shows no reference to a *renderer name* — only the library import and the extra.
- [ ] `npm run rulesync` leaves a clean tree; `diff <(tail -n +9 .rulesync/rules/CLAUDE.md) CLAUDE.md` is empty.
- [ ] No `importorskip(` in any new test module.

**The blocker actually closes**
- [ ] A real PDF renders end-to-end from composed CV text through `parse_cv` → `TemplateRenderer` → WeasyPrint, using the packaged default template:
      `DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib" python -c "<parse a sample CV, render it, assert the PDF is non-empty and starts with %PDF>"`
      This is the deliverable — everything else is scaffolding around it.
- [ ] Open the PDF and look at it. A template that renders without raising can still be visibly wrong, and no assertion in this plan catches that.

**Scope**
- [ ] `git diff main --stat` touches nothing in `cv/validate.py`, `cv/compose.py`'s `_RULES`, or `renderers/script.py` beyond its one error message (spec §Out of scope).

**Then, and only then**
- [ ] `/review-pr` **before** pushing — per the standing cadence. CodeRabbit is the scarce resource; the specialist team is free and parallel.

---

## Notes for the implementer

- **Do not defer anything.** If you find a problem, address it in this branch. A deferred-minor backlog is where fail-open guards accumulate, because each looks too small for its own round. "Address" may mean recording the reasoning in a comment; it never means leaving it unmentioned.
- **The spec is not infallible and neither is this plan.** Four corrections to the spec are listed above and were all found by grepping rather than by reading. If a count in either document does not match what you measure, the measurement wins — say so.
- **A guard that discovers nothing passes.** `all([])` is `True`. Every sweep in this plan must assert on its SCOPE as well as its findings.
