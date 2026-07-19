# Track dead-letter — an un-acted-on proposal must never silently vanish

- **Date**: 2026-07-19
- **Status**: **READY TO PLAN.**
- **Origin**: issue #49, surfaced in the review of PR #42 (the #40 classify-failure fix) by the invariant
  reviewer (Low, `requires_human_judgment`) and confirmed by a fresh architect. Pre-existing; broader
  than #40.
- **Scope decision (user-confirmed)**: **durable surfacing only.** Every un-acted-on `proposed` outcome is
  recorded durably and re-surfaced every run until a human acts or dismisses it. Auto-retry of transient
  classification failures (re-fetch + re-classify by id) is explicitly **out of scope** — a separable
  future enhancement, not folded here.

## Problem

`sluice/track/engine.py`'s run loop calls `seen.add(mid)` after processing **every** message
(`engine.py:92`). `seen` (a file-backed message-id set owned by `app.py`, `_load_seen`/`_save_seen`)
means *"processed,"* not *"acted on."* So any outcome `reconcile` returns as `proposed` — a classify
failure (`unknown`, added in #42), an unmatched/ambiguous lead, a soft/low-confidence rejection, an
offer/rejection on a lead that cannot advance — is surfaced in the run's proposals report **exactly
once**. Next run the id is in `seen` and skipped.

For a **transient** failure or an unmatched proposal in an **unattended** run whose proposals report is
never read, the item is effectively lost: reported once, to no one, and never again. This is why #40's
"a rejection email vanishes forever" symptom is **reduced but not eliminated** — #42 made the failure
surface loudly instead of silently, but surface-**once**-then-dedupe is a property of the dedup model,
not of classification, and it predates #40.

## Key finding — the `lastrun` watermark makes "just don't `seen.add`" non-viable

The issue lists three candidate mechanisms; one is disqualified by the code, which narrows the design
before it starts.

`app.py:401` scopes the Gmail query to `after: <last successful run>` (the `lastrun` watermark), and
`app.py:414` advances that watermark to *now* on every successful run. So the issue's third option —
*"bounded by construction: don't `seen.add`, re-fetch next run within the lookback window"* — **does not
work**: the watermark marches past the un-acted-on message on the very next run, so it falls out of the
query window regardless of whether it is in `seen`. **Re-fetching via the query can never be the retry
mechanism.** Any durable behaviour must be decoupled from both `seen` and the watermark. A durable
side-store is therefore the only coherent shape for "surface until acted."

## The governing rule

**An un-acted-on proposal is surfaced every run until a human deliberately clears it. It is never
auto-dropped.** Auto-dropping is exactly the silent loss #49 removes; a re-surfacing backlog is visible
and the human's `dismiss` is the lever. Noise is the signal to dismiss, not a reason to forget.

This is uniform across **all** `proposed` types — the shape the invariant reviewer and architect both
required. The one-type patch they rejected (skip `seen.add` only when `ev.type == "unknown"`) is not
used: `seen` keeps its single meaning, and a separate durable store handles surfacing for every proposed
outcome identically.

## Design

### 1. `DeadLetterDb` — new, `sluice/core/deadletter.py`

A concrete class mirroring `SeenDb`'s shape (stdlib `sqlite3`, defensive `try/except`, path owned by
`app.py`). Like `seen`/`lastrun`, this is **not** a `plugins.get` seam — there is exactly one
implementation and no surface swaps it out (the same reasoning `app.py` already records for the track
seen/lastrun state).

**Path**: *derived* from the existing track state, `tcfg.seen_db + ".deadletter.db"`, exactly as
`lastrun_path = tcfg.seen_db + ".lastrun"` is derived — so **no new config knob**. It is a local,
gitignored SQLite file: the same class of private runtime state as `seen.db` and the vault, never
committed.

**Schema** — one row per un-acted-on proposal, keyed by Gmail message-id:

