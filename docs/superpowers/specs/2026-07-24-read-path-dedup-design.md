# Read-path dedup — one role across boards and re-posts should be one note

- **Date**: 2026-07-24
- **Status**: **Round 1 `/review-plan` folded** (5 agents: 0 Critical / 5 High / 7 Medium / 2 Low — all
  applied). The dominant finding, raised by three reviewers independently: the design specified *pairwise*
  predicates (location compatibility, status precedence) but never how they compose over a cluster of
  3+ members. §1 now pins **complete-linkage** clustering (an UNKNOWN location edge may not bridge two
  DIFFERENT members — the non-transitivity that would otherwise re-open #5's silent merge) and §2 an
  **order-independent, set-based** N-way status verdict. Awaiting round-2 re-review + user sign-off.
- **Origin**: issue #23, split out of #5 on 2026-07-16. #5 owns the write path (a note must never
  absorb a *different* job); #23 owns the read path (the *same* job must not become several notes).
  A prior #23 slice already shipped (PR #46: `Lead.dedup_key` folds `_norm_location` + netstring
  length-prefixing). This is the remaining full read-path dedup, built on that.
- **Scope decision (user-confirmed)**: **human-gated reconciliation only.** Ingest is left unchanged
  — it keeps splitting on drift, which is *visible and recoverable*. A new `sluice leads dedupe`
  command surfaces suspected duplicates and merges only the clusters a human names. No silent
  auto-merge at ingest. The **post-rejection re-application cooldown** ("you were rejected by this
  company N months ago") is a real, adjacent cross-note concern but is *awareness*, not *dedup* — an
  explicit **follow-up**, out of scope here.

## Problem

The same role reaches the vault as several notes when its `company`/`title` strings drift between
boards, re-posts, or a recruiter-vs-employer listing. Each duplicate carries its own `status`, and
they drift apart: a role already `rejected` or `applied` can sit in a second note at `shortlist` and
resurface as a live target — the pipeline tailors a CV for a job already turned down, or applies twice.

**The residual is narrower than "strings drift → duplicate," and verifying that against the code
narrows the design before it starts.** When two boards post the same role with *identical*
`company`+`title` but different URLs, `Vault.upsert` already collapses them to one note: candidate 1
of `_resolve_path`'s walk is the clean `Company - Title` filename, the two leads collide on it, and
`same_opportunity`'s location tiebreaker returns SAME → `update` (never-clobber). The duplicate note
appears **only when the `company`/`title` strings themselves drift**, because that produces a
*different filename*, no candidate collides, and no comparison ever runs — a second note is created
blind. So catching it requires a **normalized** company/title comparison across notes whose filenames
differ.

## The governing risk — and why it decides the locus

A normalized company/title comparison is exactly the lever that reintroduces #5's failure class:
too-aggressive normalization silently merges two *genuinely different* roles at one company. Two facts
establish that this must not be automatic:

1. **A person routinely applies to different roles at the same company within a short window** —
   companies post many reqs at once, candidates apply to two or three well-matched ones, and
   reject-then-reapply is common. Nothing in the major ATS platforms (Greenhouse, Lever, Workday,
   Ashby) auto-rejects that; they model a candidate as one profile linked to *multiple* applications.
   So a company-level merge would bin a role the user is entitled to pursue. **The safe identity is
   `(company, role)`, never `company` alone** — which is exactly what the `Company - Title` filename
   already encodes. The merge must operate at the *role* level.

2. **The hardest #23 case has no strong signal available.** `_norm_url` deliberately *keeps* the query
   string because job-ids live there (`?jobId=`, `?jk=`), so the same posting on the same board has a
   stable URL — near-proof, safely auto-merged, and already handled. But a cross-board re-post has an
   entirely different domain and id; there is *no shared job-id* to lean on, only fuzzy
   company/title/location text. The evidence needed to merge it safely does not exist at ingest time.

The failure is **asymmetric and, per (1), frequent**: a false *split* is visible (two notes) and
recoverable via `dedupe`; a false *merge* is silent — you never see the swallowed note, so you never
learn you lost it. Therefore the merge decision on fuzzy evidence belongs to a human looking at two
clustered notes, not to an ingest heuristic guessing. This is the direct application of #23's own
constraint: **when uncertain, split; never merge.**

## Design

Two pure pieces (`cluster_duplicates`, `resolve_merge_status`) plus one store mutation (`merge_cluster`),
exposed by a thin CLI command. **No ingest change. No new sub-app** — the capability is lead-identity
logic, which already lives in `core/leads.py` + `core/status.py` + `core/vault.py`; a `leads` CLI group
in `cli.py` drives it (lazy-imported, like every other command).

### 1. `cluster_duplicates` — pure, `core/leads.py`

`cluster_duplicates(notes, *, title_noise, location_noise) -> list[list[LeadNote]]`. Groups the vault's
lead notes into suspected-duplicate clusters (size ≥ 2). Two notes are candidate duplicates iff **all
three** hold:

- **Same firm** — `_norm_tokens(company)` sets are **equal**. Set-equality (not overlap, not prefix)
  honors "never collapse distinct firms sharing a prefix": `{foo}` ≠ `{foo, industries}`.
- **Same role** — `_norm_tokens(title) − title_noise` sets are **equal**. Equality (not overlap) keeps
  seniority/specialization apart: `{senior, software, engineer}` ≠ `{software, engineer}`, and
  `{backend, engineer}` ≠ `{backend, engineering, manager}`. The *only* way two spellings of one role
  collapse is a configured `title_noise` token accounting for the difference (a board decoration the
  user has observed, e.g. `remote`/`hybrid`/`contract`).
- **Compatible location** — `_compare_locations(a, b, location_noise) != DIFFERENT`. A REQUIRED
  conjunct: two same-role notes at *different cities* are genuinely different postings (#5's location
  suffix) and must not cluster. UNKNOWN (a blank side) is *not* DIFFERENT, so it clusters — erring
  toward *proposing* (the human vets it), which is safe because merge is human-gated.

**Cluster shape is COMPLETE-LINKAGE, not transitive closure (round-1 arc-001, the sharpest finding).**
Company/title equality is an equivalence relation (transitive), so grouping by the
`(company_tokens, title_tokens)` key is safe. The *location* relation is **not** transitive: with UNKNOWN
compatible-with-everything, `London ~ blank ~ Paris` are pairwise-compatible while `London`/`Paris` are
DIFFERENT. Under transitive closure a blank-location note would **bridge** two provably-different cities
into one trio — the human sees a plausible 3-note cluster, `--merge`s it, and `merge_cluster` archives
both losers (`resolve_merge_status` is status-only and never re-checks location): the exact #5 asymmetric
silent merge this design's governing risk exists to prevent. So a valid cluster is a set in which **every
member pair is mutually `!= DIFFERENT`** (a clique on the compatibility relation). A note that would only
join by bridging two DIFFERENT members is **left unclustered** (reported as a singleton), never used to
unite them. Pinned by a `{London, blank, Paris}` test that must never yield a trio spanning DIFFERENT
cities.

`_norm_tokens` is the generic token normalizer `_norm_location` already implements (NFKD-fold, casefold,
drop combining marks, `\W+`→space, split) — factored to a shared name so title and company reuse the
exact fold, and its load-bearing ordering (NFKD before casefold; unicode-aware `\W`) is pinned once.
Clustering only *proposes*, so recall-leaning is acceptable; over-inclusion costs a rejected line in the
report, never a lost role.

### 2. `resolve_merge_status` — pure, `core/status.py`

**Key finding: there is no total order across the two lifecycles.** `_RANK` ranks only the application
ladder (`applied→phone_screen→interview→offer`); the three terminals (`rejected/accepted/withdrawn`)
are unranked, and the five triage states have no rank at all. So "prefer the most-advanced status" (the
issue's phrasing) is *undefined* for `rejected` vs `interview` or `dismiss` vs `shortlist`.

**And clusters are size ≥ 2, 3+ possible (round-1 inv-001/rev-001/arc-002).** A pairwise `(a, b)` fold
over that is order-dependent around the `conflict` sentinel and could pick a triage survivor while an
application-owned member is archived — the resurfacing bug #23 exists to kill. So the verdict is defined
as an **order-independent set operation over all N members**, not a fold:

`resolve_merge_status(statuses) -> (winner: str | None, outcome: "ok" | "conflict")`, all normalized,
computed on the *set* `S` of distinct statuses:

1. **`|S| == 1`** → that status wins (`ok`). (Subsumes the equal / all-`new` case.)
2. Partition `S` into application-owned `A`, triage-owned `T`, and non-canonical `X`.
3. **`X` non-empty and `|S| > 1`** → **`conflict`** (never-regress passes unknown statuses through
   untouched; a merge must not rank one).
4. **`A` non-empty** → application-owned dominates, so `T` is dropped from contention *before* any
   triage-vs-triage disagreement is judged (you cannot un-apply). Within `A`: two *different* terminals →
   **`conflict`**; exactly one terminal → that terminal wins (`ok`) (a rejected posting is not pulled back
   to `interview`); all live → highest `_RANK` wins (`ok`).
5. **`A` empty (all triage)** → drop `new` (the universal floor) to get `S'`. `|S'| == 0` → `new` wins;
   `|S'| == 1` → that wins (`ok`); `|S'| ≥ 2` (two different non-`new` triage states, `shortlist` vs
   `dismiss`) → **`conflict`**.

This is order-independent by construction (it reads a set), so `resolve_merge_status([a, b])` and
`[b, a]` are identical, and `[shortlist, dismiss, applied]` → `applied` (step 4 drops both triage states)
in *every* order rather than a spurious conflict. The function returns only the winning **status**; the
CLI then selects the **survivor note** among the members holding that status, breaking a tie (two members
at `new`, or two at `rejected`) deterministically by highest `last_seen`, then slug — an orchestration
concern, since it needs the notes' metadata the pure status verdict does not see.

A `conflict` cluster is **reported and refused for merge** — mirroring how `normalize_all_statuses`
refuses to collapse disagreeing duplicate status lines. The human resolves the status by hand, then
re-runs. This is the invariant that makes the whole feature safe: the merge **never regresses** a
status, because the survivor is *by construction* the note already holding the winning status.

### 3. `Vault.merge_cluster` — store mutation, `core/vault.py`

`merge_cluster(survivor_ref, loser_refs, *, alt_urls, first_seen, last_seen) -> list[str]` (the archived
loser paths, so the CLI can report them and the idempotence test can assert them). The CLI decides the
survivor (via §2) and computes the unioned audit trail; the store does the mechanical write + archive:

- **Survivor** keeps its body, enrichment, scores, and status **untouched** (never-clobber). Only the
  audit trail is unioned onto it, via the CAS path (`update_fields`/an `_cas_write` transform), so a
  concurrent edit to the survivor survives: each loser URL not already present is recorded as an
  alternate source in an `alt_urls` frontmatter key, `first_seen = min`, and `last_seen` bumped through
  the store's existing **monotonic** guard (`_bump_last_seen` never lowers it — round-1 inv-003 keeps
  monotonicity enforced at the store, not delegated to the CLI's `max`).
  - **`alt_urls` serialization is URL-safe (round-1 rev-003)**: NOT comma-joined — a URL query can
    contain a comma, and a delimiter inside a field is the exact collision `dedup_key`'s length-prefixed
    netstrings already engineer out. Serialize as a JSON array string (`json.dumps`, escapes any
    delimiter, one line, valid YAML flow-sequence, tolerated by the permissive `_fm_dict` read),
    round-trip-pinned by a test with a comma-bearing URL.
- **Ordering is atomic-by-abort (round-1 arc-003)**: the survivor's audit-trail CAS write happens
  **first**; the losers are `os.replace`'d **only on its success**. A `VaultConflict` on the survivor
  therefore aborts before *any* loser is archived — nothing is lost and the command is re-runnable. A
  crash *between* survivor-update and a later archive self-heals: a re-run re-clusters, the survivor
  already carries the union (idempotent), and the un-archived losers re-merge.
- **Each loser** is **archived, not deleted** — `os.replace`'d into `Job Applications/Job Leads/_merged/`
  (a name collision there gets a numeric suffix). Reversible, matching #5's "a merge is recoverable"
  ethos. `_merged/` is a *subdir*, so `read_leads`, ingest's `_resolve_path`, and every leads-dir scan
  skip it automatically (non-`.md` `listdir` entry) — the same convention `_inbox/` already uses in the
  Experience Library.
- **A loser's own downstream state is intentionally dropped from the active view (round-1 inv-002)**: a
  loser's scores, `relevance_notes`, `tailored_cv` pointer, or a pending `needs_signoff`/`pending_cv`
  hold do not migrate to the survivor — recoverable only by un-archiving. So the report (§4) **flags**
  any loser carrying a `tailored_cv` or an open sign-off hold, so the human sees the active state a merge
  would archive away before naming that id. Documented as deliberate in `docs/ARCHITECTURE.md`.

### 4. `sluice leads dedupe` — CLI, `cli.py`

New top-level `leads` group with a `dedupe` subcommand (argparse, lazy-imported):

- **`sluice leads dedupe`** (report; changes nothing) — scans via `store.read_leads()`, clusters (§1),
  and prints each cluster with a stable id, its members (status / path / url), the computed survivor, a
  **CONFLICT** flag where §2 cannot decide, and a **loser-state flag** where any non-survivor carries a
  `tailored_cv` or an open `needs_signoff`/`pending_cv` hold (§3 inv-002 — the active state a merge would
  archive away). `--json` for a machine-readable report.
- **`sluice leads dedupe --merge <id> [<id>…]`** — merges only the named, vetted clusters (each id typed
  is the human's sign-off, mirroring `track confirm <id>` / `cv signoff`). No blanket merge-all, so a
  false cluster cannot be swept in. A `conflict` cluster passed to `--merge` is **refused** (reported),
  not merged.
- **Stable ids**: a short hash of the cluster's **sorted member slugs**, so the id a report prints is the
  id `--merge` accepts, and two clusters sharing a `(company, title)` key but split by location get
  distinct ids. `--merge` recomputes clusters fresh and matches by id; a stale id (membership changed
  since the report) is **refused with a "re-run report" message**, never guessed.

### 5. Config knob — `dedupe_title_noise_words`, root `Config`

The clustering title-match aggressiveness is a config knob from day one, modeled exactly on
`location_noise_words`: on the **root** `Config` (a store/identity concern; `Sluice.store()` only ever
sees the root Config), `field(default_factory=list)`, loaded via `_str_list` (rejects a YAML scalar that
would `list()`-explode into characters), from the YAML file only (no env override, matching
`location_noise_words`). **Default `[]` = abstain =
strictest clustering** (titles must match under plain normalization; nothing stripped) — the safe
direction, since empty errs toward *not* merging. A **commented** example line lands in
`sluice.yaml.example` (per the commented `location_noise_words: - remote` precedent), its tokens generic
work-mode words (`remote`/`hybrid`/`contract`) — never seeded with the user's real filter list, so the
example file stays obviously-placeholder (round-1 neu-002) — or the knob is undiscoverable. The
neutral-defaults sweep (`tests/test_sluice_neutral_defaults.py`) auto-covers it (root `Config` is in
`_SWEPT_CONFIGS`); an explicit `assert c.dedupe_title_noise_words == []` travels **with** the key, per
the `location_noise_words` lesson that the enumeration ships green on keys nobody names.

Company matching stays exact-normalized (no knob); a `dedupe_company_noise_words` for corporate-form
suffixes (`Inc`/`Ltd`) is a possible future knob, deferred (YAGNI until real data shows it needed).

### 6. `existing_keys()` — delete (resolve the drift the issue names)

`Store.existing_keys()` is declared in the protocol (`core/protocols.py`), implemented in `Vault`, and
tested — but **nothing calls it**; ingest dedup reads `seen.load()` exclusively. It is url-only, so it
never served the drifted case anyway, and `dedupe` scans via `read_leads`. Leaving a tested-but-uncalled
method in a seam contract is drift either way; #23 owns the decision. **Delete it** — from the `Store`
protocol, `Vault`, its two unit tests, and the conformance assertion. `seen.db` remains the ingest gate
(a rebuildable cache); `dedupe` is the vault-as-source-of-truth reconciliation that makes losing
`seen.db` harmless.

## Tests

Behaviour-asserting, synthetic fixtures, offline. Pure pieces mutation-witnessed **by node id** (move or
delete a branch → the *named new* test reddens, and confirm no *pre-existing* test is what catches the
mutant; never add-beside).

**Fixture discipline (round-1 neu-001).** The clustering cases need *constructed* token relationships
`faker` cannot produce (a noise-token pair; `{senior, software, engineer}` vs `{software, engineer}`;
`Foo` vs `Foo Industries`). Do **not** hardcode role strings — derive a base title from the faker
`titles` pool and mutate it programmatically with a synthetic noise/seniority token, and build the
company-prefix pair from a faker base plus a synthetic suffix token (`f"{base} Industries"`). This keeps
the seeded-faker mechanism that keeps titles honest.

- **`cluster_duplicates`**: drifted title with a configured `title_noise` token → one cluster; distinct
  seniority titles (no noise) → **not** clustered; same title, different city → **not** clustered
  (location DIFFERENT); distinct firms sharing a prefix (`Foo` vs `Foo Industries`) → **not** clustered;
  a blank-location member → **still** clusters with a compatible member (the UNKNOWN-clusters direction,
  asserted in **both** member orders — round-1 tst-002); **complete-linkage** `{London, blank, Paris}` →
  **never** a trio spanning DIFFERENT cities (round-1 arc-001); url-drift with identical strings is *not*
  a cluster the command needs (upsert already merged it) — asserted as a non-regression.
- **`resolve_merge_status`** (N-ary, order-independent): pairwise cases —
  `[shortlist, new]`→`shortlist`; `[rejected, shortlist]`→`rejected` (app beats triage);
  `[rejected, interview]`→`rejected` (terminal beats live); `[applied, interview]`→`interview` (ladder);
  `[rejected, accepted]`→`conflict`; `[shortlist, dismiss]`→`conflict`; `[offer, offer]`→`offer`;
  non-canonical + different → `conflict` — **each asserted in both argument orders** `(a,b)==(b,a)` for
  `(winner, outcome)`, and a symmetry mutant must redden the named test (round-1 tst-002). **Cluster-of-3
  cases** (round-1 inv-001/rev-001/arc-002): `[new, new, rejected]`→`rejected`;
  `[shortlist, dismiss, applied]`→`applied` (app dominates, no spurious conflict);
  `[applied, interview, rejected]`→`rejected`; `[rejected, accepted, new]`→`conflict`. Each branch
  mutation-witnessed. **Survivor selection** (CLI, separate from the status verdict): two members holding
  the winning status (`new`/`new`, `rejected`/`rejected`) pick the highest-`last_seen`-then-slug member
  deterministically.
- **`Vault.merge_cluster` never-clobber (round-1 tst-001 — the survivor must be SEEDED, not empty)**:
  seed the survivor with a real advanced state (`status=applied`, `score`, `relevance_notes`,
  `tailored_cv`, `applied_date`, `applied_url`, `ats`) **and** a body via `append_body_section`, then
  assert the **whole** frontmatter dict and body survive **except** `alt_urls` (union) / `first_seen`
  (min) / `last_seen` (monotonic bump) — mirroring `test_rescrape_touches_last_seen_AND_NOTHING_ELSE`.
  Also: loser archived under `_merged/` and invisible to `read_leads`; `alt_urls` round-trips a
  comma-bearing URL (round-1 rev-003); `last_seen` is never **lowered** (round-1 inv-003).
- **`merge_cluster` CAS safety (round-1 tst-003)**: use `tests/conftest.py`'s `racing_read` to race a
  survivor edit during the audit-trail union — assert it either retries cleanly onto fresh content or
  raises `VaultConflict` (handled non-fatally by the CLI), with **no loser lost or clobbered**. Plus the
  atomicity case: a survivor-write conflict archives **zero** losers (round-1 arc-003).
- **Never-regress end-to-end** (the issue's core bug): a fresh `new` re-post clustered with a `rejected`
  note → `--merge` → survivor stays `rejected`, loser archived → the role does **not** resurface at
  `new`/`shortlist`.
- **Idempotence**: `dedupe` report → `--merge id` → re-run report yields **no** cluster for that pair
  (loser archived out of the scan). A `conflict` cluster is reported and **refused** by `--merge`; a
  stale id (membership changed since the report) is refused with a re-run message.
- **Neutral defaults**: `dedupe_title_noise_words` defaults `[]` (sweep + the traveling assertion).
- **`existing_keys` removal**: its two unit tests and the conformance assertion at
  `test_store_contract.py:325` (inside `test_reading_an_empty_store_is_not_an_error`, which stays
  coherent) are deleted; the conformance suite's own surface confirms no caller remains.

## Out of scope (follow-ups)

- **Post-rejection cooldown awareness** — surfacing "rejected by this company N months ago" when a new
  role at that company appears. Cross-note, company-level *awareness*, not dedup. File as a follow-up.
- **Ingest-time auto-merge** — deliberately rejected (see governing risk). Not a future direction to
  revisit lightly; it is the #5 failure class on the silent hot path.
- **`dedupe_company_noise_words`** — deferred until real duplicate data shows exact-normalized company
  matching is too strict.
- **`--merge-all`** — deliberately omitted; a batch-silent path is where an over-inclusive cluster gets
  merged without individual review.

## Definition of Done

1. `cluster_duplicates` (**complete-linkage**, §1), `resolve_merge_status` (**N-ary, order-independent**,
   §2), `Vault.merge_cluster` (**survivor-CAS-first / archive-on-success**, URL-safe `alt_urls`, monotonic
   `last_seen`, §3), and `cmd_leads_dedupe` (report flags CONFLICT + loser-state) implemented;
   `_norm_tokens` factored and its ordering pins retained.
2. `dedupe_title_noise_words` on root `Config` + `_str_list` load + commented `sluice.yaml.example` line +
   neutral-defaults coverage (sweep + traveling assertion).
3. `existing_keys()` removed from protocol, `Vault`, tests, and conformance assertion.
4. Every test above green; the pure pieces mutation-witnessed by node id (confirmed not caught by a
   pre-existing test) under the checked-hash `compileall` regime. Exact commands (round-1 rev-002):
   - `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` (once, before witnessing)
   - `python -m pytest`
   - `ruff check sluice tests` (ruff is **not** in `[test]`; `pip install ruff==0.15.21`, the CI pin)
5. `docs/ARCHITECTURE.md` updated: the new `leads dedupe` command; the role-level, human-gated,
   archive-not-delete merge semantics; and the deliberate drop of a loser's downstream state
   (scores/notes/CV/sign-off) from the active view. No ingest/never-clobber/never-regress contract
   *widened* (the merge upholds them, it does not change them).
6. Full suite green, ruff clean, offline/hermetic.
