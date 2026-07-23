# Vault RMW-race safety (#16) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every modify-existing-note vault write safe against a concurrent human/Obsidian/second-process edit, via content compare-and-set with atomic replace and bounded re-apply.

**Architecture:** Every existing-note write is already a surgical single-key/section/line edit. Express each as a pure `transform(text) -> text` routed through one CAS loop (`_cas_write`): atomic-replace (`_atomic_write`) only if the file is byte-unchanged since read, else re-derive from fresh content and retry, else refuse loudly (`VaultConflict`). `upsert` absorbs its own conflict into `refused`; the cv long-window guard rides in `set_tailored_cv(only_if_absent=…)`. The conflict *outcome* is a documented Store-contract property.

**Tech Stack:** Python 3.12+, standard library only (`os`, `stat`, `tempfile`, `re`, `hashlib`), `pytest`, seeded `faker`.

## Global Constraints

- **stdlib-only in `sluice/`.** New imports are `stat` and `tempfile` (both stdlib). No runtime dependency added.
- **Tests hermetic, offline, synthetic, NO THREADS.** Race simulation is a deterministic `_read` interposition (`racing_read` in `tests/conftest.py`), never a thread.
- **Mutation-witness discipline.** Mutate by MOVING or DELETING (never ADDING). Run each new test **by node id** and confirm no pre-existing test in the same file reddens. **Commit the fix before any `git checkout`-restoring witness** — a `git checkout -- <file>` wipes an uncommitted change and the empty diff hides the loss. For production mutants in `sluice/`, run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once first (content-addressed `.pyc`, defeats the stale-bytecode trap).
- **Invariants hold:** never-clobber (a re-scrape touches only `last_seen`), never-regress (application ladder forward-only; `_bump_last_seen` monotonic; `normalize` abstains on disagreement), fabrication gate stays pure/hard, empty-config abstains.
- **`VaultConflict` altitude:** defined in `core/protocols.py` (the module declaring the Store protocol), documented in the Store docstrings, asserted by the conformance suite.
- **Conventional Commits** on every commit.
- **Verify commands:** `python -m pytest` (full suite, <2 s, offline), `ruff check sluice tests` (ruff pinned `0.15.21`, not in `[test]`).

---

### Task 1: Write primitives — `VaultConflict`, `_atomic_write`, `_cas_write`, and the test racer

**Files:**
- Modify: `sluice/core/protocols.py` (add `VaultConflict`, near the top before `LeadNote`)
- Modify: `sluice/core/vault.py` (add `stat`/`tempfile` imports, import `VaultConflict`, add `_RMW_RACE_RETRIES`, `_atomic_write`, `_cas_write`)
- Modify: `tests/conftest.py` (add `racing_read` helper)
- Test: `tests/test_vault_rmw.py` (new)

**Interfaces:**
- Produces: `VaultConflict(RuntimeError)` in `sluice.core.protocols`. `_atomic_write(path: str, text: str) -> None`. `_cas_write(path: str, transform, *, retries: int = _RMW_RACE_RETRIES) -> bool` (True committed, False no-op, raises `VaultConflict`). `_RMW_RACE_RETRIES = 3`. `racing_read(monkeypatch, target_path, on_race, *, once=True) -> dict` in `tests.conftest`.

- [ ] **Step 1: Write the failing primitive tests**

Create `tests/test_vault_rmw.py`:

```python
"""#16 RMW-race safety: content-CAS + atomic replace + bounded re-apply.

Race simulation is deterministic and threadless -- `racing_read` interposes the
module-level `_read` to land one out-of-band edit in the capture->commit window.
"""
import os
import stat

import pytest

import sluice.core.vault as vaultmod
from sluice.core.vault import _atomic_write, _cas_write, _read
from sluice.core.protocols import VaultConflict
from tests.conftest import racing_read


def test_atomic_write_replaces_contents(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("old", encoding="utf-8")
    _atomic_write(str(p), "new")
    assert p.read_text(encoding="utf-8") == "new"
    # no temp siblings left behind
    assert [f.name for f in tmp_path.iterdir()] == ["n.md"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_atomic_write_preserves_mode(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("old", encoding="utf-8")
    os.chmod(p, 0o640)
    _atomic_write(str(p), "new")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640


def test_cas_write_commits_when_unchanged(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("a", encoding="utf-8")
    assert _cas_write(str(p), lambda t: t + "b") is True
    assert p.read_text(encoding="utf-8") == "ab"


def test_cas_write_noop_returns_false(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("a", encoding="utf-8")
    assert _cas_write(str(p), lambda t: t) is False  # identity transform -> no write


def test_cas_write_self_heals_when_file_changes_under_it(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("base\n", encoding="utf-8")
    # Racer appends a line once, in the capture->commit window of our first attempt.
    racing_read(monkeypatch, str(p), lambda: p.write_text("base\nRACER\n", encoding="utf-8"))
    # Our edit appends OURS; re-derived onto the racer's content, both survive.
    assert _cas_write(str(p), lambda t: t + "OURS\n") is True
    body = p.read_text(encoding="utf-8")
    assert "RACER" in body and body.endswith("OURS\n")


def test_cas_write_raises_on_sustained_race(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("v0\n", encoding="utf-8")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        p.write_text(f"v{counter['n']}\n", encoding="utf-8")  # unique content every read
    racing_read(monkeypatch, str(p), churn, once=False)
    with pytest.raises(VaultConflict):
        _cas_write(str(p), lambda t: t + "OURS\n")
```

