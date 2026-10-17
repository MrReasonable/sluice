"""MCP registration contract (#105, extended by #131, #164, #175 and #195): `tools/list`
reflects the real tools -- the read-only tools under the default write=False, plus the
write tools under write=True, each set pinned by an exact `==` below rather than restated
here -- names, and schemas that never leak the injected
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
import os

import pytest

import sluice.mcpserver as mcpserver
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import EVIDENCE_KINDS
from sluice.core.vault import Vault
from sluice.mcpserver import build_server
from tests.conftest import UNREADABLE_DIR
from tests.test_prep_brief_facade import KIND, _kind_dir, _seed, _story


def test_tools_list_names_and_schemas_never_leak_sluice():
    async def _run():
        from mcp import Client
        server = build_server(Config())
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    result = asyncio.run(_run())
    by_name = {t.name: t for t in result.tools}
    # #175: `propose_evidence` joins the WRITE tier, so it must be absent here. This
    # exact-set `==` is what enforces that -- deliberately not a separate `not in`
    # clause beside it, which would only restate what the set already says while
    # being free to go stale on its own.
    assert set(by_name) == {"list_leads", "get_lead", "doctor", "health", "list_evidence",
                           "setup_status", "prep_brief"}
    for tool in by_name.values():
        props = tool.input_schema.get("properties", {})
        assert "sluice" not in props, (
            f"{tool.name}'s schema leaked the injected `sluice` parameter: {props}")
    assert set(by_name["list_leads"].input_schema["properties"]) == {"statuses", "limit"}
    assert set(by_name["get_lead"].input_schema["properties"]) == {"lead"}
    assert set(by_name["doctor"].input_schema["properties"]) == {"offline"}
    assert by_name["health"].input_schema.get("properties", {}) == {}
    assert set(by_name["list_evidence"].input_schema["properties"]) == {"kind", "pending"}
    # #195: the slug and nothing else. A `write` property here would hand the model the one
    # argument this read-only tool exists not to have.
    assert set(by_name["prep_brief"].input_schema["properties"]) == {"lead"}
    assert by_name["prep_brief"].input_schema.get("required") == ["lead"]


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


def test_call_tool_round_trips_list_evidence_with_real_arguments(tmp_path):
    """Evidence entries are user-authored, not scraped -- unlike leads -- but still
    reach an LLM through this tool, so a non-empty response carries a `content_warning`
    of its own, in wording that names that provenance rather than borrowing the scraped
    or derived one. Asserted HERE and not only in tests/test_mcpserver.py because the
    tool's own docstring does NOT travel with a result: what an MCP client actually
    receives is this payload, so the round trip through the SDK's real dispatch is the
    layer that proves the warning reaches it. Task 9's absence sweep
    (tests/test_mcpserver.py) is what pins which evidence write/verify tools exist
    alongside it, and at which level. Seeds one verified skills entry directly on disk,
    mirroring tests/test_evidence_store.py's own `_seed` helper, rather than
    round-tripping through propose_evidence/verify_evidence's CAS machinery, which
    this test has no need to exercise."""
    base = tmp_path / "vault"
    base_dir = base / EVIDENCE_KINDS["skills"].relpath
    base_dir.mkdir(parents=True)
    (base_dir / "alpha.md").write_text(
        "---\nProficiency: P\nDomain: D\nEvidence: E\nSignal Value: S\n"
        "verified: 2026-01-01\n---\nBody text.\n", encoding="utf-8")

    import sluice.mcpserver as mcpserver_mod
    from sluice.core.leads import (
        UNTRUSTED_DERIVED_CONTENT_WARNING,
        UNTRUSTED_SCRAPED_CONTENT_WARNING,
    )

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(base)))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("list_evidence", {"kind": "skills"})

    result = asyncio.run(_run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload == {
        "kind": "skills", "pending": False, "count": 1,
        "entries": [{"title": "alpha", "verified": "2026-01-01",
                     "fields": {"Proficiency": "P", "Domain": "D",
                                "Evidence": "E", "Signal Value": "S",
                                "Label": ""}}],
        "content_warning": mcpserver_mod._LIST_EVIDENCE_CONTENT_WARNING,
    }
    # The shared tail, spelled out the same way the list_leads and get_lead round trips
    # above spell theirs: it is the clause a reworded warning would quietly drop.
    assert "whatever it says about itself" in payload["content_warning"]
    # And the provenance is named honestly rather than borrowed: an entry the user typed
    # is neither scraped nor LLM-composed, so neither of the other two warnings may appear.
    assert UNTRUSTED_SCRAPED_CONTENT_WARNING not in payload["content_warning"]
    assert UNTRUSTED_DERIVED_CONTENT_WARNING not in payload["content_warning"]


def test_call_tool_reports_a_real_sdk_error_for_a_tool_level_exception(tmp_path):
    """A tool-level exception (list_leads raising ValueError for an unknown status)
    must degrade to a proper SDK-level tool error, never crash the server.

    That degrade -- `is_error is True` rather than a dead connection -- is the property
    this test exists for, and it holds unchanged.

    What it no longer asserts is that the OFFENDING VALUE reaches the caller. Until
    mcp 2.0.0 the SDK re-raised with the original exception text, so a client saw
    `not-a-real-status` and could act on it. mcp 2.1.0 wraps every unhandled tool
    exception as `UnexpectedToolError(f"Error executing tool {name}")`, discarding the
    detail -- deliberate redaction upstream, not a regression to work around, and the
    reason is sound: a tool that lets an arbitrary exception escape is leaking whatever
    that exception happened to carry.

    Measured on the real SDK, 2026-08-24: 2.1.0 changes exactly this one assertion
    across the whole suite (4880 of 4881 tests unaffected), so nothing else in this
    repo depended on the old behaviour.

    Recovering the detail is real work rather than a test edit: `sluice/mcpserver.py`'s
    handlers would have to raise an MCP-native error type that survives the wrapper,
    instead of letting a bare `ValueError` escape into it. Until they do, a client gets
    a correct-but-vague error, and this test says so rather than pretending otherwise.
    The tool NAME does survive, and is asserted because it is the one piece of the
    diagnostic the SDK still guarantees.
    """
    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path)))
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("list_leads", {"statuses": ["not-a-real-status"]})

    result = asyncio.run(_run())
    assert result.is_error is True
    assert "list_leads" in result.content[0].text


def test_call_tool_cv_run_reports_a_real_sdk_error_for_an_invalid_backend(tmp_path, monkeypatch):
    """Companion to the ValueError test above, for cv_run's own BackendError->ValueError
    translation (sluice/mcpserver.py, decision 14): the SAME degrade-to-is_error
    contract must hold through the real dispatch, not just at the direct-call layer
    tests/test_mcpserver.py already covers. `cv_run`'s `backend` is schema-typed as an
    `enum` (pinned above in test_tools_list_under_write_true_returns_every_tool_with_exact_
    schemas, which is where 'every valid choice is accepted' is already covered without
    duplicating that set here) -- this proves an invalid value comes back as a proper
    tool error rather than crashing the server.

    It does NOT prove the value reaches `Sluice.backend`'s override guard, which this
    docstring claimed until #175 measured otherwise. `"bogus"` is rejected by pydantic's
    ARGUMENT VALIDATION against that enum, which never enters the tool body at all --
    which is also why the offending value survives into the message here while
    `list_leads`' unknown-status ValueError above is redacted to "Error executing tool
    list_leads": they are two different SDK paths, not one behaviour with an
    inconsistency. The override guard is still exercised, at the direct-call layer in
    tests/test_mcpserver.py, where no schema stands in front of it.

    cv.renderer is pointed at 'script' with a real (never-executed) file so compose_cv's
    renderer construction -- which runs BEFORE the backend override guard -- succeeds without
    WeasyPrint installed in this environment; the guard then raises before any backend
    credential is ever needed."""
    script = tmp_path / "render.py"
    script.write_text("#!/usr/bin/env python3\n")

    def _fake_cv_config():
        from sluice.cv.config import CvConfig
        c = CvConfig()
        c.renderer = "script"
        c.render_script = str(script)
        return c

    monkeypatch.setattr("sluice.cv.config.load_cv_config", _fake_cv_config)

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")), write=True)
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool(
                "cv_run", {"lead": "nothing here", "backend": "bogus"})

    result = asyncio.run(_run())
    assert result.is_error is True
    assert "bogus" in result.content[0].text


def test_call_tool_cv_run_round_trips_slop_and_voice_flags_through_real_json(
        tmp_path, monkeypatch):
    """#167 Task 16: `CvResult.slop` had no reader since it was added, and
    `voice_flags` is new. tests/test_mcpserver.py already pins that `cv_run()`'s own
    projection reads both fields and stamps the untrusted-content warning; what THIS
    file exists to prove (see its own module docstring) is the one thing that unit
    layer cannot -- that a populated `slop`/`voice_flags` list actually SURVIVES a
    real JSON-RPC round trip through the SDK's own serialization, not just an
    in-process dict comparison.

    `Sluice.compose_cv` is monkeypatched to a canned CvResult (the same shape
    test_call_tool_cv_run_reports_a_real_sdk_error_for_an_invalid_backend above
    already establishes as this file's precedent for stubbing past compose_cv's own
    heavy machinery -- vault/backend/renderer construction is not what this test is
    about) so no real vault, backend or renderer needs constructing."""
    from sluice.core.app import Sluice
    from sluice.core.leads import UNTRUSTED_DERIVED_CONTENT_WARNING
    from sluice.cv.engine import CvResult

    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf",
        slop=["SLOP leverage: I leverage strong delivery patterns."],
        voice_flags=["flag\tThis reads like a press release."],
        terms=["UNBUNDLED TERM 'Examplequery': named nowhere in your evidence: x"])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")), write=True)
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool(
                "cv_run", {"lead": "Example Foundry - Analyst"})

    out = asyncio.run(_run())
    assert out.is_error is False
    payload = json.loads(out.content[0].text)
    assert payload["slop"] == ["SLOP leverage: I leverage strong delivery patterns."]
    assert payload["voice_flags"] == ["flag\tThis reads like a press release."]
    assert payload["terms"] == [
        "UNBUNDLED TERM 'Examplequery': named nowhere in your evidence: x"]
    assert UNTRUSTED_DERIVED_CONTENT_WARNING in payload["content_warning"]


# #131: the write tools (dismiss_lead, apply_record, cv_run, cv_signoff, create_lead,
# plus #175's propose_evidence) are registered only when build_server(config, write=True)
# is called -- deliberately NO COUNT stated here. This comment read "the five write
# tools" and was already wrong the moment #175 registered a sixth, which is this repo's
# most-repeated finding shape applied to its own test file. The exact-set `==`
# assertions below are what pin the roster; a prose count cannot and never could.
# The tests above all use the default write=False and so never see them. The two
# tools/list tests below prove that omission/inclusion is REAL at the SDK layer (a
# reviewer could accept Task 11's `if write:` structure while still doubting whether
# tools/list genuinely reflects it), and the round-trip tests below that prove one
# write tool's dispatch and a concurrency sanity check both work end-to-end through
# the real SDK, not just via the direct-call unit tests in tests/test_mcpserver.py.

def test_tools_list_under_default_write_false_returns_exactly_the_original_read_tools():
    async def _run():
        from mcp import Client
        server = build_server(Config())   # write defaults to False
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    result = asyncio.run(_run())
    names = {t.name for t in result.tools}
    assert names == {"list_leads", "get_lead", "doctor", "health", "list_evidence",
                     "setup_status", "prep_brief"}, (
        "every write tool must be genuinely ABSENT from tools/list under the default "
        "(no --write) registration, not merely refusing at call time -- #175's "
        "propose_evidence included, since a read-only registration is exactly the one "
        "an agent steered by content it just read through get_lead should not be able "
        "to reach the evidence corpus from")


def test_tools_list_under_write_true_returns_every_tool_with_exact_schemas():
    async def _run():
        from mcp import Client
        server = build_server(Config(), write=True)
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    result = asyncio.run(_run())
    by_name = {t.name: t for t in result.tools}
    # Exactly ONE promotion tool, and only at --write: `verify_evidence`, which promotes
    # only what the human ticks in a client-shown form (sluice/mcpserver.py::
    # verify_evidence_step). Ordered BEFORE the exact-set `==` below so its message is the
    # one a failure shows; the `==` is still what catches a second promotion tool under
    # any other name. A substring, so `bulk_verify` or `evidence_verify` trips it too.
    assert [n for n in by_name if "verify" in n] == ["verify_evidence"], (
        f"verify-shaped tools at --write: {sorted(n for n in by_name if 'verify' in n)} -- "
        f"exactly one promotion path may exist here, and it must put a human's tick "
        f"between the model and citability")
    assert set(by_name) == {
        "list_leads", "get_lead", "doctor", "health", "list_evidence", "setup_status",
        "prep_brief", "dismiss_lead", "apply_record", "cv_run", "cv_signoff", "create_lead",
        "propose_evidence", "verify_evidence", "setup_save",
    }
    for tool in by_name.values():
        props = tool.input_schema.get("properties", {})
        assert "sluice" not in props, (
            f"{tool.name}'s schema leaked the injected `sluice` parameter: {props}")
    assert set(by_name["dismiss_lead"].input_schema["properties"]) == {"lead", "reason"}
    assert "note_tag" not in by_name["dismiss_lead"].input_schema["properties"]
    assert set(by_name["apply_record"].input_schema["properties"]) == {"lead", "ats", "url"}
    assert set(by_name["cv_run"].input_schema["properties"]) == {"lead", "backend"}
    # #195: still `{lead}` at --write. The tool is read-only at EVERY level, so the write
    # registration must not grow it a `write` argument either.
    assert set(by_name["prep_brief"].input_schema["properties"]) == {"lead"}
    assert set(by_name["verify_evidence"].input_schema["properties"]) == {"kind", "names"}, (
        "verify_evidence must take no argument that could approve on the human's behalf")
    # setup_save takes the agreed changes and the version setup_status returned, both required:
    # a save with no version would have nothing to be refused as stale against.
    schema = by_name["setup_save"].input_schema
    assert set(schema["properties"]) == {"changes", "version"}
    assert set(schema.get("required", [])) == {"changes", "version"}
    assert schema["properties"]["version"]["type"] == "string"
    items = schema["properties"]["changes"]["items"]
    if "$ref" in items:   # pydantic emits the TypedDict as a $defs entry, not inline
        items = schema["$defs"][items["$ref"].rsplit("/", 1)[-1]]
    assert set(items.get("properties", {})) == {
        "kind", "target", "value", "clear", "label", "url", "remove"}
    # A list setting's value travels as a list of strings, so an item holding a comma stays one
    # item; text stays accepted for every setting.
    assert sorted(items["properties"]["value"]["anyOf"], key=str) == sorted(
        [{"type": "string"}, {"type": "array", "items": {"type": "string"}}], key=str)
    # Minor #9 (final whole-branch review): `backend` was an unconstrained str,
    # so an invalid value surfaced only as a runtime BackendError -- typing it
    # Literal[...] puts the constraint into the client-facing schema as a genuine
    # `enum`. Since #333 it names PROVIDERS (the registry's own roster) and is
    # optional: omitted, cv uses its configured backend.
    from sluice.core.backends import DEFAULT_MODELS
    backend_schema = by_name["cv_run"].input_schema["properties"]["backend"]
    enums = [branch["enum"] for branch in backend_schema["anyOf"] if "enum" in branch]
    assert len(enums) == 1 and set(enums[0]) == set(DEFAULT_MODELS)
    assert {"type": "null"} in backend_schema["anyOf"]
    assert backend_schema.get("default") is None
    cv_signoff_props = set(by_name["cv_signoff"].input_schema["properties"])
    assert cv_signoff_props == {"lead", "discard", "confirm_token"}
    # decision 13: no default makes promote reachable by omission -- discard's own
    # default (False) plus confirm_token's own default (None) together land on the
    # needs_confirmation branch, never a silent promote.
    schema_props = by_name["cv_signoff"].input_schema["properties"]
    assert schema_props.get("discard", {}).get("default") is False
    # A MISSING "default" key also reads as None via .get(...) -- assert the key is
    # actually PRESENT first, or a dropped default would pass this silently (round-6
    # review finding).
    assert "default" in schema_props.get("confirm_token", {})
    assert schema_props["confirm_token"]["default"] is None
    assert set(by_name["create_lead"].input_schema["properties"]) == {
        "title", "company", "url", "location", "salary", "job_type", "source",
        "job_description"}
    # #175. `fields` is the caller-supplied mapping `Store.propose_evidence`'s contract
    # requires a store to reject an undeclared key from BY NAME -- `verified` among
    # them. Pinning the property SET is what turns a `verified` parameter sprouting on
    # this tool into a test failure rather than a silent new trust root, and it is the
    # only place that check can live: the store's refusal is keyed on the mapping's
    # CONTENTS, which no schema assertion can reach.
    assert set(by_name["propose_evidence"].input_schema["properties"]) == {
        "kind", "name", "fields", "body"}


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


def test_call_tool_propose_evidence_lands_pending_and_never_citable(tmp_path):
    """#175's load-bearing round trip, driven through the real SDK dispatch: an entry
    an MCP CLIENT proposed reaches the PENDING queue and does NOT reach the citable
    set. That gap is the entire safety argument for shipping a write tool here at all
    -- `read_evidence` cannot see the inbox, so an LLM-authored body is invisible to
    the CV fabrication gate until a human runs `job-sluice <kind> verify`.

    Read back through this server's OWN `list_evidence` tool rather than off the
    vault, deliberately: `pending=False` is the view the composer's corpus is built
    from, so a proposal surfacing THERE is precisely the harm, and asking the same
    question an agent would ask is what proves the two views disagree about this
    entry. A vault read would prove a file landed somewhere and nothing about which
    reader can see it."""
    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")), write=True)
        async with Client(server, raise_exceptions=True) as client:
            proposed = await client.call_tool("propose_evidence", {
                "kind": "experience", "name": "Example platform rebuild",
                "fields": {"Company": "Example Ltd", "Best For": "platform"},
                "body": "Rebuilt the ingest path.",
            })
            citable = await client.call_tool(
                "list_evidence", {"kind": "experience", "pending": False})
            pending = await client.call_tool(
                "list_evidence", {"kind": "experience", "pending": True})
            return proposed, citable, pending

    proposed, citable, pending = asyncio.run(_run())
    assert proposed.is_error is False
    body = json.loads(proposed.content[0].text)
    assert body["outcome"] == "proposed"
    # No filesystem path may reach an MCP client, and `Store.propose_evidence`'s handle
    # IS the written path for the vault store -- so forwarding it disclosed the user's
    # absolute vault directory, and the directory it names is the one a hand-placed
    # note carrying `verified:` would be citable from. This module strips paths
    # everywhere else for exactly that reason (`list_evidence` omits `path`, `get_lead`
    # returns `slug` not `ref`), and nothing on `origin/main` disclosed the vault dir at
    # any privilege level.
    #
    # Asserted over the WHOLE serialized response, not just the key that leaked: a
    # `handle`-shaped value re-appearing under any other name is the same disclosure,
    # and `"handle" not in body` alone would not see it.
    vault_dir = str(tmp_path / "vault")
    assert vault_dir not in proposed.content[0].text, (
        f"the response discloses the vault path: {proposed.content[0].text}")
    assert "handle" not in body, (
        "the store's opaque handle is back in the response -- for the vault store it "
        "is the absolute path of the written note")
    assert json.loads(citable.content[0].text)["count"] == 0, (
        "an MCP-proposed entry reached the CITABLE set -- the fabrication gate can "
        "now license a body an LLM authored")
    pending_body = json.loads(pending.content[0].text)
    assert pending_body["count"] == 1, (
        "the proposal did not reach the pending queue either, so the assertion above "
        "would hold just as well for a tool that wrote nothing at all")
    # None, not "" -- the citability key is ABSENT on a proposal, and a verified entry
    # carries the review DATE there. Asserted because it is the discriminating value:
    # `count == 0` above would also hold for a proposal that somehow arrived stamped
    # but filed out of the citable directory.
    assert pending_body["entries"][0]["verified"] is None


def test_call_tool_propose_evidence_refuses_a_name_already_taken(tmp_path):
    """A second proposal at the same name is REFUSED and reported as a structured
    outcome, not raised. Both halves matter and for different reasons.

    Refused: `Store.propose_evidence` refuses rather than overwrites, so an agent
    cannot silently replace an entry a human already reviewed the name of.

    Structured: mcp 2.1.1 wraps EVERY unhandled tool exception as
    `UnexpectedToolError("Error executing tool <name>")` and discards the message --
    measured against the real SDK for ValueError, FileExistsError and OSError alike,
    and re-measured unchanged on 2.2.0
    (the neighbouring cv_run test's surviving "bogus" comes from pydantic's own enum
    VALIDATION, which never enters the tool body, not from an exception surviving the
    wrapper). So letting FileExistsError propagate would hand the agent a string that
    cannot be told apart from an unwritable vault, and the one correct recovery --
    pick another name -- would be unreachable."""
    args = {"kind": "experience", "name": "Example platform rebuild",
            "fields": {"Company": "Example Ltd"}, "body": "Rebuilt the ingest path."}

    async def _run():
        from mcp import Client
        server = build_server(Config(vault_dir=str(tmp_path / "vault")), write=True)
        async with Client(server, raise_exceptions=True) as client:
            first = await client.call_tool("propose_evidence", args)
            second = await client.call_tool("propose_evidence", args)
            pending = await client.call_tool(
                "list_evidence", {"kind": "experience", "pending": True})
            return first, second, pending

    first, second, pending = asyncio.run(_run())
    assert json.loads(first.content[0].text)["outcome"] == "proposed"
    assert second.is_error is False, (
        "the refusal reached the client as an SDK error, which discards the store's "
        "own message -- see this test's docstring")
    refused = json.loads(second.content[0].text)
    assert refused["outcome"] == "refused"
    assert "handle" not in refused, "a refusal wrote nothing, so it has no handle"
    # The store's OWN message, forwarded verbatim: it is the only thing that says WHICH
    # set the name clashed in (the inbox, or the already-citable corpus), and only the
    # store knows. Asserted on the REDUCED SLUG rather than the whole message, so the
    # store stays free to reword the sentence around it -- but the slug itself is the
    # part a caller acts on, since it is the identity `job-sluice <kind> verify` lists.
    #
    # It used to assert `"Example platform rebuild".lower().split()[0]`, which is just
    # "example" -- satisfied by almost any message this suite could produce, including
    # one naming a completely different entry.
    assert "example-platform-rebuild" in refused["detail"].lower(), (
        f"the refusal does not name the clashing entry: {refused['detail']!r}")
    assert json.loads(pending.content[0].text)["count"] == 1, (
        "the refused second call still wrote an entry")


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
    # "conflict" is a legitimate outcome of real overlap (Sluice.dismiss_lead maps a
    # sustained VaultConflict to it), so pin only what this sanity check claims: both
    # calls reached dismiss_lead, and exactly one of them wrote.
    assert set(outcomes) <= {"dismissed", "unchanged", "conflict"}, outcomes
    assert outcomes.count("dismissed") == 1, outcomes


def test_initialize_reports_the_installed_version():
    """`serverInfo.version` is what a client shows beside the server, and the SDK's
    default is an empty string (2026-10-11, measured on the first real `mcp install`: a
    v4.4.0 server answered `version: ''`). Asserted against `sluice.__version__`, the
    version's one home, rather than a literal that a release bump would turn red."""
    from sluice import __version__

    async def _run():
        from mcp import Client
        server = build_server(Config())
        async with Client(server, raise_exceptions=True) as client:
            return client.server_info

    info = asyncio.run(_run())
    assert info.version == __version__
    assert __version__


