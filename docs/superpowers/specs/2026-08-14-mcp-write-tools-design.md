# MCP write-capable tools — the second slice over `sluice/mcpserver.py` (#131)

## Problem

#105 shipped four read-only MCP tools (`list_leads`/`get_lead`/`doctor`/`health`) over
`sluice/mcpserver.py`. Its own design spec deliberately deferred every write-capable
tool "until this slice ships and until the write-path routing rule below is proven out
in review." #105 shipped and merged 2026-08-14 (PR #126). The same day, the repo owner
filed #131, scoping the next slice from one real day-in-the-life session driving the
pipeline: without write tools, every write still happens as a hand-rolled `ssh <host>
'sudo docker exec <host> gosu <user> ... python3 -c "..."'` one-liner reaching into
`Vault`/`Sluice` internals directly, or a CLI shell-out one subcommand at a time. The
raw-Python path is strictly worse than a proper tool: it bypasses every CAS guard the
API already has by construction, since it re-implements the call inline instead of
using it.

The issue lists five named, guarded operations, each used multiple times in that one
real session — `dismiss_lead` (~8x, the single most common write), `apply_record`
(5x), `cv_run`/`cv_signoff` (several times, including one real needs-signoff hold), and
`create_lead` (2x, for leads a human found directly that no scanner had ingested). It
explicitly argues against a generic `update_lead(fields)` setter: that would fight the
codebase's CAS-guarded write philosophy. `track run`/`confirm`/`dismiss` is named but
explicitly deferred, matching #105's own deferred list.

## The settled decisions

