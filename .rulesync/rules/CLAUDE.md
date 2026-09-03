---
root: true
targets:
  - '*'
globs:
  - '**/*'
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**This file is generated.** The canonical source is `.rulesync/rules/CLAUDE.md`. Run
`npm ci --ignore-scripts && npm run rulesync` after cloning to populate the AI-tool outputs
(`CLAUDE.md`, `AGENTS.md`, `.claude/`, ...), all of which are gitignored. Editing a
generated file instead of the `.rulesync/` source is drift. The version and the flags both
live in `package.json`, so this command never names either -- and CI runs the same one,
`--ignore-scripts` included: one package in the pinned tree declares a postinstall, so a doc
that drops the flag sends a human down an install path CI deliberately does not take.

## Commands

```bash
pip install -e ".[test]"        # pytest, pytest-cov, faker (see Neutrality), jinja2 (see the
                                 # renderer seam below), setuptools + build (tests/test_packaging.py
                                 # builds a real wheel offline)
python -m pytest                # fast, fully offline: no Camofox, no network
python -m pytest tests/test_triage_engine.py            # one file
python -m pytest tests/test_triage_engine.py -k judge   # one test
ruff check sluice tests scripts         # NB: ruff is NOT in [test]; pip install ruff==0.15.21 (the CI pin)

# Coverage, the way CI runs it (#11). Every knob -- which tree, branch coverage, the missing-line
# column -- lives in pyproject.toml, so the bare flag renders the same report CI publishes.
# It REPORTS and does not gate: there is no threshold, deliberately, because a floor invites
# tests written to raise the number rather than to catch bugs.
python -m pytest --cov

# Run ONCE before mutation testing: content-addresses the .pyc caches so a mutant can't run
# stale bytecode and lie green. Proving a test fails is the mutate-then-pytest step; see below.
# `scripts` is included so mutation-testing a scripts/ helper (e.g. guard_no_bypass.py) is covered too.
python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

**Mutation testing.** The claim "this test would catch that" is worth nothing unverified, so the way
to check a test is load-bearing is to break the code and watch it go red — not to reason about it.
Two things make a mutant lie *green*, which reads as "this test is inert" and gets a real guard
deleted:

- **Mutate by MOVING or DELETING, never by ADDING.** A check added beside the original is an
  equivalent mutant: the original still fires and the suite stays green.
- **Stale bytecode.** CPython invalidates a `.pyc` on *(source mtime, size)*, so a size-preserving
  edit restored within the same second runs the OLD bytecode against the NEW source — silently.
  `text =` → `return` is exactly that shape (each carries a trailing space, so both are 7 bytes),
  and it has already cost a debugging session. The `compileall --invalidation-mode checked-hash`
  line above makes `sluice/`'s and `scripts/`'s caches content-addressed, which is what mutation
  testing needs, since mutants go in production code. Measured: 90/90 stay hash-based across mutate → pytest → restore,
  so it is durable and costs nothing measurable. Clearing `__pycache__` also works but is a
  discipline you must remember every time, and forgetting it fails in the dangerous direction.

  It content-addresses `sluice/` and `scripts/` (both are plain imports, so the checked-hash cache is
  what runs). It does **not** protect `tests/`, even though `tests` is on the line: pytest's assertion
  rewriter keeps its own `*-pytest-N.N.N.pyc` alongside, those are timestamp-based, and pytest imports
  *those*. So a size-preserving edit to a TEST file within the same second is still exposed. That is not
  the mutation-testing case (mutants go in production code under `sluice/` or `scripts/`), but do not
  read the line as protecting more than it does.

`inspect.getsource` cannot diagnose the second one — it re-reads the source file, so it happily
shows corrected code while stale bytecode executes. Run the function and look at what it returns.

**Guard tests fail open, and the failure is invisible.** Four ways it has actually happened here, each
found by running the guard rather than reading it:

- **A sweep that discovers nothing passes.** `all([])` is `True`, and a discovery loop whose matcher
  is broken yields an empty set that satisfies every assertion over it. Assert on the SCOPE, never on
  the violations: a guard must pin that it enumerated the things it meant to look at (the loaders,
  the `*Config` classes, the settings in the example file), because for a *negative* guard — a leak
  gate, a forbidden-pattern sweep — finding nothing is the success case, and demanding a non-empty
  result there would be backwards. One such assertion, added late, caught its own sweep matching
  nothing at all on the very first run — the walk resolved imports but not class definitions, so it
  had been enumerating an empty set.
- **A pattern consumed by two engines must be asserted through the engine that RUNS it.** A regex
  built for Python `re` and handed to `git grep -E` is not the same regex: inside a bracket
  expression POSIX treats `\` as a literal member, not an escape, so the class terminates early. A
  neutrality gate written that way matched nothing for its entire life while its regression test —
  which compiled the string with `re`, where the escapes do work — certified it green.
- **Hand-listed names lose to an import alias.** `from x import y as _z` walks straight past a sweep
  keyed on `"y"`. Derive the local bindings from each file's own `ImportFrom` and `ClassDef` nodes.
  For the same reason, an allow-list of path COMPONENTS accepts anything after the component; allow
  whole values.
- **A comment that states a mechanism needs a row that falsifies it.** `except BaseException` was
  justified in a comment by `KeyboardInterrupt`; swapping it to `except Exception` left the whole
  suite green, because nothing tested that arm. Prose is not a check, and a *reason* stated in a
  comment goes stale silently — grep the CLAIM, not just the code that changed.

The suite is fast and hermetic — there is no reason not to run all of it. `run_tests.sh` is the same
thing via `.venv/bin/python`, so it needs a `.venv/` (gitignored) to exist first. CI
(`.github/workflows/ci.yml`) runs `lint` (ruff + zizmor), `test` (pytest on Python
3.12/3.13/3.14), `rulesync` (regenerates `.rulesync/`'s outputs and fails the build on any drift
or hand-edited generated file), `docker` (builds the image, runs the
smoke script against it, renders the compose file and checks the ssh key path does not leak
into the container environment), and `packages` (#218: builds the wheel, sdist, .deb and .rpm
and installs each into a clean environment; the .deb and .rpm legs additionally RUN AS A
NON-ROOT user, which is the only way the #104 directory-mode class is visible -- the wheel and
sdist legs have no such step, and attaching that property to all four would read as a
guarantee three of them do not carry) -- plus `ci-success`, the aggregate gate over every one
of them. Deliberately NO COUNT in that sentence: it read "four jobs ... the aggregate gate
over the first three" while five were already running, having gone stale when `docker` was
added and never noticed, which is this repo's most-repeated finding shape applied to its own
documentation. `tests/test_ci_wiring.py::test_every_real_job_is_aggregated_by_ci_success` is
what actually pins the roster -- it sweeps the `jobs:` block and fails if any defined job is
missing from `ci-success`, which a prose count cannot do and a reader cannot verify.

Running the pipeline:

```bash
export SLUICE_CONFIG="$(pwd)/sluice.local.yaml"  # git-ignored; quoted for paths with spaces
job-sluice init --no-input --vault ./vault       # writes the config + a Judging Profile
job-sluice ingest list-sources --health
job-sluice ingest run --source reed --dry-run  # dry-run/JSON sink never writes vault or seen.db
job-sluice triage run --no-llm              # deterministic classify only, no backend call
                                           # (needs leads already in the vault: the dry run above
                                           #  writes none, so drop --dry-run to feed this)
