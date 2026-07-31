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

Two new branches — one in `_resolve_path` at the single point that creates, and one in `upsert`
beside the existing `refuse` check.

```python
# _resolve_path
for name in names:
    path = os.path.join(self.leads_dir, f"{name}.md")
    if not os.path.exists(path):
        # #81. Returns None, or ONE OF THE TWO outcome strings -- never a bool: the
        # SAME/UNKNOWN distinction decides whether the lead enters seen.db, and a bool
        # cannot carry it. See Outcome plumbing.
        archived = self._archived_match(names, lead, capped)
        if archived:
            return None, archived
        return path, "create"
    ...                                                  # update / merge / advance: untouched
```

Every existing `update`, `merge` and `refuse` outcome is byte-identical. Only the create arm gains a
predecessor. That is deliberate: `_resolve_path` is #5's walk, its docstring records a lot of
hard-won reasoning, and #81 was filed separately BECAUSE editing it is delicate.

**`upsert` needs its own branch, and WHERE it goes is load-bearing.** `_resolve_path` is called only
from `upsert`, which dispatches on the action string: `refuse` returns early, then `update` and
`merge` bump `last_seen`, and anything else falls through to the create arm. Either new action
without a branch reaches `_write(None, ...)` and raises `TypeError` — which `sink.py:46` does NOT catch (it
catches `OSError`), and `engine.py:60` calls `sink.write` OUTSIDE the per-source try, so the whole
ingest run aborts. Verified by execution against the real `Vault` and `VaultSink`.

The branch must sit **beside the `refuse` check, before `os.makedirs` + `ensure_stfolder`**
(`vault.py:562-577`). The obvious placement — beside `update`/`merge` — is after them, and would
have a lead that writes nothing still create the leads dir and the Syncthing marker. That property
is already pinned for the refuse arm by `tests/test_vault.py::test_upsert_refuses_and_writes_nothing`,
and `merged_away` needs the same test (the acceptance test's `read_leads() == 1` assertion passes
either way, because `leads_dir` already exists in that scenario).

**The probe covers ALL candidate names, not just the one the walk stopped at.** The walk returns at
its first absent candidate, but the loser may have been archived under its location-suffixed or
title-digest name (candidates 2 and 3), which the walk would never reach.

**It selects entries from one `os.listdir` by an ANCHORED match — exact name, or exact name plus a
numeric suffix — never by a bare prefix.** `merge_cluster`'s `O_EXCL` reservation loop
(`vault.py:736-744`) archives a name-colliding loser under a numeric suffix, so probing `<name>.md`
alone misses every loser after the first at a given name. But a sequential `<stem>.1.md`,
`<stem>.2.md`, … walk that stops at the first miss is exhaustive only while the sequence has no
holes — and **the documented recovery action punches one**: restoring `<stem>.1.md` out of `_merged/`
leaves `<stem>.2.md` unreachable, which fails toward resurrection. Proven by execution: three
archives built through the real `merge_cluster`, `.1.md` restored, and a sequential walk misses
`.2.md`. One `os.listdir` is immune to holes.

**The anchor is load-bearing, and an earlier draft got this wrong in the dangerous direction.** That
draft said "filtered by prefix", which is a NEW resurrection-class bug — worse than the one the
`listdir` was fixing, and reachable two different ways, each found independently by a different
reviewer:

- `same_opportunity` (`leads.py:198-209`) compares **only url and location — never company, never
  title**. In the active walk the exact filename is what carries title identity; a bare prefix
  removes that anchor and `same_opportunity` cannot recover it. Executed: a merged-away
  `Acme - Widget Engineer, Senior` bare-prefix-matches a later, genuinely different, url-less
  `Acme - Widget Engineer` at the same location → verdict SAME → suppressed **and recorded in
  `seen.db`**, so the real job is never created and never can be, while the run prints a
  `merged_away` count that reads as the fix working.
- Executed separately: `Acme - Widget Engineer` and `Acme - Widget Engineer II`, the second merged
  away, a later scrape of the FIRST → verdict UNKNOWN → also a hit. So the over-match is reachable
  under two different verdicts, which matters because it means the "treat `DIFFERENT` as a hit"
  mutation row does not cover it and it needs its own.

