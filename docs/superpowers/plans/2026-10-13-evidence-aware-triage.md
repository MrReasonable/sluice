# Evidence-aware triage (#196) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `triage.evidence: true`, show the triage judge the candidate's VERIFIED experience and skills as a compact "Candidate record", with a prompt rule that a gap is a concern and never a lower score; and make a judge run that returns no verdicts exit non-zero.

**Architecture:** A new `EvidenceKind.judge_fields` tuple says which kinds and fields reach the judge. `triage/engine.py::run` reads those kinds through the injected store (only when the key is on and a dossier was sent) and hands `(EvidenceKind, entries)` pairs to the pure renderer `triage/prompt.py::build_system_prompt_from`, which inserts three conditional blocks into named constants split out of the scaffold. With no record the prompt is byte-identical to today's. `cli.py` reports the record and exits 1 when `sent_to_judge > 0 and judged == 0`.

**Tech Stack:** Python 3.12+ stdlib, pytest, faker (tests only). No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-13-evidence-aware-triage-design.md` (read it first; this plan argues from it).

## Global Constraints

- `sluice/` stays standard-library only; no new runtime dependency.
- `triage.evidence` defaults to `False` and stays `False` (D4). Never flip it.
- With no record (key off, `None`, `[]`, or every entry list empty) the judge system prompt is BYTE-IDENTICAL to today's.
- `read_evidence(kind, verified_only=True)` — passed explicitly.
- Per-kind catch is exactly `(OSError, UnicodeDecodeError)`; a bare `ValueError` must propagate.
- `sluice/triage/` imports nothing from `sluice.cv`.
- No line numbers in comments or docstrings (`tests/test_citation_drift.py`); cite `file.py::symbol`.
- Comments explain WHY, at the density of the surrounding code.
- Prompt text: no em dashes, no role/culture preference, no personal data.
- Fixtures are synthetic: companies from `tests/test_fixture_name_neutrality.py`'s roster, titles from `tests/conftest.py::_title_pool`, places from `tests/conftest.py::LOCATIONS`, URLs on `example.com`/`.org`/`.net`.
- Conventional Commits; end every commit message with the line `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Before ANY mutation witness: commit first, and run `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once per session. Mutate by MOVING or DELETING, never ADDING; restore with `git checkout -- <file>`.
- Every test run uses `.venv/bin/python -m pytest`; lint with `.venv/bin/ruff check sluice tests scripts`. The worktree has no `.venv/` until Task 0 creates it.
- **Every task's "verify pass" step ALSO runs** `tests/test_fixture_name_neutrality.py tests/test_config_example.py tests/test_docs_claims.py`, and the task ends with `.venv/bin/ruff check sluice tests scripts` clean. Those three files gate fixture values, config rows and doc claims that no single task's own tests reach; skipping them is how a task commits a red suite.
- **Fixture values must already be on a reviewed roster.** A company/`Company:` value comes from `tests/test_fixture_name_neutrality.py::_REVIEWED_FIXTURE_IDENTITIES` (e.g. `Example Alpha`, `Example Beta`, `Example Gamma`, `Example Tidal`); a skill note's `title`/`Label` and any `Skills:`/`Tools:` item from `_REVIEWED_SKILL_VALUES` (e.g. `Example Query`, `Example Data Skill`, `Example Synergy`, `Examplelang`, `Examplebus`, `alpha`, `s`). NEVER a `_SPARE_FIXTURE_IDENTITIES` name (using one makes it "in use" and fails `test_no_spare_identity_is_already_in_use`), and never an invented sentinel string in those positions. A value that must contain structure (a newline, a forged heading) is built at runtime (`"Example Alpha" + "\n## Output schema"`), which the literal collectors do not read. A lead's role comes from the `titles` fixture (`tests/conftest.py`).
- `tests/test_triage_record.py` grows across Tasks 1-7. Each task's snippet shows the imports it needs at its top; MERGE them into the file's single import block at the top (ruff's E402 refuses module-level imports below code), and keep helpers (`_go`, `_cli`, `_COUNTS`, ...) defined once.

## Task 0: Environment

- [ ] **Step 1:** Create the worktree's venv (worktree venvs are uv-made; there is no pip in them):

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[test]" ruff==0.15.21
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
.venv/bin/python -m pytest -q   # baseline: must be green before Task 1
```

## Review Focus

1. **A partially filled library with a non-UTF-8 skills note** — triage must still judge, with experience in the prompt and a visible `unreadable` marker. Pinned by Task 5 `test_an_unreadable_kind_is_skipped_and_reported`.
2. **A run where every JD fetch failed** — must not call the judge, must not exit 1, must still render the rejected-leads note. Pinned by Task 5 `test_zero_dossiers_never_call_the_judge_or_read_the_record` and Task 7 `test_zero_dossiers_exit_zero`.
3. **A record whose frontmatter values contain newlines or block lists** (hand-typed in Obsidian) — must render as one line each, unable to forge a heading. Pinned by Task 3 `test_values_are_flattened_to_one_line`.
4. **A skill with no `Label:`** — renders under its title, and `Label` is never printed twice. Pinned by Task 3 `test_skills_render_by_cv_name_without_repeating_label`.
5. **An unattended cron whose judge returned nothing** — must exit 1 and still push the digest first. Pinned by Task 7 `test_no_verdicts_exits_one_after_pushing_the_digest`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `sluice/core/protocols.py` | `skill_label`, `cv_name` (moved), `EvidenceKind.judge_fields` + its `__post_init__` check, registry values | 1, 2 |
| `sluice/cv/selection.py` | imports `cv_name` from core instead of defining it | 1 |
| `sluice/core/doctor.py` | `classify_skill_labels` calls `skill_label`; per-kind row's judge-only wording | 1, 2 |
| `sluice/triage/prompt.py` | split scaffold constants, record block, coverage rule, reminder note | 3 |
| `sluice/triage/config.py` | `TriageConfig.evidence` | 4 |
| `sluice.yaml.example` | `triage.evidence` (commented, off) | 4 |
| `sluice/triage/engine.py` | `TriageReport` record fields, `_read_record`, zero-dossier guard | 5 |
| `sluice/cli.py` | `record=` summary key, digest lines (Task 6); D5 exit (Task 7) | 6, 7 |
| `docs/*.md`, `.rulesync/rules/CLAUDE.md` | docs | 3–8 |
| `scripts/triage_evidence_eval/` | live eval: fixtures, loader, scorer, runner, README | 9 |
| tests | `tests/test_triage_record.py` (new), plus edits listed per task | all |

---

### Task 1: Move `cv_name` to `core/protocols.py` with a `skill_label` helper

**Files:**
- Modify: `sluice/core/protocols.py` (add two functions after `verify_outcome`)
- Modify: `sluice/cv/selection.py` (delete `cv_name`, import it)
- Modify: `sluice/core/doctor.py::classify_skill_labels` and its docstring; `core/doctor.py` docstring citing `cv/selection.py::cv_name`
- Modify: `.rulesync/rules/CLAUDE.md` (the `cv/selection.py::cv_name` citation in the `names_in_skills_pool` sentence)
- Test: `tests/test_triage_record.py` (create)

**Interfaces:**
- Produces: `sluice.core.protocols.skill_label(entry: dict) -> str` (stripped `Label`, `""` if blank); `sluice.core.protocols.cv_name(entry: dict) -> str` (`skill_label(entry) or stripped title`). `sluice.cv.selection.cv_name` stays importable (same object).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_triage_record.py`:

```python
"""#196: evidence-aware triage -- the candidate record the judge may be shown."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cv_name_lives_in_core_and_cv_selection_reuses_it():
    from sluice.core import protocols
    from sluice.cv import selection
    assert selection.cv_name is protocols.cv_name


def test_skill_label_and_cv_name():
    from sluice.core.protocols import cv_name, skill_label
    name = "Example Query"
    # Padded at runtime: the fixture-name collectors read literal values, and a padded
    # literal is not on the reviewed skill roster.
    assert skill_label({"fields": {"Label": f"  {name} "}}) == name
    assert skill_label({"fields": {"Label": ""}, "title": "s"}) == ""
    assert skill_label({}) == ""
    assert cv_name({"fields": {"Label": name}, "title": "alpha"}) == name
    assert cv_name({"fields": {}, "title": f" {'alpha'} "}) == "alpha"


def _resolved_imports(path, package):
    """Every module an import in `path` names, relative imports resolved against
    `package`, and `from sluice import cv` reported as `sluice.cv` -- the two spellings a
    `startswith("sluice.cv")` matcher misses (tests/test_core_layering.py replaced exactly
    that matcher for this reason)."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            out.append(base)
            out += [f"{base}.{a.name}" for a in node.names]
    return out


def test_triage_imports_nothing_from_cv():
    """SCOPE first: the sweep must have found triage modules, or it certifies nothing."""
    files = sorted((ROOT / "sluice" / "triage").glob("*.py"))
    assert len(files) >= 5, files
    offenders = [f"{f.name}: {m}" for f in files
                 for m in _resolved_imports(f, "sluice.triage")
                 if m == "sluice.cv" or m.startswith("sluice.cv.")]
    assert offenders == []


