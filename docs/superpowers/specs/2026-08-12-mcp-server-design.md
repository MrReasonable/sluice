# MCP server — `job-sluice mcp serve`, a second front-end over `Sluice` (#105)

## Problem

Every surface that drives sluice today is `cli.py`: an agent (Claude Code or otherwise) that
wants to inspect leads, check pipeline health, or run `doctor` has to shell out to the CLI and
parse its stdout. `core/app.py:Sluice` already exists specifically to let a **surface** plugin —
"a web UI, a TUI, a daemon" — drive the pipeline programmatically without duplicating any of
`cli.py`'s wiring (see `Sluice`'s own module docstring and `docs/ARCHITECTURE.md`'s surface/adapter
split). No surface exists yet. This adds the first one: a Model Context Protocol server, so an
agent can call `list_leads`/`get_lead`/`doctor`/`health` directly instead of shelling out.

This is a read-only first slice. Write-capable tools (apply, track, dedupe/expire/reconcile, cv
signoff) are deliberately out of scope — deferred until this slice ships and until the write-path
routing rule below is proven out in review.

## The settled decisions

1. **`doctor` defaults to `offline=True`.** `Sluice.doctor()` defaults to `offline=False`, which
   does a live one-token round-trip against every configured backend — real API calls, real
   cost/latency. An agent casually calling an MCP tool must not trigger unbudgeted spend. The MCP
   tool exposes `offline` as a parameter (default `True`); nothing about the read-only slice
   requires the live path, so this keeps it unambiguously free of network/spend side effects by
   default.

2. **`health` routes through a new thin `Sluice` method, not a direct `HealthStore`/registry
   import.** `cli.py`'s `cmd_health`/`cmd_list_sources` bypass `Sluice` entirely today — a
   pre-existing inconsistency with every other command. Rather than have the MCP server repeat
   that bypass (and end up with two different wiring styles depending on which tool you're
   reading), `Sluice` gains `health_report()`, extending its existing report idiom
   (`dedupe_report`/`expire_report`/`reconcile_report`). `cmd_health` is refactored to call it too,
   removing the duplication rather than leaving a second implementation of the same read sitting
   beside the new one.

3. **`list_leads` takes `statuses` and `limit`.** The closest existing primitive,
   `Store.read_leads(statuses: set | None)`, returns everything matching in one call with no cap.
   Real backlogs run 200+ leads (measured: a 265-lead backlog elsewhere in this project's history).
   `list_leads(statuses=None, limit=None)` mirrors `read_leads`'s filter exactly and adds an
   opt-in `limit`/`truncated` pair, so an agent that already suspects a large backlog can cap a
   response deliberately and still be told whether it was cut off. `limit=None` (the default)
   returns everything matching, same as `read_leads` — this is a cap an agent reaches for, not a
   silent default protection; the response's `count` field is always present, so even an
   unnarrowed call tells the caller exactly how many leads it got back.

4. **Module shape: FastMCP, one new sibling module — plain logic functions, thin closures for
   registration.** The SDK's high-level `mcp.server.fastmcp` API (`@mcp.tool()` on a function,
   schema inferred from type hints + docstring) is the right layer for four simple request/response
   reads — the low-level `mcp.server.Server` class (hand-written JSON schemas, a manual dispatch
   table) buys control this slice has no use for. One new module, `sluice/mcpserver.py`, sits beside
   `cli.py` — not a package, not a sixth sub-app under `ingest`/`triage`/`cv`/`apply`/`track`. Four
   tools is too small to justify splitting up front; `cli.py` itself stays one file for a much
   larger surface.

   The four tools' actual logic lives in plain, top-level functions that take `sluice: Sluice` (or
   `store`) as an explicit first parameter — directly callable in a unit test with a fake, no MCP
   machinery involved. `serve(config)` is the only place `mcp` is imported (keeping the `try/except
   ImportError` guard genuinely lazy, per the Dependency section below): it builds one `Sluice`,
   constructs `FastMCP(...)`, and defines a thin, real, `@mcp_server.tool()`-decorated wrapper
   *inside itself* for each tool — a normal nested function that closes over `sluice` and delegates
   to the plain top-level function. This is deliberately NOT `functools.partial`/`functools.wraps`
   composed onto the plain function and registered directly: `functools.wraps` sets `__wrapped__`
   back to the original (unbound) function, and `inspect.signature` — which FastMCP's schema
   inference relies on — follows `__wrapped__` by default, which would put `sluice` back into the
   client-facing tool schema. A real nested function with its own explicit signature, type hints
   and docstring has no such footgun and is the normal way FastMCP's own examples show a tool being
   defined anyway.