def test_the_career_interview_prompt_is_registered_and_served_through_the_sdk():
    """The coach is a PROMPT, not a tool, so `tools/list` never shows it: this drives
    `prompts/list` and `prompts/get` through the real SDK. Registered at the DEFAULT
    privilege level (write=False), because a read-only server's coach can still interview
    and research -- the prompt itself tells the user to restart with `--write` before the
    save. Exact-set `==` so a second prompt cannot arrive unreviewed. It declares no
    arguments: see the assertion."""
    async def _run():
        from mcp import Client
        server = build_server(Config())
        async with Client(server, raise_exceptions=True) as client:
            return (await client.list_prompts(),
                    await client.get_prompt("career_interview", {}))

    listed, got = asyncio.run(_run())
    assert {p.name for p in listed.prompts} == {"career_interview", "interview_prep"}
    prompt = next(p for p in listed.prompts if p.name == "career_interview")
    # No arguments (2026-10-09, measured on Claude Code 2.1.295): the client splits text after
    # the slash command on whitespace and delivers only the first word, so a free-text argument
    # was silently truncated. The coach's opening asks what the user wants instead.
    assert (prompt.arguments or []) == []
    text = "".join(m.content.text for m in got.messages)
    assert "setup_status" in text


@pytest.mark.parametrize("write", [False, True])
def test_the_interview_prep_prompt_is_registered_and_served_at_both_levels(write):
    """The prep coach (#195), through the real SDK at each privilege level. Exact-set `==` on
    the prompt roster, as above. No arguments, for the same measured reason as career_interview:
    a slug holds spaces and the client delivers only an argument's first word. Its text names
    the read-only `prep_brief` tool at both levels, and `propose_evidence` only where that tool
    is registered: a read-only coach told to call it would reach for a tool that is not there."""
    async def _run():
        from mcp import Client
        server = build_server(Config(), write=write)
        async with Client(server, raise_exceptions=True) as client:
            return (await client.list_prompts(), await client.list_tools(),
                    await client.get_prompt("interview_prep", {}))

    listed, tools, got = asyncio.run(_run())
    assert {p.name for p in listed.prompts} == {"career_interview", "interview_prep"}
    prompt = next(p for p in listed.prompts if p.name == "interview_prep")
    assert (prompt.arguments or []) == []
    text = "".join(m.content.text for m in got.messages)
    assert "prep_brief" in text
    assert ("propose_evidence" in text) is write
    # Scope for the line above: the tool the text names exists on exactly the level it is named.
    assert ("propose_evidence" in {t.name for t in tools.tools}) is write


