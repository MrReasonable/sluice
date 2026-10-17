# Interview prep briefs (#195 PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `job-sluice leads prep --lead SLUG [--write] [--json]`, a deterministic interview
prep brief over a lead's STAR stories, ranked against its job description. With `--write` the brief
is appended to the note once, and refused when it would be degraded.

**Architecture:** The pure brief logic lives in one new core module, `core/prepbrief.py`. The
facade method `Sluice.prep_brief` does the reads (entries, JD, cache peek, status) and the single
EOF append. `rank`/`jd_keywords` move into a new `core/rank.py` so that core can rank without
importing the cv sub-app. Doctor and `verify_outcome` learn a new `EvidenceKind.read_by_prep` flag.

**Tech Stack:** Python 3.12+ stdlib only, pytest, ruff 0.15.21.

**Spec:** `docs/superpowers/specs/2026-10-16-interview-prep-design.md`. Read it before any task. It
is the authority on every rule, wording and test row; this plan sequences it into tasks. Where a
row below says "spec row", the spec's Testing (PR 1) section has the exact assertion and witness.

## Global Constraints

- `sluice/` stays standard-library only. No new runtime dependency.
- No personal data. Companies in tests come from `_REVIEWED_FIXTURE_IDENTITIES` (`Example Ltd` is
  on it). Roles come from the seeded `titles` fixture, never a literal, except the spec's hostile
  markup-only values.
- No line-number citations in comments or docstrings. Cite `file.py::symbol`
  (`tests/test_citation_drift.py`).
- Never-clobber: the ONLY write is `store.append_body_section`. Add no store method.
- No status is ever written by prep.
- No new config key.
- Conventional commits. Every commit body ends with the attribution line
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`. PR 1 bodies say `Refs #195`,
  never a closing keyword.
- `$VENV` is the main checkout's `.venv` (worktrees have none of their own). Run the suite through the
  worktree's own tree:
  `PYTHONPATH=$PWD $VENV/bin/python -m pytest -q`. Before any
  mutation witness, run
  `$VENV/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`.
- Lint: `$VENV/bin/ruff check sluice tests scripts`. If ruff is
  absent, `pip install ruff==0.15.21` into that venv.
- Witnesses MOVE or DELETE code, are committed before they run, and are restored afterwards. A
  witness must go red; record that it did.

## Review Focus

1. **A lead note carrying a hand-pasted JD that ends inside an open fence.** `--write` must refuse
   with `note_open_fence` and leave the note byte-identical. Owned by Task 5.
2. **A lead found through suggestions.** A slug typed with a different case, or a fragment, must
   give `not_found` with bounded suggestions that include leads at `interview`. Owned by Task 5.
3. **A cached page triage rejected, on a lead the user later moved to `interview`.** The refusal's
   way out must name the hand delete of `triage_blocked`, never `leads unpark`. Owned by Task 5.
4. **Rerunning `--write` after adding stories.** It must report `already_written` with the delete
   instruction, and the note must stay byte-identical. Owned by Task 5 and Task 6.
5. **A story body holding headings, fences and `<!--`.** After a write, triage's JD reader and the
   follow-up reader must see nothing of it. Owned by Task 5.

## Amendments from the plan review (binding; they override the task text where they differ)

**A1, witness discipline (applies to EVERY task).** The order within each task is: implement, run
the suite green, COMMIT, then run the witnesses. Steps listed as "witness" therefore come AFTER the
task's commit step.
- Before the witnesses, run `compileall --invalidation-mode checked-hash` once.
- Run each witness against the committed tree: make one hand edit, run the named test, see it go
  red, then restore with `git checkout -- <file>`, which is safe now that the work is committed.
- Confirm `git status` is clean before the next witness.
- Record every witness as a `mutant -> failing test` table, added to the task's commit message
  with `git commit --amend` (allowed here, because the commit is the task's own, still unpushed).
- Run EVERY witness the spec's Testing (PR 1) section names for the task's rows, not only the ones
  the task lists. If a witness stays green, the row is wrong: fix the row, never drop the witness.

**A2, fixture identity shapes.** Companies must sit where `tests/test_fixture_name_neutrality.py`'s
collectors read them.
- Story fixtures are written as literal note text containing `Company: <roster value>`, the shape
  `tests/test_evidence_store.py`'s `_seed(tmp_path, kind, name, "Company: ...\n...")` uses.
- Lead fixtures go through `Vault.upsert(Lead(..., company="Example Ltd", ...))`, exactly as
  `tests/test_followups_facade.py::_seed` does. Before relying on it, CHECK that the collectors
  read that call shape, by running the collector against the new file. If they do not, write the
  lead note as literal text carrying a `company: "Example Ltd"` frontmatter line instead.
- No helper may take the company as a non-first positional argument, and no fixture may be a
  hand-written lowercase `"company":` dict.
- Roles always come from the `titles` fixture. `test_rank.py` asserts `jd_keywords("", "Build the
  DATA pipeline, own data.") == ["build", "data", "pipeline"]`, and checks the role half against a
  fixture title's own `[a-z]{4,}` words.
- The peek key-arm rows seed three REAL lead notes (with `lead_id` set via `update_fields`, with a
  url only, and with an empty url) and pass each `note.fm` to `get_or_build`, then to `peek`.
- The one exception is the spec's hostile values, whose base names are drawn at run time from
  `_REVIEWED_FIXTURE_IDENTITIES`.

**A3, Task 1's "count a stem once" row.** Use the shape where the two counting rules disagree:
`A={"best_for": "documentation"}`, `B={"best_for": "pipeline deploy"}`,
`kws=["documenting", "documented", "pipeline", "deploy"]`, and assert `rank([A, B], kws)[0] is B`.
Witness: score per keyword instead of per stem. Put the cv/engine import in the TOP import block
(ruff E402), or use the spec's `_jd_keywords = jd_keywords` binding. Quote shell globs
(`--include='*.py'`) for zsh.

**A4, one entry adapter.** `core/prepbrief.py` gains
`entry_from_store(e: dict, fields: tuple) -> dict`, which turns a `read_evidence` dict into a prep
entry (`title`, `verified=bool(e.get("verified"))`, the declared `fields` in order, `body`,
`best_for`, `category`).
- Both the facade and the Task 4 tests call it, so the pure rows test production code.
- Add a facade row: a story whose only match is a hand-typed `Category:` word shows that word on
  its `Matched:` line. Witness: drop `category` from the adapter.
- `prepbrief` uses a PUBLIC `jdsection.lines(text)`. Add it in Task 2, keeping `_lines` as an
  alias, instead of reaching into `_lines`.

**A5, more facade rows (Task 5).**
- `test_write_refuses_a_note_ending_in_an_open_fence`: a write-ready lead, with `\n```\nposting`
  appended to the note by hand. `prep_brief(write=True)` gives `not_writable`, the codes are
  `["note_open_fence"]`, the note's bytes are unchanged, and `markdown` is not None. Witness: pass
  `open_fence=False`.
- `test_floor_boundary_through_the_facade`, parametrised over the section and the cache. With
  `min_jd_chars=N` and a JD of exactly N characters that adds a keyword, the outcome is `written`.
  At N-1 it is `not_writable`, with codes `["jd_below_floor"]`. Witness: compare `len(jd) <= N` in
  the facade instead of calling `jd_arrived`.
- **The JD reason.** For a note-section JD and for a cached JD, `brief["jd"]["reason"] == ""` and
  the Job description line reads "from the note" / "from a cached copy". Each `none` arm gives its
  code. Witness: restore the `"no_source"` initialiser.
- **A written-note line-grammar row** for the hostile company and role values: read-back equality,
  and `answer_sections` still finds a section appended after the brief.
- **An `unjudgeable` lead that is ALSO stamped**, with a cached JD. Its sentence names BOTH
  actions (delete `triage_blocked` AND move the status off `unjudgeable`): `_rejected_way_out`
  takes the status and the stamp and names both when both are set. Follow the named way out once,
  and the next `--write` gives `written`.
- **A hostile slug** (one containing `$(`, a backtick and a double quote, composed per the
  hostile-value rule) appears in the sentence only inside `shlex.quote`'s single quotes.
- **More spec rows:**
  - "Cache next" shows `built_at`;
  - `sections(TAGGED_HEADING)` matches a trailing-whitespace heading line;
  - "Heading levels": a level-2 entry heading cuts the read-back short.
- **One role read.** The facade computes `role = note.fm.get("role", "") or ""` ONCE and passes it
  to both `degradations` and a new `role=` keyword on `build_brief`.

**A6, Task 3's unnamed casualty.** `tests/test_triage_record.py::test_doctor_names_triage_evidence_for_a_judge_only_kind`
asserts the stories row's exact text, which is split over two source lines, so the plan's grep
misses it.
- Change that assertion to the new exact text (`...reviewed, and read by \`leads prep\` interview
  briefs`), and keep its no-leak property: add a synthetic unread kind whose row equals
  `...reviewed, but nothing reads this corpus yet`.
- Grep for `reads this corpus` as well as `nothing reads`.
- The full list of tests that break is whatever the suite reports red after Step 3. Fix each one
  to the exact new text, never by loosening it.

**A7, docs drift (Task 6 Step 4).** Before regenerating rulesync, grep the consumer-list claims
that prep now joins, and reword each so it names prep or lists no consumers. State no count.

```bash
grep -rn "shared by triage, cv\|both build their\|for both \`triage()\`\|relies on it too" sluice docs .rulesync/rules
```

Also reword `core/vault.py::_unreadable_reason`'s docstring so it covers non-vault sources and
states no site count.

**A8, CLI.**
- Move the handler's fixed stderr texts (at least `written_hidden`'s) to module-level constants in
  `cli.py`, and have the CLI rows compare against those constants.