1. **Write tools are gated behind `job-sluice mcp serve --write`, off by default.**
   `build_server(config, write=False)` registers the four read tools always, the five
   write tools only under `--write`. Every existing `claude mcp add job-sluice --
   job-sluice mcp serve` registration — including `README.md`'s own snippet — stays
   read-only across this upgrade; nobody silently gains write powers by updating. A
   read-only server's `tools/list` genuinely doesn't advertise the write tools either,
   shrinking what an agent steered by prompt-injected content it just read through
   `get_lead` could even attempt to call — refusing at call time would leave the tool
   name and schema in the model's context regardless, the exact surface `content_warning`
   (#130) exists to shrink. `--write` is a flag on `serve`, not a config key: it's a
   per-registration trust decision about one client, not a property of the install.

2. **`sluice/mcpserver.py` contains zero store writes.** Every write tool is a plain
   top-level function taking `sluice: Sluice` as its first parameter (unit-testable
   with no MCP machinery, matching #105's existing four tools) that validates input,
   calls exactly one `Sluice` method, and translates the result into the shared
   vocabulary (decision 15) — never `sluice.store().update_fields(...)` directly.
   Three of the five operations already have a `Sluice` facade method (`record`,
   `compose_cv`, `sign_off_cv`); two new ones land: `Sluice.dismiss_lead()` and
   `Sluice.create_lead()` (`Vault.upsert` is not exposed on `Sluice` at all today).
   Building either as an MCP-only function would guarantee a CLI fork of it later, and
   would make the MCP server the sole owner of a write path — precisely the "second
   write path" the never-clobber/never-regress invariants forbid.

   `cv_run` calls `Sluice.compose_cv` **only** — never `sluice.cv.engine`,
   `sluice.cv.render`, or `sluice.core.vault` directly. The entire fabrication gate
   (`skipped-selection` → the #60 `skipped-needs-signoff` latch → `skipped-stale` →
   `skipped-config` → the bounded retry loop → `skipped-gate`) lives inside
   `cv/engine.py:run_one`, reachable only via `Sluice.compose_cv`; no argument on that
   method disables it. This is enforced structurally by an AST import sweep on
   `sluice/mcpserver.py` (mirroring the existing sweep that proves `mcp` is imported
   nowhere outside `build_server()`), mutation-tested by adding a stray
   `from sluice.cv.engine import run_one` and confirming the sweep — not some other
   test — goes red.

3. **The parameter is `lead`, never `lead_ref`** (a rename of the issue's proposed
   signature). #105 already settled that the store's opaque `ref` handle never crosses
   the MCP boundary in either direction — every lookup re-resolves by string, exactly
   like the CLI's `--lead` always has. A parameter named `lead_ref` invites an agent to
   pass a `ref` it is never given, and would be the only parameter on the whole surface
   whose name disagrees with `get_lead(lead=...)`. One name for one concept across
   nine tools: the agent that just called `get_lead(lead="acme analyst")` types the
   same string into `dismiss_lead(lead="acme analyst")`.

4. **`dismiss_lead` resolves by EXACT slug equality; the three wrapped CLI operations
   (`apply_record`, `cv_run`, `cv_signoff`) inherit their CLI's substring matcher
   unchanged.** One principle, two consequences: a genuinely new operation picks the
   safer matcher; a wrapped operation never forks the matcher its CLI twin already
   uses.

   `dismiss_lead` has no CLI precedent to inherit from — no existing command lets a
   human dismiss a lead by hand today (`leads expire --expire <slug>` is the closest
   analog, and it deliberately uses EXACT equality, with its own code comment stating
   why: "a user typing the narrow form is choosing the safer option; it must not be
   the one that dismisses leads they did not name"). That argument applies with *more*
   force over MCP: the caller is an LLM whose `lead` string may derive from
   attacker-influenced company/role text it just read through `get_lead`, and a
   substring match multiplies the blast radius of a mis-derived fragment. The intended
   flow costs the agent nothing extra: `get_lead("acme analyst")` (substring,
   read-only, returns the exact `slug`) → `dismiss_lead(lead=<that slug>)` (exact,
   write). A slug collision from the recursive scan (#1) still refuses rather than
   picks, resolved via the shared `index_by_slug` verdict every other multi-writer
   consumer already uses.

   `apply_record`'s `record_one` and `cv_signoff`'s `sign_off_cv` both already refuse
   outright on more than one match — substring only *widens the pool a refusal
   considers*, it never picks one. Forking the matcher for these two would mean a
   fragment that resolves correctly on the CLI silently fails over MCP, which is worse
   than either matcher alone. `cv_signoff` also keeps `sign_off_cv`'s deliberately WIDE
   `TRIAGE_OWNED` resolution scope (wider than `cv_run`'s shortlist-only) — narrowing
   it would strand a held CV on a lead that got re-triaged to `dismiss`/`research`,
   exactly the case `sign_off_cv`'s own comment names.

5. **`dismiss_lead` does not expose a free-form `note_tag`** — a deliberate departure
   from the issue's literal signature. `note_tag` is `update_fields`'s idempotency key:
   an appended note is skipped when the tag is already a substring of
   `relevance_notes`. Exposing it as a free string lets an agent pass a tag from a
   *different* sub-app's convention — say, reusing `"[triage 2026-08-14]"` — which
   would silently suppress that day's real triage audit note on a later, unrelated
   `triage run` call: a cross-sub-app silent-note-loss failure, not a hypothetical one.
   `dismiss_lead` computes `f"[dismiss {date.today().isoformat()}]"` itself, matching
   the established `[triage <date>]`/`[expire <date>]` convention. The accepted cost,
   stated rather than hidden: two dismissals of the same lead on the same day record
   only the first reason (the second note is suppressed by its own tag) — the response
   reports `note_appended: bool` so this is visible, not silent.

   **`note_appended` is `True` only when the write actually committed AND the
   pre-write snapshot showed the tag absent — neither signal alone is sufficient.**
   `/review-plan`'s round-2 invariant reviewer caught that a plain post-write check
   (`the tag is present in the fresh relevance_notes`) is `True` in both the
   real-append case and the already-present case, so it can't distinguish them —
   fixed by deriving the PREDICTION from the pre-write snapshot instead, matching
   `url_dropped`/`ats_dropped`'s established input-derived shape. Round 3 caught a
   real bug in THAT fix: under genuine concurrent dismissal (Testing item 12a), the
   losing thread's pre-write snapshot correctly shows the tag absent (nobody has
   written it yet when it reads), but its own `update_fields` call then loses the
   CAS race and commits nothing — reporting `outcome: "unchanged"` alongside a
   pre-write-derived `note_appended: true`, a self-contradictory response (claiming
   to have appended a note that, per the outcome, it didn't). The pre-write snapshot
   alone predicts INTENT, not what actually happened. Fixed with a composite:
   `note_appended = (tag not in snapshot_notes) and wrote` — the snapshot check
   gates out the "already present, no append was ever going to happen" case exactly
   as before (this part doesn't need the write's fresh outcome, since a genuinely
   already-tagged snapshot means no append could ever have been attempted), and the
   `wrote` bool from `update_fields`'s own return gates out the "predicted an
   append, but this call's write didn't actually commit" case the race exposes.
   Testing item 12a asserts `note_appended` explicitly for both the winning and
   losing thread in every round, not only `outcome`, since this composite is exactly
   the kind of check that reads as correct without being exercised under real
   concurrency.

6. **`dismiss_lead`'s guards are both CAS-fresh, evaluated inside the transform —
   `_DISMISSABLE_FROM` is its OWN constant, not a rename of `_EXPIRABLE`.**
   `require_status=_DISMISSABLE_FROM = frozenset(_status.TRIAGE_OWNED)` — the FULL
   triage-owned set, `"dismiss"` included. `/review-plan`'s invariant reviewer caught a
   real bug in an earlier draft that reused `_EXPIRABLE`
   (`frozenset(TRIAGE_OWNED) - {"dismiss"}`) verbatim: that exclusion is safe for
   `expire()` *only* because `expire_report()` already filters out already-dismissed
   leads before attempting the write. `dismiss_lead` has no such pre-filter — it
   resolves one named lead directly, at whatever status it's currently at — so
   excluding `"dismiss"` from its own required set would make a same-day re-dismiss hit
   a hard CAS *refusal* instead of the `unchanged` outcome decision 5's whole
   note-tag-idempotency rationale depends on. `_EXPIRABLE` stays exactly as-is, used
   only by `expire()`; `_DISMISSABLE_FROM` is a second, independently-derived constant
   for `dismiss_lead`'s different precondition. Both stay *derived* from
   `TRIAGE_OWNED`, not hand-listed, so neither can be edited into naming an
   application-owned state — that property, not which elements are excluded, is what
   actually holds never-regress; including `"dismiss"` costs it nothing, since
   `dismiss → dismiss` is a legitimate no-op transition, never a regression.

   `require_blank=frozenset({"pending_cv"})` refuses a lead holding an unsigned
   composed CV, re-read fresh inside the same transform rather than from a stale
   snapshot the way `leads expire`'s equivalent refusal is decided today — the first
   sign-off-hold refusal in this codebase to actually be CAS-fresh, using the
   `require_blank` primitive #109 built for exactly this shape. The refusal names its
   remedy (`cv_signoff(lead=..., discard=true)`), which is on this same tool surface,
   so the agent never leaves it.

7. **`Vault._render_new` gets a `frontmatter_safe` guard on all seven interpolated
   fields, fixed at the source — protecting the live ingest/scraper path too, not
   only `create_lead`.** Verified directly: `_render_new` (`sluice/core/vault.py`)
   interpolates `company`/`role`/`location`/`salary`/`role_type`/`url`/`source` into
   double-quoted frontmatter scalars with no guard call anywhere — unlike every
   `update_fields` caller (`apply/record.py`, `track/reconcile.py`,
   `triage/resolve.py`), which all guard untrusted strings before writing. This is a
   live gap today, not one `create_lead` introduces: `ingest/base.py`'s
   `(row.get(...) or "").strip()` removes edge whitespace but not an *embedded*
   newline, so a hostile job posting can already forge a frontmatter key through a
   scraped `location`/`company` field. Per the standing project rule (address real
   findings as they're found, don't defer), this is fixed here rather than filed
   separately.

   Abstain-and-log per field, never raise: `_render_new` builds a whole note in one
   call with no per-field channel to report through the way `update_fields`'s callers
   have (`url_dropped`), and `Lead.__post_init__`'s own discipline is "coerce, never
   raise" — an exception here would abort the whole ingest-sink loop for one malformed
   scraped row, which this codebase's per-item isolation discipline (one bad lead must
   not sink the batch) forbids. `create_lead` does its own validation up front and
   raises (decision 9) — it never relies on this fallback; this is pure
   defense-in-depth for the pre-existing scraper path.

   **`company`/`role` are the two exceptions: an unsafe value there refuses the
   whole create, never abstains-and-blanks — but "unsafe" means something NARROWER
   for these two fields than `frontmatter_safe()`'s general definition.** Round-2
   `/review-plan` review caught that uniform abstain-and-blank is wrong specifically
   for these two, because they ARE the vault's identity key (`_candidate_names`'s
   `stem = f"{company} - {title}"`) — blanking one doesn't just drop a field the way
   blanking `location` does, it silently changes which note the posting maps to, and
   a later legitimate re-scrape of the same real job (with its real company name
   intact) would then create a SECOND, disconnected note instead of matching the
   first.

   Round-3 review caught two real problems with the FIRST version of this fix,
   verified directly against source and an existing test. First: `upsert`'s
   blank-identity gate is OR-satisfied (refuses only when BOTH company and role are
   blank), so it cannot catch — and this design's first draft never actually routed
   through — the single-field-unsafe case that motivates this decision in the first
   place (company forged, role legitimate). This is a genuinely NEW refusal
   condition, not a reuse of the existing gate's OR logic: `upsert` refuses when
   EITHER `company` or `role` individually fails the new check, before calling
   `_render_new` at all. Second, and more serious: reusing `frontmatter_safe()`
   verbatim — which rejects ANY embedded `"`/`\` — directly conflicts with an
   EXISTING, deliberately-designed, already-pinned test,
   `test_upsert_still_creates_a_lead_whose_field_merely_CONTAINS_quotes`
   (`tests/test_vault.py:495`), whose own docstring states the governing principle
   for this exact code path: "only a value that parses back EMPTY may be refused —
   widening the gate from 'blank' to 'contains a quote' would bin it." Sluice's own
   line-based `_fm_dict`/`_fm_value` reader (not a strict YAML parser) already
   tolerates an embedded quote in `company`/`role` today, unguarded, and that
   tolerance is product behavior this design must not regress.

   The actual threat this decision exists to close — `ingest/base.py`'s
   `.strip()` leaving an embedded NEWLINE intact, forging a new frontmatter key one
   line down — is a DIFFERENT hazard than a bare embedded quote, and the fix is
   scoped to exactly that: `company`/`role` are checked against `frontmatter_safe`'s
   own "not printable" sub-rule ONLY (which already rejects the C0/C1 control class,
   including `\n`, per its own docstring) — deliberately SKIPPING its separate
   `"`/`\` structural-character rule, which is what the pinned test requires. `url`,
   `ats`, and the other four `create_lead`/`_render_new` fields keep
   `frontmatter_safe()`'s full definition unchanged, since none of them has an
   equivalent existing test tolerating embedded quotes. Still per-item isolated,
   never an exception, never sinking the ingest batch.

8. **`apply/record.py:record()` is hardened: `ats` gets `frontmatter_safe()` +
   quoting, mirroring `url`'s existing #111 fix exactly; the write gets
   `require_status=frozenset({"shortlist"})`.** Verified directly: `record()` writes
   `"ats": resolved_ats` completely unquoted and unguarded — unlike the sibling `url`
   field beside it, which #111 already guards, drops when unsafe, and flags via
   `url_dropped`. `resolved_ats` defaults to `listing_host(<the lead's own scraped
   url>)` even when nobody passes `--ats`, so this is already reachable from scraped
   data today, not only from a human-typed flag. Over MCP `ats` becomes agent-supplied
   for the first time. Fix mirrors `url`'s shape exactly: `safe_ats =
   frontmatter_safe(resolved_ats)`; unsafe → dropped, `ats_dropped: True`, the prior
   value on disk left untouched (never-clobber); `cmd_apply_record` gets a matching
   `ats_dropped` stderr line beside the existing `url_dropped` one.

   Separately verified: `vault.update_fields(note.ref, literals)` is called with **no
   `require_status=`** — the guard is `can_apply(note.status)`, checked once against an
   in-memory snapshot before the call. This codebase's own established language calls
   that pattern "byte-identical to no guard at all." A lead that leaves `shortlist`
   between `record_one`'s read and the write gets clobbered back to `applied` with a
   fresh `applied_date` — a reachable never-regress violation today, and materially
   more reachable once `apply_record` lives inside a long-lived MCP process instead of
   a one-shot CLI invocation. `require_status=frozenset({"shortlist"})` closes it,
   mirroring the identical fix `triage/apply.py` already took for its own snapshot
   gap. `wrote is False` on this path is unambiguous (reaching `update_fields` at all
   means the snapshot said `shortlist`) and maps to a new `{"ok": False, "reason":
   "raced"}` — `cmd_apply_record` needs no rc-mapping change (every `ok: False`
   already routes to rc 1), only a corrected message string.

9. **Input validation: raise `ValueError` naming every bad field when the field IS the
   payload or the identity; abstain-and-flag when it's provenance decorating an
   already-legitimate transition.** `dismiss_lead`'s `reason` (blank, or not
   `frontmatter_safe`) and every one of `create_lead`'s seven fields raise, naming the
   full offending set — matching `list_leads`'s existing "name the full bad set, never
   silently return empty" convention. Dropping a dismissal's reasoning erases the
   entire point of the call; dropping `company`/`title` changes which note gets
   created or whether the blank-identity gate refuses outright. `apply_record`'s `url`
   and `ats` abstain-and-flag instead (decision 8) — refusing to record a
   genuinely-submitted application over one stray quote character in the ATS name
   would be the worse failure. Validation lives on the `Sluice` methods, not in
   `mcpserver.py`, so any future CLI surface inherits it for free.

10. **`create_lead` reports `Vault.upsert`'s six-member outcome vocabulary verbatim,
    never a bare "created."** Two leads sharing company+title (even with different
    URLs — the URL is not part of vault identity, `Vault._candidate_names`'
    `stem = f"{company} - {title}"` is) resolve to the same candidate note name; the
    second `create_lead` call silently returns `Vault._reconcile`'s verdict-driven
    outcome — `"updated"` when the incoming url (or, absent a url match, the
    location) proves the same posting, `"merged"` when neither does (inconclusive
    evidence, e.g. two blank locations and no matching url; traced through
    `core.leads.same_opportunity` returning `UNKNOWN`) — either way a bare
    `last_seen` bump, with the incoming url/salary/location **not recorded**. Both
    outcomes are real and reachable, not just the more common `"updated"`. The
    response's `detail` field says so explicitly rather than smoothing it into
    generic success — surfacing this collision trap is the single most valuable
    thing `create_lead`'s response can do, since it's the most likely way this tool
    surprises a caller.
    `merged_away` vs `merged_away_unproven` stay distinct (#81's own reason: only the
    proven one may ever enter a dedup store). `refused`/`merged_away*` return no
    `slug` — nothing was written. Slug resolution is a post-write re-read matched on
    `fm["company"] == company and fm["role"] == title` — the store's own identity key
    — never on `url` (an `"updated"` outcome means the incoming url was *not*
    written, so matching on it would silently report a false failure).

11. **`create_lead` does not touch `seen.db`.** `VaultSink` records `created`/
    `updated`/`merged`/`merged_away` into the ingest dedup store; `seen.db` has no
    removal path. A manually created lead joining it means a later genuine scrape of
    that same posting is silently skipped before it ever reaches the sink,
    `last_seen` never bumps from the real scraper's perspective, and the hand-created
    lead goes stale and gets expired out from under the person who just added it. The
    manual path writes the note only.

12. **`create_lead` takes no `search` parameter (silently dropped from the issue's
    proposed signature) and validates `url` as `http(s)`.** `Lead.search` is required
    by the dataclass but is never persisted anywhere: `_render_new` doesn't write it,
    and no reader of `lead.search` exists outside `_row_to_lead`'s own construction —
    verified by grep across `sluice/`. A parameter for a field that goes nowhere would
    be a lying client-facing schema entry; provenance is already carried by `source`,
    which *is* persisted. `Sluice.create_lead` passes `search=""` internally, with a
    comment recording the verification. `url` is additionally required to be
    `http://`/`https://` (matching `apply/select.eligibility`'s own rule), so a
    hand-created lead is apply-eligible by construction rather than failing later with
    a bare `no_url`.

    **`url` is required, no default, at BOTH the tool signature and the
    `Sluice.create_lead` facade** — `/review-plan` caught an earlier draft giving it a
    `= ""` default on the facade only, silently disagreeing with the tool layer and
    with the http(s) validation this same decision just mandated (defaulting a field
    to a value guaranteed to fail its own validator is pointless, and inconsistent
    defaults across the two layers is exactly the kind of drift this document is
    otherwise careful to avoid). `location` stays optional (`= ""`) at BOTH layers
    too, deliberately the opposite call: an unknown or intentionally blank location is
    real, valid data (some postings genuinely don't name one), not an error condition
    — so both layers agree it may be omitted, and both layers agree `url` may not.

    Field-name translation — `title` → frontmatter `role`, `job_type`
    → `role_type` — happens inside `Sluice.create_lead`/`Vault`; the tool's own
    parameters match the issue's names exactly (and `Lead`'s field names), because the
    tool's job is to construct a `Lead`, not speak one store's frontmatter dialect. The
    docstring states the mapping so an agent reading the lead back via `get_lead`
    isn't surprised its `fm` says `role` where it passed `title`.

13. **`cv_signoff` exposes both directions — discard directly, promote via a two-call
    confirmation token.** This is the sharpest tension in the issue, and the one
    decision that needed the repo owner's own judgment rather than mine — confirmed
    directly with them (2026-08-14): promotion ships, via the confirmation-token
    mechanism below, not CLI-only.

    `cv_signoff(lead, discard=True)` behaves exactly like the CLI's `--discard` path
    today — no latch concern, since discard only clears the hold and frees a fresh
    compose; it never promotes anything.

    `cv_signoff(lead, discard=False)` with no `confirm_token` **writes nothing**: it
    resolves the lead, reads the fresh `pending_cv` + flagged claims, and returns
    `{"outcome": "needs_confirmation", "slug", "pending_cv", "claims": [...],
    "confirm_token": <a hash of the canonical (slug, pending_cv, claims) tuple>,
    "content_warning", "detail": "NOTHING was written. Relay these claims to a human,
    get explicit approval, then call again with confirm_token to promote."}`. A
    second call passing that token back promotes **only if it still matches the
    freshly re-read claims** — a token issued against a claims-set that has since
    changed (a re-compose interleaved) returns `stale_confirmation` with a fresh
    token, having written nothing.

    Implementation reuses `Sluice.sign_off_cv`'s existing `confirm` callback as a
    **capture**, called once, after resolution and before the write — no second
    resolution, no peek/execute divergence that could resolve a different lead than
    the one it acted on. `Vault.sign_off` gains `require_pending: str | None = None`,
    compared against the FRESH `pending_cv` **inside the CAS transform**, not the
    snapshot the callback saw — without this the token would validate against stale
    bytes, which is the exact "byte-identical to no guard" failure this whole
    mechanism exists to avoid. A mismatch returns a new `"stale"` outcome, joining
    `Store.sign_off`'s documented vocabulary, its conformance test, and
    `cmd_cv_signoff`'s `_FAILED` set (nothing was signed off; exiting 0 would tell a
    caller the CV is send-ready when it is not).

    Stated honestly, not oversold: this does not prove a human saw the claims — the
    calling agent can see the token and could technically call back-to-back in one
    turn. What it guarantees is that promotion requires a second, separately-surfaced
    tool call bound to the exact claims text at the moment of promotion, which
    eliminates the realistic accident this design is actually worried about (a
    careless or default-driven single call silently promoting an unreviewed CV)
    without claiming a stronger property the local stdio transport cannot actually
    provide. `cv_signoff --lead` resolution stays scoped to all of `TRIAGE_OWNED`
    (decision 4).

14. **`cv_run` exposes only `lead` and `backend`.** Every other CLI flag is omitted for
    a stated reason, not by oversight: `all_shortlist` would let one tool call trigger
    unbounded LLM spend and browser fetches; `dry_run` is a trap rather than a cheap
    preview — `compose_cv`'s own docstring says a dry run still spends a full compose
    *and* audit per lead, it only skips the render and the write; `include_stale`
    silently overrides the #9 staleness gate, and that is not a decision this tool may
    make on its own authority; `no_serve`/`limit` have no meaning for a single-lead
    call. `backend` passes straight through to `compose_cv(backend_role=...)` with no
    duplicate CHOICE-SET validation in `mcpserver.py` — `Sluice.backend` already
    raises `BackendError` naming the full valid set at the point of use, and a second
    copy of that set in `mcpserver.py` would be a second drift site the
    shared-constant discipline elsewhere in this module exists to avoid. `compose_cv`
    itself catches that `BackendError` and re-raises it as `ValueError`, so an invalid
    `backend` joins the Error Handling section's single malformed-input contract
    rather than leaking a second exception type — that is a translation, not a
    second copy of the choice set, and it lives at the `Sluice` layer (mirroring
    `dismiss_lead`'s own `reason` validation) rather than in `mcpserver.py`, whose
    isolation sweep forbids importing `BackendError` directly. The composed CV text itself
    is never returned in the response (only `violations`/`audit_flags`/`served`/
    `dossier_failed`, matching what `cmd_cv_run` already prints) — it's an LLM
    document derived from an attacker-controlled JD, and echoing the whole thing back
    into the agent's own context is a large, unnecessary step past what the response
    needs to convey.

15. **Shared resolution vocabulary across all five write tools, including a real
    `out_of_scope` outcome — never a lying `not_found`.** `{"outcome": "not_found"}`
    and `{"outcome": "ambiguous", "candidates": [...]}` match `get_lead` exactly
    everywhere — `candidates` is always a sorted list of slugs, never
    `select_one`/`sign_off_cv`'s existing `" | "`-joined ref string (a CLI
    presentation artifact that an MCP client would eventually have to parse back into
    data, incorrectly, the moment a ref itself contained that substring).

    `/review-plan`'s architect reviewer caught that this is unbacked for `cv_signoff`
    as first drafted: `Sluice.sign_off_cv` (verified directly) returns *only* the
    joined-string form for its ambiguous case — there is no slug list anywhere in its
    existing return shape to pass through. Re-resolving the lead a second time inside
    `mcpserver.py` to produce one would be the exact peek/execute divergence decision
    13 argues against elsewhere in this document (a second resolution pass can
    legitimately disagree with the first if the vault changed in between). The actual
    fix is at the source: `Sluice.sign_off_cv`'s ambiguous branch is WIDENED to also
    return the candidate slug list it already has in hand internally (it already knows
    every matching note before it joins their refs into the printed string).

    **This is a real, small, stated change to `cmd_cv_signoff`, not a hidden no-op.**
    A round-2 verification against the actual source caught the first draft's claim
    that the CLI's own line "keeps using the joined string unchanged" as false:
    `sign_off_cv` today returns a bare 2-tuple, and `cmd_cv_signoff` consumes it with
    a strict `slug, outcome = result` unpack — any arity change breaks that line
    regardless of which branch grows the new field. The fix, matching the
    `DismissResult`/`CreateLeadResult` dataclass-report idiom this document already
    uses for the two brand-new methods: `sign_off_cv` returns a small
    `SignOffResult(slug: str, outcome: str, candidates: list = field(default_factory=
    list))` dataclass instead of a tuple. `cmd_cv_signoff`'s one unpack line becomes
    `result = sluice.sign_off_cv(...)` plus attribute access (`result.slug`,
    `result.outcome`) — its printed joined-string line is built from `result.slug`
    exactly as before, unaffected in behavior, just no longer tuple-shaped. `cv_signoff`
    (MCP) reads `result.candidates` directly from the SAME resolution `sign_off_cv`
    already performed. One resolution pass, two consumers, no drift between what the
    CLI prints and what the MCP tool returns — and the one line in `cli.py` this
    touches is named explicitly rather than assumed away.

    `out_of_scope` closes a concretely-live honesty gap this slice would otherwise
    silently inherit: `apply/select.resolve` and `Sluice.compose_cv` both scope to
    `read_leads({"shortlist"})` only, so a lead that plainly exists but is `applied`
    reports as `no_match`/`[]` today — indistinguishable from "never existed."
    Chained with `create_lead` living on the same surface, that's a real, walkable
    failure path: `apply_record` reports not-found → the agent concludes the lead was
    lost → `create_lead` → a silent `"updated"` no-op (decision 10) → the real
    application never gets recorded. The fix is one shared helper: on the underlying
    operation's own no-match path, re-resolve over *every* status with the operation's
    own matcher; exactly one hit → `{"outcome": "out_of_scope", "slug", "status",
    "detail"}` naming the fresh status and what scope the tool actually accepts. This
    is a pure re-read — it authorizes nothing and decides nothing the underlying
    operation didn't already decide.

    The helper's home, named explicitly per `/review-plan`'s architect feedback (this
    document is otherwise explicit about where every other shared, drift-prone piece
    of logic lives — `_NEVER_AN_INSTRUCTION`, `index_by_slug`, `slug_matches`): a new
    pure, store-agnostic function in `sluice/core/leads.py`, beside those two,
    `def out_of_scope_verdict(notes: list, wanted: str, *, matcher, accepted:
    frozenset) -> dict | None`. `/review-plan`'s round-2 generalist reviewer caught
    that the first draft's signature had nowhere to put the query string —
    `slug_matches`'s own real signature is `(note, wanted: str) -> bool`, so a matcher
    callable with no `wanted` parameter to close over could not actually invoke it as
    described. Fixed by threading `wanted` through explicitly: `matcher` is
    `Callable[[note, str], bool]`, called as `matcher(note, wanted)` for each of the
    already-fetched candidate `notes` (never re-fetched, so it cannot diverge from
    whatever resolution the caller already did) that falls outside `accepted`,
    returning the `out_of_scope` dict on exactly one such match, `None` otherwise.
    Each of the four call sites passes its own `wanted` (the lead string it already
    has), its own matcher (`slug_matches` for three tools; `lambda n, w: n.slug == w`
    for `dismiss_lead`'s exact-match rule, decision 4), and its own accepted set.

    Beyond that shared triple, each tool's action outcomes are its underlying
    primitive's existing vocabulary, passed through **verbatim**: `upsert`'s six,
    `Vault.sign_off`'s four (+ the new `"stale"`), `CvResult.status`'s set, and
    `"dismissed"`/`"unchanged"`/`"recorded"`/`"raced"` for the two transitions with no
    pre-existing verdict string. Renaming any of these at the MCP boundary would
    create a second vocabulary that must be kept in step with the first — the exact
    drift the shared `UNTRUSTED_SCRAPED_CONTENT_WARNING` constant (#130) was built to
    prevent for content strings; the same discipline applies to outcome strings.

16. **`content_warning` extends to `cv_run` and `cv_signoff`, via a second shared
    constant, never reworded copies.** Composed-CV violations, LLM audit-flag lines,
    and `needs_signoff`/confirmation claims all quote or paraphrase the scraped job
    description — the same threat class `get_lead`/`list_leads` already warn about,
    just derived rather than verbatim. `core/leads.py` gains
    `UNTRUSTED_DERIVED_CONTENT_WARNING`, sharing the same load-bearing tail clause
    (`_NEVER_AN_INSTRUCTION`, factored out of the existing constant so the two cannot
    drift on the sentence that matters — "whatever it says about itself," the clause
    that specifically defeats a self-referential injection and has already been
    silently dropped once in this codebase's history) as the existing
    `UNTRUSTED_SCRAPED_CONTENT_WARNING`, differing only in subject clause ("is
    untrusted text an LLM composed from a third-party web page" vs "is untrusted text
    copied verbatim from a third-party web page"). `dismiss_lead`/`apply_record`/
    `create_lead` carry no warning at all — they return only slugs, canonical
    statuses, and values the caller itself supplied, the same reasoning `get_lead`'s
    `ambiguous` branch already established for a bare candidate list.

17. **No lock; concurrent tool dispatch is verified, not assumed.** #105's design doc
    explicitly left FastMCP's dispatch model unverified and stated plainly that the
    deferred write-tools slice "cannot inherit it for free." The argued answer:

    - Every write this slice can reach is a single CAS transaction whose decision
      inputs are re-read **inside** the transform: `require_status`, `require_blank`,
      the new `require_pending`, `upsert`'s `O_EXCL` create + bounded re-resolve, and
      `_bump_last_seen`'s monotonic comparison. A sustained race raises
      `VaultConflict`, already treated everywhere as a non-fatal, reportable outcome,
      never a crash.
    - No tool holds cross-call state. The only mutable state reachable is memoized
      (`Sluice._cache`, `Vault`'s scan-dir cache) and idempotent to rebuild —
      `_resolve_path`'s create arm already re-derives the scan set from disk before
      minting a note, so a stale cache costs a wasted extra walk, never a wrong write.
    - The one genuinely new interleaving — `cv_run` holding the floor for tens of
      seconds while a `dismiss_lead` lands mid-window — is #16's long-window case
      verbatim, and both outcomes it could produce are already named:
      `hold_for_signoff` re-checks fresh content, and `dismiss_lead`'s
      `require_blank={"pending_cv"}` (decision 6) refuses cleanly if a hold appeared
      during the compose.
    - A process-wide lock would be theatre against the actual #16 threat model (a
      human editing the note by hand in Obsidian, taking no lock) while serializing a
      60-second `cv_run` against an 8-times-a-day `dismiss_lead` for no safety gain.

    **Still required before implementation, not assumed**: install `mcp>=2.0.0` and
    determine, empirically, whether `MCPServer` dispatches a sync tool function inline
    on the event loop or hands it to a worker thread — an ergonomics fact either way
    (if inline, a long `cv_run` blocks other tool calls; if threaded, it doesn't), not
    a safety one given the argument above, and it replaces #105's open caveat in
    `build_server`'s own docstring rather than leaving it stale.

    **The concurrency test exercises REAL interleaving via many rounds of REAL
    concurrent execution — not a synthetic double, and not a hope that
    `asyncio.gather` happens to interleave.** Two rounds of `/review-plan` review
    converged on the same conclusion from different angles: a naive `asyncio.gather`
    of two `call_tool("dismiss_lead")` coroutines proves nothing if `MCPServer`
    dispatches sync tools inline on the event loop (test-engineer, round 1) — with
    zero real interleaving, "exactly one `dismissed`, one `unchanged`" would hold
    under pure sequential execution too. The round-2 attempted fix (a test double
    blocking `Vault.update_fields` on a `threading.Event` mid-transform) turned out
    to have no real seam to hook into: round-2 test-engineer review verified directly
    that `update_fields` is a thin, synchronous trampoline into the private
    `_cas_write`, which runs read→transform→staleness-check→commit as one call with
    the guard logic living in a closure local to `update_fields` itself. A double
    could only patch an unnamed private function (fragile, unspecified) or
    reimplement the guard check itself — in which case the test would stay green
    even if the REAL guard were deleted, defeating its entire purpose.

    The actual fix abandons trying to force a single deterministic interleaving and
    uses this codebase's own already-proven approach to exactly this class of
    property instead — a race test needs ROUNDS, not a synthetic pause point.
    `tests/test_leads_dismiss.py` seeds one lead, then runs N=50 rounds of two REAL
    threads (`concurrent.futures.ThreadPoolExecutor`) both calling
    `Sluice.dismiss_lead` on the same lead concurrently — the actual production code
    path, actual file I/O, actual `_cas_write` retries, no mocking of the write layer
    at all. Each round synchronizes both threads on a `threading.Barrier(2)`
    immediately before the call (maximizing real overlap rather than hoping GIL/OS
    scheduling provides it — the same technique, not merely a similar one, already
    measured and proven in this repo's own `tests/conformance/test_store_contract.py`
    for an analogous file-based CAS race). Across all 50 rounds: exactly one
    thread's call reports `dismissed` (with `note_appended: true`) and the other
    `unchanged` (with `note_appended: false`) — never both `dismissed`, never both
    `unchanged`, never an unhandled exception — and the note on disk carries exactly
    one `[dismiss <date>]` entry at the end. 50 real, Barrier-synchronized rounds
    make a missed race window statistically negligible without depending on any
    dispatch-model assumption — `MCPServer`'s own
    dispatch model (inline vs threaded, still to be determined empirically per the
    paragraph above) only affects how MANY of an MCP client's concurrent
    `call_tool("dismiss_lead")` requests can be in flight at once, never whether
    `Sluice.dismiss_lead`'s OWN guard is safe under concurrent callers — which is what
    this test actually proves, at the layer where the guard lives. A single
    `asyncio.gather`-based round trip through `tests/functional/test_mcp_contract.py`
    is kept as an integration-level sanity check that the SDK path reaches
    `Sluice.dismiss_lead` at all, not as the safety proof.

18. **`job-sluice leads dismiss --lead --reason` ships in the same PR; `create_lead`
    gets no CLI command this round.** `Sluice.dismiss_lead()` rewrites an existing
    note's status — never-regress territory — and a `Sluice` write method with no CLI
    caller means a human cannot reproduce, by hand, what an agent just did to their
    vault, reopening exactly the asymmetry `Sluice` exists to close (every other
    status-writing method already has one). It is a thin wrapper (`Sluice.dismiss_lead`
    already does the work) slotting beside the existing `leads expire`, following its
    same outcome-classification shape.

    `create_lead` deliberately gets no CLI command this round, and the asymmetry is
    accepted knowingly rather than overlooked: the invariant class differs, since
    `create_lead` can only create or no-op (`upsert`'s never-clobber makes its bad case
    a silent `"updated"`, never a clobber) — there is no equivalent human-reproduction
    gap to close. A real `job-sluice leads add` also raises genuine UX questions this
    slice has no business answering (interactive prompts, `--json`, whether it belongs
    in `sluice init`'s question-catalogue idiom).

## Architecture

```
MCP client (agent, e.g. Claude Code)
      │
      ▼
sluice/mcpserver.py: build_server(config, write=False)
      │  registers the 4 existing read tools always; the 5 below ONLY when write=True
      │  validate → ONE Sluice call → translate to the shared vocabulary (decision 15)
      │  NO store writes here, ever (AST-enforced, decision 2)
      │
      ├─ dismiss_lead(sluice, lead, reason, note_tag=None)         [note_tag: internal only]
      │       └─ Sluice.dismiss_lead()                                          [NEW]
      │             └─ store.update_fields(require_status=_DISMISSABLE_FROM,
      │                                    require_blank={"pending_cv"},
      │                                    append_note=..., note_tag=...)
      │
      ├─ apply_record(sluice, lead, ats=None, url=None)
      │       └─ Sluice.record() → engine.record_one() → apply/record.py:record()
      │             └─ store.update_fields(require_status=frozenset({"shortlist"}))  [HARDENED]
      │
      ├─ cv_run(sluice, lead, backend="auto")
      │       └─ Sluice.compose_cv(lead=...)     ← the ONLY route past the fabrication gate
      │
      ├─ cv_signoff(sluice, lead, discard=False, confirm_token=None)
      │       └─ Sluice.sign_off_cv(confirm=<capture closure>, require_pending=...)
      │             └─ store.sign_off(require_pending=...)                      [EXTENDED]
      │
      └─ create_lead(sluice, title, company, url, location="", salary="",
                     job_type="", source="manual")
              └─ Sluice.create_lead()                                           [NEW]
                    └─ store.upsert(Lead(..., search=""))     [_render_new HARDENED, decision 7]

job-sluice leads dismiss --lead --reason      (new CLI command, decision 18)
      └─ cmd_leads_dismiss(args, config) → Sluice.dismiss_lead()   (same method, human path)
```

### The five tools (`sluice/mcpserver.py`)

Each a plain top-level function taking `sluice: Sluice` as its first parameter, exactly
matching the existing four tools' shape — no MCP machinery, directly callable from a
unit test with a fake/injected `Sluice`.

```python
def dismiss_lead(sluice: Sluice, lead: str, reason: str, note_tag: str | None = None) -> dict
def apply_record(sluice: Sluice, lead: str, ats: str | None = None, url: str | None = None) -> dict
def cv_run(sluice: Sluice, lead: str, backend: str = "auto") -> dict
def cv_signoff(sluice: Sluice, lead: str, discard: bool = False,
               confirm_token: str | None = None) -> dict
def create_lead(sluice: Sluice, title: str, company: str, url: str, location: str = "",
                salary: str = "", job_type: str = "", source: str = "manual") -> dict
```

Each registered in `build_server()` as a thin nested closure via `@mcp_server.tool
(name=...)` only when `write=True` — decision 4 of #105's own module-shape reasoning
(a bare `functools.partial`/`wraps` composition leaks the injected `sluice` parameter
back into the client-facing schema; a real nested function does not) applies unchanged.

**`note_tag` gets the identical treatment `sluice` itself already gets, not a second
mechanism.** `/review-plan` caught this stated only in the Architecture diagram and not
here, where an implementer would actually copy from: the signature above is the
*top-level, unit-testable* function's — the one the CLI and `tests/test_mcpserver.py`
call directly, where `note_tag` stays a real (test-only) override for exercising
idempotency deterministically. The registered closure for `dismiss_lead` has a
NARROWER signature than the function it wraps — `dismiss_lead_tool(lead: str, reason:
str) -> dict`, omitting both `sluice` (decision 4 of #105) and `note_tag` (decision 5
above) — so neither ever reaches the client-facing JSON schema. `tools/list`'s existing
per-tool property-set assertion (Testing item 10) is what proves this, not prose.

### `sluice/core/app.py` — two new `Sluice` methods, plus `sign_off_cv`'s widened return

```python
@dataclass
class DismissResult:
    outcome: str       # dismissed | unchanged | refused_status | refused_signoff_hold
                       # | not_found | ambiguous | conflict
    slug: str = ""
    status: str = ""            # the FRESH status behind a refusal/unchanged
    candidates: list = field(default_factory=list)
    note_appended: bool = False

@dataclass
class CreateLeadResult:
    outcome: str       # upsert's six-member vocabulary, verbatim
    slug: str = ""      # "" when nothing was written

@dataclass
class SignOffResult:               # replaces sign_off_cv's existing bare 2-tuple return
    slug: str = ""
    outcome: str = ""   # promoted | discarded | collision | stale | nothing
                        # | not_found | ambiguous | conflict
    candidates: list = field(default_factory=list)   # populated on ambiguous only
    # decision 15: cmd_cv_signoff's one unpack line becomes attribute access
    # (result.slug, result.outcome); its printed joined-string line is built from
    # result.slug exactly as before -- unaffected in behavior, no longer tuple-shaped.

# _EXPIRABLE (existing, unchanged) stays expire()'s own -- excludes "dismiss" because
# expire_report() already filters already-dismissed leads before writing.
# dismiss_lead has no such pre-filter, so it needs the FULL set, "dismiss" included --
# a distinct constant, not a rename. See decision 6.
_DISMISSABLE_FROM = frozenset(_status.TRIAGE_OWNED)

# Implementation note (added per /review-plan round 2's architect finding): land
# _DISMISSABLE_FROM immediately beside _EXPIRABLE's existing definition, not ~1000
# lines away where dismiss_lead itself lives, and add a matching one-line comment on
# _EXPIRABLE pointing at _DISMISSABLE_FROM -- both are TRIAGE_OWNED-derived siblings
# that diverge for a real, verified reason (this comment), and a future edit to one
# with no cue the other exists is exactly the silent-drift risk a single shared
# constant was originally meant to avoid.

def dismiss_lead(self, *, lead: str, reason: str,
                 note_tag: str | None = None) -> DismissResult: ...
def create_lead(self, *, title: str, company: str, url: str, location: str = "",
                salary: str = "", job_type: str = "", source: str = "manual"
                ) -> CreateLeadResult: ...

# EXISTING method, return type widened from a bare (slug, outcome) 2-tuple to
# SignOffResult -- see decision 15. sign_off_cv's own parameter list is unchanged;
# only what it returns changes.
def sign_off_cv(self, *, lead: str, accept: bool = True, confirm=None,
                require_pending: str | None = None) -> SignOffResult: ...
```

Matching #105's own dataclass-report idiom (`SourceHealth`, `StaleLead`,
`DedupeCluster`) rather than `expire`'s older `(name, outcome)` tuple shape, since
these are new methods free to follow the more recent convention.

## Error handling

No blanket `try/except` in `sluice/mcpserver.py` — unchanged from #105's rule. Three
categories, as before, with the write tools' additions:

- **Expected structured outcomes** (`not_found`, `ambiguous`, `out_of_scope`, every
  domain verdict in decision 15's shared vocabulary) are normal successful tool
  results, never protocol errors.
- **Genuine failures** (`VaultConflict` after a sustained race, an unexpected
  construction failure) propagate as real exceptions; nothing here swallows one.
- **Malformed input** (`dismiss_lead`'s unsafe `reason`, `create_lead`'s unsafe
  fields, `cv_run`'s bad `backend`) raises `ValueError` naming the offending field(s)
  — the SDK's own dispatch converts this to a proper `is_error: True` tool result,
  matching how `list_leads`'s existing unknown-status `ValueError` already behaves,
  pinned by an existing contract test this slice extends.

## Testing

Mirroring #105's exact layer split.

**`tests/test_mcpserver.py`** (extended — direct calls against a real `Vault`-backed
`Sluice`, via the existing `_seed`/`_app` helpers):

1. The shared resolution triple (`not_found`/`ambiguous`/`out_of_scope`) for each of
   the four write tools that resolve a lead — `out_of_scope` is the single highest-value
   test in the file, since it's the behavior that reports as `not_found` today.
2. `dismiss_lead`: exact-match proof (a fragment that *would* match under
   `slug_matches` returns `not_found` — mutation: swap the matcher, must go red);
   `dismissed` from each `TRIAGE_OWNED` status; `refused_status` on `applied`;
   `refused_signoff_hold` on a `pending_cv` lead, naming the remedy;
   same-day-repeat → `unchanged` + `note_appended: false`; `ValueError` on an unsafe
   `reason`, naming the offending characters, with nothing written.
3. **The CAS proof, mutation-tested**: patch `read_leads` to hand back a stale
   `shortlist`/no-`pending_cv` snapshot for a note that's actually `applied`/holding a
   CV on disk; assert the fresh guard still refuses and the file is unchanged.
   Deleting `require_status=`/`require_blank=` must independently turn this test red.
4. `create_lead`: `created` reports the resolvable slug; **the collision trap** — a
   second call at the same company+title, different url, returns `updated`, and the
   note's `url` on disk is unchanged (the never-clobber proof); each of the seven
   fields rejects an embedded `"`/`\n` by name; a non-http `url` raises; frontmatter
   carries no `search` key; the created note does not appear in `seen.db`.
5. `apply_record`: `recorded` writes quoted `ats`; an `ats` carrying `\n` is dropped,
   `ats_dropped: true`, and the note's `status` still landed as `applied` (the
   injection-defeat proof — the drop doesn't block the transition); the
   `require_status` CAS proof (test 3's shape); `out_of_scope` for a `new` lead,
   carrying slug+status.
6. `cv_run`: fake backend/renderer injected via `Sluice(config, backend=..., render
   er=...)`; **`skipped-needs-signoff` for a lead already holding `pending_cv`** — the
   single most important test in the slice, proving the #60 latch survives the MCP
   path unweakened; `skipped-selection`; `content_warning` present iff `violations`/
   `audit_flags` non-empty; the composed CV text itself absent from every response.
7. `cv_signoff`: `discard=True` on a held lead → `discarded`, claims returned with
   `content_warning`; `discard=False` with no token → `needs_confirmation`, **nothing
   written** (assert the note unchanged on disk); the returned token promotes on a
   second call; a token issued against now-stale claims (re-hold in between) →
   `stale_confirmation`, still nothing written; resolves a held lead sitting in
   `dismiss` status (the `TRIAGE_OWNED`-wide scope, decision 4).
8. **The isolation sweep**: an AST sweep asserting `sluice/mcpserver.py` imports from
   `sluice.` only within an explicit allow-list — modelled on the existing `mcp`-import
   sweep's shape (asserts on *scope*, ≥N import nodes examined, so a broken matcher
   cannot pass vacuously). Mutation-tested by adding `from sluice.cv.engine import
   run_one` and confirming this specific test, not some other one, goes red.

**`tests/functional/test_mcp_contract.py`** (extended — in-memory `Client`):

9. `tools/list` under `write=False` returns exactly the original four tools — the
   write tools are genuinely **absent** from the schema, not merely refusing at call
   time.
10. Under `write=True`, all nine tools, none leaking `sluice`, exact per-tool property
    sets — and `cv_signoff`'s two-call shape is schema-visible (no default that makes
    promote reachable by omission).
11. A real `call_tool("dismiss_lead", ...)` round trip followed by `call_tool
    ("get_lead", ...)` proving the store actually changed through the real SDK JSON
    envelope.
12. **The concurrency sanity check** (decision 17): a single `asyncio.gather` of two
    overlapping `call_tool("dismiss_lead")` calls against the same seeded lead,
    proving the SDK path reaches `Sluice.dismiss_lead` under concurrent dispatch at
    all — NOT the guard's safety proof (that's item 12a below, at a different tier,
    in `tests/test_leads_dismiss.py`, which is where the property actually gets
    proven under real concurrent execution rather than through the SDK).
12a. **The 50-round real-concurrency proof** (decision 17, `tests/test_leads_dismiss.py`):
    two real `ThreadPoolExecutor` threads both call `Sluice.dismiss_lead` on the same
    seeded lead, 50 times over — real production code, real file I/O, no mocking of
    the write layer. **Both threads synchronize on a `threading.Barrier(2)`
    immediately before calling `dismiss_lead`, released together, per round** —
    round-3 test-engineer review caught that "50 rounds" alone doesn't
    establish real overlap without an explicit synchronization point, and that
    exact mechanism (maximizing overlap rather than hoping GIL/OS scheduling
    provides it) already has a measured precedent in this repo's own
    `tests/conformance/test_store_contract.py`, which this test mirrors rather than
    reinvents. Every round asserts: exactly one `dismissed` and one `unchanged`
    (never both — see the `VaultConflict` note below), never an exception, exactly
    one `[dismiss <date>]` note entry at the end, AND — per round 3's own
    `note_appended` finding above — `note_appended: true` on the `dismissed`
    result and `note_appended: false` on the `unchanged` one, every round, not only
    the outcome strings. A raised `VaultConflict` is not itself a round failure
    (with only 2 contending writers against `_RMW_RACE_RETRIES = 3`, the retry
    budget makes a genuine sustained-conflict outcome unreachable in practice for
    this specific contention level — stated explicitly here, not left implicit,
    since it's exactly the kind of unstated assumption earlier rounds caught
    elsewhere in this design) — if one is ever observed, the test fails loudly
    rather than silently retrying, since an unexplained `VaultConflict` at 2-writer
    contention would itself be a signal something else is wrong. This is the
    guard's actual safety proof; item 12 above is only a sanity check that the SDK
    reaches it.

**New flat files**, matching the `test_<area>.py` convention:

- `tests/test_leads_dismiss.py` — `Sluice.dismiss_lead`'s guards/outcomes/idempotency
  directly, including the 50-round real-concurrency proof (item 12a).
- `tests/test_leads_create.py` — `Sluice.create_lead`'s mapping, verdict passthrough,
  slug resolution.
- `tests/test_vault_render_safety.py` — `_render_new`: a `location` containing
  `\nstatus: applied\n` must not forge a `status` key on the note read back through
  `read_leads`; an embedded `"` abstains with a warning, not a raise. Asserts the
  MECHANISM, not only the outcome — `/review-plan`'s test-engineer reviewer noted a
  naive truncate-at-the-newline "fix" would also satisfy an outcome-only assertion.
  The fixture is chosen so the two behaviors are observably different: on abstain,
  `location` in the resulting frontmatter is BLANK (the whole unsafe value was
  refused, per decision 7's abstain-and-log rule); a truncating "fix" would instead
  leave a non-empty, truncated value. Asserting the field is empty is what
  distinguishes real `frontmatter_safe`-abstain from a weaker workaround. **A
  companion positive case is required, not optional**: a SAFE `location` (no
  forbidden characters) must survive `_render_new` unchanged and non-blank — round-2
  test-engineer review caught that the unsafe-only fixture set is also satisfied by
  an over-broad "abstain everything unconditionally" mutant, which the positive case
  alone would catch.

  **`company`/`role` get a THIRD, distinct test case — a narrower guard than the
  other five fields, checked against a NEWLINE, not a bare quote.** Per decision 7's
  round-3 correction: an embedded newline in `company` OR `role` (checked
  individually — this is a NEW pre-check ahead of `upsert`'s own OR-based
  blank-identity gate, not a reuse of it) refuses the whole create via `upsert`'s
  `refused` outcome, never reaching `_render_new`. Required cases, all new: (a) an
  embedded newline in `company` alone (role safe) refuses — the mixed-field case
  that pins the new check is genuinely OR-based, since a naive AND-based
  implementation (mirroring the existing blank-identity gate) would wrongly let it
  through; (b) symmetric case with `role` alone; (c) **a `company` MERELY containing
  a quote character, no newline, must still create successfully** — pinning
  compatibility with the existing `tests/test_vault.py::
  test_upsert_still_creates_a_lead_whose_field_merely_CONTAINS_quotes`, since this
  design's whole point is closing the newline-forgery vector without regressing
  that already-shipped, deliberately-designed tolerance. Case (c) is what a
  round-3 review caught missing from the first draft, which would have reused
  `frontmatter_safe()`'s full definition (quotes included) and broken that existing
  test outright. The other five fields (`location`/`salary`/`role_type`/`url`/
  `source`) keep `frontmatter_safe()`'s full definition and the abstain-and-blank
  treatment unchanged, since none of them has an equivalent existing quote-tolerance
  test and none of them affects identity.
- `tests/test_leads_dismiss_cli.py` — `leads dismiss`'s outcome→rc mapping.
- Extend `tests/test_apply_record.py`/`tests/test_apply_record_cli.py` for the `ats`
  guard, the `require_status` refusal, and the new `ats_dropped` line.
- Extend `tests/conformance/test_store_contract.py` for `sign_off(require_pending=)`
  — and, per `/review-plan`'s test-engineer reviewer, directly at the `Vault.sign_off`
  layer, NOT only through `cv_signoff`'s two-call flow: seed a `pending_cv` hold using
  the same `store.hold_for_signoff(...)` helper this file's existing sign-off tests
  already use two tests above, then call `Vault.sign_off(ref, require_pending=<a
  value that does not match the fresh pending_cv>)` directly — no confirm-token layer
  anywhere in this call path — and assert it returns `"stale"` and writes nothing.
  Testing item 7's own staleness scenario (re-hold between two `cv_signoff` calls) is
  caught by the outer confirm-token comparison before `Vault.sign_off` is ever
  reached, so it alone would leave `require_pending`'s own CAS-level guard completely
  unexercised — if it were silently broken, nothing in the originally-described suite
  would notice.
- Extend `tests/functional/test_cv.py` (the existing CLI-level `cv signoff` test
  file — there is no separate `test_cv_signoff_cli.py`) for `cmd_cv_signoff`'s new
  `"stale"` outcome: forced via the same technique `test_conflict_returns_1` already
  uses (monkeypatching `Vault.sign_off` to return `"stale"`), asserting rc 1 and that
  `"stale"` is classified into `cmd_cv_signoff`'s `_FAILED` set alongside `nothing`/
  `conflict`. `/review-plan`'s generalist reviewer caught that every other new
  outcome in this design gets an explicit CLI-level test named except this one.

**Mutation-verified, not read-through** (per this repo's own stated discipline — a
check that cannot be falsified is unverified): `require_status`/`require_blank` in
`Sluice.dismiss_lead`; `require_status` in `apply/record.py`; the `frontmatter_safe`
call in `_render_new`'s new guard; **the `frontmatter_safe` call on `ats` in
`apply/record.py`** (decision 8 — `/review-plan`'s round-2 generalist reviewer caught
this list omitted it despite the guard being structurally identical to `_render_new`'s,
which the list did name); the `require_pending` comparison in `Vault.sign_off`; the
confirm-token comparison in `cv_signoff`; **`note_appended`'s composite derivation**
(decision 5 — round-2 invariant review caught the first draft's post-write-re-read
version was unfalsifiable by construction, since it can't distinguish "I appended
it" from "it was already there"; round-3 review then caught the SNAPSHOT-only fix
that replaced it could itself report `note_appended: true` for a race LOSER whose
write actually no-op'd — dropping either half of the composite, `(tag not in
snapshot) and wrote`, must independently turn Testing item 12a's per-round
`note_appended` assertion red, not just the outcome assertion); **the company/role
newline-refusal branch in `upsert`'s new pre-check** (decision 7 — round-3 review
caught this list still named only the generic `_render_new` guard after decision 7
was corrected twice this round; deleting the newline check, or widening it back to
`frontmatter_safe()`'s full quote-rejecting definition, must independently turn
both the mixed-field OR-behavior test and the quote-tolerance regression test red).
Each
guard's removal must turn its named test red, confirmed by running that test by node
ID.

All synthetic titles come from the existing seeded `titles`/`cfg_titles` faker
fixtures, never hardcoded — matching #105's own testing rule.

## Docs

- `README.md`: the `job-sluice mcp` section currently states the server is read-only —
  rewritten with `--write` and the nine-tool table.
- `docs/USAGE.md`'s `## job-sluice mcp` section: the tool table, and the two-call
  `cv_signoff` promote protocol written out explicitly, since it's the one non-obvious
  call sequence on the whole surface.
- `docs/ARCHITECTURE.md`'s surface/adapter section: states the rule that the MCP
  surface holds no store writes and that a write tool is a translation layer over a
  `Sluice` method (decision 2), not merely naming the tool count.
- `.rulesync/rules/CLAUDE.md`'s never-regress paragraph: `dismiss_lead` is the second
  `dismiss` writer, and unlike `expire`'s snapshot-decided sign-off-hold refusal, its
  own refusal is CAS-fresh via `require_blank`.
- `docs/superpowers/specs/2026-08-12-mcp-server-design.md`: Out-of-scope section and
  Changelog gain a line pointing at this document as the slice that picked up the
  deferral.

## Definition of done

- Five write tools registered only under `job-sluice mcp serve --write`; `tools/list`
  under the default (no `--write`) still returns exactly the original four — the one
  regression this slice must not cause.
- Every write in the slice routes through `update_fields`/`upsert`/`sign_off`;
  `sluice/mcpserver.py` contains no store write and no `cv.engine`/`cv.render` import
  (both AST-pinned and mutation-tested).
- `_render_new` cannot write a frontmatter-injecting value on any path, ingest
  included, without aborting the batch it's part of.
- `apply/record.py` guards `ats` and passes `require_status`; the CLI's message
  wording is corrected for the new `raced` reason.
- `Vault.sign_off`'s `require_pending`/`"stale"` exist, are documented on the `Store`
  protocol, and `cmd_cv_signoff` classifies `"stale"` as a failure (rc 1). Tested
  BOTH via the conformance suite AND directly at `Vault.sign_off` with no
  confirm-token layer in the call path — per round 1's own finding, testing only
  through `cv_signoff`'s two-call flow would leave this specific CAS guard
  unexercised, since the outer confirm-token comparison already catches that flow's
  one described staleness scenario before `Vault.sign_off` is ever reached.
- `create_lead` reports `updated`/`refused`/`merged_away*` honestly (never a bare
  "created") and writes no `seen.db` row.
- `job-sluice leads dismiss` exists, reusing `Sluice.dismiss_lead` verbatim.
- The SDK's sync-tool dispatch model is verified against the installed `mcp>=2.0.0`
  and recorded in `build_server`'s docstring, replacing #105's open caveat.
- Every guard listed under Testing's "Mutation-verified" heading has actually been
  broken and observed to turn its named test red.
- `README.md`, `docs/USAGE.md`, and `docs/ARCHITECTURE.md` are all updated in the same
  PR.

## Out of scope

- **A generic `update_lead(fields)`** — the issue's own reasoning, upheld: it would
  fight the CAS-guarded write philosophy.
- **`track run`/`confirm`/`dismiss`** — the issue defers these explicitly.
- **`cv_run`'s `all_shortlist`/`dry_run`/`include_stale`/`no_serve`/`limit`** —
  decision 14, each with a stated reason.
- **A `status` parameter on `create_lead`** — a manual lead lands at `new`; reaching
  `shortlist` needs `job-sluice triage run`. A real workflow seam (`create_lead` and
  `cv_run` don't compose without it) named explicitly in the tool's own docstring
  rather than silently left for a caller to discover.
- **`leads expire`/`dedupe --merge`/`reconcile --apply` as MCP tools** — batch writes
  need their own "one call must not launch an unbounded write sweep" design,
  independent of this slice.
- **`job-sluice leads add` CLI** — decision 18, knowingly accepted asymmetry.
- **Re-guarding `update_fields`'s OTHER unguarded `append_note` callers** — the same
  bug class as decision 7, one layer up (`triage/apply.py`'s LLM-generated
  `fit_reasoning`/`reason` text is still unguarded), with a different blast radius;
  worth its own issue with its own reproduction, not folded into this one.
- **Any transport beyond local stdio; auth/scoping; per-session spend budgets** —
  unchanged from #105.

## Changelog

- 2026-08-14: Initial design. Produced in a Claude Code plan-mode session: three
  parallel `Explore` agents surveyed the four operations' existing implementations
  (`Vault.update_fields`/`upsert`, `apply/record.py`, `cv/engine.py`'s fabrication
  gate) against #105's existing tool pattern; two independent `Plan` agents then
  designed the full slice from opposing lenses (safety-first vs
  agent-ergonomics-first) given that survey; the two highest-risk factual claims
  (`_render_new`'s unguarded frontmatter, `apply/record.py`'s unguarded `ats` +
  missing `require_status`) were independently found by both agents and verified a
  third time directly against the source before being accepted. The two designs
  converged on the core architecture (new `Sluice` methods, never a raw store write
  from `mcpserver.py`; `lead` not `lead_ref`; no lock, verified concurrency) and were
  reconciled point-by-point on their divergences — `dismiss_lead`'s exact-vs-substring
  matcher, `note_tag` exposure, `_render_new`'s fix-now-vs-file-separately split, and
  the outcome-vocabulary shape — favoring whichever position was better argued rather
  than either agent's lens uniformly. `cv_signoff`'s promote mechanism (decision 13)
  was the one decision put to the repo owner directly, since it is a judgment call
  about risk to their own real CVs and applications that no amount of code-reading
  resolves; they chose the two-call confirmation-token mechanism over a CLI-only
  restriction. Next: `/review-plan`.
- 2026-08-14: Revised after `/review-plan` (5 reviewers: 0 Critical code findings,
  1 escalated personal-data question, 6 High, 4 Medium, 1 Low). Corroborated by three
  independent reviewers (invariant/Low, generalist/High, architect/Medium): `note_tag`
  was described as never exposed to the MCP client (decision 5) but the copy-pasteable
  signature block still listed it as an ordinary parameter with no stated mechanism
  for dropping it — fixed by stating explicitly that the registered closure's own
  signature omits it, the same treatment `sluice` itself already gets. The
  invariant reviewer found the sharpest bug: `_DISMISSABLE_FROM` reused `_EXPIRABLE`
  verbatim (excluding `"dismiss"`), which is safe for `expire()` only because its
  report phase pre-filters already-dismissed leads — `dismiss_lead` has no such
  pre-filter, so a same-day re-dismiss would have hit a hard CAS refusal instead of
  the `unchanged` outcome Testing's own item 2 required; fixed with a second,
  independently-derived constant including the full `TRIAGE_OWNED` set. The
  architect reviewer found that `cv_signoff`'s promised `candidates` slug list has no
  source: `Sluice.sign_off_cv` only ever returns a joined ref string today; fixed by
  widening that method's own ambiguous-branch return rather than re-resolving inside
  `mcpserver.py` (which decision 13 already argues against elsewhere in this
  document), and by naming the shared `out_of_scope` helper's home (`core/leads.py`)
  and signature explicitly. The test-engineer reviewer found the concurrency test
  as first drafted could pass vacuously under inline SDK dispatch (no forced
  interleaving), and that `Vault.sign_off`'s new `require_pending` CAS guard was
  never actually exercised by any described test (the confirm-token layer would
  already catch the only staleness scenario given, leaving the CAS guard itself
  unwitnessed) — both fixed with tests that force the property directly rather than
  hoping the scheduler or an outer layer produces it. The generalist reviewer found
  `create_lead`'s `location`/`url` required-vs-optional split disagreed between the
  tool signature and the `Sluice` facade with no stated rule; fixed by requiring
  `url` (validated http/https either way) and defaulting `location` at BOTH layers,
  consistently. The generalist reviewer also found `cv_signoff`'s new `"stale"`
  outcome had no CLI-level test named, unlike every sibling outcome in this design;
  fixed by adding one to the existing `tests/functional/test_cv.py`. One finding
  escalated rather than fixed outright: the neutrality reviewer flagged a literal
  hostname the Problem section had copied verbatim from the issue's own text — put
  to the repo owner directly per the standing escalate-neutrally rule, since a local
  review cannot tell whether a string is a real personal host or an invented
  illustration. Confirmed real; replaced with angle-bracket placeholders in the same
  spirit as this repo's own established redaction convention (`SECURITY.md`'s
  `<host>`/`<path>` rule, `core/backends.py`'s secrets map) — the exact tokens here
  (`<host>`/`<user>`) are chosen to fit an ssh/docker/gosu one-liner rather than a
  literal reuse of that convention's own two tokens. Stated here structurally only —
  the specific string itself is deliberately not repeated in this changelog entry,
  matching this project's own #56 lesson that describing a remediation can leak
  worse than the thing it remediated.
- 2026-08-14: Revised after a second `/review-plan` (5 reviewers: 1 Critical, 3
  High, 2 Medium — this entry was itself missing until round 3 caught it, see
  below). The Critical: round 1's forced-interleaving concurrency-test design (a
  `threading.Event` blocking `Vault.update_fields` mid-transform) turned out to
  have no real seam — verified `update_fields` is a synchronous trampoline into a
  private `_cas_write` with the guard logic in a closure local to itself, so a
  double could only patch private internals or reimplement the guard (defeating
  the test); replaced entirely with 50 rounds of real `ThreadPoolExecutor`
  concurrent execution, this codebase's own already-proven approach to exactly
  this class of property. The architect reviewer found `cv_signoff`'s promised
  `candidates` slug list had no source — `Sluice.sign_off_cv` returns a bare
  2-tuple, and `cmd_cv_signoff` unpacks it strictly, so the round-1 fix's claim of
  "no CLI change needed" was false; fixed with a `SignOffResult` dataclass,
  matching `DismissResult`/`CreateLeadResult`'s established shape, and the one
  real `cli.py` line this touches named explicitly. The generalist reviewer found
  `out_of_scope_verdict`'s signature had nowhere to put the query string
  `slug_matches` actually needs; fixed by threading `wanted` through explicitly.
  The neutrality reviewer found round 1's OWN changelog entry (above) had re-typed
  the hostname it was describing the removal of — fixed by rewriting that entry to
  describe the fix structurally with no literal string at all, and an
  under-marked illustrative slug was swapped for this document's own established
  fake-data convention. Also fixed: `note_appended`'s post-write re-read couldn't
  distinguish "appended" from "already there" (moved to a pre-write-snapshot
  derivation); `_render_new`'s abstain-and-log treated `company`/`role` the same
  as the other five fields, but those two ARE the vault's identity key, so
  silently blanking one on an unsafe value risked splitting one real job into two
  disconnected notes on a later legitimate re-scrape (changed to refuse the whole
  create instead — a fix round 3 would go on to find was itself incomplete, see
  below); the mutation-verified list and DoD were updated to match every fix
  above.
- 2026-08-14: Revised after a third `/review-plan` (5 reviewers: 0 Critical, 6
  High, 2 Medium, 2 Low). No self-contradictions this round — every High was a
  genuine correctness gap in round 2's OWN new fixes, the exact pattern round 3's
  prompts asked reviewers to hunt for. Two reviewers independently caught that
  round 2's `company`/`role` fix, as written, would have reused
  `frontmatter_safe()`'s full definition (rejecting any embedded quote) and broken
  an EXISTING, already-shipped, deliberately-designed test —
  `test_upsert_still_creates_a_lead_whose_field_merely_CONTAINS_quotes` — whose
  own docstring states the actual governing principle for this code path: only a
  value that parses back EMPTY may be refused. Also caught: `upsert`'s existing
  blank-identity gate is OR-satisfied and cannot catch a single-field-unsafe case
  at all, so the claimed reuse of "the same refused outcome path" never actually
  fired for the scenario the fix was written to close. Corrected with a genuinely
  narrower, NEW pre-check: reject `company`/`role` only on an embedded newline
  (the actual key-forging vector), explicitly tolerating embedded quotes exactly
  as the existing unguarded behavior already does — three distinct test cases
  added (mixed-field OR-behavior, its symmetric case, and the quote-tolerance
  regression pin). Two reviewers independently found `note_appended`'s round-2
  pre-write-snapshot fix could itself misfire: a race LOSER's snapshot correctly
  shows the tag absent before it reads, but its own write then loses the CAS race
  and commits nothing — reporting a self-contradictory `note_appended: true`
  alongside `outcome: "unchanged"`; fixed with a composite,
  `(tag not in snapshot) and wrote`, and Testing item 12a now asserts
  `note_appended` per round, not only the outcome. The test-engineer reviewer
  found the 50-round concurrency test named no synchronization mechanism to force
  real overlap, risking round 1's exact "passes vacuously" defect one layer down;
  fixed with a `threading.Barrier(2)` per round, mirroring a technique already
  measured in this repo's own `test_store_contract.py` rather than inventing a
  new one. Three reviewers independently found `SignOffResult` (introduced last
  round) was absent from the copy-pasteable `core/app.py` code block, which still
  carried a stale "one rename" section header from before that round's own
  `_DISMISSABLE_FROM` fix — both corrected, with `sign_off_cv`'s new signature
  added to the block. Also fixed: this Changelog was genuinely missing its own
  round-2 entry (caught independently by two reviewers) — added above, once
  reconstructed accurately rather than left as a placeholder; a citation for the
  hostname-redaction convention named files that don't actually contain that
  literal, corrected to the files that do; the `VaultConflict`/`_RMW_RACE_RETRIES`
  assumption underlying the 50-round test's binary pass/fail framing is now
  stated and cited explicitly rather than left implicit.