```
track_deadletter(
  message_id     TEXT PRIMARY KEY,   -- the Gmail id; stable, unique per email
  lead           TEXT,               -- slug for a confident match; "" when no lead
  candidates     TEXT,               -- comma-joined slugs for an ambiguous proposal; else ""
  ev_type        TEXT,               -- classification type (rejection, unknown, offer, update, ...)
  proposal       TEXT,               -- the human-readable proposal line
  hint           TEXT,               -- the runnable `sluice track confirm ...` hint
  first_seen     TEXT,               -- ISO date first recorded
  times_surfaced INTEGER             -- run-count it has been surfaced (staleness signal)
)
```

Storing `proposal`/`hint`/`lead` means re-surfacing needs **no re-fetch and no re-classify** — the row
carries everything the report line needs. That is what makes "durable surfacing only" cost nothing per
run (no backend call, no Gmail call for carried entries).

`Entry` is a lightweight `@dataclass` (frozen) holding the eight columns above — the read shape returned
to callers, so `engine.py`/`cli.py` never touch raw `sqlite3` rows.

Methods (shaped to the two call sites — `run` and `confirm`/`dismiss`):

- `open_entries() -> list[Entry]` — every open row, oldest `first_seen` first.
- `bump_surfaced() -> None` — `UPDATE ... SET times_surfaced = times_surfaced + 1` over all existing rows.
- `record(entry) -> None` — `INSERT OR IGNORE` a new row (`times_surfaced=1`, `first_seen=today`).
- `clear_lead(slug) -> int` — `DELETE WHERE lead = ?` (exact).
- `clear_id(message_id) -> int` — `DELETE WHERE message_id = ?`.

A read on a missing/corrupt db returns empty (mirrors `SeenDb.load`); a write initialises the table
(mirrors `SeenDb._init`).

### 2. Run-loop mechanics — `engine.py`

The store is passed into `engine.run(...)` as a concrete argument alongside `seen` (like `client`). Per
run, all writes gated on `not dry_run` (mirrors the `seen`/`lastrun` save-on-success gating in `app.py`):

1. **Bump carried entries first** — `bump_surfaced()` over rows present at run start (they are surfaced
   again this run). Done before new records are inserted, so new rows keep `times_surfaced = 1`.
2. **Process new messages** as today. Each `res.action == "proposed"` outcome → `record(...)` a new row.
   `seen.add(mid)` still fires, unchanged — the dead-letter, not `seen`, is the durable record. The
   `seen` contract is untouched (no two-tier semantic).
3. **Emit the full open set** — `rep.open_proposals` (structured), oldest first.

The engine always collects this run's freshly-computed `proposed` outcomes into a local list (as it does
today). Report assembly then depends on `dry_run`:

- **Non-dry run**: after `record(...)`, `open_entries()` already contains the new rows, so
  `rep.open_proposals = open_entries()` — a single source of truth.
- **Dry run**: steps 1–2's writes are skipped entirely (no `bump_surfaced`, no `record`). The preview
  must still be faithful, so `rep.open_proposals = open_entries()` (persisted carried entries)
  **unioned** with this run's computed-new proposals (keyed by `message_id`, marked as not-yet-recorded),
  so a `--dry-run` shows both what is already waiting and what this run *would* newly record.

The `DeadLetterDb` writes through directly (each `record`/`bump_surfaced`/`clear_*` is its own committed
statement), so there is no whole-file save to add to `app.py`'s non-dry-run path; the engine simply does
not call the write methods when `dry_run`.

**Which outcomes are recorded**: only `res.action == "proposed"`. `reconcile` already returns `skipped`
for pure noise (`not_job`/`update` with no candidates, `reconcile.py:61-69`), so noise never enters the
dead-letter. `applied`/`calendar` outcomes acted already and are not recorded.

### 3. Clearing — `app.py` + `cli.py`

