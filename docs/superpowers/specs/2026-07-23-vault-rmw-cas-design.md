# Vault RMW-race safety — a concurrent human/process edit must never be silently overwritten

Close the remaining half of #16: every write to an *existing* lead note is a whole-file
read-modify-write with no concurrency guard, so an edit landing between the read and the write
(a human in Obsidian, Syncthing, or a second `sluice` process) is silently clobbered. The fix is
content compare-and-set with atomic replace and bounded re-apply — the same shape as the create-race
loop PR #47 already put in `upsert`.

## What unblocked this

- #16 was split out of the PR #15 review as a pre-existing property of every vault write.
- PR #47 (the first #16 slice) closed the **create-write** race: `_write(..., exclusive=True)`
  (`open("x")`, `O_CREAT|O_EXCL`) + a bounded re-reconcile loop in `upsert`. A create can no longer
  truncate a note a concurrent writer landed in the TOCTOU window.
- This spec closes the **modify-existing-note** race, which #47 did not touch.
- Mechanism was decided by brainstorm (2026-07-23): **content-CAS + atomic replace + bounded
  re-apply**, `flock`-hybrid **declined as YAGNI**. This closes #16 (see Non-goals).

## Problem

Every write to a note that already exists is `_read()` → mutate → `_write()` through a *truncating*
`open("w")`, with no lock and no compare-and-set:

| Writer | Sub-app | Edit |
| --- | --- | --- |
| `update_fields` | triage/apply, apply/record (`shortlist→applied`), track/reconcile, track/engine | set frontmatter keys, append tagged note |
| `set_tailored_cv` → `update_fields` | cv/engine | set `tailored_cv` |
| `append_body_section` | track/reconcile | append one tagged body section |
| `_bump_last_seen` | core/vault (`upsert` update/merge) | set the one `last_seen` line |
| `normalize_all_statuses` | core/vault (`leads` normalize) | collapse status lines |

An edit made to the same note between our `_read` and our `_write` is overwritten wholesale — the
exact fragility sluice exists to remove, at filesystem-concurrency altitude rather than field-level
never-clobber altitude. The vault is an Obsidian folder a human edits live, so this is real.

The cv path has a second, **wider** window. `run_batch` reads a `tailored_cv`-unset snapshot from
`read_leads`, then composes and renders (an LLM call plus a PDF render — seconds to minutes), then
writes. A human or a second process setting `tailored_cv` during that window is clobbered when the
render finishes.

## Key finding 1 — the edits are already surgical

Every write above sets one frontmatter key, appends one tagged section, or bumps one line. The
whole-file rewrite is an implementation artifact, not a semantic requirement. So on a *detected*
concurrent change, "re-derive the same surgical edit from the fresh content and write that" is
well-defined and is exactly the never-clobber philosophy: you only ever touch your own key/section,
never the human's other keys or body. This is what makes CAS-with-self-heal correct here rather than
CAS-with-give-up.

## Key finding 2 — `flock` cannot see the primary threat

The primary writer-of-concern is a **human in Obsidian** (or Syncthing propagating one). They never
call `flock`, so an advisory lock in `sluice` protects only `sluice`-vs-`sluice` and misses the case
the issue names first. A content change, by contrast, *is* visible to a content compare. Hence
content-CAS, not `flock`.

The honest limit: content-CAS keeps an **irreducible micro-window** between the compare-read and the
`os.replace` — there is no portable stdlib primitive for an atomic *conditional* replace
(`renameat2`/`RENAME_*` is Linux-only and not content-conditional). This residual is accepted because:

1. **Window size.** The gap is two syscalls (microseconds) versus the original RMW window of
   seconds–minutes (`cv run_batch`). Exposure drops by ~6 orders of magnitude.
2. **No corruption.** `os.replace` is atomic (`rename(2)`), so even a lost micro-race yields a *whole*
   file — a lost update, never a torn note.
3. **Self-healing.** Because the edits are surgical *and* monotonic/idempotent (`last_seen` only moves
   forward, `append_body_section` is tag-guarded, `_set_fm` is idempotent), a lost micro-race
   re-corrects on the next `sluice` pass — a permanent silent clobber (today) becomes a vanishingly
   rare, self-correcting blip.

