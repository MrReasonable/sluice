# Architecture

## `core/`

Shared by every sub-app:

- `config.py`: layered config. Code defaults, overridden by a `sluice.yaml`
  file, overridden last by environment variables.
- `vault.py`: the lead/experience store. Reads and writes an Obsidian-style
  markdown vault without clobbering status, scores, or notes a human or
  another agent has already set: a fresh scrape touches only a `last_seen`
  marker on an existing note.
- `backends.py`: LLM clients. `ClaudeMaxBackend` shells out to a `claude`
  CLI, local or over SSH; `AnthropicBackend` calls the Anthropic Messages
  API directly; `OpenAiCompatibleBackend` calls any OpenAI-compatible HTTP
  endpoint; `FallbackBackend` tries the first and falls back to the second
  on error; `make_backend` builds any of them by name.
- `camofox.py`: an HTTP client for a Camofox headless-browser server, the
  impure fetch boundary that ingest sources drive a tab through.
- `status.py`: the canonical status vocabulary shared across sub-apps.
  Triage owns the early states (new, shortlist, research, needs_review,
  dismiss); track owns the later ones (applied, phone_screen, ... offer,
  rejected); neither overwrites the other's.
- `seendb.py`: a sqlite dedup store for already-seen leads.
- `resilience.py`: retry-with-backoff, hard timeout, and rate-limit
  precheck helpers that wrap each source's I/O.
- `health.py`, `dossier.py`, `leads.py`, `log.py`, `relevance.py`: health
  reporting, per-lead dossier assembly, the source-agnostic `Lead` model,
  logging, and the relevance gate.

## The five sub-apps

1. **ingest** (`sluice/ingest/`): declarative sources (`base.Source`, split
   into an impure `fetch` and a pure `parse`) driven by `engine.run()`,
   which dedups via `core.seendb`, gates via `core.relevance`, and writes
   through a sink (vault or JSON) to the lead store.
2. **triage** (`sluice/triage/`): `classify.py` resolves obvious cases
   deterministically, for free; only kept, ambiguous leads are enriched
   and sent to an LLM judge (`judge.py`, `prompt.py`, over `core.backends`).
   `apply.py` writes verdicts back, skipping any lead already in the
   application lifecycle; `audit.py` logs every decision.
3. **cv** (`sluice/cv/`): select verified source material, bundle it into
   a closed set, compose a tailored CV against that bundle (an LLM call
   over `core.backends`), validate it against a fabrication gate (a hard
   fail triggers exactly one retry, then the lead is skipped rather than
   rendered ungated), render (shells out to an external script), and serve
   under an opaque, cache-busted filename.
4. **apply** (`sluice/apply/`): select eligible leads, stage the rendered
   CV file and a prep packet, and record the applied transition
   (never-clobber). Actual ATS form submission is human-driven; this
   sub-app prepares the material, it does not drive a browser.
5. **track** (`sluice/track/`): fetch Gmail and Google Calendar since the
   last run, classify each message into an `Event` (refuse rather than
   guess on ambiguity), and reconcile it against lead status
   (never-regress: a status can only move forward).

## The plugin core

Two modules and a composition root make the seams real:

- `core/plugins.py`: the adapter registry. `register(seam, name, factory)` /
  `get(seam, name)`, keyed by the name in config. An unknown name raises and
  lists the valid ones; it never falls through to a default. For the store seam
  that matters most, because a quiet wrong default means writing the user's
  leads somewhere they did not ask for.
- `core/protocols.py`: `Store`, `Fetcher`, `Renderer`. Interface only. `LeadNote`
  carries an opaque `ref` (a path for the vault, a row id for some other store)
  and a `slug` the store issues -- identity used to be re-derived from the
  markdown filename in four separate modules, and that is what pinned the store
  to a filesystem.
- `core/app.py`: `Sluice(config)`, the composition root. Resolves the adapters
  config names -- store, fetcher, renderer, and backend (by ROLE: auto/primary/
  fallback, over whichever provider config selects) -- and OWNS the pipeline
  operations as value-returning methods: `ingest()`, `triage()`, `compose_cv()`,
  `prep()`, `record()`, `track()`, `track_confirm()`, `normalize_statuses()`. It
  also owns the state those operations need that is not itself an adapter: the
  dossier cache (`dossier_cache()`), and track's file-backed seen-message set and
  last-successful-run watermark. Adapters are built lazily on first use, so an
  offline command still never constructs a browser, a store or a backend.