```

**Do NOT `cp sluice.yaml.example` into place — that gives you a config whose gates are already
CLOSED, and nothing says so.** `sluice.yaml.example` is a CATALOGUE: it ships illustrative values
ACTIVE, not commented, and `relevance_keep` is applied at ingest before dedup and before any LLM
call. Measured against a verbatim copy, `is_relevant("Senior Software Engineer")` is `False` — only
a `horticultural consultant` survives, and `accept_titles`, `contract_floor_gbp_day` and
`perm_floor_gbp` are live too. So a fresh copy scrapes and then silently discards nearly
everything, which reads as a broken source rather than a closed gate.

`job-sluice init` (#8) exists to remove that trap: it renders the config FROM the question catalogue
with every unanswered key COMMENTED, so an unanswered run writes a file that is field-for-field
equal to no config at all EXCEPT `vault_dir` — the wizard's one required answer, and the one
difference `tests/test_onboard_plan.py` exempts by name. It never overwrites an artefact, so
re-running is safe. The example file stays a catalogue to read, not a template to copy, and
`tests/test_no_copy_instruction.py` fails the build if any shipped doc goes back to instructing
the copy.

`ingest run` and `ingest test-source` drive a live Camofox browser server; every other command is
offline. `job-sluice ingest test-source ID --raw` prints the raw fetch payload, which is how golden
parser fixtures get captured. **A fresh capture is real board output, so it arrives carrying real
employer names and the posting's real location -- in `company`, in `title`, and in the URL slug.**
Scrub it before committing, then update the rosters and that source's digest in
`tests/test_fixture_name_neutrality.py`; the digest test fails until you do, and its message says
so. That gate is the whole reason #27 cannot recur silently, so do not paste a new digest without
reading the diff it is certifying.

## Architecture

Pipeline: `ingest -> triage -> cv -> apply -> track`. Five sub-apps under `sluice/`, all sitting on
`sluice/core/`, plus two COMMAND packages, neither a sixth sub-app: `sluice/onboard/` for
`job-sluice init` and (#164) `sluice/evidence/` for the nine `job-sluice
{experience,skills,stories} {add,list,verify}` handlers. Neither pipeline sub-app -- ingest,
triage, cv, apply, track -- imports either. `cli.py` imports both, and neither import sits at
cli.py's own module scope, but that is NOT uniformly the same as deferred until the command
runs: `sluice.onboard.ask`/`.plan`/`.questions` and `sluice.evidence.wizard`'s `collect_evidence`
are imported inside `cmd_init`'s own body, so none of them loads unless `init` actually runs, but
`sluice.evidence.commands` is imported inside `_build_parser()`, which runs on EVERY invocation to
build the whole argparse tree -- so it loads unconditionally, and being inside a function body
only keeps it off cli.py's module scope, not off the critical path. What stays genuinely deferred
there is the vault/backend-touching `Sluice` construction, one layer further in, inside each
`cmd_evidence_*` function body. `commands.py`'s own module docstring now states that
distinction -- it previously asserted the opposite, crediting the `_build_parser` import for a
deferral that import does not provide, which invited someone to "restore" the laziness by
hoisting the per-function `Sluice` import to module scope and putting a heavy import on every
invocation. The two packages are not mutually isolated, either: `sluice/evidence/commands.py`
imports `sluice.onboard.ask` directly (the same `NoInputAsker`/`TtyAsker` classes `cli.py` itself
imports for `cmd_init`), lazily, inside `cmd_evidence_verify` -- a deliberate cross-import between
the two command packages, not a boundary violation. `sluice/evidence/wizard.py` takes its asker
INJECTED instead and imports nothing from onboard at all. `docs/ARCHITECTURE.md` has the
per-module detail; what follows is what you cannot see from the file tree.

**Config is layered and single-file.** Code defaults < the YAML file at `$SLUICE_CONFIG` (else
`<XDG config>/sluice/config.yaml`) < env vars. Each sub-app has its own `load_*_config()` reading
its own top-level block of that same file (`triage:`, `cv:`, `apply:`, `track:`); ingest reads the
root keys. Every knob has a code default, so everything runs with no config file at all. New
tunables go in the relevant `*Config` dataclass and `sluice.yaml.example` — never hardcoded in
logic. Only `load_config` names its fields explicitly; the four sub-app loaders are
`hasattr`-filtered `setattr` loops, so a new ROOT field is dead until `load_config` names it, and
the sub-app loaders must not be "fixed" into naming theirs (`load_track_config`'s merged-denylist
branch lives in that loop).

**Every RELOCATABLE path goes through `core/paths.py` (#80).** One `resolve()`, one order — env var,
then config key, then the XDG base directory for that `kind`. An explicitly-named value — env var or
config key — is taken as the caller gave it EXCEPT for a leading `~`, which is expanded: returning it
verbatim while the XDG fallback expanded made one resolver answer two ways, and `SEEN_DB=~/state/seen.db`
then loaded an EMPTY dedup set (the #81 harm) with nothing said, because naming a path short-circuits
the relocation check that would have spoken. Expanding is where it stops — `expanduser` at ingress,
`abspath` only where a value outlives the cwd it was read in, neither at consumption;
`docs/ARCHITECTURE.md` carries that rule and the one exception it looks like it has. It is not every
path in the codebase,
and the exceptions are deliberate: seven artefact paths stay cwd-relative — the five CV working
directories in `apply/config.py`, `cv/config.py` and `cv/render.py`, the render SCRIPT
(`cv/config.py`'s `render_script`, an executable rather than a directory), and `core/vault.py`'s
`DEFAULT_VAULT` — because they name a workspace the user is standing in rather than per-system
state. `grep -rn '"\./' sluice --include='*.py' | grep -v core/paths.py` pins them at nine lines
(`cv-served` and `cv-home` appear twice each). Two things follow that are easy to undo by
accident. A path's config default must be `""`: a non-empty default is always truthy, so it
short-circuits the chain, the XDG location is never reached, and nothing goes red while the feature
is inert. And precedence belongs in the FACTORY, never ahead of an explicit constructor argument —
`stores/vault.py:_make` and `HealthStore`/`SeenDb`'s `path or resolve(...)` are that shape, because
the reverse would make an env var beat the ~150 positional `Vault(str(tmp_path))` constructions in
the suite and retarget them at a developer's real vault, green in CI throughout. The vault itself
does NOT relocate (it is the user's Obsidian directory), and its two-term `or` lives in `_make`.

Nothing is auto-migrated. A path left behind warns; the two dedup stores REFUSE, because an empty
dedup set makes every already-known lead read as unseen and silently re-submits it to the write
path. `Vault.upsert` now probes `_merged/` by name before creating (#81), so a lead a human merged
away usually self-heals rather than being re-created -- but that probe is name-keyed, so a re-scrape
whose title has drifted past every name candidate still slips past it and is created afresh, and if
its twin was already `applied` that is a second application under the user's name. **That notice is
keyed on the resolved path not EXISTING, so any code that touches it — even harmlessly — disarms it
from then on.** `sqlite3.connect` creates a 0-byte file
merely by opening one, which is how a `--dry-run` silently disabled the refusal for every later real
run; a store therefore must not create anything on a read. For the same reason a store must not read
an unreadable file as empty: the relocated case and the corrupt case cause the identical harm, so
both are loud. The two are scoped DIFFERENTLY, and the difference is deliberate: `ingest`
refuses only when the run actually writes dedup state (`--dry-run` and `--sink json` proceed), while
every `track` command refuses including its dry runs, because a track dry run READS the #49
dead-letter store to report what it would do and against a relocated store would report nothing to
do. `doctor` never refuses — a relocated file is exactly what one runs it to hear about. An
explicitly named path (env var or config key) short-circuits before the check either way, so callers
who name their own paths are immune by construction. `tests/conftest.py`'s autouse fixture sandboxes
every one of these; `XDG_CONFIG_HOME` and `HOME` are consecutive rungs of one chain, so both are
load-bearing and neither substitutes for the other.

**The `leads` passes report by default; the pipeline commands write by default.** `leads dedupe`
(`--merge ID [ID ...]`), `leads expire` (`--expire [SLUG...]`) and `leads reconcile` (`--apply`, #1)
print and change nothing until told otherwise, and none offers `--dry-run` — the default IS the dry
run, and a flag that does nothing is drift. `triage run`/`ingest run`/`track run` invert both halves. The distinguishing
property is whose judgement the write encodes: a pipeline command acts on a verdict the user
configured, while a `leads` pass writes over a set the TOOL computed, so a mistyped one should print
a list rather than change a hundred notes. **Exception: `leads dismiss` writes unconditionally on
every call** (#131), like the pipeline commands, not like its `leads` siblings — the verdict it
writes is the one the user typed (`--lead`/`--reason`), not one the tool computed. (`docs/ARCHITECTURE.md`
has the per-pass mechanics.)

**Backends are selected by role, not provider.** `--backend` takes `auto|primary|fallback`
(`claude-max`/`deepseek` survive as deprecated aliases). Which provider fills each role is config
(`primary_backend`, `fallback_backend`). `auto` degrades to primary-only when the fallback has no API
key, and warns loudly; `--backend fallback` hard-errors instead, because there is nothing to degrade
to. Construction failures are raised at build time, not at first call. See `_select_backend` in
`cli.py`.

**Sources are declarative plugins.** A module in `sluice/ingest/sources/` calls `register(...)` at
import time; the package auto-imports every sibling, and one broken plugin is logged and skipped
rather than sinking the registry. Most boards are one `BrowserListSource(id, extractor_js,
searches_spec, ...)` literal — see `remoteok.py`. `Source` splits impure `fetch` (drives the browser)
from pure `parse` (raw dict -> `list[Lead]`), which is the whole reason parsers can be tested offline
against golden fixtures. Each source ships exactly one neutral example search; a user's real search
list belongs in `sources.<id>.searches` in config.

**There is ONE base class, and that is recent.** `CarouselSource` — a one-job-at-a-time
carousel advanced by clicking a next control — was retired 2026-08-28 when its only producer
(`wttj`) moved to WTTJ's list view and left it with none. Two consequences worth knowing before
adding a source. A guard in `tests/test_ingest_url_trust.py` used to assert BOTH base classes
were reachable, as the anti-vacuity check for a two-implementation seam; with one implementation
the equivalent claim is that the base class is reached AND at least one class actually
OVERRIDES `parse` — checked against `parse` itself, not against the class count, since two
registered subclasses inherit it unchanged and a count would be satisfied while every row
still tested inherited behaviour. And `core/app.py`'s doctor sweep still
gates `auth_probe_js` on the class that honours it even though only one class exists, because a
second could grow the attribute and never evaluate it — the hazard outlives the class that
illustrated it. `git log -- sluice/ingest/base.py` has the implementation if a carousel board
turns up again.

**Health classifies the SHAPE of a run, not only its count and host (#156).** A scraper's dominant
failure mode is succeeding at reading the wrong page, not crashing — a rotted selector still returns
a plausible row count from the right host. `detect_drift` (`core/health.py`) has three
content-inspecting reasons beyond the original `zero`/`drop`/`redirect`/`blocked`/`auth`/
`unreachable`: `fallback` (a row an extractor's own degraded path stamped —
`ingest/base.py`'s `_first_degraded`, checked on raw rows so it survives even a row `parse` later
drops), `login` (a landed URL path segment matching a small vocabulary the requested path did not
ask for — segment-**prefix** matching with a non-alphanumeric boundary, never exact-segment or
substring: measured false positives on both), and `blank` (a company/link completeness collapse
aggregated over EVERY search's **parsed** leads this run, not any one search's snapshot, and never
the raw payload — naukrigulf's `parse` recovers a company `raw` never had, so measuring `raw`
reports on an intermediate nobody sees). `blank` needs THREE gates,
each measured against the real fleet rather than assumed: a row floor of 8 (below it a small
carousel's rate is noise, not signal), a source's own STICKY high-water floored at 0.8 (a separate
persisted field, not derived from the 30-run rolling window — deriving it would make a permanently
rotted source fire for exactly 30 runs and then go silent forever once its one healthy run ages out
of that window), and two consecutive low runs before firing. `login` is deliberately **excluded**
from `_RECOVERABLE` despite sounding like an expired-session case: `_is_dead` short-circuits on
`count > 0`, so membership never mattered for the incident it was built for, and including it would
grant a permanently-paywalled board the same unlimited life `_explained`'s docstring warns
`_RECOVERABLE` membership grants any reason that fires benignly. `fallback`/`login`/`blank` also
join a small `BREAKER_REASONS` set in `ingest/engine.py`: a run classified as one of them has its
leads WITHHELD from the sink for that run (never written to `seen.db`, so the next run retries from
scratch) rather than merely reported — every other reason stays report-only, EACH for its own
reason rather than one blanket claim: `auth`/`unreachable`/`zero` are structurally count==0-only
(nothing to withhold); `blocked`'s one shipped producer always yields zero rows when it fires,
though the classifier itself permits a future source's `blocked` at count>0; `redirect`
structurally CAN carry a positive count and is left out anyway because withholding on it is a
separate scope decision, not a side effect of this feature; `drop` is the lowest-confidence
signal here, so a false positive there costs a late report, while suppressing a healthy day's
leads is the worse failure.

**What is MEASURED and what is CLASSIFIED on are two different rosters, and the gap between them
is deliberate (2026-08-27).** `RATE_SIGNALS` is everything `_lead_rates` computes and `record`
high-waters — `company_rate`, `link_rate`, `location_rate`. `BLANK_SIGNALS` is the strict SUBSET
`_blank_reason` classifies on, and it is company and link only. `location_rate` exists because
location was previously measured NOWHERE, which is how reed served ~20 rows a run with location on
none of them while every check stayed green — the vocabulary was company and link, and reed kept
both. It stays OUT of the classifying set because `blank` is in `BREAKER_REASONS` and withholds
every lead the source produced: right for a company collapse, not obviously right for a location
one, where a lead keeping its title, company and link is still worth having. Promote it only on its
own measurement against the fleet's healthy windows — not on the assumption that more signals
classified is strictly better.

**`blank`'s 0.8 high-water floor has a blind spot that is REPORTED, not closed.** Skipping a signal
whose high-water never cleared the floor is right for a board that genuinely does not publish a
field (weworkremotely hardcodes an empty company) and silently wrong for one ALREADY broken when its
first run was recorded — the high-water only climbs, so it never establishes a bar to fall from and
is exempt for good. Measured: reed's company high-water was 0.1, taken from a run whose extractor
was already reading the wrong elements, so the check that would have reported the collapse was
switched off for the one source that needed it. Every source is in this state after the health file
is first created or lost. Do NOT "fix" this by lowering the floor or adding an absolute one: `blank`
bins a source's whole run, so a board that legitimately lacks a field would be binned daily, and
nothing local can tell that case from a stopped selector. `HealthStore.unguarded_signals` names the
exemption and `ingest list-sources --health` prints it — rates per source plus
`UNGUARDED(<signal>)` BY NAME, for ENABLED sources only (nothing runs for a disabled one, so
no guard can be blind). A human rules on which case a source is and records the benign ruling
in the source's own `unpublished_fields`, which silences the flag for the named field only —
without it the two boards that hardcode an empty company light it for ever, and a permanently
lit flag on benign rows is how a reader learns to skip the column. A source whose NEWEST run
recorded no rate prints `UNMEASURED` instead: below `_RATE_ROW_FLOOR` there is no rate for this
run, so `blank` cannot fire at all. The gate is the AGE (`age != 0`), not whether some rate
exists — a merely STALE rate is not coverage either, and `unguarded_signals` is consulted only
at `age == 0`. `latest_rates` returns how many runs
back the rates came from and the CLI prints that age — an undated rate can be 30 runs old, and
a stale 100% is exactly the reassuring answer a rotted extractor gives the command run to
catch it.

**A retirement is a claim about the world, and `reprobed` is where its date lives (#207 ask 4).**
Every DISABLED source declares the ISO date its retirement was last checked against the live
world, as a field on the source contract -- not as prose in the module docstring. That is a
measured choice: mining the date out of the docstring means deciding from PROSE whether a line
asserts a check HAPPENED, and every tightening of that acquired a hole -- a tuple comparison
ranked the impossible `2026-99-99` above the floor, a marker-word requirement admitted
`unverified` (it contains `verified`), and word-bounding the markers still admitted `not
verified` / `never confirmed` / `no longer verified` / `yet to be re-probed`. The set is
unbounded because it is a natural-language question; a declared date cannot be negated. The
docstring still carries the REASON — the part a human reads, which no field replaces. Malformed
values raise at construction (`validate_reprobed`); whether a disabled source must carry one,
and whether it is recent enough, is policy and lives in `tests/test_drifted_boards.py`.

**`cli.py` imports the heavy modules inside command functions, not at module scope.** That is
deliberate: it keeps offline commands (and their tests) from ever touching Camofox, the vault, or a
backend. Keep new commands lazy the same way.

## Invariants

These are load-bearing, enforced by tests, and easy to break by accident. They are also the hard
rules the review agents enforce — see `.rulesync/skills/review-pr/SKILL.md`.

**Never-clobber (writes).** A re-scrape of an existing lead touches only its `last_seen` marker —
never status, never enrichment, never the note body. Creating a note for a genuinely new lead is the
only wholesale write. Rewriting notes wholesale is the exact fragility sluice exists to remove. Every
*modify*-write (status, scores, enrichment, the CV pointer) goes through the surgical compare-and-set
path in `core/vault.py`: the edit is re-derived from the *fresh* note on each attempt and committed via
a temp-file + `os.replace` (torn-file safety), so a concurrent writer's other keys and body survive; a
sustained race raises `VaultConflict` (`core/protocols.py`, a Store-contract outcome) rather than
clobbering, and callers treat that as a non-fatal outcome. It is best-effort under a residual
compare→replace micro-window, not a lock — the primary threat is a human editing the note in Obsidian,
who takes no lock (#16). `update_fields` also accepts `require_status` (#9): a frozen set the
transform re-reads the *fresh* status against, abstaining (returning `False`, writing nothing) on a
mismatch. That check CANNOT be hoisted into the caller — probed against a real vault, a guard on the
enumerated `LeadNote` is byte-identical to no guard at all, because the snapshot is stale by
construction. It is a parameter on the existing writer rather than a second write function, because
CodeQL flags a new write function as a new sink.

`job-sluice leads reconcile` (#1) is the one pass that MOVES a note, and a move writes no note bytes —
only a directory entry, via the `O_EXCL`-reserve + `os.replace` primitive `merge_cluster` shares. It
never read-modify-writes a status, so never-regress is untouched. That is NOT the same as
never-clobber holding "by construction", and the difference is measured: a move landing between
`_cas_write`'s freshness re-read and `_atomic_write`'s `os.replace(tmp, path)` RE-CREATES the source
path, leaving two notes at one basename and a lead `upsert` then refuses permanently. No portable
stdlib atomic-conditional-rename exists, so it is the same accepted residual as `_cas_write`'s own
micro-window — documented, warned about in the command's help, and REPORTED by the sweep that caused
it rather than left for a later ingest to surface as an unexplained refusal. That report is a single
post-sweep SNAPSHOT and so is best-effort: a race landing after it is missed and surfaces on the next
run instead. It turns the common case from silent into named, which is the whole claim.

A move must also never follow a SYMLINK. `_walk` keeps `os.walk`'s `followlinks=False`, so a
symlinked `Active/` is outside the scan set: a note filed there leaves `read_leads` AND `_locate`,
every later scrape refuses, and the lead is invisible for good — measured at exit 0 with zero log
records emitted. Both the reconcile destination and the create-arm write folder refuse a symlink
rather than writing into one.

**Non-resurrection (#81), in the never-clobber family.** A lead a human merged away via `sluice
leads dedupe --merge` must not be silently re-created by a later re-scrape — a wrong create undoes a
human's decision and, if its surviving twin was already `applied`, means a second application under
the user's name. `merge_cluster` archives each loser under `leads_dir/_merged/` and stamps the note
name it was seated at into it (`archived_from_note`); `Vault._resolve_path` probes that archive
before returning `create`, via the ONE verdict `_reconcile` (shared with the active walk, so the two
cannot drift), and `_archived_match` maps the result. `upsert`'s return vocabulary is therefore
SIX-member — `created`/`updated`/`merged`/`refused`, plus `merged_away` and `merged_away_unproven`.
Both archive outcomes write NOTHING: not the note, not `leads_dir`, not the Syncthing marker. They
differ only in evidence, and that difference is the load-bearing part: `merged_away` requires a
url-PROVEN match (both urls non-empty and equal) and is the only one of the TWO the ingest sink
records in `seen.db`: it joins the allowlist, which reads
`created`/`updated`/`merged`/`merged_away`. Every weaker match — a location-token overlap, or an
inconclusive comparison — is `merged_away_unproven` and must NEVER be recorded, because `seen.db`
has no removal path and a same-company/title/location RE-POST carrying a brand-new url is a real job
that would otherwise be suppressed forever with no note anywhere to reverse it. That arm therefore
re-reports on every run until a human acts, and there is exactly ONE action — the same hand-move
`docs/ARCHITECTURE.md` documents as the recovery path: move the archived note back out of
`_merged/`. It returns to the active view, the next scrape reconciles against it as an ordinary
note, and the outcome becomes `updated` (a location-only SAME) or `merged` (an inconclusive
comparison) -- measured on BOTH arms. Either is on the allowlist, so the count stops. The Store
contract states the obligation as bounded, not absolute: a merged-away loser must remain
discoverable through the identity the store RECORDED at merge time, and a re-scrape whose identity
has drifted past that (for the vault, past every name candidate) is outside the guarantee and is
created — a visible duplicate, the direction to fail in. That recorded name is compared up to CASE
(#205), and the fold was a LIVE BREACH rather than a tidy-up: measured before it, merging a lead
away and re-scraping it as `EXAMPLE CO` rather than `Example Co` returned `created` while the
exact-casing control suppressed correctly — the guard worked and the re-scrape walked past it.
Folding can only suppress MORE, never resurrect more, and it does not widen `seen.db`, since that
arm stays gated on `url_proven`, which no name folding can manufacture. The fold has ONE home,
`core/vault.py`'s `_fold_note_name`, shared with `_locate` and `read_leads`' collision report: a
`_locate` that folds against an `_archived_match` that does not is measurably a resurrection, so
these are not three independent `.casefold()` calls. It is CASE only — Unicode normalization is a
separate axis, and every widening past case claims two differently spelled names are one job.
`_merged/` is load-bearing retention, not
scratch: do not prune it. The lead scan is recursive (#1), so `_merged/` is excluded from it BY NAME
(`_PRIVATE_SUBDIRS`, at the TOP LEVEL only) rather than by the accident that a flat `os.listdir` never descended into
it -- deleting that prune resurfaces every archived loser and undoes this invariant outright.
See `core/protocols.py`, `docs/ARCHITECTURE.md`, and
`tests/conformance/test_store_contract.py::test_merged_away_lead_is_never_recreated`.

**A lead's identity is its note name UP TO CASE (#205), and the fold has one home.** Boards render
one employer several ways and the name is built from the company string verbatim, so a byte-for-byte
match seated a note per spelling, each with its own status — one holding a live `shortlist` at score
86 while its twin held a `dismiss`, so dismissing the role under one spelling did not stop it
returning as `new` under the other. It also wedged replication silently: a case-insensitive
filesystem cannot hold the pair and Syncthing reports the folder `state=idle` while delivering
neither note. Do NOT reach for a title-caser here — the issue's own suggestion, and measured before
it was rejected. An acronym-safe one (leave a token that is all-caps or has an internal capital
untouched, minor words lowercase) converges only the all-lowercase↔mixed-case shape: it leaves an
ALL-CAPS spelling apart from its mixed-case twin, which is the shortlist-vs-dismiss pair that did
the damage, and leaves a CamelCase brand apart from its all-caps spelling. And on lowercase input
it turns `ai` into `Ai` — the corruption the acronym rule exists to prevent, since the rule can only
preserve an acronym that arrives already capitalised. Casing NORMALIZATION cannot fix a dedup
problem; case-insensitive RESOLUTION does. `_locate` probes the exact name FIRST and folds only on a miss,
which is what keeps the cost where it was (~7µs steady-state hit against ~2ms for the folded
listing over a 3000-note benchmark store) — do not "simplify" that into an unconditional fold. Its
consequence is stated rather than closed: against a pair a pre-#205 store already holds, a scrape
matching either spelling updates that one silently and only a THIRD casing reaches the ambiguous
refusal, so the standing signal is `read_leads`' own warning, which names `leads dedupe --merge` —
already a working remedy, since `cluster_duplicates` normalizes through `_norm_tokens`, which
casefolds. No note is ever RENAMED by this: renaming orphans `track_deadletter.lead`, which holds
the note stem.

**Never-regress (status).** One `status` frontmatter key, two lifecycles with separate owners
(`core/status.py`). Triage owns `new/shortlist/research/needs_review/dismiss/unjudgeable` (the last,
#169, stamped in place of a judge verdict when a dossier's job description never arrived) and may
rewrite them; track owns `applied/phone_screen/.../rejected` and triage must never touch a lead that
has entered that lifecycle. Status only moves forward on the ladder; terminals are never advanced out
of.
`shortlist -> applied` is the only transition apply may make on send; track makes the same
transition when a confirmation receipt arrives (`track/receipt.py`, #10) — both route
through the one `can_apply` predicate (`can_transition` dispatches a `--to applied` request to it,
since `track confirm` accepts an arbitrary target), so apply-on-send and track-on-receipt are the
sole crossings into the application lifecycle; every later move is an on-ladder `can_advance` step.
A receipt auto-advances only under the FULL guard set — a `proof`-tier match (the sender host
matches one of the lead's known hosts — `applied_url` then `url`, #136 — on a message whose
`Authentication-Results` records a PASS aligned with that sender, and neither host multi-tenant: no
ATS relay, no job board sluice scrapes), the lead present in `receipt_by_slug` (the combined
shortlist ∪ in-flight index `track/engine.py` matches receipts against, so a lead already at
`applied` or later can be domain-matched too — `can_apply` below is what actually restricts the
WRITE to `shortlist`), `can_apply`, and `confidence >= auto_apply_min`. Every weaker outcome
proposes to the dead-letter for a human, because a wrong `applied` silently suppresses a real
application and is irreversible. A domain match for a lead ALREADY past `shortlist` cannot write
(`can_apply` refuses), so it stamps the evidence onto the lead's own note instead of proposing —
see `docs/ARCHITECTURE.md`'s track paragraph for the reasoning.
An unrecognized status is passed through untouched rather than silently rewritten.
`job-sluice leads expire` (#9) writes `dismiss` — triage-owned, so never-regress permits it — and never
a `_TERMINAL`, since every terminal is application-owned. It reads only
`TRIAGE_OWNED - {"dismiss"}` (derived, never hand-listed, so the set cannot name an
application-owned state) and passes that same set as `require_status`, which is what actually holds
the invariant when a lead enters the application lifecycle mid-sweep. It is NOT unconditional: a
lead holding a `pending_cv` sign-off hold (#60) is refused, because dismissing it silently discards
a composed CV no human has signed off. Any second bulk-dismiss path must refuse the same.
`Sluice.dismiss_lead()` (#131) is that second writer -- a single-lead dismiss, not a
bulk sweep, so `expire_report`'s pre-filtering argument does not apply to it; it uses
its own `_DISMISSABLE_FROM` (the full `TRIAGE_OWNED` set, `dismiss` included) rather
than `_EXPIRABLE`, and its `pending_cv` sign-off-hold refusal is checked CAS-fresh
inside the write transform via `require_blank` -- unlike `leads expire`'s equivalent
refusal, which is still decided from a snapshot.

**Empty config means abstain, not match-nothing.** Every preference gate (`accept_titles`,
`target_locations`, `reject_companies`, `relevance_keep`/`relevance_drop`, pay floors) defaults to
empty/zero, and an unconfigured gate passes every lead through. Getting this backwards silently bins
someone's entire job hunt — it has happened once already (`672ad2a`), and
`tests/test_sluice_neutral_defaults.py` now fails the build if it recurs. `lead_ttl_days` (#9) is
the same shape at the root config: `0` means staleness is OFF, so an unconfigured install expires
nothing and refuses nothing. Its validator rejects `bool` *before* checking `int`, because `bool`
subclasses `int` and PyYAML resolves `yes`/`on`/`true` to `True` — so `lead_ttl_days: yes`, the
natural thing to type to turn the feature ON, would otherwise load as a one-day TTL and mark every
lead stale with no error anywhere. The list-keyed neutral-defaults sweep does NOT cover int fields
and must not be widened to: `0 == abstain` is not universal (the dossier-cache `ttl_days: int = 7`
is a legitimate non-zero default), so this knob carries its own named guard.

`lead_layout` (#1) is the THIRD root knob with that property: `""` is flat, so an unconfigured
install files notes exactly where the pre-#1 store did, and it is what keeps the whole layout
feature inert until someone opts in. Its failure mode is a NAME rather than a value — a plain
membership check against `LEAD_LAYOUTS`, with none of `lead_ttl_days`' bool-subclasses-int hazard —
so it raises and lists the valid ones at BOTH `load_config` (a YAML typo is a usage error, not a
traceback) and `Vault.__init__` (which is what covers the ~150 direct `Vault(...)` constructions a
loader-only check would miss). It carries its own named guard too, for the mirror-image reason:
the sweep is keyed on LIST defaults, so a `str` field is invisible to it.

`min_jd_chars` (#169) is the FOURTH: `0` (the shipped default) means the near-empty band is off, so
only a wholly EMPTY fetched JD is ever treated as not having arrived -- a character count above that
is a judgement about what counts as a real posting, which this repo does not ship uninvited. Its
validator follows `lead_ttl_days`' exact shape (`bool` checked first, since it subclasses `int`), for
the identical reason: `min_jd_chars: yes` is the natural spelling to turn it on, and would otherwise
load as a one-character floor, letting nearly every fetched JD through with no error anywhere. It is
shared, not per sub-app: `Sluice.dossier_cache()` reads `self.config.min_jd_chars` for both
`triage()` and `compose_cv()`'s cache, since the two already share one dossier cache directory (#80)
and must agree on the floor.

**The CV fabrication gate is hard.** `cv/validate.py` is pure and deterministic: every WORK bullet
must cite a real bundle `[id]` and every number in a bullet must appear in a cited entry; the PROFILE
prose (which has no per-bullet citations) has a source-set-wide numeric floor — a figure present
nowhere in the source set (the baseline plus every entry, never the NEGATIVE CONSTRAINTS the bundle
also carries, and never the SKILLS INVENTORY framing section #165 added — `bundle_sources` walks
`bundle["entries"]` alone, so a skills figure is licensed in neither pool, and `compose.py`'s rules
tell the model so) is a violation, citations stripped with render's exact `_CITE_RE` — and — enforced
beside it in `cv/engine.py`, since `validate` returns `[]` rather than complaining — a composed
CV missing the exact `WORK EXPERIENCE`/`PROFILE` headers fails closed, since the section-keyed
checks would otherwise silently not run. That verdict, together with `cv/engine.py`'s own inline STRUCTURAL
guards beside it (the header checks just named, plus the three name/contact-block anchors described
below), `cv/slop.py`'s unscoped HARD tier (an em dash or a literal `--`), and the renderer's own
optional `precheck`, form the HARD gate: a non-empty finding list blocks rendering, and a lead with no
attempt that ever cleared it is skipped — a CV is never rendered ungated. The gate is HANDED its
source set rather than recovering it: `validate`'s second parameter is `cv/bundle.py`'s
`BundleSources`, built by `bundle_sources(bundle)` from `build_bundle`'s own structured entries,
never by re-parsing the rendered bundle TEXT (#174) — so no line of user free text can mint or
rebind a citable `[id]`. That closed three live holes: a later body line shaped like an EARLIER
real code used to REBIND that entry's allowlist, so a fabricated figure passed while the entry's
own genuine metric was reported invented; an `[XX9]`-shaped line anywhere in the BASELINE minted a
fully citable entry of its own; and, at zero entries, the NEGATIVE CONSTRAINTS block fell through
into the PROFILE pool so a do-not-say figure was profile-permitted. That closure has a price: `cv/bundle.py`'s
`_entry_block` now feeds BOTH the rendered prompt and the gate's allowlist, so a change to how an
entry is presented to the model is also a change to what the gate permits — deliberate, since it is
what removes the three holes above, but the two can no longer be varied independently. It also
re-admits two narrow PROFILE-pool widenings the old positional parse excluded as a side effect of
its own bugs: a `=== 2020 Highlights ===`-shaped BASELINE line now permits its digits in PROFILE
prose, and an id-shaped baseline line's own digit (the `9` of a stray `[ZZ9]`) does too — see
`docs/ARCHITECTURE.md` for the mechanism. The SCOPED STYLE tier
(#167) has TWO halves and neither blocks: `cv/slop.py`'s ~40 AI-tell stems and the opt-in
model-judged `cv/voice.py` check (`cv.voice_check`). The scoping is a property of the TIER, so it
covers both — `cv/engine.py` matches the stems against, and shows the model, exactly the
PROFILE-prose/WORK-bullet lines — two of the THREE regions `cv/validate.py`'s own `section_spans`
yields (#168's Task 3 added a SKILLS region alongside them, deliberately excluded here) — never the
whole document, because a complaint naming an employer, certificate or education line is answerable
only by renaming the thing it names. A surviving finding from either ALSO drives the
retry: the engine retries composition exactly once when the HARD gate fails OR a STYLE/VOICE finding
survives, feeding every finding back, and RETAINS the last HARD-clean draft across that retry so a
worse or failed second attempt can never bin a lead the first one already cleared — a phrase may never
cost a lead. At shipped defaults (`cv.slop_allow` empty, `cv.style_hold` off) the retry still fires on
a phrase hit, so a hard-clean draft using one of the ~40 stems in prose costs a second compose call;
`compose.py`'s own prompt bans the identical list (rendered from `cv/slop.py`'s `_PHRASES`, so the two
cannot drift), which is what keeps that cost the exception rather than the rule. See
`docs/ARCHITECTURE.md` for the full two-tier mechanics. Above the hard gate sits a softer,
human-facing layer (#60, on by default via `cv.require_signoff`): an advisory LLM audit
(`cv/audit.py`) catches the qualitative fabrication the deterministic gate cannot, and an
`unsupported` flag WITHHOLDS the send-ready `tailored_cv` pointer (via
`Store.sign_off`/`hold_for_signoff`, cleared by `job-sluice cv signoff`) rather than blocking
rendering. The hold is recorded in two frontmatter keys, `pending_cv` and `needs_signoff`; the
note's `status` stays `shortlist`, so never-regress is untouched. `needs-signoff` is the
`CvResult` RUN-REPORT label for that outcome, never a `status`-key value — `docs/ARCHITECTURE.md`
states the same distinction, and this file used to contradict it. `cv.style_hold` (#167, off by default) gives a surviving STYLE/VOICE finding the SAME
consequence, deliberately a SEPARATE key from `cv.require_signoff` — that flag's True default was
chosen for FABRICATION, and riding it would withhold `tailored_cv` on any of ~40 stems out of the box
on an unconfigured install. Neither signoff flag touches the pure hard gate.

**Citability has ONE writer: `Store.verify_evidence` (#164).** The `verified:` frontmatter key is
what makes an evidence entry citable by the gate above, and `verify_evidence` is the only thing in
`sluice/` that ever writes it. Sluice's own WRITE PATHS are arranged so no other route through THEM
exists, rather than so no other route is taken. `propose_evidence` always lands under `_inbox/`,
which `read_evidence` cannot see, and its `fields` parameter cannot carry the key because
`_render_evidence_note` REJECTS an undeclared field key by name — the round-trip check beside it
cannot, since `{'verified': ...}` round-trips equal to itself. (Do not restate that as "the
signature has no parameter that could carry it": `fields` is exactly such a parameter, and a
second store could satisfy that sentence to the letter while passing the mapping straight into an
INSERT. `core/protocols.py` states the obligation, which is the form a second store is written
against.) `EvidenceKind.fields` is the user-facing set only, and `cli.py` derives `add`'s
flags from that tuple, so listing `verified` there would generate a `--verified` flag — exactly
what an agent shelling out to the CLI would reach for; and `verify` carries no `--all` and no
`--yes`, because a bulk flag is the same
hole one level up. The MCP server exposes `list_evidence` and nothing that proposes or verifies, at
any `--write` level. `verify_evidence` itself is compare-and-set against the exact bytes a human
was shown, so an edit made after approval abstains rather than becoming citable — the same
discipline `update_fields`' `require_status` uses, and reachable in practice, since the human sits
at a prompt while their editor is free to save. Two things follow. The `verified:` key is
STORE-MANAGED, so a new evidence field must never be one a caller supplies; and a second promotion
path — a bulk verifier, an MCP write tool, a `--yes` — is not a convenience but a new trust root,
and would need the whole set of refusals above rebuilt around it. `EvidenceKind` carries TWO flags since #165, because
the questions stopped having one answer: `read_by_composer` says the corpus reaches the composer's
prompt, `cited_by_gate` says the fabrication gate may LICENSE its content. `experience` is both,
`skills` is the first only (shown as framing, licensed by nothing), `stories` is neither, and
`__post_init__` refuses `cited_by_gate` without `read_by_composer` since the gate cannot license
what the composer never emitted. Every user-facing message that says what `verify` buys is keyed on
`cited_by_gate` rather than asserting citability for all three -- keying it on the wrong flag
re-creates the over-claim the flag exists to prevent.

**Where that boundary STOPS, stated rather than implied: a human editing their own vault.** The
vault is the user's Obsidian directory and hand-editing it is a first-class workflow here, so a
note hand-placed in an evidence kind's own directory carrying `verified:` IS citable — measured:
`read_evidence("experience")` returns it under the default `verified_only=True`, and nothing in
`sluice/` inspects a file it never wrote. `_refuse_citation_shaped_body` does not reach it either,
since that runs on the two WRITE paths (`_render_evidence_note` and `verify_evidence`), which is
why it is a NARROWING and #174 — `validate()`'s signature change — is the close on the gate side.
That is the same posture the rest of the store takes (never-clobber protects the user's edits, it
does not police them). What must not be claimed is that the single-writer property makes the
citable set unreachable by any other means: it makes it unreachable THROUGH SLUICE. The symlink
refusals in `Vault._evidence_dir` and `_evidence_entry_path` are the same boundary drawn on the
other axis — a store may refuse to reach OUTSIDE the vault the user named, and does, on every
directory component below it and on the entry file itself; what is inside that vault is the
user's.

**The gate is blind to the name/contact block, and that block renders as the PDF's headline (#99).**
`cv/validate.py` never inspects anything before `PROFILE`; `cv/parse.py`'s grammar takes the LAST
non-blank line before `PROFILE` as the name and everything before it as contact, with zero shape check
on either. Measured on the real production path: a composer's routine one-sentence preamble ahead of
the CV proper desyncs that assignment silently -- a LinkedIn-URL contact line became the parsed name,
the real name landed in contact, `validate()` reported zero violations, and the CV would have rendered.
`cv/engine.py`'s retry loop closes this with three inline STRUCTURAL guards, in the same shape as the
`WORK EXPERIENCE`/`PROFILE` header checks beside them: the header block's LINE COUNT must match
`contact_block(profile)`'s lines plus one (the name), its LAST line must case-fold-match
`full_name(profile)`, and the lines BEFORE that last one must equal `contact_block(profile)`'s own
non-empty lines verbatim. The third guard was added on CodeRabbit's review of the first two (PR #100):
a same-count preamble occupying exactly the contact slot, with the name still correctly anchored,
passed both of the first two checks while silently dropping the real contact information. It runs LAST,
after the name-anchor check, because a same-count REORDERING also fails the content comparison and the
anchor check's message is the more specific diagnosis for that shape. All three compare against
`cv_name`/`cv_contact` (#133/#107: `cv/engine.py` derives both once per lead, before any spend, as
`full_name(profile)`/`contact_block(profile)` off the vault's Candidate Profile note --
`core/candidate.py`) -- ground truth `cv/parse.py` never has, since it is pure and takes only `text` --
and all three live in the engine rather than reaching them through `cv.parse` or a renderer's
`precheck`, for the same reason as each other: `precheck` only reaches the `template` renderer (`script`
implements none at all, `test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar`),
while these guards must bind every renderer alike, because the shape they enforce is what `compose.py`'s
own prompt REQUESTED, not what any one renderer's LAYOUT needs. **The engine may guard what the prompt
required; only a renderer may guard what its own layout needs.** A composed CV whose derived
`full_name(profile)`/`contact_block(profile)` are blank needs no preamble at all to fail the same way --
it is the model complying exactly with what the vault declared, and no STRUCTURAL check can distinguish
a blank derived name from a genuine one. There is no placeholder sentinel this GATE compares
against any more: identity moved out of `cv.name`/`cv.contact` config keys and into the vault
note (#133/#107), so a derived value that is blank just IS blank -- "" cannot collide with a real
name the way the old `"Your Name"` default theoretically could. (The literal string no longer
ships at all: `cv/compose.py`'s `build_prompt`/`compose` made `name` a required KEYWORD-ONLY
argument with no default, closing even the unreachable path rather than leaving an inert
placeholder behind it -- the one production caller, `cv/engine.py`'s `run_one`, already passed
`name=cv_name` explicitly, so nothing there changed; `compose.py`'s own unit tests now pass a
fixture identity instead of relying on a shipped default. `contact` keeps its `""` default,
deliberately: an empty contact block is the neutral, already-abstain-shaped value this codebase
uses throughout, not a placeholder that could misrepresent anyone.) `cv/engine.py` therefore also refuses to compose at all
while either derived value is blank, before any dossier fetch or LLM spend (`skipped-config`), mirroring
the `#9` staleness guard beside it -- the same "quiet wrong default" posture this codebase takes
elsewhere (e.g. `cv/config.py`'s `load_cv_config`, which raises loudly rather than silently drop a
legacy `cv.name`/`cv.contact` left in a config file), applied here to the single most visible line of an
artefact sent under the user's identity.

