# Vault note-name byte-cap + lead-level write isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix #24 — a long non-ASCII note name no longer raises `ENAMETOOLONG`, and a single unwriteable lead no longer aborts the whole ingest run.

**Architecture:** Two independent changes. (1) `Vault._path_for` gains a byte-clamp applied *after* the existing 120-character cap, sized to the filesystem's `NAME_MAX`; the char-cap-first ordering makes the clamp a proven no-op for every note already on disk. (2) `VaultSink.write` wraps each per-lead `upsert` so an `OSError` becomes a counted `skipped` (excluded from `seen.db`, retried next run) instead of propagating out of the run.

**Tech Stack:** Python 3.12+ standard library only. pytest + faker for tests. No new dependencies.

**Design doc:** `docs/superpowers/specs/2026-07-18-vault-note-name-bytecap-design.md`

## Global Constraints

Every task's requirements implicitly include these (copied verbatim from CLAUDE.md and the spec):

- **`sluice/` is standard-library only.** `_clamp_bytes` and `os.pathconf` are stdlib; add no runtime dependency.
- **Never-clobber (writes).** A re-scrape of an existing lead touches only `last_seen` — never status, enrichment, or body. The byte-clamp must move **zero** existing notes' identities; the char-cap-first ordering is what guarantees that and is non-negotiable.
- **Neutrality: no personal data in this repo.** No employer names, locations, or contact details in `sluice/` or `tests/`. Fixtures are synthetic — use faker or explicit synthetic non-ASCII strings (CJK/emoji), never a real company or place.
- **Comments explain *why*** — the invariant upheld or the bug prevented. Match the existing density.
- **Conventional commits** (`fix(vault): ...`, `test(vault): ...`), each ending with the trailer:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Mutation discipline.** Before mutating anything to prove a test is load-bearing, run once:
  `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests`
  Then mutate by **moving or deleting**, never by adding a check beside the original (an added check is an equivalent mutant and stays green). Run the mutant in isolation and look at what the function returns.
  Use the **same** interpreter for `compileall` and pytest (both `.venv/bin/python`) — a bare `python` on a different CPython writes to a different `__pycache__` tag dir that pytest never imports, silently defeating the content-addressing. This needs the gitignored `.venv/` to exist; create it first (`python -m venv .venv && .venv/bin/pip install -e ".[test]"`), the same venv `run_tests.sh` uses.
- **Tests are offline, hermetic, and assert on behaviour**, not merely that code runs.

