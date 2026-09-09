# Lead archival: retiring dismissed lead notes out of the vault

Status: **draft, awaiting decisions** (see "Decisions needed" at the end).
Date: 2026-09-09.

## The problem, measured

`Job Applications/Job Leads/` holds **4195 notes in one flat directory**, plus 65 under
`_merged/`. Of the 4195, **3860 are `status: dismiss`, 92%.** They accumulate because
sluice has no retirement step: `leads` offers `add`, `dedupe`, `expire`, `dismiss`,
`reconcile` and `rename`, and none of them removes a note. `expire` makes it worse by
design, converting a stale lead to `dismiss`, so the pile only grows.

What that costs, and what it does not:

- **It does cost vault weight.** `Job Leads/` is 35MB of the 60MB vault, every byte
  replicating to the Mac over Syncthing and rendering as a flat 4195-note folder in
  Obsidian.
- **It does not cost triage anything measurable.** `read_leads(TRIAGE_OWNED)` returns 4115
  notes in **0.3 seconds**. Any argument for this feature framed as a runtime speed-up is
  false and should be rejected.

**The honest case for building this is growth, not the current size.** The oldest dismissed
note is only **63 days old**: the vault holds about two months of history and is
accumulating dismissals at roughly **61 a day**. Left alone the dismissed population reaches about
6000 by mid-October and 8900 by December. A retention window converts unbounded growth into a
steady state of roughly `window x 61` notes. That is the whole benefit, and it should be
stated that way rather than dressed up as a cleanup.

## Constraints that shape the design

Six facts from the current code and deployment. Each rules something out.

