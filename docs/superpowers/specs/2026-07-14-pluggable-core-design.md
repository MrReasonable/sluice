# Pluggable core: adapter registry + composition root

**Date:** 2026-07-14
**Status:** accepted

## The problem

`docs/ARCHITECTURE.md` names four adapter seams — backend, store, renderer, fetch — and says of
them: *"Today each one has exactly one implementation; there is no runtime selector because there is
nothing yet to select between."*

That was true, and it is now the thing blocking everything else. A web UI, a SQLite store, a direct
LLM API backend, a bundled renderer: each is currently a fork of `cli.py` rather than a plugin,
because `cli.py` **constructs the implementations itself**, twelve times:

```
cli.py:132,153   Camofox()          cli.py:133       SeenDb()
cli.py:138,189,206,370,404,440,506,528   Vault()
cli.py:211,372   DossierCache(...)  cli.py:340       Camofox()  (inside the dossier fetcher)
```

Two of the five seams already work. **Sources** are real plugins (`register()` at import, `pkgutil`
auto-load, a broken plugin logged and skipped rather than sinking the registry). **Backends** are
half-way (`make_backend(name, ...)` builds by name; config selects by role). The other three —
store, renderer, fetch — have no registry at all.

The good news is that this is a **wiring** problem, not a logic problem. Every engine already takes
its dependencies as parameters:

```python
ingest.engine.run(sources, ctx, sink, seen, health)
triage.engine.run(vault, cfg, backend, dossier_cache, audit, ...)
cv.engine.run_one(note, vault, cvcfg, backend, dossier_cache, ...)
apply.engine.record_one(vault, cfg, slug, ...)
track.engine.run(vault, cfg, client, backend, ...)
```

The engines are already written for pluggability. The coupling lives entirely in `cli.py`.

## Two kinds of plugin

This distinction drives the design.

- **Adapter plugins** are things core *calls* to do a job it already knows it needs: store, fetch,
  renderer, backend. Swapping one changes *how* a step happens. A name-keyed registry plus a
  `Protocol` per seam covers them.
- **Surface plugins** are things that *call core*: a web UI, a TUI, a daemon, a new subcommand.
  A registry does nothing for these. A surface needs a **programmatic API** to drive — and sluice has
  none. Its API today is `cli.py` functions with the signature `(args, config)` that construct their
  own dependencies and print to stderr. A web UI cannot drive that.

So "a web UI could be a plugin" is only true if core grows a **façade**. That is half of this spec.

### Why not pluggy

Pluggy's model is 1:N broadcast-and-aggregate. These seams are 1:1 select-by-name. Expressing
selection in pluggy (`firstresult=True` plus a `if config.store != "vault": return None` check
duplicated in every plugin) makes "which store is live?" a function of *registration order* rather
than of config — for a component whose entire job is deciding where the user's data gets written.
A dict lookup that raises on an unknown name is both deterministic and consistent with the repo's
existing fail-loudly-at-construction rule. Pluggy would also be the first unguarded third-party
import in `sluice/`, against the stdlib-only discipline.

Pluggy would earn its place for genuine 1:N extension points (lifecycle hooks, multi-channel notify
fan-out). Sluice has none today, and fan-out is better served by a real message bus than by a plugin
system. Revisit if that changes; the two compose fine.

## Architecture

Three new modules in `core/`, none large:

- **`core/plugins.py`** — the registry. `register(seam, name, factory)`, `get(seam, name)`,
  `available(seam)`. Generalises the pattern `ingest/sources/__init__.py` already proves. An unknown
  name **raises and lists the available names**, matching `make_backend`'s existing guard rather than
  falling through to a default.
- **`core/protocols.py`** — `Store`, `Fetcher`, `Renderer` as `typing.Protocol`. Interface only.
- **`core/app.py`** — `Sluice(config)`, the composition root. Resolves the adapters config names,
  owns the wiring currently smeared through `cli.py` (backend role selection, the lazy dossier
  fetcher, the seen/lastrun files), and exposes operations as **value-returning** methods.

Implementations live in per-seam packages that self-register on import, mirroring `ingest/sources/`:
`sluice/stores/`, `sluice/fetchers/`, `sluice/renderers/` (and `sluice/backends/`, added in Stage 2 —
see the superseded non-goal below). `core/vault.py` **stays where it is** — `stores/vault.py` registers
it. This is registration, not relocation.

**The engines do not change.** That is what makes this affordable.

## The Store contract

Eight methods, and one substantive change: `path: str` becomes an **opaque `ref`** that only the
store interprets.

```python
@dataclass
class LeadNote:
    ref: object      # opaque store handle (a path for VaultStore; a row id for a future SqliteStore)
    slug: str        # stable identity, ISSUED BY THE STORE
    fm: dict
    body: str
    status: str
```

### Why `slug` must be store-issued

Four modules independently re-derive the lead's identity from the markdown **filename**:

```
apply/select.py:33   apply/engine.py:23   track/classify.py:31   track/engine.py:49
    os.path.basename(note.path)[:-3]
```

`slug_matches` also falls back to substring-matching `note.path`. This is what actually pins the
store to a filesystem: a SQLite store has no filenames. Making `slug` a first-class field the store
issues collapses four copies of a path-parsing helper into `note.slug`, and is most of the seam.

`VaultStore` derives the slug from the filename exactly as today, so behaviour is preserved.

### The protocol