**`compose.py` recovers the artefact from an agentic backend's conversational envelope (#28).**
`claude --print` is Claude Code, an agent, not a completion endpoint: given a "compose X" prompt it
may write a file and summarise, ask clarifying questions, hedge about missing tools, comply with a
drifted output format, or -- the shape that survived #91's argv fix and #99/#100's header guards --
comply with everything except "no preamble, acknowledgement, commentary, separator, or closing
remark", wrapping an otherwise gate-clean CV in a short conversational aside on one or both sides,
delimited by a markdown-style `---` line. `slop.py`'s unqualified `DOUBLE-HYPHEN-DASH` rule correctly
rejects a bare `---` either way -- the fix is not to weaken that gate but to recover the real CV
before it reaches the gate at all. `compose()`'s `_unwrap_agent_envelope` strips a short aside
(fewer than four non-blank lines) from before the first such fence and/or after the last one, but
ONLY when none of those lines is one of the CV's own section headers OR shaped like one of its
entries (a bullet, or a pipe-separated `dates | LOCATION | Role` meta line); a fence anywhere else, or
one with genuine CV content on either side, is left untouched, because guessing wrong there would
silently discard a real section rather than merely fail a gate that retries. It closes the specific
gap the original fix candidate for `#28` (a two-fence-only unwrap) left open: a model complying with
"no preamble" still appends a closing remark behind a SINGLE fence, which a two-fence-only unwrap
cannot see. The entry-shape check itself closes two content-loss findings from this fix's own
`/review-pr` round, both confirmed by execution rather than argued from the code: a genuine final
WORK EXPERIENCE entry (company line, meta line, one cited bullet) is exactly as short and as
header-free as a real conversational aside, and so is a section's own body when a fence lands right
after that section's header rather than before it, since the header is then on the wrong side of the
fence for the header check alone to see. Checking for the entry's own shape catches both without
needing to remember what already appeared on the other side of a fence. The one gap still accepted
is a fence positioned between the name and `PROFILE` -- never observed on the real production path,
since every captured case wraps the WHOLE CV rather than a sub-slice of its own header, and a bare
name line has neither a section header nor an entry shape to be caught by -- which this function may
still misread as a genuine leading aside and strip along with the real name; that degrades safely
rather than silently, because the resulting headerless CV still trips the `#99` STRUCTURAL count
guard immediately above and forces the ordinary retry rather than shipping nameless.

