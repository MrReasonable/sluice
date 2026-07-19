# Track dead-letter — an un-acted-on proposal must never silently vanish

- **Date**: 2026-07-19
- **Status**: **READY TO PLAN.** Round 1 `/review-plan` (5 agents): 0 Critical / 3 High / 9 Medium / 4
  Low — all folded. Round 2 re-review (4 agents): 0 Critical / 0 High / 0 Medium / 4 Low — every round-1
  High and Medium verified resolved against the code, the four new Lows (clarity only) folded. The three
  High were all in the failure paths the feature adds — the silent-loss class #49 exists to remove —
  closed by the asymmetric failure semantics (§1) and the advance-gated clear (§3).
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

### 1. `DeadLetterDb` — new, `sluice/track/deadletter.py`

A concrete class in the **track** sub-app (beside `config.py`/`classify.py`/`reconcile.py`) — it is
track-only operational state, written by the track engine and read/cleared by track `confirm`/`dismiss`,
touched by no other sub-app. It **follows `SeenDb`'s defensive conventions** (stdlib `sqlite3`,
`_init`-on-write, a guarded read) but shares **no API and no schema** with it — `SeenDb` is the ingest
scanner's store, and track's own `seen` is a plain **text** file (`app.py:_load_seen`), *not* `SeenDb`,
so this must not read as reusing it. Like `seen`/`lastrun`, it is **not** a `plugins.get` seam — exactly
one implementation and no surface swaps it out.

**Path**: *derived* from the existing track state via a named module constant
`_DEADLETTER_SUFFIX = ".deadletter.db"` → `tcfg.seen_db + _DEADLETTER_SUFFIX`, exactly as
`lastrun_path = tcfg.seen_db + ".lastrun"` is derived — so **no new config knob** (consistent with the
track state paths, which already run on code defaults alone; a `TrackConfig` knob would be premature
surface nobody sets). The suffix is a **named constant with a comment** because the "local, gitignored"
property is load-bearing on the `.db` ending: `.gitignore`'s `*.db` covers it, but `.lastrun` is *not*
ignored, so a future rename to a non-`.db` suffix would silently leak message-ids/slugs/proposal text
(personal data) into a public repo. It is the same class of private runtime state as `seen.db` and the
vault, never committed.

**Schema** — one row per un-acted-on proposal, keyed by Gmail message-id:

