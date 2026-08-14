"""MCP registration contract (#105, extended by #131): `tools/list` reflects the real
tools -- the original four under the default write=False, all nine (plus the five
write tools) under write=True -- names, and schemas that never leak the injected
`sluice` parameter (the property decision #4's nested-closure shape in
sluice/mcpserver.py exists to guarantee) -- and a real `call_tool(...)` round-trips
through the SDK's own dispatch into the real functions, including one full
dismiss_lead write and a concurrency sanity check. Mirrors
tests/functional/test_cli_contract.py's precedent of proving a structural property
against the REAL wiring rather than a hand-rolled stand-in. No subprocess, no stdio,
no network: `mcp.Client`'s in-memory transport drives the server object directly.

No `async def test_...`: this repo carries no pytest-asyncio dependency (`test` adds
only `mcp`, `pytest`, `faker`, `pytest-cov`, `jinja2`, `setuptools`, `build`), so each
test wraps its async body in a plain `asyncio.run(...)` call instead.
"""
import asyncio
import json

from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault
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


# `health` above takes no arguments -- the SDK's JSON-Schema-driven argument binding
# is never exercised by it. list_leads/get_lead/doctor all take real parameters, and
# each is driven through call_tool here too, so a future SDK upgrade that changes how
# `list[str] | None`/`str`/`bool` parameters are coerced cannot silently break dispatch
# while every other test (both these and the direct-call unit tests in
# tests/test_mcpserver.py, which bypass the SDK entirely) stays green.
#
# list_leads/get_lead touch the store. `Sluice(config).store()` resolves the vault
# through `stores/vault.py:_make`, whose OWN docstring states the precedence
# deliberately: `os.environ.get("VAULT_DIR") or config.vault_dir or None` -- the env
# var wins over an explicit `Config(vault_dir=...)` on THIS path (a direct
# `Vault(str(tmp_path))` construction is different: the constructor's own precedence
# puts an explicit `dir` argument first, which is what protects the ~150 such
# constructions elsewhere in the suite from a leaked env var). tests/conftest.py's
# autouse `_pin_paths` fixture always sets `VAULT_DIR` to `tmp_path / "vault"`, so a
# `Config(vault_dir=str(tmp_path))` passed here is silently inert through this
# specific path -- confirmed live: seeding a lead at bare `tmp_path` and asserting a
# non-empty `list_leads`/`get_lead` result through `Sluice.store()` failed until the
# seed moved to `tmp_path / "vault"` instead, matching where `VAULT_DIR` actually
# points. Every test below seeds at THAT path (or overrides `VAULT_DIR` directly via
# `monkeypatch`) rather than repeating the assumption that sank the first draft.

def test_call_tool_round_trips_list_leads_with_real_arguments(tmp_path):
    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path)))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("list_leads", {"statuses": ["shortlist"], "limit": 5})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload == {"leads": [], "count": 0, "truncated": False}


def test_call_tool_round_trips_list_leads_non_empty_including_the_content_warning(tmp_path):
    v = Vault(str(tmp_path / "vault"))
    v.upsert(Lead(source="s", search="q", title="Example Role", company="Example Ltd",
                  url="https://example.invalid/1"))

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("list_leads", {})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["count"] == 1
    assert "whatever it says about itself" in payload["content_warning"]


def test_call_tool_round_trips_get_lead_with_real_arguments(tmp_path):
    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path)))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("get_lead", {"lead": "nothing-matches-this"})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload == {"outcome": "not_found"}


def test_call_tool_round_trips_get_lead_found_including_the_content_warning(tmp_path):
    # The two tests above only ever reach get_lead's not_found/error branches -- the
    # "found" branch (the one carrying content_warning, the field this file exists to
    # prove survives REAL SDK JSON serialization, not just a plain Python call in
    # tests/test_mcpserver.py) was never driven through the real dispatch. Seed one
    # real lead through a real Vault, matching tests/test_mcpserver.py's own `_seed`
    # convention, and call get_lead by its store-issued slug.
    v = Vault(str(tmp_path / "vault"))
    v.upsert(Lead(source="s", search="q", title="Example Role", company="Example Ltd",
                  url="https://example.invalid/1"))
    slug = next(n for n in v.read_leads() if n.fm.get("url") == "https://example.invalid/1").slug

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("get_lead", {"lead": slug})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["outcome"] == "found"
    assert payload["slug"] == slug
    assert payload["fm"]["company"] == "Example Ltd"
    assert "body" in payload
    assert "whatever it says about itself" in payload["content_warning"]


def test_call_tool_round_trips_doctor_with_real_arguments():
    async def _run():
        from mcp import Client
        server = build_server(Config())
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("doctor", {"offline": True})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert "exit_code" in payload
    assert "checks" in payload


def test_call_tool_reports_a_real_sdk_error_for_a_tool_level_exception(tmp_path):
    """A tool-level exception (list_leads raising ValueError for an unknown status)
    must degrade to a proper SDK-level tool error, never crash the server."""
    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path)))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("list_leads", {"statuses": ["not-a-real-status"]})

    result = asyncio.run(_run())
    assert result.is_error is True
    assert "not-a-real-status" in result.content[0].text


