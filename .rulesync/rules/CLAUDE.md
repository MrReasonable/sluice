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
pip install -e ".[test]"        # pytest + faker + pytest-cov (all dev-time; see Neutrality)
python -m pytest                # fast (well under a second), fully offline: no Camofox, no network
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
(`.github/workflows/ci.yml`) runs four jobs: `lint` (ruff + zizmor), `test` (pytest on Python
3.12/3.13/3.14), `rulesync` (regenerates `.rulesync/`'s outputs and fails the build on any drift
or hand-edited generated file), and `ci-success`, the aggregate gate over the first three.

Running the pipeline:

```bash
export SLUICE_CONFIG="$(pwd)/sluice.local.yaml"  # git-ignored; quoted for paths with spaces
sluice init --no-input --vault ./vault           # writes the config + a Judging Profile
sluice ingest list-sources --health
sluice ingest run --source reed --dry-run  # dry-run/JSON sink never writes vault or seen.db
sluice triage run --no-llm                 # deterministic classify only, no backend call
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

`sluice init` (#8) exists to remove that trap: it renders the config FROM the question catalogue
with every unanswered key COMMENTED, so an unanswered run writes a file that is field-for-field
equal to no config at all EXCEPT `vault_dir` — the wizard's one required answer, and the one
difference `tests/test_onboard_plan.py` exempts by name. It never overwrites an artefact, so
re-running is safe. The example file stays a catalogue to read, not a template to copy, and
`tests/test_no_copy_instruction.py` fails the build if any shipped doc goes back to instructing
the copy.

`ingest run` and `ingest test-source` drive a live Camofox browser server; every other command is
offline. `sluice ingest test-source ID --raw` prints the raw fetch payload, which is how golden
parser fixtures get captured.

## Architecture

Pipeline: `ingest -> triage -> cv -> apply -> track`. Five sub-apps under `sluice/`, all sitting on
`sluice/core/`, plus `sluice/onboard/` — a COMMAND package for `sluice init`, not a sixth sub-app:
nothing downstream imports it and it sits beside the pipeline rather than inside it.
`docs/ARCHITECTURE.md` has the per-module detail; what follows is what you cannot see from the file
tree.

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
a list rather than change a hundred notes. (`docs/ARCHITECTURE.md` has the per-pass mechanics.)

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

`sluice leads reconcile` (#1) is the one pass that MOVES a note, and a move writes no note bytes —
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
created — a visible duplicate, the direction to fail in. `_merged/` is load-bearing retention, not
scratch: do not prune it. The lead scan is recursive (#1), so `_merged/` is excluded from it BY NAME
(`_PRIVATE_SUBDIRS`, at the TOP LEVEL only) rather than by the accident that a flat `os.listdir` never descended into
it -- deleting that prune resurfaces every archived loser and undoes this invariant outright.
See `core/protocols.py`, `docs/ARCHITECTURE.md`, and
`tests/conformance/test_store_contract.py::test_merged_away_lead_is_never_recreated`.

**Never-regress (status).** One `status` frontmatter key, two lifecycles with separate owners
(`core/status.py`). Triage owns `new/shortlist/research/needs_review/dismiss` and may rewrite them;
track owns `applied/phone_screen/.../rejected` and triage must never touch a lead that has entered
that lifecycle. Status only moves forward on the ladder; terminals are never advanced out of.
`shortlist -> applied` is the only transition apply may make on send; track makes the same
transition when a confirmation receipt arrives (`track/receipt.py`, #10) — both route
through the one `can_apply` predicate (`can_transition` dispatches a `--to applied` request to it,
since `track confirm` accepts an arbitrary target), so apply-on-send and track-on-receipt are the
sole crossings into the application lifecycle; every later move is an on-ladder `can_advance` step.
A receipt auto-advances only under the FULL guard set — a `proof`-tier match (the sender host is the
lead's own host, on a message whose `Authentication-Results` records a PASS aligned with that
sender, and neither host multi-tenant: no ATS relay, no job board sluice scrapes), the lead present
in `shortlist_by_slug`, `can_apply`, and `confidence >= auto_apply_min`. Every weaker outcome
proposes to the dead-letter for a human, because a wrong `applied` silently suppresses a real
application and is irreversible.
An unrecognized status is passed through untouched rather than silently rewritten.
`sluice leads expire` (#9) writes `dismiss` — triage-owned, so never-regress permits it — and never
a `_TERMINAL`, since every terminal is application-owned. It reads only
`TRIAGE_OWNED - {"dismiss"}` (derived, never hand-listed, so the set cannot name an
application-owned state) and passes that same set as `require_status`, which is what actually holds
the invariant when a lead enters the application lifecycle mid-sweep. It is NOT unconditional: a
lead holding a `pending_cv` sign-off hold (#60) is refused, because dismissing it silently discards
a composed CV no human has signed off. Any second bulk-dismiss path must refuse the same.

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

**The CV fabrication gate is hard.** `cv/validate.py` is pure and deterministic: every WORK bullet
must cite a real bundle `[id]` and every number in a bullet must appear in a cited entry; the PROFILE
prose (which has no per-bullet citations) has a bundle-wide numeric floor — a figure present nowhere in
the bundle is a violation, citations stripped with render's exact `_CITE_RE` — and a composed CV
missing the exact `WORK EXPERIENCE`/`PROFILE` headers fails closed, since the section-keyed checks
would otherwise silently not run. A non-empty violation list blocks rendering; the engine retries
composition exactly once with the violations fed back, then skips the lead — a CV is never rendered
ungated. Above this hard gate sits a softer, human-facing layer (#60, on by default via
`cv.require_signoff`): an advisory LLM audit (`cv/audit.py`) catches the qualitative fabrication the
deterministic gate cannot, and an `unsupported` flag WITHHOLDS the send-ready `tailored_cv` pointer (status `needs-signoff`, via
`Store.sign_off`/`hold_for_signoff`, cleared by `sluice cv signoff`) rather than blocking rendering —
it never touches the pure hard gate.

**Neutrality: no personal data in this repo.** No employer names, role preferences, locations,
contact details, hostnames, or absolute paths in `sluice/` or `tests/`. The judge's criteria are read
at runtime from the user's vault (`Job Applications/Judging Profile.md`), never from source. Tests
generate synthetic job titles with seeded `faker` (`tests/conftest.py`) rather than hardcoding
anyone's taste. Personal values reach the code only through `sluice.local.yaml` and the vault.

**`sluice/` is standard-library only.** The sole exceptions: `yaml`, imported under a guarded
`try/except ImportError` in each config module, and the Google client libraries, imported lazily
inside functions in `track/google_client.py`. HTTP goes through `urllib`, not `requests`. Do not add
a runtime dependency without a deliberate decision. The rule binds `sluice/` -- what ships to a
user. The root `package.json` is not an exception to it: it pins the Node-based `rulesync` CLI
that regenerates `.rulesync/`'s AI-tool outputs, a CI-only dev-time tool that never ships in the
package and nothing a user installing `sluice` ever sees. Neither is the `test` extra
(`pytest`, `faker`, `pytest-cov`) -- installed to run the gate, never imported by `sluice/`. That
one needs saying because it is the case the table itself disguises: `test` sits beside `render`
and `google` in the same `optional-dependencies`, and those two ARE inside the rule, because a
user who opts into them installs them and `sluice/` imports them at runtime. The line is not
"optional", it is whether a user's install can end up executing it.

**Fail loudly at construction.** An unknown backend/adapter name raises and lists the valid names
rather than falling through to a default. A quiet wrong default is the bug class this codebase most
consistently engineers out; see `_select_backend`'s guard in `cli.py`.

## Conventions

- Comments explain *why* — the invariant being upheld, the bug being prevented, the trade-off taken.
  The existing code is dense with them and several encode real incidents; match that density rather
  than stripping it.
- Conventional commits (`fix(triage): ...`, `ci: ...`, `docs: ...`).
- Tests assert on behaviour, not merely that code runs. Fixtures stay synthetic.
- The four adapter seams (backend, store, renderer, fetcher — the config keys, and the
  `_STORE_SEAM`/`_FETCHER_SEAM`/`_RENDERER_SEAM` constants in `core/app.py`) are each a name-keyed
  registry resolved via `plugins.get`. The backend seam has four self-registering provider
  implementations (`anthropic`/`openai`/`claude-max`/`deepseek` in `sluice/backends/` — the names a
  config `primary_backend`/`fallback_backend` selects; `claude-max`/`deepseek` ALSO survive as
  deprecated `--backend` role aliases, which is the separate role concern below, not a second registry);
  the RENDERER seam has two self-registering
  production impls — `script` (the default external shell-out) and `weasyprint` (the bundled in-process
  one, `pip install 'sluice[render]'`) — selected by `cv.renderer`, so by-name selection between real
  implementations is already LIVE there; store and fetcher have one production impl each (`vault`,
  `camofox`). The selection is also exercised in tests — `tests/harness/` registers a fake fetcher
  (`browser.py`) and renderer (`renderer.py`) and resolves them through the same seam. The backend seam
  differs in shape, though: a role layer (auto/primary/fallback, in
  `Sluice.backend()`) sits above the provider lookup, and its factory takes resolved construction params
  (model/key/base_url), not the config object -- so it does not go through `Sluice._resolve` the way the
  other three do. Route new implementations through those seams (a self-registering module) rather than
  around them.
- `.rulesync/` is canonical. `CLAUDE.md`, `AGENTS.md`, `.claude/` and the other AI-tool outputs are
  generated and gitignored; edit the source, then regenerate.
