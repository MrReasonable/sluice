# Triage company-name resolution — classify() names the fix but never attempts it (#109)

**Status:** design approved 2026-08-10; revised after `/review-plan` (5 reviewers: 0 Critical, 9
High, 6 Medium, 1 Low — all addressed in this revision, see the changelog at the bottom).

**Issue:** #109 — `triage: classify() names the fix for a blank company but never attempts it,
and needs_review is a one-way trap`
**Sub-apps:** `triage` (the new resolution step, called from `engine.py`), `ingest` (an optional
new `Source` capability), `core` (extends the existing dossier fetch closure in `core/app.py`)

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

5. **A dedicated config knob gates tier 2, independent of `--no-llm`.** *(added in review — arch-002)*
   `TriageConfig` gains `company_resolve_fetch: bool = True`, plus a matching commented entry in
   `sluice.yaml.example`. Tier 2 runs only when `company_resolve_fetch and not no_llm`. It defaults
   **on**, which is a deliberate departure from this codebase's usual "empty config abstains"
   posture: that posture governs *preference* gates, where an unconfigured install must let
   everything through. This is a *feature* toggle instead, and a feature that ships off by default
   leaves the reported 411-lead backlog exactly as broken as before for every fresh install — the
   whole point of the design. A user who wants the LLM judge but not the extra Camofox tabs that a
   large blank-company backlog can generate sets `company_resolve_fetch: false` explicitly.

A principle underneath all five, driving several details below: **a wrong company is worse than a
blank one.** Blank explicitly signals "unknown, a human should look" — the honest state. A
mis-extracted name would silently look like ground truth and carry through `keep` → judge → apply
→ a CV addressed to the wrong employer, which is the same asymmetric-risk shape as the pay-floor
and location gates already in `classify.py` (abstain on anything not credible, never guess).
Neither tier writes anything below a confident, structurally-justified match. The same posture
governs the write path itself: a resolution whose write cannot be confirmed (a race, a dry run)
is treated as not having happened at all for this run — see "Architecture" below, which resolves
a real self-contradiction review found in the first draft of this rule.

## Architecture

`classify()` is untouched — it stays exactly what its docstring already promises: pure, no
dossier, no LLM (`classify.py:1-7`), and it still runs **first, unconditionally, for every note** —
cheap, per its own docstring ("resolves the obvious cases for free"). Resolution is a follow-up
step in `triage/engine.py`'s classify loop (`engine.py:51-75`), attempted only when the result is
*specifically* the blank-company `needs_review` branch — never ahead of classify()'s existing
title/company/location/pay-floor rejects, which don't depend on company at all:

```python
# triage/engine.py, replacing the existing
# `decision, reason = classify(note.fm, cfg)` line inside the "for note in notes:" loop:
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
                        f"company-resolve {note.ref}: left triage lifecycle mid-resolve")
        if wrote or dry_run:
            note.fm["company"] = resolved
            decision, reason = classify(note.fm, cfg)
