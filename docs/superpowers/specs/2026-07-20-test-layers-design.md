# Test layers — finishing three seams, then e2e, functional and acceptance

- **Date**: 2026-07-20
- **Status**: REVISED after two `/review-plan` rounds. R1 (5 agents): 0C/11H/11M/8L. R2 (5 agents,
  anti-flattery brief): 0C/8H/9M/4L. All folded. R2 **executed** M1/M2a/M4 (red) and measured 0.2's
  timing claim. Ready to build.
- **Goal**: add a test layer above the unit/contract suite — a full-pipeline e2e run, a functional
  layer over the CLI handlers, and executable acceptance scenarios. Four PRs; **PR 0, PR 1 and PR 2**
  are designed here in full, PR 3 in outline.
- **PR 2 design (2026-07-21)**: written after PR 0/PR 1 shipped, grounded in a survey of the five
  existing CLI-test files rather than the outline's assumptions — which corrected the outline's
  "monkeypatch-heavy" premise (see §PR 2). PR 2 mechanics executed against `main` @ `9d60849`.

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

One PR, two deliverables: **D1**, the #7 dest-sweep; **D2**, a `tests/functional/` tier over the
shared harness with a **full additive re-home** of the five existing CLI-test files. It also carries
**one small production change** (§D1's `--all`/`--source` fix), surfaced BY the sweep — so the tier is
mostly-but-not-purely test-only, and that is called out where it lands, not buried.

**Motivation re-grounded — a SECOND correction, recorded not silently fixed.** Round 1 already
falsified "the eight CLI-test files are argparse-only." A survey of the five files (2026-07-21)
falsifies the outline's other premise, **"monkeypatch-heavy."** Across all five there is exactly
**one** `monkeypatch.setattr` (`sluice.track.engine.run`, returning a canned `RunReport`); everything
else is `monkeypatch.setenv` config plus real objects against temp vaults and DBs. So the tier's
value is NOT "remove monkeypatching" — there is almost none. The real defects are:

- **Inconsistent depth.** One file mixes up to three tiers: `test_track_cli.py` calls `main([...])`
  dispatch, direct `cmd_*(SimpleNamespace, None)`, AND direct `Sluice`-method / core-helper calls.
  `test_cv_cli.py` invokes no handler at all (parse-only); the rest span the spectrum.
- **Duplicated ad-hoc scaffolding.** Each file hand-rolls its config: `_env`, `_note`,
  `tempfile.mkdtemp` vs `tmp_path`, inline YAML strings, per-file env-var pinning. There is no shared
  harness or config factory — PR 1 built exactly that and no CLI test consumes it.
- **No dest-sweep** asserting every declared `dest` is read by the handler it dispatches to. That is
  #7.

The honest win of the tier is therefore **one config factory, one registry-isolation fixture, one
`(rc, out, err)` driver, one home** — replacing five files' bespoke scaffolding at a consistent depth
(real `main(argv)` against the harness), with one documented, principled exception (track-run's
engine stub, below).

### D1 — the dest-sweep (#7)

`tests/functional/test_cli_contract.py` — **pure and static, consumes no harness.** The mechanism is
AST, not runtime tracing: a runtime proxy only observes the branch a given invocation takes
(`cmd_apply_prep` reads different dests down its `all_shortlist` / `dry_run` / else arms), so a
dest read only on an untaken path would look unread. Static analysis answers "read on SOME path,"
which is the property #7 wants.

- **Enumerate leaf parsers.** Walk `_build_parser()`; a **leaf** is a parser with `func` in its
  `_defaults` (this identifies all 15 handlers — the two-level `ingest run` and the one-level
  `health`/`doctor` alike). Its declared dests are the `_actions` minus the `-h` help action /
  `SUPPRESS`. `group`, `cmd` and `func` never appear in a leaf's own `_actions` (they belong to the
  parent subparser action, or to `_defaults`), so the structural dests are excluded for free —
  **enumerate from `_actions`, never from a parsed `Namespace`,** which would surface them.
