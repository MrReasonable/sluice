# Lead identity, write path — a note must never silently absorb a different job

- **Date**: 2026-07-16
- **Status**: **BLOCKED on #6. Do not plan or implement yet.** Plan-reviewed three times (21 → 16 → 8
  findings; round 3: 0 Critical / 3 High / 4 Medium / 1 Low). All findings folded. The user decided
  `inv-003` (keep merge-on-same-location) and, on `rev-r3-001`, decided to **fix #6 first** — see
  "Blocked on #6", below.
- **Origin**: issue #5, rescoped 2026-07-16 after the code contradicted the issue as filed. The
  read-path half moved to #23.

> **SUPERSEDED IN PART, 2026-07-31 — the Status line above is stale; the design below is not.**
>
> This spec was written on an unmerged branch and never landed with the code it describes. It is
> added to the tree now because it is the design record for `Vault._resolve_path`, which #81 edits,
> and #81's provenance says that walk is delicate and deserves its own review. What changed since:
>
> - **The "BLOCKED on #6" header no longer holds.** #5 SHIPPED (PR #44 `same_opportunity` + the
>   candidate walk, PR #45 monotonic `last_seen`), and the location normalizer the block was waiting
>   on landed separately as #25/PR #32 — `_norm_location` and `_compare_locations` in
>   `core/leads.py`, giving the tri-state SAME/DIFFERENT/UNKNOWN this document assumes but could not
>   name. **#6 itself is MISDIAGNOSED** (its headline example already passes; see
>   `tests/test_demash.py`), so the block resolved by a different route than planned.
> - **Later work this document predates:** PR #46 (`dedup_key` folding `_norm_location`), #16/PR #47
>   and PR #62 (the CREATE-race and the full CAS write path), PR #48 (the title-digest candidate),
>   #23/PR #66 (`leads dedupe` and `merge_cluster`, which is what #81 is about).
>
> Read it for the REASONING — why DIFFERENT is the sole verdict that advances the walk, why a split
> is keyed on proven difference rather than the absence of evidence, and the `inv-003` decision to
> keep merge-on-same-location. For current mechanics read the code and `docs/ARCHITECTURE.md`; where
> the two disagree, the code is authoritative.

## Blocked on #6 — read this first

**This design keys every split on a location comparison that does not exist yet.**

`DIFFERENT` is the sole verdict that advances the walk, so the location comparison decides every
split. But `core/leads.py` has exactly one normalizer — `_norm_url` — and no location normalizer;
this spec never defined one; and its own Non-goals hands "string normalization" to #23. So the
comparison is a bare string equality, and:

- `London` vs `London, UK` returns DIFFERENT → a **second note for a cross-board re-post**. Today
  that collides at `_path_for` and reports `updated`: one note. **The design would regress today's
  behaviour** — and deliver exactly the duplicate-per-re-post the user rejected when choosing the
  evidential rule.
- "The accepted cost" claims rule 2 prevents precisely that duplication. Both claims cannot stand.
- `bool("   ") is True`, so whitespace dirt splits on the *absence* of evidence, which the governing
  rule forbids.

This is this spec's own named pattern, one level down: the prose is right, and the mechanism it now
depends on was never specified (`rev-r3-001`).

**User decision (2026-07-16): fix #6 first.** Location quality determines every split here, so the
field must be made trustworthy before identity is keyed on it. #6 (company/location mashing) already
states that a mangled company "breaks any (company, role) identity built on top of it"; the same is
true of location, and this design is what makes it load-bearing. **#6 must deliver a location clean
and normalized enough to key identity on** — that is now a requirement of #6, not an aside.

**Update 2026-07-16 — the location half was split out and designed; read
`2026-07-16-location-identity-normalizer-design.md` before resuming.** The resumption is **not**
"point rule 3 at the normalizer", which is what this section originally said. That instruction is
wrong, and acting on it reintroduces the defect:

**Rule 2 below is keyed on normalized *equality*, and measured against the real fixture corpus it
fires 0 of 33 same-city re-post pairs.** Real re-posts *overlap* but are never *equal* (`London` vs
`London EC4Y`). Every one of them falls through to `UNKNOWN` → `merged` — so the counter this design
calls "its only signal" would drown in ordinary re-posts, and rule 2's stated purpose ("what stops
every employer re-post becoming a duplicate") would be defeated silently. Rule 3 was never the
problem.

So `_compare_locations(a, b, noise)` returns this design's own trichotomy, and **rules 2–4 collapse
into one call**:

```
both urls non-empty and normalized-equal -> SAME
otherwise                                -> _compare_locations(note_location, lead_location, noise)
```