- **`track confirm --lead X`** — on a successful advance, `clear_lead(X)`. The human acted; that lead's
  proposals are resolved. Wired in `Sluice.track_confirm` inside the same `not dry_run` block as the
  `update_fields` write, so a `--dry-run` confirm previews without clearing.
- **New `sluice track dismiss`** — `--id <message-id>` → `clear_id` (the only lever for no-lead entries:
  `unknown`, unmatched/ambiguous); `--lead <slug>` → `clear_lead` ("looked, no action needed", without
  advancing status). Exactly one of `--id`/`--lead` required. Follows the repo's dry-run convention:
  `--dry-run` reports the count it *would* delete without deleting.
- **Known caveat (documented, not silently swallowed)**: an *ambiguous* entry stores its slugs in
  `candidates`, not `lead`, so `confirm --lead A` will not auto-clear an ambiguous entry that merely
  *listed* A among its candidates — those are cleared by `dismiss --id`. Exact-match keeps clearing
  predictable and never clears the wrong lead; a fuzzy candidate-match is deliberately not done.

### 4. Report format & `RunReport` — `engine.py` + `cli.py`

`RunReport` gains `open_proposals: list` (structured rows) in place of the ad-hoc `proposals: list[str]`;
`proposed` still counts **this run's new** proposals. `cmd_track_run` prints every open entry, oldest
first, under an `OPEN PROPOSALS (awaiting action)` header, each annotated with `first_seen` and
`×times_surfaced`, with a `(new)` tag on entries first seen this run — so an aging backlog is visibly
aging and the human knows what to `dismiss`. The summary line gains `open=<N>`.

## Invariant interactions

- **Never-regress (status)**: the dead-letter never writes `status` — it is purely additive bookkeeping.
  `confirm` remains the sole status-writer, still through `can_advance`. A dead-lettered proposal
  (including an `unknown`) can never advance or regress a lead. This is the property the invariant
  reviewer required.
- **Never-clobber (writes)**: no vault write is added or changed. The store is a track-owned sidecar; the
  vault is untouched by recording or surfacing.
- **Neutrality**: the store is local runtime state (message-ids, slugs, proposal text) in a gitignored
  db — the same class as `seen.db`. No personal data enters `sluice/` or `tests/`; tests use synthetic
  fixtures and a temp db.
- **Empty-config-abstains**: no new preference gate; the derived path has a code default and needs no
  config.

## Consequence to name explicitly

Low-value proposals (low-confidence `update`, soft signals) that used to surface once and vanish now
**persist until dismissed** — more visible, and triage-via-`dismiss` is the intended cost. This is the
behaviour change #49 asks for, applied uniformly rather than special-cased.

## Testing

Fully offline/hermetic (no Gmail, no backend); a temp SQLite db; synthetic fixtures.

- **`DeadLetterDb` unit** — `record` then `open_entries` round-trips; `bump_surfaced` increments only
  existing rows; `clear_lead`/`clear_id` delete the right rows and return counts; a missing/corrupt db
  reads empty.
- **End-to-end `run` durability** — a proposal recorded in run 1 is re-emitted in run 2 (unacted), with
  `times_surfaced` incremented; it disappears after `confirm`/`dismiss`. This is the regression test for
  the silent-loss bug.
- **Never-regress** — a dead-lettered `unknown` (and an unmatched proposal) writes no status across runs.
- **dry-run** — a dry-run records nothing and increments nothing, yet its `open_proposals` unions the
  persisted open set with this run's computed-new proposals (the faithful-preview property).
- Every guard mutation-witnessed (move/delete → red → restore byte-identical), per the repo cadence.

## Out of scope (separable future issues)

- **Auto-retry of transient classify failures** — re-fetching pending message-ids by id and
  re-classifying up to a cap. Would auto-recover a transient `unknown`; adds a by-id fetch loop, an
  attempt cap, and per-pending backend cost. Deliberately deferred.
- **Elevating dead-letter to a Store conformance property** — this is track-app operational state, not a
  Store-seam behaviour; no second-store contract.