- **Collect reads.** AST-analyze each leaf's bound `func`, **transitively following any module-level
  `sluice.cli` function it calls with `args` passed positionally** — mapped by argument position to
  the callee's parameter name (the one real case at HEAD is `cmd_run → _selected(args, …)`).
  A read is `args.X` (an `ast.Attribute` on `Name('args')`) or `getattr(args, "X", …)` (an
  `ast.Call` to `getattr` with `Name('args')` and a string-constant name).
- **Assert** `declared ⊆ read ∪ opt_out` for every leaf.
- **The one thing the sweep finds at HEAD, and the production fix it forces.** The only genuinely
  unread dest is `("ingest run", "all")`, and review established it is NOT a benign "explicit form of
  the default": `--all` and `--source` are not mutually exclusive (`cli.py:368-369`) and `_selected`
  keys solely off `getattr(args, "source", None)` (`cli.py:59-64`), so `ingest run --source reed
  --all` silently runs only `reed` and ignores `--all` — a silent-degrade of exactly the
  declared-but-unread class #7 exists to catch. The **production fix (this PR):** put `--all` and
  `--source` in a `mutually_exclusive_group`, so the ambiguous combination now *errors* instead of
  silently dropping `--all`. This also brings the code in line with the module docstring, which
  already documents the interface as `run [--all|--source ID ...]` — the `|` claimed an exclusion the
  code never enforced.
- **The opt-out survives the fix — and that is honest, not a gap.** Mutual exclusion is argparse-level
  validation; the *handler* still never reads `args.all` (it is the explicit spelling of the default
  all-sources path, which `_selected` reaches when `args.source` is falsy). So `("ingest run", "all")`
  stays the sweep's single opt-out, now with an accurate justification: unread because it selects the
  default, and no longer a silent-degrade because the dangerous combination is rejected at parse time.
  Every other declared dest at HEAD is read.
- **Additive, and anti-#26 at its own level.** The sweep *adds* to the existing parse-only
  argparse-attribute assertions (`args.lead == "acme-em"`, `args.backend == "deepseek"`, the
  required-mutually-exclusive `SystemExit` cases); it removes none — parsing and reading are
  different properties. And the **opt-out list is itself validated**: every entry must name a real
  leaf+dest that is genuinely unread. A stale entry (the flag was removed, or is now read) fails.
  That closes, at the sweep's own level, the #26 escape where a sweep silently suppressed what it
  replaced.
- **Anti-vacuity witnesses (build-time, each run twice — the named test RED, then isolated; run
  `compileall --invalidation-mode checked-hash sluice tests` first, since the enumeration half walks
  the imported `_build_parser()` bytecode while the read half is AST over source — a same-second
  size-preserving edit could skew them):**
  (a) add a declared-but-unread `--foo` to a subparser → RED (this is an ADD, and correctly so: for a
  completeness checker `declared ⊆ read`, injecting an un-read element is the checker's own failure
  mode — it enlarges the *input set the checker quantifies over*, not a check placed beside an
  existing one, so the MOVE/DELETE discipline below does not apply to it);
  (b) delete a real read (`dry_run=args.dry_run` in `cmd_track_run`) → RED;
  (c) **delete the transitive `source` read inside `_selected`** → `ingest run`'s `source` becomes
  unread (it has NO direct read — `getattr(args,"source",None)` at `cli.py:60-61` is its only one,
  reached only via the `cmd_run → _selected` follow) → RED. **This is the load-bearing witness the
  first draft lacked:** without it the entire transitive-follow mechanism — the sweep's most
  error-prone part — is inert-testable, and an over-broad follow would stay green at HEAD;
  (d) remove `--all` from the parser but leave it in opt-out → stale-opt-out RED;
  (e) make `args.all` read → now-read-opt-out RED.
  Witnesses (b)–(e) mutate by MOVING/DELETING production code or the opt-out list, never by ADDING.

### D2 — the functional tier (`tests/functional/`, mirrors `tests/e2e/`)