Rule 2 then goes from 0/33 to 33/33. The rule table below is left as originally reviewed, so this
correction is visible rather than quietly applied.

**Three things this resumption owes, beyond rules 2–4.** They are recorded here because they are this
spec's to do, and a reader who stops at the rule table will miss them:

1. **`location_noise_words` on the root `Config`, defaulting `[]`** — the store is resolved from the
   root Config (`app.py:169`), so a sub-app block cannot reach it; `config.py:43`'s `baseline_rel`
   comment records the same lesson. It needs **`assert c.location_noise_words == []` in
   `test_ingest_defaults_carry_no_preference`, in the same change** — that file is an enumeration and
   ships green on keys nobody names, which its own comments record happening twice. It also needs a
   **commented-out** line in `sluice.yaml.example`, per the `locations:` precedent at `:11-14`;
   without it the knob is undiscoverable and the user cannot recover the `Remote`/`London` mis-split.
   This makes "Empty-config-abstains — not engaged" (below) **false**: this spec now adds a
   preference gate, and the DoD must carry an item for it.
2. **The Config-first section's "these constants stay literal" no longer holds** — one tunable now
   ships. The section needs re-deriving, not deleting.
3. **The REFUSE trigger recipe (§Testing) is stale.** Collapsing rules 2–4 changes what returns
   `DIFFERENT`, and `_compare_locations('X/Y', 'X:Y')` is `SAME` — `\W+` maps both `/` and `:` to a
   space — so the recipe is unsatisfiable and its control yields `updated`, not `created`. REFUSE
   itself stays reachable (a ≥40-char non-word run still collides), so this is a stale recipe, not a
   design break: re-derive it.

**So "the block is the only open item" is no longer true** — re-review on resumption covers §2,
Testing and the DoD against the tri-state. Everything else below remains reviewed and folded.

## Goal

Make `Vault.upsert` incapable of **silently** losing a lead. Two genuinely different jobs that happen
to share a company and a title must end up as two notes, without relocating a single existing note
and without manufacturing duplicates for the ordinary re-post.

"Silently" is load-bearing and was tightened after review. One pathological case remains where the
design refuses to store a lead — but it refuses **loudly and counted**, never by pretending it wrote
something. A loud refusal is a bug report; a silent merge is a lost job.

## Background — what the code actually does

Issue #5 as filed proposed adopting `(normalized company, normalized role)` as the lead identity.
Tracing the code first showed that framing to be wrong in three ways, each verified empirically
against a real `Vault` rather than inferred, and each independently re-verified in plan review.

**There are three identity keys, and one is dead.**

| Key | Definition | Used by |
| --- | --- | --- |
| `Lead.dedup_key` (`core/leads.py:31`) | normalized URL, falling back to `sha1(title\|company)` | `seen.db` PRIMARY KEY; within-run dedup (`ingest/engine.py:92`) |
| `Vault._path_for` (`core/vault.py:83`) | `"{company} - {title}"[:120]` | `upsert` — decides create-vs-update |
| `Vault.existing_keys()` (`core/vault.py:95`) | `url` frontmatter, normalized | **nobody** |

`existing_keys()` is declared in the `Store` protocol (`core/protocols.py:81`) and has tests, but no
production code calls it. Ingest dedup reads `seen.load()` (`ingest/engine.py:44`) exclusively. So
`seen.db` — a rebuildable cache — is the dedup gate, while the vault is the source of truth, and the
two key differently. Settling that method's fate belongs to #23, which owns the read key.

**The vault write path is already `(company, role)`-keyed.** That is precisely what `_path_for`
computes. Issue #5's proposal was therefore close to a description of the status quo on the write
path — and adopting it as the read key would make *this* issue worse, which is why the issue's own
final acceptance test ("never merges two genuinely different roles at the same company") contradicted
its proposal.

**Both directions verified.** Upserting into a real `Vault`:

| Case | Outcome |
| --- | --- |
| Same role, identical `company`+`title`, two different URLs | `created`, `updated` → one note |
| Same role, drifted strings (`Acme Corp` vs `Acme`) | `created`, `created` → two notes |
| Two different jobs, same `company`+`title`, two cities | `created`, `updated` → **one note; the second job is gone** |

The third case is this spec's subject. The surviving note keeps the *first* posting's `location` and
`url`; the second is reported `updated`, never stored, and nothing records that it existed.

## Key finding — never-clobber is what makes the loss silent

On a path collision `upsert` bumps only `last_seen`, protecting the existing note. That is
never-clobber working exactly as designed. Nothing protects the *incoming* lead.