**A renderer's `precheck` must never be STRICTER than that gate** -- with two narrowly-scoped,
individually-justified exceptions, both stated below with the test that licenses them ("the refusal
must be answerable WITHOUT inventing content"); read to the end of this section before concluding a
given refusal violates the rule. `cv/engine.py`'s retry loop also
calls the Renderer seam's optional `precheck(cv_text) -> list[str]` (`core/protocols.py`) and folds
its strings in with the gate's violations, so a renderer's own grammar reaches the model's one retry
rather than arriving after the LLM spend. That makes it the one place a formatting rule can bin a
lead the gate certified clean: gate passes → precheck refuses → compose, gate green, retry, fail,
lead binned. Instances of it shipped on the `template` renderer's parser REPEATEDLY, each found by
someone happening to think of a case and adding a row: the en dash, the em dash, the word `to`, the
terminal token's casing and spelling, a single-digit month, a case-drifted section header, a blank
line under a trailing header, an en-dash CERTIFICATES marker, a LOCATION field nothing upstream can
supply — and, on a second review round, that same LOCATION field spelled as a BLANK MIDDLE pipe
(`dates |  | Role`), which was refused while the two-field spelling of the identical fact was
accepted. Deliberately no total — the count is not derivable from anything executable, two files
carried different numbers, and this repo has already been bitten by a stale count in prose twice
(`core/paths.py`'s ingress sites). The LOCATION one is the worst shape: the only actionable reading
of "add the missing field" is *invent a city*, so a parser refusal became fabrication pressure aimed
at the feature that exists to prevent fabrication. Widen the parser, never `cv/validate.py`.

`tests/test_cv_parse.py`'s implication sweep is the standing check, and **its coverage is narrower
than the rule** — read this before concluding a case is already swept. It asserts
`validate(cv, sources) == [] ⇒ parse_cv(cv) does not raise` with the antecedent COMPUTED from the
real gate per row, over one alphabet: separator × terminal token × start-month width, applied to the
FIRST role's date range in one fixture. So it covers `parts[0]` of one meta line and nothing else.
Known un-swept axis, measured: a FOUR-field meta line
(`02/2023–present | Example Location A | Staff Engineer | Platform`) is gate-CLEAN and
refused. That one is left refusing on purpose — four fields is genuinely malformed and the message
names the expected shape, so the retry can act on it — but it is a gap in the sweep, not a case the
sweep passed.

The name/contact misassignment (#99, above) is a THIRD gate-clean, un-swept case, and it is
deliberately NOT a third exception to the implication: `parse_cv` still does not raise on it, on
purpose, because the parser has no ground truth to refuse against and tightening it would only
protect the `template` renderer for zero added coverage on the path that actually ships. Protection
lives at `cv/engine.py` instead, comparing the same header block against the vault-derived
`cv_name`/`cv_contact` (`core/candidate.py`) -- `cvcfg` carries neither field as of #133/#107.
`test_a_preamble_line_is_gate_clean_and_parsed_without_refusal_on_purpose`
(`tests/test_cv_parse.py`) pins this as intentional, the same way the two exceptions below are
pinned — read it before "fixing" this by widening the parser after all.

A REPEATED trailing header (`CERTIFICATES` … `EDUCATION` … `CERTIFICATES` again) is the second
deliberate refusal of gate-clean input, and it was added rather than inherited: measured, it was
gate-clean AND slop-clean AND parsed without raising, returning `certificates == []` with the second
block's entries gone — and since the template guards each section with `{% if document.certificates
%}`, the heading vanished with them, so the PDF was indistinguishable from a candidate who holds
none. `SKILLS` joined `CERTIFICATES`/`EDUCATION` as a third trailing section at #168's Task 7, and
the same refusal covers a repeated `SKILLS` header identically — the message names whichever
sections are actually live by deriving them from `_TRAILING_SECTIONS` rather than hand-listing a
pair that would go stale the moment a third joined it. Both exceptions pass the same test, and it is
the test to apply to any third: the refusal must
be answerable WITHOUT inventing content (here, merge the two headings). That is exactly what the
LOCATION refusal failed, and why that one went the other way. The repeat is refused even when it
turns out to be empty and so drops nothing; that over-refusal is stated in `cv/parse.py` rather than
disguised as a distinction the code draws.

The one place a parser may legitimately be stricter is a WORK bullet marker, and there the
requirement is EQUALITY with the gate, not merely "no wider": a marker `validate.py` does not also
citation-check would render an UNCITED bullet into the PDF ungated, while one it checks and the
parser rejects is the governing bug class again. `_TRAILING_MARKERS` (CERTIFICATES/EDUCATION/SKILLS,
none of which the gate ever citation-checks, so that half of the bypass argument still has no force
there) is a separate, wider tuple from `_BULLET_MARKERS` for exactly that reason — never widen the
shared one. SKILLS is not fully exempt, though: it is the FIRST trailing section the hard gate DOES
check, via containment rather than citation (`UNSOURCED SKILL`, #168), so a marker this tuple
accepts that `validate.py`'s `_SKILLS_MARKERS` does not recognise as a bullet would let that line
slip past `skills_lines` entirely and reach the PDF uncontained — a bypass of the NEW check even
though the citation argument still has no force. What that argument establishes is a FLOOR and not an
equality, and the distinction is load-bearing in one direction only: `_SKILLS_MARKERS` must never be
NARROWER than `_TRAILING_MARKERS`, while staying WIDER costs nothing, because the gate is
renderer-independent and `_TRAILING_MARKERS` is the `template` renderer's own grammar — pinning the
gate to it by equality would let a later narrowing on the parser side narrow the gate too, for every
renderer, with the guard still green. The two are pre-shaped equal today, which is their shape rather
than their obligation, and the containment check strips with the identical character set before
comparing — so never narrow either alone. `tests/test_cv_parse.py::test_the_work_bullet_markers_are_
exactly_what_the_gate_citation_checks` is the one guard over both relations: EQUALITY on the WORK
pair, the floor on the SKILLS pair. A second row asserting SKILLS equality shipped beside it on the
#168 branch, contradicting that same file's own reasoning, and was removed rather than softened into
a duplicate.

**Neutrality: no personal data in this repo.** No employer names, role preferences, locations,
contact details, hostnames, or absolute paths in `sluice/` or `tests/`. The judge's criteria are read
at runtime from the user's vault (`Job Applications/Judging Profile.md`), never from source. Tests
generate synthetic job titles with seeded `faker` (`tests/conftest.py`) rather than hardcoding
anyone's taste. Personal values reach the code only through `sluice.local.yaml` and the vault.

The GOLDEN FIXTURE CORPUS (`tests/fixtures/*/raw.json`) is bound by that rule too, and used not to
be swept by anything: every collector in `tests/test_fixture_name_neutrality.py` reads
`tests/**/*.py`, so a corpus of CAPTURED board payloads carried real employer names and a real
hunt geography through the guard written to catch exactly them (#27) -- in `company`, and also in
`title` and in URL slugs, so enumerate every key rather than the one you would think to check. A
pre-release scrub had already replaced MOST of the company names with a fictional roster, which is
what made the corpus read as reviewed -- **a PARTIAL scrub is indistinguishable from a complete one, and is how this recurred.**
Since #27 the corpus is scrubbed and ratcheted at the bottom of that same file: value rosters for
the enumerable keys (`location`, `company`), asserted in BOTH directions, plus a per-source DIGEST
over the CANONICAL PARSED JSON of the whole payload -- sorted keys, so it is blind to formatting and
key order, but sees record count, key names, numbers, booleans and REPETITION. Not a set of the
distinct strings: that loses multiplicity, and one row moving between two values already present in
the same fixture then leaves the digest byte-identical with both rosters green (measured). The digest
exists because `title` is free text the boards append the posting's location to, and no roster can
enumerate it. Two traps to not walk back into: a gazetteer of real
place names would be both the classifier that file's docstring argues against AND a leak in its own
right (writing the removed values into `tests/` to forbid them puts them back in the public tree),
and a scrub must preserve TOKEN STRUCTURE -- `core/leads.py`'s `_norm_location` reduces a value to a
token SET, so only a substitution that is one-to-one and collision-free ACROSS THE LOCATION
TOKENS leaves
`docs/superpowers/specs/2026-07-16-location-identity-evidence.py`'s derivation intact (it is the
check: every count must be unchanged).

**What #27 is and is not about.** It is the captured SET -- a corpus of scraped payloads whose
locations, taken together, read as one person's hunt geography. It is NOT a rule that a city name
is sensitive. A single ordinary city in an illustrative position discloses nothing, so each
source's one example search keeps its city (owner's ruling, 2026-08-21): a shipped example is a
real, pasteable URL, a fictional place would make it return nothing and read as a broken source,
and stripping the filter to avoid naming a city is a cost with no benefit. Do not "finish" #27 by
sweeping example searches, docstring illustrations, or any other single incidental place name --
that was proposed during this work and rejected.

IANA timezone identifiers (`Europe/London`, `Asia/Dubai`) are the one standing EXEMPTION. They are
standards keys rather than preferences, no synthetic substitute exists in the tz database, and in
`tests/test_track_ics.py` the zone's UTC+0 offset is the property under test. `sluice/track/ics.py`'s
Windows-to-IANA mapping table is the same exemption. Note also that a lowercase place-name sweep hits
`cairo/pango` -- the Cairo graphics library, in `renderers/template.py`'s import-error message -- so a
rule keyed on bare lowercase city names corrupts a real error string.

**`sluice/` is standard-library only.** The sole exceptions: `yaml`, imported under a guarded
`try/except ImportError` in each config module; the Google client libraries, imported lazily inside
functions in `track/google_client.py`; `jinja2`/`weasyprint`, both imported lazily inside
`renderers/template.py` (`renderers/weasyprint.py` -- the old bundled renderer -- is DELETED;
selecting the retired `weasyprint` renderer name now raises via `plugins._RETIRED`, naming
`template` as the replacement); and `argcomplete`, imported under the same guarded
`try/except ImportError` shape at the top of `cli.py`, behind the `completion` extra --
`argcomplete.autocomplete(parser)` is itself a no-op unless a shell's completion hook has set
`_ARGCOMPLETE`, so importing it costs nothing on an ordinary invocation, and its `.completer`
callbacks (see `_complete_source_id`/`_complete_status`) must never raise, since an exception
there breaks the user's shell on every TAB press, not just the one command.

And `mcp`, imported lazily inside `build_server()`'s own function body in
`sluice/mcpserver.py` (never at module scope, and nowhere in `cli.py` at all) behind
the `mcp` extra -- it pulls in an async/network stack (uvicorn, starlette, anyio,
pydantic, ...) meaningfully heavier than a config-file parser, so nothing outside
`job-sluice mcp serve` may cause it to load; a bare install never imports it.

HTTP goes through `urllib`, not `requests`. Do not add a runtime dependency without a deliberate decision. The rule
binds `sluice/` -- what ships to a user. The root `package.json` is not an exception to it: it
pins the Node-based `rulesync` CLI that regenerates `.rulesync/`'s AI-tool outputs, a CI-only
dev-time tool that never ships in the package and nothing a user installing `job-sluice` ever
sees. Nor is the `test` extra (`pytest`, `faker`, `pytest-cov`, `setuptools`, `build`) --
installed to run the gate and never imported by `sluice/`. Being an EXTRA is not what exempts a
package from the rule, which is the part the table disguises: `render`, `google` and `completion`
sit beside `test` in the same `optional-dependencies` and are firmly INSIDE the rule -- they
install the very `jinja2`/`weasyprint`, Google, and `argcomplete` imports named above. `jinja2`
ALSO sits in `test` (deliberately -- see Commands above, so a shipped-template test runs for real
in CI rather than skipping the way an earlier `weasyprint` importorskip once did), but being in
two extras at once does not move it out of the rule: it is still `render` that puts it firmly
inside, exactly like `weasyprint`. `mcp` sits in BOTH `mcp` and `test` for the identical reason --
CI installs only `[test]`, and `tests/functional/test_mcp_contract.py` needs the real package to
drive it for real rather than skip itself. Being in two extras does not move it out of the rule
either: it is still `mcp` that puts it firmly inside. The line is whether a user's install can end
up executing it.

**Fail loudly at construction.** An unknown backend/adapter name raises and lists the valid names
rather than falling through to a default. A quiet wrong default is the bug class this codebase most
consistently engineers out; see `_select_backend`'s guard in `cli.py`.

## Conventions

- Comments explain *why* — the invariant being upheld, the bug being prevented, the trade-off taken.
  The existing code is dense with them and several encode real incidents; match that density rather
  than stripping it.
- Conventional commits (`fix(triage): ...`, `ci: ...`, `docs: ...`). These are not decoration
  since #12: release-please reads the subjects to decide the next version and to draft the
  changelog, so a mistyped type silently changes what gets released.
- **A `!` is a claim about the USER'S INSTALL. `CHANGELOG.md`'s "What counts as breaking here"
  is the list -- do not restate it.** That section is tracked, it is the one a user reads, and a
  second copy diverges rather than agreeing: an earlier draft of this very bullet dropped its
  status-transition class and invented a CLI one.

  What is NOT written down there is the negative case, which is the one that goes wrong. An
  internal seam change does not earn a `!`. `refactor(core)!: retire read_experience_entries for
  read_evidence` (`cf5978d2`, #165) took one, and the fact that settles it is `CHANGELOG.md`'s
  own: **nothing imports `sluice` as a library.** Removing a member of a published Protocol is
  therefore invisible to every install, however REQUIRED that member was -- which is why a
  headcount of out-of-tree implementers is not the test, and could not be, since a published
  package cannot know it. Read without that fact, the seam's own documentation
  (`docs/ARCHITECTURE.md`, `tests/conformance/test_store_contract.py`) argues the other way, and
  an agent applying this rule literally lands on "qualifying".

  Get the type right in the COMMIT. The bump is computed from commits already on `main`, so the
  marker is cheap to type and awkward to unpick afterwards -- not irreversible (`CONTRIBUTING.md`
  has the version and the changelog being hand-edited inside the release PR before merging), but
  a correction after the fact rather than a substitute for the right type.

  One mechanical trap, worth knowing before you write about any of this: the breaking-change
  trailer is recognised by POSITION, not by meaning, so DESCRIBING it in a commit body can
  trigger it. Measured while drafting this bullet -- a body that opened a line with the literal
  token was inert only because a backtick preceded it, which is not a margin worth carrying. Do
  not reason about which prefixes the parser accepts; keep the token out of column one.
- **The PyPI distribution name is `job-sluice`, not `sluice`.** The latter has been squatted
  since 2015 by an unrelated, dormant zfs-snapshot tool with no console script of its own (no
  binary collision, but `pip install sluice` could never resolve here). Distribution name,
  import package, and console-script name are three independent things in Python packaging, and
  only two of them changed: `pyproject.toml`'s `[project] name` and `[project.scripts]` are both
  `job-sluice`, but `import sluice` and every `SLUICE_*` env var / `~/.config/sluice/` XDG path
  stay exactly as they are -- those are invisible to a user, and renaming them would be a
  breaking CONFIG change (this project's own change-classification rule below rates that above a
  breaking API change) for no user-visible benefit. Do not "fix" `job-sluice` back to `sluice`
  anywhere it appears in `pyproject.toml`, `cli.py`'s `prog=`/`--version`, or a user-facing
  printed string -- see `test_release_version.py` and `tests/test_docs_claims.py`, both of which
  pin this.
- **The version has ONE home: `sluice/__init__.py`.** `pyproject.toml` declares `dynamic` and
  setuptools reads that attribute statically, so `pip show job-sluice` and `job-sluice --version`
  cannot disagree — there is no second value to drift from. The line carries an
  `# x-release-please-version` marker and `release-please-config.json` lists the file in
  `extra-files`; BOTH are required, they are independent, and losing either stops the bump while
  the release PR still opens and the changelog still updates. `tests/test_release_version.py`
  pins the pair, enumerating marker-carrying files by walk rather than naming the path.
- **Releases are cut by merging release-please's PR**, never by tagging from a shell: the tag and
  the version are written by the same tool in the same commit. Edit the generated changelog entry
  IN that PR before merging — a `fix(vault): ...` subject cannot say that a config now means
  something different, and a breaking CONFIG change outranks a breaking API change here. Note the
  PR needs a token that is not the default `GITHUB_TOKEN`, or the `qa-gates` ruleset blocks it
  forever: GitHub raises no workflow runs from `GITHUB_TOKEN` events, so `ci-success` never
  reports on it. It is a GitHub App token minted per run, NOT a PAT, and the reason is the
  approval leg: a PAT opens the PR as the repo owner, nobody may approve their own PR, and
  `.coderabbit.yaml` now skips release PRs — so a PAT would deadlock them. An App authors the
  PR, leaving a human free to approve.
- Tests assert on behaviour, not merely that code runs. Fixtures stay synthetic.
- The four adapter seams (backend, store, renderer, fetcher — the config keys, and the
  `_STORE_SEAM`/`_FETCHER_SEAM`/`_RENDERER_SEAM` constants in `core/app.py`) are each a name-keyed
  registry resolved via `plugins.get`. The backend seam has four self-registering provider
  implementations (`anthropic`/`openai`/`claude-max`/`deepseek` in `sluice/backends/` — the names a
  config `primary_backend`/`fallback_backend` selects; `claude-max`/`deepseek` ALSO survive as
  deprecated `--backend` role aliases, which is the separate role concern below, not a second registry);
  the RENDERER seam has two self-registering
  production impls — `template` (the default: fills a user's Jinja2 template, or the packaged
  default, via WeasyPrint; `pip install -e '.[render]'`) and `script` (the external shell-out
  escape hatch) — selected by `cv.renderer`, so by-name selection between real implementations is
  already LIVE there. That seam has a second, OPTIONAL member, `precheck(cv_text) ->
  list[str]`: a renderer implements it only when the composed CV must satisfy a grammar of its own
  (`template` does, `script` does not, and the engine reaches it through `getattr` so an absent one
  gates nothing). Keeping it on the renderer is what stops one implementation's requirements binding
  the whole seam — measured, the engine imposing `template`'s grammar unconditionally reported
  `skipped-gate` under `cv.renderer: script` for a gate-clean CV. Being optional means it is the one
  seam member NOTHING types, so the engine checks the RETURN at the call site and raises naming the
  renderer: a `precheck` returning a bare `str` was otherwise spread one CHARACTER per violation into
  the retry prompt. `Sluice.compose_cv` resolves the renderer on a `--dry-run` too, purely so this
  hook runs — a dry run reporting no violations where a real run reports `skipped-gate` is a preview
  that false-greens the input it is previewing; a `RenderError` during that construction is caught,
  WARNED about by name, and the dry run proceeds. Store and fetcher have one production impl each (`vault`,
  `camofox`); the STORE seam has since grown the same OPTIONAL shape, `preflight() -> dict`, reached
  via `getattr` exactly like `precheck` and for the same reason (an implementation that cannot say is
  not one that is broken) — `job-sluice doctor` (see below) is the one caller, and `Vault.preflight`
  answers with FACTS (vault dir, baseline CV, Judging Profile, a total/verified/pending count for
  each of the three evidence corpora -- #164: `experience` (keeping its pre-#164
  `experience_total`/`experience_verified` names since `doctor` already consumes them),
  `skills`, `stories`, iterated off `EVIDENCE_KINDS` rather than hand-listed -- and, #133/#107,
  whether a candidate name and a contact block are declared), never
  verdicts, keeping classification in `core/doctor.py` where the backend rules already live. The
  selection is also exercised in tests — `tests/harness/` registers a fake fetcher
  (`browser.py`) and renderer (`renderer.py`) and resolves them through the same seam. The backend seam
  differs in shape, though: a role layer (auto/primary/fallback, in
  `Sluice.backend()`) sits above the provider lookup, and its factory takes resolved construction params
  (model/key/base_url), not the config object -- so it does not go through `Sluice._resolve` the way the
  other three do. Route new implementations through those seams (a self-registering module) rather than
  around them.
- `.rulesync/` is canonical. `CLAUDE.md`, `AGENTS.md`, `.claude/` and the other AI-tool outputs are
  generated and gitignored; edit the source, then regenerate. **`.claude/settings.json` is the one
  deliberate exception, tracked rather than gitignored:** Claude Code's own `enabledPlugins` key
  (written by `/plugin marketplace add`, never by rulesync) lives in the same shared file rulesync's
  `hooks` feature writes, and tracking it is the only way a plugin enable reaches every worktree and
  contributor rather than staying one machine's private config. rulesync's hooks writer merges
  additively, so the two coexist; `.gitignore` carries the `/.claude/*` + `!/.claude/settings.json`
  shape this requires (a bare `/.claude/` would make the re-include inert), and
  `tests/test_no_leaked_files.py`'s `.claude/` prefix gate carves out exactly this one path by name
  -- every other path under `.claude/` (agents/, skills/, worktrees/, scheduled_tasks.lock) stays as
  forbidden as before. Tracking it also means a CI checkout always supplies a copy before generation
  runs, which defeats two things a purely-gitignored file relies on being absent for:
  `guard_rulesync_drift.py`'s exact hook count (rulesync silently SKIPS rewriting a file that
  already matches, dropping `hooks` from its summary rather than reporting it as zero) and
  `guard_emitted_outputs.py`'s structural check (a stale-but-valid copy would survive a genuinely
  broken generate run undetected). `scripts/reset_tracked_hooks.py` runs before `npm run rulesync`
  in CI and clears just the `hooks` key -- the one part rulesync owns -- restoring both guarantees
  without discarding `enabledPlugins`; its docstring has the measured chain end to end.