```
track_deadletter(
  message_id     TEXT PRIMARY KEY,   -- the Gmail id; stable, unique per email
  lead           TEXT,               -- slug for a confident match; "" when no lead
  candidates     TEXT,               -- comma-joined slugs for an ambiguous proposal; else ""
  ev_type        TEXT,               -- classification type (rejection, unknown, offer, update, ...)
  proposal       TEXT,               -- the human-readable proposal line
  hint           TEXT,               -- report guidance: a runnable `track confirm ...` cmd OR a
                                     --   non-runnable "...; review manually" note (no-action cases)
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

**Failure semantics are deliberately asymmetric — this is the crux of the feature, not a `SeenDb` copy.**
`SeenDb.load`'s bare `except → empty` is safe for `seen` because an empty read self-heals (messages
re-process). It is *unsafe* here: every dead-lettered id is already in `seen` (see §2), so a re-run
skips it at `engine.py:55` and never re-records — a silently-empty read drops the whole backlog
**permanently**, re-creating #49's exact bug inside its fix. Therefore:

- **Writes (`record`, `bump_surfaced`, `clear_*`) RAISE on failure.** A failing `record` must propagate
  so `engine.py:96`'s per-message `except` skips `seen.add(mid)` and the message re-processes next run.
  A swallowing write that let `seen.add` commit would lose the proposal forever — the anti-guarantee.
- **Reads (`open_entries`) distinguish missing from corrupt.** A *missing* db (first run) → empty, fine.
  A *corrupt/unreadable* db → **raise / fail loudly** (surfaced to the operator), never a silent empty.
- `_init`-on-write creates the table (this part does mirror `SeenDb._init`).

The two calls *outside* the per-message `try` — `bump_surfaced()` at run start (§2 step 1) and
`open_entries()` at report assembly (§2 step 3) — raise straight out of `run()` before `app.py`'s
`_save_seen`/`_save_lastrun` (`app.py:411-414`) execute. That is **fail-safe, not a gap**: nothing has
been `seen.add`'d, the watermark is unadvanced, the backlog is untouched, and the whole run re-runs
cleanly next invocation (idempotent `INSERT OR IGNORE` `record` makes a crash after commit but before
`seen.add` a no-op). The only loss a swallowed `bump` could cause is a staleness *counter*, never a
proposal.

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

- **`track confirm --lead X`** — clears that lead's proposals **only when the advance actually
  happens**. The clear lives **in `engine.confirm`** — where the `can_advance` gate and the
  `update_fields` write already are (`engine.py:108-114`) — which gains an injected `deadletter` store
  parameter, the same injected-dependency shape as `run()` (constructed by `Sluice.track_confirm` from
  the derived path, never by the engine itself). It runs *after* `can_advance` passes and the write
  succeeds, gated on `not dry_run`. **Gating on the advance succeeding, not on `not dry_run` alone, is
  load-bearing**: a confirm that fails `can_advance` returns `ok:False` and must **not** delete the row,
  or the human thinks they acted and the proposal silently vanishes — #49's bug on the clear path. A
  `--dry-run` confirm previews without clearing.
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

**Readers of the renamed field migrate, not just `cli.py`.** `cmd_track_run` (`cli.py:295`) moves to
`open_proposals`, and the five existing hint-correctness assertions in
`tests/test_track_engine.py:102,116-118,134` — which read `rep.proposals[0]` as a formatted string (no
`<status>` placeholder, no fake `--lead "?"`, a real `--to rejected`, and — line 118 — a no-runnable-
action outcome carrying a `review manually` note) — migrate to assert the same guarantees on the
structured `Entry.hint` (which holds that same guidance line, runnable command *or* review note). The
old `proposals` field is **removed**, not kept alongside `open_proposals` (a dead, unpopulated field
kept for a green suite is itself a finding).

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

Fully offline/hermetic (no Gmail, no backend); a **real** temp SQLite `DeadLetterDb` as the default so
SQL bugs surface — the failure-semantics tests below are the deliberate exception, injecting a store
double whose `record`/`open_entries` raises, the only way to exercise the raise-then-skip-`seen` path;
synthetic fixtures pinned to the existing mechanisms — seeded-faker
`titles`/`cfg_titles` (`tests/conftest.py`) for slugs/titles and `FakeClient` message dicts (as in
`tests/test_track_engine.py`) for message-ids, so no real employer, message-id, or role is ever
hardcoded into a dead-letter row.

- **`DeadLetterDb` unit** — `record` then `open_entries` round-trips; `bump_surfaced` increments only
  existing rows; `clear_lead`/`clear_id` delete the right rows and return counts.
- **Failure semantics (the crux)** — a *missing* db reads empty; a *corrupt* db read **raises/surfaces**
  (asserted, so a bare-except regression that swallowed it goes red — otherwise the test green-lights the
  exact swallow class); a write failure **propagates** out of `record`.
- **Record → `seen` ordering** — when `record` raises, `seen.add(mid)` does **not** fire and the message
  is re-processed next run (the anti-silent-loss guarantee, witnessed end-to-end via the engine).
- **`times_surfaced` mixed in one run** — a single run with one carried entry (→2) and one newly-recorded
  entry (stays 1), so a record-before-bump regression is caught (bump-in-isolation alone would miss it).
- **End-to-end `run` durability** — a proposal recorded in run 1 is re-emitted in run 2 (unacted), with
  `times_surfaced` incremented; it disappears after `confirm`/`dismiss`. The regression test for the
  silent-loss bug.
- **Clearing paths** — a successful `confirm --lead X` clears X's entries; a `--dry-run` confirm and a
  confirm that fails `can_advance` (`ok:False`) each clear **nothing**; the exact-match caveat is pinned
  (`confirm --lead A` does **not** clear an ambiguous entry that only listed A in `candidates`).
- **`dismiss` command** — exactly one of `--id`/`--lead` required (arg validation); `--dry-run` reports
  the would-delete count without deleting.
- **Report assembly, no double-count** — non-dry `open_proposals` count equals the open-row count (new +
  carried, never duplicated); dry-run `open_proposals` unions persisted + computed-new keyed by
  `message_id` and a shared id appears **exactly once**.
- **Migrated guard tests** — the five `tests/test_track_engine.py` hint assertions now read `Entry.hint`,
  asserting the same guarantees (no `<status>` placeholder, no fake `--lead "?"`, a real `--to rejected`).
- **Never-regress** — a dead-lettered `unknown` (and an unmatched proposal) writes no status across runs.
- Every guard mutation-witnessed (move/delete → red → restore byte-identical), per the repo cadence.

## Docs to update (part of the work, not a follow-up)

`docs/ARCHITECTURE.md` lands with the code, or it drifts the moment this merges:

- the **track sub-app paragraph** gains *durable proposal surfacing* and names the new
  `track/deadletter.py` module (track is described in prose — do **not** add it to the `## core/`
  bulleted module list, which would re-create the core/-vs-track/ confusion arch-001 resolved);
- the `app.py` **owned-state sentence** ("file-backed seen-message set and last-successful-run
  watermark") gains the **third** store, the dead-letter db;
- the **method enumeration** gains **`track_dismiss`**.

## Out of scope (separable future issues)

- **Auto-retry of transient classify failures** — re-fetching pending message-ids by id and
  re-classifying up to a cap. Would auto-recover a transient `unknown`; adds a by-id fetch loop, an
  attempt cap, and per-pending backend cost. Deliberately deferred.
- **Elevating dead-letter to a Store conformance property** — this is track-app operational state, not a
  Store-seam behaviour; no second-store contract.