**So the fix cannot be to overwrite.** It must be to not collide. Every design decision below follows
from that constraint.

## The governing rule

A false merge is silent and unrecoverable: the job is destroyed with no trace. A false split is
visible and recoverable: you see two notes, and #23's `dedupe` can merge them. The repo already
reasons this way — the CV fabrication gate, empty-config-abstains, and `apply/select.py`'s refusal to
guess between duplicate shortlist records (*"never silently picks the first, unlike cv"*).

**Split only on positive evidence of difference. Never split on the absence of evidence.**

Plan review rejected an earlier, stricter formulation ("when uncertain, split") because splitting on
*absence* is unbounded: nothing about an unidentifiable lead reproduces its name on the next run, so
every scrape mints another note (`inv-002`). Absence of evidence therefore merges — which is exactly
today's behaviour, no better and no worse, and bounded. The improvement is confined to the case where
we can *prove* two postings differ.

## Design

### 1. `same_opportunity(note_fm, lead)` — new, pure, in `core/leads.py`

Lives beside `slug_matches`, which already takes a note — same precedent, and it keeps the decision
testable without a filesystem. Plan review confirmed this is the right home for the mirror reason:
pure and shared, so no second store re-derives the `url: ""` trap for itself.

```
both urls non-empty and normalized-equal      -> SAME       # proof
both locations non-empty and normalized-equal -> SAME       # inference: re-post / cross-board
both locations non-empty and differ           -> DIFFERENT  # the two-cities case; the only split
otherwise                                     -> UNKNOWN    # never splits; see the governing rule
```

> **Superseded — see "Blocked on #6", above.** Rule 2's *equality* fires 0 of 33 real re-post pairs,
> so rules 2–4 are replaced by a single `_compare_locations` call. The table is kept as reviewed so
> the correction is legible; do not implement it as written.

Only a matching URL is *proof*. Everything else is inference, and the rule is built so that inference
only ever runs in the direction that is safe to be wrong in.

**Both URLs must be non-empty.** `google.py` states that Google job cards carry no stable outbound
link, so those leads have `url: ""`. Since `_norm_url("") == _norm_url("")`, a rule of the form "urls
match → same job" would merge every Google lead sharing a company and title — reintroducing the exact
data loss this spec removes. This is a trap, not a detail; it gets a dedicated test with a positive
control.

**Why a noisy location cannot cause loss.** We split *only* on a location *difference*. Bad
extraction (#6) therefore yields spurious splits — visible, recoverable, safe. It cannot yield a
false merge. (In the reviewed draft this claim was false: UNKNOWN split too, so an extraction flake
orphaned notes. Confining splits to DIFFERENT is what makes it true.)

### 2. `Vault._resolve_path(lead) -> (path, action)` — new

`_path_for` is **unchanged** and remains the first candidate. That single decision is what makes this
a zero-migration change: every existing note keeps matching, and nothing moves.

A candidate is **nameable** only when the field it is built from is non-empty:

1. `Company - Title.md` — always nameable; unchanged; the first-seen job keeps the clean name
2. `Company - Title - {Location}.md` — nameable only when location is non-empty

There is deliberately **no URL-hashed candidate**. An earlier draft had one; it was the mechanism by
which a volatile URL (`_norm_url` keeps the query by design, `leads.py:7`) minted a fresh note every
run, building the unbounded growth this spec calls the worse failure (`inv-002`).

**Every verdict terminates the walk in place, except DIFFERENT.** That is the whole algorithm:

| At a candidate | Action | Terminates? |
| --- | --- | --- |
| free | `CREATE` here | yes |
| `SAME` | `UPDATE` here — bump `last_seen` | yes |
| `UNKNOWN` | `MERGE` here — bump `last_seen`, and count it (§3) | **yes — even if a later candidate is free** |
| `DIFFERENT` | advance to the next nameable candidate | no |
| *(ran out of candidates; every verdict was DIFFERENT)* | `REFUSE` — write nothing, log, count | yes |

**Advancing to a later candidate _is_ the split.** Since the only verdict that advances is DIFFERENT,
"split only on positive evidence of difference" is now enforced by the shape of the algorithm rather
than asserted next to it.

That distinction is the round-2 blocker (`rev-r2-001`, `tst-r2-001`, corroborated). The previous
draft recorded UNKNOWN and *continued*, resolving it only "on exhaustion" — so an UNKNOWN at
candidate 1 with a **free** candidate 2 never exhausted: it created a second note and orphaned the
first. That split on the absence of evidence, which the governing rule forbids, and it failed this
spec's own orphaning test. Two reviewers traced the same case: a note lacking `url` and `location`,
and a URL-less google lead carrying one.

An earlier draft selected the merge target *by position* ("the last nameable candidate"), which could
bump a note **proven** DIFFERENT — verbatim the harm the rule refuses for candidate 1 (`inv-004`).
Terminating on UNKNOWN removes the concept: there is no exhaustion state to select from except
all-DIFFERENT, and that one writes nothing at all.

**REFUSE is reachable only pathologically.** Every candidate proven DIFFERENT requires a non-empty
location (an empty one can never return DIFFERENT), so it needs two distinct locations sanitizing to
the same suffix, or a note whose frontmatter contradicts its own filename. Refusing loses the lead,
which is why it must be loud, counted, and — critically — **retried** (§3).

**Truncation: bound the suffix first, then cap the stem.** `_path_for`'s `[:120]` truncates from the
right, so composing the full name and *then* capping is precisely what destroys the suffix; for a
long `company - title`, candidates 1 and 2 become byte-identical and the walk collapses (`inv-001` +
`rev-001`, corroborated).

But `stem[:120 - len(suffix)]` is itself unsafe: for a location longer than 117 characters the index
goes **negative**, and Python silently reads that as "all but the last N chars" — no error, the
identity-determining cap quietly broken, reachable through #6's dirt (`inv-r2-003` + `rev-r2-003`,
corroborated). `sink.write` also sits outside `engine.py`'s per-source `try`, so an `ENAMETOOLONG`
would kill the whole run rather than one source.