# #131: the five write tools (dismiss_lead, apply_record, cv_run, cv_signoff,
# create_lead) are registered only when build_server(config, write=True) is called --
# the tests above all use the default write=False and so never see them. The two
# tools/list tests below prove that omission/inclusion is REAL at the SDK layer (a
# reviewer could accept Task 11's `if write:` structure while still doubting whether
# tools/list genuinely reflects it), and the round-trip tests below that prove one
# write tool's dispatch and a concurrency sanity check both work end-to-end through
# the real SDK, not just via the direct-call unit tests in tests/test_mcpserver.py.

def test_tools_list_under_default_write_false_returns_exactly_the_original_four():
    async def _run():
        from mcp import Client
        server = build_server(Config())   # write defaults to False
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    result = asyncio.run(_run())
    names = {t.name for t in result.tools}
    assert names == {"list_leads", "get_lead", "doctor", "health"}, (
        "the five write tools must be genuinely ABSENT from tools/list under the "
        "default (no --write) registration, not merely refusing at call time")


def test_tools_list_under_write_true_returns_all_nine_with_exact_schemas():
    async def _run():
        from mcp import Client
        server = build_server(Config(), write=True)
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    result = asyncio.run(_run())
    by_name = {t.name: t for t in result.tools}
    assert set(by_name) == {
        "list_leads", "get_lead", "doctor", "health",
        "dismiss_lead", "apply_record", "cv_run", "cv_signoff", "create_lead",
    }
    for tool in by_name.values():
        props = tool.input_schema.get("properties", {})
        assert "sluice" not in props, (
            f"{tool.name}'s schema leaked the injected `sluice` parameter: {props}")
    assert set(by_name["dismiss_lead"].input_schema["properties"]) == {"lead", "reason"}
    assert "note_tag" not in by_name["dismiss_lead"].input_schema["properties"]
    assert set(by_name["apply_record"].input_schema["properties"]) == {"lead", "ats", "url"}
    assert set(by_name["cv_run"].input_schema["properties"]) == {"lead", "backend"}
    # Minor #9 (final whole-branch review): `backend` was an unconstrained str,
    # so an invalid value surfaced only as a runtime BackendError -- typing it
    # Literal[...] (mirroring Sluice._BACKEND_ROLES/_BACKEND_ALIASES, the exact
    # set cli.py's own --backend argparse `choices` already constrains to) puts
    # the same constraint into the client-facing schema as a genuine `enum`.
    assert by_name["cv_run"].input_schema["properties"]["backend"]["enum"] == [
        "auto", "primary", "fallback", "claude-max", "deepseek"]
    cv_signoff_props = set(by_name["cv_signoff"].input_schema["properties"])
    assert cv_signoff_props == {"lead", "discard", "confirm_token"}
    # decision 13: no default makes promote reachable by omission -- discard's own
    # default (False) plus confirm_token's own default (None) together land on the
    # needs_confirmation branch, never a silent promote.
    schema_props = by_name["cv_signoff"].input_schema["properties"]
    assert schema_props.get("discard", {}).get("default") is False
    assert schema_props.get("confirm_token", {}).get("default") is None
    assert set(by_name["create_lead"].input_schema["properties"]) == {
        "title", "company", "url", "location", "salary", "job_type", "source"}


def test_call_tool_dismiss_lead_round_trips_through_the_real_dispatch(tmp_path):
    v = Vault(str(tmp_path / "vault"))
    v.upsert(Lead(source="s", search="q", title="Example Role", company="Example Ltd",
                  url="https://example.invalid/1"))
    slug = next(n for n in v.read_leads() if n.fm.get("url") == "https://example.invalid/1").slug

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")), write=True)
        async with Client(server, raise_exceptions=True) as client:
            dismissed = await client.call_tool(
                "dismiss_lead", {"lead": slug, "reason": "no longer a fit"})
            fetched = await client.call_tool("get_lead", {"lead": slug})
            return dismissed, fetched

    dismissed, fetched = asyncio.run(_run())
    assert json.loads(dismissed.content[0].text)["outcome"] == "dismissed"
    assert json.loads(fetched.content[0].text)["status"] == "dismiss"


def test_call_tool_concurrency_sanity_check_reaches_dismiss_lead_under_overlap(tmp_path):
    """Decision 17 / Testing item 12: NOT the guard's safety proof -- that's the
    50-round Barrier test in tests/test_leads_dismiss.py (item 12a), at the
    Sluice.dismiss_lead layer. This is only a sanity check that the SDK path reaches
    Sluice.dismiss_lead at all under concurrent dispatch, now that Task 11 confirmed
    MCPServer genuinely dispatches to separate worker threads."""
    v = Vault(str(tmp_path / "vault"))
    v.upsert(Lead(source="s", search="q", title="Example Role", company="Example Ltd",
                  url="https://example.invalid/1"))
    slug = next(n for n in v.read_leads() if n.fm.get("url") == "https://example.invalid/1").slug

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")), write=True)
        async with Client(server, raise_exceptions=True) as client:
            return await asyncio.gather(
                client.call_tool("dismiss_lead", {"lead": slug, "reason": "r1"}),
                client.call_tool("dismiss_lead", {"lead": slug, "reason": "r2"}),
            )

    a, b = asyncio.run(_run())
    outcomes = sorted(json.loads(r.content[0].text)["outcome"] for r in (a, b))
    assert outcomes == ["dismissed", "unchanged"]