```

Four things this restructuring fixes, each a real review finding, not a stylistic choice:

- **Cost neutrality is now actually true, not just claimed** (`arch-001`/`rev-002`, found
  independently by two reviewers). Gating on `decision == "needs_review"` — the result of a
  `classify()` call that already ran classify()'s existing free rejects — means a lead that would
  be rejected on title, location, or pay regardless of company never triggers a tier-2 page visit.
  The earlier draft attempted resolution the moment `company` was blank, *before* classify() had a
  chance to reject the lead for an unrelated reason, so a reject-anyway lead paid for a fetch that
  resolved nothing useful. The gate is `decision == "needs_review" and not company`, not reason-string
  matching against classify()'s message text — robust to a future classify() branch that produces
  `needs_review` for some other reason, since that case would see `company` already non-blank and
  skip resolution regardless of the decision value.
- **`dry_run` is honoured** (`inv-001`). Only the `vault.update_fields` call is skipped under
  `dry_run` — the resolution computation itself (tier 1 and tier 2) still runs, matching the
  existing precedent of the enrich pass, which already builds real dossiers under `dry_run` for
  reporting purposes and skips only `apply_verdict`'s write. Re-classification still happens on a
  dry run (see the `wrote or dry_run` condition) so a preview run's `report.counts` reflect what a
  real run would decide, without persisting anything.
- **The VaultConflict/require_status ambiguity is resolved, unambiguously, one way** (`test-001`).
  An earlier draft of this design stated the post-failure behaviour two different, contradictory
  ways in two different sections. There is now exactly one rule, visible directly in the code
  above: **re-classification happens if and only if the write landed, or this is a dry run (which
  never attempts a write in the first place).** On a real run where the write fails — either a
  raised `VaultConflict` from a genuine concurrent edit, or a silent `False` from `require_status`
  finding the lead has left `TRIAGE_OWNED` between the read and this write — `decision`/`reason`
  keep whatever classify() computed on its first, unconditional call, against the *unresolved*
  company. Both failure shapes get a distinct `report.failures` entry (for visibility — an operator
  can tell "a write race happened" from "the lead left triage's hands mid-resolve") but neither
  changes this run's outcome for that lead. This is the same answer test-engineer's review
  recommended, generalized to fall naturally out of the new classify-first structure rather than
  needing its own special case.
- **`get_source` no longer breaks the existing test suite** (`rev-001`). It's a new
  **keyword-only** parameter on `triage.engine.run`, defaulting to `None` — not a required
  positional argument "alongside `dossier_cache`/`audit`" as an earlier draft implied. `resolve.py`
  treats a `None` `get_source` the same as an unrecognized source id: tier 1 uniformly abstains.
  The six existing direct calls to `run(...)` in `tests/test_triage_engine.py`, none of which pass
  a `get_source`, are unaffected by construction; only `Sluice.triage()`'s own call site, and any
  *new* test that specifically exercises resolution, need to pass a real one.

`Sluice.triage()` (`core/app.py:786-815`) gains the same lazy, inside-the-method import its
`ingest()` neighbour already uses for `ingest.base`/`ingest.engine` (`core/app.py:508-509`) —
`from sluice.ingest import sources` — and passes `sources.get` as the new keyword argument to
`_triage_run`, exactly the way it already passes `cache = self.dossier_cache(...)`. `triage/`
still never imports `sluice.ingest` directly: the pipeline's sub-app dependency direction
(`ingest -> triage -> cv -> apply -> track`) only crosses at the composition root, and
`triage/engine.py` itself only ever sees a callable.

`resolve_company` is a thin orchestrator:

```python
def resolve_company(fm: dict, get_source, dossier_cache, *,
                     no_llm: bool, company_resolve_fetch: bool = True) -> str | None:
    """Tier 1 then tier 2, first confident match wins. Returns None -- never a guess --
    when both abstain. `get_source` is `sluice.ingest.sources.get` (or None, meaning tier
    1 always abstains), injected so this stays testable without importing the real
    registry."""
    url = fm.get("url") or ""
    src_id = fm.get("source") or ""
    if get_source is not None and url and src_id:
        try:
            source = get_source(src_id)
        except KeyError:
            source = None
        extractor = getattr(source, "company_from_url", None)
        if extractor:
            hit = extractor(url)
            if hit:
                return hit
    if no_llm or not company_resolve_fetch or not url:
        return None
    try:
        dossier = dossier_cache.get_or_build(fm)
    except Exception:
        return None  # a failed fetch just means "couldn't resolve" -- fall through to
                     # classify()'s existing needs_review branch, not a fatal per-lead error
    return _from_dossier(dossier)