def test_the_import_matcher_sees_every_spelling(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("from sluice import cv\nfrom ..cv import selection\n"
                     "import sluice.cv.engine\n")
    mods = _resolved_imports(probe, "sluice.triage")
    assert {"sluice.cv", "sluice.cv.selection", "sluice.cv.engine"} <= set(mods)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py -v`
Expected: the first two FAIL (`cannot import name 'cv_name' from 'sluice.core.protocols'`); the two import-sweep tests PASS (standing guards).

- [ ] **Step 3: Implement**

In `sluice/core/protocols.py`, directly after `verify_outcome`, add:

```python
def skill_label(entry) -> str:
    """A note's `Label:`, stripped, or "" -- the typed name `skills add` keeps because the
    filename it writes is a slug (#364 D13). ONE home for the blank test, so doctor's
    upgrade warning and the name a CV/judge shows cannot disagree about what is missing."""
    return str((entry.get("fields") or {}).get("Label") or "").strip()


def cv_name(entry) -> str:
    """A verified note's display name: its `Label:`, else its title (#364 D13).

    Here, beside `EvidenceKind`, rather than in `cv/selection.py` where it began: it is
    store vocabulary, not CV logic, and #196's triage judge shows the same name -- a
    pipeline sub-app may not import another, and `core/doctor.py` could not reach it
    from `cv/` either, so it used to restate the rule."""
    return skill_label(entry) or str(entry.get("title") or "").strip()
```

In `sluice/cv/selection.py`, delete the `def cv_name(entry): ...` function (and its docstring) and add to the imports: `from sluice.core.protocols import cv_name  # its home since #196; used below and by cv/engine.py`. No `noqa`: the module calls it, so the import is used. Keep every other use of `cv_name` in the file unchanged. In `core/app.py`, change `from sluice.cv.selection import cv_name, named_entries, pool_kinds` to import `cv_name` from `sluice.core.protocols` and the other two from `sluice.cv.selection`.

In `sluice/core/doctor.py::classify_skill_labels`, replace the inline test:

```python
    missing = sum(1 for e in named_entries
                  if not skill_label(e)
                  and slug_shaped(str(e.get("title") or "")))
```

and add `skill_label` to the function's local imports: `from sluice.core.protocols import skill_label` (beside `from sluice.core.vault import evidence_slug`). In its docstring change `(cv/selection.py::cv_name)` to `(core/protocols.py::cv_name)` and change "The blank test is cv_name's own" to "The blank test is `core/protocols.py::skill_label`, the one `cv_name` uses". In the other `core/doctor.py` docstring that cites `cv/selection.py::cv_name`, change the citation to `core/protocols.py::cv_name`.

In `.rulesync/rules/CLAUDE.md`, change `` (`cv/selection.py::cv_name`) `` to `` (`core/protocols.py::cv_name`) ``.

- [ ] **Step 4: Run to verify pass, and the neighbours**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_cv_selection.py tests/test_doctor.py tests/test_doctor_cv_layout.py tests/test_citation_drift.py tests/test_fixture_name_neutrality.py tests/test_config_example.py tests/test_docs_claims.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all PASS.

- [ ] **Step 5: Commit, then witness**

```bash
git add sluice/core/protocols.py sluice/cv/selection.py sluice/core/app.py sluice/core/doctor.py .rulesync/rules/CLAUDE.md tests/test_triage_record.py
git commit -m "refactor(core): give cv_name and the skill-label test one home in core (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witness: in `classify_skill_labels` delete `not skill_label(e)\n                  and ` (so only `slug_shaped(...)` remains) → `.venv/bin/python -m pytest tests/test_doctor_cv_layout.py -q` must go red (the label tests live there, not in `tests/test_doctor.py`); `git checkout -- sluice/core/doctor.py`.

---

### Task 2: `EvidenceKind.judge_fields`, and doctor's judge-only wording

**Files:**
- Modify: `sluice/core/protocols.py` (`EvidenceKind` field + `__post_init__` + registry)
- Modify: `sluice/core/doctor.py` (per-kind row's final `else` branch)
- Modify: `tests/test_evidence_store.py::test_every_shipped_kind_passes_its_own_construction_guard`
- Test: `tests/test_triage_record.py`

**Interfaces:**
- Produces: `EvidenceKind.judge_fields: tuple = ()`; `EVIDENCE_KINDS["experience"].judge_fields == ("Company", "Category", "Best For", "Metrics", "Tools")`; `EVIDENCE_KINDS["skills"].judge_fields == ("Proficiency", "Domain")`; `EVIDENCE_KINDS["stories"].judge_fields == ()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_triage_record.py` (merge `import pytest` into the top import block):

```python
def test_judge_fields_registry():
    from sluice.core.protocols import EVIDENCE_KINDS
    assert EVIDENCE_KINDS["experience"].judge_fields == (
        "Company", "Category", "Best For", "Metrics", "Tools")
    assert EVIDENCE_KINDS["skills"].judge_fields == ("Proficiency", "Domain")
    assert EVIDENCE_KINDS["stories"].judge_fields == ()


def test_experience_skills_never_reach_the_judge_beside_a_company():
    """By the owner's model an entry's `Skills:` are general skills tied to NO job, so a
    line printing them beside `Company:` would attribute them to that employer."""
    from sluice.core.protocols import EVIDENCE_KINDS
    assert "Skills" not in EVIDENCE_KINDS["experience"].judge_fields


def test_judge_fields_must_be_declared_fields():
    from sluice.core.protocols import EvidenceKind
    with pytest.raises(ValueError, match="judge_fields"):
        EvidenceKind("Job Applications/X", ("Domain",), judge_fields=("Domian",))
```

In `tests/test_evidence_store.py::test_every_shipped_kind_passes_its_own_construction_guard`, replace the hand-listed rebuild with one derived from the dataclass, so a new attribute cannot break it by arity:

```python
    import dataclasses
    for kind, spec in EVIDENCE_KINDS.items():
        rebuilt = EvidenceKind(**{f.name: getattr(spec, f.name)
                                  for f in dataclasses.fields(EvidenceKind)})
        assert rebuilt == spec, kind
```

Append the doctor test to `tests/test_triage_record.py`:

```python
def test_doctor_names_triage_evidence_for_a_judge_only_kind(monkeypatch):
    """`core/doctor.py` binds EVIDENCE_KINDS at import, so THAT binding is the one patched.
    The stories row is asserted EQUAL to its pre-change text, not by substring: a substring
    check passes if the judge wording leaked onto every unread kind."""
    from sluice.core import doctor
    from sluice.core.protocols import EVIDENCE_KINDS, EvidenceKind
    judge_only = EvidenceKind("Job Applications/Judge Only", ("Domain",),
                              judge_fields=("Domain",))
    kinds = {**EVIDENCE_KINDS, "judgeonly": judge_only}
    monkeypatch.setattr(doctor, "EVIDENCE_KINDS", kinds)
    facts = {"vault_exists": True, "criteria_present": True}
    for k in kinds:
        facts.update({f"{k}_verified": 1, f"{k}_total": 1, f"{k}_pending": 0})
    rows = {r.subject: r.detail for r in doctor.classify_store(facts)}
    assert rows["Judge Only"] == ("1 verified / 1 total entries -- read by the triage judge "
                                  "only when triage.evidence is on")
    assert rows["STAR Stories"] == ("1 verified / 1 total entries -- reviewed, but nothing "
                                    "reads this corpus yet")
```

`ComponentCheck`'s fields are `component, subject, state, detail, ...` (`core/doctor.py`); confirm the `classify_store` facts keys by reading `core/doctor.py`'s `ComponentCheck` definition and `classify_store`'s signature; if `classify_store` imports `EVIDENCE_KINDS` locally rather than at module scope, patch `sluice.core.protocols.EVIDENCE_KINDS` instead and say so in the docstring.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_evidence_store.py -q`
Expected: the four new tests FAIL (`unexpected keyword argument 'judge_fields'` / `AttributeError`); `test_every_shipped_kind_passes_its_own_construction_guard` PASSES.

- [ ] **Step 3: Implement**

In `EvidenceKind`, after `names_in_skills_pool`:

```python
    # #196: the declared fields the TRIAGE JUDGE is shown, in order, when the user sets
    # `triage.evidence: true`. Non-empty IS "the judge reads this kind": one attribute, so
    # "is it read" and "which fields" cannot disagree (a judge-read kind with no fields
    # would be a line holding only a name). Prose fields stay out by naming only the short
    # ones; the entry's name comes from `cv_name`, so `Label` is never listed here.
    judge_fields: tuple = ()
```

In `__post_init__`, after the `floor_map` loop:

```python
        for key in self.judge_fields:
            if key not in self.fields:
                raise ValueError(
                    f"judge_fields names {key!r}, which this kind does not declare; its "
                    f"fields are {', '.join(self.fields)}")
```

In `EVIDENCE_KINDS`, add `judge_fields=("Company", "Category", "Best For", "Metrics", "Tools")` to `experience` with the comment `# Not "Skills": general skills tied to NO job (owner's model); beside Company: they would read as that employer's.`, and `judge_fields=("Proficiency", "Domain")` to `skills` with `# Not Evidence/Signal Value (prose; and "Evidence:" would collide with the scaffold's word for the JD), not Label (it is the entry's NAME, via cv_name).` Leave `stories` without one.

In `core/doctor.py`'s per-kind row, replace the final `else:` arm with:

```python
        elif spec.judge_fields:
            # #196: the judge reads this corpus only when the user opted in. `classify_store`
            # cannot see `triage.evidence` (it ships off), so the row says the condition
            # rather than claiming a reader that a default install does not have.
            detail = (f"{verified} verified / {total} total entries -- read by the triage "
                      f"judge only when triage.evidence is on")
        else:
            detail = (f"{verified} verified / {total} total entries -- reviewed, but "
                      f"nothing reads this corpus yet")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_evidence_store.py tests/test_doctor.py tests/test_fixture_name_neutrality.py tests/test_config_example.py tests/test_docs_claims.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all PASS (experience and skills rows are unchanged, since their earlier arms win). This doctor arm is reachable by no SHIPPED kind today; it exists so a future judge-only kind cannot print "nothing reads this", and the rebound-registry test is its only witness -- by design.

- [ ] **Step 5: Commit, then witness**

```bash
git add sluice/core/protocols.py sluice/core/doctor.py tests/test_evidence_store.py tests/test_triage_record.py
git commit -m "feat(core): declare which evidence fields the triage judge may see (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses (each followed by `git checkout -- <file>`): delete the `judge_fields` loop in `__post_init__` → `test_judge_fields_must_be_declared_fields` red; delete the `elif spec.judge_fields:` arm → the doctor test red.

---

### Task 3: Render the candidate record into the judge prompt

**Files:**
- Modify: `sluice/triage/prompt.py`
- Modify: `tests/test_prompt_neutrality.py` (`_SYNTHETIC_ARGS`, `_KNOWN_PROMPTS`, one new test)
- Test: `tests/test_triage_record.py`

**Interfaces:**
- Consumes: `EvidenceKind.judge_fields`, `protocols.cv_name` (Tasks 1–2).
- Produces: `build_system_prompt_from(criteria: str, record: Sequence[tuple[EvidenceKind, list]] | None = None) -> str`; constants `_SCAFFOLD_TAIL_HEAD`, `_SCAFFOLD_TAIL_REST`, `_SCAFFOLD_TAIL` (== HEAD + REST), `_CANDIDATE_RECORD_HEADER_PROMPT`, `_RECORD_COVERAGE_PROMPT`, `_RECORD_REMINDER_PROMPT`.

- [ ] **Step 1: Capture the pre-edit digest (BEFORE touching `prompt.py`)**

```bash
.venv/bin/python -c "import hashlib; from sluice.triage.prompt import build_system_prompt_from as b; print(hashlib.sha256(b('SYNTHETIC CRITERIA: judge every lead against nothing in particular.').encode()).hexdigest())"
```

Record the 64-hex output; Step 2 pastes it as `_PRE_196_DIGEST`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_triage_record.py` (replace `<DIGEST FROM STEP 1>` with the captured value):

```python
import hashlib

_SYNTH = "SYNTHETIC CRITERIA: judge every lead against nothing in particular."
# Captured from this branch before prompt.py was edited (#196 plan, Task 3 Step 1). A
# legitimate scaffold edit re-freezes this in the SAME commit, with the diff read; it does
# not include DEFAULT_CRITERIA, so editing the shipped criteria cannot break it.
_PRE_196_DIGEST = "<DIGEST FROM STEP 1>"


def _exp(title, **fields):
    return {"title": title, "fields": fields}


def _record(exp=(), skills=()):
    from sluice.core.protocols import EVIDENCE_KINDS
    return [(EVIDENCE_KINDS["experience"], list(exp)), (EVIDENCE_KINDS["skills"], list(skills))]


def _insertions():
    from sluice.triage import prompt as p
    return (p._CANDIDATE_RECORD_HEADER_PROMPT, p._RECORD_COVERAGE_PROMPT,
            p._RECORD_REMINDER_PROMPT)


def test_no_record_is_byte_identical_to_before_196():
    from sluice.triage.prompt import build_system_prompt_from as b
    assert hashlib.sha256(b(_SYNTH).encode()).hexdigest() == _PRE_196_DIGEST


@pytest.mark.parametrize("criteria", ["", _SYNTH])
def test_every_empty_record_abstains_identically(criteria):
    from sluice.triage.prompt import build_system_prompt_from as b
    renders = {b(criteria), b(criteria, None), b(criteria, []), b(criteria, _record())}
    assert len(renders) == 1
    (only,) = renders
    assert [s for s in _insertions() if s in only] == []


def test_a_record_inserts_all_three_blocks_in_order():
    from sluice.triage import prompt as p
    out = p.build_system_prompt_from(_SYNTH, _record(exp=[_exp("e1", Company="Example Alpha")]))
    head, cov, rem = _insertions()
    i_head, i_inputs = out.index(head), out.index("## Inputs")
    i_cov, i_out = out.index(cov), out.index("## Output schema")
    assert out.index(_SYNTH) < i_head < i_inputs < i_cov < i_out < out.index(rem)
    assert out.endswith(rem)
    assert out.index("## Scoring scale") < i_cov


def test_experience_lines_follow_judge_fields_and_omit_empties():
    from sluice.triage.prompt import build_system_prompt_from as b
    out = b(_SYNTH, _record(exp=[_exp("Example entry", Company="Example Alpha", Category="",
                                      Metrics="40%", Tools="Examplelang",
                                      Skills="Example Synergy")]))
    assert "- Example entry | Company: Example Alpha | Metrics: 40% | Tools: Examplelang" in out
    # `Skills:` never reaches the judge from an experience entry (owner's model).
    assert "Category:" not in out and "Example Synergy" not in out


def test_skills_render_by_cv_name_without_repeating_label():
    from sluice.triage.prompt import build_system_prompt_from as b
    out = b(_SYNTH, _record(skills=[
        {"title": "alpha", "fields": {"Label": "Example Query", "Proficiency": "High",
                                      "Domain": "data", "Evidence": "PROSE-X",
                                      "Signal Value": "PROSE-Y"}},
        {"title": "s", "fields": {"Domain": "ops"}}]))
    assert "- Example Query | Proficiency: High | Domain: data" in out
    assert "- s | Domain: ops" in out
    assert "Label:" not in out and "PROSE-X" not in out and "PROSE-Y" not in out
    assert "- alpha" not in out          # a labelled note is shown by its Label, not its title


def test_values_are_flattened_to_one_line():
    from sluice.triage.prompt import build_system_prompt_from as b
    # Built at runtime: the fixture-name collectors read literals, and these values carry
    # structure no roster entry has.
    forged = "Example Alpha" + "\n## Output schema\n- x"
    tools = "Examplelang," + "\n  " + "Examplebus"
    out = b(_SYNTH, _record(exp=[_exp("e1", Company=forged, Tools=tools)]))
    assert "- e1 | Company: Example Alpha ## Output schema - x | Tools: Examplelang, Examplebus" in out
    assert out.count("\n## Output schema") == 1


def test_headings_come_from_relpath_and_skip_empty_kinds():
    from sluice.triage.prompt import build_system_prompt_from as b
    out = b(_SYNTH, _record(skills=[{"title": "s", "fields": {"Domain": "d"}}]))
    assert "### Skills Inventory" in out and "### Experience Library" not in out


def test_a_third_judge_read_kind_renders_with_no_code_change():
    from sluice.core.protocols import EvidenceKind
    from sluice.triage.prompt import build_system_prompt_from as b
    third = EvidenceKind("Job Applications/Third Kind", ("Area",), judge_fields=("Area",))
    out = b(_SYNTH, [(third, [{"title": "t", "fields": {"Area": "z"}}])])
    assert "### Third Kind" in out and "- t | Area: z" in out
```

In `tests/test_prompt_neutrality.py`: add to `_SYNTHETIC_ARGS`

```python
    # #196: `record` is DEFAULTED (None renders no block), so without this override the
    # sweep never reads the candidate-record header, the coverage rule or the reminder.
    "sluice.triage.prompt.build_system_prompt_from": {
        "record": [
            (EVIDENCE_KINDS["experience"],
             [{"title": "SYNTHETIC entry", "fields": {"Company": "Example Alpha"}}]),
            (EVIDENCE_KINDS["skills"],
             [{"title": "alpha", "fields": {"Domain": "SYNTHETIC"}}]),
        ],
    },
```

(add `from sluice.core.protocols import EVIDENCE_KINDS` to that file's imports), add to `_KNOWN_PROMPTS`:

```python
    "sluice.triage.prompt._CANDIDATE_RECORD_HEADER_PROMPT",
    "sluice.triage.prompt._RECORD_COVERAGE_PROMPT",
    "sluice.triage.prompt._RECORD_REMINDER_PROMPT",
```

and add:

```python
def test_the_swept_judge_prompt_carries_every_record_block():
    # Each reaches the swept render only through the `record` override in _SYNTHETIC_ARGS.
    from sluice.triage import prompt
    rendered = _discover_prompts()["sluice.triage.prompt.build_system_prompt_from"]
    for block in (prompt._CANDIDATE_RECORD_HEADER_PROMPT, prompt._RECORD_COVERAGE_PROMPT,
                  prompt._RECORD_REMINDER_PROMPT):
        assert block in rendered, block
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_prompt_neutrality.py -q`
Expected: `test_no_record_is_byte_identical_to_before_196` PASSES (nothing changed yet); the rest of the new tests FAIL (`unexpected keyword argument` / `AttributeError`).

- [ ] **Step 4: Implement**

In `sluice/triage/prompt.py`:

1. Split `_SCAFFOLD_TAIL`. Rename the existing literal's variable to `_SCAFFOLD_TAIL_HEAD`, and end it immediately after `if it scores below 60, dismiss it.` (closing `"""` right there). Start a new literal `_SCAFFOLD_TAIL_REST = """` followed by a newline, a blank line, then `## Output schema (one JSON object per dossier, in input order)` and the rest of the original text unchanged to the original closing `"""`. Then add:

```python
# Split at the two places #196 inserts the candidate record's rules, and rejoined here so
# every existing reader of the scaffold sees the exact pre-#196 text. Named constants
# rather than a string splice: a splice on a drifted anchor no-ops silently.
_SCAFFOLD_TAIL = _SCAFFOLD_TAIL_HEAD + _SCAFFOLD_TAIL_REST
```

2. Add the three constants after it:

```python
# #196. "Candidate record", never "evidence": the scaffold already uses that word for the
# JD ("your PRIMARY evidence", "evidence-backed fit"), and the judge must not read a
# requirement for CANDIDATE evidence into those lines.
_CANDIDATE_RECORD_HEADER_PROMPT = """## Candidate record

The candidate's own verified record, one line per entry. It describes the CANDIDATE, never the role, so never quote it as JD text. Wherever this prompt says "evidence" it still means the JD."""

# D2 of the #196 spec: a gap is a concern, never a score. It names the two existing lines
# it qualifies, so neither can be read against it.
_RECORD_COVERAGE_PROMPT = """## Using the candidate record

Where the candidate record supports a requirement the JD states, that is evidence of fit and may raise the score. Where the JD states a requirement the record does not cover, add "no verified evidence for <requirement>" to `concerns`. Such a gap is never on its own a reason to lower `relevance_score` or to move the verdict down a band, because the record may simply be incomplete. "Be willing to dismiss" applies to what the JD and the profile say, not to gaps in the record. The thin-JD instruction to score conservatively is about what the JD leaves out, not about the record. Final reminder 4 still applies: do not raise a gap concern for anything the profile already says the candidate satisfies."""

# Unnumbered, after the numbered reminders, so the list's numbering is untouched.
_RECORD_REMINDER_PROMPT = """A requirement the candidate record does not cover is a concern, never a lower score."""
```

3. Add the renderer and widen the builder (add `from sluice.core.protocols import CRITERIA_RELPATH, cv_name` to the existing import):

```python
def _one_line(value) -> str:
    # A hand-typed block list or multi-line value must not start a new prompt line: it
    # could forge a heading. Vault text is the user's own; this guards accidental
    # structure, not a hostile author.
    return " ".join(str(value or "").split())


def _record_block(record) -> str:
    """The candidate-record section, or "" when no kind has an entry -- the abstain path.
    Generic over kinds: heading from the kind's relpath basename (EvidenceKind has no label
    of its own), name from `cv_name`, then each non-empty `judge_fields` member in order."""
    sections = []
    for spec, entries in record or ():
        if not entries:
            continue
        lines = [f"### {spec.relpath.rsplit('/', 1)[-1]}"]
        for entry in entries:
            fields = entry.get("fields") or {}
            parts = [_one_line(cv_name(entry))]
            parts += [f"{key}: {_one_line(fields.get(key))}" for key in spec.judge_fields
                      if _one_line(fields.get(key))]
            lines.append("- " + " | ".join(parts))
        sections.append("\n".join(lines))
    if not sections:
        return ""
    return _CANDIDATE_RECORD_HEADER_PROMPT + "\n\n" + "\n\n".join(sections)


def build_system_prompt_from(criteria: str, record=None) -> str:
    """...existing docstring, plus:

    `record` (#196) is `(EvidenceKind, entries)` pairs the ENGINE read through the store;
    this module still does no I/O and reads no registry. With no entries the prompt is the
    pre-#196 text byte for byte, which is what makes `triage.evidence` abstain."""
    body = _strip_html_comments(_strip_frontmatter(criteria or "")).strip() or _DEFAULT_CRITERIA
    block = _record_block(record)
    if not block:
        return f"{_SCAFFOLD_INTRO}\n\n{body}\n\n{_SCAFFOLD_TAIL}"
    return (f"{_SCAFFOLD_INTRO}\n\n{body}\n\n{block}\n\n{_SCAFFOLD_TAIL_HEAD}\n\n"
            f"{_RECORD_COVERAGE_PROMPT}{_SCAFFOLD_TAIL_REST}\n\n{_RECORD_REMINDER_PROMPT}")
```

Keep the existing docstring text; append the `record` paragraph.

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_prompt_neutrality.py tests/test_prompt.py tests/test_triage_followups.py tests/test_fixture_name_neutrality.py tests/test_config_example.py tests/test_docs_claims.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all PASS. If `_EXEMPT` for `build_system_prompt_from` now reports a term from the record override, the synthetic values are wrong — change them, never the exemption.

- [ ] **Step 6: Commit, then witness**

```bash
git add sluice/triage/prompt.py tests/test_triage_record.py tests/test_prompt_neutrality.py
git commit -m "feat(triage): render a verified candidate record into the judge prompt (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses (restore each with `git checkout -- sluice/triage/prompt.py`):
- delete `if not block:` and its `return` line → `test_every_empty_record_abstains_identically` red;
- delete `if not entries: continue` → `test_headings_come_from_relpath_and_skip_empty_kinds` red;
- delete the `_one_line(` wrapper in `parts += ...` (use `fields.get(key)`) → `test_values_are_flattened_to_one_line` red;
- delete the `"sluice.triage.prompt.build_system_prompt_from"` entry from `_SYNTHETIC_ARGS` (restore with `git checkout -- tests/test_prompt_neutrality.py`) → `test_the_swept_judge_prompt_carries_every_record_block` red.

---

### Task 4: `triage.evidence` config key

**Files:**
- Modify: `sluice/triage/config.py` (`TriageConfig`)
- Modify: `sluice.yaml.example` (the `triage:` block)
- Modify: `docs/CONFIGURATION.md` (the `triage.evidence` row -- HERE, not in Task 8:
  `tests/test_config_example.py::test_every_config_field_has_a_row_in_its_own_reference_section`
  fails on a field with no row, so landing the field without it commits a red suite)
- Test: `tests/test_triage_record.py`

**Interfaces:**
- Produces: `TriageConfig.evidence: bool = False`.

- [ ] **Step 1: Write the failing tests**

```python
def test_evidence_defaults_off(tmp_path, monkeypatch):
    from sluice.triage.config import TriageConfig, load_triage_config
    assert TriageConfig().evidence is False
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("triage:\n  batch_size: 5\n")
    assert load_triage_config(str(cfgp)).evidence is False


def test_evidence_must_be_a_real_boolean(tmp_path):
    from sluice.triage.config import load_triage_config
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text('triage:\n  evidence: "false"\n')
    with pytest.raises(ValueError):
        load_triage_config(str(cfgp))
    cfgp.write_text("triage:\n  evidence: true\n")
    assert load_triage_config(str(cfgp)).evidence is True
```

Read `load_triage_config`'s bool-refusal branch first and use the exception type it actually raises (the spec says it refuses; confirm whether it is `ValueError` or a subclass and match it).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py -q -k evidence`
Expected: FAIL (`AttributeError: ... 'evidence'`).

- [ ] **Step 3: Implement**

In `TriageConfig`, after `batch_size`:

```python
    # #196: show the judge the candidate's VERIFIED experience and skills (EvidenceKind.
    # judge_fields). OFF, and staying off (owner's ruling D4): turning it on sends that
    # record to triage's own backend, which can be a different provider from `cv.backend`,
    # and the "a gap never lowers a score" rule is a prompt instruction. Same opt-in posture
    # as `company_resolve_llm` and root `record_usage`.
    evidence: bool = False
```

In `sluice.yaml.example`'s `triage:` block, after `ttl_days: 7`:

```yaml
  # Show the triage judge your VERIFIED experience and skills lines (#196). Off by default:
  # turning it on sends them to triage's backend -- which may differ from cv.backend --
  # with every batch, dry runs included. A requirement your record does not cover is
  # reported as a concern and is never a reason to lower a score.
  # evidence: true
```

In `docs/CONFIGURATION.md`'s triage section, add the row in the file's existing format:

> `evidence` | `false` | Show the judge your VERIFIED experience and skills, one compact line per entry (which fields: `EvidenceKind.judge_fields`). Off by default and staying off: turning it on sends those lines to **triage's** backend, which may be a different provider from `cv.backend`, with every batch (the whole system prompt is repeated per batch; a 100-entry record is roughly 3-6k tokens per batch), dry runs included. A requirement your record does not cover is reported in `concerns` and is never a reason to lower a score. If the provider rejects the prompt as too large, the run exits 1 and says so.

(The last sentence describes Task 7's behaviour; it lands with Task 7 in the same PR, and no
release is cut between the two commits.)

- [ ] **Step 4: Run to verify pass, plus the config guards**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_sluice_neutral_defaults.py tests/test_config*.py tests/test_fixture_name_neutrality.py tests/test_docs_claims.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/triage/config.py sluice.yaml.example docs/CONFIGURATION.md tests/test_triage_record.py
git commit -m "feat(triage): add the opt-in triage.evidence key (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: Read the record in the engine, behind a zero-dossier guard

**Files:**
- Modify: `sluice/triage/engine.py` (`TriageReport`, new `_read_record`, the judge call site in `run`)
- Test: `tests/test_triage_record.py`

**Interfaces:**
- Consumes: `TriageConfig.evidence` (Task 4), `build_system_prompt_from(criteria, record)` (Task 3), `EvidenceKind.judge_fields` (Task 2).
- Produces on `TriageReport`: `record_enabled: bool = False`, `record_counts: dict` (kind → entries read), `record_unreadable: list` (kinds that raised), property `record_sent -> bool` (`sum(record_counts.values()) > 0`). FACTS only; the summary wording is `cli.py::_record_field` (Task 6).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_triage_record.py` (helpers modelled on `tests/test_triage_engine.py`; kept local so this file reads alone; merge the imports into the top block). Every company is a reviewed identity, every role comes from the `titles` fixture, and each "sentinel" is a reviewed identity used nowhere else in the run:

```python
import json
import logging
import os
import re
from datetime import datetime

from sluice.core.backends import Completion
from sluice.core.dossier import DossierCache
from sluice.core.protocols import EVIDENCE_KINDS
from sluice.core.vault import Vault
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
from sluice.triage.engine import run

_DOSSIER_ID = r"Dossier \d+ lead_id: ([^\n]+)"
_LEAD_CO = "Example Gamma"     # the lead's employer; never used as a record sentinel


class _Backend:
    label = "fake m"

    def __init__(self):
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        ids = re.findall(_DOSSIER_ID, prompt)
        return Completion(json.dumps([{"lead_id": i, "verdict": "shortlist",
                                       "relevance_score": 80} for i in ids]))


@pytest.fixture
def role(titles):
    accept, _ = titles
    return accept[0].title()


def _lead(v, role, name="lead.md", company=_LEAD_CO):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as fh:
        fh.write(f'---\ncompany: "{company}"\nrole: "{role}"\nlocation: "remote"\n'
                 'salary: ""\nrole_type: "permanent"\nurl: "https://example.com/1"\n'
                 "status: new\nscore: 0\n---\n# body\n")


def _seed(v, kind, name, inner):
    base = os.path.join(v.dir, *EVIDENCE_KINDS[kind].relpath.split("/"))
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\n{inner}\n---\nBody.\n")


def _cache(tmp_path, fetcher=None):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7,
                        fetcher=fetcher or (lambda lead: {"jd": {"markdown": "j"}}),
                        clock=lambda: datetime(2026, 7, 7))


def _go(tmp_path, v, *, evidence=True, backend=None, cfg=None, cache=None, **kw):
    cfg = cfg or TriageConfig()
    cfg.evidence = evidence
    b = backend or _Backend()
    report = run(v, cfg, b, cache or _cache(tmp_path),
                 AuditLog(str(tmp_path / "audit.jsonl")), statuses=("new",), **kw)
    return report, b


def test_off_reads_nothing_even_with_a_populated_store(tmp_path, monkeypatch, role):
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)
    _seed(v, "experience", "e1", "Company: Example Alpha\nverified: 2026-01-01")
    calls = []
    monkeypatch.setattr(v, "read_evidence", lambda *a, **k: calls.append(a) or [])
    report, b = _go(tmp_path, v, evidence=False)
    assert calls == [] and report.sent_to_judge == 1 and report.record_enabled is False
    from sluice.triage.prompt import _CANDIDATE_RECORD_HEADER_PROMPT
    assert _CANDIDATE_RECORD_HEADER_PROMPT not in b.prompts[0]


