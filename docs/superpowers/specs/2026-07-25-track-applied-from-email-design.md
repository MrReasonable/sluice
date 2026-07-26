# Advance a lead to `applied` from its confirmation email (#10)

**Status:** design approved 2026-07-25; revised twice after `/review-plan` (5 reviewers each round).
Round 1 addressed 3 High + mediums (match-rule ambiguity, `.co.uk` false-proof, msg/slug wiring).
Round 2 confirmed those fixed (0 Critical/High) and folded 2 Medium + 3 Low mechanical items
(`Event.receipt_tier` declaration, non-breaking reconcile signature, residual-note breadth,
`can_apply` docstring, an intra-run reflection test).
**Issue:** #10 — `feat(track): advance a lead to applied from its confirmation email`
**Sub-app:** `track`

## Problem

`track` reads email and classifies signals (interviews, rejections, offers) but does not close
the most basic loop: an application-confirmation receipt does not advance the matching lead to
`applied`. So a role applied to weeks ago can still sit at `shortlist`, which means it shows up as
un-applied backlog, the `cv` step re-tailors a CV for it, and the `apply` step can send a **second
application** to a company that already has one.

The failure this feature must not introduce is the inverse: a **false** `applied`. A receipt from an
unrelated service that merely names the company, **or a receipt matched to the wrong lead**, must not
advance a lead — because a wrong `applied` silently suppresses a real application (the never-clobber /
never-regress family of harms this codebase engineers out). "Wrong lead" is a first-class failure
here, not only "wrong company": two roles at one company, or two companies on one shared ATS host,
are the realistic ambiguity cases.

## Chosen approach

Two decisions were settled during brainstorming (both user-approved):

1. **Match strategy: domain-anchored, deterministic.** The LLM decides only *"is this an application
   receipt"*. The lead **match** is deterministic: the receipt's sender host (or an apply-link host in
   the body) must match the lead's posting-`url` host, or come from an ATS relay host
   (`ats_relay_domains`) corroborated by the company appearing in the body. A name-only mention never
   matches, and an **ambiguous** match never auto-advances.

2. **Write policy: tiered.** A **proof-grade, unambiguous** match auto-advances `shortlist → applied`
   and records evidence. A weaker **corroborated** match (ATS-relay host + company-in-body), and any
   **ambiguous** match (a host that matches more than one shortlist lead), only **propose**
   (dead-letter → human `sluice track confirm --to applied`). This mirrors `same_opportunity`'s
   SAME/UNKNOWN split, `classify._resolve_lead`'s refuse-on-ambiguity, and the existing auto-advance
   confidence bars in `reconcile`.

The receipt path grafts onto the existing `track run` pipeline (`fetch → classify → reconcile`,
per-message resilient, `--dry-run` throughout). **No new CLI command**; receipts surface in the
`RunReport` and dead-letter proposals like every other signal, and the existing `confirm` is
extended to accept `applied` as a target.

### Flow

```
message ──▶ classify (LLM) ──type=receipt──▶ engine.run(): match_receipt(msg, shortlist_leads, ats_relay_domains)
                                                    │   (raw msg in scope here; sets ev.lead_slug / ev.candidates / ev.receipt_tier)
                                                    ▼
                                              reconcile receipt branch
                        proof + unambiguous  ───┼──▶ can_apply → auto-advance shortlist→applied + evidence
                        corroborated / ambiguous ┼──▶ propose (dead-letter → `track confirm --to applied`)
                        no domain match       ───┴──▶ fall back to the LLM's own name guess (surfacing
                                                        only, never a write) → known lead: propose for
                                                        review; nothing known: skip
```

## The match rule (the load-bearing detail)

This section supersedes the earlier "registrable last-two-labels" heuristic, which false-matched
multi-part TLDs (`bigco.co.uk` and `random.co.uk` both reduce to `co.uk`). Matching is done on
**full hosts**, never on a reconstructed registrable domain, so there is no eTLD+1 to get wrong.

