# Triage company-name resolution — classify() names the fix but never attempts it (#109)

**Status:** design approved 2026-08-10; revised after three rounds of `/review-plan`, each
re-verifying the prior round's fixes against the actual text rather than trusting the changelog.
Round 1 (5 reviewers): 0 Critical, 9 High, 6 Medium, 1 Low. Round 2: 1 Critical, 4 High, 5 Medium,
2 Low — the Critical was caused by round 1's own fix. Round 3 (reviewers specifically directed to
scrutinize round 2's own new fixes): 0 Critical, 3 High, 4 Medium, 3 Low. All addressed — changelog
at the bottom.

**Issue:** #109 — `triage: classify() names the fix for a blank company but never attempts it,
and needs_review is a one-way trap`
**Sub-apps:** `triage` (the new resolution step, called from `engine.py`, and a hardening fix to
existing `apply.py` writes), `ingest` (an optional new `Source` capability), `core` (extends the
existing dossier fetch closure and `DossierCache`'s cache-key derivation in `core/app.py`/`core/dossier.py`)

## Problem

`classify()` (`sluice/triage/classify.py:142-143`) hits this branch whenever `company` is blank
or `"unknown"`:

```python
if not company or company.lower() == "unknown":
    return "needs_review", "No company name; visit URL to identify"
```

That message names a real fix — visit the URL — but nothing in the pipeline ever does it. On a
real production vault, 411 of 424 `needs_review` leads (97%) carry this exact reason, across
nearly every configured source. `needs_review` is not in `triage run`'s default `--status`
(`cli.py:1041`, `new,research`), so a lead landing there never gets a resolution retry — not
because retrying is expensive, but because nothing has ever tried. See the full issue text for
the sampled URL shapes and the ~60% free-recovery estimate this design is built against.

## The five settled decisions

1. **Both cascade tiers land in this pass** — URL-pattern extraction (free, no network) and
   dossier-fetch-based page extraction (a real page visit, still no LLM call). Not split into a
   follow-up: the project's default is to fold self-contained work into one PR, and tier 2 reuses
   machinery (the dossier fetch closure, the `DossierCache`) tier 1 doesn't touch, so there's no
   natural checkpoint between them that isn't also mid-feature.

2. **A resolved company is written back to vault frontmatter**, through the same surgical
   `update_fields` CAS path every other triage write uses — not held in memory for one run. The
   issue's own framing ("visit URL to identify") implies the value should end up recorded, not
   re-derived every re-run.

3. **Tier 2 stays gated behind `--no-llm`**, even though it makes no LLM call. `--no-llm` today
   means "classify + apply + audit only, zero network" (`triage/engine.py:1-7`, dossier building
   only happens inside `if keeps and not no_llm` at `engine.py:78`). Silently changing that to "no
   LLM, but may still open a browser" is the kind of quiet redefinition CLAUDE.md's "fail loudly at
   construction" posture warns against — anything relying on `--no-llm` meaning fully offline
   would break without a flag change to point at. Under `--no-llm`, only tier 1 runs.

4. **Tier-1 URL knowledge lives on the `Source` that owns it**, as a new *optional* protocol
   member `company_from_url`, not in a central regex table inside `triage/`. This mirrors the
   `Renderer.precheck` / `Store.preflight` pattern already established for exactly this
   shape — "an implementation that cannot say is not one that is broken"
   (`core/protocols.py:140-146`). URL-shape knowledge for each board already lives in that board's
   source module (see the DOM-rebound comments in `naukrigulf.py`/`weworkremotely.py` — sources
   already get maintained when a board's markup shifts); duplicating that knowledge into a second,
   centrally-maintained table is exactly the kind of drift CLAUDE.md's "enumerate, don't hand-list"
   lesson exists to prevent.

5. **A dedicated config knob gates tier 2, independent of `--no-llm`, and it defaults OFF.**
   *(added in review round 1 as `arch-002`; the default flipped in round 2 as `inv2-003`.)*
   `TriageConfig` gains `company_resolve_fetch: bool = False`, plus a matching commented entry in
   `sluice.yaml.example`. Tier 2 runs only when `company_resolve_fetch and not no_llm`. Round 1
   shipped this defaulting **on** ("the fix should just work"); round 2's invariant review found
   that argument doesn't survive contact with this codebase's own precedent — `lead_ttl_days` and
   `lead_layout` are both "the whole point of the fix" toggles that still default to abstain/off,
   specifically because each starts new automatic behaviour an unconfigured install never opted
   into. `company_resolve_fetch=True` by default would mean every existing install starts opening
   real Camofox tabs against arbitrary third-party sites for its entire `needs_review` backlog the
   moment it upgrades, unprompted. **Tier 1 is unaffected by this knob and still runs
   unconditionally** — it's free, no network, so it carries none of the "unconfigured install gets
   new automatic behaviour" cost this knob exists to gate. Only the real page-visit tier requires
   opt-in.

A principle underneath all five, driving several details below: **a wrong company is worse than a
blank one.** Blank explicitly signals "unknown, a human should look" — the honest state. A
mis-extracted name would silently look like ground truth and carry through `keep` → judge → apply
→ a CV addressed to the wrong employer, which is the same asymmetric-risk shape as the pay-floor
and location gates already in `classify.py` (abstain on anything not credible, never guess).
Neither tier writes anything below a confident, structurally-justified match. The same posture
governs the write path itself: a resolution whose write cannot be confirmed (a race, a dry run)
is treated as not having happened at all for this run — see "Architecture" below.

## Architecture

`classify()` is untouched — it stays exactly what its docstring already promises: pure, no
dossier, no LLM (`classify.py:1-7`), and it still runs **first, unconditionally, for every note** —
cheap, per its own docstring ("resolves the obvious cases for free"). Resolution is a follow-up
step in `triage/engine.py`'s classify loop (`engine.py:51-75`), attempted only when the result is
*specifically* the blank-company `needs_review` branch — never ahead of classify()'s existing
title/company/location/pay-floor rejects, which don't depend on company at all. `resolve` is
imported at module scope in `engine.py`, alongside the existing `from sluice.triage.classify import
classify` (`engine.py:17`) — it needs no lazy treatment the way `Sluice.triage()`'s `sluice.ingest`
import does, since `resolve.py` itself imports nothing heavy: `get_source` and `dossier_cache` are
both injected, never constructed inside it.

`run()`'s signature gains one new keyword-only parameter:

```python
def run(vault, cfg, backend, dossier_cache, audit, *,
        statuses=("new", "research"), limit=None, dry_run=False, no_llm=False,
        get_source=None):