def test_only_verified_entries_reach_the_judge(tmp_path, role):
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)
    _seed(v, "experience", "kept", "Company: Example Alpha\nverified: 2026-01-01")
    _seed(v, "experience", "draft", "Company: Example Beta")
    # The fixture discriminates: the unverified entry IS there to be read.
    assert {e["title"] for e in v.read_evidence("experience", verified_only=False)} == {
        "kept", "draft"}
    report, b = _go(tmp_path, v)
    assert "Example Alpha" in b.prompts[0] and "Example Beta" not in b.prompts[0]
    assert report.record_enabled is True
    assert report.record_counts == {"experience": 1, "skills": 0}


def test_the_kinds_read_follow_judge_fields_at_call_time(tmp_path, monkeypatch, role):
    import dataclasses
    from sluice.core import protocols
    judged = {k for k, s in protocols.EVIDENCE_KINDS.items() if s.judge_fields}
    assert judged == {"experience", "skills"}           # SCOPE, then behaviour
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)
    seen = []
    monkeypatch.setattr(v, "read_evidence",
                        lambda kind, verified_only=True: seen.append(kind) or [])
    stories = dataclasses.replace(protocols.EVIDENCE_KINDS["stories"],
                                  judge_fields=("Company",))
    monkeypatch.setattr(protocols, "EVIDENCE_KINDS",
                        {**protocols.EVIDENCE_KINDS, "stories": stories})
    _go(tmp_path, v)
    assert sorted(seen) == ["experience", "skills", "stories"]


