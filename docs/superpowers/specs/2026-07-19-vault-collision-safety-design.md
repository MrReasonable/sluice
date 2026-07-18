# Vault collision safety — a note must never silently absorb a different job

- **Date**: 2026-07-19
- **Status**: **READY TO PLAN.** The block is cleared (see "What unblocked this").
- **Origin**: issue #5, rescoped 2026-07-16 (the read-path half moved to #23). This is the **write path** only.
- **Supersedes**: `docs/superpowers/specs/2026-07-16-lead-identity-write-path-design.md` (parked on the
  stale `fix/lead-identity-write-path` branch, written before #24/#25 merged). That draft was plan-reviewed
  three times (21 → 16 → 8 findings; round 3: 0 Critical / 3 High); all findings are folded here. This
  document reuses its reviewed reasoning and corrects it to what actually shipped.

## What unblocked this

The parked draft was "BLOCKED on #6 — fix #6 first." That decision bundled two worries, and both are now
resolved:

1. **The comparison mechanism did not exist.** The draft keyed splits on a bare location *equality*, which
   fired **0 of 33** real same-city re-post pairs (`London` ≠ `London EC4Y`), so every re-post would have
   split — a regression of today's one-note-per-re-post behaviour. **#25 (MERGED) shipped
   `_compare_locations(a, b, noise=frozenset()) -> SAME|DIFFERENT|UNKNOWN`** in `core/leads.py`, keyed on
   token **overlap**, whose docstring states outright *"DIFFERENT is the only verdict #5 acts on."* Verified
   against real inputs: `London` vs `London, UK`/`London EC4Y`/`London ∙ Choose area` → SAME (no duplicate);
   `London` vs `Manchester` → DIFFERENT (the two-cities case #5 fixes); either side empty → UNKNOWN (merge,
   today's behaviour). The equality regression is gone.
2. **The location field could be dirty (#6's domain).** #6 is **not a blocker**: #5 splits *only* on a
   location `DIFFERENT`, so extraction dirt can only ever push toward a false **split** (visible via two
   notes, recoverable via #23's dedupe) — never a false **merge** (the silent, fatal loss #5 removes) and
   never a regression of today's behaviour. #6 affects the false-split *rate*, not safety. (Memory records #6
   as misdiagnosed with no reproducer, so "fix #6 first" would stall #5 indefinitely against an
   unreproducible bug.)

**#24 (MERGED) closed the truncation problem** the parked draft filed as its own issue: `_path_for` now
byte-clamps the note name to the filesystem's `NAME_MAX` after the 120-char cap, and `VaultSink.write`
isolates a write `OSError` as a counted `skipped` kept out of `seen.db`. This design composes with both.

## Problem

`Vault._path_for` derives a note's path from company+title and `upsert` decides create-vs-update on that path
alone, so **two genuinely different jobs sharing a company and title collapse into one note** — the same
title at the same firm in two cities. Verified against a real `Vault`: upserting two such postings returns
`created` then `updated`; the surviving note keeps the *first* posting's `location`/`url`, and the second is
never stored, is reported `updated`, and nothing records it existed. This is silent, unrecoverable data loss.
It is the highest-severity failure in the store: a false split is visible and fixable; a false merge destroys
the lead with no trace. (The pre-existing 120-char truncation is a narrower instance of the same collision.)

## Key finding — never-clobber is what makes the loss silent

On a path collision `upsert` bumps only `last_seen`, protecting the *existing* note. That is never-clobber
working exactly as designed. Nothing protects the *incoming* lead. **So the fix cannot be to overwrite. It
must be to not collide** — a path that distinguishes distinct opportunities. Every decision below follows
from that.

## The governing rule

A false merge is silent and unrecoverable; a false split is visible (two notes) and recoverable (#23's
`dedupe`). The repo already reasons this way (the CV fabrication gate; empty-config-abstains;
`apply/select.py` refusing to guess between duplicate shortlist records).

**Split only on positive evidence of difference. Never split on the absence of evidence.**

Splitting on *absence* is unbounded — nothing about an unidentifiable lead reproduces its name next run, so
every scrape would mint another note. Absence of evidence therefore **merges** (today's behaviour, bounded).
The improvement is confined to the case where we can *prove* two postings differ.

## Design

### 1. `same_opportunity(note_fm, lead, noise) -> SAME|DIFFERENT|UNKNOWN` — new, pure, `core/leads.py`

Lives beside `slug_matches` (which already takes a note-shaped mapping) — pure and shared, so no second store
re-derives the `url:""` trap for itself. It collapses the parked draft's four rules into two lines, now that
#25's `_compare_locations` exists:

```
note_url = note_fm.get("url", "")
if lead.url and note_url and _norm_url(lead.url) == _norm_url(note_url):
    return SAME                                                     # proof: both URLs non-empty and equal
return _compare_locations(note_fm.get("location", ""), lead.location, noise)
```

- **Only a matching URL is *proof*.** Everything else is #25's inference, built so inference only ever runs
  in the direction that is safe to be wrong in (a wrong SAME/UNKNOWN merges — today's behaviour; only a wrong
  DIFFERENT splits, and it is visible).
- **Both URLs must be non-empty.** `google.py` leads carry `url:""`, and `_norm_url("") == _norm_url("")`, so
  a rule "urls match → same" would merge every Google lead sharing company+title — reintroducing the exact
  loss this removes. This is a trap, not a detail; it gets a dedicated test with a positive control.
- **A noisy location cannot cause loss.** We split only on a location *difference*; #6's dirt yields spurious
  splits (visible, recoverable), never a false merge.

### 2. `Vault._resolve_path(lead) -> (path, action)` — new

`_path_for` is **unchanged** and remains **candidate 1** — the whole reason this is a zero-migration change:
every existing note keeps matching, nothing moves. A candidate is **nameable** only when the field it is
built from is non-empty:

1. `Company - Title.md` — always nameable; the first-seen job keeps the clean name.
2. `Company - Title - {Location}.md` — nameable only when `lead.location` is non-empty.

There is deliberately **no URL-hashed candidate** — a volatile URL (`_norm_url` keeps the query string) would
mint a fresh note every run, the unbounded growth this design's governing rule forbids.

The walk visits candidates in order. **Every verdict terminates in place except `DIFFERENT`:**

| At a candidate | Action | Terminates? |
| --- | --- | --- |
| free (no file) | `CREATE` here | yes |
| existing, `SAME` | `UPDATE` here — bump `last_seen` | yes |
| existing, `UNKNOWN` | `MERGE` here — bump `last_seen`, count it | **yes — even if a later candidate is free** |
| existing, `DIFFERENT` | advance to the next nameable candidate | no |
| ran out (every existing candidate was `DIFFERENT`) | `REFUSE` — write nothing, log, count; `path = None` | yes |

**Advancing only on `DIFFERENT` is what enforces the governing rule structurally** rather than by assertion.
**Terminating on `UNKNOWN` (not "on exhaustion")** is the round-2 fix: the earlier draft recorded UNKNOWN and
*continued*, so an UNKNOWN at candidate 1 with a *free* candidate 2 created a second note and orphaned the
first — splitting on absence of evidence. A note lacking both `url` and `location`, met by a URL-less Google
lead carrying a location, must MERGE at candidate 1, never CREATE at candidate 2.

An empty-location lead can never yield `DIFFERENT` (`_compare_locations` returns UNKNOWN when either side is
empty), so it always terminates at candidate 1 and never reaches REFUSE.

**REFUSE is reachable only pathologically:** two locations that sanitize to the *same* filename suffix yet
carry *disjoint* tokens under `_norm_location`, or a note whose frontmatter contradicts its own filename.
Refusing loses the lead, so it must be loud, counted, and — critically — **retried** (§4).

### 3. Truncation — #24 does the byte-safety; this bounds the suffix

Candidate 1 is `_path_for`, already byte-capped by #24 (`[:120]` char-cap, then
`_clamp_bytes(name, _name_max() - len(b".md"))`). Candidate 2 must not let the suffix be eaten by the char
cap, and must not let the stem budget go negative:

```
suffix = sanitize(location)[:_SUFFIX_MAX]                       # _SUFFIX_MAX = 40, a literal (see Config-first)
stem   = f"{company} - {title}"
name   = stem[:120 - len(_SEP) - len(suffix)] + _SEP + suffix   # stem budget >= 77, never negative
# then #24's byte-clamp for filesystem safety:
name   = _clamp_bytes(name, self._name_max() - len(b".md"))
```

Bounding the suffix to `_SUFFIX_MAX` **before** the arithmetic is what guarantees the stem budget can never go
negative (a negative index silently keeps "all but the last N chars", quietly breaking identity). Sanitizing
matches `_path_for` (`/` and `:` → `-`). The parked draft's entire "ENAMETOOLONG escapes `sink.write`, filed
as its own issue" section is **closed by #24**: its byte-clamp plus the sink's `OSError → skipped` already
handle an over-long name at lead granularity. `_resolve_path` returns `path = None` for REFUSE — no correct
path exists, and the REFUSE branch never dereferences it.

### 4. `Vault.upsert` — four honest outcomes, reconciled with #24's `skipped`

```
path, action = self._resolve_path(lead)
CREATE -> _write(path, self._render_new(lead)); return "created"
UPDATE -> self._bump_last_seen(path, ...);      return "updated"    # unchanged — never-clobber holds
MERGE  -> self._bump_last_seen(path, ...);      return "merged"     # indiscriminable; counted
REFUSE -> _log.warning(...); write nothing;     return "refused"
```

`"updated"` now means only what it says: an existing note for a lead we *identified*. `"merged"` is the
indiscriminable case (bumped `last_seen`, but we could not prove same-or-different). #24's create-failure
unlink/partial-note guard stays on the CREATE path unchanged.

**The two call-site changes are load-bearing, not incidental** — without them the new outcomes are theatre.

**(a) `ingest/sink.py` records only outcomes that mean a note now exists.** #24 already made the sink isolate
a write `OSError` as `skipped` and keep it out of `recorded`. This design extends the guard to the *return
value* with an **allowlist**:

```
try:
    outcome = self.vault.upsert(lead)               # created | updated | merged | refused
    counts[outcome] = counts.get(outcome, 0) + 1
    if outcome in ("created", "updated", "merged"): # allowlist: a note now exists
        recorded.append(lead)                        # refused stays un-seen -> retried next run
except OSError as e:                                 # #24: a write that physically failed
    counts["skipped"] += 1
    _log.warning(...)
```

**The allowlist is the point, not a style choice.** The denylist spelling (`if outcome != "refused"`) would
record an out-of-vocabulary outcome from a future store, drop it from `seen.db` forever, and count it into no
visible bucket — a silent, permanent loss re-entered through the fix for the previous one. Stating the branch
positively over "a note now exists" makes an unknown outcome fail safe (un-seen, retried). `refused` and #24's
`skipped` are **kept distinct**: different causes (deliberate decline vs physical write failure), both
un-recorded and retried, each named for diagnostics.

**(b) `sluice/cli.py` prints the new counts.** #24 already prints `created`/`updated`/`skipped`. Extend
`_print_report` to also print `merged`/`refused` **when non-zero**, `refused` named as a refusal. **MERGE's
count is its only signal** — it does not log by construction (indistinguishable from a re-scrape at the
moment it happens), so if the count is not printed, the accepted cost below loses its visibility. Every read
uses `.get(key, 0)`; a clean run never `KeyError`s.

### 5. Config — `location_noise_words`, wired

`same_opportunity` takes `noise`, sourced from a new **root** Config field (the store is resolved from the
root Config, so a sub-app block cannot reach it):

- `Config.location_noise_words: list = field(default_factory=list)` — **defaults `[]`** (abstain: no
  subtraction). Parsed in `load_config` (`data.get("location_noise_words") or []`).
- Held on the `Vault` (constructed from root Config, like `baseline_rel`) and passed into `same_opportunity`.
- **`assert c.location_noise_words == []`** added to `test_ingest_defaults_carry_no_preference` — that guard
  file ships green on keys nobody names, so a new gate needs its own assertion in the same change.
- A **commented** line in `sluice.yaml.example` (per the `locations:` precedent), so the knob is
  discoverable and the user can, e.g., make `Remote` vs `London` merge by adding `remote` to the list.

Empty-config-abstains holds: an empty list subtracts nothing, and `_compare_locations` behaves exactly as with
no noise argument.

## Invariants

- **Never-clobber — holds, untouched.** UPDATE and MERGE bump only `last_seen`; REFUSE writes nothing; CREATE
  is a genuinely new note. #24's partial-note unlink on create-failure is unchanged.
- **Never-regress — not engaged.** `can_advance` governs only `APPLICATION_OWNED`; every note this creates is
  `status: new`. A strengthening falls out: a note that has reached `applied`/`rejected` can no longer be
  touched by a colliding *different* lead — that lead now gets its own path.
- **Empty-config-abstains — engaged, and satisfied.** `location_noise_words` defaults `[]` (no subtraction),
  guarded by the neutral-defaults assertion above.
- **CV fabrication gate — untouched.**
- **Stdlib-only** — `re`/`os` only; no new dependency (the URL-hash candidate is gone, so no `hashlib`).
- **No silent failures** — REFUSE is the design's answer to the one case it cannot serve: loud, counted, and
  retried. MERGE is counted and surfaced at the CLI.

## Config-first

The `120`-char cap, `_SUFFIX_MAX`, and the suffix separator are **identity-determining**: as config, changing
one silently re-keys every note and duplicates the vault on the next scrape — the failure this removes, sold
as a knob. They stay hardcoded. `location_noise_words` is different in kind — it tunes *comparison policy*,
not identity — so it is legitimately config, and empty-abstains keeps it safe.

## The accepted cost — stated honestly

Two genuinely different jobs sharing company, title **and** location (two teams, one city) still merge,
silently, reported as `"updated"` — matching non-empty locations resolve to `SAME → UPDATE`, which is
indistinguishable from an ordinary re-scrape, so this is the one case with **no** distinct signal (unlike
`merged`/`refused`, which are counted). **This is a choice, not a limitation.** The SAME-on-matching-location
rule fires deliberately, because it is what stops every employer re-post and cross-board listing from becoming
a duplicate note with its own drifting status. The tension, plainly: it optimises for the cross-board re-post
and pays with the two-teams-one-city merge. Your 2026-07-16 decision keeps that trade. It is written here
rather than buried, and pinned by a test, so a future change notices it rather than discovering it.

## Testing

**The identity semantic belongs in the conformance suite.** `upsert` is on the `Store` protocol, and
never-merge is the same class of property as never-clobber: a SQLite store keying on `(company, title)` would
reintroduce this loss and pass every `Vault`-only test.

- **`tests/conformance/test_store_contract.py`** — behavioural probes in `Store` terms:
  - two different jobs sharing company+title **and differing in location** produce two notes, neither lost
    (the location difference is what makes them *provably* different — stating it as "two different jobs"
    alone contradicts the merge-on-same-location rule);
  - identical strings with two non-empty URLs produce one note;
  - a re-scrape touches only `last_seen`;
  - two URL-less leads sharing company+title **but differing in location** produce two notes — an empty URL is
    never proof of sameness;
  - `upsert` never reports a write it did not perform, **and its return is always one of the four outcomes**
    (the assertion that stops an out-of-vocabulary outcome reaching the sink allowlist).
- **`tests/test_vault.py`** — filesystem specifics: the 120-char prefix collision resolving to two notes; the
  suffix surviving truncation; the over-long-location bound; candidate naming.
- **`tests/conftest.py`** — a seeded `locations` fixture beside `titles` (via `fake.city()`), and the `_lead()`
  helpers in `test_vault.py` and `test_store_contract.py` source their location from it; `test_vault.py`'s
  `location="London"` default is removed. (Scoped to **those two files** — not a grep over `tests/`, which
  trips over legitimate `target_locations`/config-gate placenames elsewhere.)
- **Idempotence across N≥3 `upsert` runs**, asserting the slug *set* (a mint-per-run scheme only manifests
  across repeated runs).
- **The note-side-empty class** — a note whose own frontmatter lacks `location`, met by a URL-less lead
  carrying one, MERGES at candidate 1 and is never orphaned.
- **REFUSE's trigger is pinned** — construct the pathological case (two distinct locations that sanitize to
  one suffix but carry disjoint `_norm_location` tokens), assert `"refused"`, that **nothing was written**,
  and that the lead is **absent from `seen.db`** so the next run retries it. (The concrete input is derived in
  the plan — the parked draft's `X/Y` vs `X:Y` recipe is stale, because `_norm_location` maps both to the same
  tokens → SAME.)
- **The accepted residual is pinned by a test** documenting the two-teams-one-city merge.
- **The empty-URL trap gets a direct `same_opportunity` test with a positive control**, naming its inputs.
- **`merged` and `refused` counts are asserted at the CLI surface** (not merely in `report.written`), as
  counted numbers rather than by matching log prose (neutrality-safe).
- **`location_noise_words` defaults `[]`** asserted in the neutral-defaults guard; a `noise`-populated
  `same_opportunity` test shows subtraction changes a verdict.
- Fixtures stay synthetic; the suite stays offline.

## Non-goals

- **Merging duplicates that already exist**, and cross-board/re-post merging generally — **#23**, which owns
  the read key, string normalization, `sluice leads dedupe`, and the fate of `existing_keys()`.
- **Fixing company/location extraction — #6.** Related but not a blocker: splits only on a difference, so
  dirt fails safe.
- **Relocating or renaming any existing note.** Zero migration is a requirement, not an aspiration.
- **`cv`'s silent first-match pick** on an ambiguous `--lead` (`core/app.py` picks `notes[0]` where
  `apply/select.py` refuses). This design increases the population of same-(company,role) notes, making that
  pre-existing behaviour more reachable. Its own follow-up issue; see Risks.

## Definition of done

Each item names the surface it is satisfied at (a DoD item that can pass without fixing anything certifies).

- The conformance probes pass for `Vault`, stated in `Store` terms so a second store inherits them —
  including that two provably-different jobs (differing location) produce two notes.
- `core/protocols.py` and `Store.upsert`'s docstring define what "already stored" means, name the four
  outcomes, and mark `"refused"` **MAY-return** (it arises from filename candidates colliding, which a DB
  store need not encounter) — pinned by the conformance vocabulary assertion, not prose alone.
- `ingest/sink.py`'s module docstring (updated by #24 for `skipped`) and the loop are updated for
  `merged`/`refused`; the allowlist is stated positively over "a note now exists".
- A `locations` fixture exists in `tests/conftest.py`; `_lead()` in `test_vault.py` and
  `test_store_contract.py` sources its location from it; `test_vault.py`'s `location="London"` default is gone.
- `_resolve_path` is idempotent across N≥3 runs, asserted on the slug set.
- A note whose own frontmatter lacks a discriminator MERGES at candidate 1 — even when a later candidate is
  free — and is never orphaned.
- **Two companies sharing one long location produce two notes, each naming its company, every name ≤120 chars
  and within `NAME_MAX` bytes** — the *identity property*, not the absence of an exception.
- The `"refused"` trigger test asserts `"refused"`, that nothing was written, and that the lead is absent from
  `seen.db` and retried next run.
- `sluice/cli.py` prints `merged` and `refused`, verified by asserting the CLI output; every count read uses
  `.get(key, 0)` and a clean run does not `KeyError`.
- `Config.location_noise_words` defaults `[]`, is asserted in `test_ingest_defaults_carry_no_preference`, has
  a commented `sluice.yaml.example` line, and threads to `same_opportunity`; a populated-noise test shows it
  changes a verdict.
- `docs/ARCHITECTURE.md`'s conformance-guarantee list is updated to name the identity rule.
- ruff clean; full suite green and offline; every load-bearing guard mutation-witnessed.

## Risks and notes

- **More `--lead <slug>` ambiguity.** Splitting creates more same-(company,role) notes. `apply` already
  refuses on ambiguity (safe); `cv` silently picks the first — pre-existing, now more reachable, worth a
  follow-up issue (not folded in here).
- **REFUSE declines to store a lead.** Loud, counted, retried every run until the cause is fixed — the
  `seen.db` exclusion is what makes it a standing bug report rather than a one-shot obituary. Reachable only
  pathologically. The alternative (a URL-hashed fallback name) reintroduces unbounded growth.
- **The two-teams-one-city merge** — see "The accepted cost". Decided, not overlooked; its honesty depends on
  the printed `merged` count.

## Process

Full heavy path, since this touches the vault write path: writing-plans → subagent/inline TDD implementation
→ whole-branch review → `/review-pr` before pushing (CodeRabbit is the scarce resource) → `path-to-green`.
Escalate rather than guess on the invariants.
