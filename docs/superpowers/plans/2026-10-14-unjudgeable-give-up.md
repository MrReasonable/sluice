# Unjudgeable give-up (#402) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop re-fetching and re-judging leads that keep coming back `unjudgeable`, behind an opt-in threshold `triage.unjudgeable_give_up_after` (0 = off).

**Architecture:** A per-lead frontmatter counter `triage_unjudgeable_count` rides the existing status write in `triage/apply.py`; the engine evicts the cached dossier on a landed judge `unjudgeable` so every count is a fresh fetch, and withholds leads at or above the threshold from the fetch/judge path after the free classify pass.

**Tech Stack:** Python 3.12+ stdlib, pytest, the real `Vault` store.

**Spec:** `docs/superpowers/specs/2026-10-14-unjudgeable-give-up-design.md` — every task implements a section of it; read the matching section before each task.

## Global Constraints

- `sluice/` stays standard-library only; no new dependency.
- `0` means OFF: at `0` nothing is counted, nothing evicted, nothing skipped (spec Decision 4). Only §5's clear of a leftover count runs at `0`.
- Every write goes through `Vault.update_fields` (never a new write function: CodeQL sink).
- Never cite a line number in a comment or docstring; cite `file.py::symbol`.
- Comments explain WHY, matching the surrounding density.
- Conventional commits; end every commit message with `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Verification: `.venv/bin/python -m pytest -q -p no:cacheprovider` (exit 0) and `.venv/bin/ruff check sluice tests scripts`.

## Review Focus

1. A hand-typed block list under `triage_next` on an `unjudgeable` lead must survive a counting write (the `_HAND_EDITABLE_KEYS` merge) — Task 2.
2. A lead with NO counter that gets a real verdict must gain no key — Task 2.
3. `--limit 1` on a vault full of given-up leads must still fetch/judge one other lead — Task 4.
4. A given-up lead must not be reported as "passed the pre-gate / still waiting for a judge" in the `--no-llm` digest — Task 5.
5. A `cv run` racing a triage eviction must refetch, not crash — Task 3.

---

### Task 1: Config knob, validator, neutral-default guards, example, reference row

**Files:**
- Modify: `sluice/triage/config.py` (`TriageConfig`, `load_triage_config`)
- Modify: `sluice.yaml.example` (triage block, commented entry)
- Modify: `docs/CONFIGURATION.md` (triage table row)
- Test: `tests/test_sluice_neutral_defaults.py`

**Interfaces:** Produces `TriageConfig.unjudgeable_give_up_after: int = 0`.

- [ ] **Step 1: failing tests** beside the `min_jd_chars` block:

```python
def test_unjudgeable_give_up_after_dataclass_default_is_off():
    assert TriageConfig().unjudgeable_give_up_after == 0

def test_unjudgeable_give_up_after_loader_default_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("SLUICE_CONFIG", str(tmp_path / "absent.yaml"))
    assert load_triage_config().unjudgeable_give_up_after == 0

