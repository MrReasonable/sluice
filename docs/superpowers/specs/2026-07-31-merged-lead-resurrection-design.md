# Merged-lead resurrection — the write path must honour a human's merge decision (#81)

- **Date**: 2026-07-31
- **Origin**: issue #81, filed during `/review-plan` on #80 and **before #80 landed**. Two of the
  three premises in the issue body were invalidated by #80 shipping (PR #82); see "What #80
  changed", below. The core defect is unaffected and is confirmed by execution here.
- **Status**: designed, approved. Implementation follows in the plan.

## Problem

`sluice leads dedupe --merge <id>` is the one human-gated pass that acts on the TOOL's computed
duplicate set. It unions the losers' audit trail onto the survivor and archives each loser to
`Job Applications/Job Leads/_merged/` — reversible, invisible to `read_leads`.

`Vault._resolve_path` (`core/vault.py:173`) never consults that archive. It walks
`leads_dir/<name>.md` for each name candidate and returns `create` at the first one that does not
exist. A merged-away loser's note is no longer at any of those names, so a re-scrape of the same
posting resolves to `create` and mints a brand-new note with `status: new`.

Normally `seen.db` hides this: the loser's `dedup_key` was recorded when it was first ingested, so
`ingest/engine.py:93` filters it before it ever reaches the sink. The defect is only reachable when
the dedup set is empty — and an empty dedup set is still reachable in the field, deliberately (see
below). When it is, the report reads as an ordinary `created: N`. Nothing distinguishes a
resurrection from a genuinely new lead.

**The harm is asymmetric and irreversible in the direction that matters.** The re-created note is
indistinguishable from a real lead, so it is triaged, shortlisted, and can be applied to — a second
application to one job under the user's name, which cannot be unsent.

### Confirmed by execution, not by reading

Against `10b0cdd`, driving the real `Vault` and the real `merge_cluster`:

```
active notes before merge: ['Example Ltd - Senior Widget Engineer',
                            'Example Ltd - Widget Engineer, Senior']
clusters:                  [['Example Ltd - Senior Widget Engineer',
                             'Example Ltd - Widget Engineer, Senior']]
archived:                  ['Job Applications/Job Leads/_merged/Example Ltd - Widget Engineer, Senior.md']
active notes after merge:  ['Example Ltd - Senior Widget Engineer']

re-ingest of the merged-away lead -> created          # ← reported as ordinary `created: N`
active notes now:          ['Example Ltd - Senior Widget Engineer',
                            'Example Ltd - Widget Engineer, Senior']
```

Two corrections to the issue's framing, both found by running it rather than reasoning about it:

- **The drift that reaches this is title WORD ORDER, not company spelling.** `cluster_duplicates`
  requires `_norm_tokens(company)` to be EQUAL, so `Example Ltd` / `Example Limited` clusters
  nothing. `Senior Widget Engineer` / `Widget Engineer, Senior` share a token SET, so they cluster —
  and they produce different filenames, which is precisely why the survivor's name does not catch
  the loser's re-scrape.
- **Line numbers in the issue body have drifted.** `_resolve_path` is `vault.py:173` (the issue says
  148–177); `merged_dir` is `vault.py:718` (the issue says 674). Cite the code, not the issue.

### What #80 changed, and what remains

The issue names two routes to an empty dedup set. Both are now closed:

- *"`SeenDb.load` swallows every failure and returns an empty set"* — closed. A CORRUPT db now
  raises, and `SeenDb.load`'s comment names #81 as the harm it is preventing (`seendb.py:37`, `:62`).
- *"`seen.db` is cwd-relative"* — closed. It resolves through `core/paths.py` (`env_var="SEEN_DB"`,
  kind `state`), and a relocated store refuses.

**The residual that keeps #81 live is deliberate.** An existing db with no `seen_jobs` table still
reads as EMPTY rather than raising (`seendb.py:46-51`): 0-byte files left behind by the pre-#80 bug
are valid empty sqlite dbs, so raising there would hard-fail exactly the users the #80 fix was for.
An empty dedup set therefore remains reachable by design, alongside the ordinary cases — a fresh
machine with a synced vault, a retargeted `SEEN_DB`.

So #81 is **not** "dedup state is easy to lose". It is: *an empty dedup set is reachable BY DESIGN
for one population, and the write path holds no independent record of a human's merge decision.*

## The record already exists — no new store

The issue offers two proposals: have the candidate walk consult `_merged/`, or keep a durable
merged-name index independent of `seen.db`. **That is a false fork.** The archived loser note IS a
durable index independent of `seen.db`: it carries the loser's full frontmatter — company, role,
location, url — which is everything `same_opportunity` needs, in the vault, human-readable, and
reversible by moving the file back.

A separate index was rejected, and the reasons are worth recording because they will come back:

- It would be keyed on `dedup_key`, which is exactly what `seen.db` keys on. It would be a second
  `seen.db` holding only merged keys — derived data duplicating what is already on disk, with its
  own drift and its own corruption modes.
- `merge_cluster` receives `loser_refs` (paths), not `Lead`s, so it cannot compute `dedup_key`
  without reconstructing a `Lead` from frontmatter — coupling the archive step to the lead model.
- Per #80 it would be a new relocatable state path, needing an env var, a config key, an XDG kind,
  and a refuse-on-relocation notice. That is a large amount of machinery to re-derive a fact the
  vault already holds.

## The change

One new branch in `_resolve_path`, at the single point that creates:

```python
for name in names:
    path = os.path.join(self.leads_dir, f"{name}.md")
    if not os.path.exists(path):
        if self._archived_match(names, lead, capped):   # #81
            return None, "merged_away"
        return path, "create"
    ...                                                  # update / merge / advance: untouched
```

Every existing `update`, `merge` and `refuse` outcome is byte-identical. Only the create arm gains a
predecessor. That is deliberate: `_resolve_path` is #5's walk, its docstring records a lot of
hard-won reasoning, and #81 was filed separately BECAUSE editing it is delicate. A change confined
to one arm is also cleanly mutation-testable — deleting the branch reddens exactly one new test and
nothing else.

**The probe covers ALL candidate names, not just the one the walk stopped at.** The walk returns at
its first absent candidate, but the loser may have been archived under its location-suffixed or
title-digest name (candidates 2 and 3), which the walk would never reach. Probing the full candidate
list costs at most three extra `os.path.exists` calls, on the create path only.

**It also probes the numeric-suffix variants.** `merge_cluster`'s `O_EXCL` reservation loop
(`vault.py:736-744`) archives a name-colliding loser as `<stem>.1.md`, `<stem>.2.md`, and so on.
#81's own scenario is what produces one: resurrect, then merge away again. Probing `<name>.md` alone
would miss every loser after the first at a given name. The probe walks `n = 1, 2, …` until a probe
misses, mirroring the archive loop exactly.

### The verdict must be SHARED with the active walk, not re-implemented

The probe has to reach the same SAME / UNKNOWN / DIFFERENT decision the active walk reaches,
including the `url_proven` and `title_lost` refinements PR #48 added. A second copy kept in sync by
a comment is the #30 failure mode — a check that must match another check, with prose standing in
for the guarantee.

So the existing verdict block inside the loop is extracted to one helper that both callers use.
The active walk's behaviour must be unchanged by the extraction; that is what the 1780 existing
tests are for.

Verdict handling in the probe:

| archived note's verdict | probe |
| --- | --- |
| `DIFFERENT` (or `title_lost`) | keep probing — this lead is genuinely a different job |
| `SAME` | hit → `merged_away` |
| `UNKNOWN` | hit → `merged_away` |

**UNKNOWN suppresses**, which matches #5's asymmetry rather than inverting it. In the active walk
UNKNOWN means "do not split" (merge, never mint a second note). Here it means "do not resurrect".
The failure directions are not symmetric: a wrong suppress writes no note for a genuinely different
job, which is counted in the report and recovered by moving the archived note back out of
`_merged/`; a wrong create is the irreversible harm this whole issue is about.

## Outcome plumbing

`merged_away` becomes a fifth `upsert` outcome, threaded through three sites:

1. **`ingest/sink.py`** — added to the positive allowlist at `sink.py:41`, so the lead IS recorded
   in `seen.db`. This self-heals the dedup state: the resurrection is suppressed once and then
   filtered at ingest on every later run, rather than the archive being probed forever. The
   allowlist's comment states its rule as "a note now EXISTS" — the archived note does exist, so the
   rule holds, but the comment must say so explicitly. A widened allowlist sitting under prose that
   no longer obviously covers it is how a rationale goes stale silently (#9/PR #76).
2. **`cli.py:225`** — a sparse report line, printed only when non-zero, alongside `merged` and
   `refused`.
3. **`ingest/engine.py:34`** — no change; `written` is a sparse dict and every read uses `.get`.

**Naming.** `merged_away` shares a word with `upsert`'s existing `merged` (merged-on-uncertainty,
which WRITES a `last_seen` bump), so a run can print `2 merged, 1 merged_away`. It was chosen anyway
because it names the human's own action, which is what makes the line legible to the person reading
it: *you merged this away.* `suppressed` and `archived` were considered and say less about the cause.

## One `Store` contract addition

`merge_cluster` is on the `Store` protocol, so **"a lead merged away via `merge_cluster` is never
re-created by `upsert`"** is a store-agnostic safety property, not a vault-store mechanism. It is in
the never-clobber family: it protects a human's decision from being silently undone, and the harm is
irreversible. That is the class the conformance suite exists for.