So the suffix is bounded to a fixed maximum **before** the arithmetic, guaranteeing the stem budget
can never go negative:

```
suffix  = sanitize(location)[:_SUFFIX_MAX]        # _SUFFIX_MAX = 40, a literal (see Config-first)
stem    = f"{company} - {title}"
name    = stem[:120 - len(_SEP) - len(suffix)] + _SEP + suffix    # budget >= 77, always positive
```

Sanitizing matches `_path_for` (`/` and `:` replaced). `_resolve_path` returns `path = None` when the
action is REFUSE — no correct path exists, since every candidate is a note proven DIFFERENT, and
populating that slot by position is exactly the `inv-004` harm the walk was restructured to remove
(`rev-r3-003`). The REFUSE branch never dereferences it.

**The 120 cap counts characters, not bytes** — a pre-existing property of `_path_for`'s `[:120]`,
neither introduced nor worsened here. `ext4`'s `NAME_MAX` is 255 *bytes*, and a 120-character
non-ASCII name can reach ~480 (363 measured for a CJK sample), so `ENAMETOOLONG` remains reachable;
`engine.py:60`'s `sink.write(fresh)` also sits outside the per-source `try` that closes at line 55,
so it would abort the whole run rather than one source. This spec does **not** claim to close that —
an earlier DoD asserted "no `ENAMETOOLONG` escapes `sink.write`", which the mechanism does not deliver
(`rev-r3-002`). Certifying a guarantee you have not built is the over-claiming this project has now
corrected four times. **Filed as its own issue against the pre-existing `[:120]`.** The natural fix
composes cleanly when it happens: `Vault._write` guards `OSError` and returns `"refused"`, which the
four-outcome vocabulary already accommodates and §3(a)'s allowlist already retries.

The existing 120-char truncation is itself a narrow instance of this bug: two different long titles
sharing a 120-char prefix collide at candidate 1. They now resolve through the same walk to different
notes, without `_path_for` changing.

### 3. `Vault.upsert` — four honest outcomes, wired all the way out

```
path, action = self._resolve_path(lead)
CREATE -> _write(path, self._render_new(lead));  return "created"
UPDATE -> self._bump_last_seen(path, ...);       return "updated"   # unchanged — never-clobber holds
MERGE  -> self._bump_last_seen(path, ...);       return "merged"    # indiscriminable; counted
REFUSE -> log loudly; write nothing;             return "refused"
```

The reviewed draft's DoD demanded `upsert` "never returns `updated` for a lead that was not stored",
which its own accepted merge falsified (`rev-002`, `tst-002`) — an unsatisfiable claim invites
someone to weaken the item preventing unbounded growth. `"updated"` now means only what it says: an
existing note for a lead we identified.

**Two call-site changes are load-bearing, not incidental.** Round 2 found that without them the new
outcomes are theatre: the mechanism that makes MERGE and REFUSE *visible* is the entire justification
for accepting them.