```

And inside the existing `for note in notes:` loop:

**Superseded during implementation (post-round-3, found in `/review-pr`):** the `update_fields`
call below shows only `require_status`. The shipped code also passes `require_blank={"company"}`
— a new, generalized `Vault.update_fields` parameter (added to the `Store` protocol contract too)
closing a second race this same tier-2 fetch widens: a human editing the note's `company` field by
hand during the multi-second page visit could otherwise be silently overwritten by the scraped
value once the fetch completes. `require_status` alone does not cover this — it re-reads `status`,
not `company`. See `docs/ARCHITECTURE.md`'s write-contract section for the shipped shape; this
spec's code sketch is left as originally approved rather than rewritten to match.

```python
company = (note.fm.get("company") or "").strip()
decision, reason = classify(note.fm, cfg)
if decision == "needs_review" and not company:
    resolved = resolve.resolve_company(
        note.fm, get_source, dossier_cache,
        no_llm=no_llm, company_resolve_fetch=cfg.company_resolve_fetch)
    if resolved:
        wrote = False
        if not dry_run:
            try:
                wrote = vault.update_fields(
                    note.ref, {"company": f'"{resolved}"'},
                    require_status=frozenset(_status.TRIAGE_OWNED))
            except VaultConflict as e:
                report.failures.append(f"company-resolve {note.ref}: {e}")
            else:
                if not wrote:
                    report.failures.append(
                        f"company-resolve {note.ref}: company write did not land "
                        "(status changed, or the value was already current)")
        if wrote or dry_run:
            note.fm["company"] = resolved
            decision, reason = classify(note.fm, cfg)
```

Five things this restructuring fixes, each a real review finding, not a stylistic choice:

- **Cost neutrality is now actually true, not just claimed** (`arch-001`/`rev-002`, found
  independently by two reviewers). Gating on `decision == "needs_review"` — the result of a
  `classify()` call that already ran classify()'s existing free rejects — means a lead that would
  be rejected on title, location, or pay regardless of company never triggers a tier-2 page visit.
  The gate is `decision == "needs_review" and not company`, not reason-string matching against
  classify()'s message text — robust to a future classify() branch that produces `needs_review` for
  some other reason, since that case would see `company` already non-blank and skip resolution
  regardless of the decision value.
- **`dry_run` is honoured** (`inv-001`). Only the `vault.update_fields` call is skipped under
  `dry_run` — the resolution computation itself (tier 1 and tier 2) still runs, matching the
  existing precedent of the enrich pass, which already builds real dossiers under `dry_run` for
  reporting purposes and skips only `apply_verdict`'s write. **Corrected in round 2 (`test2-001`):**
  re-classifying under `dry_run` does *not* make `report.counts` preview the reject/needs_review
  outcome — `engine.py`'s existing `dry_run` branch hardcodes `outcome = "skipped"` for *every*
  lead regardless of decision, a pre-existing behaviour this design doesn't touch, verified by
  running the real loop against synthetic fixtures. The one real, visible benefit of
  re-classifying under `dry_run` is narrower than originally claimed: a lead that *would resolve
  and become `keep`* gets its `report.counts["keep"]` bump correctly (that increment happens
  unconditionally, before any write attempt, the moment `decision == "keep"` is reached) — without
  re-classification it would stay miscounted under `needs_review`. For the reject/needs_review
  sub-case, `dry_run`'s reporting is unchanged from today: flattened to "skipped," same as every
  other lead in a dry run.
- **The VaultConflict/require_status ambiguity is resolved, unambiguously, one way** (`test-001`).
  There is exactly one rule, visible directly in the code above: **re-classification happens if and
  only if the write landed, or this is a dry run (which never attempts a write in the first
  place).** On a real run where the write fails — either a raised `VaultConflict` from a genuine
  concurrent edit, or a silent `False` from `require_status` — `decision`/`reason` keep whatever
  classify() computed on its first, unconditional call, against the *unresolved* company. **The
  `False` case has two distinct causes, not one** (`rev2-001`, found in round 2): `require_status`
  finding the lead has left `TRIAGE_OWNED`, *or* `update_fields`'s documented "a write of a value
  the note already holds returns `False`" behaviour (`core/protocols.py:249-256`) — realistic here
  since both tiers are deterministic, so a second concurrent resolution can legitimately compute the
  identical company. The log message is deliberately non-specific about which ("company write did
  not land (status changed, or the value was already current)") rather than asserting a cause it
  cannot distinguish from the boolean alone. The already-current case is self-healing regardless: on
  the *next* run, `note.fm["company"]` reads back non-blank (someone already wrote it), `not
  company` is `False`, resolution is never attempted, and `classify()`'s first call already sees the
  correct value — the cost is one skipped run-cycle, not a stuck lead.
- **`get_source` no longer breaks the existing test suite** (`rev-001`). It's a new
  **keyword-only** parameter on `triage.engine.run`, defaulting to `None` — not a required
  positional argument "alongside `dossier_cache`/`audit`" as an earlier draft implied (see the
  updated signature above). `resolve.py` treats a `None` `get_source` the same as an unrecognized
  source id: tier 1 uniformly abstains. The six existing direct calls to `run(...)` in
  `tests/test_triage_engine.py`, none of which pass a `get_source`, are unaffected by construction;
  only `Sluice.triage()`'s own call site, and any *new* test that specifically exercises
  resolution, need to pass a real one.
- **Malformed scraped content cannot inject a frontmatter line** (`inv2-002`, Critical-adjacent —
  see "Guarding the write against scraped content" below). `resolved` is the first raw,
  unmediated open-web string this codebase has ever written into frontmatter — every existing
  quoted-write precedent (`glassdoor_rating`, `culture_flags`) is either numeric or LLM-mediated.
  `resolve_company` now validates before returning.

`Sluice.triage()` (`core/app.py:786-815`) gains the same lazy, inside-the-method import its
`ingest()` neighbour already uses for `ingest.base`/`ingest.engine` (`core/app.py:508-509`) —
`from sluice.ingest import sources` — and passes `sources.get` as the new keyword argument to
`_triage_run`, exactly the way it already passes `cache = self.dossier_cache(...)`. `triage/`
still never imports `sluice.ingest` directly: the pipeline's sub-app dependency direction
(`ingest -> triage -> cv -> apply -> track`) only crosses at the composition root, and
`triage/engine.py` itself only ever sees a callable.

`resolve_company` is a thin orchestrator:

```python
_UNSAFE_CHARS = ('"', "\n", "\r")