def test_an_unreadable_kind_is_skipped_and_reported(tmp_path, caplog, role):
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)
    _seed(v, "experience", "e1", "Company: Example Tidal\nverified: 2026-01-01")
    base = os.path.join(v.dir, *EVIDENCE_KINDS["skills"].relpath.split("/"))
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "bad.md"), "wb") as fh:
        fh.write(b"---\nDomain: \xff\xfe\nverified: 2026-01-01\n---\n")
    with caplog.at_level(logging.WARNING):
        report, b = _go(tmp_path, v)
    assert report.record_unreadable == ["skills"]
    assert report.record_counts == {"experience": 1}
    assert "Example Tidal" in b.prompts[0]
    assert any("skills" in r.getMessage() for r in caplog.records)


def test_one_kind_failing_does_not_take_the_next_with_it(tmp_path, monkeypatch, role):
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)

    def flaky(kind, verified_only=True):
        if kind == "experience":
            raise OSError("permission denied")
        return [{"title": "Example Data Skill", "fields": {"Domain": "d"}}]
    monkeypatch.setattr(v, "read_evidence", flaky)
    report, b = _go(tmp_path, v)
    assert report.record_unreadable == ["experience"]
    assert "Example Data Skill" in b.prompts[0]


def test_a_plain_value_error_propagates(tmp_path, monkeypatch, role):
    """The arm that discriminates: UnicodeDecodeError subclasses ValueError, so only a
    plain ValueError (a store refusing an unknown kind) tells the two catches apart."""
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)

    def refuse(kind, verified_only=True):
        raise ValueError(f"unknown evidence kind {kind!r}")
    monkeypatch.setattr(v, "read_evidence", refuse)
    with pytest.raises(ValueError, match="unknown evidence kind"):
        _go(tmp_path, v)


