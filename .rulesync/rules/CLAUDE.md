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
`npx rulesync@9.6.3 generate -t '*' -f '*'` after cloning to populate the AI-tool outputs
(`CLAUDE.md`, `AGENTS.md`, `.claude/`, ...), all of which are gitignored. Editing a
generated file instead of the `.rulesync/` source is drift.

## Commands

```bash
pip install -e ".[test]"        # pytest + faker (the suite needs faker; see Neutrality)
python -m pytest                # fast (well under a second), fully offline: no Camofox, no network
python -m pytest tests/test_triage_engine.py            # one file
python -m pytest tests/test_triage_engine.py -k judge   # one test
ruff check sluice tests         # NB: ruff is NOT in [test]; pip install ruff==0.15.21 (the CI pin)

# Run ONCE before mutation testing: content-addresses sluice/'s .pyc cache so a mutant can't run
# stale bytecode and lie green. Proving a test fails is the mutate-then-pytest step; see below.
python -m compileall -q -f --invalidation-mode checked-hash sluice tests
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
  line above makes `sluice/`'s cache content-addressed, which is what mutation testing needs, since
  mutants go in production code. Measured: 90/90 stay hash-based across mutate → pytest → restore,
  so it is durable and costs nothing measurable. Clearing `__pycache__` also works but is a
  discipline you must remember every time, and forgetting it fails in the dangerous direction.

  It does **not** cover `tests/`: pytest's assertion rewriter keeps its own
  `*-pytest-N.N.N.pyc` alongside, those are timestamp-based, and pytest imports *those*. So a
  size-preserving edit to a TEST file within the same second is still exposed. That is not the
  mutation-testing case, but do not read the line above as protecting more than it does.

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
only wholesale write. Rewriting notes wholesale is the exact fragility sluice exists to remove
(`core/vault.py`).

**Never-regress (status).** One `status` frontmatter key, two lifecycles with separate owners
(`core/status.py`). Triage owns `new/shortlist/research/needs_review/dismiss` and may rewrite them;
track owns `applied/phone_screen/.../rejected` and triage must never touch a lead that has entered
that lifecycle. Status only moves forward on the ladder; terminals are never advanced out of.
`shortlist -> applied` is the *only* transition apply may make. An unrecognized status is passed
through untouched rather than silently rewritten.

**Empty config means abstain, not match-nothing.** Every preference gate (`accept_titles`,
`target_locations`, `reject_companies`, `relevance_keep`/`relevance_drop`, pay floors) defaults to
empty/zero, and an unconfigured gate passes every lead through. Getting this backwards silently bins
someone's entire job hunt — it has happened once already (`672ad2a`), and
`tests/test_sluice_neutral_defaults.py` now fails the build if it recurs.

**The CV fabrication gate is hard.** `cv/validate.py` is pure and deterministic: every WORK bullet
must cite a real bundle `[id]`, and every number in a bullet must appear in a cited entry. A non-empty
violation list blocks rendering. The engine retries composition exactly once with the violations fed
back, then skips the lead — a CV is never rendered ungated.

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
- The four adapter seams (backend, store, renderer, fetch) are each a name-keyed registry resolved via
  `plugins.get`. The backend seam has four provider implementations (claude-max/anthropic/deepseek/openai)
  selected by name; store, renderer, and fetch have one each today and no runtime selection is exercised
  yet. The backend seam differs in shape, though: a role layer (auto/primary/fallback, in
  `Sluice.backend()`) sits above the provider lookup, and its factory takes resolved construction params
  (model/key/base_url), not the config object -- so it does not go through `Sluice._resolve` the way the
  other three do. Route new implementations through those seams (a self-registering module) rather than
  around them.
- `.rulesync/` is canonical. `CLAUDE.md`, `AGENTS.md`, `.claude/` and the other AI-tool outputs are
  generated and gitignored; edit the source, then regenerate.