5. **"Gate posture" is not a fifth tool.** The issue lists `list_leads`/`get_lead`/`doctor`/
   `health`/"gate posture" as the read-only surface. `Sluice.doctor()`'s own docstring already
   covers "every preference gate's current posture (abstaining or active)" — it's part of the
   `doctor` report today, not a separate read. The real surface is four tools.

## Dependency: the `mcp` extra

`sluice/` is standard-library only except for a short, deliberate exception list in
`.rulesync/rules/CLAUDE.md` (`yaml`, the Google client libs, `jinja2`/`weasyprint`, `argcomplete`),
each guarded and each behind its own `optional-dependencies` extra. This adds one more:

```toml
[project.optional-dependencies]
mcp = ["mcp"]
```

**Also added to the `test` extra**, mirroring `jinja2`'s existing dual membership in `render` AND
`test`: CI installs only `pip install -e ".[test]"` (`.github/workflows/ci.yml`), and layer 2's
`tests/functional/test_mcp_contract.py` (below) imports `mcp` for real to drive the contract test.
Without this, that test either fails collection or gets "fixed" with `pytest.importorskip("mcp")`
— exactly the trap `test_no_test_module_uses_importorskip` (`tests/test_renderer_template.py`,
sweeping all of `tests/`) exists to catch, after it already recurred twice in this repo
(weasyprint, then jinja2).

Imported lazily **inside functions** (`cmd_mcp_serve` in `cli.py`, and the serve function in
`sluice/mcpserver.py`), guarded `try/except ImportError` — matching `jinja2`/`weasyprint`'s
lazy-inside-function shape (not `yaml`'s module-scope-guarded shape), because `mcp` pulls in an
async/network stack that is meaningfully heavier than a config-file parser and has no reason to
load for any command that isn't `job-sluice mcp serve`. A bare `job-sluice` install never imports
it.

**`.rulesync/rules/CLAUDE.md`'s stdlib-only paragraph gets `mcp` added to its enumerated exception
list in this same PR** — not left implicit — naming what it's for, how it's guarded, and which
extra gates it, in the same voice the existing entries use.

## CLI wiring

One new top-level group, same `add_parser`/`add_subparsers`/`set_defaults(func=...)` shape every
other group in `cli.py` already uses:

```
job-sluice mcp serve       run the MCP server (stdio transport)
```

`cmd_mcp_serve(args, config)` lazy-imports `sluice.mcpserver`, calls `sluice.mcpserver.serve(config)`.
A missing `mcp` extra is caught as `ImportError` at this one site and turned into an rc-2 usage
error naming `pip install job-sluice[mcp]` — the same shape `load_config`'s existing malformed-config
`ValueError` already gets, never a raw traceback.

## Architecture

```
job-sluice mcp serve
        │
        ▼
cli.py: cmd_mcp_serve(args, config)
        │  lazy-imports sluice.mcpserver
        ▼
sluice/mcpserver.py: serve(config)
        │  imports mcp HERE (the only place); builds ONE Sluice(config); defines a thin
        │  @mcp_server.tool() closure per tool, each delegating to a plain top-level
        │  function below; mcp_server.run() (stdio)
        ▼
   FastMCP tool dispatch
        │
        ├─ list_leads(sluice, statuses=None, limit=None)  → Sluice.store().read_leads(...)
        ├─ get_lead(sluice, lead: str)                     → Sluice.store().read_leads() + slug_matches
        ├─ doctor(sluice, offline=True)                    → Sluice.doctor(offline=...)
        └─ health(sluice)                                  → Sluice.health_report()   [NEW]
```

`Sluice(config)` is built once, at `serve()` time — matching how every `cmd_*` in `cli.py` builds
exactly one `Sluice(config)` per invocation, not one per operation. Unlike a one-shot CLI
invocation, `mcp serve` is long-running, and `Sluice`'s adapter cache (`self._cache` in
`_resolve`) lives for the process's whole lifetime: an edited `sluice.yaml` is picked up only on
the next `mcp serve` restart, not live — worth stating explicitly since it never came up for a
one-shot CLI command.

