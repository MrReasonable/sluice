# Research-lead migration (`leads unpark`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record each refused `unjudgeable` verdict on a `research` lead as a dated `triage_blocked` stamp, and add `job-sluice leads unpark`, which lists stamped leads and, with `--apply`, moves them to `needs_review`.

**Architecture:** One pure reader (`core/leads.py::blocked_stamp`) defines what a stamp is. `triage/apply.py` writes the stamp when a main write is refused, and clears it whenever a triage write lands. The engine counts the new `"blocked"` outcome in its own report field and the digest names it. A `Sluice` facade pair (`unpark_report`/`unpark`) and a CLI pass modelled on `leads expire` do the move. Every write goes through the existing `Vault.update_fields` with its CAS-fresh guards.

**Tech Stack:** Python 3.12+, stdlib only in `sluice/`, pytest with seeded faker for titles.

**Spec:** `docs/superpowers/specs/2026-10-14-research-migration-design.md`

## Global Constraints

- Every write is `update_fields` on the existing writer: no new write function (CodeQL sink).
- The stamp write: `require_status=frozenset({"research"})`, `require_blank=frozenset({"followup_asked"})`, `preserve_block_values=frozenset({"triage_blocked"})`.
- The move: `{"status": "needs_review", "triage_blocked": '""'}` with `require_status={"research"}`, `require_blank={"followup_asked"}`, `require_unchanged={"triage_blocked": <report stamp>}`, `preserve_block_values={"triage_blocked"}`.
- A stamp is a `triage_blocked` value that `date.fromisoformat` accepts after stripping, on one line. Anything else is the person's own text and is never listed or overwritten.
- `"blocked"` is grouped with `skipped` everywhere: never counted `unjudgeable`, never in `unjudgeable_by`, never surfaced, never audited.
- `TriageReport.blocked_research` is its own field, NOT a `counts` row.
- The `leads unpark` report writes nothing; only `--apply [SLUG ...]` (`nargs="*", default=None`, `is not None` dispatch) writes. No `--dry-run`.
- `--apply` exits 1 if any result is `abstained`, `conflict`, `unreadable`, `no-match` or `ambiguous`.
- Fixtures are synthetic: titles from `tests/conftest.py`'s `titles` fixture; companies as `Example <Word>` literals (`Example Ltd`, `Example Alpha`, ...), never `faker`'s company generator; urls on `example.invalid`, the reserved host the neighbouring tests use.
- No board name, lead count or company from the owner's install in any doc, comment, commit message or PR body.
- The judge backend is stubbed in every test before anything runs. No test makes a paid call.
- Commits: Conventional Commits, scope `triage`/`leads`/`docs`, each ending with the line `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Before any mutation witness: commit first, and run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once per session. Witnesses MOVE or DELETE code, never add. Restore with `git checkout -- <file>` and re-run the row green.
- Never cite a line number in a comment (`tests/test_citation_drift.py`).

## Review Focus

- **A hand-typed `triage_blocked` (a word, or a `|` block scalar).** The person expects it left alone: never listed, never cleared by a verdict, never cleared by an answer. Pinned in Tasks 1, 3, 6 and 7.
- **A lead the person marks asked while the nightly run is judging it.** They expect it to stay in their follow-up list, not be stamped or moved. Pinned in Tasks 2 and 7.
- **`--apply` run twice, or after the nightly run re-stamped the lead with a later date.** The person expects the second run to abstain cleanly and say so, not move twice or exit 0 on nothing. Pinned in Task 8.
- **Two notes sharing one slug.** The person expects `--apply SLUG` to refuse that slug as `ambiguous` and still move the others. Pinned in Task 7.
- **Answering a follow-up on a lead that was stamped before the question.** The person expects the answered lead not to come back into `unpark`. Pinned in Task 6.

---

### Task 0: Worktree environment

The worktree for branch `fix/research-migration-302` has no `.venv/` of its own.

- [ ] **Step 1: Create it and install the test extra**

Run, from the worktree root: `uv venv .venv && uv pip install --python .venv/bin/python -e ".[test]" ruff==0.15.21`
Expected: installs complete; `.venv/bin/python -c "import sluice, pytest, faker"` exits 0.

- [ ] **Step 2: Baseline**

Run: `.venv/bin/python -m pytest -q` then `python -m compileall` per Global Constraints (`.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`).
Expected: the suite is green on the untouched branch. If it is not, stop and report; do not start Task 1 on a red baseline.

---

### Task 1: `blocked_stamp` — what counts as a stamp

**Files:**
- Modify: `sluice/core/leads.py` (add a function after `index_by_slug`)
- Test: `tests/test_triage_blocked.py` (create)

**Interfaces:**
- Produces: `sluice.core.leads.blocked_stamp(fm: dict) -> str`: the raw `triage_blocked` value when it is a one-line ISO date, else `""`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_triage_blocked.py`:

```python
"""#302: the `triage_blocked` stamp -- what counts as one, where triage writes and clears it,
and how the engine counts a refused `unjudgeable` verdict on a `research` lead."""
import pytest

from sluice.core.leads import blocked_stamp


@pytest.mark.parametrize("value,expected", [
    ("2031-03-14", "2031-03-14"),
    (" 2031-03-14 ", " 2031-03-14 "),   # the RAW value: require_unchanged compares it as stored
    ("", ""),
    ("blocked", ""),                     # a hand-typed word is the person's own text
    ("|", ""),                           # a block scalar reads as its indicator, not blank
    ("2031-03-14\n", ""),                # never a multi-line value, even one strip() would pass
    ("2031-13-40", ""),
])
def test_blocked_stamp_accepts_only_a_one_line_iso_date(value, expected):
    assert blocked_stamp({"triage_blocked": value}) == expected


def test_blocked_stamp_of_a_note_without_the_key_is_blank():
    assert blocked_stamp({}) == ""
    assert blocked_stamp({"triage_blocked": None}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py -v`
Expected: FAIL with `ImportError: cannot import name 'blocked_stamp'`

- [ ] **Step 3: Write minimal implementation**

In `sluice/core/leads.py`, after `index_by_slug` (`date` is already imported there):

```python
def blocked_stamp(fm: dict) -> str:
    """The lead's `triage_blocked` stamp (#302), or "" when it carries none.

    Triage writes the key as a bare ISO date when the judge (or the pre-judge gate) returns
    `unjudgeable` for a `research` lead and that write is refused. Only that shape is a stamp.
    Anything else -- a word a person typed, or a block scalar, which reads as its `|`
    indicator rather than blank -- is the person's own text: `leads unpark` must not list it
    and no clear may write over it. Returned RAW, unstripped, because callers pass it to
    `update_fields(require_unchanged=...)`, which compares the stored value as stored.

    Lives in `core/` rather than `triage/` because the `Sluice` facade reads it too, and
    `core/` may not import a sub-app."""
    raw = fm.get("triage_blocked") or ""
    if "\n" in raw:
        return ""
    try:
        date.fromisoformat(raw.strip())
    except ValueError:
        return ""
    return raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py -v`
Expected: PASS (9 rows)

- [ ] **Step 5: Commit**