- **`tests/functional/conftest.py`** provides two things:
  - **the registry-isolation autouse fixture, LIFTED into `tests/harness/`** as a shared helper both
    tiers import. PR 1's copy lives in `tests/e2e/conftest.py`; a functional tier needs the identical
    behaviour (the harness registers `scripted`/`recording` through the real `register()` API), so the
    fixture moves to the harness and e2e imports it too — one implementation, no drift. It MUST keep
    PR 1's ordering guarantee: force each seam's `autoload` before snapshotting, or restore drops the
    production impls permanently.
  - **a `cli` driver fixture** built on `build_harness(...)`, exposing `run(argv) -> (rc, out, err)`
    (capturing via `capsys`) and the `Harness` (its `vault`, `paths`, `recorder`). It **forwards the
    preference knobs** (`target_locations`, `accept_titles`, `reject_titles`, pay floors) through to
    `build_harness` so a test can set the gate config its scenario needs — which requires
    `build_harness` to grow a `reject_titles` param (defaulting **empty**, i.e. abstain; the shipped
    factory today sets only `accept_titles`/`target_locations`/floors). This is load-bearing for the
    triage neutral-defaults re-home below, which must run with `target_locations=()`.
- **The bounded constructor patch — the accepted backend bridge.** Handlers build their own
  `Sluice(config)` and the CLI exposes no backend injection point; `main(argv)` passes no override,
  and registering a fake backend is barred (`test_backend_registry` asserts set-equality). The
  driver therefore `monkeypatch.setattr`s `sluice.core.app.Sluice` to **a subclass of the captured
  real class** (NOT a delegating function or proxy) whose `__init__` `setdefault`s
  `backend=<the test's ScriptedBackend>`, `sleep=<noop>` (so ingest's page-settle costs nothing —
  PR 0.2 measured 5.013s real) and `today` when a test needs a fixed clock, then calls `super()`.
  A subclass, not a proxy, because the patched name is the *module global* and `Sluice`'s own methods
  resolve it at call time — `doctor()` self-references `Sluice.available("backend")`
  (`app.py:535`), a classmethod a bare-callable wrapper would not carry (`AttributeError` in a
  functional `doctor` test). It works because every handler does a **lazy** `from sluice.core.app
  import Sluice` at
  call time (the "cli.py imports heavy modules inside command functions" convention), so the patched
  attribute is what they get. It binds only composition-root seams — the CLI-tier equivalent of
  PR 1's `harness.sluice()`; **all business logic stays real,** and backend-free handlers never touch
  the bound backend. This is the tier's single patch, and it is NOT a production test-seam: adding an
  `app_factory=` parameter to `main` would itself be the "capability reachable only from tests" that
  #7 exists to catch, so it is deliberately avoided.
- **track-run's Gmail edge.** `Sluice.track(client=…)` is a *method* argument, not a constructor
  seam, so the constructor patch does not reach it. The migrated track-run tests **preserve the
  existing `sluice.track.engine.run` stub** (returning a canned `RunReport`) to exercise the
  handler's stderr formatting and the four-branch lastrun-gating contract (success → lastrun written;
  dry-run → none; `auth_error` → rc 1, none; `deadletter_error` → rc 0 but none, so the watermark
  never advances past an unpersisted message). This is the one legitimate sub-engine stub — the
  correct seam for post-engine handler logic, which a live Gmail+LLM run could not test
  deterministically anyway. It is documented as such, not left to read as ad-hoc depth.
- **Drive via real `main(argv)`,** not direct `cmd_*(SimpleNamespace, None)`: real parse + dispatch,
  and `config` is always `load_config()` (never the `None` the old direct-call tests passed). Each
  functional test seeds its own scenario lead notes into the harness vault (the harness supplies
  config + a seeded baseline/experience vault; the test supplies the scenario), exactly as the e2e
  tests do. This also makes each handler test a *dynamic* complement to the static sweep.

### Migration map — every assertion preserved (#26 zone)

