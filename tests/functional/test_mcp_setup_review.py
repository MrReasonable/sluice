"""setup_review end to end through the real SDK, in memory (see test_mcp_verify_evidence.py
for the harness). Writes are read back from disk, never taken from the tool's report."""
import asyncio
import json
import os
from pathlib import Path

from sluice import mcpserver
from sluice.core.config import Config
from sluice.core.paths import config_file
from sluice.core.protocols import CRITERIA_RELPATH
from sluice.mcpserver import build_server
from sluice.onboard.plan import build_plan


def _call(changes, answer, *, with_callback=True, seen=None):
    from mcp import Client, types

    async def cb(context, params):
        if seen is not None:
            seen.append(params)
        action, content = answer(params)
        return types.ElicitResult(action=action, content=content)

    async def _run():
        kw = {"elicitation_callback": cb} if with_callback else {}
        async with Client(build_server(Config(), write=True), **kw) as client:
            r = await client.call_tool("setup_review", {"changes": changes})
            return json.loads(r.content[0].text)

    return asyncio.run(_run())


def _tick(*indexes):
    return lambda p: ("accept", {f"entry_{i}": True for i in indexes})


def _tick_all(p):
    return "accept", {k: True for k in p.requested_schema["properties"]}


def _assert_profile_row_ok(doctor_out):
    """The Judging Profile row is OK with the exact found-detail. Not `"found" in row`: a MISSING
    profile's detail is "not found -- ...", which contains that word too, so a stale holder
    pointed at a profile-less vault would pass."""
    row = next(c for c in doctor_out["components"] if c["subject"] == "Judging Profile")
    assert (row["state"], row["detail"]) == ("ok", "found")


def _existing_hunt():
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(build_plan({}).config_text)


def test_only_ticked_units_are_written():
    _existing_hunt()
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                 {"kind": "config", "target": "min_jd_chars", "value": "200"}], _tick(1))
    text = Path(config_file()).read_text()
    assert "lead_ttl_days: 30" in text and "min_jd_chars: 200" not in text
    assert {u["outcome"] for u in out["units"]} == {"written", "declined"}


def test_an_edit_between_form_and_retry_conflicts_only_that_note():
    _existing_hunt()
    vault = Path(os.environ["VAULT_DIR"])
    (vault / "Job Applications").mkdir(parents=True)
    # A short profile on purpose: the shipped default's "Who this candidate is" section is
    # tall enough to fill a whole form, which would page the config unit onto a second form
    # and leave this row with nothing to show that only the edited note conflicts.
    (vault / CRITERIA_RELPATH).write_text("# Judging Profile\n\n## Who this candidate is\n\nOld.\n")

    def edit_then_tick(params):
        (vault / CRITERIA_RELPATH).write_text("edited in Obsidian\n")
        return "accept", {k: True for k in params.requested_schema["properties"]}

    out = _call([{"kind": "profile", "target": "Who this candidate is", "value": "Example."},
                 {"kind": "config", "target": "lead_ttl_days", "value": "30"}], edit_then_tick)
    by = {u["unit"]: u["outcome"] for u in out["units"]}
    assert by["config:lead_ttl_days"] == "written"
    assert by["profile:## Who this candidate is"] == "conflict"
    assert (vault / CRITERIA_RELPATH).read_text() == "edited in Obsidian\n"


def test_a_client_without_forms_gets_unsupported_client():
    _existing_hunt()
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                _tick(1), with_callback=False)
    assert out["outcome"] == "unsupported_client"
    assert "lead_ttl_days: 30" not in Path(config_file()).read_text()


def test_decline_writes_nothing():
    _existing_hunt()
    before = Path(config_file()).read_text()
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                lambda p: ("decline", None))
    assert out["outcome"] == "declined" and Path(config_file()).read_text() == before


def test_doctor_sees_the_new_vault_in_the_same_server_session(tmp_path, monkeypatch):
    """The holder rebuild only exists inside ONE server instance, so the review and the
    doctor call share one Client session here."""
    from mcp import Client, types
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    vault = tmp_path / "chosen-vault"

    async def tick_all(context, params):
        return types.ElicitResult(action="accept", content={
            k: True for k in params.requested_schema["properties"]})

    async def _run():
        async with Client(build_server(Config(), write=True),
                          elicitation_callback=tick_all) as client:
            r = await client.call_tool("setup_review", {"changes": [
                {"kind": "config", "target": "vault_dir", "value": str(vault)}]})
            d = await client.call_tool("doctor", {})
            return json.loads(r.content[0].text), json.loads(d.content[0].text)

    review_out, doctor_out = asyncio.run(_run())
    assert review_out["config_written"] is True
    assert (vault / CRITERIA_RELPATH).exists()
    assert not (tmp_path / "empty" / "vault").exists()
    # doctor through the REBUILT holder reads the new vault: its Judging Profile row is OK
    # ("found"), where the stale holder's cwd-relative vault would report it missing.
    _assert_profile_row_ok(doctor_out)


def test_first_run_with_vault_dir_env_writes_into_that_vault():
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}], _tick_all)
    assert out["config_written"] is True
    assert (Path(os.environ["VAULT_DIR"]) / CRITERIA_RELPATH).exists()
    assert os.environ["VAULT_DIR"] in Path(config_file()).read_text()


def test_the_form_boxes_start_unticked_and_show_new_and_old_text():
    _existing_hunt()
    seen = []
    _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
          lambda p: ("cancel", None), seen=seen)
    prop = seen[0].requested_schema["properties"]["entry_1"]
    assert prop["default"] is False and "New:\n30" in prop["description"]
    assert "\n" not in seen[0].message


def test_first_run_without_vault_env_sets_everything_aside_until_a_vault_is_named(
        tmp_path, monkeypatch):
    from mcp import Client, types
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    vault = tmp_path / "named-vault"

    async def tick_all(context, params):
        return types.ElicitResult(action="accept", content={
            k: True for k in params.requested_schema["properties"]})

    async def _run():
        async with Client(build_server(Config(), write=True),
                          elicitation_callback=tick_all) as client:
            first = await client.call_tool("setup_review", {"changes": [
                {"kind": "config", "target": "lead_ttl_days", "value": "30"}]})
            created_after_first = Path(config_file()).exists()
            second = await client.call_tool("setup_review", {"changes": [
                {"kind": "config", "target": "vault_dir", "value": str(vault)},
                {"kind": "config", "target": "lead_ttl_days", "value": "30"}]})
            d = await client.call_tool("doctor", {})
            return (json.loads(first.content[0].text), created_after_first,
                    json.loads(second.content[0].text), json.loads(d.content[0].text))

    first, created, second, doctor_out = asyncio.run(_run())
    assert first["units"] == [] or all(u["outcome"] == "set_aside" for u in first["units"])
    assert first["set_aside"] and all(
        "where your notes live" in a["reason"] for a in first["set_aside"])
    assert not created
    assert {u["outcome"] for u in second["units"]} == {"written"}
    assert (vault / CRITERIA_RELPATH).exists()
    _assert_profile_row_ok(doctor_out)


def test_a_failed_holder_rebuild_keeps_the_outcomes_and_asks_for_a_restart(monkeypatch):
    def boom():
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(mcpserver, "_holder_sluice", boom)
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}], _tick_all)
    assert out["config_written"] is True
    assert {u["outcome"] for u in out["units"]} == {"written"}
    assert out["restart_needed"]