### `Sluice.health_report()` (new, `core/app.py`)

Mirrors `cmd_health`'s existing logic as a pure value-returning method, following the same
dataclass-report idiom as `StaleLead`/`DedupeCluster`:

```python
@dataclass
class SourceHealth:
    id: str
    kind: str
    baseline: float
    recent: list        # health.counts(id)
    should_retire: bool

def health_report(self) -> list[SourceHealth]:
    from sluice.core.health import HealthStore
    from sluice.ingest import sources as registry
    health = HealthStore()
    return [SourceHealth(id=src.id, kind=src.kind, baseline=health.baseline(src.id),
                          recent=health.counts(src.id),
                          should_retire=health.should_retire(src.id))
            for src in sorted(registry.all_sources(), key=lambda s: s.id)]
```

`cmd_health` is refactored to call this instead of constructing `HealthStore`/reading the registry
itself. `cmd_list_sources --health` (`cli.py:148-159`) still constructs its own `HealthStore()`
and walks the registry independently — considered and deliberately deferred rather than folded in
here, since it also needs enabled/disabled overlay state that `health_report()` doesn't compute;
not this slice's problem to solve.

### The four tools (`sluice/mcpserver.py`)

Each is a plain top-level function taking `sluice: Sluice` as its first parameter — no MCP
machinery, directly callable from a unit test with a fake. `serve()` wraps each in a thin
`@mcp_server.tool()`-decorated closure (see decision #4) that supplies `sluice` and exposes only
the remaining parameters to the client-facing schema.

- **`list_leads(sluice, statuses: list[str] | None = None, limit: int | None = None)`** — validates
  `statuses` against `core.status.CANONICAL`, raising a tool-level error listing the valid set on
  an unrecognized value (the same "fail loudly, list valid names" convention as an unknown
  backend/adapter/seam — never silently returns `[]` for a typo). Calls
  `sluice.store().read_leads(set(statuses) if statuses else None)`. Returns a **curated per-lead
  summary** — `slug`, `status`, `company`, `role`, `url`, `first_seen`, `last_seen`, and the
  `tailored_cv`/`needs_signoff`/`pending_cv` flags — never the full frontmatter or body, so a
  large backlog can't flood one response. Response shape: `{"leads": [...], "count": N,
  "truncated": bool}`.

- **`get_lead(sluice, lead: str)`** — same parameter name as the CLI's `--lead`. Resolves by substring
  match via `core.leads.slug_matches`, the same helper `compose_cv`/`sign_off_cv` already use —
  never accepts or returns the store's opaque `ref` handle across the MCP boundary; every lookup
  re-resolves by string, exactly like the CLI always has. Three outcomes, matching the
  "never guess an identity" invariant already enforced at every other multi-match site in this
  codebase (`compose_cv`'s `skipped-ambiguous`, `expire`'s `ambiguous`/`no-match`):
  - zero matches → `{"outcome": "not_found"}`
  - two or more matches → `{"outcome": "ambiguous", "candidates": [...slugs...]}` (never picks one)
  - exactly one match → `{"outcome": "found", "slug", "status", "fm": {...}, "body": "..."}` — full
    frontmatter + body, since this is the single-lead detail view.

  `outcome` is deliberately not named `status`, to avoid colliding with the lead's own `status`
  frontmatter field inside the payload.

- **`doctor(sluice, offline: bool = True)`** — calls `sluice.doctor(offline=offline)` and
  serializes the returned `DoctorReport` via `dataclasses.asdict()`. `DoctorReport`/`BackendCheck`/
  `ComponentCheck`/`BackendTarget`/`RoleUse` are already plain dataclasses, and `asdict` recurses
  through the whole tree — the same technique `cmd_test_source` already uses for its own JSON
  output (`cli.py:302`), not a new pattern. The response includes `exit_code` (via
  `report.exit_code()`) so an agent gets the same pass/fail signal a human gets from the CLI's
  process exit code, without parsing text.

- **`health(sluice)`** — calls `sluice.health_report()`, serializes the `SourceHealth` list via
  `asdict()`.

## Error handling

No blanket `try/except` in `sluice/mcpserver.py`. Three categories:

- **Expected structured outcomes** (`not_found`, `ambiguous`, a `dead` doctor check, an empty
  health list) are normal successful tool results, never protocol errors — consistent with how
  the rest of the codebase already treats these (`doctor`'s exit code is data; `compose_cv`'s
  `skipped-ambiguous` is a result row, never an exception the caller must catch).
- **Genuine failures** (a vault I/O error, an unexpected construction failure) propagate as real
  exceptions. FastMCP's own dispatch converts an uncaught exception into a protocol-level tool
  error; nothing here swallows it, matching the codebase's existing discipline of isolating only
  specific, named, recoverable conditions (`VaultConflict`, a per-lead `OSError`) rather than
  catching broadly.
- **The `mcp` extra not installed** is the one place a narrow catch is justified — see CLI wiring
  above.

## Testing

Three layers, mirroring this codebase's existing layer split:

1. **`tests/test_mcpserver.py`** (flat, alongside `test_doctor.py`/`test_health.py`) — the four
   plain top-level functions (`list_leads`, `get_lead`, `doctor`, `health`) called directly against
   an injected `Sluice`/fake `Store`, no protocol machinery, no `serve()` involved. Covers:
   status-filter validation, `limit`/`truncated` behaviour, `get_lead`'s three-way
   not_found/ambiguous/found split, `doctor`'s offline-by-default + `exit_code` passthrough,
   `health_report`'s shape. Offline-hermeticity layer. Also holds the two guard tests from layer 3
   below, since they exercise this same module.

2. **`tests/functional/test_mcp_contract.py`** (mirrors the existing `test_cli_contract.py`
   precedent) — uses the SDK's in-memory `Client` transport (`mcp.Client(mcp_server,
   raise_exceptions=True)` per the SDK's hosted docs) to prove the tools are correctly
   *registered*: `tools/list` returns the right names/schemas — including that `sluice` never
   leaks into a tool's client-facing schema, the property the nested-closure shape in decision #4
   exists to guarantee — and a real `call_tool(...)` round-trips through FastMCP's own dispatch
   into the real functions. No subprocess, no stdio, no network. The exact `Client` API (whether
   `raise_exceptions` exists on the pinned SDK version, whether the older
   `create_connected_server_and_client_session` helper is really gone) is taken from the SDK's
   hosted docs, not yet executed against an installed package — verify it against the actual `mcp`
   version this repo pins before locking this test's shape; do not carry the docs read forward as
   verified.

3. **Two guard tests in `tests/test_mcpserver.py`, NOT modeled on `test_cli_completion.py`'s
   shape.** That shape relies on `argcomplete` being imported at `cli.py`'s MODULE scope, so a test
   can monkeypatch the `cli.argcomplete` attribute to simulate absence. Under decision #4's
   fully-lazy-inside-`serve()` shape, `sluice/mcpserver.py` has no module-level `mcp` attribute to
   patch, so a different technique is needed:
   - `monkeypatch.setitem(sys.modules, "mcp", None)` before calling `serve(config)` (or
     `cmd_mcp_serve`), forcing the real `from mcp.server.fastmcp import FastMCP` line to raise
     `ImportError` without actually uninstalling the package — the standard technique for
     simulating an absent optional import. Asserts `cmd_mcp_serve` degrades to the rc-2 usage
     error, never an uncaught traceback.
   - A static/AST sweep (the same technique `test_no_test_module_uses_importorskip` already uses)
     asserting `sluice/mcpserver.py` and `sluice/cli.py` carry no `mcp` import outside `serve()`'s
     own function body — the same hermeticity property `test_hermeticity.py` polices for network,
     proven structurally rather than trusted to hold because nobody's added one yet.

## Docs

- `.rulesync/rules/CLAUDE.md`'s stdlib-only paragraph: add `mcp` to the exception list (same PR,
  per the issue's own precondition).
- `README.md`: a `job-sluice mcp serve` line beside the other command groups, and a
  `claude mcp add job-sluice -- job-sluice mcp serve` registration snippet — same PR, not deferred;
  the issue's own "Shape" section already names this as the only publish-surface change needed (no
  new PyPI/Docker channel).
- `docs/ARCHITECTURE.md`'s surface/adapter section: its current wording describes the surface
  story in a world where no surface exists yet ("a web UI written today has nothing left in
  `cli.py` worth forking") — this PR is what makes a real surface exist for the first time, so
  that paragraph gets updated to name `sluice/mcpserver.py` as the first one. Same PR, not
  deferred — leaving it as-is would be exactly the "stale docs get believed" failure this review
  discipline exists to catch.

## Definition of done

- `job-sluice mcp serve` starts a stdio MCP server exposing `list_leads`, `get_lead`, `doctor`,
  `health`.
- `mcp` is a new `optional-dependencies` extra, also present in `test` (so CI actually runs the
  contract test); a bare, non-test install never imports it.
- `.rulesync/rules/CLAUDE.md`'s stdlib-only rule documents the `mcp` exception.
- `Sluice.health_report()` exists; `cmd_health` calls it instead of duplicating `HealthStore`/
  registry construction.
- `README.md` and `docs/ARCHITECTURE.md`'s surface/adapter section are updated in this same PR.
- All three test layers pass, including the two guard tests in `tests/test_mcpserver.py`
  confirming `mcp` is importable nowhere outside `serve()`'s own function body, both structurally
  (AST sweep) and behaviourally (a simulated-absent import degrades cleanly to rc-2).

## Out of scope

- Any write-capable tool (apply, track, leads dedupe/expire/reconcile, cv signoff) — deferred
  until this slice ships and the write-path routing rule (route through `Vault.update_fields` +
  `require_status`, never a new write path) is proven out in review.
- Any transport beyond local stdio — auth/scoping for a non-stdio transport is explicitly out of
  scope per the issue, named here only so it isn't assumed away.
- A schema/validation layer beyond what FastMCP infers from type hints — nothing in this slice's
  four tools needs more.

## Changelog

- 2026-08-12: Initial design, via `superpowers:brainstorming`.
- 2026-08-12: Revised after `/review-plan` (5 reviewers: 1 Critical, 1 High, 3 Medium, 4 Low).
  Fixes: `mcp` added to the `test` extra too (Critical — the plan's own contract test would not
  have run in CI as written); the module-shape self-contradiction between decision #4 and the
  Testing section (High, independently caught by two reviewers) resolved by moving to plain
  top-level logic functions plus thin nested closures for registration, which also fixed layer
  3's guard-test design (it could no longer mirror `test_cli_completion.py`'s shape once nothing
  is imported at module scope). Folded in: doc updates moved into this PR's Definition of Done,
  the layer-3 test file named, `list_leads`'s truncation rationale reworded to match its actual
  default behaviour, and one-sentence notes added on config-restart semantics and the deferred
  `list-sources`/`health_report` duplication.
