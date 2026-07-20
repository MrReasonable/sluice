# Test layers — finishing three seams, then e2e, functional and acceptance

- **Date**: 2026-07-20
- **Status**: REVISED after round 1 `/review-plan` (5 agents: 0C / 11H / 11M / 8L — all folded).
  Awaiting round 2.
- **Goal**: add a test layer above the unit/contract suite — a full-pipeline e2e run, a functional
  layer over the CLI handlers, and executable acceptance scenarios. Four PRs; **PR 0 and PR 1** are
  designed here in full, PR 2 and PR 3 in outline.

## Corrections to round 1, recorded rather than quietly fixed

Round 1 found the plan's motivation partly false and three of its four mutation witnesses inert.
Both errors are the same shape — a claim asserted from a sample rather than checked — so they are
recorded here rather than silently repaired.

- **"The eight CLI-test files are argparse-parsing tests only; no handler is ever invoked" was
  FALSE.** It generalised from one file. `tests/test_apply_cli.py` calls `cmd_apply_prep` /
  `cmd_apply_record`; `tests/test_track_cli.py` calls three `cmd_track_*`; `tests/test_cli.py` and
  `tests/test_triage_cli.py` call `main([...])`, the full dispatch path. **Only `test_cv_cli.py` is
  parse-only.** PR 2 and #7 survive on different grounds — see §PR 2.
- **The second gap holds:** no test spans `ingest → triage → cv → apply → track`. Verified by two
  reviewers. PR 1's motivation is unaffected.
- **Three of four mutation witnesses would have stayed green.** See §Mutation witnesses, which now
  specifies the *scenario shape* that makes each observable, not just the mutation.
- **The backend row of the substitution table cited `app.py:386`**, which documents the Gmail
  `client` and explicitly contrasts it *with* the backend. Three reviewers caught it.

## PR 0 — finish three seams

Not test accommodations. Two are seams the codebase already declares and fails to wire; the third is
a gap three existing features already feel. Each lands as its own commit with its own justification,
and each is mutation-witnessed independently of the harness.

### 0.1 `Sluice.backend()` must honour `_overrides`

`__init__` accepts `**overrides` and stores them; `_resolve` honours them for store, fetcher and
renderer. `backend()` never calls `_resolve` and never reads `_overrides`, so
`Sluice(config, backend=X)` is **accepted and silently ignored**.

That is the failure mode `backend()`'s own comment says it exists to prevent — it raises on an
unrecognised role rather than "land silently in `auto`", calling that "the same quiet-wrong-default
this method exists to remove". The constructor advertising an override it drops is that same class.

Fix: consult `_overrides` at the top of `backend()`. Inert in production (nothing passes one today).
**Per-instance, and it never touches the global registry** — so
`tests/test_backend_registry.py`'s set-equality assertion is untouched. That guard is precisely why
registering a fake backend is not an option (round 1, two reviewers).

### 0.2 `Sluice.ingest()` must pass `Ctx.sleep`

`ingest/base.py:33` declares `sleep: Callable = field(default=time.sleep)` with the docstring "an
injectable sleep so tests don't actually wait for page settle". `core/app.py:253` builds
`Ctx(camofox=..., config=...)` and drops it, so a shipped source costs ~4-5s of real sleep per
search — against a whole suite that runs in ~3.5s.

Fix: thread it through as an explicit constructor argument, following the house convention already
documented for track's Gmail `client`: "a plain constructor argument, not a registered adapter seam."

### 0.3 An injectable clock

The only genuinely new seam, and the weakest case, so it is stated plainly. Its justification is not
the harness but three existing date-dependent behaviours with no app-level test:
`Vault._bump_last_seen`'s monotonicity (#45), the `lastrun` watermark (#49), and lead staleness (#9)
if it lands. `ingest/sink.py` calls a module-level `_today()`; nothing above it can move the clock.

Fix: an injectable `today` on the same explicit-constructor-argument pattern.

**Why seams rather than monkeypatch:** monkeypatch binds to *where a symbol was imported*, so
patching `sink._today` works until someone imports it elsewhere and it silently stops patching. A
finished seam cannot rot that way. And 0.1 is a live defect regardless of testing.

## PR 1 — the harness and the e2e run

### 1.1 Substitution points (corrected)

