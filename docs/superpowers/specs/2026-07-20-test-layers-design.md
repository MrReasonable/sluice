# Test layers — functional, end-to-end, and executable acceptance

- **Date**: 2026-07-20
- **Status**: DRAFT — awaiting `/review-plan`.
- **Goal**: give sluice a test layer above the unit/contract suite: a full-pipeline end-to-end run, a
  functional layer that invokes CLI handlers, and executable acceptance scenarios phrased as user
  outcomes. This spec designs **all three** but scopes **PR 1 (harness + e2e)** for execution.

## What exists today, and what does not

721 tests: per-module unit tests, a store conformance suite, and golden parser fixtures. Two gaps,
both verified rather than assumed:

**1. There is no functional layer.** The eight files that look like CLI tests are
**argparse-parsing tests only** — `tests/test_cv_cli.py` calls `_build_parser().parse_args([...])`
and asserts on the resulting `Namespace`. No handler is ever invoked. The wiring *between* argparse
and the handlers has no coverage.

That is exactly **issue #7** ("no CLI flag may be parsed but ignored"), whose body records three real
instances: `triage --backend` parsed and never forwarded; a `backend_choice` parameter no CLI caller
could set; an unrecognised `--backend` value falling through to a default. Each was invisible because
both ends read correctly and only the wiring was absent.

**2. Nothing spans the pipeline.** Five test files touch more than one sub-app, but all are
config/doctor/backend-selection tests. No test walks `ingest → triage → cv → apply → track`.

## Decisions (user-confirmed)

- **E2E boundary: seam-faked, real everything else.** No live tier. The suite stays offline,
  deterministic and in CI, per CLAUDE.md's testing invariant.
- **UAT means executable acceptance scenarios**, not a manual checklist and not an inspectable
  artefact directory. A checklist nobody runs rots silently; scenarios in CI cannot.
- **Sequence A**: harness + e2e first, then functional, then acceptance. The harness is the
  load-bearing piece, and e2e is its most demanding consumer — designing it against the *least*
  demanding consumer risks a reshape later.
- **#7 is closed by PR 2**, not left open beside near-identical machinery.

## The substitution points already exist

No monkeypatching and no production change is required. Verified against the code:

| Seam | Substitution point |
| --- | --- |
| store | a real `Vault` on `tmp_path` — real markdown I/O, real `_resolve_path` walk, real never-clobber |
| fetcher | `plugins` seam, config key `fetcher` (`core/app.py:172`) |
| renderer | `plugins` seam, config key `cv.renderer` (`core/app.py:178`) |
| backend | `Sluice.backend()` — documented in-code (`app.py:386`) as "the test seam rather than a `plugins.get` lookup" |
| Gmail | `client` is already an explicit constructor argument, documented at `app.py:383` as the test seam |

**Fakes are registered through the public `register()` API from `tests/`, not patched around the
seams.** Two consequences worth stating: it respects the architecture rule that new implementations
route *through* the seams; and `docs/ARCHITECTURE.md` currently records that the fetcher and renderer
seams have one implementation each with "no runtime selection exercised yet" — this harness becomes
the second implementation of both, so the seams stop being an untested claim.

**The fakes live in `tests/`, never in `sluice/`.** Shipping a `scripted` fetcher inside a
stdlib-only production package would be test-only code in the shipped artefact.

## PR 1 — the harness and the e2e run

### 1. Harness (`tests/e2e/adapters.py`, `tests/e2e/conftest.py`)

- **`scripted` fetcher** — returns canned raw payloads keyed by `(source_id, search)`. Because it
  substitutes only `fetch`, the real pure `Source.parse` runs against them, so the parser layer is
  exercised rather than bypassed.
- **`recording` renderer** — records every `cv_text` it is asked to render and writes a stub file.
  Recording (not discarding) is what keeps "no CV was rendered when the gate failed" assertable; a
  renderer that silently no-ops would make the fabrication-gate assertion vacuous.
- **`ScriptedBackend`** — deterministic `complete(prompt)` returning canned responses keyed by prompt
  shape (triage verdict / CV composition / track classification). Records every prompt so a test can
  assert what the model was actually asked.
- **`FakeGoogleClient`** — matches `RealGoogleClient`'s surface, serves canned messages.
- **Real config on `tmp_path`** written as YAML and pointed at by `SLUICE_CONFIG`, so config layering
  (code defaults < file < env) runs for real.