def resolve_company(fm: dict, get_source, dossier_cache, *,
                     no_llm: bool, company_resolve_fetch: bool = False) -> str | None:
    """Tier 1 then tier 2, first confident match wins. Returns None -- never a guess --
    when both abstain, INCLUDING when a candidate contains a frontmatter-structural
    character. `get_source` is `sluice.ingest.sources.get` (or None, meaning tier 1
    always abstains), injected so this stays testable without importing the real
    registry."""
    def _safe(candidate):
        return candidate if candidate and not any(c in candidate for c in _UNSAFE_CHARS) else None

    url = fm.get("url") or ""
    src_id = fm.get("source") or ""
    if get_source is not None and url and src_id:
        try:
            source = get_source(src_id)
        except KeyError:
            source = None
        extractor = getattr(source, "company_from_url", None)
        if extractor:
            try:
                hit = _safe(extractor(url))
            except Exception:
                hit = None  # a per-source extractor is newly-authored, hand-maintained regex
                            # code running against live scraped URLs -- exactly the untrusted
                            # input class the _safe guard exists for. One source's bug on one
                            # unanticipated URL shape must not crash the whole triage run.
            if hit:
                return hit
    if no_llm or not company_resolve_fetch or not url:
        return None
    try:
        dossier = dossier_cache.get_or_build(fm)
    except Exception:
        return None  # a failed fetch just means "couldn't resolve" -- fall through to
                     # classify()'s existing needs_review branch, not a fatal per-lead error
    return _safe(_from_dossier(dossier))
```

The `try/except KeyError` on `get_source` covers a lead whose `source` frontmatter names a
retired/renamed source module — abstain, not raise, consistent with "an unrecognized status is
passed through untouched" elsewhere in this codebase's error posture. **The `try/except Exception`
around `extractor(url)` (added in round 3, `rev3-001`, High)** covers the same untrusted-input
class from the other direction: unlike `get_or_build`'s existing isolation, a per-source
`company_from_url` implementation had no equivalent guard in earlier drafts, despite being
newly-authored, hand-maintained regex code running against live scraped URLs on every triage run —
exactly the input class this design elsewhere treats as untrustworthy (the whole reason `_safe`
exists). Without it, one source's extractor raising on an unanticipated URL shape (an unmatched
`.group()`, a malformed slug) would propagate uncaught out of `resolve_company`, out of `engine.py`'s
per-note loop (which has no enclosing guard around the call either), and crash the entire `triage
run` for every remaining lead in the batch — not merely fail the one problematic lead, the way
`get_source`'s `KeyError`, the dossier fetch, and the JSON-LD parse are all already isolated to do.

The `try/except Exception` around `dossier_cache.get_or_build` is deliberately **softer** than the
enrich pass's own handling of the same call (`engine.py:82-86`, which records into
`report.failures` and `continue`s — dropping the lead from `keeps` entirely, because a `keep`
verdict genuinely cannot be judged without a JD). A failed resolution fetch loses nothing a failed
enrich-pass fetch would: the lead simply keeps classify()'s already-computed `needs_review`
result, exactly as it would with this feature absent. No `report.failures` entry here specifically,
because "couldn't resolve a company" is not a run failure — it's the documented residual (see
below); a *write* failure after a *successful* resolution is the case that gets logged, per the
rule above.

### Guarding the write against scraped content

*(new in round 2, `inv2-002`, High.)* `core/vault.py`'s `_set_fm` writes its `literal` argument
verbatim into the frontmatter line via regex substitution — by design, so the caller controls
quoting, and every existing caller of that contract (`glassdoor_rating`, `culture_flags`) supplies
either a number or LLM-mediated structured output. `resolved` is neither: it's a raw substring
pulled directly from a third-party page's `document.title` or JSON-LD, with explicitly no LLM pass
in between (per decision 1, "no LLM-based company guessing"). An embedded `"` followed by a
newline and a fabricated `key: value` line — accidental mangled JSON-LD, or a deliberately
adversarial listing — written verbatim could inject a new top-level frontmatter line on the next
hand-rolled (non-YAML-parser) read.

`resolve_company`'s `_safe` helper (shown above) rejects any candidate containing a literal `"`,
`\n`, or `\r` from *either* tier, treating it as an abstain rather than attempting to escape it —
consistent with this design's governing "abstain over guess" posture, and simpler than
establishing an escaping convention this hand-rolled frontmatter format doesn't otherwise have.
This is the first time raw, unmediated open-web content reaches a frontmatter write in this
codebase, so the guard belongs at the point of writing, not left to `_set_fm`'s existing
verbatim-literal contract.

### Hardening `apply_classification`/`apply_verdict` against the widened race

*(new in round 2, `inv2-001`, **Critical** — the highest-severity finding across both review
rounds.)* The company write above is correctly protected by a fresh `require_status` re-read. But
the *very next* write in the same code path — `apply_classification`'s status write
(`triage/apply.py:21-30`, pre-existing and otherwise unmodified by this design) — is guarded only
by `_guarded(note)`, which checks `note.status`, a plain dataclass field frozen at `read_leads()`
time (`LeadNote`, `core/protocols.py:129-134`), not a fresh read. `update_fields`'s own docstring
calls this exact shape "byte-identical to having no guard at all" against a real vault.

Before this design, the window between that stale snapshot and the write was sub-millisecond — no
I/O sits between them. This design inserts a real, synchronous tier-2 dossier fetch (DNS, page
load, JS render — realistically seconds) directly ahead of that same write, and specifically for
the `needs_review` population: leads the design's own words call out as "a human should look" —
exactly the ones most likely to be under active human review at that moment. If a receipt or a
manual `apply record` lands in that now-widened window, `apply_classification` writes `status:
needs_review` straight over a real `applied` status with nothing to catch it: silent, and `track`
stops tracking a real application with no forward-only move available to undo it. This is the
named Critical trigger verbatim: "triage writing to an already-`APPLICATION_OWNED` lead."

The fix: extend `require_status=frozenset(_status.TRIAGE_OWNED)` to `apply_classification`'s
`update_fields` call, and — since the enrich/judge pass has the *same* stale-guard shape behind an
even longer pre-existing dossier-fetch-plus-LLM-judge round trip — to `apply_verdict`'s as well,
closing an equally real gap that predates this feature entirely:

```python
# triage/apply.py -- both functions gain the same parameter, and both distinguish the
# NEW race from the pre-existing, unrelated _guarded() skip by returning a different string:

def apply_classification(vault, note, decision, reason) -> str:
    if _guarded(note):
        return "skipped"
    new_status = _DECISION_STATUS.get(decision, "needs_review")
    tag = f"[triage {date.today().isoformat()}]"
    wrote = vault.update_fields(
        note.ref, {"status": new_status},
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
        require_status=frozenset(_status.TRIAGE_OWNED))
    return "applied" if wrote else "skipped-race"
```

