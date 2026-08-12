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
   Real backlogs can run into the hundreds of leads over a multi-month search.
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
   to the plain top-level function. This is deliberately NOT `functools.partial` (optionally
   combined with `functools.wraps`) composed onto the plain function and registered directly. Two
   separate problems close off that path: a bare `functools.partial(list_leads, sluice)` correctly
   hides `sluice` from `inspect.signature` — which FastMCP's schema inference relies on — but has
   no `__name__`/`__doc__` of its own, so FastMCP can't infer the tool's name/description without
   an explicit override; adding `functools.wraps(list_leads)` to fix THAT sets `__wrapped__` back
   to the ORIGINAL (unbound) function, and `inspect.signature` follows `__wrapped__` by default,
   putting `sluice` right back into the client-facing schema — the exact leak the bare partial
   avoided. A real nested function with its own explicit signature, type hints and docstring has
   neither problem and is the normal way FastMCP's own examples show a tool being defined anyway.

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

`mcp` itself is imported in exactly ONE place: inside `serve()`'s own function body in
`sluice/mcpserver.py` — not in `cmd_mcp_serve`, which only lazy-imports the `sluice.mcpserver`
MODULE (zero `mcp` imports of its own). `serve()` wraps that one import in a
`try/except ImportError`, matching `jinja2`/`weasyprint`'s lazy-inside-function shape (not
`yaml`'s module-scope-guarded shape) — because `mcp` pulls in an async/network stack that is
meaningfully heavier than a config-file parser and has no reason to load for any command that
isn't `job-sluice mcp serve` — and re-raises it as a distinct `McpNotInstalled` exception (defined
in `sluice/mcpserver.py`), which `cmd_mcp_serve` catches specifically (see CLI wiring), rather
than letting the bare `ImportError` propagate for `cmd_mcp_serve` to catch broadly. A bare
`job-sluice` install never imports it.

**`.rulesync/rules/CLAUDE.md`'s stdlib-only paragraph gets `mcp` added to its enumerated exception
list in this same PR** — not left implicit — naming what it's for, how it's guarded, and which
extra gates it, in the same voice the existing entries use.

## CLI wiring

One new top-level group, same `add_parser`/`add_subparsers`/`set_defaults(func=...)` shape every
other group in `cli.py` already uses:

```
job-sluice mcp serve       run the MCP server (stdio transport)
```

