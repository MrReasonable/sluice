# Vault subfolders — a recursive lead scan, an opt-in Active/Archive layout, `leads reconcile` (#1)

- **Date**: 2026-08-01
- **Origin**: issue #1. The issue proposes a design but settles neither the folder set, what moves a
  note between folders, nor migration, and does not raise the exclusion rule or the lead predicate at
  all. Those are decided here (see *Decisions*).
- **Status**: designed, approved. Implementation follows in the plan.
- **Ships as**: two PRs, in order. PR A is the recursive scan; PR B is the layout and the command.
  Both `Refs #1`; PR B closes it. **The order is load-bearing**: shipping any part of the layout
  before the scan is recursive moves notes out of the scanned directory and re-ingests the entire
  archive as new leads. One implementation plan per PR — PR B's is written after PR A merges, so it
  plans against the shipped `_locate`/`_scan_dirs` rather than against this document's sketch of them.

## Problem

Every lead note lives in one flat directory and the scan is non-recursive: `read_leads`
(`core/vault.py:457`) and `normalize_statuses` (`:712`) both `os.listdir` a single `leads_dir`, and
`_resolve_path` (`:422`) checks exactly one path per name candidate.

Two consequences, both from the issue and both real:

1. **The directory is unusable at scale.** A long-running store accumulates thousands of notes,
   overwhelmingly dismissed. The active set is a tiny fraction with no way to get the rest out of
   the way.
2. **Nothing else can live alongside the leads.** Any other note type a user wants nearby (interview
   prep, research) has nowhere to go that sluice will still read, so it ends up dumped in with the
   leads.

### The trap this work must not spring