`apply_verdict` gains the identical `require_status=` argument and the identical
`return "applied" if wrote else "skipped-race"` change.

**`"skipped-race"`, not a reused `"skipped"`** — two round-3 findings (`arch3-001`, `inv3-001`,
independently converging on the same gap from different angles) showed that simply reusing
`"skipped"` here was not enough. `engine.py`'s existing loop unconditionally calls `_audit(...)`
with the never-applied `decision`/`reason` after computing `outcome`, for every outcome — including
the pre-existing `dry_run` "skipped" case, where that's the *desired* behaviour (it's how a dry
run's in-memory preview gets populated; `_audit`'s persisted half is separately gated on `not
dry_run`, so a dry-run entry is never written to disk in the first place). But for a **real**
(non-dry_run) run, invariant review traced the consequence precisely: a race the CAS guard
correctly stops from touching the vault still produces a *persisted* audit-log entry claiming a
reject/needs_review decision for a lead the vault correctly still shows as `applied` —
`render_rejected_note` renders straight from that log, so a human-facing "rejected leads" summary
can list a job as dismissed while the note itself says otherwise. The pre-existing `_guarded()`
skip (which predates this design and returns plain `"skipped"`, unchanged) has the same latent
shape, but fixing that is explicitly out of scope here (see "Out of scope" below) — this fix is
scoped to the *new* race this design's own tier-2 fetch introduces, not a general sweep.

In `engine.py`'s classify-pass and judge-pass loops: `report.counts` still buckets `"skipped-race"`
under `"skipped"` (unchanged aggregate stat — the lead genuinely wasn't decided this run), but
`_audit(...)` is now called only when `outcome not in ("skipped-race",)`, closing the false-entry
gap, and a `report.failures` entry is added specifically for `"skipped-race"` (mirroring the
company write's own cause-agnostic message), giving an operator the same visibility into this race
that the company write already has — closing `arch3-001`'s asymmetry finding directly.

Existing tests are unaffected: none construct the "lead exits `TRIAGE_OWNED` between read and
write" race this guard specifically catches, and none depend on `apply_classification`/
`apply_verdict` ever returning anything but `"applied"` or the pre-existing `"skipped"` — that
shape is deliberately exercised only by the new race test named below, mirroring how `#9`'s own
`require_status` race test was built. This is judged in-scope for #109 rather than a deferred
follow-up: it's the same existing parameter `update_fields` already carries (shipped by `#9`),
applied to two calls that already sit on the code path this design's own change widens — "address
as you find it," not a new mechanism.

**A narrower, accepted ambiguity remains, deliberately unaddressed** (`rev3-002`, round 3): `wrote`
can be `False` for the same two reasons discussed for the company write above — a genuine
`require_status` mismatch, *or* a benign "the note already holds this value" no-op (realistic here
too: a same-day re-triage of a `needs_review` lead recomputing an identical decision — a scenario
this design itself makes newly common, since it's what makes repeated `--status needs_review`
re-runs useful). Both collapse to `"skipped-race"` and both get bucketed under `report.counts["skipped"]`,
even though the already-current case reflects a lead whose real status is already correct. This is
the same ambiguity `rev2-001` named and accepted for the company write, applied consistently here
rather than re-litigated: distinguishing the two causes would require `update_fields` to expose
*why* it returned `False`, a broader contract change out of scope for this design. No data is at
risk either way — the vault's status is correct in both cases — only the printed run summary is
mildly imprecise for the already-current sub-case, exactly as already accepted elsewhere in this
design.

### Tier 1 — `Source.company_from_url`

An optional `Source` protocol member. `ingest/base.py:46-54`'s `Source(Protocol)` currently carries
no docstring at all — **corrected in round 2 (`arch2-003`)**, an earlier draft wrongly described
this as "a fifth line" in an existing docstring. It gets a *new* docstring, in the same
optional-member documentation style `Store`'s (`core/protocols.py:136-165`) already uses for
`preflight`:

```python
def company_from_url(self, url: str) -> str | None: ...
```

Implemented only where a board's real URL shape unambiguously encodes a company with a clear
delimiter on both ends of the slug. **At least one source ships in this pass** (`rev-003` — an
earlier draft deferred every source to implementation time with no committed minimum): **Wellfound**,
whose card links already carry `/company/<slug>` — `wellfound.py`'s extractor JS matches
`a[href*="/company/"]` — an unambiguous shape, delimited by the literal `/company/` segment on one
side and the next `/` or end-of-string on the other. Illustrative pattern, **not** a specification
to implement against directly: `r"^https?://(?:www\.)?wellfound\.com/company/([a-z0-9-]+)"`, the
captured slug de-hyphenated and title-cased.

**This is now a hard gate, not an aside** (`rev2-002`, round 2 — round 1's "to be verified... not
copied verbatim" language appeared nowhere in Testing, Mutation witnesses, Dependency order, or
Definition of Done, so nothing stopped an implementer from copying the illustrative regex verbatim
and writing fixtures crafted to match it, self-certifying an unverified pattern). Dependency order
below now names the real capture as a blocking first step for Wellfound specifically, before its
golden fixtures are written.

Additional sources may qualify once their real captures are examined during implementation — a
shape with only one clean boundary (e.g. a flat `company-role-words` slug with no terminator) is
left unimplemented rather than implemented with a guessed split point, per the abstain-over-guess
principle above. `getattr(source, "company_from_url", None)` being absent is tier 1 abstaining for
that source, same as today.

### Tier 2 — dossier fetch + page extraction

Not source-specific — lives in `resolve.py` as `_from_dossier`. The existing fetch closure
(`core/app.py:435-481`) is extended to also capture, in the same already-open tab:

- `document.title`
- the text content of any `script[type="application/ld+json"]` tag

added to the dossier dict alongside the existing `jd.markdown` (`core/app.py:481`,
`dossier.py:54-65` — `DossierCache` gains two new optional keys, `page_title` and
`structured_data`, both defaulting to `""` so old cached dossiers without them still parse).
`_from_dossier` tries, in order:

1. Parse `structured_data` as JSON-LD, look for a `schema.org/JobPosting` node, read
   `hiringOrganization.name`. Structured, board-authored, highest confidence.
2. A small set of well-known `"<role> at <Company> | <Board>"` / `"<Company> hiring <role>"`
   title-shaped patterns against `page_title`.
3. Otherwise `None`.

**The title-pattern heuristic (step 2) is held to the same confidence bar as tier 1** (`inv-002`,
`neu-001`). Only specific, real-capture-validated shapes qualify; a `page_title` that superficially
resembles but does not cleanly match a known pattern (contains "hiring" or "at" without the
expected structure) abstains rather than guesses, proven by a dedicated near-miss test and
mutation-witness row.

**The two new dossier fields are excluded from what reaches the judge** (`arch-003` — round 1's
draft added them to the same dict `dossier.slim()` forwards, uncapped, into every judge prompt;
`slim()` today caps only `jd.markdown` at `jd_limit=4000` chars and strips only `lead_snapshot`, so
raw JSON-LD — several KB on some boards — would have flowed uncapped into every `keep` lead's
judge call, not just tier-2-resolved ones). `slim()` (`dossier.py:17-23`) additionally drops
`page_title` and `structured_data` from its output: they are resolution-only fields, not
judge-relevant. This exclusion belongs in `slim()`, not at storage time in `get_or_build` — the two
fields must still be *present* in what `get_or_build` returns, since `_from_dossier` reads them
directly off that return value; `slim()`'s existing job is exactly "what the judge needs, not what's
cached," making it the correct seam.

**"One page visit total" needed a real fix, not just a claim** (`test2-002`, round 2, High —
empirically falsified by running the real cache-key logic, not merely reasoned about). The original
claim: because this is the *same fetch* a `keep` verdict needs anyway for judging, and
`DossierCache.get_or_build` is TTL-cached, a lead that resolves via tier 2 and ends up `keep` costs
exactly one page visit total. That's false as originally specified: `DossierCache.cache_key`
(`dossier.py:33-34`) is `lead.get("lead_id") or _slug(lead)`, and `_slug` derives from
`company`+`role`. Real vault frontmatter never carries `lead_id` (confirmed: it isn't among the
fields `_render_new` writes), so `cache_key` always falls back to `_slug` for a triage-sourced
lead — and `_slug({"company": "", "role": "Staff Engineer"})` differs from
`_slug({"company": "Example Co", "role": "Staff Engineer"})`. The classify-pass tier-2 fetch runs
while `company` is still blank; by the time the enrich pass calls `get_or_build` again, this
design has already mutated `note.fm["company"]` to the resolved value — a *different* cache key, a
cache miss, and a second real fetch.

**Fix:** `DossierCache.cache_key` now prefers a stable hash of the lead's `url` over the
company/role slug, since `url` doesn't change across the mutation this design performs and is
already this codebase's identity signal of choice for "the same posting" elsewhere (the url-proven
matching in `core/vault.py`):

```python
# core/dossier.py
import hashlib

def cache_key(self, lead: dict) -> str:
    lead_id = lead.get("lead_id")
    if lead_id:
        return lead_id
    url = lead.get("url")
    if url:
        return "url-" + hashlib.sha256(url.encode()).hexdigest()[:16]
    return _slug(lead)
```

Every lead that reaches tier 2 is guaranteed to carry a non-empty `url` — `resolve_company` already
requires it (`if no_llm or not company_resolve_fetch or not url: return None`) — so this is exactly
the case the fix targets. A lead with no `url` at all (a Google lead, per `#23`) never reaches tier
2 in the first place and keeps falling back to `_slug`, unchanged. **Accepted, bounded cost:**
changing `cache_key`'s derivation invalidates every dossier currently cached under the old
company/role-slug scheme, once — the next run for each such lead is a cache miss and a real
re-fetch, same as if the cache directory had been cleared. `DossierCache` is a pure performance
optimisation bounded by `ttl_days`, not a correctness-bearing store, so a one-time invalidation
wave is a cost, not a bug; nothing else in `core/dossier.py` parses cache filenames as anything but
an opaque key.

**This cache is shared with `cv`, not triage-exclusive** (`arch3-002`, round 3): `core/app.py`'s
`dossier_cache()` docstring states "the ONE dossier cache directory, for both triage and cv (`#80`)",
and `cv/engine.py`'s `run_one` calls the same `get_or_build`. The one-time invalidation wave above
sweeps cv's cached dossiers too, permanently re-keyed thereafter — functionally benign (a shortlisted
lead reaching `cv` already carries a stable, non-blank `company`, so its key was already stable
before this fix), but worth stating rather than leaving as an unacknowledged cross-sub-app effect.