- [ ] **Step 2: Add the `racing_read` helper to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
def racing_read(monkeypatch, target_path, on_race, *, once=True):
    """Interpose sluice.core.vault._read to simulate a concurrent writer landing in the
    capture->commit window (#16), without threads. `on_race()` performs one out-of-band
    edit to the file. It fires after the FIRST read of target_path (once=True) or on
    EVERY read (once=False, for exhaustion -- on_race must then change the content each
    call). The read returns the PRE-edit bytes, so it is robust to a mutant that deletes
    _cas_write's second (compare) read. Returns the fired-state dict."""
    import sluice.core.vault as vaultmod
    real_read = vaultmod._read
    state = {"fired": False}
    def racer(path):
        text = real_read(path)
        if str(path) == str(target_path) and (not once or not state["fired"]):
            state["fired"] = True
            on_race()
        return text
    monkeypatch.setattr(vaultmod, "_read", racer)
    return state
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_vault_rmw.py -q`
Expected: FAIL — `ImportError: cannot import name '_atomic_write'` (and `VaultConflict`, `_cas_write`).

- [ ] **Step 4: Add `VaultConflict` to `core/protocols.py`**

Insert after the module docstring's imports (line 14, after `from typing import Protocol`), before `@dataclass class LeadNote`:

```python
class VaultConflict(RuntimeError):
    """A modify-write refused because the stored note changed since it was read.

    The store re-derived its surgical edit from the moved content up to a bounded number
    of times; sustained flapping means it wrote nothing. This is never-clobber under
    filesystem concurrency (a human editing in Obsidian, Syncthing, or a second sluice
    process). Callers treat it as non-fatal: the lead is left in its prior state and
    re-attempted next run. `upsert` absorbs its own occurrence into the `refused` outcome
    rather than raising. The CAS *mechanism* is vault-specific, but this *outcome* is a
    store-agnostic contract property, the same altitude as last_seen-monotonicity. See #16.
    """
```

- [ ] **Step 5: Add the primitives to `core/vault.py`**

Add `import stat` and `import tempfile` to the import block (line 16-19 region). Extend the protocols import:

```python
from sluice.core.protocols import LeadNote, VaultConflict
```

Add the constant next to `_CREATE_RACE_RETRIES` (line 35):

```python
_RMW_RACE_RETRIES = 3  # #16: bounded re-derivations before a modify-write refuses loudly
```

Add both helpers next to `_write` (after `_write`, ~line 530):

```python
def _atomic_write(path: str, text: str) -> None:
    """Replace `path`'s contents atomically: write a temp sibling, then os.replace.

    os.replace is atomic (rename(2)) on POSIX and Windows, so a concurrent reader/writer
    always sees a whole file, never a torn one -- the write half of #16's modify-path
    safety. The temp is a SAME-DIRECTORY sibling so os.replace stays on one filesystem (a
    cross-device rename raises OSError). A fresh temp carries umask-default mode, so when
    the target already exists its mode is copied onto the temp before the replace --
    otherwise a modify-write would silently change the note's permissions. On any failure
    the temp is removed before re-raising."""
    d = os.path.dirname(path) or "."
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = None
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sluice-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cas_write(path: str, transform, *, retries: int = _RMW_RACE_RETRIES) -> bool:
    """Apply a surgical edit under compare-and-set. `transform(current_text) -> new_text`
    is re-derived from the CURRENT bytes each iteration. Commit (atomic replace) only if
    the file is byte-unchanged since capture; otherwise re-derive from the fresh content
    and retry. Returns True if a change was committed, False if the transform was a no-op
    (new == text -- an older-or-equal last_seen, an already-present tag, an only_if_absent
    field already set). Raises VaultConflict after `retries` lost races. This is the
    modify-path twin of upsert's create-race loop (#16). The second _read is NOT redundant
    with the first: an external process can write during `transform`, and re-deriving from
    the fresh bytes each iteration is what makes the new==text no-op correct rather than a
    silently dropped edit."""
    for _ in range(retries):
        text = _read(path)
        new = transform(text)
        if new == text:
            return False
        if _read(path) == text:
            _atomic_write(path, new)
            return True
    raise VaultConflict(path)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_vault_rmw.py -q`
Expected: PASS — every test in the file green (no reliance on a specific count).

- [ ] **Step 7: Commit**

```bash
git add sluice/core/protocols.py sluice/core/vault.py tests/conftest.py tests/test_vault_rmw.py
git commit -m "feat(vault): content-CAS write primitives + VaultConflict (#16)"
```

---

### Task 2: Refactor the four surgical writers + `write_document` onto the CAS/atomic path

**Files:**
- Modify: `sluice/core/vault.py` (`update_fields`, `set_tailored_cv`, `append_body_section`, `_bump_last_seen`, `write_document`)
- Test: `tests/test_vault_rmw.py` (add)

**Interfaces:**
- Consumes: `_cas_write`, `_atomic_write` (Task 1).
- Produces: `set_tailored_cv(self, ref, value, *, only_if_absent=False) -> bool` (new keyword + bool return). `update_fields`/`append_body_section`/`_bump_last_seen` keep their signatures; each now routes through `_cas_write`.

- [ ] **Step 1: Write the failing self-heal tests**

Add to `tests/test_vault_rmw.py`:

```python
from sluice.core.vault import Vault


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _seed_note(tmp_path, name="Acme - Analyst.md", extra=""):
    d = _leads_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"---\ncompany: \"Acme\"\nrole: \"Analyst\"\nstatus: new\n{extra}---\n\n# body\n",
        encoding="utf-8")
    return d / name


def test_update_fields_self_heals_a_concurrent_different_key(tmp_path, monkeypatch):
    f = _seed_note(tmp_path)
    v = Vault(str(tmp_path))
    # Racer sets a DIFFERENT key (score) during our status write.
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "status: new", "status: new\nscore: 9"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.update_fields(str(f), {"status": "shortlist"})
    txt = f.read_text(encoding="utf-8")
    assert "status: shortlist" in txt   # ours, re-applied
    assert "score: 9" in txt            # racer's, preserved
    assert "# body" in txt              # body intact