def test_setup_save_registered_description_states_the_save_rules():
    """A tool has two docstrings, and the client reads only the REGISTERED one: the rules the
    coach must keep before calling setup_save live there, read through the real SDK."""
    async def _run():
        from mcp import Client
        server = build_server(Config(), write=True)
        async with Client(server, raise_exceptions=True) as client:
            return await client.list_tools()

    (tool,) = [t for t in asyncio.run(_run()).tools if t.name == "setup_save"]
    desc = " ".join(tool.description.split())
    for phrase in ("playing every change back to the user in chat",
                   "hearing an explicit yes", '"stale"', "setup_status's `kinds`",
                   "`previous`", "`not_restorable`", "written, set_aside or failed",
                   "A list setting's `value` is a list of strings, one item each, never "
                   "comma-joined", "a list, for a list setting"):
        assert phrase in desc, phrase
    assert "form" not in desc and "tick" not in desc


# ── prep_brief (#195) ───────────────────────────────────────────────────────────
# Each row drives the REAL tool through the SDK's dispatch, so the reply is the JSON a client
# receives. Leads and stories are seeded as literal note text through
# tests/test_prep_brief_facade.py's own helpers, whose company spellings the fixture-name
# collectors read; the role comes from the seeded `titles` fixture. The vault root is
# `tmp_path / "vault"` because tests/conftest.py pins `VAULT_DIR` there and the server's store
# resolves that env var first (see the comment above
# test_call_tool_round_trips_list_leads_with_real_arguments).