Implementations live in `sluice/stores/`, `sluice/fetchers/`, `sluice/renderers/`
and `sluice/backends/`, each self-registering on import exactly as
`ingest/sources/` already did.

There are two kinds of plugin. An **adapter** plugin is something core calls (a
store, a renderer, a fetcher, a backend); a **surface** plugin is something that
calls core (a web UI, a TUI, a daemon). The registry serves adapters. Surfaces
need a programmatic API to drive, and `Sluice` is that API: it resolves the
adapters AND drives the pipeline, so a surface no longer constructs a `Vault()`
or a `Camofox()`, builds a backend, or duplicates the triage/compose/prep/record/
track wiring itself. `cli.py` is now a thin shell over `Sluice` -- each command
builds one, calls one method, and formats the result for the terminal -- so a web
UI written today has nothing left in `cli.py` worth forking.

`tests/conformance/test_store_contract.py` is parameterised over every registered
store and asserts never-clobber (a re-scrape touches only `last_seen`, and that
marker may only move **forward** — an older re-scrape leaves the newer stored value,
so `last_seen` is monotonic), never-regress, slug/ref identity, and
never-silently-absorb-a-different-opportunity: two jobs with a proven **location**
difference produce two notes, and when identity is uncertain `upsert` returns an
explicit `merged` rather than absorbing the lead silently (#5). `merged` is a
permitted outcome — the property forbids the *silent* absorb, not the merge. Location
is the discriminator, so two distinct long titles that share the 120-char filename
prefix **and** location still merge (a bounded residual; the store cannot see the
truncated title tail).
Location is decided one layer up too: the ingest **read** key (`Lead.dedup_key`, for
URL-less leads) folds in `_norm_location(location)`, so the engine's own dedup does not
collapse two cities *before* the store's split can run (#23). The two layers use two
notions of sameness — an equality hash upstream, token overlap in the store — but the
upstream key can only *over*-split relative to the store, which is the safe direction:
anything the engine lets through, the store re-merges.
Those guarantees used to live inside `core/vault.py`; a second store would have
shipped without them. They are now properties of the contract, and a store passes
that suite or it does not ship.

## Adapter-selector seams

Four points in the config are the seams for pluggable adapters.

- **backend**: `sluice/backends/`, selected by provider name through the adapter
  registry (`make_backend` is now a thin shim over `plugins.get("backend", name)`).
  Implementations: `claude-max` (flat-rate `claude --print` CLI), `anthropic` (direct
  Messages API), `deepseek` and `openai` (OpenAI-compatible). Role selection
  (`auto`/`primary`/`fallback`) sits ABOVE the provider seam, in `Sluice.backend()`:
  the config picks which provider fills each role, the role picks which backend runs.
- **store**: `sluice/stores/`, selected by `store:` (default `vault`).
  Implementations: `vault` (the Obsidian-style markdown vault in
  `core/vault.py`). A SQLite store is the obvious next one, and the
  conformance suite is what it must pass.
- **renderer**: `sluice/renderers/`, selected by `cv.renderer:` (default
  `script`). Implementations: `script` (shells out to the external WeasyPrint
  script at `cv.render_script`) and `weasyprint` (bundled, in-process, needs
  `pip install 'sluice[render]'`). Note the shipped `render_script` default
  points at a file that does not exist in the repo; `script` now says so at
  construction rather than dying after a CV has been composed and gated.
- **fetch**: `sluice/fetchers/`, selected by `fetcher:` (default `camofox`).
  Implementations: `camofox` (the headless-browser HTTP server).
- **sources**: `ingest/sources/`, the registry all of the above are modelled on.

`sluice doctor` is a read-only preflight over the backend seam: it enumerates every
configured backend (primary and fallback, per sub-app), classifies each as
`ok`/`degraded`/`dead`, and exits non-zero when a run-blocking backend is dead. The
classification is role-aware -- a keyless fallback degrades (the sanctioned
primary-only path, exit 0), while a keyed-but-broken backend is `dead` regardless of
role, the silently-non-functional fallback the tool exists to catch. Live round-trip
by default; `--offline` for a config-only check; `--strict` to also fail on degraded.
