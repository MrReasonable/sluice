# Test layers — finishing three seams, then e2e, functional and acceptance

- **Date**: 2026-07-20
- **Status**: REVISED after two `/review-plan` rounds. R1 (5 agents): 0C/11H/11M/8L. R2 (5 agents,
  anti-flattery brief): 0C/8H/9M/4L. All folded. R2 **executed** M1/M2a/M4 (red) and measured 0.2's
  timing claim. Ready to build.
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

## PR 0 — three dropped injection points

**Round-2 correction:** the previous draft called 0.3 "the only genuinely new seam" and argued it
against YAGNI. That was wrong, and asserted without reading the signature — three reviewers found it
independently. `VaultSink.__init__` already takes `*, today=None` (`sink.py:25`, used by 6 tests) and
`Sluice.track` already takes `now_iso=None` (`app.py:378`). So all three items are **the same
defect in three places: the composition root accepts, or could accept, an injection point and drops
it.** No new seam is introduced, and the YAGNI question disappears.

### 0.1 `Sluice.backend()` must honour `_overrides`

`__init__` stores `**overrides`; `_resolve` honours them for store/fetcher/renderer; `backend()`
never calls `_resolve`, so `Sluice(config, backend=X)` is accepted and silently ignored — the
quiet-wrong-default class `backend()`'s own comment says it exists to remove.

**Placement is load-bearing.** The check goes **after** the role-validation raise (`app.py:199-202`),
not at the top. Three reviewers independently verified that checking first makes
`Sluice(cfg, backend=X).backend("primry", ...)` return `X` instead of raising — the fix for one
quiet-wrong-default installing another. `test_unknown_role_raises_rather_than_defaulting_to_auto`
passes no override, so it stays green; a new test pins the override-plus-bad-role case.

**`backend()` must NOT route through `_resolve`.** `_resolve` memoizes per seam, and `backend()` is
deliberately uncached (its own comment, `app.py:190-192`) because each sub-app passes different
config — caching would hand cv triage's model.

### 0.2 `Sluice.ingest()` must pass `Ctx.sleep`

`ingest/base.py:33` declares `sleep: Callable = field(default=time.sleep)` — "an injectable sleep so
tests don't actually wait for page settle". `app.py:253` drops it.

**Measured, not estimated:** a shipped `BrowserListSource` (remoteok, `wait=4`, `scrolls=2`) takes
**5.013s** with real sleep and **11µs** with a no-op, against a suite that runs in **1.16s** total.

### 0.3 `Sluice.ingest()` must pass `today` to `VaultSink`

One line: `app.py:259` builds `VaultSink(self.store(), seen)` and drops the `today=` the sink already
accepts. Identical in shape to 0.2. A new `Sluice`-level clock would have been *worse* — a second
clock beside `now_iso` in a different shape (date string vs UTC ISO timestamp), which is the
inconsistency PR 0 exists to remove.

**Hazard checked and cleared:** an injectable clock could in principle let a caller move time
backwards and defeat `_bump_last_seen` monotonicity. Executed — create at 2026-07-09, re-scrape at
2026-07-01 — `last_seen` stayed at 2026-07-09 and nothing was written. Not a new hazard.

### 0.4 Validate override keys

`__init__` filters `None` out of `**overrides` but never validates the keys, so after 0.1 a
correctly-spelled override works and a misspelled one is *still* silently ignored — the same defect
one level up. Live trap: the docs call the seam `fetch`, the key is `fetcher`. Validate against the
four seam names and raise `UnknownAdapter`, listing them, per the fail-loudly-at-construction rule.

**Ordering constraint this creates:** `sleep` and `today` must be explicit keyword-only parameters,
not `**overrides` members, or key validation rejects them.

### 0.5 `tests/conformance/seeds.py` — remove `Solarflux`

The fixture table below cites `seeds.py` as the `example.invalid` exemplar, and that same file seeds
a real registered company name. A harness author following this plan would copy the bad convention
from the file the plan praises. **Scope decision (user-confirmed): fix `seeds.py` only.** The name is
live in five sites; the other four are a repo-wide pass and are named in §Fixtures as known-bad, not
silently left.