def _prep_root(tmp_path):
    return tmp_path / "vault"


def _prep_config(tmp_path):
    return Config(vault_dir=str(_prep_root(tmp_path)), dossier_dir=str(tmp_path / "dos"))


def _call_prep_brief(tmp_path, lead, *, write=False):
    """The tool's reply through the SDK, after asserting that no path is anywhere in the text
    the client receives: the vault root, resolved or not, never appears."""
    async def _run():
        from mcp import Client
        server = build_server(_prep_config(tmp_path), write=write)
        async with Client(server, raise_exceptions=True) as client:
            return await client.call_tool("prep_brief", {"lead": lead})

    result = asyncio.run(_run())
    assert result.is_error is False
    text = result.content[0].text
    for leak in {str(tmp_path), os.path.realpath(tmp_path)}:
        assert leak not in text, f"a path reached the prep_brief reply: {text}"
    return json.loads(text)


def _facade(tmp_path):
    """The facade the server wraps, on the same vault, for the reply to be compared against."""
    return Sluice(_prep_config(tmp_path))


_PREP_REPLY_KEYS = {"outcome", "slug", "status", "candidates", "suggestions", "more_suggestions",
                    "reason", "brief", "jd"}
_PREP_OK_WARNINGS = {"jd_warning", "lead_warning", "entries_warning"}


