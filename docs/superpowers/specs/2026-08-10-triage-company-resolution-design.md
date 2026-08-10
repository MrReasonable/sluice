# Triage company-name resolution — classify() names the fix but never attempts it (#109)

**Status:** design approved 2026-08-10, pending `/review-plan` and spec self-review.

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

## The four settled decisions

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

A principle underneath all four, driving several details below: **a wrong company is worse than a
blank one.** Blank explicitly signals "unknown, a human should look" — the honest state. A
mis-extracted name would silently look like ground truth and carry through `keep` → judge → apply
→ a CV addressed to the wrong employer, which is the same asymmetric-risk shape as the pay-floor
and location gates already in `classify.py` (abstain on anything not credible, never guess).
Neither tier writes anything below a confident, structurally-justified match.

## Architecture

`classify()` is untouched — it stays exactly what its docstring already promises: pure, no
dossier, no LLM (`classify.py:1-7`). Resolution happens in `triage/engine.py`'s classify loop
(`engine.py:51-75`), before `classify()` is called, in a new module `sluice/triage/resolve.py`:

```python
# triage/engine.py, inside the existing "for note in notes:" loop, before
# `decision, reason = classify(note.fm, cfg)`:
company = (note.fm.get("company") or "").strip()
if not company or company.lower() == "unknown":
    resolved = resolve.resolve_company(note.fm, get_source, dossier_cache, no_llm=no_llm)
    if resolved:
        wrote = vault.update_fields(
            note.ref, {"company": f'"{resolved}"'},
            require_status=frozenset(_status.TRIAGE_OWNED))
        if wrote:
            note.fm["company"] = resolved
decision, reason = classify(note.fm, cfg)
```

`get_source` is a new parameter on `triage.engine.run` (alongside `dossier_cache`/`audit`,
`engine.py:34-35`), not an import: `triage/` must not import `sluice.ingest` directly, since the
pipeline's sub-app dependency direction (`ingest -> triage -> cv -> apply -> track`) only crosses
at the composition root. `Sluice.triage()` (`core/app.py:786-815`) gains the same lazy,
inside-the-method import its `ingest()` neighbour already uses for `ingest.base`/`ingest.engine`
(`core/app.py:508-509`) — `from sluice.ingest import sources` — and passes `sources.get` as the new
argument to `_triage_run`, exactly the way it already passes `cache = self.dossier_cache(...)`.
`triage/engine.py` itself only ever sees a callable.

`resolve_company` is a thin orchestrator:

```python
def resolve_company(fm: dict, get_source, dossier_cache, *, no_llm: bool) -> str | None:
    """Tier 1 then tier 2, first confident match wins. Returns None -- never a guess --
    when both abstain. `get_source` is `sluice.ingest.sources.get`, injected so this
    stays testable without importing the real registry."""
    url = fm.get("url") or ""
    src_id = fm.get("source") or ""
    if url and src_id:
        try:
            source = get_source(src_id)
        except KeyError:
            source = None
        extractor = getattr(source, "company_from_url", None)
        if extractor:
            hit = extractor(url)
            if hit:
                return hit
    if no_llm or not url:
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
enrich-pass fetch would: the lead simply proceeds to `classify()` with its company still blank,
landing on the same `needs_review` branch it would have reached with this feature absent. No
`report.failures` entry, because "couldn't resolve a company" is not a run failure — it's the
documented residual (see below).

### Tier 1 — `Source.company_from_url`

An optional `Source` protocol member (`ingest/base.py:46-54` gains a fifth, optional line in the
docstring, not the `Protocol` body itself — same non-required shape `precheck`/`preflight` use):

```python
def company_from_url(self, url: str) -> str | None: ...
```

Implemented only where a board's real URL shape unambiguously encodes a company with a clear
delimiter on both ends of the slug. **Which sources qualify is determined during implementation**
against real captures (`job-sluice ingest test-source ID --raw`), not guessed from the issue's
generic examples — a shape with only one clean boundary (e.g. a flat `company-role-words` slug
with no terminator) is left unimplemented rather than implemented with a guessed split point, per
the abstain-over-guess principle above. `getattr(source, "company_from_url", None)` being absent
is tier 1 abstaining for that source, same as today.

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

Because this is the **same fetch** a `keep` verdict needs anyway for judging, and
`DossierCache.get_or_build` is TTL-cached (`dossier.py:49-52`), a lead that resolves via tier 2 and
ends up `keep` costs exactly one page visit total — the classify-pass fetch is the enrich-pass's
cache hit, not a second fetch. A lead that resolves via tier 2 and still ends up `reject` (its now-
known company matches `reject_companies`) or `needs_review` (some other classify branch) leaves a
now-unused cached dossier on disk until its TTL expires — accepted, matching how the existing cache
already tolerates a `keep` that never gets judged in a `--no-llm` run.

### The vault write

One
`update_fields(note.ref, {"company": f'"{resolved}"'}, require_status=frozenset(_status.TRIAGE_OWNED))`
call, quoted the same way `_render_new` originally writes the field (`vault.py:1863`) and the way
`apply_verdict` already quotes `glassdoor_rating`/`culture_flags` (`triage/apply.py:43-44`).
Independent of the classify decision's own write — `apply_classification` still only fires for
`reject`/`needs_review` (`engine.py:60-75`), never for `keep`, and this write must happen for all
three outcomes, so it cannot be folded into `apply_classification`'s existing call.

`_status.TRIAGE_OWNED` (`core/status.py:14`, imported in `engine.py:12` as `_status` already)
guards the same race the `#9`
staleness feature's `require_status` param exists for: if the lead entered the application
lifecycle between `read_leads` and this write (a receipt, a manual `apply record`), the write
abstains — returns `False`, writes nothing — and this run proceeds with `note.fm["company"]` still
blank, exactly as it would without this feature. Never-clobber is preserved by construction, not by
a caller-side check (`update_fields`'s own docstring: "this CANNOT be delegated to the caller").

A `VaultConflict` from a genuine concurrent edit is caught at the same site the existing
`apply_classification`/`apply_verdict` calls already catch it (`engine.py:60-68`), logged into
`report.failures`, and this lead is skipped for the rest of this run's classify pass — retried
clean next run.

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
  miss returns `None`; `no_llm=True` never calls the dossier cache even when tier 1 would have
  missed; an unknown `source` id (`get_source` raising `KeyError`) abstains rather than raising; a
  `dossier_cache.get_or_build` exception abstains rather than propagating.
- **Per-source `company_from_url` golden fixtures:** one confident-match case and one abstain case
  (a URL shape that doesn't match) per implementing source, mirroring the existing golden-parser-
  fixture convention (`docs/ARCHITECTURE.md`'s pure/impure `Source` split).
- **`_from_dossier` unit tests:** a JobPosting JSON-LD hit; a title-pattern hit when structured data
  is absent; both absent or unparseable → `None`; malformed JSON-LD → `None`, not a raised
  exception.
- **A regression test pinning `classify()`'s signature** — it must not gain a `dossier_cache`,
  `sources`, or `fetcher` parameter, protecting the docstring's "no dossier, no LLM" contract the
  same way `test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar` protects the
  renderer seam's optionality.
- **`engine.py` integration tests:** a blank-company lead that resolves via tier 1 and would now be
  `reject` under `reject_companies` ends up `dismiss`, not stuck at `needs_review`; the same lead
  under tier 2; `--no-llm` leaves a tier-1-miss lead unresolved and still reaching the existing
  blank-company `needs_review` branch; a `VaultConflict` on the company write is recorded in
  `report.failures` and the lead's classify decision still runs against the unresolved company.
- **Dossier schema tests:** an old cached dossier JSON missing `page_title`/`structured_data`
  still loads via `get_or_build`'s existing freshness check without raising.

### Mutation witnesses

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.
Mutate by moving or deleting, never adding.

| Mutant | Must redden |
| --- | --- |
| Delete the `no_llm` guard before the tier-2 fetch | the `--no-llm` never-calls-dossier-cache test |
| Delete `require_status=` from the company write | a racing test: lead advances to `applied` between read and write, company write must not land |
| Swap tier order (try tier 2 before tier 1) | the tier-1-hit-skips-dossier-cache test |
| Delete the JSON-LD parse's `try/except` | the malformed-structured-data-abstains test |
| Delete the `if hit:` guard so a `company_from_url` returning `""` is treated as resolved | the per-source abstain-case fixture (a non-matching URL, extractor returns `""`) |

## Docs

- `docs/ARCHITECTURE.md`: the `Source` protocol's optional-member list gains
  `company_from_url`, alongside the existing note about `precheck`/`preflight`; the dossier
  fetch closure's description gains the two new captured fields; `Sluice.triage()`'s description
  in the composition-root section notes it now also threads `sources.get` into `triage.engine.run`,
  the same way it already threads `dossier_cache`/`backend`.
- `.rulesync/rules/CLAUDE.md`: not touched by this spec directly — flagged for a follow-up edit
  once implemented, since the CV-fabrication-gate section's "abstain over guess" framing now has a
  sibling instance worth cross-referencing, and `.rulesync/` is self-edited per standing project
  convention.

## Definition of done

```bash
ruff check sluice tests
python -m pytest
```

Dependency order: the `DossierCache` schema addition and the extended fetch closure first (nothing
else can be tested against them otherwise), then `Source.company_from_url` plus whichever sources
implement it, then `resolve.py`, then the `engine.py`/`Sluice.triage()` `get_source`-threading and
call-site wiring last.

## Out of scope

**No LLM-based company guessing.** Both tiers are deterministic extraction, never inference.

**No change to `--status` defaults.** Ruled out directly by the issue.

**No backfill command.** The existing `--status needs_review` flag already covers the backlog.

**No change to `classify()`'s signature or purity contract.**

**Not every source gets `company_from_url` in this pass.** Only shapes confirmed unambiguous from
real captures during implementation; a source left without it simply keeps relying on tier 2.

## The residual

Tier 2 still can't resolve a lead whose page genuinely never states the employer (a recruiter
posting on behalf of an undisclosed client, a since-removed listing). That lead correctly stays
`needs_review` — the honest outcome this design does not try to remove, only to earn.