`title_lost` is no backstop — it is gated on `capped` and dormant under 120 chars. Round 1's
sequential `<stem>.N.md` walk could not reach either entry at all, so this defect was created
entirely by the fix for round 1's finding. The match must therefore be
`entry == f"{name}.md"` or `re.fullmatch(re.escape(name) + r"\.\d+\.md", entry)` — hole-immune like
the `listdir`, exact like the sequential walk, which is precisely the set `merge_cluster` produces.

**`_merged/` may not exist at all.** It is created lazily (`vault.py:718`), so on any install that
has never merged, the `listdir` raises `FileNotFoundError`. That is the expected, overwhelmingly
common case and means "no hit" — but it must be handled as its own condition, NOT by a bare
`except OSError`, which would swallow an unreadable-directory error and silently disarm the guard on
exactly the vaults where it matters.

**Cost.** The create path today does ZERO reads — `_resolve_path` returns at the first absent
candidate before any `_read`. The probe changes that to one `os.listdir` of `_merged/` plus a read
and frontmatter parse per ANCHOR-matching entry, because reaching a verdict needs `fm`, not a
`stat`. That is a real complexity change on the create path and `docs/ARCHITECTURE.md` should record
it, not the "at most three `os.path.exists` calls" this spec claimed in an earlier draft. The
directory is at human scale — only a `leads dedupe --merge` adds to it — so no pruning machinery is
warranted.

### The verdict must be SHARED with the active walk, not re-implemented

The probe has to reach the same SAME / UNKNOWN / DIFFERENT decision the active walk reaches,
including the `url_proven` and `title_lost` refinements PR #48 added. A second copy kept in sync by
a comment is the #30 failure mode — a check that must match another check, with prose standing in
for the guarantee.

So the existing verdict block inside the loop is extracted to one helper that both callers use.
**The helper is `vault.py`-private and must NOT move to `core/leads.py`.** It mixes `same_opportunity`
and `_norm_url` (the pure core verdict) with `capped`, `_title_key` and `_title_digest`, which are
filename-cap-specific. PR #48 deliberately kept title comparison OUT of `same_opportunity` to avoid
entangling it with #23's title normalization; reading "exactly ONE place" as an invitation to
consolidate beside `same_opportunity` would undo that ruling. Both callers live in `vault.py`, so
that is the correct altitude.

"The 1780 existing tests cover the extraction" is NOT true, and treating it as the check is the
failure this repo keeps repeating. Mutation-tested: deleting `and not title_lost` from either arm
reddens 2 tests each and deleting `not url_proven` reddens 1 — but **deleting `capped and` leaves
the full suite GREEN**, and it is not an equivalent mutant. It makes `title_lost` fire for short
titles too, so a human correcting a note's `role` in Obsidian (the #16 threat model) turns every
later re-scrape of a short-title url-less lead into an advance, and `last_seen` stops advancing —
which #9's staleness sweep can then read as a dead posting. `capped` is exactly the term this design
threads into the new helper by hand, so mis-threading it is the modelled slip and it lands green.
The extraction therefore needs its own witnesses (below), not the suite.

Verdict handling in the probe:

| archived entry | probe |
| --- | --- |
| read succeeded, `fm` has neither `company` nor `role` | **skip** — not a note at all, never a hit; log it |
| `DIFFERENT` (or `title_lost`) | keep probing — this lead is genuinely a different job |
| `SAME` | hit → `merged_away`, recorded in `seen.db` |
| `UNKNOWN` | hit → `merged_away_unproven`, **NOT** recorded, logged with the lead and the matched path |

**The skip arm is a PREDICATE, not an outcome, and an earlier draft stated it as the latter.**
"Unparseable or 0-byte" names a symptom an implementer cannot test for: a 0-byte reservation yields
`inner=None`, while a file carrying a `---` block whose lines do not parse yields a non-empty
`inner` and the SAME `fm={}`. Both must skip, so the condition has to be written against the parsed
result, not against the file's shape.