| Point | How | Notes |
| --- | --- | --- |
| store | real `Vault` on `tmp_path` | real markdown I/O, real `_resolve_path`, real never-clobber |
| fetcher | `plugins` seam, config key `fetcher` | **it is a browser *client*** (`create_tab`/`evaluate`/`scroll`/`close_tab`) consumed *by* `BrowserListSource.fetch`, keyed by URL and extractor JS — not by `(source_id, search)` |
| renderer | `plugins` seam, config key `cv.renderer` | recording, not no-op — see 1.2 |
| backend | `Sluice(backend=...)` override, enabled by PR 0.1 | never register a fake backend |
| Gmail | `client` constructor argument (`app.py:383`) | already an explicit test seam |
| sleep / clock | constructor arguments, enabled by PR 0.2 / 0.3 | |

**Never register a fake store.** `tests/conformance/test_store_contract.py:32` parameterises over
every registered store, so a fake would be silently pulled into the conformance suite and asserted
against the full Store contract. Same reasoning bars a fake backend (registry guard).

Fetcher and renderer *are* registered through the public `register()` API: selection is name-keyed
from config, neither registry has an enumerating guard, and direct injection would leave
`plugins.get` and the config-key wiring untested — which is much of the point.

### 1.2 Harness (`tests/harness/`)

`tests/harness/`, not `tests/e2e/adapters.py`: PR 2 and PR 3 consume it too, and putting it under one
layer's directory bakes in a move.

- **`recording` renderer** — records every `cv_text`. Recording rather than discarding is what keeps
  "no CV was rendered when the gate failed" assertable; a no-op renderer makes that assertion vacuous.
- **`scripted` browser client** — serves canned DOM payloads keyed by URL, so a **shipped**
  `BrowserListSource` runs its real `fetch` and its real pure `parse`.
- **`ScriptedBackend`** — keyed by an explicit discriminator, **not** "prompt shape". Round 1 flagged
  that as a placeholder: the triage prompt embeds vault-sourced text, so substring keying is
  unstable. The rule: dispatch on the first line of the prompt's task header, and **raise on an
  unrecognised prompt** rather than returning a default — a silent default would let a mis-wired call
  pass as success.
- **`FakeGoogleClient`** — matches `RealGoogleClient`'s surface.
- **Config factory** — writes YAML to `tmp_path`, `SLUICE_CONFIG` points at it, and
  **`cv.output_dir` is pinned inside `tmp_path`**. It defaults to `./cv-output`, relative to pytest's
  cwd, so an unpinned run writes CV output into the repo.

### 1.3 The e2e test

One run through `ingest → triage → cv → apply → track`, asserting user-visible outcomes at each hop.
**It includes one lead whose composed CV fails the gate** — not deferred to PR 3, because witness M3
needs it (§Mutation witnesses).

## Mutation witnesses — the mutation *and* the scenario