```bash
git add sluice/core/leads.py tests/test_triage_blocked.py
git commit -m "feat(triage): define the triage_blocked stamp (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witness**

Delete the `if "\n" in raw: return ""` lines; the `"2031-03-14\n"` row must go red (`strip()` would otherwise make it a valid date). Then replace `date.fromisoformat(raw.strip())` with `pass`; the `"blocked"` and `"|"` rows must go red. Restore with `git checkout -- sluice/core/leads.py` after each.

---

### Task 2: Stamp a refused `unjudgeable` on a `research` lead

**Files:**
- Modify: `sluice/triage/apply.py` (`apply_classification`, `apply_verdict`, new `_stamp_blocked`, imports)
- Modify: `tests/test_apply.py::test_a_judge_returned_unjudgeable_must_not_overwrite_a_research_lead` (its expected outcome changes from `"unchanged"` to `"blocked"`, and it gains a stamp assertion)
- Test: `tests/test_triage_blocked.py`

**Interfaces:**
- Consumes: `blocked_stamp(fm) -> str` (Task 1).
- Produces: `apply_verdict(...)` and `apply_classification(...)` may now return `"blocked"` in addition to `"applied"`, `"unchanged"`, `"skipped"`. `_stamp_blocked(vault, note, today: str) -> str` returning `"blocked"` or `"unchanged"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_triage_blocked.py`:

```python
import os

import sluice.triage.apply as apply_mod
from sluice.core.vault import Vault
from sluice.triage.apply import apply_classification, apply_verdict

TODAY = "2031-03-14"
UNJUDGEABLE = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "the page is a sign-in wall, not a posting"}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """Fix triage's clock to a date the host cannot produce, so a row that read the real
    clock goes red rather than passing on the day it was written."""
    class _Date(apply_mod.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(TODAY)
    monkeypatch.setattr(apply_mod, "date", _Date)


def _note(v, name, status, extra=()):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(['company: "Example Ltd"', f"status: {status}",
                                     "score: 60", 'relevance_notes: ""', *extra])
                + "\n---\n# body\n")
    return next(n for n in v.read_leads() if os.path.basename(str(n.ref)) == name)


def _text(note):
    return open(note.ref, encoding="utf-8").read()


def test_a_refused_unjudgeable_on_a_research_lead_stamps_today_and_nothing_else(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    after = _text(note)
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY
    # Only the one key was added: status, score, body and relevance_notes are untouched.
    assert after.replace(f'triage_blocked: "{TODAY}"\n', "") == before


@pytest.mark.parametrize("status", ["new", "unjudgeable"])
def test_a_lead_whose_unjudgeable_write_lands_is_not_stamped(tmp_path, status):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", status)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "applied"
    assert "triage_blocked" not in _text(note)


def test_an_application_owned_lead_is_not_stamped(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "applied")
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "skipped"
    assert _text(note) == before


def test_a_new_lead_set_to_research_mid_judge_is_not_stamped(tmp_path):
    # Witnesses the snapshot-`research` gate in _stamp_blocked. A `shortlist` row cannot:
    # the stamp's own require_status refuses that either way. Here the FRESH status is
    # `research`, so only the snapshot gate keeps the stamp off; the next run stamps it.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new")                       # snapshot: new
    v.update_fields(note.ref, {"status": "research"})   # a human moves it mid-judge
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)


def test_a_research_lead_that_becomes_applied_mid_judge_is_not_stamped(tmp_path):
    # Witnesses the stamp's require_status. The snapshot is `research`, so _guarded and the
    # snapshot gate both pass; only the CAS-fresh re-read inside update_fields refuses.
    # `applied`, not `new`: the main verdict cannot land on `applied` either, so the stamp
    # write is the one that decides the outcome.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    v.update_fields(note.ref, {"status": "applied"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)


def test_a_lead_marked_asked_mid_judge_is_not_stamped(tmp_path):
    # Witnesses require_blank on the stamp: the human's follow-up question outranks it.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    v.update_fields(note.ref, {"followup_asked": TODAY})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)