> **Superseded during implementation (pre-push review, Critical).** The rule below originally
> admitted **apply-link hosts** — every host found in `msg["body_text"]` — as receipt hosts
> alongside the sender. Two things were wrong with that, and both are corrected in the text that
> follows. (1) Body links are **sender-controlled** footer content: any sender can link anything, so
> a link proves nothing about who sent the mail, and admitting them let an unrelated sender supply
> both a proof host and the ATS-relay flag. (2) The proof tier assumed a lead's `fm["url"]` host
> identifies the **employer**; sluice scrapes job **boards**, so for most leads that host is a
> multi-tenant aggregator, and `ats_relay_domains`' eight ATS vendors do not cover them.

Host extraction (deterministic, from the raw `msg` dict — never from the LLM's `ev.links`, and
never from the body):
- **sender host** — parse `msg["headers"]["from"]` for the email address, take its domain,
  `.lower()`, strip a leading `www.`. This is the *only* receipt host.
- **lead host** — `urlparse(note.fm["url"]).hostname`, lowercased, `www.`-stripped. A lead with an
  empty `url` has no host and can never match (abstain — a url-less lead is not evidence).

Predicates:
- `is_ats(host)` — True iff `host == K` or `host.endswith("." + K)` for some key `K` in
  `ats_relay_domains` (so `boards.greenhouse.io` matches key `greenhouse.io`).