- **`README.md` and everything under `docs/`, plus `CONTRIBUTING.md`/`SECURITY.md`, are the
  opposite of the point above: tracked, hand-written, human-facing documentation, not generated
  outputs.** Edit them directly; there is no source-of-truth file to regenerate them from, the way
  there is for `CLAUDE.md`/`AGENTS.md`. Several of them DO carry automated checks rather than a
  generator. **State NO COUNT of them, here or anywhere.** Three successive attempts to write
  one in this bullet were each wrong -- "the one exception", then "TWO of them, both in
  `tests/test_docs_claims.py`" (contradicted six lines below by INSTALL's own list), then "Two
  walk the real `cli.py` parser" (`_command_tree()` has seven call sites, and the sweeps built
  on it validate command claims across every file in `_DOCS`). Each fix wrote a new number
  instead of deleting the number. `tests/test_docs_claims.py` is the file; read it for the
  roster.
  Two checks are worth naming for what they DO, without implying they are the whole set:
  `docs/USAGE.md` fails the build if a command it documents stops existing or a real command
  goes undocumented, and (#221) `README.md`'s Commands table fails it if the table and the
  parser tree disagree in EITHER direction, on groups or on subcommands. That second one exists
  because nothing checked the table AS A TABLE -- the parser was already swept against README's
  prose invocations, but a row names its group once and lists its subcommands as bare backticked
  tokens in the next cell, an adjacency no prose sweep matches. It claimed ten top-level groups
  against a real thirteen, and named four of `leads`' five subcommands, while `USAGE.md` carried
  all of them and stayed green. Same generate-then-diff discipline as the `rulesync` CI job,
  applied to hand-written files instead of generated ones. Note README is ALSO
  `pyproject.toml`'s `readme`, so a false claim there ships to PyPI as the package description;
  its sample lead note is swept for identities too (`tests/test_fixture_name_neutrality.py`) --
  the company, the location and every url INSIDE that one fenced block, in BOTH halves of it
  (the frontmatter keys and the rendered restatements below the closing `---`), never README's
  prose, and never `role`/`salary`/`role_type`, for which no roster exists and inventing one
  would be the classifier that file's own docstring argues against. Scope that claim by
  IDENTITY, never by spelling: an earlier cut said "frontmatter keys", which described the code
  exactly while leaving the rendered half unswept, and a real employer, place and ATS host all
  shipped green past it. `docs/ARCHITECTURE.md` is the living technical
  description (module-by-module, the seams, the store contract); `docs/USAGE.md` is the CLI
  reference; `docs/CONFIGURATION.md` is the config-key reference; `docs/TROUBLESHOOTING.md` is
  fixes for specific failures; `docs/INSTALL.md` is the per-channel install guide (#104). That
  last one is the doc whose claims rot fastest, and what IS pinned about it is worth knowing
  precisely, because the gap is narrower than "nothing" and wider than "it is covered". Guarded:
  every published channel has install instructions and INSTALL's two method tables agree
  (`test_docs_claims.py`); its credential table matches `core/app.py`'s real provider->env map;
  every doc URL a `sluice/` runtime string prints, and every anchored link between shipped docs,
  resolves to a real heading (`test_doc_links_from_code.py`). NOT guarded, and this is the part
  that matters: nothing runs a COMMAND in that file against the channel serving it. A wrong
  `pip`/`brew`/`docker` invocation, a flag that no longer exists, an argument order that has
  changed -- all ship green. So a command added or changed there must be RUN, not reasoned about. `docs/superpowers/specs/` and `.../plans/` are historical design
  documents once implemented -- not maintained, and the code wins on any disagreement.
