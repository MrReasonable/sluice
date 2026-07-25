# Advance a lead to `applied` from its confirmation email (#10)

**Status:** design approved 2026-07-25
**Issue:** #10 — `feat(track): advance a lead to applied from its confirmation email`
**Sub-app:** `track`

## Problem

`track` reads email and classifies signals (interviews, rejections, offers) but does not close
the most basic loop: an application-confirmation receipt does not advance the matching lead to
`applied`. So a role applied to weeks ago can still sit at `shortlist`, which means it shows up as
un-applied backlog, the `cv` step re-tailors a CV for it, and the `apply` step can send a **second
application** to a company that already has one.

The failure this feature must not introduce is the inverse: a **false** `applied`. A receipt from an
unrelated service that merely names the company must not advance the lead, because a wrong `applied`
silently suppresses a real application (the never-clobber / never-regress family of harms this
codebase engineers out).

## Chosen approach

Two decisions were settled during brainstorming (both user-approved):

1. **Match strategy: domain-anchored, deterministic.** The LLM decides only *"is this an application
   receipt"*. The lead **match** is deterministic: the receipt's sender domain (or an apply-link
   domain in the body) must equal the lead's posting-`url` domain, or an ATS relay domain
   (`ats_relay_domains`) corroborated by the company appearing in the body. A name-only mention never
   matches.