- `is_multi_tenant(host)` — the same suffix test against `ats_relay_domains` **and**
  `job_board_domains` (the boards sluice ships ingest sources for, keyed by registrable domain, so
  one key covers a board's subdomains). These are the hosts shared by many employers.
- `hosts_match(a, b)` — True iff `a == b` or `a.endswith("." + b)` or `b.endswith("." + a)`
  (equality or a subdomain relationship in either direction, on **full hosts**). `random.co.uk` and
  `bigco.co.uk` do **not** match — neither is a subdomain of the other.

Resolution over the shortlist set (each lead `L` with host `H_L`, sender host `S`):
- **proof-eligible** `L`: `hosts_match(S, H_L)` and **not** `is_multi_tenant(S)` and **not**
  `is_multi_tenant(H_L)`. (A multi-tenant host on either side is never proof — `boards.greenhouse.io`
  is shared by every greenhouse-hosted lead and `linkedin.com` by every board-sourced one, so
  neither proves anything about *which* company. The lead-side test is not redundant: a
  non-registrable configured host would leave its parent readable as a non-shared sender.)
- **corroborated-eligible** `L`: `is_ats(S)` **and** `L`'s company tokens all appear in
  the subject+body (token match, reusing the tokenization already in `core/leads.py`).

The consequence is accepted deliberately: a board-sourced lead can no longer be proof-matched, so it
degrades to corroboration or to a proposal. A missed auto-advance costs one confirmation; a wrong
one silently suppresses a real application and is effectively irreversible.

`match_receipt` returns `ReceiptMatch(lead_slug: str | None, tier: str, candidates: list[str])`,
`tier ∈ {"proof", "corroborated", "none"}`:
- exactly one proof-eligible lead → `(slug, "proof", [])`. (An implementation-time *cross-tier
  ambiguity* branch — one proof lead plus a DIFFERENT corroborated lead — has been removed along
  with body links: corroboration now requires an ATS sender and proof requires a non-multi-tenant
  one, so the two tiers are disjoint by construction and the branch was unreachable.)
- more than one proof-eligible lead → `(None, "corroborated", [slugs])` — **ambiguous, propose**.
- else exactly one corroborated-eligible lead → `(slug, "corroborated", [])`.
- more than one corroborated-eligible lead → `(None, "corroborated", [slugs])` — propose.
- none → `(None, "none", [])`.

**Refuse-on-ambiguity is structural, not denylist-dependent.** Proof safety does not rest on
`ats_relay_domains` being exhaustive: an ATS *not* in the default (BambooHR, Personio, …) that uses a
per-company subdomain (`acme.bamboohr.example`) still matches exactly one lead and is genuinely
specific; any *shared multi-tenant* host that is unlisted — an unlisted shared-host ATS, or a
PaaS/pages parent that hosts many tenants under one domain (a shared `*.pages.example`-style host) —
would match multiple leads and be caught by the ambiguity refusal (→ propose), or match one lead and
advance it: a residual the ambiguity rule cannot see, accepted and documented, and shrunk by keeping
the ATS/shared-host default current. (The residual is not ATS-specific — `hosts_match` is bidirectional,
so any shared parent domain shared by a lead and a receipt is in scope.)
Emptying `ats_relay_domains` or `job_board_domains` disables the *safety downgrade* (a shared host
could then read as proof for a lone lead); both are safety denylists, **not** preference gates, and
both defaults are non-empty by design (the list-only neutral-defaults sweep does not touch these
dicts — see #26/#63). For the same reason a user block **merges over** the shipped default rather
than replacing it: adding one in-house ATS must not drop the shipped entries, since that widens the
proof tier. Document this in `sluice.yaml.example`.

## Components

### `sluice/track/receipt.py` (new — pure, the testable core)

- Implements `match_receipt` and the predicates above. No I/O; takes the raw `msg` dict (a plain
  dict, like `Source.parse`'s input — purity holds) plus the shortlist leads, the ATS map and the
  job-board map.
- `urllib.parse` for host extraction (already imported elsewhere in `sluice/`, so it is an accepted
  stdlib import; core stays standard-library only).
- Company-token comparison reuses the normalization in `core/leads.py` rather than reinventing it.

### `sluice/track/classify.py`

- Add `receipt` to `_TYPES` so the LLM can classify an application acknowledgement.
- For a `receipt`, the LLM's name-resolution is **not authoritative for `lead_slug`** — the
  deterministic domain matcher (run in `engine.run`) owns lead resolution. `ev.lead_slug` is left
  `None` at classify time and is filled in by the engine.
- Declare a new field on the `Event` dataclass: `receipt_tier: str | None = None`. The engine writes it
  from the `match_receipt` result and the reconcile receipt branch reads it, so it must be a real
  field (not an ad-hoc attribute) — reconcile tests construct `Event(receipt_tier=...)` directly.
- **(added during implementation)** The LLM's guess is not discarded, only kept out of the write
  path: `_resolve_lead` still runs against the same in-flight `leads` list, and its result lands in
  two new fields, `llm_lead_slug`/`llm_candidates`, that the write path never reads. `engine.run`
  reads them only to decide whether an unmatched receipt is worth surfacing (see below) — a
  lower-trust, name-based signal good enough to flag for a human, not to act on.

### `sluice/track/engine.py`

- `run()` loads `shortlist_by_slug = {n.slug: n for n in vault.read_leads({"shortlist"})}` — kept
  **out of the LLM prompt** (the classify prompt stays in-flight-only; the deterministic matcher
  receives the shortlist set).
- **Where the match happens.** After `classify`, if `ev.type == "receipt"`, the engine (where the raw
  `msg` is in scope, `engine.py:85`) calls `match_receipt(msg, shortlist_by_slug.values(),
  cfg.ats_relay_domains)` and writes the result back onto the Event: `ev.lead_slug = match.lead_slug`,
  `ev.candidates = match.candidates`, and a new `ev.receipt_tier = match.tier`. This is what makes the
  proposal hint, dead-letter attribution, `deadletter.clear_lead`, and the intra-run reflection — all
  keyed on `ev.lead_slug` — work for receipts exactly as for other signals (fixes the
  `lead_slug=None` misfires).
- `_PROPOSE_TARGET["receipt"] = "applied"` so a proposed (corroborated/ambiguous) receipt emits a
  `sluice track confirm --lead "<slug>" --to applied` hint (or, when ambiguous, the multi-candidate
  hint the existing code already builds from `ev.candidates`).
- Intra-run never-regress reflection: a receipt advances a **shortlist** lead, which lives in
  `shortlist_by_slug`, *not* `note_by_slug` (that holds only in-flight leads). Reflect the new
  `applied` status back into `shortlist_by_slug[slug]` so a second receipt for the same lead in one
  run reconciles against current state; `deadletter.clear_lead` keys on the same slug.
- `confirm()` uses `can_transition` (so `--to applied` is legal from `shortlist`).
- **(added during implementation) Unmatched-receipt surfacing**, superseding the flow's original
  blanket "no match → skip": when `match_receipt` finds no domain evidence at all
  (`ev.receipt_tier == "none"`, so `reconcile` skipped) AND the LLM's own fallback resolved to a
  known in-flight lead (`ev.llm_lead_slug`) or an ambiguous set of them (`ev.llm_candidates`), the
  run records a dead-letter proposal naming that lead / those candidates instead of staying silent —
  SURFACING only, never a write (the note is untouched, no `--to applied` hint since an in-flight
  lead can't legally take one). This closes a #40-class silent loss: a message the LLM types
  `receipt` but which actually concerns an already-in-flight lead (say a rejection it mislabelled)
  previously vanished with no trace, since `match_receipt` only ever searches `shortlist_by_slug`
  and such a lead structurally cannot appear there. Only when the LLM's own guess *also* resolves to
  nothing does the run stay quiet, unchanged from the original design. The write gate itself is
  untouched: an advance still requires `receipt_tier == "proof"` AND the note in `shortlist_by_slug`
  AND `can_apply` AND `confidence >= auto_apply_min`.

### `sluice/track/reconcile.py`

- `reconcile()` gains `shortlist_by_slug` as a **keyword-only, defaulted** parameter — appended so it
  does not shift `dry_run`: `reconcile(event, note_by_slug, vault, cfg, client, dry_run=False, *,
  shortlist_by_slug=None)` (None treated as `{}`). This keeps all 8 existing `reconcile(...)` callers in
  `tests/test_track_reconcile.py` working unchanged; only `engine.run` and the new receipt tests pass
  the map. A receipt event with an empty `shortlist_by_slug` simply finds no match (skips).
- A **receipt branch placed before the generic no-match None-guard** (`reconcile.py:61`), so a receipt
  with a resolved `ev.lead_slug` is handled by its own logic rather than being proposed as
  "unmatched/ambiguous". It looks the note up in `shortlist_by_slug`:
  - `ev.receipt_tier == "proof"` + a resolved `ev.lead_slug` + `_status.can_apply(note.status)` + LLM
    confidence ≥ `auto_apply_min` → write `{status: applied, last_signal: today}` via surgical
    `update_fields` and append the evidence section; `action = "applied"`, `status_to = "applied"`.
  - `ev.receipt_tier == "corroborated"`, or `ev.candidates` non-empty (ambiguous) → propose
    (`action = "proposed"`), proposal names the matched lead / candidates and `--to applied`.
  - otherwise (`"none"`) → skip.
- **Receipt-specific field set (not `_advance`).** The receipt branch writes only
  `{status: applied, last_signal: today}` (optionally `applied_date`). It must **not** reuse
  `_advance`, which opportunistically stamps `interview_date`/`interview_link` from `ev.when`/
  `ev.links` — a receipt routinely carries a portal URL, and stamping `interview_link` onto an
  `applied` lead is semantically wrong (surgical, so not a clobber, but mislabeled).
- Evidence: `append_body_section(note.ref, tag, "## Application receipt ...")` recording sender,
  subject, date, matched host, and tier. Tag keyed by `message_id` so a re-receipt is idempotent
  (never-clobber append; never overwrites the body).

### `sluice/core/status.py`

- Add `can_transition(current, target)`: if `normalize(target) == "applied"` dispatch to
  `can_apply(current)`; else dispatch to `can_advance(current, target)`.
- **Rationale (corrected per arch-003):** `confirm()` is the caller — it accepts an arbitrary `--to`
  target and needs the routing. `reconcile`'s receipt branch calls `can_apply` **directly** because it
  already knows the target is `applied`. `can_transition` centralizes target→predicate routing in
  `status.py`, the owner of the ladder, rather than inlining a second dispatch at the `confirm` call
  site. It adds no new ladder logic and does not blur the deliberately-separate `can_apply` /
  `can_advance` predicates: `shortlist → applied` is the `can_apply` transition (shortlist is
  triage-owned, applied application-owned), and `can_advance` — which requires *both* ends
  application-owned — would wrongly reject it.

### `sluice/track/config.py` + `sluice.yaml.example`

- **Consume** `ats_relay_domains` (defined today, used nowhere — this feature is its first consumer).
- Add `auto_apply_min: float = 0.75` — the LLM's receipt-classification confidence floor for a
  proof-grade auto-advance. Corroborated/ambiguous tiers always propose regardless of confidence.
  Document both, and the `ats_relay_domains` safety-denylist semantics, in `sluice.yaml.example`.

## Documentation (arch-001 — fold into this PR, user-approved)

Track now performs `shortlist → applied`, which the docs describe as apply-only. Update, and
regenerate the AI-tool outputs (`npx rulesync@9.6.3 generate -t '*' -f '*'`):
- `.rulesync/rules/CLAUDE.md` (canonical, human-gated) — the never-regress paragraph: note that a
  confirmation **receipt** (track, via `can_apply`) also advances `shortlist → applied`, distinct from
  apply's send path; `shortlist → applied` remains the only *apply* transition.
- `docs/ARCHITECTURE.md` — the track and status-lifecycle sections gain the receipt actor.
- `sluice/core/status.py` — both the **module** docstring and the **`can_apply` function** docstring
  (`status.py:44-48`, which frames the transition as apply-exclusive) record that track also routes
  through `can_apply` to advance `shortlist → applied` on a receipt, alongside apply.

## Invariants upheld

- **Never-regress (status).** `can_apply` returns True *only* for `shortlist`. A receipt against an
  `interview`, `offer`, `applied`, or terminal lead is refused — it can never pull a lead backward,
  and is idempotent on an already-`applied` lead. Ambiguity never auto-advances. `confirm --to
  applied` is gated by the same predicate.
- **Never-clobber (writes).** Status via surgical `update_fields` (named keys only, receipt-specific
  field set); evidence via `append_body_section` (append, never overwrite a human decision or body).
- **Empty config abstains.** A url-less lead never matches. `ats_relay_domains` is a safety denylist
  with a non-empty default, not a preference gate; `auto_apply_min` is a confidence floor. No new
  preference gate.
- **Standard-library only.** `urllib.parse` for host extraction; no new dependency.
- **Pure/impure split.** `match_receipt` is pure (plain dict in, no I/O), called from the engine where
  the raw `msg` is in scope; host/URL extraction is deterministic (from `msg`), never from the LLM's
  `ev.links`.

## Testing (synthetic fixtures, offline; mirrors `test_track_reconcile.py` / `test_track_engine.py` / `test_core_status_apply.py`)

Reconcile/engine tests fake the backend the same way the existing track tests do (a `FakeBackend`
returning a canned classification — see `test_track_classify.py` / `test_track_reconcile.py`), so the
suite stays offline. `match_receipt` is pure and tested directly.

The three from #10:

1. A proof-grade, unambiguous receipt advances a matching `shortlist` lead to `applied`, with the
   evidence section recorded.
2. A receipt from an unrelated service that merely mentions the company name (host mismatch) → **no
   write**: assert `res.status_to is None`, `status: shortlist` still present, and **no** `##
   Application receipt` section appended (not merely `tier == "none"`).
3. A receipt cannot regress a lead already at a later stage (`interview` stays `interview`; no write).

Plus edges:

4. ATS-relay host + company-in-body → **proposed** (corroborated), not auto-applied; the dead-letter
   `Entry` carries the matched slug and a runnable `confirm --lead <slug> --to applied` hint
   (guards inv-002).
5. ATS-relay host with the company token **absent** from the body → **no write** (same absence
   assertions as test 2).
6. Idempotent re-receipt: after two identical receipts, the evidence section appears **exactly once**
   (count occurrences) and status is written once.
7. **Ambiguity → propose, never advance** (guards inv-001): (a) two shortlist leads sharing one
   non-ATS host, one receipt → `candidates` has both, `action == "proposed"`, **neither** lead
   advanced; (b) a shared-host ATS not in `ats_relay_domains` matching two leads → proposed;
   (c) **cross-tier** (added during implementation): one lead proof-eligible via a link in the
   body, a DIFFERENT lead corroborated-eligible via an ATS sender + company name → the proof
   winner is refused too, proposing both (`test_cross_tier_ambiguity_proof_plus_different_corrob_lead_refuses`;
   guarded against over-firing by `test_proof_survives_when_corrob_is_the_same_lead`).
8. `confirm --to applied` succeeds off a proposed receipt; `confirm --to applied` on a non-shortlist
   lead is refused with the current status as the reason.
9. A receipt advance writes **no** `interview_date` / `interview_link` (guards inv-003).
10. A classify-level test (using the existing `FakeBackend`): a receipt email is typed `receipt`, and
    a `lead` the LLM returns for it is **ignored for `ev.lead_slug`/`ev.candidates`** (not resolved
    into the authoritative fields) — the deterministic matcher owns those. (Added during
    implementation: the same guess is *also* resolved, separately, into `ev.llm_lead_slug`/
    `ev.llm_candidates` for surfacing-only use — see test 13.)
11. A pure `match_receipt` unit table, cases enumerated **adversarially** (not hand-picked — THE
    LESSON): proof (exact host; `careers.<host>` subdomain), corroborated (ATS + company),
    and a full set of `none` traps: `evilexample.com`, `example.com.attacker.invalid`,
    `notexample.com` (substring, not subdomain), a sibling subdomain of a *different* registrable
    domain, `bigco.co.uk` vs `random.co.uk` (multi-part-TLD, must **not** match), and a shared-ATS
    parent (never proof).
12. Intra-run reflection (engine-level): two receipts for the **same** shortlist lead in one
    `engine.run` advance it exactly once — the first writes `applied`, the second reconciles against
    the reflected `applied` snapshot in `shortlist_by_slug` (so `can_apply` is now False) and writes
    nothing further. Guards the separate-snapshot reflection path (`shortlist_by_slug`, not
    `note_by_slug`), which the reconcile-level idempotency test (6) does not exercise.
13. **(added during implementation) Unmatched-receipt surfacing** (engine-level), superseding this
    doc's original blanket "no match → skip": `test_receipt_about_inflight_lead_surfaces_without_writing`
    (the LLM fallback uniquely resolves an in-flight lead → one dead-letter row naming it, note
    byte-unchanged), `test_receipt_ambiguous_inflight_fallback_surfaces_both_candidates` (the
    fallback resolves ambiguously between two same-company in-flight leads → one row naming both,
    neither note touched), and `test_receipt_matching_nothing_stays_quiet` (domain match AND LLM
    fallback both resolve to nothing → no dead-letter row, no write, unchanged from the original
    design).

**Fixture neutrality.** Company hosts use the RFC-reserved `example.com` / `example.invalid` family
(guaranteed unregistrable; a local roster cannot tell whether an invented name is real). The
**company-name string** used in the corroboration fixtures is likewise a bare placeholder derived
from the reserved host (e.g. company `Example`), **not** a plausible invented firm (neut-001). The ATS
relay hosts (`greenhouse.io`, `lever.co`, …) may be named directly: they are real ATSs already present
in the shipped `ats_relay_domains` config (asserted in `test_track_config.py`), not personal data.

**Mutation witness (named).** In the receipt reconcile branch, swapping `can_apply` for `can_advance`
must redden **test 1** (`test_receipt_proof_advances_shortlist_to_applied` — `can_advance` refuses
`shortlist → applied`, so the assertion that status became `applied` fails). Run that node id and
confirm it reddens. Test 3 is **inert** for this mutant (both predicates refuse `interview → applied`,
so it stays green) — do not rely on it. Because the mutant lives in the *new* branch, the
"no pre-existing test catches it" check passes trivially; state that so the witness is not mistaken
for load-bearing coverage of pre-existing code.

## Scope

~1 new pure module (`receipt.py`) + focused edits to `classify.py`, `reconcile.py` (new signature),
`engine.py`, `status.py`, `config.py`, `sluice.yaml.example`, plus the doc updates
(`.rulesync/rules/CLAUDE.md`, `docs/ARCHITECTURE.md`, `status.py` docstring) and a rulesync
regenerate. No new dependency, no adapter-seam change, no new CLI command. Neutrality: synthetic
fixtures only — company hosts *and names* in the RFC-reserved family; real ATS relay hosts may be
named (they already ship in `ats_relay_domains`).
