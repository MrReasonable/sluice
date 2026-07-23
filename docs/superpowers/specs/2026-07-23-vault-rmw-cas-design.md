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
        if _read(path) != text:                # changed under us since capture?
            continue                           # re-derive from the fresh content next iteration
        if new == text:
            return False                       # genuine no-op against the CURRENT content
        _atomic_write(path, new)
        return True
    raise VaultConflict(path)
```

`VaultConflict` carries the path. Idempotency and monotonicity fall out for free: a
transform that returns its input unchanged is a no-op and writes nothing, so `_bump_last_seen`'s
"older-or-equal → write nothing" and `append_body_section`'s "tag already present → don't append"
need no special-casing — they *are* the `new == text` branch.

`_RMW_RACE_RETRIES` is a new identity-neutral constant (mirrors `_CREATE_RACE_RETRIES = 3`).

### 2a. `VaultConflict` is a Store-contract outcome, not a vault detail

The CAS *mechanism* (content compare) is vault-specific — a SQLite store would use a row version.
But the *outcome* ("a write refused because the stored state moved since we read it; nothing written;
retry next run") is store-agnostic, the same altitude `last_seen`-monotonicity was lifted to when it
moved from a vault mechanism into the Store contract + conformance suite. So `VaultConflict` is
defined in a **core** module the Store protocol can name — `core/protocols.py` (which declares the
Store protocol) or a `core/errors.py` it imports — not in `core/vault.py`. `protocols.py`'s
`update_fields` / `append_body_section` docstrings gain "may raise `VaultConflict` on sustained
conflict", and the store conformance suite gains a property asserting it (a second store that silently
inherited this would be an undocumented contract, the arc-001 concern).

**Two vehicles, split by whether the caller already models outcomes as a return value:**

- `upsert` **absorbs** a `_bump_last_seen` conflict into its existing
  `created`/`updated`/`merged`/`refused` vocabulary — it returns `refused`, so no exception ever
  crosses the ingest boundary. This mirrors how `upsert` already folds `FileExistsError` (the
  create-race) into `refused`; the sink's `except OSError` per-lead guard needs no change.
- `set_tailored_cv(ref, value, *, only_if_absent=…)` returns a `bool` (see §4).
- The two field-writers `update_fields` and `append_body_section` **raise** `VaultConflict`, caught
  per-lead by their callers (§5). These are the callers already inside per-lead resilience loops, so
  an exception is the idiomatic vehicle there (matching the dossier/render `try/except` pattern).

### 3. Refactor the five writers to `transform` closures

Each writer becomes a closure over its arguments, passed to `_cas_write`; no writer reads or writes
the file itself. The closure captures the *intent* (which keys, which note, which section) and
applies it to whatever current text `_cas_write` hands it — which is what makes re-apply correct.

- `update_fields(ref, fields, *, append_note, note_tag)` — transform splits frontmatter, `_set_fm`s
  each key, and (if `note_tag` absent from the *fresh* `relevance_notes`) appends the tagged note.
  A concurrent writer's other keys and body survive; a concurrent writer that already added the same
  tag is respected (append skipped); a different concurrent note is preserved (ours appends after it).
- `set_tailored_cv(ref, value, *, only_if_absent=False) -> bool` — its own transform (not a bare
  `update_fields` delegate, since it now carries a condition): sets `tailored_cv` on the fresh content,
  except when `only_if_absent` and `tailored_cv` is already present (return the text unchanged → a
  `_cas_write` no-op → `False`). See §4 for why the condition lives in the transform.
- `append_body_section(ref, tag, section_md) -> bool` — transform returns the text unchanged when
  `tag` is anywhere in the fresh file (→ `_cas_write` returns False), else appends. The bool is
  `_cas_write`'s return.
- `_bump_last_seen(path, last_seen)` — transform re-applies the monotonic rule against the fresh
  `last_seen`; older-or-equal returns the text unchanged (no write). Never-regress holds under
  concurrency: if a concurrent writer bumped to a newer date, our re-derivation now sees it and
  no-ops rather than regressing.
- `normalize_all_statuses` — each per-note write routes through `_cas_write` with a transform that
  **recomputes `canonical` from the fresh text** and **re-applies the disagree→abstain rule** on that
  fresh text (return the text unchanged — a `_cas_write` no-op — when the fresh status lines disagree).
  The snapshot may drive the run summary (changed/unchanged/conflicts) *only*, never the written
  value: deriving `canonical` or the no-conflict decision from the stale snapshot would let CAS stamp
  a stale status over a concurrent edit, or auto-guess a freshly-introduced conflict rather than
  abstaining (a never-regress break). The per-note `_cas_write` is wrapped in `try/except
  VaultConflict` and the note recorded under a `conflicts`/`skipped` bucket, so one conflicting note
  never aborts the whole sweep.

### 4. cv long-window TOCTOU — condition pushed into the write (`cv/engine.py` + `set_tailored_cv`)

`run_batch`'s `skipped-has-cv` check reads a `read_leads` snapshot; the compose+render window is
seconds–minutes, and the only write is `set_tailored_cv` inside the **shared** `run_one`
(`engine.py:119`), which serves both the batch path (must skip if a CV now exists) and the direct
`sluice cv --lead X` re-tailor (must overwrite, per the issue). So the guard cannot be a pre-write
re-read in `run_one` (it would break the direct overwrite), and it must not be `_read(note.ref)` in
the engine — `note.ref` is an opaque store handle and reading it as a path reaches from the cv sub-app
into `core.vault`, breaking the store seam. A pre-read plus an *unconditional* CAS transform also
leaves a residual clobber: a human's `tailored_cv` landing after the check but during the CAS loop is
re-derived over.

Push the condition **into the write**, through the injected store:

```
set_tailored_cv(ref, value, *, only_if_absent: bool = False) -> bool
```

When `only_if_absent`, the transform closure returns the text unchanged (a `_cas_write` no-op → `False`)
if `tailored_cv` is already present in the *fresh* content, else sets it. Because the check lives in
the transform, it is **re-evaluated on every CAS re-derive** and is therefore atomic — it closes the
residual clobber CAS alone cannot. `run_one` gains a `guard_existing_cv` flag it passes straight
through as `only_if_absent`: `run_batch` sets it `True` (skip a CV produced during the render, map a
`False` return to `skipped-has-cv`), the direct caller leaves it `False` (intentional overwrite,
still CAS-safe against *unrelated* concurrent status/body edits). No engine-side re-read; the store
stays the only reader of its own notes.

*Orphaned-served residual (low):* when the batch path skips the pointer write, the CV has already been
rendered into `served_dir`, leaving a served PDF the note never references — wasted work, not an
invariant break (that CV passed the gate). Keeping `run_batch`'s cheap snapshot `skipped-has-cv` skip
*before* compose/render avoids it in the common case; the `only_if_absent` write is the atomic backstop
for the race. An optional pre-render re-read could close the wasted-work window too, at the cost of a
store read — deferred.

### 5. Caller resilience — every raise site enumerated

`VaultConflict` must be non-fatal at every site: the lead is left in its prior state, logged, and
re-attempted next run — never a partial write, never a batch abort. After §2a's absorb-on-return
split, the only sites that *raise* are callers of `update_fields` and `append_body_section`. Enumerate
**all** of them (the earlier draft asserted "non-fatal at every call site" while its list was both
incomplete and wrong — the exact "assert a mechanism, miss a caller" trap this review caught):

| Raise site | Currently guarded? | Disposition |
| --- | --- | --- |
| `upsert` → `_bump_last_seen` (ingest, `sink.py`) | sink catches `OSError` only | **Absorb in `upsert`** → return `refused` (§2a); no exception reaches the sink; sink unchanged. Add a test: a `_bump_last_seen` conflict on one lead leaves the batch running and that lead out of `seen.db`. |
| `triage/engine.py:56` `apply_classification` | **no** | Wrap in `except VaultConflict`; count to `report.failures`; continue. |
| `triage/engine.py:92` `apply_verdict` | **no** | Same. |
| `apply/record.py:27` `update_fields` | **verify** the apply engine's per-lead loop | Wrap in `except VaultConflict` if not already under a per-lead boundary. |
| `track/reconcile.py:34,46` | yes — `track` `run()` loop, `engine.py:135` `except Exception` | Already absorbed; no change. |
| `track/engine.py:163` `confirm` | **no** — returns dicts only on its early refusal paths; the write is unwrapped | Wrap the write; return `{"ok": False, "reason": "conflict"}`. |
| `cv` direct path (`app.py:365` → `run_one`, `only_if_absent=False`) | `run_batch` wraps `run_one`; the **direct** path does not | Catch at the CLI/`run_one` boundary → a `skipped`/`error` `CvResult`, not a traceback. (The batch path is already wrapped at `engine.py:132`.) |
| `normalize_all_statuses` per-note write | internal | Wrapped inside the sweep (§3) → `conflicts`/`skipped` bucket. |

Prefer `except VaultConflict` (specific) over broad `except Exception` at the **new** triage sites:
a broad catch would silently count a genuine never-regress/logic bug in the apply layer as a transient
conflict (rev-004). The existing broad `except Exception` in `track`'s `run()` loop stays — narrowing
it is out of scope.

## Invariant interactions

- **Never-clobber (writes).** Strengthened, not weakened: never-clobber now holds under concurrency,
  not just against sluice's own re-scrape. A re-scrape still bumps only `last_seen`.
- **Never-regress (status).** `_bump_last_seen`'s monotonicity composes with CAS (§3); a concurrent
  newer bump is respected, not regressed. `normalize_all_statuses` re-applies its disagree→abstain
  rule on the *fresh* text (§3), so CAS never stamps a stale status or auto-guesses a fresh conflict.
- **CV fabrication gate.** The `only_if_absent` write (§4) prevents clobbering a produced CV;
  rendering is still gated — this touches only *when/whether* the served pointer is written, never the
  gate.
- **Empty-config-abstains.** Untouched — no preference gate is involved.
- **Store contract.** The conflict *outcome* joins `last_seen`-monotonicity as a documented,
  conformance-asserted Store property (§2a); the CAS *mechanism* stays vault-only. A second store
  inherits a documented behaviour, not a surprise.

## The accepted cost — stated honestly

- A ~2-syscall micro-window (compare → replace) remains; a writer landing there produces
  last-writer-wins (a whole file, never torn), and the edit self-heals on the next pass (Key finding 2).
- No `fsync`: a power loss mid-write can lose the *last* atomic write, but never corrupts the note
  (temp file is the casualty). #16 is concurrent-edit safety, not crash durability.

## Testing (hermetic, offline, no threads)

**Race harness.** Interpose the module-level `_read` with a **path-scoped one-shot** racer: it fires
*after the first capture read of the target path*, performs one real out-of-band edit to the file, and
returns the pre-edit bytes, then delegates for all later calls. Keying it to the first capture-read of
the target — not to a global `_read` call count — is what makes it robust to the compare-delete
mutant, which removes the second `_read` per iteration and would otherwise shift a count-keyed racer
onto the wrong logical read (and lie green). No threads (flaky, and would break the fast/hermetic
suite). Test (2)'s racer is the same wrapper *without* the one-shot (edits every attempt); it
terminates via the bounded `range(_RMW_RACE_RETRIES)`.

1. **Self-heal.** Racer sets key B during our set-of-key-A write → both A (ours, re-applied) and B
   (racer's) present, body intact. Load-bearing because `update_fields` writes the whole file from the
   stale snapshot, so without CAS the different-key racer edit is clobbered.
2. **Exhaustion.** Racer rewrites every attempt → `VaultConflict` raised, note still holds the racer's
   last content (nothing of ours clobbered).
3. **`last_seen` monotonic under CAS** — the *concurrent* guarantee: a newer bump landing mid-write is
   respected on re-derive, not regressed. See the witness note below — the generic "monotonic branch"
   mutant is caught by pre-existing tests, so this test must be witnessed by a snapshot-vs-fresh mutant.
4. **Raced frontmatter edit, body byte-identical.** Racer edits a frontmatter key while we edit
   another; assert the body survives both writes byte-for-byte. (The non-concurrent version already
   exists at `test_vault.py:407` etc.; this is the *concurrent* variant, else it adds no new coverage.)
5. **cv `only_if_absent`.** `tailored_cv` set during the render window → the batch path (`run_one`
   with `guard_existing_cv=True`) returns `skipped-has-cv` and does not overwrite; the direct path
   (`guard_existing_cv=False`) overwrites. Assert both.
6. **`append_body_section` self-heal** and **7. `normalize_all_statuses` self-heal** — racer edits a
   different key/region during our append / status-collapse; assert our tagged section (resp. the
   canonical status) *and* the racer's edit both survive. Each of the five refactored writers gets a
   concurrency test; without these two, `append_body_section` and `normalize` would carry the new
   guarantee unwitnessed.
8. **Ingest absorb.** A `_bump_last_seen` `VaultConflict` on one lead → `upsert` returns `refused`, the
   ingest batch keeps running, and that lead stays out of `seen.db` (so it is retried next run).
9. **Triage resilience — both sites.** Inject a `VaultConflict` on one lead's `apply_classification`
   (site `:56`) and, separately, on one lead's `apply_verdict` (site `:92`); each time assert the
   *other* lead's outcome is still `applied` **and** the conflict is counted in `report.failures`
   (assert survivors-processed + counted, never merely "did not raise").
10. **Permission preservation.** `chmod` a seeded note to a distinctive mode, run a modify-write,
    assert `stat.S_IMODE` unchanged (guard the assert for portability).
11. **Store conformance.** Add a conflict-outcome property to the store conformance suite alongside
    `last_seen`-monotonicity (§2a).

**Mutation-witness** (mutate by MOVING/DELETING — never ADDING — run each in isolation, restore
byte-clean). New tests added to *existing* files cannot be isolated by "run the file alone"; run each
**by node id** and confirm no pre-existing test in the file reddens:

- Delete `_cas_write`'s `_read(path) == text` commit guard (unconditional commit) → test (1) reddens.
- Delete the exhaustion `raise VaultConflict` → test (2) reddens.
- **Re-derive `_bump_last_seen`'s monotonic compare from the stale captured snapshot instead of the
  transform's fresh `text`** → test (3) reddens *alone*. (The generic "weaken the monotonic branch"
  mutant is killed by the pre-existing sequential tests `test_vault.py:77,90`, so it witnesses nothing
  about test (3); record that explicitly.)
- Make `set_tailored_cv`'s transform set `tailored_cv` unconditionally (ignore `only_if_absent`) →
  test (5) reddens.
- Delete the `append_body_section` fresh-tag check / the `normalize` fresh-canonical recompute → tests
  (6)/(7) redden.
- Remove the `upsert` conflict→`refused` absorb → test (8) reddens (a raw exception escapes the sink).
- Delete each triage site's `except VaultConflict` (one per site) → the corresponding scenario in
  test (9) reddens.

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

1. `_atomic_write`, `_cas_write`, `_RMW_RACE_RETRIES` in `core/vault.py`; `VaultConflict` in a **core**
   module the Store protocol references (`core/protocols.py` or a `core/errors.py` it imports) — §2a.
2. The five writers refactored to `transform` closures through `_cas_write`; `write_document` on
   `_atomic_write` (no CAS); `_atomic_write` preserves the target's mode (permission-drift risk).
3. `upsert` **absorbs** its `_bump_last_seen` conflict into `refused` (§2a);
   `set_tailored_cv(ref, value, *, only_if_absent=False) -> bool`; `run_one` gains `guard_existing_cv`
   (batch `True`, direct `False`) threaded straight to `only_if_absent` (§4).
4. Every raise site handled per the §5 table: `except VaultConflict` at `triage/engine.py:56,92`;
   verify/guard `apply/record.py:27`; wrap `track confirm` (`{"ok": False, "reason": "conflict"}`);
   catch the cv direct path; `normalize_all_statuses` per-note wrap → `conflicts`/`skipped` bucket.
5. `core/protocols.py`'s `update_fields`/`append_body_section` docstrings note "may raise
   `VaultConflict` on sustained conflict"; the store conformance suite gains a conflict-outcome
   property (alongside `last_seen`-monotonicity).
6. Concurrency test suite (the eleven cases above) passing; every mutation-witness confirmed (RED
   under the mutant, restored byte-clean; new tests isolated **by node id**, not "run the file alone").
7. Docs: `docs/ARCHITECTURE.md`'s vault section notes the RMW-race guard, **and** its
   store-contract/conformance paragraph gains the conflict outcome (part of the work, not a follow-up).
8. Full suite green, `ruff check sluice tests` clean.
9. PR body: `Closes #16`, with the `flock`-hybrid documented as a deliberate non-goal; and the
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