def test_append_body_section_self_heals(tmp_path, monkeypatch):
    f = _seed_note(tmp_path)
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "status: new", "status: shortlist"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    assert v.append_body_section(str(f), "<!--t-->", "<!--t-->\nsection") is True
    txt = f.read_text(encoding="utf-8")
    assert "status: shortlist" in txt   # racer's frontmatter edit preserved
    assert "section" in txt             # our append landed


def test_bump_last_seen_does_not_regress_under_a_concurrent_newer_bump(tmp_path, monkeypatch):
    # THE concurrent guarantee (distinct from the sequential monotonic tests in test_vault.py):
    # a newer bump landing mid-write must win, not be regressed by our re-derive.
    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\n")
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "last_seen: 2026-07-10", "last_seen: 2026-07-15"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v._bump_last_seen(str(f), "2026-07-12")   # older than the racer's concurrent bump
    assert "last_seen: 2026-07-15" in f.read_text(encoding="utf-8")


def test_raced_frontmatter_edit_leaves_body_byte_identical(tmp_path, monkeypatch):
    f = _seed_note(tmp_path)
    original_body = "\n# body\n"
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "status: new", "status: new\nscore: 3"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.update_fields(str(f), {"status": "research"})
    assert f.read_text(encoding="utf-8").endswith(original_body)


def test_set_tailored_cv_only_if_absent_skips_when_present(tmp_path):
    f = _seed_note(tmp_path, extra="tailored_cv: EXISTING.pdf\n")
    v = Vault(str(tmp_path))
    assert v.set_tailored_cv(str(f), "NEW.pdf", only_if_absent=True) is False
    assert "EXISTING.pdf" in f.read_text(encoding="utf-8")
    assert "NEW.pdf" not in f.read_text(encoding="utf-8")