def test_prep_brief_ok_carries_the_facades_brief_jd_and_one_warning_per_provenance(
        tmp_path, titles):
    # Witnesses: rename `brief` in the reply; drop any warning; hand `jd` an empty `text`.
    root = _prep_root(tmp_path)
    _story(root, "Told")
    slug = _seed(root, role=titles[0][0].title())
    reply = _call_prep_brief(tmp_path, slug)
    r = _facade(tmp_path).prep_brief(lead=slug)
    assert r.outcome == "ok"
    # Scope: the fixture carries a JD and a story body, so the equalities below compare real
    # text rather than two empty strings.
    assert r.jd_text and any(e["body"] for k in r.brief["kinds"].values()
                             for e in k["entries"])
    assert set(reply) == _PREP_REPLY_KEYS | _PREP_OK_WARNINGS
    assert reply["outcome"] == "ok" and reply["slug"] == slug and reply["status"] == r.status
    assert reply["jd"] == {"source": r.jd_source, "text": r.jd_text}
    assert reply["brief"] == r.brief
    assert reply["jd_warning"] == mcpserver._PREP_JD_WARNING
    assert reply["lead_warning"] == mcpserver._PREP_LEAD_WARNING
    assert reply["entries_warning"] == mcpserver._PREP_ENTRIES_WARNING
    # Each warning names the part of the reply it is about, so a caller can tell them apart.
    assert "`jd`" in reply["jd_warning"]
    assert "`brief.lead`" in reply["lead_warning"]
    assert "`verified: false`" in reply["entries_warning"]


