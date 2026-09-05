"""MCP registration contract (#105, extended by #131, #164 and #175): `tools/list`
reflects the real tools -- the read-only tools (list_leads, get_lead, doctor, health,
list_evidence) under the default write=False, plus the write tools under write=True
-- names, and schemas that never leak the injected
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
from sluice.core.protocols import EVIDENCE_KINDS
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
    # #175: `propose_evidence` joins the WRITE tier, so it must be absent here. This
    # exact-set `==` is what enforces that -- deliberately not a separate `not in`
    # clause beside it, which would only restate what the set already says while
    # being free to go stale on its own.
    assert set(by_name) == {"list_leads", "get_lead", "doctor", "health", "list_evidence"}
    for tool in by_name.values():
        props = tool.input_schema.get("properties", {})
        assert "sluice" not in props, (
            f"{tool.name}'s schema leaked the injected `sluice` parameter: {props}")
    assert set(by_name["list_leads"].input_schema["properties"]) == {"statuses", "limit"}
    assert set(by_name["get_lead"].input_schema["properties"]) == {"lead"}
    assert set(by_name["doctor"].input_schema["properties"]) == {"offline"}
    assert by_name["health"].input_schema.get("properties", {}) == {}
    assert set(by_name["list_evidence"].input_schema["properties"]) == {"kind", "pending"}


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
    (tests/test_mcpserver.py) is what pins there is no write or verify tool alongside
    it. Seeds one verified skills entry directly on disk,
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
                                "Evidence": "E", "Signal Value": "S"}}],
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
    duplicating that set here) -- this proves an invalid value past that schema still
    reaches Sluice.backend's own role guard and comes back as a proper tool error either
    way, rather than crashing the server.

    cv.renderer is pointed at 'script' with a real (never-executed) file so compose_cv's
    renderer construction -- which runs BEFORE the backend role guard -- succeeds without
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
        voice_flags=["flag\tThis reads like a press release."])
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
    assert names == {"list_leads", "get_lead", "doctor", "health", "list_evidence"}, (
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
    assert set(by_name) == {
        "list_leads", "get_lead", "doctor", "health", "list_evidence",
        "dismiss_lead", "apply_record", "cv_run", "cv_signoff", "create_lead",
        "propose_evidence",
    }
    # #175's whole scope in one line: a PROPOSE tool ships, a VERIFY tool does not --
    # at this, the HIGHEST privilege level, which is the only level where the claim
    # says anything (write=False forbids both by the set assertion above). Promotion
    # to citable stays interactive-only; that is #164's central decision, and the
    # exact-set `==` immediately above is what actually holds it against an
    # unanticipated name. This clause is the readable diagnosis when it breaks --
    # a substring, so `verify_evidence`, `evidence_verify` and `bulk_verify` all trip
    # it, and a future read tool with `verify` in its name should be renamed rather
    # than have this narrowed.
    assert not [n for n in by_name if "verify" in n], (
        f"a verify tool is registered at --write: {sorted(by_name)} -- promoting an "
        f"evidence entry to citable is interactive-only (#164), and a second "
        f"promotion path is a new trust root, not a convenience")
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
    # A MISSING "default" key also reads as None via .get(...) -- assert the key is
    # actually PRESENT first, or a dropped default would pass this silently (round-6
    # review finding).
    assert "default" in schema_props.get("confirm_token", {})
    assert schema_props["confirm_token"]["default"] is None
    assert set(by_name["create_lead"].input_schema["properties"]) == {
        "title", "company", "url", "location", "salary", "job_type", "source"}
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
    # Non-empty is the whole of `Store.propose_evidence`'s handle contract -- it is
    # OPAQUE, so asserting anything about its SHAPE (that it is a path, that it ends
    # in .md) would pin this test to the vault store rather than to the contract.
    assert body["handle"]
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
    measured against the real SDK for ValueError, FileExistsError and OSError alike
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
    # The store's OWN message, forwarded verbatim: it is the only thing that says
    # WHICH set the name clashed in (the inbox, or the already-citable corpus), and
    # only the store knows. Asserted by a substring of the name rather than the whole
    # message so the store stays free to reword it.
    assert "Example platform rebuild".lower().split()[0] in refused["detail"].lower()
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