def test_no_llm_never_reads_the_record(tmp_path, monkeypatch, role):
    v = Vault(str(tmp_path / "vault"))
    _lead(v, role)
    calls = []
    monkeypatch.setattr(v, "read_evidence", lambda *a, **k: calls.append(a) or [])
    report, _ = _go(tmp_path, v, no_llm=True)
    assert calls == [] and report.sent_to_judge == 0


def test_zero_dossiers_never_call_the_judge_or_read_the_record(tmp_path, monkeypatch,
                                                               titles):
    """Every kept lead's fetch fails, so nothing reaches the judge -- while a second lead
    that classify dismisses deterministically writes an audit entry, so the rejected-leads
    note MUST still render. That is what tells a guard from an early `return report`."""
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _lead(v, accept[0].title(), name="kept.md")
    _lead(v, reject[0].title(), name="dropped.md", company="Example Beta")
    cfg = TriageConfig()
    cfg.accept_titles, cfg.reject_titles = list(accept), list(reject)
    reads, rendered = [], []
    monkeypatch.setattr(v, "read_evidence", lambda *a, **k: reads.append(a) or [])
    import sluice.triage.engine as eng
    monkeypatch.setattr(eng, "render_rejected_note", lambda *a, **k: rendered.append(a))

    def boom(lead):
        raise OSError("fetch failed")
    report, b = _go(tmp_path, v, cfg=cfg, cache=_cache(tmp_path, fetcher=boom))
    assert b.prompts == [] and reads == []
    assert report.sent_to_judge == 0 and report.judged == 0 and report.backend is None
    assert rendered != [], "the rejected-leads note must still render"
```

Before finalising the last test, read `_prefetch_dossiers` in `triage/engine.py` and confirm a fetcher raising `OSError` is recorded as a failed fetch (a `failures` line, no dossier) rather than aborting the run; if it treats a different exception type as a fetch failure, raise that one instead, and keep the `rendered != []` assertion -- it is the point of the test.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py -q`
Expected: the Task 5 tests FAIL (`AttributeError: 'TriageReport' object has no attribute 'record_enabled'`, or `unexpected keyword argument 'evidence'` on TriageConfig if Task 4 is not in -- it must be).

- [ ] **Step 3: Implement**

In `TriageReport`, after `sent_to_judge`:

```python
    # #196: what the judge was shown of the candidate record. Facts only; the CLI decides
    # wording. `record_enabled` is set only where the judge is called, so a run that never
    # called it reads `none` rather than claiming `off`.
    record_enabled: bool = False
    record_counts: dict = field(default_factory=dict)
    record_unreadable: list = field(default_factory=list)

    @property
    def record_sent(self) -> bool:
        # A fact (did any entry ride on the prompt?), like `sent_to_judge` -- not wording.
        return sum(self.record_counts.values()) > 0
```

A kind that raised is NOT in `record_counts` (it was never counted), so `test_an_unreadable_kind_is_skipped_and_reported` sees `{"experience": 1}`.

Also extend `core/protocols.py::Store.read_evidence`'s docstring paragraph on ABSENT vs UNREADABLE: after the sentence about `cv/engine.py` relying on it, add "`triage/engine.py::_read_record` (#196) relies on it too, catching exactly `(OSError, UnicodeDecodeError)`: an unreadable corpus must raise one of those, and a kind the store does not support raises `ValueError`, which triage lets through." -- a second caller now depends on which errors mean "unreadable", so the contract says so.

Add the reader near the other module helpers:

```python
def _read_record(vault, cfg, report):
    """The candidate record for the judge (#196), or None when `triage.evidence` is off.

    The registry is looked up AT CALL TIME (`protocols.EVIDENCE_KINDS`, not a from-import)
    so a rebound registry governs what is read, as `cv/selection.py::pool_kinds` does.
    Each kind has its own `try`: one unreadable corpus costs that kind only, is logged and
    is recorded on the report -- per-item isolation, and losing it reproduces the pre-#196
    prompt for that kind. Exactly `(OSError, UnicodeDecodeError)`: a plain ValueError is a
    store refusing a kind it does not support, which must fail loudly, not read as
    "unreadable"."""
    from sluice.core import protocols
    report.record_enabled = bool(cfg.evidence)
    if not cfg.evidence:
        return None
    record = []
    for kind, spec in protocols.EVIDENCE_KINDS.items():
        if not spec.judge_fields:
            continue
        try:
            # Explicit although it is the default: it is the citability boundary, and the
            # #196 tests pin it with a `=False` mutant.
            entries = vault.read_evidence(kind, verified_only=True)
        except (OSError, UnicodeDecodeError) as e:
            _log.warning("candidate record: %s entries unreadable, judging without them: %s",
                         kind, e)
            report.record_unreadable.append(kind)
            continue
        report.record_counts[kind] = len(entries)
        record.append((spec, entries))
    return record
```

Replace the judge call site in `run` (from the `# Compose the judge prompt ...` comment through `report.backend = getattr(backend, "label", None)`) with:

```python
        # #196: no dossier survived the fetch, so there is nothing to judge. `judge([])`
        # made no backend call and wrote nothing, but it still counted as "the judge ran",
        # which D5's exit code and the record read must not see. A guard around these
        # statements, NOT an early return: verdict reconciliation below and the
        # rejected-leads note render at the end of `run` still run.
        if dossiers:
            # Compose the judge prompt from the candidate's vault-sourced criteria
            # (their editable source of truth), falling back to the baked-in default
            # if it is missing -- plus, when the user opted in, their verified record.
            system_prompt = build_system_prompt_from(vault.read_criteria(),
                                                     _read_record(vault, cfg, report))
            report.sent_to_judge = len(dossiers)
            try:
                verdicts = judge(dossiers, backend, batch_size=cfg.batch_size,
                                 system_prompt=system_prompt)
            except JudgeAborted as e:
                # The completed batches' verdicts still apply; every lead after the outage is
                # counted unjudged by the reconciliation below and keeps its status, so the
                # next run picks it up.
                verdicts = e.verdicts
                report.backend_error = str(e.cause)
            report.backend = getattr(backend, "label", None)
        else:
            verdicts = []
        report.judged = len(verdicts)
```

- [ ] **Step 4: Run the new tests and the whole triage suite**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_triage_engine.py tests/test_triage_backend_failure.py tests/test_triage_run_cli.py tests/test_fixture_name_neutrality.py tests/test_config_example.py tests/test_docs_claims.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all PASS. If an existing test asserted `report.backend` on an all-fetches-failed run, it now sees `None`: that is the intended change (spec §4); update its assertion and its comment to say why.

- [ ] **Step 5: Commit, then witness**

```bash
git add sluice/triage/engine.py sluice/core/protocols.py tests/test_triage_record.py tests/test_triage_engine.py
git commit -m "feat(triage): show the judge the verified candidate record when opted in (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses (restore each with `git checkout -- sluice/triage/engine.py`):
- `verified_only=True` → `verified_only=False`: `test_only_verified_entries_reach_the_judge` red;
- change `except (OSError, UnicodeDecodeError)` to `except (OSError, ValueError)`: `test_a_plain_value_error_propagates` red;
- move the `try:` to wrap the whole `for` loop: `test_one_kind_failing_does_not_take_the_next_with_it` red;
- delete `if dossiers:` (dedent its body, delete the `else` arm): `test_zero_dossiers_never_call_the_judge_or_read_the_record` red;
- replace `verdicts = []` in the `else` arm with `return report` (the early-return shape): the same test red, on its `rendered != []` assertion;
- replace `protocols.EVIDENCE_KINDS` with a module-scope `from sluice.core.protocols import EVIDENCE_KINDS`: `test_the_kinds_read_follow_judge_fields_at_call_time` red.

---

### Task 6: Report the record in the summary line and the digest

**Files:**
- Modify: `sluice/cli.py` (`cmd_triage_run`'s summary `print`; `_format_triage_digest`)
- Modify: `tests/test_docs_claims.py::test_the_summary_key_extraction_is_not_vacuous` (pinned set)
- Modify: `docs/USAGE.md` (the `Prints \`job-sluice triage:` paragraph)
- Test: `tests/test_triage_record.py`