### 2. The e2e test (`tests/e2e/test_pipeline.py`)

One run through `ingest → triage → cv → apply → track`, asserting **user-visible outcomes** at each
hop: notes created in the vault, statuses moving along the ladder, a CV composed and gated, a
rejection email advancing a lead, an un-acted-on proposal reaching the dead-letter.

### 3. The re-run case

Run `ingest` a second time over the same leads and assert **only `last_seen` moved** — never-clobber
across a real pipeline rather than at unit level. No current test covers this end to end.

## Proving the e2e test can fail

A green end-to-end test that would stay green under a real regression is worse than no test: it
reports coverage it does not have. So the harness is only accepted once each of the four load-bearing
invariants is **witnessed** — the invariant is broken in production code and the e2e test observed
red, then restored byte-identically.

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once first. Mutate by
moving or deleting.

| # | Mutation in production code | Expected |
| --- | --- | --- |
| 1 | `Vault.upsert` rewrites the note body on re-scrape (never-clobber) | re-run test red |
| 2 | `can_advance` permits a backward move on the ladder (never-regress) | e2e red |
| 3 | `cv/engine.py` renders despite a non-empty violation list (fabrication gate) | e2e red |
| 4 | A preference gate rejects when unconfigured (the `672ad2a` abstain shape) | e2e red |

If any mutation leaves the e2e test green, that invariant is **not** covered by it and the test is
extended or the gap is recorded — not quietly accepted.

## PR 2 — functional layer, and #7

Invoke each command's handler (not the parser) against the harness, asserting on files written, exit
codes and stdout. Plus the sweep #7 asks for: walk the argparse tree and assert every declared `dest`
is read by the handler it dispatches to, with an explicit opt-out list that must carry a justification
per entry.

**Constraint carried from #26's review:** the sweep must be **additive**. It does not replace any
existing parse-level assertion — #26 records an escape where a sweep silently dropped what the
enumeration it replaced had asserted.

## PR 3 — executable acceptance scenarios (`tests/uat/`)

Scenarios named and asserted in the user's terms rather than the code's, over the same harness:

- a shortlisted lead's composed CV contains no figure absent from the bundle;
- a rejection email moves that lead to `rejected` and clears its dead-letter entry;
- an empty config bins nothing — every lead survives triage;
- a re-scrape of a lead already triaged does not disturb the decision;
- a CV that fails the gate is never written to the output directory.

Each maps to a load-bearing invariant, so the acceptance layer states in user language what the
invariant tests state in code terms.

## Fixtures — neutrality

All fixture data synthetic: the `Example ...` company family and `conftest.py`'s `Alfa`/`Bravo`/
`Charlie` locations. **Company names are checked against the real world before use** — PR #51 landed
`Solarflux` and `Trueverse` as "obviously invented" fixtures and both are real registered companies,
caught only by a web-capable reviewer. Nothing is copied from a real render, a real CV, a real job
posting, or `sluice.local.yaml`. Job descriptions and emails in fixtures are written for the test.

## Definition of done (PR 1)

- `python -m pytest` green; the e2e tier adds no network and no browser.
- Suite stays fast. **The e2e tier's own wall-clock is recorded in the PR**; if it materially changes
  the suite's character, that is a decision to surface, not absorb silently.
- `ruff check sluice tests` clean.
- Every mutation in the table above run, its stated outcome observed, then restored byte-identically.
- No production code changed. If PR 1 turns out to need one, that is a finding about the seams and is
  raised rather than patched in passing.
- `docs/ARCHITECTURE.md` updated: the fetcher and renderer seams now have a second implementation.

## Out of scope

- Any live tier (real Camofox, real LLM, real Gmail). Explicitly declined.
- **#39** (the backend seam has three implementations and no conformance suite) — adjacent and real,
  but a different piece of work: it is about conformance across providers, not pipeline coverage.
- Performance or load testing.
- Changing any production behaviour. This spec adds tests only.

## Commits (PR 1)

1. `test(e2e): scripted fetcher and recording renderer for the adapter seams`
2. `test(e2e): deterministic backend and fake Google client`
3. `test(e2e): walk the full pipeline in one run`
4. `test(e2e): a re-scrape touches only last_seen end to end`
5. `docs(architecture): record the second fetcher and renderer implementations`