def test_a_second_run_the_same_day_is_still_blocked_and_writes_nothing(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    note = v.read_leads()[0]                              # the snapshot now carries TODAY
    before = _text(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "blocked"
    assert _text(note) == before


def test_an_older_stamp_on_a_lead_that_leaves_research_mid_judge_is_unchanged(tmp_path):
    # Witnesses the `== today` arm: a check of "has a stamp" would call this "blocked".
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked: 2031-03-01"])
    v.update_fields(note.ref, {"status": "applied"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"


def test_a_multiline_hand_typed_triage_blocked_is_left_alone(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked:", "  - my own note"])
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked:\n  - my own note" in _text(note)


def test_the_pre_judge_gate_refusal_on_a_research_lead_stamps_too(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    assert apply_classification(v, note, "unjudgeable", "no job description") == "blocked"
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY
    assert v.read_leads()[0].status == "research"


def test_a_refused_unjudgeable_on_a_shortlist_lead_is_unchanged_and_unstamped(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "shortlist")
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "unchanged"
    assert "triage_blocked" not in _text(note)
```

In `tests/test_apply.py::test_a_judge_returned_unjudgeable_must_not_overwrite_a_research_lead`, replace its last two lines with:

```python
    # #302: the refusal is now recorded as a `triage_blocked` stamp, never a status change.
    assert apply_verdict(v, note, verdict, {}) == "blocked"
    assert v.read_leads()[0].status == "research"
```

and replace the comment's last paragraph ("The consequence is deliberate ... behind them.") with:

```python
    # The consequence is deliberate and is NOT a silent one: the refusal stamps
    # `triage_blocked`, and `job-sluice leads unpark` lists those leads for a human to move
    # (#302), rather than a nightly cron moving them behind the person whose queue it is.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py tests/test_apply.py -v`
Expected: FAIL on the rows expecting `"blocked"` and a stamp (outcome is `"unchanged"`, no `triage_blocked` key). The rows expecting `"unchanged"`/no stamp may already pass; that is expected, since their guards are witnessed in Step 6.

- [ ] **Step 3: Write minimal implementation**

In `sluice/triage/apply.py`, change the import line `from sluice.core.leads import FRAMING_KEYS` to:

```python
from sluice.core.leads import FRAMING_KEYS, blocked_stamp
```

Add after `_HAND_EDITABLE_KEYS`:

```python
_RESEARCH = frozenset({"research"})


def _stamp_blocked(vault, note, today: str) -> str:
    """Record a REFUSED `unjudgeable` on a `research` lead as `triage_blocked: <today>` (#302).

    `_VERDICT_REQUIRE`/`_DECISION_REQUIRE` refuse that write, rightly: `status` cannot tell a
    conservative-score artifact from a research task a human set. The stamp is what lets
    `job-sluice leads unpark` list those leads for a human to move. It sets one key and
    nothing else.

    Only a lead whose SNAPSHOT is `research` is tried, so a refusal on `shortlist`,
    `dismiss` and the rest costs no extra write; a lead hand-moved to `research` while being
    judged is stamped on the next run instead. The write's own guards are re-read fresh
    inside the store's CAS: the lead must still be at `research`, and must not have been
    marked asked (a follow-up question outranks the stamp).

    "blocked" when the stamp landed, or when the snapshot already carried today's stamp (a
    second run the same day is a byte-identical no-op, so the write returns False). The
    store has no single-note read, and a whole-vault re-read per refused lead is too dear
    for the nightly loop, so that second arm reads the snapshot. Residual, stated: a lead
    stamped earlier today that leaves `research` mid-judge is counted once more in this
    run's digest. The outcome only feeds a count; `leads unpark` re-reads before acting."""
    if _status.normalize(note.status) != "research":
        return "unchanged"
    if vault.update_fields(note.ref, {"triage_blocked": f'"{today}"'},
                           require_status=_RESEARCH,
                           require_blank=frozenset({"followup_asked"}),
                           preserve_block_values=frozenset({"triage_blocked"})):
        return "blocked"
    return "blocked" if blocked_stamp(note.fm) == today else "unchanged"
```

In `apply_classification`, replace `tag = f"[triage {date.today().isoformat()}]"` with:

```python
    today = date.today().isoformat()
    tag = f"[triage {today}]"
```

and replace its final `return "applied" if wrote else "unchanged"` with:

```python
    if wrote:
        return "applied"
    if decision == "unjudgeable":
        # #302: the pre-judge gate's refusal on a `research` lead is recorded the same way
        # the judge's is. See _stamp_blocked.
        return _stamp_blocked(vault, note, today)
    return "unchanged"
```

In `apply_verdict`, replace `tag = f"[triage {date.today().isoformat()}]"` with:

```python
    today = date.today().isoformat()
    tag = f"[triage {today}]"
```

and replace its final `return "applied" if wrote else "unchanged"` with:

```python
    if wrote:
        return "applied"
    if status == "unjudgeable":
        return _stamp_blocked(vault, note, today)
    return "unchanged"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py tests/test_apply.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/triage/apply.py tests/test_triage_blocked.py tests/test_apply.py
git commit -m "feat(triage): stamp a refused unjudgeable verdict on a research lead (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witnesses (one at a time, restoring with `git checkout -- sluice/triage/apply.py` after each)**

| Mutation | Row that must go red |
|---|---|
| delete the `if _status.normalize(note.status) != "research": return "unchanged"` lines | `test_a_new_lead_set_to_research_mid_judge_is_not_stamped` |
| delete `require_status=_RESEARCH,` | `test_a_research_lead_that_becomes_applied_mid_judge_is_not_stamped` |
| delete `require_blank=frozenset({"followup_asked"}),` | `test_a_lead_marked_asked_mid_judge_is_not_stamped` |
| replace the last line's `blocked_stamp(note.fm) == today` with `blocked_stamp(note.fm)` | `test_an_older_stamp_on_a_lead_that_leaves_research_mid_judge_is_unchanged` |
| replace the last line with `return "unchanged"` | `test_a_second_run_the_same_day_is_still_blocked_and_writes_nothing` |
| delete the `if decision == "unjudgeable":` arm in `apply_classification` | `test_the_pre_judge_gate_refusal_on_a_research_lead_stamps_too` |
| delete `preserve_block_values=frozenset({"triage_blocked"})` | `test_a_multiline_hand_typed_triage_blocked_is_left_alone` (if it stays green, `require_blank`/the vault's multi-line handling already protects it: record which, and keep the argument as defence in depth with a comment saying so) |

---

### Task 3: Clear the stamp when a triage write lands

**Files:**
- Modify: `sluice/triage/apply.py` (`apply_classification`, `apply_verdict`, `_HAND_EDITABLE_KEYS`, the comment above `_VERDICT_REQUIRE`, the #118 comment in `apply_classification`)
- Test: `tests/test_triage_blocked.py`

**Interfaces:**
- Consumes: `blocked_stamp` (Task 1).
- Produces: no new names. Behaviour: a landed verdict or classification on a stamped lead writes `triage_blocked: ""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_triage_blocked.py`:

```python
SHORTLIST = {"verdict": "shortlist", "relevance_score": 80, "fit_reasoning": "fits"}


def test_a_landed_verdict_clears_a_stamp(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked: 2031-03-01"])
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    assert v.read_leads()[0].fm["triage_blocked"] == ""


def test_a_landed_classification_clears_a_stamp(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", ["triage_blocked: 2031-03-01"])
    assert apply_classification(v, note, "reject", "out of scope") == "applied"
    assert v.read_leads()[0].fm["triage_blocked"] == ""


def test_a_landed_verdict_on_an_unstamped_lead_adds_no_key(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    apply_verdict(v, note, SHORTLIST, {})
    assert "triage_blocked" not in _text(note)


@pytest.mark.parametrize("value", ["my own note", "|"])
def test_a_landed_verdict_never_clears_a_non_date_value(tmp_path, value):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", [f"triage_blocked: {value}"])
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    assert f"triage_blocked: {value}" in _text(note)


def test_a_restamp_mid_judge_refuses_the_verdict(tmp_path):
    # Witnesses require_unchanged on triage_blocked. A DIFFERENT date: a same-day re-stamp
    # is byte-identical and could not exercise the guard.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research", ["triage_blocked: 2031-03-01"])
    v.update_fields(note.ref, {"triage_blocked": "2031-03-02"})
    assert apply_verdict(v, note, SHORTLIST, {}) == "unchanged"
    assert v.read_leads()[0].fm["triage_blocked"] == "2031-03-02"


def test_both_stale_stamps_clear_together(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", ["triage_blocked: 2031-03-01", "followup_asked: 2031-02-01"])
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    fm = v.read_leads()[0].fm
    assert fm["triage_blocked"] == "" and fm["followup_asked"] == ""


def test_a_followup_restamp_mid_judge_still_refuses_when_both_stamps_are_present(tmp_path):
    # The row that fails if the triage_blocked entry REPLACES the followup_asked one in
    # require_unchanged instead of joining it.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", ["triage_blocked: 2031-03-01", "followup_asked: 2031-02-01"])
    v.update_fields(note.ref, {"followup_asked": "2031-03-02"})
    assert apply_verdict(v, note, SHORTLIST, {}) == "unchanged"
    assert v.read_leads()[0].fm["followup_asked"] == "2031-03-02"


def test_a_blocked_restamp_mid_judge_still_refuses_when_both_stamps_are_present(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", ["triage_blocked: 2031-03-01", "followup_asked: 2031-02-01"])
    v.update_fields(note.ref, {"triage_blocked": "2031-03-02"})
    assert apply_verdict(v, note, SHORTLIST, {}) == "unchanged"
    assert v.read_leads()[0].fm["triage_blocked"] == "2031-03-02"


def test_a_restamp_mid_classify_refuses_the_classification(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", ["triage_blocked: 2031-03-01"])
    v.update_fields(note.ref, {"triage_blocked": "2031-03-02"})
    assert apply_classification(v, note, "reject", "out of scope") == "unchanged"
    assert v.read_leads()[0].fm["triage_blocked"] == "2031-03-02"
    assert v.read_leads()[0].status == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py -v -k "clear or restamp or non_date or unstamped"`
Expected: FAIL on the two `clears_a_stamp` rows, `both_stale_stamps_clear_together`, `a_restamp_mid_judge_refuses_the_verdict`, `a_blocked_restamp...` and `a_restamp_mid_classify...`.

- [ ] **Step 3: Write minimal implementation**

In `sluice/triage/apply.py`, change `_HAND_EDITABLE_KEYS` to:

```python
_HAND_EDITABLE_KEYS = frozenset(FRAMING_KEYS) | {"triage_next", "followup_asked",
                                                 "triage_blocked"}
```

and append to the comment above it: `` `triage_blocked` (#302) is defence in depth the same way: a clear runs only when the snapshot holds a date stamp, and a value turned multi-line since then fails the clear's `require_unchanged` first. ``

In `apply_classification`, replace the `wrote = vault.update_fields(...)` call with:

```python
    # #302: a landed classification clears a `triage_blocked` stamp, as a landed verdict
    # does (see apply_verdict). Only a real date stamp is cleared; a hand-typed value is left.
    fields = {"status": new_status}
    require_unchanged = None
    stamp = blocked_stamp(note.fm)
    if stamp:
        fields["triage_blocked"] = '""'
        require_unchanged = {"triage_blocked": stamp}
    wrote = vault.update_fields(
        note.ref, fields,
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
        require_status=_DECISION_REQUIRE.get(decision, frozenset(_status.TRIAGE_OWNED)),
        require_unchanged=require_unchanged,
        preserve_block_values=_HAND_EDITABLE_KEYS)
```

and replace the #118 comment below it with:

```python
    # #118: `wrote=False` is either a genuine no-op -- require_status refused on a fresh
    # re-read (the lead already left TRIAGE_OWNED), or a byte-identical rewrite -- or (#302)
    # the stale-stamp `require_unchanged` refusing a `triage_blocked` re-stamp that landed
    # mid-classify, where the newer stamp stands. A REAL content collision raises
    # VaultConflict instead, caught one level up in triage/engine.py. Never a failure.
```

In `apply_verdict`, replace the block from `stamp = note.fm.get("followup_asked", "") or ""` through `require_unchanged = {"followup_asked": stamp}` with:

```python
    require_unchanged = {}
    stamp = note.fm.get("followup_asked", "") or ""
    if stamp.strip():
        fields["followup_asked"] = '""'
        require_unchanged["followup_asked"] = stamp
    # #302: the same rule for `triage_blocked`, MERGED into the one dict so neither entry
    # replaces the other. Only a real date stamp is cleared; a hand-typed value is left.
    blocked = blocked_stamp(note.fm)
    if blocked:
        fields["triage_blocked"] = '""'
        require_unchanged["triage_blocked"] = blocked
```

and in its `vault.update_fields(...)` call change `require_unchanged=require_unchanged,` to `require_unchanged=require_unchanged or None,`.

Replace the last paragraph of the comment above `_VERDICT_REQUIRE` ("The cost is that leads already parked ... silently.") with:

```python
# The cost is that leads already parked in `research` by the OLD behaviour stay parked.
# Clearing them rewrites a human's queue, so it is a pass a human runs: the refusal stamps
# `triage_blocked` (see _stamp_blocked) and `job-sluice leads unpark` lists those leads and
# moves them only on `--apply` (#302), never a nightly cron acting on them silently.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py tests/test_apply.py tests/test_triage_followups.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/triage/apply.py tests/test_triage_blocked.py
git commit -m "feat(triage): clear the triage_blocked stamp when a triage write lands (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witnesses (restore with `git checkout -- sluice/triage/apply.py` after each)**

| Mutation | Row that must go red |
|---|---|
| delete the `if blocked:` block in `apply_verdict` | `test_a_landed_verdict_clears_a_stamp` |
| delete the `if stamp:` block in `apply_classification` | `test_a_landed_classification_clears_a_stamp` |
| in `apply_verdict`, delete `require_unchanged["triage_blocked"] = blocked` | `test_a_restamp_mid_judge_refuses_the_verdict` |
| in `apply_verdict`, change `require_unchanged["triage_blocked"] = blocked` to `require_unchanged = {"triage_blocked": blocked}` (moves the assignment, replacing the dict) | `test_a_followup_restamp_mid_judge_still_refuses_when_both_stamps_are_present` |
| in `apply_classification`, delete `require_unchanged=require_unchanged,` | `test_a_restamp_mid_classify_refuses_the_classification` |
| replace `blocked = blocked_stamp(note.fm)` with `blocked = note.fm.get("triage_blocked", "")` | `test_a_landed_verdict_never_clears_a_non_date_value` |

`triage_blocked` in `_HAND_EDITABLE_KEYS` has no witness, as its comment says.

---

### Task 4: Engine counts `"blocked"` in its own field

**Files:**
- Modify: `sluice/triage/engine.py` (`TriageReport`, the pre-judge gate's counting key, the judge site's counting key and audit condition)
- Test: `tests/test_triage_blocked.py`

**Interfaces:**
- Consumes: `"blocked"` outcomes from Task 2.
- Produces: `TriageReport.blocked_research: int` (default 0).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_triage_blocked.py`:

```python
import json
import re
from datetime import datetime

from sluice.core.dossier import DossierCache
from sluice.core.backends import Completion
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
import sluice.triage.engine as eng
from tests.test_triage_engine import _DOSSIER_ID, _fields


class _Unjudgeable:
    """A stub judge that answers `unjudgeable` for every lead it is handed. No network."""
    last_backend = "primary"

    def __init__(self):
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        ids = re.findall(_DOSSIER_ID, prompt)
        return Completion(json.dumps([{"lead_id": i, "verdict": "unjudgeable",
                                       "relevance_score": 0,
                                       "fit_reasoning": "a sign-in wall"} for i in ids]))


def _engine_run(tmp_path, titles, status, jd):
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, "a.md"), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(_fields("Example Ltd", accept[0].title(), status,
                                            url="https://example.invalid/a"))
                + "\n---\n# Lead\n")
    cache = DossierCache(str(tmp_path / "dos"), ttl_days=7,
                         fetcher=lambda lead: {"jd": {"markdown": jd}},
                         clock=lambda: datetime(2031, 3, 14))
    cfg = TriageConfig(); cfg.accept_titles = list(accept)
    audit_path = tmp_path / "a.jsonl"
    report = eng.run(v, cfg, _Unjudgeable(), cache, AuditLog(str(audit_path)),
                     statuses=(status,))
    rows = audit_path.read_text().splitlines() if audit_path.exists() else []
    return report, v, [json.loads(r) for r in rows]


def test_a_judge_refusal_counts_as_blocked_and_nothing_else(tmp_path, titles):
    report, v, rows = _engine_run(tmp_path, titles, "research", "an example posting")
    assert report.blocked_research == 1
    assert report.counts["skipped"] == 1
    assert report.counts["unjudgeable"] == 0
    assert report.unjudgeable_by["judge"] == 0
    assert report.surfaced == []
    assert not any(r.get("stage") == "judge" for r in rows)
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY


def test_a_pre_judge_refusal_counts_as_blocked_and_nothing_else(tmp_path, titles):
    report, v, _ = _engine_run(tmp_path, titles, "research", "")
    assert report.blocked_research == 1
    assert report.counts["skipped"] == 1
    assert report.counts["unjudgeable"] == 0
    assert report.unjudgeable_by["pre_gate"] == 0
    assert v.read_leads()[0].fm["triage_blocked"] == TODAY


def test_an_unblocked_run_reports_zero(tmp_path, titles):
    report, _, _ = _engine_run(tmp_path, titles, "new", "an example posting")
    assert report.blocked_research == 0
    assert report.counts["unjudgeable"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py -v -k "refusal_counts or unblocked_run"`
Expected: FAIL with `AttributeError: 'TriageReport' object has no attribute 'blocked_research'`

- [ ] **Step 3: Write minimal implementation**

In `sluice/triage/engine.py`, add to `TriageReport` directly after the `unjudgeable_by` field:

```python
    # #302: research leads whose `unjudgeable` verdict (judge or pre-judge gate) was refused
    # and recorded as a `triage_blocked` stamp instead. A cause breakdown like
    # `unjudgeable_by`, not an outcome row: those leads are counted under `skipped`.
    blocked_research: int = 0
```

At the pre-judge gate site, replace `key = "skipped" if outcome in ("skipped", "unchanged") else "unjudgeable"` with:

```python
                key = ("skipped" if outcome in ("skipped", "unchanged", "blocked")
                       else "unjudgeable")
                # #302: a refused `unjudgeable` on a `research` lead, recorded as a stamp.
                if outcome == "blocked":
                    report.blocked_research += 1
```

At the judge site, replace `key = "skipped" if outcome in ("skipped", "unchanged") else clamp_verdict(` with `key = "skipped" if outcome in ("skipped", "unchanged", "blocked") else clamp_verdict(`, add directly after the `report.counts[key] = ...` line:

```python
            # #302: counted on its own, never under `unjudgeable`: no lead became unjudgeable.
            if outcome == "blocked":
                report.blocked_research += 1
```

and replace the audit condition `if outcome not in ("skipped", "unchanged"):` (the judge site's, directly above the `_audit({... "stage": "judge" ...})` call) with `if outcome not in ("skipped", "unchanged", "blocked"):`. Leave the two checks in the classify pass alone: it never decides `unjudgeable`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_triage_blocked.py tests/test_triage_engine.py tests/test_triage_followups.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/triage/engine.py tests/test_triage_blocked.py
git commit -m "feat(triage): count blocked research leads in the triage report (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witnesses (restore with `git checkout -- sluice/triage/engine.py` after each)**

| Mutation | Row that must go red |
|---|---|
| remove `"blocked", ` from the judge site's counting tuple | `test_a_judge_refusal_counts_as_blocked_and_nothing_else` (`counts["unjudgeable"]`) |
| remove `"blocked"` from the judge site's audit tuple | `test_a_judge_refusal_counts_as_blocked_and_nothing_else` (judge row) |
| remove `"blocked"` from the pre-judge gate's tuple | `test_a_pre_judge_refusal_counts_as_blocked_and_nothing_else` |
| delete either `report.blocked_research += 1` | the matching `refusal_counts` row |

---

### Task 5: Digest line

**Files:**
- Modify: `sluice/cli.py::_format_triage_digest`
- Test: `tests/test_triage_run_cli.py`

**Interfaces:**
- Consumes: `TriageReport.blocked_research` (Task 4).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_triage_run_cli.py` (it already defines `_report`), and change its import line `from sluice.cli import _build_parser, cmd_triage_run` to `from sluice.cli import _build_parser, _format_triage_digest, cmd_triage_run`:

```python
def test_the_digest_names_blocked_research_leads_and_how_to_review_them():
    line = ("2 research leads could not be judged from their page; "
            "review with: job-sluice leads unpark")
    assert line in _format_triage_digest(_report(blocked_research=2)).splitlines()
    one = ("1 research lead could not be judged from its page; "
           "review with: job-sluice leads unpark")
    assert one in _format_triage_digest(_report(blocked_research=1)).splitlines()
    assert "unpark" not in _format_triage_digest(_report())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_triage_run_cli.py -v -k blocked_research`
Expected: FAIL (`AssertionError`, line not present)

- [ ] **Step 3: Write minimal implementation**

In `_format_triage_digest`, directly before `if report.failures:`:

```python
    blocked = getattr(report, "blocked_research", 0)
    if blocked:
        # #302: the nightly run cannot move these (status cannot tell them from a research
        # task a person set), so the one useful thing to say is the command that can.
        lines.append(f"{blocked} research lead{'' if blocked == 1 else 's'} could not be "
                     f"judged from {'its' if blocked == 1 else 'their'} page; review with: "
                     "job-sluice leads unpark")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_triage_run_cli.py tests/test_triage_record.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/cli.py tests/test_triage_run_cli.py
git commit -m "feat(triage): name blocked research leads in the run digest (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: Answering a follow-up clears a stamp

**Files:**
- Modify: `sluice/core/app.py::Sluice.record_answer`
- Test: `tests/test_followups_facade.py`

**Interfaces:**
- Consumes: `blocked_stamp` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_followups_facade.py`:

```python
def _note_text(tmp_path):
    return pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text(encoding="utf-8")


def test_an_answer_clears_a_triage_blocked_stamp(tmp_path):
    # #302: an answered research task is the person's live decision; a stamp from before the
    # question must not bring it back into `leads unpark`.
    slug = _seed(tmp_path, followup_asked=_ago(3), triage_blocked=_ago(9))
    assert _app(tmp_path).record_answer(lead=slug, text="the band is fine").outcome == "answered"
    assert Vault(str(tmp_path)).read_leads()[0].fm["triage_blocked"] == ""


def test_an_answer_on_an_unstamped_lead_adds_no_triage_blocked_key(tmp_path):
    slug = _seed(tmp_path, followup_asked=_ago(3))
    _app(tmp_path).record_answer(lead=slug, text="the band is fine")
    assert "triage_blocked" not in _note_text(tmp_path)


def test_an_answer_leaves_a_hand_typed_triage_blocked_alone(tmp_path):
    slug = _seed(tmp_path, followup_asked=_ago(3), triage_blocked="my own note")
    _app(tmp_path).record_answer(lead=slug, text="the band is fine")
    assert "triage_blocked: my own note" in _note_text(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_followups_facade.py -v -k triage_blocked`
Expected: FAIL on `test_an_answer_clears_a_triage_blocked_stamp`

- [ ] **Step 3: Write minimal implementation**

In `Sluice.record_answer`, add `from sluice.core.leads import blocked_stamp` beside the existing function-local imports, and replace the `store.update_fields(note.ref, {"followup_asked": '""'}, ...)` call with:

```python
            # #302: a `triage_blocked` stamp from before the question goes too, or the answered
            # lead would come back into `leads unpark` as if its board had blocked it. Only a
            # real date stamp is cleared, so an unstamped lead gains no key and a hand-typed
            # value is left alone.
            fields = {"followup_asked": '""'}
            if blocked_stamp(note.fm):
                fields["triage_blocked"] = '""'
            store.update_fields(note.ref, fields, require_status=research,
                                preserve_block_values=frozenset({"followup_asked",
                                                                 "triage_blocked"}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_followups_facade.py tests/test_leads_followups_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_followups_facade.py
git commit -m "feat(leads): clear a triage_blocked stamp when a follow-up is answered (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witnesses (restore with `git checkout -- sluice/core/app.py` after each)**

| Mutation | Row that must go red |
|---|---|
| delete the `if blocked_stamp(note.fm):` two lines | `test_an_answer_clears_a_triage_blocked_stamp` |
| dedent `fields["triage_blocked"] = '""'` out of the `if` (a MOVE, so the write becomes unconditional) | `test_an_answer_on_an_unstamped_lead_adds_no_triage_blocked_key` |

The `"triage_blocked"` entry in `preserve_block_values` is reached only when the snapshot held a date stamp and the value turned multi-line before the write; no row reaches it, and the comment should say it is defence in depth.

---

### Task 7: `Sluice.unpark_report` and `Sluice.unpark`

**Files:**
- Modify: `sluice/core/app.py` (new `UnparkRow` dataclass beside `StaleLead`; two methods after `expire`)
- Test: `tests/test_leads_unpark.py` (create)

**Interfaces:**
- Consumes: `blocked_stamp` (Task 1), `followups.awaiting_reply`, `index_by_slug`, `ambiguous_slug_warnings`.
- Produces:
  - `UnparkRow(slug: str, ref: object, company: str, role: str, stamp: str, host: str)` dataclass.
  - `Sluice.unpark_report() -> list[UnparkRow]`.
  - `Sluice.unpark(slugs: list[str] | None = None) -> tuple[list[UnparkRow], list[tuple[str, str]]]`; outcomes `moved`, `abstained`, `conflict`, `unreadable`, `no-match`, `ambiguous`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leads_unpark.py`:

```python
"""#302: `leads unpark` -- report-first, human-gated move of stamped research leads to
needs_review. Fixture notes are built through `Vault.upsert` so their slugs are real
store-issued filenames, as in tests/test_leads_expire.py."""
import pathlib

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault

TODAY = "2031-03-14"
STAMP = "2031-03-10"


@pytest.fixture(autouse=True)
def _role(titles):
    global _TITLES
    _TITLES = [t.title() for t in titles[0]]


def _seed(tmp_path, *, n=0, status="research", company="Example Ltd", **extra):
    url = f"https://example.invalid/{n}"
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=_TITLES[n], company=company, url=url))
    note = next(x for x in v.read_leads() if x.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path):
    return Sluice(Config(), store=Vault(str(tmp_path)), today=lambda: TODAY)


def _by_slug(tmp_path, slug):
    return next(n for n in Vault(str(tmp_path)).read_leads() if n.slug == slug)


def _bytes(tmp_path):
    root = pathlib.Path(tmp_path)
    return {p: p.read_bytes() for p in root.rglob("*.md")}


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_lists_stamped_research_leads_with_the_raw_stamp(tmp_path):
    slug = _seed(tmp_path, triage_blocked=STAMP)
    _seed(tmp_path, n=1)                                   # unstamped: not listed
    [row] = _app(tmp_path).unpark_report()
    assert (row.slug, row.stamp, row.host, row.company) == (
        slug, STAMP, "example.invalid", "Example Ltd")


def test_the_report_changes_nothing(tmp_path):
    _seed(tmp_path, triage_blocked=STAMP)
    before = _bytes(tmp_path)
    _app(tmp_path).unpark_report()
    assert _bytes(tmp_path) == before


def test_the_report_skips_a_lead_awaiting_a_reply(tmp_path):
    _seed(tmp_path, triage_blocked=STAMP, followup_asked="2031-03-12")
    assert _app(tmp_path).unpark_report() == []


@pytest.mark.parametrize("value", ["my own note", "|"])
def test_the_report_skips_a_hand_typed_value(tmp_path, value):
    _seed(tmp_path, triage_blocked=value)
    assert _app(tmp_path).unpark_report() == []


def test_the_report_skips_a_stamped_lead_not_at_research(tmp_path):
    _seed(tmp_path, status="needs_review", triage_blocked=STAMP)
    assert _app(tmp_path).unpark_report() == []


def test_an_empty_report_is_empty(tmp_path):
    assert _app(tmp_path).unpark_report() == []


# ── the move ─────────────────────────────────────────────────────────────────

def test_unpark_moves_every_listed_lead_and_clears_the_stamp(tmp_path):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    rows, results = _app(tmp_path).unpark()
    assert sorted(results) == sorted([(a, "moved"), (b, "moved")])
    assert {r.slug for r in rows} == {a, b}
    for slug in (a, b):
        note = _by_slug(tmp_path, slug)
        assert note.status == "needs_review"
        assert note.fm["triage_blocked"] == ""
        assert f"[unpark {TODAY}]" in note.fm["relevance_notes"]


def test_unpark_narrows_to_named_slugs(tmp_path):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    rows, results = _app(tmp_path).unpark(slugs=[a])
    assert results == [(a, "moved")] and [r.slug for r in rows] == [a]
    assert _by_slug(tmp_path, b).status == "research"


def test_an_unknown_slug_is_reported_and_the_named_valid_one_still_moves(tmp_path):
    a = _seed(tmp_path, triage_blocked=STAMP)
    _, results = _app(tmp_path).unpark(slugs=["Example Nowhere - Nothing", a])
    assert ("Example Nowhere - Nothing", "no-match") in results and (a, "moved") in results


def test_a_slug_two_listed_notes_claim_is_ambiguous(tmp_path, monkeypatch):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    app = _app(tmp_path)
    real = app.unpark_report
    def twins():
        rows = real()
        return [r if r.slug != b else type(r)(**{**r.__dict__, "slug": a}) for r in rows]
    monkeypatch.setattr(app, "unpark_report", twins)
    _, results = app.unpark(slugs=[a])
    assert results == [(a, "ambiguous")]
    assert _by_slug(tmp_path, b).status == "research"


def test_a_restamp_with_a_different_date_after_the_report_abstains(tmp_path, monkeypatch):
    # Witnesses require_unchanged. A DIFFERENT date: a same-day re-stamp is byte-identical.
    from tests.conftest import racing_read
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    state = racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"triage_blocked": "2031-03-13"}))
    _, results = _app(tmp_path).unpark()
    assert state["fired"]
    assert results == [(slug, "abstained")]
    assert _by_slug(tmp_path, slug).status == "research"


def test_a_lead_moved_off_research_mid_sweep_abstains(tmp_path, monkeypatch):
    # Witnesses require_status on the move.
    from tests.conftest import racing_read
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    state = racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"status": "applied"}))
    _, results = _app(tmp_path).unpark()
    assert state["fired"] and results == [(slug, "abstained")]
    assert _by_slug(tmp_path, slug).status == "applied"


def test_a_lead_marked_asked_mid_sweep_abstains(tmp_path, monkeypatch):
    # Witnesses require_blank on the move.
    from tests.conftest import racing_read
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    state = racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"followup_asked": "2031-03-14"}))
    _, results = _app(tmp_path).unpark()
    assert state["fired"] and results == [(slug, "abstained")]
    assert _by_slug(tmp_path, slug).status == "research"


def test_a_vault_conflict_on_one_lead_does_not_stop_the_rest(tmp_path, monkeypatch):
    _seed(tmp_path, triage_blocked=STAMP)
    _seed(tmp_path, n=1, triage_blocked=STAMP)
    app = _app(tmp_path)
    store = app.store()
    real = store.update_fields
    calls = {"n": 0}
    def flaky(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict("synthetic race")
        return real(ref, fields, **kw)
    monkeypatch.setattr(store, "update_fields", flaky)
    _, results = app.unpark()
    assert sorted(o for _, o in results) == ["conflict", "moved"]


def test_unpark_with_nothing_listed_writes_nothing(tmp_path):
    _seed(tmp_path)
    before = _bytes(tmp_path)
    assert _app(tmp_path).unpark() == ([], [])
    assert _bytes(tmp_path) == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_unpark.py -v`
Expected: FAIL with `AttributeError: 'Sluice' object has no attribute 'unpark_report'`

- [ ] **Step 3: Write minimal implementation**

In `sluice/core/app.py`, after the `StaleLead` dataclass:

```python
@dataclass
class UnparkRow:
    """One `research` lead `leads unpark` lists (#302): triage's judge (or its pre-judge gate)
    said the page was not something it could judge, and the write was refused because
    `status` cannot tell a conservative-score artifact from a research task a person set.
    `stamp` is the RAW stored value, exactly as the move's `require_unchanged` compares it."""
    slug: str
    ref: object
    company: str
    role: str
    stamp: str
    host: str
```

After `Sluice.expire`:

```python
    def unpark_report(self) -> list:
        """The #302 report: `research` leads carrying a `triage_blocked` stamp, minus any
        awaiting a follow-up reply (the person is actively working those). Changes nothing."""
        from urllib.parse import urlsplit

        from sluice.core import followups
        from sluice.core.leads import blocked_stamp
        rows = []
        for note in self.store().read_leads(frozenset({"research"})):
            stamp = blocked_stamp(note.fm)
            if not stamp or followups.awaiting_reply(note.fm):
                continue
            try:
                host = urlsplit(note.fm.get("url", "") or "").hostname or ""
            except ValueError:
                host = ""
            rows.append(UnparkRow(slug=note.slug, ref=note.ref,
                                  company=note.fm.get("company", "") or "",
                                  role=note.fm.get("role", "") or "",
                                  stamp=stamp, host=host))
        return rows

    def unpark(self, slugs=None):
        """Move the reported leads (or only the `slugs` named, matched EXACTLY) from
        `research` to `needs_review` (#302). Returns `(rows, results)` from ONE read, so the
        CLI's listing and outcomes cannot disagree: `rows` are the leads acted on, `results`
        `[(slug, outcome)]` with outcome one of `moved`; `abstained` (the write's fresh
        re-read refused: re-judged, re-stamped, moved off `research` or marked asked since
        the report); `conflict` (a sustained write race, isolated to that lead); `unreadable`
        (an OSError on that lead's ref); `no-match`; `ambiguous` (two listed notes claim the
        slug -- see index_by_slug)."""
        from sluice.core.leads import ambiguous_slug_warnings, index_by_slug
        from sluice.core.protocols import VaultConflict
        report = self.unpark_report()
        store = self.store()
        results = []
        if slugs:
            by_slug, dropped = index_by_slug(report)
            for msg in ambiguous_slug_warnings("unpark: blocked lead", dropped):
                _log.warning("%s", msg)
            chosen = []
            for s in slugs:
                r = by_slug.get(s)
                if r is not None:
                    chosen.append(r)
                else:
                    results.append((s, "ambiguous" if s in dropped else "no-match"))
        else:
            chosen = list(report)
        today = (self._today or _today)()
        tag = f"[unpark {today}]"
        note = (f"{tag} triage could not judge this lead from its page (not a posting, or "
                "too little text); moved from research for a human to look at")
        for r in chosen:
            try:
                # Every guard is re-read fresh inside the store's CAS, never checked against
                # the report's snapshot, which is stale by construction.
                wrote = store.update_fields(
                    r.ref, {"status": "needs_review", "triage_blocked": '""'},
                    append_note=note, note_tag=tag,
                    require_status=frozenset({"research"}),
                    require_blank=frozenset({"followup_asked"}),
                    require_unchanged={"triage_blocked": r.stamp},
                    preserve_block_values=frozenset({"triage_blocked"}))
            except VaultConflict:
                _log.warning("unpark: %s lost the write race, left untouched", r.slug)
                results.append((r.slug, "conflict"))
                continue
            except OSError as e:
                # Same rule as `expire`: a note moved or deleted mid-sweep must not discard
                # the record of what WAS written.
                _log.warning("unpark: %s could not be written (%s), left untouched", r.slug, e)
                results.append((r.slug, "unreadable"))
                continue
            results.append((r.slug, "moved" if wrote else "abstained"))
        return chosen, results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_unpark.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_leads_unpark.py
git commit -m "feat(leads): add the unpark report and move to the facade (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witnesses (restore with `git checkout -- sluice/core/app.py` after each)**

| Mutation | Row that must go red |
|---|---|
| delete `require_status=frozenset({"research"}),` in `unpark` | `test_a_lead_moved_off_research_mid_sweep_abstains` |
| delete `require_blank=frozenset({"followup_asked"}),` in `unpark` | `test_a_lead_marked_asked_mid_sweep_abstains` |
| delete `require_unchanged={"triage_blocked": r.stamp},` | `test_a_restamp_with_a_different_date_after_the_report_abstains` |
| delete `or followups.awaiting_reply(note.fm)` | `test_the_report_skips_a_lead_awaiting_a_reply` |
| replace `blocked_stamp(note.fm)` with `note.fm.get("triage_blocked", "")` | `test_the_report_skips_a_hand_typed_value` |
| delete the `except VaultConflict:` arm | `test_a_vault_conflict_on_one_lead_does_not_stop_the_rest` |

The move's `preserve_block_values` has no reachable row (the report lists only date stamps, and a value turned multi-line since fails `require_unchanged` first); say so in a comment beside it.

---

### Task 8: `job-sluice leads unpark` command

**Files:**
- Modify: `sluice/cli.py` (new `cmd_leads_unpark` after `cmd_leads_expire`; parser after the `expire` sub-parser)
- Test: `tests/test_leads_unpark.py`

**Interfaces:**
- Consumes: `Sluice.unpark_report()`, `Sluice.unpark(slugs)` (Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leads_unpark.py`:

```python
import json

from sluice.cli import _build_parser, cmd_leads_unpark


def _cli(tmp_path, monkeypatch, argv):
    real_init = Sluice.__init__
    def init(self, config=None, **kw):
        real_init(self, config or Config(), store=Vault(str(tmp_path)), today=lambda: TODAY)
    monkeypatch.setattr(Sluice, "__init__", init)
    args = _build_parser().parse_args(["leads", "unpark", *argv])
    return cmd_leads_unpark(args, Config())


def test_the_bare_command_lists_and_writes_nothing(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, triage_blocked=STAMP)
    before = _bytes(tmp_path)
    assert _cli(tmp_path, monkeypatch, []) == 0
    out, err = capsys.readouterr()
    assert slug in out and STAMP in out and "example.invalid" in out
    assert "0 written (--apply to move)" in err
    assert _bytes(tmp_path) == before


def test_a_bare_apply_moves_every_listed_lead(tmp_path, monkeypatch):
    a = _seed(tmp_path, triage_blocked=STAMP)
    b = _seed(tmp_path, n=1, triage_blocked=STAMP)
    assert _cli(tmp_path, monkeypatch, ["--apply"]) == 0
    assert {_by_slug(tmp_path, s).status for s in (a, b)} == {"needs_review"}


def test_apply_with_an_unknown_slug_moves_the_valid_one_and_exits_1(tmp_path, monkeypatch):
    a = _seed(tmp_path, triage_blocked=STAMP)
    assert _cli(tmp_path, monkeypatch, ["--apply", a, "Example Nowhere - Nothing"]) == 1
    assert _by_slug(tmp_path, a).status == "needs_review"


def test_apply_exits_1_when_a_lead_abstains(tmp_path, monkeypatch):
    from tests.conftest import racing_read
    slug = _seed(tmp_path, triage_blocked=STAMP)
    ref = _by_slug(tmp_path, slug).ref
    racing_read(monkeypatch, ref, lambda: Vault(str(tmp_path)).update_fields(
        ref, {"triage_blocked": "2031-03-13"}))
    assert _cli(tmp_path, monkeypatch, ["--apply"]) == 1


def test_apply_on_an_empty_report_exits_0(tmp_path, monkeypatch):
    assert _cli(tmp_path, monkeypatch, ["--apply"]) == 0


def test_json_report_shape(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, triage_blocked=STAMP)
    assert _cli(tmp_path, monkeypatch, ["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc == {"leads": [{"slug": slug, "company": "Example Ltd",
                              "role": _by_slug(tmp_path, slug).fm["role"],
                              "stamp": STAMP, "host": "example.invalid"}]}


def test_json_apply_shape_lists_only_the_named_lead(tmp_path, monkeypatch, capsys):
    a = _seed(tmp_path, triage_blocked=STAMP)
    _seed(tmp_path, n=1, triage_blocked=STAMP)
    assert _cli(tmp_path, monkeypatch, ["--apply", a, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["slug"] for r in doc["leads"]] == [a]
    assert doc["results"] == [{"slug": a, "outcome": "moved"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leads_unpark.py -v -k "bare or apply or json"`
Expected: FAIL with `ImportError: cannot import name 'cmd_leads_unpark'`

- [ ] **Step 3: Write minimal implementation**

In `sluice/cli.py`, after `cmd_leads_expire`:

```python
def cmd_leads_unpark(args, config) -> int:
    """#302: list `research` leads triage could not judge from their page, and with
    `--apply` move them to `needs_review`. Report-first like every `leads` pass that writes
    over a set the TOOL computed."""
    from sluice.core.app import Sluice

    app = Sluice(config)

    def _row(r):
        return {"slug": r.slug, "company": r.company, "role": r.role,
                "stamp": r.stamp, "host": r.host}

    if args.apply is not None:       # NOT truthiness: [] is the bulk case and is falsy
        rows, results = app.unpark(slugs=args.apply)
        if args.json:
            print(json.dumps({"leads": [_row(r) for r in rows],
                              "results": [{"slug": s, "outcome": o} for s, o in results]}))
        counts = {}
        for slug, outcome in results:
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome != "moved":
                print(f"unpark: {slug}: {outcome}", file=sys.stderr)
        print("unpark: " + (", ".join(f"{n} {o}" for o, n in sorted(counts.items()))
                            or "nothing to move"), file=sys.stderr)
        # cmd_leads_expire's rule: any outcome where a write the user asked for did not
        # happen exits non-zero, so an apply that moved nothing never reads as success.
        _FAILED = {"abstained", "conflict", "unreadable", "no-match", "ambiguous"}
        return 1 if any(o in _FAILED for _, o in results) else 0

    report = app.unpark_report()
    if args.json:
        print(json.dumps({"leads": [_row(r) for r in report]}))
        return 0
    for r in report:
        print(f"[blocked] {r.slug}  stamped {r.stamp}  {r.host}")
    print(f"unpark: {len(report)} listed, 0 written (--apply to move)", file=sys.stderr)
    return 0
```

After the `expire` sub-parser's `ex.set_defaults(func=cmd_leads_expire)`:

```python
    up = leads.add_parser("unpark",
                          help="list research leads triage could not judge from their page; "
                               "--apply moves them to needs_review")
    # `nargs="*", default=None` + an `is not None` dispatch, for the reason the `expire`
    # parser above gives: a bare flag parses to a FALSY [].
    up.add_argument("--apply", nargs="*", default=None, metavar="SLUG",
                    help='move the listed leads; name slugs to narrow, e.g. '
                         '--apply "Example Ltd - Example Role"')
    up.add_argument("--json", action="store_true", help="machine-readable output")
    up.set_defaults(func=cmd_leads_unpark)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leads_unpark.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/cli.py tests/test_leads_unpark.py
git commit -m "feat(leads): add job-sluice leads unpark (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 6: Mutation witnesses (restore with `git checkout -- sluice/cli.py` after each)**

| Mutation | Row that must go red |
|---|---|
| change `if args.apply is not None:` to `if args.apply:` | `test_a_bare_apply_moves_every_listed_lead` |
| delete `"abstained", ` from `_FAILED` | `test_apply_exits_1_when_a_lead_abstains` |
| delete `"no-match", ` from `_FAILED` | `test_apply_with_an_unknown_slug_moves_the_valid_one_and_exits_1` |

---

### Task 9: Docs, regenerate, full verification

**Files:**
- Modify: `docs/USAGE.md` (new section after `leads expire`), `README.md` (Commands table row), `docs/ARCHITECTURE.md` (triage and `leads` passes paragraphs), `.rulesync/rules/CLAUDE.md` (`leads` passes paragraph)
- Regenerate: `npm run rulesync`

- [ ] **Step 1: Run the doc-claims tests to see them fail**

Run: `.venv/bin/python -m pytest tests/test_docs_claims.py -v`
Expected: FAIL: `leads unpark` exists in the parser but not in `docs/USAGE.md` or README's Commands table.

- [ ] **Step 2: `docs/USAGE.md`** — insert after the `leads expire` section:

```markdown
### `job-sluice leads unpark [--apply [SLUG ...]] [--json]`

Lists `research` leads that triage could not judge from their page (stdout, one row per lead):
```
[blocked] <slug>  stamped <date>  <host>
```
When the judge, or the check that a job description arrived, returns `unjudgeable` for a
`research` lead, triage may not overwrite it (status cannot tell that lead from a research
task you set yourself), so it records a dated `triage_blocked` stamp instead. The nightly
digest counts these. A lead waiting on a follow-up reply is not listed.

`--apply` (bare, or naming specific slugs) moves the listed leads to `needs_review`, clears the
stamp and appends a dated note. Each move re-checks the lead first and abstains if it has been
re-judged, re-stamped, moved off `research` or marked asked since the listing. With `--apply`,
exits 1 if any outcome is `abstained`/`conflict`/`unreadable`/`no-match`/`ambiguous`.
`--json` prints `{"leads": [...]}`, plus `"results"` with `--apply`.
```

- [ ] **Step 3: `README.md`** — in the Commands table row for `job-sluice leads`, change `(`add`, `dedupe`, `expire`, `dismiss`, `reconcile`, `rename`)` to `(`add`, `dedupe`, `expire`, `unpark`, `dismiss`, `reconcile`, `rename`)`.

- [ ] **Step 4: `docs/ARCHITECTURE.md`** — after the paragraph that introduces `job-sluice leads expire` (it begins "makes it stale. `job-sluice leads expire` REPORTS the stale set"), add:

```markdown
**`job-sluice leads unpark`** (#302) clears leads parked in `research` by the pre-#300
conservative score. `_VERDICT_REQUIRE` and `_DECISION_REQUIRE` refuse to write `unjudgeable`
over `research`, because `status` records where a lead is, never how it got there. So the
refusal is recorded instead: `triage/apply.py::_stamp_blocked` sets `triage_blocked` to the
date, through `update_fields` with `require_status={"research"}` and
`require_blank={"followup_asked"}`, and only for a lead whose snapshot is at `research`. Any
triage write that lands clears a stamp in the same write (`require_unchanged` on the stamp,
merged with the `followup_asked` one), and so does recording a follow-up answer. A stamp is
only a one-line ISO date (`core/leads.py::blocked_stamp`); any other value is the person's own
text and is never listed or cleared. The engine counts the outcome in
`TriageReport.blocked_research`, separate from `counts`, and never audits it. The pass reports
by default; `--apply` moves each listed lead to `needs_review` with `require_status`,
`require_blank` and `require_unchanged={"triage_blocked": <listed stamp>}` all re-read fresh,
so a lead changed since the listing abstains. Residual: a lead moved off `research` by
`leads dismiss`, `leads expire` or the reverdict pass keeps its stamp until a triage write
clears it, so a lead later hand-set back to `research` is listed with that older date.
```

- [ ] **Step 5: `.rulesync/rules/CLAUDE.md`** — in the paragraph beginning "**The `leads` passes report by default", change `leads expire` (`--expire [SLUG...]`) and `leads reconcile` (`--apply`, #1)` to `leads expire` (`--expire [SLUG...]`), `leads unpark` (`--apply [SLUG ...]`, #302) and `leads reconcile` (`--apply`, #1)`. Then regenerate:

Run: `npm ci --ignore-scripts && npm run rulesync && git status --short`
Expected: only tracked files you edited are modified (generated outputs are gitignored).

- [ ] **Step 6: Full verification**

Run each and expect the stated result:

- `.venv/bin/python -m pytest -q` → all pass
- `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q` → all pass
- `TZ=Pacific/Kiritimati .venv/bin/python -m pytest -q tests/test_triage_blocked.py tests/test_leads_unpark.py tests/test_followups_facade.py` → all pass
- `.venv/bin/ruff check sluice tests scripts` → `All checks passed!`
- `grep -rn "triage_blocked" sluice | grep -v "^sluice/core/leads.py\|^sluice/triage/apply.py\|^sluice/core/app.py"` → no output (the key has exactly the readers and writers this plan adds)

- [ ] **Step 7: Commit**

```bash
git add docs/USAGE.md README.md docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md
git commit -m "docs: document leads unpark and the triage_blocked stamp (#302)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

- [ ] **Step 8: Per-commit check**

Run: `git rebase -x ".venv/bin/python -m pytest -q -x tests/test_triage_blocked.py tests/test_apply.py tests/test_leads_unpark.py tests/test_followups_facade.py tests/test_triage_run_cli.py" origin/main`
Expected: every commit passes the targeted files (the full suite already ran at the tip).