**(a) `ingest/sink.py` must not record a refused lead in `seen.db`** (`inv-r2-001` + `arc-r2-002` +
`tst-r2-004`, corroborated). `sink.py:33` appends to `recorded` unconditionally and `seendb.save`
persists it; `engine.py:93` then drops that lead before `upsert` on every later run. So a refusal
fires **once**, then the lead is gone for good — even after the operator fixes the cause. As wired,
the refusal is an obituary, not a bug report. Fix:

```
outcome = self.vault.upsert(lead)
counts[outcome] = counts.get(outcome, 0) + 1
if outcome in ("created", "updated", "merged"):   # allowlist: outcomes that mean a note now exists
    recorded.append(lead)                          # anything else stays un-seen, so the next run retries
```

**The allowlist is the point, not a style choice** (`arc-r3-002`). The obvious spelling —
`if outcome != "refused"` — is a *denylist over a vocabulary*, and the first draft of this fix used
it. An out-of-vocabulary outcome from a second store would then be recorded in `seen.db`, dropped by
`engine.py:93` forever, and counted into no visible bucket: a silent, permanent lead loss re-entered
through the fix for the previous silent, permanent lead loss. Stating the branch positively over
"a note now exists" means an unknown outcome leaves the lead un-seen and retried — the safe default,
and the same allowlist-over-denylist reasoning the no-bypass guard arrived at.

A refused lead therefore recurs every run until the collision is fixed. That is the point: REFUSE
writes nothing, so retrying it cannot regrow notes.

**(b) `sluice/cli.py` must print the new counts** (`inv-r2-002` + `arc-r2-001` + `rev-r2-002` +
`tst-r2-002` — **four reviewers**). `ingest/sink.py:32` accumulates with `counts.get(outcome, 0) + 1`
and `ingest/engine.py:60-62` sums the sink dict generically by key — both verified — but `cli.py:147`
hardcodes the two it prints:

```python
print(f"written: {w['created']} created, {w['updated']} updated", file=sys.stderr)
```

`merged`/`refused` reach `report.written` and stop dead. A DoD that says "through the sink and the
ingest report" is satisfied by `assert report.written["refused"] == 1` while the user sees nothing —
this spec's own bug class, inside its own DoD. `cli.py` prints `created`/`updated` always, and
`merged`/`refused` whenever non-zero, with `refused` named as a refusal rather than folded in with
`skipped`. Every read uses `.get(key, 0)`: `w['merged']` would `KeyError` on a clean run.

**MERGE's count is its only signal.** REFUSE at least logs; MERGE does not, by construction — it is
indistinguishable from a re-scrape at the moment it happens. If the count is not printed, MERGE is
silent, and "The accepted cost" below loses the visibility argument it rests on.

## The accepted cost — stated honestly

Two genuinely different jobs sharing company, title **and** location (two teams, one city) still
merge, silently, reported as `"updated"`.

**This is a choice, not a limitation.** The reviewed draft claimed "no parsed field discriminates
them"; that is false for every source but google — distinct URLs *do* discriminate. Rule 2 (same
location → SAME) simply fires first, and fires *deliberately*, because it is what stops every
employer re-post and cross-board listing from becoming a duplicate note with its own drifting status.

The genuine tension, stated plainly: **rule 2 optimises for the cross-board re-post and pays for it
with the two-teams-one-city merge.** The user's decision (2026-07-16) is to keep that trade. The
alternative — splitting whenever two non-empty URLs differ — closes this hole and reintroduces a
duplicate per re-post, which is the harm #23 exists to fix.

This is the one place the design knowingly loses data. It is written here rather than buried, because
a cost described as a constraint is how a design stops being reviewable.

## Invariants

- **Never-clobber — holds, untouched.** `UPDATE` and `MERGE` bump only `last_seen`; `REFUSE` writes
  nothing.
- **Never-regress — not engaged.** `can_advance` governs only `APPLICATION_OWNED` (`status.py:64`);
  the triage-owned states are a flat set with no ladder. Every note this design creates is `status:
  new`, exactly as `_render_new` writes today. Verified in plan review.
- **A strengthening falls out.** A note that has reached `applied`/`rejected` can no longer be
  touched by a colliding *different* lead — that lead now gets its own path.
- **Empty-config-abstains** — no new preference gate; not engaged.
- **CV fabrication gate** — untouched.
- **Stdlib-only** — `re` only; no new dependency. (The URL-hash candidate is gone, so `hashlib` is no
  longer needed here.)
- **No silent failures** — `REFUSE` is the design's answer to the one case it cannot serve: loud,
  counted, and never reported as a write.

## Config-first: these constants stay literal

The 120-char cap, `_SUFFIX_MAX`, and the suffix separator are **identity-determining**. As config,
changing one silently re-keys every note and duplicates the vault on the next scrape — which is the
failure this spec removes, sold as a knob. Plan review was explicit on this; they stay hardcoded.