| Old file | → functional tier (handler behaviour) | → unit/app/store (below-CLI, re-homed not dropped) |
| --- | --- | --- |
| `test_cv_cli.py` (parse-only, 2) | `cv run` for real (via `main` + the scripted backend) + kept choices/default parser assertions | — |
| `test_cli.py` (7) | `list-sources`; `enable`/`disable` persistence; `health`; **`ingest run` no-enabled-sources → rc 1** (new — unwitnessed at HEAD, `cli.py:114-116`); **`--all`/`--source` mutual-exclusion → `SystemExit`** (new, pins the production fix) | **both** `_print_report` behaviours — `surfaces_skipped` AND the sparse merged/refused `#5` guard (`cli.py:147-155`) → a `_print_report` unit test |
| `test_triage_cli.py` (2) | `normalize-status --dry-run` (negative side-effect); **`run --no-llm` with explicit `target_locations=()`** — dismiss a location-bearing lead via a synthetic `reject_titles`, assert the dismissal is *attributable* (same lead is NOT dismissed without the reject) | — |
| `test_apply_cli.py` (10) | `prep` lead / all-shortlist / dry-run — incl. the load-bearing pin `"apply-prep: northwind dry-run" in err` AND `"previewed" not in err`; **`prep` no-match → rc 1 + `"skipped"`** (`cli.py:266-267`); `record` status-flip / refused | — |
| `test_track_cli.py` (9) | `run` four-branch lastrun gating (engine stub); `confirm` status-advance; **`dismiss` dry-run-then-real dispatch** via `main(["track","dismiss",…])` — rc contract + `open_entries()` unchanged-then-cleared (test 9, added by a prior review as its "finding 3"; it has no other home) | `_load/_save_lastrun`, `track_dismiss` dict-returns + deadletter `open_entries()` state + the `ValueError` selector guard → app/store unit test |