def test_set_tailored_cv_overwrites_by_default(tmp_path):
    f = _seed_note(tmp_path, extra="tailored_cv: EXISTING.pdf\n")
    v = Vault(str(tmp_path))
    assert v.set_tailored_cv(str(f), "NEW.pdf") is True
    assert "NEW.pdf" in f.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_vault_rmw.py -q -k "self_heal or last_seen or byte_identical or only_if_absent or overwrites"`
Expected: FAIL — `set_tailored_cv() got an unexpected keyword 'only_if_absent'`, and the self-heal tests clobber (racer edits lost).

- [ ] **Step 3: Refactor the four writers + `write_document`**

In `sluice/core/vault.py`, replace `update_fields`:

```python
    def update_fields(self, ref, fields: dict, *,
                      append_note: str | None = None,
                      note_tag: str | None = None) -> None:
        """Surgically set frontmatter keys (literal YAML scalars), body byte-for-byte
        intact. Optionally append a guarded note to relevance_notes (skipped if note_tag
        is present, so re-runs are idempotent). Routed through _cas_write: the edit is
        re-derived from the CURRENT note on each attempt, so a concurrent writer's other
        keys and body survive. May raise VaultConflict on sustained conflict (#16)."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            for key, literal in fields.items():
                inner = _set_fm(inner, key, literal)
            if append_note and note_tag:
                current = _fm_value(inner, "relevance_notes")
                if note_tag not in current:
                    merged = (current + " " + append_note).strip()
                    inner = _set_fm(inner, "relevance_notes", f'"{merged}"')
            return f"---\n{inner}\n---\n{body}"
        _cas_write(ref, transform)
```

Replace `append_body_section`:

```python
    def append_body_section(self, ref, tag: str, section_md: str) -> bool:
        """Append a markdown section to the body, idempotently: if `tag` is anywhere in
        the FRESH file, do nothing and return False. Routed through _cas_write, so the
        tag re-check runs against current content. May raise VaultConflict (#16)."""
        def transform(text: str) -> str:
            if tag in text:
                return text
            sep = "" if text.endswith("\n") else "\n"
            return f"{text}{sep}\n{section_md}\n"
        return _cas_write(ref, transform)
```

Replace `set_tailored_cv`:

```python
    def set_tailored_cv(self, ref, value: str, *, only_if_absent: bool = False) -> bool:
        """Set the tailored_cv frontmatter field, body byte-for-byte intact. When
        `only_if_absent`, do NOT overwrite a tailored_cv that is already present in the
        FRESH content (return False) -- the batch cv path uses this to avoid clobbering a
        CV produced during its compose+render window; the check lives in the transform so
        it is atomic under CAS. Returns whether a write happened. May raise VaultConflict
        (#16, #16 cv long-window)."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            if only_if_absent and _fm_value(inner, "tailored_cv"):
                return text
            inner = _set_fm(inner, "tailored_cv", value)
            return f"---\n{inner}\n---\n{body}"
        return _cas_write(ref, transform)
```

Replace `_bump_last_seen`'s write path (keep the monotonic docstring):

```python
    def _bump_last_seen(self, path: str, last_seen: str) -> None:
        """Set the last_seen line inside existing frontmatter, preserving every other key
        and the whole body verbatim. last_seen is MONOTONIC: an incoming stamp older-or-
        equal to the stored one is ignored. Routed through _cas_write, so the monotonic
        decision is re-derived from the FRESH last_seen each attempt -- a concurrent newer
        bump is respected, never regressed (#16). May raise VaultConflict; upsert absorbs
        it (Task 4)."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                return f"---\nlast_seen: {last_seen}\n---\n{text}"
            m = re.search(r"(?m)^\s*last_seen\s*:\s*(.*)$", inner)
            if m:
                if last_seen <= m.group(1).strip().strip('"').strip("'"):
                    return text  # older-or-equal: never regress, write nothing
                inner = re.sub(r"(?m)^\s*last_seen\s*:.*$", f"last_seen: {last_seen}", inner)
            else:
                inner = f"{inner}\nlast_seen: {last_seen}"
            return f"---\n{inner}\n---\n{body}"
        _cas_write(path, transform)
```

In `write_document`, change the final write from `_write(path, text)` to `_atomic_write(path, text)` (torn-file safety; the escape guard above it is unchanged, no CAS — sluice regenerates this digest wholesale).

- [ ] **Step 4: Run to verify pass + full suite**

Run: `python -m pytest tests/test_vault_rmw.py tests/test_vault.py tests/test_vault_rw.py tests/test_core_vault_append.py tests/test_core_vault_cv.py -q`
Expected: PASS (existing vault tests still green — the refactor is byte-identical for the non-raced path).

- [ ] **Step 5: Mutation-witness the self-heal + monotonic tests**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```
Mutant A (unconditional commit): in `_cas_write`, DELETE the `if _read(path) == text:` guard so it always `_atomic_write`s. Run:
`python -m pytest tests/test_vault_rmw.py::test_update_fields_self_heals_a_concurrent_different_key -q` → expect FAIL. Restore.

Mutant B (stale-snapshot monotonic): in `_bump_last_seen`, capture `snap = _read(path)` before `_cas_write` and compare `last_seen <=` the last_seen parsed from `snap` (not from the transform's `text`). Run:
`python -m pytest tests/test_vault_rmw.py::test_bump_last_seen_does_not_regress_under_a_concurrent_newer_bump -q` → expect FAIL, and confirm `tests/test_vault.py::test_upsert_does_not_regress_last_seen_on_older_rescrape` stays GREEN (the generic "weaken monotonic" mutant is caught there; this test is credited only for the concurrent guarantee). Restore.

Mutant C (only_if_absent ignored): make `set_tailored_cv`'s transform set `tailored_cv` unconditionally. Run:
`python -m pytest tests/test_vault_rmw.py::test_set_tailored_cv_only_if_absent_skips_when_present -q` → expect FAIL. Restore.

Verify restored byte-clean: `git diff --stat sluice/core/vault.py` → empty.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_rmw.py
git commit -m "refactor(vault): route the surgical writers through content-CAS (#16)"
```

---

### Task 3: `normalize_all_statuses` under CAS — recompute + abstain from fresh, wrap per note

**Files:**
- Modify: `sluice/core/vault.py` (`normalize_all_statuses`, add module helper `_normalize_status_transform`)
- Test: `tests/test_vault_rmw.py` (add)

**Interfaces:**
- Consumes: `_cas_write`, `VaultConflict`, `_collapse_status_lines`, `_status`.
- Produces: `normalize_all_statuses` unchanged signature; summary may gain a `skipped` list on race.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vault_rmw.py`:

```python
def test_normalize_self_heals_a_concurrent_non_status_edit(tmp_path, monkeypatch):
    d = _leads_dir(tmp_path); d.mkdir(parents=True, exist_ok=True)
    f = d / "Acme - Analyst.md"
    f.write_text('---\ncompany: "Acme"\nstatus: "new"\n---\n\n# body\n', encoding="utf-8")
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            'company: "Acme"', 'company: "Acme"\nscore: 7'), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.normalize_all_statuses(dry_run=False)
    txt = f.read_text(encoding="utf-8")
    assert "status: new" in txt      # canonicalised, quotes dropped
    assert "score: 7" in txt         # racer's edit preserved


def test_normalize_abstains_when_a_race_introduces_a_conflict(tmp_path, monkeypatch):
    d = _leads_dir(tmp_path); d.mkdir(parents=True, exist_ok=True)
    f = d / "Acme - Analyst.md"
    f.write_text('---\ncompany: "Acme"\nstatus: "new"\n---\n\n# body\n', encoding="utf-8")
    v = Vault(str(tmp_path))
    def racer():  # concurrent edit introduces a DISAGREEING second status line
        f.write_text(f.read_text(encoding="utf-8").replace(
            'status: "new"', 'status: "new"\nstatus: dismiss'), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.normalize_all_statuses(dry_run=False)
    txt = f.read_text(encoding="utf-8")
    # abstained: both disagreeing lines still present, not auto-guessed to one value
    assert "status: new" in txt and "status: dismiss" in txt
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_vault_rmw.py -q -k normalize`
Expected: FAIL — the current whole-file `_write` from the snapshot clobbers the racer's edit, and the collapse is computed from the stale snapshot.

- [ ] **Step 3: Add the module transform + rewrite the writer**

Add a module-level helper near `_collapse_status_lines` in `sluice/core/vault.py`:

```python
def _normalize_status_transform(text: str) -> str:
    """Collapse a note's status lines to their single canonical value, recomputed from the
    CURRENT text. Abstain (return text unchanged -> a _cas_write no-op) when the fresh
    status lines DISAGREE: a concurrent edit that introduced a conflict must be reported,
    never auto-guessed (never-regress). #16: derive from fresh, never from the snapshot."""
    inner, body = _split_frontmatter(text)
    if inner is None:
        return text
    norms = [_status.normalize(r.strip())
             for r in re.findall(r"(?m)^\s*status\s*:\s*(.*)$", inner)]
    if len(set(norms)) > 1:
        return text
    canonical = norms[0] if norms else ""
    return f"---\n{_collapse_status_lines(inner, canonical)}\n---\n{body}"
```

Rewrite `normalize_all_statuses`'s write branch (the summary decisions stay snapshot-driven; only the write is CAS-guarded and re-derived):

```python
    def normalize_all_statuses(self, dry_run: bool = False) -> dict:
        """Canonicalize every lead note's status ... (docstring unchanged) ...
        Per-note writes go through _cas_write, so a concurrent edit is re-collapsed from
        fresh content and a race-introduced conflict is abstained on (#16); one conflicting
        note never aborts the sweep."""
        summary = {"changed": 0, "unchanged": 0, "unknown": [], "conflicts": []}
        for note in self.read_leads():
            inner, _ = _split_frontmatter(_read(note.ref))
            if inner is None:
                summary["unchanged"] += 1
                continue
            raws = re.findall(r"(?m)^\s*status\s*:\s*(.*)$", inner)
            norms = [_status.normalize(r.strip()) for r in raws]
            if len(set(norms)) > 1:  # conflicting duplicate statuses -> hands off
                summary["conflicts"].append(
                    (os.path.basename(note.ref), sorted(set(norms))))
                continue
            canonical = norms[0] if norms else ""
            if not _status.is_canonical(canonical):
                summary["unknown"].append(canonical)
            status_lines = [line for line in inner.split("\n")
                            if re.match(r"^\s*status\s*:", line)]
            already = len(status_lines) == 1 and status_lines[0].strip() == f"status: {canonical}"
            if already:
                summary["unchanged"] += 1
                continue
            summary["changed"] += 1
            if dry_run:
                continue
            try:
                _cas_write(note.ref, _normalize_status_transform)
            except VaultConflict:
                summary.setdefault("skipped", []).append(os.path.basename(note.ref))
        return summary
```

- [ ] **Step 4: Run to verify pass + existing normalize tests**

Run: `python -m pytest tests/test_vault_rmw.py -k normalize tests/test_vault.py -q`
Expected: PASS. Also `python -m pytest tests/conformance/test_store_contract.py -k normalize -q` → PASS.

- [ ] **Step 5: Mutation-witness**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```
Mutant (snapshot collapse): make `_normalize_status_transform` ignore its `text` arg and collapse a captured snapshot instead — concretely, replace the abstain guard `if len(set(norms)) > 1: return text` by DELETING it (auto-guess on disagreement). Run:
`python -m pytest tests/test_vault_rmw.py::test_normalize_abstains_when_a_race_introduces_a_conflict -q` → expect FAIL. Restore, confirm `git diff --stat` empty.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_rmw.py
git commit -m "fix(vault): normalize re-collapses + abstains from fresh content under CAS (#16)"
```

---

### Task 4: `upsert` absorbs its `_bump_last_seen` conflict into `refused` (ingest path)

**Files:**
- Modify: `sluice/core/vault.py` (`upsert` update/merge branches)
- Test: `tests/test_vault_rmw.py` (add)

**Interfaces:**
- Consumes: `_bump_last_seen` (raises `VaultConflict`), `VaultConflict`.
- Produces: `upsert` returns `"refused"` on a sustained `_bump_last_seen` race (no exception crosses the ingest sink, which catches only `OSError`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vault_rmw.py`:

```python
def test_upsert_absorbs_a_bump_conflict_into_refused(tmp_path, monkeypatch):
    from sluice.core.leads import Lead
    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\nurl: \"https://example.invalid/1\"\n")
    v = Vault(str(tmp_path))
    lead = Lead(source="b", search="s", title="Analyst", company="Acme",
                location="", salary="", url="https://example.invalid/1",
                last_seen="2026-07-20")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        f.write_text(f.read_text(encoding="utf-8").replace(
            f.read_text(encoding='utf-8').split('last_seen: ')[1].split('\n')[0],
            f"2026-08-{counter['n']:02d}"), encoding="utf-8")
    racing_read(monkeypatch, str(f), churn, once=False)
    assert v.upsert(lead) == "refused"   # not an uncaught VaultConflict


def test_ingest_sink_survives_a_bump_conflict_and_keeps_the_lead_unrecorded(tmp_path, monkeypatch):
    from sluice.core.leads import Lead
    from sluice.ingest.sink import VaultSink

    class _SeenSpy:
        def __init__(self): self.saved = []
        def save(self, leads): self.saved.extend(leads)

    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\nurl: \"https://example.invalid/1\"\n")
    v = Vault(str(tmp_path))
    seen = _SeenSpy()
    sink = VaultSink(v, seen, today=lambda: "2026-07-20")
    conflicting = Lead(source="b", search="s", title="Analyst", company="Acme",
                       location="", salary="", url="https://example.invalid/1")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        cur = f.read_text(encoding="utf-8")
        prev = cur.split("last_seen: ")[1].split("\n")[0]
        f.write_text(cur.replace(f"last_seen: {prev}", f"last_seen: 2026-08-{counter['n']:02d}"),
                     encoding="utf-8")
    racing_read(monkeypatch, str(f), churn, once=False)
    counts = sink.write([conflicting])
    assert counts.get("refused") == 1        # counted, batch did not abort
    assert conflicting not in seen.saved     # stays out of seen.db -> retried next run
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_vault_rmw.py -q -k "absorbs or sink_survives"`
Expected: FAIL — `VaultConflict` propagates out of `upsert` (and out of the sink's `except OSError`).

- [ ] **Step 3: Wrap the bump calls in `upsert`**

In `sluice/core/vault.py` `upsert`, wrap each `_bump_last_seen` call. Replace the `update` branch:

```python
            if action == "update":
                try:
                    self._bump_last_seen(path, lead.last_seen or _today())
                except VaultConflict:
                    # #16: the last_seen bump lost the race repeatedly. Absorb into the
                    # store's existing concurrency-loss vocabulary (like the FileExistsError
                    # create-race above) so no exception crosses the ingest sink; the lead
                    # stays out of seen.db and is retried next run.
                    _log.warning("vault refused lead %r: last_seen bump raced repeatedly",
                                 lead.dedup_key)
                    return "refused"
                return "updated"
```

Replace the `merge` branch's bump identically (keep the existing merge comment above the bump):

```python
            if action == "merge":
                # We could not prove same-or-different, so we do NOT split ... (comment kept)
                try:
                    self._bump_last_seen(path, lead.last_seen or _today())
                except VaultConflict:
                    _log.warning("vault refused lead %r: last_seen bump raced repeatedly",
                                 lead.dedup_key)
                    return "refused"
                return "merged"
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_vault_rmw.py -q && python -m pytest tests/test_vault.py tests/conformance -q`
Expected: PASS.

- [ ] **Step 5: Mutation-witness**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```
Mutant: DELETE the `try/except VaultConflict -> return "refused"` in the `update` branch (let the bump raise). Run:
`python -m pytest tests/test_vault_rmw.py::test_ingest_sink_survives_a_bump_conflict_and_keeps_the_lead_unrecorded -q` → expect FAIL (raw exception escapes the sink). Restore, `git diff --stat` empty.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/vault.py tests/test_vault_rmw.py
git commit -m "fix(vault): upsert absorbs a last_seen-bump conflict into refused (#16)"
```

---

### Task 5: cv long-window guard — `run_one` `guard_existing_cv`, wired through the write

**Files:**
- Modify: `sluice/cv/engine.py` (`run_one` signature + the served write; `run_batch` passes the flag)
- Modify: `sluice/core/app.py` (catch `VaultConflict` on the direct cv path, ~line 365)
- Test: `tests/test_cv_engine.py` (add)

**Interfaces:**
- Consumes: `set_tailored_cv(ref, value, *, only_if_absent) -> bool` (Task 2), `VaultConflict`.
- Produces: `run_one(..., guard_existing_cv=False)`. A batch lead whose CV appeared during render returns `CvResult(status="skipped-has-cv")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cv_engine.py` (follow the file's existing fixtures for `note`, `vault`, `cvcfg`, `backend`, `renderer`; this shows the new assertions):

```python
def test_run_one_batch_guard_skips_when_cv_appeared_during_render(monkeypatch, ...):
    # note is a shortlist lead with no tailored_cv at read time; a concurrent writer sets
    # tailored_cv during compose+render. With guard_existing_cv=True the batch must NOT
    # overwrite it.
    ... # set up note/vault so the note already has tailored_cv on disk at write time
    res = run_one(note, vault, cvcfg, backend, cache, renderer=renderer,
                  guard_existing_cv=True)
    assert res.status == "skipped-has-cv"
    assert vault.read_leads()[0].fm.get("tailored_cv") == "PREEXISTING.pdf (2026-07-10)"


def test_run_one_direct_path_overwrites(monkeypatch, ...):
    res = run_one(note, vault, cvcfg, backend, cache, renderer=renderer)  # default False
    assert res.status == "rendered"
    assert vault.read_leads()[0].fm.get("tailored_cv") != "PREEXISTING.pdf (2026-07-10)"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cv_engine.py -q -k "guard_skips or direct_path_overwrites"`
Expected: FAIL — `run_one() got an unexpected keyword 'guard_existing_cv'`.

- [ ] **Step 3: Wire the flag through `run_one` and `run_batch`**

In `sluice/cv/engine.py`, change the `run_one` signature (line 44):

```python
def run_one(note, vault, cvcfg, backend, dossier_cache, *, renderer, dry_run=False,
            guard_existing_cv=False) -> CvResult:
```

Replace the served write (line 118-121):

```python
    if served:
        wrote = vault.set_tailored_cv(
            note.ref, f"{served} ({date.today().isoformat()})",
            only_if_absent=guard_existing_cv)
        if guard_existing_cv and not wrote:
            # A CV appeared for this lead during our compose+render window; do not clobber
            # it. The served PDF we rendered is left in served_dir (it passed the gate);
            # only the note pointer is withheld. See #16 cv long-window.
            return CvResult(note.ref, "skipped-has-cv", audit_flags=audit_flags,
                            backend=backend_used)
    return CvResult(note.ref, "rendered", audit_flags=audit_flags,
                    served=served, backend=backend_used)
```

In `run_batch`, pass the flag on the `run_one` call (line 134):

```python
            results.append(run_one(note, vault, cvcfg, backend, dossier_cache,
                                   renderer=renderer, dry_run=dry_run,
                                   guard_existing_cv=True))
```

- [ ] **Step 4: Catch the direct-path conflict in `app.py`**

In `sluice/core/app.py`, extend the lazy import (line 344) and wrap the direct return (line 365-366):

```python
        from sluice.cv.engine import CvResult, run_batch, run_one
        from sluice.core.protocols import VaultConflict
        ...
        try:
            return [run_one(notes[0], store, cvcfg, backend, cache, renderer=renderer,
                            dry_run=dry_run)]
        except VaultConflict as e:
            _log.warning("cv re-tailor for %s lost the write race: %s", notes[0].ref, e)
            return [CvResult(notes[0].ref, "error")]
```

- [ ] **Step 5: Run to verify pass + full cv suite**

Run: `python -m pytest tests/test_cv_engine.py -q`
Expected: PASS (existing cv tests still green — default `guard_existing_cv=False` preserves current behaviour for the direct path; `run_batch` now guards).

- [ ] **Step 6: Mutation-witness**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```
Mutant: DELETE the `if guard_existing_cv and not wrote:` early-return block in `run_one`. Run:
`python -m pytest tests/test_cv_engine.py::test_run_one_batch_guard_skips_when_cv_appeared_during_render -q` → expect FAIL, and confirm no pre-existing `test_cv_engine.py` test reddens (run the file: `python -m pytest tests/test_cv_engine.py -q`). Restore, `git diff --stat` empty.

- [ ] **Step 7: Commit**

```bash
git add sluice/cv/engine.py sluice/core/app.py tests/test_cv_engine.py
git commit -m "fix(cv): guard the long-window tailored_cv write against a concurrent CV (#16)"
```

---

### Task 6: Caller resilience across triage / apply / track

**Files:**
- Modify: `sluice/triage/engine.py` (wrap the two apply calls, lines 56 & 92)
- Modify: `sluice/apply/record.py` (guard the `update_fields` at line 27)
- Modify: `sluice/track/engine.py` (guard the `confirm` write at line 163)
- Test: `tests/test_triage_engine.py`, `tests/test_apply_record.py`, `tests/test_track_engine.py` (add)

**Interfaces:**
- Consumes: `VaultConflict`.
- Produces: a `VaultConflict` from any of these callers is non-fatal — triage counts it and continues; apply/track return `{"ok": False, "reason": "conflict"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_triage_engine.py` — inject a `VaultConflict` on one lead's apply and assert the batch survives and it is counted, for BOTH sites:

```python
def test_triage_classify_conflict_is_counted_and_batch_continues(monkeypatch, ...):
    # two keep-reject leads; make apply_classification raise VaultConflict on the first
    from sluice.core.protocols import VaultConflict
    import sluice.triage.engine as eng
    calls = {"n": 0}
    real = eng.apply_classification
    def flaky(vault, note, decision, reason):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(note.ref)
        return real(vault, note, decision, reason)
    monkeypatch.setattr(eng, "apply_classification", flaky)
    report = eng.run(...)  # per the file's harness
    assert report.failures  # the conflict was recorded
    # the SECOND lead was still applied (survivor processed)
    ...


def test_triage_judge_conflict_is_counted_and_batch_continues(monkeypatch, ...):
    # symmetric, targeting apply_verdict (site :92)
    ...
```

`tests/test_apply_record.py`:

```python
def test_record_returns_conflict_on_vault_conflict(monkeypatch, ...):
    from sluice.core.protocols import VaultConflict
    def boom(*a, **k): raise VaultConflict("x")
    monkeypatch.setattr(vault, "update_fields", boom)
    out = record(vault, shortlist_note, cfg)
    assert out == {"ok": False, "reason": "conflict"}
```

`tests/test_track_engine.py`:

```python
def test_confirm_returns_conflict_on_vault_conflict(monkeypatch, ...):
    from sluice.core.protocols import VaultConflict
    def boom(*a, **k): raise VaultConflict("x")
    monkeypatch.setattr(vault, "update_fields", boom)
    out = confirm(vault, cfg, slug, to="applied", deadletter=dl)
    assert out == {"ok": False, "reason": "conflict"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_triage_engine.py tests/test_apply_record.py tests/test_track_engine.py -q -k conflict`
Expected: FAIL — the conflict propagates uncaught (aborts the triage batch; escapes record/confirm as a traceback).

- [ ] **Step 3: Guard the triage sites**

In `sluice/triage/engine.py`, add `from sluice.core.protocols import VaultConflict` to the imports. Wrap the classify-pass apply (line 56):

```python
        try:
            outcome = "skipped" if dry_run else apply_classification(
                vault, note, decision, reason)
        except VaultConflict as e:
            # #16: a concurrent edit won the write race; leave the lead as-is, retried next
            # run. except VaultConflict (not broad Exception) so a real apply-layer logic bug
            # is not silently counted as a transient conflict.
            report.failures.append(f"apply {note.ref}: {e}")
            continue
```

Wrap the judge-pass apply (line 92) the same way:

```python
            try:
                outcome = "skipped" if dry_run else apply_verdict(
                    vault, note, verdict, dossier)
            except VaultConflict as e:
                report.failures.append(f"apply {note.ref}: {e}")
                continue
```

(Adjust the `report.counts` bookkeeping below each to only run when `outcome` was set; the `continue` skips the counting lines for the conflicted lead.)

- [ ] **Step 4: Guard apply/record and track/confirm**

In `sluice/apply/record.py`, add `from sluice.core.protocols import VaultConflict` and wrap the write (line 23-27):

```python
    if not dry_run:
        literals = dict(fields)
        if url:
            literals["applied_url"] = f'"{url}"'
        try:
            vault.update_fields(note.ref, literals)
        except VaultConflict:
            return {"ok": False, "reason": "conflict"}
    return {"ok": True, "fields": fields}
```

In `sluice/track/engine.py`, add the import and wrap the `confirm` write (line 163); the `deadletter.clear_lead` must only run after a successful write:

```python
    if not dry_run:
        fields = {"status": _status.normalize(to), "last_signal": date.today().isoformat()}
        if when:
            fields["interview_date"] = f'"{when}"'
        try:
            vault.update_fields(note.ref, fields)
        except VaultConflict:
            return {"ok": False, "reason": "conflict"}
        deadletter.clear_lead(note.slug)
    return {"ok": True, "from": note.status, "to": _status.normalize(to)}
```

- [ ] **Step 5: Run to verify pass + the sub-app suites**

Run: `python -m pytest tests/test_triage_engine.py tests/test_apply_record.py tests/test_track_engine.py -q`
Expected: PASS.

- [ ] **Step 6: Mutation-witness (per site, by node id)**

```bash
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```
- DELETE the classify-pass `try/except` (line 56) → `tests/test_triage_engine.py::test_triage_classify_conflict_is_counted_and_batch_continues` FAILs; the judge test stays green. Restore.
- DELETE the judge-pass `try/except` (line 92) → the judge test FAILs; the classify test stays green. Restore.
- DELETE record's `except VaultConflict` → `test_record_returns_conflict_on_vault_conflict` FAILs. Restore.
- DELETE confirm's `except VaultConflict` → `test_confirm_returns_conflict_on_vault_conflict` FAILs. Restore.
Confirm `git diff --stat` empty after each.

- [ ] **Step 7: Commit**

```bash
git add sluice/triage/engine.py sluice/apply/record.py sluice/track/engine.py \
        tests/test_triage_engine.py tests/test_apply_record.py tests/test_track_engine.py
git commit -m "fix(triage,apply,track): make a VaultConflict non-fatal at every caller (#16)"
```

---

### Task 7: Store contract — docstrings, conformance property, and ARCHITECTURE.md

**Files:**
- Modify: `sluice/core/protocols.py` (docstrings on `update_fields`, `append_body_section`, `set_tailored_cv`, `normalize_all_statuses`)
- Modify: `tests/conformance/test_store_contract.py` (add a conflict-outcome property)
- Modify: `docs/ARCHITECTURE.md` (vault write path + the store-contract/conformance paragraph)
- Test: the conformance suite itself (Step 1)

**Interfaces:**
- Consumes: `set_tailored_cv(only_if_absent=…) -> bool`, `VaultConflict`.
- Produces: a documented, conformance-asserted contract property.

- [ ] **Step 1: Write the failing conformance property**

Add to `tests/conformance/test_store_contract.py` (uses the file's `_make_store`, `_lead`; interposes `_read` like `racing_read` but store-agnostic — a filesystem store here, so target the note's `ref`):

```python
def test_a_sustained_write_conflict_refuses_rather_than_clobbers(store_name, tmp_path, monkeypatch):
    """The conflict OUTCOME is a contract property (§2a of the #16 design): a modify-write
    that keeps losing the race must refuse loudly (raise VaultConflict for the field-writers,
    or return `refused` from upsert) and write nothing -- never a partial clobber. Skipped
    for stores whose write is not read-modify-write (they cannot exhibit the race)."""
    from sluice.core.protocols import VaultConflict
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    before = store.read_leads()[0].fm
    # Interpose the vault read so every capture sees a moved file (filesystem store only).
    import sluice.core.vault as vaultmod
    if getattr(store, "__class__", None).__module__ != vaultmod.__name__:
        pytest.skip("conflict simulation is filesystem-store specific")
    real = vaultmod._read
    n = {"i": 0}
    def churn(path):
        text = real(path)
        if str(path) == str(ref):
            n["i"] += 1
            vaultmod._write(path, text + f"\nrace: {n['i']}")
        return text
    monkeypatch.setattr(vaultmod, "_read", churn)
    with pytest.raises(VaultConflict):
        store.update_fields(ref, {"status": "shortlist"})
    monkeypatch.setattr(vaultmod, "_read", real)
    after = store.read_leads()[0].fm
    assert after.get("status") == before.get("status"), "a refused write still clobbered status"
```

- [ ] **Step 2: Run to verify pass (behaviour already implemented in Tasks 1-6)**

Run: `python -m pytest tests/conformance/test_store_contract.py -q -k conflict`
Expected: PASS — this property is already satisfied; the test pins it into the contract so a second store cannot ship without it.

- [ ] **Step 3: Update the Store protocol docstrings**

In `sluice/core/protocols.py`, extend `update_fields`'s docstring and add one to `append_body_section`/`set_tailored_cv`:

```python
    def update_fields(self, ref, fields: dict, *, append_note=None, note_tag=None) -> None:
        """Set exactly the named frontmatter keys, leaving the body byte-for-byte intact.
        This is the sanctioned write path for triage, cv, apply and track. MAY raise
        VaultConflict if the note changed under a sustained concurrent edit and the store
        could not re-apply without clobbering (see VaultConflict; #16). Callers treat that
        as non-fatal."""
        ...

    def append_body_section(self, ref, tag: str, section_md: str) -> bool:
        """Append a tagged section to the body, idempotently (returns False if `tag` is
        already present). MAY raise VaultConflict on sustained concurrent edit (#16)."""
        ...

    def set_tailored_cv(self, ref, value: str, *, only_if_absent: bool = False) -> bool:
        """Set the served-CV pointer. When only_if_absent, do not overwrite an existing one
        (returns False). Returns whether a write happened. MAY raise VaultConflict (#16)."""
        ...
```

- [ ] **Step 4: Update `docs/ARCHITECTURE.md`**

Find the vault write-path description and the store-contract/conformance paragraph. Add: (a) that modify-writes are content-CAS + atomic-replace with bounded re-apply, refusing loudly (`VaultConflict`) on sustained conflict, mirroring the create-race loop; (b) that the conflict *outcome* joins never-clobber and last_seen-monotonicity as a conformance-asserted Store property, while the CAS *mechanism* is vault-specific.

- [ ] **Step 5: Run the full suite + ruff + compile check**

```bash
python -m pytest
ruff check sluice tests
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
```
Expected: all green; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/protocols.py tests/conformance/test_store_contract.py docs/ARCHITECTURE.md
git commit -m "docs(vault): document the conflict outcome as a Store-contract property (#16)"
```

---

## Post-plan: PR body

When opening the PR, the body must:
- `Closes #16`.
- Document the `flock`-hybrid (sluice-vs-sluice micro-residual) as a deliberate YAGNI non-goal — reopen if concurrent `sluice` invocation against one vault becomes real.
- Flag (propose, do NOT apply — `.rulesync/` is human-gated) that the CLAUDE.md never-clobber paragraph now under-describes the concurrency guard.

## Self-Review

- **Spec coverage:** §1 `_atomic_write`→T1; §2 `_cas_write`/`VaultConflict`→T1; §2a altitude/absorb/vehicles→T1 (def), T4 (upsert absorb), T7 (contract docs+conformance); §3 five writers→T2 (four)+T3 (normalize); §4 cv only_if_absent→T2 (write) + T5 (wiring); §5 every raise site→T4 (ingest) + T6 (triage/apply/track); Testing 11 cases→T1-7; DoD 1-9→T1-7. No gap.
- **Placeholder scan:** the two sub-app test bodies (T5/T6) show the load-bearing assertions and reference each file's existing fixtures rather than reprint an unfamiliar harness; all production code is complete.
- **Type consistency:** `set_tailored_cv(ref, value, *, only_if_absent=False) -> bool`, `_cas_write(path, transform, *, retries) -> bool`, `run_one(..., guard_existing_cv=False)`, `VaultConflict` from `sluice.core.protocols` — used identically across T2/T5/T7.
