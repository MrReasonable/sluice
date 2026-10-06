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
its own top-level block of that same file (`triage:`, `cv:`, `apply:`, `track:`); the root
keys, read by `load_config`, carry ingest's settings and every setting no single sub-app owns (for
example the adapter seams, the dossier keys, `lead_ttl_days` and `backend_timeout`). Every knob has a code default, so everything runs with no config file at all. New
tunables go in the relevant `*Config` dataclass and `sluice.yaml.example` — never hardcoded in
logic. Only `load_config` names its fields explicitly; the four sub-app loaders are
`hasattr`-filtered `setattr` loops, so a new ROOT field is dead until `load_config` names it, and
the sub-app loaders must not be "fixed" into naming theirs (`load_track_config`'s merged-denylist
branch lives in that loop). One exception to each loader reading only its own block:
`load_config` also refuses the retired CV inputs (`core/config.py::refuse_retired_cv_inputs`: the
root `baseline_rel` and the `cv:` block's `employers`, #364/#365/#368), because it runs on every command, so
the first command after an upgrade stops on them rather than the key going quietly dead.

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
a list rather than change a hundred notes. **Exception: `leads dismiss` (#131) and `leads add`
(#241) both write unconditionally on every call**, like the pipeline commands, not like their
`leads` siblings — what each writes is what the user typed (`--lead`/`--reason`; the new lead's
own fields), not something the tool computed, so there is no computed set to preview first. Note
that is a property of the CONTENT, not of the command acting on one lead the user named:
`leads expire --expire <slug>` names a single lead too and STILL needs its flag, because the
verdict it writes there (stale, therefore dismiss) is the tool's derivation and not something the
user typed. Do not reach for `leads rename` as that example -- it carries only `--apply`/`--json`
and sweeps the whole vault, so it is not single-lead at all; an earlier cut of this paragraph said
it was. (`docs/ARCHITECTURE.md` has the per-pass mechanics.)

`leads add` is the only route into the lead store needing neither a browser nor an MCP client —
`ingest run` needs a Camofox server, and `mcpserver.create_lead` drives this very facade without one
but only under `mcp serve --write`, so from the CLI alone a fresh install could not reach `triage` or
`cv` at all (#241). Do not shorten that to "the only offline route": the MCP tool is offline too, and
the same paragraph names it two sentences later. It is a thin front-end over `Sluice.create_lead`,
the SAME facade that MCP tool drives, not a second writer: never-clobber, the #81 archive probe and
the decision to leave `seen.db` untouched all live in that facade already, and a sibling write
function would be a new CodeQL sink with every one of those to re-argue. It reports `Vault.upsert`'s
six-member vocabulary verbatim and exits non-zero on the three that write nothing.

It exposes no `--source`, and the reason is the BOARD-NAME GUARD, measured:
`triage/resolve.py`'s `_is_board_name` discards a resolved company equal to the source id, so
`_is_board_name("Reed", {"source": "reed"})` is True while the same call under the facade's `manual`
default is False — a `--source reed` would throw away an employer genuinely called Reed. resolve.py
ALSO looks the id up to call that source's optional `company_from_url`, but that arm is a FORWARD
hazard only and must not be cited as a live one: an earlier cut of this paragraph claimed
`--source reed` would aim reed's url extractor at a foreign url, and reed defines no such hook at
all — `wellfound` is the only source that does, and it anchors its regex on its own host and
abstains. `--role-type`'s accepted set is DERIVED from `roletype._ALIASES` (exported as
`ACCEPTED_ROLE_TYPES`), never pinned to `contract|permanent`: 11 of the 13 spellings the facade
honours are aliases, so pinning the pair made the CLI reject `perm` and `freelance`, which the MCP
tool over the same facade accepts.

**One backend per stage, retried on itself, never swapped (#333).** Each of `triage:`, `cv:` and
`track:` names one `backend` and `model`; triage's tier-3 company resolution has its own
`resolve_backend`/`resolve_model` (empty = triage's own). There is no fallback provider: it used to
answer a failure by sending the prompt to a second provider and model, so a run could complete with
every gate green on a model nobody chose, and the audit could review that model's own draft.
`Sluice.backend()` wraps the one provider in `RetryingBackend`, which retries a
`BackendError(transient=True)` (root `backend_retries`, default 2) and never one marked
non-transient -- a missing key, a 4xx other than 408/429, a truncation, which fail identically every
time. A TRANSIENT failure that outlives the retries is an outage: triage stops judging, track
stops classifying (leaving the rest unseen) and a cv batch stops, each reported loudly with a
non-zero exit. A NON-transient one is the item's own -- that batch, message or lead -- and is
handled per item, never as an outage, or one over-long email would hold track's watermark for
good. cv's advisory audit is the narrower case: an audit that could not run HOLDS the CV for
sign-off under `cv.require_signoff` (the exit code is unchanged, the CV having composed), and with
that switched off it is served unaudited exactly as an `unsupported` flag would be. `--backend` (and MCP `cv_run`'s `backend`) names a PROVIDER for one run --
the configured one keeps its model, another uses its `DEFAULT_MODELS` entry -- and the retired role
names `auto`/`primary`/`fallback` are refused with a migration message. The retired config keys
(`core/config.py::RETIRED_BACKEND_KEYS`) REFUSE to load rather than being dropped by the
`hasattr`-filtered loaders, on the owner's ruling, with no deprecation window. Construction failures
are raised at build time, not at first call.

**`complete()` returns a `Completion`, not a string (#308), and `Usage.input_tokens` is DEFINED
rather than copied.** The seam carries the text plus an optional `Usage`, so a call's cost belongs
to that call -- the retired `FallbackBackend.last_backend` was the counter-example the widening
was argued against, overwritten per call and unable to say which leg served which completion. `input_tokens`
means the TOTAL input including anything served from cache, and each provider's parse normalises
INTO that definition: Anthropic's own `input_tokens` counts uncached tokens only, with
`cache_read_input_tokens`/`cache_creation_input_tokens` beside it rather than inside it, so copying
the field across puts the cache hit rate above 1.0 on exactly the well-cached call the number
exists to report. DeepSeek's docs do not state how `prompt_tokens` relates to its hit/miss split,
so the parser sums hit+miss when both are present and assumes no relation. Every count is
`int | None`, and None means NOT REPORTED: `claude-max` is flat-rate in text mode and answers with
its identity and no counts, because zeros would claim the call was free, and a bare `usage=None`
would leave its calls anonymous in the log. `provider` is threaded down from `make_backend`, never
written as a literal in each factory -- that is a third spelling of something the module already
states twice, so a module copied to add a provider would keep the original's label and mislabel
every row silently. Spend from a call that billed and then RAISED rides on `BackendError.usage`,
the only carrier where there is no return value -- and `RetryingBackend` carries every failed
attempt's spend forward, onto the eventual success as `unserved_usage` or onto the final error.

**Metering is wrapped where a backend is HANDED to a stage, not where it is called.** `meter(log,
backend, stage, lead=None)` (`core/usage.py`) returns the backend unchanged when there is no log --
which is the DEFAULT: recording is OFF unless the root `record_usage` key is true (or
`SLUICE_USAGE` names a path, which also switches it on), because its per-lead rows carry the
lead's slug and so name the employers a user is applying to. That is `0 == abstain` applied to a
WRITE rather than a filter, and it is deliberately unlike `triage.audit_jsonl`, which is always on
because it records what sluice DECIDED rather than what the user SPENT.

The SWITCH and the LOCATION are two keys, and the split is worth knowing before "simplifying" it
back. `usage_jsonl` says only WHERE, and an empty one resolves to the XDG state file exactly like
every other relocatable path -- so `record_usage: true` alone needs no path. They were ONE key
first, an empty value meaning OFF, which left this the one `paths.resolve` caller that returned
above its own XDG rung -- every other passes a `kind` and reaches it -- and left a user who
switched recording on with nowhere for the file to go: the command had to tell them to invent
one. Turning a feature on and choosing where its file lives are two
questions. `SLUICE_USAGE` keeps both jobs deliberately -- relocating a log that is switched off has
nothing it could mean -- while the switch has NO env var, since an exported variable that silently
starts writing employer names is the surprise the default exists to prevent.
Almost every wrap is in `core/app.py`, because almost every backend built there serves exactly one
stage; `cv/engine.py` is the exception and takes the log itself, since it spends ONE backend on
compose, audit and voice. It is also the only stage recording a LEAD -- as the store-issued
`slug`, never the opaque `LeadNote.ref`, which is a filesystem path for the vault store -- and
that is a placement choice rather than a scope fact: triage's tier-3 resolve has a note in
scope at its own call site and is wrapped once at the boundary anyway, being a bulk pass. So a
guard of the shape
"every module holding a `.complete(` also meters" is FALSE BY DESIGN -- the first draft of
`tests/test_usage_wiring.py` asserted that and would have had to be narrowed until it checked
nothing. What that file rosters instead is both ENDS against hand-written targets: every
`.complete(` call site, and every stage literal `meter(...)` passes. It cannot prove the wiring
FIRES, which is a dataflow question, so each stage also names the runtime test that witnesses it
recording. That gap is not hypothetical: a `.complete(` in `core/app.py::doctor` was claimed
metered by a COMMIT MESSAGE while no `meter(...)` call existed, and the report was short by every
probe ever run with nothing red.

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
`update_fields` also takes `preserve_block_values` (#329): a named key whose fresh stored
value spans several lines -- a hand-typed block list or block scalar -- is left unwritten rather
than corrupted by `_set_fm`'s single-line replace, while the other fields still land. Its
`append_note` write to `relevance_notes` is a separate write path, not a `fields` key, and
abstains the same way when the fresh stored value itself spans several lines, rather than
corrupting it. Every frontmatter read and write finds a key at the frontmatter's base indent
(`core/vault.py::_base_indent`, used by `_key_lines`), value from the key's OWN line. A
blank `key:` therefore reads blank even when a hand-typed block list sits under it, so every write
decision that asks "blank / unchanged / present" also asks `_holds_multiline_value` and treats a
multi-line value as PRESENT; a reader moved without its guard turns a refusal into an overwrite.

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
and canonical equivalence (#205, then #299), and the fold was a LIVE BREACH rather than a tidy-up: measured before it, merging a lead
away and re-scraping it as `EXAMPLE CO` rather than `Example Co` returned `created` while the
exact-casing control suppressed correctly — the guard worked and the re-scrape walked past it.
Folding can only suppress MORE, never resurrect more, and it does not widen `seen.db`, since that
arm stays gated on `url_proven`, which no name folding can manufacture. The fold has ONE home,
`core/names.py`'s `fold_note_name` (its own module, so the vault store and `core/layout.py` both
import it and neither reaches into the other for the fold), and EVERY path that resolves a lead by name goes through it —
`_locate`, `_archived_match`, `read_leads`' report and `reconcile_names`. Do not restate that as a
count; it shipped as three and was stale inside the same branch. A `_locate` that folds against an
`_archived_match` that does not is measurably a resurrection, and a `reconcile_names` that does not
measurably mint the pair, so these are not independent `.casefold()` calls. It folds CASE and
CANONICAL EQUIVALENCE and stops there (#205 then #299): the shape is UAX #15's canonical caseless
match, `NFD(casefold(NFD(x)))`, since `casefold` alone normalizes nothing and two composition forms
of one accented employer therefore seated two notes with two statuses. Canonical equivalence is not
a widening past spelling — it says the two strings ARE the same text. The ceiling is on the
NORMALIZATION applied, not on the equivalence that results: `casefold` is FULL case folding
and merges the fi/ff/ffi ligatures by itself (measured), so "no compatibility equivalence"
would be breached by the shipped code. What must stay out is NFKD/NFKC, which would also
merge a superscript with its digit and a full-width letter with its ASCII form. Do not carry
`core/leads.py`'s `_norm_tokens` NFKD across: that compares token SETS for a human-gated report,
not filenames for a write decision. The fold ALSO has a second kind of consumer since #298 —
`_folded_archive_names`/`_archive_name_candidates` fold to choose an archive FILENAME rather than
to resolve a lead, and both halves must fold identically or the collision skip silently stops
firing.
A THIRD kind since #364/#365/#368: `core/layout.py::fold_employer` matches an experience
entry's `Company:` to a CV Layout role through the same fold (whitespace collapsed on top),
imported from `core/names.py` like every other consumer. It sits on the IDENTITY side -- a wider fold makes
more entries eligible for more roles, which loosens `WRONG EMPLOYER` -- so the fold must not
widen for it either.
`_merged/` is load-bearing retention, not
scratch: do not prune it. The lead scan is recursive (#1), so `_merged/` is excluded from it BY NAME
(`_PRIVATE_SUBDIRS`, at the TOP LEVEL only) rather than by the accident that a flat `os.listdir` never descended into
it -- deleting that prune resurfaces every archived loser and undoes this invariant outright.
See `core/protocols.py`, `docs/ARCHITECTURE.md`, and
`tests/conformance/test_store_contract.py::test_merged_away_lead_is_never_recreated`.

**A lead's identity is its note name UP TO CASE and UNICODE CANONICAL EQUIVALENCE (#205,
then #299), and the fold has one home.** Boards render
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
which is what keeps the cost where it was (the folded listing is about three orders of magnitude
dearer than the stat probe and scales with the note count) — do not "simplify" that into an
unconditional fold. Its
consequence is stated rather than closed: against a pair a pre-#205 store already holds, a scrape
matching either spelling updates that one silently and only a THIRD casing reaches the ambiguous
refusal, so the standing signal is `read_leads`' own warning. That warning names `leads dedupe`,
which already CLUSTERS such a pair (`cluster_duplicates` normalizes through `_norm_tokens`, which
casefolds) — but do not upgrade that to "`--merge` resolves it", which was the first wording and is
false on the reported pair: `resolve_merge_status` returns `conflict` for two distinct non-`new`
triage states, so a `shortlist`/`dismiss` twin pair clusters and refuses to merge (measured), which
is correct because picking the survivor is the human judgement a conflict demands. The report sweeps
every lead note WALKED rather than the list `read_leads` returns — unlike the duplicate-slug sweep
beside it — because the pair is a property of the store and a `shortlist`/`dismiss` pair shows only
one twin to any status-filtered read. No note is RENAMED by any of this, and the reason is SCOPE, not orphaned tracker
state: casing normalization does not fix dedup, and `Sluice.rename` (#151) already refiles
dead-letter rows via `DeadLetterDb.rename_lead` and refuses upfront if that store is unreachable.

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
`target_locations`, `reject_companies`, `relevance_keep`/`relevance_drop`, `listing_languages`, pay
floors) defaults to
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

**A CV is assembled, never parsed (#364/#365/#368).** `cv run` asks the backend for a JSON
reply -- a profile, cited bullets per role SLOT, and skill picks from a closed list -- and
sluice builds the `CvDocument` itself (`cv/document.py::assemble`): the CV Layout note
(`Job Applications/CV Layout.md`, parsed by `core/layout.py`) gives every role's heading,
dates, location and title and which experience entries each role may cite, and the Candidate
Profile gives the name and contact. The model writes no heading, date, name, contact,
certificate or education line, so the gate checks none of them, and no such check must be
added: a check that refuses vault text makes the user prove their own data. The checks exist
to catch the MODEL putting words in the user's mouth, and stop there. `cv/engine.py` refuses a
lead before any dossier fetch or LLM spend (`skipped-config`) while the Candidate Profile's
derived name or contact block is blank, or the CV Layout note is gone -- the name becomes the
PDF's headline, and a blank one is the quiet wrong default this codebase engineers out.
`cv/reply.py` reads a reply: `extract_json` tries fenced blocks first, then each plausible
`{`, and takes the first decoded object carrying `profile` and `roles` and no example
placeholder -- so chat around the JSON (a backend such as `claude --print` is an agent, and
talks) is ignored rather than parsed -- and an object with a duplicate key is never read
with one value silently winning: it is set aside, and is a finding when no other candidate
carries the full shape. `parse_reply` shape-checks it into a typed `Reply` or returns
`REPLY:` findings; slot ids match case-insensitively, and a role heading naming exactly one
slot reads as that slot. A BULLET whose text fails a check is
the exception: it stays in the `Reply` with its findings filed under
`Reply.bullet_findings` by (slot, position), because only the selection knows whether it
survives the budget. `cv/selection.py::select` decides what
may render: skill picks off the pool, then duplicates, then beyond `skills_max` are DROPPED
-- in that order, so a rejected pick never uses up the cap -- and bullets beyond a role's
budget are TRIMMED, the first N kept (a slot with no eligible entry has budget 0). It
carries forward the text findings of the KEPT bullets only (`Selection.findings`), which the
engine adds to the attempt's violations. A drop is reported (`skills_dropped`,
`bullets_trimmed`) and never refused -- a trimmed bullet's text findings included -- so it
never costs a retry or a lead.

**The fabrication gate is hard, and reads only what the model wrote.**
`cv/validate.py::check_selection` runs over the SELECTION -- the profile and the kept bullets,
never vault text: every kept bullet cites entries that exist (`UNCITED BULLET`, `BAD
CITATION`) and are eligible for its slot (`WRONG EMPLOYER`); every figure in a bullet appears
among its cited entries' figures, and every figure in the profile among some entry's
(`INVENTED METRIC`, `INVENTED PROFILE METRIC`); a tool from the entries' `Tools:` vocabulary
that a bullet names in its declared spelling must be declared (in any case) by a cited entry,
or named in that entry's own title or body (`MISATTRIBUTED TOOL`), a check that runs only
while some verified entry declares `Tools:`, which every composed result reports
(`CvResult.attribution_check_off`); and a `fabrication_decoys` term matches as a whole token
sequence, never across a sentence break (`FABRICATED`). A FIGURE is a digit run read whole
(`core/tokens.py::figures`): any character `unicodedata.digit` gives a value counts as a
digit, normalised to ASCII on both sides, so a figure cannot dodge the gate by script;
thousands groups joined by a comma, NBSP, narrow NBSP or thin space read as one number
(`50,000` is `50000`), and digits joined by a single `.` are one figure (`8.3`, never the
licensed `8` and `3`). `core/tokens.py::group_reading` is the one rule for every group
separator, used both by `figures` and by `cv/reply.py`, so the two cannot disagree. Declared
`Tools:` names are blanked by their own token spans before figures are read (a bullet's cited
entries' tools; for the profile, every entry's), never by substring. An em dash or a literal
`--` in the same texts (`cv/slop.py::check_hard`) is the hard slop tier and reports in `slop`.
`REPLY:` findings are HARD too: a reply that cannot be read, a bracket or line break in a
text, a section heading given as content, no bullets in any role that can carry them, an
invisible format character anywhere -- and, in the MODEL's text, these number shapes
(`cv/reply.py::_text_findings`): a character with a numeric value `figures` cannot read
(`unicodedata.numeric` gives one and `unicodedata.digit` does not, in ANY category: a Roman
numeral, a circled number above nine, a vulgar fraction, a CJK numeral, which is a letter --
category Lo -- to Unicode; a circled digit or a superscript has a digit value and is read, not
refused), a decimal written with a comma
(`2,5x` would read as 25), digits split by a separator `figures` cannot join, and an
ASCII-space group (`Led 3 100-person teams` is three teams or 3100, so `figures` reads that
shape both ways on the entry side and the model may not write it). A residual, stated: a
number grouped by a space after a lone-letter prefix (`x1 000`) reads
as two figures and is not refused, because the label rule cannot tell it from `Q3 120`
(`core/tokens.py::group_reading`). Look-alike letters are
a `REPLY:` finding too, for the whole-term checks' sake -- a full-width or other compatibility form,
or a word mixing Latin with another script, which would let a tool or decoy name dodge a
match -- and so is a non-Latin letter touching a digit (a Cyrillic or Greek O in `8O%` reads
as 80 on the page while `figures` reads 8; the word scan cannot see it, since its pattern
excludes digits). "Touching" looks through what renders as no gap -- a combining mark,
whitespace other than an ASCII space, one figure separator (`8.O` reads 8.0) -- and a digit is
`core/tokens.py::digit_value`, the one predicate `figures` reads runs with, so the two cannot
disagree on where a run starts or ends. A second residual, stated: an ASCII letter touching a digit (`8O%` with a
Latin O, `2l0`) is NOT refused, because `5G`, `O2` and `10l` are real text, so only its
digits are checked and the advisory audit is the one backstop. `check_selection` has ONE accumulator, extended only by `v.append(<CATEGORY> ...)`
with the category inline: `tests/test_docs_claims.py` derives the gate's categories from
exactly that shape and pins `docs/TROUBLESHOOTING.md` to them, so a category reached any
other way is documented by nothing.

**An entry's figures and tools each have one home.** `cv/validate.py::entry_facts` takes an
entry's figures from `cv/bundle.py::_entry_block` -- the lines the composer is shown for it,
minus its own `[id]` code -- and its tools from `core/tokens.py::tool_items`, shown by a
SEPARATE emitter (`_tools_line`), so a digit inside a tool's name never licenses a figure.
The owner's model splits an experience entry's annotations in two: `Tools:` is the specific
tools and hard skills tied to the job (attribution-checked); `Skills:` is general soft skills
tied to NO job. `Skills:` items are SKILLS-pool candidates (`cv/selection.py::build_pool`) and
nothing else: no emitter shows them inside the entry, `term_vocabulary` does not count them,
and they are never attribution-checked, never a figure and never span-blanked, which is why
`core/tokens.py::skill_items` skips the digit-led-token rule `tool_items` enforces. Do not
"restore" a per-entry `skills=` line: shown in an entry and counted as vocabulary, a tool name
left in `Skills:` could be claimed under that employer with nothing to flag it.
The gate is HANDED its source set rather than recovering it (#174): `entry_facts` walks the
bundle's structured entries, never the rendered prompt text, so no line of vault free text
can mint or rebind a citable `[id]`. A line added to `_entry_block` becomes a source for that
entry; the frozen-literal guards in `tests/test_cv_bundle.py` are the ratchet, and
re-freezing `FROZEN_BUNDLE_TEXT` after widening `_entry_block` moves that comparison with the
widening -- only `test_entry_facts_sentinels_hold_independent_of_the_frozen_literal` does not
move with it -- so read the freeze diff. Nothing else licenses a figure: not the guidance
(`cv.negatives`), not the Skills Inventory, not the CV Layout, not the triage notes (#329),
and not a baseline CV, which nothing reads. `core/tokens.py` is the one tokeniser and term
matcher, shared with `doctor` because `core/` may not import a sub-app. A `Tools:` item with
a token that begins with a digit is refused before any spend (`missing_prerequisites`),
since a name shaped like a figure would let an invented figure vanish with it.

**The retry contract.** A HARD finding, or a STYLE/VOICE finding that survives (#167, #194),
drives EXACTLY one retry, fed the findings and the previous reply's drops. The loop RETAINS the
hard-clean attempt with the fewest style/voice findings -- a tie keeps the later one, and an
attempt whose voice check failed never displaces one whose voice was measured -- and a lead
with no hard-clean attempt is skipped, never rendered. `best` holds the retained SELECTION,
drops included, and the rebind after the loop is the one assignment every later reader goes
through: `assemble`, `cv.rendered.md`, the audit and the render all take the retained
attempt. The STYLE tier -- `cv/slop.py`'s AI-tell stems, `cv/terms.py`'s unbundled-term check
against `cv/bundle.py::term_vocabulary` (on by default via `cv.term_check`, reported in
`CvResult.terms`), and the opt-in model-judged `cv/voice.py` check (`cv.voice_check`) -- reads
the model's text only (`cv/document.py::model_lines`), because a complaint about an employer,
certificate or skill name is answerable only by renaming the thing it names. At shipped
defaults a phrase hit still costs the second compose call; `cv/compose.py`'s prompt bans the
identical list (less `cv.slop_allow`), rendered from `cv/slop.py`'s `_PHRASES`, which is what keeps that the
exception. `term_vocabulary` is built from the entries, their tools, the Skills Inventory and
the CV Layout, and subtracts NOTHING (#368: reading prose negatives as bans stripped terms the
user's own evidence carried). `cv.negatives` is free-text guidance to the composer that no
deterministic check reads (the advisory auditor is shown it, `cv/bundle.py::render_audit_bundle`); a ban belongs in `cv.fabrication_decoys`.

**The advisory audit and the holds.** Above the hard gate an LLM audit (`cv/audit.py`) reads
`cv/document.py::audit_text` -- the profile and each kept bullet with its cites, under its
role heading for context, and no other vault text (no date, location, title, certificate,
education line or skill), which it has no truth for -- against
`cv/bundle.py::render_audit_bundle`, which carries each entry's `Tools:` line so a tool the
gate licensed does not read as unsupported. An `unsupported` flag, or an audit that could not
run (#333), WITHHOLDS the send-ready `tailored_cv` pointer under `cv.require_signoff` (on by
default) via `Store.hold_for_signoff`, cleared by `job-sluice cv signoff`. The hold is
recorded in `pending_cv` and `needs_signoff`; the note's `status` stays `shortlist`, so
never-regress is untouched, and `needs-signoff` is the `CvResult` run-report label, never a
`status` value. `cv.style_hold` (off by default) gives a surviving STYLE/VOICE finding the
same consequence, deliberately a SEPARATE key: `require_signoff`'s True default was chosen for
fabrication, and riding it would withhold `tailored_cv` on a phrase.

**The Renderer seam takes the document.** `Renderer.render(document, out_dir, *,
neutral_name)`: `template` renders the `CvDocument` directly; `script` is handed
`cv/document.py::to_text(document)` without citations -- the meta line POSITIONAL
(`dates | location | title`, always three fields), dates joined by an en dash -- pinned by the
literal in `tests/test_cv_script_golden.py`, captured from the old pipeline. There is no
`precheck` (removed in #364/#365/#368) and no renderer grammar to keep in step with the gate:
a renderer receives data, so it can refuse nothing the gate certified, and it is reached only
past the hard gate. `CvDocument`/`Role` live in `core/protocols.py` and keep their exact
fields, because user templates are written against them.

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
hole one level up. The MCP server exposes `list_evidence` at every level and, under `--write`,
`propose_evidence` (#175) and `verify_evidence`. A proposal lands under `INBOX_SUBDIR`, which
`read_evidence` cannot see, and cannot stamp the key (the same `_render_evidence_note` refusal above
is what holds it, since `fields` is caller-supplied), so it is inert until a human promotes it.
The `verify_evidence` MCP tool is a second route to `Store.verify_evidence` (through
`Sluice.promote_reviewed_evidence`), and it keeps a human in front of every promotion: it has the CLIENT show, in full and each under its own checkbox, every entry it offers in a review
form (SEP-2322 input-required elicitation), promotes only entries the client returned an explicit
`true` for, and only when the entry's current text still hashes to what the form showed. Its input
is exactly `{kind, names}` (pinned in `tests/functional/test_mcp_contract.py`), so the model has no
argument through which to approve. What this guards against is the MODEL accidentally making its
own claims citable; it is deliberately NOT hardened against a client or hook configured to answer
the form for the user, which is the user's own tooling acting for them -- do not add signed state or
tamper checks in its name. `Store.verify_evidence` itself is compare-and-set against the exact bytes
a human was shown, so an edit made after approval abstains rather than becoming citable — the same
discipline `update_fields`' `require_status` uses, and reachable in practice, since the human
reviews while their editor is free to save. Two things follow. The `verified:` key is
STORE-MANAGED, so a new evidence field must never be one a caller supplies; and a promotion path
with NO human in it — a bulk verifier, a `--yes`, a verify argument the model could set — must not
ship. What makes something a promotion path is that it can stamp `verified:`, not that it writes:
`propose_evidence` writes and is not one. `EvidenceKind` carries a flag per question, because the questions stopped having one answer:
`read_by_composer` says the corpus reaches the composer's prompt, `cited_by_gate` says the
fabrication gate may LICENSE its content, and `names_in_skills_pool` (#364/#365/#368, D12)
says a verified note's NAME -- its `Label:`, else its title (`cv/selection.py::cv_name`) -- may
appear in a CV's SKILLS section. `experience` is read and cited (its `Tools:` and `Skills:`
items reach the skills pool through `tool_items` and `skill_items` whatever that flag says); `skills` is read and named, and is
RECOGNISED by `cv/bundle.py::term_vocabulary`, so the unbundled-term check does not report a
declared skill, but nothing LICENSES it; `stories` is neither. `__post_init__` refuses
`cited_by_gate` without `read_by_composer`, since the gate cannot license what the composer
never emitted. `add`'s unverified notice, the `init` wizard's
summary and the MCP `propose_evidence` RESULT say what verifying buys through
`core/protocols.py::verify_outcome`, keyed on `cited_by_gate` and then `names_in_skills_pool`,
and `core/doctor.py::classify_store`'s per-kind row branches on the same two flags in the same
order -- keying such a message on the wrong flag re-creates the over-claim the flags exist to
prevent.

**Where that boundary STOPS, stated rather than implied: a human editing their own vault.** The
vault is the user's Obsidian directory and hand-editing it is a first-class workflow here, so a
note hand-placed in an evidence kind's own directory carrying `verified:` IS citable — measured:
`read_evidence("experience")` returns it under the default `verified_only=True`, and nothing in
`sluice/` inspects a file it never wrote. `_refuse_citation_shaped_body` does not reach it either,
since that runs on the two WRITE paths (`_render_evidence_note` and `verify_evidence`), which is
why it is a NARROWING and #174 -- the gate reading structured entries (`cv/validate.py::entry_facts`)
rather than parsing bundle text -- is the close on the gate side.
That is the same posture the rest of the store takes (never-clobber protects the user's edits, it
does not police them). What must not be claimed is that the single-writer property makes the
citable set unreachable by any other means: it makes it unreachable THROUGH SLUICE. The symlink
refusals in `Vault._evidence_dir` and `_evidence_entry_path` are the same boundary drawn on the
other axis — a store may refuse to reach OUTSIDE the vault the user named, and does, on every
directory component below it and on the entry file itself; what is inside that vault is the
user's.

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
`try/except ImportError` in each config module and in `core/vault.py`, whose write path asks PyYAML
whether a single-line frontmatter write would break a note a person typed by hand (#329); the Google
client libraries, imported lazily inside
functions in `track/google_client.py`; `google_auth_oauthlib`, imported lazily inside functions in
`track/auth.py` (#201, and see below); `jinja2`/`weasyprint`, both imported lazily inside
`renderers/template.py` (`renderers/weasyprint.py` -- the old bundled renderer -- is DELETED;
selecting the retired `weasyprint` renderer name now raises via `plugins._RETIRED`, naming
`template` as the replacement); and `argcomplete`, imported under the same guarded
`try/except ImportError` shape at the top of `cli.py`, behind the `completion` extra --
`argcomplete.autocomplete(parser)` is itself a no-op unless a shell's completion hook has set
`_ARGCOMPLETE`, so importing it costs nothing on an ordinary invocation, and its `.completer`
callbacks (see `_complete_source_id`/`_complete_status`) must never raise, since an exception
there breaks the user's shell on every TAB press, not just the one command.

**The two google entries are a deliberate SPLIT, and `track/auth.py`'s placement is a property
rather than a style match.** They are not folded into one because `pip install -U` does not
re-resolve extras: every `[google]` install predating #201 carries the client libraries and NOT
`google-auth-oauthlib` while `track run` keeps working perfectly, so a probe demanding both would
report SETUP across that whole population on upgrade. And the import must stay INSIDE the
functions -- at module scope `probe_flow_available`, the function whose entire job is to report
the package's absence politely, becomes unreachable on exactly the installs that lack it, since
the import fails first, and its crafted message is replaced by a raw traceback. That is NOT a
claim that no test would notice a hoist -- measured, hoisting both imports to module scope and
running `tests/test_track_auth.py tests/test_doctor.py` turns 79 of them red with
`ModuleNotFoundError` at `auth.py`'s own import line: every test in both files does `from
sluice.track import auth` unstubbed (the one test that pokes `sys.modules` sets the entry to
`None`, which makes the import RAISE rather than succeed), and CI's `[test]` install never
carries `google-auth-oauthlib` (`google` is a separate extra exercised through fakes). This is
written down anyway because the PLACEMENT argument above -- WHICH population's polite message a
hoist would break -- is not something a `ModuleNotFoundError` traceback states, not because the
suite is blind to the mutation.

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
consistently engineers out; see `Sluice.backend`'s override guard in `core/app.py`.

**Terminal output is escaped at two chokepoints, both applying one policy function (#280).**
`sluice` prints scraped board text and LLM output about a composed CV verbatim, so a terminal
control character (ESC, CR, the rest of C0, DEL, the C1 block, a lone surrogate, U+2028/U+2029 --
`core/safeout.py::is_control`) surviving into either would drive the operator's terminal rather
than print to it. `core/safeout.py::escape_for_terminal` is the one function both chokepoints
call. `cli.py::main` installs it as a stream wrapper (`safeout.installed()`) over stdout and
stderr for the whole invocation, covering every `print` call including an uncaught traceback --
caught INSIDE that same context manager and turned into `SystemExit(1)`, never via a
`sys.excepthook`, because a hook installed and restored alongside the wrapper is inert (the
`finally` restores it during unwinding, before the interpreter would call it, so the raw
traceback reaches the terminal first). `core/log.py::get_logger` is the second, independent
chokepoint, and it is NOT redundant with the wrapper: `logging.StreamHandler` binds its stream at
construction, and importing `sluice.cli` builds loggers before `main()` installs the wrapper, so
those handlers hold the ORIGINAL stderr and the wrapper never sees their records -- an escaping
`Formatter` covers them instead. Both leave two characters alone, deliberately. `\t` is never
escaped: it advances to the next tab stop and cannot recolour, reposition, hide output or reach
the clipboard, and it is load-bearing -- `audit_flags`/`voice_flags` (`cli.py::cmd_cv_run`) are
tab-separated columns a blanket strip would destroy. `\n` is never escaped either, and that IS a
stated residual: an injected newline forges an extra output line. It is bounded -- hiding or
repositioning prior output needs CR or ESC, and both are escaped -- and only the call site could
tell an injected newline from sluice's own formatting. `apply/packet.py::render_json` must stay on
`json.dumps`'s default `ensure_ascii=True`: at `ensure_ascii=False` it emits DEL, the C1 block and
U+2028 raw, since JSON's own escaping rule covers C0 only, and the terminal wrapper would then
rewrite one of those raw bytes into a `\x`/`\u` sequence the JSON it sits inside cannot parse --
turning the one documented machine-readable channel unparseable on a single scraped byte.

## Conventions

- Comments explain *why* — the invariant being upheld, the bug being prevented, the trade-off taken.
  The existing code is dense with them and several encode real incidents; match that density rather
  than stripping it.
- **Never cite a LINE NUMBER in a comment or docstring (#191).** Cite `file.py::symbol`, or the
  file plus the claim quoted so `grep` finds it. A line number rots invisibly: any edit above it
  moves the target while the citation still resolves, so a structural check passes and reads as
  proof. Measured before the rule: of 30 line citations in live code, just ONE pointed at a blank
  line and none past EOF, while at least five resolved to something unrelated — one claimed a
  `set_tailored_cv` call logged an exception, cited identically from three files. That ratio is
  the whole argument for banning the line number rather than range-checking it. `tests/test_citation_drift.py`
  enforces both halves: the ban, and that a `::symbol` names a symbol that exists. It reads prose
  through `tokenize`/`ast` rather than as raw text, because a regex over file bytes flags
  `tests/test_no_leaked_files.py`'s grep-shaped fixture strings — a guard failing on its own
  fixtures is one that gets deleted. `docs/superpowers/{specs,plans}` are out of scope, being
  historical.
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
- The adapter seams (backend, store, renderer, fetcher, rates — the config keys, and the
  `_SEAMS` roster in `core/app.py`, which is what a guard test pins -- state no COUNT of
  them here, that sentence has gone stale once already) are each a name-keyed
  registry resolved via `plugins.get`. The backend seam's self-registering provider
  implementations live in `sluice/backends/` — their names are what each stage's `backend` key and
  a one-run `--backend` select, and `DEFAULT_MODELS` in `core/backends.py` is their roster;
  the RENDERER seam has two self-registering
  production impls — `template` (the default: fills a user's Jinja2 template, or the packaged
  default, via WeasyPrint; `pip install -e '.[render]'`) and `script` (the external shell-out
  escape hatch) — selected by `cv.renderer`, so by-name selection between real implementations is
  already LIVE there. A renderer is handed the assembled `CvDocument` and nothing else (see the
  CV paragraphs above), and `Sluice.compose_cv` constructs no renderer for a `--dry-run`, which
  never renders -- but it still looks `cv.renderer`'s NAME up in the registry, so an unknown or
  retired name fails the preview before any backend call exactly as it fails the real run. Store and fetcher have one production impl each (`vault`,
  `camofox`); the STORE seam has an OPTIONAL member, `preflight() -> dict`, reached via `getattr`
  because an implementation that cannot say is not one that is broken — `job-sluice doctor` (see below) is the one caller, and `Vault.preflight`
  answers with FACTS (vault dir, Judging Profile, a total/verified/pending count for
  each evidence corpus -- #164: `experience` (keeping its pre-#164
  `experience_total`/`experience_verified` names since `doctor` already consumes them),
  `skills`, `stories`, iterated off `EVIDENCE_KINDS` rather than hand-listed -- and, #133/#107,
  whether a candidate name and a contact block are declared), never
  verdicts, keeping classification in `core/doctor.py` where the backend rules already live. The
  selection is also exercised in tests — `tests/harness/` registers a fake fetcher
  (`browser.py`) and renderer (`renderer.py`) and resolves them through the same seam. The backend seam
  differs in shape, though: `Sluice.backend()` resolves the stage's provider (or a one-run override)
  and wraps it in `RetryingBackend`, and its factory takes resolved construction params
  (model/key/base_url), not the config object -- so it does not go through `Sluice._resolve` the way the
  other seams do. Route new implementations through those seams (a self-registering module) rather than
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
