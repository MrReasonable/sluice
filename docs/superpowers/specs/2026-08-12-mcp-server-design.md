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
   `list_leads(statuses=None, limit=None)` mirrors `read_leads`'s filter exactly and adds a
   response-side `limit`/`truncated` so an agent can't accidentally pull a multi-hundred-lead dump
   into one tool response without knowing it happened.

4. **Module shape: FastMCP, one new sibling module.** The SDK's high-level `mcp.server.fastmcp`
   API (`@mcp.tool()` on a plain typed function, schema inferred from type hints + docstring) is
   the right layer for four simple request/response reads — the low-level `mcp.server.Server`
   class (hand-written JSON schemas, a manual dispatch table) buys control this slice has no use
   for. One new module, `sluice/mcpserver.py`, sits beside `cli.py` — not a package, not a sixth
   sub-app under `ingest`/`triage`/`cv`/`apply`/`track`. Four tools is too small to justify
   splitting up front; `cli.py` itself stays one file for a much larger surface.

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
        │  builds ONE Sluice(config), registers 4 tools, mcp_server.run() (stdio)
        ▼
   FastMCP tool dispatch
        │
        ├─ list_leads(statuses=None, limit=None)  → Sluice.store().read_leads(...)
        ├─ get_lead(lead: str)                     → Sluice.store().read_leads() + slug_matches
        ├─ doctor(offline=True)                    → Sluice.doctor(offline=...)
        └─ health()                                → Sluice.health_report()   [NEW]
```

`Sluice(config)` is built once, at `serve()` time — matching how every `cmd_*` in `cli.py` builds
exactly one `Sluice(config)` per invocation, not one per operation.

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
itself.

### The four tools (`sluice/mcpserver.py`)

- **`list_leads(statuses: list[str] | None = None, limit: int | None = None)`** — validates
  `statuses` against `core.status.CANONICAL`, raising a tool-level error listing the valid set on
  an unrecognized value (the same "fail loudly, list valid names" convention as an unknown
  backend/adapter/seam — never silently returns `[]` for a typo). Calls
  `store().read_leads(set(statuses) if statuses else None)`. Returns a **curated per-lead
  summary** — `slug`, `status`, `company`, `role`, `url`, `first_seen`, `last_seen`, and the
  `tailored_cv`/`needs_signoff`/`pending_cv` flags — never the full frontmatter or body, so a
  large backlog can't flood one response. Response shape: `{"leads": [...], "count": N,
  "truncated": bool}`.

- **`get_lead(lead: str)`** — same parameter name as the CLI's `--lead`. Resolves by substring
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

- **`doctor(offline: bool = True)`** — calls `Sluice(config).doctor(offline=offline)` and
  serializes the returned `DoctorReport` via `dataclasses.asdict()`. `DoctorReport`/`BackendCheck`/
  `ComponentCheck`/`BackendTarget`/`RoleUse` are already plain dataclasses, and `asdict` recurses
  through the whole tree — the same technique `cmd_test_source` already uses for its own JSON
  output (`cli.py:302`), not a new pattern. The response includes `exit_code` (via
  `report.exit_code()`) so an agent gets the same pass/fail signal a human gets from the CLI's
  process exit code, without parsing text.

- **`health()`** — calls `Sluice.health_report()`, serializes the `SourceHealth` list via
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
   tool functions called as plain Python callables against an injected `Sluice`/fake `Store`, no
   protocol machinery. Covers: status-filter validation, `limit`/`truncated` behaviour, `get_lead`'s
   three-way not_found/ambiguous/found split, `doctor`'s offline-by-default + `exit_code`
   passthrough, `health_report`'s shape. Offline-hermeticity layer.

2. **`tests/functional/test_mcp_contract.py`** (mirrors the existing `test_cli_contract.py`
   precedent) — uses the SDK's real in-memory `mcp.Client(mcp_server, raise_exceptions=True)`
   transport (the SDK's older `create_connected_server_and_client_session` helper was removed in
   favour of this — confirmed against the current SDK docs, not assumed) to prove the tools are
   correctly *registered*: `tools/list` returns the right names/schemas, and a real
   `call_tool(...)` round-trips through FastMCP's own dispatch into the real functions. No
   subprocess, no stdio, no network.

3. **A guard test mirroring `tests/test_cli_completion.py`'s exact shape** — monkeypatches the
   `mcp` import to absent and asserts `cmd_mcp_serve` degrades to the rc-2 usage error rather than
   a raw `ImportError` traceback, plus asserts that importing `sluice.cli`/`sluice.mcpserver` at
   all never requires the `mcp` package — the same hermeticity property `test_hermeticity.py`
   already polices for network, applied to this new optional import.

## Docs

- `.rulesync/rules/CLAUDE.md`'s stdlib-only paragraph: add `mcp` to the exception list (same PR,
  per the issue's own precondition).
- `README.md`/`docs/ARCHITECTURE.md`: a `job-sluice mcp serve` line beside the other command
  groups, and a `claude mcp add job-sluice -- job-sluice mcp serve` registration snippet, once this
  ships — the issue's own "Shape" section already names this as the only publish-surface change
  needed (no new PyPI/Docker channel).

## Definition of done

- `job-sluice mcp serve` starts a stdio MCP server exposing `list_leads`, `get_lead`, `doctor`,
  `health`.
- `mcp` is a new `optional-dependencies` extra; a bare install never imports it.
- `.rulesync/rules/CLAUDE.md`'s stdlib-only rule documents the `mcp` exception.
- `Sluice.health_report()` exists; `cmd_health` calls it instead of duplicating `HealthStore`/
  registry construction.
- All three test layers above pass; `tests/test_hermeticity.py`-style guard confirms no command
  outside `mcp serve` ever imports `mcp`.

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