**1. `_merged/` is already an in-vault archive, and the pattern works.** `_walk` prunes it
by NAME (#81), so `read_leads` never returns a note filed there; `Vault._resolve_path`
still consults it, so a note stays findable for restore; and notes carry an
`archived_from_note` stamp recording provenance. A new holding area should reuse this
machinery rather than invent a second, subtly different one.

**2. Deduplication does not depend on the note files.** Ingest dedups against `seen.db`
(12.4MB, `job-scanner/seen.db`), separate storage. Removing a note therefore cannot cause a
lead to be re-ingested, provided `seen.db` is left alone. **The design must never touch
`seen.db`.** That single guarantee is what makes archival safe at all.

**3. `dismiss` is in `TRIAGE_OWNED`, and a `--status dismiss` sweep is the documented
recovery path.** When a release changes how leads are judged, recovery for a wrongly
dismissed lead is to re-select it by status; the #223 pay-basis re-verdict relied on exactly
this. Archiving removes a note from that sweep, so **the selection rule must be time-boxed**
and must never archive a recent dismissal.

**4. Syncthing file versioning is OFF for the `obsidian-vault` folder** (confirmed via
`/rest/config/folders`: empty `versioning.type`). A deletion on Hermes propagates to the Mac
permanently, with no `.stversions` copy. The archive is therefore the only copy, which
forces write-verify-then-remove ordering and rules out any design where the vault removal
happens first.

**5. The repo's report-first convention.** `expire`, `dedupe`, `reconcile` and `rename` all
report by default and mutate only under an explicit flag, and `lead_ttl_days` uses the
"0 means off" idiom. This feature must match both, or it becomes the one destructive command
in the family that behaves differently from its siblings.

**6. Filesystem mtime is NOT a proxy for when a lead was dismissed.** Bulk passes rewrite
notes: the #223 re-verdict touched thousands, and nightly triage re-judges and rewrites
`research` leads. Measured consequence: 3137 of 3860 dismissed notes have an mtime inside 14
days, while their `first_seen` dates spread across two months. **Selecting on mtime would
under-archive massively and unpredictably.** `first_seen` is present on **3369 of 3860**
(87%), parses cleanly on all of them, and is the only trustworthy age signal available.

## Approaches considered

**A. In-vault `_archived/`, mirroring `_merged/`.** Restore is nearly free and constraint 4
stops mattering, since nothing leaves the vault. **Rejected as the primary goal:** it does
not solve the stated problem. The 35MB still syncs and Obsidian still shows the folder. It
only removes notes from a read set that already costs 0.3s.

**B. Out-of-vault archive, no in-vault trace.** Solves the weight problem completely.
**Rejected:** it destroys the in-vault record that a role was seen and rejected, which is the
only human-readable answer to "did we already look at this company?" once the note is gone.

**C (recommended). Out-of-vault archive, with an in-vault index and a restore path.** Move
the note to an archive root outside the vault, append a manifest row, and maintain one
in-vault index note. One file replaces hundreds. The vault keeps the record, the Mac stops
carrying the bulk, `seen.db` keeps the dedup guarantee, and `leads archive --restore <slug>`
brings a note back when a sweep needs it.

## Design

### Command surface

```
job-sluice leads archive [--apply] [--older-than DAYS] [--limit N] [--json]
job-sluice leads archive --restore <slug>
```

Report-first: without `--apply` it prints what it would archive, writes nothing, exits 0.
`--older-than` overrides the configured window for one run. `--limit` bounds a first
cautious pass.

### Selection rule

Eligible when **all** hold. Each conjunct closes a specific hazard, so none may be dropped
without re-reading this section.

1. Normalized status is exactly `dismiss`. Not "not in the active set": `unjudgeable` and
   `needs_review` are unfinished work, `rejected` is application history.
2. The note is not under `_merged/`. That is dedupe's holding area with its own restore
   semantics; two archival mechanisms must not overlap on one file.
3. `first_seen` is present and parses as a date, and `today - first_seen > window`.
4. The note carries a company or a role. A note with neither is already skipped by
   `read_leads` and is not ours to move.

**A note without a usable `first_seen` is never archived.** It is counted and reported as
"no age signal" so the population stays visible. This is deliberate, per constraint 6: the
only fallback available is mtime, mtime is known-unreliable here, and guessing wrong deletes
a note off the Mac permanently. 491 notes are in this state today; if that number matters
later, the fix is to backfill `first_seen`, not to loosen this rule.

### Move mechanics, ordered for constraint 4

Per note, in this order, never batched:

1. Write the note's full content to `<archive_root>/<YYYY-MM>/<slug>.md`, where `YYYY-MM`
   comes from `first_seen`. Create parents as needed.
2. Read the archived file back and compare bytes against the source. A mismatch aborts that
   note, leaves the vault copy untouched, and records a failure.
3. Append one JSON line to `<archive_root>/manifest.jsonl`: `slug`, `company`, `role`,
   `url`, `first_seen`, `archived_at`, and `archived_from` (the original vault-relative
   path).
4. Only now remove the vault note.

If the process dies between any two steps the worst outcome is a duplicate archive copy or
an unreferenced manifest row, never a lost note. That asymmetry is the point of the ordering.

### The in-vault index

Maintain `Job Applications/Archived Leads.md`, rewritten from `manifest.jsonl` at the end of
an `--apply` run: a dated table of company, role, `first_seen`, and archive path. Derived
from the manifest rather than appended to incrementally, so a partial run cannot leave the
index disagreeing with the archive. Same "render a summary note from a log" shape
`render_rejected_note` already uses for the rejected-leads audit.

### Restore

`--restore <slug>` finds the manifest row, copies the file back to its recorded
`archived_from` path, verifies, removes the archive copy, and marks the row restored. It
refuses when a note already exists at the target path rather than overwriting, and refuses
when a slug appears in more than one unrestored row rather than guessing.

### Configuration

Two new keys, following the `lead_ttl_days` idiom:

- `archive_dismissed_after_days` (int, default **0 = off**). Nothing archives until it is
  set. A destructive default is not acceptable for a feature whose blast radius is the Mac's
  copy of the vault.
- `archive_root` (path, default `resolve(kind="state", name="archived-leads")`, i.e.
  `~/.local/state/sluice/archived-leads`). Must resolve OUTSIDE `VAULT_DIR`; the command
  refuses to run otherwise, since an archive root inside the vault would sync the bulk
  straight back and silently defeat the feature.

## Failure modes and what happens

| Condition | Behaviour |
|---|---|
| `archive_dismissed_after_days` is 0 | Report "archival is off", exit 0, write nothing. Mirrors `expire`'s unset-TTL message. |
| `archive_root` resolves inside the vault | Refuse, exit 2, name both paths. A usage error, not a crash. |
| Note has no usable `first_seen` | Never archived. Counted and reported as "no age signal". |
| Archive write fails (disk, permissions) | Skip that note, vault copy untouched, count a failure, exit non-zero. |
| Read-back mismatch | Same. The vault copy is never removed on an unverified archive. |
| Vault removal fails after a good archive | Report it: a duplicate now exists. Re-running is safe, the archive write being idempotent on content. |
| Slug appears twice unrestored in the manifest | Restore refuses and names both rows. |

## Testing

Each with the production edit that would break it:

- A `dismiss` note inside the window is NOT selected (delete the age comparison).
- A `dismiss` note with **no `first_seen`** is NOT selected, even when its mtime is ancient
  (this is constraint 6, and it is the test most likely to be "simplified" away later).
- `needs_review` / `unjudgeable` / `rejected` notes are never selected (drop a status
  conjunct).
- A note under `_merged/` is never selected (drop conjunct 2).
- Without `--apply` the vault is byte-identical afterwards (the report-first contract).
- The archive copy exists and matches before the vault copy is removed: simulate a failed
  read-back and assert the vault note survives.
- **`seen.db` is byte-identical across an `--apply` run.** This is constraint 2 and deserves
  its own test, because a future refactor that "tidies up" by pruning seen entries would
  silently re-ingest every archived lead.
- Restore round-trips a note to its original path, and refuses when the target exists.
- An `archive_root` inside `VAULT_DIR` exits 2.

## Out of scope

- Archiving anything other than `dismiss`.
- Automatic scheduling. This runs by hand until it has been watched working at least once.
- Compressing or pruning the archive. It is markdown; a retention policy for the retention
  policy is not needed.
- Backfilling `first_seen` on the 491 notes that lack it. Worth doing, separately.
- Any change to `seen.db`, ingest, or the scanners.

## Decisions needed

1. **Window.** Measured against real `first_seen` dates today:

   | window | archived now | dismissals left | steady state (~61/day) |
   |---|---|---|---|
   | 30d | 1685 | 2175 | ~1830 dismissed |
   | 45d | 965 | 2895 | ~2745 |
   | 60d | 423 | 3437 | ~3660 |
   | 90d | **0** | 3860 | ~5490 |

   **90 days archives nothing**, because the vault is only 63 days old. I recommend **30
   days**: it is the only option that meaningfully caps growth, and it still leaves a month
   of dismissals sweepable for the constraint-3 recovery case, which is longer than the #223
   re-verdict needed.

2. **In-vault index: keep or drop?** It costs one large note. If the out-of-vault manifest
   is enough for you, dropping it removes the rewrite-from-manifest step entirely.

3. **Enable Syncthing versioning on `obsidian-vault` first?** Currently off, so the first
   `--apply` deletes files on the Mac with no local safety net. Turning on `simple`
   versioning with a small keep count first would make this reversible from the Mac side
   too. **I would do this regardless of the feature.**

4. **First run bounded?** Run `--apply --limit 50` once, confirm the Mac side and Obsidian
   look right, then run unbounded.

## Appendix: how the numbers were measured

All against the live vault inside the Hermes container on 2026-09-09.

- Status counts: walked `Job Leads/`, parsed the first `status:` line of each `.md`. 4195
  flat notes, 3860 of them `dismiss` (a recursive walk including `_merged/` gives 4260 and
  3909; the flat figures are the ones this feature acts on).
- Read cost: timed `store.read_leads(set(_status.TRIAGE_OWNED))`: 4115 notes, 0.3s.
- `_merged/` exclusion: `read_leads` returned 4115 where a raw walk found 4179 triage-owned
  files, a 64-note difference against the 65 notes in `_merged/` (the remaining one is not
  triage-owned). Consistent with the prune-by-name in `_walk`.
- Age signal: `first_seen` present and parseable on 3369 of 3860 dismissed notes, absent on
  491, unparseable on 0. Oldest 63 days, newest 1 day, median 31 days.
- mtime unreliability: 3137 of 3860 dismissed notes have an mtime inside 14 days, against a
  `first_seen` spread of two months.
- Accumulation rate: 3860 dismissals over 63 days, about 61 a day.
- Syncthing versioning: `GET /rest/config/folders`, `obsidian-vault`, empty
  `versioning.type`.