**It must key on `company`/`role`, NOT on `url`/`location`.** An earlier draft said "no identity
keys (no `url`, no `location`)" — which would skip a legitimate archived loser and resurrect it. A
real note can carry `url: ""` (`same_opportunity`'s own docstring records that google leads do) and
a blank `location`; that combination is exactly the UNKNOWN case this probe exists to suppress, and
it is indistinguishable from an empty file if you test the same keys the verdict consumes. `company`
and `role` are always non-empty on a note `_render_new` produced and are never read by
`same_opportunity`, so they discriminate "is this a note at all" from "what does this note say"
without the two questions collapsing into one.

**It is not an edge case.** `merge_cluster`'s own `O_EXCL` reservation leaves a 0-byte file under a
real lead's archived name if the process dies before `os.replace`, and its cleanup runs only inside
`except OSError` with the unlink itself wrapped in `except OSError: pass`. `same_opportunity` scores
`fm={}` as UNKNOWN, so without this arm one orphaned reservation would suppress every future lead at
that name — permanently, since the UNKNOWN arm used to enter `seen.db`.

**An `OSError` reading an archived entry must NOT be skipped.** The nearest neighbour is the trap:
`read_leads` (`vault.py:244-247`) does `except OSError: continue`, and copying that shape here would
make an *unreadable* archived loser stop suppressing, re-minting the lead as ordinary `created: N` —
resurrection by way of a permissions error. The error must propagate so `sink.py:46` counts the lead
`skipped` and keeps it out of `seen.db` for a retry next run. Skip means "we read it and it carries
no identity"; it never means "we could not read it".

**The skip arm logs too.** Both hit arms log, and an earlier draft left this one silent — yet it is
the only arm that fails toward *resurrection*, so an archived loser whose note becomes unparseable
would silently stop suppressing with nothing said.

**Why the SAME and UNKNOWN arms are recorded differently.** An earlier draft justified suppressing
on UNKNOWN by saying a wrong suppress is "counted in the report and recovered by moving the archived
note back out of `_merged/`". **Both halves of that were false**, and three reviewers found it
independently. Recording the lead in `seen.db` makes `engine.py:93` filter its `dedup_key` before
the sink on every later run; `SeenDb`'s public API is `load`/`save`/`path` with no removal, and no
CLI exposes one. So the lead could never reach the write path again, and restoring the archived note
gives it no note of its own — it restores the unrelated loser. The report prints bare counts, so the
human could not even tell which job was dropped.

The reading that failed was "UNKNOWN suppressing matches #5's asymmetry". It does not, in a specific
way: in the ACTIVE walk, UNKNOWN leaves the lead represented by a live, visible note (it merges and
bumps `last_seen`). In the probe there is no such note — `read_leads` skips `_merged/` — so UNKNOWN
would leave it represented by nothing at all. The asymmetry argument holds only where the evidence
is strong, so the arms split: **SAME self-heals into `seen.db`; UNKNOWN suppresses but is never
recorded**, so it re-surfaces and re-reports every run until a human acts. Re-reporting is the
correct signal here, not noise — it is the only thing that makes a weak-evidence suppression
visible. Both arms log a warning naming the `dedup_key` and the matched archive path, because a
bare count cannot identify the job.

## Outcome plumbing

**The outcome vocabulary goes from four members to SIX, not five.** Suppression carries an evidence
strength, and that strength decides whether the lead enters `seen.db` — a decision that is
irreversible in one direction, so it cannot ride on a side channel:

| outcome | verdict | enters `seen.db`? |
| --- | --- | --- |
| `merged_away` | SAME — proven | yes; the dedup state self-heals |
| `merged_away_unproven` | UNKNOWN — weak | **no**; re-surfaces and re-reports until a human acts |

Two strings rather than one-plus-a-flag: a single string with a side channel is the second source of
truth this codebase engineers out, and the sink's allowlist is deliberately POSITIVE, so an
unrecognised outcome fails safe by staying out of `seen.db`. This split is a STORE concern despite
looking like policy — the store is the only thing that can report how strong its evidence was; the
sink maps that strength to `seen.db` policy. The MAY-return split below already covers a store with
no such concept.

Threaded through four sites:

1. **`core/vault.py:upsert`** — the branch described above, beside `refuse` and before the makedirs.
   This is the site an earlier draft omitted, and omitting it is not cosmetic: without it the run
   aborts with an uncaught `TypeError`. **Both** strings need the branch; an uncounted second string
   walks straight into that same abort.
2. **`ingest/sink.py`** — the positive allowlist at `sink.py:41`. `merged_away` joins it;
   `merged_away_unproven` must NOT. The allowlist's comment states its rule as "a note now EXISTS";
   the archived note does exist, so the rule holds for the proven arm, but the comment must say so
   explicitly rather than leaving a widened list under prose that no longer obviously covers it
   (#9/PR #76).
3. **`cli.py:225`** — two sparse report lines, printed only when non-zero, alongside `merged` and
   `refused`.
4. **`tests/conformance/test_store_contract.py::test_upsert_return_is_always_within_the_vocabulary`**
   — pins a four-member vocabulary and its own docstring calls itself "the assertion that stops an
   out-of-vocab outcome slipping past the sink's allowlist". It stays **GREEN unmodified**, because
   its scenario produces neither new string — so it is also green against an UNDER-widening that adds
   only one of the two. It must be widened deliberately, to six.

**`ingest/engine.py:34`** needs no change — `written` is a sparse dict and every read uses `.get`.

**The four-member vocabulary is asserted as complete in prose in at least 11 places across 6 files,
and none of them reddens.** Derive that list rather than copying one — the same treatment DoD 7
applies to `#81`'s rationale, for the same reason. It includes `_resolve_path`'s own docstring
(`vault.py:175` — the function being edited), `Vault.upsert`'s (`vault.py:547`), `sink.py:4`, `:9`
and `:39`, and `cli.py:222`. An earlier draft named three of them.

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

This ruling was tested rather than assumed: a reviewer reproduced the resurrection store-agnostically
using the **location-split shape the conformance suite already uses** (`test_store_contract.py:159`)
— no title drift, no `_merged/`, no filenames in the test's vocabulary — and confirmed a synthetic-id
store does not get the property free (the loser row is gone, the natural-key match finds nothing, it
creates).

**That shape is mandatory for the conformance test, not optional.** The obvious setup — this spec's
own falsifier, two notes for one opportunity via title word-order drift — is a **vault-filename
artefact**. A store keyed on synthetic ids reconciles both scrapes to one record, so
`cluster_duplicates` finds no cluster, `merge_cluster` archives nothing, and the assertions ("not
created", "exactly one note") are satisfied **trivially, on exactly the store class the contract
exists to constrain**. That is the "a sweep that discovers nothing passes" failure mode, and the same
file already guards against it one level up at `test_store_contract.py:34-39`. The test must
therefore assert on its own SCOPE — that a merge actually happened and a loser actually became
unreachable — before asserting the property.

Four sites, all in the same PR (the PR #45 lesson: a conformance test that widens what every
implementation must do is drift unless the human-readable contract moves with it):

- `tests/conformance/test_store_contract.py` — the property, parameterised over every registered
  store, in the location-split shape, with scope assertions.
- `core/protocols.py` — `upsert`'s docstring. `merged_away` is **MAY-return** (a store keyed on
  synthetic ids has no archive concept); the non-resurrection property is **MUST-honour** for any
  store implementing `merge_cluster`. Those are different strengths and the docstring must not
  collapse them.
- `core/protocols.py` — **`merge_cluster`'s docstring (`protocols.py:127-140`), the site an earlier
  draft missed.** It currently says losers are "removed/archived" and imposes no obligation that
  survives the call. A store that hard-deletes cannot honour non-resurrection by construction, so the
  new MUST silently narrows `merge_cluster`'s contract — in the one place a second-store author
  implementing `merge_cluster` will actually read, since they have no reason to read `upsert`'s
  docstring. This is the same PR #45 lesson applied to one method and missed on the other.

  **State the OBLIGATION, not the vault's mechanism.** An earlier draft said "requires RETENTION",
  which names how the vault happens to do it and over-constrains everyone else. The obligation is:
  *a merged-away loser must remain discoverable by `upsert` and invisible to `read_leads`.* A
  natural-key tombstone satisfies that; keeping the whole note, as the vault does, is one way and not
  the required one. The docstring must also say what the returned "archived loser handles" mean for a
  store that archives nothing — a tombstone id is a handle.
- `docs/ARCHITECTURE.md` — the store-contract paragraph near line 293 and the dedupe section near
  line 378, plus the create-path cost note.

## Testing

**Fixture discipline.** The control is *non-preference placeholder*, not *randomly generated* — and
an earlier draft got the reasoning backwards, saying the faker pool was "needed only by" the
`cluster_duplicates`-routed test while also saying that test needs relationships "faker cannot
produce". Both cannot hold. The repo has already ruled: `tests/test_leads_cluster.py`'s module
docstring says its cluster fixtures are deliberately generic non-preference placeholders chosen to
exercise specific token-set relationships "that a faker-generated string cannot reliably produce --
**they are not faker-derived**". So a `cluster_duplicates`-routed test is precisely the case faker
cannot serve, and **no test here needs the faker pools**.

What binds:

- **Locations** come from `tests/conftest.py`'s `LOCATIONS` constant (`Alfa`/`Bravo`/`Charlie`,
  "never a real place"). Both files these tests join import it — `tests/test_vault.py:7` and
  `tests/conformance/test_store_contract.py:30` — and the no-personal-data rule binds `tests/` (DoD
  11 permitted place words in a `sluice/` docstring only, and does not reach here).
- **Titles and companies** follow `test_vault.py`'s abstract-placeholder convention (`X`, `Y`,
  `Acme`). A word-order-drift pair is constructed, not hardcoded from a real posting.