Contrast PR #48's ruling, which went the other way: title-level disambiguation of a capped-prefix
collision is a vault-filename mechanism, and a second store keyed on synthetic ids honours it for
free. This one a second store does NOT get for free — a store that archives losers and then creates
freely would resurrect them — so it must be stated.

Three sites, all in the same PR (the PR #45 lesson: a conformance test that widens what every
implementation must do is drift unless the human-readable contract moves with it):

- `tests/conformance/test_store_contract.py` — the property, parameterised over every registered
  store.
- `core/protocols.py` — `upsert`'s docstring. `merged_away` is **MAY-return** (a store keyed on
  synthetic ids has no archive concept); the non-resurrection property is **MUST-honour** for any
  store implementing `merge_cluster`. Those are different strengths and the docstring must not
  collapse them.
- `docs/ARCHITECTURE.md` — the store-contract paragraph near line 293 and the dedupe section near
  line 378.

## Testing

Unit, against a real `Vault` on `tmp_path`:

- **The acceptance property**, which is the falsifier run above, as a test: build a vault, upsert two
  drifting scrapes of one job, `merge_cluster` them, re-upsert the loser's `Lead`, assert the outcome
  is `merged_away` and that `read_leads()` still returns exactly one note.
- A loser archived under its **location-suffixed** name is found — pins that the probe covers all
  candidates, not just the one the walk stopped at.
- A loser archived under a **numeric-suffix** name (`<stem>.1.md`) is found — pins the suffix walk.
  Built by merging away two same-named losers in sequence, not by hand-placing a file, so the
  fixture cannot drift from what `merge_cluster` actually writes.
- An archived note **proven DIFFERENT** does not suppress: a genuinely new job at a merged-away name
  is still created.
- An archived note whose verdict is **UNKNOWN** suppresses.
- The **active walk is unchanged**: update, merge and refuse all behave exactly as before when
  `_merged/` is absent, and when `_merged/` holds a proven-different note.

Sink and CLI:

- A `merged_away` outcome IS recorded in `seen.db` (assert on the store's contents, not on the count).
- The CLI report prints the line when non-zero and omits it when zero.

Conformance:

- The non-resurrection property, in `tests/conformance/test_store_contract.py`.

### Mutation witnesses

Each mutant is made by MOVING or DELETING, never by adding, and each must redden its own witness and
no other. Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
first. Each new test is also run **by node id alone**, to confirm no pre-existing test in the same
file is what catches the mutant.

| mutation | expected witness |
| --- | --- |
| delete the `_archived_match` branch entirely | the acceptance test |
| probe only the candidate the walk stopped at (drop the loop over `names`) | the location-suffix test |
| probe only `<name>.md` (drop the numeric-suffix walk) | the `.1.md` test |
| treat `UNKNOWN` as "keep probing" instead of a hit | the UNKNOWN test |
| treat `DIFFERENT` as a hit | the proven-different test |
| drop `merged_away` from the sink allowlist | the seen.db test |

## Definition of done

1. The falsifier above passes as a committed test; it fails on `10b0cdd`.
2. `_resolve_path`'s update / merge / refuse arms are behaviourally unchanged (full suite green).
3. The verdict logic exists in exactly ONE place, used by both the walk and the probe.
4. `merged_away` is plumbed through sink, seen.db and the CLI report.
5. The contract is stated in all three of the conformance suite, `core/protocols.py`, and
   `docs/ARCHITECTURE.md`.
6. Every mutation in the table above reddens exactly its own witness, verified by node id.
7. `ruff check sluice tests scripts` clean; full suite green.

## Out of scope

- **Routing the re-scrape to the survivor.** Considered and declined for this PR. It would have
  `merge_cluster` stamp `merged_into: <survivor>` into the archived loser, and a probe hit would
  bump the SURVIVOR's `last_seen`. It costs a new frontmatter key, a resolution hop, and a
  dangling-pointer fallback, and it is not needed to close the stated harm.
- **A url index over the archive.** The probe is name-keyed, so a re-scrape whose title drifts far
  enough to change its filename is missed. Closing that means indexing on url rather than name,
  which changes `upsert`'s cost model and is #23 territory.
- **A config knob.** A silent second application is not a preference.

## The residual

Two, both narrower than the defect being closed and both failing toward the safe direction:

- **Name drift past the candidate set.** As above: a re-scrape whose title changes enough to produce
  a different `_note_name` than the archived loser's is not found, and is created. This is the same
  name-sensitivity the active walk already has, and the same one `seen.db` covers in the normal case.
- **A second-order #9 gap this PR does not close.** After a merge, if the board the SURVIVOR came
  from delists the job while the loser's board still lists it, the survivor's `last_seen` stops
  advancing and #9's staleness sweep can expire a posting that is still live. Suppression makes this
  no worse than it is today (the loser's re-scrape already bumped nothing, since it was filtered by
  `seen.db`), but survivor-routing would have closed it. Recorded here so it is not rediscovered as
  a new bug.