2. **Write policy: tiered.** A **proof-grade** match (domain equality on the company's own domain)
   auto-advances `shortlist → applied` and records evidence. A weaker **corroborated** match
   (ATS-relay domain + company-in-body) only **proposes** (dead-letter → human `sluice track
   confirm --to applied`). This mirrors `same_opportunity`'s SAME/UNKNOWN split and the existing
   auto-advance confidence bars in `reconcile`.

The receipt path grafts onto the existing `track run` pipeline (`fetch → classify → reconcile`,
per-message resilient, `--dry-run` throughout). **No new CLI command**; receipts surface in the
`RunReport` and dead-letter proposals like every other signal, and the existing `confirm` is
extended to accept `applied` as a target.

### Flow

```
message ──▶ classify (LLM) ──type=receipt──▶ match_receipt() [pure, deterministic]
                                                    │
                        proof-grade domain match ───┼──▶ can_apply → auto-advance shortlist→applied + evidence
                        ATS-relay corroboration  ───┼──▶ propose (dead-letter → `track confirm --to applied`)
                        no domain match          ───┴──▶ skip (a name-only mention never matches)
```

## Components

### `sluice/track/receipt.py` (new — pure, the testable core)

```
match_receipt(msg, shortlist_leads, ats_relay_domains) -> ReceiptMatch(lead_slug, tier)
    tier in {"proof", "corroborated", "none"}
```

- Deterministically extracts the **sender host** (from the `From:` header address) and any
  **apply-link hosts** (from URLs in the body), lowercased, `www.`-stripped.
- Compares those against each shortlist lead's `fm["url"]` host:
  - **proof** — registrable-domain equality **where that domain is NOT an ATS relay domain**. The
    ATS exclusion closes the shared-parent hole: `boards.greenhouse.io` is shared by every
    greenhouse-hosted lead, so an ATS host can never be proof by domain alone.
  - **corroborated** — the sender/link domain **is** an ATS relay domain (present in
    `ats_relay_domains`) **and** the lead's company tokens appear in the subject/body.
  - **none** — otherwise. A name-only mention with a mismatched domain never matches; an ATS-relay
    domain with no company corroboration never matches.
- Registrable-domain heuristic: strip a leading `www.`; treat the host's last two labels as the
  registrable domain for the equality test, and additionally accept a subdomain of the lead's own
  registrable domain (e.g. `careers.example.com` vs `example.com`). Documented as a pragmatic
  heuristic; the ATS-relay suffix match handles the relay case, and the ATS-exclusion prevents the
  shared-parent false merge. Pinned by the unit table.
- No I/O — offline-testable, in the grain of `core/leads.py:same_opportunity`.
- `urllib.parse` is used for host extraction (already imported elsewhere in `sluice/`, so it is an
  accepted stdlib import; core stays standard-library only).

### `sluice/track/classify.py`

- Add `receipt` to `_TYPES` so the LLM can classify an application acknowledgement.
- For a `receipt`, do **not** run the LLM name-resolution (`_resolve_lead`) — the deterministic
  domain matcher owns lead resolution for receipts. Any `lead` the LLM returns for a receipt is
  ignored.

### `sluice/track/reconcile.py`

- New `receipt` branch:
  - Compute the `ReceiptMatch` against the shortlist set.
  - **proof** tier + `can_apply(note.status)` true + LLM confidence ≥ `auto_apply_min` →
    `_advance(note, "applied", ...)` (surgical `update_fields`) and append the evidence section;
    `action = "applied"`, `status_to = "applied"`.
  - **corroborated** tier → propose (dead-letter), `action = "proposed"`, proposal names the
    matched lead and `--to applied`.
  - **none** → skip.
- Evidence: `append_body_section(note.ref, tag, "## Application receipt ...")` recording sender,
  subject, date, matched domain, and tier. Tag keyed by `message_id` so a re-receipt is idempotent
  (never-clobber append; never overwrites body).

### `sluice/core/status.py`

- Add `can_transition(current, target)`: if `normalize(target) == "applied"` dispatch to
  `can_apply(current)`; else dispatch to `can_advance(current, target)`. This keeps the ladder logic
  in `status.py` (its owner) and gives `reconcile` and `confirm` one shared entry point.
- **Rationale:** `shortlist → applied` is the `can_apply` transition (shortlist is triage-owned,
  applied is application-owned). Every existing reconcile branch and `confirm()` use `can_advance`,
  which requires *both* ends application-owned and would therefore wrongly **reject**
  `shortlist → applied`. `can_transition` routes the `applied` target correctly without weakening
  either predicate.

### `sluice/track/engine.py`

- `run()` additionally loads `shortlist_leads = vault.read_leads({"shortlist"})`, kept **out of the
  LLM prompt** (the classify prompt stays in-flight-only; the deterministic matcher receives the
  shortlist set). Pass the shortlist set into `reconcile` for the receipt branch.
- `_PROPOSE_TARGET["receipt"] = "applied"` so a proposed (corroborated) receipt emits a
  `sluice track confirm --lead "<slug>" --to applied` hint.
- Intra-run never-regress reflection: when a receipt advances a shortlist lead, reflect the new
  `applied` status back into the shortlist snapshot so a second receipt for the same lead in one run
  reconciles against current state.
- `confirm()` uses `can_transition` (so `--to applied` is legal from `shortlist`).

### `sluice/track/config.py` + `sluice.yaml.example`

- **Consume** `ats_relay_domains` (defined today, used nowhere — this feature is its first consumer).
- Add `auto_apply_min: float = 0.75` — the LLM's receipt-classification confidence floor for a
  proof-grade auto-advance. Corroborated tier always proposes regardless of confidence. Document
  both in `sluice.yaml.example`.

## Invariants upheld

- **Never-regress (status).** `can_apply` returns True *only* for `shortlist`. A receipt against an
  `interview`, `offer`, `applied`, or terminal lead is refused — it can never pull a lead backward,
  and is idempotent on an already-`applied` lead. `confirm --to applied` is gated by the same gate.
- **Never-clobber (writes).** Status via surgical `update_fields` (named keys only); evidence via
  `append_body_section` (append, never overwrite a human decision or the note body).
- **Empty config abstains.** An empty `ats_relay_domains` disables only the corroborated tier; the
  proof tier still works. No new preference gate; `auto_apply_min` is a confidence floor, not a
  preference filter.
- **Standard-library only.** `urllib.parse` for host extraction; no new dependency.
- **Pure/impure split.** `match_receipt` is pure (no I/O), like the parser layer, so it is tested
  offline against synthetic messages.

## Testing (synthetic fixtures, offline; mirrors `test_track_reconcile.py` / `test_core_status_apply.py`)

The three from #10:

1. A confirmation receipt (proof-grade domain match) advances a matching `shortlist` lead to
   `applied`, with evidence recorded.
2. A receipt from an unrelated service that merely mentions the company name (domain mismatch) does
   **not** match.
3. A receipt cannot regress a lead already at a later stage (`interview` stays `interview`).

Plus edges:

4. ATS-relay domain + company-in-body → **proposed** (corroborated tier), not auto-applied.
5. ATS-relay domain alone, no company corroboration → **no match**.
6. Idempotent re-receipt: a second identical receipt does not double-write (already `applied`,
   `can_apply` false; evidence tag dedups).
7. `confirm --to applied` succeeds off a proposed receipt; `confirm --to applied` on a non-shortlist
   lead is refused with the current status as the reason.
8. A pure `match_receipt` unit table over {proof, corroborated, none} inputs, including a
   `careers.<host>` vs `<host>` case (subdomain-of-registrable proof) and a shared ATS-parent
   host (never proof).

**Fixture neutrality.** Company domains in fixtures use the RFC-reserved `example.com` /
`example.invalid` family (guaranteed unregistrable, so no invented domain can collide with a real
firm — a local roster cannot tell whether an invented name is real). The ATS relay hosts
(`greenhouse.io`, `lever.co`, …) may be named directly: they are real ATSs already present in the
shipped `ats_relay_domains` config, not personal data.

Mutation witness: in the receipt reconcile branch, swapping `can_apply`/`can_transition` for
`can_advance` must redden a named test (it would reject `shortlist → applied`); run the test by node
id and confirm it fails, and confirm no pre-existing test is what catches it.

## Scope

~1 new pure module (`receipt.py`) + focused edits to `classify.py`, `reconcile.py`, `engine.py`,
`status.py`, `config.py`, `sluice.yaml.example`. No new dependency, no seam change, no new CLI
command. Neutrality: synthetic fixtures only — no real employer names or domains; company hosts use
the RFC-reserved `example.com` / `example.invalid` family (real ATS relay hosts may be named, as
they already ship in `ats_relay_domains`).
