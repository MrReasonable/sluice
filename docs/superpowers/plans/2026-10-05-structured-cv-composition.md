# Structured CV Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-text CV composition with structured composition — the model returns JSON content (profile, cited bullets per role slot, skills picked from a closed list) and sluice assembles the CV document from the vault — fixing #364, #365 and #368 in one PR released as 4.0.0.

**Architecture:** New pure modules carry the design: `core/tokens.py` (the one tokeniser and term matcher), `core/layout.py` (CV Layout parsing, employer matching, the slot table), `cv/reply.py` (extract and shape-check the JSON reply), `cv/selection.py` (skills pool and budgets), `cv/document.py` (assemble the `CvDocument`, write its text forms). They are built and tested ALONGSIDE the text pipeline first, with the structured run loop and doctor's new rows, so every commit stays green; then one switch task moves `cv run` onto them, and the next deletes the text machinery (`cv/parse.py`, `section_spans`, the envelope unwrap, the #99 guards, `precheck`). Config, the neutrality guards, docs and `.rulesync/` follow.

**Tech Stack:** Python 3.12–3.14, standard library plus the declared `pyyaml` dependency; pytest with seeded faker; ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-10-05-structured-cv-composition-design.md` (commit `74d98e5a`). Every task argues from it; read the section a task names before starting it.

## Global Constraints

- Work in this branch's own worktree, branch `feat/structured-cv-composition`, and run every command from its root. Run Python only as `.venv/bin/python` (the venv is uv-made and has no pip: install with `uv pip install -p .venv/bin/python <pkg>`). Never `cd` into the main checkout.
- `sluice/` stays standard-library only; the one exception this plan uses is `yaml`, imported behind the existing guarded import in `sluice/core/vault.py`.
- **No personal data** in `sluice/`, `tests/`, `docs/` or any commit message or PR text: no real employer names, locations, contact details, hostnames or absolute paths. Fixtures use `Example …` names from the reviewed rosters, `tests/conftest.py`'s seeded faker pools (`titles`, `LOCATIONS`), and synthetic shapes (`Examplelang`/`Examplelangscript`, `SYNTHETIC-…` tokens) — never real technology names in tool, decoy or tokeniser rows (owner's ruling recorded in `tests/test_fixture_name_neutrality.py::_REVIEWED_SKILL_VALUES`).
- **Every commit is green:** `.venv/bin/python -m pytest -q` passes and `.venv/bin/ruff check sluice tests scripts` is clean (install ruff once: `uv pip install -p .venv/bin/python ruff==0.15.21`).
- **A red test the task does not name is part of the task.** Each whole-suite step can surface one (plan review measured several, now named in their tasks; more may exist, since a "PASS" here is a prediction until the step runs). Fix it within the task under the ledger rules: port it to the new input keeping its assertion, or delete it only when its subject is gone, with a ledger row; and name it in the commit body. Never loosen an assertion, exclude a member by name, or widen an allow-list to get green. A fix that needs a design decision this plan does not make is a STOP: ask the owner.
- **Conventional Commits** (`feat(cv): …`, `test(cv): …`, `docs: …`, `refactor(core): …`). Exactly TWO commits carry `!`, each because it breaks an existing install: Task 18's `feat(cv)!:` (composition) and Task 20's `feat(config)!:` (two config keys that loaded now stop every command — a breaking CONFIG change, which `CHANGELOG.md` ranks above an API one). No other: an internal seam change is not breaking (CLAUDE.md, "A `!` is a claim about the USER'S INSTALL"). Never put the breaking-change trailer token at the start of a line in a commit body. End EVERY commit message with this line:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Never cite a line number** in a comment or docstring (#191): cite `file.py::symbol`.
- Comments explain WHY (the invariant, the bug prevented, the trade-off) at the density of the surrounding code.
- **"Add to <existing file>"** means: merge any import lines the block shows into that file's existing imports at the top (dropping one the file already has), and put the rest after the file's last test unless the step says where. A block's own imports are shown so the names it uses are explicit, not so they land mid-file.
- `.rulesync/` is canonical; `CLAUDE.md`, `AGENTS.md` and `.claude/` are generated. Edit `.rulesync/rules/CLAUDE.md` and regenerate with `npm ci --ignore-scripts && npm run rulesync`.
- **Mutation witnesses** (Task 24) run after `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`, mutate by DELETING or by SWAPPING the old behaviour in at one site — never by adding beside — and commit before every witness so a witness can never leave the tree mutated.
- **Real-vault data never enters the repo** (spec §11). Task 25's measurement runs from a cwd outside every worktree.
- Spec deviations made while planning, each for a reason the spec could not see:
  - `SECTION_HEADINGS` lives in `core/protocols.py` (spec §7.1 says `cv/document.py`), because `core/layout.py` must refuse a layout heading equal to one and `core/` may not import a sub-app. `cv/document.py` re-exports it, so there is still one home.
  - `cv/selection.py` is a sixth new module (the spec folds selection into the engine flow without naming a home); it keeps the selection rules pure and testable apart from the engine.
  - Decoy refusals at load identify a decoy by its POSITION in `cv.fabrication_decoys`, never by echoing its value — the repo's never-echo rule for personal config values (`core/config.py::refuse_retired_locations`). Spec §9.3 says "naming the decoy"; position names it without echoing it.
  - Doctor rows report COUNTS and the command that lists the entries (`job-sluice experience list`), never entry titles or decoy values — the repo's "no doctor row carries user-authored text" rule (`sluice/evidence/commands.py`'s list docstring).
  - The structured functions keep the names they were built under beside the text pipeline: `cv/compose.py::build_structured_prompt` and `compose_structured` (the spec's `build_prompt` and `compose`), `cv/bundle.py::render_structured_bundle` (the composer's corpus, which the spec describes as `render_composer_bundle` minus the baseline, plus `Tools:`), `render_audit_bundle` (the auditor's, `render_bundle` with `Tools:`) and `term_vocabulary(bundle, layout)` (the rebuilt `mention_vocab`). Both pipelines must coexist until Task 18, so these are new functions rather than edits, and renaming them back after Task 19 would churn every test and doc that names them for no change in behaviour. `run_one_structured` is the one exception: Task 18 renames it `run_one`, the name `core/app.py` and the tests call.
  - `SLOP EM-DASH` and `DOUBLE-HYPHEN-DASH` (spec §6.1's table) stay in `cv/slop.py::check_hard`, which the engine runs over the model's own texts BESIDE `check_selection` rather than inside it. The scope is the spec's -- the profile and the kept bullets, never vault text -- and `check_hard` keeps its own tests and its `slop` result field.
  - The fixture-name sweep's `location` and `title` collectors (spec §12.2) read the structured-CV positions only -- `Role(...)`, `LayoutRole(...)`, layout YAML and layout-shaped dicts -- not every `location=`/`title=` keyword in `tests/`: measured, an unscoped `location=` collector reaches existing fixtures that are not CV text.
  - `Label` is filled from the typed name in `Sluice.add_evidence` (spec §4.4 says `cmd_evidence_add`): the CLI, the `init` wizard and the MCP proposal tool all reach the store through that one facade, so the name survives the slug on every route, and the rule has one home.
  - Spec §12.2 asks the docs-sample sweep to match each list's item count to its prose. Prose states no count a test can read, so the sweep checks the shape that rule exists for: each education item is one qualification (exactly one ` | `), and each placeholder is refused at its own path.

## Review Focus

The five inputs the spec implies but no spec-listed row exercises, most likely to bite first. Each has its test in the owning task.

1. **A model that writes slot ids in another case** (`"r1"` for `R1`) — a reasonable person expects it to work. Pinned in Task 8 (`test_slot_ids_match_case_insensitively`).
2. **A `Company:` written as an Obsidian list property, or with a non-breaking space** — both must match a role. Pinned in Task 5 (`test_a_block_list_company_and_an_nbsp_still_match`).
3. **A JSON-looking object in the model's chat BEFORE the real reply** (it echoes a fragment of the job ad) — the real reply must still win. Pinned in Task 8 (`test_a_json_object_in_chat_before_the_reply_does_not_win`).
4. **A skill pick with a trailing full stop or stray whitespace** (`"Example Query."`) — it must keep, in the pool's spelling. Pinned in Task 9 (`test_a_pick_with_a_trailing_period_still_keeps`).
5. **A roll-up matched through one part of a multi-employer company** (`Company: Example Beta / Example Meridian` under a roll-up listing only `Example Meridian`) — the entry must be citable there. Pinned in Task 5 (`test_a_multi_employer_company_matches_through_one_part`).

---

## File Map

**New modules**
- `sluice/core/tokens.py` — `WORD_RE`, `TOKEN_RULE_RE`, `tokens`, `segments`, `find_term`, `figures`, `tool_items`, `decoy_problem`, `validate_decoys`. The one tokeniser/matcher the gate and `doctor` share.
- `sluice/core/layout.py` — `parse_layout`, `fold_employer`, `employers_of`, `Placement`, `place`, `Slot`, `build_slots`, `no_citable_slot`, `placement_counts`, `layout_text`.
- `sluice/cv/reply.py` — `Bullet`, `Reply`, `extract_json`, `parse_reply`.
- `sluice/cv/selection.py` — `Selection`, `cv_name`, `build_pool`, `select`, `zero_bullet_findings`.
- `sluice/cv/document.py` — `AssembledCv`, `format_dates`, `assemble`, `to_text`, `audit_text`, `model_lines`; re-exports `SECTION_HEADINGS`.

**Contract additions** — `sluice/core/protocols.py`: `CV_LAYOUT_RELPATH`, `SECTION_HEADINGS`, `Role`, `CvDocument` (moved from `cv/parse.py`), `LayoutError`, `LayoutRole`, `CvLayout`, `Store.read_cv_layout`, `Renderer.render(document, …)`, `EvidenceKind.legacy_fields`, `EvidenceKind.names_in_skills_pool`.

**Rewritten** — `sluice/cv/engine.py` (run loop), `sluice/cv/validate.py` (checks over a selection), `sluice/cv/compose.py` (structured prompt), `sluice/cv/bundle.py` (structured, audit and vocabulary renderings).

**Deleted** — `sluice/cv/parse.py`.

**Touched** — `sluice/core/vault.py`, `sluice/stores/vault.py`, `sluice/core/doctor.py`, `sluice/core/app.py`, `sluice/core/config.py`, `sluice/cv/config.py`, `sluice/cv/artefacts.py`, `sluice/cv/slop.py`, `sluice/renderers/template.py`, `sluice/renderers/script.py`, `sluice/evidence/commands.py`, `sluice/onboard/questions.py`, `sluice/mcpserver.py`, `sluice/cli.py`, `sluice/templates/cv_plain.html.j2`, `scripts/smoke_installed.py`, `sluice.yaml.example`, the docs in spec §14, `.rulesync/`.

**Tests** — new files `tests/test_core_tokens.py`, `tests/test_core_layout.py`, `tests/test_core_layout_slots.py`, `tests/test_cv_reply.py`, `tests/test_cv_selection.py`, `tests/test_cv_document.py`, `tests/test_cv_script_golden.py`, `tests/test_cv_checks.py`, `tests/test_cv_structured_prompt.py`, `tests/test_cv_structured_bundle.py`, `tests/test_sandbox_guard.py`, `tests/test_cv_structured_engine.py`, `tests/test_doctor_cv_layout.py`, `tests/test_skills_pool_wording.py`, `tests/test_cv_attribution_vaults.py`, `tests/test_config_retired_cv_keys.py`, `tests/test_docs_layout_samples.py`, and the helper `tests/structured_cv.py`; plus the ports Tasks 18 and 19 list. The test ledger, `docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md`, records every deleted or changed test.

## Task order and why

Tasks 1–17 are ADDITIVE: each new module, the structured run loop and doctor's new classifiers land beside the text pipeline with their own tests, so the suite stays green. Task 18 is the switch: `cv run` composes structured, and every surface reports it. Task 19 deletes the dead text pipeline, its tests and the guards built on it (retargeted, never dropped). Tasks 20–23 move config, the neutrality guards, the docs and `.rulesync/`. Tasks 24–26 prove it, measure it and ship it.

| # | Task | Depends on |
|---|---|---|
| 1 | Sandbox guard; the two leaking tests | — |
| 2 | Capture the `script` golden (pre-change) | — |
| 3 | `core/tokens.py` | — |
| 4 | Contract types; `parse_layout` | 3 |
| 5 | Employer matching; the slot table | 4 |
| 6 | `Store.read_cv_layout` | 4 |
| 7 | `EvidenceKind` flags; `Label:`; legacy fields | — |
| 8 | `cv/reply.py` | 4 |
| 9 | `cv/selection.py` | 3, 5, 8 |
| 10 | `cv/document.py` | 2, 4, 9 |
| 11 | Hard checks over a selection | 3, 5, 9 |
| 12 | Structured prompt | 5, 8–11 |
| 13 | Structured bundle renderings and entry facts | 3, 5, 11 |
| 14 | Renderer seam takes a `CvDocument` | 10 |
| 15 | Artefact names and run-record keys | — |
| 16 | The structured run loop, beside `run_one` | 3–15 |
| 17 | Doctor's rows for the new model (pure; not wired) | 3–5 |
| 18 | THE SWITCH: `cv run` composes structured | 1–17 |
| 19 | Remove the text CV pipeline | 18 |
| 20 | Config, setup and the store | 19 |
| 21 | Neutrality guards reach the new fixture positions | 20 |
| 22 | Docs, and the shipped templates' comments | 18–21 |
| 23 | `.rulesync/` | 18–22 |
| 24 | Ledger, witnesses, full verification | 18–23 |
| 25 | The owner's migration and real composes | 24 |
| 26 | Pre-push review, PR | 25 |

---

### Task 1: Sandbox guard, and the tests that write into the working directory

Spec §12.1 ("Sandbox"). Lands first so every later task is checked by it.

**Files:**
- Modify: `tests/conftest.py` (append the guard below `_forbid_dns`)
- Create: `tests/test_sandbox_guard.py`
- Modify: `tests/test_cv_backend_failure.py` (the two single-lead tests)

**Interfaces:**
- Produces (in `tests/conftest.py`): `_relative_path_defaults() -> frozenset[str]`, `_WATCHED: list[str]` (absolute paths, mutable so a control test can extend it), `_VIOLATIONS: list[tuple[str, str, str]]` (nodeid, event, path), `_guard_audit(event, args)`, the autouse fixtures `_sandbox_guard` (function) and `_sandbox_session_check` (session).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sandbox_guard.py`:

```python
"""The sandbox guard in tests/conftest.py must exist and must see what it claims to.

Lives outside conftest.py because pytest does not collect conftest.py: an assertion there
would itself be inert (the same reason tests/test_hermeticity.py exists for the DNS guard).
"""
import os

import tests.conftest as guard


def test_the_watched_set_is_exactly_the_relative_path_defaults_in_sluice():
    # A scope pin, not a violation check: for a NEGATIVE guard, finding nothing is the
    # success case, so the guard must prove it enumerated what it meant to watch. A walk
    # that matched nothing would otherwise pass every test forever.
    assert guard._relative_path_defaults() == frozenset({
        "./cv-home", "./cv-host", "./cv-output", "./cv-served",
        "./scripts/cv_render_v2.py", "./vault",
    })


def test_a_container_side_path_is_excluded_by_name():
    # `apply.camofox_cv_dir` names a path inside the browser container, not on this host.
    assert "./cv-uploads" not in guard._relative_path_defaults()


def test_the_guard_records_a_write_under_a_watched_path(tmp_path, request):
    # The positive control: without it a guard whose hook never fires reads as a clean
    # suite.
    watched = str(tmp_path / "cv-output")
    guard._WATCHED.append(watched)
    try:
        os.makedirs(watched)
        with open(os.path.join(watched, "x.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        mine = [v for v in guard._VIOLATIONS if v[0] == request.node.nodeid]
        assert {v[1] for v in mine} >= {"os.mkdir", "open"}, mine
    finally:
        guard._WATCHED.remove(watched)
        # Clear this control's own records so the guard does not fail the control itself.
        guard._VIOLATIONS[:] = [v for v in guard._VIOLATIONS
                                if v[0] != request.node.nodeid]


def test_a_read_is_never_recorded(tmp_path, request):
    watched = str(tmp_path / "cv-served")
    os.makedirs(watched)
    with open(os.path.join(watched, "x.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    guard._WATCHED.append(watched)
    try:
        with open(os.path.join(watched, "x.txt"), encoding="utf-8") as fh:
            fh.read()
        assert not [v for v in guard._VIOLATIONS if v[0] == request.node.nodeid]
    finally:
        guard._WATCHED.remove(watched)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sandbox_guard.py -q`
Expected: FAIL with `AttributeError: module 'tests.conftest' has no attribute '_relative_path_defaults'`.

- [ ] **Step 3: Implement the guard**

Append to `tests/conftest.py`:

```python
# --- Sandbox guard (#364/#365/#368 spec §12.1) -----------------------------------------
# Every `./…` path default in sluice/ resolves against the CURRENT DIRECTORY, so a test that
# reaches one without chdir-ing into tmp_path writes into whatever directory pytest was
# started from -- measured: two single-lead tests in test_cv_backend_failure.py wrote a
# prompt and a run.json into the worktree's own cv-output/. The DNS guard above cannot see
# a filesystem write, and the HOME/XDG sandbox in `_pin_paths` does not cover a path that
# is relative by design.
#
# An in-process audit hook rather than a before/after snapshot: a snapshot cannot tell
# this process's writes from a concurrent real `cv run`, an editor or Finder's .DS_Store,
# and would redden whichever innocent test was running; the hook sees only this process.
# It RECORDS rather than raises, because sluice/ has `except BaseException` arms that would
# swallow a raise, and fails the offending test at teardown -- naming the test.
import ast as _ast
import os as _os
import sys as _sys

_SESSION_CWD = _os.getcwd()
# `apply.camofox_cv_dir` names a path INSIDE the browser container, not on this host.
_CONTAINER_PATHS = frozenset({"./cv-uploads"})


def _relative_path_defaults():
    """Every `"./…"` string constant in sluice/, bar core/paths.py (which RESOLVES paths
    rather than naming one) and container-side paths. Derived by AST walk, never
    hand-listed, so a new cwd-relative default is watched the day it lands; pinned by
    tests/test_sandbox_guard.py."""
    root = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "sluice")
    found = set()
    for dirpath, _dirs, files in _os.walk(root):
        for name in files:
            path = _os.path.join(dirpath, name)
            if not name.endswith(".py") or path.endswith(_os.path.join("core", "paths.py")):
                continue
            with open(path, encoding="utf-8") as fh:
                tree = _ast.parse(fh.read(), path)
            for node in _ast.walk(tree):
                if (isinstance(node, _ast.Constant) and isinstance(node.value, str)
                        and node.value.startswith("./") and len(node.value) > 2
                        and not any(c.isspace() for c in node.value)):
                    found.add(node.value)
    return frozenset(found - _CONTAINER_PATHS)


# Absolute, anchored at the SESSION-START cwd. A file-valued default (the render script) is
# watched as that exact path, never its parent, so importing scripts/ (which writes its
# __pycache__ beside it) is not a violation.
_WATCHED = [_os.path.normpath(_os.path.join(_SESSION_CWD, p))
            for p in sorted(_relative_path_defaults())]
_VIOLATIONS = []
_ARMED = {"nodeid": None}
_WRITE_FLAGS = _os.O_WRONLY | _os.O_RDWR | _os.O_CREAT | _os.O_APPEND | _os.O_TRUNC


def _is_watched(path):
    try:
        resolved = _os.path.abspath(_os.fspath(path))
    except TypeError:
        return False                      # a file descriptor, not a path
    return any(resolved == w or resolved.startswith(w + _os.sep) for w in _WATCHED)


def _guard_audit(event, args):
    nodeid = _ARMED["nodeid"]
    if nodeid is None:
        return
    if event == "open":
        path, mode, flags = args
        writes = (any(c in mode for c in "wax+") if isinstance(mode, str)
                  else bool((flags or 0) & _WRITE_FLAGS))
        targets = [path] if writes else []
    elif event in ("os.mkdir", "sqlite3.connect"):
        targets = [args[0]]
    elif event in ("os.rename", "os.replace"):
        targets = [args[1]]
    else:
        return
    for target in targets:
        if isinstance(target, (str, bytes, _os.PathLike)) and _is_watched(target):
            _VIOLATIONS.append((nodeid, event, _os.fsdecode(_os.fspath(target))))


_sys.addaudithook(_guard_audit)


@pytest.fixture(autouse=True)
def _sandbox_guard(request):
    _ARMED["nodeid"] = request.node.nodeid
    try:
        yield
    finally:
        _ARMED["nodeid"] = None
    mine = [v for v in _VIOLATIONS if v[0] == request.node.nodeid]
    if mine:
        pytest.fail("this test wrote into a cwd-relative path default -- chdir into "
                    "tmp_path or pass an explicit path: "
                    + "; ".join(f"{event} {path}" for _n, event, path in mine))


@pytest.fixture(scope="session", autouse=True)
def _sandbox_session_check():
    # The hook sees only this process. A subprocess that writes a watched path is caught
    # here instead: anything absent at session start and present at the end.
    before = {w for w in _WATCHED if _os.path.exists(w)}
    yield
    appeared = [w for w in _WATCHED if w not in before and _os.path.exists(w)]
    if appeared:
        raise AssertionError("the test session created cwd-relative path defaults "
                             f"(a subprocess, which the audit hook cannot see): {appeared}")
```

Then make the two known offenders run in `tmp_path`. In `tests/test_cv_backend_failure.py`, add `monkeypatch.chdir(tmp_path)` as the first line of `test_the_single_lead_path_reports_an_outage_as_a_result` and of `test_the_single_lead_path_reports_a_non_transient_error_as_a_result`, with this comment above the first:

```python
    # cv.output_dir defaults to ./cv-output, relative to the cwd by design; without this
    # the run's prompt and run.json land in whatever directory pytest was started from.
```

- [ ] **Step 4: Run the whole suite and fix every test the guard reports**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -30`
Expected: PASS. If the guard fails any OTHER test ("this test wrote into a cwd-relative path default"), fix that test the same way (`monkeypatch.chdir(tmp_path)`, or pass an explicit path into the config it builds) in this task. The owner's rule is to address what is found, not defer it.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_sandbox_guard.py tests/test_cv_backend_failure.py
git commit -F - <<'EOF'
test: fail any test that writes into a cwd-relative path default

Two single-lead tests in test_cv_backend_failure.py wrote a prompt and a
run.json into the directory pytest was started from, because cv.output_dir
is relative by design. An audit hook now records any write under a
"./..." default in sluice/ and fails the offending test by name; the
watched set is derived from the source and pinned.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 2: Capture the `script` golden before anything changes

Spec §12.3. The golden pins what a `script` renderer receives today, captured from the CURRENT `cv/render.py::strip_citations` so Task 10's `to_text` is held to the old pipeline's output rather than to itself.

**Files:**
- Create: `tests/test_cv_script_golden.py`

**Interfaces:**
- Produces: `tests.test_cv_script_golden.CANONICAL_CV` (str, with citations) and `GOLDEN` (str, citation-free). Task 10 imports both.

- [ ] **Step 1: Write the test**

Create `tests/test_cv_script_golden.py`:

```python
"""What a `script` renderer receives, captured from today's pipeline (#364/#365/#368 §12.3).

CANONICAL_CV is a CV in today's composed-text format; GOLDEN is what
`cv/render.py::strip_citations` hands a render script for it. Task 10's `to_text` must
reproduce GOLDEN exactly for the equivalent CvDocument. Both are literals: deriving GOLDEN
from `to_text` would pin `to_text` against itself.
"""
CANONICAL_CV = """+1 555 0100

JANE ROE

PROFILE
I build reliable systems.

WORK EXPERIENCE

Example Systems
02/2023–present | Example Location A | SYNTHETIC-TITLE-1
- Shipped the platform [EF1]

Example Analytics
06/2020–01/2023 | Example Location B | SYNTHETIC-TITLE-2
- Grew team from 3 to 8 [EF1] [EF2]

CERTIFICATES
- Example Scrum Master

EDUCATION
- Example University, 09/2010–07/2014 | BSc Example

SKILLS
- Example Query
"""

GOLDEN = """+1 555 0100

JANE ROE

PROFILE
I build reliable systems.

WORK EXPERIENCE

Example Systems
02/2023–present | Example Location A | SYNTHETIC-TITLE-1
- Shipped the platform

Example Analytics
06/2020–01/2023 | Example Location B | SYNTHETIC-TITLE-2
- Grew team from 3 to 8

CERTIFICATES
- Example Scrum Master

EDUCATION
- Example University, 09/2010–07/2014 | BSc Example

SKILLS
- Example Query
"""


def test_the_golden_is_what_todays_strip_citations_hands_a_script():
    from sluice.cv.render import strip_citations
    assert strip_citations(CANONICAL_CV) == GOLDEN
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_cv_script_golden.py -q`
Expected: PASS (it records today's behaviour; it is the capture, not a new rule).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cv_script_golden.py
git commit -F - <<'EOF'
test(cv): capture what a script renderer receives today

A golden for the structured pipeline's to_text to reproduce, captured
from the current strip_citations so the new writer is held to the old
output rather than to itself.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 3: `core/tokens.py` — the one tokeniser and term matcher

Spec §4.2, §6.1 (Figures, `FABRICATED`), §8, §9.5. Pure. Moves `_WORD_RE` and `SKILL_TOKEN_RE` out of `cv/bundle.py` (which re-exports them, so `cv/terms.py` and `cv/validate.py` keep importing from where they do today) and adds the matcher, the figure scanner, the `Tools:` reader and the decoy validator the new pipeline and `doctor` share.

**Files:**
- Create: `sluice/core/tokens.py`
- Modify: `sluice/cv/bundle.py` (replace the two regex definitions with re-exports; move their long rationale comments into `core/tokens.py`)
- Test: `tests/test_core_tokens.py`

**Interfaces:**
- Produces:
  - `WORD_RE: re.Pattern`, `TOKEN_RULE_RE: re.Pattern`
  - `tokens(text: str) -> list[str]`
  - `segments(text: str) -> list[list[tuple[str, int, int]]]` — runs of `(token, start, end)` split at sentence punctuation
  - `find_term(text: str, term: str, *, case_sensitive: bool = False, lower_accepts_capital: bool = False) -> list[tuple[tuple[int, int], ...]]` — one tuple of TOKEN spans per whole-term occurrence
  - `figures(text: str, *, remove=()) -> frozenset[str]` — digit runs in any script, normalised to ASCII, after blanking each `(start, end)` in `remove`
  - `tool_items(entry: dict, field: str = "Tools") -> list[str]` — raises `ValueError` on a nameless or digit-led item
  - `decoy_problem(decoy) -> str | None`, `validate_decoys(decoys) -> None` — raises `ValueError` naming each bad decoy by POSITION

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_tokens.py`:

```python
"""core/tokens.py: the one tokeniser and term matcher the CV gate and doctor share.

Synthetic names only (owner's ruling, tests/test_fixture_name_neutrality.py): an
Examplelang / Examplelangscript pair stands in for a short name inside a longer one.
"""
import pytest

from sluice.core import tokens as T


def test_the_bundle_re_exports_the_one_tokeniser():
    from sluice.cv import bundle
    assert bundle._WORD_RE is T.WORD_RE
    assert bundle.SKILL_TOKEN_RE is T.TOKEN_RULE_RE


def test_sentence_punctuation_splits_segments_and_an_inner_dot_does_not():
    segs = T.segments("Built Example.lang tooling. Health checks ran")
    assert [[t for t, _s, _e in seg] for seg in segs] == [
        ["Built", "Example.lang", "tooling"], ["Health", "checks", "ran"]]


def test_a_whole_term_never_matches_inside_a_longer_token():
    assert T.find_term("We ship Examplelangscript daily", "Examplelang") == []
    assert len(T.find_term("We ship Examplelang daily", "Examplelang")) == 1


def test_matching_is_case_insensitive_by_default():
    assert len(T.find_term("we ship examplelang", "Examplelang")) == 1


def test_case_sensitive_matching_needs_identical_case():
    assert T.find_term("we ship examplelang", "Examplelang", case_sensitive=True) == []


def test_a_lowercase_term_may_accept_a_capitalised_mention_when_asked():
    text = "Coaching sessions ran weekly"
    assert T.find_term(text, "coaching", case_sensitive=True) == []
    assert len(T.find_term(text, "coaching", case_sensitive=True,
                           lower_accepts_capital=True)) == 1


def test_a_capitalised_term_never_matches_a_lowercase_word():
    # The Go / go-live shape: a capitalised tool must not be licensed by an ordinary word.
    assert T.find_term("Led the ex-live cutover", "Ex", case_sensitive=True,
                       lower_accepts_capital=True) == []


def test_a_phrase_never_matches_across_a_sentence_break():
    assert T.find_term("at Example. Zephyr checks ran", "Example Zephyr") == []
    assert len(T.find_term("at Example Zephyr, checks ran", "Example Zephyr")) == 1


def test_a_hyphenated_compound_matches_its_spaced_spelling():
    assert len(T.find_term("a co-founder of it", "co founder")) == 1


def test_a_term_touching_a_non_ascii_letter_or_digit_is_not_whole():
    assert T.find_term("Société Example", "Soci") == []
    assert T.find_term("Widget3０ units", "Widget3") == []


def test_figures_finds_digits_in_any_script_and_normalises_them():
    assert T.figures("grew ５００% in 2024") == {"500", "2024"}
    assert T.figures("grew ٥٠٠%") == {"500"}
    assert T.figures("handled 10⁶ requests") == {"106"}


def test_figures_blanks_only_the_spans_it_is_given():
    text = "Ran Example Widget3 at 30 sites"
    spans = [s for occ in T.find_term(text, "Example Widget3") for s in occ]
    assert T.figures(text, remove=spans) == {"30"}


def test_removing_a_multi_token_tool_never_removes_the_gap_between_its_tokens():
    # A full-width figure between two tokens of a licensed tool must still be scanned.
    text = "Example ５００ Widget"
    spans = [s for occ in T.find_term(text, "Example Widget") for s in occ]
    assert T.figures(text, remove=spans) == {"500"}


def test_a_licensed_name_never_launders_a_longer_figure():
    text = "Ran Widget30 nodes"
    spans = [s for occ in T.find_term(text, "Widget3") for s in occ]
    assert T.figures(text, remove=spans) == {"30"}


def test_tool_items_reads_a_comma_list():
    entry = {"fields": {"Tools": "Examplelang, .Examplenet , Example.lang"}}
    assert T.tool_items(entry) == ["Examplelang", ".Examplenet", "Example.lang"]


def test_tool_items_refuses_a_nameless_item():
    with pytest.raises(ValueError, match="no name"):
        T.tool_items({"fields": {"Tools": "Examplelang, ..."}})


def test_tool_items_refuses_a_digit_led_token():
    with pytest.raises(ValueError, match="must begin with a letter"):
        T.tool_items({"fields": {"Tools": "Examplestandard 9001"}})


@pytest.mark.parametrize("decoy", ["Examplelang#", ".Examplenet", "Example.lang",
                                   "Example Zephyr", "co founder"])
def test_the_decoy_validator_accepts_what_the_matcher_can_see(decoy):
    assert T.decoy_problem(decoy) is None


@pytest.mark.parametrize("decoy", ["", "Example-Zephyr", "100%", "Ph.D.",
                                   "Examplé", "日本"])
def test_the_decoy_validator_refuses_what_the_matcher_would_drop(decoy):
    assert T.decoy_problem(decoy)


def test_validate_decoys_names_the_position_and_never_echoes_the_value():
    with pytest.raises(ValueError) as exc:
        T.validate_decoys(["Example Zephyr", "Example-Secret"])
    assert "entry 2" in str(exc.value)
    assert "Example-Secret" not in str(exc.value)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_tokens.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sluice.core.tokens'`.

- [ ] **Step 3: Implement `sluice/core/tokens.py`**

```python
"""The ONE tokeniser and term matcher the CV gate and `doctor` share (#364/#365/#368).

In core/, not cv/: `core/doctor.py` must answer the same questions the gate answers (does
this decoy match that tool?), and core/ may not import a sub-app. A second copy in doctor
is how the two would come to disagree -- the bug class this repo's #30 incident names.
"""
import re
import unicodedata

# <MOVE HERE, verbatim, the rationale comment that sits above `_WORD_RE` in
#  cv/bundle.py today -- the trailing-dot measurement and the three consumers it broke.>
WORD_RE = re.compile(r"\.?[A-Za-z0-9#+]+(?:\.[A-Za-z0-9#+]+)*")

# <MOVE HERE, verbatim, the rationale comment that sits above `SKILL_TOKEN_RE` in
#  cv/bundle.py today -- why the rule is PER TOKEN, and why a digit-led name such as
#  `ISO 9001` stays refused: span removal subtracts from the numeric gate.>
TOKEN_RULE_RE = re.compile(r"^\.?[A-Za-z]")

# Sentence punctuation between two tokens ends a phrase, so a two-word decoy never matches
# across a sentence break ("at Example. Zephyr checks"). A dot INSIDE a token (`Node.js`)
# is part of the token and never reaches this set.
_BREAK = frozenset(".;:!?")


def tokens(text):
    return WORD_RE.findall(text or "")


def segments(text):
    """Runs of adjacent tokens as `(token, start, end)`, split at sentence punctuation."""
    text = text or ""
    out, cur, prev_end = [], [], None
    for m in WORD_RE.finditer(text):
        if prev_end is not None and any(c in _BREAK for c in text[prev_end:m.start()]):
            out.append(cur)
            cur = []
        cur.append((m.group(), m.start(), m.end()))
        prev_end = m.end()
    if cur:
        out.append(cur)
    return out


def _token_equal(hay, needle, *, case_sensitive, lower_accepts_capital):
    if not case_sensitive:
        return hay.casefold() == needle.casefold()
    if hay == needle:
        return True
    # A lowercase declared name (`coaching`) also accepts its sentence-initial capital;
    # a capitalised one (`Go`) never accepts a lowercase word (`go-live`).
    return (lower_accepts_capital and needle.islower()
            and hay == needle[:1].upper() + needle[1:])


def find_term(text, term, *, case_sensitive=False, lower_accepts_capital=False):
    """Every whole-term occurrence of `term` in `text`, as a tuple of TOKEN spans each.

    Whole-term means the term's token SEQUENCE inside one segment, with no alphanumeric
    character of ANY script touching either end -- so a name is never found inside a
    longer token, and an ASCII token run is never found inside a word that continues in
    another script. Token spans, not one span, because a caller that removes a match
    (figure scanning) must never remove the gap BETWEEN tokens, where a figure can sit."""
    text = text or ""
    needle = tokens(term)
    if not needle:
        return []
    found = []
    for seg in segments(text):
        for i in range(len(seg) - len(needle) + 1):
            window = seg[i:i + len(needle)]
            if not all(_token_equal(h[0], n, case_sensitive=case_sensitive,
                                    lower_accepts_capital=lower_accepts_capital)
                       for h, n in zip(window, needle)):
                continue
            start, end = window[0][1], window[-1][2]
            if (start > 0 and text[start - 1].isalnum()) or (
                    end < len(text) and text[end].isalnum()):
                continue
            found.append(tuple((s, e) for _t, s, e in window))
    return found


def figures(text, *, remove=()):
    """The digit runs in `text`, each normalised to ASCII, after blanking `remove`.

    A digit is any character `unicodedata.digit` gives a value: full-width, Arabic-Indic
    and superscript alike, so a figure cannot dodge the gate by script. Blanked spans become
    spaces, so removing a name can never join two digit runs into a new figure."""
    chars = list(text or "")
    for start, end in remove:
        for i in range(start, end):
            chars[i] = " "
    out, run = set(), []
    for c in chars + [" "]:
        d = unicodedata.digit(c, None)
        if d is not None:
            run.append(str(d))
        elif run:
            out.add("".join(run))
            run = []
    return frozenset(out)


def tool_items(entry, field="Tools"):
    """The `Tools:` items one evidence entry declares; a blank value declares none.

    Raises on a value the gate could not safely use: an item with no name at all, or a
    token that leads with a digit (see TOKEN_RULE_RE)."""
    raw = (entry.get("fields") or {}).get(field, "") or ""
    items = [t.strip() for t in raw.split(",") if t.strip()]
    for item in items:
        toks = tokens(item)
        if not toks:
            raise ValueError(f"{field} item {item!r} is invalid: it contains no name at "
                             f"all -- leave {field}: blank instead")
        for tok in toks:
            if not TOKEN_RULE_RE.match(tok):
                raise ValueError(
                    f"{field} item {item!r} is invalid: every token must begin with a "
                    f"letter, or a dot then a letter -- {tok!r} does not. A digit-led name "
                    "is refused because span removal would let a figure vanish with it")
    return items


def decoy_problem(decoy):
    """Why a `fabrication_decoys` entry can never match, or None when it can.

    A decoy the tokeniser cannot fully see would be a configured ban that silently never
    fires: text that tokenises to nothing (another script), or a character the matcher
    drops (a hyphen, a `%`, an accented letter)."""
    if not isinstance(decoy, str) or not decoy.strip():
        return "is empty"
    covered = set()
    for m in WORD_RE.finditer(decoy):
        covered.update(range(m.start(), m.end()))
    if not covered:
        return "has no letters or digits the matcher can see"
    if any(not c.isspace() and i not in covered for i, c in enumerate(decoy)):
        return ("contains a character the matcher drops -- use letters, digits, '#', '+' "
                "and inner dots only (write a hyphen as a space)")
    return None


def validate_decoys(decoys):
    """Raise if any decoy can never match. Names each by POSITION, never by value: a decoy
    is often a name the user wants kept off their CV, and an error travels further than the
    config file it came from."""
    problems = [(i, p) for i, d in enumerate(decoys or ()) if (p := decoy_problem(d))]
    if problems:
        raise ValueError("cv.fabrication_decoys: "
                         + "; ".join(f"entry {i + 1} {p}" for i, p in problems))
```

In `sluice/cv/bundle.py`, delete the `SKILL_TOKEN_RE = …` and `_WORD_RE = …` definitions (after moving their comments, see the two `<MOVE HERE>` notes above — the notes are instructions to you, not code to paste) and add near the top:

```python
# The one tokeniser and its per-token rule now live in core/tokens.py, where core/doctor.py
# can share them; these names stay importable from here for cv/terms.py and cv/validate.py.
from sluice.core.tokens import TOKEN_RULE_RE as SKILL_TOKEN_RE
from sluice.core.tokens import WORD_RE as _WORD_RE
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_core_tokens.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/tokens.py sluice/cv/bundle.py tests/test_core_tokens.py
git commit -F - <<'EOF'
feat(core): one tokeniser and whole-term matcher for the CV gate and doctor

Moves the word pattern and its per-token rule into core/tokens.py and adds
a matcher that never finds a name inside a longer token or across a
sentence break, a figure scanner that reads digits in any script, a Tools:
reader, and a decoy validator. Nothing uses them yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 4: Contract types, and `parse_layout`

Spec §4.1, §7.1. The types the Store and Renderer seams will carry move into `core/protocols.py` (the architect measured an import cycle with them anywhere under `cv/`), and `core/layout.py` validates a CV Layout mapping, reporting EVERY problem at once.

**Files:**
- Modify: `sluice/core/protocols.py` (add the constants and types below; move `Role`/`CvDocument` here)
- Modify: `sluice/cv/parse.py` (delete its own `Role`/`CvDocument` definitions; import them from `sluice.core.protocols`, so `from sluice.cv.parse import CvDocument, Role` keeps working until Task 19 deletes the module)
- Create: `sluice/core/layout.py`
- Test: `tests/test_core_layout.py`

**Interfaces:**
- Produces in `sluice/core/protocols.py`:
  - `CV_LAYOUT_RELPATH = "Job Applications/CV Layout.md"`
  - `SECTION_HEADINGS = ("PROFILE", "WORK EXPERIENCE", "CERTIFICATES", "EDUCATION", "SKILLS")`
  - `Role` and `CvDocument` — the exact dataclasses from `cv/parse.py`, fields unchanged (`Role(company, dates, location, title, bullets)`, `CvDocument(name, contact, profile, work, skills, certificates, education)`)
  - `class LayoutError(ValueError)` with `.problems: tuple[str, ...]`
  - `@dataclass(frozen=True) LayoutRole(heading: str, start: str, end: str, location: str = "", title: str = "", employers: tuple = (), bullets_max: int | None = None)` — `start`/`end` because `from` is a keyword; `end` is `"MM/YYYY"` or `"present"`
  - `@dataclass(frozen=True) CvLayout(roles: tuple, skills_max: int | None = None, certificates: tuple = (), education: tuple = (), any_role: tuple = (), omitted: tuple = ())`
- Produces in `sluice/core/layout.py`: `ROLE_KEYS`, `TOP_KEYS`, `PLACEHOLDERS`, `parse_layout(mapping) -> CvLayout` (raises `LayoutError`), `fold_employer(name: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_layout.py`:

```python
"""core/layout.py::parse_layout -- one row per validation rule in the spec's §4.1.

Mappings are built directly here (the pure parser); tests/test_core_layout_store.py reads
YAML TEXT through the store, the route a user's note takes.
"""
import pytest

from sluice.core.layout import parse_layout
from sluice.core.protocols import SECTION_HEADINGS, CvLayout, LayoutError, LayoutRole


def _role(**over):
    role = {"heading": "Example Alpha", "from": "01/2020", "to": "present"}
    role.update(over)
    return role


def _problems(mapping):
    with pytest.raises(LayoutError) as exc:
        parse_layout(mapping)
    return exc.value.problems


def test_a_minimal_layout_parses_with_every_optional_field_abstaining():
    layout = parse_layout({"roles": [_role()]})
    assert layout == CvLayout(roles=(LayoutRole(
        heading="Example Alpha", start="01/2020", end="present", location="", title="",
        employers=("Example Alpha",), bullets_max=None),))
    assert (layout.skills_max, layout.certificates, layout.education,
            layout.any_role, layout.omitted) == (None, (), (), (), ())


def test_every_field_is_read():
    layout = parse_layout({
        "skills_max": 4,
        "roles": [_role(location="Example Location A", title="SYNTHETIC-TITLE-1",
                        bullets_max=3, employers=["Example Beta", "Example Meridian"])],
        "certificates": ["Example Cert"], "education": ["Example University, BSc"],
        "any_role": ["Example Cartography"], "omitted": ["Example Tidal"],
    })
    role = layout.roles[0]
    assert (role.location, role.title, role.bullets_max, role.employers) == (
        "Example Location A", "SYNTHETIC-TITLE-1", 3, ("Example Beta", "Example Meridian"))
    assert (layout.skills_max, layout.certificates, layout.education,
            layout.any_role, layout.omitted) == (
        4, ("Example Cert",), ("Example University, BSc",), ("Example Cartography",),
        ("Example Tidal",))


def test_a_non_mapping_frontmatter_is_refused():
    assert "frontmatter" in _problems(["roles"])[0]


def test_roles_must_be_a_non_empty_list():
    assert any(p.startswith("roles:") for p in _problems({}))
    assert any(p.startswith("roles:") for p in _problems({"roles": []}))


def test_every_problem_is_reported_at_once():
    problems = _problems({"roles": [_role(**{"from": "13/2020"}), _role(to="soon")],
                          "skills_max": -1})
    assert len(problems) == 3, problems


def test_a_bad_month_names_its_path():
    assert _problems({"roles": [_role(**{"from": "13/2020"})]}) == (
        "roles[0].from: is not MM/YYYY",)


def test_from_equal_to_to_is_accepted_and_from_after_to_is_refused():
    parse_layout({"roles": [_role(**{"from": "03/2020", "to": "03/2020"})]})
    assert _problems({"roles": [_role(**{"from": "04/2020", "to": "03/2020"})]}) == (
        "roles[0]: from is after to",)


def test_present_is_normalised_whatever_its_case():
    assert parse_layout({"roles": [_role(to="PRESENT")]}).roles[0].end == "present"


def test_a_valueless_bullets_max_reads_as_absent_never_zero():
    assert parse_layout({"roles": [_role(bullets_max=None)]}).roles[0].bullets_max is None


def test_zero_is_a_real_cap():
    assert parse_layout({"roles": [_role(bullets_max=0)]}).roles[0].bullets_max == 0


@pytest.mark.parametrize("bad", [True, -1, 2.5, "3"])
def test_a_cap_must_be_a_whole_number(bad):
    assert _problems({"roles": [_role(bullets_max=bad)]})[0].startswith(
        "roles[0].bullets_max:")
    assert _problems({"roles": [_role()], "skills_max": bad})[0].startswith("skills_max:")


def test_an_unknown_role_key_is_refused_with_a_hint():
    assert _problems({"roles": [_role(bullet_max=3)]}) == (
        "roles[0].bullet_max: unknown key -- did you mean bullets_max?",)


def test_a_per_role_key_at_the_top_level_is_refused_by_name():
    assert _problems({"roles": [_role()], "bullets_max": 3}) == (
        "bullets_max: belongs inside a role, under roles:",)


@pytest.mark.parametrize("key,meant", [("skill_max", "skills_max"), ("anyrole", "any_role"),
                                       ("role", "roles"), ("certifications", "certificates")])
def test_a_near_miss_top_level_key_is_refused(key, meant):
    assert _problems({"roles": [_role()], key: 1}) == (
        f"{key}: unknown key -- did you mean {meant}?",)


@pytest.mark.parametrize("key", ["tags", "base", "aliases", "description", "notes"])
def test_obsidian_and_user_metadata_at_the_top_level_is_ignored(key):
    parse_layout({"roles": [_role()], key: "anything"})


def test_a_scalar_where_a_list_belongs_is_refused():
    assert _problems({"roles": [_role()], "certificates": "Example Cert"}) == (
        "certificates: must be a list -- put each item on its own '- ' line",)


def test_a_valueless_list_key_reads_as_absent():
    assert parse_layout({"roles": [_role()], "education": None}).education == ()


@pytest.mark.parametrize("field", ["heading", "location", "title"])
def test_a_pipe_is_refused_in_meta_fields(field):
    assert _problems({"roles": [_role(**{field: "Example | Alpha"})]})[0].startswith(
        f"roles[0].{field}: '|'")


def test_a_heading_equal_to_a_section_heading_is_refused():
    # Derived from the one heading tuple, in another case: the fold is what is under test.
    heading = SECTION_HEADINGS[1].title()
    assert _problems({"roles": [_role(heading=heading)]}) == (
        "roles[0].heading: equals a CV section heading",)


def test_a_line_break_or_control_character_is_refused_in_any_string():
    assert _problems({"roles": [_role(title="SYNTHETIC\nTITLE")]})[0].startswith(
        "roles[0].title: contains a line break")
    assert _problems({"roles": [_role()], "education": ["Example\x0bUniversity"]})[0] \
        .startswith("education[0]: contains a line break")


def test_a_documented_placeholder_left_anywhere_is_refused_at_its_path():
    assert _problems({"roles": [_role(location="<location>")]}) == (
        "roles[0].location: still holds the example placeholder <location>",)
    assert _problems({"roles": [_role(**{"from": "<MM/YYYY>"})]}) == (
        "roles[0].from: still holds the example placeholder <MM/YYYY>",)


def test_a_company_both_omitted_and_in_a_role_is_refused():
    # The same employer in another case is one name after the fold. Bound to a name rather
    # than written inline, so a text sweep of list literals sees no `.casefold()` item.
    folded = ["Example Beta".casefold()]
    assert _problems({"roles": [_role(employers=["Example Beta"])], "omitted": folded}) == (
        "omitted[0]: also listed under any_role or a role's employers",)


def test_an_empty_employers_list_is_refused():
    assert _problems({"roles": [_role(employers=[])]}) == (
        "roles[0].employers: must not be empty",)


def test_a_missing_or_blank_heading_is_refused():
    headless = {"from": "01/2020", "to": "present"}
    assert _problems({"roles": [headless]}) == ("roles[0].heading: required",)
    assert _problems({"roles": [_role(heading="  ")]}) == ("roles[0].heading: must not be blank",)


def test_a_role_that_is_not_a_mapping_is_refused():
    assert _problems({"roles": ["Example Alpha"]}) == (
        "roles[0]: must be a mapping (heading:, from:, to:, ...)",)


def test_a_non_text_location_or_title_is_refused():
    assert _problems({"roles": [_role(location=7)]}) == (
        "roles[0].location: must be text, found int",)
    assert _problems({"roles": [_role(title=True)]}) == (
        "roles[0].title: must be text, found bool",)


def test_a_blank_employers_item_is_refused():
    assert _problems({"roles": [_role(employers=["Example Beta", " "])]}) == (
        "roles[0].employers[1]: must not be blank",)


def test_a_missing_from_is_refused():
    fromless = {"heading": "Example Alpha", "to": "present"}
    assert _problems({"roles": [fromless]}) == ("roles[0].from: required",)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_layout.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sluice.core.layout'`.

- [ ] **Step 3: Add the contract types to `sluice/core/protocols.py`**

Move `Role` and `CvDocument` (with their docstrings: they are the public contract user templates are written against) from `sluice/cv/parse.py` into `sluice/core/protocols.py`, and add beside them:

```python
# The CV Layout note (#364/#365/#368): the vault's one record of which roles a CV shows and
# how. Beside the Candidate Profile, which supplies the name and contact.
CV_LAYOUT_RELPATH = "Job Applications/CV Layout.md"

# The canonical CV text's section headings, in the order cv/document.py::to_text writes
# them. Here rather than in cv/document.py because core/layout.py must refuse a layout
# heading equal to one and core/ may not import a sub-app; cv/document.py re-exports it.
SECTION_HEADINGS = ("PROFILE", "WORK EXPERIENCE", "CERTIFICATES", "EDUCATION", "SKILLS")


class LayoutError(ValueError):
    """The CV Layout note is malformed. Carries EVERY problem found, each naming its path,
    so one edit fixes them all. A ValueError so the existing `(OSError, ValueError)`
    catches keep holding; a caller that tells malformed from unreadable catches this
    first."""

    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__("the CV Layout note is malformed:\n  - " + "\n  - ".join(self.problems))


@dataclass(frozen=True)
class LayoutRole:
    """One heading on the CV. `start`/`end` are the note's `from`/`to` (`from` is a Python
    keyword); `end` is `MM/YYYY` or `present`. `employers` defaults to `(heading,)`;
    `bullets_max` is None for no cap, 0 for none."""
    heading: str
    start: str
    end: str
    location: str = ""
    title: str = ""
    employers: tuple = ()
    bullets_max: int | None = None


@dataclass(frozen=True)
class CvLayout:
    roles: tuple
    skills_max: int | None = None
    certificates: tuple = ()
    education: tuple = ()
    any_role: tuple = ()
    omitted: tuple = ()
```

In `sluice/cv/parse.py`, replace the two class definitions with:

```python
# The public template contract now lives in core/protocols.py, where the Renderer seam can
# name it without a core -> cv import; re-exported here until this module is retired.
from sluice.core.protocols import CvDocument, Role  # noqa: F401
```

- [ ] **Step 4: Implement `sluice/core/layout.py`**

```python
"""The CV Layout note: its validation, and how evidence entries map onto its roles.

Pure. The store reads the note's YAML and hands the mapping here
(core/vault.py::Vault.read_cv_layout), so `doctor` and the engine share ONE reading of
what a layout means.
"""
import difflib
import re

from sluice.core.protocols import SECTION_HEADINGS, CvLayout, LayoutError, LayoutRole
from sluice.core.safeout import is_control

ROLE_KEYS = ("heading", "from", "to", "location", "title", "employers", "bullets_max")
TOP_KEYS = ("roles", "skills_max", "certificates", "education", "any_role", "omitted")
_LIST_KEYS = ("certificates", "education", "any_role", "omitted")
# The placeholders docs/CONFIGURATION.md's example uses. A value still carrying one is a
# copy nobody finished editing, and refusing it keeps a literal "<title>" off a CV sent
# under the user's name.
PLACEHOLDERS = ("<MM/YYYY>", "<MM/YYYY or present>", "<location>", "<title>", "<n>",
                "<company>", "<certificate>", "<institution, dates | qualification>")
_DATE_RE = re.compile(r"(0[1-9]|1[0-2])/(\d{4})")
_HEADINGS = frozenset(h.casefold() for h in SECTION_HEADINGS)


def fold_employer(name):
    """One employer name, folded for matching: the repo's one name fold, whitespace
    collapsed (so a non-breaking space or a doubled space still matches).

    Imported INSIDE the function: core/vault.py imports this module for read_cv_layout, so
    a module-scope import back would be a cycle. The fold has ONE home -- never copy it."""
    from sluice.core.vault import _fold_note_name
    return " ".join(_fold_note_name(name).split())


def parse_layout(mapping):
    """A validated CvLayout, or LayoutError listing every problem in the mapping."""
    if not isinstance(mapping, dict):
        raise LayoutError([
            "frontmatter: expected the layout's keys (roles:, ...) in the note's YAML "
            f"frontmatter between --- lines, found {type(mapping).__name__}"])
    problems = []
    for key in mapping:
        if not isinstance(key, str) or key in TOP_KEYS:
            continue
        if key in ROLE_KEYS:
            problems.append(f"{key}: belongs inside a role, under roles:")
            continue
        # A high cutoff: `description:` and `notes:` are ordinary note metadata and must
        # stay ignored, while `skill_max:` is a typo that would silently lift a cap.
        near = difflib.get_close_matches(key, TOP_KEYS, n=1, cutoff=0.8)
        if near:
            problems.append(f"{key}: unknown key -- did you mean {near[0]}?")
    roles = []
    raw_roles = mapping.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        problems.append("roles: required -- a list with one entry per heading on the CV")
    else:
        for i, raw in enumerate(raw_roles):
            role = _role(f"roles[{i}]", raw, problems)
            if role is not None:
                roles.append(role)
    skills_max = _cap("skills_max", mapping.get("skills_max"), problems)
    lists = {k: _str_list(k, mapping.get(k), problems, required=False) for k in _LIST_KEYS}
    _contradictions(roles, lists, problems)
    if problems:
        raise LayoutError(problems)
    return CvLayout(roles=tuple(roles), skills_max=skills_max, **lists)


def _text(path, value, problems, *, required, meta=False):
    if value is None:
        if required:
            problems.append(f"{path}: required")
        return ""
    if not isinstance(value, str):
        problems.append(f"{path}: must be text, found {type(value).__name__}")
        return ""
    if required and not value.strip():
        problems.append(f"{path}: must not be blank")
        return ""
    # A layout string is written into lines a `script` renderer re-reads, so it must not be
    # able to forge a line any more than model text can (spec §4.1).
    if any(is_control(c) for c in value):
        problems.append(f"{path}: contains a line break or control character")
        return ""
    hit = next((p for p in PLACEHOLDERS if p in value), None)
    if hit:
        problems.append(f"{path}: still holds the example placeholder {hit}")
        return ""
    if meta and "|" in value:
        problems.append(f"{path}: '|' separates the CV's meta-line fields, so it cannot "
                        "appear here")
        return ""
    if meta and value.strip().casefold() in _HEADINGS:
        problems.append(f"{path}: equals a CV section heading")
        return ""
    return value.strip()


def _date(path, value, problems, *, allow_present):
    text = _text(path, value, problems, required=True)
    if not text:
        return None
    if allow_present and text.casefold() == "present":
        return "present"
    if not _DATE_RE.fullmatch(text):
        # The value is NOT echoed: this message reaches `doctor`, whose rows go to MCP
        # clients whole, and a layout date is employment history. The path names the field.
        problems.append(f"{path}: is not MM/YYYY"
                        + (" or present" if allow_present else ""))
        return None
    return text


def _order(date):
    if date == "present":
        return (9999, 99)
    month, year = date.split("/")
    return (int(year), int(month))


def _cap(path, value, problems):
    # bool BEFORE int: PyYAML reads `yes` as True, and True is an int.
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        problems.append(f"{path}: must be a whole number of 0 or more (0 means none; leave "
                        "the key out for no cap)")
        return None
    return value


def _str_list(path, value, problems, *, required):
    if value is None:
        return ()
    if not isinstance(value, list):
        problems.append(f"{path}: must be a list -- put each item on its own '- ' line")
        return ()
    if required and not value:
        problems.append(f"{path}: must not be empty")
        return ()
    out = []
    for j, item in enumerate(value):
        text = _text(f"{path}[{j}]", item, problems, required=True)
        if text:
            out.append(text)
    return tuple(out)


def _role(path, raw, problems):
    if not isinstance(raw, dict):
        problems.append(f"{path}: must be a mapping (heading:, from:, to:, ...)")
        return None
    for key in raw:
        if key not in ROLE_KEYS:
            near = difflib.get_close_matches(str(key), ROLE_KEYS, n=1, cutoff=0.6)
            problems.append(f"{path}.{key}: unknown key"
                            + (f" -- did you mean {near[0]}?" if near else ""))
    heading = _text(f"{path}.heading", raw.get("heading"), problems, required=True, meta=True)
    start = _date(f"{path}.from", raw.get("from"), problems, allow_present=False)
    end = _date(f"{path}.to", raw.get("to"), problems, allow_present=True)
    if start and end and _order(start) > _order(end):
        problems.append(f"{path}: from is after to")
    location = _text(f"{path}.location", raw.get("location"), problems, required=False,
                     meta=True)
    title = _text(f"{path}.title", raw.get("title"), problems, required=False, meta=True)
    if raw.get("employers") is None:
        employers = (heading,) if heading else ()
    else:
        employers = _str_list(f"{path}.employers", raw["employers"], problems, required=True)
    bullets_max = _cap(f"{path}.bullets_max", raw.get("bullets_max"), problems)
    return LayoutRole(heading=heading, start=start or "", end=end or "", location=location,
                      title=title, employers=employers, bullets_max=bullets_max)


def _contradictions(roles, lists, problems):
    """A company both omitted and placed is a layout that says two opposite things."""
    if not lists["omitted"]:
        return
    placed = {fold_employer(c) for c in lists["any_role"]}
    for role in roles:
        placed |= {fold_employer(e) for e in role.employers}
    for j, company in enumerate(lists["omitted"]):
        if fold_employer(company) in placed:
            problems.append(f"omitted[{j}]: also listed under any_role or a role's employers")
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_core_layout.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/protocols.py sluice/core/layout.py sluice/cv/parse.py tests/test_core_layout.py
git commit -F - <<'EOF'
feat(core): CV Layout contract types and their validation

Role and CvDocument move into core/protocols.py beside the new CvLayout,
LayoutRole and LayoutError, so the Store and Renderer seams can name them
without importing a sub-app. parse_layout reports every problem in a
layout at once, each naming its path. Nothing reads a layout yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 5: Employer matching and the slot table

Spec §4.3, D6 (as revised: blank and unmatched companies are citable nowhere). Adds to `core/layout.py` the one reading of which entry may be cited under which role, built ONCE per lead.

**Files:**
- Modify: `sluice/core/layout.py`
- Test: `tests/test_core_layout_slots.py`

**Interfaces:**
- Consumes: `CvLayout`, `LayoutRole` (Task 4), `fold_employer` (Task 4).
- Produces in `sluice/core/layout.py`:
  - `employers_of(company: str) -> frozenset[str]` — the whole value and its `,` `;` `/` parts, each folded
  - `@dataclass(frozen=True) Placement(reason: str, roles: frozenset)` — `reason` ∈ `{"role", "any_role", "omitted", "blank", "unmatched"}`; `roles` holds indexes into `layout.roles`
  - `place(layout: CvLayout, company: str) -> Placement`
  - `@dataclass(frozen=True) Slot(id: str, role: LayoutRole, eligible: tuple, budget: int | None)` — `id` is `"R1"`…; `budget` is the EFFECTIVE budget: `None` = no cap, `0` when `bullets_max == 0` or no entry is eligible
  - `build_slots(layout: CvLayout, entries: list[dict]) -> tuple[Slot, ...]` — `entries` are bundle entries carrying `"id"` and `"company"`
  - `no_citable_slot(slots) -> bool` — true when no slot can carry a bullet although some role wants bullets
  - `placement_counts(layout: CvLayout, entries: list[dict]) -> dict[str, int]`
  - `layout_text(layout: CvLayout) -> str` — every string the layout shows the composer, one per line (for the term vocabulary, Task 13)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_core_layout_slots.py`:

```python
"""core/layout.py: which entry may be cited under which role (spec §4.3, D6)."""
from sluice.core.layout import (Placement, build_slots, employers_of, layout_text,
                                no_citable_slot, place, placement_counts)
from sluice.core.protocols import CvLayout, LayoutRole


def _layout(*roles, any_role=(), omitted=()):
    return CvLayout(roles=tuple(roles), any_role=any_role, omitted=omitted)


def _role(heading, employers=None, bullets_max=None):
    return LayoutRole(heading=heading, start="01/2020", end="present",
                      employers=tuple(employers or (heading,)), bullets_max=bullets_max)


ALPHA = _role("Example Alpha")
GROUP = _role("Example Northgate", employers=["Example Beta", "Example Meridian"], bullets_max=3)


def test_employers_of_takes_the_whole_value_and_each_part_folded():
    assert employers_of("Example Beta / Example Meridian") == {
        "example beta / example meridian", "example beta", "example meridian"}


def test_a_block_list_company_and_an_nbsp_still_match():
    # Review Focus 2: Obsidian writes a list property, which the vault reader joins with
    # ", "; a pasted name can carry a non-breaking space.
    layout = _layout(GROUP)
    assert place(layout, "Example Beta, Example Meridian").reason == "role"
    assert place(layout, "Example\u00a0Beta").roles == frozenset({0})


def test_a_multi_employer_company_matches_through_one_part():
    # Review Focus 5: an entry spanning two employers is citable under a roll-up that
    # names only one of them.
    assert place(_layout(ALPHA, GROUP), "Example Telemetry / Example Meridian").roles == frozenset({1})


def test_the_whole_value_keeps_a_comma_inside_one_employer_matchable():
    layout = _layout(_role("Example, Inc"))
    assert place(layout, "Example, Inc").reason == "role"
    # The role side is never split, so a company merely ending in Inc does not match.
    assert place(layout, "Example Co, Inc").reason == "unmatched"


def test_canonically_equivalent_spellings_match():
    # One employer typed precomposed (NFC) in the layout and decomposed (NFD) in an
    # entry is one name, so one match: the vault's own fold (#205, #299).
    layout = _layout(_role("Example Co Soci\u00e9t\u00e9"))
    assert place(layout, "Example Co Socie\u0301te\u0301").reason == "role"


def test_any_role_makes_an_entry_eligible_everywhere():
    layout = _layout(ALPHA, GROUP, any_role=("Example Cartography",))
    assert place(layout, "Example Cartography") == Placement("any_role", frozenset({0, 1}))


def test_omitted_blank_and_unmatched_are_eligible_nowhere():
    layout = _layout(ALPHA, omitted=("Example Tidal",))
    assert place(layout, "Example Tidal") == Placement("omitted", frozenset())
    assert place(layout, "") == Placement("blank", frozenset())
    assert place(layout, "   ") == Placement("blank", frozenset())
    assert place(layout, "Example Robotics") == Placement("unmatched", frozenset())


def test_a_role_without_employers_scopes_by_its_heading():
    layout = _layout(ALPHA)
    assert place(layout, "example alpha").roles == frozenset({0})


def test_build_slots_numbers_roles_in_layout_order_with_their_eligible_ids():
    entries = [{"id": "EA1", "company": "Example Alpha"},
               {"id": "EB1", "company": "Example Beta"},
               {"id": "EN1", "company": "Example Robotics"}]
    slots = build_slots(_layout(ALPHA, GROUP), entries)
    assert [(s.id, s.role.heading, s.eligible, s.budget) for s in slots] == [
        ("R1", "Example Alpha", ("EA1",), None),
        ("R2", "Example Northgate", ("EB1",), 3)]


def test_a_slot_with_no_eligible_entry_has_a_budget_of_zero():
    slots = build_slots(_layout(ALPHA, GROUP), [{"id": "EA1", "company": "Example Alpha"}])
    assert [s.budget for s in slots] == [None, 0]


def test_no_citable_slot_needs_a_role_that_wants_bullets():
    nothing_matches = build_slots(_layout(ALPHA), [{"id": "EN1", "company": "Example Robotics"}])
    assert no_citable_slot(nothing_matches)
    headings_only = build_slots(_layout(_role("Example Alpha", bullets_max=0)),
                                [{"id": "EA1", "company": "Example Alpha"}])
    assert not no_citable_slot(headings_only)


def test_placement_counts_tallies_each_reason():
    layout = _layout(ALPHA, omitted=("Example Tidal",))
    entries = [{"id": "A", "company": "Example Alpha"}, {"id": "B", "company": ""},
               {"id": "C", "company": "Example Tidal"}, {"id": "D", "company": "Example X"},
               {"id": "E", "company": "Example Y"}]
    assert placement_counts(layout, entries) == {
        "role": 1, "any_role": 0, "omitted": 1, "blank": 1, "unmatched": 2}


def test_layout_text_carries_every_string_the_composer_is_shown():
    layout = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                        location="Example Location A",
                                        title="SYNTHETIC-TITLE-1",
                                        employers=("Example Beta",)),),
                      certificates=("Example Cert",), education=("Example University",))
    text = layout_text(layout)
    for value in ("Example Alpha", "Example Location A", "SYNTHETIC-TITLE-1",
                  "Example Beta", "Example Cert", "Example University"):
        assert value in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_layout_slots.py -q`
Expected: FAIL with `ImportError: cannot import name 'Placement' from 'sluice.core.layout'`.

- [ ] **Step 3: Implement**

Append to `sluice/core/layout.py` (and add `from dataclasses import dataclass` to its imports):

```python
_PARTS_RE = re.compile(r"[,;/]")


def employers_of(company):
    """The folded employers an entry's `Company:` names: the whole value AND each `,` `;` `/`
    part. Taking the whole value too keeps an employer whose own name contains a comma
    matchable; the ROLE side is never split (see `place`)."""
    raw = company if isinstance(company, str) else ""
    folded = (fold_employer(p) for p in [raw, *_PARTS_RE.split(raw)])
    return frozenset(f for f in folded if f)


@dataclass(frozen=True)
class Placement:
    reason: str        # "role" | "any_role" | "omitted" | "blank" | "unmatched"
    roles: frozenset   # indexes into layout.roles this entry may be cited under


def place(layout, company):
    """Where one entry may be cited (spec D6). A blank or unmatched company is citable
    NOWHERE, so the model can never move work under an employer the user did not put it
    under; `any_role:` is the explicit way to make an entry fit every role."""
    parts = employers_of(company)
    if not parts:
        return Placement("blank", frozenset())
    if parts & {fold_employer(c) for c in layout.any_role}:
        return Placement("any_role", frozenset(range(len(layout.roles))))
    # A role's own heading stands in when it lists no employers: parse_layout always fills
    # them, but a LayoutRole built directly must not silently match nothing.
    matched = frozenset(i for i, role in enumerate(layout.roles)
                        if parts & {fold_employer(e) for e in role.employers or (role.heading,)})
    if matched:
        return Placement("role", matched)
    if parts & {fold_employer(c) for c in layout.omitted}:
        return Placement("omitted", frozenset())
    return Placement("unmatched", frozenset())


@dataclass(frozen=True)
class Slot:
    id: str              # "R1", "R2", ... in layout order
    role: object         # LayoutRole
    eligible: tuple      # entry ids citable here, in bundle order
    budget: object       # effective cap: None = no cap; 0 = no bullets


def build_slots(layout, entries):
    """The slot table, built ONCE per lead and handed to every stage that needs it, so the
    prompt can never offer a cite the gate then refuses. A slot with no eligible entry has
    an effective budget of 0: shown as heading-only, anything written there trimmed, so it
    never costs a retry."""
    places = {e["id"]: place(layout, e.get("company", "")) for e in entries}
    slots = []
    for i, role in enumerate(layout.roles):
        eligible = tuple(e["id"] for e in entries if i in places[e["id"]].roles)
        slots.append(Slot(f"R{i + 1}", role, eligible, role.bullets_max if eligible else 0))
    return tuple(slots)


def no_citable_slot(slots):
    """True when no slot can carry a bullet although some role asks for bullets -- a
    misconfiguration (say, legal-suffix company names the headings do not match) that
    would otherwise spend a dossier fetch and two compose calls per lead before failing."""
    return (all(s.budget == 0 for s in slots)
            and any(s.role.bullets_max != 0 for s in slots))


def placement_counts(layout, entries):
    counts = dict.fromkeys(("role", "any_role", "omitted", "blank", "unmatched"), 0)
    for e in entries:
        counts[place(layout, e.get("company", "")).reason] += 1
    return counts


def layout_text(layout):
    """Every string the layout shows the composer, one per line: what the term check must
    recognise as the user's own words."""
    parts = []
    for role in layout.roles:
        parts += [role.heading, role.location, role.title, *role.employers]
    parts += [*layout.certificates, *layout.education, *layout.any_role, *layout.omitted]
    return "\n".join(p for p in parts if p)
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_core_layout_slots.py tests/test_core_layout.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/layout.py tests/test_core_layout_slots.py
git commit -F - <<'EOF'
feat(core): map evidence entries onto CV Layout roles

An entry is citable under the roles whose employers its Company: names,
under every role when its company is listed as any_role, and nowhere when
its company is blank, omitted or unmatched. build_slots turns that into
the one slot table every later stage shares.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 6: `Store.read_cv_layout`

Spec §4.1 (Store seam), §12.1 (Conformance, YAML through the store). A must-support Store member with three outcomes kept apart: absent → `None`, malformed → `LayoutError`, unreadable → `OSError`/`ValueError` (#242: unreadable is never read as absent).

**Files:**
- Modify: `sluice/core/protocols.py` (`Store.read_cv_layout`)
- Modify: `sluice/core/vault.py` (`Vault.read_cv_layout`; module-scope `from sluice.core.layout import parse_layout` — the cycle breaks in `core/layout.py::fold_employer`, which imports `core.vault` lazily)
- Modify: `tests/conformance/seeds.py` (`layout=` seeder), `tests/conformance/test_store_contract.py` (contract rows)
- Modify: `tests/conftest.py` (`SYNTHETIC_LAYOUT`, `layout_yaml()`)
- Modify: `tests/test_cv_engine.py` (`FakeVault.read_cv_layout`; derive the signature test's method list from the `Store` protocol)
- Modify: `tests/test_mcpserver.py` (`_STORE_READ_METHODS` gains `"read_cv_layout"`)
- Create: `tests/test_core_layout_store.py` (vault-specific rows)

**Interfaces:**
- Consumes: `parse_layout`, `CV_LAYOUT_RELPATH`, `LayoutError`, `CvLayout` (Task 4).
- Produces: `Store.read_cv_layout(self) -> CvLayout | None`; `Vault.read_cv_layout`; `tests.conftest.SYNTHETIC_LAYOUT: CvLayout` (one role headed `Example Foundry`, matching `tests/test_cv_engine.py::ENTRIES`' company) and `tests.conftest.layout_yaml(roles=None, **top) -> str` (a note's text: YAML frontmatter written with block lists); `FakeVault(entries, notes=None, candidate=…, layout=SYNTHETIC_LAYOUT)` with `read_cv_layout()`.

- [ ] **Step 1: Write the shared fixtures and the failing tests**

Add to `tests/conftest.py` (beside `LOCATIONS`):

```python
from sluice.core.protocols import CvLayout, LayoutRole

# One shared synthetic layout for every CV test (spec §12.2): a heading matching the
# engine tests' Example Foundry entry, a title from the seeded faker pool, a location from
# LOCATIONS. Never a real employment history.
SYNTHETIC_LAYOUT = CvLayout(
    roles=(LayoutRole(heading="Example Foundry", start="02/2023", end="present",
                      location=LOCATIONS[0], title=_title_pool()[0]),),
    certificates=("Example Scrum Master",), education=("Example University, BSc Example",))


def layout_yaml(roles=None, **top):
    """A CV Layout note's TEXT: YAML frontmatter written with BLOCK lists -- the shape
    docs/CONFIGURATION.md documents, and the route a user's note takes. In block style a
    comma inside an item is safe; only the inline `[a, b]` style splits on it."""
    import yaml
    roles = roles if roles is not None else [
        {"heading": "Example Foundry", "from": "02/2023", "to": "present"}]
    body = yaml.safe_dump({"roles": roles, **top}, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
    return f"---\n{body}---\n"
```

Create `tests/test_core_layout_store.py`:

```python
"""Vault.read_cv_layout: the vault-specific outcomes, and YAML read through the store."""
import os

import pytest

from sluice.core.protocols import CV_LAYOUT_RELPATH, LayoutError
from sluice.core.vault import Vault
from tests.conftest import layout_yaml


def _vault(tmp_path, text=None, raw=None):
    v = Vault(str(tmp_path))
    if text is not None:
        v.write_document(CV_LAYOUT_RELPATH, text)
    if raw is not None:
        path = os.path.join(str(tmp_path), *CV_LAYOUT_RELPATH.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
    return v


def test_a_symlinked_note_is_refused_as_unreadable(tmp_path):
    target = tmp_path / "elsewhere.md"
    target.write_text(layout_yaml(), encoding="utf-8")
    path = tmp_path.joinpath(*CV_LAYOUT_RELPATH.split("/"))
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    with pytest.raises(OSError):
        Vault(str(tmp_path)).read_cv_layout()


def test_a_non_utf8_note_is_unreadable_not_malformed(tmp_path):
    v = _vault(tmp_path, raw=b"---\nroles: \xff\n---\n")
    with pytest.raises(ValueError) as exc:
        v.read_cv_layout()
    assert not isinstance(exc.value, LayoutError)


def test_pyyaml_unavailable_is_a_layout_error_naming_it(tmp_path, monkeypatch):
    import sluice.core.vault as vault_mod
    v = _vault(tmp_path, layout_yaml())
    monkeypatch.setattr(vault_mod, "yaml", None)
    with pytest.raises(LayoutError, match="PyYAML"):
        v.read_cv_layout()


def test_runaway_nesting_is_a_layout_error(tmp_path):
    v = _vault(tmp_path, "---\nroles: " + "[" * 5000 + "]" * 5000 + "\n---\n")
    with pytest.raises(LayoutError):
        v.read_cv_layout()


def test_a_note_without_frontmatter_is_malformed(tmp_path):
    with pytest.raises(LayoutError, match="frontmatter"):
        _vault(tmp_path, "roles: written in the body\n").read_cv_layout()


def test_an_inline_list_splits_an_unquoted_comma_and_a_block_list_does_not(tmp_path):
    # The trap docs/CONFIGURATION.md warns about, measured through the real route.
    flow = "---\nroles:\n  - heading: Example Alpha\n    from: 01/2020\n    to: present\n" \
           "    employers: [Example, Inc]\n---\n"
    assert _vault(tmp_path / "a", flow).read_cv_layout().roles[0].employers == (
        "Example", "Inc")
    block = layout_yaml([{"heading": "Example Alpha", "from": "01/2020", "to": "present",
                          "employers": ["Example, Inc"]}])
    assert _vault(tmp_path / "b", block).read_cv_layout().roles[0].employers == (
        "Example, Inc",)


def test_a_quoted_comma_survives_in_any_role_and_education(tmp_path):
    layout = _vault(tmp_path, layout_yaml(any_role=["Example, Inc"],
                                          education=["Example University, BSc"])
                    ).read_cv_layout()
    assert layout.any_role == ("Example, Inc",)
    assert layout.education == ("Example University, BSc",)


@pytest.mark.parametrize("module", ["sluice.core.layout", "sluice.core.vault"])
def test_each_module_imports_first_in_a_fresh_interpreter(module):
    """Spec §9.5: core/vault.py imports parse_layout, and core/layout.py needs vault's fold.
    Module-scope imports on both sides would be an ImportError at startup, in whichever
    order a fresh process imports them -- which an in-process test, with both already
    loaded, can never see."""
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", f"import {module}"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
```

Add to `tests/conformance/test_store_contract.py` (module-level `pytestmark` already parametrizes `store_name`):

```python
from sluice.core.protocols import CvLayout, LayoutError, LayoutRole
from tests.conftest import layout_yaml


def test_read_cv_layout_is_none_when_the_note_is_absent(store_name, tmp_path, monkeypatch):
    assert _make_store(store_name, tmp_path, monkeypatch).read_cv_layout() is None


def test_read_cv_layout_round_trips_a_declared_layout(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    seed(store_name, store, layout=layout_yaml(
        [{"heading": "Example Alpha", "from": "01/2020", "to": "present",
          "location": "Example Location A", "bullets_max": 3}], skills_max=5))
    assert store.read_cv_layout() == CvLayout(
        roles=(LayoutRole("Example Alpha", "01/2020", "present",
                          location="Example Location A", employers=("Example Alpha",),
                          bullets_max=3),), skills_max=5)


def test_read_cv_layout_refuses_a_malformed_layout_listing_every_problem(
        store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    seed(store_name, store, layout=layout_yaml(
        [{"heading": "Example Alpha", "from": "13/2020", "to": "soon"}]))
    with pytest.raises(LayoutError) as exc:
        store.read_cv_layout()
    assert len(exc.value.problems) == 2


def test_read_cv_layout_reports_invalid_yaml_as_malformed(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    seed(store_name, store, layout="---\nroles: [unclosed\n---\n")
    with pytest.raises(LayoutError, match="not valid YAML") as exc:
        store.read_cv_layout()
    # It says WHERE, and never repeats the note's text.
    assert "frontmatter line" in str(exc.value) and "unclosed" not in str(exc.value)


def test_read_cv_layout_refuses_non_mapping_frontmatter(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    seed(store_name, store, layout="---\n- a list\n---\n")
    with pytest.raises(LayoutError, match="frontmatter"):
        store.read_cv_layout()
```

In `tests/conformance/seeds.py`, give `_seed_vault` a `layout=None` keyword:

```python
    if layout is not None:
        store.write_document(CV_LAYOUT_RELPATH, layout)
```

(import `CV_LAYOUT_RELPATH` from `sluice.core.protocols` beside `CANDIDATE_PROFILE_RELPATH`).

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_layout_store.py tests/conformance/test_store_contract.py -q -k "layout"`
Expected: FAIL with `AttributeError: 'Vault' object has no attribute 'read_cv_layout'`.

- [ ] **Step 3: Implement**

In `sluice/core/protocols.py`, add to `class Store` (beside `read_candidate_profile`):

```python
    def read_cv_layout(self) -> "CvLayout | None":
        """The user's CV Layout (CV_LAYOUT_RELPATH): which roles a CV shows and how.

        MUST-support, like read_candidate_profile. Three outcomes, kept apart: None when
        the note is absent; LayoutError when it is malformed (any core/layout.py rule, or
        YAML the store cannot read as YAML); OSError/ValueError when it cannot be read at
        all (a symlink out of the store, a permission error, a non-UTF-8 file). An
        unreadable note must never read as absent (#242)."""
        ...
```

In `sluice/core/vault.py`, add `from sluice.core.layout import parse_layout` to the module imports and `CV_LAYOUT_RELPATH`/`LayoutError` to its `sluice.core.protocols` import, then add beside `read_candidate_profile`:

```python
    def read_cv_layout(self):
        """See Store.read_cv_layout."""
        path = self._doc_path(CV_LAYOUT_RELPATH)
        # Refused like a symlinked evidence entry: the layout decides what reaches every CV,
        # so it is read only from inside the vault the user named.
        if os.path.islink(path):
            raise OSError(f"{CV_LAYOUT_RELPATH} is a symlink -- sluice reads it only from "
                          "inside your vault")
        try:
            text = _read(path)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return None
        if yaml is None:
            raise LayoutError(["PyYAML is not installed, and the CV Layout is YAML -- it is "
                               "a declared dependency of job-sluice; reinstall the package"])
        inner, _body = _split_frontmatter(text)
        try:
            mapping = yaml.safe_load(inner) if inner and inner.strip() else None
        except (yaml.YAMLError, RecursionError) as e:
            # Where, never what: PyYAML's own message quotes the offending line, and this
            # one reaches `doctor`, whose rows go to MCP clients whole. A line and column in
            # the frontmatter find the fault without repeating the user's text.
            mark = getattr(e, "problem_mark", None)
            where = (f" at frontmatter line {mark.line + 1}, column {mark.column + 1}"
                     if mark is not None else "")
            raise LayoutError([f"{CV_LAYOUT_RELPATH}: the frontmatter is not valid YAML"
                               f"{where} ({type(e).__name__})"]) from e
        return parse_layout(mapping)
```

In `tests/test_cv_engine.py`:
- `FakeVault.__init__` gains `layout=SYNTHETIC_LAYOUT` (imported from `tests.conftest`) stored as `self._layout`, and the class gains `def read_cv_layout(self): return self._layout`.
- The sweep below reaches every `Store` member the fake implements, and two of them have drifted from `Vault` (measured): `update_fields` lacks `require_status`, `require_blank`, `blank_values`, `require_unchanged` and `preserve_block_values`, and `sign_off` lacks `require_pending`. Give both the real parameter lists, keyword-only with `None` defaults. The fake HONOURS what it can mirror cheaply -- `require_status` (abstain, returning `False`, when the fresh status is not in the set), `require_blank` (abstain when a named key is non-blank) and `require_pending` (return `"nothing"` when the fresh `pending_cv` differs) -- and RAISES `NotImplementedError` naming the parameter for any other one passed a non-`None` value. A fake never accepts a guard and silently ignores it: that is the shape that made a real guard look tested. Do not exclude members from the sweep by name.
- Replace the hand-listed tuple in `test_the_fake_vault_conforms_to_the_real_store_signature` with the protocol-derived set, keeping a floor so the sweep cannot pass by checking nothing:

```python
    from sluice.core.protocols import Store
    shared = sorted(n for n in dir(Store)
                    if not n.startswith("_") and callable(getattr(Store, n))
                    and hasattr(FakeVault, n))
    # Scope floor: the members the CV path drives must be among those checked.
    assert {"read_evidence", "read_candidate_profile", "read_cv_layout",
            "set_tailored_cv", "read_leads"} <= set(shared), shared
    for name in shared:
        real_sig = inspect.signature(getattr(Vault, name))
        fake_sig = inspect.signature(getattr(FakeVault, name))
        assert list(fake_sig.parameters) == list(real_sig.parameters), (
            f"FakeVault.{name}{fake_sig} has drifted from Vault.{name}{real_sig}. "
            f"A fake that outlives the contract it fakes hides real breakage.")
```

In `tests/test_mcpserver.py`, add `"read_cv_layout"` to `_STORE_READ_METHODS` and to the comment above it that names the read-only members. `_STORE_WRITE_METHODS` is derived as every `Store` member MINUS that hand list, so a read left off it is swept as a write, and a read the protocol later drops (Task 20 drops `read_baseline`) lingers there unnoticed. Add, below the `_STORE_WRITE_METHODS` assertion, the row that holds the list to the protocol in both directions:

```python
def test_store_read_methods_are_exactly_the_protocols_reads():
    # _STORE_WRITE_METHODS subtracts this hand list from every Store member, so a read
    # missing here is swept as a WRITE, and one the protocol dropped lingers here. Checked
    # against the protocol itself, in both directions.
    reads = {n for n, m in vars(Store).items() if n.startswith("read_") and callable(m)}
    assert reads == _STORE_READ_METHODS
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_core_layout_store.py tests/conformance -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py tests/conftest.py tests/conformance tests/test_core_layout_store.py tests/test_cv_engine.py tests/test_mcpserver.py
git commit -F - <<'EOF'
feat(store): read the CV Layout note

Store.read_cv_layout returns None when the note is absent, raises
LayoutError when it is malformed (including YAML it cannot parse) and lets
an unreadable note fail as unreadable, never as absent. The engine's fake
store signature check now derives its roster from the Store protocol.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 7: `EvidenceKind` flags, the `Label:` field, legacy-field presence

Spec §4.2 (legacy fields), §4.4 (`Label:`, D12, D13). Additive: the experience kind keeps `Skills` in its fields until Task 18; this task adds the mechanisms and the skills kind's new field and flag. The user-facing messages that say what verifying a skill BUYS change in Task 18, the commit that makes them true -- until then no CV lists a skill note's name.

**Files:**
- Modify: `sluice/core/protocols.py` (`EvidenceKind` gains `legacy_fields` and `names_in_skills_pool`; the skills kind gains `Label` and the flag; delete the attribute COUNT from `EvidenceKind`'s docstring)
- Modify: `sluice/core/vault.py` (`_evidence_entries` materialises `"legacy"`)
- Modify: `sluice/core/app.py` (`Sluice.add_evidence` fills a blank `Label` from the typed name), `sluice/cv/bundle.py` (`_framing_lines` heads a skill by its CV name), `docs/USAGE.md` (`skills add --label`)
- Modify: `sluice/core/protocols.py` (`Store.read_evidence`'s docstring lists `legacy` among the keys every entry carries)
- Test: `tests/test_evidence_kinds.py`, `tests/test_evidence_cli.py`, `tests/test_evidence_store.py`; and the existing rows a fifth skills field reaches (measured by plan review, each ported keeping its assertion): `tests/test_cv_bundle.py::test_the_framing_reads_the_kinds_own_declared_fields`, `tests/test_docs_claims.py::test_every_evidence_add_flag_is_documented[skills]`, `tests/test_evidence_cli.py`'s wizard rows (`test_the_wizard_carries_on_past_an_entry_it_could_not_capture`, `test_the_wizard_captures_an_optional_body_for_every_kind`), `tests/functional/test_init.py::test_the_evidence_wizard_runs_through_cmd_init_and_seeds_the_correct_vault`, `tests/test_mcpserver.py::test_list_evidence_shapes_entries_to_title_verified_fields_only`, `tests/functional/test_mcp_contract.py::test_call_tool_round_trips_list_evidence_with_real_arguments`, `tests/test_evidence_store.py::test_read_evidence_returns_the_eight_key_floor_plus_fields_for_every_kind` and `::test_every_shipped_kind_passes_its_own_construction_guard`

**Interfaces:**
- Produces:
  - `EvidenceKind.legacy_fields: tuple = ()` — frontmatter keys a kind no longer READS as data but whose PRESENCE stays visible; must not overlap `fields` (refused at construction)
  - `EvidenceKind.names_in_skills_pool: bool = False` — a verified entry's CV name may be listed in a CV's SKILLS section (true for `skills` only). Governs the NAMES route only: an experience entry's `Tools:` reach the pool through `tool_items` regardless
  - every entry dict from `read_evidence` gains `"legacy": {key: bool}` (non-empty value present), never inside `"fields"`
  - `EVIDENCE_KINDS["skills"].fields` ends with `"Label"`, so `skills add` gains `--label`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_evidence_kinds.py`:

```python
import pytest

from sluice.core.protocols import EvidenceKind


def test_only_the_skills_kind_lists_its_names_in_the_skills_pool():
    assert {k for k, s in EVIDENCE_KINDS.items() if s.names_in_skills_pool} == {"skills"}


def test_the_skills_kind_declares_a_label():
    assert "Label" in EVIDENCE_KINDS["skills"].fields


def test_a_legacy_field_may_not_also_be_a_declared_field():
    with pytest.raises(ValueError, match="legacy"):
        EvidenceKind("Job Applications/Example", ("Company",), legacy_fields=("Company",))
```

Add to `tests/test_evidence_store.py`:

```python
def test_a_legacy_field_reports_presence_outside_fields(tmp_path, monkeypatch):
    import dataclasses
    from sluice.core import protocols
    from sluice.core.vault import Vault
    spec = dataclasses.replace(protocols.EVIDENCE_KINDS["experience"],
                               legacy_fields=("Retired",))
    monkeypatch.setitem(protocols.EVIDENCE_KINDS, "experience", spec)
    v = Vault(str(tmp_path))
    v.write_document("Job Applications/Experience Library/one.md",
                     "---\nCompany: Example Alpha\nRetired: anything\nverified: 2026-01-01\n---\nBody\n")
    v.write_document("Job Applications/Experience Library/two.md",
                     "---\nCompany: Example Alpha\nRetired: \nverified: 2026-01-01\n---\nBody\n")
    by_title = {e["title"]: e for e in v.read_evidence("experience")}
    assert by_title["one"]["legacy"] == {"Retired": True}
    assert by_title["two"]["legacy"] == {"Retired": False}
    assert "Retired" not in by_title["one"]["fields"]
```

In `tests/test_evidence_cli.py`, update `test_add_writes_the_per_field_flag_values_into_frontmatter`'s expected `fields` dict to include `"Label": "widget"` (the name typed with `--name` is now kept in `Label`), and add:

```python
def test_skills_add_keeps_the_typed_name_in_label_when_the_filename_is_a_slug(
        tmp_path, monkeypatch):
    # D13: a skill's CV name must survive the filename slug.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "Examplelang#"]) == 0
    entries = Vault(str(tmp_path)).read_pending_evidence("skills")
    assert entries[0]["fields"]["Label"] == "Examplelang#"


def test_an_explicit_label_wins_over_the_name(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "widget", "--label", "Example Widget"]) == 0
    entries = Vault(str(tmp_path)).read_pending_evidence("skills")
    assert entries[0]["fields"]["Label"] == "Example Widget"
```

(`main`, `Vault`, `EVIDENCE_KINDS` are already imported at the top of that file; add any that are not.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_kinds.py tests/test_evidence_cli.py tests/test_evidence_store.py -q`
Expected: FAIL (`AttributeError: … 'names_in_skills_pool'` and a missing `Label` key).

- [ ] **Step 3: Implement**

`sluice/core/protocols.py` — add two attributes to `EvidenceKind` and a check to its `__post_init__`:

```python
    # Keys this kind no longer READS as data but whose PRESENCE stays visible
    # (#364/#365/#368 spec §4.2): `Skills:` after `Tools:` replaced it, so `doctor` and `cv
    # run` can tell an upgraded vault from an unconfigured one. Materialised by the store
    # under each entry's own "legacy" key, never inside "fields", so no CLI flag, wizard
    # prompt or MCP proposal can carry one.
    legacy_fields: tuple = ()
    # A verified entry's CV NAME (its `Label:`, else its title) may be listed in a CV's
    # SKILLS section (spec D12). The NAMES route only: an experience entry's `Tools:` reach
    # the pool through core/tokens.py::tool_items whatever this flag says.
    names_in_skills_pool: bool = False
```

```python
        overlap = set(self.legacy_fields) & set(self.fields)
        if overlap:
            raise ValueError(f"legacy_fields {sorted(overlap)} are also declared fields; a key "
                             "is either read as data or retired, never both")
```

In `EVIDENCE_KINDS`, the skills kind becomes:

```python
    "skills": EvidenceKind("Job Applications/Skills Inventory",
                           ("Proficiency", "Domain", "Evidence", "Signal Value", "Label"),
                           read_by_composer=True, names_in_skills_pool=True,
                           floor_map=(("best_for", "Domain"),)),
```

and in `EvidenceKind`'s docstring, delete the sentence that counts its attributes ("THREE of the five attributes …") — state which attributes bind a store without a number (the repo's rule for counts in prose).

`sluice/core/vault.py::_evidence_entries` — add beside `"fields"`:

```python
                # Presence only, never the value: a retired key is not data (spec §4.2).
                "legacy": {k: bool(str(fm.get(k, "") or "").strip())
                           for k in spec.legacy_fields},
```

`sluice/core/app.py` — in `Sluice.add_evidence`, before the store call (it is the one facade the CLI, the `init` wizard and the MCP proposal tool share, so the rule has one home):

```python
        # D13: a skill note's filename is a slug (`C#` becomes `c.md`), so the name the user
        # typed is kept in Label:, which is what a CV lists. An explicit Label wins.
        spec = EVIDENCE_KINDS[kind]
        if "Label" in spec.fields and not str(fields.get("Label") or "").strip():
            fields = {**fields, "Label": name}
```

The `init` wizard asks for `Label` like any declared field (its prompt loop is keyed on `spec.fields`); a blank answer reaches the line above and takes the typed name.

`sluice/cv/bundle.py::_framing_lines` heads each skill line with its CV name -- `Label` when set, else the title -- so the composer sees the spelling the pool offers and every declared field still reaches the framing (`test_the_framing_reads_the_kinds_own_declared_fields` pins that; its expected field set gains `Label`). `docs/USAGE.md` documents `skills add --label` beside the other per-field flags.

`Store.read_evidence`'s docstring adds `legacy` to the keys every entry carries -- `{key: bool}` per retired field, presence only, never the value -- because consumers read it with `.get()`, so a second store that omitted it would never be caught and the attribution warning would simply never fire. The conformance row (Task 18) asserts the key is PRESENT on every entry, not only that its values are right.

- [ ] **Step 4: Run the touched tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_evidence_kinds.py tests/test_evidence_cli.py tests/test_evidence_store.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py sluice/core/app.py sluice/cv/bundle.py docs/USAGE.md tests
git commit -F - <<'EOF'
feat(evidence): skill notes keep their CV name in Label:

skills add now keeps the name the user typed in a Label: field, because
the note's filename is a slug. Evidence kinds gain a flag for kinds whose
verified names a CV may list, and can declare retired fields whose
presence the store still reports, outside the data fields.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 8: `cv/reply.py` — find the JSON reply and check its shape

Spec §5.2, §5.3. Pure. A shape problem becomes a `REPLY:` finding the engine feeds to the single retry; nothing here raises.

**Files:**
- Create: `sluice/cv/reply.py`
- Test: `tests/test_cv_reply.py`

**Interfaces:**
- Consumes: `SECTION_HEADINGS` (Task 4), `core/safeout.py::is_control`.
- Produces:
  - `@dataclass(frozen=True) Bullet(text: str, cites: tuple)`
  - `@dataclass(frozen=True) Reply(profile: str, roles: dict, skills: tuple, skills_malformed: bool = False)` — `roles` maps a slot id (the PROMPT's spelling, e.g. `"R1"`) to a tuple of `Bullet`
  - `PLACEHOLDERS = ("<profile>", "<slot>", "<bullet>", "<id>", "<skill from the list>")`
  - `extract_json(text: str) -> dict | list[str]` — the reply object, or `REPLY:` findings
  - `parse_reply(obj, slot_ids) -> Reply | list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_reply.py`:

```python
"""cv/reply.py: finding the composer's JSON reply and checking its shape (spec §5.2-§5.3)."""
import json

import pytest

from sluice.cv.reply import Bullet, Reply, extract_json, parse_reply

SLOTS = ("R1", "R2")
GOOD = {"profile": "I build reliable systems.",
        "roles": {"R1": [{"text": "Shipped the platform", "cites": ["EF1"]}]},
        "skills": ["Example Query"]}


def _reply_text(obj=GOOD):
    return json.dumps(obj)


def _findings(obj):
    out = parse_reply(obj, SLOTS)
    assert isinstance(out, list), out
    return out


# --- extract_json -----------------------------------------------------------------------

def test_a_bare_object_is_extracted():
    assert extract_json(_reply_text()) == GOOD


def test_a_fenced_reply_in_the_real_captured_shape_is_extracted():
    # Backends put a newline after the opening fence; raw_decode does not skip it.
    assert extract_json("```json\n" + _reply_text() + "\n```") == GOOD


def test_chat_around_the_reply_is_ignored():
    assert extract_json("Here you go:\n" + _reply_text() + "\nHope that helps!") == GOOD


def test_a_decodable_brace_in_chat_before_a_fenced_reply_does_not_win():
    # The separating row: only the fenced-first, profile-and-roles rule passes it.
    text = "Sure {} here is the CV:\n```json\n" + _reply_text() + "\n```"
    assert extract_json(text) == GOOD


def test_an_echoed_shape_before_a_fenced_reply_does_not_win():
    # The fragment carries profile AND roles, so the profile-and-roles preference cannot
    # choose between it and the reply: only the fenced-first rule picks the fenced reply.
    # Task 24's witness 11 deletes that rule and must turn THIS row red.
    echo = '{"profile": "<profile>", "roles": {}}'
    text = "You asked for " + echo + " so:\n```json\n" + _reply_text() + "\n```"
    assert extract_json(text) == GOOD


def test_a_json_object_in_chat_before_the_reply_does_not_win():
    # Review Focus 3: the model echoes a JSON-looking fragment of the job ad first.
    text = 'The ad said {"team": "platform"} so:\n' + _reply_text()
    assert extract_json(text) == GOOD


def test_a_top_level_array_is_a_finding():
    assert extract_json(json.dumps([GOOD])) == [
        "REPLY: the reply must be one JSON object, not a list"]


def test_a_duplicate_key_is_reported_by_name():
    text = '{"profile": "a", "profile": "b", "roles": {}}'
    assert extract_json(text) == ["REPLY: duplicate key 'profile' -- give each key once"]


def test_no_json_at_all_is_a_finding():
    assert extract_json("I could not write the CV.") == [
        "REPLY: no JSON object in the reply -- reply with one JSON object and nothing else"]


# --- parse_reply ------------------------------------------------------------------------

def test_a_good_reply_parses():
    assert parse_reply(GOOD, SLOTS) == Reply(
        profile="I build reliable systems.",
        roles={"R1": (Bullet("Shipped the platform", ("EF1",)),)},
        skills=("Example Query",))


def test_slot_ids_match_case_insensitively():
    # Review Focus 1: a model that writes "r1" for R1.
    reply = parse_reply({**GOOD, "roles": {"r1": GOOD["roles"]["R1"]}}, SLOTS)
    assert set(reply.roles) == {"R1"}


@pytest.mark.parametrize("obj", [
    {"profile": "x"},                                         # roles absent
    {"profile": "x", "Roles": {}},                            # misspelt at the top level
    {"cv": {"profile": "x", "roles": {}}},                    # nested under another key
])
def test_roles_missing_or_misplaced_is_a_finding(obj):
    assert any('"roles"' in f for f in _findings(obj))


def test_present_but_empty_roles_parses_and_is_left_to_the_selection_rule():
    assert parse_reply({"profile": "x", "roles": {}}, SLOTS).roles == {}


def test_a_missing_or_blank_profile_is_a_finding():
    assert "REPLY: profile missing or empty" in _findings({"roles": {}})[0]
    assert "REPLY: profile missing or empty" in _findings({"profile": " ", "roles": {}})[0]


def test_an_unknown_slot_is_a_finding():
    assert _findings({**GOOD, "roles": {"R9": []}}) == [
        "REPLY: unknown slot 'R9' -- use only the slot ids given"]


@pytest.mark.parametrize("bullets,expected", [
    ("not a list", "REPLY: R1 must be a list of bullets"),
    (["not an object"], 'REPLY: R1 bullet 1 must be an object with "text" and "cites"'),
    ([{"text": " ", "cites": ["EF1"]}], "REPLY: R1 bullet 1 has no text"),
    ([{"text": "x", "cites": "EF1"}], 'REPLY: R1 bullet 1 "cites" must be a list of entry ids'),
    ([{"text": "x", "cites": [1]}], 'REPLY: R1 bullet 1 "cites" must be a list of entry ids'),
])
def test_malformed_bullets_are_findings(bullets, expected):
    assert _findings({**GOOD, "roles": {"R1": bullets}}) == [expected]


@pytest.mark.parametrize("text", ["Cut costs [40%] in a year", "Shipped it [EF1]"])
def test_a_bracket_in_bullet_text_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        'REPLY: R1 bullet 1 contains a bracket -- put entry ids in "cites", never in the text']


def test_a_bracket_in_the_profile_is_a_finding():
    assert _findings({**GOOD, "profile": "Grew [500] users."}) == [
        'REPLY: profile contains a bracket -- put entry ids in "cites", never in the text']


@pytest.mark.parametrize("text", ["line one\nline two", "a\u2028b", "a\x1bb"])
def test_a_line_break_or_control_character_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 contains a line break or control character -- write it as one line"]


@pytest.mark.parametrize("text", ["Grew to 2\u200b5 users", "a\u200db", "a\u2060b",
                                  "a\u202eb", "a\u00adb"])
def test_an_invisible_format_character_is_a_finding(text):
    assert _findings({**GOOD, "roles": {"R1": [{"text": text, "cites": ["EF1"]}]}}) == [
        "REPLY: R1 bullet 1 contains an invisible formatting character -- write plain text"]


def test_an_invisible_format_character_in_the_profile_is_a_finding():
    assert _findings({**GOOD, "profile": "I grew it to 2\u200b5."}) == [
        "REPLY: profile contains an invisible formatting character -- write plain text"]


def test_a_text_equal_to_a_section_heading_is_a_finding():
    assert _findings({**GOOD, "profile": "Work Experience"}) == [
        "REPLY: profile is a section heading, not content"]


@pytest.mark.parametrize("where", ["profile", "bullet", "skill"])
def test_an_echoed_placeholder_is_a_finding(where):
    obj = json.loads(json.dumps(GOOD))
    if where == "profile":
        obj["profile"] = "<profile>"
    elif where == "bullet":
        obj["roles"]["R1"][0]["text"] = "<bullet>"
    else:
        obj["skills"] = ["<skill from the list>"]
    assert any("example placeholder" in f for f in _findings(obj))


def test_a_malformed_skills_value_is_flagged_never_refused():
    reply = parse_reply({**GOOD, "skills": "Example Query"}, SLOTS)
    assert (reply.skills, reply.skills_malformed) == ((), True)


def test_missing_skills_means_none():
    reply = parse_reply({k: v for k, v in GOOD.items() if k != "skills"}, SLOTS)
    assert (reply.skills, reply.skills_malformed) == ((), False)


def test_unknown_keys_are_ignored_at_the_top_level_and_inside_a_bullet():
    obj = {**GOOD, "notes": "x",
           "roles": {"R1": [{"text": "Shipped it", "cites": ["EF1"], "title": "x"}]}}
    assert isinstance(parse_reply(obj, SLOTS), Reply)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_reply.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sluice.cv.reply'`.

- [ ] **Step 3: Implement `sluice/cv/reply.py`**

```python
"""Read the composer's reply: find its JSON object and check its shape (#364/#365/#368).

Pure, and it never raises: every problem becomes a `REPLY:` finding the engine feeds to its
single retry, naming the field so the model can fix exactly that. The text this module
accepts is exactly the text that renders -- nothing downstream strips or rewrites it.
"""
import json
import re
import unicodedata
from dataclasses import dataclass

from sluice.core.protocols import SECTION_HEADINGS
from sluice.core.safeout import is_control

# The placeholders the prompt's JSON shape uses (cv/compose.py). `_prefix` can produce
# none of them, so a reply still carrying one is a backend echoing the example back.
PLACEHOLDERS = ("<profile>", "<slot>", "<bullet>", "<id>", "<skill from the list>")
_FENCE_RE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.S)
_HEADINGS = frozenset(h.casefold() for h in SECTION_HEADINGS)


@dataclass(frozen=True)
class Bullet:
    text: str
    cites: tuple


@dataclass(frozen=True)
class Reply:
    profile: str
    roles: dict           # slot id (the prompt's spelling) -> tuple[Bullet, ...]
    skills: tuple
    skills_malformed: bool = False


class _DuplicateKey(ValueError):
    def __init__(self, key):
        super().__init__(key)
        self.key = key


def _no_duplicates(pairs):
    # json.loads keeps the LAST of two equal keys silently; a reply carrying two `roles`
    # would render one of them unchecked by intent. Refuse instead.
    out = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def extract_json(text):
    """The reply's JSON object, or a list of REPLY findings.

    Tries each fenced block, stripped (a backend puts a newline after the fence, which
    raw_decode will not skip), then each `{` in the text. Prefers the first object carrying
    both `profile` and `roles`, so a stray `{}` or an echoed fragment of the job ad in the
    chat before the reply cannot beat it."""
    text = text or ""
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            if isinstance(json.loads(stripped), list):
                return ["REPLY: the reply must be one JSON object, not a list"]
        except ValueError:
            pass
    decoder = json.JSONDecoder(object_pairs_hook=_no_duplicates)
    candidates = [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    candidates += [text[i:] for i, c in enumerate(text) if c == "{"]
    fallback = None
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            obj, _end = decoder.raw_decode(candidate)
        except _DuplicateKey as e:
            return [f"REPLY: duplicate key {e.key!r} -- give each key once"]
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if "profile" in obj and "roles" in obj:
            return obj
        if fallback is None and ("profile" in obj or "roles" in obj):
            fallback = obj
    if fallback is not None:
        return fallback
    return ["REPLY: no JSON object in the reply -- reply with one JSON object and nothing else"]


def _text_findings(where, text):
    if "[" in text or "]" in text:
        return [f'REPLY: {where} contains a bracket -- put entry ids in "cites", never in '
                "the text"]
    if any(is_control(c) for c in text):
        return [f"REPLY: {where} contains a line break or control character -- write it as "
                "one line"]
    # Invisible format characters (zero-width space, joiners, bidi overrides, soft hyphen)
    # pass is_control, and one between two digits splits a figure the PDF shows whole:
    # "2<ZWSP>5" renders as 25 while core/tokens.py::figures reads 2 and 5.
    if any(unicodedata.category(c) == "Cf" for c in text):
        return [f"REPLY: {where} contains an invisible formatting character -- write plain "
                "text"]
    if text.strip().casefold() in _HEADINGS:
        return [f"REPLY: {where} is a section heading, not content"]
    return []


def _placeholder_findings(obj):
    found = []

    def walk(value):
        if isinstance(value, str):
            found.extend(p for p in PLACEHOLDERS if p in value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(obj)
    return [f"REPLY: the reply still carries the example placeholder {p} -- replace it "
            "with real content" for p in sorted(set(found))]


def parse_reply(obj, slot_ids):
    """A typed Reply, or REPLY findings naming each problem's field.

    `skills` alone is never refused: a malformed list is flagged and dropped, because the
    SKILLS section is framing-grade and framing never costs a lead (spec §5.2)."""
    if not isinstance(obj, dict):
        return ["REPLY: the reply must be one JSON object"]
    findings = _placeholder_findings(obj)
    profile = obj.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        findings.append("REPLY: profile missing or empty -- give a 2 to 3 sentence profile")
        profile = ""
    else:
        findings += _text_findings("profile", profile)
    # Matched case-insensitively: a model writing "r1" for R1 has made no real mistake.
    by_fold = {s.casefold(): s for s in slot_ids}
    roles = {}
    raw_roles = obj.get("roles")
    if not isinstance(raw_roles, dict):
        findings.append('REPLY: "roles" missing or not an object -- map each slot id to its '
                        "bullets")
    else:
        for key, bullets in raw_roles.items():
            slot = by_fold.get(str(key).strip().casefold())
            if slot is None:
                findings.append(f"REPLY: unknown slot {key!r} -- use only the slot ids given")
                continue
            if not isinstance(bullets, list):
                findings.append(f"REPLY: {slot} must be a list of bullets")
                continue
            kept = []
            for n, b in enumerate(bullets, 1):
                where = f"{slot} bullet {n}"
                if not isinstance(b, dict):
                    findings.append(f'REPLY: {where} must be an object with "text" and "cites"')
                    continue
                text, cites = b.get("text"), b.get("cites")
                if not isinstance(text, str) or not text.strip():
                    findings.append(f"REPLY: {where} has no text")
                    continue
                if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
                    findings.append(f'REPLY: {where} "cites" must be a list of entry ids')
                    continue
                bad = _text_findings(where, text)
                if bad:
                    findings += bad
                    continue
                kept.append(Bullet(text.strip(), tuple(c.strip() for c in cites)))
            roles[slot] = roles.get(slot, ()) + tuple(kept)
    raw_skills = obj.get("skills")
    skills, malformed = (), False
    if raw_skills is not None:
        if isinstance(raw_skills, list) and all(isinstance(s, str) for s in raw_skills):
            skills = tuple(s.strip() for s in raw_skills if s.strip())
        else:
            malformed = True
    if findings:
        return findings
    return Reply(profile.strip(), roles, skills, malformed)
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_reply.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/reply.py tests/test_cv_reply.py
git commit -F - <<'EOF'
feat(cv): read the composer's JSON reply and check its shape

extract_json finds the reply object even when a backend wraps it in a
code fence or chat, and refuses a duplicate key rather than keeping one
silently. parse_reply turns every shape problem into a REPLY finding
naming its field: brackets, line breaks and echoed placeholders in text
are refused, so what is checked is exactly what renders.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 9: `cv/selection.py` — the skills pool, budgets, and the zero-bullet rule

Spec §4.4, §5.2 (zero-bullet rule over the selection), §6.0 (select step), §6.2, D10. Pure. The selection is the ONE value later stages check, retain, assemble, audit and render.

**Files:**
- Create: `sluice/cv/selection.py`
- Modify: `tests/test_fixture_name_neutrality.py` (`_REVIEWED_SKILL_VALUES` gains four spelling variants; Step 4)
- Test: `tests/test_cv_selection.py`

**Interfaces:**
- Consumes: `find_term`, `tool_items` (Task 3); `Slot` (Task 5); `Bullet`, `Reply` (Task 8).
- Produces:
  - `@dataclass(frozen=True) Selection(profile: str, roles: dict, skills: tuple, skills_dropped: tuple = (), bullets_trimmed: tuple = ())` — `roles` has a key for EVERY slot id (an empty tuple when none kept)
  - `cv_name(entry: dict) -> str` — a skill note's `Label:`, else its title
  - `pool_kinds() -> tuple[str, ...]` — the evidence kinds flagged `names_in_skills_pool`
  - `named_entries(read_evidence) -> list[dict]` — verified entries of every pool kind, read through the given `read_evidence(kind, verified_only=True)` callable (the Store method)
  - `build_pool(named, experience_entries, decoys=()) -> tuple[str, ...]` — `named` is what `named_entries` returned
  - `skills_requested(pool, skills_max) -> bool`
  - `select(reply: Reply, slots, pool, skills_max) -> Selection`
  - `zero_bullet_findings(selection: Selection, slots) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_selection.py`:

```python
"""cv/selection.py: what a reply may render (spec §4.4, §6.0, §6.2, D10)."""
from sluice.core.layout import Slot
from sluice.core.protocols import LayoutRole
from sluice.cv.reply import Bullet, Reply
from sluice.cv.selection import (build_pool, cv_name, named_entries, select,
                                 skills_requested, zero_bullet_findings)


def _slot(sid, heading, budget=None, eligible=("EA1",), bullets_max="same"):
    role = LayoutRole(heading, "01/2020", "present",
                      bullets_max=budget if bullets_max == "same" else bullets_max)
    return Slot(sid, role, eligible, budget)


def _bullets(n, prefix="Did"):
    return tuple(Bullet(f"{prefix} thing {i}", ("EA1",)) for i in range(1, n + 1))


def _reply(roles=None, skills=(), malformed=False):
    return Reply("I build reliable systems.", roles or {}, tuple(skills), malformed)


POOL = ("Example Query", "Examplelang", ".Examplenet")


def test_cv_name_prefers_the_label_and_falls_back_to_the_title():
    # The title is the slug `skills add` writes for the typed name.
    assert cv_name({"title": "examplelang", "fields": {"Label": "Examplelang#"}}) == "Examplelang#"
    assert cv_name({"title": "Example Query", "fields": {"Label": "  "}}) == "Example Query"


def test_the_pool_takes_inventory_names_first_then_tools_deduplicated():
    named = [{"title": "Example Query", "fields": {}}]
    experience = [{"fields": {"Tools": "example query, Examplelang"}}]
    assert build_pool(named, experience) == ("Example Query", "Examplelang")


def test_a_decoy_matching_item_never_enters_the_pool():
    experience = [{"fields": {"Tools": "Examplelang, Exampleban"}}]
    assert build_pool([], experience, decoys=("exampleban",)) == ("Examplelang",)


def test_skills_are_requested_only_with_a_pool_and_a_non_zero_cap():
    assert skills_requested(POOL, None) and skills_requested(POOL, 3)
    assert not skills_requested((), None)
    assert not skills_requested(POOL, 0)


def test_a_kept_pick_renders_in_the_pools_spelling_and_a_one_token_difference_drops():
    sel = select(_reply(skills=["example query", "Example Queries"]), (), POOL, None)
    assert sel.skills == ("Example Query",)
    assert sel.skills_dropped == ("'Example Queries': not one of your skills",)


def test_a_pick_with_a_trailing_period_still_keeps():
    # Review Focus 4: "Example Query." is the same skill, and stray whitespace is noise.
    assert select(_reply(skills=["  Example Query. "]), (), POOL, None).skills == (
        "Example Query",)


def test_off_pool_and_duplicate_picks_drop_before_the_cap_is_applied():
    sel = select(_reply(skills=["Example Ghost", "Examplelang", "examplelang",
                                "Example Query"]), (), POOL, 2)
    assert sel.skills == ("Examplelang", "Example Query")
    assert sel.skills_dropped == ("'Example Ghost': not one of your skills",
                                  "'examplelang': listed twice")


def test_picks_beyond_the_cap_drop_and_are_reported():
    sel = select(_reply(skills=list(POOL)), (), POOL, 2)
    assert sel.skills == ("Example Query", "Examplelang")
    assert sel.skills_dropped == ("'.Examplenet': over skills_max (2)",)


def test_a_malformed_skills_list_drops_everything_and_says_so():
    sel = select(_reply(malformed=True), (), POOL, None)
    assert (sel.skills, sel.skills_dropped) == (
        (), ("the skills list was not a list of strings, so none was used",))


def test_skills_max_zero_requests_none_and_reports_nothing():
    sel = select(_reply(skills=["Example Query"]), (), POOL, 0)
    assert (sel.skills, sel.skills_dropped) == ((), ())


def test_an_absent_cap_keeps_every_bullet_and_reports_nothing():
    slots = (_slot("R1", "Example Alpha", budget=None),)
    sel = select(_reply({"R1": _bullets(7)}), slots, (), None)
    assert (len(sel.roles["R1"]), sel.bullets_trimmed) == (7, ())


def test_a_budget_equal_to_the_bullet_count_trims_nothing():
    slots = (_slot("R1", "Example Alpha", budget=3),)
    assert select(_reply({"R1": _bullets(3)}), slots, (), None).bullets_trimmed == ()


def test_one_bullet_over_budget_is_trimmed_keeping_the_first_n():
    slots = (_slot("R1", "Example Alpha", budget=3),)
    sel = select(_reply({"R1": _bullets(4)}), slots, (), None)
    assert [b.text for b in sel.roles["R1"]] == ["Did thing 1", "Did thing 2", "Did thing 3"]
    assert sel.bullets_trimmed == ("R1 (Example Alpha): kept 3 of 4",)


def test_a_zero_budget_keeps_nothing():
    slots = (_slot("R1", "Example Alpha", budget=0),)
    assert select(_reply({"R1": _bullets(2)}), slots, (), None).roles["R1"] == ()


def test_every_slot_has_a_key_even_when_the_reply_left_it_out():
    slots = (_slot("R1", "Example Alpha"), _slot("R2", "Example Beta"))
    assert select(_reply({"R1": _bullets(1)}), slots, (), None).roles["R2"] == ()


def test_no_bullets_in_any_capable_slot_is_a_finding():
    slots = (_slot("R1", "Example Alpha"), _slot("R2", "Example Beta", budget=0))
    sel = select(_reply({"R2": _bullets(2)}), slots, (), None)   # only in a 0-budget slot
    assert zero_bullet_findings(sel, slots) == [
        "REPLY: no bullets in any role that can carry them -- write bullets for the roles "
        "that list citable entries"]


def test_a_headings_only_layout_renders_with_no_bullets_and_no_finding():
    slots = (_slot("R1", "Example Alpha", budget=0),)
    assert zero_bullet_findings(select(_reply(), slots, (), None), slots) == []


def test_the_pool_holds_exactly_the_names_of_kinds_flagged_for_it():
    # Execution-derived sibling of the cited_by_gate test (spec §4.4, D12): offer one
    # verified entry of EVERY kind, each with a distinct sentinel name, through the reader
    # the engine uses, and ask whose names reached the pool.
    from sluice.core.protocols import EVIDENCE_KINDS

    def read_evidence(kind, verified_only=True):
        return [{"title": f"Example Zephyr {kind.title()}", "fields": {}}]

    pool = build_pool(named_entries(read_evidence), [])
    assert pool == tuple(f"Example Zephyr {k.title()}" for k, s in EVIDENCE_KINDS.items()
                         if s.names_in_skills_pool)
    assert pool, "no kind feeds the pool: the sweep would pass vacuously"


def test_the_pool_follows_the_flag_not_the_kind_name(monkeypatch):
    # With only `skills` flagged, a pool_kinds hard-wired to ("skills",) passes the row
    # above. Moving the flag to another kind separates the two; recording verified_only
    # catches a reader that would offer unverified names.
    import dataclasses

    from sluice.core import protocols
    moved = {k: dataclasses.replace(spec, names_in_skills_pool=(k == "stories"))
             for k, spec in protocols.EVIDENCE_KINDS.items()}
    monkeypatch.setattr(protocols, "EVIDENCE_KINDS", moved)
    asked = []

    def read_evidence(kind, verified_only=True):
        asked.append(verified_only)
        return [{"title": f"Example Zephyr {kind.title()}", "fields": {}}]

    assert build_pool(named_entries(read_evidence), []) == ("Example Zephyr Stories",)
    assert asked and all(asked), "the pool read unverified entries"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_selection.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sluice.cv.selection'`.

- [ ] **Step 3: Implement `sluice/cv/selection.py`**

```python
"""Choose what a reply may render: skills from the pool, bullets within budget (#364/#365/#368).

Pure. Its output is ONE Selection -- what the hard checks and the style tier inspect, what
the engine retains, assembles, audits and renders. A trimmed bullet or a dropped pick is
never checked, so it can never cost a retry or a lead (spec §2: over budget is trimmed and
reported, never refused).
"""
from dataclasses import dataclass

from sluice.core.tokens import find_term, tool_items


@dataclass(frozen=True)
class Selection:
    profile: str
    roles: dict            # slot id -> tuple[Bullet, ...], a key for EVERY slot
    skills: tuple          # kept picks, in the pool's spelling, in the model's order
    skills_dropped: tuple = ()
    bullets_trimmed: tuple = ()


def cv_name(entry):
    """A skill note's name on a CV: its Label:, else its title (D13 -- the filename of a
    note `skills add` created is a slug, so the typed name lives in Label:)."""
    label = str((entry.get("fields") or {}).get("Label") or "").strip()
    return label or str(entry.get("title") or "").strip()


def pool_kinds():
    from sluice.core.protocols import EVIDENCE_KINDS
    return tuple(k for k, spec in EVIDENCE_KINDS.items() if spec.names_in_skills_pool)


def named_entries(read_evidence):
    """Verified entries of every kind whose names a CV may list (D12), read through the
    Store's own `read_evidence`. Keyed on the flag, never on a kind's name, so the flag is
    what decides -- and the execution-derived test can catch a hard-wired kind."""
    return [e for kind in pool_kinds() for e in read_evidence(kind, verified_only=True)]


def _key(item):
    # A pick with a trailing full stop or stray whitespace is the same skill.
    return item.strip().rstrip(".").strip().casefold()


def build_pool(named, experience_entries, decoys=()):
    """The closed list a CV's SKILLS section may draw from: verified skill names first
    (so their spelling wins), then verified entries' Tools:, de-duplicated. An item a
    `fabrication_decoys` term matches never enters it: a ban beats the user's own list."""
    out, seen = [], set()
    candidates = [cv_name(e) for e in named]
    candidates += [t for e in experience_entries for t in tool_items(e)]
    for item in candidates:
        key = _key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        if any(find_term(item, d) for d in decoys or ()):
            continue
        out.append(item)
    return tuple(out)


def skills_requested(pool, skills_max):
    """Skills are asked for only when there is something to pick and a cap above zero."""
    return bool(pool) and skills_max != 0


def select(reply, slots, pool, skills_max):
    kept, dropped = [], []
    if skills_requested(pool, skills_max):
        index = {_key(p): p for p in pool}
        if reply.skills_malformed:
            dropped.append("the skills list was not a list of strings, so none was used")
        used = set()
        for pick in reply.skills:
            key = _key(pick)
            if key not in index:
                dropped.append(f"{pick!r}: not one of your skills")
            elif key in used:
                dropped.append(f"{pick!r}: listed twice")
            else:
                used.add(key)
                kept.append(index[key])
        # The cap applies AFTER off-pool and duplicate drops, so a rejected pick never
        # uses up a place the user allowed.
        if skills_max is not None and len(kept) > skills_max:
            dropped += [f"{extra!r}: over skills_max ({skills_max})" for extra in kept[skills_max:]]
            kept = kept[:skills_max]
    roles, trimmed = {}, []
    for slot in slots:
        bullets = tuple(reply.roles.get(slot.id, ()))
        keep = bullets if slot.budget is None else bullets[:slot.budget]
        if len(keep) < len(bullets):
            trimmed.append(f"{slot.id} ({slot.role.heading}): kept {len(keep)} of "
                           f"{len(bullets)}")
        roles[slot.id] = keep
    return Selection(reply.profile, roles, tuple(kept), tuple(dropped), tuple(trimmed))


def zero_bullet_findings(selection, slots):
    """A CV with no work bullets at all is refused -- unless every slot's budget is 0,
    which is a headings-only CV the user configured (D10). Over the SELECTION, so bullets
    written only into 0-budget slots do not count."""
    capable = [s for s in slots if s.budget != 0]
    if capable and not any(selection.roles.get(s.id) for s in capable):
        return ["REPLY: no bullets in any role that can carry them -- write bullets for the "
                "roles that list citable entries"]
    return []
```

- [ ] **Step 4: Review the four new skill values the roster sweep finds**

Run: `.venv/bin/python -m pytest tests/test_fixture_name_neutrality.py -q`
Expected: FAIL in `test_evidence_skill_values_are_on_the_reviewed_roster`, naming exactly `'Example Queries'`, `'Example Query.'`, `'example query'` and `'examplelang'`: the `skills=` picks this task's tests pass are a swept skill position. Each is a spelling variant of a value already on the roster, synthetic by construction. Record that call by adding them to `_REVIEWED_SKILL_VALUES` in `tests/test_fixture_name_neutrality.py`, after its existing entries:

```python
    # Invented for #364/#365 (tests/test_cv_selection.py): spelling variants of reviewed
    # values -- a plural, a trailing full stop, lower case -- that the skill-pick matcher
    # must keep or drop by its own rule. Each is a variant of a value already above.
    "Example Queries",
    "Example Query.",
    "example query",
    "examplelang",
```

If the failure names any OTHER value, stop: a fixture in this task carries a value this plan did not intend, and the fix is the fixture, never the roster.

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_selection.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/selection.py tests/test_cv_selection.py tests/test_fixture_name_neutrality.py
git commit -F - <<'EOF'
feat(cv): select what a structured reply may render

Skills are kept only from the closed pool of verified skill names and
entry tools, in the pool's spelling; off-list picks, duplicates and picks
beyond skills_max are dropped and reported. Bullets beyond a role's budget
are trimmed, keeping the first N. A reply with no bullets in any role that
can carry them is a REPLY finding.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 10: `cv/document.py` — assemble the CV and write its text forms

Spec §7.1, §7.3, §6.4 (`audit_text`). Pure.

**Files:**
- Create: `sluice/cv/document.py`
- Test: `tests/test_cv_document.py`

**Interfaces:**
- Consumes: `CvDocument`, `Role`, `SECTION_HEADINGS`, `CvLayout`, `LayoutRole` (Task 4); `Slot` (Task 5); `Bullet` (Task 8); `Selection` (Task 9); `core/candidate.py::full_name`, `contact_block`; `tests.test_cv_script_golden.CANONICAL_CV`, `GOLDEN` (Task 2).
- Produces:
  - `SECTION_HEADINGS` (re-exported from `core/protocols.py`)
  - `@dataclass(frozen=True) AssembledCv(document: CvDocument, cites: tuple)` — `cites[r][b]` is the tuple of entry ids behind bullet `b` of work role `r`
  - `format_dates(role: LayoutRole) -> str` — `MM/YYYY–MM/YYYY` or `MM/YYYY–present`, en dash
  - `assemble(layout, slots, selection, candidate) -> AssembledCv`
  - `to_text(document: CvDocument, *, cites=None) -> str`
  - `audit_text(selection, slots) -> str`
  - `model_lines(selection, slots) -> list[tuple[int, str]]` — the profile and every kept bullet, numbered, for the style tier

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_document.py`:

```python
"""cv/document.py: the CV sluice builds, and its text forms (spec §7.1, §7.3, §6.4)."""
from sluice.core.layout import Slot
from sluice.core.protocols import CandidateProfile, CvLayout, LayoutRole
from sluice.cv.document import assemble, audit_text, model_lines, to_text
from sluice.cv.reply import Bullet, Reply
from sluice.cv.selection import Selection, select
from tests.test_cv_script_golden import CANONICAL_CV, GOLDEN

CANDIDATE = CandidateProfile(forenames="Jane", surname="Roe", mobile="+1 555 0100")
SYSTEMS = LayoutRole("Example Systems", "02/2023", "present", location="Example Location A",
                     title="SYNTHETIC-TITLE-1")
ANALYTICS = LayoutRole("Example Analytics", "06/2020", "01/2023",
                       location="Example Location B", title="SYNTHETIC-TITLE-2")
LAYOUT = CvLayout(roles=(SYSTEMS, ANALYTICS), certificates=("Example Scrum Master",),
                  education=("Example University, 09/2010–07/2014 | BSc Example",))
SLOTS = (Slot("R1", SYSTEMS, ("EF1",), None), Slot("R2", ANALYTICS, ("EF1", "EF2"), None))
SELECTION = Selection(
    profile="I build reliable systems.",
    roles={"R1": (Bullet("Shipped the platform", ("EF1",)),),
           "R2": (Bullet("Grew team from 3 to 8", ("EF1", "EF2")),)},
    skills=("Example Query",))


def _assembled(layout=LAYOUT, slots=SLOTS, selection=SELECTION):
    return assemble(layout, slots, selection, CANDIDATE)


def test_to_text_reproduces_what_a_script_received_from_the_old_pipeline():
    assert to_text(_assembled().document) == GOLDEN


def test_to_text_with_cites_is_the_cited_canonical_form():
    a = _assembled()
    assert to_text(a.document, cites=a.cites) == CANONICAL_CV


def test_the_document_takes_its_structure_from_the_layout_and_the_candidate():
    doc = _assembled().document
    assert doc.name == "JANE ROE"
    assert doc.contact == "+1 555 0100"
    assert [(r.company, r.dates, r.location, r.title) for r in doc.work] == [
        ("Example Systems", "02/2023–present", "Example Location A", "SYNTHETIC-TITLE-1"),
        ("Example Analytics", "06/2020–01/2023", "Example Location B", "SYNTHETIC-TITLE-2")]
    assert doc.certificates == ["Example Scrum Master"]
    assert doc.skills == ["Example Query"]


def test_no_document_string_carries_a_citation():
    """Spec §12.1: cites live in AssembledCv.cites, `to_text(..., cites=True)` and the audit
    excerpt -- never in the document a renderer receives. Checked with cv/render.py's own
    pattern, so a renderer that strips citations finds nothing to strip."""
    from sluice.cv.render import _CITE_RE
    a = _assembled()
    assert any(c for role in a.cites for c in role), "premise: the selection carries cites"
    doc = a.document
    strings = [doc.name, doc.contact, doc.profile, *doc.skills, *doc.certificates,
               *doc.education]
    for role in doc.work:
        strings += [role.company, role.dates, role.location, role.title, *role.bullets]
    assert [s for s in strings if _CITE_RE.search(s)] == []


def test_bullets_land_under_their_own_slot_whatever_the_reply_key_order():
    reply = Reply("I build.", {"R2": (Bullet("Second role work", ("EF2",)),),
                               "R1": (Bullet("First role work", ("EF1",)),)}, ())
    a = _assembled(selection=select(reply, SLOTS, (), None))
    assert [r.bullets for r in a.document.work] == [["First role work"], ["Second role work"]]
    assert a.cites == ((("EF1",),), (("EF2",),))


def test_an_omitted_middle_slot_renders_empty_and_shifts_nothing():
    third = LayoutRole("Example Meridian", "01/2018", "05/2020")
    layout = CvLayout(roles=(SYSTEMS, ANALYTICS, third))
    slots = SLOTS + (Slot("R3", third, ("EG1",), None),)
    reply = Reply("I build.", {"R1": (Bullet("One", ("EF1",)),),
                               "R3": (Bullet("Three", ("EG1",)),)}, ())
    doc = assemble(layout, slots, select(reply, slots, (), None), CANDIDATE).document
    assert [r.bullets for r in doc.work] == [["One"], [], ["Three"]]
    # The audit pairs each bullet with its OWN heading too, and skips the empty role.
    assert audit_text(select(reply, slots, (), None), slots) == (
        "PROFILE\nI build.\n\nExample Systems\n- One [EF1]\n\nExample Meridian\n- Three [EG1]\n")


def test_the_meta_line_is_always_three_positional_fields():
    untitled = LayoutRole("Example Northgate", "08/2001", "present", location="Example Location A")
    unlocated = LayoutRole("Example Beta", "01/2019", "12/2019", title="SYNTHETIC-TITLE-3")
    layout = CvLayout(roles=(untitled, unlocated))
    slots = (Slot("R1", untitled, (), 0), Slot("R2", unlocated, (), 0))
    text = to_text(assemble(layout, slots, Selection("p", {"R1": (), "R2": ()}, ()),
                            CANDIDATE).document)
    lines = text.splitlines()
    assert "08/2001–present | Example Location A | " in lines
    assert "01/2019–12/2019 |  | SYNTHETIC-TITLE-3" in lines


def test_a_role_with_no_bullets_writes_its_heading_and_meta_line_only():
    layout = CvLayout(roles=(SYSTEMS,))
    slots = (Slot("R1", SYSTEMS, (), 0),)
    text = to_text(assemble(layout, slots, Selection("p", {"R1": ()}, ()), CANDIDATE).document)
    block = text.split("WORK EXPERIENCE\n\n", 1)[1]
    assert block.startswith("Example Systems\n02/2023–present | Example Location A | "
                            "SYNTHETIC-TITLE-1\n\n")


def test_no_section_heading_is_written_for_an_empty_section():
    layout = CvLayout(roles=(SYSTEMS,))
    text = to_text(assemble(layout, (SLOTS[0],), Selection("p", {"R1": ()}, ()),
                            CANDIDATE).document)
    for heading in ("CERTIFICATES", "EDUCATION", "SKILLS"):
        assert heading not in text.splitlines()


def test_the_audit_sees_the_model_text_with_headings_and_cites_and_nothing_else():
    text = audit_text(SELECTION, SLOTS)
    assert text == ("PROFILE\nI build reliable systems.\n\nExample Systems\n"
                    "- Shipped the platform [EF1]\n\nExample Analytics\n"
                    "- Grew team from 3 to 8 [EF1] [EF2]\n")
    for vault_only in ("02/2023", "Example Location A", "SYNTHETIC-TITLE-1",
                       "Example Scrum Master", "Example Query", "JANE ROE"):
        assert vault_only not in text


def test_model_lines_are_the_profile_then_every_kept_bullet_in_slot_order():
    assert model_lines(SELECTION, SLOTS) == [
        (1, "I build reliable systems."), (2, "Shipped the platform"),
        (3, "Grew team from 3 to 8")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_document.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sluice.cv.document'`.

- [ ] **Step 3: Implement `sluice/cv/document.py`**

```python
"""Build the CV document from the vault and a selection, and write its text forms
(#364/#365/#368 spec §7).

Pure. Every vault-sourced field (name, contact, headings, dates, locations, titles,
certificates, education) comes from the CV Layout and the Candidate Profile; the model
supplies only the profile, the bullets and its skill picks -- already checked, and kept in
the pool's spelling, by the time they reach here.
"""
from dataclasses import dataclass

from sluice.core.candidate import contact_block, full_name
from sluice.core.protocols import SECTION_HEADINGS, CvDocument, Role

__all__ = ["SECTION_HEADINGS", "AssembledCv", "assemble", "audit_text", "format_dates",
           "model_lines", "to_text"]


@dataclass(frozen=True)
class AssembledCv:
    document: CvDocument
    cites: tuple           # cites[r][b]: entry ids behind bullet b of work role r


def format_dates(role):
    return f"{role.start}–{role.end}"


def assemble(layout, slots, selection, candidate):
    """One Role per layout role, in layout order, each filled from ITS OWN slot by id --
    never by zipping the reply's slots against the layout, whose key order the model
    chooses (a zip would put one employer's work under another's heading)."""
    work, cites = [], []
    for slot in slots:
        bullets = selection.roles.get(slot.id, ())
        work.append(Role(company=slot.role.heading, dates=format_dates(slot.role),
                         location=slot.role.location, title=slot.role.title,
                         bullets=[b.text for b in bullets]))
        cites.append(tuple(b.cites for b in bullets))
    document = CvDocument(
        # Upper-cased, keeping today's output: the composer was asked for the name in
        # capitals and the old parser kept that line as the PDF headline.
        name=full_name(candidate).upper(), contact=contact_block(candidate),
        profile=selection.profile, work=work, skills=list(selection.skills),
        certificates=list(layout.certificates), education=list(layout.education))
    return AssembledCv(document, tuple(cites))


def to_text(document, *, cites=None):
    """The canonical CV text: what a `script` renderer receives (without cites) and what
    cv.rendered.md records (with them). The meta line is POSITIONAL -- always
    `dates | location | title`, an empty field written as empty -- so a script reading
    fields by position never reads a location as a title."""
    lines = [ln for ln in document.contact.splitlines() if ln.strip()]
    lines += ["", document.name, "", SECTION_HEADINGS[0], document.profile, "",
              SECTION_HEADINGS[1], ""]
    for r, role in enumerate(document.work):
        lines += [role.company, f"{role.dates} | {role.location} | {role.title}"]
        for b, text in enumerate(role.bullets):
            suffix = "".join(f" [{c}]" for c in cites[r][b]) if cites is not None else ""
            lines.append(f"- {text}{suffix}")
        lines.append("")
    for heading, items in ((SECTION_HEADINGS[2], document.certificates),
                           (SECTION_HEADINGS[3], document.education),
                           (SECTION_HEADINGS[4], document.skills)):
        if items:
            lines += [heading, *[f"- {item}" for item in items], ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def audit_text(selection, slots):
    """What the advisory audit reads: the text the MODEL wrote, each bullet under its role
    heading with its cites. Never a date, location, title, certificate, education line or
    skill: those are the user's own vault data, the auditor has no truth for them, and
    auditing them would hold almost every CV (spec §6.4)."""
    lines = [SECTION_HEADINGS[0], selection.profile]
    for slot in slots:
        bullets = selection.roles.get(slot.id, ())
        if bullets:
            lines += ["", slot.role.heading]
            lines += [f"- {b.text}" + "".join(f" [{c}]" for c in b.cites) for b in bullets]
    return "\n".join(lines) + "\n"


def model_lines(selection, slots):
    """The profile and every kept bullet as (n, text) pairs: what the style tier reads."""
    texts = [selection.profile]
    for slot in slots:
        texts += [b.text for b in selection.roles.get(slot.id, ())]
    return list(enumerate(texts, 1))
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_document.py tests/test_cv_script_golden.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/document.py tests/test_cv_document.py
git commit -F - <<'EOF'
feat(cv): assemble the CV document from the vault and a selection

Headings, dates, locations, titles, certificates and education come from
the CV Layout; name and contact from the Candidate Profile; the profile,
bullets and skills from the checked selection, each bullet under its own
slot. to_text writes what a script renderer received from the old
pipeline, and audit_text gives the advisory audit only the model's text.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 11: The hard checks, over a selection

Spec §6.1. Added to `cv/validate.py` BESIDE today's text `validate()`, which Task 19 deletes. Every row reads the text the MODEL wrote; vault text is never refused.

**Files:**
- Modify: `sluice/cv/validate.py` (add `EntryFacts`, `entry_facts`, `check_selection`)
- Modify: `docs/TROUBLESHOOTING.md` (the `skipped-gate` section explains the two new categories; Step 4)
- Test: `tests/test_cv_checks.py`

**Interfaces:**
- Consumes: `find_term`, `figures`, `tool_items` (Task 3); `place` (Task 5); `Slot` (Task 5); `Bullet` (Task 8); `Selection` (Task 9); `cv/bundle.py::_entry_block`.
- Produces:
  - `@dataclass(frozen=True) EntryFacts(figures: frozenset, tools: tuple, text: str, placement: str, role_headings: tuple)` — per bundle entry: its normalised figures (from `_entry_block`, its own source lines), its `Tools:` items, its title and body (the text that may license a tool), and its placement
  - `entry_facts(bundle: dict, layout: CvLayout) -> dict[str, EntryFacts]` keyed by entry id
  - `check_selection(selection, slots, facts, *, decoys=()) -> list[str]` — the refused findings, profile first, then each slot's kept bullets in order. The em dash / double-hyphen rows (`cv/slop.py::check_hard`) are NOT here: the engine runs them over the same texts and reports them in `slop`, as it always has (Task 16)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_checks.py`:

```python
"""cv/validate.py::check_selection -- the hard checks over the model's text (spec §6.1).

Synthetic names throughout: Examplelang / Examplelangscript for a name inside a longer
token, Exampleco for a capitalised tool beside the lowercase word exampleco-live.
"""
from sluice.core.layout import Slot
from sluice.core.protocols import CvLayout, LayoutRole
from sluice.cv.reply import Bullet
from sluice.cv.selection import Selection
from sluice.cv.validate import EntryFacts, check_selection, entry_facts

ALPHA = LayoutRole("Example Alpha", "01/2020", "present")
BETA = LayoutRole("Example Beta", "01/2015", "12/2019")
SLOTS = (Slot("R1", ALPHA, ("EA1",), None), Slot("R2", BETA, ("EB1",), None))


def _facts(**over):
    base = {
        "EA1": EntryFacts(frozenset({"3", "8"}), (), "Grew the team\nGrew 3 to 8.",
                          "role", ("Example Alpha",)),
        # Tools passed by keyword: the fixture-name sweep reads a `tools=` value, never a
        # position (tests/test_fixture_name_neutrality.py::_is_skill_kwarg).
        "EB1": EntryFacts(frozenset({"12"}),
                          tools=("Examplelang3", "Exampleco", "examplecoach"),
                          text="Built the platform\nRan Examplelang3 on 12 nodes.",
                          placement="role", role_headings=("Example Beta",)),
    }
    base.update(over)
    return base


def _check(profile="I build reliable systems.", r1=(), r2=(), facts=None, decoys=()):
    sel = Selection(profile, {"R1": tuple(r1), "R2": tuple(r2)}, ())
    return check_selection(sel, SLOTS, facts or _facts(), decoys=decoys)


def _b(text, *cites):
    return Bullet(text, tuple(cites))


def test_a_clean_selection_has_no_findings():
    assert _check(r1=[_b("Grew the team from 3 to 8", "EA1")],
                  r2=[_b("Ran Examplelang3 on 12 nodes", "EB1")]) == []


def test_an_uncited_bullet_is_refused():
    assert _check(r1=[_b("Grew the team")]) == ["UNCITED BULLET: R1 bullet 1: Grew the team"]


def test_a_cite_to_no_entry_is_refused():
    assert _check(r1=[_b("Grew the team", "ZZ9")]) == [
        "BAD CITATION ['ZZ9']: not bundle entries - R1 bullet 1: Grew the team"]


def test_a_cite_from_another_role_is_refused_and_the_same_cite_in_its_own_role_is_clean():
    assert _check(r2=[_b("Grew the team from 3 to 8", "EA1")]) == [
        "WRONG EMPLOYER: R2 bullet 1 cites EA1, which belongs to Example Alpha - "
        "Grew the team from 3 to 8"]
    assert _check(r1=[_b("Grew the team from 3 to 8", "EA1")]) == []


def test_only_the_ineligible_cite_of_a_mixed_bullet_is_named():
    assert _check(r1=[_b("Grew the team", "EA1", "EB1")]) == [
        "WRONG EMPLOYER: R1 bullet 1 cites EB1, which belongs to Example Beta - Grew the team"]


def test_the_wrong_employer_message_says_why_an_entry_fits_nowhere():
    facts = _facts(EN1=EntryFacts(frozenset(), (), "", "unmatched", ()),
                   EZ1=EntryFacts(frozenset(), (), "", "blank", ()))
    assert _check(r1=[_b("Did a thing", "EN1"), _b("Did more", "EZ1")], facts=facts) == [
        "WRONG EMPLOYER: R1 bullet 1 cites EN1, which is not on your CV - Did a thing",
        "WRONG EMPLOYER: R1 bullet 2 cites EZ1, which has no company - Did more"]


def test_a_figure_absent_from_the_cited_entries_is_refused():
    assert _check(r1=[_b("Grew the team from 3 to 40", "EA1")]) == [
        "INVENTED METRIC ['40'] not in ['EA1']: Grew the team from 3 to 40"]


def test_a_bracketed_figure_is_still_scanned_when_it_reaches_the_check():
    # The bullet-side twin of test_a_profile_non_id_bracketed_number_is_flagged: nothing
    # here strips a bracket, so a figure inside one cannot hide (spec §6.1).
    assert _check(r1=[_b("Cut costs [40%] across the team", "EA1")]) == [
        "INVENTED METRIC ['40'] not in ['EA1']: Cut costs [40%] across the team"]


def test_figures_in_other_scripts_are_checked_and_normalised():
    assert _check(r1=[_b("Grew the team from 3 to ８", "EA1")]) == []
    assert _check(r1=[_b("Grew to ４０", "EA1")]) == [
        "INVENTED METRIC ['40'] not in ['EA1']: Grew to ４０"]


def test_an_entry_in_native_digits_licenses_the_ascii_figure():
    facts = _facts(EA1=EntryFacts(frozenset({"500"}), (), "Served ٥٠٠ users.",
                                  "role", ("Example Alpha",)))
    assert _check(r1=[_b("Served 500 users", "EA1")], facts=facts) == []


def test_entry_facts_normalise_an_entrys_native_digits():
    # Through entry_facts, so the ENTRY side's normalisation is what is under test (the row
    # above hands check_selection figures already normalised).
    from sluice.cv import bundle as B
    b = B.build_bundle([{"title": "Served users", "company": "Example Alpha", "metrics": "",
                         "body": "Served ٥٠٠ users."}], "", [], [], {"Example Alpha": "EA"})
    assert entry_facts(b, CvLayout(roles=(ALPHA,)))["EA1"].figures == frozenset({"500"})


def test_a_figure_only_an_uncited_entry_carries_is_refused():
    # The gate's core rule: a bullet's figure must come from an entry IT cites. 12 is EB1's
    # figure; this bullet cites EA1 only. Every other INVENTED METRIC row uses a figure no
    # entry carries, so a gate licensing EVERY entry's figures would pass them all.
    assert _check(r1=[_b("Grew the team from 3 to 12", "EA1")]) == [
        "INVENTED METRIC ['12'] not in ['EA1']: Grew the team from 3 to 12"]


def test_a_figure_inside_a_cited_tool_name_is_not_a_metric():
    assert _check(r2=[_b("Ran Examplelang3 across the estate", "EB1")]) == []


def test_a_licensed_name_never_launders_a_longer_figure():
    assert _check(r2=[_b("Ran Examplelang30 nodes", "EB1")]) == [
        "INVENTED METRIC ['30'] not in ['EB1']: Ran Examplelang30 nodes"]


def test_a_fabricated_figure_beside_a_licensed_tool_is_still_refused():
    assert _check(r2=[_b("Ran Examplelang3 for 99 users", "EB1")]) == [
        "INVENTED METRIC ['99'] not in ['EB1']: Ran Examplelang3 for 99 users"]


def test_span_removal_is_licensed_by_the_cited_entries_only():
    # EA1 declares no tools and does not carry the figure 3, so only a removal licensed by
    # ANOTHER entry's declared tools could hide the 3 inside the name, which must not happen.
    facts = _facts(EA1=EntryFacts(frozenset({"8"}), (), "Grew the team.", "role",
                                  ("Example Alpha",)))
    assert _check(r1=[_b("Ran Examplelang3 for the team", "EA1")], facts=facts) == [
        "INVENTED METRIC ['3'] not in ['EA1']: Ran Examplelang3 for the team",
        "MISATTRIBUTED TOOL 'Examplelang3' not in ['EA1']: Ran Examplelang3 for the team"]


def test_a_profile_figure_must_be_in_some_entry():
    assert _check(profile="I grew teams from 3 to 12.") == []
    assert _check(profile="I grew teams to 40.") == [
        "INVENTED PROFILE METRIC 40 not in your evidence: I grew teams to 40."]


def test_only_tools_never_other_names_strip_a_profile_digit():
    assert _check(profile="Skilled in Examplequery2.") == [
        "INVENTED PROFILE METRIC 2 not in your evidence: Skilled in Examplequery2."]


def test_a_tool_declared_by_the_cited_entry_is_licensed():
    assert _check(r2=[_b("Built it in Exampleco", "EB1")]) == []


def test_a_tool_another_entry_declares_is_misattributed():
    assert _check(r1=[_b("Built it in Exampleco", "EA1")]) == [
        "MISATTRIBUTED TOOL 'Exampleco' not in ['EA1']: Built it in Exampleco"]


def test_a_cited_entrys_own_text_licenses_a_tool_by_the_same_case_rule():
    licensed = _facts(EA1=EntryFacts(frozenset(), (), "Moved the platform to Exampleco.",
                                     "role", ("Example Alpha",)))
    lowercase_word = _facts(EA1=EntryFacts(frozenset(), (), "Ran the exampleco-live cutover.",
                                           "role", ("Example Alpha",)))
    assert _check(r1=[_b("Built it in Exampleco", "EA1")], facts=licensed) == []
    assert _check(r1=[_b("Built it in Exampleco", "EA1")], facts=lowercase_word) == [
        "MISATTRIBUTED TOOL 'Exampleco' not in ['EA1']: Built it in Exampleco"]


def test_a_lowercase_declared_tool_is_licensed_by_a_capitalised_mention():
    facts = _facts(EA1=EntryFacts(frozenset(), (), "Examplecoach sessions ran weekly.",
                                  "role", ("Example Alpha",)))
    assert _check(r1=[_b("Ran examplecoach sessions", "EA1")], facts=facts) == []


def test_a_longer_token_in_the_cited_text_never_licenses_the_shorter_tool():
    facts = _facts(EA1=EntryFacts(frozenset(), (), "Wrote Examplelangscript daily.",
                                  "role", ("Example Alpha",)),
                   EB1=EntryFacts(frozenset(), ("Examplelang",), "", "role",
                                  ("Example Beta",)))
    assert _check(r1=[_b("Wrote Examplelang daily", "EA1")], facts=facts) == [
        "MISATTRIBUTED TOOL 'Examplelang' not in ['EA1']: Wrote Examplelang daily"]


def test_a_lowercase_listed_item_is_policed_like_any_other():
    assert _check(r1=[_b("Ran examplecoach sessions", "EA1")]) == [
        "MISATTRIBUTED TOOL 'examplecoach' not in ['EA1']: Ran examplecoach sessions"]


def test_with_no_tools_declared_anywhere_the_attribution_check_does_not_run():
    facts = {k: EntryFacts(f.figures, (), f.text, f.placement, f.role_headings)
             for k, f in _facts().items()}
    assert _check(r1=[_b("Built it in Exampleco", "EA1")], facts=facts) == []


def test_a_decoy_is_refused_as_a_whole_term_only():
    assert _check(r1=[_b("Shipped Examplelang tooling", "EA1")], decoys=("examplelang",)) == [
        "FABRICATED: contains 'examplelang'"]
    assert _check(r1=[_b("Shipped Examplelangscript tooling", "EA1")],
                  decoys=("examplelang",)) == []


def test_a_multi_word_decoy_matches_as_a_phrase_but_not_across_a_sentence_break():
    assert _check(profile="I ran Example Zephyr work.", decoys=("Example Zephyr",)) == [
        "FABRICATED: contains 'Example Zephyr'"]
    assert _check(profile="I ran Example. Zephyr checks ran.", decoys=("Example Zephyr",)) == []



def test_entry_facts_reads_figures_tools_text_and_placement_from_the_bundle():
    from sluice.cv.bundle import build_bundle
    entries = [{"title": "Grew the team", "company": "Example Alpha", "best_for": "",
                "category": "", "metrics": "3 ８", "body": "Grew 3 to 8.",
                "fields": {"Tools": "Exampleco"}}]
    bundle = build_bundle(entries, "BASELINE", [], [], {"Example Alpha": "EA"})
    layout = CvLayout(roles=(ALPHA,))
    facts = entry_facts(bundle, layout)
    assert facts == {"EA1": EntryFacts(frozenset({"3", "8"}), ("Exampleco",),
                                       "Grew the team\nGrew 3 to 8.", "role",
                                       ("Example Alpha",))}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_checks.py -q`
Expected: FAIL with `ImportError: cannot import name 'EntryFacts' from 'sluice.cv.validate'`.

- [ ] **Step 3: Implement**

Add to `sluice/cv/validate.py` (imports: `from dataclasses import dataclass`; `from sluice.core.layout import place`; `from sluice.core.tokens import figures, find_term, tool_items`):

```python
@dataclass(frozen=True)
class EntryFacts:
    """What one bundle entry licenses (#364/#365/#368 spec §6.1): its figures, normalised
    to ASCII from its OWN source lines; its Tools: items; the title and body text that may
    license a tool it never listed; and where the CV Layout places it."""
    figures: frozenset
    tools: tuple
    text: str
    placement: str        # core/layout.py::Placement.reason
    role_headings: tuple  # headings of the roles it may be cited under


def entry_facts(bundle, layout):
    from sluice.cv.bundle import _entry_block
    out = {}
    for e in bundle["entries"]:
        eid = e["id"]
        if eid in out:
            raise ValueError(f"duplicate bundle entry id {eid!r}: ids must be unique, "
                             "since each one keys its own allowlist")
        block = _entry_block(e)
        block[0] = block[0][len(eid) + 2:]          # drop the leading `[{eid}]`
        where = place(layout, e.get("company", ""))
        out[eid] = EntryFacts(
            figures=figures("\n".join(block)), tools=tuple(tool_items(e)),
            text=f"{e.get('title', '')}\n{e.get('body', '')}", placement=where.reason,
            role_headings=tuple(layout.roles[i].heading for i in sorted(where.roles)))
    return out


def _spans(text, tools):
    return [span for tool in tools for occurrence in find_term(text, tool)
            for span in occurrence]


def _licenses(fact, tool):
    # Declared (in any case: the declaration is the user's), or named by the entry's own
    # title or body under the trigger's case rule -- so `go-live` never licenses `Go`, while
    # a sentence-initial `Coaching` licenses a declared `coaching`.
    return (tool.casefold() in {t.casefold() for t in fact.tools}
            or bool(find_term(fact.text, tool, case_sensitive=True, lower_accepts_capital=True)))


def _belongs(fact):
    if fact.placement in ("role", "any_role"):
        return f"belongs to {', '.join(fact.role_headings)}"
    if fact.placement == "blank":
        return "has no company"
    return "is not on your CV"


def check_selection(selection, slots, facts, *, decoys=()):
    """The HARD findings for one selection. Reads only the text the model wrote -- the
    profile and the kept bullets -- never vault text, which is the user's (spec §2). The em
    dash and double-hyphen rows (cv/slop.py::check_hard) run beside this in the engine, over
    the same texts, and report as `slop` the way they always have.

    ONE accumulator, extended only by `v.append(...)` with the category spelled inline:
    tests/test_docs_claims.py derives the gate's categories from exactly that shape (it
    shares `v` with the text `validate()` above until Task 19 deletes it), and
    docs/TROUBLESHOOTING.md must explain every one."""
    v = []
    vocabulary = sorted({t for f in facts.values() for t in f.tools})
    every_tool = [t for f in facts.values() for t in f.tools]
    every_figure = frozenset().union(*(f.figures for f in facts.values())) if facts else frozenset()
    profile = selection.profile
    for decoy in decoys:
        if find_term(profile, decoy):
            v.append(f"FABRICATED: contains '{decoy}'")
    # The profile has no cites, so any entry's figures license it, and only entries' TOOLS
    # (never a Skills Inventory name) blank a digit inside a name (#165).
    for n in sorted(figures(profile, remove=_spans(profile, every_tool)) - every_figure):
        v.append(f"INVENTED PROFILE METRIC {n} not in your evidence: {profile.strip()[:50]}")
    for slot in slots:
        for i, bullet in enumerate(selection.roles.get(slot.id, ()), 1):
            where, snip = f"{slot.id} bullet {i}", bullet.text.strip()[:50]
            if not bullet.cites:
                v.append(f"UNCITED BULLET: {where}: {bullet.text.strip()[:60]}")
                continue
            bad = [c for c in bullet.cites if c not in facts]
            if bad:
                v.append(f"BAD CITATION {bad}: not bundle entries - {where}: {snip}")
                continue
            wrong = [c for c in bullet.cites if c not in slot.eligible]
            for c in wrong:
                v.append(f"WRONG EMPLOYER: {where} cites {c}, which {_belongs(facts[c])} "
                         f"- {snip}")
            if wrong:
                continue
            cited = [facts[c] for c in bullet.cites]
            licensed = frozenset().union(*(f.figures for f in cited))
            invented = sorted(figures(bullet.text,
                                      remove=_spans(bullet.text, [t for f in cited for t in f.tools]))
                              - licensed)
            if invented:
                v.append(f"INVENTED METRIC {invented} not in {list(bullet.cites)}: {snip}")
            for tool in vocabulary:
                if (find_term(bullet.text, tool, case_sensitive=True)
                        and not any(_licenses(f, tool) for f in cited)):
                    v.append(f"MISATTRIBUTED TOOL {tool!r} not in {list(bullet.cites)}: {snip}")
            for decoy in decoys:
                if find_term(bullet.text, decoy):
                    v.append(f"FABRICATED: contains '{decoy}'")
    return v
```

- [ ] **Step 4: Explain the two new categories where a user reads them**

`tests/test_docs_claims.py` derives the gate's categories from `cv/validate.py`'s appends and requires `docs/TROUBLESHOOTING.md`'s `skipped-gate` section to explain exactly that set, so it now asks for `WRONG EMPLOYER` and `MISATTRIBUTED TOOL`. Run `.venv/bin/python -m pytest tests/test_docs_claims.py -q` and confirm those two are the ones named unexplained. Then add these bullets to that section's list, directly after the `INVENTED PROFILE METRIC` bullet (the guard reads each bullet's backticked head, before the ` — `):

```markdown
- `WRONG EMPLOYER` — a **WORK bullet** citing an entry that does not belong to the role it
  sits under: the entry belongs to another role on the CV, to a company left off it, or to no
  company at all. The fix is the citation, or the entry's `Company:` and the CV Layout.
- `MISATTRIBUTED TOOL` — a **WORK bullet** naming a tool that some verified entry lists in
  `Tools:` but none of the entries it cites lists or mentions. Right tool, wrong role: the
  fix is the citation.
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_checks.py tests/test_docs_claims.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/validate.py tests/test_cv_checks.py docs/TROUBLESHOOTING.md
git commit -F - <<'EOF'
feat(cv): hard checks over a structured selection

Every bullet must cite entries that exist and that belong to its own
role; figures must come from the cited entries, scanned in any script
with tool names blanked token by token; a tool named in a bullet must be
declared or named by a cited entry; decoys match as whole terms. Only the
model's text is checked. The text gate still runs; nothing calls this yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 12: The structured composer prompt

Spec §5.1. Added to `cv/compose.py` BESIDE today's `build_prompt`/`compose`, which Task 19 deletes. Every new block of shipped text lives in `build_structured_prompt` or a `*PROMPT*` constant so `tests/test_prompt_neutrality.py` discovers it.

**Files:**
- Modify: `sluice/cv/compose.py` (add `_STRUCTURED_RULES_PROMPT`, `_STRUCTURED_SKILLS_RULE_PROMPT`, `_NO_SKILLS_RULE_PROMPT`, `_JSON_SHAPE_PROMPT`, `_JSON_SHAPE_NO_SKILLS_PROMPT`, `_ROLE_SLOTS_PROMPT_HEADER`, `_SKILLS_POOL_PROMPT_HEADER`, `_RETRY_FINDINGS_PROMPT_HEADER`, `_RETRY_DROPS_PROMPT_HEADER`, `build_structured_prompt`, `compose_structured`)
- Modify: `tests/test_usage_wiring.py` (`_CALL_SITES` gains `("cv/compose.py", "compose_structured"): ("cv-compose", 1)`: its counter-compared roster names every `.complete(` call site, and `compose_structured` adds one. Measured: without it `test_every_llm_call_site_is_declared` is red)
- Modify: `tests/test_prompt_neutrality.py` (two helpers that build real findings and a real audit excerpt; `_SYNTHETIC_ARGS` entries for the structured prompt and the audit prompt; a `_KNOWN_PROMPTS` entry; coverage rows)
- Test: `tests/test_cv_structured_prompt.py`

**Interfaces:**
- Consumes: `Slot` (Task 5), `format_dates` (Task 10), `skills_requested` (Task 9); in `cv/compose.py`: `_TRIAGE_FRAMING_PROMPT_RULE`, `_TRIAGE_FRAMING_PROMPT_HEADER`, `_banned_phrases_sentence`. In `tests/test_prompt_neutrality.py` only: `Bullet`, `parse_reply` (Task 8), `Selection` (Task 9), `audit_text` (Task 10), `EntryFacts`, `check_selection` (Task 11).
- Produces:
  - `build_structured_prompt(bundle_text, jd, company, role, *, name, slots, pool=(), skills_max=None, prior_findings=None, prior_drops=None, slop_allow=None, triage_framing=()) -> str`
  - `compose_structured(backend, bundle_text, jd, company, role, *, name, slots, pool=(), skills_max=None, prior_findings=None, prior_drops=None, slop_allow=None, triage_framing=(), on_prompt=None) -> str` — the backend's reply text, unmodified

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_structured_prompt.py`:

```python
"""cv/compose.py::build_structured_prompt -- what the composer is asked for (spec §5.1)."""
from sluice.core.layout import Slot
from sluice.core.protocols import LayoutRole
from sluice.cv import compose as C
from sluice.cv.slop import _PHRASES

ALPHA = LayoutRole("Example Alpha", "01/2020", "present", title="SYNTHETIC-TITLE-1")
GROUP = LayoutRole("Example Northgate", "08/2001", "03/2015")
SLOTS = (Slot("R1", ALPHA, ("EA1", "EA2"), 5), Slot("R2", GROUP, (), 0))


def _prompt(**over):
    kw = dict(name="Jane Roe", slots=SLOTS, pool=("Example Query", "Examplelang"),
              skills_max=4)
    kw.update(over)
    return C.build_structured_prompt("BUNDLE TEXT", "THE JD", "Example Co", "Analyst", **kw)


def test_each_slot_lists_its_heading_dates_cites_and_budget():
    p = _prompt()
    assert "R1: Example Alpha | 01/2020–present | SYNTHETIC-TITLE-1 | may cite: EA1, EA2 | " \
           "up to 5 bullets" in p
    assert "R2: Example Northgate | 08/2001–03/2015 | may cite: none | no bullets" in p


def test_an_uncapped_slot_says_so():
    p = _prompt(slots=(Slot("R1", ALPHA, ("EA1",), None),))
    assert "| any number of bullets" in p


def test_the_skills_pool_is_a_closed_list_with_its_cap():
    p = _prompt()
    assert C._SKILLS_POOL_PROMPT_HEADER in p
    assert "- Example Query\n- Examplelang" in p
    assert "at most 4" in p
    assert '"skills": ["<skill from the list>"]' in p


def test_no_skills_are_asked_for_with_an_empty_pool_or_a_zero_cap():
    for p in (_prompt(pool=()), _prompt(skills_max=0)):
        assert C._SKILLS_POOL_PROMPT_HEADER not in p
        assert '"skills"' not in p
        assert C._NO_SKILLS_RULE_PROMPT.strip() in p


def test_the_reply_shape_is_shown_with_placeholders_only():
    assert '{"profile": "<profile>", "roles": {"<slot>": [{"text": "<bullet>", ' \
           '"cites": ["<id>"]}]}' in _prompt()


def test_the_prompt_never_mentions_a_baseline_cv():
    assert "BASELINE" not in _prompt()


def test_the_prompt_carries_the_whole_ban_list_and_one_double_hyphen():
    p = _prompt()
    assert [ph for ph in _PHRASES if ph not in p] == []
    # The prompt bans "--"; it must not model one beyond the single place it names it.
    assert p.count("--") == 1


def test_the_retry_lists_the_findings_and_the_drops():
    p = _prompt(prior_findings=["UNCITED BULLET: R1 bullet 1: x"],
                prior_drops=["'Example Ghost': not one of your skills"])
    assert C._RETRY_FINDINGS_PROMPT_HEADER in p and "- UNCITED BULLET: R1 bullet 1: x" in p
    assert C._RETRY_DROPS_PROMPT_HEADER in p and "- 'Example Ghost': not one" in p


def test_no_retry_block_on_a_first_attempt():
    p = _prompt()
    assert C._RETRY_FINDINGS_PROMPT_HEADER not in p
    assert C._RETRY_DROPS_PROMPT_HEADER not in p


def test_compose_structured_returns_the_reply_unmodified_and_reports_its_prompt():
    from sluice.core.backends import Completion

    class Backend:
        def complete(self, prompt):
            self.prompt = prompt
            return Completion("```json\n{}\n``` and some chat")

    seen = []
    be = Backend()
    out = C.compose_structured(be, "BUNDLE TEXT", "THE JD", "Example Co", "Analyst",
                               name="Jane Roe", slots=SLOTS, on_prompt=seen.append)
    assert out == "```json\n{}\n``` and some chat"
    assert seen == [be.prompt]


# tests/test_cv_compose.py's static guard reads `_RULES` alone, and goes with it in Task 19
# (spec §12.2: the static CV-prompt guard points at every rule constant the new prompt
# renders). Its vocabulary is copied here, never imported, because that row is deleted then.
_FORBIDDEN_PREFERENCES = (
    # company type / industry
    "startup", "enterprise", "faang", "unicorn", "well-funded",
    # work style / location
    "remote-first", "fast-paced", "onsite", "relocation",
    # compensation
    "salary", "equity", "compensation", "six-figure",
    # role shapes (from the triage guard's vocabulary)
    "engineering manager", "team lead", "tech lead", "scrum master",
    # culture rubric / hype
    "dora", "kanban", "rockstar", "ninja",
)


def _prompt_constants():
    """Every `*PROMPT*`-named constant the structured prompt can render: compose's rules,
    headers and JSON shape, and the bundle's section headers. DISCOVERED, so a constant
    added later is checked with no edit here."""
    from sluice.cv import bundle as B

    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, (tuple, list, frozenset, set)):
            return [s for v in value for s in strings(v)]
        return []

    return {f"{m.__name__}.{n}": " ".join(strings(v)) for m in (C, B)
            for n, v in vars(m).items() if "PROMPT" in n and strings(v)}


def test_no_structured_prompt_constant_names_a_job_or_culture_preference():
    """Static on purpose: `build_structured_prompt`'s output interpolates the caller's job
    ad, which may legitimately say "startup". tests/test_prompt_neutrality.py sweeps the
    RENDERED prompt, but can only carry terms every shipped prompt honours, so this fuller
    list stays here."""
    found = _prompt_constants()
    # SCOPE first: a discovery that matched nothing would pass the check below vacuously.
    assert {"sluice.cv.compose._STRUCTURED_RULES_PROMPT", "sluice.cv.compose._JSON_SHAPE_PROMPT",
            "sluice.cv.compose._ROLE_SLOTS_PROMPT_HEADER",
            "sluice.cv.compose._RETRY_FINDINGS_PROMPT_HEADER"} <= found.keys(), sorted(found)
    leaked = {name: hits for name, text in found.items()
              if (hits := [t for t in _FORBIDDEN_PREFERENCES if t in text.lower()])}
    assert not leaked, f"a shipped CV prompt constant names a job or culture preference: {leaked}"
```

In `tests/test_prompt_neutrality.py`, add these two helpers directly above `_SYNTHETIC_ARGS` (spec §12.2: the message templates of the new finding kinds, and the audit's per-bullet block, are shipped text the model reads, so they are swept as RENDERED by the real code rather than stood in for by a placeholder):

```python
def _structured_findings():
    """One finding of each kind the text pipeline never produced -- `REPLY:`, `WRONG
    EMPLOYER`, `MISATTRIBUTED TOOL` -- made by the REAL checks over synthetic input. The
    retry block shows them to the model, so their wording is prompt text, and a placeholder
    string in their place would sweep none of it."""
    from sluice.core.layout import Slot
    from sluice.core.protocols import LayoutRole
    from sluice.cv.reply import Bullet, parse_reply
    from sluice.cv.selection import Selection
    from sluice.cv.validate import EntryFacts, check_selection
    slots = (Slot("R1", LayoutRole("SYNTHETIC heading", "01/2020", "present"), ("SY1",), None),
             Slot("R2", LayoutRole("SYNTHETIC group", "01/2010", "12/2019"), ("SY2",), None))
    facts = {"SY1": EntryFacts(frozenset(), ("Examplelang",), "SYNTHETIC", "role",
                               ("SYNTHETIC heading",)),
             "SY2": EntryFacts(frozenset(), ("Examplelangscript",), "SYNTHETIC", "role",
                               ("SYNTHETIC group",))}
    selection = Selection(profile="SYNTHETIC profile.", skills=(), roles={
        "R1": (Bullet("Built SYNTHETIC tooling.", ("SY2",)),       # WRONG EMPLOYER
               Bullet("Ran Examplelangscript jobs.", ("SY1",))),    # MISATTRIBUTED TOOL
        "R2": ()})
    refused = parse_reply({"roles": {}}, ("R1", "R2"))                # REPLY: no profile
    assert isinstance(refused, list), "premise: a reply with no profile is refused"
    return tuple(check_selection(selection, slots, facts) + refused)


def _structured_audit_excerpt():
    """What the auditor reads under structured composition -- cv/document.py::audit_text
    over a synthetic selection -- so its per-bullet block is swept inside the audit prompt."""
    from sluice.core.layout import Slot
    from sluice.core.protocols import LayoutRole
    from sluice.cv.document import audit_text
    from sluice.cv.reply import Bullet
    from sluice.cv.selection import Selection
    slot = Slot("R1", LayoutRole("SYNTHETIC heading", "01/2020", "present"), ("SY1",), None)
    selection = Selection(profile="SYNTHETIC profile.", skills=(),
                          roles={"R1": (Bullet("Built SYNTHETIC tooling.", ("SY1",)),)})
    return audit_text(selection, (slot,))
```

and add to `_SYNTHETIC_ARGS` (with the module's import of `Slot`/`LayoutRole` at the top of the file):

```python
    # The structured composer (#364/#365/#368). `slots` is required and must be real Slot
    # objects; the rest are DEFAULTED and each gates a conditional block -- the skills
    # pool, the retry findings and drops, and the triage framing -- so a defaulted render
    # would sweep none of that shipped text. The findings are REAL ones (see the helper).
    "sluice.cv.compose.build_structured_prompt": {
        "slots": (Slot("R1", LayoutRole("SYNTHETIC heading", "01/2020", "present",
                                        title="SYNTHETIC title"), ("SY1",), 3),
                  Slot("R2", LayoutRole("SYNTHETIC group", "01/2010", "12/2019"), (), 0)),
        "pool": ("SYNTHETIC skill",), "skills_max": 2,
        "prior_findings": _structured_findings(), "prior_drops": ("SYNTHETIC drop",),
        "triage_framing": ("SYNTHETIC framing",)},
    # The auditor's CV text is the excerpt audit_text builds, not a placeholder string.
    "sluice.cv.audit.build_audit_prompt": {"cv_text": _structured_audit_excerpt()},
```

add `"sluice.cv.compose.build_structured_prompt"` to `_KNOWN_PROMPTS`, and add these coverage rows:

```python
def test_the_swept_structured_prompt_carries_every_conditional_block():
    # Each block below reaches the rendered prompt only through an override in
    # _SYNTHETIC_ARGS; delete one and the sweep would pass over a render without it.
    from sluice.cv import compose
    rendered = _discover_prompts()["sluice.cv.compose.build_structured_prompt"]
    for block in (compose._ROLE_SLOTS_PROMPT_HEADER, compose._SKILLS_POOL_PROMPT_HEADER,
                  compose._RETRY_FINDINGS_PROMPT_HEADER, compose._RETRY_DROPS_PROMPT_HEADER,
                  compose._TRIAGE_FRAMING_PROMPT_HEADER, compose._JSON_SHAPE_PROMPT):
        assert block in rendered, block


def test_the_swept_structured_prompt_carries_the_whole_enforced_ban_list():
    rendered = _discover_prompts()["sluice.cv.compose.build_structured_prompt"]
    assert [p for p in _PHRASES if p not in rendered] == []


def test_the_retry_block_sweeps_one_finding_of_each_new_kind():
    # They reach the rendered prompt only through _structured_findings(); a placeholder in
    # its place would leave the sweep green over none of their wording.
    rendered = _discover_prompts()["sluice.cv.compose.build_structured_prompt"]
    for kind in ("REPLY:", "WRONG EMPLOYER", "MISATTRIBUTED TOOL"):
        assert kind in rendered, kind


def test_the_swept_audit_prompt_carries_the_per_bullet_block():
    # The excerpt reaches the rendered audit prompt only through the cv_text override.
    from sluice.core.protocols import SECTION_HEADINGS
    rendered = _discover_prompts()["sluice.cv.audit.build_audit_prompt"]
    assert SECTION_HEADINGS[0] in rendered
    assert "- Built SYNTHETIC tooling. [SY1]" in rendered
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_structured_prompt.py tests/test_prompt_neutrality.py -q`
Expected: FAIL with `AttributeError: module 'sluice.cv.compose' has no attribute 'build_structured_prompt'`.

- [ ] **Step 3: Implement**

Add to `sluice/cv/compose.py`:

```python
# --- Structured composition (#364/#365/#368) -------------------------------------------
# The composer returns JSON CONTENT and sluice builds the CV (spec §5). No text format
# contract, no baseline CV, no envelope to unwrap: the role slots come from the CV Layout,
# and every rule below is about content.
_STRUCTURED_RULES_PROMPT = """CV RULES (follow exactly):

- YOUR TASK IS TO TAILOR, NOT TO WRITE. You are given the candidate's verified facts in the VERIFIED EXPERIENCE ENTRIES. Rephrase, reorder, and emphasise ONLY those facts to fit this specific role. You add nothing that is not already in the entries.
- The VERIFIED EXPERIENCE ENTRIES are the ONLY permitted source for the profile and the bullets. If a detail is not in an entry, leave it out. Never infer from general knowledge, from the job ad, or from what the role "should" have. NO FABRICATION of any kind: no employers, roles, dates, titles, numbers, metrics, tools, skills, certifications, achievements, or motivations that are not in the entries.
- If the role asks for experience, a skill, or a quality the entries do not contain, DO NOT add it. Omit it. A shorter, honest CV is correct; an invented match is a failure.
- Rephrasing changes wording and emphasis, never facts or numbers. Any number you include must remain unchanged from the entry it came from.
- Fill each ROLE SLOT only with work from the entries that slot lists, and never move work between slots.
- Every bullet's "cites" lists the id of EACH entry it draws on, including every entry it takes a number or a tool from. A bullet may cite only entries its slot lists.
- Any number in a bullet must appear in an entry it cites. Any number in the profile must appear in a VERIFIED EXPERIENCE ENTRY.
- Text never contains square brackets, citation codes or line breaks: citations go in "cites", and each bullet is one line.
- Order each slot's bullets most relevant first, within its bullet limit. A slot marked "no bullets" gets none.
- The SKILLS INVENTORY is FRAMING for the profile and the bullets: use it to choose which entries to lead with, never cite it, and never rest a claim in the profile or a bullet on it alone. It IS a source for the skills list.
{triage_framing_rule}{skills_rule}- NO em dashes anywhere. Use commas, colons, semicolons, periods, or parentheses. No double hyphens (--).
- No AI slop (avoid these words/phrases and any inflection of them: {banned_phrases}). Short sentences. Real metrics only.
- Profile: "I" voice, 2 to 3 sentences, composed ONLY from facts in the VERIFIED EXPERIENCE ENTRIES, ordered and emphasised for {role}. No motivations, aspirations, or company-specific claims.
- Reply with ONE JSON object and nothing else: no preamble, commentary or closing remark. Use exactly this shape, replacing each <...> placeholder:
{json_shape}"""

_STRUCTURED_SKILLS_RULE_PROMPT = (
    "- Pick the skills list ONLY from SKILLS YOU MAY LIST, spelled exactly as listed, most "
    "relevant to this role first{cap}.\n")
_NO_SKILLS_RULE_PROMPT = "- Do not include a skills list.\n"
# Placeholders only (spec §5.2): `_prefix` can produce none of them, and cv/reply.py
# refuses a reply that still carries one, so a backend echoing the example cannot ship it.
_JSON_SHAPE_PROMPT = ('{"profile": "<profile>", "roles": {"<slot>": [{"text": "<bullet>", '
                      '"cites": ["<id>"]}]}, "skills": ["<skill from the list>"]}')
_JSON_SHAPE_NO_SKILLS_PROMPT = ('{"profile": "<profile>", "roles": {"<slot>": [{"text": '
                                '"<bullet>", "cites": ["<id>"]}]}}')
_ROLE_SLOTS_PROMPT_HEADER = "=== ROLE SLOTS (fill each by its id; never move work between slots) ==="
_SKILLS_POOL_PROMPT_HEADER = "=== SKILLS YOU MAY LIST (pick the ones most relevant to this role) ==="
_RETRY_FINDINGS_PROMPT_HEADER = ("=== YOUR PREVIOUS REPLY FAILED THE GATE. Fix these and reply "
                                 "again with the FULL JSON object: ===")
_RETRY_DROPS_PROMPT_HEADER = "=== DROPPED FROM YOUR PREVIOUS REPLY (choose better this time) ==="


def _slot_line(slot):
    from sluice.cv.document import format_dates
    fields = [slot.role.heading, format_dates(slot.role)]
    if slot.role.title:
        fields.append(slot.role.title)
    cites = ", ".join(slot.eligible) or "none"
    budget = ("no bullets" if slot.budget == 0
              else "any number of bullets" if slot.budget is None
              else f"up to {slot.budget} bullets")
    return f"{slot.id}: {' | '.join(fields)} | may cite: {cites} | {budget}"


def build_structured_prompt(bundle_text, jd, company, role, *, name, slots, pool=(),
                            skills_max=None, prior_findings=None, prior_drops=None,
                            slop_allow=None, triage_framing=()):
    from sluice.cv.selection import skills_requested
    asked = skills_requested(pool, skills_max)
    skills_rule = (_STRUCTURED_SKILLS_RULE_PROMPT.format(
                       cap=f", at most {skills_max}" if skills_max else "")
                   if asked else _NO_SKILLS_RULE_PROMPT)
    parts = [
        f"Compose a tailored CV for {name} applying for {role} at {company}.",
        "",
        _STRUCTURED_RULES_PROMPT.format(
            triage_framing_rule=_TRIAGE_FRAMING_PROMPT_RULE if triage_framing else "",
            skills_rule=skills_rule, banned_phrases=_banned_phrases_sentence(slop_allow),
            role=role, json_shape=_JSON_SHAPE_PROMPT if asked else _JSON_SHAPE_NO_SKILLS_PROMPT),
        "",
        "=== THE ROLE (JD) ===",
        jd or "(no JD text captured; compose from the entries for a general fit)",
        "",
    ]
    if triage_framing:
        parts += [_TRIAGE_FRAMING_PROMPT_HEADER, *[f"- {line}" for line in triage_framing], ""]
    parts += [_ROLE_SLOTS_PROMPT_HEADER, *[_slot_line(s) for s in slots], ""]
    if asked:
        parts += [_SKILLS_POOL_PROMPT_HEADER, *[f"- {item}" for item in pool], ""]
    parts.append(bundle_text)
    if prior_findings:
        parts += ["", _RETRY_FINDINGS_PROMPT_HEADER, *[f"- {f}" for f in prior_findings]]
    if prior_drops:
        parts += ["", _RETRY_DROPS_PROMPT_HEADER, *[f"- {d}" for d in prior_drops]]
    return "\n".join(parts)


def compose_structured(backend, bundle_text, jd, company, role, *, name, slots, pool=(),
                       skills_max=None, prior_findings=None, prior_drops=None,
                       slop_allow=None, triage_framing=(), on_prompt=None):
    """The backend's reply, exactly as received: cv/reply.py finds the JSON in it, so each
    attempt's artefact is the raw reply and nothing here can hide what came back."""
    prompt = build_structured_prompt(
        bundle_text, jd, company, role, name=name, slots=slots, pool=pool,
        skills_max=skills_max, prior_findings=prior_findings, prior_drops=prior_drops,
        slop_allow=slop_allow, triage_framing=triage_framing)
    if on_prompt is not None:
        on_prompt(prompt)
    return backend.complete(prompt).text
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_structured_prompt.py tests/test_prompt_neutrality.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both. If the neutrality sweep reports a `_FORBIDDEN` term in the new text, reword the shipped text; never add an `_EXEMPT` entry for a `sluice.cv` prompt (spec §12.2).

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/compose.py tests/test_cv_structured_prompt.py tests/test_prompt_neutrality.py
git commit -F - <<'EOF'
feat(cv): the structured composer prompt

Asks for JSON content: a profile, bullets per role slot with their cites
in a separate field, and skills picked from a closed list. Each slot shows
its heading, dates, the entries it may cite and its bullet budget. No
baseline CV and no text format contract. The neutrality sweep reaches
every conditional block. Nothing calls it yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 13: Bundle renderings for structured composition, and the term vocabulary (#368)

Spec §5.1 (the entries the composer sees), §6.4 (the auditor's corpus), §8 (#368). Added to `cv/bundle.py` BESIDE `render_bundle`, `render_composer_bundle` and `mention_vocab`, which Task 19 deletes.

**Files:**
- Modify: `sluice/cv/bundle.py` (add `_TOOLS_SOURCE_PROMPT`, the four `*_HEADER_PROMPT` section headers, `_tools_line`, `_guidance_section`, `render_structured_bundle`, `render_audit_bundle`, `term_vocabulary`)
- Modify: `tests/test_prompt_neutrality.py` (`_KNOWN_PROMPTS` gains the five new constants)
- Test: `tests/test_cv_structured_bundle.py`

**Interfaces:**
- Consumes: `tool_items`, `WORD_RE` (Task 3); `layout_text` (Task 5); `entry_facts` (Task 11); in `cv/bundle.py`: `_entry_block`, `_framing_lines`.
- Produces:
  - `render_structured_bundle(bundle: dict) -> str` — the composer's source text: entries (with a `tools=` line each), the Skills Inventory framing, the candidate's guidance; no baseline
  - `render_audit_bundle(bundle: dict) -> str` — the auditor's corpus: entries with their `tools=` lines, and the guidance; no baseline, no inventory
  - `term_vocabulary(bundle: dict, layout) -> frozenset[str]` — every case-folded word of the entries, their tools, the inventory framing and the layout text; subtracts nothing

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cv_structured_bundle.py`:

```python
"""cv/bundle.py's structured renderings and the term vocabulary (spec §5.1, §6.4, §8)."""
from sluice.core.protocols import CvLayout, LayoutRole
from sluice.cv import bundle as B
from sluice.cv.validate import entry_facts

ENTRY = {"title": "Grew the team", "company": "Example Alpha", "best_for": "", "category": "",
         "metrics": "3 8", "body": "Grew 3 to 8 on Examplelang9.",
         "fields": {"Tools": "Examplelang9, Exampleco"}}
SKILL = {"title": "Example Zephyr", "fields": {"Domain": "people"}, "body": ""}
LAYOUT = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                    location="Example Location A"),),
                  certificates=("Example Cert",))


def _bundle(skills=(), negatives=()):
    return B.build_bundle([ENTRY], "BASELINE TEXT", list(negatives), [], {"Example Alpha": "EA"},
                          skills=skills)


def test_the_composer_sees_each_entry_with_its_tools_and_no_baseline():
    text = B.render_structured_bundle(_bundle())
    assert "[EA1] (Example Alpha) Grew the team | metrics=3 8" in text
    assert "tools=Examplelang9, Exampleco" in text
    assert "BASELINE" not in text


def test_the_inventory_is_framing_and_carries_the_tools_constraint():
    text = B.render_structured_bundle(_bundle(skills=[SKILL]))
    assert "- Example Zephyr" in text
    assert B._TOOLS_SOURCE_PROMPT in text


def test_the_guidance_is_shown_as_guidance():
    text = B.render_structured_bundle(_bundle(negatives=["Lead with delivery."]))
    assert "=== THE CANDIDATE'S GUIDANCE" in text and "- Lead with delivery." in text


def test_the_auditor_sees_the_entries_tools_but_neither_baseline_nor_inventory():
    text = B.render_audit_bundle(_bundle(skills=[SKILL]))
    assert "tools=Examplelang9, Exampleco" in text
    assert "BASELINE" not in text and "Example Zephyr" not in text


def test_a_tools_line_never_licenses_a_figure():
    # The tools line is a separate emitter from _entry_block, so a digit inside a tool
    # name never reaches an entry's figures. This entry's ONLY 9 is inside a tool name.
    entry = {**ENTRY, "body": "Grew 3 to 8.", "fields": {"Tools": "Examplelang9"}}
    bundle = B.build_bundle([entry], "", [], [], {"Example Alpha": "EA"})
    assert "9" not in entry_facts(bundle, LAYOUT)["EA1"].figures


def test_the_vocabulary_holds_entries_tools_inventory_and_layout_words():
    vocab = B.term_vocabulary(_bundle(skills=[SKILL]), LAYOUT)
    for word in ("grew", "examplelang9", "exampleco", "zephyr", "example", "location", "cert"):
        assert word in vocab, word
    assert "baseline" not in vocab


def test_a_prose_negative_subtracts_nothing_from_the_vocabulary():
    # #368, synthetic: a layout instruction written as a negative, full of names and
    # capitals the user's own evidence carries, used to strip them all.
    negative = ("PRE-EXAMPLE ROLL-UP: every role before Example Alpha collapses into ONE "
                "block. Never give EXAMPLECO its own heading.")
    with_neg = B.term_vocabulary(_bundle(negatives=[negative]), LAYOUT)
    without = B.term_vocabulary(_bundle(), LAYOUT)
    assert with_neg == without
    assert {"example", "alpha", "exampleco"} <= with_neg
```

Add to `_KNOWN_PROMPTS` in `tests/test_prompt_neutrality.py`: `"sluice.cv.bundle._TOOLS_SOURCE_PROMPT"`, `"sluice.cv.bundle._ENTRIES_HEADER_PROMPT"`, `"sluice.cv.bundle._INVENTORY_HEADER_PROMPT"`, `"sluice.cv.bundle._GUIDANCE_HEADER_PROMPT"` and `"sluice.cv.bundle._AUDIT_ENTRIES_HEADER_PROMPT"`. Every one is shipped text the model reads, and a constant whose name carries `PROMPT` is what the sweep discovers (spec §5.1, neutrality reach).

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_structured_bundle.py -q`
Expected: FAIL with `AttributeError: module 'sluice.cv.bundle' has no attribute 'render_structured_bundle'`.

- [ ] **Step 3: Implement**

Add to `sluice/cv/bundle.py` (import `tool_items` from `sluice.core.tokens` and `layout_text` from `sluice.core.layout`):

```python
# The structured composer's claim-source constraint (#364/#365/#368). Names the entries
# alone: under spec D2 the baseline CV is not read when composing, so naming it would point
# the model at a source it cannot see.
_TOOLS_SOURCE_PROMPT = ("in the profile and the bullets, claim no technology, language, "
                        "framework or tool that is not named in the VERIFIED EXPERIENCE "
                        "ENTRIES above")

# The section headers the composer and the auditor read. Named *PROMPT* so
# tests/test_prompt_neutrality.py sweeps them with every other shipped prompt text.
_ENTRIES_HEADER_PROMPT = ("=== VERIFIED EXPERIENCE ENTRIES (the ONLY source for the profile "
                          "and bullets; cite by id) ===")
_INVENTORY_HEADER_PROMPT = ("=== SKILLS INVENTORY (framing for the profile and bullets; a "
                            "source only for the skills list) ===")
_GUIDANCE_HEADER_PROMPT = "=== THE CANDIDATE'S GUIDANCE (follow it; it is not a source) ==="
_AUDIT_ENTRIES_HEADER_PROMPT = ("=== VERIFIED EXPERIENCE ENTRIES (the ONLY truth; cited by "
                                "id) ===")


def _tools_line(entry):
    """An entry's Tools:, shown beside its block. A SEPARATE emitter from _entry_block on
    purpose: entry_facts harvests figures from _entry_block alone, so a digit inside a tool
    name (`Examplelang9`) can never license a figure."""
    items = tool_items(entry)
    return [f"tools={', '.join(items)}"] if items else []


def _guidance_section(bundle, extra=()):
    """`cv.negatives`, shown as what it now is (spec D7): the user's free-text guidance to
    the composer. No check reads it and nothing in it is a source."""
    return ([_GUIDANCE_HEADER_PROMPT]
            + [f"- {n}" for n in list(extra) + list(bundle["negatives"])])


def _entries_section(bundle, heading):
    lines = [heading]
    for e in bundle["entries"]:
        lines += _entry_block(e) + _tools_line(e)
        lines.append("")
    return lines


def render_structured_bundle(bundle):
    """The composer's source text: entries with their tools, the Skills Inventory as
    framing, the guidance. No baseline (spec D2)."""
    lines = _entries_section(bundle, _ENTRIES_HEADER_PROMPT)
    extra = ()
    if bundle.get("skills"):
        lines.append(_INVENTORY_HEADER_PROMPT)
        for sk in bundle["skills"]:
            lines += _framing_lines(sk)
        lines.append("")
        extra = (_TOOLS_SOURCE_PROMPT,)
    return "\n".join(lines + _guidance_section(bundle, extra=extra))


def render_audit_bundle(bundle):
    """The advisory auditor's truth: the entries WITH their tools -- the hard gate licenses
    a tool through Tools:, so the auditor must see the same evidence or every tool-naming
    bullet would read unsupported and be held (spec §6.4) -- and the guidance. No baseline,
    no inventory."""
    lines = _entries_section(bundle, _AUDIT_ENTRIES_HEADER_PROMPT)
    return "\n".join(lines + _guidance_section(bundle))


def term_vocabulary(bundle, layout):
    """What the unbundled-term check (cv/terms.py, #194) recognises: every word of the
    entries, their tools, the Skills Inventory framing and the CV Layout. It subtracts
    NOTHING -- #368: reading prose negatives as bans stripped terms the user's own evidence
    carries. A real "never claim X" belongs in cv.fabrication_decoys, a hard check."""
    lines = []
    for e in bundle["entries"]:
        lines += _entry_block(e) + _tools_line(e)
    for s in bundle.get("skills", ()):
        lines += _framing_lines(s)
    lines.append(layout_text(layout))
    return frozenset(t.casefold() for line in lines for t in _WORD_RE.findall(line or ""))
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_structured_bundle.py tests/test_prompt_neutrality.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/bundle.py tests/test_cv_structured_bundle.py tests/test_prompt_neutrality.py
git commit -F - <<'EOF'
feat(cv): structured and audit bundles, and a term vocabulary from the vault

The composer and the auditor see each entry with its tools and no baseline
CV. The unbundled-term vocabulary is built from the entries, their tools,
the Skills Inventory and the CV Layout, and subtracts nothing: a prose
negative no longer strips terms the user's own evidence carries (#368).
Not wired in yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 14: The renderer seam takes a `CvDocument`

Spec §7.2. Until Task 18 switches the engine, a renderer accepts EITHER a `CvDocument` (the new path, tested here) or composed text (today's engine path, unchanged), so the suite stays green and `cv/engine.py` keeps not importing `cv/parse.py` (`tests/test_cv_engine.py::test_the_engine_no_longer_imports_the_template_grammar`). Task 19 deletes the text branch.

**Files:**
- Modify: `sluice/core/protocols.py` (`Renderer.render`'s signature and docstring)
- Modify: `sluice/renderers/template.py`, `sluice/renderers/script.py`
- Test: `tests/test_renderer_template.py`, `tests/test_renderers.py`

**Interfaces:**
- Consumes: `CvDocument` (Task 4), `to_text` (Task 10), `tests.test_cv_script_golden.GOLDEN` (Task 2).
- Produces: `Renderer.render(self, document: CvDocument, out_dir: str, *, neutral_name: str = "CV.pdf") -> str`. TRANSITIONAL in both shipped renderers: a `str` argument still takes today's path, marked `# TRANSITIONAL: removed in Task 19` in a comment beside it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_renderer_template.py`:

```python
def test_a_document_renders_without_being_parsed(tmp_path, monkeypatch):
    from sluice.core.protocols import CvDocument, Role
    import sluice.cv.parse as parse_mod

    def refuse(_text):
        raise AssertionError("a CvDocument must not be parsed")

    monkeypatch.setattr(parse_mod, "parse_cv", refuse)
    doc = CvDocument(name="JANE ROE", contact="+1 555 0100", profile="I build.",
                     work=[Role("Example Alpha", "01/2020–present", "", "", ["Shipped it"])],
                     skills=[], certificates=[], education=[])
    r = _renderer(tmp_path, "{{ document.work[0].company }}|{{ document.work[0].bullets[0] }}")
    r.render(doc, str(tmp_path / "out"))
    assert FakeHTML.captured["html"] == "Example Alpha|Shipped it"
```

Add to `tests/test_renderers.py`:

```python
def test_every_registered_renderer_takes_a_document():
    import inspect
    from sluice.core import plugins
    from sluice.renderers import template, script  # noqa: F401  (self-registering)
    names = plugins.available("renderer")
    assert {"template", "script"} <= set(names), names
    for cls in (template.TemplateRenderer, script.ScriptRenderer):
        params = list(inspect.signature(cls.render).parameters)
        assert params == ["self", "document", "out_dir", "neutral_name"], (cls, params)


def test_the_script_renderer_hands_its_script_the_canonical_citation_free_text(
        tmp_path, monkeypatch):
    from sluice.cv import render as render_mod
    from sluice.renderers.script import ScriptRenderer
    from tests.test_cv_document import _assembled
    from tests.test_cv_script_golden import GOLDEN

    script = tmp_path / "render.py"
    script.write_text("", encoding="utf-8")
    seen = {}

    def runner(argv, **_kw):
        clean, pdf = argv[2], argv[3]
        with open(clean, encoding="utf-8") as fh:
            seen["text"] = fh.read()
        open(pdf, "wb").close()

        class Done:
            returncode, stderr = 0, ""
        return Done()

    r = ScriptRenderer(str(script), python_bin="python3", home=str(tmp_path))
    real = render_mod.render
    monkeypatch.setattr(render_mod, "render", lambda *a, **kw: real(*a, runner=runner, **kw))
    r.render(_assembled().document, str(tmp_path / "out"))
    assert seen["text"] == GOLDEN
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_renderer_template.py tests/test_renderers.py -q`
Expected: FAIL (`render()`'s first parameter is still named `cv_text`, and a `CvDocument` reaches `parse_cv`).

- [ ] **Step 3: Implement**

`sluice/core/protocols.py` — `Renderer.render` becomes:

```python
    def render(self, document: "CvDocument", out_dir: str, *, neutral_name: str = "CV.pdf") -> str: ...
```

and its docstring says: the renderer receives the CvDocument sluice ASSEMBLED from the vault and a checked reply (#364/#365/#368 spec §7); nothing parses CV text, and a renderer that needs text writes `cv/document.py::to_text(document)`. Until Task 18 switches the engine, the docstring ALSO says, in a sentence marked TRANSITIONAL, that a `str` is still accepted and parsed and that the optional `precheck` still runs on it; Task 19 deletes that sentence with the `str` branch.

`sluice/renderers/template.py` — rename the parameter and accept a document:

```python
    def render(self, document, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        # TRANSITIONAL: removed in Task 19. Until the engine assembles a CvDocument it still
        # hands this renderer composed text, which is parsed exactly as before.
        if isinstance(document, str):
            document = parse_cv(document)
        os.makedirs(out_dir, exist_ok=True)
        # ... the rest of today's body, unchanged, rendering `document=document` ...
```

`sluice/renderers/script.py`:

```python
    def render(self, document, out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        from sluice.cv.render import render as _render
        # A CvDocument is written in the canonical text format (cv/document.py::to_text),
        # citation-free: exactly what a script received from the old pipeline, pinned by
        # tests/test_cv_script_golden.py. TRANSITIONAL: the str branch is removed in Task 19.
        if not isinstance(document, str):
            from sluice.cv.document import to_text
            document = to_text(document)
        return _render(document, out_dir, render_script=self.script,
                       python_bin=self.python_bin, home=self.home,
                       neutral_name=neutral_name)
```

- [ ] **Step 4: Run the touched tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_renderer_template.py tests/test_renderers.py tests/test_cv_engine.py -q && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/protocols.py sluice/renderers/template.py sluice/renderers/script.py tests/test_renderer_template.py tests/test_renderers.py
git commit -F - <<'EOF'
refactor(renderers): render a CvDocument rather than parsing text

The Renderer seam now receives the document sluice assembles. template
renders it directly; script writes the canonical text, the same text a
script received from the old pipeline. Both still accept composed text
until the engine switches over.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 15: Artefact names and run-record keys

Spec §7.4, §9.2. The raw reply gets a name that promises nothing about its content, and a 3.x run's attempt files are still cleared.

**Files:**
- Modify: `sluice/cv/artefacts.py` (`reply_name`, `_OWNED`, `_write_record`, `finish`)
- Modify: `sluice/cv/engine.py` (`CvResult` gains three fields; nothing sets them yet)
- Modify: `docs/USAGE.md` (the "Diagnostic artefacts" table's reply row and `run.json` keys)
- Test: `tests/test_cv_run_artefacts.py`

**Interfaces:**
- Produces:
  - `cv/artefacts.py::reply_name(attempt: int) -> str` → `"reply.attempt-{attempt}.txt"` (replaces `draft_name`; `RunArtefacts.composed` writes it)
  - `run.json` gains `"skills_dropped": list`, `"bullets_trimmed": list`, `"attribution_check_off": bool`
  - `CvResult` gains `skills_dropped: list = field(default_factory=list)`, `bullets_trimmed: list = field(default_factory=list)`, `attribution_check_off: bool = False`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_run_artefacts.py` (each row drives `RunArtefacts` directly in `tmp_path`):

```python
def test_a_reply_is_saved_under_a_name_that_promises_nothing_about_its_content(tmp_path):
    from sluice.cv.artefacts import RunArtefacts
    rec = RunArtefacts(dry_run=True)
    rec.begin(str(tmp_path), lead="x", entry_ids=[], dossier_failed=False,
              skills_unreadable=False)
    rec.composed(1, "chat then {}")
    assert (tmp_path / "reply.attempt-1.txt").read_text(encoding="utf-8") == "chat then {}"


def test_a_3x_runs_attempt_files_are_cleared_by_the_next_run(tmp_path):
    from sluice.cv.artefacts import RunArtefacts
    (tmp_path / "cv.attempt-1.md").write_text("old draft", encoding="utf-8")
    (tmp_path / "cv.attempt-2.md").write_text("old draft", encoding="utf-8")
    RunArtefacts(dry_run=True).begin(str(tmp_path), lead="x", entry_ids=[],
                                     dossier_failed=False, skills_unreadable=False)
    assert not list(tmp_path.glob("cv.attempt-*.md"))


def test_the_run_record_carries_the_selection_report(tmp_path):
    import json
    from sluice.cv.artefacts import RunArtefacts
    from sluice.cv.engine import CvResult
    rec = RunArtefacts(dry_run=True)
    rec.begin(str(tmp_path), lead="x", entry_ids=[], dossier_failed=False,
              skills_unreadable=False)
    rec.finish(CvResult("x", "dry-run", skills_dropped=["'A': not one of your skills"],
                        bullets_trimmed=["R1 (Example Alpha): kept 2 of 3"],
                        attribution_check_off=True))
    record = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert record["skills_dropped"] == ["'A': not one of your skills"]
    assert record["bullets_trimmed"] == ["R1 (Example Alpha): kept 2 of 3"]
    assert record["attribution_check_off"] is True
```

Update any existing assertion in that file that names `cv.attempt-N.md` to `reply.attempt-N.txt`.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_run_artefacts.py -q`
Expected: FAIL (`reply.attempt-1.txt` missing; `CvResult` has no `skills_dropped`).

- [ ] **Step 3: Implement**

In `sluice/cv/artefacts.py`, replace `draft_name` with `reply_name` and update `RunArtefacts.composed` and the module docstring's file list to match:

```python
def reply_name(attempt: int) -> str:
    # The backend's reply exactly as received (#364/#365/#368): `.txt` because a reply can
    # be chat-wrapped or fenced, so it promises nothing about being JSON.
    return f"reply.attempt-{attempt}.txt"


# Every name this module writes -- and the 3.x attempt name it no longer writes, so the
# first 4.0 run in an upgraded directory clears a stale cv.attempt-N.md rather than leaving
# it beside the new run.json looking current.
_OWNED = re.compile(r"run\.json|cv\.rendered\.md|prompt\.attempt-\d+\.txt"
                    r"|reply\.attempt-\d+\.txt|cv\.attempt-\d+\.md")
```

`_write_record` gains keyword parameters `skills_dropped`, `bullets_trimmed`, `attribution_check_off` written into the record dict beside `"terms"`, and `finish(result)` passes `skills_dropped=list(getattr(result, "skills_dropped", []))`, `bullets_trimmed=list(getattr(result, "bullets_trimmed", []))`, `attribution_check_off=bool(getattr(result, "attribution_check_off", False))`. `finish_error` passes `[]`, `[]`, `False`.

In `sluice/cv/engine.py::CvResult`, add after `terms`:

```python
    skills_dropped: list = field(default_factory=list)     # #365: picks off the pool, over the cap
    bullets_trimmed: list = field(default_factory=list)    # bullets beyond a role's budget
    attribution_check_off: bool = False                    # no verified entry declares Tools:
```

In `docs/USAGE.md`'s "Diagnostic artefacts" table, replace the `cv.attempt-N.md` row with:

```markdown
| `reply.attempt-N.txt` | attempt N's reply exactly as the backend returned it, before anything read it. `.txt` because a reply can carry chat around its JSON |
```

and add `skills_dropped`, `bullets_trimmed` and `attribution_check_off` to the `run.json` row's key list, after `terms`. Add one sentence after the table: "A directory holding a 3.x run's `cv.attempt-N.md` files has them cleared by the next run, like the rest of the set."

- [ ] **Step 4: Run the touched tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_run_artefacts.py -q && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/cv/artefacts.py sluice/cv/engine.py tests/test_cv_run_artefacts.py docs/USAGE.md
git commit -F - <<'EOF'
feat(cv): save each reply as reply.attempt-N.txt and record the selection report

A reply may be chat-wrapped, so its artefact no longer claims to be a CV.
A 3.x run's cv.attempt-N.md files are still cleared by the next run.
run.json gains skills_dropped, bullets_trimmed and attribution_check_off.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 16: The structured run loop, built beside `run_one`

Spec §6.0 (the order per attempt and after the loop), §6.4, §6.6, §7.1, §7.4, §9.1 (the layout refusal), §12.1 (Retention, Audit scope, Vault text, Budgets, Provenance), §12.2 (engine contract). The loop lands as `run_one_structured`, called only by its own tests; Task 18 makes it `run_one` and deletes the text loop. Every refusal, the retry contract, the retention rule and the hold/write tail are `_run_one`'s, carried over; what an ATTEMPT is changes.

**Files:**
- Modify: `sluice/cv/engine.py` (add `_IDENTITY_REFUSAL`, `_LAYOUT_REFUSAL`, `run_one_structured`, `_run_structured`; widen `CvResult.error`'s comment)
- Create: `tests/structured_cv.py` (shared fakes for the structured loop; a helper module, not collected)
- Test: `tests/test_cv_structured_engine.py`

**Interfaces:**
- Consumes: `build_slots` (Task 5); `CvLayout`, `CV_LAYOUT_RELPATH` (Task 4); `extract_json`, `parse_reply`, `Reply` (Task 8); `named_entries`, `build_pool`, `select`, `zero_bullet_findings` (Task 9); `assemble`, `to_text`, `audit_text`, `model_lines` (Task 10); `entry_facts`, `check_selection` (Task 11); `compose_structured` (Task 12); `render_structured_bundle`, `render_audit_bundle`, `term_vocabulary` (Task 13); `Renderer.render(document, …)` (Task 14); `RunArtefacts.composed` writing `reply.attempt-N.txt` and `CvResult`'s three new fields (Task 15).
- Produces:
  - `run_one_structured(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run=False, guard_existing_cv=False, policy=StalenessPolicy(), usage=None) -> CvResult` — `run_one`'s signature; Task 18 renames it to `run_one`
  - `_IDENTITY_REFUSAL`, `_LAYOUT_REFUSAL: str` — the `skipped-config` reasons, carried on `CvResult.error` so a caller names the right note
  - `tests/structured_cv.py`: `CANDIDATE`, `LAYOUT`, `layout_with(*, role0=None, **top)`, `ENTRIES`, `SKILLS`, `PREFIX_MAP`, `GOOD_R1`, `GOOD_R2`, `reply(profile=…, roles=None, skills=…, **extra) -> str`, `Note`, `FakeVault`, `Cache`, `RecordingRenderer`, `ReplyBackend`, `cfg(**over)`

- [ ] **Step 1: Write the shared fakes**

Create `tests/structured_cv.py`:

```python
"""Fakes for the structured CV loop (#364/#365/#368).

A vault that holds a CV Layout and evidence by kind, a backend that answers each compose
with the next scripted reply and records what the composer, the auditor and the voice
judge were each shown, a renderer that records the DOCUMENT it is handed, and a reply
builder. Imported by the structured-engine tests; never collected itself (no `test_`
prefix), like tests/template_content.py.
"""
import dataclasses
import json
import os

from sluice.core.backends import Completion
from sluice.core.protocols import CandidateProfile, CvLayout, LayoutRole

# The first line of each prompt the loop sends: the backend routes on it, and an
# unrecognised prompt raises rather than getting a default answer.
COMPOSE_FIRST_LINE = "Compose a tailored CV for"            # cv/compose.py::build_structured_prompt
AUDIT_FIRST_LINE = "You are auditing a CV for fabrication."  # cv/audit.py::build_audit_prompt
VOICE_FIRST_LINE = "You are judging the VOICE of a CV"       # cv/voice.py::build_voice_prompt

CANDIDATE = CandidateProfile(forenames="Jane", surname="Roe", mobile="+1 555 0100")

LAYOUT = CvLayout(
    roles=(LayoutRole("Example Alpha", "02/2023", "present", location="Example Location A",
                      title="SYNTHETIC-TITLE-1"),
           LayoutRole("Example Beta", "06/2020", "01/2023", location="Example Location B",
                      title="SYNTHETIC-TITLE-2")),
    certificates=("Example Scrum Master",), education=("Example University, BSc Example",))


def layout_with(*, role0=None, **top):
    """LAYOUT with its first role's fields, and any top-level fields, replaced."""
    roles = list(LAYOUT.roles)
    if role0:
        roles[0] = dataclasses.replace(roles[0], **role0)
    return dataclasses.replace(LAYOUT, roles=tuple(roles), **top)


# One verified entry per role, each with figures of its own. EA1 declares `Tools:`, so the
# misattributed-tool check is ON and only EA1 licenses Examplelang. `CI` in EA1's body
# keeps a bullet naming it free of an unbundled-term finding.
ENTRIES = [
    {"title": "Grew the team", "company": "Example Alpha", "best_for": "delivery",
     "category": "people", "metrics": "3 8", "body": "Grew 3 to 8 with CI.",
     "fields": {"Tools": "Examplelang"}},
    {"title": "Cut the build time", "company": "Example Beta", "best_for": "platform",
     "category": "engineering", "metrics": "40", "body": "Cut builds by 40 percent.",
     "fields": {}},
]
SKILLS = [{"title": "Example Query", "best_for": "data", "category": "", "metrics": "",
           "body": "", "fields": {"Domain": "data"}}]
PREFIX_MAP = {"Example Alpha": "EA", "Example Beta": "EB"}

# One clean, cited bullet per role: every figure licensed by the cited entry, the one tool
# declared by it, no slop stem, no unbundled term.
GOOD_R1 = {"text": "Grew the team from 3 to 8 on Examplelang", "cites": ["EA1"]}
GOOD_R2 = {"text": "Cut build time by 40%", "cites": ["EB1"]}


def reply(profile="I build reliable systems.", roles=None, skills=("Examplelang",),
          **extra):
    """A reply's JSON text; `roles` defaults to GOOD_R1 under R1 and GOOD_R2 under R2."""
    obj = {"profile": profile,
           "roles": {"R1": [GOOD_R1], "R2": [GOOD_R2]} if roles is None else roles,
           "skills": list(skills), **extra}
    return json.dumps(obj)


def cfg(**over):
    """A CvConfig writing only inside the per-test sandbox (HOME is a fresh tmp_path)."""
    from sluice.cv.config import CvConfig
    c = CvConfig()
    c.output_dir = os.path.join(os.environ["HOME"], "cvout")
    c.served_dir = os.path.join(os.environ["HOME"], "cvserved")
    c.prefix_map = dict(PREFIX_MAP)
    for key, value in over.items():
        setattr(c, key, value)
    return c


class Note:
    def __init__(self, fm=None,
                 path="Job Applications/Job Leads/Example Alpha - SYNTHETIC-ROLE.md"):
        self.fm = {"status": "shortlist", "company": "Example Alpha",
                   "role": "SYNTHETIC-ROLE", **(fm or {})}
        self.ref, self.slug = path, path.split("/")[-1][:-3]


class FakeVault:
    """The Store members the structured CV path drives, and no others. Each signature is
    pinned to the real Vault's by test_cv_structured_engine.py's conformance row."""

    def __init__(self, *, layout=LAYOUT, experience=None, skills=None,
                 candidate=CANDIDATE, notes=(), skills_error=None):
        self._layout = layout
        self._evidence = {"experience": list(ENTRIES if experience is None else experience),
                          "skills": list(SKILLS if skills is None else skills)}
        self._candidate, self._notes = candidate, list(notes)
        self._skills_error = skills_error
        self.tailored, self.holds = {}, {}

    def read_cv_layout(self):
        return self._layout

    def read_candidate_profile(self):
        return self._candidate

    def read_evidence(self, kind, verified_only=True):
        if kind == "skills" and self._skills_error is not None:
            raise self._skills_error
        return [dict(e) for e in self._evidence.get(kind, [])]

    def read_leads(self, statuses=None):
        return list(self._notes)

    def set_tailored_cv(self, ref, value, *, only_if_absent=False):
        if only_if_absent and ref in self.tailored:
            return False
        self.tailored[ref] = value
        return True

    def hold_for_signoff(self, ref, *, pending, claims):
        if ref in self.tailored:
            return False
        self.holds[ref] = (pending, json.loads(claims))
        return True


class Cache:
    """A dossier cache that counts fetches: the only witness that a refusal spent nothing."""

    def __init__(self):
        self.calls = 0

    def get_or_build(self, fm):
        self.calls += 1
        return {"jd": {"markdown": "we value delivery"}}

    def jd_arrived(self, dossier):
        return True


class RecordingRenderer:
    """Records the DOCUMENT and directory it was handed, and writes a stand-in PDF there so
    the real `serve` runs."""

    def __init__(self):
        self.rendered = []

    def render(self, document, out_dir, *, neutral_name="CV.pdf"):
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, neutral_name)
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4 stand-in")
        self.rendered.append((document, out_dir))
        return path


class ReplyBackend:
    """Answers the Nth compose with the Nth scripted reply, and each audit and voice check
    with `audit_out` / `voice_out` -- one answer for every call, or a list giving the Nth
    call the Nth answer. Any answer that is an exception instance is RAISED instead, which is
    how a test scripts a backend failure. Records each prompt by kind."""
    label = "scripted"

    def __init__(self, replies, *, audit_out="supported\tclaim\tEA1", voice_out=""):
        self.replies = list(replies)
        self.audit_out, self.voice_out = audit_out, voice_out
        self.compose_prompts, self.audit_prompts, self.voice_prompts = [], [], []

    @staticmethod
    def _answer(answers, calls, kind):
        if isinstance(answers, list):
            assert len(calls) <= len(answers), (
                f"{kind} call {len(calls)} but only {len(answers)} answers scripted")
            answers = answers[len(calls) - 1]
        if isinstance(answers, BaseException):
            raise answers
        return Completion(answers)

    def complete(self, prompt):
        first = prompt.splitlines()[0] if prompt else ""
        if first.startswith(COMPOSE_FIRST_LINE):
            self.compose_prompts.append(prompt)
            assert len(self.compose_prompts) <= len(self.replies), (
                f"compose call {len(self.compose_prompts)} but only {len(self.replies)} "
                "replies scripted -- the retry budget is one")
            return self._answer(self.replies, self.compose_prompts, "compose")
        if first.startswith(AUDIT_FIRST_LINE):
            self.audit_prompts.append(prompt)
            return self._answer(self.audit_out, self.audit_prompts, "audit")
        if first.startswith(VOICE_FIRST_LINE):
            self.voice_prompts.append(prompt)
            return self._answer(self.voice_out, self.voice_prompts, "voice")
        raise AssertionError(f"ReplyBackend: unrecognised prompt {first!r}")

    def audited(self):
        """The text each audit was asked about: cv/audit.py puts it after `=== CV ===` and
        appends one newline."""
        out = []
        for prompt in self.audit_prompts:
            body = prompt.partition("=== CV ===\n")[2]
            assert body, "cv/audit.py no longer carries the audited text under '=== CV ==='"
            out.append(body[:-1])
        return out

    def assert_consumed(self):
        assert len(self.compose_prompts) == len(self.replies), (
            f"{len(self.replies)} replies scripted, {len(self.compose_prompts)} composed")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_cv_structured_engine.py`:

```python
"""The structured CV loop (#364/#365/#368), driven through fakes.

Built beside run_one until Task 18 makes it run_one, so every row calls
run_one_structured. Spec §6.0 (the order per attempt), §6.4 (audit scope), §7.1
(assembly), §9.1 (the layout refusal), §12.1 (Retention, Audit scope, Vault text, Budgets,
Provenance) and §12.2 (the engine contract).
"""
import dataclasses
import inspect
import json
import os
import typing

import pytest

from sluice.core.backends import BackendError
from sluice.core.protocols import (CANDIDATE_PROFILE_RELPATH, CV_LAYOUT_RELPATH,
                                   CandidateProfile, CvDocument)
from sluice.cv.document import to_text
from sluice.cv.engine import run_one_structured
from tests.structured_cv import (ENTRIES, GOOD_R1, GOOD_R2, Cache, FakeVault, Note,
                                 RecordingRenderer, ReplyBackend, cfg, layout_with, reply)

CAPPED = layout_with(role0={"bullets_max": 1})
OUT_SLUG = "example-alpha-synthetic-role"     # cv/engine.py::_slug of the Note's company/role

# A hard-dirty reply (an invented figure) and a hard-clean reply with one slop stem.
DIRTY = reply(roles={"R1": [{"text": "Grew the team to 99", "cites": ["EA1"]}],
                     "R2": [GOOD_R2]})
STYLED = reply(roles={"R1": [{"text": "Fostered a team that grew from 3 to 8",
                              "cites": ["EA1"]}],
                      "R2": [GOOD_R2]})
# Spec §12.1 Retention: attempt 1 is hard-clean with one surviving style finding plus drops
# (an off-pool pick, an over-budget bullet); attempt 2 is hard-dirty with DIFFERENT drops.
ATTEMPT_1 = reply(roles={"R1": [{"text": "Fostered a team that grew from 3 to 8",
                                 "cites": ["EA1"]},
                                {"text": "Ran the Examplelang rollout", "cites": ["EA1"]}],
                         "R2": [GOOD_R2]},
                  skills=["Examplelang", "Example Ghost"])
ATTEMPT_2 = reply(profile="I ship platforms.",
                  roles={"R1": [{"text": "Grew the team from 3 to 99", "cites": ["EA1"]}],
                         "R2": [GOOD_R2]},
                  skills=["Example Zephyr"])


def _run(replies, *, vault=None, config=None, dry_run=False, **backend_kw):
    be, rend, v = ReplyBackend(replies, **backend_kw), RecordingRenderer(), vault or FakeVault()
    res = run_one_structured(Note(), v, config or cfg(), be, Cache(), renderer=rend,
                             dry_run=dry_run)
    return res, be, rend, v


def _out_dir():
    return os.path.join(cfg().output_dir, OUT_SLUG)


# --- refusals before any spend ------------------------------------------------------

def test_a_vault_without_a_cv_layout_is_refused_before_any_spend():
    cache, be = Cache(), ReplyBackend([])
    res = run_one_structured(Note(), FakeVault(layout=None), cfg(), be, cache,
                             renderer=RecordingRenderer())
    assert (res.status, cache.calls, be.compose_prompts) == ("skipped-config", 0, [])
    assert CV_LAYOUT_RELPATH in res.error


def test_a_blank_identity_is_refused_naming_the_candidate_profile():
    res, be, _rend, _v = _run([], vault=FakeVault(candidate=CandidateProfile()))
    assert (res.status, be.compose_prompts) == ("skipped-config", [])
    assert CANDIDATE_PROFILE_RELPATH in res.error


# --- one attempt, end to end ----------------------------------------------------------

def test_a_clean_reply_renders_the_document_sluice_assembled():
    res, be, rend, v = _run([reply()])
    assert res.status == "rendered"
    assert (len(be.compose_prompts), len(be.audit_prompts)) == (1, 1)
    [(doc, _out)] = rend.rendered
    assert isinstance(doc, CvDocument)
    assert [(r.company, r.dates, r.location, r.title, r.bullets) for r in doc.work] == [
        ("Example Alpha", "02/2023–present", "Example Location A", "SYNTHETIC-TITLE-1",
         [GOOD_R1["text"]]),
        ("Example Beta", "06/2020–01/2023", "Example Location B", "SYNTHETIC-TITLE-2",
         [GOOD_R2["text"]])]
    assert (doc.name, doc.contact, doc.skills, doc.certificates, doc.education) == (
        "JANE ROE", "+1 555 0100", ["Examplelang"], ["Example Scrum Master"],
        ["Example University, BSc Example"])
    assert list(v.tailored) == [Note().ref] and v.tailored[Note().ref].startswith(res.served)
    assert (res.skills_dropped, res.bullets_trimmed, res.attribution_check_off) == (
        [], [], False)


def test_the_composer_is_shown_the_role_slots_the_pool_and_each_entrys_tools():
    _res, be, _rend, _v = _run([reply()])
    [prompt] = be.compose_prompts
    assert ("R1: Example Alpha | 02/2023–present | SYNTHETIC-TITLE-1 | may cite: EA1 | "
            "any number of bullets") in prompt
    assert "- Example Query" in prompt and "- Examplelang" in prompt
    assert "tools=Examplelang" in prompt


def test_an_unsupported_audit_flag_holds_the_cv_for_sign_off():
    res, _be, _rend, v = _run([reply()], audit_out="unsupported\tclaim\tNONE")
    assert (res.status, v.tailored) == ("needs-signoff", {})
    [(pending, claims)] = v.holds.values()
    assert pending.startswith(res.served) and claims[0].startswith("unsupported")


# --- the retry and what it is fed -----------------------------------------------------

def test_a_reply_that_is_not_json_feeds_the_retry_and_a_second_one_skips_the_lead():
    res, be, rend, _v = _run(["Sure! Here is your CV.", "Still no JSON."])
    assert len(be.compose_prompts) == 2
    assert "- REPLY: no JSON object in the reply" in be.compose_prompts[1]
    assert (res.status, res.violations, rend.rendered) == (
        "skipped-gate", ["REPLY: no JSON object in the reply"], [])


def test_a_retry_lists_the_previous_replys_drops():
    first = reply(roles={"R1": [{"text": "Grew the team to 99", "cites": ["EA1"]}],
                         "R2": [GOOD_R2]},
                  skills=["Example Ghost"])
    _res, be, _rend, _v = _run([first, reply()])
    retry = be.compose_prompts[1]
    assert "=== DROPPED FROM YOUR PREVIOUS REPLY" in retry
    assert "- 'Example Ghost': not one of your skills" in retry


# --- the engine contract (spec §12.2) -------------------------------------------------

def test_the_retry_happens_exactly_once():
    res, be, rend, _v = _run([DIRTY, DIRTY])
    assert len(be.compose_prompts) == 2
    assert (res.status, rend.rendered) == ("skipped-gate", [])


@pytest.mark.parametrize("sequence,status,composes", [
    (["clean"], "rendered", 1),
    (["dirty", "clean"], "rendered", 2),
    (["dirty", "dirty"], "skipped-gate", 2),
    (["styled", "dirty"], "rendered", 2)])
def test_skipped_gate_if_and_only_if_no_attempt_was_hard_clean(sequence, status, composes):
    replies = {"clean": reply(), "dirty": DIRTY, "styled": STYLED}
    res, be, _rend, _v = _run([replies[s] for s in sequence])
    assert (len(be.compose_prompts), res.status) == (composes, status)


def test_the_retained_attempt_is_rendered_with_its_own_drops():
    res, be, rend, _v = _run([ATTEMPT_1, ATTEMPT_2], vault=FakeVault(layout=CAPPED))
    assert len(be.compose_prompts) == 2
    be.assert_consumed()
    [(doc, _out)] = rend.rendered
    assert doc.work[0].bullets == ["Fostered a team that grew from 3 to 8"]
    assert res.skills_dropped == ["'Example Ghost': not one of your skills"]
    assert res.bullets_trimmed == ["R1 (Example Alpha): kept 1 of 2"]
    assert res.status == "rendered" and [s.split(":")[0] for s in res.slop] == ["SLOP foster"]


def test_the_retained_attempt_is_the_one_audited():
    _res, be, _rend, _v = _run([ATTEMPT_1, ATTEMPT_2], vault=FakeVault(layout=CAPPED))
    [audited] = be.audited()
    assert "Fostered a team that grew from 3 to 8 [EA1]" in audited
    assert "99" not in audited and "I ship platforms." not in audited


def test_a_first_compose_that_raises_bins_the_lead():
    with pytest.raises(BackendError):
        _run([BackendError("compose timeout")])


def test_a_retry_that_raises_ships_the_draft_attempt_one_earned():
    res, be, rend, _v = _run([STYLED, BackendError("compose timeout")])
    assert (len(be.compose_prompts), res.status) == (2, "rendered")
    [(doc, _out)] = rend.rendered
    assert doc.work[0].bullets == ["Fostered a team that grew from 3 to 8"]


def test_the_voice_check_is_not_spent_on_a_hard_dirty_attempt():
    _res, be, _rend, _v = _run([DIRTY, DIRTY], config=cfg(voice_check=True))
    assert be.voice_prompts == []


def test_voice_check_off_spends_no_extra_call():
    _res, be, _rend, _v = _run([reply()])
    assert (len(be.compose_prompts), len(be.audit_prompts), be.voice_prompts) == (1, 1, [])


def test_the_voice_judge_is_shown_only_the_models_text():
    _res, be, _rend, _v = _run([reply()], config=cfg(voice_check=True))
    [prompt] = be.voice_prompts
    assert prompt.partition("=== EXCERPT ===\n")[2] == (
        f"I build reliable systems.\n{GOOD_R1['text']}\n{GOOD_R2['text']}\n")


# --- audit scope (spec §6.4) ----------------------------------------------------------

def test_the_auditor_reads_only_the_models_text_and_the_tools_of_what_it_cites():
    capped = reply(roles={"R1": [GOOD_R1, {"text": "Ran the Examplelang rollout",
                                           "cites": ["EA1"]}],
                          "R2": [GOOD_R2]},
                   skills=["Example Query"])
    _res, be, _rend, _v = _run([capped], vault=FakeVault(layout=CAPPED))
    [audited] = be.audited()
    assert audited == ("PROFILE\nI build reliable systems.\n\nExample Alpha\n"
                       f"- {GOOD_R1['text']} [EA1]\n\nExample Beta\n- {GOOD_R2['text']} [EB1]\n")
    for absent in ("02/2023", "Example Scrum Master", "Example University", "Example Query",
                   "Ran the Examplelang rollout"):
        assert absent not in audited, absent
    assert "tools=Examplelang" in be.audit_prompts[0]


# --- vault text is never refused (spec §6.1, §6.3) -------------------------------------

DASHED = layout_with(role0={"heading": "Example Alpha — Example Northgate",
                            "employers": ("Example Alpha",)},
                     certificates=("Example Synergy — Master",),
                     education=("Example University — BSc Example",))
SYNERGY_SKILL = [{"title": "Example Synergy", "best_for": "", "category": "", "metrics": "",
                  "body": "", "fields": {"Domain": "people"}}]


def test_vault_text_with_an_em_dash_or_a_slop_stem_renders_without_a_finding():
    res, be, rend, _v = _run([reply(skills=["Example Synergy"])],
                             vault=FakeVault(layout=DASHED, skills=SYNERGY_SKILL))
    assert (res.status, res.violations, res.slop, len(be.compose_prompts)) == (
        "rendered", [], [], 1)
    [(doc, _out)] = rend.rendered
    assert doc.work[0].company == "Example Alpha — Example Northgate"
    assert (doc.skills, doc.certificates) == (["Example Synergy"], ["Example Synergy — Master"])


def test_the_same_em_dash_or_slop_stem_in_a_bullet_is_the_models_and_is_found():
    dashed = reply(roles={"R1": [{"text": "Grew the team — from 3 to 8", "cites": ["EA1"]}],
                          "R2": [{"text": "Built synergy while cutting build time by 40%",
                                  "cites": ["EB1"]}]})
    res, _be, _rend, _v = _run([dashed, dashed])
    assert (res.status, res.violations) == ("skipped-gate", [])
    assert any(s.startswith("SLOP EM-DASH") for s in res.slop)
    assert any(s.startswith("SLOP synergy") for s in res.slop)


# --- budgets (spec §4.1, D10) -----------------------------------------------------------

def test_an_over_budget_bullet_costs_no_retry_and_is_never_audited():
    over = reply(roles={"R1": [GOOD_R1, {"text": "Leveraged Examplezz to reach 99",
                                         "cites": ["EA1"]}],
                        "R2": [GOOD_R2]})
    res, be, _rend, _v = _run([over], vault=FakeVault(layout=CAPPED))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    assert res.bullets_trimmed == ["R1 (Example Alpha): kept 1 of 2"]
    assert (res.violations, res.slop, res.terms) == ([], [], [])
    assert "Examplezz" not in be.audited()[0]


def test_a_layout_of_zero_budgets_renders_headings_only():
    zero = dataclasses.replace(CAPPED, roles=tuple(
        dataclasses.replace(r, bullets_max=0) for r in CAPPED.roles))
    res, be, rend, _v = _run([reply(roles={})], vault=FakeVault(layout=zero))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    [(doc, _out)] = rend.rendered
    assert [(r.company, r.bullets) for r in doc.work] == [
        ("Example Alpha", []), ("Example Beta", [])]


# --- the attribution switch (spec §4.2, §6.6) -------------------------------------------

MISATTRIBUTED = reply(roles={"R1": [GOOD_R1],
                             "R2": [{"text": "Cut build time by 40% with Examplelang",
                                     "cites": ["EB1"]}]})


def test_a_misattributed_tool_is_refused_while_any_entry_declares_tools():
    res, _be, _rend, _v = _run([MISATTRIBUTED, MISATTRIBUTED])
    assert (res.status, res.attribution_check_off) == ("skipped-gate", False)
    assert any(v.startswith("MISATTRIBUTED TOOL 'Examplelang'") for v in res.violations)


def test_with_no_entry_declaring_tools_the_check_is_off_and_the_result_says_so():
    # Examplelang moves into EA1's body so the unbundled-term check still knows the word:
    # this row isolates the switch.
    no_tools = [{**ENTRIES[0], "fields": {}, "body": "Grew 3 to 8 with CI on Examplelang."},
                {**ENTRIES[1], "fields": {}}]
    res, _be, _rend, _v = _run([MISATTRIBUTED], vault=FakeVault(experience=no_tools))
    assert (res.status, res.attribution_check_off, res.violations) == ("rendered", True, [])


# --- the Skills Inventory never costs a lead (#165) --------------------------------------

def test_an_unreadable_skills_inventory_composes_without_its_framing_or_its_names():
    res, be, _rend, _v = _run([reply()], vault=FakeVault(skills_error=OSError("unreadable")))
    assert (res.status, res.skills_unreadable) == ("rendered", True)
    [prompt] = be.compose_prompts
    assert "Example Query" not in prompt and "- Examplelang" in prompt


# --- what a run leaves behind (spec §7.4) ------------------------------------------------

def test_each_reply_is_kept_raw_and_the_run_record_carries_the_selection_report():
    chatty = "Here you go: " + reply(
        roles={"R1": [GOOD_R1, {"text": "Ran the Examplelang rollout", "cites": ["EA1"]}],
               "R2": [GOOD_R2]},
        skills=["Examplelang", "Example Ghost"])
    res, _be, _rend, _v = _run([chatty], vault=FakeVault(layout=CAPPED))
    with open(os.path.join(_out_dir(), "reply.attempt-1.txt"), encoding="utf-8") as fh:
        assert fh.read() == chatty
    with open(os.path.join(_out_dir(), "run.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    assert (record["skills_dropped"], record["bullets_trimmed"],
            record["attribution_check_off"]) == (
        res.skills_dropped, res.bullets_trimmed, False)
    assert record["skills_dropped"] == ["'Example Ghost': not one of your skills"]
    assert record["bullets_trimmed"] == ["R1 (Example Alpha): kept 1 of 2"]


def test_a_dry_run_audits_and_records_but_renders_and_writes_nothing():
    res, be, rend, v = _run([reply()], dry_run=True)
    assert (res.status, rend.rendered, v.tailored, v.holds) == ("dry-run", [], {}, {})
    assert len(be.audit_prompts) == 1
    with open(os.path.join(_out_dir(), "cv.rendered.md"), encoding="utf-8") as fh:
        assert f"- {GOOD_R1['text']} [EA1]" in fh.read()


# --- provenance (spec §12.1, four parts) -------------------------------------------------

# Part 1: every leaf of the document, and where its value comes from. Closed: no default
# arm, so a field added to CvDocument or Role without a decision fails here.
PROVENANCE = {
    "name": "vault", "contact": "vault", "profile": "model",
    "work[].company": "vault", "work[].dates": "vault", "work[].location": "vault",
    "work[].title": "vault", "work[].bullets[]": "model",
    "skills[]": "model-selected, vault-valued",
    "certificates[]": "vault", "education[]": "vault",
}


def _leaf_paths(cls, prefix=""):
    hints, out = typing.get_type_hints(cls), set()
    for f in dataclasses.fields(cls):
        t, path = hints[f.name], prefix + f.name
        if typing.get_origin(t) is list:
            (inner,) = typing.get_args(t)
            out |= (_leaf_paths(inner, path + "[].") if dataclasses.is_dataclass(inner)
                    else {path + "[]"})
        elif dataclasses.is_dataclass(t):
            out |= _leaf_paths(t, path + ".")
        else:
            out.add(path)
    return out


def _leaves(doc):
    out = {"name": [doc.name], "contact": [doc.contact], "profile": [doc.profile],
           "skills[]": list(doc.skills), "certificates[]": list(doc.certificates),
           "education[]": list(doc.education),
           "work[].bullets[]": [b for r in doc.work for b in r.bullets]}
    for key in ("company", "dates", "location", "title"):
        out[f"work[].{key}"] = [getattr(r, key) for r in doc.work]
    return out


def test_every_document_leaf_has_a_declared_provenance():
    assert _leaf_paths(CvDocument) == set(PROVENANCE)


def test_every_fixture_leaf_is_non_empty_and_unique():
    # Part 2: a scope assertion on VALUES, so the canary rows below can attribute every
    # rendered string to exactly one source.
    _res, _be, rend, _v = _run([reply(skills=["Examplelang", "Example Query"])])
    [(doc, _out)] = rend.rendered
    leaves = _leaves(doc)
    assert set(leaves) == set(PROVENANCE)
    values = [value for group in leaves.values() for value in group]
    assert all(values) and len(values) == len(set(values)), values


CANARIES = {"name": "CANARY-NAME", "contact": "CANARY-CONTACT", "company": "CANARY-COMPANY",
            "dates": "CANARY-DATES", "location": "CANARY-LOCATION", "title": "CANARY-TITLE",
            "certificates": ["CANARY-CERT"], "education": ["CANARY-EDU"],
            "work": ["CANARY-WORK"]}


def test_a_hostile_reply_cannot_reach_a_vault_owned_field():
    # Part 3: the parser ignores unknown keys, at the top level and inside a bullet, so a
    # reply may carry keys named like every vault-owned field. The row first proves the CV
    # RENDERED, so it cannot pass by rendering nothing.
    bullet = {**GOOD_R1, **{k: v for k, v in CANARIES.items() if isinstance(v, str)}}
    hostile = reply(roles={"R1": [bullet], "R2": [GOOD_R2]}, **CANARIES)
    res, be, rend, _v = _run([hostile])
    assert (res.status, len(rend.rendered)) == ("rendered", 1)
    [(doc, out_dir)] = rend.rendered
    assert doc.work and doc.profile
    with open(os.path.join(out_dir, "cv.rendered.md"), encoding="utf-8") as fh:
        recorded = fh.read()
    for surface in (to_text(doc), recorded, be.audited()[0]):
        assert "CANARY" not in surface


def test_the_document_carries_the_retained_attempts_text():
    # Part 4: through the engine, on the Retention sequence.
    _res, _be, rend, _v = _run([ATTEMPT_1, ATTEMPT_2], vault=FakeVault(layout=CAPPED))
    [(doc, _out)] = rend.rendered
    first = json.loads(ATTEMPT_1)
    assert doc.profile == first["profile"]
    assert doc.work[0].bullets == [first["roles"]["R1"][0]["text"]]
    assert doc.skills == ["Examplelang"]


# --- the fake is the store it fakes -------------------------------------------------------

def test_the_fake_vault_matches_the_store_it_fakes():
    from sluice.core.protocols import Store
    from sluice.core.vault import Vault
    shared = sorted(n for n in dir(Store) if not n.startswith("_")
                    and callable(getattr(Store, n)) and hasattr(FakeVault, n))
    assert {"read_cv_layout", "read_candidate_profile", "read_evidence", "read_leads",
            "set_tailored_cv", "hold_for_signoff"} <= set(shared), shared
    for name in shared:
        fake, real = (inspect.signature(getattr(c, name)) for c in (FakeVault, Vault))
        assert list(fake.parameters) == list(real.parameters), (name, fake, real)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cv_structured_engine.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'run_one_structured' from 'sluice.cv.engine'`.

- [ ] **Step 4: Implement the loop**

In `sluice/cv/engine.py`, add to the imports:

```python
from sluice.core.layout import build_slots
from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH, CV_LAYOUT_RELPATH
from sluice.cv.document import assemble, audit_text, model_lines, to_text
from sluice.cv.reply import Reply, extract_json, parse_reply
from sluice.cv.selection import build_pool, named_entries, select, zero_bullet_findings
from sluice.cv.validate import check_selection, entry_facts
```

Replace `CvResult.error`'s comment with:

```python
    # Why this lead ended without a CV, as a message rather than an exception so a result
    # stays a plain value to print: a backend failure (#333, on `backend-unavailable` and on
    # the single-lead path's `error`), or which note refused a `skipped-config` lead (the
    # Candidate Profile or the CV Layout, #364/#365/#368), so a caller names the right one.
    # Empty otherwise.
```

Then add, after `run_one`/`_run_one`:

```python
# --- #364/#365/#368: structured composition --------------------------------------------
# TRANSITIONAL until Task 18, which makes this `run_one` and deletes the text loop above.
# Every refusal, the retry contract, the retention rule and the hold/write tail are
# `_run_one`'s, carried over with their reasons (see the comments there). What changed is
# what an attempt IS: a JSON reply read and checked as data, against slots built from the
# CV Layout, instead of a CV document re-read by three grammars.

_IDENTITY_REFUSAL = (
    f"the vault's Candidate Profile note ({CANDIDATE_PROFILE_RELPATH}) has no declared name "
    "or contact details -- fill it in before composing (the name becomes the PDF's "
    "headline, and the contact block is emitted verbatim)")
_LAYOUT_REFUSAL = (
    f"the vault has no CV Layout note ({CV_LAYOUT_RELPATH}) -- create it before composing: "
    "every role heading, date, location and title on the CV comes from it")


def run_one_structured(note, vault, cvcfg, backend, dossier_cache, *, renderer,
                       dry_run=False, guard_existing_cv=False, policy=StalenessPolicy(),
                       usage=None) -> CvResult:
    # `run_one`'s wrapper, unchanged: every way out of the body finishes the artefacts here.
    record = _artefacts.RunArtefacts(dry_run=dry_run)
    try:
        result = _run_structured(note, vault, cvcfg, backend, dossier_cache,
                                 renderer=renderer, dry_run=dry_run,
                                 guard_existing_cv=guard_existing_cv, policy=policy,
                                 usage=usage, record=record)
    except Exception as e:
        record.finish_error(e)
        e.artefacts_failed = record.failed
        raise
    record.finish(result)
    result.artefacts_failed = record.failed
    return result


def _run_structured(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run,
                    guard_existing_cv, policy, usage, record) -> CvResult:
    fm = note.fm
    # The refusals before any spend, in `_run_one`'s order: not shortlisted, held for
    # sign-off (#60), stale (#9), no declared identity (#107).
    if _status.normalize(fm.get("status", "")) != "shortlist":
        return CvResult(note.ref, "skipped-selection")
    if fm.get("pending_cv"):
        return CvResult(note.ref, "skipped-needs-signoff")
    if policy.blocks(fm.get("last_seen", "")):
        return CvResult(note.ref, "skipped-stale")
    candidate = vault.read_candidate_profile()
    cv_name = full_name(candidate)
    if not cv_name.strip() or not contact_block(candidate).strip():
        return CvResult(note.ref, "skipped-config", error=_IDENTITY_REFUSAL)
    # The CV Layout is the structure every CV is assembled into (spec §4.1), so without one
    # there is nothing to compose INTO. `missing_prerequisites` refuses the whole run first;
    # this catches a note deleted since, before any spend. `None` is never read as zero
    # roles. A malformed or unreadable note RAISES here, and run_batch records that lead as
    # `error`: naming the problem is the prerequisite check's job, once per run.
    layout = vault.read_cv_layout()
    if layout is None:
        return CvResult(note.ref, "skipped-config", error=_LAYOUT_REFUSAL)

    company, role = fm.get("company", ""), fm.get("role", "")
    framing = _compose.framing_lines(*(fm.get(key, "") for key in FRAMING_KEYS))
    jd, dossier_failed = "", False
    try:
        d = dossier_cache.get_or_build(fm)
        jd = (d.get("jd") or {}).get("markdown", "")
        # #18/#169: a fetch that produced no JD earns the flag, and keeps the text it got.
        if not dossier_cache.jd_arrived(d):
            dossier_failed = True
    except Exception as e:
        _log.warning("dossier for %s failed: %s", note.ref, e)
        dossier_failed = True

    try:
        entries = vault.read_evidence("experience", verified_only=True)
        # A broken Skills Inventory never costs a lead (#165): its framing AND its skill
        # names fall back to nothing together. (OSError, ValueError): a non-UTF-8 note
        # raises UnicodeDecodeError, a ValueError.
        skills_unreadable = False
        try:
            skills = vault.read_evidence("skills", verified_only=True)
            named = named_entries(vault.read_evidence)
        except (OSError, ValueError) as e:
            _log.warning("skills inventory for %s unreadable, composing without it: %s",
                         note.ref, e)
            skills, named, skills_unreadable = [], [], True
        # Everything below is derived ONCE, before any compose, from the same bundle: a
        # fault knowable here must not cost an LLM call first.
        b = _bundle.build_bundle(entries, "", cvcfg.negatives, _jd_keywords(role, jd),
                                 cvcfg.prefix_map, skills=skills)
        slots = build_slots(layout, b["entries"])
        slot_ids = [s.id for s in slots]
        facts = entry_facts(b, layout)
        # spec §6.6: the misattributed-tool check runs only while some verified entry
        # declares Tools:, and every result says whether it did.
        attribution_off = not any(f.tools for f in facts.values())
        pool = build_pool(named, entries, decoys=cvcfg.fabrication_decoys)
        bundle_text = _bundle.render_structured_bundle(b)
        audit_bundle_text = _bundle.render_audit_bundle(b)
        vocab = _bundle.term_vocabulary(b, layout)

        out_dir = f"{cvcfg.output_dir}/{_slug(company, role)}"
        record.begin(out_dir, lead=note.slug, entry_ids=[e["id"] for e in b["entries"]],
                     dossier_failed=dossier_failed, skills_unreadable=skills_unreadable)

        retry_findings = retry_drops = None
        violations, slop_err, slop_msgs, term_msgs, voice_flags = [], [], [], [], []
        selection = None
        # The hard-clean attempt with the fewest style/voice findings, as
        # (selection, slop_msgs, term_msgs, voice_flags); `_run_one`'s `best`, holding the
        # SELECTION -- and with it the drop report -- instead of a draft's text.
        best, best_voice_measured = None, False
        for attempt in range(1, 3):
            try:
                raw = _compose.compose_structured(
                    meter(usage, backend, "cv-compose", lead=note.slug), bundle_text, jd,
                    company, role, name=cv_name, slots=slots, pool=pool,
                    skills_max=layout.skills_max, prior_findings=retry_findings,
                    prior_drops=retry_drops, slop_allow=cvcfg.slop_allow,
                    triage_framing=framing, on_prompt=partial(record.prompt, attempt))
            except Exception as e:
                # `_run_one`'s rule: a retry that never returns must not bin a lead attempt
                # 1 already earned; with nothing retained, re-raise.
                record.compose_failed(attempt, e)
                if best is None:
                    raise
                _log.warning("cv retry compose for %s failed (%s); shipping the retained "
                             "hard-clean draft", note.ref, e)
                break
            record.composed(attempt, raw)
            # spec §6.0, per attempt: read the reply, select from it, check the selection.
            # A reply that cannot be read has nothing to select: its REPLY findings are the
            # whole of this attempt's verdict.
            obj = extract_json(raw)
            reply = parse_reply(obj, slot_ids) if isinstance(obj, dict) else obj
            voice_flags, voice_failed, voice_measured = [], False, False
            if not isinstance(reply, Reply):
                violations, slop_err, slop_msgs, term_msgs = list(reply), [], [], []
                selection = None
            else:
                selection = select(reply, slots, pool, layout.skills_max)
                violations = (zero_bullet_findings(selection, slots)
                              + check_selection(selection, slots, facts,
                                                decoys=cvcfg.fabrication_decoys))
                lines = model_lines(selection, slots)
                # The BLOCKING slop tier -- an em dash or a literal `--` -- over the model's
                # texts only (spec §6.1): a vault string carrying one is the user's and
                # renders. Reported in `slop`, beside the phrase tier, as it always has been.
                slop_err = [f"SLOP {label}: {snip}" for _n, text in lines
                            for _ln, label, snip in _slop_hard(text)]
                # The STYLE tier over the text the MODEL wrote and nothing else (spec
                # §6.3): a slop stem inside a certificate or a skill's name is the user's.
                slop_msgs = [f"SLOP {phrase}: {snip}" for _ln, phrase, snip
                             in _slop_phrases(lines, allow=cvcfg.slop_allow)]
                term_msgs = ([f"UNBUNDLED TERM {term!r}: named nowhere in your evidence: "
                              f"{snip}" for _ln, term, snip in _unbundled_terms(lines, vocab)]
                             if cvcfg.term_check else [])
                excerpt = "\n".join(text for _ln, text in lines)
                if not violations and not slop_err:
                    # Opt-in, and never spent on a draft the hard gate already refused or
                    # on blank prose; fails open (see `_run_one`).
                    if cvcfg.voice_check and excerpt.strip():
                        try:
                            _report, voice_flags = run_voice(
                                meter(usage, backend, "cv-voice", lead=note.slug), excerpt)
                            voice_measured = True
                        except Exception as e:
                            _log.warning("voice check for %s failed (%s); treating as "
                                         "clean", note.ref, e)
                            voice_flags, voice_failed = [], True
                    # `_run_one`'s retention rule (#194): fewest style/voice findings, a tie
                    # keeps the later attempt, and an attempt whose voice check failed never
                    # displaces one whose voice was measured.
                    found = len(slop_msgs) + len(term_msgs) + len(voice_flags)
                    if best is None or (
                            found <= len(best[1]) + len(best[2]) + len(best[3])
                            and not (voice_failed and best_voice_measured)):
                        best = (selection, slop_msgs, term_msgs, voice_flags)
                        best_voice_measured = voice_measured
                        record.retained(attempt)
                    if not found:
                        break
            retry_findings = (violations + slop_err + slop_msgs + term_msgs
                              + [f"VOICE: {flag}" for flag in voice_flags])
            # spec §6.2: a drop never causes a retry, but a retry lists them so the model
            # can choose better.
            retry_drops = (list(selection.skills_dropped + selection.bullets_trimmed)
                           if selection is not None else None)

        backend_used = getattr(backend, "label", None)
        if best is None:
            # No attempt was ever hard-clean: every finding describes the LAST attempt.
            return CvResult(
                note.ref, "skipped-gate", violations=violations, slop=slop_err + slop_msgs,
                terms=term_msgs, voice_flags=voice_flags, backend=backend_used,
                dossier_failed=dossier_failed, skills_unreadable=skills_unreadable,
                skills_dropped=list(selection.skills_dropped) if selection else [],
                bullets_trimmed=list(selection.bullets_trimmed) if selection else [],
                attribution_check_off=attribution_off)
        # THE REBIND (see `_run_one`): every reader below takes the RETAINED attempt, and
        # this is the one assignment that makes it so.
        selection, slop_msgs, term_msgs, voice_flags = best
        report = dict(slop=slop_msgs, terms=term_msgs, voice_flags=voice_flags,
                      backend=backend_used, dossier_failed=dossier_failed,
                      skills_unreadable=skills_unreadable,
                      skills_dropped=list(selection.skills_dropped),
                      bullets_trimmed=list(selection.bullets_trimmed),
                      attribution_check_off=attribution_off)

        assembled = assemble(layout, slots, selection, candidate)
        # Kept BEFORE the audit, so a dry run, and a run whose audit or render raises, still
        # leave the document sluice built -- each bullet with its citations -- beside the
        # replies it came from (spec §7.4).
        record.rendering(to_text(assembled.document, cites=assembled.cites))
        # The audit reads what the MODEL wrote, never vault text (spec §6.4). Advisory to
        # the model, but whether it RAN is not (#333): see `_run_one`.
        audit_unavailable = ""
        try:
            _report, audit_flags = run_audit(
                meter(usage, backend, "cv-audit", lead=note.slug),
                audit_text(selection, slots), audit_bundle_text)
        except Exception as e:
            _log.warning("advisory audit failed for %s: %s", note.ref, e)
            audit_flags, audit_unavailable = [], str(e)
        if dry_run:
            return CvResult(note.ref, "dry-run", audit_flags=audit_flags, **report)

        from sluice.cv import render as _render
        pdf = renderer.render(assembled.document, out_dir,
                              neutral_name=cvcfg.neutral_filename)
        record.rendered(pdf)
        served = (_render.serve(pdf, cvcfg.served_dir, served_prefix=cvcfg.served_prefix)
                  if cvcfg.served_dir else None)
        # The hold/write tail is `_run_one`'s, unchanged (#60, #167, #194, #329, #333).
        style_blockers = ([f"style\t{msg}" for msg in slop_msgs + voice_flags]
                          + [f"term\t{msg}" for msg in term_msgs]
                          if cvcfg.style_hold else [])
        unaudited = [f"unaudited\t{audit_unavailable}"] if audit_unavailable else []
        blockers = (
            (unsupported_claims(audit_flags) + unaudited if cvcfg.require_signoff else [])
            + style_blockers)
        if served and blockers:
            held = vault.hold_for_signoff(
                note.ref, pending=f"{served} ({date.today().isoformat()})",
                claims=json.dumps(blockers + framing_entries(framing)))
            if not held:
                return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags, **report)
            return CvResult(note.ref, "needs-signoff", audit_flags=audit_flags,
                            served=served, **report)
        if served:
            wrote = vault.set_tailored_cv(
                note.ref, f"{served} ({date.today().isoformat()})",
                only_if_absent=guard_existing_cv)
            if guard_existing_cv and not wrote:
                return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags, **report)
        return CvResult(note.ref, "rendered", audit_flags=audit_flags, served=served,
                        **report)
    except Exception as e:
        e.dossier_failed = dossier_failed
        raise
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cv_structured_engine.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both. If a row fails, fix the LOOP, never the row: each row encodes a spec rule.

- [ ] **Step 6: Commit**

```bash
git add sluice/cv/engine.py tests/structured_cv.py tests/test_cv_structured_engine.py
git commit -F - <<'EOF'
feat(cv): the structured run loop, beside run_one

Each attempt's reply is read as JSON, selected from by slot, checked as
data and retained by the existing rule; sluice assembles the document from
the CV Layout and the Candidate Profile, audits only what the model wrote,
and renders the document. A vault without a CV Layout is refused before
any spend. Not yet what cv run calls.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 17: Doctor's rows for the new model, and warnings shown by default

Spec §9.1, D14, §8 (decoys contradicting the user's data), §6.6. ADDITIVE: pure classifiers and the printer change land with their tests; Task 18 wires them into `Sluice.doctor` and removes the rows they replace. Every row reports COUNTS, positions and the command that lists entries, never an entry title, a tool, a skill or a decoy value, because a `DoctorReport` reaches MCP clients whole.

**Files:**
- Modify: `sluice/core/doctor.py` (`ComponentCheck.warn_by_default`, `Verdict.warning_rows`, `classify_cv_layout`, `classify_tools`, `classify_cv_eligibility`, `classify_attribution`, `classify_decoys`)
- Modify: `sluice/cli.py` (`_print_doctor_verdict`: the warning section and the quiet count)
- Test: `tests/test_doctor_cv_layout.py`

**Interfaces:**
- Consumes: `CvLayout`, `LayoutError`, `CV_LAYOUT_RELPATH` (Task 4); `placement_counts`, `layout_text` (Task 5); `tool_items`, `find_term` (Task 3).
- Produces:
  - `ComponentCheck(..., warn_by_default: bool = False)` — legal on a `DEGRADED` row only (raises otherwise)
  - `Verdict.warning_rows: list` — DEGRADED rows that block nothing and carry `warn_by_default`
  - `classify_cv_layout(layout, error=None) -> ComponentCheck` — subject `cv_layout`; OK / SETUP / DEAD, blocking `cv` except when OK
  - `classify_tools(experience_entries) -> list[ComponentCheck]` — one DEAD row blocking `cv`, or none
  - `classify_cv_eligibility(layout, experience_entries) -> list[ComponentCheck]` — up to two D14 warning rows
  - `classify_attribution(experience_entries) -> list[ComponentCheck]` — the §6.6 warning row, or none
  - `classify_decoys(decoys, experience_entries, skill_names, layout) -> list[ComponentCheck]` — the §8 warning row, or none

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doctor_cv_layout.py`:

```python
"""Doctor's rows for the structured CV (#364/#365/#368 spec §9.1, D14, §8, §6.6), each in
each state, asserted through the DEFAULT printed view -- a row's state alone is not what a
user reads."""
import pytest

from sluice.core.doctor import (DEAD, DEGRADED, OK, SETUP, ComponentCheck, DoctorReport,
                                classify_attribution, classify_cv_eligibility,
                                classify_cv_layout, classify_decoys, classify_tools)
from sluice.core.protocols import CV_LAYOUT_RELPATH, CvLayout, LayoutError, LayoutRole

LAYOUT = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                    employers=("Example Alpha",)),),
                  omitted=("Example Tidal",))


def _entry(company="Example Alpha", tools="", skills=False):
    # Keywords, not dict literals keyed by the field names: the fixture-name sweep reads such
    # a literal's value as a skill NAME, and these are a parameter and a presence flag.
    return {"title": "SYNTHETIC-ENTRY", "company": company, "fields": dict(Tools=tools),
            "legacy": dict(Skills=skills)}


def _printed(capsys, *rows, strict=False):
    from sluice.cli import _print_doctor_verdict
    report = DoctorReport(checks=[], components=list(rows))
    _print_doctor_verdict(report, offline=True, strict=strict,
                          exit_code=report.exit_code(strict=strict))
    return " ".join(capsys.readouterr().out.split())


# --- the CV Layout row -------------------------------------------------------------------

def test_a_parsed_layout_is_ok_with_its_role_count():
    row = classify_cv_layout(LAYOUT)
    assert (row.subject, row.state, row.detail) == ("cv_layout", OK, "1 role")


def test_an_absent_layout_is_setup_blocking_cv_and_says_where(capsys):
    row = classify_cv_layout(None)
    assert (row.state, row.blocks) == (SETUP, ("cv",))
    out = _printed(capsys, row)
    assert "Still to set up:" in out and CV_LAYOUT_RELPATH in out


def test_a_malformed_layout_is_dead_and_lists_every_problem(capsys):
    row = classify_cv_layout(None, LayoutError(["roles[0].from: required",
                                                "roles[0].to: required"]))
    assert (row.state, row.blocks) == (DEAD, ("cv",))
    out = _printed(capsys, row)
    assert "Not working:" in out
    assert "roles[0].from: required" in out and "roles[0].to: required" in out


def test_an_unreadable_layout_is_dead_never_absent():
    row = classify_cv_layout(None, OSError("permission denied"))
    assert (row.state, row.blocks) == (DEAD, ("cv",))
    assert "could not be read" in row.detail and "permission denied" in row.detail


# --- an unusable Tools item, which the gate cannot use -------------------------------------

def test_an_unusable_tools_item_is_dead_and_counted_never_named(capsys):
    rows = classify_tools([_entry(tools="Examplelang"), _entry(tools="9001 Examplestandard")])
    assert [(r.state, r.blocks) for r in rows] == [(DEAD, ("cv",))]
    out = _printed(capsys, *rows)
    assert "1 verified entry" in out and "job-sluice experience list" in out
    assert "9001" not in out and "SYNTHETIC-ENTRY" not in out


def test_usable_tools_draw_no_row():
    assert classify_tools([_entry(tools="Examplelang, .Examplenet"), _entry()]) == []


# --- entries no CV can cite (D6, D14) -------------------------------------------------------

def test_unmatched_and_companyless_entries_are_warned_about_by_default(capsys):
    rows = classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Robotics"),
                                            _entry("Example Robotics"), _entry("")])
    assert [(r.subject, r.state, r.blocks, r.warn_by_default) for r in rows] == [
        ("cv_layout (not on your CV)", DEGRADED, (), True),
        ("cv_layout (no company)", DEGRADED, (), True)]
    out = _printed(capsys, *rows)
    assert "Worth a look" in out
    assert "2 verified experience entries" in out and "1 verified experience entry" in out
    assert "Example Robotics" not in out
    assert "more degraded" not in out, "a listed warning must not also be counted as quiet"


def test_an_omitted_or_matched_entry_draws_nothing():
    assert classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Tidal")]) == []


# --- the attribution check going dark (spec §6.6) ---------------------------------------

def test_an_upgraded_vault_with_no_tools_is_warned_about():
    [row] = classify_attribution([_entry(skills=True), _entry()])
    assert (row.state, row.blocks, row.warn_by_default) == (DEGRADED, (), True)
    assert "1 still carries the retired Skills:" in row.detail


def test_a_vault_with_neither_field_is_an_unconfigured_install_and_draws_nothing():
    assert classify_attribution([_entry(), _entry()]) == []


def test_any_declared_tools_turns_the_check_on_and_draws_nothing():
    assert classify_attribution([_entry(skills=True), _entry(tools="Examplelang")]) == []


# --- a decoy contradicting the user's own data (spec §8) ------------------------------------

@pytest.mark.parametrize("decoys,entries,names,layout", [
    (["examplelang"], [_entry(tools="Examplelang")], [], None),
    (["Example Query"], [], ["Example Query"], None),
    (["example tidal"], [], [], LAYOUT),
])
def test_a_decoy_matching_the_users_own_data_is_warned_about_by_position(
        decoys, entries, names, layout):
    [row] = classify_decoys(["Example Zephyr"] + decoys, entries, names, layout)
    assert (row.state, row.blocks, row.warn_by_default) == (DEGRADED, (), True)
    assert "entry 2" in row.detail
    assert decoys[0] not in row.detail and "Example Zephyr" not in row.detail


def test_a_decoy_inside_a_longer_token_is_no_contradiction():
    assert classify_decoys(["examplelang"], [_entry(tools="Examplelangscript")], [],
                           None) == []


# --- the flag itself ------------------------------------------------------------------

def test_warn_by_default_is_refused_on_a_row_that_is_not_degraded():
    with pytest.raises(ValueError, match="warn_by_default"):
        ComponentCheck("store", "x", OK, "fine", warn_by_default=True)


def test_a_warning_row_blocks_nothing_and_strict_still_fails_on_it(capsys):
    [row] = classify_attribution([_entry(skills=True)])
    report = DoctorReport(checks=[], components=[row])
    assert report.verdict().buckets["cv"] == report.verdict().buckets["ingest"]
    assert (report.exit_code(), report.exit_code(strict=True)) == (0, 1)
    out = _printed(capsys, row, strict=True)
    assert out.count("cv attribution check") == 1, "listed once, not twice, under --strict"
```

(`buckets["cv"] == buckets["ingest"]` holds because neither capability has a blocker: both read READY. If `CAPABILITIES` names ingest differently, use any capability name from `sluice.core.doctor.CAPABILITIES` that this report leaves unblocked.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_doctor_cv_layout.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'classify_attribution'`.

- [ ] **Step 3: Implement**

In `sluice/core/doctor.py`, give `ComponentCheck` its new field after `blocks`, and extend `__post_init__`:

```python
    blocks: tuple = ()
    # D14 (#364/#365/#368): a DEGRADED row that blocks nothing but is worth reading by
    # default -- a CV that composes while leaving some of the user's work off, or a check
    # gone quiet. Without it the default view folds the row into "N more degraded".
    # `--require` reads `blocks` alone, so this can never make a row block; `--strict` fails
    # on it as on any DEGRADED row.
    warn_by_default: bool = False
```

```python
        if self.warn_by_default and self.state != DEGRADED:
            raise ValueError(
                f"ComponentCheck({self.component}/{self.subject}) sets warn_by_default on a "
                f"{self.state!r} row; only a DEGRADED row is printed as a warning")
```

Give `Verdict` a field `warning_rows: list = field(default_factory=list)` (with a one-line comment: the DEGRADED rows that block nothing but carry `warn_by_default`, D14), and in `DoctorReport.verdict()` pass:

```python
                       warning_rows=[c for c in degraded_rows
                                     if getattr(c, "warn_by_default", False)
                                     and not getattr(c, "blocks", ())],
```

Then add the classifiers at the end of the module:

```python
# --- #364/#365/#368: the CV Layout and what the structured CV reads --------------------
#
# Every row here reports counts, positions and the command that lists the entries -- never
# an entry title, a tool, a skill or a decoy -- because a DoctorReport reaches MCP clients
# whole. Pure: Sluice.doctor reads the store once and passes what it read.

def classify_cv_layout(layout, error=None) -> ComponentCheck:
    """The `store / cv_layout` row: the note every CV is assembled into (spec §9.1).

    From ONE read, which Sluice.doctor makes in its own `try` (#259: one bad note never
    collapses the store rows), passing the parsed layout or the exception. It blocks `cv`
    in every state but OK, in step with `cv run`, which refuses before any spend when the
    note is absent, malformed or unreadable."""
    from sluice.core.protocols import CV_LAYOUT_RELPATH, LayoutError
    if isinstance(error, LayoutError):
        return ComponentCheck("store", "cv_layout", DEAD,
                              f"{CV_LAYOUT_RELPATH} is malformed: " + "; ".join(error.problems),
                              blocks=("cv",))
    if error is not None:
        # Unreadable is never reported as absent (#242).
        return ComponentCheck("store", "cv_layout", DEAD,
                              f"{CV_LAYOUT_RELPATH} could not be read -- {error}",
                              blocks=("cv",))
    if layout is None:
        return ComponentCheck(
            "store", "cv_layout", SETUP,
            f"no CV Layout note at {CV_LAYOUT_RELPATH} -- every heading, date, location and "
            "title on a CV comes from it (docs/CONFIGURATION.md)", blocks=("cv",))
    n = len(layout.roles)
    return ComponentCheck("store", "cv_layout", OK, f"{n} role{'' if n == 1 else 's'}")


def classify_tools(experience_entries) -> list:
    """One DEAD row, blocking `cv`, when a verified experience entry's `Tools:` holds an
    item the gate cannot use (spec §4.2): `cv run` refuses before any spend then, naming
    the entry and the item on the user's own terminal; this row counts them."""
    from sluice.core.tokens import tool_items
    bad = 0
    for entry in experience_entries:
        try:
            tool_items(entry)
        except ValueError:
            bad += 1
    if not bad:
        return []
    return [ComponentCheck(
        "store", "Experience Library (Tools)", DEAD,
        f"{bad} verified entr{'y' if bad == 1 else 'ies'} declare{'s' if bad == 1 else ''} a "
        "Tools: item the CV gate cannot use -- every word of a tool's name must begin with "
        "a letter (job-sluice experience list)", blocks=("cv",))]


def classify_cv_eligibility(layout, experience_entries) -> list:
    """D14 warning rows: verified entries no CV can cite (spec §4.3, D6).

    `unmatched` -- a Company: matching no role's heading or employers, nor any_role or
    omitted -- and `blank` -- no Company: at all -- are citable nowhere, and the CV still
    composes. So each is DEGRADED, blocks nothing, and is listed by default: a user who
    verified an entry and never sees its work on a CV is owed the reason. `omitted` is the
    user's own choice and draws nothing."""
    from sluice.core.layout import placement_counts
    counts = placement_counts(layout, experience_entries)
    rows = []

    def entries(n):
        return f"{n} verified experience entr{'y' if n == 1 else 'ies'}"

    if counts.get("unmatched"):
        n = counts["unmatched"]
        rows.append(ComponentCheck(
            "store", "cv_layout (not on your CV)", DEGRADED,
            f"{entries(n)} name{'s' if n == 1 else ''} a Company: that matches no role's "
            "heading or employers, nor "
            "any_role or omitted, in the CV Layout, so no CV can cite them -- add the company "
            "to a role, or to omitted if leaving it off is deliberate "
            "(job-sluice experience list)", warn_by_default=True))
    if counts.get("blank"):
        n = counts["blank"]
        rows.append(ComponentCheck(
            "store", "cv_layout (no company)", DEGRADED,
            f"{entries(n)} ha{'s' if n == 1 else 've'} no Company:, so no CV can cite them -- "
            "give each the company "
            "it happened at (job-sluice experience list)", warn_by_default=True))
    return rows


def classify_attribution(experience_entries) -> list:
    """The spec §6.6 warning. On an UPGRADED vault -- a verified entry still carries a
    non-empty legacy Skills: and none declares Tools: -- the misattributed-tool check is
    off, and the user who annotated their entries is owed the reason. A vault with neither
    field is an unconfigured install and draws nothing (empty config abstains)."""
    from sluice.core.tokens import tool_items

    def declares(entry):
        try:
            return bool(tool_items(entry))
        except ValueError:
            return True   # an unusable item is classify_tools' DEAD row, not this one

    if any(declares(e) for e in experience_entries):
        return []
    legacy = sum(1 for e in experience_entries if (e.get("legacy") or {}).get("Skills"))
    if not legacy:
        return []
    return [ComponentCheck(
        "store", "cv attribution check", DEGRADED,
        f"off: no verified experience entry declares Tools:, and {legacy} still "
        f"carr{'ies' if legacy == 1 else 'y'} the retired Skills: -- sluice no longer reads "
        "Skills:; move each entry's tools into Tools: to turn the check on "
        "(docs/CONFIGURATION.md)", warn_by_default=True)]


def classify_decoys(decoys, experience_entries, skill_names, layout) -> list:
    """The spec §8 warning: a `cv.fabrication_decoys` entry matching, as a whole term, the
    user's own data -- a verified entry's Tools: item, a verified skill's CV name, or any CV
    Layout text. The ban contradicts that data: it keeps the tool or skill off every CV's
    skills list, while layout text renders regardless. `skill_names` arrive already derived
    (Sluice.doctor passes cv/selection.py::cv_name's answers), so this and the pool agree.
    Decoys are named by POSITION, never echoed."""
    from sluice.core.layout import layout_text
    from sluice.core.tokens import find_term, tool_items

    def items(entry):
        try:
            return tool_items(entry)
        except ValueError:
            return []

    texts = ([t for e in experience_entries for t in items(e)] + list(skill_names)
             + ([layout_text(layout)] if layout is not None else []))
    hits = [i for i, d in enumerate(decoys, 1) if any(find_term(t, d) for t in texts)]
    if not hits:
        return []
    where = ", ".join(f"entry {i}" for i in hits)
    return [ComponentCheck(
        "gates", "cv.fabrication_decoys", DEGRADED,
        f"cv.fabrication_decoys {where} matches your own Tools:, a verified skill's name or "
        "your CV Layout -- it keeps that tool or skill off every CV's skills list, and "
        "layout text renders regardless; remove the decoy, or the data it contradicts",
        warn_by_default=True)]
```

In `sluice/cli.py::_print_doctor_verdict`, exclude warning rows from the `--strict` extras and the quiet count, and list them in their own section:

```python
    strict_only = [c for c in v.degraded_rows
                   if strict and c not in v.degraded_blocking_rows
                   and c not in v.warning_rows]
    for heading, rows in (("Still to set up", v.setup_rows),
                          ("Not working", v.broken_rows),
                          ("Working, but not properly",
                           v.degraded_blocking_rows + strict_only),
                          # D14: degraded rows that block nothing but are worth reading.
                          ("Worth a look (these block nothing)", v.warning_rows)):
```

and in the closing lines:

```python
        quiet = [c for c in v.degraded_rows
                 if c not in v.degraded_blocking_rows and c not in v.warning_rows]
```

- [ ] **Step 4: Run the new tests and the whole suite**

Run: `.venv/bin/python -m pytest tests/test_doctor_cv_layout.py tests/test_doctor_verdict.py -q && .venv/bin/python -m pytest -q`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/doctor.py sluice/cli.py tests/test_doctor_cv_layout.py
git commit -F - <<'EOF'
feat(doctor): rows for the CV Layout, Tools and entries no CV can cite

Pure classifiers for the CV Layout note, an unusable Tools: item, entries
whose company matches no role or is blank, the attribution check going
quiet on an upgraded vault, and a decoy that contradicts the user's own
data. Warning rows block nothing and are listed by default. Not wired into
doctor yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 18: THE SWITCH — `cv run` composes structured

Spec §4.2 (`Tools:` replaces `Skills:`), §4.4/D12 (what verifying a skill note buys), §6.6, §7.2 (dry run), §9.1 (doctor rows, prerequisites), §9.2 (reporting), §12.1/§12.2 (the rows named below). This is the ONE `feat(cv)!` commit. After it, `cv run` composes structured, every surface reports the new fields, and the text machinery (`cv/parse.py`, the old `validate`/`compose`/bundle renderers, `precheck`) is dead but still present: Task 19 deletes it with its own tests, so that this commit's diff is the behaviour change and nothing else.

The port is large and mostly mechanical. Its discipline: **a guard is never weakened to make it pass.** A red test is either (a) a test of something this spec deletes — record it in the ledger as obsolete, naming the spec section, and delete it — or (b) a property that survives — port it to the new input, keeping its assertion, and record where it went. Never (c) loosen an assertion so it passes.

**Files:**
- Modify: `sluice/core/protocols.py` (`EVIDENCE_KINDS["experience"]`; `verify_outcome` moves here; the `Renderer` docstring stops describing `precheck` as live)
- Modify: `sluice/evidence/commands.py` (`verify_outcome` re-exported; `experience list` shows `Company:` and `Tools:`)
- Modify: `sluice/cv/engine.py` (`run_one` is the structured loop; the text loop and its helpers go; `missing_prerequisites` rewritten; `run_warnings` added)
- Modify: `sluice/core/app.py` (`compose_cv`: run warnings, no dry-run renderer; `doctor`: the new rows; `pending_evidence_detail`)
- Modify: `sluice/core/doctor.py` (`classify_store`: baseline rows go, skills row keyed on D12; `classify_skills_request`, `classify_skills_reconciliation`, `classify_negatives_vs_skills` and their helpers deleted)
- Modify: `sluice/cli.py` (`cmd_cv_run`: the three new fields, the `skipped-config` reason)
- Modify: `sluice/mcpserver.py` (`cv_run`: the three new fields and the content warning; the propose detail)
- Modify: `docs/USAGE.md`, `docs/TROUBLESHOOTING.md` (only the passages `tests/test_docs_claims.py` pins to this code; Task 22 rewrites the rest)
- Modify: `tests/conftest.py` (`make_composable`), `tests/harness/config.py`, `tests/harness/renderer.py`, `tests/harness/__init__.py`, `tests/harness/registry.py` (the snapshot derives its seams), `tests/harness/backend.py` (a comment), the five `tests/e2e/` CV tests, `tests/functional/test_cv.py`
- Modify: the store doubles `compose_cv`, `run_one` and `Sluice.doctor` reach, `tests/test_app_operations.py`'s dry-run rows, `tests/test_doctor_verdict.py`, `tests/test_cv_run_cli.py`, `tests/test_docs_claims.py` (Steps 5 and 8 name each)
- Modify: every test the full suite reports red after the production change (Step 8), per the rules in Step 7
- Create: `tests/test_skills_pool_wording.py`, `tests/test_harness_registry.py`, `docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md`
- Test: `tests/test_cv_prerequisites.py` (rewritten), `tests/test_doctor_cv_layout.py` (wiring rows added), `tests/test_cv_structured_engine.py` (renamed calls), `tests/test_cv_attribution_vaults.py` (new)

**Interfaces:**
- Consumes: everything Tasks 3–17 produce.
- Produces:
  - `run_one(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run=False, guard_existing_cv=False, policy=StalenessPolicy(), usage=None) -> CvResult` — Task 16's loop, now the only one
  - `missing_prerequisites(vault) -> list[str]` — the CV Layout (absent / malformed / unreadable), each verified experience entry's `Tools:`, no citable slot, the citable corpus
  - `run_warnings(vault) -> list[str]` — logged once per run by `Sluice.compose_cv`
  - `sluice/core/protocols.py::verify_outcome(spec, subject="it") -> str` — keyed on `cited_by_gate`, then `names_in_skills_pool`
  - `sluice/core/app.py::pending_evidence_detail(kind) -> str`
  - `EVIDENCE_KINDS["experience"].fields == ("Company", "Category", "Best For", "Metrics", "Tools")`, `.legacy_fields == ("Skills",)`

- [ ] **Step 1: Start the ledger**

Spec §12.3: every test this branch deletes, and every test whose body it changes, gets one line saying what property it protected and where that property is now. Task 24 derives the required set and checks it; this task and Task 19 write the lines as they go, because the person deleting a test is the one who knows what it protected.

Create `docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md`:

```markdown
# Structured CV composition: test ledger

One line per test this branch deletes or changes (spec §12.3). Task 24 derives the required
set from the fork point and checks that every `ported as` names a test collected on the
branch.

| Test | What it protected | Now |
|---|---|---|
```

A line is a table row: the node id as `tests/<file>.py::<test>` (parametrized tests by function name), the property in a few words, and either `obsolete: <why>, spec §<n>` or `ported as tests/<file>.py::<test>`. Add the row in the same commit that deletes or changes the test.

- [ ] **Step 2: `Tools:` replaces `Skills:`, and what verifying a skill note buys (D12)**

Write the failing tests first. Create `tests/test_skills_pool_wording.py`:

```python
"""D12 (#364/#365/#368): what verifying an evidence note buys is said by its kind's own
flags in all three places a user reads it -- `verify`'s own message, doctor's store row and
the MCP propose result -- and never claims what the kind cannot back."""
import pytest

from sluice.core.app import pending_evidence_detail
from sluice.core.doctor import classify_store
from sluice.core.protocols import EVIDENCE_KINDS, verify_outcome

_FACTS = {"vault_exists": True, "criteria_present": True,
          "candidate_name_present": True, "candidate_contact_present": True,
          **{f"{k}_{n}": 1 for k in EVIDENCE_KINDS for n in ("verified", "total")}}


@pytest.mark.parametrize("kind", sorted(EVIDENCE_KINDS))
def test_each_message_claims_exactly_what_the_kinds_flags_grant(kind):
    spec = EVIDENCE_KINDS[kind]
    label = spec.relpath.rsplit("/", 1)[-1]
    [row] = [r for r in classify_store(_FACTS) if r.subject == label]
    for text in (verify_outcome(spec), row.detail, pending_evidence_detail(kind)):
        assert ("citable" in text) == spec.cited_by_gate, (kind, text)
        assert ("skills list" in text) == spec.names_in_skills_pool, (kind, text)


def test_the_kinds_disagree_on_both_flags_so_the_row_above_can_fail():
    assert {s.cited_by_gate for s in EVIDENCE_KINDS.values()} == {True, False}
    assert {s.names_in_skills_pool for s in EVIDENCE_KINDS.values()} == {True, False}


def test_experience_declares_tools_and_keeps_skills_as_legacy_presence_only():
    spec = EVIDENCE_KINDS["experience"]
    assert spec.fields == ("Company", "Category", "Best For", "Metrics", "Tools")
    assert spec.legacy_fields == ("Skills",)


def test_doctor_no_longer_reports_a_baseline_cv():
    assert not [r for r in classify_store(_FACTS) if "baseline" in r.subject]
```

Add to `tests/test_evidence_store.py` (the real store surfaces a legacy field's PRESENCE, outside `fields`, and refuses to write it):

```python
def test_a_legacy_skills_line_is_surfaced_as_presence_and_never_written(tmp_path):
    from sluice.core.vault import Vault
    v = Vault(str(tmp_path / "vault"))
    v.propose_evidence("experience", name="alpha", fields={"Company": "Example Alpha"})
    [pending] = v.read_pending_evidence("experience")
    with open(pending["path"], encoding="utf-8") as fh:
        raw = fh.read().replace("---\n", "---\nSkills: Examplelang\n", 1)
    with open(pending["path"], "w", encoding="utf-8") as fh:
        fh.write(raw)
    assert v.verify_evidence("experience", "alpha", today="2026-09-03", reviewed=raw)
    [entry] = v.read_evidence("experience")
    assert "Skills" not in entry["fields"] and entry["legacy"] == dict(Skills=True)
    with pytest.raises(ValueError, match="Skills"):
        v.propose_evidence("experience", name="beta", fields={"Skills": "Examplelang"})
```

Add the same row to `tests/conformance/test_store_contract.py`, parametrized over `store_name` and written with that file's `_make_store` and seeding helpers, so every registered store must surface a legacy field's presence outside `fields`, with `"legacy"` present on EVERY entry it returns (including one carrying no retired key) (spec §4.2: "a conformance row makes a second store surface it too").

Add to `tests/test_evidence_cli.py`, beside the `Skills:`-surfacing rows it supersedes (those three port to `Tools:` in Step 7):

```python
def test_experience_list_shows_each_entrys_company_and_tools(tmp_path, monkeypatch, capsys):
    """The doctor rows for entries no CV can cite report COUNTS (a doctor row never carries
    user-authored text), so this is where a user finds WHICH entries: by the company and the
    tools each one declares. The notes are written straight into the vault so both are
    verified."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    exp = Vault(str(tmp_path))._evidence_dir("experience")
    os.makedirs(exp, exist_ok=True)
    for name, company, tools in (("alpha", "Example Alpha", "Examplelang"), ("beta", "", "")):
        with open(os.path.join(exp, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nCompany: {company}\nCategory: \nBest For: \nMetrics: \n"
                     f"Tools: {tools}\nverified: 2026-08-25\n---\nBody.\n")
    assert main(["experience", "list"]) == 0
    lines = {ln.split("  [")[0]: ln for ln in capsys.readouterr().out.splitlines()}
    assert "Company: Example Alpha" in lines["alpha"] and "Tools: Examplelang" in lines["alpha"]
    # The label and its value are checked apart: written as one string, the fixture-name
    # sweep reads it as a company called "(none)".
    beta = lines["beta"]
    assert "(none)" in beta.partition("Company")[2] and "Tools" not in beta
```

Run: `.venv/bin/python -m pytest tests/test_skills_pool_wording.py tests/test_evidence_store.py tests/test_evidence_cli.py -q`
Expected: FAIL (`ImportError: cannot import name 'verify_outcome' from 'sluice.core.protocols'`).

Then implement. In `sluice/core/protocols.py`, the experience kind becomes:

```python
    "experience": EvidenceKind("Job Applications/Experience Library",
                               ("Company", "Category", "Best For", "Metrics", "Tools"),
                               cited_by_gate=True, read_by_composer=True,
                               # #364/#365/#368 (spec §4.2): `Tools:` is the attribution
                               # index now. `Skills:` is no longer read; the store reports
                               # only whether an entry still carries it, so doctor and
                               # `cv run` can say the check is off on an upgraded vault.
                               legacy_fields=("Skills",)),
```

Rewrite the comment block above `EVIDENCE_KINDS` that describes `Skills:` as the bundle's and gate's attribution source (it cites `cv/bundle.py`'s `_skill_items`) so it describes `Tools:` and `core/tokens.py::tool_items`, and add after `EVIDENCE_KINDS`:

```python
def verify_outcome(spec, subject: str = "it") -> str:
    """What `verify` actually BUYS for this kind, as a verb phrase -- the one place, so no
    message can over-claim on its own (#164 review, M2). Keyed on the kind's flags, never its
    name: `cited_by_gate` first (the gate may license its content), then
    `names_in_skills_pool` (D12: a verified note's name may appear in a CV's skills list).
    Here rather than in sluice/evidence/ so core/doctor.py and core/app.py can say the same
    thing; `subject` lets the init wizard's plural summary share the sentence."""
    if spec.cited_by_gate:
        return f"make {subject} citable"
    if spec.names_in_skills_pool:
        return f"make {subject} available to a CV's skills list"
    return f"mark {subject} reviewed"
```

In `sluice/evidence/commands.py`, delete the local `verify_outcome` and import it: `from sluice.core.protocols import verify_outcome  # noqa: F401 -- re-exported for cli.py`. Rewrite `cmd_evidence_list`'s display: for a kind whose fields include `Company`, every line shows `  Company: <value>` or `  Company: (none)`; for a kind whose fields include `Tools`, a non-blank value shows `  Tools: <value>` (the same blank-is-absent and `isinstance(..., str)` handling the `Skills:` display had, which carries over with its comment re-pointed at `tool_items`). Its docstring says the doctor rows it now resolves are the "not on your CV", "no company" and unusable-`Tools:` rows.

In `sluice/core/doctor.py::classify_store`, delete both `baseline_rel` rows (the `if not facts.get("baseline_exists")` block and its `else`), delete the docstring's baseline sentences, re-anchor its "the same shape `cv/engine.py` already gives the renderer seam's optional `precheck`" on `Store.preflight` being optional, and key the evidence row on D12:

```python
        if spec.cited_by_gate:
            detail = (f"{verified} verified / {total} total entries -- only verified "
                      f"entries are citable by the CV fabrication gate")
        elif spec.names_in_skills_pool:
            detail = (f"{verified} verified / {total} total entries -- framing for the CV "
                      f"composer, and each verified entry's name (its Label:, else its "
                      f"title) can appear in a CV's skills list")
        elif spec.read_by_composer:
            detail = (f"{verified} verified / {total} total entries -- shown to the CV "
                      f"composer as framing; not a citable source for the gate")
        else:
            detail = (f"{verified} verified / {total} total entries -- reviewed, but "
                      f"nothing reads this corpus yet")
```

In `sluice/core/app.py`, at module level:

```python
def pending_evidence_detail(kind: str) -> str:
    """The MCP `propose_evidence` result's detail: what a proposal is, and what verifying it
    would buy for THIS kind (D12) -- here because mcpserver.py may not import
    core/protocols.py itself (its isolation sweep)."""
    from sluice.core.protocols import EVIDENCE_KINDS, verify_outcome
    return (f"proposed only -- it does nothing until a human runs `job-sluice {kind} verify` "
            f"to {verify_outcome(EVIDENCE_KINDS[kind])}. It is not visible to "
            "list_evidence's default view, and there is deliberately no tool here that "
            "promotes one.")
```

In `sluice/mcpserver.py`, import `pending_evidence_detail` from `sluice.core.app`, return `"detail": pending_evidence_detail(kind)`, and delete `_PROPOSE_EVIDENCE_PENDING_DETAIL` (its comment's reasoning moves to the new function's docstring). Port each `tests/test_mcpserver.py` row that compared against the constant to compare against `pending_evidence_detail(kind)` (ledger: ported).

Run: `.venv/bin/python -m pytest tests/test_skills_pool_wording.py tests/test_evidence_store.py tests/test_evidence_cli.py tests/test_evidence_kinds.py -q`
Expected: PASS. (The rest of the suite is red until Step 8.)

- [ ] **Step 3: The engine runs the structured loop, and refuses an unready vault for the new reasons**

Replace `tests/test_cv_prerequisites.py` with the file below. Ledger rows for the replaced file: `test_a_missing_baseline_is_reported_not_raised` → ported as `test_an_absent_layout_is_reported_not_raised` (the baseline is no longer read, spec D2/§4.5; the property — a missing required note is reported, never raised — carries to the CV Layout); `test_an_undecodable_baseline_is_reported_not_raised` → ported as `test_an_undecodable_layout_is_unreadable_not_absent`; `test_an_unreadable_vault_is_not_reported_as_an_empty_one` → ported as `test_an_unreadable_layout_is_not_reported_as_absent`; `test_a_whitespace_only_baseline_is_not_a_baseline` → obsolete: the baseline is not read, spec §4.5; `test_preflight_and_the_refusal_agree_about_a_blank_baseline` → ported as `tests/test_doctor_cv_layout.py::test_doctor_and_the_refusal_agree_about_every_layout_state` (Step 4); every other row → ported under its own name.

```python
"""#242, carried into #364/#365/#368: cv's config-level preconditions, refused once and
before any spend (spec §9.1). Each is equally true of every lead in a run, so none is a
per-lead fact.

The ORDER is load-bearing and asserted directly: the check precedes the RENDERER, the
backend and the dossier fetch -- on a bare install the renderer raises first, so a check
placed after it never runs for the newcomer it exists to help.
"""
import os

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.protocols import CV_LAYOUT_RELPATH
from sluice.cv.engine import missing_prerequisites
from tests.conftest import UNREADABLE_DIR, layout_yaml

ALPHA = (("alpha", {"Company": "Example Foundry"}),)


def _vault(tmp_path, *, layout="default", entries=ALPHA):
    """A real vault: a CV Layout note (none when `layout` is None) and verified experience
    entries proposed and verified through the store -- the route a user's entries take."""
    from sluice.core.vault import Vault

    v = Vault(str(tmp_path / "vault"))
    if layout is not None:
        path = os.path.join(v.dir, CV_LAYOUT_RELPATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(layout_yaml() if layout == "default" else layout)
    for name, fields in entries:
        v.propose_evidence("experience", name=name, fields=fields)
        pending = {e["title"]: e for e in v.read_pending_evidence("experience")}
        with open(pending[name]["path"], encoding="utf-8") as fh:
            raw = fh.read()
        assert v.verify_evidence("experience", name, today="2026-09-03", reviewed=raw), name
    return v


def _layout_path(v):
    path = os.path.join(v.dir, CV_LAYOUT_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _seed_shortlist_leads(vault_dir, n=1):
    """Shortlist leads, so `run_one` is genuinely reachable and the dossier path is live: an
    ordering test with no selectable lead cannot fail on a dossier regression."""
    from sluice.core.leads import Lead
    from sluice.core.vault import Vault

    v = Vault(str(vault_dir))
    for i in range(n):
        v.upsert(Lead(source="manual", search="", title=f"SYNTHETIC-ROLE-{i}",
                      company="Example Foundry", location="", salary="",
                      url=f"https://example.invalid/jobs/{i}", job_type="",
                      job_type_source="", first_seen="", last_seen=""))
    for note in v.read_leads():
        v.update_fields(note.ref, {"status": "shortlist"})
    return v


def _forbid_spend(monkeypatch):
    for name in ("renderer", "backend", "dossier_cache"):
        monkeypatch.setattr(
            Sluice, name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f"{_n} ran")))


# --- the CV Layout ------------------------------------------------------------------

def test_a_composable_vault_has_no_missing_prerequisite(tmp_path):
    assert missing_prerequisites(_vault(tmp_path)) == []


def test_an_absent_layout_is_reported_not_raised(tmp_path):
    [msg] = missing_prerequisites(_vault(tmp_path, layout=None))
    assert msg.startswith("no CV Layout note at") and CV_LAYOUT_RELPATH in msg


def test_a_malformed_layout_reports_every_problem(tmp_path):
    [msg] = missing_prerequisites(
        _vault(tmp_path, layout="---\nroles:\n  - heading: Example Foundry\n---\n"))
    assert "is malformed" in msg
    assert "roles[0].from: required" in msg and "roles[0].to: required" in msg


def test_an_undecodable_layout_is_unreadable_not_absent(tmp_path):
    # UnicodeDecodeError descends from ValueError, not OSError: an `except OSError` alone
    # would let a layout saved as UTF-16 escape as a traceback.
    v = _vault(tmp_path, layout=None)
    with open(_layout_path(v), "wb") as fh:
        fh.write(b"\xff\xfe\x00 not utf-8")
    [msg] = missing_prerequisites(v)
    assert msg.startswith("cannot read your CV Layout note")


def test_a_symlinked_layout_is_unreadable_not_absent(tmp_path):
    v = _vault(tmp_path, layout=None)
    outside = tmp_path / "outside.md"
    outside.write_text(layout_yaml(), encoding="utf-8")
    os.symlink(outside, _layout_path(v))
    [msg] = missing_prerequisites(v)
    assert msg.startswith("cannot read your CV Layout note") and "symlink" in msg


@UNREADABLE_DIR
def test_an_unreadable_layout_is_not_reported_as_absent(tmp_path):
    v = _vault(tmp_path)
    path = _layout_path(v)
    os.chmod(path, 0o000)
    try:
        [msg] = missing_prerequisites(v)
    finally:
        os.chmod(path, 0o644)
    assert msg.startswith("cannot read your CV Layout note")
    assert "Permission denied" in msg or "Errno 13" in msg


# --- the experience entries ----------------------------------------------------------

def test_an_empty_citable_corpus_is_reported(tmp_path):
    [msg] = missing_prerequisites(_vault(tmp_path, entries=()))
    assert msg.startswith("no verified experience entries")


def test_empty_skills_and_stories_do_not_block_a_run(tmp_path):
    v = _vault(tmp_path)
    assert v.read_evidence("skills") == [] and v.read_evidence("stories") == []
    assert missing_prerequisites(v) == []


def test_an_unverified_entry_does_not_satisfy_the_corpus(tmp_path):
    v = _vault(tmp_path, entries=())
    d = os.path.join(v.dir, "Job Applications", "Experience Library")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nCompany: "Example Foundry"\n---\n\n# alpha\n')
    assert v.read_evidence("experience", verified_only=False), "precondition: it exists"
    assert v.read_evidence("experience") == [], "premise: the default read is verified-only"
    assert any("no verified experience" in m for m in missing_prerequisites(v))


def test_an_unreadable_corpus_does_not_tell_you_to_add_an_entry():
    class _Unreadable:
        def read_cv_layout(self):
            return None

        def read_evidence(self, kind, verified_only=True):
            raise OSError("refusing to read through it: symlinked evidence directory")

    missing = missing_prerequisites(_Unreadable())
    assert any("cannot read your experience entries" in m for m in missing), missing
    assert all("experience add" not in m for m in missing), missing


def test_a_tools_item_the_gate_cannot_use_is_named_with_its_entry(tmp_path):
    v = _vault(tmp_path, entries=(
        ("alpha", {"Company": "Example Foundry", "Tools": "Examplelang, 9001 Examplestandard"}),))
    [msg] = missing_prerequisites(v)
    assert msg.startswith("your experience entry 'alpha':") and "9001" in msg


def test_no_slot_that_can_cite_anything_is_reported_naming_doctor(tmp_path):
    [msg] = missing_prerequisites(
        _vault(tmp_path, entries=(("alpha", {"Company": "Example Robotics"}),)))
    assert msg.startswith("no role in your CV Layout can cite any verified experience entry")
    assert "job-sluice doctor" in msg


def test_a_layout_of_zero_budgets_is_a_choice_not_a_misconfiguration(tmp_path):
    zero = layout_yaml([{"heading": "Example Foundry", "from": "02/2023", "to": "present",
                         "bullets_max": 0}])
    v = _vault(tmp_path, layout=zero, entries=(("alpha", {"Company": "Example Robotics"}),))
    assert missing_prerequisites(v) == []


# --- before any spend, through the facade ---------------------------------------------

def test_the_refusal_precedes_the_renderer_and_the_backend(tmp_path, monkeypatch):
    sl = Sluice(Config(vault_dir=str(tmp_path / "vault")))
    _seed_shortlist_leads(tmp_path / "vault")
    _forbid_spend(monkeypatch)
    with pytest.raises(ValueError) as exc:
        sl.compose_cv(all_shortlist=True)
    assert "not set up to compose yet" in str(exc.value)
    assert "CV Layout" in str(exc.value)


def test_a_dry_run_is_refused_too(tmp_path, monkeypatch):
    sl = Sluice(Config(vault_dir=str(tmp_path / "vault")))
    _seed_shortlist_leads(tmp_path / "vault")
    _forbid_spend(monkeypatch)
    with pytest.raises(ValueError, match="not set up to compose yet"):
        sl.compose_cv(all_shortlist=True, dry_run=True)


@pytest.mark.parametrize("entries,expected", [
    ((("alpha", {"Company": "Example Foundry", "Tools": "9001 Examplestandard"}),),
     "your experience entry 'alpha':"),
    ((("alpha", {"Company": "Example Robotics"}),),
     "no role in your CV Layout can cite any verified experience entry"),
])
def test_a_run_over_two_leads_is_refused_once_with_no_fetch_and_no_compose(
        tmp_path, monkeypatch, entries, expected):
    v = _vault(tmp_path, entries=entries)
    _seed_shortlist_leads(v.dir, n=2)
    _forbid_spend(monkeypatch)
    with pytest.raises(ValueError) as exc:
        Sluice(Config(vault_dir=v.dir)).compose_cv(all_shortlist=True)
    assert str(exc.value).count(expected) == 1, str(exc.value)


def test_the_refusal_reaches_the_cli_as_exit_2(tmp_path, monkeypatch, capsys):
    from sluice.cli import main

    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    rc = main(["cv", "run", "--all-shortlist"])
    out = capsys.readouterr()
    assert rc == 2, f"documented exit 2, got {rc}"
    assert "not set up to compose yet" in (out.out + out.err)
```

In `tests/test_cv_structured_engine.py`, replace every `run_one_structured` with `run_one` (the import and each call), and drop "Built beside run_one until Task 18 makes it run_one, so every row calls run_one_structured." from its docstring. Ledger: one row, `tests/test_cv_structured_engine.py` (every test) → ported as the same ids, calls renamed.

Run: `.venv/bin/python -m pytest tests/test_cv_prerequisites.py tests/test_cv_structured_engine.py -q`
Expected: FAIL — the prerequisite rows on the old baseline wording, the structured-engine rows because `run_one` is still the text loop.

Now implement in `sluice/cv/engine.py`:

1. Delete `run_one` and `_run_one` (the text loop), `_CONTACT_REWORDINGS`, `_contact_key`, `_same_contact_reworded` and `_contact_rewording`. Rename `run_one_structured` to `run_one` and `_run_structured` to `_run_one`, and delete the `TRANSITIONAL until Task 18` comment block above them. Where a comment in the loop said "see `_run_one`" about a reason that lived in the deleted text loop (the refusal order, the retention rule, the rebind, the audit's #333 hold), move that reason's sentence into the comment itself, so no comment points at code that no longer exists.
2. Imports: drop `section_spans` and `validate as _validate` from the `sluice.cv.validate` import (keep `check_selection, entry_facts`); add `LayoutError` and `EVIDENCE_KINDS` to the `sluice.core.protocols` import, `no_citable_slot` to the `sluice.core.layout` import, and `from sluice.core.tokens import tool_items`.
3. Replace the module docstring with:

```python
"""CV tailoring orchestrator (#364/#365/#368): select -> bundle -> compose -> read the reply
-> select from it -> check -> assemble -> audit -> render -> serve -> record -> notify.

Composition is a bounded backend call over the closed verified bundle, and its reply is DATA:
a JSON object holding the profile, cited bullets per role slot and skill picks from a closed
list. sluice reads it (cv/reply.py), selects what may render (cv/selection.py), checks the
selection (cv/validate.py::check_selection), and assembles the CV itself from the CV Layout
and the Candidate Profile (cv/document.py): the model never writes a heading, a date, a
name, a certificate or an education line.

The gate has two tiers, over the MODEL's text only -- vault text is the user's. A HARD one:
the reply's shape, the fabrication checks over the selection, and an em dash or a literal
`--`. A SCOPED STYLE one (#167): slop phrases and unbundled terms (#194). Either drives
exactly one retry with the findings, and the previous reply's drops, fed back; the loop
RETAINS the hard-clean attempt with the fewest style/voice findings (a tie keeps the later
attempt, and one whose voice check failed never displaces one whose voice was measured). So a
lead is skipped -- never rendered ungated -- when no attempt ever cleared the hard tier, and
never merely over a phrase. dry_run computes and reports, and writes nothing to the store,
the renderer or served_dir.

It DOES write the run's diagnostic artefacts (cv/artefacts.py), and that is decided rather
than inherited: a dry run already spends a composition and an audit per lead, each reply as
received and the document sluice built are the parts of that spend worth keeping, and
`output_dir` is a scratch workspace nothing downstream reads. run.json records
`dry_run: true`, and a dry run replaces an earlier real run's artefacts for the same slug.

An OPT-IN model-judged check (`cv.voice_check`, cv/voice.py) rides the same retry once the
HARD tier is clean, shown the same lines the phrase list reads. Off by default and fails
open on a backend error. At shipped defaults a surviving STYLE finding costs nothing beyond
the retry: `cv.style_hold` (also opt-in) is the ONLY thing that turns it into a sign-off hold
on `tailored_cv`, a separate gate from `cv.require_signoff`, which governs the fabrication
hold alone."""
```

4. Replace `missing_prerequisites` with:

```python
def missing_prerequisites(vault) -> list:
    """Config-level preconditions EVERY lead in a run shares, as user-facing strings (#242),
    checked ONCE per run before any spend -- and deliberately not in `run_one`, because a
    fact equally true of every lead belongs in one line, not N.

    Four (spec §9.1). The CV Layout note: absent, malformed or unreadable, each said its own
    way since the remedies differ. A verified experience entry whose `Tools:` the gate cannot
    use, naming the entry and the item on the user's own terminal (doctor's row counts them
    instead, since its rows reach MCP clients). No slot that can cite anything while the
    layout asks for bullets -- typically companies the headings do not match. And an empty or
    unreadable citable corpus. UNREADABLE is never reported as ABSENT: a read failure carries
    its own error, the rule `Vault.preflight` follows for the same corpora.

    The corpus check is keyed on `cited_by_gate`, not on every kind: an empty Skills
    Inventory or STAR Stories corpus cannot make a CV fail, so neither is required."""
    missing = []
    layout = None
    try:
        layout = vault.read_cv_layout()
    except LayoutError as exc:
        missing.append(f"your CV Layout note ({CV_LAYOUT_RELPATH}) is malformed:\n      - "
                       + "\n      - ".join(exc.problems))
    except (OSError, ValueError) as exc:         # ValueError covers UnicodeDecodeError
        missing.append(f"cannot read your CV Layout note ({CV_LAYOUT_RELPATH}) -- {exc}")
    else:
        if layout is None:
            missing.append(f"no CV Layout note at {CV_LAYOUT_RELPATH} -- every role heading, "
                           "date, location and title on a CV comes from it "
                           "(docs/CONFIGURATION.md shows its shape)")
    for name, kind in EVIDENCE_KINDS.items():
        if not kind.cited_by_gate:
            continue
        try:
            entries = vault.read_evidence(name)
        except (OSError, ValueError) as exc:
            # Report WHY, and never name `<kind> add`: it reaches the corpus through the
            # same resolver, so it would fail identically.
            missing.append(f"cannot read your {name} entries -- {exc}")
            continue
        if not entries:
            missing.append(
                f"no verified {name} entries -- every WORK bullet must cite one, so the "
                f"fabrication gate would reject any CV composed without them. "
                f"Add with `job-sluice {name} add`, then `job-sluice {name} verify`")
            continue
        for entry in entries:
            try:
                tool_items(entry)
            except ValueError as exc:
                missing.append(f"your {name} entry {entry['title']!r}: {exc}")
        if layout is not None:
            # The ids are only labels here: build_slots keys eligibility by them.
            slots = build_slots(layout, [dict(e, id=str(i)) for i, e in enumerate(entries)])
            if no_citable_slot(slots):
                missing.append(
                    f"no role in your CV Layout can cite any verified {name} entry -- each "
                    "entry's Company: must equal a role's heading or one of its employers, "
                    "or be listed under any_role; `job-sluice doctor` counts the entries "
                    "that match nothing ('not on your CV', 'no company')")
    return missing


def run_warnings(vault) -> list:
    """WARNINGS `cv run` logs once per RUN, never per lead (spec §6.6): today, the
    misattributed-tool check gone quiet on an upgraded vault. The sentence is doctor's own row
    (core/doctor.py::classify_attribution), so the two cannot disagree. An unreadable corpus
    is `missing_prerequisites`' to report, and is not repeated here."""
    from sluice.core.doctor import classify_attribution
    try:
        entries = vault.read_evidence("experience", verified_only=True)
    except (OSError, ValueError):
        return []
    return [f"cv: {row.subject} {row.detail}" for row in classify_attribution(entries)]
```

Run: `.venv/bin/python -m pytest tests/test_cv_prerequisites.py tests/test_cv_structured_engine.py -q`
Expected: PASS. (The facade rows pass already: `Sluice.compose_cv` calls `missing_prerequisites` before it builds a renderer or a backend.)

- [ ] **Step 4: Wire the facade — `compose_cv`'s warnings and dry run, and doctor's rows**

Write the failing tests. Add to `tests/test_doctor_cv_layout.py` (its imports gain `import os`, `from sluice.cv.engine import missing_prerequisites`, `from tests.conftest import layout_yaml` and `from tests.test_cv_prerequisites import _layout_path, _vault`):

```python
# --- wired into Sluice.doctor, and agreeing with `cv run` (spec §9.1) -----------------

MALFORMED = "---\nroles:\n  - heading: Example Foundry\n---\n"


def _doctor(v):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    return Sluice(Config(vault_dir=v.dir)).doctor(offline=True)


def _with_layout(tmp_path, state, entries=(("alpha", {"Company": "Example Foundry"}),)):
    v = _vault(tmp_path, layout={"valid": "default", "malformed": MALFORMED}.get(state),
               entries=entries)
    if state == "unreadable":
        outside = tmp_path / "outside.md"
        outside.write_text(layout_yaml(), encoding="utf-8")
        os.symlink(outside, _layout_path(v))
    return v


@pytest.mark.parametrize("state", ["valid", "absent", "malformed", "unreadable"])
def test_doctor_and_the_refusal_agree_about_every_layout_state(tmp_path, state):
    # Compared at the ROW: `--require cv` also reads the renderer row, which is DEAD wherever
    # WeasyPrint is not installed -- CI included -- so the capability bucket cannot isolate
    # the layout's own verdict.
    v = _with_layout(tmp_path, state)
    [row] = [c for c in _doctor(v).components if c.subject == "cv_layout"]
    refused = [m for m in missing_prerequisites(v) if "CV Layout" in m]
    assert (row.state != OK) == bool(refused) == ("cv" in row.blocks), (state, row, refused)


@pytest.mark.parametrize("tools,refused", [("Examplelang", False),
                                           ("9001 Examplestandard", True)])
def test_doctor_and_the_refusal_agree_about_tools(tmp_path, tools, refused):
    v = _vault(tmp_path, entries=(("alpha", dict({"Company": "Example Foundry"}, Tools=tools)),))
    rows = [c for c in _doctor(v).components if c.subject == "Experience Library (Tools)"]
    assert bool(rows) == refused == bool(missing_prerequisites(v))


@pytest.mark.parametrize("state", ["absent", "malformed", "unreadable"])
def test_no_eligibility_row_without_a_parsed_layout(tmp_path, state):
    v = _with_layout(tmp_path, state, entries=(("alpha", {"Company": "Example Robotics"}),
                                               ("beta", {})))
    subjects = {c.subject for c in _doctor(v).components}
    assert not {"cv_layout (not on your CV)", "cv_layout (no company)"} & subjects


def test_doctor_lists_the_eligibility_warnings_by_default(tmp_path, capsys):
    from sluice.cli import _print_doctor_verdict
    v = _vault(tmp_path, entries=(("alpha", {"Company": "Example Robotics"}), ("beta", {}),
                                  ("gamma", {"Company": "Example Foundry"})))
    report = _doctor(v)
    _print_doctor_verdict(report, offline=True, strict=False, exit_code=report.exit_code())
    out = " ".join(capsys.readouterr().out.split())
    assert "Worth a look" in out
    assert "1 verified experience entry names a Company:" in out
    assert "1 verified experience entry has no Company:" in out


def test_doctor_warns_when_a_decoy_bans_a_declared_tool(tmp_path, monkeypatch):
    config = tmp_path / "sluice.yaml"
    config.write_text("cv:\n  fabrication_decoys: [examplelang]\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(config))
    v = _vault(tmp_path, entries=(("alpha", {"Company": "Example Foundry",
                                             "Tools": "Examplelang"}),))
    [row] = [c for c in _doctor(v).components if c.subject == "cv.fabrication_decoys"]
    assert row.warn_by_default and "entry 1" in row.detail


@pytest.mark.parametrize("item", ["Examplelang#", "Example.lang", ".Examplenet", "Examplelang.",
                                  "9Examplelang"])
def test_doctor_and_the_gate_agree_on_each_token_edge_case(tmp_path, item):
    """Spec §9.5: doctor and the gate share core/tokens.py, so they cannot disagree about a
    `#`-suffixed token, a dotted name, a leading-dot name or a trailing dot. Pinned, against
    the day one of them grows its own parsing."""
    v = _vault(tmp_path, entries=(("alpha", dict({"Company": "Example Foundry"}, Tools=item)),))
    doctor_refuses = any(c.subject == "Experience Library (Tools)" for c in _doctor(v).components)
    assert doctor_refuses == bool(missing_prerequisites(v)), item


def test_doctor_reports_no_baseline_and_no_skills_rows(tmp_path):
    subjects = {c.subject for c in _doctor(_vault(tmp_path)).components}
    assert "baseline_rel" not in subjects
    assert not [s for s in subjects if "Skills)" in s or "Skills:)" in s]
```

Create `tests/test_cv_attribution_vaults.py`:

```python
"""Whether the misattributed-tool check ran is REPORTED, never left to infer (spec §6.6,
§12.1): four vaults read through the real Vault -- never a hand-built entry dict -- each
asserting the flag on CvResult, run.json and the MCP result, and the run-wide WARNING on an
upgraded vault only, once across two leads. D13's end-to-end row lives here too, since it
needs the same real-store fixture."""
import json

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from tests.structured_cv import Cache, RecordingRenderer, ReplyBackend, reply
from tests.test_cv_prerequisites import _seed_shortlist_leads, _vault

# "Example Foundry" codes to EX (cv/bundle.py::_prefix's fallback); the entry has no body,
# so the bullet carries no figure.
REPLY = reply(roles={"R1": [{"text": "Shipped the platform", "cites": ["EX1"]}]}, skills=())


def _vault_with(tmp_path, *, tools="", skills="", unverified_tools=""):
    v = _vault(tmp_path, entries=())
    # `Tools` as a keyword: in a dict literal keyed by the field name, with a variable as
    # its value, the fixture-name sweep reads the variable's NAME as a tool.
    fields = dict({"Company": "Example Foundry"}, **(dict(Tools=tools) if tools else {}))
    v.propose_evidence("experience", name="alpha", fields=fields)
    [pending] = v.read_pending_evidence("experience")
    with open(pending["path"], encoding="utf-8") as fh:
        raw = fh.read()
    if skills:
        # A 3.x entry's `Skills:` line: the user's own text, which the store no longer
        # writes, so it is added the way a user's vault already holds it.
        raw = raw.replace("---\n", f"---\nSkills: {skills}\n", 1)
        with open(pending["path"], "w", encoding="utf-8") as fh:
            fh.write(raw)
    assert v.verify_evidence("experience", "alpha", today="2026-09-03", reviewed=raw)
    if unverified_tools:
        v.propose_evidence("experience", name="beta",
                           fields=dict({"Company": "Example Foundry"}, Tools=unverified_tools))
    return v


def _app(v, tmp_path, monkeypatch, replies):
    monkeypatch.chdir(tmp_path)        # cv.output_dir and served_dir default to ./cv-*
    be, rend = ReplyBackend(replies), RecordingRenderer()
    monkeypatch.setattr(Sluice, "backend", lambda self, **kw: be)
    monkeypatch.setattr(Sluice, "renderer", lambda self, cvcfg: rend)
    monkeypatch.setattr(Sluice, "dossier_cache", lambda self, *a, **k: Cache())
    return Sluice(Config(vault_dir=v.dir))


@pytest.mark.parametrize("vault_kw,off,warned", [
    ({"skills": "Examplelang"}, True, True),             # upgraded: a Skills line, no Tools
    ({}, True, False),                                    # neither: an unconfigured install
    ({"tools": "Examplelang"}, False, False),             # a verified Tools line turns it on
    ({"unverified_tools": "Examplelang"}, True, False),   # an unverified one does not
], ids=["skills-only", "neither", "verified-tools", "unverified-tools"])
def test_the_attribution_flag_is_reported_on_every_surface(
        tmp_path, monkeypatch, caplog, vault_kw, off, warned):
    from sluice.mcpserver import cv_run
    v = _vault_with(tmp_path, **vault_kw)
    _seed_shortlist_leads(v.dir, n=2)
    app = _app(v, tmp_path, monkeypatch, [REPLY, REPLY, REPLY])
    with caplog.at_level("WARNING"):
        results = app.compose_cv(all_shortlist=True)
    assert [(r.status, r.attribution_check_off) for r in results] == [("rendered", off)] * 2
    said = [r for r in caplog.records if "attribution check" in r.getMessage()]
    assert len(said) == (1 if warned else 0), "once per RUN, never per lead"
    for i in range(2):
        run = json.loads((tmp_path / "cv-output" / f"example-foundry-synthetic-role-{i}"
                          / "run.json").read_text(encoding="utf-8"))
        assert run["attribution_check_off"] is off
    assert cv_run(app, lead="SYNTHETIC-ROLE-0")["attribution_check_off"] is off


def test_a_skill_name_the_slug_destroys_reaches_the_cv_through_its_label(tmp_path, monkeypatch):
    """Spec §12.1 (D13), through the real store: a skill proposed under a name its filename
    slug destroys, verified, then picked -- the document lists the TYPED spelling, which
    only Label: kept."""
    v = _vault_with(tmp_path)
    v.propose_evidence("skills", name="Examplelang#", fields={"Label": "Examplelang#"})
    [pending] = v.read_pending_evidence("skills")
    assert pending["title"] != "Examplelang#", "premise: the slug changed the typed name"
    with open(pending["path"], encoding="utf-8") as fh:
        raw = fh.read()
    assert v.verify_evidence("skills", pending["title"], today="2026-09-03", reviewed=raw)
    _seed_shortlist_leads(v.dir, n=1)
    picked = reply(roles={"R1": [{"text": "Shipped the platform", "cites": ["EX1"]}]},
                   skills=["Examplelang#"])
    app = _app(v, tmp_path, monkeypatch, [picked])
    [result] = app.compose_cv(all_shortlist=True)
    assert result.status == "rendered"
    [(document, _out)] = app.renderer(None).rendered     # _app's renderer ignores its argument
    assert list(document.skills) == ["Examplelang#"]


def test_unverified_names_and_tools_never_reach_the_pool(tmp_path, monkeypatch):
    """Spec §12.1 (Skills pool), through the real store: only VERIFIED entries feed the pool.
    A pick of an unverified skill note's name, or of an unverified entry's tool, is dropped
    and reported, never listed. The verified entry's own tool keeps the pool non-empty, so
    skills ARE requested and the two picks are refused for what they are."""
    v = _vault_with(tmp_path, tools="Examplelang", unverified_tools="Examplelangscript")
    v.propose_evidence("skills", name="Example Query", fields={})      # never verified
    _seed_shortlist_leads(v.dir, n=1)
    picked = reply(roles={"R1": [{"text": "Shipped the platform", "cites": ["EX1"]}]},
                   skills=["Example Query", "Examplelangscript"])
    app = _app(v, tmp_path, monkeypatch, [picked])
    [result] = app.compose_cv(all_shortlist=True)
    [(document, _out)] = app.renderer(None).rendered
    assert list(document.skills) == []
    for name in ("Example Query", "Examplelangscript"):
        assert any(name in dropped for dropped in result.skills_dropped), name
```

Run: `.venv/bin/python -m pytest tests/test_doctor_cv_layout.py tests/test_cv_attribution_vaults.py -q`
Expected: FAIL (doctor has no `cv_layout` row yet; `compose_cv` logs no warning; the MCP result has no `attribution_check_off`).

Then implement. In `sluice/core/app.py::Sluice.compose_cv`, import `run_warnings` beside `missing_prerequisites`, and directly after the prerequisites refusal:

```python
        # spec §6.6: once per RUN, never per lead.
        for warning in run_warnings(store):
            _log.warning("%s", warning)
        # No renderer for a dry run: it never renders. A dry run used to build one only so the
        # renderer's `precheck` could run, and that hook no longer exists (spec §7.2).
        renderer = None if dry_run else self.renderer(cvcfg)
```

replacing the `if dry_run: ... RenderError ... else: renderer = self.renderer(cvcfg)` block. In its docstring, replace the paragraphs about resolving the renderer for a dry run with: "A dry run builds no renderer and renders nothing; it still spends a composition and an audit call per lead, since both run above the dry-run return in `cv/engine.py::run_one`."

In `Sluice.doctor`, replace everything from the `# #259. Read HERE` comment through the `classify_skills_reconciliation` call with:

```python
            # #364/#365/#368 (spec §9.1). The CV Layout is read ONCE, in its own try (#259:
            # one bad note never collapses the store rows), and classified purely. The rows
            # that need the parsed layout run only when it parsed, so an absent, malformed or
            # unreadable note produces exactly the cv_layout row.
            layout, layout_error = None, None
            try:
                layout = store.read_cv_layout()
            except Exception as e:  # noqa: BLE001 -- it IS the cv_layout row's verdict
                layout_error = e
            components.append(_doctor.classify_cv_layout(layout, layout_error))

            # The VERIFIED experience entries, read once and shared by every row below: the
            # gate and the skills pool read only those.
            experience = None
            try:
                experience = store.read_evidence("experience", verified_only=True)
            except Exception as e:  # noqa: BLE001 -- classify_store reports an unreadable
                # corpus DEAD when the store implements preflight; this is the only signal
                # when it does not.
                _log.warning("experience read for the cv rows failed: %s", e)
            else:
                components.extend(_doctor.classify_tools(experience))
                components.extend(_doctor.classify_attribution(experience))
                if layout is not None:
                    components.extend(_doctor.classify_cv_eligibility(layout, experience))

            # The skill names the pool would offer, derived by cv/selection.py itself, so
            # this row and the composer agree on what a skill is called (D12, D13).
            from sluice.cv.selection import cv_name, named_entries
            skill_names = None
            try:
                skill_names = [cv_name(e) for e in named_entries(store.read_evidence)]
            except Exception as e:  # noqa: BLE001 -- same reasoning as the read above
                _log.warning("skills read for the decoy cross-check failed: %s", e)
            if cv_cfg is not None and experience is not None and skill_names is not None:
                components.extend(_doctor.classify_decoys(
                    cv_cfg.fabrication_decoys, experience, skill_names, layout))
```

and update `Sluice.doctor`'s docstring: the CV Layout note replaces the baseline CV in its list of what doctor preflights, and the sentences about the negatives-vs-Skills-Inventory cross-check go (the comment at the `load_cv_config()` guard that names that cross-check as a consumer of `cv_cfg` now names the decoy row).

In `sluice/core/doctor.py`, delete `classify_skills_request`, `classify_skills_reconciliation`, `classify_negatives_vs_skills` and what only they use: `_declared_skills`, `_SKILLS_FIELD` and its comment block, `_MIN_TERM_LEN`, `_NEGATION_WORDS`, `_NEGATION_STEMS`, `_CLAUSE_BREAK_RE` and `_negated_stems`. Then run `.venv/bin/ruff check sluice` and drop any import it reports unused (`stem`, `stem_all` and `tokens` from `sluice.core.stem` are the likely ones — remove a name only if ruff says it is unused).

`mcpserver.py::cv_run` gains the flag in this step (the rest of its reporting is Step 5): add `"attribution_check_off": r.attribution_check_off` to the `out` dict beside `skills_unreadable`.

Run: `.venv/bin/python -m pytest tests/test_doctor_cv_layout.py tests/test_cv_attribution_vaults.py tests/test_cv_prerequisites.py -q`
Expected: PASS.

- [ ] **Step 5: Report the new fields on every surface, and keep the pinned docs in step**

Spec §9.2. `tests/test_docs_claims.py` pins `cmd_cv_run`'s per-result keys and per-finding labels to `docs/USAGE.md`, and the gate's categories to `docs/TROUBLESHOOTING.md`'s `skipped-gate` section, so each change below lands with its doc in this step.

Write the failing tests. Add to `tests/test_cv_run_cli.py`:

```python
def test_cmd_cv_run_reports_the_selection_and_the_attribution_state(monkeypatch, tmp_path,
                                                                    capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf",
        skills_dropped=["'Example Ghost': not one of your skills"],
        bullets_trimmed=["R1 (Example Foundry): kept 2 of 3"], attribution_check_off=True)
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])
    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "skills_dropped=1 bullets_trimmed=1" in err
    assert "attribution_check_off=True" in err
    assert _detail_lines(err) == ["  DROPPED: 'Example Ghost': not one of your skills",
                                  "  TRIMMED: R1 (Example Foundry): kept 2 of 3"]


def test_cmd_cv_run_names_whichever_note_refused_a_skipped_config_lead(monkeypatch,
                                                                      tmp_path, capsys):
    from sluice.cv.engine import _IDENTITY_REFUSAL, _LAYOUT_REFUSAL
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    results = [CvResult("a.md", "skipped-config", error=_LAYOUT_REFUSAL),
               CvResult("b.md", "skipped-config", error=_IDENTITY_REFUSAL)]
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: results)
    assert cmd_cv_run(_args(), Config()) == 1
    err = capsys.readouterr().err
    assert f"cv: {_LAYOUT_REFUSAL}" in err and f"cv: {_IDENTITY_REFUSAL}" in err
```

Add to `tests/test_mcpserver.py`:

```python
def test_cv_run_tool_reports_drops_and_trims_and_warns_only_for_model_text(monkeypatch,
                                                                          tmp_path):
    """`skills_dropped` quotes the MODEL's own picks, which a job ad can steer, so it joins
    the content warning's trigger (spec §9.2); `bullets_trimmed` is a slot id, a vault
    heading and two counts, so it does not. Each list alone, so neither can pass because the
    other was populated."""
    from sluice.cv.engine import CvResult

    trimmed = CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
                       served="Example_CV_deadbeef.pdf",
                       bullets_trimmed=["R1 (Example Foundry): kept 2 of 3"])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [trimmed])
    out = cv_run(_cv_app(Vault(str(tmp_path))), "Example Foundry - Analyst")
    assert out["bullets_trimmed"] == ["R1 (Example Foundry): kept 2 of 3"]
    assert "skills_dropped" not in out and "content_warning" not in out

    dropped = CvResult("Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
                       served="Example_CV_deadbeef.pdf",
                       skills_dropped=["'Example Ghost': not one of your skills"])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [dropped])
    out = cv_run(_cv_app(Vault(str(tmp_path))), "Example Foundry - Analyst")
    assert out["skills_dropped"] == ["'Example Ghost': not one of your skills"]
    assert out["content_warning"] == mcpserver_mod._CV_RUN_CONTENT_WARNING
    assert "skills_dropped" in mcpserver_mod._CV_RUN_CONTENT_WARNING
```

In `tests/test_docs_claims.py`: `test_the_cv_summary_key_extraction_is_not_vacuous`'s expected set gains `"skills_dropped"`, `"bullets_trimmed"` and `"attribution_check_off"`; `test_usage_md_documents_the_label_each_finding_kind_actually_gets`'s expected dict gains `"skills_dropped": "  DROPPED: "` and `"bullets_trimmed": "  TRIMMED: "`; and `_FOLDED_IN_CATEGORIES` becomes `{"REPLY": "sluice/cv/reply.py"}`, its comment saying that `REPLY` findings come from reading the reply (`cv/reply.py`, and the zero-bullet rule in `cv/selection.py`), while `STRUCTURAL` and `FORMAT` no longer exist once the structure is data (spec §6.5). Ledger: those three tests → ported as the same ids, expectations widened to the new fields. `test_troubleshooting_names_the_two_folded_in_violation_producers` is renamed `test_troubleshooting_names_the_folded_in_violation_producer`, its docstring saying that `REPLY` reaches `violations` from outside `cv/validate.py` -- `cv/reply.py`'s reading of the reply, and `cv/selection.py`'s zero-bullet rule -- so the category sweep above cannot see it (ledger: ported as the renamed id).

`tests/test_cv_run_cli.py::test_cmd_cv_run_prints_the_gate_violations_on_skipped_gate` keeps its two-producer shape with today's producers: its `CvResult`'s violations become `"INVENTED METRIC ['80'] not in ['EF1']: Cut costs by 80 percent"` (`cv/validate.py`) and `"REPLY: profile missing or empty -- give a 2 to 3 sentence profile"` (`cv/reply.py`), `violations=2` stays, the two indented-line assertions follow the new strings, and the docstring and the closing comment name `cv/validate.py` and `cv/reply.py` as the producers in place of the renderer's `precheck`, `STRUCTURAL` and `FORMAT` (ledger: ported, premise changed, spec §6.1).

Run: `.venv/bin/python -m pytest tests/test_cv_run_cli.py tests/test_mcpserver.py tests/test_docs_claims.py -q`
Expected: FAIL (no new keys printed; the docs do not name them).

Then implement. In `sluice/cli.py::cmd_cv_run`:

- Replace the `skipped-config` block (the one that imports `CANDIDATE_PROFILE_RELPATH` and prints a fixed sentence about the Candidate Profile) with the engine's own reason, one line per distinct refusal; keep the comment above it, rewording its last sentence to say the reason now comes from `CvResult.error` because two notes can refuse a lead:

```python
    refusals = sorted({r.error for r in results if r.status == "skipped-config"})
    if refusals:
        for reason in refusals:
            print(f"cv: {reason}", file=sys.stderr)
        return 1
```

- The per-result line becomes:

```python
        print(f"cv: {r.status} {r.lead} served={r.served} "
              f"violations={len(r.violations)} audit_flags={len(r.audit_flags)} "
              f"slop={len(r.slop)} voice_flags={len(r.voice_flags)} terms={len(r.terms)} "
              f"skills_dropped={len(r.skills_dropped)} "
              f"bullets_trimmed={len(r.bullets_trimmed)} "
              f"dossier_failed={r.dossier_failed} "
              f"skills_unreadable={r.skills_unreadable} "
              f"attribution_check_off={r.attribution_check_off} "
              f"artefacts_failed={r.artefacts_failed}",
              file=sys.stderr)
```

- After the `terms` loop, print the two new kinds, each with its own label:

```python
        for d in r.skills_dropped:
            print(f"  DROPPED: {d}", file=sys.stderr)
        for t in r.bullets_trimmed:
            print(f"  TRIMMED: {t}", file=sys.stderr)
```

In `sluice/mcpserver.py::cv_run`, after the `terms` key:

```python
    # spec §9.2: what the selection dropped. Sparse like the lists above. Only
    # `skills_dropped` quotes model-written text -- the model's own picks, which a job ad
    # can steer -- so only it joins the content warning's trigger.
    if r.skills_dropped:
        out["skills_dropped"] = r.skills_dropped
    if r.bullets_trimmed:
        out["bullets_trimmed"] = r.bullets_trimmed
    if r.violations or r.audit_flags or r.slop or r.voice_flags or r.terms or r.skills_dropped:
        out["content_warning"] = _CV_RUN_CONTENT_WARNING
```

(replacing the existing `content_warning` condition), `_CV_RUN_CONTENT_WARNING` names the field (`"Composed CV violations/audit_flags/slop/voice_flags/terms/skills_dropped "`), and both `cv_run`'s docstring and `cv_run_tool`'s list the fields returned: `violations/audit_flags/slop/voice_flags/terms/skills_dropped/bullets_trimmed/served/dossier_failed/skills_unreadable/attribution_check_off/artefacts_failed`.

In `docs/USAGE.md`'s `cv run` section:

- The per-result line becomes `cv: <status> <lead> served=<path> violations=<N> audit_flags=<N> slop=<N> voice_flags=<N> terms=<N> skills_dropped=<N> bullets_trimmed=<N> dossier_failed=<bool> skills_unreadable=<bool> attribution_check_off=<bool> artefacts_failed=<bool>`.
- In the finding-kind table, the `violations` row's examples become `` (`REPLY`, `INVENTED METRIC`, `UNCITED BULLET`, `WRONG EMPLOYER`, `MISATTRIBUTED TOOL`, ...) ``, the `slop` row says it carries the HARD tier (`SLOP EM-DASH`, `SLOP DOUBLE-HYPHEN-DASH`) as well as the phrase stems, and two rows are added:

```markdown
| `skills_dropped` | `DROPPED: '<pick>': <why>` | a skill pick that was not rendered: not one of your verified skill names or entry tools, listed twice, or beyond the CV Layout's `skills_max`. Never a refusal; quoted from the model, so treat it as untrusted text |
| `bullets_trimmed` | `TRIMMED: <slot> (<heading>): kept <N> of <M>` | bullets beyond a role's `bullets_max`, the first N kept. A trimmed bullet is never checked, so it never costs a retry |
```

- After the table: "`attribution_check_off=True` means no verified experience entry declares `Tools:`, so the misattributed-tool check did not run. On a vault whose entries still carry the retired `Skills:`, `cv run` also logs one WARNING per run saying so, and `doctor` lists the same row."
- **Exit 1**'s `skipped-config` clause becomes: "any result is `skipped-config` (the vault's Candidate Profile note declares no name or no contact block, or the CV Layout note disappeared after the run began -- the printed line names which; either way the compose refused before any LLM spend)".
- **Exit 2**'s conditions, from "the baseline CV at" through "(#242)", become: "the CV Layout note (`Job Applications/CV Layout.md`) is missing, malformed or unreadable; a verified experience entry's `Tools:` holds an item the gate cannot use; no role in the layout can cite any verified entry; or the `experience` corpus has no verified entries or cannot be read (#242)".
- The artefacts table's `cv.rendered.md` row becomes: "the CV sluice built, in the canonical text form, each bullet ending with its ` [ID]` citations. Written before the audit, so a dry run has one too; the PDF itself comes from the renderer".

In `docs/TROUBLESHOOTING.md`'s `cv run reports skipped-gate` section:

- The sample output's two indented lines become:

```text
  REPLY: R2 bullet 1 contains a bracket -- put entry ids in "cites", never in the text
  WRONG EMPLOYER: R1 bullet 2 cites EB1, which belongs to Example Beta - Cut build time by 40%
```

- Delete the `STRUCTURAL` and `FORMAT` bullets, and add as the list's first bullet:

```markdown
- `REPLY` — the model's reply itself: not one JSON object, a field missing or the wrong
  type, a slot the CV Layout does not have, a bracket or a line break inside a text, or no
  bullets in any role that can carry them. The retry is told exactly which; nothing in your
  vault needs to change.
```

- The `violations=0` paragraph's "an em dash or a literal `--` anywhere in the document" becomes "an em dash or a literal `--` in the profile or a bullet the model wrote (never in your own vault text, which renders as written)".
- The artefacts sentence's "`cv.attempt-1.md` and `cv.attempt-2.md` are the two drafts they were found in" becomes "`reply.attempt-1.txt` and `reply.attempt-2.txt` are the two replies they were found in, exactly as received".

Run: `.venv/bin/python -m pytest tests/test_cv_run_cli.py tests/test_mcpserver.py tests/test_docs_claims.py -q`
Expected: PASS. (`TROUBLESHOOTING.md` keeps its `UNSOURCED SKILL`, `MISATTRIBUTED SKILL`, `MISSING EMPLOYER` and `NOT REVERSE-CHRONOLOGICAL` bullets for now: the guard derives the categories from `cv/validate.py`, where the text `validate()` that emits them stays until Task 19 deletes it and them together.)

- [ ] **Step 6: The harness composes structured, end to end**

Spec §12.2 (Harness and end-to-end). These tiers run the real composition root, so each fixture below replaces a CV text with a REPLY, and each `recorder.rendered == [PASSING_CV]` becomes an assertion on the DOCUMENT the renderer received.

`tests/harness/config.py`: replace `PASSING_CV` and `GATE_FAILING_CV` with:

```python
# A reply, as the composer returns it (spec §5.2): one cited bullet under the one role the
# harness layout declares, its figures all in the seeded entry.
PASSING_REPLY = json.dumps({
    "profile": "I build reliable systems.",
    "roles": {"R1": [{"text": "Grew the team from 3 to 8 engineers", "cites": ["EF1"]}]},
    "skills": [],
})
# What a run of PASSING_REPLY hands the renderer, as the bullets of its one role.
PASSING_BULLETS = ["Grew the team from 3 to 8 engineers"]
# Fails exactly ONE named hard check -- a figure the cited entry does not carry -- so a row
# can assert which (spec §12.2).
GATE_FAILING_REPLY = json.dumps({
    "profile": "I build reliable systems.",
    "roles": {"R1": [{"text": "Grew the team from 3 to 80 engineers", "cites": ["EF1"]}]},
    "skills": [],
})
GATE_FAILING_FINDING = "INVENTED METRIC ['80'] not in ['EF1']"
# The CV Layout every harness vault is seeded with: one role, at the employer the default
# experience entry names, so its work is citable under R1.
DEFAULT_LAYOUT_ROLES = [{"heading": "Example Foundry", "from": "02/2023", "to": "present",
                         "location": "Example Location A", "title": "SYNTHETIC-TITLE-1"}]
```

(add `import json` and `from tests.conftest import layout_yaml` to its imports). In `_seed_vault`, write the CV Layout note and `Tools:` instead of `Skills:`:

```python
    layout_path = os.path.join(vault_dir, CV_LAYOUT_RELPATH)
    os.makedirs(os.path.dirname(layout_path), exist_ok=True)
    with open(layout_path, "w", encoding="utf-8") as f:
        f.write(layout_yaml(DEFAULT_LAYOUT_ROLES))
```

placed after the baseline write, with `CV_LAYOUT_RELPATH` imported from `sluice.core.protocols` beside `CANDIDATE_PROFILE_RELPATH`, and the frontmatter line `f'Skills: "{e.get("skills", "")}"'` becoming `f'Tools: "{e.get("tools", "")}"'`.

`tests/harness/renderer.py`: delete `RecordingRenderer.precheck` (the engine no longer calls one, spec §7.2), and make `render` record the document:

```python
    def render(self, document, out_dir, *, neutral_name="CV.pdf"):
        self.recorder.rendered.append(document)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, neutral_name)
        with open(path, "wb") as f:
            f.write(_MINIMAL_PDF)
        self.recorder.paths.append(path)
        return path
```

`Recorder.rendered`'s comment becomes "every CvDocument the engine asked to render", and the module docstring's "keep every `cv_text`" becomes "keep every document". `tests/harness/__init__.py` exports `PASSING_REPLY`, `PASSING_BULLETS`, `GATE_FAILING_REPLY` and `GATE_FAILING_FINDING` in place of `PASSING_CV` and `GATE_FAILING_CV`.

`tests/harness/backend.py`: `_CV`'s trailing comment cites `cv/compose.py::build_structured_prompt`. The structured prompt opens with the same words, so the routing itself does not change.

`tests/harness/registry.py` (spec §12.2): the snapshot forces EVERY seam's one-time autoload, from `core.app._SEAMS`, instead of its hand-listed four. Measured in a fresh interpreter: after the four, `sluice.rates` is still unimported and the registry holds no `rates` entry, so the restore after the first test that autoloads it drops the production `frankfurter` plugin for the rest of the process. The import line becomes `from sluice.core.app import _SEAMS, Sluice`, and the setup moves into a plain function the fixture calls, so a test can drive it:

```python
def _snapshot():
    """Force every seam's one-time autoload, then copy the registry. The seams come from
    core.app._SEAMS: a hand-listed four skipped `rates`, so the restore after the first
    test that autoloaded it dropped the production rates plugin for good (the cached
    import never re-runs autoload)."""
    for seam in _SEAMS:
        Sluice.available(seam)  # triggers each seam's one-time autoload
    return {seam: dict(impls) for seam, impls in plugins._REGISTRY.items()}
```

`isolate_plugin_registry` keeps its docstring; its body becomes `snapshot = _snapshot()`, `yield`, then the same `clear()`/`update(snapshot)` restore. Create `tests/test_harness_registry.py`:

```python
"""tests/harness/registry.py's snapshot reaches every seam (spec §12.2)."""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_the_registry_snapshot_holds_every_seams_production_plugins():
    """In a FRESH interpreter, because that is the only place the ordering shows: inside
    the suite some earlier test has usually autoloaded every seam already, so a snapshot
    that forced only four would look complete here."""
    code = ("from sluice.core.app import _SEAMS\n"
            "from tests.harness.registry import _snapshot\n"
            "snap = _snapshot()\n"
            "missing = [s for s in _SEAMS if not snap.get(s)]\n"
            "assert not missing, missing\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
```

`tests/conftest.py::make_composable` seeds what `cv run` now requires:

```python
def make_composable(vault):
    """Give a vault the config-level preconditions `cv run` requires (#242, spec §9.1): a CV
    Layout note, and one verified experience entry whose Company: a role in it matches.

    Deliberately NOT keyed on `skills`/`stories`: `missing_prerequisites` requires neither,
    so neither does this."""
    import os

    from sluice.core.protocols import CV_LAYOUT_RELPATH

    path = os.path.join(vault.dir, CV_LAYOUT_RELPATH)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(layout_yaml())
    if vault.read_evidence("experience"):
        return vault                       # idempotent: a second call must not re-propose
    vault.propose_evidence("experience", name="alpha", fields={"Company": "Example Foundry"})
    pending = {e["title"]: e for e in vault.read_pending_evidence("experience")}
    with open(pending["alpha"]["path"], encoding="utf-8") as fh:
        raw = fh.read()
    vault.verify_evidence("experience", "alpha", today="2026-09-03", reviewed=raw)
    return vault
```

The end-to-end and functional tests:

- `tests/e2e/test_a_clean_lead_reaches_rejected.py`: `cv_by_company={"Example Foundry": PASSING_REPLY, "Example Telemetry": GATE_FAILING_REPLY}`, and `assert h.recorder.rendered == [PASSING_CV]` becomes `assert [d.work[0].bullets for d in h.recorder.rendered] == [PASSING_BULLETS]` (exact, so it still catches a spurious extra render and the failing reply rendered past the gate). Its `cv.rendered.md`-absent assertion on the gated lead holds as written: that file is written only for a retained attempt.
- `tests/e2e/test_a_run_reports_what_it_spent.py`: `PASSING_CV` → `PASSING_REPLY`. It stays the `cv-compose` witness (spec §12.2).
- `tests/e2e/test_a_cv_citing_an_unbacked_figure_never_ships.py`: the violation becomes a reply, and the assertions keep their shape:

```python
NUMERIC_VIOLATION_REPLY = json.dumps({
    "profile": "I build reliable systems.",
    "roles": {"R1": [{"text": "Cut deploy time by 42 percent", "cites": ["EF1"]}]},
    "skills": []})
```

  with `assert "INVENTED METRIC" in r.violations[0] and "42" in r.violations[0]` and `assert h.recorder.rendered == []` unchanged.
- `tests/e2e/test_a_cv_citing_an_unbacked_skill_never_ships.py`: a pick off the pool is now DROPPED, not refused (spec §6.2), so the lead renders without it. `ANNOTATED_EXPERIENCE = [{**DEFAULT_EXPERIENCE[0], "tools": "Example Query"}]`, the reply picks `["Example Ghost"]`, and the assertions become:

```python
    assert any(_SKILLS_POOL_PROMPT_HEADER in p and "- Example Query" in p
               for p in backend.prompts)
    assert (r.status, composes) == ("rendered", 1)
    assert r.skills_dropped == ["'Example Ghost': not one of your skills"]
    assert [d.skills for d in h.recorder.rendered] == [[]]
```

  importing `_SKILLS_POOL_PROMPT_HEADER` from `sluice.cv.compose`. Its docstring says the property is unchanged — an unbacked skill never ships — and only the mechanism moved from a refusal to a drop.
- `tests/e2e/test_an_unannotated_vault_never_requests_a_skills_section.py`: with no `Tools:` and no verified skill notes the pool is empty, so no skills are requested (spec §4.4):

```python
    for prompt in compose_prompts:
        assert _SKILLS_POOL_PROMPT_HEADER not in prompt
        assert _NO_SKILLS_RULE_PROMPT in prompt
    assert len(compose_prompts) == 1
    assert (r.status, r.violations) == ("rendered", [])
    [doc] = h.recorder.rendered
    assert doc.skills == [] and "SKILLS" not in to_text(doc)
```

  importing `_NO_SKILLS_RULE_PROMPT` and `_SKILLS_POOL_PROMPT_HEADER` from `sluice.cv.compose` and `to_text` from `sluice.cv.document`.
- `tests/functional/test_cv.py`: `PASSING_CV` → `PASSING_REPLY`, and each `h.recorder.rendered == [PASSING_CV]` becomes `[d.work[0].bullets for d in h.recorder.rendered] == [PASSING_BULLETS]`.

Each of these is a ledger row: ported as the same id.

Add to `tests/test_renderers.py` (spec §12.1: every registered renderer AND every test fake matches the new parameters):

```python
def test_every_test_fake_renderer_takes_a_document():
    import inspect
    from tests.harness.renderer import RecordingRenderer as HarnessRenderer
    from tests.structured_cv import RecordingRenderer
    from tests.test_cv_engine import FakeRenderer
    for cls in (HarnessRenderer, RecordingRenderer, FakeRenderer):
        params = list(inspect.signature(cls.render).parameters)
        assert params == ["self", "document", "out_dir", "neutral_name"], (cls, params)
```

Run: `.venv/bin/python -m pytest tests/e2e tests/functional/test_cv.py tests/test_renderers.py -q`
Expected: PASS.

Accepted window, stated: from this commit `FABRICATED` matches decoys as whole terms, while `validate_decoys` refuses an unmatchable decoy at load only from Task 20. In the commits between, such a decoy never matches. All of them ship in one release, so no user meets the window.

- [ ] **Step 7: The fixture-name sweep reads `Tools:` wherever it read `Skills:`**

Spec §12.2. This lands WITH the field rename, not later: the moment a fixture moves from `Skills:` to `Tools:`, a sweep that cannot see `Tools:` reports the value's roster entry as stale, and the honest response to a stale entry is to delete it — which would delete a review for a value still in `tests/`. In `tests/test_fixture_name_neutrality.py`:

- After `_SKILL_BLOCK_LIST_COLLECTOR`, add:

```python
# #364/#365/#368: `Tools:` is the experience entries' attribution field now (spec §4.2) and
# holds what `Skills:` held -- a tool's NAME -- so it is swept in every spelling `Skills:` is,
# onto the same reviewed roster. `Skills:` stays swept too: upgrade fixtures still write it.
_TOOLS_COLLECTOR = ("evidence Tools: (frontmatter or dict literal)",
                    _evidence_field_re("Tools"))
_TOOLS_BLOCK_LIST_COLLECTOR = ("evidence Tools: (YAML block list)",
                               _evidence_block_list_re("Tools"))
```

- `_is_skill_kwarg` returns `n in ("skills", "tools") or n.endswith(("_skills", "_tools"))`, its docstring naming both.
- In `_ast_skill_values`, every dict-key test `k.value == "Skills"` becomes `k.value in ("Skills", "Tools")`.
- `_all_fixture_skill_values` reads both:

```python
    values = set()
    for collector in (_SKILL_COLLECTOR, _TOOLS_COLLECTOR):
        for raw in _collect(collector[1]):
            values |= {part.strip() for part in raw.split(",") if part.strip()}
    for text in _test_sources():
        for collector in (_SKILL_BLOCK_LIST_COLLECTOR, _TOOLS_BLOCK_LIST_COLLECTOR):
            values |= set(_block_list_items(collector[1], text))
        values |= _ast_skill_values(text)
    return values
```

  and its docstring says `Tools:` joins `Skills:` and why.
- Add a shape row and the residual `Skills:` floor (spec: "a separate floor is kept for the residual `Skills:` fixtures"):

```python
def test_the_evidence_tools_collectors_see_every_shape_they_claim_to():
    """The four spellings, on synthetic strings: frontmatter, a dict literal, a YAML block
    list and a keyword argument -- `Tools:` reuses machinery measured on `Skills:`, and this
    is what says it reaches the new key at all."""
    assert [_identity_of(v) for v in _TOOLS_COLLECTOR[1].findall(
        "Tools: Examplelang\\nverified: x")] == ["Examplelang"]
    assert [_identity_of(v) for v in _TOOLS_COLLECTOR[1].findall(
        'fields={"Tools": "Example Query"}')] == ["Example Query"]
    assert _block_list_items(_TOOLS_BLOCK_LIST_COLLECTOR[1],
                             "Tools:\\n  - Example Widget\\n  - Examplelang\\nverified: x") == [
        "Example Widget", "Examplelang"]
    assert _ast_skill_values('f(tools=["Example Framework"])\n') == {"Example Framework"}


def test_the_residual_skills_fixtures_are_still_swept():
    """Upgrade fixtures still write the retired `Skills:` field, so its collector must keep
    finding them: a floor of its own, so a broken `Skills:` pattern cannot hide behind the
    `Tools:` values in the union."""
    assert _collect(_SKILL_COLLECTOR[1]), "the Skills: collector matched no fixture"
```

Run: `.venv/bin/python -m pytest tests/test_fixture_name_neutrality.py -q`
Expected: PASS once Step 8's ports are in; before them it may name `Tools:` values the ports have not yet introduced, or `Skills:` values they have not yet moved. A value it names as UNREVIEWED that a ported fixture introduced on purpose is reviewed the usual way: confirm it is synthetic and add it to `_REVIEWED_SKILL_VALUES` with a comment naming the test. Measured against this plan's own fixtures, the values these collectors reach FIRST here are synthetic tool names Tasks 3, 9, 11, 13 and 17 wrote into `Tools` positions -- `.Examplenet`, `Example.lang`, `Examplestandard 9001`, `9001 Examplestandard`, `Exampleco`, `Examplelang3`, `Examplelang9`, `examplecoach`, `Exampleban`, `Examplelangscript` -- and `Examplelang#`, from Step 4's `Label:` row. Any OTHER value it names that you did not yourself just write as an obvious synthetic placeholder is a STOP: the default fix is the fixture, never the roster, and a value you cannot confirm is synthetic is escalated to the owner. A value that is a FRAGMENT of prose or code is never rostered, since it is no skill at all: reword the comment so `Tools` is not followed by a colon and ordinary words, or build the dict as `dict({...}, Tools=value)` so a variable's NAME is not read as a tool.

- [ ] **Step 8: Port the remaining engine-facing tests**

Run `.venv/bin/python -m pytest -q -p no:randomly 2>&1 | tail -40` and work through the red tests by file. The three rules again: delete only a test of deleted behaviour (ledger: `obsolete: <why>, spec §<n>`, plus where its surviving property is now pinned, if anywhere); port everything else to the new input with its assertion kept (ledger: `ported as <id>`); never loosen an assertion to make it pass. Tests that call the text machinery directly (`parse_cv`, the text `validate`, `build_prompt`, `render_bundle`, `bundle_sources`, `mention_vocab`) and still pass are LEFT for Task 19, which deletes that machinery with them.

**`tests/test_cv_engine.py`** keeps its own fakes — Task 6 already gave its `FakeVault` a `layout=SYNTHETIC_LAYOUT` and `read_cv_layout`, and its `ENTRIES` (company `Example Foundry`, prefix `EF`) match that layout's one role — and changes its payloads and its routing:

- `FakeRenderer.render(self, document, out_dir, *, neutral_name="CV.pdf")` records the document. Delete `PrecheckingRenderer`.
- Every fake backend in the file (`FakeBackend`, `_SequenceBackend`, `_VoiceBackend`, `ComposeCountingBackend`, `_CountingBackend`, `RecordingBackend` and the voice-failure subclasses) recognises a COMPOSE prompt by `prompt.startswith("Compose a tailored CV for")` in place of the `"SOURCE BUNDLE" in prompt and "auditing" not in prompt` test (the structured prompt carries no `SOURCE BUNDLE` header; the audit prompt still opens "You are auditing").
- Replace `CLEAN_CV` and its text variants with replies under the SAME names `_DRAFTS` keys them by, so test bodies keyed on those names are unchanged:

```python
import json


CLEAN_BULLETS = ("Shipped", "Grew team from 3 to 8", "Coached", "CI")


def _reply(profile="I build reliable systems.", bullets=CLEAN_BULLETS, skills=()):
    """A reply for SYNTHETIC_LAYOUT's one role (R1, Example Foundry), every bullet citing
    EF1. `CI` is in ENTRIES' body, so naming it draws no unbundled-term finding."""
    return json.dumps({"profile": profile,
                       "roles": {"R1": [{"text": b, "cites": ["EF1"]} for b in bullets]},
                       "skills": list(skills)})


CLEAN_REPLY = _reply()
HARD_DIRTY_REPLY = _reply(bullets=("Shipped", "Grew team from 3 to 8", "Coached — and mentored",
                                   "CI"))
STYLE_DIRTY_REPLY = _reply(profile="I leverage the same delivery patterns across teams.")
STYLE_DIRTIER_REPLY = _reply(profile="I leverage seamless delivery patterns across teams.")
STYLE_DIRTY_B_REPLY = _reply(profile="I foster the same delivery patterns across teams.")
UNBUNDLED_TERM_REPLY = _reply(profile="I build reliable systems on Examplequery.")
STYLE_DIRTY_WITH_TERM_REPLY = _reply(
    profile="I leverage the same delivery patterns across teams on Examplequery.")
# A slop stem in an EMPLOYER heading is vault text and draws nothing; the same stem family in
# the profile is the model's and is found. Run against EMPLOYER_PHRASE_LAYOUT.
EMPLOYER_PHRASE_REPLY = _reply(profile="I streamline delivery for platform teams.")
EMPLOYER_PHRASE_LAYOUT = dataclasses.replace(SYNTHETIC_LAYOUT, roles=(dataclasses.replace(
    SYNTHETIC_LAYOUT.roles[0], heading="Example Leverage",
    employers=("Example Foundry",)),))

_DRAFTS = {
    "clean": CLEAN_REPLY,
    "unbundled-term": UNBUNDLED_TERM_REPLY,
    "style-dirty-with-term": STYLE_DIRTY_WITH_TERM_REPLY,
    "hard-clean-style-dirty": STYLE_DIRTY_REPLY,
    "hard-clean-style-dirtier": STYLE_DIRTIER_REPLY,
    "hard-clean-style-dirty-b": STYLE_DIRTY_B_REPLY,
    "hard-dirty": HARD_DIRTY_REPLY,
    "employer-phrase": EMPLOYER_PHRASE_REPLY,
}
```

  (`import dataclasses`; `SYNTHETIC_LAYOUT` from `tests.conftest`.) The voice fixtures (`_VOICE_TWO_LINE_CV` and kin) become `_reply(...)` calls whose profile or bullets carry the phrases the scripted voice judge marks, so the judge still finds them in its excerpt.
- Assertions on what was rendered or audited compare the new forms. `rend.rendered == [CLEAN_CV]` becomes `[d.work[0].bullets for d in rend.rendered] == [list(CLEAN_BULLETS)]` — or, where the test is about WHICH attempt rendered, the profile alone (`[d.profile for d in rend.rendered] == [...]`). An `audited == [...]` comparison expects `cv/document.py::audit_text` of the retained reply: compute it with a helper that runs the reply through the same pure functions the engine uses (`extract_json`, `parse_reply`, `select`, `audit_text`) over `build_slots(SYNTHETIC_LAYOUT, <bundle entries>)`, so the test asserts WHICH attempt was audited without restating the text form.

These tests in `tests/test_cv_engine.py` are deleted, with these ledger rows; every test not listed is ported in place:

| Test | Now |
|---|---|
| `test_the_unparseable_fixture_still_passes_the_gate` | obsolete: the renderer grammar and `FORMAT` are gone, spec §6.5, §7.2 |
| `test_a_parse_failure_feeds_the_retry_not_the_bin`, `test_a_parse_failure_that_survives_the_retry_skips_the_lead` | ported as `tests/test_cv_structured_engine.py::test_a_reply_that_is_not_json_feeds_the_retry_and_a_second_one_skips_the_lead` |
| `test_the_preamble_fixtures_are_gate_clean_and_misparse`, `test_the_publications_fixture_passes_the_gate` | obsolete: the model writes no header and no section, spec §6.5 |
| `test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar`, `test_the_engine_folds_a_precheck_complaint_in_with_the_gates_own`, `test_a_precheck_returning_a_bare_string_is_refused_by_name`, `test_a_precheck_returning_a_non_string_element_is_refused_by_name`, `test_the_precheck_contract_still_accepts_both_intended_shapes` | obsolete: `precheck` is removed from the seam, spec §7.2 |
| `test_the_real_template_renderer_prechecks_through_run_one` | obsolete: `precheck` is removed, spec §7.2; the template renders the document without parsing, pinned by `tests/test_renderer_template.py::test_a_document_renders_without_being_parsed` |
| `test_drifted_work_header_fails_closed`, `test_a_headerless_draft_puts_the_hard_violation_first` | obsolete: no `STRUCTURAL` guard, the structure is data, spec §6.5 |
| `test_missing_profile_header_is_structural` | ported as the missing-`profile` row of `tests/test_cv_reply.py` (name it exactly in the ledger) |
| `test_a_preamble_before_the_name_fails_closed`, `test_a_preamble_with_a_real_contact_block_fails_closed`, `test_a_reversed_header_block_fails_closed`, `test_a_preamble_replacing_the_contact_line_fails_closed`, `test_a_reworded_contact_block_refuses_but_says_so_accurately`, `test_a_preamble_reaches_the_retry_not_the_bin`, `test_a_header_stripped_between_name_and_profile_still_fails_closed` | ported as `tests/test_cv_structured_engine.py::test_a_hostile_reply_cannot_reach_a_vault_owned_field`: the name and contact come from the Candidate Profile and never from the model, spec §6.5, §7.1 |
| `test_a_trailing_conversational_envelope_is_recovered_on_the_first_attempt` | ported as `tests/test_cv_structured_engine.py::test_each_reply_is_kept_raw_and_the_run_record_carries_the_selection_report` (a chat-wrapped reply rendered on its first attempt) |
| `test_a_line_in_BOTH_scoped_regions_is_complained_about_once` | obsolete: a reply has exactly one profile, spec §5.2 |
| `test_no_voice_call_is_spent_on_a_draft_with_no_prose_in_scope` | obsolete: a reply's profile is never empty, spec §5.2 |
| `test_a_voice_finding_on_an_EMPLOYER_line_never_reaches_the_retry`, `test_a_voice_finding_on_a_CERTIFICATE_line_never_reaches_the_retry` | ported as `tests/test_cv_structured_engine.py::test_the_voice_judge_is_shown_only_the_models_text` |
| `test_the_style_tier_is_not_scoped_over_the_skills_region` | ported as `tests/test_cv_structured_engine.py::test_vault_text_with_an_em_dash_or_a_slop_stem_renders_without_a_finding` |
| `test_one_malformed_skills_value_fails_every_lead_in_the_run` | ported as `tests/test_cv_prerequisites.py::test_a_run_over_two_leads_is_refused_once_with_no_fetch_and_no_compose` (an unusable `Tools:` is refused once, before any spend) |

Ported in place with a changed premise, each its own ledger row: `test_clean_cv_is_actually_clean` (the clean REPLY renders on its first attempt with no finding of any kind); `test_the_compose_prompt_carries_the_derived_identity_not_cvcfg` (the prompt names the derived candidate; the contact block no longer reaches the composer at all, since sluice assembles it — assert its absence); `test_the_composer_is_asked_for_a_skills_section_when_an_entry_declares_one` and its `_not_asked_` sibling (asked iff the skills pool is non-empty: an entry declaring `Tools:` or a verified skill note, spec §4.4); `test_skipped_gate_slop_carries_both_tiers_SLOP_formatted` (unchanged meaning: `slop` still carries the HARD tier, now found in the model's text only); the fixture-premise rows (`test_the_sequence_fixtures_are_the_tiers_they_claim`, `test_the_retention_fixtures_carry_the_finding_counts_they_claim`, `test_the_unbundled_term_fixture_carries_exactly_one_term`, `test_the_style_dirty_with_term_fixture_carries_one_slop_and_one_term`), which compute each reply's tier through the same pure functions the engine runs. `test_a_poisoned_entry_body_cannot_launder_a_fabricated_figure_at_validate` calls the text `validate` and is left for Task 19.

**The other files**, by what their failure means:

- `tests/test_cv_run_artefacts.py`, `tests/test_cv_backend_failure.py`, `tests/test_cv_triage_framing.py`, `tests/test_dossier_guard.py`, `tests/test_cv_terms.py` (its engine-level rows), `tests/test_app_operations.py`, `tests/test_app_injection.py`: they drive `run_one` or `compose_cv` with a CV text and a text-routing backend. Port the payload (a `_reply`-shaped JSON reply, or `tests/structured_cv.py`'s `reply()`) and the routing exactly as above; keep every assertion. Two premises change, each said in the ledger row: `cv.rendered.md` is now written for a dry run too (spec §6.0), and holds the document with citations rather than a draft's text (spec §7.4); the composer's prompt now carries triage framing inside the structured prompt, in the same `_TRIAGE_FRAMING_PROMPT_HEADER` block.
- `tests/test_cv_engine.py::_vault_with_candidate` writes the CV Layout note (`layout_yaml()` from `tests.conftest`, at `CV_LAYOUT_RELPATH`) in place of the baseline CV, and its docstring says why: `run_one` now refuses a vault with no layout (`skipped-config`) before any spend, which is what the baseline's missing-file raise used to block, so the rows here that must REACH the backend -- `test_a_fully_declared_identity_reaches_the_backend`, the both-declared case of `test_run_ones_skipped_config_status_and_doctors_candidate_profile_row_agree`, and `tests/test_onboard_questions.py`'s candidate-note probe, which borrows the helper -- need one. Ledger: changed, premise: the layout replaces the baseline as the note `run_one` reads first (spec §4.5).
- Every hand-written store double that `compose_cv`, `run_one` or `Sluice.doctor` reaches gains `def read_cv_layout(self): return SYNTHETIC_LAYOUT` (from `tests.conftest`), and its verified experience entries carry `"company": "Example Foundry"` -- the layout's one role, under the top-level key the real store also returns -- or `missing_prerequisites` refuses for want of a citable slot. `grep -rn "def read_evidence(" tests` lists them: `tests/test_app_injection.py` (two), `tests/test_app_operations.py` (two), `tests/test_doctor.py` (`_MinStore`, whose comment naming `read_baseline` as a member `compose_cv` checks before any spend names `read_cv_layout`; and `_StoreMissingCriteria`, so the strict-exit test's one degraded row stays the only one), `tests/test_mcpserver.py` (two), `tests/test_cv_engine.py` (the two beside `FakeVault`) and `tests/test_cv_run_artefacts.py` (one). A double is a ledger row only where a test's assertion changes.
- `tests/test_app_operations.py`: the two dry-run rows that exist because a dry run built a renderer for its `precheck` keep the property each one protects. Replace `_PrecheckStore`, `test_a_dry_run_applies_the_renderers_precheck_exactly_as_a_real_run_does`, `_precheck_cvcfg` and `test_a_dry_run_survives_a_renderer_it_cannot_construct` with the block below (ledger: both ported as the new ids -- premise changed: a dry run builds no renderer, spec §7.2):

```python
class _GateStore:
    """The minimum Store surface `compose_cv` and `run_one` touch before the gate: one
    shortlist note, the CV Layout, the experience entries the bundle is built from, and the
    candidate's identity."""

    def __init__(self, note):
        self._note = note

    def read_leads(self, statuses=None):
        return [self._note]

    def read_cv_layout(self):
        from tests.conftest import SYNTHETIC_LAYOUT
        return SYNTHETIC_LAYOUT

    def read_evidence(self, kind, verified_only=True):
        from tests.test_cv_engine import ENTRIES
        return ENTRIES if kind == "experience" else []

    def read_candidate_profile(self):
        from tests.test_cv_engine import DEFAULT_CANDIDATE
        return DEFAULT_CANDIDATE


def _gate_cvcfg(tmp_path):
    """CvConfig with the ENTRIES prefix_map the replies' EF1 cites need, and output/served
    dirs under tmp_path so nothing can reach a real one."""
    from sluice.cv.config import CvConfig
    c = CvConfig()
    c.output_dir = str(tmp_path / "cvout")
    c.served_dir = str(tmp_path / "cvserved")
    c.prefix_map = {"Example Foundry": "EF"}
    return c


def _gate_app(tmp_path, monkeypatch, reply_text, **kw):
    from tests.test_cv_engine import FakeBackend, FakeCache, Note
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    app = Sluice(Config(), store=_GateStore(note), **kw)
    monkeypatch.setattr(app, "backend", lambda *a, **k: FakeBackend(reply_text))
    monkeypatch.setattr(app, "dossier_cache", lambda *a, **k: FakeCache())
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: _gate_cvcfg(tmp_path))
    return app


def test_a_dry_run_reports_the_same_findings_as_a_real_run(tmp_path, monkeypatch):
    """A dry run must never report a CV clean that a real run refuses. It once resolved the
    renderer only so the renderer's grammar hook ran; that hook is gone, and every check
    now runs on the reply before the dry-run return, so the two runs agree -- pinned as
    EQUALITY on a reply the gate refuses, not against a literal."""
    from tests.test_cv_engine import FakeRenderer, _reply
    invented = _reply(bullets=("Cut costs by 80 percent",))      # 80 is in no entry
    app = _gate_app(tmp_path, monkeypatch, invented, renderer=FakeRenderer())
    dry, = app.compose_cv(lead="Acme", dry_run=True)
    real, = app.compose_cv(lead="Acme", dry_run=False)
    assert real.status == "skipped-gate", (
        "the real run stopped refusing this reply, so the two runs could agree while "
        "checking nothing -- the fixture, not the dry run, is what broke")
    assert any("INVENTED METRIC" in v for v in real.violations)
    assert (dry.status, dry.violations) == (real.status, real.violations)


def test_a_dry_run_never_builds_the_renderer(tmp_path, monkeypatch):
    """Spec §7.2: a dry run never renders, so a renderer that cannot even be constructed
    (an uninstalled WeasyPrint, a `cv.template` that is not a file) costs it nothing."""
    from sluice.core.protocols import RenderError
    from tests.test_cv_engine import CLEAN_REPLY
    app = _gate_app(tmp_path, monkeypatch, CLEAN_REPLY)
    built = []

    def _boom(_cvcfg):
        built.append(True)
        raise RenderError("renderer 'template': cv.template is not a file")
    monkeypatch.setattr(app, "renderer", _boom)
    result, = app.compose_cv(lead="Acme", dry_run=True)
    assert result.status == "dry-run"
    assert built == [], "the dry run constructed a renderer it can never use"
```

- `tests/test_doctor_verdict.py::test_a_baseline_the_user_named_and_that_is_gone_is_dead_and_exits_one` is deleted (ledger: `obsolete: baseline_rel is retired, spec §4.5; ported as test_a_cv_layout_the_user_broke_is_dead_and_exits_one`). Its property -- a CV input the user broke makes `doctor` exit 1, never "Nothing is broken" -- is carried to the CV Layout:

```python
def test_a_cv_layout_the_user_broke_is_dead_and_exits_one(monkeypatch, tmp_path):
    """Ported from the retired baseline_rel row: a CV Layout note that exists but does not
    parse means every `cv run` refuses before any spend, and `doctor` -- the command run to
    find out why -- must not answer "Nothing is broken". An ABSENT note stays the quiet
    unsupplied case."""
    from sluice.core.app import Sluice
    from sluice.core.protocols import CV_LAYOUT_RELPATH
    from sluice.core.vault import Vault

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(vault_dir))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(Sluice, "store", lambda self: Vault(str(vault_dir)))
    report = Sluice().doctor(offline=True)
    assert {c.subject: c for c in report.components}["cv_layout"].state == SETUP
    assert report.exit_code() == 0

    note = vault_dir / CV_LAYOUT_RELPATH
    note.parent.mkdir(parents=True)
    note.write_text("---\nroles: [\n---\n", encoding="utf-8")
    report = Sluice().doctor(offline=True)
    assert {c.subject: c for c in report.components}["cv_layout"].state == DEAD
    assert report.exit_code() == 1
```

- `README.md`'s offline `doctor` sample: its `baseline_rel` row becomes a `cv_layout` row carrying `classify_cv_layout`'s SETUP detail, IN THIS COMMIT -- `tests/test_readme_quickstart.py::test_every_quickstart_transcript_reproduces` replays that capture against the real command and is red from the moment the baseline row goes (measured). Regenerate the capture by running the command the transcript shows; never hand-edit it.
- `tests/test_doctor_verdict.py::test_the_verdict_and_the_table_can_never_disagree_about_a_row`: drop the `baseline_exists` and `baseline_rel_is_default` facts and the comment above them, since `classify_store` reads neither now. Its SETUP rows still come from the empty experience library and the undeclared candidate identity (measured: both remain SETUP with the baseline facts gone). Ledger: changed, property kept.
- `tests/test_doctor.py`: the tests of `classify_skills_request`, `classify_skills_reconciliation`, `classify_negatives_vs_skills` and the baseline rows are deleted — ledger `obsolete: <classifier> removed, spec §9.1`, and for the negatives cross-check `ported as tests/test_doctor_cv_layout.py::test_a_decoy_matching_the_users_own_data_is_warned_about_by_position` (spec §8 retires it for the decoy row). A test that merely builds a vault and finds the baseline row missing is ported to the CV Layout row.
- `tests/test_evidence_*.py`, `tests/test_sluice_neutral_defaults.py`, `tests/test_onboard_*.py`, `tests/test_mcpserver.py`: `Skills` as an experience field becomes `Tools` (a flag, a frontmatter line, an expected `fields` dict); the `experience list` rows that surfaced `Skills:` port to `Tools:`.
- `tests/test_fixture_name_neutrality.py`: when a deleted fixture was the only carrier of a rostered value, `test_the_skill_roster_has_no_stale_entries` or `test_the_reviewed_roster_carries_no_identity_the_fixtures_stopped_using` names it. Remove that roster entry, which is what its message asks for: a reviewed value with no fixture left pre-approves its next re-introduction.

- [ ] **Step 9: Run everything**

Run: `.venv/bin/ruff check sluice tests scripts && .venv/bin/python -m pytest -q`
Expected: PASS. Then run the two environment checks the memory records as having caught real breakage: `TZ=Asia/Dubai .venv/bin/python -m pytest -q` and `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q`. Both PASS.

Check the ledger covers this commit: every test deleted or changed in `git diff --stat HEAD -- tests/` has its row. Task 24 checks it mechanically; do not leave that to Task 24.

- [ ] **Step 10: Commit**

```bash
git add -A sluice tests docs/USAGE.md docs/TROUBLESHOOTING.md docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md
git commit -F - <<'EOF'
feat(cv)!: compose CVs as structured content assembled from the vault

cv run now asks the backend for a JSON reply -- the profile, cited
bullets for each role slot, and skill picks from a closed list -- and
builds the CV itself: the CV Layout note gives every heading, date,
location and title, and the Candidate Profile the name and contact.
The checks read only what the model wrote. Fixes #364, #365 and #368.

A vault needs Job Applications/CV Layout.md before cv run composes, and
experience entries name their tools in Tools:, since Skills: is no
longer read. The SKILLS section comes from verified Skills Inventory
names and entries' Tools:, and prose negatives no longer strip terms.
doctor reports the layout, unusable Tools: items, entries no role can
cite, and the attribution check when it is off.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 19: Remove the text CV pipeline

Spec §9.4 (removed code), §12.2 (guards carried, not retired). After Task 18 nothing in `cv run` reaches the text machinery; this task deletes it, and the tests whose only subject it was, and moves every GUARD that was built on it onto the structured checks — retargeted, never dropped. No behaviour changes, so the commit is a `refactor`.

**Files:**
- Delete: `sluice/cv/parse.py`, `tests/test_cv_parse.py`, `tests/test_cv_skills_containment.py`
- Modify: `sluice/cv/validate.py`, `sluice/cv/compose.py`, `sluice/cv/bundle.py`, `sluice/cv/terms.py`, `sluice/cv/engine.py`, `sluice/core/protocols.py`, `sluice/renderers/template.py`, `sluice/renderers/script.py`, `scripts/smoke_installed.py`
- Modify (tests): `tests/test_cv_validate.py`, `tests/test_cv_compose.py`, `tests/test_cv_bundle.py`, `tests/test_cv_mention_vocab.py`, `tests/test_cv_checks.py`, `tests/test_renderer_template.py`, `tests/test_renderers.py`, `tests/test_smoke_installed.py`, `tests/test_prompt_neutrality.py`, `tests/test_docs_claims.py`, `tests/template_content.py`, `tests/test_fixture_name_neutrality.py`, `tests/test_cv_terms.py`, `tests/test_cv_triage_framing.py`, `tests/test_usage_wiring.py`, `tests/test_onboard_questions.py`, `tests/test_no_leaked_files.py`, `tests/test_backends.py`, `tests/test_evidence_store.py`, `tests/test_cv_engine.py` (the one row Task 18 left here)
- Modify: `docs/TROUBLESHOOTING.md` (the categories the gate can no longer emit; the `precheck` section)
- Modify: the ledger

**Interfaces:**
- Consumes: Tasks 3–18.
- Produces: `build_bundle(entries, negatives, jd_keywords, prefix_map, skills=()) -> dict` (no `baseline` parameter, no `"baseline"` key); `Renderer.render(document, out_dir, *, neutral_name)` with no `str` branch; `cv/validate.py` holding only `EntryFacts`, `entry_facts`, `check_selection` and their helpers.

- [ ] **Step 1: Delete the machinery**

- `sluice/cv/parse.py`: delete the module. `CvDocument`/`Role` already live in `core/protocols.py` (Task 4).
- `sluice/cv/validate.py`: delete the text `validate`, `section_spans`, `skills_lines`, the marker tuples (`_BULLET_MARKERS`, `_SKILLS_MARKERS` and any sibling) and every helper only they use, leaving `EntryFacts`, `entry_facts`, `_spans`, `_licenses`, `_belongs`, `check_selection` and the imports those need. Keep `_CITE_RE` importable from it: `cv/terms.py` imports it, and `tests/test_cv_validate.py::test_profile_strip_matches_render_citation_shape` pins it to `cv/render.py`'s. Rewrite the module docstring to describe the checks over a selection (spec §6.1), and keep `check_selection`'s note on the single accumulator.
- `sluice/cv/compose.py`: delete `build_prompt`, `compose`, `_RULES`, `_unwrap_agent_envelope` and its helpers, `_REQUIRED_HEADERS`, `_SKILLS_ATTRIBUTION_PROMPT_RULE`, `_SKILLS_FORMAT_PROMPT_RULE`, `_SKILLS_PROMPT_BLOCK`, `_employer_line` and anything only they use. Keep `framing_lines`, `_TRIAGE_FRAMING_PROMPT_RULE`, `_TRIAGE_FRAMING_PROMPT_HEADER`, `_TRIAGE_FRAMING_PROMPT_LABELS`, `_banned_phrases_sentence` and the structured prompt (Task 12). The module docstring describes the structured prompt.
- `sluice/cv/bundle.py`: delete `render_bundle`, `render_composer_bundle`, `mention_vocab`, `bundle_sources` and the `BundleSources`/`EntrySources` types with it, `_skill_items`, `_entry_skills_line`, `_source_section`, `_DERIVED_NEGATIVE_PROMPT` and the `SKILL_TOKEN_RE` re-export, plus anything only they use. `build_bundle` loses its `baseline` parameter and its result loses the `"baseline"` key, and its `_skill_items` validation loop goes (`Tools:` items are validated before any spend by `missing_prerequisites`, and `entry_facts` raises on one regardless). Rewrite `_entry_block`'s docstring: it now feeds the composer's entries section, the auditor's, and `entry_facts`' figures, and the RATCHET paragraph names `tests/test_cv_bundle.py`'s retargeted guards. In `cv/engine.py`, `_bundle.build_bundle(entries, "", cvcfg.negatives, …)` drops the `""`.
- `sluice/cv/terms.py`: its docstrings and its two `TypeError` messages name `cv.bundle.term_vocabulary(bundle, layout)` in place of `mention_vocab`.
- `sluice/renderers/template.py`: delete `precheck` and the `isinstance(document, str)` branch with its `parse_cv` import; `sluice/renderers/script.py`: delete its `str` branch. `sluice/core/protocols.py`'s `Renderer` docstring no longer mentions `precheck`; the `Store` docstring that cited `precheck` as the precedent for an optional seam member cites `Store.preflight`.
- `sluice/cv/engine.py`: no comment may still describe a deleted mechanism — grep it for `parse`, `section_spans`, `precheck`, `STRUCTURAL` and `baseline`.
- `scripts/smoke_installed.py`: `_RENDER_PROBE_CV` becomes a document, built lazily so the script's own import stays sluice-free:

```python
def _render_probe_document():
    """The document `check_real_render` renders: every section the size floor relies on is
    populated (tests/test_smoke_installed.py pins that offline)."""
    from sluice.core.protocols import CvDocument, Role
    return CvDocument(
        name="EXAMPLE PERSON", contact="Email: someone@example.invalid",
        profile="Engineer with nine years building data pipelines.",
        work=[Role(company="Example Data", dates="03/2021–present",
                   location="Example Location A", title="SYNTHETIC-TITLE-1",
                   bullets=["Cut p99 latency to under 200ms"])],
        skills=[], certificates=[], education=["Example University, BSc Example"])
```

  and `check_real_render` calls `renderer.render(_render_probe_document(), out_dir)`.

- [ ] **Step 2: Retarget the guards built on the deleted machinery**

Spec §12.2 names each; they move onto `entry_facts`/`check_selection`/`term_vocabulary` with their sentinels and their discriminating assertions intact. Each is a ledger row: `ported as <its new id>`.

Every `build_bundle(...)` call in `tests/` drops its baseline argument (`grep -rn "build_bundle(" tests` finds them; `tests/test_cv_checks.py` and `tests/test_cv_structured_bundle.py` have some). In `tests/test_cv_bundle.py` ( `_frozen_bundle` drops `baseline=FROZEN_BASELINE`; `FROZEN_BASELINE` stays only as the frozen literal's provenance comment, or goes if nothing reads it), with `from sluice.core.protocols import CvLayout, LayoutRole` and `from sluice.cv.validate import entry_facts`:

```python
# Placement does not change an entry's figures, so any layout will do for the pool guards.
_ANY_LAYOUT = CvLayout(roles=(LayoutRole("Example Alpha 31", "01/2020", "present"),))


def _figures(bundle):
    return {eid: f.figures for eid, f in entry_facts(bundle, _ANY_LAYOUT).items()}


def test_the_allowlist_still_matches_the_frozen_prompt():
    """The co-variant detector, retargeted (spec §12.2): `_entry_block` feeds both the
    composer's text and each entry's figures, so a field dropped from it would leave any
    render-versus-figures comparison agreeing. The frozen literal, captured before the
    change, is what makes the loss visible. Its baseline block is no longer anyone's pool:
    the baseline is not read (spec D2)."""
    nums, _baseline = _oracle(FROZEN_BUNDLE_TEXT)
    assert _figures(_frozen_bundle()) == nums


def test_entry_facts_sentinels_hold_independent_of_the_frozen_literal():
    """Compares against NO literal, so re-freezing cannot bring it back into sync."""
    f = _figures(_frozen_bundle())
    assert {"31", "32", "33", "34", "37", "38"} <= f["AL1"]     # company, title, metrics, body
    assert not ({"35", "36"} & f["AL1"])                      # best_for, category: never emitted
    assert {"41", "43", "47"} <= f["BE1"] and not ({"45", "46"} & f["BE1"])
    assert f["AL2"] == frozenset({"51", "53"})
    every = set().union(*f.values())
    assert not ({"71", "72"} & every)                         # the Skills Inventory's figures
    assert not ({"91", "92"} & every)                         # the negatives'
    assert not ({"21", "22"} & every)                         # the baseline's: read by nothing


def test_a_skills_digit_is_licensed_in_neither_pool():
    """THE load-bearing #165 row, against no literal: the skill's own figures (8, 62) may
    license nothing -- not an entry's figures, and (tests/test_cv_checks.py) not the
    profile's."""
    every = set().union(*_figures(_bundle_with_skills()).values())
    assert not ({"8", "62"} & every), (
        "a skills digit reached an entry's figures -- the framing lines have been folded "
        "into _entry_block, which licenses them for that entry")


def test_the_derived_constraint_reaches_no_number_pool():
    """#31: the guidance -- the negatives and the derived constraint -- is shown to the model
    and is deliberately not a source."""
    b = B.build_bundle(FROZEN_ENTRIES, ["never claim 91 users"], [], FROZEN_PREFIX_MAP,
                       skills=[_SKILL])
    assert not ({"91"} & set().union(*_figures(b).values()))
    text = B.render_structured_bundle(b)
    assert text.index(B._GUIDANCE_HEADER_PROMPT) < text.index(B._TOOLS_SOURCE_PROMPT)


def test_the_derived_constraint_names_the_same_claim_sources_as_the_prompt_rule():
    """Both strings land in the SAME prompt, so a source one names and the other does not is a
    contradiction the composer can only resolve by guessing. Reads the REAL rule text."""
    from sluice.cv.compose import _STRUCTURED_RULES_PROMPT
    assert "VERIFIED EXPERIENCE ENTRIES" in B._TOOLS_SOURCE_PROMPT
    assert "SKILLS INVENTORY" not in B._TOOLS_SOURCE_PROMPT
    for text in (B._TOOLS_SOURCE_PROMPT, _STRUCTURED_RULES_PROMPT):
        assert "BASELINE" not in text       # spec D2: the baseline is not read when composing
    assert ("The VERIFIED EXPERIENCE ENTRIES are the ONLY permitted source for the profile "
            "and the bullets") in _STRUCTURED_RULES_PROMPT


def test_the_derived_constraint_names_no_skill_and_so_cannot_go_stale():
    assert "Example Cloud" not in B._TOOLS_SOURCE_PROMPT
    assert "platform" not in B._TOOLS_SOURCE_PROMPT


def test_layout_dates_and_headings_reach_no_figure_pool():
    # Spec §12.2's new exclusion: the layout is the user's, and its digits license nothing.
    layout = CvLayout(roles=(LayoutRole("Example Northgate 2020", "01/2019", "06/2021",
                                        employers=("Example Alpha",)),))
    b = B.build_bundle([{"title": "Ran it", "company": "Example Alpha", "metrics": "",
                         "body": "Ran it."}], [], [], {})
    assert [f.figures for f in entry_facts(b, layout).values()] == [frozenset()]


@pytest.mark.parametrize("value", ["Example 9001", "Example 2.0", "9E modelling",
                                   "5X", "123.45ab"])
def test_a_digit_leading_skill_token_stays_refused_whatever_it_names(value):
    """The STATED over-refusal, carried onto `Tools:` (spec §4.2, §13): a word-then-number
    name is structurally the metric shorthand `Result 92`, so it is refused, and said so."""
    from sluice.core.tokens import tool_items
    with pytest.raises(ValueError, match="must begin with a letter"):
        tool_items({"fields": dict(Tools=value)})
```

(Its docstring keeps the paragraph about the synthetic values and `docs/USAGE.md`.) The other `test_cv_bundle.py` rows whose subject is a deleted function (`render_bundle`, `render_composer_bundle`, `bundle_sources`' baseline pool, `_entry_skills_line`, `_skill_items`, the composer bundle's text format, `_DERIVED_NEGATIVE_PROMPT`'s wording) are deleted — ledger `obsolete: <function> removed, spec §9.4`, adding `ported as tests/test_cv_structured_bundle.py::<row>` where Task 13 pins the surviving property (the inventory as framing, the guidance section, `tools=` lines).

In `tests/test_cv_checks.py`, add (with `from sluice.cv import bundle as B`, `from sluice.cv.reply import Bullet` already present):

```python
def _facts_from(entries, negatives=()):
    b = B.build_bundle(entries, list(negatives), [], {"Example Alpha": "EA"})
    return entry_facts(b, CvLayout(roles=(ALPHA,)))


_SCALED = [{"title": "Scaled the platform", "company": "Example Alpha", "metrics": "40",
            "body": "Scaled to 40 nodes."}]


def test_negatives_block_does_not_widen_the_last_entrys_allowlist():
    facts = _facts_from(_SCALED, negatives=["never claim 500 users"])
    assert _check(r1=[_b("Scaled the platform to 500 users", "EA1")], facts=facts) == [
        "INVENTED METRIC ['500'] not in ['EA1']: Scaled the platform to 500 users"]


def test_profile_number_from_negatives_is_flagged():
    facts = _facts_from(_SCALED, negatives=["never claim 500 users"])
    assert _check(profile="I scaled to 500 users.", facts=facts) == [
        "INVENTED PROFILE METRIC 500 not in your evidence: I scaled to 500 users."]


def test_at_zero_entries_the_negatives_no_longer_reach_the_profile_pool():
    facts = _facts_from([], negatives=["never claim 500 users"])
    assert facts == {}
    selection = Selection("I scaled to 500 users.", {"R1": (), "R2": ()}, ())
    assert check_selection(selection, SLOTS, facts) == [
        "INVENTED PROFILE METRIC 500 not in your evidence: I scaled to 500 users."]


def test_the_vocabulary_cannot_widen_the_hard_gate():
    """SEPARATION (spec §8): the term vocabulary is a STYLE-only pool and carries an
    inventory-only name; the hard gate must still refuse that name's digit, asserted on the
    discriminating message, so a gate that started reading the vocabulary goes red here."""
    layout = CvLayout(roles=(ALPHA,))
    b = B.build_bundle([{"title": "Ran things", "company": "Example Alpha", "metrics": "",
                         "body": "Ran things."}], [], [], {"Example Alpha": "EA"},
                       skills=[{"title": "Examplelang9", "fields": {}, "body": ""}])
    assert "examplelang9" in B.term_vocabulary(b, layout), "premise: the pool carries it"
    selection = Selection("I build.", {"R1": (_b("Ran Examplelang9 for the team", "EA1"),)},
                          ())
    assert check_selection(selection, (Slot("R1", ALPHA, ("EA1",), None),),
                           entry_facts(b, layout)) == [
        "INVENTED METRIC ['9'] not in ['EA1']: Ran Examplelang9 for the team"]
```

(`Selection`, `Slot`, `check_selection`, `entry_facts` and `CvLayout` are already imported there.) `tests/test_cv_validate.py`'s `test_a_profile_non_id_bracketed_number_is_flagged` → ported as `tests/test_cv_reply.py::test_a_bracket_in_the_profile_is_a_finding` (the REPLY refusal now catches it before any figure check, spec §5.2), with its bullet-side twin `tests/test_cv_checks.py::test_a_bracketed_figure_is_still_scanned_when_it_reaches_the_check`. Every other `test_cv_validate.py` row tests the text `validate` and is deleted, ledger `obsolete: the line-based validate is removed, spec §9.4`, adding `ported as tests/test_cv_checks.py::<row>` wherever a `check_selection` row pins the same property (citations, figures in other scripts, decoys, the profile pool). `test_profile_strip_matches_render_citation_shape` stays. Delete the file if nothing is left in it.

`tests/test_cv_mention_vocab.py`: rows about `mention_vocab`'s contents port to `B.term_vocabulary(bundle, layout)` with the same assertions (entries, tools, inventory framing, a non-ASCII name tokenising identically on both sides, and `test_a_negative_is_not_left_behind_as_a_source_of_its_own` — a negative's own words are not in the vocabulary, now because the vocabulary never reads negatives at all); rows about the BASELINE in the vocabulary are obsolete (spec §8: the baseline leaves it). Ledger accordingly. Its module docstring cites `cv/bundle.py::term_vocabulary`: `tests/test_citation_drift.py` fails a `::symbol` citation whose symbol no longer exists. `tests/test_usage_wiring.py` is the other test file citing a symbol this task deletes: drop its `("cv/compose.py", "compose")` row from `_CALL_SITES` (measured: declared but no longer found), and re-point its docstring's `cv/compose.py::compose` at `::compose_structured` (ledger: changed). The `sluice/` citations are in `sluice/cv/terms.py`, rewritten in Step 1, and in code Tasks 18 and 19 delete.

`tests/test_evidence_store.py::test_cited_by_gate_is_exactly_what_bundle_sources_actually_licenses` keeps its oracle — execution, not a source grep — and asks the mechanism that now licenses figures: build the same two-kind bundle (without the baseline argument), take `licensed = set().union(*(f.figures for f in entry_facts(b, CvLayout(roles=(LayoutRole("Example Co", "01/2020", "present"),))).values()))`, and keep both assertions unchanged. Rename it `test_cited_by_gate_is_exactly_what_the_gate_actually_licenses` (ledger: ported as the new name). Its sibling for the skills pool is Task 9's `tests/test_cv_selection.py::test_the_pool_holds_exactly_the_names_of_kinds_flagged_for_it` (spec §4.4).

`tests/test_cv_engine.py::test_the_engine_no_longer_imports_the_template_grammar` is deleted (ledger: `obsolete: cv/parse.py is removed, spec §9.4` -- with the module gone the import guard checks nothing, and its message still points at `precheck`).

`tests/test_cv_parse.py` and `tests/test_cv_skills_containment.py`: deleted, one ledger row per test — `obsolete: the text parser is removed, spec §9.4` and `obsolete: UNSOURCED SKILL and MISATTRIBUTED SKILL are gone, spec §6.5`, the latter `ported as tests/test_cv_checks.py::test_a_tool_another_entry_declares_is_misattributed` where the row was about attribution.

`tests/test_cv_compose.py`: rows about the deleted functions are deleted (ledger: `obsolete: …, spec §9.4`, with `ported as tests/test_cv_structured_prompt.py::<row>` for the triage framing, the banned phrases and `slop_allow`); `framing_lines`' rows stay. `test_cv_prompt_expresses_no_role_or_culture_preference` reads `_RULES` and goes with it: ledger `ported as tests/test_cv_structured_prompt.py::test_no_structured_prompt_constant_names_a_job_or_culture_preference` (Task 12), which reads every rule constant the structured prompt renders.

`tests/test_renderer_template.py`: the `precheck` rows and the str-path rows are deleted (ledger `obsolete: precheck removed, spec §7.2`); every row that rendered TEXT renders a document instead (`parse_cv(text)` in a fixture becomes the equivalent `CvDocument` literal). `tests/test_renderers.py`: the str-path row goes. `tests/test_smoke_installed.py`: `fake.cv_text == smoke._RENDER_PROBE_CV` becomes `fake.document == smoke._render_probe_document()` (rename the fake's attribute), and `test_the_render_probe_is_a_cv_the_parser_accepts` becomes:

```python
def test_the_render_probe_populates_every_section_the_size_floor_relies_on():
    """A probe with an empty section would render the near-blank page the size floor
    refuses, failing every channel for the SCRIPT's fault (spec §12.2)."""
    doc = smoke._render_probe_document()
    assert doc.name and doc.contact and doc.profile and doc.education
    assert [(r.company, r.title) for r in doc.work] == [("Example Data", "SYNTHETIC-TITLE-1")]
    assert doc.work[0].bullets, "the work entry has no bullet"
```

`tests/test_prompt_neutrality.py`: remove `"sluice.cv.compose.build_prompt"` and `"sluice.cv.bundle._DERIVED_NEGATIVE_PROMPT"` (and any other deleted constant the discovery no longer finds) from `_KNOWN_PROMPTS`, delete `_SYNTHETIC_ARGS["sluice.cv.compose.build_prompt"]`, and port each row that rendered `build_prompt` (the triage-framing coverage rows) to render `build_structured_prompt` with `_SYNTHETIC_ARGS`' structured entry. Never add an `_EXEMPT` entry for a `sluice.cv` prompt (spec §12.2). `tests/test_prompt.py` sweeps the triage prompt and is unaffected.

`tests/template_content.py::composer_headings()` re-derives from the one heading tuple (spec §12.2):

```python
def composer_headings() -> set[str]:
    """The section headings a template may legitimately print: DERIVED from
    core/protocols.py::SECTION_HEADINGS, the one tuple cv/document.py::to_text writes
    (#364/#365/#368) -- never from a rendered prompt, whose all-caps lines include the
    substituted candidate name and would admit that literal into the allowlist for the
    template no-content guards and the shipped-file leak sweep.

    Callers must assert it is non-empty: `set() <= anything` is True, so a derivation that
    silently stopped matching would make every comparison below pass."""
    from sluice.core.protocols import SECTION_HEADINGS
    return set(SECTION_HEADINGS)
```

`tests/test_fixture_name_neutrality.py`: `_skills_run`'s docstring re-anchors on `cv/document.py::to_text` and `SECTION_HEADINGS` (its run still ends at the other trailing headings, which `to_text` writes); comments elsewhere in the file that cite `section_spans`, `cv/parse.py` or `bundle_sources` cite what replaced them. Remove a roster entry only when the staleness row names it and no fixture carries it any more.

`tests/test_cv_terms.py`: drop the module-level `from sluice.cv.validate import section_spans`, and swap `mention_vocab` for `term_vocabulary` in the `sluice.cv.bundle` import. `test_a_wrongly_shaped_vocabulary_raises_naming_the_type` matches `term_vocabulary`, the name the `TypeError` gives after Step 1. The anti-vacuity row reads the reply the way the engine does (ledger: both ported as the same ids):

```python
def test_the_check_scans_real_lines_finds_candidates_and_reports_nothing_bundled():
    """Roster: tests/test_cv_engine.py::CLEAN_REPLY with its own ENTRIES bundle, read as the
    engine reads a reply (cv/document.py::model_lines). All three clauses are needed: (c)
    alone passes on a sweep that scanned nothing (a) or whose rule admits nothing (b)."""
    import json

    from sluice.core.layout import build_slots
    from sluice.cv.document import model_lines
    from sluice.cv.reply import Bullet
    from sluice.cv.selection import Selection
    from tests.conftest import SYNTHETIC_LAYOUT
    from tests.test_cv_engine import CLEAN_REPLY, ENTRIES
    data = json.loads(CLEAN_REPLY)
    selection = Selection(profile=data["profile"], skills=(), roles={
        slot: tuple(Bullet(b["text"], tuple(b["cites"])) for b in bullets)
        for slot, bullets in data["roles"].items()})
    bundle = build_bundle(ENTRIES, [], [], {"Example Foundry": "EF"})
    lines = model_lines(selection, build_slots(SYNTHETIC_LAYOUT, bundle["entries"]))
    assert lines, "(a) model_lines yielded no lines"
    assert any(candidates(text) for _ln, text in lines), "(b) the rule admits nothing"
    vocab = term_vocabulary(bundle, SYNTHETIC_LAYOUT)
    assert unbundled_terms(lines, vocab) == [], "(c) the fixture's own bundle must cover it"
```

`tests/test_cv_triage_framing.py`: four rows call `C.build_prompt`/`C.compose` directly and go red here (measured). Port each property onto the structured prompt (ledger: ported as the new ids): `test_the_unframed_prompt_matches_the_pre_329_shape_at_both_splice_points` → the unframed `build_structured_prompt` carries neither `_TRIAGE_FRAMING_PROMPT_RULE` nor `_TRIAGE_FRAMING_PROMPT_HEADER`; `test_framing_adds_exactly_its_rule_and_its_section` → framing adds exactly the rule and the section, nothing else (diff the framed and unframed renders); `test_the_section_sits_after_the_jd_and_outside_the_source_bundle` → `rendered.index("THE JD") < rendered.index(_TRIAGE_FRAMING_PROMPT_HEADER)`, and the header is not inside the bundle text's span; `test_compose_forwards_the_framing_into_the_prompt_it_sends` → `compose_structured(..., triage_framing=...)` puts the header in the one prompt the backend received.

`tests/test_onboard_questions.py`: `test_the_employers_hint_describes_the_check_that_actually_runs` imports the text `validate`, so it goes HERE rather than with the `cv_employers` question in Task 20 -- ledger `obsolete: MISSING EMPLOYER left with the text gate, spec §6.5`. `test_the_candidate_note_prose_describes_the_check_that_actually_runs` calls it "the employers-hint probe above" in its docstring and in its closing comment; both lose that reference and keep their own reasoning.

`tests/test_no_leaked_files.py`: the comment above `test_docs_template_examples_contribute_no_static_content` and its docstring say the heading vocabulary derives from `cv/compose.py`'s `_RULES`; both say `core/protocols.py::SECTION_HEADINGS`, which `tests/template_content.py::composer_headings()` now reads (above). The guard's code does not change.

`tests/test_backends.py`: the #28 comment's "six arms through the real build_prompt/render_bundle/validate" reads "six arms through the text pipeline of the time".

- [ ] **Step 3: The docs the guards pin**

`tests/test_docs_claims.py`: the gate's categories are now exactly what `check_selection` appends, so `test_the_gate_category_sweep_is_not_vacuous` moves its scope pin deliberately — the categories spec §6.5 deletes are gone:

```python
    cats = _validate_categories()
    assert len(cats) >= 7, f"the violation-category sweep found only {sorted(cats)}"
    for anchor in ("INVENTED METRIC", "UNCITED BULLET", "WRONG EMPLOYER",
                   "MISATTRIBUTED TOOL"):
        assert anchor in cats, f"{anchor} vanished from the sweep: {sorted(cats)}"
```

(ledger: ported as the same id, floor and anchors moved to the surviving categories, spec §6.5). In `docs/TROUBLESHOOTING.md`'s `skipped-gate` section, delete the `UNSOURCED SKILL` and `MISATTRIBUTED SKILL` bullets; the `FABRICATED` / `MISSING EMPLOYER` / `NOT REVERSE-CHRONOLOGICAL` bullet becomes:

```markdown
- `FABRICATED` — a term you listed in `cv.fabrication_decoys`, found as a whole term in the
  profile or a bullet the model wrote.
```

and the `INVENTED PROFILE METRIC` bullet's "(the baseline plus every entry)" becomes "(every verified entry)". Delete the "A gate-clean CV is still refused (a renderer `precheck` violation)" section: `precheck` is gone.

- [ ] **Step 4: Run everything**

Run: `.venv/bin/ruff check sluice tests scripts && .venv/bin/python -m pytest -q`
Expected: PASS. Confirm nothing still imports the deleted names:

Run: `grep -rnE "cv\.parse|parse_cv|section_spans|bundle_sources|render_composer_bundle|mention_vocab|_DERIVED_NEGATIVE_PROMPT|precheck|read_baseline\(" sluice scripts tests | grep -v "^tests/.*ledger"`
Expected: no IMPORT or CALL of a deleted name. What may still match, and why: `read_baseline` (Task 20 removes it); three unrelated prechecks (`sluice/core/vault.py`'s `reconcile_names` layers, `sluice/ingest/engine.py`'s HEAD precheck, `tests/test_leads_rename.py`); and prose in `sluice/` that cites the renderer's `precheck` as a precedent (`core/protocols.py`, `core/doctor.py`, `core/app.py`, `ingest/base.py`, `triage/engine.py`, `cli.py`), which Task 22 Step 4 rewrites. Then check the ledger has a row for every test this commit deleted or changed.

- [ ] **Step 5: Commit**

```bash
git add -A sluice scripts tests docs/TROUBLESHOOTING.md docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md
git commit -F - <<'EOF'
refactor(cv): remove the text CV pipeline

Nothing in cv run reaches it since the switch: the CV parser, the
line-based validate, the text prompt and envelope unwrap, the text bundle
renderers and the renderer precheck go, with the tests whose only subject
they were. The guards built on them move onto the structured checks.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 20: Config, setup and the store follow the new model

Spec §9.3 (D11 retired keys raise; decoys validated at load; `init` drops `cv_employers`; `sluice.yaml.example`), §9.4 (`Store.read_baseline` removed), §12.2 (neutral defaults; store contract; onboarding). After Task 19 nothing reads the baseline CV or `cv.employers`; this task makes leaving either in a config a loud error rather than a silent no-op, removes the baseline from the Store seam, and validates decoys where they are loaded.

**Files:**
- Modify: `sluice/core/config.py` (`refuse_retired_cv_inputs`; `Config.baseline_rel` and its `load_config` line removed)
- Modify: `sluice/cv/config.py` (`CvConfig.employers` removed; the `cv.baseline_rel` refusal re-pointed and no longer echoing its value; decoys validated)
- Modify: `sluice/core/protocols.py` (`Store.read_baseline` removed; the `write_document` and preflight docstrings stop calling the baseline the gate's ground truth), `sluice/core/vault.py` (`read_baseline`, the `baseline_rel` parameter and attribute, the preflight baseline facts), `sluice/stores/vault.py` (`_make` passes no `baseline_rel`)
- Modify: `sluice/core/app.py` (doctor's comment and docstring listing what `load_cv_config` can raise)
- Modify: `sluice/onboard/questions.py` (the `cv_employers` question removed)
- Modify: `sluice.yaml.example`, `docs/CONFIGURATION.md` (only the two retired keys' entries: `tests/test_docs_claims.py` pins them; Task 22 rewrites the rest)
- Modify (tests): `tests/test_sluice_neutral_defaults.py`, `tests/conformance/test_store_contract.py`, `tests/conformance/seeds.py`, `tests/test_onboard_plan.py`, `tests/test_onboard_questions.py`, `tests/test_docs_claims.py`, `tests/harness/config.py`, `tests/test_cv_config.py`, `tests/test_core_vault_cv.py`, `tests/test_plugins.py`, `tests/functional/test_init.py`, `tests/test_onboard_emit.py`, `tests/test_config_paths.py`, `tests/test_mcpserver.py`, and the store doubles that still define `read_baseline` (`tests/test_app_injection.py`, `tests/test_app_operations.py`, `tests/test_doctor.py`, `tests/test_cv_engine.py`)
- Create: `tests/test_config_retired_cv_keys.py`

**Interfaces:**
- Produces: `sluice/core/config.py::refuse_retired_cv_inputs(data: dict) -> None`, called by `load_config` beside `refuse_retired_locations`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_retired_cv_keys.py`:

```python
"""D11 (#364/#365/#368): the retired `baseline_rel` and `cv.employers` keys RAISE, naming
the CV Layout, from `load_config` -- so every command stops on them, not only `cv`. Neither
value is echoed: an error travels further than the config file it came from."""
import pytest

from sluice.core.config import load_config


@pytest.mark.parametrize("text,key", [
    ("baseline_rel: My CV/Example CV.md\n", "baseline_rel"),
    ("cv:\n  employers: [Example Alpha]\n", "cv.employers"),
], ids=["root-baseline_rel", "cv.employers"])
def test_a_retired_key_stops_every_command_naming_the_cv_layout(tmp_path, text, key):
    path = tmp_path / "sluice.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_config(str(path))
    message = str(exc.value)
    assert key in message and "CV Layout" in message
    assert "Example" not in message, "a retired key's value is never echoed"


def test_a_clean_config_loads(tmp_path):
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  fabrication_decoys: [examplelang]\n", encoding="utf-8")
    load_config(str(path))


def test_the_refusal_stops_a_command_that_has_nothing_to_do_with_cvs(tmp_path, monkeypatch,
                                                                    capsys):
    from sluice.cli import main
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  employers: [Example Alpha]\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(path))
    assert main(["ingest", "list-sources"]) != 0
    assert "cv.employers" in capsys.readouterr().err


def test_cv_baseline_rel_names_the_cv_layout_and_echoes_nothing(tmp_path):
    from sluice.cv.config import load_cv_config
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  baseline_rel: My CV/Example CV.md\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_cv_config(str(path))
    assert "CV Layout" in str(exc.value) and "Example CV" not in str(exc.value)


def test_an_unmatchable_decoy_is_refused_at_load_by_its_position(tmp_path):
    from sluice.cv.config import load_cv_config
    path = tmp_path / "sluice.yaml"
    path.write_text("cv:\n  fabrication_decoys: [examplelang, Exämple]\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_cv_config(str(path))
    assert "entry 2" in str(exc.value) and "Exämple" not in str(exc.value)
```

In `tests/test_sluice_neutral_defaults.py` — the only sanctioned deletions are the assertions on the two removed fields (`c.employers == []`, and `c.baseline_rel`'s two lines with their comment); every comment elsewhere in the file that cites the `baseline_rel` assertion re-points at the sweep below. Add:

```python
def test_the_cv_layouts_optional_fields_abstain():
    """Spec §12.2: every optional layout field defaults to the abstain shape -- no cap, no
    location or title, empty lists -- and a role without `employers:` scopes by its heading."""
    from sluice.core.layout import parse_layout, place
    layout = parse_layout({"roles": [{"heading": "Example Alpha", "from": "01/2020",
                                      "to": "present"}]})
    [role] = layout.roles
    # employers is DERIVED as the heading (Task 4), which is what scopes the role.
    assert (role.location, role.title, role.employers, role.bullets_max) == (
        "", "", ("Example Alpha",), None)
    assert (layout.skills_max, layout.certificates, layout.education, layout.any_role,
            layout.omitted) == (None, (), (), (), ())
    assert place(layout, "Example Alpha").roles == frozenset({0})


def test_an_absent_layout_reads_as_none_with_no_built_in_fallback(tmp_path):
    from sluice.core.vault import Vault
    assert Vault(str(tmp_path / "vault")).read_cv_layout() is None


def test_no_str_default_on_a_swept_config_is_an_absolute_or_home_path():
    """Replaces the deleted `baseline_rel` relativity assertion (spec §12.2): a path default
    that is absolute or `~`-rooted is someone's machine. DERIVED over every swept config's
    str fields, with the scope pinned, and tied to the sandbox guard: every `./` default
    found here is one tests/conftest.py's guard derives and watches."""
    import dataclasses
    from tests.conftest import _relative_path_defaults

    checked = [(cls.__name__, f.name, f.default) for cls in _SWEPT_CONFIGS
               for f in dataclasses.fields(cls) if isinstance(f.default, str)]
    assert len(checked) >= 10, checked
    bad = [c for c in checked if c[2].startswith(("/", "~"))]
    assert not bad, f"a config ships a machine's path as its default: {bad}"
    relative = {d for _c, _f, d in checked if d.startswith("./")}
    assert relative, "no ./ default found: the link to the sandbox guard would hold vacuously"
    assert relative <= _relative_path_defaults()
```

In `tests/conformance/test_store_contract.py`: `test_read_baseline_takes_no_path_argument_and_reads_the_baseline` is deleted (ledger `obsolete: Store.read_baseline is removed, spec §9.4`), and the three `write_document` rows that read back through `read_baseline` write to `CRITERIA_RELPATH` and read back through `read_criteria`, keeping every assertion (ledger: ported as the same ids). `tests/conformance/seeds.py` drops its `baseline` seeder.

In `tests/test_onboard_plan.py` (spec §9.3: retargeted, not deleted): the nested-list leg's `"cv_employers": ['O\'Example: "the #1"']` becomes `"reject_companies": ['O\'Example: "the #1"']` asserted through `load_triage_config(path).reject_companies`; the control-character leg's `"cv_employers": ["a\x0bb"]` becomes `"relevance_keep": ["a\x0bb"]` asserted through `load_config(ctrl_path).relevance_keep`; the leg comments credit those keys. `tests/test_onboard_questions.py::test_every_value_bearing_question_states_its_consequence` keeps its history in the past tense: `cv_employers` WAS the exception, and its hint described the opposite of the completeness check `cv/validate.py` then ran (its probe went in Task 19).

The rest of the tree that reads what this task removes:
- `tests/test_core_vault_cv.py::test_read_baseline` is deleted (ledger `obsolete: Store.read_baseline is removed, spec §9.4`), and `_vault_with` loses its `baseline` parameter when nothing else passes one.
- `tests/test_plugins.py::test_store_factory_honours_a_non_default_baseline_rel` is deleted (ledger `obsolete: Config.baseline_rel is removed, spec §9.4; the CV Layout's path is a fixed CV_LAYOUT_RELPATH, so no factory wiring replaces it`).
- `tests/functional/test_init.py`: the two init-transcript tests anchor on the `cv_employers` prompt as "the catalogue's OWN first question". The anchor becomes DERIVED, so the next removal cannot strand it: `first = next(q.prompt for q in catalogue(default_vault=str(vault)) if q.key != "vault_dir")` (with `from sluice.onboard.questions import catalogue`), asserted `in shown`; both comments say what is asked first is whatever the catalogue lists after `vault_dir` (ledger: ported, same ids).
- `tests/test_onboard_emit.py`: the corpus comment's "e.g. cv_employers" names a `cv:` key that still exists (`cv.negatives`).
- `tests/test_config_paths.py::test_retired_sub_app_dossier_dir_raises`: its second comment contrasts this message with baseline_rel's, which echoed its value; the re-pointed `cv.baseline_rel` refusal no longer does, so the comment says that neither message echoes a value, this one because a host path usually sits under a home directory.
- `tests/test_mcpserver.py`: `"read_baseline"` leaves `_STORE_READ_METHODS` and the comment above it; `test_store_read_methods_are_exactly_the_protocols_reads` (Task 6) fails until it does.
- Each store double's `read_baseline` method goes, with any comment that names it as a member the CV path needs.

In `tests/test_docs_claims.py::_RETIRED_CONFIG`, the `cv.baseline_rel` row's reason becomes `"cv.baseline_rel -- retired: no baseline CV is read; raises naming the CV Layout"`, and add:

```python
    (re.compile(r"(?<![\w.])baseline_rel:"),
     "a root `baseline_rel:` key -- retired: no baseline CV is read; raises naming the CV Layout",
     "baseline_rel: My CV/CV.md"),
    (re.compile(r"\bcv\.employers\b"),
     "cv.employers -- retired: the CV Layout's roles say which employers a CV shows; raises",
     "Set cv.employers: [Example Alpha] in your config."),
```

If the file's nested-YAML half of that sweep enumerates nested keys separately, add `employers` under `cv:` there too, in its existing shape.

`tests/harness/config.py`: `_seed_vault` and `build_harness` stop writing the baseline (`My CV/CV.md` and the `baseline` parameter go).

Run: `.venv/bin/python -m pytest tests/test_config_retired_cv_keys.py tests/test_sluice_neutral_defaults.py tests/test_docs_claims.py -q`
Expected: FAIL (the keys load silently; the docs still name them).

- [ ] **Step 2: Implement**

`sluice/core/config.py`, beside `refuse_retired_locations`:

```python
def refuse_retired_cv_inputs(data: dict) -> None:
    """Raise if a config still sets either input #364/#365/#368 retired (D11).

    The root `baseline_rel` named the baseline CV, which nothing reads now: a CV's structure
    comes from the CV Layout note. `cv.employers` was the completeness roster MISSING
    EMPLOYER checked, and the CV Layout's roles now say which employers a CV shows. Both
    RAISE rather than being dropped by the loaders' hasattr filter, because a user who set
    either would otherwise watch it stop meaning anything with no word said -- and here in
    `load_config`, which every command runs, so the first command after an upgrade says so.

    Neither VALUE is echoed: a CV path or an employer list is personal, and an exception
    travels further than the config file it came from."""
    if "baseline_rel" in data:
        raise ValueError(
            "the root `baseline_rel` key is retired: sluice no longer reads a baseline CV. "
            "A CV's roles, dates and headings now come from the CV Layout note "
            "(Job Applications/CV Layout.md, shape in docs/CONFIGURATION.md) -- delete the "
            "key.")
    cv = data.get("cv")
    if isinstance(cv, dict) and "employers" in cv:
        raise ValueError(
            "cv.employers is retired: the CV Layout note (Job Applications/CV Layout.md) "
            "now says which employers a CV shows, as its roles -- delete the key, and give "
            "each role in the layout the employers its entries name.")
```

and call `refuse_retired_cv_inputs(data)` directly after `refuse_retired_locations(data)` in `load_config`. Delete `Config.baseline_rel`, its comment block, its `load_config` line, and the `_MYCV_BASELINE` constant if nothing else reads it.

`sluice/cv/config.py`: delete `CvConfig.employers` and its comment; the `cv.baseline_rel` refusal becomes

```python
    if "baseline_rel" in data:
        # Re-pointed (#364/#365/#368): its old advice -- move the key to the top level --
        # would now send a user to a key that also raises. The value is not echoed.
        raise ValueError(
            "cv.baseline_rel is retired: sluice no longer reads a baseline CV. A CV's roles, "
            "dates and headings come from the CV Layout note (Job Applications/CV Layout.md) "
            "-- delete the key.")
```

and, after the loading loop, before `return cfg`:

```python
    # Spec §8: a decoy the shared tokeniser cannot represent could never match, so it is
    # refused here, by POSITION, rather than left silently inert.
    validate_decoys(cfg.fabrication_decoys)
```

with `from sluice.core.tokens import validate_decoys`. Update the container-check comment that lists `employers` among the gate's lists.

The store: delete `Store.read_baseline` from `sluice/core/protocols.py` and `Vault.read_baseline`, `Vault.__init__`'s `baseline_rel` parameter and `self.baseline_rel` from `sluice/core/vault.py`; `Vault.preflight` stops reporting `baseline_exists` and `baseline_rel_is_default` (and its docstring stops listing them); `stores/vault.py::_make` stops passing `baseline_rel` (its docstring too). The `write_document` docstrings in both files, and the preflight contract in `protocols.py`, stop calling the baseline "the fabrication gate's ground truth": the gate's truth is the verified evidence entries.

`sluice/onboard/questions.py`: delete the `cv_employers` Question and the comment above it, and rewrite the `#107` comment that says the catalogue "keeps only cv_employers below" to say the catalogue writes no `cv:` key at all.

`sluice/core/app.py::Sluice.doctor`: its docstring's and comment's example of what `load_cv_config()` raises becomes "a retired key (`cv.baseline_rel`), a decoy the gate could never match, or a list given as a scalar".

`sluice.yaml.example`: delete the root `baseline_rel` and `cv.employers` entries, and where `cv.employers` was, add:

```yaml
  # Which employers a CV shows, in what order, under which headings and dates, is the CV
  # Layout note in your vault: Job Applications/CV Layout.md (shape in docs/CONFIGURATION.md).
```

`docs/CONFIGURATION.md`: delete the root `baseline_rel` and `cv.employers` entries (the guard above forbids a doc naming either); Task 22 adds the CV Layout section that replaces them.

- [ ] **Step 3: Run everything**

Run: `.venv/bin/ruff check sluice tests scripts && .venv/bin/python -m pytest -q`
Expected: PASS. Then `grep -rn "baseline_rel\|read_baseline\|cv_employers\|cv\.employers" sluice scripts sluice.yaml.example tests` — expected: only the refusals' own messages and comments, their tests (`tests/test_config_retired_cv_keys.py`, `tests/test_cv_config.py`'s re-pointed refusal row), `tests/test_docs_claims.py::_RETIRED_CONFIG`, the hand-built `ComponentCheck("store", "baseline_rel", …)` rows in `tests/test_doctor_verdict.py` (an arbitrary subject string there, exercising verdict mechanics), and sentences stating a removal.

- [ ] **Step 4: Commit**

```bash
git add -A sluice tests sluice.yaml.example docs/CONFIGURATION.md docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md
git commit -F - <<'EOF'
feat(config)!: retire baseline_rel and cv.employers loudly

Both keys now stop every command with a message naming the CV Layout,
rather than being silently ignored, and neither value is echoed. A
fabrication decoy the gate could never match is refused at load, by its
position. The store no longer reads a baseline CV, and init no longer
asks for employers.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 21: The neutrality guards reach the new fixture positions

Spec §12.2 (Fixture-name neutrality). A CV Layout is a person's employment history, and a reply or a Skills Inventory note names their skills: a real one copied into a fixture must not ship green. Every new position is routed onto a roster that ALREADY exists — never a new roster — so the existing in-use union, staleness rows and review messages keep working unchanged: layout headings, employers, `any_role`/`omitted` and a document's `company` onto the employer roster through `_all_fixture_identities()`; reply `skills` lists and Skills Inventory names onto the skill roster through `_all_fixture_skill_values()`. Three positions no roster holds get CONVENTION rows: locations, certificates/education, titles. (`Tools:` joined the skill sweep in Task 18, with the field.)

Two deviations from spec §12.2, each measured: `location=` and `title=` are swept in CV-layout and document positions only, not as keywords anywhere — anywhere would reach 15 existing lead and ingest fixtures whose locations are faker towns and synthetic tokens, outside this change. And the AST collector cannot see a value passed through a helper's own parameter (`_role(heading)`); the `_CV_TEST_MODULES` sweep, which reads every `Example …` name in a module's whole text, is what covers those modules.

**Files:**
- Modify: `tests/test_fixture_name_neutrality.py`

**Interfaces:**
- Consumes: the file's own `_evidence_block_list_re`, `_block_list_items`, `_string_const_bindings`, `_test_sources`, `_identity_of`, `_is_source_text`, `_REVIEWED_FIXTURE_IDENTITIES`, `_REVIEWED_SKILL_VALUES`, `_CV_IDENTITY_RE`, `_CV_IDENTITY_EXEMPT`, `_unreviewed_identities`; `tests/conftest.py::LOCATIONS`, `_title_pool`; `sluice.core.protocols.EVIDENCE_KINDS`.
- Produces: `_LAYOUT_HEADING_RE` (a member of `_IDENTITY_COLLECTORS`), `_layout_list_items(text)`, `_whole_strings(node, consts)`, `_ast_cv_values(text) -> dict[str, set]`, `_cv_position_values() -> dict[str, set]`.

- [ ] **Step 1: The collectors**

Add to `tests/test_fixture_name_neutrality.py`, before `_IDENTITY_COLLECTORS` (it is built at import, so its members must be defined above it):

```python
# --- #364/#365/#368: the structured CV's identity-bearing positions --------------------

# A layout role's `heading`, in the dict-key spelling (`"heading": "X"`) and the YAML one
# (`heading: X` at a line start or after a list dash, real or packed `\n`). Anchored, never a
# bare `heading:` -- measured, that matched a docstring's prose and an error message
# (`roles[0].heading: equals a CV section heading`). ONE capture group, as `_collect` needs.
_LAYOUT_HEADING_RE = re.compile(
    r'''(?:["']heading["']\s*:\s*["']|(?:^|\\n)[ \t]*(?:-[ \t]+)?heading:[ \t]*)'''
    r'''([^\s"'`{\\][^"'`{\\\n]*?)(?=[ \t]*(?:["'\\]|$))''', re.M)
```

and add `("layout heading: (dict key or YAML)", _LAYOUT_HEADING_RE)` as the last member of `_IDENTITY_COLLECTORS`. Then, after `_all_fixture_identities`:

```python
_LAYOUT_LIST_KEYS = ("employers", "any_role", "omitted")
_FLOW_ITEM_RE = re.compile(r'''"([^"\\\n]*)"|'([^'\\\n]*)'|([^,"'\s\\][^,"'\\]*?)\s*(?=,|$)''')


def _layout_flow_list_re(key):
    """`key: [a, b]` -- a YAML flow list -- and `"key": ["a", "b"]`, the same text shape.
    Captures the bracket's contents, which `_layout_list_items` splits quote-aware: a comma
    inside a quoted item (`"Example, Inc"`) is part of the name."""
    return re.compile(rf'''["']?{key}["']?\s*:[ \t]*\[([^\]\n]*)\]''')


def _layout_list_items(text):
    """Every employer-shaped item of a layout list (employers, any_role, omitted) in one
    module's text: YAML flow, YAML block and dict-key spellings."""
    items = []
    for key in _LAYOUT_LIST_KEYS:
        for run in _layout_flow_list_re(key).findall(text):
            for m in _FLOW_ITEM_RE.finditer(run):
                item = next((g for g in m.groups() if g), "").strip()
                if item:
                    items.append(item)
        items += _block_list_items(_evidence_block_list_re(key), text)
    return items


def _whole_strings(node, consts=None) -> set:
    """`_skill_strings` WITHOUT its comma split: an employer (`Example, Inc`) or an
    education line (`Example University, BSc`) is one value, never a list."""
    nodes = node.elts if isinstance(node, (ast.List, ast.Tuple, ast.Set)) else [node]
    out = set()
    for n in nodes:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
        elif consts is not None and isinstance(n, ast.Name) and n.id in consts:
            out |= consts[n.id]
    return {v for v in out if v}


# LayoutRole / Role parameters by POSITION, so a positional construction is read too.
_CV_CALLS = {"LayoutRole": ("heading", "start", "end", "location", "title", "employers",
                            "bullets_max"),
             "Role": ("company", "dates", "location", "title", "bullets")}
_CV_CALL_CATEGORY = {"heading": "identity", "company": "identity", "employers": "identity",
                     "location": "location", "title": "title"}
# Keywords read wherever they appear: CvLayout, CvDocument, layout_yaml(**top), helpers.
_CV_ANY_KWARG = {"heading": "identity", "employers": "identity", "any_role": "identity",
                 "omitted": "identity", "certificates": "credential",
                 "education": "credential"}


def _skills_only_fields():
    """The frontmatter keys only a Skills Inventory note carries -- DERIVED from the kinds,
    so a field added to that kind is recognised with no edit here, and a key another kind
    shares can never make a non-skill entry read as one."""
    from sluice.core.protocols import EVIDENCE_KINDS
    others = set().union(*(k.fields for n, k in EVIDENCE_KINDS.items() if n != "skills"))
    return frozenset(EVIDENCE_KINDS["skills"].fields) - others


def _ast_cv_values(text):
    """{category: values} for the structured-CV positions an AST can reach in one module.

    Reached: LayoutRole and Role calls (positional and keyword); the heading, employers,
    any_role, omitted, certificates and education keywords wherever they appear; a dict
    shaped like a layout role (a "heading" key) or a layout (a "roles" key); a dict shaped
    like a reply (a "skills" key beside "profile" or "roles"); and a Skills Inventory entry
    (its "fields" carry a key only that kind declares), whose "title" and "Label" are skill
    names. NOT reached, stated so nobody reads coverage into it: a value passed through a
    helper's own parameter (`_role(heading)`), and any computed value."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    consts = _string_const_bindings(tree)
    skill_fields = _skills_only_fields()
    out = {}

    def add(category, node):
        out.setdefault(category, set()).update(_whole_strings(node, consts))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            params = _CV_CALLS.get(name, ())
            for i, arg in enumerate(node.args):
                if i < len(params) and params[i] in _CV_CALL_CATEGORY:
                    add(_CV_CALL_CATEGORY[params[i]], arg)
            for kw in node.keywords:
                if params and kw.arg in _CV_CALL_CATEGORY:
                    add(_CV_CALL_CATEGORY[kw.arg], kw.value)
                elif kw.arg in _CV_ANY_KWARG:
                    add(_CV_ANY_KWARG[kw.arg], kw.value)
        elif isinstance(node, ast.Dict):
            keys = {k.value: v for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "heading" in keys:
                for key in ("heading", "employers", "location", "title"):
                    if key in keys:
                        add(_CV_CALL_CATEGORY[key], keys[key])
            if "roles" in keys or "heading" in keys:
                for key in ("any_role", "omitted", "certificates", "education"):
                    if key in keys:
                        add(_CV_ANY_KWARG[key], keys[key])
            if "company" in keys and "bullets" in keys:      # a document role as a dict
                add("identity", keys["company"])
            if "skills" in keys and ("profile" in keys or "roles" in keys):
                add("skill", keys["skills"])
            inner = keys.get("fields")
            if isinstance(inner, ast.Dict):
                fkeys = {k.value: v for k, v in zip(inner.keys, inner.values)
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if set(fkeys) & skill_fields:
                    if "title" in keys:
                        add("skill", keys["title"])
                    if "Label" in fkeys:
                        add("skill", fkeys["Label"])
    return out


_CV_MODULE_KWARGS = {"location": "location", "title": "title"}


def _cv_module_kwarg_values(text):
    """`location=` and `title=` on ANY call, read only in the CV test modules: there a helper
    such as tests/test_core_layout.py's `_role(location=...)` is how a layout is built, and a
    helper's keyword is invisible to `_ast_cv_values`. Outside them the same keywords name
    lead and ingest fixtures, which keep their own conventions."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    consts = _string_const_bindings(tree)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _CV_MODULE_KWARGS:
                    out.setdefault(_CV_MODULE_KWARGS[kw.arg], set()).update(
                        _whole_strings(kw.value, consts))
    return out


def _cv_position_values():
    """{category: values} across every test module, plus the helper keywords of the CV
    test modules (`_cv_module_kwarg_values`)."""
    merged = {}
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if path.name == _SELF:
            continue
        text = path.read_text(encoding="utf-8")
        parts = [_ast_cv_values(text)]
        if path.name in _CV_TEST_MODULES:
            parts.append(_cv_module_kwarg_values(text))
        for part in parts:
            for category, values in part.items():
                merged.setdefault(category, set()).update(values)
    return merged
```

Route them onto the existing rosters:

- `_all_fixture_identities()` gains, after its block-list line:

```python
    # #364/#365/#368: layout lists and the AST positions (headings, employers, any_role,
    # omitted, a document's company), normalised like every other identity.
    for text in _test_sources():
        for raw in _layout_list_items(text) + sorted(_ast_cv_values(text).get("identity", ())):
            name = _identity_of(raw)
            if not _is_source_text(name):
                names.add(name)
```

- `_all_fixture_skill_values()` gains `values |= {v.strip() for v in _ast_cv_values(text).get("skill", ()) if v.strip()}` inside its `for text in _test_sources():` loop -- stripped and blank-free like the comma-split values above it, since a blank `Label:` fixture is a test of the title fallback, not a skill -- and its docstring says reply picks and Skills Inventory names are skill values too — ONE union, so the roster gate and its staleness row read the same set.

- [ ] **Step 2: The rows**

Add:

```python
def test_the_layout_heading_collector_sees_every_shape_it_claims_to():
    seen = {
        "dict key": ('{"heading": "Example Alpha", "from": "01/2020"}', "Example Alpha"),
        "yaml, list dash": ("roles:\\n  - heading: Example Beta\\n    from: x", "Example Beta"),
        "yaml, indented": ("  heading: Example Meridian\\n", "Example Meridian"),
    }
    for shape, (text, expected) in seen.items():
        assert [_identity_of(v) for v in _LAYOUT_HEADING_RE.findall(text)] == [expected], shape
    for prose in ("roles[0].heading: equals a CV section heading", 'f"heading: {h}"'):
        assert _LAYOUT_HEADING_RE.findall(prose) == [], prose


def test_the_layout_list_collector_sees_every_shape_it_claims_to():
    assert _layout_list_items('"employers": ["Example, Inc", "Example Beta"]') == [
        "Example, Inc", "Example Beta"]
    assert _layout_list_items("employers: [Example, Inc]") == ["Example", "Inc"]
    assert _layout_list_items("omitted:\\n  - Example Tidal\\nfrom: x") == ["Example Tidal"]


def test_the_cv_ast_collector_sees_every_shape_it_claims_to():
    source = (
        'LayoutRole("Example Pos", "01/2020", "present", "Example Location A",\n'
        '           "SYNTHETIC-T1", ("Example Emp",))\n'
        'LayoutRole(heading="Example Kw", start="x", end="y", location="Example Location B",\n'
        '           title="SYNTHETIC-T2", employers=("Example Emp2",))\n'
        'Role("Example Co2", "d", "Example Location C", "SYNTHETIC-T3", [])\n'
        'CvLayout(roles=(), certificates=("Example Cert2",), any_role=("Example Any",))\n'
        'x = {"heading": "Example Dict", "employers": ["Example Emp3"], "title": "SYNTHETIC-T4"}\n'
        'y = {"profile": "p", "roles": {}, "skills": ["Example Pick"]}\n'
        'z = {"title": "Example Note", "fields": {"Domain": "d", "Label": "Example Label"}}\n'
        'layout_yaml([{"heading": "Example Yaml"}], omitted=["Example Omit"],\n'
        '            education=["Example University, BSc Example"])\n')
    found = _ast_cv_values(source)
    assert found["identity"] == {"Example Pos", "Example Emp", "Example Kw", "Example Emp2",
                                 "Example Co2", "Example Any", "Example Dict", "Example Emp3",
                                 "Example Yaml", "Example Omit"}
    assert found["location"] == {"Example Location A", "Example Location B",
                                 "Example Location C"}
    assert found["title"] == {"SYNTHETIC-T1", "SYNTHETIC-T2", "SYNTHETIC-T3", "SYNTHETIC-T4"}
    assert found["credential"] == {"Example Cert2", "Example University, BSc Example"}
    assert found["skill"] == {"Example Pick", "Example Note", "Example Label"}


@pytest.mark.parametrize("category", ["identity", "location", "title", "credential", "skill"])
def test_the_cv_ast_collector_finds_fixtures_in_every_category(category):
    # A floor per category: for a negative guard finding nothing is the success case, so the
    # sweep must prove it looked.
    assert _cv_position_values().get(category), category


def test_the_cv_module_keyword_sweep_reads_a_helpers_location_and_title():
    assert _cv_module_kwarg_values(
        '_role(location="Example Location Q", title="SYNTHETIC-TQ")\n') == {
        "location": {"Example Location Q"}, "title": {"SYNTHETIC-TQ"}}


def test_the_cv_ast_collector_reads_a_document_roles_company_as_a_dict():
    assert _ast_cv_values('{"company": "Example Dictco", "dates": "x", "bullets": []}\n')[
        "identity"] == {"Example Dictco"}


def test_layout_and_document_locations_follow_the_location_convention():
    from tests.conftest import LOCATIONS
    bad = sorted(v for v in _cv_position_values().get("location", ())
                 if not (v in LOCATIONS or re.fullmatch(r"Example Location( [A-Z0-9]+)?", v)
                         or v.startswith("<")))
    assert not bad, f"location value(s) {bad}: use LOCATIONS or `Example Location <X>`"


def test_layout_and_document_titles_are_synthetic_or_from_the_seeded_pool():
    from tests.conftest import _title_pool
    pool = set(_title_pool())
    bad = sorted(v for v in _cv_position_values().get("title", ())
                 if v and not (v in pool or v.startswith(("SYNTHETIC", "<"))))
    assert not bad, (f"title value(s) {bad}: use a SYNTHETIC-... token or a title from the "
                     "seeded faker pool (tests/conftest.py::titles), never a hardcoded job title")


def test_certificates_and_education_are_synthetic():
    # A PREFIX, never a substring: "<a real institution>, BSc Example" contains the word and
    # names a real place. What a prefix cannot catch -- a real name after "Example " -- is
    # the same residual every `Example ...` convention in this file carries.
    bad = sorted(v for v in _cv_position_values().get("credential", ())
                 if not v.startswith(("Example", "SYNTHETIC", "<")))
    assert not bad, f"certificate/education value(s) {bad}: start the value with `Example ...`"


def _script_sources():
    root = _TESTS_DIR.parent / "scripts"
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.py"))]


def test_scripts_carry_no_unreviewed_cv_identity_and_follow_the_conventions():
    """scripts/ ships to no user but is public, and scripts/smoke_installed.py carries a CV
    document literal (spec §12.2): the same rosters and conventions hold there."""
    from tests.conftest import LOCATIONS
    found = {}
    for _name, text in _script_sources():
        for category, values in _ast_cv_values(text).items():
            found.setdefault(category, set()).update(values)
    assert found.get("identity"), "the scripts sweep found no CV document at all"
    names = {n for v in found["identity"] for n in _CV_IDENTITY_RE.findall(v)}
    assert _unreviewed_identities(names, _REVIEWED_FIXTURE_IDENTITIES | _CV_IDENTITY_EXEMPT,
                                  ()) == []
    assert all(v.startswith(("Example", "SYNTHETIC")) for v in found["identity"])
    assert all(v in LOCATIONS or v.startswith("Example Location")
               for v in found.get("location", ()))
    assert all(v.startswith("SYNTHETIC") for v in found.get("title", ()) if v)
```

Bump the pins deliberately: `test_the_collector_split_this_file_documents_is_the_split_it_has` now asserts eight collectors and six on the employer roster, and the docstring of `test_every_collector_actually_finds_fixtures` describes eight, naming the layout heading as the sixth roster-feeding one. Add every new module carrying CV identities to `_CV_MODULES_NOT_MATCHING_THE_CONVENTION`: `test_core_layout.py`, `test_core_layout_slots.py`, `test_core_layout_store.py`, `test_core_tokens.py`, `structured_cv.py`, `test_doctor_cv_layout.py`, `test_skills_pool_wording.py` (the `test_cv_*.py` ones are already globbed). In `test_the_cv_module_set_is_derived_and_not_hand_listed`, raise the glob floor to the number of `test_cv_*.py` files on the branch, and delete the docstring's arithmetic ("13 glob matches + 3 exceptions = 16"): a count in prose is the repo's most-repeated finding.

- [ ] **Step 3: Run it, and record the reviews it asks for**

Run: `.venv/bin/python -m pytest tests/test_fixture_name_neutrality.py -q`
Expected: the shape rows and floors PASS; the roster rows FAIL naming the values the new positions reach that nobody has reviewed yet. Measured against the plan's fixtures before writing this task, they are `Example, Inc` and `Inc` (the layout comma-splitting rows: an employer named with a comma, and the half a flow list splits off), `Example Alpha — Example Northgate` (Task 16's em-dash heading, two roster names joined), and the Skills Inventory names `Example Cloud Skill`, `Example Data Skill`, `alpha` and `s` (existing fixtures, newly reached). For EACH value named: confirm it is synthetic and add it to the roster the message names, with a comment naming the test and saying whether this plan introduced the value or it is a pre-existing fixture newly reached, in the roster's existing style. A value you cannot confirm is synthetic is a STOP: escalate it, never roster it on a guess. Never widen a pattern to make a row pass. Re-run until PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_fixture_name_neutrality.py
git commit -F - <<'EOF'
test: sweep the structured CV's identity positions for real names

Layout headings and employers and a document's company feed the employer
roster; reply skill picks and Skills Inventory names feed the skill
roster; locations, titles, certificates and education are held to the
synthetic conventions; scripts/ is swept too. Each collector has its own
shape row and floor.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 22: The docs, and the shipped templates' comments

Spec §14 (docs and prose), §12.2 (the docs layout-sample sweep), §7.2 (an untitled role renders cleanly). Tasks 11, 15, 18, 19 and 20 already changed the passages a test pins to the code; this task writes the rest. Docs are hand-written: there is no generator.

**Files:**
- Create: `tests/test_docs_layout_samples.py`
- Modify: `docs/CONFIGURATION.md`, `docs/USAGE.md`, `docs/ARCHITECTURE.md`, `docs/TROUBLESHOOTING.md`, `docs/AI-SETUP.md`, `docs/GUARANTEES.md`, `README.md`
- Modify: `sluice/templates/cv_plain.html.j2`, `docs/cv-template-example.html.j2` (comments only)
- Test: `tests/test_renderer_template.py`

- [ ] **Step 1: The docs-sample sweep (failing first)**

Create `tests/test_docs_layout_samples.py`:

```python
"""Spec §12.2: every CV Layout sample in the shipped docs is a TEMPLATE, not a layout. A
verbatim copy must refuse at every placeholder rather than render one, and the identities a
sample shows are reviewed or `Example`-shaped, since a layout is an employment history."""
import pathlib
import re

import pytest
import yaml

from sluice.core.layout import parse_layout
from sluice.core.protocols import LayoutError

_DOCS = [pathlib.Path("README.md"), *sorted(pathlib.Path("docs").glob("*.md"))]
_FENCE = re.compile(r"^```ya?ml\n(.*?)^```", re.M | re.S)
_PLACEHOLDER = re.compile(r"<[^<>]+>")


def _samples():
    """(doc name, mapping) for every fenced YAML block with a top-level `roles:` key. The
    note's own `---` frontmatter fences are dropped first: kept, PyYAML reads two documents."""
    out = []
    for path in _DOCS:
        for block in _FENCE.findall(path.read_text(encoding="utf-8")):
            body = "\n".join(ln for ln in block.splitlines() if ln.strip() != "---")
            try:
                data = yaml.safe_load(body)
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and "roles" in data:
                out.append((path.name, data))
    return out


def _placeholder_paths(value, path=""):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _placeholder_paths(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            yield from _placeholder_paths(inner, f"{path}[{i}]")
    elif isinstance(value, str) and _PLACEHOLDER.search(value):
        yield path


def test_the_docs_show_a_layout_sample():
    # The must-find floor: a sweep that found no sample would pass every row below.
    assert _samples(), "no CV Layout sample in README.md or docs/*.md"


def test_every_layout_sample_refuses_at_every_placeholder():
    for name, data in _samples():
        paths = list(_placeholder_paths(data))
        assert paths, f"{name}: a sample with no placeholder would load as a real layout"
        with pytest.raises(LayoutError) as exc:
            parse_layout(data)
        for p in paths:
            assert any(problem.startswith(f"{p}:") for problem in exc.value.problems), (
                f"{name}: the placeholder at {p} is not refused by name")


def test_layout_samples_show_only_reviewed_or_example_shaped_identities():
    from tests.test_fixture_name_neutrality import _REVIEWED_FIXTURE_IDENTITIES
    for name, data in _samples():
        for role in data.get("roles") or []:
            for value in [role.get("heading"), *(role.get("employers") or [])]:
                if value is None or _PLACEHOLDER.search(str(value)):
                    continue
                assert (str(value).startswith("Example")
                        or value in _REVIEWED_FIXTURE_IDENTITIES), (name, value)


def test_a_samples_education_items_are_one_qualification_each():
    # Spec §12.2: one item per qualification, in the shape the sample shows -- dates then
    # ONE qualification after the meta separator -- so a copy is written a line per item.
    for name, data in _samples():
        for item in data.get("education") or []:
            assert str(item).count("|") == 1, (name, item)
```

Run: `.venv/bin/python -m pytest tests/test_docs_layout_samples.py -q`
Expected: FAIL in `test_the_docs_show_a_layout_sample`.

- [ ] **Step 2: `docs/CONFIGURATION.md`**

Add a section after "Candidate Profile (vault note)":

````markdown
## CV Layout (vault note)

`Job Applications/CV Layout.md` decides what every CV shows, in what order: each role's
heading, dates, location and title, which of your experience entries may be cited under it,
and your certificates and education. `cv run` refuses until it exists, and `doctor` says so.
Every CV is assembled from it: the model never writes a heading, a date, your name or a
certificate.

The note is YAML frontmatter. Every value below is a placeholder to replace; a copy that
keeps one is refused, naming where it is:

```yaml
---
# skills_max: <n>                     # optional; absent = no cap, 0 = no SKILLS section
roles:                                # CV order, top to bottom; required, at least one
  - heading: "Example Alpha"          # required: the employer, as the CV prints it
    from: "<MM/YYYY>"                 # required
    to: "<MM/YYYY or present>"        # required
    location: "<location>"            # optional
    title: "<title>"                  # optional
    # bullets_max: <n>                # optional; absent = no cap, 0 = the heading only
  - heading: "Example Northgate"      # a roll-up: one heading over several employers
    from: "<MM/YYYY>"
    to: "<MM/YYYY>"
    employers:                        # optional; default: the heading itself
      - "Example Beta"
      - "Example Meridian"
any_role:                             # optional: companies whose entries fit any role
  - "<company>"
omitted:                              # optional: companies left off the CV on purpose
  - "<company>"
certificates:                         # optional
  - "<certificate>"
education:                            # optional; one item per qualification
  - "<institution, dates | qualification>"
---
```

**One item per line.** In YAML's inline style (`[a, b]`) an unquoted comma SPLITS an item, so
`employers: [Example, Inc]` is two employers. Write each item on its own `- ` line, as above,
or quote it.

**Which entries a role may cite.** An experience entry is matched to a role when its
`Company:` -- or one of its parts split on `,` `;` `/` -- equals the role's heading or one of
its `employers`, ignoring case and accents. An entry whose company is under `any_role:` may be
cited under any role; one under `omitted:`, under none. An entry with no `Company:`, or one
matching nothing, is cited nowhere: `doctor` counts them ("not on your CV", "no company") and
`job-sluice experience list` shows which, by company.

**Budgets.** `bullets_max` caps one role's bullets: the first N the model wrote are kept, the
rest reported as trimmed, never refused. `skills_max` caps the SKILLS section. Absent means no
cap; `0` means none. A role with no entry it may cite is shown heading-only.

**Rules the note is checked against:** `from`/`to` are `MM/YYYY` (`to` may be `present`, in
any case), and `from` is not after `to`; `heading`, `location` and `title` hold no `|` and are
not a CV section heading; no value holds a line break or control character; an unknown key
inside a role is an error, and so is a near miss of a top-level key (`skill_max`); a company
both omitted and listed elsewhere is an error. Other top-level keys are yours (`tags:`,
`aliases:`) and are ignored. Every problem is reported at once, each with its place.
````

In the evidence-notes part of the doc (add a subsection under "CV Layout" if none exists):

```markdown
### `Tools:` on experience entries

List the tools, technologies, methods and standards an entry used, comma-separated or as a
block list. Once any verified entry declares `Tools:`, a CV bullet naming one must cite an
entry that lists it, or whose own text names it in the same case (the misattributed-tool
check), and every listed tool can appear in your SKILLS section. Every word of a tool's name
must begin with a letter, so a digit-led standard such as `ISO 9001` is refused: a name shaped
like a figure would let an invented figure hide inside it. `Skills:` is no longer read; move
its values here.

### `Label:` on Skills Inventory notes

A verified skill note's name can appear in a CV's SKILLS section: its `Label:` if set, else
its title. `skills add --name` sets the label to the name exactly as typed, which survives the
filename's slug.
```

In the `cv:` section: `fabrication_decoys` becomes "terms the composer must never claim. A CV whose profile or bullets name one, as a whole term in any case, is refused. A decoy the matcher cannot represent -- a non-Latin or accented word, or one with punctuation inside -- is refused when the config loads, by its position in the list. Write a hyphenated compound with a space (`co founder` matches `co-founder`)."; `negatives` becomes "your guidance to the composer, in your own words: shown to it, read by no check. A real "never claim X" belongs in `fabrication_decoys`."; delete `employers` (done in Task 20) and add a sentence: "`baseline_rel` and `cv.employers` are retired and stop every command until removed: the CV Layout replaces both."

- [ ] **Step 3: The other docs**

- `README.md`: the prerequisites table's baseline row becomes `| A CV Layout note at Job Applications/CV Layout.md | cv run | refused before any fetch or backend call |`; (the offline `doctor` sample was already updated in Task 18); the commands table's `skills` row reads "capture and verify skills: framing for the composer, and a verified note's name can appear in a CV's SKILLS section (`add`, `list`, `verify`)". The CV paragraph of "How it works" says the CV is assembled from the vault and the model writes only the profile, the bullets and its skill picks.
- `docs/USAGE.md`: `experience add` lists `--tools` (not `--skills`), `skills add` lists `--label`; the `cv run` intro describes the structured flow in two sentences.
- `docs/ARCHITECTURE.md`: grep it for the claim words spec §14 lists (`precheck`, `read_baseline`, `baseline_rel`, `baseline CV`, `ground truth`, `parse_cv`, `parse.py`, `cv/parse`, `section_spans`, `cv_employers`, `cv.employers`, `classify_negatives_vs_skills`, `FORMAT:`, `Skills:`) and rewrite each passage to the new design: the cv sub-app's flow (spec §6.0), the Store seam (`read_cv_layout`, no `read_baseline`), the Renderer seam (a `CvDocument`, no `precheck`), the citability prose keyed on `cited_by_gate` and `names_in_skills_pool`, and the fold paragraph gaining employer matching as a third consumer kind (spec §9.5: a wider fold loosens `WRONG EMPLOYER`, so the fold must not widen for it). A grep for removed words cannot find what is MISSING, so add explicitly: one entry per new module under its package's section (`core/tokens.py`, `core/layout.py`, `cv/reply.py`, `cv/selection.py`, `cv/document.py`); that `core/layout.py` imports `core/vault.py`'s fold lazily, inside the function, to break the cycle; that `SECTION_HEADINGS`, `CvDocument`/`Role`, `CvLayout`/`LayoutRole`, `LayoutError` and `verify_outcome` now live in `core/protocols.py`; and, in the config paragraph, the one exception to "each loader reads its own block": `load_config` also refuses the retired `cv.*` inputs, so that every command stops on them, not only `cv` commands.
- `docs/TROUBLESHOOTING.md`: the `skipped-config` section names both causes (the Candidate Profile, or a CV Layout note that disappeared after the run began) and quotes neither message in full. Add:

```markdown
## `doctor` counts entries "not on your CV" or with "no company"

A verified experience entry is cited only under a role its `Company:` matches (see the CV
Layout section of `docs/CONFIGURATION.md`). Run `job-sluice experience list` to see each
entry's company, then either add that company to a role's `employers`, list it under
`any_role:` or `omitted:`, or give the entry the `Company:` it happened at.

## `doctor` says the cv attribution check is off

No verified experience entry declares `Tools:`, while some still carry the retired `Skills:`.
sluice no longer reads `Skills:`. Move each entry's tools into `Tools:`; once any entry
declares one, a bullet naming a tool must cite an entry that lists it.
```

- `docs/AI-SETUP.md`: step 4 becomes:

```markdown
### 4. CV Layout: interview, then write

Ask which roles their CV shows, newest first: each employer as the CV should print it, the
dates, and optionally the location and title; any roll-up of earlier roles under one heading,
and which employers it covers; companies whose work fits any role; companies to leave off;
certificates; education. Then create `Job Applications/CV Layout.md` in the shape
`docs/CONFIGURATION.md` shows -- ONLY if it does not exist. If it does, show them a diff of
what you would change and edit only what they approve; never rewrite it. Every value is
theirs: never fill in a date, a title or a qualification they did not give you.
```

  and step 5 opens: "If they have an existing CV, read it as source material and propose one entry per real achievement, each with the `--company` a role in their CV Layout names and its tools in `--tools`:" (the command line gains `--tools "..."`). The "Things that will look like bugs" list gains the attribution-check warning and the "not on your CV" count.
- `docs/GUARANTEES.md`, "The CV cannot invent things": the bullets become —

```markdown
- Every work bullet must cite a verified entry, and only an entry that belongs to the role it
  sits under (for entries that name a company).
- Every number in a bullet must appear in an entry it cites; every number in the profile
  must appear in some entry.
- Once any entry declares `Tools:`, a tool named in a bullet must be listed, or named, by an
  entry it cites.
- Headings, dates, locations, titles, certificates, education, your name and your contact
  details come from your vault, never from the model.
- A skill the model picks that is not one of your verified skills or tools is dropped, never
  shown.
```

  and "One limit" says the gate checks what the MODEL wrote, while a custom Jinja2 template is free text sluice does not audit.
- `sluice/templates/cv_plain.html.j2` and `docs/cv-template-example.html.j2`: "a heading the composer already emits" becomes "a section heading sluice writes (`core/protocols.py::SECTION_HEADINGS`)", and the `select()` comment says it drops a blank location or title -- both optional in the CV Layout -- so neither leaves a dangling separator. Add to `tests/test_renderer_template.py`:

```python
def test_an_untitled_role_does_not_leave_a_dangling_separator(tmp_path):
    # Spec §7.2: a layout role's title is optional now, so an untitled role is ordinary.
    from sluice.core.protocols import CvDocument, Role
    doc = CvDocument(name="JANE ROE", contact="+1 555 0100", profile="I build.",
                     work=[Role("Example Alpha", "01/2020–present", "Example Location A", "",
                                ["Shipped it"])],
                     skills=[], certificates=[], education=[])
    _renderer(tmp_path).render(doc, str(tmp_path / "out"))
    html = FakeHTML.captured["html"]
    assert "01/2020–present | Example Location A<" in html.replace("\n", "")
    assert "Example Location A | " not in html
```

- [ ] **Step 4: Prose inside code**

Spec §14: grep the CLAIM words across `sluice/`, `scripts/` and `tests/` — `precheck`, `read_baseline`, `baseline_rel`, `baseline CV`, `ground truth`, `parse_cv`, `parse.py`, `cv/parse`, `cv.parse`, `section_spans`, `cv_employers`, `cv.employers`, `classify_negatives_vs_skills`, `FORMAT:`, `Skills:`, `framing, licensed by nothing`, `composer already emits` — and rewrite every comment or docstring that still states a removed mechanism. Sites known before Tasks 18–20 touched some of them: `core/protocols.py`'s `Store` docstring, `read_candidate_profile`'s and `EvidenceKind`'s (whose attribute COUNT is deleted, not bumped); `ingest/base.py::Source`; `triage/engine.py`'s dry-run justification; `cv/voice.py`'s and `core/safeout.py`'s docstrings; `cv/slop.py::check_hard`'s docstring, which argues whole-document scope (it now runs over the model's text only); `cli.py`'s `FORMAT` comment; `mcpserver.py`'s doctor description. A mention that says a thing was removed, and by which issue, may stay.

Run: `grep -rnE "precheck|read_baseline|parse_cv|section_spans|cv_employers|classify_negatives_vs_skills|composer already emits" sluice scripts tests`
Expected: no match outside a sentence that states the removal.

- [ ] **Step 5: Run everything**

Run: `.venv/bin/python -m pytest tests/test_docs_layout_samples.py tests/test_docs_claims.py tests/test_doc_links_from_code.py tests/test_citation_drift.py tests/test_renderer_template.py -q && .venv/bin/python -m pytest -q`
Expected: PASS. A docs-claims or doc-links failure here is a doc that does not match the code: fix the doc.

- [ ] **Step 6: Commit**

```bash
git add README.md docs sluice scripts tests
git commit -F - <<'EOF'
docs: describe structured CV composition and the CV Layout note

How a CV is assembled now, the CV Layout note and its rules, Tools: and
Label:, decoys and negatives, the new doctor rows, and the setup steps
for an AI agent. A test holds every layout sample in the docs to refuse
at its placeholders and to show only reviewed or Example-shaped names.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 23: `.rulesync/` describes the structured CV

Spec §14 (`.rulesync/`). `.rulesync/rules/CLAUDE.md` is the highest-leverage place to assert something false: every future agent reads it. Its CV-gate sections describe machinery this change removed (the parser and its implication sweep, the marker tuples, the envelope unwrap, the #99 header guards, the SKILLS rows, `precheck`), so they are REWRITTEN, not amended — and every sentence below describes code that exists by this task. Regenerate after editing; the generated `CLAUDE.md`/`AGENTS.md`/`.claude/` are gitignored.

**Files:**
- Modify: `.rulesync/rules/CLAUDE.md`
- Modify: `.rulesync/skills/review-pr/SKILL.md`, `.rulesync/skills/review-plan/SKILL.md`, `.rulesync/skills/path-to-green/SKILL.md`, `.rulesync/skills/address-comments/SKILL.md`, `.rulesync/subagents/sluice-invariant-reviewer.md`

- [ ] **Step 1: Replace the CV-gate sections**

In `.rulesync/rules/CLAUDE.md`, replace the section that begins `**The CV fabrication gate is hard.**` (it runs until `**Citability has ONE writer`) with:

```markdown
**A CV is assembled, never parsed (#364/#365/#368).** `cv run` asks the backend for a JSON
reply -- a profile, cited bullets per role SLOT, and skill picks from a closed list -- and
sluice builds the `CvDocument` itself: the CV Layout note (`Job Applications/CV Layout.md`,
`core/layout.py`) gives every role's heading, dates, location and title and which experience
entries each role may cite, and the Candidate Profile gives the name and contact. The model
writes no heading, date, name, certificate or education line, so there is no check on any of
them, and none must be added: a check that refuses vault text makes the user prove their own
data. The checks exist to catch the MODEL putting words in the user's mouth, and stop there.
`cv/reply.py` reads a reply (`extract_json`: fenced blocks first, then each `{`; the first
decoded object carrying `profile` and `roles` wins; a duplicate key is a finding, never a
skip) and shape-checks it (`parse_reply`: a typed `Reply`, or `REPLY:` findings; slot ids
match case-insensitively). `cv/selection.py` decides what may render: skill picks off the
pool, then duplicates, then beyond `skills_max` are DROPPED -- in that order, so a rejected
pick never uses up the cap -- and bullets beyond a role's budget are TRIMMED, the first N
kept. A drop is reported (`skills_dropped`, `bullets_trimmed`) and never refused, so it never
costs a retry or a lead.

**The fabrication gate is hard, and reads only what the model wrote.**
`cv/validate.py::check_selection` runs over the SELECTION: every kept bullet cites entries
that exist (`UNCITED BULLET`, `BAD CITATION`) and are eligible for its slot
(`WRONG EMPLOYER`); every figure in a bullet appears among its cited entries' figures, and
every figure in the profile among some entry's (`INVENTED METRIC`, `INVENTED PROFILE
METRIC`) -- digits in any script, normalised on both sides, with the cited entries' `Tools:`
tokens blanked by their own token spans first, never by substring; a tool from the verified
`Tools:` vocabulary named in a bullet must be declared, or named in the same case, by a cited
entry (`MISATTRIBUTED TOOL`), a check that runs only while some verified entry declares
`Tools:`, and every result says whether it did (`attribution_check_off`); and a
`fabrication_decoys` term matches as a whole token sequence, never across a sentence break
(`FABRICATED`). An em dash or a literal `--` in the same texts (`cv/slop.py::check_hard`) is the
hard slop tier and reports in `slop`. A reply that cannot be read, a bracket or line break in a
text, or no bullets in any role that can carry them, is a `REPLY:` finding.
`check_selection` has ONE accumulator, extended only by `v.append(<CATEGORY> ...)` with the
category inline: `tests/test_docs_claims.py` derives the gate's categories from exactly that
shape and pins `docs/TROUBLESHOOTING.md` to them, so a category reached any other way is
documented by nothing.

**An entry's figures and tools each have one home.** `entry_facts` takes an entry's figures
from `cv/bundle.py::_entry_block` -- the lines the composer is shown for it -- and its tools
from `core/tokens.py::tool_items`, emitted SEPARATELY, so a digit inside a tool's name never
licenses a figure. A line added to `_entry_block` becomes a source for that entry; the
frozen-literal guards in `tests/test_cv_bundle.py` are the ratchet, and re-freezing
`FROZEN_BUNDLE_TEXT` after widening `_entry_block` is the one move that launders a widening,
so read the freeze diff. Nothing else licenses a figure: not the negatives, not the Skills
Inventory, not the CV Layout, and not a baseline CV, which nothing reads. `core/tokens.py` is
the one tokeniser and term matcher, shared with `doctor` because `core/` may not import a
sub-app. A `Tools:` item whose token begins with a digit is refused before any spend
(`missing_prerequisites`), since a name shaped like a figure would let an invented figure
vanish with it.

**The retry contract.** A HARD finding, or a STYLE/VOICE finding that survives (#167, #194),
drives EXACTLY one retry, fed the findings and the previous reply's drops. The loop RETAINS the
hard-clean attempt with the fewest style/voice findings -- a tie keeps the later one, and an
attempt whose voice check failed never displaces one whose voice was measured -- and a lead
with no hard-clean attempt is skipped, never rendered. `best` holds the retained SELECTION,
drops included, and the rebind after the loop is the one assignment every later reader goes
through: `assemble`, `cv.rendered.md`, the audit and the render all take the retained
attempt. The STYLE tier (slop phrases, unbundled terms against
`cv/bundle.py::term_vocabulary`, the opt-in voice check) reads the model's text only.
`term_vocabulary` is built from the entries, their tools, the Skills Inventory and the CV
Layout, and subtracts NOTHING (#368: reading prose negatives as bans stripped terms the user's
own evidence carried). `cv.negatives` is free-text guidance to the composer that no check
reads; a ban belongs in `cv.fabrication_decoys`.

**The advisory audit and the holds.** Above the hard gate an LLM audit (`cv/audit.py`) reads
`cv/document.py::audit_text` -- the profile and each kept bullet under its role heading, with
its cites, and no vault text, which it has no truth for -- against
`cv/bundle.py::render_audit_bundle`, which carries each entry's `Tools:` line so a tool the
gate licensed does not read as unsupported. An `unsupported` flag, or an audit that could not
run (#333), WITHHOLDS the send-ready `tailored_cv` pointer under `cv.require_signoff` (on by
default) via `Store.hold_for_signoff`, cleared by `job-sluice cv signoff`. The hold is
recorded in `pending_cv` and `needs_signoff`; the note's `status` stays `shortlist`, so
never-regress is untouched, and `needs-signoff` is the `CvResult` run-report label, never a
`status` value. `cv.style_hold` (off by default) gives a surviving STYLE/VOICE finding the
same consequence, deliberately a SEPARATE key: `require_signoff`'s True default was chosen for
fabrication, and riding it would withhold `tailored_cv` on a phrase.

**The Renderer seam takes the document.** `Renderer.render(document, out_dir, *,
neutral_name)`: `template` renders the `CvDocument` directly; `script` is handed
`cv/document.py::to_text(document)` without citations -- the meta line POSITIONAL
(`dates | location | title`, always three fields), dates joined by an en dash -- pinned by the
literal in `tests/test_cv_script_golden.py`, captured from the old pipeline. There is no
`precheck` and no renderer grammar to keep in step with the gate: a renderer receives data,
so it can refuse nothing the gate certified. `CvDocument`/`Role` live in `core/protocols.py`
and keep their exact fields, because user templates are written against them.
```

Then delete, whole, the three sections that describe what is gone: `**The gate is blind to the name/contact block, and that block renders as the PDF's headline (#99).**`, `` **`compose.py` recovers the artefact from an agentic backend's conversational envelope (#28).** `` and `` **A renderer's `precheck` must never be STRICTER than that gate** `` (with its implication-sweep, repeated-header and marker-equality paragraphs), up to `**Neutrality: no personal data in this repo.**`.

- [ ] **Step 2: The other passages that state a removed mechanism**

- In `**Citability has ONE writer**`, replace the sentences from "`EvidenceKind` carries TWO flags since #165" to the end of that paragraph with:

```markdown
`EvidenceKind` carries THREE flags, because the questions stopped having one answer:
`read_by_composer` says the corpus reaches the composer's prompt, `cited_by_gate` says the
fabrication gate may LICENSE its content, and `names_in_skills_pool` (#364/#365/#368, D12)
says a verified note's NAME -- its `Label:`, else its title -- may appear in a CV's SKILLS
section. `experience` is read and cited; `skills` is read and named; `stories` is neither.
`__post_init__` refuses `cited_by_gate` without `read_by_composer`, since the gate cannot
license what the composer never emitted. A fourth attribute, `legacy_fields`, is
presence-only: the store reports whether a note still carries a retired field (`Skills` on
experience) and never its value, so nothing can read it as a field again. Every message that
says what `verify` buys -- `verify` itself, `doctor`'s store row and the MCP propose result --
comes from `core/protocols.py::verify_outcome`, keyed on `cited_by_gate` and then
`names_in_skills_pool`, so none can over-claim.
```

- In the non-resurrection section, after the sentence ending "both halves must fold identically or the collision skip silently stops firing.", add:

```markdown
A THIRD kind since #364/#365/#368: `core/layout.py::fold_employer` matches an experience
entry's `Company:` to a CV Layout role through the same fold, imported inside the function
(the import cycle with `core/vault.py` breaks there). It sits on the IDENTITY side -- a wider
fold makes more entries eligible for more roles, which loosens `WRONG EMPLOYER` -- so the fold
must not widen for it either.
```

- In the Conventions bullet on adapter seams: delete the sentences describing the renderer's optional `precheck` member, the engine's `getattr` of it, its return-type check, and `Sluice.compose_cv` resolving the renderer on a `--dry-run` "purely so this hook runs"; say instead that a dry run builds no renderer. The `preflight()` sentence stops saying "exactly like `precheck`" (it is optional because a store that cannot say is not a broken one), and its list of facts drops "baseline CV". The same bullet's "the way the other three do" is a count that was already wrong (`_SEAMS` has five members): delete the number -- "the way the other seams do". In the config paragraph, name the one exception to each loader reading its own block: `load_config` also refuses the retired `cv.*` inputs, so that every command stops on them.
- Grep the file for the remaining claim words (`baseline`, `parse_cv`, `parse.py`, `section_spans`, `precheck`, `MISSING EMPLOYER`, `UNSOURCED SKILL`, `cv.employers`, `Skills:`) and rewrite or delete each sentence that states a removed mechanism. Historical mentions that say "removed in #364/#365/#368" may stay.

In the routing tables, the fabrication gate is `sluice/cv/validate.py`, `sluice/cv/engine.py`, `sluice/cv/reply.py`, `sluice/cv/selection.py`, `sluice/cv/document.py`, `sluice/core/layout.py` and `sluice/core/tokens.py`: add the five new files to the gate row of `.rulesync/skills/review-pr/SKILL.md`, the gate bullet of `path-to-green` and `address-comments`, the seam lists of `review-plan`, and the heading and file list of `.rulesync/subagents/sluice-invariant-reviewer.md`'s section 3.

- [ ] **Step 3: Commit**

```bash
git add .rulesync
git commit -F - <<'EOF'
docs: describe structured CV composition in the agent rules

The CV-gate rules are rewritten for a CV assembled from the vault: the
reply and selection, the hard checks over the model's text, the one home
for an entry's figures and tools, the retry contract, the audit and the
document-taking renderer seam. The parser, precheck, envelope and header
guard sections are gone with the code, and the new modules join the
review routing.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```


- [ ] **Step 4: Regenerate and check, the way CI's `rulesync` job does**

With Step 3's commit in place, run CI's own sequence, which ends by requiring a clean tree:

```bash
npm ci --ignore-scripts
python3 scripts/reset_tracked_hooks.py .
npm run rulesync | tee /tmp/rulesync-output.txt
.venv/bin/python scripts/guard_rulesync_drift.py /tmp/rulesync-output.txt
.venv/bin/python scripts/guard_emitted_outputs.py .
git status --porcelain
```

Expected: both guards exit 0 and `git status --porcelain` prints nothing (generated files are gitignored; `.claude/settings.json`'s `hooks` key is regenerated to what is committed).

Run: `grep -rnE "precheck|parse_cv|section_spans|_unwrap_agent_envelope|read_baseline|MISSING EMPLOYER|UNSOURCED SKILL" .rulesync`
Expected: no match, or only a sentence saying the thing was removed.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (`tests/test_guard_rulesync_drift.py`, `tests/test_guard_emitted_outputs.py` and `tests/test_rulesync_version_pin.py` among them).
### Task 24: The ledger, the witnesses, and full verification

Spec §12.3. The ledger is checked, not trusted; every new gate row is WITNESSED by breaking the code it pins and watching it go red; and the suite runs in the environments the memory records as having caught real breakage.

**Files:**
- Modify: `docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md` (missing rows; a Witnesses section)
- Create (outside the repo, never committed): `$SCRATCH/check_ledger.py`, where `$SCRATCH` is a directory outside every worktree (e.g. `mktemp -d`)

- [ ] **Step 1: Check the ledger mechanically**

Write `$SCRATCH/check_ledger.py`:

```python
"""Spec §12.3: the ledger covers every test the branch deleted or changed, and every
`ported as` names a test collected on the branch. Run from the worktree root."""
import ast
import pathlib
import re
import subprocess
import sys

LEDGER = pathlib.Path("docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md")


def git(*args, cwd="."):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def collected(root):
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                          "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True)
    ids = {ln.split("[")[0] for ln in out.stdout.splitlines() if "::" in ln}
    assert ids, f"collected nothing under {root}: {out.stdout[-500:]}{out.stderr[-500:]}"
    return ids


def functions(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {n.name: ast.get_source_segment(source, n) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")}


fork = git("merge-base", "origin/main", "HEAD").strip()
fork_tree = pathlib.Path(sys.argv[1])           # a `git worktree add <path> <fork>` checkout
assert git("rev-parse", "HEAD", cwd=fork_tree).strip() == fork, "the fork tree is not at the fork point"
before, after = collected(fork_tree), collected(".")

changed = set()
for path in git("diff", "--name-only", f"{fork}...HEAD", "--", "tests").split():
    if not path.endswith(".py"):
        continue
    old = functions((fork_tree / path).read_text(encoding="utf-8")) if (fork_tree / path).exists() else {}
    new = functions(pathlib.Path(path).read_text(encoding="utf-8")) if pathlib.Path(path).exists() else {}
    changed |= {f"{path}::{name}" for name in old.keys() & new.keys() if old[name] != new[name]}

required = (before - after) | changed
rows = {}
for line in LEDGER.read_text(encoding="utf-8").splitlines():
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) != 3 or not cells[0].startswith("`tests/"):
        continue
    for test_id in re.findall(r"`(tests/[^`]+)`", cells[0]):
        rows[test_id] = cells[2]

missing = sorted(required - rows.keys())
ported = {t for v in rows.values() for t in re.findall(r"ported as `(tests/[^`]+)`", v)}
dangling = sorted(t for t in ported if t.split("[")[0] not in after)
print(f"required {len(required)}, ledgered {len(rows)}, missing {len(missing)}, "
      f"dangling ported-as {len(dangling)}")
for t in missing:
    print("MISSING", t)
for t in dangling:
    print("DANGLING", t)
sys.exit(1 if missing or dangling else 0)
```

Run:

```bash
SCRATCH=$(mktemp -d)
git worktree add "$SCRATCH/fork" "$(git merge-base origin/main HEAD)"
.venv/bin/python "$SCRATCH/check_ledger.py" "$SCRATCH/fork"
git worktree remove "$SCRATCH/fork"
```

Expected: exit 0 with `missing 0, dangling ported-as 0`. For each MISSING id, add its row — the property it protected, and `obsolete: …, spec §…` or `ported as …` — never a blanket row. For each DANGLING id, fix the row to name a test that exists.

- [ ] **Step 2: Witness every new gate row**

Prepare once: `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`. Then, for EACH row below: confirm `git status --porcelain` is empty (commit first if not — a witness must never leave the tree mutated), make the ONE mutation named, run the named test file, confirm the named row goes RED for the stated reason (read the assertion, not just the exit code), then `git checkout -- sluice` and confirm the tree is clean again. Mutate by DELETING or by SWAPPING the old behaviour in at one site; never by adding a check beside the original.

| # | Mutation (one site) | Must go red |
|---|---|---|
| 1 | `cv/bundle.py::term_vocabulary`: subtract the case-folded tokens of `bundle["negatives"]` from the result | `tests/test_cv_structured_bundle.py::test_a_prose_negative_subtracts_nothing_from_the_vocabulary` |
| 2 | `core/tokens.py::find_term`: replace the token-sequence match with `term.casefold() in text.casefold()` | `tests/test_cv_checks.py::test_a_decoy_is_refused_as_a_whole_term_only` |
| 3a | `cv/reply.py::_text_findings`: delete the bracket arm | `tests/test_cv_reply.py::test_a_bracket_in_the_profile_is_a_finding` and `::test_a_bracket_in_bullet_text_is_a_finding` |
| 3b | `core/tokens.py::figures`: strip `\[[^\]]+\]` from the text before scanning | `tests/test_cv_checks.py::test_a_bracketed_figure_is_still_scanned_when_it_reaches_the_check` |
| 4a | `cv/validate.py::_spans`: remove a licensed tool by substring rather than by its token spans | `tests/test_cv_checks.py::test_a_licensed_name_never_launders_a_longer_figure` |
| 4b | `cv/validate.py::check_selection`: blank the span of EVERY letter-led token in the profile, as a name list would, instead of only the entries' declared tools | `tests/test_cv_checks.py::test_only_tools_never_other_names_strip_a_profile_digit` |
| 4c | `cv/validate.py::check_selection`: remove spans licensed by EVERY entry's tools, not only the cited entries' | `tests/test_cv_checks.py::test_span_removal_is_licensed_by_the_cited_entries_only` |
| 5 | `cv/validate.py::_licenses`: compare the entry's text case-insensitively | `tests/test_cv_checks.py::test_a_cited_entrys_own_text_licenses_a_tool_by_the_same_case_rule` and `::test_a_longer_token_in_the_cited_text_never_licenses_the_shorter_tool` |
| 6 | `cv/validate.py::entry_facts`: harvest an entry's figures as ASCII `[0-9]+` runs instead of through `figures()`, so only the reply side is normalised | `tests/test_cv_checks.py::test_entry_facts_normalise_an_entrys_native_digits` |
| 7 | `cv/document.py::assemble`: take each role's bullets by zipping the reply's slots against the layout, not by slot id | `tests/test_cv_document.py::test_bullets_land_under_their_own_slot_whatever_the_reply_key_order` and `::test_an_omitted_middle_slot_renders_empty_and_shifts_nothing` |
| 8a | `cv/engine.py::_run_one`: delete the rebind `selection, slop_msgs, term_msgs, voice_flags = best` | `tests/test_cv_structured_engine.py::test_the_document_carries_the_retained_attempts_text` and `::test_the_retained_attempt_is_rendered_with_its_own_drops` |
| 8b | `cv/engine.py::_run_one`: report `skills_dropped`/`bullets_trimmed` from the LAST attempt's selection instead of the retained one | `tests/test_cv_structured_engine.py::test_the_retained_attempt_is_rendered_with_its_own_drops` |
| 9 | `cv/selection.py::select`: keep the reply's own spelling of a pick instead of the pool's | `tests/test_cv_selection.py::test_a_kept_pick_renders_in_the_pools_spelling_and_a_one_token_difference_drops` |
| 10 | `cv/document.py::to_text`: join the meta line with `" | ".join(f for f in (...) if f)` | `tests/test_cv_document.py::test_the_meta_line_is_always_three_positional_fields` |
| 11 | `cv/reply.py::extract_json`: drop the fenced-block candidates (try only each `{`) | `tests/test_cv_reply.py::test_an_echoed_shape_before_a_fenced_reply_does_not_win` |
| 12 | `cv/reply.py::_text_findings`: delete the format-character (`Cf`) arm | `tests/test_cv_reply.py::test_an_invisible_format_character_is_a_finding` |
| 13 | `cv/validate.py::check_selection`: license a bullet's figures from EVERY entry (`licensed = every_figure`) instead of its cited ones | `tests/test_cv_checks.py::test_a_figure_only_an_uncited_entry_carries_is_refused` |

Record each in the ledger file under a `## Witnesses` heading: the row number, the mutation, the test that went red, and the assertion message it failed with (one line). A row that STAYS green is a finding, not a skip: either the test cannot see the mutation (strengthen it) or the mutation is equivalent (choose one that is not) — and per the memory, a surviving mutant is evidence about the harness until proven otherwise, so first confirm the mutation actually ran (stale bytecode, the wrong file).

- [ ] **Step 3: Full verification**

Run each; every one must PASS:

```bash
.venv/bin/ruff check sluice tests scripts
.venv/bin/python -m pytest -q
TZ=Asia/Dubai .venv/bin/python -m pytest -q
TZ=Pacific/Kiritimati .venv/bin/python -m pytest -q
env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-10-05-structured-cv-composition-ledger.md
git commit -F - <<'EOF'
docs(plan): complete the test ledger and record the witnesses

Every test this branch deleted or changed has a row, checked against the
fork point's collection, and every ported-as names a collected test.
Each new gate row was witnessed red by breaking the code it pins.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

### Task 25: The owner's migration and real composes

Spec §11 (measurement and migration stay private), §12.4 (real composes before merge; the JSON-schema contingency). EVERYTHING in this task touches the owner's real vault and config and is done OUTSIDE every worktree. Nothing from it — no lead, employer, heading, skill, date or count tied to a name — enters a commit, the PR, an issue, the CHANGELOG or a release note. If the PR must say a measurement happened, it gives aggregate counts only (for example, "attempt 1 parsed for N of N leads").

- [ ] **Step 1: Ask before touching anything**

Use `AskUserQuestion` to ask the owner whether to migrate their vault now, and whether real dry runs may spend their backend's calls. Do nothing further in this task without a yes.

- [ ] **Step 2: Draft, show, and write only what the owner approves**

From a cwd outside every worktree, with their real config:

1. **CV Layout.** Read their vault's existing CV material, draft `CV Layout.md` into a scratch directory outside every worktree, and show it to them. Write it to `Job Applications/CV Layout.md` only if that note does not exist; if it does, show a diff and change only what they approve. Never invent a date, a title or a qualification.
2. **`Tools:`.** For each verified experience entry still carrying `Skills:`, draft the `Tools:` value it implies (the same names, digit-led ones flagged for their decision), show the list, and apply only what they approve — editing each note's frontmatter line, nothing else in it.
3. **Companies.** Run `job-sluice doctor`; for each entry counted "not on your CV" or "no company", show `job-sluice experience list`'s line for it and the fix you propose (a role's `employers`, `any_role`, `omitted`, or the entry's `Company:`); apply only what they approve.
4. **Decoys and negatives.** In their own config (never this repo), show which `cv.negatives` lines are bans ("never claim X") and propose moving each X to `cv.fabrication_decoys`, the rest staying as guidance; apply only what they approve. Then `job-sluice doctor` again: no `cv` row may remain blocking.

- [ ] **Step 3: Real composes, measured**

With permission, `job-sluice cv run --dry-run --lead <fragment>` for a handful of real shortlisted leads, from a cwd outside every worktree and with `cv.output_dir` outside them too. For each lead record, privately: whether attempt 1's JSON parsed, compose calls, bullets total, hard findings, skills dropped and bullets trimmed, style findings, and the audit's unsupported count. Report the table to the owner; delete the artefacts or leave them with the owner once the counts are taken.

- [ ] **Step 4: The contingency, only if a reply failed to parse**

If any attempt-1 reply failed to parse, build spec §12.4's hint, test-first, as its own commit before Task 26: `complete(prompt, *, json_schema=None)` becomes a keyword-only per-call hint on the backend seam; `core/usage.py::MeteredBackend` and `core/backends.py::RetryingBackend` forward it; the per-token providers accept and ignore it; `claude-max` passes it as the CLI's `--json-schema` after confirming on the configured host that the flag exists (`claude --help`); `compose_structured` passes the reply's schema. Rows: every provider accepts the keyword; each wrapper forwards it; only `claude-max` maps it. Then re-run Step 3. If every reply parsed, this step is skipped, and the PR says the measurement did not require it.

### Task 26: Pre-push review, and the PR

The merge gate in the project memory (`procedural_merge_gate.md`): the local review team first, because CodeRabbit is scarce; CodeRabbit is the sole approver; never bypass.

- [ ] **Step 1: Every commit green, on the current `main`**

```bash
git fetch origin
git rebase origin/main
git rebase -x ".venv/bin/python -m pytest -q -x" origin/main
```

Expected: every commit passes (the memory records that a long-lived branch can fail CI purely because `main` gained guards since the fork, and that an autosquash must be tested commit by commit, not only at the tip).

- [ ] **Step 2: `/review-pr` before pushing**

Run the `/review-pr` skill over the branch. Fix each confirmed finding in a `fixup!` commit aimed at the commit that introduced it, fold them with `git rebase --autosquash origin/main` (non-interactive since git 2.44; this machine has 2.56), and re-run Step 1's per-commit check. A finding about personal data is escalated to the owner neutrally, never "fixed" by guessing.

- [ ] **Step 3: Open the PR, and notify**

Push the branch and open the PR with `gh pr create`. The body: what changed and why (the one-paragraph problem statement from the spec's §1, with no personal data), the migration steps from spec §10 (the CHANGELOG entry is edited later, in release-please's PR), how it was verified (the suite, the witnesses table's existence, aggregate real-compose counts only), and `Fixes #364`, `Fixes #365`, `Fixes #368`. Confirm with `gh pr view --json closingIssuesReferences` that exactly those three are linked. Then send the owner a push notification that the PR is open, with its URL.

- [ ] **Step 4: CodeRabbit**

Follow the memory's CodeRabbit procedure: a new PR auto-reviews; read every review with `--paginate`; verify each finding against the code before acting on it; resolve a thread only once the fix exists in the tree; and stop for the owner's approval of the merge itself.