**Scope boundary:** The collision class (two distinct leads whose truncated names match → the second silently bumps the first's `last_seen`) is **out of scope** — it is issue #5. Do not add a discriminator here.

---

### Task 1: `_clamp_bytes` — a codepoint-safe byte truncator

A pure helper: the largest UTF-8 prefix of a string that fits in a byte budget, never splitting a multi-byte codepoint. Isolated and independently testable so its boundary behaviour is pinned before `_path_for` consumes it.

**Files:**
- Modify: `sluice/core/vault.py` (add module-level function near the other helpers at the bottom, e.g. after `_fm_dict`)
- Test: `tests/test_vault.py` (add tests; existing file)

**Interfaces:**
- Consumes: nothing.
- Produces: `_clamp_bytes(s: str, limit: int) -> str` — returns the longest prefix of `s` whose UTF-8 encoding is ≤ `limit` bytes, always valid UTF-8.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vault.py`:

```python
from sluice.core.vault import _clamp_bytes


def test_clamp_bytes_keeps_string_within_budget_unchanged():
    assert _clamp_bytes("hello", 100) == "hello"


def test_clamp_bytes_truncates_ascii_to_byte_budget():
    assert _clamp_bytes("hello", 3) == "hel"


def test_clamp_bytes_never_splits_a_multibyte_codepoint():
    # "測" encodes to 3 UTF-8 bytes. A 4-byte budget must keep exactly one whole
    # char, never one-and-a-fraction — the guarantee _path_for relies on.
    out = _clamp_bytes("測測", 4)
    assert out == "測"
    assert len(out.encode("utf-8")) <= 4
    out.encode("utf-8").decode("utf-8")  # must be valid UTF-8 (no exception)


def test_clamp_bytes_boundary_exact_and_too_small():
    assert _clamp_bytes("測", 3) == "測"   # exact fit
    assert _clamp_bytes("測", 2) == ""     # cannot fit even one whole char
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault.py -k clamp_bytes -v`
Expected: FAIL — `ImportError: cannot import name '_clamp_bytes'`.

- [ ] **Step 3: Implement `_clamp_bytes`**

Add to `sluice/core/vault.py` (module scope, with the other `_`-helpers):

```python
def _clamp_bytes(s: str, limit: int) -> str:
    """Largest UTF-8 prefix of `s` within `limit` bytes, never splitting a codepoint.
    Slicing the encoded bytes can cut mid-sequence; decode(errors="ignore") then drops
    the incomplete trailing bytes, which IS the 'never split a codepoint' guarantee."""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault.py -k clamp_bytes -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Prove the tests are load-bearing (mutation check)**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Mutate `_clamp_bytes` by **replacing** the body (move/delete, not add) with a naive char slice and confirm RED, then restore:

```python
    return s[:limit]                       # char slice, not byte-aware
```
Run: `.venv/bin/python -m pytest tests/test_vault.py -k clamp_bytes -v`
Expected: FAIL on `test_clamp_bytes_never_splits...` (`"測測"[:4]` == `"測測"` → 6 bytes > 4).

Then mutate to drop `errors="ignore"`:
```python
    return s.encode("utf-8")[:limit].decode("utf-8")
```
Expected: FAIL on `test_clamp_bytes_never_splits...` with `UnicodeDecodeError`.

Restore the correct implementation. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "fix(vault): add _clamp_bytes, a codepoint-safe byte truncator

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: byte-clamp `_path_for` against the filesystem's NAME_MAX

Wire `_clamp_bytes` into the note-naming path, sized to `os.pathconf`'s `PC_NAME_MAX` (255 fallback), applied **after** the 120-char cap. Prove the crash is fixed AND that no existing note's identity moves.

**Files:**
- Modify: `sluice/core/vault.py` — `Vault.__init__` (line 70-73), add `Vault._name_max` method, modify `_path_for` (line 83-87)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `_clamp_bytes(s, limit)` from Task 1.
- Produces: `Vault._name_max() -> int` (cached filesystem name-length limit in bytes); `_path_for` unchanged in signature.

- [ ] **Step 1: Write the failing tests**

Add `import os` to the top of `tests/test_vault.py` (needed by the pathconf tests), then add (reuse the existing `_lead` and `_leads_dir` helpers already at the top of the file):

```python
def test_long_non_ascii_name_fits_the_byte_budget(tmp_path):
    # A 120-CHARACTER CJK company is ~360 bytes — over NAME_MAX. Inject a small
    # budget so the assertion is filesystem-independent, then verify the written
    # filename fits it and decodes cleanly (no split codepoint).
    v = Vault(str(tmp_path))
    v._name_max_cache = 64
    v.upsert(_lead(company="測" * 200, title="X"))
    files = list(_leads_dir(tmp_path).glob("*.md"))
    assert len(files) == 1
    name = files[0].name
    assert len(name.encode("utf-8")) <= 64        # whole filename within budget
    name.encode("utf-8").decode("utf-8")          # valid UTF-8, no partial char


def test_byte_clamp_is_a_noop_for_a_name_that_fits(tmp_path):
    # never-clobber guard: a name already within the byte budget MUST keep the exact
    # char-capped path it has today, or a re-scrape would create a duplicate note.
    # Inject the budget (255) explicitly so the assertion does NOT silently ride the
    # pathconf-failure fallback: _path_for is called directly here, so leads_dir does
    # not exist yet and a real os.pathconf would raise.
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    lead = _lead(company="X" * 200, title="Y")     # f-string -> 120 'X' after [:120]
    expected = _leads_dir(tmp_path) / ("X" * 120 + ".md")
    assert v._path_for(lead) == str(expected)


def test_name_max_reads_pathconf(tmp_path, monkeypatch):
    # The SUCCESS branch: _name_max returns the filesystem's real PC_NAME_MAX, not the
    # 255 fallback. Mock pathconf to a non-255 value so a hardcoded-255 mutant reddens
    # even on a 255-limit filesystem where the fallback would otherwise mask it.
    v = Vault(str(tmp_path))
    os.makedirs(v.leads_dir, exist_ok=True)        # pathconf needs an existing path
    monkeypatch.setattr(os, "pathconf", lambda *a: 143)
    assert v._name_max() == 143


def test_name_max_falls_back_when_pathconf_unsupported(tmp_path, monkeypatch):
    # The FALLBACK branch: pathconf unsupported (some network/FUSE mounts) -> 255.
    v = Vault(str(tmp_path))
    def _boom(*a):
        raise OSError("PC_NAME_MAX unsupported")
    monkeypatch.setattr(os, "pathconf", _boom)
    assert v._name_max() == 255
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault.py -k "byte_budget or noop_for_a_name or name_max" -v`
Expected, precisely (the RED is genuine; the *reason* matters so you don't misread the traceback):
- `test_long_non_ascii_name_fits_the_byte_budget` ERRORS red: on a 255-byte-limit FS the old `_path_for` produces a ~360-byte basename and `v.upsert(...)` raises `OSError`/`ENAMETOOLONG` at `open()`, *before* the byte assertion runs. (Setting `v._name_max_cache = 64` on an instance whose `__init__` doesn't define the attribute is legal Python and harmless — the old `_path_for` never reads it — so there is no `AttributeError`.)
- `test_name_max_reads_pathconf` and `test_name_max_falls_back_when_pathconf_unsupported` FAIL with `AttributeError: 'Vault' object has no attribute '_name_max'` (method not added yet).
- `test_byte_clamp_is_a_noop_for_a_name_that_fits` PASSES already (the old `_path_for` yields `X*120.md`, and `_name_max_cache = 255` is an unread no-op on it) — expected; it is a regression guard for Step 3, not a fail-first test.

- [ ] **Step 3: Implement the cache field, `_name_max`, and the clamp**

In `Vault.__init__` (currently line 70-73), add the cache field:

```python
    def __init__(self, dir: str | None = None, *, baseline_rel: str = _MYCV_BASELINE):
        self.dir = dir or os.environ.get("VAULT_DIR", _DEFAULT_VAULT)
        self.leads_dir = os.path.join(self.dir, _LEADS_SUBDIR)
        self.baseline_rel = baseline_rel
        self._name_max_cache: int | None = None
```

Add the `_name_max` method (next to `_path_for`):

```python
    def _name_max(self) -> int:
        """The filesystem's max filename length in BYTES for the leads dir, cached.
        os.pathconf needs an existing path; in the normal flow upsert makes leads_dir
        before _path_for runs. A direct _path_for call before the dir exists (e.g. a
        unit test) just takes the 255 fallback below, which also covers filesystems
        where pathconf is unsupported (some network/FUSE mounts)."""
        if self._name_max_cache is None:
            try:
                self._name_max_cache = os.pathconf(self.leads_dir, "PC_NAME_MAX")
            except (OSError, ValueError, AttributeError):
                self._name_max_cache = 255
        return self._name_max_cache
```

Modify `_path_for` (currently line 83-87). The 120-char cap stays FIRST; the byte-clamp is applied after, sized to leave room for the `.md` extension:

```python
    def _path_for(self, lead: Lead) -> str:
        """Match the old pipeline's naming exactly, so an existing note for the same
        company+role is UPDATED in place rather than duplicated.

        The cap is (1) 120 CHARACTERS then (2) a byte-clamp to NAME_MAX. The ORDER is
        load-bearing: char-cap first makes the byte-clamp a no-op for every name already
        on disk (each was written, so its stem is already <= NAME_MAX bytes), so no
        existing note's identity moves. Only names that ENAMETOOLONG today change — and
        they have no note to duplicate. Byte-capping *instead* of char-capping would keep
        more of a 121-255 char ASCII name than today, renaming existing notes -> duplicates.
        """
        safe = f"{lead.company} - {lead.title}"[:120].replace("/", "-").replace(":", "-")
        safe = _clamp_bytes(safe, self._name_max() - len(b".md"))
        return os.path.join(self.leads_dir, f"{safe}.md")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault.py -v`
Expected: PASS — the four new tests plus every pre-existing vault test (the sanitize-slashes and create/update tests confirm the no-op holds for short names).

- [ ] **Step 5: Prove the tests are load-bearing (mutation check)**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Mutate `_path_for` by **removing** the clamp line (delete, not comment-beside):
```python
        safe = f"{lead.company} - {lead.title}"[:120].replace("/", "-").replace(":", "-")
        return os.path.join(self.leads_dir, f"{safe}.md")
```
Expected: `test_long_non_ascii_name_fits_the_byte_budget` errors red — with no clamp, `upsert` writes a ~360-byte name and `open()` raises `ENAMETOOLONG` on a 255-limit FS (the injected 64 budget is unused once the clamp is gone). `test_name_max_reads_pathconf`/`_falls_back` stay green — they exercise `_name_max`, which this mutant leaves intact.

Restore, then mutate to byte-cap ONLY (drop `[:120]` — the issue's literal proposal, the actual duplication trap):
```python
        safe = f"{lead.company} - {lead.title}".replace("/", "-").replace(":", "-")
        safe = _clamp_bytes(safe, self._name_max() - len(b".md"))
        return os.path.join(self.leads_dir, f"{safe}.md")
```
Expected: `test_byte_clamp_is_a_noop_for_a_name_that_fits` FAILs — with the default 255 budget the 200-'X' name keeps 204 chars, not 120, so the path differs from `X*120.md`. Every existing note whose full name is 121–255 chars would be renamed → duplicated on its next re-scrape. This is the trap, caught as a test.

Restore the correct implementation. Confirm green.

**Honest note on ordering.** The design doc says "char-cap FIRST, then byte-clamp." Do **not** try to write a test that catches char-cap↔byte-clamp *reordering*: both operations are prefix truncations, and composing two prefix truncations is commutative — `clamp(s[:120], b) == clamp(s, b)[:120]` for all inputs (verified across ASCII/CJK/mixed at several budgets). Reordering is therefore a provably **equivalent mutant**; no test catches it and none should. What is load-bearing is *retaining* the 120-char cap at all — pinned by the byte-cap-only mutant above. The two tests together pin the real guarantees: (1) an over-long name is clamped into the byte budget, (2) a name that already fits keeps its exact char-capped identity.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "fix(vault): the 120-char note-name cap counts characters, not bytes

_path_for now byte-clamps to the filesystem's NAME_MAX after the 120-char cap,
so a long non-ASCII company/title no longer raises ENAMETOOLONG. Char-cap stays
first: that makes the clamp a no-op for every note already on disk, so no
existing note's identity moves and no duplicate is created.

Refs #24.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: isolate a failed write in `VaultSink.write`

Wrap the per-lead `upsert` so an `OSError` from any one lead becomes a counted `skipped`, is logged, and is left out of `seen.db` (so it retries next run) — instead of propagating and aborting the run.

**Files:**
- Modify: `sluice/ingest/sink.py` — add a module logger, wrap the loop body in `VaultSink.write` (line 23-38), and update the module docstring (it is the sink contract) for `skipped`'s new meaning
- Test: `tests/test_sink.py`

**Interfaces:**
- Consumes: `Vault.upsert(lead) -> str` (may raise `OSError`); `sluice.core.log.get_logger`.
- Produces: `VaultSink.write` unchanged in signature; `skipped` count now non-zero when a lead's write fails.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sink.py`:

```python
def test_vaultsink_isolates_a_failing_write(tmp_path, monkeypatch):
    # One lead the store cannot write must not sink the batch: it is counted
    # `skipped`, kept OUT of seen.db (so it retries next run), and its neighbours
    # are still written.
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    good1 = _lead(company="Aye", url="https://a/1")
    bad = _lead(company="Bee", url="https://a/2")
    good2 = _lead(company="Cee", url="https://a/3")

    real_upsert = vault.upsert

    def flaky(lead):
        if lead.url == "https://a/2":
            raise OSError("simulated store refusal")
        return real_upsert(lead)

    monkeypatch.setattr(vault, "upsert", flaky)
    counts = VaultSink(vault, seen, today=lambda: "2026-07-07").write([good1, bad, good2])

    assert counts == {"created": 2, "updated": 0, "skipped": 1}
    loaded = seen.load()
    assert "https://a/1" in loaded and "https://a/3" in loaded
    assert "https://a/2" not in loaded            # retried next run, not swallowed
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sink.py -k isolates_a_failing_write -v`
Expected: FAIL — the `OSError` propagates out of `write`, so the assertion is never reached (test errors).

- [ ] **Step 3: Add the logger, update the docstring, and guard the loop**

Update the module docstring (lines 1-7) so the sink contract describes `skipped`'s new meaning. Change the VaultSink sentence to:

```python
"""Sinks: where deduped, relevance-passed leads land.

VaultSink stamps first_seen/last_seen, upserts each lead into the Obsidian vault
(never clobbering status), then records it in seen.db so the next run dedups it.
A lead the vault refuses to write (OSError - name too long on an odd FS,
permissions, disk full) is counted `skipped`, logged, and kept OUT of seen.db so
the next run retries it, rather than aborting the run. JsonSink emits one JSON
object per line - for `--sink json` and the legacy-diff tool. Both return
{created, updated, skipped}.
"""
```

At the top of `sluice/ingest/sink.py`, after the existing imports, add:

```python
from sluice.core.log import get_logger

_log = get_logger("ingest.sink")
```

Replace the loop body in `VaultSink.write` (line 23-38) with:

```python
    def write(self, leads) -> dict:
        counts = {"created": 0, "updated": 0, "skipped": 0}
        recorded = []
        for lead in leads:
            stamp = self._today()
            if not lead.first_seen:
                lead.first_seen = stamp
            lead.last_seen = stamp
            try:
                outcome = self.vault.upsert(lead)  # "created" | "updated"
                counts[outcome] = counts.get(outcome, 0) + 1
                recorded.append(lead)
            except OSError as e:
                # A lead the store cannot write (name too long on an odd FS,
                # permissions, disk full) must not sink the batch or the run. Count
                # it, log it, and leave it OUT of `recorded` so it never enters
                # seen.db and is retried next run. See #24. OSError is the filesystem
                # store's failure mode; a future SQLite store would raise sqlite3.Error,
                # so this catch would need widening when that store arrives.
                counts["skipped"] += 1
                _log.warning("vault refused lead %r: %s", lead.dedup_key, e)
        if recorded:
            # Record everything the sink touched so the next run dedups it - some
            # updated leads (pre-existing vault notes) may not yet be in seen.db.
            self.seendb.save(recorded)
        return counts
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sink.py -v`
Expected: PASS — the new isolation test plus the three pre-existing sink tests.

- [ ] **Step 5: Prove the test is load-bearing (mutation check)**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Mutate by **removing** the `try/except` (unguard the write — delete the wrapper, dedent the body):
```python
            outcome = self.vault.upsert(lead)
            counts[outcome] = counts.get(outcome, 0) + 1
            recorded.append(lead)
```
Run: `.venv/bin/python -m pytest tests/test_sink.py -k isolates_a_failing_write -v`
Expected: FAIL — `OSError` propagates, `write` never returns.

Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/ingest/sink.py tests/test_sink.py
git commit -m "fix(ingest): a lead the vault refuses is skipped, not fatal

VaultSink.write wraps each upsert: an OSError becomes a counted `skipped`,
logged, and kept out of seen.db so it retries next run — instead of aborting
the whole ingest run and every source scheduled after it.

Refs #24.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: unlink a partial note on create-failure (`Vault.upsert`)

Task 3's guard makes a residual `OSError` (disk-full/interrupt mid-write) non-fatal — but `_write` opens `"w"`, which creates a truncated 0-byte file at open time. That partial file would be treated as a real note on the next re-scrape (`os.path.exists` True → `"updated"` → `last_seen` bumped on garbage, never re-created). Remove the partial on the create path so the retried lead is created cleanly.

**Files:**
- Modify: `sluice/core/vault.py` — the create branch of `Vault.upsert` (line 285)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: `_path_for`, `_render_new`, `_write` (existing).
- Produces: `Vault.upsert` unchanged in signature; a create whose write fails leaves no file behind and re-raises (so the sink counts it `skipped`).

- [ ] **Step 1: Write the failing test**

Add `import pytest` to the top of `tests/test_vault.py` (and `import os` if Task 2 has not already added it), then add:

```python
def test_upsert_removes_partial_note_when_create_write_fails(tmp_path, monkeypatch):
    # A create whose write fails mid-way must not leave a partial file: open("w")
    # truncates/creates at open time, and a lingering 0-byte note would be treated as
    # real on the next re-scrape (exists -> "updated" -> last_seen bumped on garbage).
    import sluice.core.vault as vault_mod
    v = Vault(str(tmp_path))

    def failing_write(p, text):
        with open(p, "w", encoding="utf-8"):   # leave a 0-byte file, as open("w") does
            pass
        raise OSError("disk full mid-write")

    monkeypatch.setattr(vault_mod, "_write", failing_write)
    with pytest.raises(OSError):
        v.upsert(_lead())
    assert list(_leads_dir(tmp_path).glob("*.md")) == []   # partial artifact removed
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vault.py -k removes_partial_note -v`
Expected: FAIL — `upsert` re-raises the `OSError` (so `pytest.raises` is satisfied), but with no cleanup the 0-byte file lingers, so the final `glob("*.md") == []` assertion fails.

- [ ] **Step 3: Guard the create write**

In `Vault.upsert`, replace the create branch (currently `_write(path, self._render_new(lead)); return "created"`) with:

```python
        try:
            _write(path, self._render_new(lead))
        except OSError:
            # A create whose write fails mid-way leaves open("w")'s truncated 0-byte
            # file behind; a later re-scrape would see it exists and treat the garbage
            # as a real note (bump last_seen, never re-create). Remove the partial so
            # the retried lead is created cleanly, then re-raise so the sink counts it
            # skipped and keeps it out of seen.db. See #24.
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise
        return "created"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_vault.py -k removes_partial_note -v`
Expected: PASS.

- [ ] **Step 5: Prove the test is load-bearing (mutation check)**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Mutate by **removing** the `try/except` (delete the wrapper, leave the bare `_write` + `return`):
```python
        _write(path, self._render_new(lead))
        return "created"
```
Run: `.venv/bin/python -m pytest tests/test_vault.py -k removes_partial_note -v`
Expected: FAIL — the partial 0-byte file is no longer unlinked, so `glob("*.md") == []` fails.

Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault.py
git commit -m "fix(vault): remove a partial note when a create write fails

open(\"w\") creates a 0-byte file before bytes land, so a mid-write OSError
(disk full, interrupt) leaves a partial note that a later re-scrape would treat
as real. upsert now unlinks it and re-raises, so the sink retries the lead.

Refs #24.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: surface `skipped` in the ingest summary (`cli.py`)

Task 3 makes `skipped` non-zero for the first time, but `cli._print_report` prints only created/updated — a run that refuses leads shows a clean-looking summary and the loss lives only in a per-lead log line. Surface it.

**Files:**
- Modify: `sluice/cli.py` — `_print_report` (line 147)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `report.written["skipped"]`.
- Produces: the ingest summary line now includes the skipped count.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (it already uses `capsys`):

```python
def test_print_report_surfaces_skipped(capsys):
    from sluice.cli import _print_report

    class _R:
        sources = []
        written = {"created": 1, "updated": 2, "skipped": 3}

    _print_report(_R())
    err = capsys.readouterr().err        # the summary prints to stderr
    assert "3 skipped" in err
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k surfaces_skipped -v`
Expected: FAIL — the summary omits skipped, so `"3 skipped"` is not in stderr.

- [ ] **Step 3: Add skipped to the summary line**

In `sluice/cli.py`, `_print_report`, replace:

```python
    print(f"written: {w['created']} created, {w['updated']} updated", file=sys.stderr)
```
with:
```python
    print(f"written: {w['created']} created, {w['updated']} updated, "
          f"{w['skipped']} skipped", file=sys.stderr)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k surfaces_skipped -v`
Expected: PASS.

- [ ] **Step 5: Prove the test is load-bearing (mutation check)**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Mutate by **deleting** the `, {w['skipped']} skipped` fragment (restore the original single-clause f-string).
Run: `.venv/bin/python -m pytest tests/test_cli.py -k surfaces_skipped -v`
Expected: FAIL — `"3 skipped"` absent from stderr.

Restore. Confirm green.

- [ ] **Step 6: Commit**

```bash
git add sluice/cli.py tests/test_cli.py
git commit -m "fix(cli): show the skipped count in the ingest summary

A run that refuses leads (a store OSError, now isolated per-lead) previously
printed a clean-looking created/updated summary; the loss was only in a log line.

Refs #24.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: end-to-end blast-radius regression test

Prove the whole-run guarantee the issue names: a store failure on one source's lead does not prevent a **later source** from being written. This exercises the real `VaultSink` inside `engine.run`, closing the loop between Task 3's unit fix and the run-level symptom in #24.

**Files:**
- Test: `tests/test_engine.py` (add one test; reuse the existing `FakeSource`, `_ctx`, `_health` helpers)

**Interfaces:**
- Consumes: `engine.run(sources, ctx, sink, seen, health, *, retries=...)`, real `VaultSink`, real `Vault`, real `SeenDb`. No production code changes — this is a regression test over Task 3's fix.

- [ ] **Step 1: Write the regression test**

This task adds no production code, so the test passes on arrival (Task 3 already landed the guard). It is a genuine regression guard, not a fail-first TDD test — its load-bearing proof is the Step 3 mutation, not a red Step 2. Add to `tests/test_engine.py` (add the imports it needs at the top if absent: `from sluice.core.vault import Vault`, `from sluice.core.seendb import SeenDb`, `from sluice.ingest.sink import VaultSink`):

```python
def test_one_unwriteable_lead_does_not_stop_a_later_source(tmp_path, monkeypatch):
    # #24 blast radius: an OSError writing source A's lead must not abort the run
    # or skip source B. With Task 3's per-lead guard, source B is still written.
    src_a = FakeSource("aaa", [{"title": "Banker", "link": "http://x/1", "company": "Aye"}])
    src_b = FakeSource("bbb", [{"title": "Banker", "link": "http://x/2", "company": "Bee"}])

    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    sink = VaultSink(vault, seen, today=lambda: "2026-07-07")

    real_upsert = vault.upsert

    def flaky(lead):
        if lead.url == "http://x/1":       # source A's lead only
            raise OSError("simulated store refusal")
        return real_upsert(lead)

    monkeypatch.setattr(vault, "upsert", flaky)
    report = run([src_a, src_b], _ctx(), sink, seen, _health(tmp_path), retries=1)

    leads_dir = tmp_path / "vault" / "Job Applications" / "Job Leads"
    assert (leads_dir / "Bee - Banker.md").exists()      # later source still written
    assert not (leads_dir / "Aye - Banker.md").exists()  # failed lead not written
    assert report.written["skipped"] == 1
```

Note: `FakeSource.parse` reads `r.get("company", "")`, so the `company` key in each row sets the note filename.

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/test_engine.py -k unwriteable_lead -v`
Expected: PASS (Task 3 already added the guard). If it FAILs with a propagating `OSError`, Task 3's guard is missing or wrong — fix Task 3, do not weaken this test.

- [ ] **Step 3: Prove the test is load-bearing (mutation check)**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```

Mutate `VaultSink.write` again by **removing** the `try/except` (as in Task 3, Step 5).
Run: `.venv/bin/python -m pytest tests/test_engine.py -k unwriteable_lead -v`
Expected: FAIL — the `OSError` propagates out of `sink.write`, out of `run`, so source B's note is never written.

Restore. Confirm green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_engine.py
git commit -m "test(ingest): a failed write on one source does not skip later sources

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Final: full suite + lint

- [ ] **Run the whole suite** (fast, hermetic):

Run: `.venv/bin/python -m pytest`
Expected: all pass (613 existing + the new tests).

- [ ] **Lint:**

Run: `ruff check sluice tests`
Expected: clean. (ruff is not in `[test]`; `pip install ruff==0.15.21` if absent.)

- [ ] **Pre-push review** per the standing rule: run `/review-pr` BEFORE pushing the branch for CodeRabbit, since CodeRabbit is the scarce ~1h resource and the specialist team is free and parallel. Address findings, then push and open the PR. Use **`Fixes #24`** in the PR body — the byte-cap + isolation is the whole of #24's scope (the collision class is separately owned by #5), so merging should auto-close #24. The individual task commits use `Refs #24` (which does not auto-close); only the PR's `Fixes #24` closes the issue on merge.

## Self-Review

**Spec coverage:**
- Piece 1 (naming fix) → Task 2 (with `_clamp_bytes` split into Task 1). ✓
- Piece 2 (lead-level isolation) → Task 3. ✓
- Spec test 1 (byte-cap holds) → Task 2 `test_long_non_ascii_name_fits_the_byte_budget`. ✓
- Spec test 2 (duplication-safety) → Task 2 `test_byte_clamp_is_a_noop_for_a_name_that_fits`. ✓
- Spec test 3 (never split multibyte) → Task 1 `test_clamp_bytes_never_splits_a_multibyte_codepoint`. ✓
- Spec test 4 (lead isolation: skipped, not in seen.db, run continues) → Task 3 + Task 6. ✓
- Non-goals (collision → #5; moving `sink.write` inside the per-source `try`; `seendb.save` failures) → stated in the Global Constraints scope boundary; not implemented. ✓

**Folded in from the 5-agent plan review (2026-07-18):**
- `_name_max` pathconf **success** and **fallback** branches now tested → Task 2 `test_name_max_reads_pathconf` / `test_name_max_falls_back_when_pathconf_unsupported`; the noop test injects the budget explicitly instead of riding the fallback accidentally. ✓
- Residual-`OSError` **partial file** closed on the create path → Task 4 (unlink + re-raise). ✓
- `skipped` **surfaced** in the CLI summary → Task 5. ✓
- Sink-contract **docstring** updated for `skipped`'s new meaning → Task 3 Step 3. ✓
- **Interpreter unified** (`.venv/bin/python` for `compileall` + pytest); `.venv/` creation noted → Global Constraints. ✓
- **`Fixes #24`** (the `Refs`/`Fixes` contradiction removed) → Final. ✓
- Predicted RED **reasons corrected** (ENAMETOOLONG, not a byte-assertion or AttributeError) → Task 2 Steps 2/5. ✓
- `_name_max` **"only caller" comment** corrected → Task 2 Step 3.

**Placeholder scan:** No TBD/TODO. Every code step shows complete code. The one honest caveat — char-cap↔byte-clamp reordering is a provably equivalent mutant (both are prefix truncations; composition is commutative, verified empirically and independently re-verified in the plan review across 20k random inputs) — is documented in Task 2 Step 5. The duplication-safety property is pinned against the byte-cap-only mutant (the issue's literal proposal), which IS caught by `test_byte_clamp_is_a_noop_for_a_name_that_fits`.

**Type consistency:** `_clamp_bytes(s: str, limit: int) -> str` defined in Task 1, consumed identically in Task 2. `_name_max() -> int` defined and consumed in Task 2. `VaultSink.write` signature unchanged across Tasks 3/6. `Vault.upsert` signature unchanged (Task 4). `_name_max_cache: int | None` initialised in `__init__`, read in `_name_max`. `_print_report` reads `report.written["skipped"]` (Task 5), which `RunReport.written` already defines. ✓