## Testing

### The identity semantic belongs in the conformance suite

`upsert` is on the `Store` **protocol**, and never-merge is the same class of property as
never-clobber. `tests/conformance/test_store_contract.py:5-9` argues the case itself — a property
proven only of one implementation is one the second store ships without — and
`docs/ARCHITECTURE.md:111-114` already promises a future store author that the conformance suite is
what it must pass. Two reviewers landed on this independently (`arc-001`, `tst-003`): a SQLite store
keying on `(company, title)` would reintroduce this exact data loss **and pass every test the
reviewed draft described**.

So the split is:

- **`tests/conformance/test_store_contract.py`** — the behavioural probes, stated in `Store` terms.
  Its parametrized `_STORES`/`_make_store` shape supports every one of these without filesystem
  knowledge (verified in round 2):
  - two different jobs sharing company+title **and differing in location** produce two notes, neither
    lost. The location difference is not incidental — it is what makes them *provably* different.
    Stating the probe as "two different jobs" alone contradicts rule 2, which merges them when the
    location matches (`tst-r2-005`);
  - identical strings with two URLs produce one note;
  - a re-scrape touches only `last_seen`;
  - two URL-less leads sharing company+title **but differing in location** produce two notes — an
    empty URL is never proof of sameness. The location difference is what makes this probe
    discriminate rule 1's both-urls-non-empty guard rather than contradict rule 2: with empty or
    equal locations the design correctly MERGES, so the probe as first written asserted against
    correct behaviour in two of its three possible inputs (`arc-r3-001` — the same defect
    `tst-r2-005` caught in the sibling bullet, which I fixed there and reproduced here);
  - `upsert` never reports a write it did not perform, **and its return is always within the
    four-value vocabulary** — the assertion that stops an out-of-vocabulary outcome reaching
    `sink.py`'s allowlist (`arc-r3-002`).
- **`tests/test_vault.py`** — the filesystem specifics: the 120-char prefix collision, the suffix
  surviving truncation, the over-long-location bound, candidate naming.
- **`Store.upsert`'s docstring and `core/protocols.py`** state the split-never-merge rule, point at
  `same_opportunity`, and define the outcome vocabulary. Today the contract says what happens once a
  lead is "already stored" without ever defining what that *means* — exactly this spec's subject —
  and the revision took the return type from two values to four without saying so in the protocol
  (`arc-r2-003`). `"refused"` is declared **MAY return**: it arises from filename candidates
  colliding, which a database-backed store need never encounter.

### Test shapes review demanded

- **A seeded `locations` fixture in `tests/conftest.py`, beside `titles` — and the `_lead()` helpers
  must be able to reach it.** There is no location convention today: `conftest.py`'s seeded faker
  covers titles only, the sole synthetic placename is one inline `"testville"`, and the existing
  lead-level literals are real cities. This design makes `location` the discriminator, puts it in
  *filenames*, and demands two-cities tests — so location literals multiply into asserted output
  strings in the one field whose precedent is real cities (`neu-001`).

  Round 2 found the first fix insufficient, and the gap is instructive: **both files this section
  routes tests to build leads via a plain module-level `_lead()` helper** (`tests/test_vault.py:5`,
  `tests/conformance/test_store_contract.py:50`). Neither is a fixture, so a session-scoped
  `locations` cannot be injected — the implementer types a literal because *that is the only thing
  that compiles*. Worse, `test_vault.py`'s `_lead()` already defaults `location="London"`, which this
  design promotes into asserted filenames. A DoD reading "the new tests use it" is satisfiable while
  that default stands, because `_lead()` is not a new test (`neu-r2-001`). So: `_lead()` must take
  its location from the fixture, and the existing default must go. The fixture is the mechanism;
  prose asking nicely is not.

  **Do not over-correct into impossible placenames.** `_title_pool` uses `fake.job()`, which returns
  *real* job titles. The property is "generated, not chosen by whoever runs sluice"
  (`conftest.py:5`), so a seeded `fake.city()` meets the bar.
- **Idempotence needs N≥3 `upsert` runs asserting the slug *set*** — the failure it guards (a scheme
  that mints a note per run) only manifests across repeated runs (`tst-004`).
- **The note-side-empty class** — a note whose *own* frontmatter lacks `location` (legacy, or an
  extraction flake). `tests/test_vault.py:56` already seeds this shape and stays green only because
  its URL matches; drop the URL and it must MERGE, not orphan. This is the case that caught two
  successive drafts (`tst-001`, `rev-r2-001`/`tst-r2-001`): specifically, a note lacking both
  discriminators plus a URL-less lead **carrying** a location, where candidate 2 is free. It must
  MERGE at candidate 1, never create at candidate 2.