- **URLs** — an earlier draft omitted these entirely, and this plan needs url-carrying fixtures for
  the `url_proven` probe test and for the `seen.db` contents assertion, where the recorded
  `dedup_key` IS the normalized url (`core/leads.py:173-174`). Use the `.invalid` convention of the
  files being joined: `ex.invalid` in `tests/test_vault_merge_cluster.py` (38 uses) and
  `example.invalid` in `tests/conformance/test_store_contract.py` (14 uses), both zero `.example`.
  (`.example` dominates repo-wide at ~103 uses, but not in these files — match the neighbours.)

Unit, against a real `Vault` on `tmp_path`:

- **The acceptance property**, which is the falsifier run above, as a test: build a vault, upsert two
  drifting scrapes of one job, `merge_cluster` them, re-upsert the loser's `Lead`, assert the outcome
  is `merged_away` and that `read_leads()` still returns exactly one note.
- **`merged_away` writes nothing** — the `refuse` arm's own property
  (`test_upsert_refuses_and_writes_nothing`), applied to the new arm: no note, and **no `.stfolder`**.
  It must NOT also assert on `leads_dir`: `_merged/` lives INSIDE `leads_dir` (`vault.py:718`), so
  `leads_dir` exists in every scenario that can reach the probe, and asserting its absence is
  unsatisfiable rather than strict.

  **The fixture must hand-seed the archive, not build it through `upsert` + `merge_cluster`.**
  Measured: built the way every other recipe here is built, `.stfolder` already exists too, so the
  whole-tree snapshot is identical **with and without** the branch-placement mutant — the test is
  inert for the very property it exists to pin. Only a `_seed_note`-style archive, written into a
  vault where `upsert` has never run, leaves the tree clean enough for the assertion to bite. This is
  the one place in the plan where the realistic public-API fixture is the WRONG choice, and the
  reason is worth keeping: the mutant moves the branch a few lines, and every side effect it would
  newly cause has already been caused by the setup.