- Add a parser row, modelled on `test_the_subparser_registers_apply_and_has_no_dry_run`: `prep`
  registers `--lead`, `--write` and `--json`, and neither `--dry-run` nor `--apply`.
- `tests/test_docs_claims.py`'s `27` appears twice (in `expected` and in the message); move both.

**A9, template neutrality.** In `tests/test_prepbrief.py`, render every fixed sentence: each
degradation code, each rejected route, both order notes and the pending sentence. Run each through
`tests/test_coach_prompt.py`'s no-preference predicate, with a scope assertion that every code in
`CODES` was rendered.

**A10, deviations from the spec, stated.**
- The slug enters the `jd_rejected_page` sentence, per Decision 7: an exception to §4's slot rule.
  It is shell-quoted with `shlex.quote` rather than double-quoted, so a pasted command cannot run a
  `$(...)` that a scraped company carried.
- `jd_line` is built inside `build_brief`.

---

### Task 1: Move `rank`/`jd_keywords` into `core/rank.py`, add `matched_keywords`

**Files:**
- Create: `sluice/core/rank.py`, `tests/test_rank.py`
- Modify: `sluice/cv/bundle.py` (`rank` becomes an import), `sluice/cv/engine.py` (`_jd_keywords`
  becomes an import), `tests/test_core_layering.py` (module count +1)
- Prose repoint: grep for the claim (see Step 6)

**Interfaces:**
- Produces:
  - `sluice.core.rank.jd_keywords(role: str, jd: str | None) -> list[str]`
  - `sluice.core.rank.rank(entries: list[dict], keywords: list[str]) -> list[dict]`
  - `sluice.core.rank.matched_keywords(entry: dict, keywords: list[str]) -> list[str]`
  - `sluice.cv.bundle.rank` stays importable, and so does `sluice.cv.engine._jd_keywords`.

- [ ] **Step 1: Write the failing tests** in `tests/test_rank.py`:

```python
"""core/rank.py (#195): the one JD keyword tokeniser and ranker, shared by cv and prep."""
from sluice.core.rank import jd_keywords, matched_keywords, rank


def test_jd_keywords_are_sorted_unique_lowercased_four_letter_words():
    assert jd_keywords("Data Lead", "Build the DATA pipeline, own data.") == [
        "build", "data", "lead", "pipeline"]


def test_jd_keywords_accept_a_missing_jd():
    assert jd_keywords("Data Lead", None) == jd_keywords("Data Lead", "") == ["data", "lead"]


def test_matched_keywords_share_rank_s_haystack_and_count_a_stem_once():
    entry = {"best_for": "documentation", "category": "", "title": "x"}
    kws = ["documenting", "documented", "unrelated"]
    assert matched_keywords(entry, kws) == ["documented", "documenting"]
    # rank scores stems, so the two keywords sharing a stem count once
    other = {"best_for": "documentation unrelated", "category": "", "title": "y"}
    assert rank([entry, other], kws)[0] is other


def test_rank_orders_and_never_excludes():
    a = {"best_for": "", "category": "", "title": "a"}
    b = {"best_for": "pipeline", "category": "", "title": "b"}
    assert rank([a, b], ["pipeline"]) == [b, a]
```

- [ ] **Step 2: Run them and confirm they fail.**
  `PYTHONPATH=$PWD $VENV/bin/python -m pytest tests/test_rank.py -q`. Expected: `ModuleNotFoundError: sluice.core.rank`.

- [ ] **Step 3: Implement `sluice/core/rank.py`.** Move `rank`'s body and docstring verbatim from
  `sluice/cv/bundle.py::rank`, repointing its docstring's `cv/engine.py:_jd_keywords` mention to
  `core/rank.py::jd_keywords`. Reword "Today's only production caller" to name the callers with no
  count: "its production callers (cv, the interview prep brief)".

```python
"""The one JD keyword tokeniser and evidence ranker (#165, #195).

Here in core, not in cv/bundle.py where `rank` began, because core/prepbrief.py ranks with it and
core may not import a sub-app. cv/bundle.py imports `rank` from here, and cv/engine.py binds
`jd_keywords` as `_jd_keywords`. The precedents are core/jdsection.py and core/protocols.py::cv_name.
`rank`'s score and `matched_keywords` read ONE haystack builder, so the terms a brief shows are
exactly the terms that ranked it.
"""
import re

from sluice.core.stem import stem_all as _stem_all


def jd_keywords(role: str, jd) -> list:
    """Every 4+ letter ASCII word in the role and the JD, lowercased, sorted, unique."""
    return sorted({w for w in re.findall(r"[a-z]{4,}", f"{role} {jd or ''}".lower())})


def _haystack(entry: dict) -> set:
    # best_for/category/title, never body: matching into free prose lets a long entry
    # out-score a precise one on volume alone (see `rank`).
    return _stem_all(f"{entry.get('best_for', '')} {entry.get('category', '')} "
                     f"{entry.get('title', '')}")


def rank(entries: list[dict], keywords: list[str]) -> list[dict]:
    # <move the existing docstring here, repointed as Step 3 says>
    wanted = _stem_all(" ".join(keywords))
    return sorted(entries, key=lambda e: len(wanted & _haystack(e)), reverse=True)


def matched_keywords(entry: dict, keywords: list[str]) -> list[str]:
    """The keywords whose stem lands in `entry`'s haystack, sorted: what ranked it."""
    hay = _haystack(entry)
    return sorted({k for k in keywords if _stem_all(k) & hay})
```