```python
class Store(Protocol):
    def read_leads(self, statuses: set | None = None) -> list[LeadNote]: ...
    def upsert(self, lead: Lead) -> str: ...
    def update_fields(self, ref, fields: dict, *, append_note=None, note_tag=None) -> None: ...
    def append_body_section(self, ref, tag: str, section_md: str) -> bool: ...
    def set_tailored_cv(self, ref, value: str) -> None: ...
    def read_experience_entries(self, verified_only: bool = True) -> list[dict]: ...
    def read_baseline(self) -> str: ...
    def existing_keys(self) -> set[str]: ...
    def normalize_all_statuses(self, dry_run: bool = False) -> dict: ...
```

Two deliberate omissions:

- **`ensure_stfolder()` is gone.** It writes a Syncthing marker file — pure Obsidian trivia — and
  `cli.py` calls it today. `VaultStore` does it to itself on init; no other store knows the concept
  exists.
- **`read_baseline(rel=...)` loses its path argument.** Where the baseline CV lives is the store's
  business, configured on the store, not passed in by a caller who should not know paths exist.

Because the engines only ever *pass `note.path` back* and never parse it (once the four slug helpers
die), this change is mechanical at every call site.

## The Fetcher and Renderer contracts

```python
class Fetcher(Protocol):          # what ingest sources drive; today Camofox
    def create_tab(self, url: str) -> str | None: ...
    def evaluate(self, tab: str, js: str) -> dict: ...
    def scroll(self, tab: str, amount: int) -> None: ...
    def close_tab(self, tab: str) -> None: ...

class Renderer(Protocol):
    def render(self, cv_text: str, out_dir: str, *, neutral_name: str) -> str: ...
```

The renderer seam also **fixes a live bug**: `cv/config.render_script` defaults to
`./scripts/cv_render_v2.py`, which **does not exist in the repo**. Every fresh clone's `sluice cv run`
fails at the last step, and it fails at call time rather than at construction. Two implementations
ship: `ScriptRenderer` (today's shell-out, still the default, so no behaviour change) and
`WeasyPrintRenderer` (in-process, using the `weasyprint` extra already declared in `pyproject.toml`).
`ScriptRenderer` now validates its script exists **at construction**, per the fail-loudly rule.

## Config and selection

Selection is by name, in config, with a code default that preserves today's behaviour exactly:

```yaml
store:    vault        # the only implementation today
fetcher:  camofox
cv:
  renderer: script     # or: weasyprint
```

An unknown name raises at construction and lists the valid names. It never falls through to a
default — that is the bug class this codebase most consistently engineers out.

## Conformance suites: how the invariants survive

**This is the part that must not be skipped.** Never-clobber and never-regress currently live inside
`core/vault.py`. Once the store is pluggable they cannot live in one implementation; they have to
become a property of the **contract**.

`tests/conformance/test_store_contract.py` is parameterised over every registered store and asserts
the behaviour, not the implementation:

- a re-scrape of an existing lead touches **only** `last_seen` (status, scores, enrichment and body
  are byte-for-byte unchanged)
- `update_fields` sets exactly the named keys and leaves the body byte-for-byte intact
- `read_leads(statuses=...)` filters on the **normalised** status
- an unrecognised status is passed through untouched, never rewritten
- `slug` is stable across reads and unique per lead
- `ref` round-trips: a `ref` from `read_leads` is accepted by every write method

Any future store passes this suite or it does not ship. That is the whole point: the invariants stop
being a property of the vault and become a property of *being a store*.

## Status after implementation (2026-07-15)

Adversarial review of the first cut found that the composition root **resolved adapters
but did not own the operations**: `_build_backend`, `_dossier_fetcher` and the
seen/lastrun handling remained in `cli.py`, so a web UI would still have had to
duplicate that wiring. The "surface plugins are now possible" claim in this spec was
therefore ahead of the code at that point.

That gap is now closed. `Sluice` owns backend role-selection, the dossier cache, and
track's seen/lastrun state, and exposes the pipeline itself as value-returning methods --
`ingest()`, `triage()`, `compose_cv()`, `prep()`, `record()`, `track()`,
`track_confirm()`, `normalize_statuses()`. `cli.py` was shrunk to match: every `cmd_*`
function now builds a `Sluice(config)`, calls one method, and formats the result: it owns
argument parsing and printing, nothing else. The per-command backend-construction
wrappers (`_build_backend`, `_build_compose_backend`, `_track_backend`) and the lazy
dossier-fetcher closure are deleted; their behaviour lives in `Sluice.backend()` and
`Sluice.dossier_cache()`. A surface can now drive the full pipeline through `Sluice`
without forking anything out of `cli.py` -- the "a surface is a plugin" claim this spec
originally made is true rather than aspirational.

## Non-goals

- No SQLite store. The seam is the deliverable; a second implementation is a separate piece of work
  and is what will prove the contract is right.
- No web UI. Adapter resolution AND the operation façade (`Sluice.triage()` and friends)
  are both available now, but building an actual web UI on top of `Sluice` is separate
  work this spec does not do.
- No entry-point discovery / third-party plugins. Internal seams, in-tree implementations. Promoting
  to entry-point discovery later is additive (`importlib.metadata` is stdlib, ~5 lines).
- Sources and backends keep their existing registries. They work; churning them buys nothing.
  _(Superseded for backends by Stage 2, 2026-07-15: the backend provider registry was unified
  into `core/plugins.py` via `sluice/backends/`; `make_backend` became a shim over it. Sources
  keep their own registry.)_

## Testing and migration

The bar for this refactor: **the existing suite passes unchanged.** It is a wiring change, so a test
that has to be edited to accommodate it is evidence of a behaviour change that needs justifying.
New tests cover the registry (unknown name raises and lists), the façade (resolves what config
names), and the store conformance suite above.
