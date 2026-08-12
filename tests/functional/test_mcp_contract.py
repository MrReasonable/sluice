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