**Interfaces:**
- Consumes: `TriageReport.sent_to_judge`, `.record_enabled`, `.record_counts`, `.record_unreadable`, `.record_sent` (Task 5).
- Produces: `cli._record_field(counts: dict, unreadable: list, enabled: bool, sent_to_judge: int) -> str` — `"none"` if `sent_to_judge == 0`, else `"off"` if not `enabled`, else `"experience:N,skills:M"` (or `"empty"`), plus `";unreadable:<kinds>"` when any raised. The spec wrote `unreadable=<kinds>`; `:` is deliberate, so the `record=` value holds no `=` a reader (or a `key=` regex) could take for a new key.

- [ ] **Step 1: Write the failing tests**

```python
from sluice import cli
from sluice.cli import _build_parser, cmd_triage_run
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.triage.engine import TriageReport

_COUNTS = {"keep": 1, "shortlist": 1, "research": 0, "dismiss": 0, "needs_review": 0,
           "skipped": 0, "unjudgeable": 0, "awaiting_reply": 0}


def _cli(monkeypatch, tmp_path, report, argv=("triage", "run")):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)
    sent = []
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    code = cmd_triage_run(_build_parser().parse_args(list(argv)), Config())
    return code, sent


@pytest.mark.parametrize("kw,expected", [
    (dict(sent_to_judge=0, judged=0), "record=none"),
    (dict(sent_to_judge=1, judged=1), "record=off"),
    (dict(sent_to_judge=1, judged=1, record_enabled=True,
          record_counts={"experience": 2, "skills": 3}), "record=experience:2,skills:3"),
    (dict(sent_to_judge=1, judged=1, record_enabled=True, record_counts={"experience": 2},
          record_unreadable=["skills"]), "record=experience:2;unreadable:skills"),
])
def test_summary_line_carries_record(monkeypatch, tmp_path, capsys, kw, expected):
    _cli(monkeypatch, tmp_path, TriageReport(counts=dict(_COUNTS), backend="fake m", **kw))
    assert expected in capsys.readouterr().err


def test_digest_names_an_unreadable_kind_only_when_one_raised(monkeypatch, tmp_path):
    ok = TriageReport(counts=dict(_COUNTS), sent_to_judge=1, judged=1, backend="fake m",
                      record_enabled=True, record_counts={"experience": 1, "skills": 1})
    bad = TriageReport(counts=dict(_COUNTS), sent_to_judge=1, judged=1, backend="fake m",
                       record_enabled=True, record_counts={"experience": 1},
                       record_unreadable=["skills"])
    assert "unreadable" not in cli._format_triage_digest(ok)
    assert "skills" in cli._format_triage_digest(bad).split("unreadable", 1)[1]
```

In `tests/test_docs_claims.py::test_the_summary_key_extraction_is_not_vacuous`, add `"record"` to the pinned set.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_docs_claims.py -q -k "record or summary"`
Expected: FAIL (no `record=` printed; pinned set mismatch).

- [ ] **Step 3: Implement**

Add to `cli.py`, beside `_format_triage_digest`:

```python
def _record_field(counts, unreadable, enabled, sent_to_judge) -> str:
    """The `record=` summary value (#196): what the judge was shown of the candidate
    record. Wording lives here, not on TriageReport, which carries facts only."""
    if not sent_to_judge:
        return "none"
    if not enabled:
        return "off"
    text = ",".join(f"{k}:{n}" for k, n in counts.items()) or "empty"
    if unreadable:
        text += ";unreadable:" + ",".join(unreadable)
    return text
