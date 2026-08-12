# MCP server (`job-sluice mcp serve`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `job-sluice mcp serve`, a read-only Model Context Protocol server exposing `list_leads`/`get_lead`/`doctor`/`health` over stdio, as the first real surface plugin over `Sluice`.

**Architecture:** One new sibling module, `sluice/mcpserver.py`, holding four plain top-level functions (directly unit-testable against an injected `Sluice`) plus a `build_server(config)`/`serve(config)` pair that is the ONLY place the `mcp` package is imported. `Sluice` gains a fifth report method, `health_report()`, which `cmd_health` is refactored to call instead of duplicating the read. `cli.py` gains one new two-level command group (`mcp serve`) that lazy-imports the sibling module and turns a missing `mcp` install into an rc-2 usage error.

**Tech Stack:** Python 3.12+, argparse, the MCP Python SDK (`mcp>=2.0.0`, `mcp.server.mcpserver.MCPServer`), pytest (no pytest-asyncio -- see Global Constraints).

**Source spec:** `docs/superpowers/specs/2026-08-12-mcp-server-design.md` (4th revision, `674fae2`, three independent `/review-plan` rounds). This plan implements it, with the corrections below made and verified while writing this plan -- see each note's "Verified" line for how.

## Global Constraints

- **`mcp` extra floor: `mcp>=2.0.0`.** The design spec named this as unresolved ("the exact Client API ... is taken from the SDK's hosted docs, not yet executed against an installed package ... do not carry the docs read forward as verified"). Resolved here by installing both `mcp==1.29.0` and `mcp==2.0.0` into throwaway venvs and driving each API for real, 2026-08-12.
- **The design's "FastMCP" is a stand-in for `mcp.server.mcpserver.MCPServer`, not a literal name.** `mcp.server.fastmcp.FastMCP` exists in the 1.x line (confirmed on 1.29.0) but was REMOVED in 2.0.0's rewrite. 2.0.0 ships an API-compatible renamed class, `MCPServer`, at `mcp.server.mcpserver.MCPServer` -- same `.tool(name=None, title=None, description=None, ...)` decorator signature, same `.run(transport="stdio")`. Verified live: a `.tool()`-decorated nested closure that closes over an injected value and takes only the remaining params produces a client-facing schema that omits the injected value, exactly as decision #4 requires. Every task below imports `MCPServer`, never `FastMCP`.
- **The design's `mcp.Client(...)` is real, but ONLY on `mcp>=2.0.0`.** `mcp==1.29.0` has no top-level `Client` at all (the in-memory test helper there is `mcp.shared.memory.create_connected_server_and_client_session`, an async generator, not a context manager). `mcp==2.0.0` adds `mcp.Client(server, raise_exceptions=True)` as an async context manager with `await client.list_tools()` / `await client.call_tool(name, args)`, matching the design's Testing item 2 shape exactly. This is the deciding reason for the `>=2.0.0` floor: it is the only version where both the server-side `MCPServer.tool()` API and the client-side `Client(...)` API the design wants coexist in one package.
- **No pytest-asyncio in this repo's `test` extra.** Every async body in the tests below runs via a plain `def test_...():` that calls `asyncio.run(...)` on a nested `async def _run(): ...`, never `async def test_...` with a marker.
- **Nested tool closures need a DIFFERENT Python identifier than their matching top-level plain function, plus an explicit `name=` override.** A nested `def list_leads(...): return list_leads(sluice, ...)` inside `build_server()` makes the inner `list_leads` call ITSELF (Python's scoping binds the name to the local def as soon as it starts) rather than the module-level function. Executed, not just reasoned about: for this design's actual shape (the injected `sluice` is an extra argument the inner signature never declares), the self-call fails immediately with a `TypeError` on the first call, not a silent hang; a separately-constructed arity-matched variant genuinely stack-overflows with `RecursionError` instead. Either shape is broken. This plan suffixes every nested closure `_tool` (`list_leads_tool`, `get_lead_tool`, `doctor_tool`, `health_tool`) and passes `name="list_leads"` etc. explicitly so the CLIENT-facing tool name still matches the plain function's name.
- **`build_server(config)` / `serve(config)` split, not a single `serve()`.** The design speaks only of `serve()`, but `serve()` as specified both imports `mcp` AND blocks forever in `mcp_server.run("stdio")` -- there is no way for the layer-2 contract test (which needs a constructed-but-not-running server to hand to `Client`) to reach it without hanging. `build_server(config)` does the import + `Sluice` construction + tool registration and returns the built `MCPServer`; `serve(config)` is `build_server(config).run("stdio")`, one line. The DoD requirement "`mcp` importable nowhere outside `serve()`'s own function body" becomes, precisely, "nowhere outside `build_server()`'s own function body" -- the AST sweep in Task 4 checks `build_server`, not `serve`.
- **`tests/test_mcpserver.py`'s synthetic lead data uses hardcoded placeholder company/role names through a real `Vault(tmp_path)`, matching `tests/test_leads_expire.py`'s `_lead`/`_seed` convention -- NOT the `titles`/`cfg_titles` faker fixtures the design's Testing section names.** Checked: `titles`/`cfg_titles` (`tests/conftest.py`) generate `TriageConfig.accept_titles`/`reject_titles` word lists and are consumed only by `tests/test_classify.py`, `test_engine.py`, `test_relevance.py`, `test_triage_config.py`, `test_triage_engine.py`, `test_app_operations.py` -- none of them produce a company, role, status or lead note, so there is nothing to "go through" for this feature. The actual established convention for a synthetic lead note anywhere else in the suite (`tests/test_leads_expire.py`, `tests/test_apply_record_cli.py`) is a hardcoded `"Example ..."` placeholder written through a real `Vault`.
- **`Sluice.health_report()` cannot take an injected registry** -- it calls `sluice.ingest.sources.all_sources()` directly, matching `cmd_health`'s prior inline code. Its "at least two synthetic sources" falsifiability requirement (round-2 review finding) is satisfied against the REAL registry instead (22 sources registered at time of writing, confirmed via `ls sluice/ingest/sources/*.py`), not an injected fake.
- Every `Sluice`/`Vault`/`Config` construction below matches this codebase's existing test convention (`Sluice(Config(), store=Vault(str(tmp_path)))`), not a hand-rolled fake store.
- CI installs `pip install -e ".[test]"` only (`.github/workflows/ci.yml`) -- `mcp` MUST be added to the `test` extra, not just the new `mcp` extra, or Task 5's contract test does not run in CI.

---

## Task 1: `Sluice.health_report()`

**Files:**
- Modify: `sluice/core/app.py` (insert a `SourceHealth` dataclass + `health_report()` method after `reconcile()` ends / before `def triage(` begins, i.e. between the current lines 856 and 858; add a one-line comment at `self._cache: dict = {}`, line 302)
- Test: `tests/test_health.py` (append)

**Interfaces:**
- Produces: `SourceHealth` (dataclass: `id: str`, `kind: str`, `baseline: float`, `recent: list`, `should_retire: bool`) and `Sluice.health_report(self) -> list[SourceHealth]`, both in `sluice/core/app.py` -- consumed by Task 2 (`cmd_health`) and Task 3 (`mcpserver.health`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_health.py`:

```python
def test_health_report_reflects_the_real_registry_sorted_by_id(tmp_path):
    """AT LEAST TWO real sources, so the sort claim is falsifiable -- with one element,
    a sorted list and an unsorted list are byte-identical and this would pass vacuously
    even with the sort call deleted."""
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.ingest import sources as registry

    ids = sorted(s.id for s in registry.all_sources())
    assert len(ids) >= 2, "the real source registry enumerated fewer than two sources"
    first, second = ids[0], ids[-1]

    # HealthStore() resolves via SLUICE_HEALTH, sandboxed into tmp_path by the autouse
    # _pin_paths fixture in tests/conftest.py -- no explicit path needed.
    h = HealthStore()
    h.record(first, 5)
    h.record(second, 0)
    h.record(second, 0)
    h.record(second, 0)  # three zero runs -> should_retire

    report = Sluice(Config()).health_report()
    got = [s for s in report if s.id in (first, second)]
    assert [s.id for s in got] == sorted(s.id for s in got), \
        "health_report() must be sorted by source id"

    by_id = {s.id: s for s in report}
    assert by_id[first].baseline == 5.0
    assert by_id[first].recent == [5]
    assert by_id[first].should_retire is False
    assert by_id[second].should_retire is True
    assert all(isinstance(s.kind, str) and s.kind for s in report), \
        "every SourceHealth must carry its source's real kind"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py::test_health_report_reflects_the_real_registry_sorted_by_id -v`
Expected: FAIL with `AttributeError: 'Sluice' object has no attribute 'health_report'`

- [ ] **Step 3: Implement**

In `sluice/core/app.py`, change the `self._cache: dict = {}` line (currently line 302, inside `Sluice.__init__`) to:

```python
        # Cached per seam for the process's WHOLE lifetime (see _resolve) -- correct only
        # because every adapter factory _resolve can currently reach (vault, camofox) has
        # no construction-time side effects. A one-shot CLI invocation never exercised
        # that fact; a long-lived caller (`mcp serve`, sluice/mcpserver.py) depends on it.
        # A future adapter factory with a construction-time side effect must either stay
        # free of one or revisit this cache.
        self._cache: dict = {}
```

Then insert, after `reconcile()`'s closing line and before `def triage(`:

```python
    def health_report(self) -> list:
        """The per-source health REPORT `job-sluice health` and the MCP `health` tool
        both show -- sorted by source id, mirroring `dedupe_report`/`expire_report`/
        `reconcile_report`'s report-idiom. Changes nothing.

        `cmd_list_sources --health` (cli.py) still constructs its own `HealthStore()`
        and walks the registry independently: it also needs enabled/disabled overlay
        state this method does not compute, considered and deliberately not folded in
        here (#105)."""
        from sluice.core.health import HealthStore
        from sluice.ingest import sources as registry
        health = HealthStore()
        return [SourceHealth(id=src.id, kind=src.kind, baseline=health.baseline(src.id),
                             recent=health.counts(src.id),
                             should_retire=health.should_retire(src.id))
                for src in sorted(registry.all_sources(), key=lambda s: s.id)]
```

And add the dataclass near the other report dataclasses (beside `DedupeCluster`, before the `Sluice` class):

```python
@dataclass
class SourceHealth:
    """One source's health, as `job-sluice health` and the MCP `health` tool both
    report it. Mirrors `cmd_health`'s prior inline read -- now the single
    implementation both share (#105)."""
    id: str
    kind: str
    baseline: float
    recent: list        # health.counts(id)
    should_retire: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_health.py
git commit -m "feat(core): add Sluice.health_report(), the shared per-source health report"
```

---

## Task 2: `cmd_health` refactor + CLI regression test

**Files:**
- Modify: `sluice/cli.py` (replace `cmd_health`, lines 256-263)
- Test: `tests/test_health_cli.py` (new)

**Interfaces:**
- Consumes: `Sluice.health_report()` (Task 1), `Sluice(config)` from `sluice.core.app`.
- No new interfaces produced -- `cmd_health(args, config) -> int`'s signature and printed output are unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_health_cli.py`:

```python
"""`job-sluice health` at the CLI layer: `cmd_health`'s printed output must be
byte-identical before and after the #105 refactor to call `Sluice.health_report()`.
`tests/test_health.py` tests `HealthStore` (and, after #105, `Sluice.health_report()`)
directly and never exercised `cmd_health` itself -- this is that CLI-level regression
test, matching this repo's `test_<command>_cli.py` convention
(`test_apply_record_cli.py`, `test_leads_dedupe_cli.py`)."""
from sluice.cli import _build_parser, cmd_health
from sluice.core.config import Config
from sluice.core.health import HealthStore
from sluice.ingest import sources as registry


def test_cmd_health_prints_one_line_per_source_with_baseline_and_recent(capsys):
    ids = sorted(s.id for s in registry.all_sources())
    assert len(ids) >= 2, "the real source registry enumerated fewer than two sources"
    first, second = ids[0], ids[-1]

    h = HealthStore()  # sandboxed into tmp_path by the autouse _pin_paths fixture
    h.record(first, 5)
    h.record(second, 0)
    h.record(second, 0)
    h.record(second, 0)  # three zero runs -> RETIRE

    args = _build_parser().parse_args(["health"])
    assert cmd_health(args, Config()) == 0

    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines()}
    assert "baseline=5" in lines[first]
    assert "recent=[5]" in lines[first]
    assert "RETIRE" not in lines[first]
    assert "RETIRE" in lines[second]
```

- [ ] **Step 2: Run test to verify it passes against the CURRENT (pre-refactor) `cmd_health`**

Run: `pytest tests/test_health_cli.py -v`
Expected: PASS -- this pins the CURRENT output byte-for-byte before touching the implementation, so Step 4 below is a genuine regression check, not a test written to match new code.

- [ ] **Step 3: Refactor `cmd_health`**

In `sluice/cli.py`, replace (lines 256-263):

```python
def cmd_health(args, config) -> int:
    health = HealthStore()
    for src in sorted(registry.all_sources(), key=lambda s: s.id):
        counts = health.counts(src.id)
        flag = " RETIRE" if health.should_retire(src.id) else ""
        print(f"{src.id:16} baseline={health.baseline(src.id):.0f} "
              f"recent={counts}{flag}")
    return 0
```

with:

```python
def cmd_health(args, config) -> int:
    from sluice.core.app import Sluice

    for src in Sluice(config).health_report():
        flag = " RETIRE" if src.should_retire else ""
        print(f"{src.id:16} baseline={src.baseline:.0f} recent={src.recent}{flag}")
    return 0
```

(`HealthStore` and `registry` stay imported at the top of `cli.py`: `cmd_list_sources` still uses both directly, unchanged.)

- [ ] **Step 4: Run test to verify it still passes**

Run: `pytest tests/test_health_cli.py tests/test_health.py -v`
Expected: PASS -- output is byte-identical, now produced through `Sluice.health_report()`.

- [ ] **Step 5: Commit**

```bash
git add sluice/cli.py tests/test_health_cli.py
git commit -m "refactor(cli): cmd_health calls Sluice.health_report() instead of duplicating the read"
```

---

## Task 3: `sluice/mcpserver.py` -- the four plain tool functions

**Files:**
- Create: `sluice/mcpserver.py` (four plain functions only -- no `mcp` import, no `serve`/`build_server`, no `McpNotInstalled`; those land in Task 4)
- Test: `tests/test_mcpserver.py` (new)

**Interfaces:**
- Produces: `list_leads(sluice, statuses=None, limit=None) -> dict`, `get_lead(sluice, lead: str) -> dict`, `doctor(sluice, offline=True) -> dict`, `health(sluice) -> dict`, all in `sluice/mcpserver.py` -- consumed by Task 4's `build_server()`.
- Consumes: `Sluice.store()`, `Sluice.doctor(offline=...)`, `Sluice.health_report()` (Task 1), `core.leads.slug_matches`, `core.status.CANONICAL`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcpserver.py`:

```python
"""sluice/mcpserver.py's plain tool functions, called directly against a real
`Sluice`/`Vault` -- no MCP protocol machinery, no `build_server()`/`serve()`. Fixture
notes are built through `Vault.upsert` so their slugs are REAL store-issued filenames,
matching `tests/test_leads_expire.py`'s own rationale for doing the same (a hand-written
slug format could pass here while the shipped command matches nothing).
"""
import dataclasses

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault
from sluice.mcpserver import doctor, get_lead, health, list_leads


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1"):
    return Lead(source="s", search="q", title=title, company=company, url=url)


def _seed(tmp_path, *, status="shortlist", company="Example Ltd", title="Example Role",
          url="https://example.invalid/1", **extra):
    """Create one lead note through a real Vault and set its status/extra fields.
    Returns its store-issued slug."""
    v = Vault(str(tmp_path))
    v.upsert(_lead(company=company, title=title, url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path):
    return Sluice(Config(), store=Vault(str(tmp_path)))


# ── list_leads ───────────────────────────────────────────────────────────────

def test_list_leads_returns_a_curated_summary_never_the_body(tmp_path):
    slug = _seed(tmp_path, status="shortlist", first_seen="2026-01-01", last_seen="2026-02-01")
    out = list_leads(_app(tmp_path))
    assert out["count"] == 1
    assert out["truncated"] is False
    row = out["leads"][0]
    assert row["slug"] == slug
    assert row["status"] == "shortlist"
    assert row["company"] == "Example Ltd"
    assert row["role"] == "Example Role"
    assert row["first_seen"] == "2026-01-01"
    assert row["last_seen"] == "2026-02-01"
    assert row["tailored_cv"] is False
    assert "body" not in row and "fm" not in row


def test_list_leads_filters_by_status(tmp_path):
    # DISTINCT titles, not just distinct urls: Vault identity is company+title(+location)
    # (see `Vault._candidate_names`, `stem = f"{company} - {title}"`) -- url plays no part
    # in it, so two leads sharing a title would collide onto ONE note and this would
    # seed only one lead instead of two, passing (or failing) for the wrong reason.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, status="dismiss", title="Example Role Two", url="https://example.invalid/2")
    out = list_leads(_app(tmp_path), statuses=["shortlist"])
    assert out["count"] == 1
    assert out["leads"][0]["status"] == "shortlist"


def test_list_leads_rejects_an_unknown_status_naming_the_valid_set(tmp_path):
    try:
        list_leads(_app(tmp_path), statuses=["not-a-real-status"])
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "not-a-real-status" in str(e)
        assert "shortlist" in str(e)  # a real canonical status, proving the valid set is named


def test_list_leads_limit_truncates_and_reports_truncated(tmp_path):
    # Same distinct-title reasoning as test_list_leads_filters_by_status above.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, status="shortlist", title="Example Role Two", url="https://example.invalid/2")
    out = list_leads(_app(tmp_path), limit=1)
    assert out["count"] == 1
    assert out["truncated"] is True


def test_list_leads_no_limit_returns_everything_untruncated(tmp_path):
    # Same distinct-title reasoning as test_list_leads_filters_by_status above.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, status="shortlist", title="Example Role Two", url="https://example.invalid/2")
    out = list_leads(_app(tmp_path))
    assert out["count"] == 2
    assert out["truncated"] is False


def test_list_leads_surfaces_the_cv_flags(tmp_path):
    _seed(tmp_path, status="shortlist", tailored_cv="CV_deadbeef.pdf (2026-07-09)",
          needs_signoff="unsupported claim", pending_cv="CV_deadbeef.pdf (2026-07-09)")
    row = list_leads(_app(tmp_path))["leads"][0]
    assert row["tailored_cv"] is True
    assert row["needs_signoff"] is True
    assert row["pending_cv"] is True


# ── get_lead ─────────────────────────────────────────────────────────────────

def test_get_lead_not_found(tmp_path):
    assert get_lead(_app(tmp_path), "nothing here") == {"outcome": "not_found"}


def test_get_lead_found_returns_full_frontmatter_and_body(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    out = get_lead(_app(tmp_path), slug)
    assert out["outcome"] == "found"
    assert out["slug"] == slug
    assert out["status"] == "shortlist"
    assert out["fm"]["company"] == "Example Ltd"
    assert "body" in out


def test_get_lead_ambiguous_names_every_candidate_and_picks_none(tmp_path):
    slug1 = _seed(tmp_path, company="Example Northgate", title="Analyst",
                  url="https://example.invalid/1")
    slug2 = _seed(tmp_path, company="Example Northgate", title="Analyst Two",
                  url="https://example.invalid/2")
    out = get_lead(_app(tmp_path), "Example Northgate")
    assert out["outcome"] == "ambiguous"
    assert sorted(out["candidates"]) == sorted([slug1, slug2])


def test_get_lead_matches_across_every_status_not_just_shortlist(tmp_path):
    slug = _seed(tmp_path, status="dismiss")
    out = get_lead(_app(tmp_path), slug)
    assert out["outcome"] == "found"
    assert out["status"] == "dismiss"


# ── doctor ───────────────────────────────────────────────────────────────────

def test_doctor_defaults_to_offline_and_matches_sluice_doctor_offline(tmp_path):
    # mcpserver.doctor exposes no `probe` seam (an MCP client cannot inject a python
    # callable), so this cannot inject a fake round-trip the way tests/test_doctor.py
    # does. Proven indirectly instead: the default call must reproduce
    # `sluice.doctor(offline=True)` exactly. If the wrapper's default were secretly
    # False, this would risk a live backend round-trip (a keyless claude-max primary
    # needs no credentials, so it IS "known and testable" in the live branch) and the
    # two reports would very likely diverge or the test would hang.
    app = Sluice(Config(), store=Vault(str(tmp_path)))
    out = doctor(app)
    expected = app.doctor(offline=True)
    assert out == {**dataclasses.asdict(expected), "exit_code": expected.exit_code(strict=False)}


# ── health ───────────────────────────────────────────────────────────────────

def test_health_wraps_health_report_as_asdict_sources(tmp_path):
    app = Sluice(Config(), store=Vault(str(tmp_path)))
    out = health(app)
    assert out == {"sources": [dataclasses.asdict(s) for s in app.health_report()]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcpserver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sluice.mcpserver'`

- [ ] **Step 3: Implement**

Create `sluice/mcpserver.py`:

```python
"""sluice/mcpserver.py -- a Model Context Protocol server: a second front-end over
`Sluice`, exposing four read-only tools (list_leads, get_lead, doctor, health) to an
MCP client (e.g. Claude Code) over stdio (#105).

The `mcp` package is imported in exactly ONE place: inside `build_server()`'s own
function body. See docs/superpowers/plans/2026-08-12-mcp-server.md's Global Constraints
for why `build_server()` (not `serve()`) is that place, and this module's own
`tests/test_mcpserver.py` / `tests/functional/test_mcp_contract.py` for how that is
enforced and proven.
"""
import dataclasses

from sluice.core.app import Sluice
from sluice.core.leads import slug_matches
from sluice.core.status import CANONICAL


class McpNotInstalled(RuntimeError):
    """Raised by `build_server()` when the `mcp` package's import fails.
    `cmd_mcp_serve` (cli.py) catches this specifically and turns it into a usage
    error naming the extra to install -- never a bare `except ImportError`, which
    could misattribute an unrelated import failure deep inside a later tool call."""


def list_leads(sluice: Sluice, statuses: list | None = None, limit: int | None = None) -> dict:
    """Every lead matching `statuses` (or every lead, unfiltered), as a curated
    per-lead summary -- never the full frontmatter or body, so a large backlog
    cannot flood one response. Raises ValueError, naming the valid set, on an
    unrecognized status -- never silently returns [] for a typo."""
    if statuses:
        unknown = sorted(set(statuses) - CANONICAL)
        if unknown:
            raise ValueError(
                f"unknown status {unknown[0]!r} (expected one of {sorted(CANONICAL)})")
    notes = sluice.store().read_leads(set(statuses) if statuses else None)
    truncated = limit is not None and len(notes) > limit
    if limit is not None:
        notes = notes[:limit]
    leads = [{
        "slug": n.slug, "status": n.status,
        "company": n.fm.get("company", ""), "role": n.fm.get("role", ""),
        "url": n.fm.get("url", ""),
        "first_seen": n.fm.get("first_seen", ""), "last_seen": n.fm.get("last_seen", ""),
        "tailored_cv": bool(n.fm.get("tailored_cv")),
        "needs_signoff": bool(n.fm.get("needs_signoff")),
        "pending_cv": bool(n.fm.get("pending_cv")),
    } for n in notes]
    return {"leads": leads, "count": len(leads), "truncated": truncated}


def get_lead(sluice: Sluice, lead: str) -> dict:
    """Resolve `lead` by substring match over every lead (not status-scoped), the
    same rule `cv`/`apply` use for `--lead`. Never guesses an identity: zero matches
    -> not_found, two-or-more -> ambiguous (candidates named, nothing picked),
    exactly one -> the full frontmatter + body (the single-lead detail view)."""
    notes = [n for n in sluice.store().read_leads() if slug_matches(n, lead)]
    if not notes:
        return {"outcome": "not_found"}
    if len(notes) > 1:
        return {"outcome": "ambiguous", "candidates": sorted(n.slug for n in notes)}
    n = notes[0]
    return {"outcome": "found", "slug": n.slug, "status": n.status, "fm": n.fm, "body": n.body}


def doctor(sluice: Sluice, offline: bool = True) -> dict:
    """Preflight backends, renderer, store artefacts and gate posture. Offline by
    default: an agent calling this tool casually must not trigger unbudgeted live
    spend. `exit_code` is `DoctorReport.exit_code(strict=False)`, the CLI's own
    default -- the full report is already in the response, so an agent can apply
    its own strictness policy over the raw checks."""
    report = sluice.doctor(offline=offline)
    out = dataclasses.asdict(report)
    out["exit_code"] = report.exit_code(strict=False)
    return out


def health(sluice: Sluice) -> dict:
    """Per-source scrape baseline + retire state, sorted by source id."""
    return {"sources": [dataclasses.asdict(s) for s in sluice.health_report()]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcpserver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sluice/mcpserver.py tests/test_mcpserver.py
git commit -m "feat(mcp): add the four plain MCP tool functions (list_leads, get_lead, doctor, health)"
```

---

## Task 4: `mcp` dependency, `build_server`/`serve`, CLI wiring, import guards, docs

**Files:**
- Modify: `pyproject.toml` (add the `mcp` extra; add `mcp` to `test`)
- Modify: `.rulesync/rules/CLAUDE.md` (add `mcp` to the stdlib-only exception list, two spots)
- Modify: `sluice/mcpserver.py` (add `build_server(config)` / `serve(config)`)
- Modify: `sluice/cli.py` (module docstring; new `# ── mcp ──` section with `cmd_mcp_serve`; `_build_parser()` gains the `mcp serve` group)
- Modify: `tests/test_mcpserver.py` (append the two guard tests)
- Modify: `README.md` (Commands table + new "MCP server" subsection)
- Modify: `docs/ARCHITECTURE.md` (surface/adapter paragraph)

**Interfaces:**
- Consumes: `list_leads`, `get_lead`, `doctor`, `health`, `McpNotInstalled` (Task 3).
- Produces: `build_server(config) -> MCPServer` and `serve(config) -> None` in `sluice/mcpserver.py`; `cmd_mcp_serve(args, config) -> int` in `sluice/cli.py` -- consumed by Task 5's contract test (`build_server`) and by a human running `job-sluice mcp serve` (`serve`/`cmd_mcp_serve`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcpserver.py`:

```python
# ── import-guard tests (item 3 of the design's Testing section) ──────────────
#
# These prove DIFFERENT things about the same overall property ("mcp leaks nowhere
# outside build_server()"), despite sounding redundant. An executed reproduction
# during plan review proved the runtime guard below CANNOT detect a stray top-level
# `mcp` import once an earlier test in THIS FILE has already imported
# `sluice.mcpserver` unguarded (which every test above does) -- by the time the
# runtime guard's patch is installed, the stray import already ran and succeeded,
# invisibly. The AST sweep is the SOLE test that can catch that. See
# docs/superpowers/specs/2026-08-12-mcp-server-design.md's Testing section, item 3,
# for the full argument.

def test_mcp_imported_nowhere_outside_build_server():
    """The AST sweep: every mcp-named Import/ImportFrom node in sluice/cli.py and
    sluice/mcpserver.py must be a DESCENDANT of build_server()'s own FunctionDef --
    not merely "build_server's body contains at least one such node" (a stray
    top-level `import mcp` sitting ALONGSIDE a correctly-guarded one must still fail
    this)."""
    import ast
    import inspect

    import sluice.cli as cli_mod
    import sluice.mcpserver as mcpserver_mod

    def _bad_mcp_imports(module, allowed_func_name=None):
        tree = ast.parse(inspect.getsource(module))
        allowed_ids = set()
        if allowed_func_name is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == allowed_func_name:
                    allowed_ids = {id(n) for n in ast.walk(node)}
                    break
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "mcp" or name.startswith("mcp."):
                    if id(node) not in allowed_ids:
                        bad.append(name)
        return bad

    assert _bad_mcp_imports(cli_mod) == [], "sluice/cli.py must carry no mcp import"
    assert _bad_mcp_imports(mcpserver_mod, allowed_func_name="build_server") == [], (
        "sluice/mcpserver.py must import mcp only inside build_server()'s own "
        "function body (directly, or via a helper nested inside it)")


def test_cmd_mcp_serve_degrades_to_rc2_when_mcp_is_absent(monkeypatch, capsys):
    """The runtime guard: `mcp` genuinely absent (not merely `sys.modules["mcp"] =
    None`, which a reproduction during plan review proved silently no-ops once `mcp`
    is a genuine test-extra dependency cached elsewhere in the same session) must
    degrade `cmd_mcp_serve` to an rc-2 usage error, never an uncaught traceback."""
    import builtins

    real_import = builtins.__import__

    def _raise_for_mcp(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError(f"simulated: {name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise_for_mcp)

    from sluice.cli import _build_parser, cmd_mcp_serve
    from sluice.core.config import Config

    args = _build_parser().parse_args(["mcp", "serve"])
    assert cmd_mcp_serve(args, Config()) == 2
    assert "job-sluice[mcp]" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcpserver.py -k "build_server or degrades_to_rc2" -v`
Expected: the runtime guard test (`test_cmd_mcp_serve_degrades_to_rc2_when_mcp_is_absent`) FAILS with `ImportError: cannot import name 'cmd_mcp_serve'` -- `sluice/cli.py` has no `mcp` wiring yet. The AST sweep test (`test_mcp_imported_nowhere_outside_build_server`) is VACUOUSLY GREEN at this point: `sluice/mcpserver.py` carries no `mcp`-named import anywhere yet (Task 3 deliberately added none), so there is nothing for the sweep to find regardless of whether `build_server` exists. It only becomes a meaningful (falsifiable) check once Step 3c adds the real `mcp` import -- the mutation test in Step 4 is what actually exercises its ability to fail.

- [ ] **Step 3a: Add the `mcp` dependency**

In `pyproject.toml`, add to `[project.optional-dependencies]` (after the `completion` block, before `test`):

```toml
# `job-sluice mcp serve`'s only dependency (sluice/mcpserver.py:build_server(), the
# ONE place `mcp` is imported -- see .rulesync/rules/CLAUDE.md's stdlib-only
# exception list). Pinned at >=2.0.0: that release renamed the high-level server
# class FastMCP -> MCPServer (mcp.server.fastmcp -> mcp.server.mcpserver) and is the
# first version to ship a top-level `mcp.Client` for in-memory testing
# (tests/functional/test_mcp_contract.py) -- verified against a real install,
# 2026-08-12. A 1.x install has FastMCP but no top-level Client; >=2.0.0 is the only
# floor where both halves of this feature are buildable from one dependency.
mcp = ["mcp>=2.0.0"]
```

Change the `test` line from:

```toml
test = ["pytest", "faker", "pytest-cov", "jinja2", "setuptools>=83.0.0", "build"]
```

to:

```toml
test = ["pytest", "faker", "pytest-cov", "jinja2", "setuptools>=83.0.0", "build", "mcp>=2.0.0"]
```

and extend the comment block above it (currently the "jinja2 is in BOTH `render` and `test`" paragraph) with one more sentence: "`mcp` is in BOTH `mcp` and `test` for the identical reason: CI installs only `[test]`, so `tests/functional/test_mcp_contract.py` needs the real package to drive `Client`/`MCPServer` for real, not skip itself via `pytest.importorskip`."

- [ ] **Step 3b: Add `mcp` to the stdlib-only exception list**

In `.rulesync/rules/CLAUDE.md`, in the paragraph starting "**`sluice/` is standard-library only.**" (currently lines 475-498), after the `argcomplete` sentence ("...since an exception there breaks the user's shell on every TAB press, not just the one command.") and before "HTTP goes through `urllib`...", insert:

```
And `mcp`, imported lazily inside `build_server()`'s own function body in
`sluice/mcpserver.py` (never at module scope, and nowhere in `cli.py` at all) behind
the `mcp` extra -- it pulls in an async/network stack (uvicorn, starlette, anyio,
pydantic, ...) meaningfully heavier than a config-file parser, so nothing outside
`job-sluice mcp serve` may cause it to load; a bare install never imports it.
```

And in the later paragraph ("`render`, `google` and `completion` sit beside `test`... jinja2 ALSO sits in `test`..."), after the jinja2 sentence, add: "`mcp` sits in BOTH `mcp` and `test` for the identical reason -- CI installs only `[test]`, and `tests/functional/test_mcp_contract.py` needs the real package to drive it for real rather than skip itself. Being in two extras does not move it out of the rule either: it is still `mcp` that puts it firmly inside."

- [ ] **Step 3c: Add `build_server`/`serve` to `sluice/mcpserver.py`**

Append to `sluice/mcpserver.py` (after the four plain functions):

```python
def build_server(config):
    """Build one `Sluice(config)`, register the four tools against it, and return
    the constructed (NOT yet running) MCPServer. `mcp` is imported HERE and nowhere
    else -- see the module docstring. `serve()` below is the live-process entry
    point; tests reach this function directly (via the SDK's in-memory `Client`)
    so they never have to block on `.run()`.

    `Sluice(config)` is built ONCE, matching how every `cmd_*` in cli.py builds
    exactly one `Sluice(config)` per invocation. Unlike a one-shot CLI command,
    `mcp serve` is long-running: an edited `sluice.yaml` is picked up only on the
    next restart, not live. Whether FastMCP's stdio transport can dispatch
    overlapping tool calls against this one shared `Sluice` is not verified here;
    believed benign for this read-only slice (see the design doc's Architecture
    section) but not an assumption a future write-tools slice may inherit for
    free."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as e:
        raise McpNotInstalled(
            "the 'mcp' package is not installed -- run `pip install job-sluice[mcp]`"
        ) from e

    sluice = Sluice(config)
    mcp_server = MCPServer("sluice")

    @mcp_server.tool(name="list_leads")
    def list_leads_tool(statuses: list[str] | None = None, limit: int | None = None) -> dict:
        """List leads, optionally filtered by status and capped by limit."""
        return list_leads(sluice, statuses=statuses, limit=limit)

    @mcp_server.tool(name="get_lead")
    def get_lead_tool(lead: str) -> dict:
        """Look up one lead by a substring of its company, role or store slug."""
        return get_lead(sluice, lead)

    @mcp_server.tool(name="doctor")
    def doctor_tool(offline: bool = True) -> dict:
        """Preflight backends, renderer, store artefacts and gate posture."""
        return doctor(sluice, offline=offline)

    @mcp_server.tool(name="health")
    def health_tool() -> dict:
        """Per-source scrape baseline + retire state."""
        return health(sluice)

    return mcp_server


def serve(config) -> None:
    """Run the MCP server over stdio for the rest of the process's life."""
    build_server(config).run("stdio")
```

- [ ] **Step 3d: Wire the CLI**

In `sluice/cli.py`, update the module docstring (lines 1-14) to add, after the `job-sluice health` line:

```
  job-sluice mcp serve                          run the MCP server (stdio transport)
```

Add a new section near the `doctor` section (before `# ── doctor ───...`, currently line 912):

```python
# ── mcp ───────────────────────────────────────────────────────────────────────
def cmd_mcp_serve(args, config) -> int:
    from sluice import mcpserver

    try:
        mcpserver.serve(config)
    except mcpserver.McpNotInstalled as exc:
        print(f"job-sluice: {exc}", file=sys.stderr)
        return 2
    return 0
```

In `_build_parser()`, after the `health` parser (currently lines 1163-1164) and before `init` (line 1166):

```python
    mcp_group = top.add_parser("mcp", help="Model Context Protocol server").add_subparsers(
        dest="cmd", required=True)
    mcp_serve = mcp_group.add_parser("serve", help="run the MCP server (stdio transport)")
    mcp_serve.set_defaults(func=cmd_mcp_serve)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pip install -e ".[mcp,test]"` (installs the real `mcp` package for the first time), then:
`pytest tests/test_mcpserver.py -v`
Expected: PASS, all tests in the file (function tests from Task 3 plus the two new guard tests).

Then mutation-test the AST sweep by hand (per the design's Testing item 3): temporarily add a stray top-level `import mcp` near the top of `sluice/mcpserver.py`, run `pytest tests/test_mcpserver.py::test_mcp_imported_nowhere_outside_build_server -v` and confirm it goes RED, then revert the stray import. Separately, mutation-test the runtime guard: temporarily delete the `except mcpserver.McpNotInstalled` branch in `cmd_mcp_serve` (let the exception propagate bare), run `pytest tests/test_mcpserver.py::test_cmd_mcp_serve_degrades_to_rc2_when_mcp_is_absent -v` and confirm it goes RED, then revert.

Also run the full suite once to confirm nothing else regressed:
`pytest -q`
Expected: PASS (no new failures; `tests/test_renderer_template.py::test_no_test_module_uses_importorskip` already sweeps all of `tests/`, so it automatically covers the two new test files without any change).

- [ ] **Step 5: Update the docs named in the design's Definition of Done**

In `README.md`:
- Change "Nine top-level command groups." (line 275) to "Ten top-level command groups."
- Add a row to the Commands table, after the `job-sluice health` row:
  `| \`job-sluice mcp\` | run a Model Context Protocol server over stdio, for an agent to drive sluice directly (\`serve\`) |`
- Insert a new subsection after the Commands table (before `## Rendering prerequisites`):

```markdown
## MCP server

`job-sluice mcp serve` runs sluice as a Model Context Protocol server over stdio, so
an agent (Claude Code or otherwise) can call `list_leads`/`get_lead`/`doctor`/`health`
directly instead of shelling out to the CLI and parsing its stdout. Read-only for
now -- see [`docs/ARCHITECTURE.md`](https://github.com/MrReasonable/sluice/blob/main/docs/ARCHITECTURE.md)'s surface/adapter section. Needs `pip install -e '.[mcp]'`.

Register it with Claude Code:

```
claude mcp add job-sluice -- job-sluice mcp serve
```
```

In `docs/ARCHITECTURE.md`, in the surface/adapter paragraph (currently lines 336-344), change the closing sentence from:

```
`cli.py` is now a thin shell over `Sluice` -- each command
builds one, calls one method, and formats the result for the terminal -- so a web
UI written today has nothing left in `cli.py` worth forking.
```

to:

```
`cli.py` is now a thin shell over `Sluice` -- each command
builds one, calls one method, and formats the result for the terminal -- so a surface
built today has nothing left in `cli.py` worth forking. `sluice/mcpserver.py` (#105)
is the first one: a Model Context Protocol server exposing four read-only tools
(`list_leads`, `get_lead`, `doctor`, `health`) over stdio.
```

In `sluice/cli.py`'s own module docstring (already touched in Step 3d above) -- no further change needed here.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .rulesync/rules/CLAUDE.md sluice/mcpserver.py sluice/cli.py \
        tests/test_mcpserver.py README.md docs/ARCHITECTURE.md
git commit -m "feat(mcp): wire job-sluice mcp serve, guard the mcp import, update docs"
```

- [ ] **Step 7: Regenerate and verify rulesync output**

Run: `npm run rulesync` (regenerates `.claude/agents/*.md` and other AI-tool outputs from `.rulesync/rules/CLAUDE.md`)
Run: `python scripts/guard_rulesync_drift.py` (or the project's equivalent drift check named in `.github/workflows/ci.yml`)
Expected: clean tree, no drift.

```bash
git add -A
git commit -m "chore: regenerate rulesync output for the mcp stdlib-only exception" --allow-empty
```

(Use `--allow-empty` only if the regeneration produced no changes to stage; otherwise omit it and let the commit carry the regenerated files.)

---

## Task 5: Layer-2 contract test (`tests/functional/test_mcp_contract.py`)

**Files:**
- Create: `tests/functional/test_mcp_contract.py`

**Interfaces:**
- Consumes: `build_server(config)` (Task 4), `mcp.Client` (the real SDK, `mcp>=2.0.0`).

- [ ] **Step 1: Write the failing tests**

Create `tests/functional/test_mcp_contract.py`:

```python
"""MCP registration contract (#105): `tools/list` reflects the real four tools --
names, and schemas that never leak the injected `sluice` parameter (the property
decision #4's nested-closure shape in sluice/mcpserver.py exists to guarantee) -- and
a real `call_tool(...)` round-trips through the SDK's own dispatch into the real
functions. Mirrors tests/functional/test_cli_contract.py's precedent of proving a
structural property against the REAL wiring rather than a hand-rolled stand-in. No
subprocess, no stdio, no network: `mcp.Client`'s in-memory transport drives the
server object directly.

No `async def test_...`: this repo carries no pytest-asyncio dependency (`test` adds
only `mcp`, `pytest`, `faker`, `pytest-cov`, `jinja2`, `setuptools`, `build`), so each
test wraps its async body in a plain `asyncio.run(...)` call instead.
"""
import asyncio
import json

from sluice.core.config import Config
from sluice.mcpserver import build_server


def test_tools_list_names_and_schemas_never_leak_sluice():
    async def _run():
        from mcp import Client
        server = build_server(Config())
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    result = asyncio.run(_run())
    by_name = {t.name: t for t in result.tools}
    assert set(by_name) == {"list_leads", "get_lead", "doctor", "health"}
    for tool in by_name.values():
        props = tool.input_schema.get("properties", {})
        assert "sluice" not in props, (
            f"{tool.name}'s schema leaked the injected `sluice` parameter: {props}")
    assert set(by_name["list_leads"].input_schema["properties"]) == {"statuses", "limit"}
    assert set(by_name["get_lead"].input_schema["properties"]) == {"lead"}
    assert set(by_name["doctor"].input_schema["properties"]) == {"offline"}
    assert by_name["health"].input_schema.get("properties", {}) == {}


def test_call_tool_round_trips_through_the_real_dispatch():
    async def _run():
        from mcp import Client
        server = build_server(Config())
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("health", {})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert "sources" in payload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/functional/test_mcp_contract.py -v`
Expected: FAIL (collection error) if `mcp` is not yet installed in this environment (`pip install -e ".[mcp,test]"` from Task 4 Step 4 should already have installed it -- if this is a fresh environment, install it now). If `mcp` IS installed, this should already PASS since Task 4 built `build_server` correctly; if so, treat this step as the verification run instead and skip straight to confirming green.

- [ ] **Step 3: (Implementation already complete via Task 4 -- this task is pure verification)**

No production code changes: `build_server()` was implemented in Task 4. This task exists as its own reviewer gate because a reviewer could accept Task 4's structure (the import guards, the CLI wiring) while still finding fault with whether the ACTUAL registered schema/names are correct -- which only a real round trip against the SDK proves.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/functional/test_mcp_contract.py -v`
Expected: PASS

Run the full suite once more:
`pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/functional/test_mcp_contract.py
git commit -m "test(mcp): add the layer-2 in-memory Client contract test"
```

---

## Definition of Done (from the design spec, cross-checked against the tasks above)

- [x] `job-sluice mcp serve` starts a stdio MCP server exposing `list_leads`, `get_lead`, `doctor`, `health` -- Task 4.
- [x] `mcp` is a new `optional-dependencies` extra, also present in `test`, with a confirmed version floor (`>=2.0.0`, verified live) -- Task 4 Step 3a.
- [x] `.rulesync/rules/CLAUDE.md`'s stdlib-only rule documents the `mcp` exception -- Task 4 Step 3b + Step 7 (regenerate).
- [x] `Sluice.health_report()` exists; `cmd_health` calls it; `tests/test_health_cli.py` confirms unchanged output -- Tasks 1-2.
- [x] `Sluice._resolve`'s no-construction-time-side-effects constraint is a comment at `self._cache` -- Task 1 Step 3.
- [x] `README.md`, `cli.py`'s module docstring, and `docs/ARCHITECTURE.md`'s surface/adapter section are all updated -- Task 4 Steps 3d/5.
- [x] All four test surfaces pass; the AST sweep alone confirms the import-guard invariant (mutation-tested); the runtime guard test separately confirms `cmd_mcp_serve` degrades cleanly (mutation-tested) -- Task 4.

## Self-Review

**Spec coverage:** All 5 numbered "settled decisions", the Dependency section, CLI wiring, Architecture (including the `_resolve` comment requirement), `health_report()`, all four tools, Error handling (no blanket try/except anywhere in `sluice/mcpserver.py` -- confirmed: `list_leads` raises `ValueError` directly, `get_lead`/`doctor`/`health` never catch), all four Testing items, all three Docs items, and every Definition of Done line map to a task above. Out-of-scope items (write tools, non-stdio transport, a validation layer) are not implemented, matching the spec.

**Placeholder scan:** No "TBD"/"similar to"/unfleshed steps remain; every code block is complete, runnable code, verified against the real installed `mcp` package where the design spec had left that unverified.

**Type consistency:** `SourceHealth` (Task 1) is consumed identically in `cmd_health` (Task 2, via `.baseline`/`.recent`/`.should_retire`) and in `mcpserver.health()` (Task 3, via `dataclasses.asdict`). `list_leads`/`get_lead`/`doctor`/`health`'s signatures in Task 3 exactly match how Task 4's `build_server()` calls them (positional `sluice` first, then the same keyword names). `McpNotInstalled` is defined in Task 3 and consumed identically in Task 4's `build_server()` (raise site) and `cmd_mcp_serve` (catch site).