def test_prep_brief_not_found_suggests_with_its_own_warning(tmp_path, titles):
    # Witness: drop `suggestions_warning`. A case-changed slug is an exact-slug miss whose
    # substring suggestion is the real slug (tests/test_prep_brief_facade.py's own row).
    root = _prep_root(tmp_path)
    slug = _seed(root, role=titles[0][0].title())
    reply = _call_prep_brief(tmp_path, slug.upper())
    assert set(reply) == _PREP_REPLY_KEYS | {"suggestions_warning"}
    assert reply["outcome"] == "not_found"
    assert reply["suggestions"] == [slug] and reply["more_suggestions"] == 0
    assert reply["suggestions_warning"] == mcpserver._PREP_SUGGESTIONS_WARNING
    assert reply["brief"] is None and reply["jd"] == {"source": "none", "text": ""}


def test_prep_brief_not_found_passes_the_bound_and_the_count_of_the_rest(tmp_path):
    # Witness: hand `more_suggestions` a constant 0. Two more matching leads than the bound, so
    # the reply holds exactly the bound and counts the two it left out, as the facade does.
    # The roles come from the seeded pool behind the `titles` fixture, which itself carries
    # too few for this many leads.
    from sluice.core.prepbrief import SUGGESTION_LIMIT
    from tests.conftest import _title_pool
    root = _prep_root(tmp_path)
    roles = [t.title() for t in _title_pool() if "/" not in t][:SUGGESTION_LIMIT + 2]
    assert len(set(roles)) == SUGGESTION_LIMIT + 2   # scope: that many distinct leads
    for role in roles:
        _seed(root, role=role)
    reply = _call_prep_brief(tmp_path, "Example Ltd")
    r = _facade(tmp_path).prep_brief(lead="Example Ltd")
    assert reply["outcome"] == "not_found"
    assert len(reply["suggestions"]) == SUGGESTION_LIMIT and reply["more_suggestions"] == 2
    assert (reply["suggestions"], reply["more_suggestions"]) == (r.suggestions,
                                                                 r.more_suggestions)