- **REFUSE's trigger is pinned** — a test that constructs the pathological case (two distinct
  locations sanitizing to the same suffix) and asserts `"refused"`, that **nothing was written**, and
  that the lead is **absent from `seen.db`** so the next run retries it (`tst-r2-003`, `inv-r2-001`).
  Its own test, not a corollary of another.
- **The accepted residual is pinned by a test** that documents it, so a future change notices it
  rather than discovering it (`tst-005`).
- **The empty-URL trap gets a direct `same_opportunity` test with a positive control** — naming its
  inputs, not just asserting the happy path (`tst-006`).
- **The `"refused"` and `"merged"` counts are asserted at the CLI surface**, not merely in
  `report.written` — asserting the dict is exactly the vacuous test round 2 caught. Assert them as
  counted numbers rather than by matching log prose, which keeps the assertion neutrality-safe.
- Fixtures stay synthetic; the suite stays offline.

## Non-goals

- **Merging duplicates that already exist**, and cross-board/re-post merging generally — that is #23,
  which owns the read key, string normalization, `sluice leads dedupe`, and the fate of
  `existing_keys()`. Plan review confirmed deferring `existing_keys()` is correct, not scope evasion.
- **Fixing company/location extraction** — #6. Related but not a blocker: this design splits only on
  a location *difference*, so #6's dirt fails safe.
- **Relocating or renaming any existing note.** Zero migration is a requirement, not an aspiration.
- **`cv`'s silent first-match pick** on an ambiguous `--lead` (`core/app.py:331` picks `notes[0]`
  where `apply/select.py` refuses). This design increases the population of same-(company,role)
  notes, making that pre-existing behaviour more reachable. Its own issue; see Risks.

## Definition of done

Each item names the surface it is satisfied at. Round 2's lesson was that "surfaces through the
ingest report" and "the new tests use it" were both satisfiable while the defect they named stood —
a DoD item that can pass without fixing anything is worse than no item, because it certifies.

- The conformance probes above pass for `Vault`, stated in `Store` terms so a second store inherits
  them — including that two provably-different jobs (differing location) produce two notes.
- `core/protocols.py` and `Store.upsert`'s docstring define what "already stored" means, name the
  four outcomes, and mark `"refused"` MAY-return — pinned by the conformance vocabulary assertion
  above, not prose alone (`arc-r3-002`).
- `sink.py:6`'s module docstring ("Both return {created, updated, skipped}") and `sink.py:31`'s
  `# "created" | "updated"` comment are updated. Both become false the moment §3(a) lands
  (`rev-r3-003`).
- A `locations` fixture exists in `tests/conftest.py`; **`_lead()` in `tests/test_vault.py` and
  `tests/conformance/test_store_contract.py` sources its location from it, and
  `test_vault.py:5`'s `location="London"` default is gone.** The check is scoped to **those two
  files** — not a grep over `tests/`, which trips over legitimate uses: `test_classify.py:116-117`'s
  `London`/`Berlin` is a deliberate `target_locations` keep/reject and case-insensitivity pair, and
  `conftest.py:46`'s `testville` is a config gate. Six further files hold lead-level placenames and
  are out of scope here (`tst-r3-002`). This is the over-correction the neutrality reviewer warned
  against in round 2, made anyway in round 3, and scoped here.
- `_resolve_path` is idempotent across N≥3 runs, asserted on the slug set.
- A note whose own frontmatter lacks a discriminator MERGES at candidate 1 — even when a later
  candidate is free — and is never orphaned.
- **Two companies sharing one long location produce two notes, each naming its company, with every
  name ≤120 chars.** This asserts the *identity property*, not the absence of an exception
  (`tst-r3-001`). The earlier item — "a location longer than the suffix bound cannot make the stem
  budget negative" — was green on the bug it named: bounding the suffix to 40 puts the budget floor
  at 77, so it can never go negative, and a test at the stated bound never engages the formula. Worse,
  writing it that way stopped the search: in the real harm band nothing raises, and at 131+ chars the
  composed name degenerates to `" - <location>.md"` with **company and title erased**, colliding two
  companies. That is the failure worth pinning.
- The `"refused"` trigger test uses **three** prior leads with empty URLs throughout: two distinct
  locations that sanitize to one suffix (`X/Y` and `X:Y` both → `X-Y`, verified) plus a third to
  occupy candidate 2. The two-lead recipe yields `"created"` — lead 2 finds candidate 2 free
  (`tst-r3-003`).