```

The `try/except KeyError` on `get_source` covers a lead whose `source` frontmatter names a
retired/renamed source module — abstain, not raise, consistent with "an unrecognized status is
passed through untouched" elsewhere in this codebase's error posture.

The `try/except Exception` around `dossier_cache.get_or_build` is deliberately **softer** than the
enrich pass's own handling of the same call (`engine.py:82-86`, which records into
`report.failures` and `continue`s — dropping the lead from `keeps` entirely, because a `keep`
verdict genuinely cannot be judged without a JD). A failed resolution fetch loses nothing a failed
enrich-pass fetch would: the lead simply keeps classify()'s already-computed `needs_review`
result, exactly as it would with this feature absent. No `report.failures` entry here specifically,
because "couldn't resolve a company" is not a run failure — it's the documented residual (see
below); a *write* failure after a *successful* resolution is the case that gets logged, per the
rule above.

### Tier 1 — `Source.company_from_url`

An optional `Source` protocol member (`ingest/base.py:46-54` gains a fifth, optional line in the
docstring, not the `Protocol` body itself — same non-required shape `precheck`/`preflight` use):

```python
def company_from_url(self, url: str) -> str | None: ...
```

Implemented only where a board's real URL shape unambiguously encodes a company with a clear
delimiter on both ends of the slug. **At least one source ships in this pass** (`rev-003` — an
earlier draft deferred every source to implementation time with no committed minimum, which made
`ruff`/`pytest` satisfiable with zero tier-1 coverage and no test to catch it): **Wellfound**,
whose card links already carry `/company/<slug>` — `wellfound.py`'s extractor JS matches
`a[href*="/company/"]` — an unambiguous shape, delimited by the literal `/company/` segment on one
side and the next `/` or end-of-string on the other. Illustrative pattern, to be verified against a
real `job-sluice ingest test-source wellfound --raw` capture before landing, not copied verbatim
from this design doc: `r"^https?://(?:www\.)?wellfound\.com/company/([a-z0-9-]+)"`, the captured
slug de-hyphenated and title-cased.

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
`neu-001` — two reviewers independently found the earlier draft asserted "abstain over guess" as a
governing principle without proving it structurally for this specific branch, the one most capable
of an over-permissive match). Only specific, real-capture-validated shapes qualify; a `page_title`
that superficially resembles but does not cleanly match a known pattern (contains "hiring" or "at"
without the expected structure) abstains rather than guesses, proven by a dedicated near-miss test
and mutation-witness row (see Testing/Mutation witnesses below), not merely asserted in prose the
way the earlier draft left it.

**The two new dossier fields are excluded from what reaches the judge** (`arch-003` — the earlier
draft added them to the same dict `dossier.slim()` forwards, uncapped, into every judge prompt via
`judge.py`'s `_build_prompt`; `slim()` today caps only `jd.markdown` at `jd_limit=4000` chars and
strips only `lead_snapshot`, so raw JSON-LD — several KB on some boards — would have flowed
uncapped into every `keep` lead's judge call, not just tier-2-resolved ones). `slim()`
(`dossier.py:17-23`) additionally drops `page_title` and `structured_data` from its output: they
are resolution-only fields, not judge-relevant — by the time judging runs, `company` is already a
separate, already-resolved frontmatter field, and the judge never needed the raw page data that
produced it.

Because this is the **same fetch** a `keep` verdict needs anyway for judging, and
`DossierCache.get_or_build` is TTL-cached (`dossier.py:49-52`), a lead that resolves via tier 2 and
ends up `keep` costs exactly one page visit total — the classify-pass fetch is the enrich-pass's
cache hit, not a second fetch. This claim is proven by a dedicated test, not merely asserted — see
Testing (`test-003`). A lead that resolves via tier 2 and still ends up `reject` (its now-known
company matches `reject_companies`) or `needs_review` (some other classify branch) leaves a
now-unused cached dossier on disk until its TTL expires — accepted, matching how the existing cache
already tolerates a `keep` that never gets judged in a `--no-llm` run.

### The vault write

The write itself: `update_fields(note.ref, {"company": f'"{resolved}"'},
require_status=frozenset(_status.TRIAGE_OWNED))`, quoted the same way `_render_new` originally
writes the field (`vault.py:1863`) and the way `apply_verdict` already quotes
`glassdoor_rating`/`culture_flags` (`triage/apply.py:43-44`). It happens, when it happens, *before*
the re-classified decision is computed — not folded into `apply_classification`'s own write, since
that write only fires from the *re-classified* decision and only for `reject`/`needs_review`
(never `keep`), while this write must be attempted for all three eventual outcomes.

`_status.TRIAGE_OWNED` (`core/status.py:14`, imported in `engine.py:12` as `_status` already)
guards the same race the `#9` staleness feature's `require_status` param exists for: if the lead
entered the application lifecycle between `read_leads` and this write (a receipt, a manual `apply
record`), the write abstains — returns `False`, writes nothing. Never-clobber is preserved by
construction, not by a caller-side check (`update_fields`'s own docstring: "this CANNOT be
delegated to the caller"). Both failure shapes — the raised `VaultConflict` and the silent
`require_status` `False` — are handled by the single rule stated in "Architecture" above: no
re-classification, a `report.failures` entry, and this lead makes no progress on this run, retried
clean next time.

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
  tier 1 would have missed; `company_resolve_fetch=False` never calls the dossier cache either,
  independent of `no_llm`; an unknown `source` id (`get_source` raising `KeyError`) abstains rather
  than raising; a `dossier_cache.get_or_build` exception abstains rather than propagating.
- **Per-source `company_from_url` golden fixtures:** Wellfound's confident-match case and its
  abstain case (a `wellfound.com` URL without a `/company/` segment), mirroring the existing
  golden-parser-fixture convention (`docs/ARCHITECTURE.md`'s pure/impure `Source` split). Any
  additional source implemented during implementation gets the same pair.
- **`_from_dossier` unit tests:** a JobPosting JSON-LD hit; a title-pattern hit when structured data
  is absent; a title-pattern **near-miss** that abstains rather than guessing (`inv-002`/`neu-001` —
  a `page_title` containing "hiring" or "at" without the expected structure); both absent or
  unparseable → `None`; malformed JSON-LD → `None`, not a raised exception.
- **A regression test pinning `classify()`'s signature** — it must not gain a `dossier_cache`,
  `sources`, or `fetcher` parameter, protecting the docstring's "no dossier, no LLM" contract the
  same way `test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar` protects the
  renderer seam's optionality.
- **`engine.py` integration tests, covering the restructured ordering:**
  - A lead with a blank company that `classify()` would reject on title/location/pay grounds
    regardless never triggers resolution — a recording fake `dossier_cache` asserts zero
    `get_or_build` calls. This is the test that actually *proves* the `arch-001`/`rev-002` cost-
    neutrality fix, not merely a re-statement of it.
  - A blank-company lead that resolves via tier 1 and would now be `reject` under
    `reject_companies` ends up `dismiss`, not stuck at `needs_review`; the same lead under tier 2.
  - `--no-llm` leaves a tier-1-miss lead unresolved and still reaching the existing blank-company
    `needs_review` branch. `company_resolve_fetch=False` does the same, independent of `--no-llm`.
  - A `VaultConflict` **and, separately, a `require_status` abstain (the lead advances to an
    `APPLICATION_OWNED` status between `read_leads` and the company write — mirroring `#9`'s
    `require_status` race test construction)** on the company write both leave the lead's decision
    as classify()'s original `needs_review` result, unchanged, with a `report.failures` entry —
    proving both failure shapes converge on one behaviour (`test-001`, `test-002`), not two
    different, untested ones.
  - Under `dry_run`, a lead that resolves successfully is **not** written to the vault (asserted
    directly against the fake store), but `report.counts` **does** reflect the re-classified
    decision — proving the preview-without-writing behaviour rather than merely asserting it.
  - **Cache reuse across the classify and enrich passes** (`test-003`): a recording fake fetcher
    proves a lead resolved via tier 2 and then re-classified to `keep` triggers exactly one
    fetcher call across *both* the classify-pass resolution and the later enrich-pass judge build —
    the test that actually proves the "one page visit total" cost claim.
- **Dossier schema tests:** an old cached dossier JSON missing `page_title`/`structured_data`
  still loads via `get_or_build`'s existing freshness check without raising; `slim()` output never
  contains `page_title`/`structured_data` regardless of whether the source dossier carries them.

### Mutation witnesses

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Mutate by moving or deleting, never adding.

| Mutant | Must redden |
| --- | --- |
| Move the resolve attempt to run before `classify()` (or drop the `decision == "needs_review" and not company` guard) | the zero-`get_or_build`-calls test for a title/location/pay-reject blank-company lead |
| Delete the `if not dry_run` guard around the write | the dry-run-writes-nothing test for a resolvable lead |
| Delete the `no_llm` guard before the tier-2 fetch | the `--no-llm` never-calls-dossier-cache test |
| Delete the `company_resolve_fetch` guard before the tier-2 fetch | the `company_resolve_fetch=False` never-calls-dossier-cache test |
| Delete `require_status=` from the company write | the `require_status`-abstain racing test (lead advances to `applied` between read and write; company write must not land) |
| Swap tier order (try tier 2 before tier 1) | the tier-1-hit-skips-dossier-cache test |
| Delete the JSON-LD parse's `try/except` | the malformed-structured-data-abstains test |
| Loosen the title-pattern regex boundary | the title-pattern near-miss abstain test |
| Delete the `if hit:` guard so a `company_from_url` returning `""` is treated as resolved | the `resolve_company`-level "tier-1 miss falls through to tier 2" test — **not** the per-source golden fixture, which calls `company_from_url` directly and never reaches `resolve.py`'s orchestration (`test-004` — the earlier draft misattributed this row) |

## Docs

- `docs/ARCHITECTURE.md`: the `Source` protocol's optional-member list gains
  `company_from_url`, alongside the existing note about `precheck`/`preflight`; the dossier
  fetch closure's description (a new addition, since no such section currently exists at this
  granularity) gains the two new captured fields and the `slim()` exclusion; `Sluice.triage()`'s
  description in the composition-root section notes it now also threads `sources.get` into
  `triage.engine.run`, the same way it already threads `dossier_cache`/`backend`. **Two more
  additions found in review (`arch-004`):** the "five sub-apps" narrative (`ARCHITECTURE.md:167-170`)
  currently frames triage's classify pass as resolving obvious cases "for free" before anything is
  enriched — no longer fully accurate once a real page visit can happen mid-classify-pass for a
  blank-company lead, so that paragraph needs a caveat; and `dossier.py`'s one-line module
  description (`ARCHITECTURE.md:126-127`) needs updating for the schema addition.
- `.rulesync/rules/CLAUDE.md`: not touched by this spec directly — flagged for a follow-up edit
  once implemented, since the CV-fabrication-gate section's "abstain over guess" framing now has a
  sibling instance worth cross-referencing, and `.rulesync/` is self-edited per standing project
  convention.
- `sluice.yaml.example`: the new `triage.company_resolve_fetch` knob, active (not commented) at its
  default `true` — unlike the abstain-default preference gates elsewhere in the file, this is a
  feature toggle whose correct illustrative value **is** its shipped default, per decision 5 above.

## Definition of done

```bash
ruff check sluice tests
python -m pytest
```

Dependency order: the `DossierCache` schema addition, the extended fetch closure, and `slim()`'s
new exclusion first (nothing else can be tested against them otherwise), then
`Source.company_from_url` on Wellfound (the committed minimum) plus any other source whose real
capture qualifies, then `resolve.py`, then the `engine.py`/`Sluice.triage()` `get_source`-threading
and call-site wiring last — verified early against the existing `tests/test_triage_engine.py` call
sites, which must stay green unmodified given `get_source`'s default.

## Out of scope

**No LLM-based company guessing.** Both tiers are deterministic extraction, never inference.

**No change to `--status` defaults.** Ruled out directly by the issue.

**No backfill command.** The existing `--status needs_review` flag already covers the backlog.

**No change to `classify()`'s signature or purity contract.**

**Not every source gets `company_from_url` in this pass — only Wellfound is committed.** Other
sources whose real captures turn out unambiguous during implementation are a small addition, not a
new mechanism; a source left without it simply keeps relying on tier 2.

## The residual

Tier 2 still can't resolve a lead whose page genuinely never states the employer (a recruiter
posting on behalf of an undisclosed client, a since-removed listing). That lead correctly stays
`needs_review` — the honest outcome this design does not try to remove, only to earn.

