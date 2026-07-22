# PR 3 — complete the e2e acceptance suite (supersedes the 2026-07-20 spec's §PR 3 outline)

- **Date**: 2026-07-22
- **Status**: CONVERGED after three `/review-plan` rounds. R1 (4 agents): 0C/3H/4M/2L — all folded.
  R2 (verifying the folds): 0C/0H/0M/5L — all folded. R3: **0C/0H/0M/0L — all four reviewers clean**;
  every fold re-verified against `eb68b73` and all five scenario witnesses traced to real
  redden-paths. Ready to build. Brainstormed 2026-07-22; grounded against the code at
  `main @ eb68b73`. Supersedes the `tests/uat/` outline in
  `docs/superpowers/specs/2026-07-20-test-layers-design.md` §PR 3. Final placement (fold this back
  into that arc doc's §PR 3 vs keep standalone) is a post-review decision.

## Corrections from /review-plan, recorded rather than silently fixed

Three High findings were each corroborated by more than one independent reviewer — the strongest
signal this review produces — and every one was a mechanism I *asserted* without running it, the exact
error this repo names. Recorded here, fixed in the scenario designs below:

- **S5's filesystem assertion was wrong** (3 reviewers). `glob("*") == []` fails on un-mutated code:
  the recording renderer writes a real PDF for the *clean* lead, so `cv_output/` is non-empty. Assert
  the gate-failing lead's subdir is absent while the clean one's is present.
- **S2's run-1 signal must key the entry to the definite lead slug** (3 reviewers). An "ambiguous"
  signal records `lead=""`, which `clear_lead(slug)` never matches; run 1 must be a matched-lead
  low-confidence proposal, the lead seeded in-flight, and the run-1 `Entry.lead == slug` asserted.
- **S3's numeric violation must be the CV's only gate failure** (2 reviewers). Any other violation
  leaves it `skipped-gate` under the numeric-check mutation → the witness goes inert.
- **S1's witness and attribution must align on the location gate** (2 reviewers) and the lead must
  carry a location for the `target_locations`-guard mutation to redden — satisfied by driving S1
  through the board (the `remoteok` source's `extra` sets `"Remote"` at parse; the extractor JS is
  inert in the harness). Attribution via neutral location tokens sidesteps the reject-title neutrality
  edge entirely.
- **Goal**: make `tests/e2e/` a **comprehensive acceptance suite** — every load-bearing product
  promise has exactly one readable, user-named end-to-end scenario over the existing
  `tests/harness/`. No new tier, no new directory, no production change.

## The decision this records, not silently makes

The 2026-07-20 spec committed PR 3 to a separate `tests/uat/` directory of "acceptance scenarios in
the user's terms." Brainstorming 2026-07-22 **revised that**: an acceptance tier that walks the same
pipeline over the same harness as `tests/e2e/` is the e2e tier — a parallel directory would be the
duplication the whole test-layers arc exists to avoid. So:

- **UAT folds into `tests/e2e/`.** There is no `tests/uat/`.
- The candidate "distinct driving model" for a fourth tier — a *sequence* of real `sluice ...`
  commands, distinct from e2e's single shared `app` because each `main(argv)` re-runs `load_config()`
  (`cli.py:475`) and builds a fresh `Sluice(config)` — was **rejected**. That difference is real but
  guards no failure: the vault is read fresh from disk either way. Building a tier around it would be
  the "assert a mechanism, write a check that cannot falsify it" pattern this repo keeps catching.
- PR 3's deliverable is therefore **completeness**, not a new mechanism: audit the e2e suite against
  the load-bearing invariants and add one user-named scenario for each promise not yet spanned
  end-to-end.

## Honest framing (carried from PR 1) — most of these are integration coverage, not unique witnesses

PR 1 established empirically that of its e2e scenarios, **only M3 (the CV gate) was uniquely caught**
by the new tier; the rest re-checked invariants already witnessed at the unit tier. The e2e tier is
**integration coverage**: it proves the sub-apps wire together and the invariants hold *through the
composition root*, not a second copy of the unit witnesses.

This PR inherits that discipline. Each scenario below names its invariant and states whether it is
**uniquely caught here** or **pre-witnessed elsewhere** (integration coverage). No scenario is sold as
a unique witness without the two-run mutation check below proving it. Overselling an integration test
as a unique witness is itself the error this project most consistently makes.

## Coverage audit — where the e2e suite stands at `eb68b73`

Read all three existing e2e tests to build this (counted, not sampled):

| Product promise (invariant) | Covered end-to-end today? | Where |
| --- | --- | --- |
| Never-clobber — re-scrape moves only `last_seen` | ✅ | `test_rescrape` (with the upsert-ran precondition) |
| Never-regress — triage won't touch an application-owned lead | ✅ | `test_triage_never_regress` |
| CV gate — a gate-failing CV is never rendered (structural arm) | ✅ | `test_full_pipeline` (recorder-based) |
| CV gate — numeric arm (a bullet citing a figure absent from the bundle) | ❌ | not e2e; full_pipeline uses the drifted-header path |
| CV gate — no fabricated CV reaches `cv.output_dir` (filesystem) | ⚠️ partial | asserts the recorder + `tailored_cv` marker, not an empty output dir |
| Empty-config-abstains — an unset gate bins nothing | ❌ | only unit `test_sluice_neutral_defaults` (config defaults) + functional (triage-only) |
| Dead-letter durability (#49) — rejection → `rejected` **and** clears the entry | ⚠️ partial | full_pipeline reaches `rejected`, never asserts `open_entries()` cleared |
| Never-regress — a `rejected` terminal is never advanced out | ❌ | not e2e (reachable only via `track_confirm`) |

The bottom five rows are the work.

## File plan (`tests/e2e/`)

| Action | File | Promise |
| --- | --- | --- |
| `git mv` + edit | `test_full_pipeline.py` → `test_a_clean_lead_reaches_rejected.py` | happy path + CV gate (structural); **+ filesystem assertion** |
| `git mv` | `test_rescrape.py` → `test_a_rescrape_keeps_my_edits.py` | never-clobber (unchanged content) |
| `git mv` | `test_triage_never_regress.py` → `test_triage_leaves_my_application.py` | never-regress vs application-owned (unchanged content) |
| new | `test_an_empty_config_bins_nothing.py` | empty-config-abstains |
| new | `test_a_rejection_clears_my_backlog.py` | dead-letter durability (#49) |
| new | `test_a_cv_citing_an_unbacked_figure_never_ships.py` | CV gate — numeric arm + retry-then-skip |
| new | `test_a_rejected_lead_cannot_be_dragged_back.py` | never-regress terminal |

The three renames make the whole suite read as a user-promise catalog (the "acceptance in user terms"
intent), at the cost of git-blame churn on three files — done with `git mv` so history follows as
cleanly as it can. **The test *function* names are renamed to match** (`test_full_pipeline_walk` →
`test_a_clean_lead_reaches_rejected`, `test_rescrape_touches_only_last_seen` →
`test_a_rescrape_keeps_my_edits`, `test_triage_leaves_an_application_owned_lead_untouched` →
`test_triage_leaves_my_application`) so the catalog reads coherently at collection time — a
behaviour-neutral rename, no assertion changed. The two content-unchanged files carry no behavioural
edit; the renamed full_pipeline test additionally gets S5's assertion (below).

## Scenario designs — each with its anti-vacuity precondition

### S1 — `test_an_empty_config_bins_nothing.py` (empty-config-abstains)

**Ingest one lead through the board** (a `remoteok` harness row), so it carries the location token the
source applies at parse time: `BrowserListSource.extra={"location": "Remote"}` (`remoteok.py`), merged
onto every lead in `base.py:117`. (In the harness the extractor JS is inert — the scripted client
serves canned rows — so it is `extra`, not the JS, that sets `"Remote"`; crediting the JS was a
round-2 precision finding.) Then triage through the real `Sluice` with **every preference gate
explicitly empty**. Assert the lead is **not** dismissed (`Vault.read_leads()[0].status != "dismiss"`).

- **The trap, load-bearing:** `build_harness` defaults `target_locations=("remote",)` — *the literal
  `672ad2a` bug value* (`config.py:111`). So the harness is built with **explicit**
  `target_locations=()`, `accept_titles=()`, `reject_titles=()`, `perm_floor_gbp=0`,
  `contract_floor_gbp_day=0`.
- **Witness and attribution are aligned on the LOCATION gate** (fix for a review finding: the first
  draft witnessed the `target_locations` guard but attributed via `reject_titles`, a *different* gate,
  and the ingested lead must actually carry a location for the guard mutation to redden — the reject
  branch is `cfg.target_locations and location and …`, `classify.py:123`).
  - **Attribution (anti-vacuity):** the same board-ingested lead, harness rebuilt with
    `target_locations=("Alfa",)` — a neutral `conftest.LOCATIONS` token that does **not** match
    `"Remote"` — comes back `dismiss`. Proves the location gate *would* bin this exact lead, so the
    empty-gate pass is abstention, not a bland lead.
  - **Two harnesses, sequential, distinct tmp dirs** (round-2 finding): the abstain arm and the
    attribution arm each call `build_harness` on their **own** tmp dir. Reusing one `tmp_path` collides
    — `seen.db` skips the re-scrape and the note is no longer `new` — and `build_harness` writes the
    config before it seeds the vault (`config.py:173` before `:185`), so a shared dir would misfire.
    Each arm re-reads `$SLUICE_CONFIG`, so they run sequentially, not concurrently. Fails loud, but the
    plan must not gloss it as one rebuild.
  - **Witness:** delete the `cfg.target_locations and` short-circuit (`classify.py:123`) → the empty
    gate stops abstaining, the lead's `"Remote"` is compared against the empty list and rejected → S1's
    "not dismissed" assertion reddens. Non-inert only because the ingested lead carries `"Remote"`, so
    S1 must drive through the board, never a hand-seeded note.
  - Run twice: S1 RED, then deselected to confirm the functional triage-abstain test also catches it
    (it does). **Framing: composition-root integration proof** — the first test to drive
    empty-config-abstains through the full `ingest → triage` root; the behavioural abstain is already
    functional-witnessed.
- **Neutrality:** the attribution uses neutral location tokens only (`"Remote"` from the board,
  `"Alfa"` from `conftest.LOCATIONS`) — no real location, and no title literal, so the reject-title
  leak edge does not arise. If a future variant adds a `reject_titles` dimension, both the lead title
  and the reject value must derive from the **same** `conftest.py` seeded-faker fixture so they match
  by construction (never a hardcoded title literal — the neutrality reviewer's neu-001).

### S2 — `test_a_rejection_clears_my_backlog.py` (dead-letter #49)

**Seed the lead in-flight** (status `applied`) before either run — `track.engine.run` filters to
`_INFLIGHT` (`engine.py:64-65`), so a lead in any other status never reaches `reconcile` and both
runs' preconditions go vacuous. Then two `Sluice.track` runs against that one lead:

1. **Run 1 — establish the backlog (precondition).** A **matched-lead, low-confidence** signal — one
   that resolves to `action="proposed"` with `ev.lead_slug` **set to the lead's slug** (a
   low-confidence phone_screen/rejection with no schedulable signal, which falls through `reconcile`
   to a definite-lead proposal). This records a dead-letter Entry keyed under that slug
   (`engine.py:109-127`). **Assert the run-1 Entry's `lead` equals the lead's slug** — not merely that
   `open_entries()` is non-empty. (Fix for a review finding, three reviewers: an *ambiguous* signal
   records `lead=""` (`engine.py:119`), and run 2's `clear_lead(slug)` does `WHERE lead = ?`
   (`deadletter.py:111`), so it would never match and "cleared" would fail even un-mutated.)
2. **Run 2 — resolve it.** A high-confidence rejection for the same lead auto-advances it to
   `rejected` (`res.action == "applied"`, `engine.py:101`), firing `clear_lead(ev.lead_slug)`
   (`engine.py:108`). Assert the lead is `rejected` **and** `open_entries()` is now **empty**.

- **Why the precondition is load-bearing:** `clear_lead` on an empty store returns 0 and creates no
  file (`deadletter.py:106-115`); and an entry recorded under the wrong key is never cleared. Run 1's
  `Entry.lead == slug` assertion proves a *clearable* entry existed. Same shape as the re-scrape M1 trap.
- **Distinct message ids across the two runs** (round-2 finding): the runs share a persisted `seen`
  set (`engine.py:80` skips a seen `mid`), so run 1's proposal email and run 2's rejection email must
  carry **different** message ids, or run 2 is deduped to a no-op. Fails loud (the run-2 `rejected`
  assertion would catch it), but the narrative states it.
- **Witness:** delete the `clear_lead(ev.lead_slug)` call (`engine.py:108`) → run 2's `open_entries()`
  stays non-empty → S2 reddens. Run twice: S2 RED, then deselected — check whether an engine-level
  unit test already exercises clear-on-auto-advance (the store-level `clear_lead` is unit-witnessed at
  `test_track_deadletter`; the *engine wiring* may not be). Framing set by that result.

### S3 — `test_a_cv_citing_an_unbacked_figure_never_ships.py` (CV gate — numeric arm)

Hand `ScriptedBackend` a canned CV (keyed by company) whose WORK EXPERIENCE bullet cites a figure
present in **no** cited bundle entry, and whose header is the correct `WORK EXPERIENCE` (so the gate
*runs* — this is the numeric arm, not the structural-drift arm full_pipeline uses). The composition
root runs `validate` → finds the numeric violation → retries once → skips.

- **Expressibility, verified:** the retry appends `prior_violations` to the *end* of the prompt
  (`compose.py:61-64`); the first line stays `"Compose a tailored CV for {name} applying for {role}
  at {company}."` unchanged, so `ScriptedBackend`'s first-line-prefix key returns the *same* violating
  CV on retry → the gate fails again → `skipped-gate`. No harness change needed.
- **Exactly one violation (anti-vacuity, per a review finding):** the canned CV must be clean in every
  other respect — every bullet cited, reverse-chronological, slop-free, correct `WORK EXPERIENCE`
  header — so its **only** gate failure is the uncited figure. Otherwise deleting `validate.py`'s
  numeric check leaves the CV `skipped-gate` on the *other* violation and S3 stays green under its own
  mutation (inert). The same anti-vacuity care S1/S2 get.
- **Assert:** `result.status == "skipped-gate"`; `result.violations` names the numeric violation;
  `h.recorder.rendered == []` (nothing was rendered); the lead carries no `tailored_cv`.
- **Distinct wiring:** full_pipeline's gate-fail lead trips the *structural* guard (`engine.py:79`,
  the "gate did not run" path); S3 exercises `validate`'s numeric-citation check **and** the
  retry-once-then-skip loop on a real violation — wiring the structural path never reaches.
- **Witness:** in `cv/validate.py`, move/delete the numeric-citation check so an uncited figure passes
  → the CV renders → S3 reddens. Run twice: S3 RED, then deselected → the `test_cv_validate` unit
  tests also kill it → **integration framing**, with the retry-then-skip loop as the wiring S3
  uniquely drives end-to-end.

### S4 — `test_a_rejected_lead_cannot_be_dragged_back.py` (never-regress terminal)

Seed a `rejected` lead into the tmp vault; run `Sluice.track_confirm(lead=slug, to="offer")`. Assert
the confirm is refused (`ok is False`) and the status is still `rejected` byte-for-byte.

- **Why `confirm`, not an email:** `track.engine.run` filters to `_INFLIGHT`
  (applied/phone_screen/interview/offer), so a `rejected` lead never reaches `reconcile` via an email
  signal. The terminal guard is reachable end-to-end **only** through `track_confirm` (which reads all
  statuses). `can_advance` refuses moves out of a terminal (`core/status.py`).
- **Witness:** move/delete `can_advance`'s terminal guard → the confirm advances the rejected lead →
  S4 reddens. Run twice: S4 RED, then deselected → `test_track_engine`'s confirm-backward-refused unit
  test also catches it → **integration framing**.

### S5 — filesystem augmentation to `test_a_clean_lead_reaches_rejected.py`

After the CV hop, assert on the **gate-failing lead's** output subdir specifically, not global
emptiness (fix for a review finding, three reviewers: the recording renderer writes a real `%PDF` to
`{cv_output}/{slug}/CV.pdf` for every gate-*passing* CV — `renderer.py:35-42`, `cv/engine.py:103` —
and this test's clean Example Foundry lead renders successfully, so `cv_output/` is non-empty and a
`glob("*") == []` assertion would fail on un-mutated code). Assert the **gate-failing** lead's slug
subdir is **absent** while the **clean** lead's is **present** — the on-disk form of "the fabricated CV
never reached the output directory, but the good one did." One or two lines; strengthens an existing
test rather than adding a file.

## Witness methodology (the non-negotiable discipline)

Per `domain_test_layers.md` and CLAUDE.md's mutation-testing section:

- Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` **once** before
  mutating, so `sluice/`'s bytecode is content-addressed and a size-preserving same-second edit cannot
  run stale bytecode.
- **Mutate by MOVING or DELETING, never by ADDING** — a check added beside the original leaves the
  original firing and the suite green (an equivalent mutant).
- **Every witness runs twice:** (a) the named new scenario goes RED; (b) the suite with that scenario's
  file deselected isolates whether a pre-existing test also catches it. (b) is what tells unique-catch
  from integration coverage — and the honest, common outcome here is "pre-witnessed at the unit tier."
- Restore the production source byte-identically after each mutant; re-run the full suite green.

## Neutrality

Every fixture uses `build_harness`'s vetted conventions: `Example …` companies, `example.invalid`
domains/emails, the `Remote` work-arrangement token, synthetic round pay floors, and — where a
scenario sets a preference gate — a preference **value** that is synthetic and opinion-free, never a
real job-category preference.

- **S1's attribution stays on the location gate, neutral tokens only:** `"Remote"` (from the board)
  and `"Alfa"` (`conftest.LOCATIONS`), so no title literal enters the repo and the reject-title leak
  edge does not arise. Any future `reject_titles` variant must draw both the lead title and the reject
  value from the **same** `conftest.py` seeded-faker fixture (never a hardcoded title literal — the
  round-1 neutrality finding). The scenario asserts the *mechanism* (same lead binned-vs-abstained),
  never a real preference.
- The author runs a name-check over the new fixtures and lists what was checked in the PR body
  (`Example …` is verified-today against Companies House, not structurally unclaimable). A hit means
  replace, not justify.
- No new fixture repeats the known-bad real company names still live in `test_cv_bundle.py` /
  `test_core_vault_cv.py` / `test_store_contract.py` (tracked as #53).

## CI

- `pyproject.toml:28-29` sets `testpaths = ["tests"]`, `addopts = "-q"` — no `--ignore`, no marker
  filter. `ci.yml:46` runs bare `python -m pytest`, so the **entire `tests/` tree is collected**;
  `tests/e2e/` is already collected today. New `tests/e2e/*.py` files run automatically — **no CI
  change needed.**
- The `test` job runs the full suite on the **matrix Python 3.12 / 3.13 / 3.14** (`ci.yml:34-35`),
  gated by `ci-success`. The DoD gate is therefore "green on the CI matrix", not merely "green on one
  local interpreter".
- The new scenarios are hermetic — the harness fakes every I/O boundary (browser client, renderer,
  backend, Google client) — so they stay offline / no-browser on the runners.

## Definition of done

- Suite green with no network and no browser, locally and on the CI matrix (3.12/3.13/3.14); ruff
  clean (`ruff check sluice tests`).
- **Four new scenarios (S1–S4) each mutation-witnessed twice** — the named scenario RED, then the
  suite with it deselected — after `compileall --invalidation-mode checked-hash sluice tests`, sources
  restored byte-identically. Each scenario's PR-body note states its invariant and its unique-vs-
  integration status.
- S5's one-line filesystem assertion added to the renamed full_pipeline test.
- Three `git mv` renames land history-preserving; the two content-unchanged renames carry no
  behavioural edit.
- **No production change.** `sluice.yaml.example` untouched (no new tunable). This PR touches only
  `tests/` and the design/plan docs (this spec, the implementation plan, and the 2026-07-20 arc-doc
  §PR 3 pointer).
- The stale `.rulesync/rules/CLAUDE.md` lines ("no runtime selection exercised yet"; `fetch` for the
  `fetcher` key) stay **human-gated** — noted for the user, not edited here.
- One CodeRabbit slot (per the merge gate). `/review-pr` before push.

## Out of scope

- Any live tier (real Camofox, LLM, Gmail). Declined, as in the parent spec.
- Any production behaviour change. PR 3 is test-only — unlike PR 2, it carries no `cli.py` fix.
- Renaming `tests/functional/` or `tests/e2e/`'s directory, or touching the harness beyond consuming
  it. If a scenario needs a harness capability the harness lacks (it should not — S1–S5 are all
  expressible against the current `build_harness` / `ScriptedBackend`), that is a finding to surface,
  not a silent lift.

## Commits (provisional)

1. `test(e2e): rename the pipeline tests to user-promise names` — three `git mv`s **plus the matching
   test-function renames** (behaviour-neutral; no assertion changed).
2. `test(e2e): an empty config bins nothing` — S1 (+ attribution).
3. `test(e2e): a rejection clears the dead-letter backlog` — S2 (two-run precondition).
4. `test(e2e): a CV citing an unbacked figure never ships` — S3 (numeric arm + retry-then-skip).
5. `test(e2e): a rejected lead cannot be dragged back` — S4.
6. `test(e2e): assert no gated CV reaches the output directory` — S5 augmentation.