- **`sluice/cli.py` prints `merged` and `refused`**, verified by asserting the CLI output, not
  `report.written`. Every count read uses `.get(key, 0)` and a clean run does not `KeyError`.
- **A refused lead is absent from `seen.db`** and is retried — and re-reported — on the next run.
- `docs/ARCHITECTURE.md`'s conformance-guarantee list (`:95-99`, `:111-114`) is updated to name the
  identity rule.
- ruff clean; full suite green and offline.

## Risks and notes

- **More `--lead <slug>` ambiguity.** Splitting necessarily creates more same-(company,role) notes.
  `apply` already refuses on ambiguity (safe). `cv` silently picks the first — pre-existing, now more
  reachable, and worth a follow-up issue.
- **`REFUSE` declines to store a lead.** Loud, counted, and **retried every run** until the cause is
  fixed — the seen.db exclusion in §3(a) is what makes it a standing bug report rather than a
  one-shot obituary. Reachable only pathologically (a sanitization collision, or frontmatter
  hand-edited away from its filename). The alternative — a URL-hashed fallback name — reintroduces
  `inv-002`'s unbounded growth for a rarer benefit. Plan review considered REFUSE architecturally and
  declined to file against it: the store owns its naming scheme and is the only actor that can know
  its candidates collided; surfacing the conflict instead would mean exporting candidate paths
  through the contract, re-pinning the store to a filesystem.
- **The two-teams-one-city merge** — see "The accepted cost", above. Decided, not overlooked. Note
  its honesty depends on §3(b): MERGE has no log, so the printed count is its *only* signal.
- **`relevance_notes` is untouched by this design.** The reviewed draft had ingest seeding a
  `needs_review` reason there; confining splits to DIFFERENT removed the branch that needed it, and
  with it a sub-app boundary question.
- `_slug_for` derives the slug from the filename, so suffixed notes get suffixed slugs.
  `slug_matches` matches frontmatter `company-role` first, so `--lead` keeps working — it becomes
  ambiguous rather than broken, which is the safe direction and is what `apply` already handles.

## Process

**Blocked on #6 — do not start.** When it unblocks: full heavy path, since this touches vault write
paths. writing-plans → subagent-driven implementation → final whole-branch review → /review-pr →
CodeRabbit → path-to-green. Escalate rather than guess.

**Round 1** (5 specialists, 2026-07-16): 0 Critical / 11 High / 7 Medium / 3 Low. Every factual claim
the draft made about the codebase was independently verified true; every High was in the *mechanism*,
not the premises. Four defects were corroborated by two or more reviewers: the inverted truncation
rule, the two-valued action tuple, the unsatisfiable return-value claim, and the conformance-placement
gap. Confining splits to DIFFERENT resolved four Highs at once and removed the URL-hash candidate, the
`needs_review` branch, and a sub-app boundary question along with them.

**Round 3** (reviewer, test-engineer, architect — scoped to the new tree-facing claims): 0 Critical /
3 High / 4 Medium / 1 Low. The architect returned *ready to plan*; the other two did not. It found
`rev-r3-001` (this spec is blocked on #6 — see the top), and two DoD items that were **green on the
bugs they named** — the round-2 pattern reproduced inside the fixes for the round-2 pattern. It also
caught the `sink.py` allowlist: the fix for one silent permanent lead loss had re-entered another.

**Round 2** (same roster, against the revision): 0 Critical / 10 High / 6 Medium. It confirmed the
round-1 fixes held — the truncation rule, the unbounded-growth class, the exhausted-walk selection,
and the DIFFERENT/UNKNOWN ambiguity were each verified resolved rather than taken on trust. Nine of
the ten Highs collapsed into three corroborated clusters, and the pattern across them is worth
recording:

**Every round-2 High was a case of the rule and the mechanism disagreeing** — the same error, three
times, each time invisible from inside the argument that justified it:

- the walk *said* "UNKNOWN never splits" and then split on UNKNOWN whenever a later candidate was
  free, because CREATE fired during the walk and MERGE only on exhaustion;
- REFUSE was justified as "loud beats silent", while `sink.py` recorded refused leads in `seen.db`,
  making it loud exactly once and silent forever after;
- MERGE's count was justified as the visibility that made the accepted cost honest, while `cli.py`
  hardcoded the two keys it prints.

In each case the prose was right, the mechanism did the opposite, and no amount of re-reading the
prose would have found it. That is the argument for reviewing the mechanism against the tree rather
than the document against itself — and for the reviewers' standing instruction to verify claimed
resolutions rather than accept them.
