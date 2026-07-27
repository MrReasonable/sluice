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
pip install -e ".[test]"        # pytest + faker (the suite needs faker; see Neutrality)
python -m pytest                # fast (well under a second), fully offline: no Camofox, no network
python -m pytest tests/test_triage_engine.py            # one file
python -m pytest tests/test_triage_engine.py -k judge   # one test
ruff check sluice tests scripts         # NB: ruff is NOT in [test]; pip install ruff==0.15.21 (the CI pin)

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

The suite is fast and hermetic — there is no reason not to run all of it. `run_tests.sh` is the same
thing via `.venv/bin/python`, so it needs a `.venv/` (gitignored) to exist first. CI
(`.github/workflows/ci.yml`) runs ruff + zizmor, then pytest on Python 3.12/3.13/3.14.

Running the pipeline:

```bash
cp sluice.yaml.example sluice.local.yaml   # git-ignored
export SLUICE_CONFIG=$(pwd)/sluice.local.yaml
sluice ingest list-sources --health
sluice ingest run --source reed --dry-run  # dry-run/JSON sink never writes vault or seen.db
sluice triage run --no-llm                 # deterministic classify only, no backend call
```

`ingest run` and `ingest test-source` drive a live Camofox browser server; every other command is
offline. `sluice ingest test-source ID --raw` prints the raw fetch payload, which is how golden
parser fixtures get captured.

## Architecture

Pipeline: `ingest -> triage -> cv -> apply -> track`. Five sub-apps under `sluice/`, all sitting on
`sluice/core/`. `docs/ARCHITECTURE.md` has the per-module detail; what follows is what you cannot
see from the file tree.

**Config is layered and single-file.** Code defaults < the YAML file at `$SLUICE_CONFIG` < env vars.
Each sub-app has its own `load_*_config()` reading its own top-level block of that same file
(`triage:`, `cv:`, `apply:`, `track:`); ingest reads the root keys. Every knob has a code default, so
everything runs with no config file at all. New tunables go in the relevant `*Config` dataclass and
`sluice.yaml.example` — never hardcoded in logic.

**The `leads` passes report by default; the pipeline commands write by default.** `leads dedupe`
(`--merge ID [ID ...]`) and `leads expire` (`--expire [SLUG...]`) print and change nothing until
told otherwise, and neither offers `--dry-run` — the default IS the dry run, and a flag that does
nothing is drift. `triage run`/`ingest run`/`track run` invert both halves. The distinguishing
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
a runtime dependency without a deliberate decision.

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