`flock` would close only the `sluice`-vs-`sluice` slice of that micro-residual, at the cost of the
`fcntl`/`msvcrt` portability split, for a race that needs two concurrent `sluice` processes on one
note. Declined (see Non-goals).

## The governing rule

> A write to an existing note commits only if the file is **byte-unchanged since we read it**. On a
> detected change, re-derive the surgical edit from the **fresh** content and retry, bounded. Sustained
> flapping refuses loudly (`VaultConflict`) and writes nothing.

Same posture as #47's create-race ("exclusive create + bounded re-reconcile → refuse loudly") and as
the duplicate-status conflict `normalize_all_statuses` already declines to auto-resolve.

## Design

All in `core/vault.py` unless noted.

### 1. `_atomic_write(path, text)` — temp sibling + `os.replace`

Write `text` to a temporary sibling in the *same directory* (so `os.replace` stays on one
filesystem), then `os.replace(tmp, path)`. `os.replace` is atomic on POSIX and Windows, so a
concurrent reader/writer always sees a whole file, never a partial one. On any failure, unlink the
temp and re-raise.

This replaces the truncating `open("w")` on the **modify** path only. The **create** path keeps
`_write(..., exclusive=True)` (`open("x")`) — a different race, already solved by #47; O_EXCL is what
*detects* the create-race and must stay. Two distinct primitives for two distinct races.

`write_document` (the rejected-leads digest the store owns and regenerates wholesale) routes through
`_atomic_write` for torn-file safety but takes **no** CAS — a wholesale regenerate is intended there,
there is no human edit to preserve.