### PR 0's own tests

**Round-2 finding (dependency-order):** PR 0's DoD demanded "revert the fix, watch a test go red",
but nothing at HEAD constructs `Sluice(config, backend=...)`, so reverting 0.1 leaves the suite
green. PR 0 therefore ships **its own unit tests** — covering the three injection points (backend
override, `Ctx.sleep`, the sink's clock) and 0.4's key validation, in both its accept and reject
directions — so each fix is witnessed without depending on PR 1's harness. Seven test cases in one
file, `tests/test_app_injection.py`.

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
  unstable. The rule: dispatch on a stable **prefix** of the prompt's first line, and **raise on an unrecognised prompt**.
  Round 2: exact first-line matching works for triage and track (stable literals) but NOT cv —
  `cv/compose.py:49` is `f"Compose a tailored CV for {name} applying for {role} at {company}."`,
  fully interpolated, so exact matching would raise on every CV call and M3 could not run rather than returning a default — a silent default would let a mis-wired call
  pass as success.
- **`FakeGoogleClient`** — matches `RealGoogleClient`'s surface.
- **Config factory** — writes YAML to `tmp_path`, `SLUICE_CONFIG` points at it, and
  **`cv.output_dir` is pinned inside `tmp_path`**. It defaults to `./cv-output`, relative to pytest's
  cwd, so an unpinned run writes CV output into the repo.

### 1.3 The e2e test

One run through `ingest → triage → cv → apply → track`, asserting user-visible outcomes at each hop.
**It includes one lead whose composed CV fails the gate** — not deferred to PR 3, because witness M3
needs it (§Mutation witnesses).

## Mutation witnesses — mutation, scenario, and the test that must go red

Round 1: three of four would have stayed green. Round 2 executed the rebuilt set and found two more
problems, both folded below.

**Methodology, from round 2 — this is the part that generalises.** A mutation killed by a
*pre-existing* test witnesses nothing about the new tier. M3's mutant is already caught by four
`test_cv_engine.py` tests, so "watch it go red" merely re-observed the old suite. **Every witness
names the specific test that must go red, and is run with that test deselected to confirm the
mutation is otherwise green.** Run `compileall --invalidation-mode checked-hash` once first; mutate
by moving or deleting.

| # | Mutation | Enabling scenario — required | Status |
| --- | --- | --- | --- |
| M1 | `Vault.upsert` rewrites the body on re-scrape | Fresh `seen_db` + advanced clock + `written['updated']==1` as a **precondition**. `_run_source:93` skips seen keys before `sink.write:60`, so without this `upsert` never runs. | **executed, red** |
| M2a | `can_advance` backward/rank guard → `return True` | seeded `interview` lead + phone-screen signal | **executed, red** |
| M2b | `can_advance` **terminal** guard deleted | seeded **`rejected`** lead + phone-screen signal | **round-2 addition.** M2a alone is green for this mutant — and it is the never-regress case with the worst failure, a rejected lead resurrected |
| M3 | `cv/engine.py` renders despite violations | the gate-failing lead; assert on the **recorder** | red, but 4 pre-existing tests also kill it — **must be run with those deselected** |
| M4 | preference gate rejects when unconfigured | empty `target_locations` + a lead carrying a location | **executed, red** |
| M5 | `triage/apply.py::_guarded` → `False` | a lead already at `applied`. Executed by round 2: triage then returns `"applied"` and writes `{'status': 'new'}` over an application-owned lead. | **round-2 addition** — previously unwitnessed |
| M6 | `can_apply` weakened — **name the exact guard change when PR 1 builds it**; "weakened" is not reproducible as written | apply attempted from a non-`shortlist` status | **round-2 addition.** `can_apply` is deliberately a different predicate from `can_advance` and had no witness |

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

**Round-2 additions — three gaps in the table above.**

- **The CV author's identity was missing entirely, and it is the most PII-dense artefact here.**
  `cv/engine.py:60` calls `vault.read_baseline()` and `:68` passes `name=cvcfg.name,
  contact=cvcfg.contact`. So the cv hop needs a **synthetic baseline CV seeded into the tmp vault**
  plus synthetic `cv.name` / `cv.contact`. `CvConfig.contact` ships `""` with the comment "Entirely
  personal, so the code ships with no contact info" — the harness must not be where that gets filled
  in with anything real.
- **Nine config paths default relative to cwd, not one.** `cv.output_dir`, `cv.served_dir`,
  `cv.dossier_dir`, `cv.render_home`, `triage.dossier_dir`, `triage.audit_jsonl`, `track.seen_db`
  (plus its `.lastrun` / `.deadletter.db`), `track.token_path` (`./google_token.json` —
  credential-shaped), and `core/seendb.py`'s `_DEFAULT = "./seen.db"`. **All are pinned inside
  `tmp_path`, and the run asserts the repo-root listing is unchanged afterwards.** The `seen.db`
  default also breaks witness M1, which requires a fresh one.
- **Pay floors have no fixture, so "values come from `conftest.py`" is unimplementable for them.**
  `conftest.py` supplies titles and `LOCATIONS` but nothing for `contract_floor_gbp_day` /
  `perm_floor_gbp`. Resolution: pay floors are **synthetic round numbers chosen for the scenario**,
  stated as the one deliberate exception, because a currency-denominated personal number is exactly
  what must not be copied from real config.

**Known-bad, deliberately not fixed here:** `Solarflux` is live in five sites. PR 0.5 fixes
`tests/conformance/seeds.py` only — the one this spec cites as an exemplar. `test_cv_bundle.py`,
`test_core_vault_cv.py` and the remainder stay, and `Zenith` / `Novacraft` are unassessed. Do not
copy any of them as a convention.

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

**Note for whoever writes fixtures.** This is a **known rule violation tracked as #53**, not an
accepted state: CLAUDE.md requires synthetic fixtures and these are not. After PR 0.5, real
registered company names remain live in **three** files: `tests/test_cv_bundle.py`, `tests/test_core_vault_cv.py`, and
`tests/conformance/test_store_contract.py` — the last being in the *same directory* as the seed
fixture 0.5 cleans, so it is the nearest wrong example to hand. `Solarflux`, `Trueverse` and
`Zenith` are all real marks. Do not copy any of them as a convention.
(An earlier count of "five sites" was wrong: it is 4 files / 12 occurrences at `main`, 3 files
after 0.5. The miscount is itself the argument against an honour-based name check — see below.)

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

**PR 0:** each fix mutation-witnessed twice — the named new test RED, and the suite with that test
file deselected GREEN. **Landed as one production commit, not three:** validating override keys
forces `sleep`/`today` to be explicit keyword-only params rather than `**overrides` members, so
0.4 cannot land apart from 0.2/0.3 (§0.4's ordering constraint).
Also required:
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

**PR 0** (as landed)
1. `fix(core): the composition root must not drop injected dependencies` — 0.1 through 0.4 in one
   commit, per the ordering constraint in §0.4
2. `test(conformance): drop a real company name from the seed fixture` — 0.5
3. `docs(architecture): distinguish adapter seams from injected collaborators`
4. `fix(ingest): Ctx tolerates sleep=None instead of trusting its callers` — pre-push review fold
5. `fix(conformance): the seed fixture wrote a frontmatter key the store never reads` — review fold

*(The originally planned split — one commit per seam — is superseded: validating override keys forces
`sleep`/`today` to be explicit keyword-only params, so 0.4 cannot land apart from 0.2/0.3.)*

**PR 1**
4. `test(harness): recording renderer and scripted browser client`
5. `test(harness): scripted backend and fake Google client`
6. `test(harness): synthetic fixture set and config factory`
7. `test(e2e): walk the full pipeline in one run`
8. `test(e2e): a re-scrape reaches the vault and touches only last_seen`