- [ ] **Step 4: Rebind the cv callers.**
  - In `sluice/cv/bundle.py`, delete `def rank` and add `from sluice.core.rank import rank` beside
    the other `sluice.core` imports. `build_bundle` keeps calling `rank(...)`, and
    `from sluice.cv.bundle import rank` keeps working.
  - In `sluice/cv/engine.py`, replace `def _jd_keywords` with
    `from sluice.core.rank import jd_keywords as _jd_keywords`, plus a one-line comment saying the
    module-level name is what `tests/test_dossier_guard.py` monkeypatches. The call site keeps
    calling `_jd_keywords(role, jd)`.
  - If `_stem_all` is now unused in `cv/bundle.py`, remove its import, and only then.

- [ ] **Step 5: Bump the core module count.** In
  `tests/test_core_layering.py::test_core_module_discovery_finds_the_real_fleet`, change
  `expected = 36` to `expected = 37`.

- [ ] **Step 6: Repoint the prose.** Grep the claim:

```bash
grep -rn "bundle.py:rank\|bundle.py::rank\|_jd_keywords\|rank()\|ranker" sluice tests docs --include=*.py --include=*.md | grep -v docs/superpowers
```

  Repoint each PROSE hit at `core/rank.py::rank` / `core/rank.py::jd_keywords`, stating no caller
  count. That covers `core/stem.py`'s consumer list, `EVIDENCE_KINDS["stories"]`'s "a future
  consumer" comment, `EvidenceKind`'s docstring and skills floor-map comment, `Store.read_evidence`,
  `core/vault.py` comments, `tests/test_cv_bundle.py` and `docs/ARCHITECTURE.md`. Do not touch the
  `_jd_keywords` CODE binding or the monkeypatch.

- [ ] **Step 7: Run the suite.**
  `PYTHONPATH=$PWD $VENV/bin/python -m pytest -q` passes, and ruff is clean. Witness for the
  cv binding: temporarily make cv/engine.py's `build_bundle` call site call `jd_keywords` directly
  (import it by that name). `tests/test_dossier_guard.py::test_the_cv_consumer_proceeds_with_an_empty_jd`
  must go red. Restore it.

- [ ] **Step 8: Commit.** Use the message
  `refactor(core): move rank and jd_keywords into core/rank.py (#195)`. The body explains that
  core/prepbrief.py needs them and core may not import cv, and ends with `Refs #195` and the
  attribution line.

### Task 2: `jdsection.open_fence` and `DossierCache.peek`