*Non-goal for this write:* `fsync`/power-loss durability. `os.replace` gives atomicity against
concurrent edits (which is #16); crash-durability is a separate concern and adding a per-write
`fsync` across the suite is avoided to keep it fast (see Non-goals).

### 2. `_cas_write(path, transform, *, retries) -> bool` + `VaultConflict`

The one loop every modify-write routes through:

```python
def _cas_write(path: str, transform, *, retries: int = _RMW_RACE_RETRIES) -> bool:
    """Apply `transform(current_text) -> new_text` to `path` under compare-and-set.
    Returns True if a change was committed, False if the transform was a no-op
    (new == text — e.g. an older-or-equal last_seen, or an already-present tag).
    Raises VaultConflict if `retries` re-derivations all lose the race."""
    for _ in range(retries):
        text = _read(path)                     # capture
        new = transform(text)                  # derive the edit from CURRENT bytes
        if new == text:
            return False                       # idempotent no-op — write nothing
        if _read(path) == text:                # still unchanged since capture?
            _atomic_write(path, new)
            return True
        # changed under us — loop, re-derive from the fresh content
    raise VaultConflict(path)
```

`VaultConflict(RuntimeError)` carries the path. Idempotency and monotonicity fall out for free: a
transform that returns its input unchanged is a no-op and writes nothing, so `_bump_last_seen`'s
"older-or-equal → write nothing" and `append_body_section`'s "tag already present → don't append"
need no special-casing — they *are* the `new == text` branch.

`_RMW_RACE_RETRIES` is a new identity-neutral constant (mirrors `_CREATE_RACE_RETRIES = 3`).

### 3. Refactor the five writers to `transform` closures

Each writer becomes a closure over its arguments, passed to `_cas_write`; no writer reads or writes
the file itself. The closure captures the *intent* (which keys, which note, which section) and
applies it to whatever current text `_cas_write` hands it — which is what makes re-apply correct.

- `update_fields(ref, fields, *, append_note, note_tag)` — transform splits frontmatter, `_set_fm`s
  each key, and (if `note_tag` absent from the *fresh* `relevance_notes`) appends the tagged note.
  A concurrent writer's other keys and body survive; a concurrent writer that already added the same
  tag is respected (append skipped); a different concurrent note is preserved (ours appends after it).
- `set_tailored_cv(ref, value)` — unchanged surface; delegates to `update_fields`, so CAS for free.
- `append_body_section(ref, tag, section_md) -> bool` — transform returns the text unchanged when
  `tag` is anywhere in the fresh file (→ `_cas_write` returns False), else appends. The bool is
  `_cas_write`'s return.
- `_bump_last_seen(path, last_seen)` — transform re-applies the monotonic rule against the fresh
  `last_seen`; older-or-equal returns the text unchanged (no write). Never-regress holds under
  concurrency: if a concurrent writer bumped to a newer date, our re-derivation now sees it and
  no-ops rather than regressing.
- `normalize_all_statuses` — each per-note write routes through `_cas_write` with a transform that
  re-collapses status lines from the fresh content. The run summary (changed/unchanged/conflicts) is
  computed from the read snapshot and is best-effort reporting; the *write* is CAS-guarded.

### 4. cv long-window TOCTOU — check-at-write (`cv/engine.py`)

`run_batch`'s `skipped-has-cv` check reads a `read_leads` snapshot; the compose+render window is
seconds–minutes. Add a **re-read guard**: immediately before `set_tailored_cv`, re-read the note; if
`tailored_cv` is now set, return `skipped-has-cv` and do not write — a CV a human or other process
produced during our render must not be clobbered.

`run_one`'s direct path (`sluice cv --lead X`) stays an explicit re-tailor that overwrites
`tailored_cv` (per the issue, that is intended) — but it now writes through CAS, so it never clobbers
*unrelated* concurrent edits (status, body) on the same note.

CAS alone is not sufficient for the cv case: `set_tailored_cv`'s transform sets `tailored_cv`
unconditionally, so under re-apply it would overwrite a *different* `tailored_cv` a human wrote during
the render. The check-at-write is the semantic guard that CAS cannot supply.

### 5. Caller resilience — two triage `try/except` boundaries

`VaultConflict` is non-fatal at every call site: the lead is left in its prior state, logged, and
re-attempted next run — self-healing, never a partial write. `cv run_batch` (per-lead `try/except`)
and `track confirm` (returns a dict) already absorb it. The two triage apply calls
(`triage/engine.py:56` `apply_classification`, `:92` `apply_verdict`) are **not** under a per-lead
guard, so a raised conflict would abort the batch. Wrap each in the same per-lead `try/except` the
engine already uses for `dossier_cache.get_or_build` and `cv run_batch` uses for render: log to
`report.failures`, count it, continue.

## Invariant interactions

- **Never-clobber (writes).** Strengthened, not weakened: never-clobber now holds under concurrency,
  not just against sluice's own re-scrape. A re-scrape still bumps only `last_seen`.
- **Never-regress (status).** `_bump_last_seen`'s monotonicity composes with CAS (§3); a concurrent
  newer bump is respected, not regressed.
- **CV fabrication gate.** The check-at-write (§4) prevents clobbering a produced CV; rendering is
  still gated — this touches only *when* the served pointer is written, never the gate.
- **Empty-config-abstains.** Untouched — no preference gate is involved.

## The accepted cost — stated honestly

- A ~2-syscall micro-window (compare → replace) remains; a writer landing there produces
  last-writer-wins (a whole file, never torn), and the edit self-heals on the next pass (Key finding 2).
- No `fsync`: a power loss mid-write can lose the *last* atomic write, but never corrupts the note
  (temp file is the casualty). #16 is concurrent-edit safety, not crash durability.

## Testing (hermetic, offline, no threads)

Deterministic race simulation by interposing `_read`: a wrapper that, on a chosen call, performs one
real out-of-band edit to the file (the "racer" landing between capture and commit) and then delegates.
No threads — threads would be flaky and violate the fast/hermetic suite.

1. **Self-heal.** Racer sets key B once during our set-of-key-A write → assert both A (ours,
   re-applied) and B (racer's) are present, body intact.
2. **Exhaustion.** Racer rewrites on every attempt → assert `VaultConflict` raised and the note still
   holds the racer's last content (nothing of ours clobbered).
3. **`last_seen` monotonic under CAS.** A concurrent newer bump is not regressed; an older-or-equal
   incoming stamp writes nothing.
4. **Body byte-identical for FM-only edits** (existing property, re-asserted under the new path).
5. **cv check-at-write.** `tailored_cv` set during the render window → `run_batch` returns
   `skipped-has-cv`, does not overwrite.
6. **Triage resilience.** A `VaultConflict` on one lead is caught, counted, and the batch continues.

Mutation-witness (mutate by MOVING/DELETING, run each in isolation, restore byte-clean):
- Make `_cas_write`'s commit unconditional (delete the `_read(path) == text` guard) → the self-heal
  test (1) reddens.
- Delete the exhaustion `raise VaultConflict` (fall through to return) → test (2) reddens.
- Delete the cv re-read guard → test (5) reddens.
- Weaken `_bump_last_seen`'s monotonic branch → test (3) reddens.

Commit the fix before any git-checkout-restoring witness (a `git checkout -- <file>` wipes an
uncommitted working-tree change and the empty post-run diff hides the loss).

## Non-goals

- **`flock` hybrid** (`sluice`-vs-`sluice` micro-residual). Declined as YAGNI: needs two concurrent
  `sluice` processes writing one note; the portability cost (`fcntl` vs `msvcrt`) is not justified.
  Reopen #16 if concurrent `sluice` invocation against one vault becomes real.
- **`fsync`/power-loss durability.** Separate concern; not #16.
- **`run_one`'s explicit re-tailor.** Stays an intentional overwrite (per the issue); only made
  CAS-safe against unrelated concurrent edits.
- **`existing_keys()`/read-path dedup** (#23) — different issue.

## Definition of done

1. `_atomic_write`, `_cas_write`, `VaultConflict`, `_RMW_RACE_RETRIES` in `core/vault.py`.
2. The five writers refactored to `transform` closures through `_cas_write`; `write_document` on
   `_atomic_write` (no CAS).
3. cv `run_batch` check-at-write guard; `run_one` writes via CAS.
4. Per-lead `try/except` around `triage/engine.py:56,92`.
5. Concurrency test suite (six cases above) passing; every mutation-witness confirmed (RED under the
   mutant, restored byte-clean; new tests isolated by node id per the layers rule).
6. `docs/ARCHITECTURE.md` vault section notes the RMW-race guard (part of the work, not a follow-up).
7. Full suite green, `ruff check` clean.
8. PR body: `Closes #16`, with the `flock`-hybrid documented as a deliberate non-goal; and the
   `.rulesync/` CLAUDE.md never-clobber paragraph flagged as under-describing the concurrency guard
   (human-gated — propose, do not apply).

## Risks and notes

- **Same-filesystem temp.** The temp sibling must share the leads dir's filesystem or `os.replace`
  raises `OSError` (cross-device). Same-dir sibling guarantees this. A leads dir that is itself a
  mount is still one filesystem for its children.
- **Permission drift.** A fresh temp is created with umask-default mode, so `os.replace` onto an
  existing note would silently change its mode. When the target exists, copy its mode onto the temp
  (`os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))`) before the replace, so a modify-write
  preserves the note's permissions.
- **Windows.** `os.replace` replaces atomically even when the target exists; an inode swap under a
  reader is fine (Obsidian re-reads on external change). No `open`-file-unlink hazard — the temp is
  ours and closed before replace.
- **CodeQL new-sink.** Prefer routing through the *existing* `_write` shape (or `_atomic_write` as a
  peer helper) over minting a fresh public write function — a new write sink re-flags long-standing
  clear-text-storage behaviour on the diff (the #47 lesson that consolidated `_write_new` into a
  `_write(..., exclusive=...)` flag).
- **`_split_frontmatter`/`_set_fm` are already the transform primitives** — the refactor moves the
  read/write to the edges and leaves the frontmatter logic verbatim, so format-preservation is
  unchanged.

## Process

Brainstorm (2026-07-23) → this spec → `/review-plan` (specialist team) → implementation plan → TDD
build with mutation-witness → `/review-pr` before push → CodeRabbit → path-to-green → merge gate.