`cmd_mcp_serve(args, config)` lazy-imports `sluice.mcpserver` (an unguarded import — the module
itself carries no `mcp` dependency of its own), then calls `sluice.mcpserver.serve(config)` INSIDE
a `try/except mcpserver.McpNotInstalled` — a distinct exception type, not a bare
`except ImportError`. `serve()` catches the `mcp` import's `ImportError` itself, at the single
site it can occur, and re-raises it as `McpNotInstalled`; nothing else `serve()` does (building
`Sluice`, constructing `FastMCP`, or the tool-dispatch loop `mcp_server.run()` enters for the rest
of the process's life) can raise that specific type. A broad `except ImportError` around the
whole `serve(config)` call — including its blocking lifetime — would risk misattributing a
genuinely unrelated `ImportError` (say, from a lazy adapter resolution deep inside a later tool
call) to a missing `mcp` install; the distinct exception type closes that off structurally rather
than resting on an assumption about whether FastMCP isolates per-call exceptions from its caller.
A missing `mcp` extra is caught this way and turned into an rc-2 usage error naming
`pip install job-sluice[mcp]` — the same shape `load_config`'s existing malformed-config
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
one-shot CLI command. Concurrency is the other new dimension a long-lived process introduces:
whether FastMCP's stdio transport can dispatch overlapping tool calls against this one shared
`Sluice` is not verified here. Believed benign for THIS slice regardless of the answer — every
tool is a pure read with no cross-call state, and `_resolve`'s cache is idempotent to rebuild even
if two calls raced to populate it — but this is exactly the assumption the deferred write-tools
slice cannot inherit for free, and should verify FastMCP's actual dispatch model rather than
assume it. This benign-today claim holds only because the adapter factories `_resolve` can
currently reach (`vault`, `camofox`) have no construction-time side effects — an invariant that
exists only in this design document today, not as a comment near `self._cache` in the code, where
a future second adapter implementation would need to see it to avoid silently breaking it. Land a
one-line comment on `Sluice._resolve` in this same PR stating that constraint explicitly.

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

`cmd_health` currently has no test at the CLI level — `tests/test_health.py` tests `HealthStore`
directly, not the command. See Testing (item 4) for the new regression test this refactor needs.

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
  `report.exit_code(strict=False)`, the CLI's own default) so an agent gets the same pass/fail
  signal a human gets from the CLI's process exit code, without parsing text. The CLI's `--strict`
  flag is deliberately NOT mirrored as a tool parameter: the full `DoctorReport` — every check, not
  just the exit code — is already in the response, so an agent can apply its own strictness policy
  over the raw checks directly, without needing a second pre-computed variant.

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
- **The `mcp` extra not installed** is the one place a narrow catch is justified — `serve()`
  catches it at the import site only and re-raises `McpNotInstalled`, which `cmd_mcp_serve` catches
  specifically; see CLI wiring above. Deliberately narrower than an `except ImportError` around
  `serve()`'s whole call would be — see the rationale there.

## Testing

Four test surfaces, mirroring this codebase's existing layer split:

1. **`tests/test_mcpserver.py`** (flat, alongside `test_doctor.py`/`test_health.py`) — the four
   plain top-level functions (`list_leads`, `get_lead`, `doctor`, `health`) called directly against
   an injected `Sluice`/fake `Store`, no protocol machinery, no `serve()` involved. Covers:
   status-filter validation, `limit`/`truncated` behaviour, `get_lead`'s three-way
   not_found/ambiguous/found split, `doctor`'s offline-by-default + `exit_code` passthrough,
   `health_report`'s shape — with AT LEAST TWO synthetic sources, so the `sorted(...,
   key=lambda s: s.id)` ordering claim is actually falsifiable rather than vacuously true on a
   single-element list. Synthetic lead/job data goes through the existing seeded `titles`/
   `cfg_titles` faker fixtures in `tests/conftest.py`, matching the rest of the suite — never a
   hardcoded title. Offline-hermeticity layer. Also holds the two guard tests from item 3 below,
   since they exercise this same module.

2. **`tests/functional/test_mcp_contract.py`** (mirrors the existing `test_cli_contract.py`
   precedent) — uses the SDK's in-memory `Client` transport (`mcp.Client(mcp_server,
   raise_exceptions=True)` per the SDK's hosted docs) to prove the tools are correctly
   *registered*: `tools/list` returns the right names/schemas — including that `sluice` never
   leaks into a tool's client-facing schema, the property the nested-closure shape in decision #4
   exists to guarantee — and a real `call_tool(...)` round-trips through FastMCP's own dispatch
   into the real functions. No subprocess, no stdio, no network. The exact `Client` API (whether
   `raise_exceptions` exists on the pinned SDK version, whether the older
   `create_connected_server_and_client_session` helper is really gone) is taken from the SDK's
   hosted docs, not yet executed against an installed package — and `mcp = ["mcp"]` carries no
   version floor yet either. Both need resolving together before implementation: install, confirm
   the real `Client`/`FastMCP` API against the resolved version, and pin a floor in
   `pyproject.toml` (matching this repo's one precedent, `setuptools>=83.0.0`) or note the
   confirmed shape here. Do not carry the docs read forward as verified.

3. **Two guard tests in `tests/test_mcpserver.py`, NOT modeled on `test_cli_completion.py`'s
   shape** (that shape relies on `argcomplete` being imported at `cli.py`'s MODULE scope, so a
   test can monkeypatch the `cli.argcomplete` attribute — decision #4's fully-lazy-inside-`serve()`
   shape has no such module-level attribute to patch) — and the two prove DIFFERENT things,
   despite both guarding the same overall property:
   - **The AST sweep is the SOLE test enforcing "`mcp` imported nowhere outside `serve()`."**
     Stated per file since `serve()` only exists in one of them: `sluice/cli.py` carries no `mcp`
     import anywhere; `sluice/mcpserver.py` carries no `mcp` import outside `serve()`'s own
     function body. Required shape: collect every `Import`/`ImportFrom` AST node in the file whose
     module name is `mcp` or starts with `mcp.`, and assert each one's enclosing scope is the
     `serve` `FunctionDef` — not merely that `serve`'s body contains at least one such node (a
     stray top-level `import mcp` sitting ALONGSIDE a correctly-guarded one inside `serve()` must
     still fail this). Mutation-tested: add a stray top-level `import mcp`, confirm the sweep (not
     the runtime guard below) goes red.
   - **The runtime guard test proves a different, narrower thing: `cmd_mcp_serve` degrades cleanly
     when `mcp` is genuinely absent — NOT that `mcp` is imported nowhere else.** It cannot prove
     the latter: item 1's tests in this same file import `sluice.mcpserver` unguarded first, so by
     the time this guard test runs the module is already cached, and a stray top-level `import
     mcp` sitting beside `serve()`'s own guarded one would already have executed and succeeded,
     invisibly, before this test's patch is even installed. Uses
     `monkeypatch.setattr(builtins, "__import__", ...)` (pytest's `monkeypatch` fixture, NOT a
     hand-written `try/finally`, matching `test_cli_completion.py`'s restoration convention — a
     botched manual restore of a process-global builtin would break unrelated imports for the rest
     of the pytest session, likely misdiagnosed as flakiness) to patch `builtins.__import__` (NOT
     `sys.modules`) to raise `ImportError` for any name equal to or starting with `mcp`, before
     calling `cmd_mcp_serve`/`serve(config)`. A `sys.modules` sentinel trick
     (`monkeypatch.setitem(sys.modules, "mcp", None)`) was tried and rejected: once `mcp` is a
     genuine `test`-extra dependency (per the Dependency section, needed for item 2's real
     import), `mcp.server.fastmcp` gets cached under its full dotted name somewhere in the same
     pytest session — CPython's import machinery resolves an already-cached full dotted name
     directly, without ever re-checking the parent package's sentinel, so `sys.modules["mcp"] =
     None` silently fails to raise once that caching has happened, with test EXECUTION ORDER (not
     the technique) deciding whether it fires. This was reproduced with a stdlib stand-in
     (`xml.etree.ElementTree`), not assumed. Patching `__import__` intercepts BEFORE any cache
     lookup, so it raises unconditionally regardless of what else has been imported in the
     session. Asserts `cmd_mcp_serve` degrades to the rc-2 usage error, never an uncaught
     traceback — mutation-tested (delete the `except McpNotInstalled` branch, confirm the test
     goes red) once implemented, not trusted on read-through, given how easily the rejected
     technique silently no-oped.

4. **`tests/test_health_cli.py`** (new, matching this repo's existing `test_<command>_cli.py`
   convention — `test_apply_record_cli.py`, `test_leads_dedupe_cli.py`) — asserts `cmd_health`'s
   printed output is unchanged before and after the refactor to call `Sluice.health_report()`,
   since `tests/test_health.py` today tests `HealthStore` directly and never exercises
   `cmd_health` at all.

## Docs

- `.rulesync/rules/CLAUDE.md`'s stdlib-only paragraph: add `mcp` to the exception list (same PR,
  per the issue's own precondition).
- `cli.py`'s own module docstring already lists every top-level command group in a tabular format
  (`job-sluice health   per-source baseline + retire state`) — add `job-sluice mcp serve` there
  too, same PR, matching the existing per-command convention rather than leaving it stale the
  moment this ships.
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
  contract test) with a confirmed version floor pinned in `pyproject.toml`; a bare, non-test
  install never imports it.
- `.rulesync/rules/CLAUDE.md`'s stdlib-only rule documents the `mcp` exception.
- `Sluice.health_report()` exists; `cmd_health` calls it instead of duplicating `HealthStore`/
  registry construction, and `tests/test_health_cli.py` confirms `cmd_health`'s printed output is
  unchanged by the refactor.
- `Sluice._resolve`'s no-construction-time-side-effects constraint is stated as a comment at
  `self._cache` in `core/app.py`, not only in this design doc.
- `README.md`, `cli.py`'s module docstring, and `docs/ARCHITECTURE.md`'s surface/adapter section
  are all updated in this same PR.
- All four test surfaces pass. The AST sweep alone confirms `mcp` is importable nowhere outside
  `serve()`'s own function body (mutation-tested); the runtime guard test separately confirms
  `cmd_mcp_serve` degrades cleanly to rc-2 when `mcp` is absent — two distinct properties, not
  redundant confirmations of one.

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
- 2026-08-12: Revised after a second `/review-plan` (5 reviewers: 1 Critical, 1 High, 4 Medium,
  2 Low). The Critical was empirically-executed, not merely argued: the layer-3 guard test's
  `sys.modules["mcp"] = None` technique was reproduced (with a stdlib stand-in) to silently no-op
  once `mcp.server.fastmcp` is cached elsewhere in the same pytest session — which the previous
  round's own fix (adding `mcp` to `test`) guarantees will happen — replaced with an
  `__import__`-patching technique that doesn't depend on cache state. The High was a leftover
  self-inconsistency from the previous revision: the Dependency section still described `mcp` as
  imported at two sites after decision #4 was fixed to say one. Folded in: the "265-lead backlog"
  rationale reworded to drop a real number from this project's own private history; the
  functools.partial/wraps rationale corrected (partial alone doesn't leak the signature; wraps
  does); a `cmd_health` regression-test requirement added; a concurrency caveat added beside the
  config-restart note; the layer-2 SDK-API verification note tightened.
- 2026-08-12: Revised after a third `/review-plan` (5 reviewers: 0 Critical, 3 High, 4 Medium,
  3 Low). No self-contradictions this round (per the architect's own read) — the three Highs were
  a genuine correctness gap and two "round-2 fix landed in one section but not Testing" gaps, the
  same pattern the round-3 prompts specifically asked reviewers to hunt for. Fixes: the broad
  `except ImportError` around the whole `serve(config)` call replaced with a distinct
  `McpNotInstalled` exception raised only at the `mcp` import site, removing (not just hedging)
  the risk of misattributing an unrelated `ImportError` to a missing `mcp` install; a `cmd_health`
  CLI-regression test given an actual home in Testing (`tests/test_health_cli.py`, matching this
  repo's `test_<command>_cli.py` convention) after landing only in the Architecture/DoD sections
  last round; the Testing section's "both structurally and behaviourally" redundancy claim
  corrected — an executed reproduction proved the runtime guard test cannot detect a stray
  top-level `mcp` import once an earlier test in the same file has already cached the module, so
  the AST sweep is the SOLE enforcer of that property, and its required shape (every `mcp`-named
  import node must be a descendant of `serve`'s `FunctionDef`, not merely "at least one exists")
  is now stated explicitly. Folded in: `mcp`'s missing version floor tied to the same
  before-implementation verification step as the `Client` API; `cli.py`'s module docstring added
  to the same-PR doc updates; a `--strict`-omission rationale added to the `doctor` tool; the
  `__import__` patch's `monkeypatch` restoration mechanism stated explicitly; a code-comment
  requirement added for `Sluice._resolve`'s undocumented no-side-effects constraint; test data
  tied to the existing seeded-faker fixtures; `health_report`'s sort-order test requires ≥2
  sources to be falsifiable.