`_merged/` — where `merge_cluster` archives the losers of a human-vetted duplicate merge (#23), and
which #81's non-resurrection guarantee depends on staying out of the active view — **is invisible to
`read_leads` only incidentally.** `os.listdir` is non-recursive and the entry `_merged` is a
directory, so it fails the `name.endswith(".md")` check. Nothing excludes it by name.

The moment the scan goes recursive, every archived loser reappears in the active view. That undoes
#81 outright and re-exposes its harm: a lead a human merged away is re-created, triaged, shortlisted
and can be applied to — a second application to one job under the user's name, which cannot be
unsent.

Measured, both arms, on a synthetic 5500-note vault (2000 `Active/`, 3000 `Archive/`, 500
`_merged/`):

| walk | notes found | from `_merged/` |
| --- | --- | --- |
| with the `dirnames[:]` prune | 5000 | **0** |
| without it | 5500 | **500** |

No test on `main` can catch this, because the walk cannot reach an archived note to begin with. The
exclusion must become explicit and carry its own witness.

## Decisions

Four decisions, each put to the user, each load-bearing below. The issue leaves the first three open;
the fourth it does not raise.

1. **Sluice does not own the layout, it offers one.** The recursive scan is unconditional. The
   Active/Archive layout is config-gated and OFF by default, so an unconfigured install behaves
   byte-identically to today. *Migration is not a separate mechanism*: a flat vault is simply maximal
   drift, and `leads reconcile` repairs it. One code path, not two.
2. **Only `leads reconcile` moves a note.** No pipeline command relocates anything. Folder-vs-status
   drift between reconcile runs is normal and harmless, because the scan is recursive.
3. **The Archive set is derived, not hand-listed**: `dismiss` plus every terminal, read from
   `core/status.py` — `dismiss`, `rejected`, `accepted`, `withdrawn`. A terminal added to
   `status.py` later archives automatically rather than silently staying Active.
4. **The scan skips only directories sluice itself creates** — today just `_merged`. Everything else
   under the leads dir is the user's and is scanned.

Decision 4 is the one whose alternative is a trap in the opposite direction. A `_`-prefix rule
(matching the implicit convention that hides `_inbox/` in the Experience Library) would silently
swallow a user folder named `_archive` or `_old`. That is the *same harm class as the trap*, pointed
the other way: invisible notes are invisible to the write path too, so every one of them is
re-created as a duplicate on the next scrape. An explicit set of store-owned directories cannot
over-exclude, and it is checkable — see *The scope guard*.

## Design

### One field doing two jobs

`self.leads_dir` currently conflates two concepts. Separating them is the whole design.

| Concept | Today | After |
| --- | --- | --- |
| **Scan set** — every directory a lead may be *read* from | `leads_dir`, flat | `leads_dir` recursively, minus `_PRIVATE_SUBDIRS` |
| **Write folder** — the *one* directory a new note is created in | `leads_dir` | `leads_dir`, or `leads_dir/Active` when configured |

### The identity rule

> **A lead's identity is its note NAME. Its folder is not part of its identity.**

`_resolve_path` searches the scan set for `<candidate>.md` and reconciles against whatever it finds,
wherever it sits. Three things follow:

- `leads reconcile` moves a note without changing what it is — no re-ingest, no duplicate.
- #81's documented recovery (hand-move a note out of `_merged/`) keeps working unchanged. The
  restored note is found by name in whatever folder it lands in, the next scrape reconciles against
  it as an ordinary note, and the `merged_away_unproven` re-reporting stops.
- Migration is not a special case, per decision 1.

The rule holds end to end because **nothing persists a lead note path across runs.** Verified by
enumeration of every candidate (read, not executed): `note.ref` is consumed within a single command
immediately after `read_leads`; `tailored_cv` points at a CV artefact, not at a note; the #49
dead-letter store keys on `lead` = the *slug*, and the slug is the filename stem, which a move
preserves.

### What makes a `.md` file a lead note

Motivation 2 is the reason this matters. Once the walk is recursive, a user folder under the leads
dir — `Interview Prep/`, `Research/` — is in the scan set, and a rule of "every `.md` file is a
lead" would return its notes as phantom leads. That would make the second motivation *worse*, not
better: the user gains a place to put other notes and sluice immediately starts triaging them.

So `read_leads` and `normalize_statuses` skip a file carrying **neither `company` nor `role`** in
frontmatter. This is not a new predicate — `_archived_match` already uses exactly it, and it is
right in both places for the **same** reason, not a mirrored one:

> In both, skipping too eagerly loses a note that really exists.

- In `_archived_match`, a skipped archive entry stops suppressing, so a lead a human merged away is
  resurrected — #81's harm.
- In `read_leads`/`_locate`, a skipped file drops a lead from the read path and from the write
  path's lookup, so the next scrape mints a duplicate.

Hence `neither`, not `either`: a hand edit that blanks `role` (the #16 threat model — a human
editing in Obsidian) must leave the note a lead, so one surviving field is enough. A user's
interview-prep note carries neither and is skipped.

The direction of failure is therefore "keep an ambiguous file as a lead", which costs a junk row in
a report, against "drop a real lead", which costs a duplicate note. The cheap error is the one taken.

This is a behaviour change for **flat** vaults too — a stray `.md` in the leads dir stops being
returned as a `LeadNote` — so it is deliberate and gets its own test rather than riding in on the
recursion.

`_locate` deliberately does **not** apply the predicate: identity is the note NAME (above), and a
name-keyed lookup is what lets a hand-blanked note still be found and updated rather than duplicated.
The residual — a non-lead note squatting a lead's exact candidate name is reconciled against as
though it were a lead — **exists today** in the flat directory and is neither introduced nor widened
here.

### The one new failure mode: ambiguous identity

A candidate name resolving to **two or more** paths means two notes claim one identity. The store
cannot know which is the lead; bumping `last_seen` on the wrong one leaves the other to rot
silently. This returns the existing **`refuse`** outcome — nothing written, both paths logged, the
lead kept out of `seen.db` so it re-reports every run until a human merges or renames.

Nothing sluice does produces it: new notes are created in exactly one folder, and reconcile refuses
a colliding move (PR B). It arrives only by hand — a copied note, or a part-way manual reorganisation
— but *reachable is enough to need a verdict rather than a guess*.

On the read path the same collision surfaces as two `LeadNote`s sharing a slug. That already
degrades safely: `apply/select.select_one` refuses `len(matches) > 1` with an `ambiguous:` reason.
No new work.

### Cost: the scan set is cached per `Vault` instance

Measured on the same 5500-note vault:

| approach | cost |
| --- | --- |
| re-derive the directory list per lead | 2.82 ms/call → **1.41 s** per 500-lead run |
| full note walk, per lead | 4.22 ms/call |
| **cached directory list**, 500 leads × 3 dirs | **3.66 ms total** |

So the list is computed once and cached. The staleness window is a human creating a subfolder
mid-run; the cost is one duplicate note — the recoverable direction, and the same posture the
existing create-race takes.

`os.walk` does not follow symlinks (`followlinks=False` by default); measured, a self-symlink inside
the leads dir terminates the walk cleanly rather than looping.

## PR A — the recursive scan (`vault.py` only)

No config, no new command, no folders created.

**1. The exclusion constant.**

```python
_PRIVATE_SUBDIRS = frozenset({_MERGED_SUBDIR})
```

Pruned **at the top level only** (`if dirpath == self.leads_dir: dirnames[:] = [...]`). That is not
laziness — it is what keeps the exclusion and #81's probe in agreement *by construction*: the pruned
path is `leads_dir/_merged` and `_archived_match`'s `merged_dir` is `leads_dir/_merged`. One
constant, two consumers, no way to drift. A guard asserts the two resolve to the same string, so an
edit to either is caught by the other.

**2. `_scan_dirs()`** — cached; returns `[leads_dir]` at minimum even when the directory does not
exist yet, so `upsert`'s first-run `makedirs` needs no special case. `_locate(name)` is then just
`[p for d in self._scan_dirs() if os.path.exists(p := os.path.join(d, f"{name}.md"))]` — O(folders)
per candidate against a cached list, which is what the measurement above pays for.

**3. `read_leads` and `normalize_statuses` walk it**, sorted by full path, and apply the
neither-`company`-nor-`role` skip from *What makes a `.md` file a lead note*. Sorting by full path is
byte-identical to today's `sorted(os.listdir(...))` for a flat vault, so existing ordering
assumptions survive. `normalize_statuses` keeps excluding `_merged/` — today's exclusion is
incidental and making it explicit is a no-op in behaviour; it must not write into archived losers.

**4. `_resolve_path` resolves a candidate across the scan set.**

```python
for name in names:
    found = self._locate(name)              # candidate -> 0, 1, or many paths
    if len(found) > 1:
        return None, "refuse"               # ambiguous identity
    if not found:
        archived = self._archived_match(names, lead, capped)
        if archived:
            return None, archived           # #81, unchanged
        return os.path.join(self.leads_dir, f"{name}.md"), "create"
    ...reconcile against found[0], unchanged...
```

`_archived_match`'s call site, its arguments and both `merged_away` outcomes are untouched. What
changes is only *when* the walk concludes "no active note exists" — from one `os.path.exists` to a
search across the scan set. That is the direction that matters: the old check could miss a note in a
subfolder and resurrect it; the new one cannot.

**5. The scope guard.** A test enumerates every `os.makedirs` target under `leads_dir` in `vault.py`
and asserts each is either the leads dir itself (which is the write folder until PR B introduces
one) or a member of `_PRIVATE_SUBDIRS`. It asserts on the
**scope** — that it found the `makedirs` calls at all — because for a negative guard, finding
nothing is the success case and an empty sweep would otherwise pass vacuously.

## PR B — the layout and `leads reconcile`

**The config knob**, one root-`Config` field. Root, not a sub-app block, for the same reason
`location_noise_words` is: `Sluice.store()` resolves the store from `self.config`, so a key the
store must honour cannot live in a sub-app block.

```yaml
lead_layout: ""      # "" = flat, exactly as today.  "active_archive" = Active/ + Archive/
```

`""` is the default, so an unconfigured install is byte-identical to today. An **unknown** value
raises at construction and lists the valid names, per `_select_backend`'s precedent — a typo'd
`lead_layout: activearchive` must not degrade silently to flat.

It goes in `sluice.yaml.example` **commented out**, following `lead_ttl_days` and `locations` rather
than the file's active illustrative values: the discriminator there is stated in the file itself —
this file is copied, and an active value hands every copier a judgement they never made. A layout is
exactly that. It is deliberately **not** added to `sluice init`'s question catalogue: a fresh install
has no notes to organise, so the question is premature, and the example file is where the
`location_noise_words` precedent puts a knob of this kind.

**The mapping, derived.** `core/status.py` gains a public `is_terminal(status)` predicate beside its
existing `is_application_owned` / `is_canonical` / `can_apply`. The vault's rule is then:

```python
archived = s == "dismiss" or _status.is_terminal(s)   # dismiss, rejected, accepted, withdrawn
```

A **non-canonical status is never moved**: never-regress passes an unrecognized status through
untouched, so reconcile leaves it exactly where it is and reports it in an `unknown` bucket,
mirroring `normalize_statuses`' existing one.

**`sluice leads reconcile`** reports by default; `--apply` moves. No `--dry-run` — the default *is*
the dry run, and a flag that does nothing is drift. This matches `leads dedupe` and `leads expire`:
a `leads` pass writes over a set the *tool* computed, so a mistyped one prints a list rather than
moving a hundred notes.

**The move primitive** is the one `merge_cluster` already ships and that already survived two
CodeRabbit rounds: `O_EXCL`-reserve the destination, then `os.replace(src, dst)`. It moves whatever
`src` names at that instant, so a concurrent Obsidian save is carried rather than lost, and it
overwrites only our own zero-byte reservation. The single difference is collision policy —
`merge_cluster` takes the next numeric suffix; **reconcile refuses and reports**, because a suffix
changes the filename, which is the slug, which is the identity.

Reconcile does **not** refuse a note holding a `pending_cv` sign-off hold. `leads expire` refuses
that because dismissing silently discards a composed CV no human signed off; a move discards nothing
and, per the enumeration above, breaks no pointer.

**Also landing in PR B** — the two items #8 deferred to this issue: `cmd_init`'s filesystem path join
for the two report arms that receive no handle, and `triage/prompt.py`'s `load_criteria` /
`build_system_prompt` now being test-only surface.

## Error handling

`os.walk`'s default `onerror=None` **silently drops an unreadable directory**. Measured: with
`Archive/` at mode `000`, a 6-note vault yields 3 notes, no error, no log. That is mass invisibility
— and therefore mass re-ingest — arriving through a permissions bit rather than a code path. The
store contract already forbids reading an unreadable thing as empty ("the relocated case and the
corrupt case cause the identical harm, so both are loud"); a directory is that rule at folder scale.

| Situation | Behaviour |
| --- | --- |
| Unreadable directory in the scan set | **Raise** (`os.walk(..., onerror=)`). Aborting the command beats silently triaging part of the vault. |
| Candidate name resolves to 2+ paths | `refuse` — nothing written, both paths logged, lead stays out of `seen.db` |
| Reconcile move collides at destination | Refuse *that note*, report it, continue the sweep (per-note isolation, like `merge_cluster`'s per-loser `OSError`) |
| Reconcile move raises `OSError` | Log, count under `skipped`, continue |
| Non-canonical status | Never moved; reported under `unknown` |
| Unreadable *file* | Unchanged: `except OSError: continue` — one lead, not a folder |

## Testing

Eight mutation witnesses. Every one is a **delete or a move, never an add** — a check added beside
the original is an equivalent mutant and stays green.

| Mutation | Test that must redden |
| --- | --- |
| Delete the `dirnames[:]` prune | `test_archived_loser_is_invisible_to_the_recursive_scan` (measured: 500 losers surface) |
| Revert `_locate` to a single-folder `os.path.exists` | a note in `Archive/` re-scrapes as `created` instead of `updated` |
| Delete the `len(found) > 1` guard | the ambiguity test |
| Change the lead predicate from `neither` to `either` | a note with `company` but no `role` disappears from `read_leads` |
| Delete the lead predicate | a note in a user's `Interview Prep/` folder is returned as a lead |
| Restore `os.walk`'s default `onerror` | the unreadable-subdirectory test |
| Hand-list the archived statuses | the `status.CANONICAL` enumeration guard |
| Drop reconcile's collision refusal | the collision test |

The unreadable-subdirectory test must skip when euid is 0 — `chmod 000` does not bind root, so the
test would otherwise pass vacuously in a container that runs as root.

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once before
any mutation run: a size-preserving edit restored within the same second otherwise executes stale
bytecode and reads as "this test is inert".

**No new Store-contract property.** Folders are a vault mechanism; a store keyed on synthetic ids has
none. Per the #48 ruling — if only *this* store's mechanism provides it, it is implementation detail,
not a conformance guarantee. What the conformance suite already asserts
(`test_merged_away_lead_is_never_recreated`) must keep passing, and PR B adds a vault-level test that
it also holds with `lead_layout` enabled.

## Residuals, accepted

1. **Scan-set cache staleness.** A human creating a subfolder mid-run is not seen until the next
   `Vault`. Cost: one duplicate note — visible, mergeable, the recoverable direction.
2. **`_merged/` is pruned at the top level only.** A user who moves `_merged/` into a subfolder
   resurfaces its contents. That same move also breaks `_archived_match`, which reads exactly
   `leads_dir/_merged` — so the two stay consistent: the archive is simply gone and its notes are
   active again, which is the documented recovery path applied wholesale. Visible and reversible.
3. **Ambiguity degrades ingest for one lead** until a human renames or merges. Logged, and the lead
   re-reports every run rather than going quiet.
4. **A single bad permission bit aborts a command.** Deliberate, per *Error handling*.
5. **Reconcile is not atomic across notes.** It moves note-by-note with per-note isolation, so an
   interrupted run leaves a partially reconciled vault. That is not a defect: the layout is a
   derived view, drift is the normal state between runs, and re-running converges.

## Out of scope

- Subfolders for the Experience Library. `read_experience_entries` (`:529`) has the identical
  incidental-invisibility shape around `_inbox/`, but it is a different directory serving a
  different sub-app, and widening this change to it would put an unrelated seam in the same review.
- Any user-defined status→folder mapping. `lead_layout` selects one named layout; an arbitrary map
  keyed on status would need to answer what happens to an unknown status, which never-regress
  already answers ("leave it alone") in a way a config map would invite users to override.
- Automatic reconcile on any pipeline command (decision 2).