## Changelog

**2026-08-10, post-`/review-plan`:** 5 reviewers (invariant, neutrality, cross-cutting, test-
engineer, architect), 0 Critical / 9 High / 6 Medium / 1 Low, all addressed:

- Restructured resolution to run *after* classify()'s existing free rejects, not before
  (`arch-001`, `rev-002`) — the single biggest structural change, closing a real cost-neutrality
  gap.
- Gated the vault write behind `not dry_run` (`inv-001`).
- Replaced a genuine two-section self-contradiction about post-`VaultConflict` behaviour with one
  unambiguous rule, falling naturally out of the restructuring (`test-001`).
- Made `get_source` keyword-only with a `None` default so the existing test suite's six direct
  `triage.engine.run` calls need no changes (`rev-001`).
- Excluded the two new dossier fields from `slim()`'s judge-prompt output (`arch-003`).
- Added a `company_resolve_fetch` config knob, independent of `--no-llm` (`arch-002`).
- Committed Wellfound as the one required tier-1 source for this pass, instead of deferring every
  source with no minimum (`rev-003`).
- Named the missing `require_status`-race test and the missing cache-reuse test explicitly
  (`test-002`, `test-003`).
- Held the tier-2 title-pattern heuristic to the same confidence bar as tier 1, with a near-miss
  abstain test and mutation-witness row (`inv-002`, `neu-001`).
- Fixed a mutation-witness row's test attribution (`test-004`).
- Folded the `try/except VaultConflict` directly into the illustrative code, and added the two
  missing `docs/ARCHITECTURE.md` sections (`arch-005`/`inv-003`, `arch-004`).