A lead that resolves via tier 2 and still ends up `reject` (its now-known company matches
`reject_companies`) or `needs_review` (some other classify branch) leaves a now-unused cached
dossier on disk until its TTL expires — accepted, matching how the existing cache already tolerates
a `keep` that never gets judged in a `--no-llm` run.

### The vault write

The write itself: `update_fields(note.ref, {"company": f'"{resolved}"'},
require_status=frozenset(_status.TRIAGE_OWNED))`, quoted the same way `_render_new` originally
writes the field (`vault.py:1863`) and the way `apply_verdict` already quotes
`glassdoor_rating`/`culture_flags` (`triage/apply.py:43-44`) — now additionally guarded by
`resolve_company`'s own `_safe` check against frontmatter-structural characters (see "Guarding the
write against scraped content" above). It happens, when it happens, *before* the re-classified
decision is computed — not folded into `apply_classification`'s own write, since that write only
fires from the *re-classified* decision and only for `reject`/`needs_review` (never `keep`), while
this write must be attempted for all three eventual outcomes.

`_status.TRIAGE_OWNED` (`core/status.py:14`, imported in `engine.py:12` as `_status` already)
guards the same race the `#9` staleness feature's `require_status` param exists for: if the lead
entered the application lifecycle between `read_leads` and this write (a receipt, a manual `apply
record`), the write abstains — returns `False`, writes nothing. Never-clobber is preserved by
construction, not by a caller-side check (`update_fields`'s own docstring: "this CANNOT be
delegated to the caller"). Both `False` causes — a genuine `require_status` mismatch and a benign
"the value already matched" no-op — are handled by the single rule stated in "Architecture" above:
no re-classification, a `report.failures` entry with a cause-agnostic message, and this lead makes
no progress on this run, retried clean next time (or, for the already-matched case, self-healing
the very next run — see above). **The very next write on this same code path
(`apply_classification`) needed the identical hardening — see "Hardening `apply_classification`/
`apply_verdict`" above; without it, this write's own protection would have been necessary but not
sufficient.**

### Backlog

No new CLI surface. `job-sluice triage run --status needs_review` already exists and now benefits:
every backlogged lead gets one real resolution attempt on its next explicit re-run against this
code. Widening the default `--status` to include `needs_review` is explicitly not part of this fix
(the issue rules it out directly — re-running against an *unchanged* company reproduces the
identical verdict forever; this design is what makes the company non-unchanged).

## Testing

Behaviour-asserting, offline except the tier-2 fetch tests (which use the existing injected-fetcher
harness, never real Camofox). Fixture URLs use the `example.invalid` / `example-co` family, matching
the existing neutrality convention (`tests/conftest.py`, `#31`/`#53`).

- **`resolve_company` unit tests:** tier-1 hit never calls the dossier cache (a recording fake
  `DossierCache` asserting zero `get_or_build` calls); tier-1 miss falls through to tier 2; both
  miss returns `None`; `get_source=None` skips tier 1 unconditionally (uniform abstain, the default
  every non-resolution-aware caller gets); `no_llm=True` never calls the dossier cache even when
  tier 1 would have missed; `company_resolve_fetch=False` (the shipped default) never calls the
  dossier cache either, independent of `no_llm`; an unknown `source` id (`get_source` raising
  `KeyError`) abstains rather than raising; a `dossier_cache.get_or_build` exception abstains
  rather than propagating; **a `company_from_url` implementation raising an arbitrary exception
  abstains rather than propagating** (`rev3-001`, round 3 — the tier-1 counterpart to the existing
  tier-2 fetch-exception test).
- **`TriageConfig().company_resolve_fetch is False`, and `load_triage_config()` with no config
  file yields `False`** (`neu3-001`, round 3) — a dedicated pinning test, named the same way
  `lead_ttl_days`/`lead_layout`'s own abstain-default guards are, so a later edit cannot silently
  re-flip the round-2 default correction (`inv2-003`) without a test going red.
- **Scraped-content safety (`inv2-002`):** a tier-1 extractor and a tier-2 `_from_dossier` result
  each containing an embedded `"`, `\n`, **or `\r`** (round 3, `test3-002` — the third `_UNSAFE_CHARS`
  member, previously unnamed here) are rejected — `resolve_company` returns `None`, not the unsafe
  candidate — for both tiers independently.
- **Per-source `company_from_url` golden fixtures:** Wellfound's confident-match case and its
  abstain case (a `wellfound.com` URL without a `/company/` segment), mirroring the existing
  golden-parser-fixture convention. **Built from a real capture, not the illustrative regex above**
  (`rev2-002`) — see Dependency order below. Any additional source implemented during
  implementation gets the same pair, same rule.
- **`_from_dossier` unit tests:** a JobPosting JSON-LD hit; a title-pattern hit when structured data
  is absent; **JSON-LD wins when both structured data and a matching title pattern are present and
  disagree** (`test3-001`, round 3 — the priority between the two tier-2 extraction methods was
  previously asserted in prose only, with no test proving it); a title-pattern **near-miss** that
  abstains rather than guessing (a `page_title` containing "hiring" or "at" without the expected
  structure); both absent or unparseable → `None`; malformed JSON-LD → `None`, not a raised
  exception.
- **A regression test pinning `classify()`'s signature** — it must not gain a `dossier_cache`,
  `sources`, or `fetcher` parameter, protecting the docstring's "no dossier, no LLM" contract the
  same way `test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar` protects the
  renderer seam's optionality.
- **`DossierCache.cache_key` tests (`test2-002`):** a lead with a `url` produces the *same* key
  before and after its `company` field changes (the fix's core property); a lead with a `lead_id`
  still prefers it over `url`; a lead with neither falls back to `_slug`, unchanged from today.
- **`engine.py` integration tests, covering the restructured ordering:**
  - A lead with a blank company that `classify()` would reject on title/location/pay grounds
    regardless never triggers resolution — a recording fake `dossier_cache` asserts zero
    `get_or_build` calls. This is the test that actually *proves* the `arch-001`/`rev-002` cost-
    neutrality fix, not merely a re-statement of it.
  - A blank-company lead that resolves via tier 1 and would now be `reject` under
    `reject_companies` ends up `dismiss`, not stuck at `needs_review`; the same lead under tier 2.
  - `--no-llm` leaves a tier-1-miss lead unresolved and still reaching the existing blank-company
    `needs_review` branch. `company_resolve_fetch=False` (the default) does the same, independent
    of `--no-llm`.
  - A `VaultConflict`, and separately a `require_status` abstain (the lead advances to an
    `APPLICATION_OWNED` status between `read_leads` and the company write — mirroring `#9`'s
    `require_status` race test construction), on the company write both leave the lead's decision
    as classify()'s original `needs_review` result, unchanged, with a `report.failures` entry —
    proving both failure shapes converge on one behaviour, not two different, untested ones. A
    third variant asserts the "value already current" no-op case does **not** produce a misleading
    lifecycle-exit claim in the log message (`rev2-001`).
  - **The `apply_classification`/`apply_verdict` hardening race** (`inv2-001` — the Critical fix):
    a lead advances to `applied` (or is judged concurrently to an `APPLICATION_OWNED` status)
    between `read_leads` and `apply_classification`'s/`apply_verdict`'s own write; both must return
    `"skipped-race"`, the vault's status must remain `applied`, and neither writes `needs_review`/a
    verdict status over it. Constructed the same way `#9`'s own `require_status` race test is.
  - **The audit log carries no false entry for that same race** (`arch3-001`/`inv3-001`, round 3):
    in the same scenario, `audit.read_recent(30)` (what `render_rejected_note` renders from) must
    contain **no** entry for that lead — proving the `_audit(...)` gate on `outcome not in
    ("skipped-race",)` actually prevents the persisted false record, not merely that the vault
    status survives. A companion assertion confirms `report.failures` **does** gain an entry for
    this case, distinct from the pre-existing, unrelated `_guarded()`-at-read-time skip (which
    remains untouched and silent, per "Out of scope" below).
  - Under `dry_run`, a lead that resolves and would become `keep` shows the correct bump in
    `report.counts["keep"]`; a lead that resolves and would become `reject`/`needs_review` is
    **not** distinguishable in `report.counts` from an unresolved lead (both bucket to `"skipped"`)
    — asserting the corrected, narrower claim from `test2-001`, not the original overstated one.
  - **Cache reuse across the classify and enrich passes:** a recording fake fetcher proves a lead
    resolved via tier 2 and then re-classified to `keep` triggers exactly one fetcher call across
    *both* the classify-pass resolution and the later enrich-pass judge build — the test that
    actually proves the "one page visit total" cost claim, now that `cache_key` makes it true.
- **Dossier schema tests:** an old cached dossier JSON missing `page_title`/`structured_data`
  still loads via `get_or_build`'s existing freshness check without raising; `slim()` output never
  contains `page_title`/`structured_data`, asserted against a fixture where **both fields are
  non-empty** (an empty-fields fixture wouldn't distinguish "excluded" from "was never populated").

### Mutation witnesses

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Mutate by moving or deleting, never adding.

| Mutant | Must redden |
| --- | --- |
| Move the resolve attempt to run before `classify()` (or drop the `decision == "needs_review" and not company` guard) | the zero-`get_or_build`-calls test for a title/location/pay-reject blank-company lead |
| Delete the `if not dry_run` guard around the write | the dry-run-writes-nothing test for a resolvable lead |
| Delete the `no_llm` guard before the tier-2 fetch | the `--no-llm` never-calls-dossier-cache test |
| Delete the `company_resolve_fetch` guard before the tier-2 fetch | the `company_resolve_fetch=False` (default) never-calls-dossier-cache test |
| Delete `require_status=` from the company write | the `require_status`-abstain racing test (lead advances to `applied` between read and write; company write must not land) |
| Delete `require_status=` from `apply_classification`'s write | the `apply_classification` hardening race test — the Critical fix (`inv2-001`) |
| Delete `require_status=` from `apply_verdict`'s write | the equivalent `apply_verdict` hardening race test |
| Delete the `_safe(...)` check on either tier's candidate | the scraped-content-safety test for that tier |
| Delete `cache_key`'s url-hash preference (fall back to `_slug` unconditionally) | the cache-key-stable-across-company-mutation test, and the cache-reuse integration test |
| Swap tier order (try tier 2 before tier 1) | the tier-1-hit-skips-dossier-cache test |
| Delete the JSON-LD parse's `try/except` | the malformed-structured-data-abstains test |
| Loosen the title-pattern regex boundary | the title-pattern near-miss abstain test |
| Delete the `if hit:` guard so a `company_from_url` returning `""` is treated as resolved | the `resolve_company`-level "tier-1 miss falls through to tier 2" test — **not** the per-source golden fixture, which calls `company_from_url` directly and never reaches `resolve.py`'s orchestration |
| Delete `slim()`'s `page_title`/`structured_data` exclusion | the `slim()`-never-contains-those-keys test (built from a fixture with both fields populated) |
| Delete the `except KeyError` around `get_source(src_id)` | the unknown-source-id-abstains test |
| Delete the `except Exception` around `dossier_cache.get_or_build` inside `resolve_company` | the fetch-exception-abstains test |
| Delete the `except Exception` around `extractor(url)` inside `resolve_company` | the extractor-exception-abstains test (`rev3-001`) |
| Swap the two extraction attempts inside `_from_dossier` (title-pattern before JSON-LD) | the JSON-LD-wins-when-both-present test (`test3-001`) |
| Change `apply_classification`'s/`apply_verdict`'s `"skipped-race"` back to plain `"skipped"` | the audit-log-has-no-false-entry test for the hardening race (`inv2-001`/`inv3-001`) |
| Delete the `outcome not in ("skipped-race",)` guard around `_audit(...)` | the same audit-log-has-no-false-entry test |
| Delete `TriageConfig.company_resolve_fetch`'s `False` default (change to `True`) | the `TriageConfig().company_resolve_fetch is False` pinning test (`neu3-001`) |

## Docs

- `docs/ARCHITECTURE.md`: the `Source` protocol gains a new docstring documenting the optional
  `company_from_url` member, in the same style `Store`'s docstring already documents `preflight`;
  the dossier fetch closure's description (a new addition, since no such section currently exists
  at this granularity) gains the two new captured fields, the `slim()` exclusion, and the
  `cache_key` change; `Sluice.triage()`'s description in the composition-root section notes it now
  also threads `sources.get` into `triage.engine.run`, the same way it already threads
  `dossier_cache`/`backend`. The "five sub-apps" narrative (`ARCHITECTURE.md:167-170`) currently
  frames triage's classify pass as resolving obvious cases "for free" before anything is enriched —
  no longer fully accurate once a real page visit can happen mid-classify-pass for a blank-company
  lead, so that paragraph needs a caveat; `dossier.py`'s one-line module description
  (`ARCHITECTURE.md:126-127`) needs updating for the schema addition.
- **`docs/CONFIGURATION.md`** *(added in round 2, `arch2-001`)*: a new row in the `triage:` table
  for `company_resolve_fetch` (default `false`), stating its off-by-default rationale the same way
  the config-key reference already documents every other `TriageConfig` field.
- `.rulesync/rules/CLAUDE.md`: not touched by this spec directly — flagged for a follow-up edit
  once implemented, since the CV-fabrication-gate section's "abstain over guess" framing now has a
  sibling instance worth cross-referencing, and `.rulesync/` is self-edited per standing project
  convention.
- `sluice.yaml.example`: the new `triage.company_resolve_fetch` knob, commented out at `false` —
  matching the file's existing convention for opt-in feature toggles (`lead_ttl_days`), not the
  "active illustrative value" convention reserved for preference knobs a user is expected to tune.
- **`core/vault.py`'s `_set_fm` docstring** *(added in round 3, `arch3-003`)*: gains one clause
  noting that a caller writing unmediated external content is responsible for its own
  structural-character guard before the value reaches `_set_fm` — pointing at `resolve.py`'s
  `_safe` as the precedent. `_safe` is correctly placed in `resolve.py`, not inside `_set_fm`
  itself: `_set_fm`'s `literal` argument is already the post-quote string (e.g. `f'"{resolved}"'`),
  so a blanket character check at that layer would reject the wrapping quotes every existing
  quoted caller (`glassdoor_rating`, `culture_flags`) already relies on. The check is only
  meaningful pre-quote, which only the caller holds — this docstring clause is a warning for the
  next raw-content writer, not a design change.

## Definition of done

```bash
ruff check sluice tests
python -m pytest
```

Dependency order:

1. `DossierCache`'s schema addition (`page_title`/`structured_data`), the `cache_key` url-hash
   preference, and `slim()`'s new exclusion — nothing else *in tier 2* can be tested against them
   otherwise.
2. A real `job-sluice ingest test-source wellfound --raw` capture, *before* Wellfound's
   `company_from_url` and its golden fixtures are written (`rev2-002`) — the illustrative pattern
   above is not a specification. **Independent of step 1** (`rev3-003`, round 3): Wellfound's
   `company_from_url` is a pure tier-1 URL-string extractor that touches neither `DossierCache`,
   `page_title`, `structured_data`, nor `cache_key` — it may be done before, after, or in parallel
   with step 1.
3. `resolve.py`, including the `_safe` scraped-content guard and the tier-1 extractor's own
   `try/except` isolation — this step genuinely depends on both 1 and 2.
4. The `engine.py`/`Sluice.triage()` `get_source`-threading and call-site wiring, verified early
   against the existing `tests/test_triage_engine.py` call sites, which must stay green unmodified
   given `get_source`'s default.
5. The `apply_classification`/`apply_verdict` `require_status` hardening (`inv2-001`) — independent
   of the rest and could in principle land first, but ordered last here only because it's most
   easily tested once the rest of the race-test scaffolding (from step 4's `require_status` tests)
   already exists to mirror.

## Out of scope

**No LLM-based company guessing.** Both tiers are deterministic extraction, never inference.

**No change to `--status` defaults.** Ruled out directly by the issue.

**No backfill command.** The existing `--status needs_review` flag already covers the backlog.

**No change to `classify()`'s signature or purity contract.**

**Not every source gets `company_from_url` in this pass — only Wellfound is committed.** Other
sources whose real captures turn out unambiguous during implementation are a small addition, not a
new mechanism; a source left without it simply keeps relying on tier 2.

**No broader audit of every other stale-`note.status` `_guarded()` call site beyond
`apply_classification`/`apply_verdict`.** This design fixes the two writes on the path it directly
widens. If other pre-existing call sites share the same stale-guard shape, that's a separate,
dedicated sweep — flagged here, not silently absorbed into #109's scope.

## The residual

Tier 2 still can't resolve a lead whose page genuinely never states the employer (a recruiter
posting on behalf of an undisclosed client, a since-removed listing). That lead correctly stays
`needs_review` — the honest outcome this design does not try to remove, only to earn.

## Changelog

**2026-08-10, round 1 post-`/review-plan`:** 5 reviewers, 0 Critical / 9 High / 6 Medium / 1 Low,
all addressed — restructured resolution to run after classify()'s free rejects, gated the write
behind `dry_run`, resolved a VaultConflict-handling self-contradiction, made `get_source`
keyword-only with a safe default, excluded new dossier fields from the judge prompt, added the
`company_resolve_fetch` knob, committed Wellfound as the required tier-1 source, named two missing
tests, held the title-pattern heuristic to tier 1's confidence bar, fixed a mutation-witness
attribution, and closed two docs/snippet gaps.

**2026-08-10, round 2 post-`/review-plan`:** 5 reviewers (re-verifying round 1's fixes against the
actual text, not the changelog's claims), 1 Critical / 4 High / 5 Medium / 2 Low, all addressed:

- **Critical:** extended `require_status` hardening to `apply_classification`'s and
  `apply_verdict`'s existing writes, closing a real never-clobber gap this design's own tier-2
  fetch widened (`inv2-001`).
- **High:** added a `_safe` guard rejecting frontmatter-structural characters in a resolved
  company, since this is the first raw, unmediated open-web content ever written to frontmatter in
  this codebase (`inv2-002`).
- **High:** flipped `company_resolve_fetch`'s default from on to off, matching this codebase's own
  `lead_ttl_days`/`lead_layout` precedent for opt-in feature toggles (`inv2-003`, escalated as a
  genuine values trade-off and decided by the user).
- **High:** corrected the false claim that `dry_run`'s `report.counts` previews the reject/
  needs_review outcome — it doesn't, unchanged pre-existing behaviour; only the `keep` count is
  meaningfully more accurate (`test2-001`).
- **High:** fixed `DossierCache.cache_key` to prefer a url-hash over the company/role slug, closing
  a real gap where the "one page visit total" cost claim was empirically false — the classify-pass
  and enrich-pass fetches landed under different cache keys and always double-fetched (`test2-002`).
- **Medium:** added `docs/CONFIGURATION.md` to the Docs section (`arch2-001`); stated `resolve.py`'s
  module-scope import explicitly (`arch2-002`); distinguished the "value already current" no-op
  from a genuine `require_status` abstain in the log message and self-healing behaviour (`rev2-001`);
  turned the Wellfound real-capture requirement into a hard Dependency-order gate (`rev2-002`); added
  three missing mutation-witness rows (`slim()` exclusion, both abstain-on-exception guards)
  (`test2-003`).
- **Low:** corrected a factual claim about an existing (non-existent) `Source` Protocol docstring
  (`arch2-003`); added the actual updated `run()` signature to the code sketch (`rev2-003`).

**2026-08-10, round 3 post-`/review-plan`:** 5 reviewers, specifically directed to scrutinize round
2's own new fixes hardest — since round 2's Critical finding was itself caused by round 1's fix.
0 Critical / 3 High / 4 Medium / 3 Low, all addressed. No new Critical, but two reviewers
(architect, invariant) independently converged on the same real gap in round 2's own Critical fix
from different angles:

- **High (two reviewers, one fix):** the `require_status` hardening on `apply_classification`/
  `apply_verdict` correctly stops the vault clobber, but the surrounding `engine.py` loop still
  unconditionally wrote a *persisted audit-log entry* claiming the never-applied decision — which
  `render_rejected_note` renders into a human-facing summary, so a race the fix successfully aborts
  at the data layer could still make a real application look dismissed in that summary. Fixed by
  having `apply_classification`/`apply_verdict` return a new, distinct `"skipped-race"` outcome
  (rather than reusing plain `"skipped"`), gating the audit call on it, and adding a
  `report.failures` entry for visibility — deliberately scoped to only the *new* race this design
  introduces, not a general sweep of the pre-existing, unrelated `_guarded()` skip (`arch3-001`,
  `inv3-001`).
- **High:** wrapped the tier-1 `company_from_url` extractor call in the same exception isolation
  the tier-2 fetch already has — newly-authored, per-source regex code running against live
  scraped URLs had no guard, so one source's bug on one unanticipated URL shape could have crashed
  the entire `triage run` (`rev3-001`).
- **Medium:** explicitly accepted (rather than re-engineered) the same `wrote=False`
  cause-ambiguity already named for the company write, now also present on the hardened
  `apply_classification`/`apply_verdict` writes — no data risk, only a mildly imprecise
  `report.counts` bucket for an already-correct lead (`rev3-002`). Acknowledged the shared
  `DossierCache` directory means the `cache_key` fix's one-time invalidation also sweeps `cv`'s
  cached dossiers, functionally benign but previously unstated (`arch3-002`). Added a dedicated
  test pinning `TriageConfig().company_resolve_fetch is False`, matching this codebase's own
  convention for abstain-default regression tests (`neu3-001`). Added a test proving JSON-LD wins
  over the title-pattern heuristic when both are present and disagree, previously asserted in prose
  only (`test3-001`).
- **Low:** corrected the Dependency order's implied step-1-before-step-2 dependency — Wellfound's
  `company_from_url` is independent of the `DossierCache`/`cache_key` work (`rev3-003`); added a
  docstring clause to `_set_fm` warning future raw-content writers about the responsibility `_safe`
  discharges here (`arch3-003`); named the third `_UNSAFE_CHARS` member (`\r`) explicitly in the
  scraped-content-safety test description, previously only two of three were named (`test3-002`).