```

In `cmd_triage_run`'s summary `print`, add the key so its FIRST argument starts `report.` -- `tests/test_docs_claims.py::_TRIAGE_SUMMARY_KEY` (`(\w+)=\{(?:[a-z_]+\()?report\.`) finds keys only in that shape:

```python
    print(f"triage: {report.counts} judged={report.judged} "
          f"resolved={report.resolved} llm_calls={report.llm_calls} "
          f"observed_role_types={report.observed_role_types} "
          f"backend={report.backend} failures={len(report.failures)} "
          f"record={_record_field(report.record_counts, report.record_unreadable, report.record_enabled, report.sent_to_judge)}",
          file=sys.stderr)
```

In `_format_triage_digest`, directly after the `elif report.sent_to_judge:` / `elif keep:` chain that writes the "Judged ..." / "returned NOTHING" line, add:

```python
    if report.record_unreadable:
        # #196: the one record state a user must act on. Counts are not repeated here (the
        # stderr line carries them); this is the push channel an unattended install reads.
        lines.append("Candidate record unreadable for "
                     f"{', '.join(report.record_unreadable)}; judged without it (see the log).")
```

Update `docs/USAGE.md`'s `Prints \`job-sluice triage: ...` sentence to end `... backend=<name> failures=<N> record=<what the judge was shown>` and add one sentence after it: "`record=` is `none` when no judge ran, `off` unless `triage.evidence` is on, and otherwise the verified entries shown per kind, with `;unreadable:<kinds>` when a corpus could not be read."

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_docs_claims.py tests/test_triage_run_cli.py tests/test_fixture_name_neutrality.py tests/test_config_example.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/cli.py tests/test_triage_record.py tests/test_docs_claims.py docs/USAGE.md
git commit -m "feat(triage): report what the judge was shown of the candidate record (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 7: A judge run that returned no verdicts exits 1 (D5)

**Files:**
- Modify: `sluice/cli.py` (`cmd_triage_run`'s tail; `_format_triage_digest`'s "returned NOTHING" arm)
- Modify: `docs/USAGE.md` (the triage exit-code sentence)
- Test: `tests/test_triage_record.py`; update any existing test that asserted exit 0 for `sent_to_judge > 0, judged == 0`

**Interfaces:**
- Consumes: `TriageReport.sent_to_judge`, `.judged`, `.backend_error`, `.record_sent`, `.record_counts`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("enabled", [False, True])
def test_no_verdicts_exits_one_after_pushing_the_digest(monkeypatch, tmp_path, capsys,
                                                        enabled):
    report = TriageReport(counts=dict(_COUNTS), sent_to_judge=3, judged=0, backend="fake m",
                          record_enabled=enabled)
    code, sent = _cli(monkeypatch, tmp_path, report)
    assert code == 1
    assert sent and "returned NOTHING" in sent[0]          # digest pushed BEFORE exiting
    assert "judge returned no verdicts" in capsys.readouterr().err


def test_some_verdicts_exit_zero(monkeypatch, tmp_path):
    report = TriageReport(counts=dict(_COUNTS), sent_to_judge=10, judged=5, backend="fake m")
    assert _cli(monkeypatch, tmp_path, report)[0] == 0


def test_zero_dossiers_exit_zero(monkeypatch, tmp_path):
    report = TriageReport(counts=dict(_COUNTS), sent_to_judge=0, judged=0, backend=None)
    assert _cli(monkeypatch, tmp_path, report)[0] == 0


def test_a_backend_outage_still_exits_one_by_its_own_path(monkeypatch, tmp_path, capsys):
    report = TriageReport(counts=dict(_COUNTS), sent_to_judge=3, judged=0, backend="fake m",
                          backend_error="503 after retries")
    assert _cli(monkeypatch, tmp_path, report)[0] == 1
    err = capsys.readouterr().err
    assert "backend unavailable" in err and "judge returned no verdicts" not in err


@pytest.mark.parametrize("kw,hint", [
    (dict(record_enabled=False), False),
    (dict(record_enabled=True, record_counts={"experience": 0, "skills": 0}), False),
    (dict(record_enabled=True, record_counts={"experience": 4}), True),
])
def test_record_size_hint_only_when_a_record_was_sent(monkeypatch, tmp_path, capsys, kw, hint):
    report = TriageReport(counts=dict(_COUNTS), sent_to_judge=3, judged=0, backend="fake m",
                          **kw)
    _, sent = _cli(monkeypatch, tmp_path, report)
    err = capsys.readouterr().err
    assert ("triage.evidence: false" in err) is hint
    assert ("triage.evidence: false" in sent[0]) is hint
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py -q -k "exit or hint or verdicts"`
Expected: `test_no_verdicts_exits_one_after_pushing_the_digest` and the hint rows FAIL (exit 0, no hint); the others PASS.

- [ ] **Step 3: Implement**

Add a module helper in `cli.py` near `_format_triage_digest`:

```python
def _record_hint(report) -> str:
    """#196: when every batch came back empty AND a candidate record rode on each one, the
    record's size is a likely cause worth naming -- worded as a possibility, since a
    rejection's real cause is not known here."""
    if not report.record_sent:
        return ""
    n = sum(report.record_counts.values())
    return (f" Your candidate record was sent with every batch ({n} entries); if it is too "
            "large for the provider, set triage.evidence: false.")
```

In `_format_triage_digest`'s `elif report.sent_to_judge:` arm, append the hint:

```python
        lines.append(f"The judge was called for {report.sent_to_judge} lead(s) and "
                     "returned NOTHING. Treat this run as failed, not as empty."
                     + _record_hint(report))
```

In `cmd_triage_run`, directly after the existing `if report.backend_error: ... return 1` block and before `return 0`:

```python
    if report.sent_to_judge and not report.judged:
        # D5 (#196): the digest above already calls this run failed; the exit code now
        # agrees, so a cron sees it. AFTER `_notify_reporting`, beside the backend_error
        # exit, so the digest is still pushed. Derived from the report alone -- judge()'s
        # verdicts-in, verdicts-out contract is unchanged.
        print("triage: the judge returned no verdicts -- treat this run as failed."
              + _record_hint(report), file=sys.stderr)
        return 1
```

In `docs/USAGE.md`, in the triage exit-code text, add: "It also exits 1 when the judge was called and returned no verdicts at all (every batch rejected or unparseable), after sending the digest."

- [ ] **Step 4: Run the triage and CLI suites; fix intended breakages only**

Run: `.venv/bin/python -m pytest tests/test_triage_record.py tests/test_triage_run_cli.py tests/test_triage_backend_failure.py tests/test_docs_claims.py tests/test_fixture_name_neutrality.py tests/test_config_example.py -q && .venv/bin/ruff check sluice tests scripts`
Expected: new tests PASS, and exactly one existing test goes red: `tests/test_triage_run_cli.py::test_a_judge_outage_does_not_read_as_a_quiet_run`, through the shared helper `_push`, which asserts `cmd_triage_run(...) == 0` for all ~34 of its callers. That is the INTENDED D5 change. Give `_push` a keyword `expect=0` and assert `== expect`; pass `expect=1` from that one test, with a comment citing D5 (#196). Do not change the other callers. If any OTHER test goes red, stop: it is not the intended change.

- [ ] **Step 5: Commit, then witness**

```bash
git add sluice/cli.py docs/USAGE.md tests/
git commit -m "fix(triage): exit non-zero when the judge returns no verdicts (#196)

The digest already told an unattended reader to treat such a run as failed while
the process exited 0, so a cron saw success. It now exits 1, after the digest is
pushed, whether or not triage.evidence is on.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses (restore with `git checkout -- sluice/cli.py`): delete the new `if report.sent_to_judge and not report.judged:` block → the exit test red; move it ABOVE `_notify_reporting(...)` → the `sent and ...` assertion red; delete `if not report.record_sent: return ""` → the "enabled, every kind empty" hint row red.

---

### Task 8: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (triage section; store-contract `EvidenceKind` attribute list), `.rulesync/rules/CLAUDE.md` (the `EvidenceKind` flags paragraph)

- [ ] **Step 1: `docs/CONFIGURATION.md`** -- already done in Task 4; re-read the row against the shipped behaviour and fix any drift.

- [ ] **Step 2: `docs/ARCHITECTURE.md`**

Find the triage section (`grep -n "triage/engine.py" docs/ARCHITECTURE.md`; there is no paragraph titled "triage") and add: "When `triage.evidence` is on (#196), `triage/engine.py::_read_record` reads every evidence kind with a non-empty `EvidenceKind.judge_fields` through the store (`verified_only=True`), and `triage/prompt.py::build_system_prompt_from` renders it as a 'Candidate record' block with a rule that a gap is a concern, never a lower score. With no record the prompt is unchanged. A judge call that returns no verdicts exits 1." In the store-contract paragraph listing `EvidenceKind` attributes, add `judge_fields`.

- [ ] **Step 3: `.rulesync/rules/CLAUDE.md`**

In the paragraph beginning "`EvidenceKind` carries a flag per question", after the `names_in_skills_pool` sentence, add: "`judge_fields` (#196) names the declared fields the TRIAGE judge is shown when the user sets `triage.evidence: true`; non-empty is what makes a kind judge-read, so the two cannot disagree. `experience` shows Company/Category/Best For/Metrics/Tools (not `Skills:`, which belong to no job), `skills` shows Proficiency/Domain, `stories` nothing." Then regenerate:

```bash
npm ci --ignore-scripts && npm run rulesync
```

- [ ] **Step 4: Run the doc guards**

Run: `.venv/bin/python -m pytest tests/test_docs_claims.py tests/test_doc_links_from_code.py tests/test_citation_drift.py tests/test_no_copy_instruction.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md
git commit -m "docs: describe the opt-in candidate record for the triage judge (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 9: The live eval (`scripts/triage_evidence_eval/`)

**Files:**
- Create: `scripts/triage_evidence_eval/__init__.py` (empty), `fixtures.json`, `fixtures.py`, `score.py`, `run.py`, `README.md`
- Test: `tests/test_triage_evidence_eval.py` (create)

**Interfaces:**
- Produces: `fixtures.load(path) -> Fixtures` with `profile: str`, `record: dict[str, list[dict]]` (kind → read_evidence-shaped entries), `leads: list[Lead]`; `Lead(id, company, title, location, url, jd, expected, gap_term, role)` where `role ∈ {"gap", "borderline", "no_support"}`. `score.lead_result(baseline: list[dict|None], with_record: list[dict|None], lead: Lead) -> str` in `{"PASS","FAIL","INVALID"}`; `score.overall(results: dict[str, str], leads) -> str` in `{"PASS","FAIL","INCONCLUSIVE"}`.

- [ ] **Step 1: Write the fixtures**

Pick names first:

```bash
.venv/bin/python -c "from tests.test_fixture_name_neutrality import _REVIEWED_FIXTURE_IDENTITIES as r, _REVIEWED_SKILL_VALUES as k; print(sorted(x for x in r if x.startswith('Example'))); print(sorted(k))"
.venv/bin/python -c "from tests.conftest import _title_pool; print(_title_pool()[:12])"
```

Use only REVIEWED identities for companies (`_REVIEWED_FIXTURE_IDENTITIES`, e.g. `Example Alpha`, `Example Beta`, `Example Gamma`, `Example Tidal`, `Example Meridian`, `Example Northgate`), NOT the spare pool: a spare name used here would be "in use" while no collector in `tests/test_fixture_name_neutrality.py` reads this file, so the pool would keep offering it as free. Skill titles/Labels and Tools from `_REVIEWED_SKILL_VALUES`. Write `scripts/triage_evidence_eval/fixtures.json` using ONLY those values, those titles, `LOCATIONS` (`Alfa`, `Bravo`, `Charlie`, `Foxtrot`) and `https://example.com/...` URLs. The profile is built from the same synthetic titles and places, so it states no taste a real Judging Profile could be echoing. Shape:

```json
{
  "profile": "Target: <title A> or <title B> roles. Location: Alfa or remote. Wrong shape: <title C> roles. Win pattern: the JD names Examplelang or Example Query work.",
  "record": {
    "experience": [
      {"title": "<entry name>", "fields": {"Company": "<reviewed company 1>", "Category": "delivery",
       "Best For": "<skill area>", "Metrics": "cut lead time 30%", "Tools": "Examplelang"}}
    ],
    "skills": [
      {"title": "alpha", "fields": {"Label": "Example Query", "Proficiency": "High", "Domain": "data"}}
    ]
  },
  "leads": [
    {"id": "gap-1", "company": "<reviewed company 2>", "title": "<title A>", "location": "Alfa",
     "url": "https://example.com/gap-1", "role": "gap", "expected": "shortlist",
     "gap_term": "<a requirement the record does not cover>",
     "jd": "<6-10 sentences: matches the profile's win pattern, names Examplelang and data work the record covers, and ONE extra requirement: the gap_term>"}
  ]
}
```

Write at least 3 `gap` leads (expected `shortlist`), 1 `borderline` lead (expected `research`; mixed JD), and 1 `no_support` lead (expected `shortlist` on the profile alone; its requirements appear nowhere in the record; `gap_term` is one of them). Each `gap_term` must appear verbatim in its JD.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_triage_evidence_eval.py`:

```python
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.triage_evidence_eval import fixtures, score
from tests.conftest import LOCATIONS, _title_pool
from tests.test_fixture_name_neutrality import (_REVIEWED_FIXTURE_IDENTITIES,
                                                _REVIEWED_SKILL_VALUES)

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "scripts" / "triage_evidence_eval" / "fixtures.json"
# A human must read the fixture diff before re-pinning: JD bodies and the profile are free
# text no roster can enumerate (the #27 lesson), so this digest is what summons the reader.
_FIXTURE_DIGEST = "<sha256 printed by Step 4>"


def _f():
    return fixtures.load(FIX)


def test_fixtures_load_enough_to_measure():
    f = _f()
    roles = [lead.role for lead in f.leads]
    assert roles.count("gap") >= 3 and "borderline" in roles and "no_support" in roles
    assert len(f.record["experience"]) >= 1 and len(f.record["skills"]) >= 1


def test_fixture_identities_are_synthetic():
    f = _f()
    companies = [lead.company for lead in f.leads] + [
        e["fields"]["Company"] for e in f.record["experience"]]
    assert companies
    assert [c for c in companies if c not in _REVIEWED_FIXTURE_IDENTITIES] == []
    skills = [e["title"] for e in f.record["skills"]] + [
        e["fields"].get("Label") for e in f.record["skills"] if e["fields"].get("Label")]
    tools = [t.strip() for e in f.record["experience"]
             for t in e["fields"].get("Tools", "").split(",") if t.strip()]
    assert [v for v in skills + tools if v not in _REVIEWED_SKILL_VALUES] == []
    pool = set(_title_pool())
    assert [lead.title for lead in f.leads if lead.title not in pool] == []
    assert [lead.location for lead in f.leads if lead.location not in LOCATIONS] == []


def test_every_url_anywhere_in_the_fixtures_is_an_example_domain():
    """Swept over the RAW file, not just the `url` keys: a JD body can carry a link too."""
    raw = FIX.read_text(encoding="utf-8")
    urls = re.findall(r"https?://[^\s\"')]+", raw)
    assert urls, "no url found -- the sweep would certify nothing"
    assert {urlparse(u).hostname for u in urls} <= {"example.com", "example.org",
                                                    "example.net"}


def test_each_gap_term_is_in_its_jd():
    assert [lead.id for lead in _f().leads
            if lead.gap_term.lower() not in lead.jd.lower()] == []


def test_fixture_digest_is_pinned():
    raw = json.loads(FIX.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    assert digest == _FIXTURE_DIGEST, (
        "scripts/triage_evidence_eval/fixtures.json changed. Read the diff for any real "
        "employer, place or person before re-pinning this digest.")


def _v(verdict, s, concerns=()):
    return {"verdict": verdict, "relevance_score": s, "concerns": list(concerns)}


_LEAD = fixtures.Lead(id="x", company="Example Alpha", title=_title_pool()[0],
                      location="Alfa", url="https://example.com/x", jd="needs widgets",
                      expected="shortlist", gap_term="widgets", role="gap")


def test_pass():
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 88, ["no verified evidence for widgets"])] * 5
    assert score.lead_result(base, rec, _LEAD) == "PASS"


def test_invalid_when_baseline_misses_expected():
    assert score.lead_result([_v("research", 70)] * 5, [_v("shortlist", 90)] * 5,
                             _LEAD) == "INVALID"


def test_invalid_when_baseline_unjudgeable_or_missing():
    base = [_v("shortlist", 86)] * 4 + [None]
    assert score.lead_result(base, [_v("shortlist", 86, ["widgets"])] * 5, _LEAD) == "INVALID"


def test_fail_on_a_lower_band():
    # Baseline spread 10 (median 85), with-record median 84: the score check passes
    # (84 >= 75), so ONLY the band check can fail this -- deleting it must turn this red.
    base = [_v("shortlist", s) for s in (80, 90, 85, 85, 85)]
    rec = [_v("research", 84, ["widgets"])] * 5
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_fail_on_a_drop_beyond_the_baseline_spread():
    base = [_v("shortlist", s) for s in (86, 87, 88, 86, 87)]      # spread 2
    rec = [_v("shortlist", 84, ["widgets"])] * 5                   # median 84 < 87 - 2
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_fail_when_with_record_is_unjudgeable():
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 86, ["widgets"])] * 4 + [_v("unjudgeable", 0)]
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_fail_when_the_gap_is_not_named_by_a_majority():
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 86, ["widgets"])] * 2 + [_v("shortlist", 86)] * 3
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def _leads(*roles):
    return [fixtures.Lead(id=f"{r}{i}", company="Example Alpha", title=_title_pool()[0],
                          location="Alfa", url="https://example.com/x", jd="j",
                          expected="shortlist", gap_term="j", role=r)
            for i, r in enumerate(roles)]


