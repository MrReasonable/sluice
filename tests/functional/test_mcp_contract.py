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


# `health` above takes no arguments -- the SDK's JSON-Schema-driven argument binding
# is never exercised by it. list_leads/get_lead/doctor all take real parameters, and
# each is driven through call_tool here too, so a future SDK upgrade that changes how
# `list[str] | None`/`str`/`bool` parameters are coerced cannot silently break dispatch
# while every other test (both these and the direct-call unit tests in
# tests/test_mcpserver.py, which bypass the SDK entirely) stays green.
#
# list_leads/get_lead touch the store, so each gets an explicit Config(vault_dir=
# str(tmp_path)) rather than relying solely on the autouse _pin_paths fixture
# (tests/conftest.py) that already sandboxes VAULT_DIR for every test under tests/ --
# matching tests/test_mcpserver.py's own explicit Vault(str(tmp_path)) convention,
# so this file's hermeticity doesn't depend on a reader knowing about a fixture
# defined elsewhere.

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