def test_prep_brief_not_found_with_nothing_to_suggest_carries_no_warning(tmp_path, titles):
    # A warning attached to nothing trains a caller to skim past it (list_leads' rule).
    _seed(_prep_root(tmp_path), role=titles[0][0].title())
    reply = _call_prep_brief(tmp_path, "zz-no-lead-matches-this")
    assert reply["outcome"] == "not_found" and reply["suggestions"] == []
    assert set(reply) == _PREP_REPLY_KEYS


def test_prep_brief_ambiguous_names_the_candidates_and_picks_none(tmp_path, titles):
    root = _prep_root(tmp_path)
    slug = _seed(root, role=titles[0][0].title())
    leads = os.path.join(str(root), "Job Applications", "Job Leads")
    for sub in ("Active", "Archive"):
        os.makedirs(os.path.join(leads, sub))
    os.replace(os.path.join(leads, f"{slug}.md"), os.path.join(leads, "Active", f"{slug}.md"))
    _seed(root, role=titles[0][0].title(), name=os.path.join("Archive", slug))
    reply = _call_prep_brief(tmp_path, slug)
    assert set(reply) == _PREP_REPLY_KEYS
    assert reply["outcome"] == "ambiguous" and reply["candidates"] == [slug, slug]
    assert reply["brief"] is None and reply["slug"] == ""