- A loser archived under its **location-suffixed** name is found. **The fixture must leave candidate
  1 ALSO absent** — otherwise the "probe only the candidate the walk stopped at" mutant stays green,
  because the walk itself stops at candidate 2, which is exactly what that mutant probes. Recipe:
  merge away BOTH the candidate-1 and candidate-2 notes into a third survivor.
- A loser archived under a **numeric-suffix** name is found. The recipe "resurrect, then merge away
  again" is **unreachable post-fix** — the resurrect step now returns `merged_away` and writes no
  note, so nothing exists to archive second. Working recipe, all through the public API: merge A
  away; let a proven-DIFFERENT B take the same name; merge B away. **Then re-upsert B, not A** — A
  sits at `_merged/<base>.md` and an exact-name probe already catches it, so only B's re-upsert
  exercises the suffix path at all. Getting this wrong makes the test green against the
  exact-name-only mutant.
- **The over-match test**: two genuinely different jobs whose names share a prefix
  (`Acme - Widget Engineer`, `Acme - Widget Engineer II`), the second merged away, the first
  re-scraped — assert it is `created`. This is the anchored-match witness, and its verdict is
  UNKNOWN, so no other row covers it.
- **An unreadable archived entry** does not silently stop suppressing: the `OSError` reaches the
  sink as `skipped`, and the lead stays out of `seen.db` for a retry.