def test_unjudgeable_give_up_after_absent_key_abstains(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"; p.write_text("triage:\n  batch_size: 5\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_triage_config().unjudgeable_give_up_after == 0

def test_unjudgeable_give_up_after_round_trips(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"; p.write_text("triage:\n  unjudgeable_give_up_after: 3\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_triage_config().unjudgeable_give_up_after == 3

@pytest.mark.parametrize("raw", ["yes", "on", "true", "True", "-1", "2.5", '"3"'])
def test_unjudgeable_give_up_after_refuses_non_counts(tmp_path, monkeypatch, raw):
    p = tmp_path / "c.yaml"; p.write_text(f"triage:\n  unjudgeable_give_up_after: {raw}\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="unjudgeable_give_up_after"):
        load_triage_config()

def test_example_config_ships_unjudgeable_give_up_after_off():
    # mirror test_example_config_ships_min_jd_chars_off: load the shipped example and assert 0
```

(Copy the exact loader/example mechanics from the existing `min_jd_chars`/`lead_ttl_days` tests in the same file; the names above are the contract.)

- [ ] **Step 2:** run `.venv/bin/python -m pytest tests/test_sluice_neutral_defaults.py -k unjudgeable -q` → FAIL (AttributeError).
- [ ] **Step 3: implement.** Field on `TriageConfig` with a why-comment (spec §6). After the overlay loop in `load_triage_config`, unconditionally:

```python
    v = cfg.unjudgeable_give_up_after
    if isinstance(v, bool) or type(v) is not int or v < 0:
        raise ValueError(
            f"triage.unjudgeable_give_up_after must be a whole number of 0 or more (0 = never "
            f"give up), got {v!r}. ...why bool is checked first...")
```

Commented example entry with the "set YOUR OWN" marker; CONFIGURATION.md row.
- [ ] **Step 4:** run the file plus `tests/test_config_example.py` → PASS. Witness: set the dataclass default to 3, the default guards go red; uncomment the example line, the example guard goes red. Restore.
- [ ] **Step 5:** commit `feat(triage): add triage.unjudgeable_give_up_after (off by default) (#402)`.

### Task 2: Counter parser, counting, clearing in `triage/apply.py`

**Files:** Modify `sluice/triage/apply.py`; Test: create `tests/test_triage_give_up.py`.

**Interfaces:**
- Produces `unjudgeable_count(fm: dict) -> int | None` (spec §1).
- `apply_classification(vault, note, decision, reason, *, count_unjudgeable: bool = False) -> str`
- `apply_verdict(vault, note, verdict, dossier, *, count_unjudgeable: bool = False) -> str`
- `_HAND_EDITABLE_KEYS` gains `"triage_unjudgeable_count"`.

- [ ] **Step 1: failing tests** (real `Vault`, the `_note`/`_text` helpers and the fixed-date fixture copied from `tests/test_triage_blocked.py`):
  - `test_unjudgeable_count_reads_a_real_note` parametrised: absent→0, `""`→0, `0`→0, `3`→3, `"3"`→3, `'3'`→3, block list→0; `five`, `-1`, `3.0`, `true`, block scalar `|`→None.
  - `test_counting_off_by_default_writes_no_key` (no keyword → no key).
  - `test_a_judge_unjudgeable_counts_from_new_and_from_unjudgeable` (absent→1, 1→2) and the same through `apply_classification`.
  - `test_a_research_lead_is_stamped_and_not_counted`.
  - `test_a_lead_moved_out_mid_judge_is_not_counted` (human sets `shortlist` after snapshot).
  - `test_a_count_reset_mid_judge_is_not_restored` (snapshot 2, human writes `""`, stale verdict → `unchanged`, key still blank) and `..._mid_classify_...`.
  - `test_a_block_list_counter_lets_the_status_land` (status `unjudgeable`, counter lines byte-identical).
  - `test_an_unreadable_counter_is_left_byte_identical` parametrised over the single-line unreadables.
  - `test_a_hand_typed_triage_next_block_survives_a_counting_verdict`.
  - `test_a_real_verdict_clears_a_count`, `test_a_real_verdict_adds_no_key_to_an_uncounted_lead`, `test_counter_and_triage_blocked_clear_together`, `test_an_unreadable_counter_survives_a_real_verdict`, `test_a_reject_classification_clears_a_count`.
  - `test_a_count_edited_mid_judge_refuses_the_clearing_verdict` (snapshot 3, human sets 5, shortlist verdict → `unchanged`, stays 5) and `..._clearing_classification`.
- [ ] **Step 2:** run → FAIL (ImportError on `unjudgeable_count`).
- [ ] **Step 3: implement** (spec §1, §2, §5):

```python
_COUNT_KEY = "triage_unjudgeable_count"
_COUNT_RE = re.compile(r"[0-9]+")

def unjudgeable_count(fm: dict) -> int | None:
    raw = fm.get(_COUNT_KEY) or ""
    if "\n" in raw:
        return None
    s = raw.strip()
    if not s:
        return 0
    return int(s) if _COUNT_RE.fullmatch(s) else None

def _count_fields(note, decision_is_unjudgeable: bool, count_unjudgeable: bool,
                  fields: dict, require_unchanged: dict) -> None:
    raw = note.fm.get(_COUNT_KEY) or ""
    k = unjudgeable_count(note.fm)
    if decision_is_unjudgeable:
        if not count_unjudgeable or k is None:
            return
        fields[_COUNT_KEY] = str(k + 1)
        if raw.strip():
            require_unchanged[_COUNT_KEY] = raw
    elif k:   # an int > 0: clear it, under CAS
        fields[_COUNT_KEY] = '""'
        require_unchanged[_COUNT_KEY] = raw
```

Both writers build `require_unchanged` as a dict, call `_count_fields`, and pass `require_unchanged or None`. Add the key to `_HAND_EDITABLE_KEYS` with a comment.
- [ ] **Step 4:** run the new file and `tests/test_triage_blocked.py tests/test_triage_apply*.py` → PASS. Witnesses (one at a time, restore after each): delete the counting `require_unchanged` line → mid-judge reset row red; delete the clear's `require_unchanged` line → clear-CAS row red; drop the key from `_HAND_EDITABLE_KEYS` → block-list row red.
- [ ] **Step 5:** commit `feat(triage): count consecutive unjudgeable results on the lead (#402)`.

### Task 3: `DossierCache.forget` and the eviction race

**Files:** Modify `sluice/core/dossier.py`; Test: `tests/test_dossier_forget.py` (new).

**Interfaces:** Produces `DossierCache.forget(lead: dict) -> None`.

- [ ] **Step 1: failing tests:** `test_forget_removes_the_entry_for_that_lead`, `test_forget_on_a_missing_entry_is_silent`, `test_forget_propagates_other_os_errors` (monkeypatch `os.remove` to raise `PermissionError`), `test_get_or_build_refetches_when_the_entry_vanishes_after_the_freshness_check` (monkeypatch `_fresh` to delete the file and return True; assert the fetcher is called and a dossier is returned).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: implement:**

```python
    def forget(self, lead: dict) -> None:
        try:
            os.remove(self._path(lead))
        except FileNotFoundError:
            pass
```

and in `get_or_build`, the fresh read:

```python
        if self._fresh(path):
            try:
                return json.loads(open(path, encoding="utf-8").read())
            except FileNotFoundError:
                pass    # evicted between the check and the read: a miss, fetch below
```

Update the `get_or_build` #169 comment: `jd_arrived` owns what is persisted; the triage engine may evict through `forget`.
- [ ] **Step 4:** run → PASS; full `tests/test_dossier*.py`.
- [ ] **Step 5:** commit `feat(dossier): let a caller evict one cached dossier (#402)`.

### Task 4: Engine — count, evict, give up

**Files:** Modify `sluice/triage/engine.py` (`TriageReport.counts`, `run`); Test: `tests/test_triage_give_up.py` (engine section); Modify `tests/test_triage_record.py::_COUNTS`.

**Interfaces:** Consumes Tasks 1–3. Produces `report.counts["given_up"]`.

- [ ] **Step 1: failing tests** (two-lead harness: a recording fetcher `calls.append(lead["url"])` returning a JD long enough to pass `jd_arrived`, the `_Unjudgeable` stub and a `_Shortlist` stub recording prompts, cold cache, filenames `a-*.md`/`b-*.md` to fix order, titles from the `titles` fixture):
  - `test_counts_on_both_producers_through_the_engine` (pre-gate with empty JD; judge with chrome JD).
  - `test_a_given_up_lead_is_not_fetched_or_judged` (N=2, lead at 2 vs control at 1; given-up url not in calls, slug not in any prompt; `given_up == 1`, `keep == 1`).
  - Predicate rows: `new` at ≥N judged; pasted-JD at ≥N judged; N-1 judged; N=0 judged; section read raising → not given up.
  - `test_given_up_under_dry_run_and_explicit_status`.
  - `test_given_up_leads_do_not_consume_the_limit` and `test_limit_with_several_given_up_leads`.
  - `test_a_given_up_lead_still_meets_reject_rules` (reject_companies matching → dismissed, count cleared).
  - `test_section_results_count_nothing` (short section; judge-unjudgeable on a section dossier).
  - forget rows: run twice at N=3 cold cache → unjudgeable lead fetched 1→2, shortlist control stays 1; refused research lead keeps a pre-seeded file; pasted-JD lead keeps a pre-seeded file at its key; N=0 keeps the file; dry-run keeps the file; twin sharing `cache_key` refetches; `forget` raising `PermissionError` → `report.failures` and the run continues.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: implement** (spec §2–§4):
  - `"given_up": 0` in `TriageReport.counts`' default dict.
  - `N = cfg.unjudgeable_give_up_after` once at the top of `run`.
  - `_given_up(note)` closure: `N > 0 and normalize(status) == "unjudgeable" and (k := unjudgeable_count(fm)) is not None and k >= N and not section_text`, where a `jdsection.read` raise → False.
  - After the `awaiting_reply` filter: `given = [n for n in notes if _given_up(n)]`, `notes = [n for n in notes if not in given]`, limit cut, `notes = notes + given`, `given_ids = {n.ref for n in given}`.
  - Classify pass: at `decision == "keep"`, if `note.ref in given_ids`: `report.counts["given_up"] += 1; continue` BEFORE the `keep` increment.
  - Pre-gate: `apply_classification(..., count_unjudgeable=N > 0 and i not in from_section)`.
  - Dossier assembly: `if i in from_section: section_ids.add(d["lead_id"])`.
  - Verdict apply: `apply_verdict(..., count_unjudgeable=N > 0 and lead_id not in section_ids)`; after it, if `N > 0 and outcome == "applied" and clamp_verdict(...) == "unjudgeable" and lead_id not in section_ids`: `try: dossier_cache.forget(note.fm) except OSError as e: report.failures.append(f"dossier {note.ref}: could not drop the cached page ({e}); the next count reads it again")`.
- [ ] **Step 4:** run the new file plus `tests/test_triage_engine.py tests/test_triage_blocked.py tests/test_triage_record.py` → PASS. Witnesses: move the split below `if limit:` → limit row red; move the withhold below `_prefetch_dossiers` → not-fetched row red; move it above classify → reject-rules row red; delete each of the two `from_section`/`section_ids` overrides → its row red; pass `dossier` instead of `note.fm` to `forget` → run-twice row red; delete `outcome == "applied"` → refused-research row red; delete `N > 0` from the forget condition → N=0 row red.
- [ ] **Step 5:** commit `feat(triage): give up on leads that stay unjudgeable (#402)`.

### Task 5: Digest, help text, docs

**Files:** Modify `sluice/cli.py` (`_TRIAGE_FILTERED_WORDS`, `--limit` help), `docs/USAGE.md`, `docs/ARCHITECTURE.md`, `docs/TROUBLESHOOTING.md`; Test: `tests/test_triage_run_cli.py` (or wherever `cmd_triage_run` digest tests live).

- [ ] **Step 1: failing tests:** `test_digest_reports_given_up_with_the_retry_instruction` (counts `given_up=2` → output contains `2 given up` and `triage_unjudgeable_count`); `test_no_llm_digest_does_not_call_given_up_leads_waiting` (a report with `given_up=1, keep=0` under no-llm renders no "still waiting" line).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: implement:** row `("given_up", "{n} given up after repeated unjudgeable results (delete triage_unjudgeable_count from a note, or paste its posting under ## Job description, to retry)")`; `--limit` help "cap the leads fetched or judged"; docs per spec "Docs".
- [ ] **Step 4:** full suite and ruff → exit 0. Also `env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q -p no:cacheprovider`.
- [ ] **Step 5:** commit `docs(triage): document giving up on unjudgeable leads (#402)`.