**Files:**
- Modify: `sluice/core/jdsection.py` (add `open_fence`, and add prep to the docstring's consumers),
  `sluice/core/dossier.py` (add `peek`)
- Test: `tests/test_jdsection.py`, `tests/test_dossier_peek.py` (new)

**Interfaces:**
- Produces:
  - `jdsection.open_fence(body: str) -> bool`
  - `DossierCache.peek(lead: dict) -> dict | None`. It raises `ValueError("the cached dossier is not
    valid JSON")` or `ValueError("the cached dossier is not a JSON object")`, and propagates any
    `OSError` other than `FileNotFoundError`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_jdsection.py (append)
from sluice.core.jdsection import open_fence

def test_open_fence_reports_a_body_ending_inside_a_fence():
    assert open_fence("x\n```\ncode") and open_fence("x\n~~~\ncode")
    assert not open_fence("x\n```\ncode\n```\n") and not open_fence("plain")
```

```python
# tests/test_dossier_peek.py
"""DossierCache.peek (#195): read-only, ignores TTL and content, never fetches or creates."""
import json, os
from datetime import datetime, timedelta
import pytest
from sluice.core.dossier import DossierCache

def _fetcher(calls):
    def f(lead):
        calls.append(lead)
        return {"jd": {"markdown": "a real posting " * 20}}
    return f

@pytest.mark.parametrize("fm", [{"lead_id": "L1"}, {"url": "https://example.invalid/a"},
                                {"company": "Example Ltd", "role": "r"}])
def test_peek_finds_what_get_or_build_wrote_through_every_key_arm(tmp_path, fm):
    calls = []
    DossierCache(str(tmp_path), 7, _fetcher(calls)).get_or_build(fm)
    d = DossierCache(str(tmp_path), 7, _fetcher(calls)).peek(fm)
    assert d is not None and "real posting" in d["jd"]["markdown"]

def test_peek_ignores_the_ttl_and_the_floor_and_never_fetches(tmp_path):
    calls = []
    old = DossierCache(str(tmp_path), 7, _fetcher(calls),
                       clock=lambda: datetime.now() - timedelta(days=99))
    old.get_or_build({"lead_id": "L"})
    n = len(calls)
    c = DossierCache(str(tmp_path), 0, _fetcher(calls), min_jd_chars=10**6)
    assert c.peek({"lead_id": "L"}) is not None and len(calls) == n

def test_peek_on_a_missing_file_or_dir_returns_none_and_creates_nothing(tmp_path):
    d = tmp_path / "absent"
    assert DossierCache(str(d), 7, _fetcher([])).peek({"lead_id": "L"}) is None
    assert not d.exists()

@pytest.mark.parametrize("payload,msg", [("{not json", "not valid JSON"), ("[1, 2]", "not a JSON object")])
def test_peek_refuses_a_broken_file_with_a_path_free_message(tmp_path, payload, msg):
    c = DossierCache(str(tmp_path), 7, _fetcher([]))
    with open(c._path({"lead_id": "L"}), "w", encoding="utf-8") as f:
        f.write(payload)
    with pytest.raises(ValueError) as e:
        c.peek({"lead_id": "L"})
    assert msg in str(e.value) and str(tmp_path) not in str(e.value)
```

  Check `DossierCache.get_or_build`'s real signature and its fetcher contract before relying on
  `_fetcher`'s shape. The suite's existing dossier tests show how a stub fetcher returns an enrich
  dict. Match them.

- [ ] **Step 2: Confirm they fail** (`ImportError` / `AttributeError`).

- [ ] **Step 3: Implement.**

```python
# sluice/core/jdsection.py
def open_fence(body: str) -> bool:
    """True when `body` ENDS inside an unclosed code fence, so anything appended after it would
    be swallowed into the fence's section (#195: `leads prep --write` refuses then). One scanner:
    `_scan_fences`' still-open opener, never a second copy of the fence grammar."""
    return bool(_scan_fences(_lines(body))[1])
```

```python
# sluice/core/dossier.py, a DossierCache method beside `forget`
def peek(self, lead: dict) -> dict | None:
    """The cached dossier for `lead` (the note's frontmatter, never a dossier dict -- see
    `forget`), or None when there is none. Read-only (#195): ignores the TTL and the content,
    never calls the fetcher, never creates a directory or a file. For an interview-stage lead the
    cached page may be the only copy of a posting since taken down.

    A broken file raises ValueError with a FIXED, path-free message: the caller hands the text to
    core/vault.py::_unreadable_reason, which passes an errno-less message through as it is, and a
    path in it would carry the user's home directory into a brief, --json and MCP."""
    try:
        with open(self._path(lead), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("the cached dossier is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("the cached dossier is not a JSON object")
    return data
```

  Add `prep` (core/prepbrief.py via the facade) to jdsection's module-docstring consumer sentence.

- [ ] **Step 4: Run the tests, the full suite and ruff. All pass.** Witness for `open_fence`:
  return `_scan_fences(...)[0]` truthiness instead of the opener; the test goes red.

- [ ] **Step 5: Commit** with the message `feat(core): read-only DossierCache.peek and jdsection.open_fence (#195)`.

### Task 3: `EvidenceKind.read_by_prep`, doctor and `verify_outcome` suffixes

**Files:**
- Modify:
  - `sluice/core/protocols.py`: the field, `stories` sets it, the `verify_outcome` suffix, and the
    `read_pending_evidence` docstring count;
  - `sluice/core/doctor.py`: the `classify_store` suffix and the `ComponentCheck` docstring;
  - `sluice/core/vault.py` (`Vault.preflight` comment);
  - `sluice/core/app.py` (`Sluice.doctor`'s comment on stories' `blocks=()`).
- Test:
  - `tests/test_doctor.py` (the per-kind loop flips, and a synthetic unread kind);
  - `tests/test_evidence_store.py` (the flag pin);
  - `tests/test_doctor_verdict.py` (reworded reasons);
  - the existing wording tests for `verify_outcome`, `stories add`'s notice, the `init` summary and
    the MCP evidence-tool registered descriptions. Update their expected text to the new suffix;
    never loosen an assertion.
- Docs: `docs/USAGE.md`, `docs/TROUBLESHOOTING.md`, `docs/ARCHITECTURE.md` (every "nothing
  reads"/"not yet consumed"/"consumed by nothing" claim about stories).

**Interfaces:**
- Produces:
  - `EvidenceKind.read_by_prep: bool = False` (True on `EVIDENCE_KINDS["stories"]`).
  - `verify_outcome(spec, subject)` appends `" and include {subject} in interview prep briefs"`
    for a `read_by_prep` kind.

- [ ] **Step 1: Write the failing tests.**
  - In `tests/test_doctor.py`'s per-kind loop: for a `read_by_prep` kind, assert the row contains
    `` read by `leads prep` interview briefs `` and NOT `nothing reads this corpus yet`.
  - Replace the loop's unread-arm anti-vacuity assert with
    `assert any(s.read_by_prep and not s.read_by_composer for s in EVIDENCE_KINDS.values()), "the prep arm must be reached"`.
  - New tests (monkeypatching `doctor.EVIDENCE_KINDS`, as `tests/test_triage_record.py` patches it):
    - a synthetic kind with NO reader still prints `nothing reads this corpus yet`;
    - a synthetic kind carrying `read_by_prep=True` AND `names_in_skills_pool=True` gets the prep
      clause from `classify_store`, and `verify_outcome(spec, subject="stories entries")` ends
      `and include stories entries in interview prep briefs`.
  - In `tests/test_evidence_store.py`, pin `read_by_prep` per kind beside the existing flag pins
    (stories True, every other kind False).

- [ ] **Step 2: Confirm they fail.**

- [ ] **Step 3: Implement.**

```python
# protocols.py, EvidenceKind, beside names_in_skills_pool
    # #195: `leads prep` (core/prepbrief.py via Sluice.prep_brief) shows this corpus's entries in
    # an interview prep brief. The flag is what the facade iterates, so the flag and the reader
    # cannot disagree, and doctor/verify_outcome key their wording on it.
    read_by_prep: bool = False
```

```python
# verify_outcome: the SAME suffix shape classify_store uses (#196). An arm after the two early
# returns would never fire for a kind carrying either earlier flag.
def verify_outcome(spec, subject: str = "it") -> str:
    if spec.cited_by_gate:
        out = f"make {subject} citable"
    elif spec.names_in_skills_pool:
        out = f"make {subject} available to a CV's skills list"
    else:
        out = f"mark {subject} reviewed"
    if spec.read_by_prep:
        out += f" and include {subject} in interview prep briefs"
    return out
```

```python
# doctor.py classify_store: the `else` arm's guard becomes
            if not spec.judge_fields and not spec.read_by_prep:
                detail += ", but nothing reads this corpus yet"
# and after the `if spec.judge_fields:` suffix block:
        if spec.read_by_prep:
            # #195: a SUFFIX after the chain, as judge_fields is (#196): an arm would sit behind
            # an earlier one and never fire for a kind that also carries another flag.
            detail += ", and read by `leads prep` interview briefs"
```

  Set `read_by_prep=True` on `EVIDENCE_KINDS["stories"]`. Grep the claim and reword every hit:

```bash
grep -rn "nothing reads\|not yet consumed\|consumed by nothing" sluice tests docs | grep -v docs/superpowers
```

  Leave `mcpserver.py`'s `list_evidence` description ("nothing reads until a human verifies them")
  as it is: prep reads only the inbox's length. Reword `Store.read_pending_evidence`'s "this
  reader's three consumers" with no count. Fix CLAUDE.md's `classify_store`/`verify_outcome`
  sentence in Task 6, not here.

- [ ] **Step 4: Run the suite and ruff.** Update every wording test that now sees the suffix to
  the exact new text. Witnesses:
  - delete the `classify_store` suffix (loop red);
  - delete the suppression (loop red);
  - make classify_store's clause an `elif` in the chain (the synthetic two-flag kind goes red);
  - make verify_outcome's clause an arm after the early returns (red);
  - hard-code "it" (red).

- [ ] **Step 5: Commit** with the message `feat(evidence): read_by_prep flag; doctor and verify name the interview brief (#195)`.

### Task 4: `core/prepbrief.py`, the pure brief

**Files:**
- Create: `sluice/core/prepbrief.py`, `tests/test_prepbrief.py`
- Modify: `tests/test_core_layering.py` (count +1: `expected = 38`)

**Interfaces:**
- Consumes: `core/rank.py` (Task 1), `core/jdsection.py` (Task 2).
- Produces (exact names the facade and CLI use):
  - Constants: `HEADING`, `TAG`, `TAGGED_HEADING`, `SUGGESTION_LIMIT = 10`, the codes
    `NO_ENTRIES`, `NO_JD`, `JD_BELOW_FLOOR`, `JD_NO_KEYWORD`, `JD_REJECTED_PAGE` and
    `NOTE_OPEN_FENCE`, and `UNRANKED = frozenset({NO_JD, JD_NO_KEYWORD})`.
  - `Degradation(code: str, sentence: str)`, frozen.
  - `BriefEntry(title, verified: bool, fields: dict, matched: list, body: str)`.
  - `Brief(slug, company, role, status, jd_source, jd_chars: int, built_at, jd_reason, jd_line, kinds: dict, degradations: list, order_note: str)`,
    where `kinds` maps each kind to `{"pending": int, "entries": list[BriefEntry]}`.
  - `degradations(*, kinds, jd, jd_source, role, min_jd_chars, jd_arrived, rejected_page, open_fence, rejected_route, slug) -> list[Degradation]`.
    Here `kinds` maps each kind to `(entries: list[dict], pending: int)`, and the entry dicts carry
    `title`, `verified` (bool), `fields` (dict, declared order), `body`, `best_for` and `category`.
    `rejected_route` is one of `"unpark" | "answer" | "stamp" | "status"`.
  - `build_brief(*, slug, fm, status, jd, jd_source, jd_reason, jd_reason_text, built_at, kinds, degradations) -> Brief`.
  - `render_markdown(brief) -> str`, `to_json(brief) -> dict`.
  - `written_section(body, markdown) -> bool`, `tagged_section_exists(body) -> bool`.

- [ ] **Step 1: Write the failing tests** in `tests/test_prepbrief.py`. These are pure rows. Build
  the entries from notes read back through a real `Vault`, using a `_story(vault_dir, title, company, best_for, body, verified)`
  helper that writes `Job Applications/STAR Stories/<title>.md` with frontmatter
  `Company:`/`Best For:` (and `verified: 2026-01-01` when verified). Then read with
  `Vault(dir).read_evidence("stories", verified_only=False)` and adapt the dicts the way the facade
  will (Task 5: `verified=bool(e["verified"])`, `fields` in `EVIDENCE_KINDS["stories"].fields`
  order). The hostile-value rows use the spec's run-time composition: `"```" + COMPANY`, where
  `COMPANY` is drawn from `_REVIEWED_FIXTURE_IDENTITIES` and asserted on it. Rows to write (the spec's
  §Testing gives each one's assertion and witness):
  - **Degradations, each alone**, asserted as `[d.code for d in ...] == [code]`:
    - `no_entries`;
    - `no_jd`, at min_jd_chars 0 AND at a positive floor;
    - `jd_below_floor` via `jd_arrived=False` with JD text that adds keywords;
    - `jd_no_keyword` on `"TBD"`;
    - `jd_rejected_page`, once per route, each sentence naming its route's command with the slug
      (`leads unpark --apply "<slug>"` and that it moves the lead to needs_review; `leads answer`;
      the hand delete of `triage_blocked`; the hand status change);
    - `note_open_fence`.
  - **Two at once:** `no_entries` plus `no_jd`, in `CODES` order.
  - **One decision, not two:** `jd_arrived=False` with a long JD gives `[jd_below_floor]`, and
    `jd_arrived=True` with a short JD gives `[]`.
  - **Every degradation sentence names a way out.**
  - **Ordering:**
    - casefold (`alpha.md`, `Beta.md`);
    - ranked, with a cased tie (`Beta`, `alpha` with equal non-zero scores give alpha then Beta);
    - no-JD and no-keyword title order, where the last-sorting entry's Best For holds a role word.
      Assert that precondition with `jd_keywords(role, "")`, and assert the matching order-note
      sentence.
  - **Haystack parity:** three stories matching one, two and three JD words in Best For, a
    hand-typed `Category:` and the title respectively. Each word shows on its own story's
    `Matched:` line, and the order is three, two, one.
  - **`Matched:` with a keyword-less role:** a story whose Best For contains `none`, a JD without
    it, and no role. `none` is absent from its `Matched:` line.
  - **Line grammar:** every rendered line matches a regex set (the tagged heading, `### `/`#### `,
    `- [A-Za-z ]+: `, `> `/`>`, blank, or a sentence from `prepbrief`'s templates), for the hostile
    company/role values. Each hostile value appears in the text.
  - **Golden render:** one fixed brief equals an expected string byte for byte, with the role
    interpolated from the titles fixture.
  - **CR/LF:** a title, company and role composed with `\r## Job description\rx`, and a body of
    `a\r## Job description\rx`, give `jdsection.read(rendered) == ("", 0)`.
  - **`to_json` shape:** exactly the spec's key set, `verified` as booleans, no `path`, no JD text,
    no markdown.
  - **`written_section` / `tagged_section_exists`:** a tagged section is found, while an untagged
    copy of the heading and a heading with two spaces before the tag are not. A recased whole line
    is found (the stated residual).

- [ ] **Step 2: Confirm they fail** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `sluice/core/prepbrief.py`.** It imports only `sluice.core.rank` and
  `sluice.core.jdsection`. Module docstring: cite the spec, state the import shape (the facade
  calls it, mcpserver reads `TAGGED_HEADING`, cli's handler reads `HEADING`, no sub-app imports it)
  and the closed line grammar.

```python
import shlex
from dataclasses import dataclass

from sluice.core import jdsection
from sluice.core.rank import jd_keywords, matched_keywords, rank

HEADING = "## Interview prep brief (job-sluice)"
TAG = "<!-- sluice:interview-prep -->"
TAGGED_HEADING = f"{HEADING} {TAG}"
SUGGESTION_LIMIT = 10

NO_ENTRIES, NO_JD, JD_BELOW_FLOOR = "no_entries", "no_jd", "jd_below_floor"
JD_NO_KEYWORD, JD_REJECTED_PAGE, NOTE_OPEN_FENCE = (
    "jd_no_keyword", "jd_rejected_page", "note_open_fence")
CODES = (NO_ENTRIES, NO_JD, JD_BELOW_FLOOR, JD_NO_KEYWORD, JD_REJECTED_PAGE, NOTE_OPEN_FENCE)
UNRANKED = frozenset({NO_JD, JD_NO_KEYWORD})
_PASTE = "Paste the full posting under `## Job description` in the note."


@dataclass(frozen=True)
class Degradation:
    code: str
    sentence: str


@dataclass
class BriefEntry:
    title: str
    verified: bool
    fields: dict
    matched: list
    body: str


@dataclass
class Brief:
    slug: str
    company: str
    role: str
    status: str
    jd_source: str
    jd_chars: int
    built_at: str
    jd_reason: str
    jd_line: str
    kinds: dict
    degradations: list
    order_note: str = ""


def _one(value) -> str:
    """A single-line value: CR and LF become spaces (a title comes from a filename, and this
    renderer does not trust where its input came from)."""
    return str(value or "").replace("\r", " ").replace("\n", " ")


def _rejected_way_out(route: str, slug: str) -> str:
    # shlex.quote, not double quotes: the slug is a filename built from scraped company and title,
    # and a printed command the user pastes must not run a `$(...)` it carries.
    slug = shlex.quote(_one(slug))
    if route == "unpark":
        return (f" If you know this cached copy is the real posting, run "
                f"`job-sluice leads unpark --apply {slug}`; it clears the stamp and also "
                f"moves the lead to needs_review.")
    if route == "answer":
        return (f" If you know this cached copy is the real posting, record the reply with "
                f"`job-sluice leads answer --lead {slug} --text ...`; it clears the stamp.")
    if route == "stamp":
        return (" If you know this cached copy is the real posting, delete the "
                "`triage_blocked` value in the note by hand.")
    return (" If you know this cached copy is the real posting, move the lead's status off "
            "`unjudgeable` by hand.")


def degradations(*, kinds, jd, jd_source, role, min_jd_chars, jd_arrived, rejected_page,
                 open_fence, rejected_route, slug) -> list:
    out = []
    for kind, (entries, pending) in kinds.items():
        if not entries:
            more = (f", or review the {pending} waiting in the inbox with "
                    f"`job-sluice {kind} verify`" if pending else "")
            out.append(Degradation(NO_ENTRIES, f"No {kind} entries to rehearse. Add one with "
                                               f"`job-sluice {kind} add`{more}."))
    if jd_source == "none":
        out.append(Degradation(NO_JD, f"No job description was found. {_PASTE}"))
    else:
        # ONLY WHEN A JD EXISTS (spec Decision 7): a missing JD reports no_jd alone.
        if not jd_arrived:
            out.append(Degradation(JD_BELOW_FLOOR, (
                f"The job description is shorter than min_jd_chars ({min_jd_chars}), so it may "
                f"be page text rather than a posting. {_PASTE}")))
        if jd_keywords(role, jd) == jd_keywords(role, ""):
            out.append(Degradation(JD_NO_KEYWORD, (
                "The job description adds no word beyond the role's that the ranker can match "
                f"(it reads words of four or more ASCII letters). {_PASTE}")))
        if rejected_page:
            out.append(Degradation(JD_REJECTED_PAGE, (
                "The cached job description is a page triage would not judge: the judge "
                "rejected it, or a later fetch came back empty while this older copy stayed "
                f"cached. {_PASTE}" + _rejected_way_out(rejected_route, slug))))
    if open_fence:
        out.append(Degradation(NOTE_OPEN_FENCE, (
            "The note ends inside an unclosed code fence, so a brief written now would be "
            "hidden inside it. Close that fence with a line of the same character (backticks "
            "or tildes), at least as many as opened it.")))
    return sorted(out, key=lambda d: CODES.index(d.code))
```

  `build_brief`:
  - Sort each kind's entry dicts by `str.casefold(title)`.
  - Unless `{d.code for d in degradations} & UNRANKED`, follow with `rank(..., jd_keywords(role, jd))`
    and record `matched_keywords`.
  - Set `order_note` to the spec's no-JD sentence or its no-keyword sentence.
  - Build `jd_line`:
    - `"from the note (N characters)"`;
    - `"from a cached copy built <built_at> (N characters)"`;
    - `"none (no section and no cached copy)"`;
    - `"none (the cached copy holds no job description)"`;
    - `"none (the cached copy could not be read: <jd_reason_text>)"`.
  - `company`/`role` come from `fm.get("company", "")`/`fm.get("role", "")`.

  `render_markdown`:
  - Lines in order: `TAGGED_HEADING`, a blank line, then `- Lead:`, `- Role:`, `- Status:` and
    `- Job description:`, each with its `_one(...)` value.
  - Each degradation sentence as its own paragraph, then `order_note`.
  - Per kind: `### <Kind>` (`kind.capitalize()`), then `- Entries: N, U not yet verified`, then the
    pending sentence when `pending`
    ("N entries are waiting in the inbox. Run `job-sluice <kind> verify`, then delete this section
    and run `leads prep --write` again, to include them.").
  - Per entry:
    - `#### <title>` and `- Verified: yes|no`;
    - each field as `- <Name>: <value>`;
    - `- Matched: a, b` when ranked, or `- Matched: (no keyword)` when ranked with none. The line
      is omitted when unranked;
    - the body blockquoted: `["> " + l if l else ">" for l in jdsection._lines(body)]`.
  - Join with `"\n"`.

  `to_json`:

```python
{"lead": {"slug", "company", "role", "status"},
 "jd": {"source", "chars", "built_at", "reason"},
 "degradations": [{"code", "sentence"}],
 "kinds": {kind: {"pending": int,
                  "entries": [{"title", "verified": bool, "fields", "matched", "body"}]}}}
```

  `written_section`/`tagged_section_exists`:

```python
def _own_sections(body: str) -> list:
    # The WHOLE tagged heading line, no `tag` argument: jdsection._is_heading then compares the
    # entire line, so an untagged copy of the heading never counts (spec §1).
    return jdsection.sections(body, TAGGED_HEADING)

def written_section(body: str, markdown: str) -> bool:
    return markdown.split("\n", 1)[1].strip() in _own_sections(body)

def tagged_section_exists(body: str) -> bool:
    return bool(_own_sections(body))
```

- [ ] **Step 4: Bump `tests/test_core_layering.py` to `expected = 38`.** Run
  `tests/test_prepbrief.py`, the full suite and ruff. Record each spec witness red: drop the
  blockquote prefix, delete the label prefix, split on `\n` only, delete the CR/LF replacement,
  delete the casefold presort (both branches), drop each "only when a JD exists" guard, compare
  `len(jd)` inside `degradations`, and emit "yes"/"no" in `to_json`.

- [ ] **Step 5: Commit** with the message `feat(core): prepbrief, the pure interview prep brief (#195)`.

### Task 5: `Sluice.prep_brief` and `BriefResult`

**Files:**
- Modify: `sluice/core/app.py` (add the `BriefResult` dataclass beside `AnswerResult`, and the
  `prep_brief` method beside `record_answer`), `tests/test_app_operations.py` (`prep_brief` joins
  the disjointness set)
- Test: `tests/test_prep_brief_facade.py` (new). Follow `tests/test_followups_facade.py`'s
  conventions: an injected `today`, `Vault(str(tmp_path))`, `_seed` via `Vault.upsert` plus
  `update_fields`, titles from the fixture, and `Example Ltd`.

**Interfaces:**
- Consumes: Task 2 (`peek`, `open_fence`), Task 3 (`read_by_prep`), Task 4 (everything).
- Produces:
  - `BriefResult(outcome, slug="", status="", candidates=[], suggestions=[], more_suggestions=0, markdown=None, brief=None, jd_source="none", jd_text="", reason="")`.
    Its outcomes are `ok | written | already_written | written_hidden | present_unreadable | not_writable | conflict | write_failed | not_found | ambiguous | entries_unreadable`.
  - `Sluice.prep_brief(*, lead: str, write: bool = False) -> BriefResult`.

- [ ] **Step 1: Write the failing tests.** These are the spec's facade rows, through a real Vault:
  - **Entries:** labels; inbox entries excluded and counted; the three `entries_unreadable` rows;
    the path-free chmod row (`UNREADABLE_DIR`); no `path` in the output; the flag consumer witness
    (skills); the derived per-kind pin; and two prep kinds with one empty.
  - **JD:** precedence; empty and unreadable cache (path-free, three rows); the sentinel
    `ZZ-SENTINEL-77` absent from the markdown, the written bytes and the JSON, but present in
    `jd_text`; no fetch; a `jdsection.read` raise propagates.
  - **Degradations through the facade:**
    - `Unjudgeable` recased;
    - a stamped `research` lead;
    - a typed word in the stamp, with no section and a cached JD (writes);
    - controls that write;
    - the stale-copy row;
    - each way out unblocking a write: `leads unpark --apply` on research, a hand delete at
      `interview`, `leads answer` for a lead awaiting a reply, and a hand status change.
  - **Write:**
    - the byte relation `after == before + sep + "\n" + markdown + "\n"`;
    - reruns give `already_written`;
    - a pending story, then verify, then rerun;
    - a degraded rerun says the note already has a brief;
    - a concurrent fence (a spy store) gives `written_hidden`;
    - the tag out of place gives `present_unreadable`;
    - recased sections;
    - the note gone gives `not_found`;
    - `VaultConflict`/`FileNotFoundError` give `conflict`, and `PermissionError` gives
      `write_failed`;
    - any status writes;
    - no LLM and no citation.
  - **Resolve:**
    - exact slug;
    - a fragment on an `interview` lead in the suggestions;
    - an empty and a one-letter fragment give exactly `SUGGESTION_LIMIT` plus a
      `more_suggestions` count;
    - twins give `ambiguous`;
    - a case-changed slug.
  - **Rendering safety (Review Focus 5):** the cache-only JD, the own-section JD and the
    `record_answer` rows, with assertions in the spec's fixed order.

- [ ] **Step 2: Confirm they fail** (`AttributeError: prep_brief`).

- [ ] **Step 3: Implement** in `core/app.py`. Keep every import lazy, inside the method:

```python
@dataclass
class BriefResult:
    """#195: one interview prep brief. See the spec's §5 for each outcome. `reason` is the
    degradation sentences joined (not_writable), or a path-free error text (entries_unreadable,
    write_failed), else ""."""
    outcome: str
    slug: str = ""
    status: str = ""
    candidates: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    more_suggestions: int = 0
    markdown: str | None = None
    brief: dict | None = None
    jd_source: str = "none"
    jd_text: str = ""
    reason: str = ""
```

```python
    def prep_brief(self, *, lead: str, write: bool = False) -> BriefResult:
        """#195: build the lead's interview prep brief and, with `write`, append it once.
        Exact slug only (never a substring: the caller may be an LLM); a miss carries bounded
        substring SUGGESTIONS, which are only ever reported. The ONE write is an EOF append;
        no status is written."""
        from sluice.core import followups, jdsection, prepbrief
        from sluice.core.dossier import jd_text
        from sluice.core.leads import blocked_stamp, slug_matches
        from sluice.core.protocols import EVIDENCE_KINDS, VaultConflict
        from sluice.core.vault import _unreadable_reason
        store, note, candidates = self._resolve_exact(lead)
        if candidates:
            return BriefResult(outcome="ambiguous", candidates=candidates)
        if note is None:
            hits = sorted(n.slug for n in store.read_leads() if slug_matches(n, lead))
            cap = prepbrief.SUGGESTION_LIMIT
            return BriefResult(outcome="not_found", suggestions=hits[:cap],
                               more_suggestions=max(0, len(hits) - cap))
        kinds = {}
        for kind, spec in EVIDENCE_KINDS.items():
            if not spec.read_by_prep:
                continue
            try:
                raw = store.read_evidence(kind, verified_only=False)
                pending = len(store.read_pending_evidence(kind))
            except (OSError, UnicodeDecodeError) as e:
                return BriefResult(outcome="entries_unreadable", slug=note.slug,
                                   status=note.status, reason=f"{kind}: {_unreadable_reason(e)}")
            kinds[kind] = ([{
                "title": e["title"], "verified": bool(e.get("verified")),
                "fields": {n: (e.get("fields") or {}).get(n, "") for n in spec.fields},
                "body": e.get("body", ""), "best_for": e.get("best_for", ""),
                "category": e.get("category", "")} for e in raw], pending)
        section, _count = jdsection.read(note.body)   # a reader error propagates (cv's posture)
        jd, jd_source, built_at, jd_reason, reason_text = "", "none", "", "", ""
        cache = self.dossier_cache(self._dossier_dir(), 0, self.config.min_jd_chars)
        if section:
            jd, jd_source = section, "note"
        else:
            try:
                d = cache.peek(note.fm)
            except (OSError, ValueError) as e:
                d, jd_reason, reason_text = None, "cache_unreadable", _unreadable_reason(e)
            if d is None and not jd_reason:
                jd_reason = "no_source"   # ONLY here: a found JD carries no reason (spec §3)
            if d is not None:
                if jd_text(d):
                    jd, jd_source, built_at = jd_text(d), "cached", str(d.get("built_at") or "")
                else:
                    jd_reason = "cache_empty"
        stamp = blocked_stamp(note.fm)
        status = _status.normalize(note.status)
        rejected = jd_source == "cached" and (status == "unjudgeable" or bool(stamp))
        if stamp and status == "research":
            route = "answer" if followups.awaiting_reply(note.fm) else "unpark"
        else:
            route = "stamp" if stamp else "status"
        degs = prepbrief.degradations(
            kinds=kinds, jd=jd, jd_source=jd_source, role=note.fm.get("role", "") or "",
            min_jd_chars=self.config.min_jd_chars,
            jd_arrived=bool(jd) and cache.jd_arrived({"jd": {"markdown": jd}}),
            rejected_page=rejected, open_fence=jdsection.open_fence(note.body),
            rejected_route=route, slug=note.slug)
        brief = prepbrief.build_brief(
            slug=note.slug, fm=note.fm, status=note.status, jd=jd, jd_source=jd_source,
            jd_reason=jd_reason, jd_reason_text=reason_text, built_at=built_at, kinds=kinds,
            degradations=degs)
        markdown = prepbrief.render_markdown(brief)
        base = dict(slug=note.slug, status=note.status, markdown=markdown,
                    brief=prepbrief.to_json(brief), jd_source=jd_source, jd_text=jd)
        if not write:
            return BriefResult(outcome="ok", **base)
        if degs:
            reason = "; ".join(d.sentence for d in degs)
            if prepbrief.tagged_section_exists(note.body):
                reason += "; the note already has a brief"
            return BriefResult(outcome="not_writable", reason=reason, **base)
        try:
            appended = store.append_body_section(note.ref, prepbrief.TAG, markdown)
        except (VaultConflict, FileNotFoundError) as e:
            _log.warning("prep_brief: %s lost the write: %s", note.ref, e)
            return BriefResult(outcome="conflict", **base)
        except OSError as e:
            return BriefResult(outcome="write_failed", reason=_unreadable_reason(e), **base)
        fresh = _reread_lead(store, note)
        if fresh is None:
            return BriefResult(outcome="not_found", **base)
        if appended:
            ok = prepbrief.written_section(fresh.body, markdown)
            return BriefResult(outcome="written" if ok else "written_hidden", **base)
        exists = prepbrief.tagged_section_exists(fresh.body)
        return BriefResult(outcome="already_written" if exists else "present_unreadable", **base)
```

  Check these against the code as you write, and adapt to what is real, not to this sketch:
  - `slug_matches`' signature;
  - `Store.read_pending_evidence` exists on the store in use;
  - `self.dossier_cache(...)` raising on a malformed `dossier_allow_hosts`;
  - `_status` is the module alias already imported in `core/app.py`;
  - `_log` exists.

- [ ] **Step 4: Add `prep_brief` to `tests/test_app_operations.py`'s facade-name disjointness set.**
  Run the facade tests, the full suite and ruff. Record the spec witnesses red:
  - swap the JD order;
  - catch only `VaultConflict`, and catch only `FileNotFoundError`;
  - classify by heading presence;
  - read with the optional-tag `sections`;
  - read `note.fm["status"]` raw;
  - read `triage_blocked` without `blocked_stamp`;
  - drop the suggestion bound;
  - compute suggestions from a triage-status-filtered read;
  - hard-code `"stories"`;
  - read the pending entries into the brief;
  - name `leads unpark` for every stamped lead.

- [ ] **Step 5: Commit** with the message `feat(leads): Sluice.prep_brief builds and writes an interview prep brief (#195)`.

### Task 6: The `leads prep` CLI, the `get_lead` warning, docs and CLAUDE.md

**Files:**
- Modify:
  - `sluice/cli.py`: the parser beside `answer` and the `cmd_leads_prep` handler;
  - `sluice/mcpserver.py`: import `TAGGED_HEADING` from `sluice.core.prepbrief`, and a sentence in
    `_GET_LEAD_CONTENT_WARNING`;
  - `tests/test_mcpserver.py`: `"sluice.core.prepbrief"` joins `_ISOLATION_ALLOWED_MODULES` with a
    "pure section grammar and renderer; no write path" comment;
  - `tests/test_coach_prompt.py`: `get_lead`'s new sentence joins
    `test_registered_descriptions_name_no_preference` by name, with a scope assertion;
  - `tests/test_docs_claims.py`: the command-count literal moves +1. Cut its docstring's stale
    numbers and literal history, and KEEP the never-derive argument;
  - `README.md`: the Commands table's `leads` row gains `prep`;
  - `docs/USAGE.md`: a `### job-sluice leads prep` section, plus the `leads` intro paragraph, where
    `rename` is also added;
  - `docs/ARCHITECTURE.md`: the prepbrief bullet naming its importers, core/rank.py,
    `DossierCache.peek`, `jdsection.open_fence` and the stories row;
  - `docs/CONFIGURATION.md` and `sluice.yaml.example`: `min_jd_chars`' new effect;
  - `docs/TROUBLESHOOTING.md`: each degradation and its way out;
  - `.rulesync/rules/CLAUDE.md`: the spec §8 sentences. Name the importers with NO count. Then run
    `npm run rulesync`.
- Test: `tests/test_leads_prep_cli.py` (new).

**Interfaces:**
- Consumes: `Sluice.prep_brief`, `BriefResult`, `prepbrief.HEADING` (Tasks 4 and 5).

- [ ] **Step 1: Write the failing CLI tests**, driving `cli.main([...])` the way
  `tests/test_leads_dismiss_cli.py` does. The rows:
  - every outcome's stderr line and exit code, per the spec §6 table;
  - `--json` only on stdout, one parseable document for every outcome, and exit codes identical to
    the non-JSON run;
  - `--write --json` on `written_hidden` exits 1;
  - the default stdout equals `markdown + "\n"`, and a following `--write` changes the note by
    exactly the byte relation;
  - DEL and U+2028 in a body survive as escaped JSON;
  - `did you mean:` prints the bounded list plus `... and N more`;
  - **the heading has one spelling:** with
    `monkeypatch.setattr(prepbrief, "HEADING", "## Renamed brief (job-sluice)")`, the
    `already_written` and `present_unreadable` lines contain `Renamed brief (job-sluice)` and not
    `Interview prep brief`;
  - `get_lead`'s content warning contains `prepbrief.TAGGED_HEADING` and calls the Lead and Role
    lines scraped.

- [ ] **Step 2: Confirm they fail.**

- [ ] **Step 3: Implement the parser and the handler.**

```python
    pp = leads.add_parser("prep",
                          help="print an interview prep brief for one lead from your STAR "
                               "stories; --write appends it to the note once")
    pp.add_argument("--lead", required=True, metavar="SLUG",
                    help="the exact store-issued slug (the note's filename)")
    pp.add_argument("--write", action="store_true",
                    help="append the brief to the note, once; refused when it is degraded")
    pp.add_argument("--json", action="store_true",
                    help="print the result as one JSON document on stdout")
    pp.set_defaults(func=cmd_leads_prep)
```

```python
_PREP_OK = frozenset({"ok", "written", "already_written"})


def cmd_leads_prep(args, config) -> int:
    from sluice.core import prepbrief   # pure; for HEADING's ONE spelling
    from sluice.core.app import Sluice
    r = Sluice(config).prep_brief(lead=args.lead, write=args.write)
    code = 0 if r.outcome in _PREP_OK else 1
    if args.json:
        print(json.dumps({"outcome": r.outcome, "slug": r.slug, "status": r.status,
                          "candidates": r.candidates, "suggestions": r.suggestions,
                          "more_suggestions": r.more_suggestions, "brief": r.brief,
                          "reason": r.reason}))
        return code
    if r.outcome in ("not_found", "ambiguous"):
        _lead_refusal("prep", r, args.lead)
        if r.suggestions:
            print("did you mean:", file=sys.stderr)
            for s in r.suggestions:
                print(f"  {s}", file=sys.stderr)
            if r.more_suggestions:
                print(f"  ... and {r.more_suggestions} more", file=sys.stderr)
        return 1
    if r.outcome == "entries_unreadable":
        print(f"leads prep: {r.slug}: could not read the evidence -- {r.reason}", file=sys.stderr)
        return 1
    print(r.markdown)
    if not args.write:
        return 0
    heading = prepbrief.HEADING.lstrip("# ")
    lines = {
        "written": "written to the note",
        "already_written": (f'not written -- the note already has an "{heading}" section; the '
                            f"brief above is a fresh render and may differ from it. Delete that "
                            f"section to write a fresh one"),
        "not_writable": f"not written -- {r.reason}",
        "written_hidden": ("written, but an unclosed code fence before it now hides it, and "
                           "whichever section that fence sits in (the job description or the "
                           "follow-up answers, say) will read the brief as its own text until "
                           "the fence is closed"),
        "present_unreadable": (f'the note carries the interview-prep tag but no readable '
                               f'"{heading}" section -- fix the note by hand'),
        "write_failed": f"could not write the note -- {r.reason}",
    }
    if r.outcome == "conflict":
        _lead_refusal("prep", r, args.lead)
    else:
        print(f"leads prep: {r.slug}: {lines[r.outcome]}", file=sys.stderr)
    return code
```

  The `get_lead` warning sentence (spec §6) states:
  - a section under `TAGGED_HEADING` is sluice's own brief;
  - its Lead and Role lines are scraped;
  - its entry text is the user's own vault entries;
  - an entry marked `Verified: no` was placed by hand and not reviewed through `<kind> verify`;
  - its limit: a verbatim copy of the line, tag included, cannot be told apart from sluice's own.

- [ ] **Step 4: Write the docs and CLAUDE.md edits, then regenerate.** Run `npm run rulesync`.
  Then run the full suite, ruff, and these:
  - `tests/test_docs_claims.py`, `tests/test_citation_drift.py`, `tests/test_no_leaked_files.py`;
  - `tests/functional/test_cli_contract.py::test_every_declared_dest_is_read_by_its_handler`;
  - `tests/test_backend_timeout_config.py::test_importing_the_cli_loads_no_backend_module`.

  Run the datetime-sensitive suite under `TZ=Asia/Dubai` and `TZ=Pacific/Kiritimati`, and once
  with `env PATH="/usr/bin:/bin"`. The witness is hard-coding the heading text in `cli.py`: the
  renamed-heading row goes red.

- [ ] **Step 5: Commit** with the message `feat(leads): job-sluice leads prep (#195)`. Its body summarises the command and the
  write-once rule, and ends with `Refs #195`.

---

## Finish

1. Run `/review-pr` (the local specialist team) on the branch. Fold the findings as fixups, then
   autosquash into the task commits. Test EACH commit after the autosquash.
2. Squash the spec's revision commits into one `docs(spec): interview prep briefs from STAR stories (#195, part 1)`.
   Keep `chore(skills)`, `docs(plan)` and the task commits.
3. Push, and open the PR with `refs #195` and no closing keyword. Check `closingIssuesReferences`
   is empty. Push-notify the owner.
4. Drive it to green with `path-to-green`. CodeRabbit is the sole approver; never bypass.
5. After the merge: PR 2 (the MCP tool, the prompt and the eval runner) gets its own plan from the
   spec's §9–§10.