- **A hole in the numeric sequence does not hide an archive behind it**: build three archives at one
  stem, restore `<stem>.1.md` out of `_merged/`, assert the lead behind `<stem>.2.md` is still
  suppressed. This is the case the prefix scan exists for.
- A **0-byte or unparseable** entry in `_merged/` is skipped, not treated as a hit.
- An archived note **proven DIFFERENT** does not suppress: a genuinely new job at a merged-away name
  is still created.
- An archived note whose verdict is **UNKNOWN** suppresses, and is **NOT** recorded in `seen.db`.
- **Capped-title probe tests**, for the shared verdict helper: one where `title_lost` should fire in
  the probe, one where `url_proven` overrides it. Without these, every probe scenario has `capped`
  false, so `title_lost` and `url_proven` are dormant throughout and a probe that re-implements the
  verdict without them is green.

  **The `title_lost` case needs a CONTROL ARM.** Asserting only that a capped-title mismatch yields
  `created` is byte-identical to a probe that never matched anything at all (measured), so it passes
  under a fully inert probe. Pair it with the same fixture carrying a MATCHING `role`, asserting
  `merged_away`: the two together show the probe ran AND that `title_lost` is what decided it. The
  `url_proven` test is sound as stated and needs no control.
- The **active walk is unchanged**: update, merge and refuse behave exactly as before when `_merged/`
  is absent, and when `_merged/` holds a proven-different note.

Sink and CLI:

- A **SAME**-arm `merged_away` IS recorded in `seen.db`; an **UNKNOWN**-arm one is NOT (assert on the
  store's contents, not on the count).
- The CLI report prints the lines when non-zero and omits them when zero.

Conformance:

- The non-resurrection property, in the location-split shape, with scope assertions (above).

### Mutation witnesses

Each mutant is made by MOVING or DELETING, never by adding. Run
`python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first. Each new
test is also run **by node id alone**, to confirm no pre-existing test in the same file is what
catches the mutant.

| mutation | expected witness |
| --- | --- |
| delete the `_archived_match` branch entirely | acceptance (+ others; see below) |
| probe only the candidate the walk stopped at | location-suffix (with cand-1-absent fixture) |
| **match `<name>.md` exactly, no suffix handling at all** | the numeric-suffix test |
| sequential suffix walk instead of the anchored scan | the hole test |
| **drop the anchor — bare `startswith` instead of exact-or-numeric-suffix** | the over-match test |
| treat `UNKNOWN` as "keep probing" | the UNKNOWN test |
| treat `DIFFERENT` as a hit | the proven-different test |
| treat an unparseable entry as UNKNOWN rather than skipping | the 0-byte test |
| `except OSError: continue` on an unreadable entry | the unreadable-entry test |
| **move the `upsert` branch below `os.makedirs`/`ensure_stfolder`** | the writes-nothing test (hand-seeded fixture only) |
| drop the SAME arm from the sink allowlist | the seen.db test |
| record the UNKNOWN arm in `seen.db` | the UNKNOWN-not-recorded test |

Two of these rows are new because an earlier draft's table could not police them. **The
exact-name-only row is the simplest slip available** and had no row at all — the sequential-walk row
is strictly weaker, and "no row is inert" cannot police a row that does not exist. **The anchor row
needs its own test** because the over-match's verdict is `UNKNOWN`, so the "treat `DIFFERENT` as a
hit" row does not reach it: the over-match test is two genuinely different jobs whose names share a
prefix (`Acme - Widget Engineer` and `Acme - Widget Engineer II`), the second merged away, the first
re-scraped — it must be `created`, never suppressed.

**Extraction witnesses — these are the rows an earlier draft lacked entirely.** Every row above
mutates the new branch's control flow; none touches the verdict, which is the only edit that reaches
the delicate function's existing behaviour. Each must redden one **walk-side** and one **probe-side**
test in the same run:

| mutation in the shared helper | walk-side witness | probe-side witness |
| --- | --- | --- |
| delete `capped and` from `title_lost` | note at `X - Y` with mismatched `role`, no location, no url: `merged` → `refused` | same note ARCHIVED: `merged_away` → `created` |
| delete `and not title_lost` | existing capped-title tests (2 each arm) | capped-title probe test |
| delete `not url_proven` | existing url-stable drift test | url-override probe test |

The `capped and` row is the load-bearing one: mutation-tested on the current tree it leaves the
**full suite green**, and `capped` is the term this design threads into the helper by hand.

## Definition of done

1. The falsifier above passes as a committed test; it fails on `10b0cdd`.
2. `_resolve_path`'s update / merge / refuse arms are behaviourally unchanged — established by the
   extraction witnesses, NOT by "full suite green", which is provably non-falsifying here.
3. The verdict logic exists in exactly ONE place — a `vault.py`-private helper, not `core/leads.py`.
4. **Both** `merged_away` and `merged_away_unproven` are plumbed through `upsert`, the CLI report,
   and the outcome-vocabulary conformance test; only `merged_away` joins the sink allowlist. The
   vocabulary test is green against an under-widening that adds just one of the two, so membership of
   BOTH is pinned inside the conformance non-resurrection test — which actually produces a
   `merged_away` — rather than left to "widen it deliberately".
5. The contract is stated in the conformance suite, BOTH `upsert`'s and `merge_cluster`'s docstrings
   in `core/protocols.py`, and `docs/ARCHITECTURE.md`.
6. Every mutation in the tables above reddens **at least** its own witness, and **no row is inert**.
   ("Exactly its own witness" is unsatisfiable: deleting the branch reddens four of the new tests,
   and treating DIFFERENT as a hit breaks the numeric-suffix fixture's own construction. Demanding
   exactness invites narrowing tests until it holds.)
7. Every live site stating #81's rationale as currently-true is reconciled. **Derive the list, do not
   copy one** — `grep -rn "#81" sluice tests docs --include='*.py' --include='*.md' | grep -v
   superpowers/specs` returns **12** as of this commit, across `core/paths.py` (×3), `core/seendb.py`
   (×2), `core/app.py`, `docs/ARCHITECTURE.md`, `tests/test_path_refusal.py` (×2), `tests/test_paths.py`,
   and `tests/test_seendb.py` (×2). A review of this spec hand-listed five of them and missed seven,
   which is the enumeration failure verbatim — so the DoD is the grep, not a list.

   **What changes is the SCOPE of the claim, not its truth.** Those refusals stay correct and must
   not be softened: an empty dedup set is still bad, and after this fix a merged-away lead can still
   be re-created when its re-scrape drifts past the name candidates (see The residual). What goes
   stale is the unqualified form — "an empty dedup set re-creates EVERY lead a human merged away" —
   and `tests/test_path_refusal.py:9`, which says `_resolve_path` "never consults
   `leads_dir/_merged/` (#81, true today and out of scope)" in so many words, becomes flatly false.
   Nothing goes red for any of them: the #9/PR #76 failure mode, which an earlier draft of this spec
   cited for one site and missed for eleven.
8. `ruff check sluice tests scripts` clean; full suite green.

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

Three, all narrower than the defect being closed and all failing toward the safe direction:

- **Name drift past the candidate set.** As above: a re-scrape whose title changes enough to produce
  a different `_note_name` than the archived loser's is not found, and is created. This is the same
  name-sensitivity the active walk already has, and the same one `seen.db` covers in the normal case.
- **The UNKNOWN arm re-reports every run.** Deliberate, and the accepted cost of not recording it:
  a weak-evidence suppression stays visible until a human resolves it, rather than becoming a silent
  permanent drop. The archive probe also keeps running for that lead on every future run. If this
  proves noisy in practice the fix is to surface it in `leads dedupe report`, not to record it.
- **A second-order #9 gap this PR does not close.** After a merge, if the board the SURVIVOR came
  from delists the job while the loser's board still lists it, the survivor's `last_seen` stops
  advancing and #9's staleness sweep can expire a posting that is still live. Suppression makes this
  no worse than it is today (the loser's re-scrape already bumped nothing, since it was filtered by
  `seen.db`), but survivor-routing would have closed it. Recorded here so it is not rediscovered as
  a new bug.