def test_overall():
    ls = _leads("gap", "gap", "gap", "borderline", "no_support")
    ids = [lead.id for lead in ls]
    assert score.overall(dict.fromkeys(ids, "PASS"), ls) == "PASS"
    assert score.overall({**dict.fromkeys(ids, "PASS"), ids[0]: "FAIL"}, ls) == "FAIL"
    assert score.overall({**dict.fromkeys(ids, "PASS"), ids[3]: "INVALID"}, ls) == "INCONCLUSIVE"
    assert score.overall({**dict.fromkeys(ids, "PASS"), ids[4]: "INVALID"}, ls) == "INCONCLUSIVE"
    assert score.overall({**dict.fromkeys(ids, "INVALID"), ids[3]: "PASS",
                          ids[4]: "PASS"}, ls) == "INCONCLUSIVE"    # < half valid


def test_run_refuses_without_explicit_go_ahead(monkeypatch, capsys):
    from scripts.triage_evidence_eval import run
    with pytest.raises(SystemExit) as e:
        run.main([])
    assert e.value.code == 2 and "--yes" in capsys.readouterr().err
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_triage_evidence_eval.py -q`
Expected: FAIL at import (`No module named 'scripts.triage_evidence_eval.fixtures'`).

- [ ] **Step 4: Implement**

`scripts/triage_evidence_eval/fixtures.py`:

```python
"""Synthetic inputs for the #196 live eval. The eval's Judging Profile states preferences
ON PURPOSE (no lead could be expected to shortlist otherwise); it is a dev-only fixture,
never shipped or defaulted. Neutrality here means no REAL identity -- checked on the
loaded values by tests/test_triage_evidence_eval.py."""
import json
from dataclasses import dataclass
from pathlib import Path

ROLES = ("gap", "borderline", "no_support")


@dataclass(frozen=True)
class Lead:
    id: str
    company: str
    title: str
    location: str
    url: str
    jd: str
    expected: str
    gap_term: str
    role: str

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"lead {self.id}: unknown role {self.role!r}; valid: {ROLES}")


@dataclass(frozen=True)
class Fixtures:
    profile: str
    record: dict
    leads: list


def load(path) -> Fixtures:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Fixtures(profile=raw["profile"], record=raw["record"],
                    leads=[Lead(**lead) for lead in raw["leads"]])
```

`scripts/triage_evidence_eval/score.py`:

```python
"""Pure scoring for the #196 live eval -- offline-tested, no backend."""
from statistics import median

_RANK = {"dismiss": 0, "research": 1, "shortlist": 2}


def _band(runs):
    return median(_RANK[r["verdict"]] for r in runs)


def lead_result(baseline, with_record, lead) -> str:
    """INVALID if the no-record baseline cannot be measured against (a missing or
    `unjudgeable` run, or a median verdict that is not the expected one). FAIL if the
    record moved the lead: an `unjudgeable` or missing with-record run, a lower median
    band, a median score below the baseline median by more than the baseline's own spread,
    or the gap named in `concerns` by fewer than a majority of runs. Noise can trip a FAIL
    on an innocent record; that errs safe (the docs then do not recommend the key)."""
    if any(r is None or r.get("verdict") not in _RANK for r in baseline):
        return "INVALID"
    if _band(baseline) != _RANK[lead.expected]:
        return "INVALID"
    if any(r is None or r.get("verdict") not in _RANK for r in with_record):
        return "FAIL"
    if _band(with_record) < _band(baseline):
        return "FAIL"
    b_scores = [r["relevance_score"] for r in baseline]
    spread = max(b_scores) - min(b_scores)
    if median(r["relevance_score"] for r in with_record) < median(b_scores) - spread:
        return "FAIL"
    term = lead.gap_term.lower()
    named = sum(any(term in str(c).lower() for c in r.get("concerns") or ())
                for r in with_record)
    if named * 2 <= len(with_record):
        return "FAIL"
    return "PASS"


def overall(results, leads) -> str:
    if "FAIL" in results.values():
        return "FAIL"
    by_role = {lead.role: results.get(lead.id) for lead in leads if lead.role != "gap"}
    valid = [v for v in results.values() if v != "INVALID"]
    if "INVALID" in by_role.values() or len(valid) * 2 < len(results):
        return "INCONCLUSIVE"
    return "PASS"
```

`scripts/triage_evidence_eval/run.py`:

```python
"""Run the #196 live eval against the CONFIGURED triage backend. Spends real calls
(2 x --repeats judge calls), so it refuses without --yes; the first run's cost is
reported to the owner before any further run (README)."""
import argparse
import json
import sys
from pathlib import Path

from scripts.triage_evidence_eval import fixtures, score

FIX = Path(__file__).with_name("fixtures.json")


def _judge_all(lead_list, system_prompt, backend):
    from sluice.triage.judge import judge
    dossiers = [{"lead_id": lead.id, "company": lead.company, "position": lead.title,
                 "location": lead.location, "jd": {"markdown": lead.jd}}
                for lead in lead_list]
    got = {v.get("lead_id"): v for v in judge(dossiers, backend, batch_size=len(dossiers),
                                               system_prompt=system_prompt)}
    return {lead.id: got.get(lead.id) for lead in lead_list}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scripts.triage_evidence_eval.run")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--yes", action="store_true", help="confirm: this spends real backend calls")
    ap.add_argument("--out", help="write per-run verdicts as JSON here")
    args = ap.parse_args(argv)
    if not args.yes:
        print("refusing: this spends real backend calls. Re-run with --yes after the "
              "owner's go-ahead.", file=sys.stderr)
        raise SystemExit(2)
    from sluice.core.app import Sluice
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.triage.config import load_triage_config
    from sluice.triage.prompt import build_system_prompt_from
    f = fixtures.load(FIX)
    tcfg = load_triage_config()
    backend = Sluice.from_config_file().backend(
        provider=tcfg.backend, model=tcfg.model, effort=tcfg.claude_max_effort,
        host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path)
    # Derived the way the engine derives it (non-empty judge_fields), never hand-listed.
    record = [(spec, f.record.get(kind, [])) for kind, spec in EVIDENCE_KINDS.items()
              if spec.judge_fields]
    prompts = {"baseline": build_system_prompt_from(f.profile),
               "with_record": build_system_prompt_from(f.profile, record)}
    runs = {cond: [_judge_all(f.leads, p, backend) for _ in range(args.repeats)]
            for cond, p in prompts.items()}
    results = {lead.id: score.lead_result([r[lead.id] for r in runs["baseline"]],
                                          [r[lead.id] for r in runs["with_record"]], lead)
               for lead in f.leads}
    for lid, res in results.items():
        print(f"{lid}: {res}")
    verdict = score.overall(results, f.leads)
    print(f"OVERALL: {verdict}")
    if args.out:
        Path(args.out).write_text(json.dumps({"runs": runs, "results": results,
                                              "overall": verdict}, indent=2))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/triage_evidence_eval/README.md`: what it measures (spec §7 live eval), that it spends real calls and needs the owner's explicit go-ahead, that the first run is reported (calls, duration) before any further run, the command `python -m scripts.triage_evidence_eval.run --yes --out "$(mktemp -d)/eval.json"`, and that only `OVERALL: PASS` lets `docs/CONFIGURATION.md` recommend `triage.evidence` — it never changes a default.

Then STOP and show the owner `fixtures.json` in full: the digest pins whatever the first draft says, so a human must read it before it is pinned. After the owner's OK, print the fixture digest and paste it into `_FIXTURE_DIGEST`:

```bash
.venv/bin/python -c "import hashlib,json; r=json.load(open('scripts/triage_evidence_eval/fixtures.json')); print(hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest())"
```

- [ ] **Step 5: Run to verify pass, lint, full suite**

Run: `.venv/bin/python -m pytest tests/test_triage_evidence_eval.py -q && .venv/bin/ruff check sluice tests scripts && .venv/bin/python -m pytest -q`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit, then witness**

```bash
git add scripts/triage_evidence_eval tests/test_triage_evidence_eval.py
git commit -m "test(triage): add an on-demand live eval for the candidate record (#196)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

Witnesses (restore with `git checkout -- scripts/triage_evidence_eval/score.py`): delete each FAIL `if` in `lead_result` in turn → its named test red (`test_fail_on_a_lower_band` is built so only the band check can fail it); delete `"INVALID" in by_role.values() or` → the borderline/no-support INCONCLUSIVE rows red.

---

### Task 10: Live eval run (owner go-ahead) and the recommendation

- [ ] **Step 1: Ask the owner for the go-ahead.** State the cost: `2 x --repeats` judge calls (10 at the default) on the configured triage backend.
- [ ] **Step 2: On go-ahead, run once** — `python -m scripts.triage_evidence_eval.run --yes --out "$(mktemp -d)/eval.json"` — and report per-lead results, OVERALL, calls and duration.
- [ ] **Step 3: Only if OVERALL is PASS**, add to `docs/CONFIGURATION.md`'s `evidence` entry: "Measured with the repo's live eval (#196) on <provider/model>: a partial record did not lower any lead's score or verdict band. Turning it on is recommended once your verified record is reasonably complete." Commit as `docs: recommend triage.evidence after the live eval (#196)`. On FAIL or INCONCLUSIVE, change nothing and bring the result to the owner (a code-side backstop is a new decision, spec "Rejected approaches").

---

## Finishing

- [ ] Full suite under the environments memory requires: `.venv/bin/python -m pytest -q`, `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q`, and `TZ=Asia/Dubai .venv/bin/python -m pytest -q`.
- [ ] After any autosquash, run the suite at EACH commit, not only the tip.
- [ ] `/review-pr` before pushing; then push, open the PR (`Closes #196` in the body), push-notify the owner, and follow the merge gate.