Round 1's table named mutations without the scenario that makes them observable, and three would have
stayed green. Each row now carries its enabling scenario; a row without one is not a witness.

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests` once first.

| # | Mutation | Enabling scenario — **required** |
| --- | --- | --- |
| M1 | `Vault.upsert` rewrites the note body on re-scrape | Re-run with a **fresh `seen_db`** and an **advanced clock**. `_run_source` skips a seen key *before* `sink.write`, so `upsert` never executes and the mutant never runs. Assert `written['updated'] == 1` as a **precondition** that the vault was reached, then assert a hand-edited body survived. |
| M2 | `can_advance` drops its terminal/backward guard | A seeded **`interview`** lead receiving a **phone-screen** signal. `applied → rejected` is forward-to-terminal — real and mutant both return `True`, so the only scenario in round 1's plan could not distinguish them. |
| M3 | `cv/engine.py` renders despite a non-empty violation list | The **gate-failing lead** from 1.3. `render` is reached only past `if gate_msgs: return`, so on a passing lead the mutation is a no-op. Assert on the **recorder**, not just on `status`. |
| M4 | A preference gate rejects when unconfigured | A config variant leaving `target_locations` **empty** *and* a lead carrying a location. If the harness config sets every gate, the mutant is masked. |

If any mutation leaves the suite green, that invariant is **not** covered — extend the scenario or
record the gap. Do not accept it quietly.

## Fixtures — neutrality

The harness creates more PII-shaped surface than anything recently merged: postings, JDs, CV bundle
entries, rejection emails, a whole vault. Round 1 found the previous draft constrained company names
only.

| Artefact | Convention |
| --- | --- |
| Company names | `Example ...` family |
| Domains, emails, URLs | `example.invalid` (already this repo's convention — `tests/test_sink.py`, `tests/conformance/seeds.py`) |
| Locations | `conftest.py`'s `LOCATIONS` (`Alfa`/`Bravo`/`Charlie`) |
| Job titles | `conftest.py`'s seeded-faker title fixtures |
| Salaries, metrics | synthetic, round numbers |

**Preference values are sourced from `tests/conftest.py`'s existing fixtures, never invented.** The
harness config must carry `accept_titles` / `target_locations` / pay floors to drive triage, and that
is exactly where the maintainer's real preferences would enter. PR 3 sharpens the risk: scenarios
phrased in the user's terms are where an author reaches for the roles and cities they actually want,
because that is what makes them read true. Scenario *names* stay in user language; scenario *values*
come from the fixtures.

**Name check — with a mechanism.** "Check names against the real world" was a round-1 placeholder: no
owner, no method, no consequence, and addressed to reviewers who have no web access. Replaced with:
(a) prefer structurally unclaimable names so the check is rarely load-bearing; (b) the **author**
runs the check and lists what was checked in the PR body; (c) a hit means replace, not justify.

**Note for whoever writes fixtures:** `Solarflux` and `Trueverse` are still live at HEAD in
`tests/test_cv_bundle.py` and `tests/test_core_vault_cv.py` (PR #51 fixed only the three files it
touched). Both are real registered companies. Do not copy them as a convention.

## PR 2 — functional layer, and #7

**Motivation re-grounded.** The round-1 claim was false. The real gap: handler-level tests exist but
are ad hoc — no shared harness, monkeypatch-heavy, inconsistent depth (`test_cv_cli.py` never invokes
a handler at all), and there is no sweep asserting every declared `dest` is read by the handler it
dispatches to. That sweep is #7.

**Constraint from #26's review:** the sweep is **additive**. It removes no existing parse-level
assertion; #26 records an escape where a sweep silently dropped what the enumeration it replaced had
asserted.

## PR 3 — executable acceptance scenarios (`tests/uat/`)

Scenarios named in the user's terms over the same harness — a shortlisted lead's CV contains no figure
absent from the bundle; a rejection email moves that lead to `rejected` and clears its dead-letter
entry; an empty config bins nothing; a re-scrape does not disturb a triaged decision; a CV failing the
gate never reaches the output directory. Each maps to a load-bearing invariant.

## Definition of done

**PR 0:** each seam its own commit; each mutation-witnessed (revert the fix, watch a test go red);
`sluice.yaml.example` untouched (no new config knob — these are constructor arguments, not tunables);
suite green; ruff clean.

**PR 1:** suite green with no network and no browser; **the e2e tier's wall-clock recorded in the PR
body** — if it materially changes the suite's character that is a decision to surface, not absorb;
every mutation row run with its enabling scenario and the stated outcome observed, restored
byte-identically; ruff clean.

## Docs

`docs/ARCHITECTURE.md` already lists **two** renderer implementations (`script`, `weasyprint`), so a
harness fake would be a third — and a test fake does not belong in that document's `Implementations:`
lists at all, which enumerate what config can select. The stale sentence is
`.rulesync/rules/CLAUDE.md:157` ("no runtime selection is exercised yet"), which is canonical; the
root `CLAUDE.md` is generated from it. **`.rulesync/` is human-gated — this edit is proposed, not
applied, and escalated for approval.**

What PR 1 *can* honestly claim: the **fetcher** seam's runtime selection is exercised for the first
time.

## Out of scope

- Any live tier (real Camofox, LLM, Gmail). Declined.
- **#39** (backend seam, three implementations, no conformance suite) — adjacent, and round 1 showed
  it is not *cleanly* separable, since the backend registry's enumerating guard is what bars a fake.
  PR 0.1 sidesteps it via per-instance override rather than resolving it. Recorded, not folded.
- Performance/load testing; any production behaviour change beyond PR 0's three seams.

## Commits

**PR 0**
1. `fix(core): Sluice.backend honours a constructor override (#7-adjacent)`
2. `fix(ingest): Sluice.ingest threads Ctx.sleep`
3. `feat(core): an injectable clock for date-dependent behaviour`

**PR 1**
4. `test(harness): recording renderer and scripted browser client`
5. `test(harness): scripted backend and fake Google client`
6. `test(harness): synthetic fixture set and config factory`
7. `test(e2e): walk the full pipeline in one run`
8. `test(e2e): a re-scrape reaches the vault and touches only last_seen`