def _assert_entries_unreadable(reply, slug):
    assert set(reply) == _PREP_REPLY_KEYS
    assert reply["outcome"] == "entries_unreadable" and reply["slug"] == slug
    assert reply["reason"].startswith(f"{KIND}: ")
    assert reply["brief"] is None


def test_prep_brief_entries_unreadable_on_a_story_that_is_not_utf8(tmp_path, titles):
    # Runs everywhere, unlike the chmod row below (which skips as root).
    root = _prep_root(tmp_path)
    slug = _seed(root, role=titles[0][0].title())
    os.makedirs(_kind_dir(root), exist_ok=True)
    with open(os.path.join(_kind_dir(root), "Bad.md"), "wb") as fh:
        fh.write(b"---\nCompany: Example Ltd\n---\n\xff\xfe broken\n")
    _assert_entries_unreadable(_call_prep_brief(tmp_path, slug), slug)


@UNREADABLE_DIR
def test_prep_brief_entries_unreadable_on_a_story_it_cannot_open(tmp_path, titles):
    root = _prep_root(tmp_path)
    path = _story(root, "Told")
    slug = _seed(root, role=titles[0][0].title())
    os.chmod(path, 0)
    try:
        reply = _call_prep_brief(tmp_path, slug)
    finally:
        os.chmod(path, 0o644)
    _assert_entries_unreadable(reply, slug)
    assert reply["reason"] == f"{KIND}: PermissionError: Permission denied"


@pytest.mark.parametrize("write", [False, True])
def test_prep_brief_never_writes_at_either_level(tmp_path, titles, write):
    # Witness: pass `write=True` in the tool. The fixture is WRITE-READY (a verified story
    # with a `Company:` line and a JD section), so the control at the end proves a write was
    # possible here: an unchanged note means the tool did not write, not that it could not.
    root = _prep_root(tmp_path)
    _story(root, "Told")
    slug = _seed(root, role=titles[0][0].title())
    path = os.path.join(str(root), "Job Applications", "Job Leads", f"{slug}.md")
    with open(path, "rb") as fh:
        before = fh.read()
    assert _call_prep_brief(tmp_path, slug, write=write)["outcome"] == "ok"
    with open(path, "rb") as fh:
        assert fh.read() == before
    assert _facade(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    with open(path, "rb") as fh:
        assert fh.read() != before