**Every migrated fixture identity value is re-expressed in the vetted conventions**, carrying over
only the behavioural assertion, not the literal string: `Tidemark` → the `Example …` family;
`--ats greenhouse` → `example-ats` (the one-line #55 fix); the hardcoded `reject_titles` value
(`"aid/development worker"`) → a synthetic title from `conftest.py`'s seeded-faker fixtures. The
author name-check runs over the **five re-homed files**, not only PR 1's set — because the spec's
neutrality convention below governs *new* fixtures, and without this clause the re-home ships with no
neutrality gate over the exact files it copies.

**Additivity is verified by a per-assertion LEDGER, not by "assertion kind present."** A kinds
checklist cannot catch a dropped *instance* whose kind survives in another test — which is exactly how
`test_cmd_track_dismiss_dry_run_then_real`, one of the two `_print_report` cases, and the apply
no-match branch nearly vanished above while `return-code` / `stderr-substring` stayed green elsewhere.
The DoD therefore requires a written **old assertion → new home** ledger for all five files (each old
assertion mapped to a named new functional test or a specific re-homed unit test, counts preserved),
plus the belt-and-braces check of **running the old five files alongside the new tier for one commit**
to confirm zero net assertion loss before deleting them.

**The ledger records PRECONDITIONS, not just assertion strings.** The sharpest near-miss is the
triage neutral-defaults test: its assertion `Vault.read_leads()[0].status == "dismiss"` is
load-bearing ONLY under the precondition `target_locations == ()` (empty) — that is what proves the
unconfigured location gate abstains (the `672ad2a` invariant, per the test's own comment). Re-home it
onto `build_harness`'s default `target_locations=("remote",)` — *the literal `672ad2a` bug value* — and
the same lead is binned by the *location* gate, the assertion still passes, and the test greenly
demonstrates the historical bug instead of guarding against it. So the ledger entry for it carries the
precondition (`target_locations=()`) and the attribution check (not dismissed without the reject),
not merely the status string.

The assertion kinds that must all appear in the ledger: return codes 0/1; stdout AND stderr
substrings (the summary prints to stderr); the `apply-prep` wording pin (`"dry-run"` present AND
`"previewed"` absent); filesystem positive/negative side-effects (`CV.pdf` staged or not, `lastrun`
present or not); front-matter mutations (`status: applied`, `status: offer`, dry-run leaving
`dismissed` untouched, `Vault.read_leads()[0].status == "dismiss"` under empty `target_locations`);
deadletter store counts/sets/empty; `track_dismiss` dict returns; `pytest.raises(SystemExit)` for
argparse and `pytest.raises(ValueError)` for the dismiss selector guard; the pure argparse-attribute
assertions.

### Non-duplication

The sweep is **static** ("every declared dest is read on some path"); the functional tests are
**behavioural** ("the handler forwards its args and produces the right effect"); the e2e tier drives
`Sluice` **directly**, never through `cli.py`, so `cmd_cv_run`'s own handler logic (its stderr
formatting, the `--all-shortlist` empty→rc1 branch, the notify) is covered by nothing today and
nothing in e2e. The `--backend`-parsed-but-not-forwarded bug that motivates #7 is caught **both**
statically (the sweep: `args.backend` unread → red) and behaviourally — but the behavioural witness
must be an **invalid role**, `main([…, "--backend", "not-a-role"]) → rc 1 / `BackendError``, with a
valid role succeeding. "Observe which role the scripted backend was built for" does NOT work: the
per-instance override short-circuits `Sluice.backend()` for *every* valid role after only a role-name
guard (`app.py:213-238`), so a dropped `--backend` (defaulting to `auto`) returns the same object as a
forwarded one. Only the invalid-role path is behaviourally observable — it reaches the role guard iff
`args.backend` was forwarded — and it is a genuinely different failure surface from the sweep's
(the sweep catches "handler never reads `args.backend`"; this catches "handler reads it but forwards a
wrong/absent value").

### Neutrality and harness-reuse gotchas (carried from PR 1)

- **Both new AND migrated** fixtures follow `build_harness`'s vetted conventions: `Example …`
  companies, `example.invalid` domains/emails, the `Remote` work-arrangement token, synthetic round
  pay floors, and preference-list values (titles) from `conftest.py`'s seeded-faker fixtures. The
  re-home is where this bites hardest, because "preserve the assertion verbatim" pushes an implementer
  to copy a real-looking name unchanged — see the re-expression clause in the migration map above.
- `ScriptedBackend` keys composed CVs by **company** — fine for single-lead functional tests; lift
  only if a scenario needs two different CVs at one company.
- The scripted browser client matches `document.body.innerText` **exactly**, not by substring.

### DoD

Suite green with no network and no browser; ruff clean; `sluice.yaml.example` untouched (the driver
uses constructor arguments, not a new config knob — `build_harness`'s new `reject_titles` param is a
factory argument, not a shipped tunable). **One production change**, called out here rather than
buried: the `--all`/`--source` `mutually_exclusive_group` in `cli.py`'s parser, pinned by a new
`SystemExit` test; no other production behaviour changes. The **five** D1 witnesses (a)–(e) each run
twice — named test RED, then isolated — after `compileall --invalidation-mode checked-hash sluice
tests`; witness (c) specifically proves the transitive-follow is live. The migration **ledger
balances** (every old assertion mapped to a new home, zero net loss, verified by running old+new
together for one commit), including the triage `target_locations=()` precondition. One CodeRabbit slot
(per the merge gate). The stale `.rulesync/rules/CLAUDE.md` lines ("no runtime selection exercised
yet"; `fetch` for the `fetcher` key) are noted human-gated — proposed to the user, not applied here.

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

**PR 2** (D1 = the sweep + its one production fix, D2 = the functional tier + re-home)
1. `test(harness): lift the registry-isolation fixture out of the e2e conftest` — shared by both
   tiers, e2e now imports it; no behaviour change.
2. `fix(ingest): --all and --source are mutually exclusive` — the one production change; lands BEFORE
   the sweep so the tree never has the sweep blessing a live silent-degrade. Pinned by a `SystemExit`
   test.
3. `test(functional): #7 dest-sweep — every declared flag is read by its handler` — D1, closes #7
   (opt-out list = the now-honest `("ingest run","all")`).
4. `test(functional): a cli driver over the harness` — conftest + the bounded constructor patch
   (subclass), and `build_harness`'s `reject_titles` knob.
5. `test(functional): re-home the ingest and health CLI tests` (+ the `_print_report` unit test, the
   no-sources→rc1 and mutual-exclusion coverage).
6. `test(functional): re-home the triage and cv CLI tests` (triage with explicit `target_locations=()`).
7. `test(functional): re-home the apply CLI tests` (+ the prep no-match branch).
8. `test(functional): re-home the track CLI tests` (+ the `dismiss` dispatch test and the lastrun /
   `track_dismiss` unit tests).
