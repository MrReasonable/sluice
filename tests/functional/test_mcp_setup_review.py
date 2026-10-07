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


def test_an_unexpected_error_in_the_step_is_a_structured_failure_with_no_path(
        tmp_path, monkeypatch):
    """mcp discards a tool exception's message, so an error escaping the review step would reach
    the client as an opaque failure. It comes back as `failed` instead, naming the error's kind
    but never the path the error carried, and the holder is still rebuilt."""
    secret = str(tmp_path / "somewhere" / "config.yaml")

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", secret)

    rebuilt = []
    real = mcpserver._holder_sluice
    monkeypatch.setattr(mcpserver, "setup_review_step", boom)
    monkeypatch.setattr(mcpserver, "_holder_sluice", lambda: rebuilt.append(1) or real())
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}], _tick_all)
    assert out["outcome"] == "failed"
    assert out["reason"].startswith("PermissionError: Permission denied")
    assert secret not in json.dumps(out) and str(tmp_path) not in json.dumps(out)
    assert rebuilt


def test_a_step_crash_whose_holder_rebuild_also_fails_still_asks_for_a_restart(
        tmp_path, monkeypatch):
    """The crash report is built after the rebuild, from nothing the step returned, so the
    rebuild's failure must be carried across to it: whatever the step wrote before it raised is
    on disk while the holder still serves the old config. Both errors carry a path; the report
    names neither."""
    secret = str(tmp_path / "somewhere" / "config.yaml")

    def step_boom(*a, **k):
        raise PermissionError(13, "Permission denied", secret)

    def rebuild_boom():
        raise OSError(2, "No such file or directory", secret)

    monkeypatch.setattr(mcpserver, "setup_review_step", step_boom)
    monkeypatch.setattr(mcpserver, "_holder_sluice", rebuild_boom)
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}], _tick_all)
    assert out["outcome"] == "failed"
    assert out["restart_needed"].startswith("FileNotFoundError: restart")
    text = json.dumps(out)
    assert secret not in text and str(tmp_path) not in text
    assert os.path.realpath(tmp_path) not in text


def test_the_vault_dir_box_shows_the_path_and_no_response_carries_a_discovered_one(
        tmp_path, monkeypatch):
    """Both halves of the path rule. The box ECHOES the user's own answer (resolved), because a
    human must see every value before it is written; no response carries a path the SERVER
    discovered -- here the working directory the server was started from, which a relative
    answer would have resolved against."""
    monkeypatch.delenv("VAULT_DIR")
    cwd = tmp_path / "server-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    vault = tmp_path / "chosen-vault"
    seen = []
    out = _call([{"kind": "config", "target": "vault_dir", "value": str(vault)}],
                lambda p: ("cancel", None), seen=seen)
    assert f"New:\n\"{vault}\"" in seen[0].requested_schema["properties"]["entry_1"][
        "description"]
    assert str(cwd) not in json.dumps(out)
    assert os.path.realpath(cwd) not in json.dumps(out)

    seen.clear()
    out = _call([{"kind": "config", "target": "vault_dir", "value": "notes"},
                 {"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                _tick_all, seen=seen)
    assert seen == [], "a relative vault_dir must not reach a form"
    assert out["outcome"] == "nothing_to_review"
    reasons = {a["change"]: a["reason"] for a in out["set_aside"]}
    assert "relative path" in reasons["config: vault_dir"]
    text = json.dumps(out)
    assert str(tmp_path) not in text and os.path.realpath(tmp_path) not in text
    assert not Path(config_file()).exists() and not (cwd / "notes").exists()


def test_a_config_created_while_a_first_run_form_is_open_conflicts_and_writes_nothing(
        tmp_path, monkeypatch):
    """The notes' shas still match across the race (absent == absent), but the new config names
    its own vault: writing the ticked note would land it in a vault the user never saw."""
    from sluice.core.protocols import ROLE_BRIEF_RELPATH
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    chosen, planted = tmp_path / "chosen-vault", tmp_path / "planted-vault"
    planted_text = build_plan({"vault_dir": str(planted)}).config_text

    def plant_then_tick(params):
        os.makedirs(os.path.dirname(config_file()), exist_ok=True)
        Path(config_file()).write_text(planted_text)
        return "accept", {k: True for k in params.requested_schema["properties"]}

    out = _call([{"kind": "config", "target": "vault_dir", "value": str(chosen)},
                 {"kind": "brief", "target": "Pay structure", "value": "Example."}],
                plant_then_tick)
    assert {u["unit"] for u in out["units"]} == {"config:vault_dir", "brief:## Pay structure"}
    assert {u["outcome"] for u in out["units"]} == {"conflict"}
    assert out["config_written"] is False
    assert Path(config_file()).read_bytes() == planted_text.encode()
    for vault in (chosen, planted, tmp_path / "empty" / "vault"):
        assert not (vault / ROLE_BRIEF_RELPATH).exists(), vault
    assert not chosen.exists() and not planted.exists()


def test_a_multi_line_search_entry_is_a_structured_outcome_and_writes_nothing():
    """The search box is shown (propose does not run the editor for a search) and ticked; the
    retry must still answer with a structured outcome naming the remedy, never an error."""
    from tests.test_onboard_edit import MULTILINE_ENTRY as _SYNTHETIC
    # This row runs the real server, whose source registry decides what a search may target, so
    # it genuinely needs a REGISTERED adapter id; the shared fixture text uses a synthetic one.
    MULTILINE_ENTRY = _SYNTHETIC.replace("example-board", "remoteok")
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(MULTILINE_ENTRY)
    out = _call([{"kind": "search", "target": "remoteok", "label": "Third",
                  "url": "https://example.invalid/c"}], _tick_all)
    assert out["outcome"] == "completed"
    (row,) = out["units"]
    assert row["outcome"] == "set_aside" and "flow form" in row["reason"]
    assert Path(config_file()).read_text() == MULTILINE_ENTRY


def test_a_default_first_run_create_that_fails_is_reported_not_silent():
    """The default Leads view is created on a first run with no box of its own; when that
    create does not land, `artefacts` and `detail` say so instead of leaving it to doctor."""
    from sluice.core.protocols import LEADS_VIEW_RELPATH
    view = Path(os.environ["VAULT_DIR"]) / LEADS_VIEW_RELPATH
    view.mkdir(parents=True)            # a directory where the view file should go
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"}], _tick_all)
    assert out["config_written"] is True
    assert out["artefacts"]["profile"]["outcome"] == "written"
    assert out["artefacts"]["view"]["outcome"] == "conflict"
    assert "view" in out["detail"]
    assert {u["unit"] for u in out["units"]} == {"config:lead_ttl_days"}


def _no_discovered_path(out, tmp_path):
    """Whole serialised result: the sandbox root covers the config's directory and the vault."""
    text = json.dumps(out)
    assert str(tmp_path) not in text and os.path.realpath(tmp_path) not in text


def test_ticked_and_unticked_units_on_one_note_write_only_the_ticked_one(tmp_path):
    from sluice.core.protocols import ROLE_BRIEF_RELPATH
    _existing_hunt()
    out = _call([{"kind": "brief", "target": "Pay structure", "value": "Example ticked."},
                 {"kind": "brief", "target": "Sources consulted", "value": "Example unticked."}],
                _tick(1))
    brief = (Path(os.environ["VAULT_DIR"]) / ROLE_BRIEF_RELPATH).read_text()
    assert "Example ticked." in brief and "Example unticked." not in brief
    by = {u["unit"]: u["outcome"] for u in out["units"]}
    assert by == {"brief:## Pay structure": "written", "brief:## Sources consulted": "declined"}
    _no_discovered_path(out, tmp_path)


def test_cancel_writes_nothing(tmp_path):
    _existing_hunt()
    before = Path(config_file()).read_bytes()
    out = _call([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                 {"kind": "brief", "target": "Pay structure", "value": "Example."}],
                lambda p: ("cancel", None))
    assert out["outcome"] == "cancelled" and out["config_written"] is False
    assert {u["outcome"] for u in out["units"]} == {"declined"}
    assert Path(config_file()).read_bytes() == before
    assert not (Path(os.environ["VAULT_DIR"]) / "Job Applications").exists()
    _no_discovered_path(out, tmp_path)


def test_a_change_that_did_not_fit_the_form_is_reported_not_shown_and_not_written(tmp_path):
    """Three tall Role Brief sections cannot share one form; the one left over is `not_shown`
    and is written by no tick, however the shown boxes are answered. A regression row with no
    deletion witness: the write list is built only from the shown boxes, so there is no single
    line whose removal would let an unshown change through."""
    from sluice.core.protocols import ROLE_BRIEF_RELPATH
    _existing_hunt()
    tall = "\n".join(f"Example line {i}." for i in range(12))
    sections = ["The role, as researched", "Pay structure", "Sources consulted"]
    seen = []
    out = _call([{"kind": "brief", "target": s, "value": f"{s} marker.\n{tall}"}
                 for s in sections], _tick_all, seen=seen)
    assert len(seen) == 1 and out["not_shown"], "the batch fitted one form; this row is vacuous"
    left = {k.removeprefix("brief:## ") for k in out["not_shown"]}
    brief = (Path(os.environ["VAULT_DIR"]) / ROLE_BRIEF_RELPATH).read_text()
    for s in sections:
        assert (f"{s} marker." in brief) is (s not in left), s
    assert {u["unit"] for u in out["units"]}.isdisjoint(out["not_shown"])
    _no_discovered_path(out, tmp_path)


def test_a_config_removed_while_the_form_is_open_conflicts_and_writes_nothing(tmp_path):
    """CodeRabbit: the guard caught only a config CREATED during the form. One REMOVED moves the
    vault too (back to $VAULT_DIR or the default), so the boxes would land somewhere else."""
    from sluice.core.protocols import ROLE_BRIEF_RELPATH
    _existing_hunt()

    def remove_then_tick(params):
        os.unlink(config_file())
        return "accept", {k: True for k in params.requested_schema["properties"]}

    out = _call([{"kind": "brief", "target": "Pay structure", "value": "Example."}],
                remove_then_tick)
    assert [(u["outcome"], u["reason"]) for u in out["units"]] == [
        ("conflict", "the sluice config was removed while the form was open")]
    assert not Path(config_file()).exists()
    assert not (Path(os.environ["VAULT_DIR"]) / ROLE_BRIEF_RELPATH).exists()
    _no_discovered_path(out, tmp_path)


def test_a_vault_that_moved_while_the_form_is_open_conflicts_and_writes_nothing(
        tmp_path, monkeypatch):
    """The config exists on both legs and no shown artefact's sha changed (the Role Brief is
    absent in both vaults), yet the vault now resolves elsewhere: only the vault recorded in
    the form's state -- as a digest, so no path travels through the client -- can see it."""
    from sluice.core.protocols import ROLE_BRIEF_RELPATH
    _existing_hunt()
    first = Path(os.environ["VAULT_DIR"])
    other = tmp_path / "other-vault"

    def move_then_tick(params):
        monkeypatch.setenv("VAULT_DIR", str(other))
        return "accept", {k: True for k in params.requested_schema["properties"]}

    out = _call([{"kind": "brief", "target": "Pay structure", "value": "Example."}],
                move_then_tick)
    assert [(u["outcome"], u["reason"]) for u in out["units"]] == [
        ("conflict", "the vault sluice writes to changed while the form was open")]
    for vault in (first, other):
        assert not (vault / ROLE_BRIEF_RELPATH).exists(), vault
    _no_discovered_path(out, tmp_path)


def _search_written(out):
    """The search unit was written AND the file on disk loads it: read back through the loader,
    never taken from the report."""
    from sluice.core.config import load_config
    (row,) = out["units"]
    assert row["outcome"] == "written", row
    src = load_config(config_file()).sources["remoteok"]
    assert [list(e[:2]) for e in src.searches] == [["Example", "https://example.invalid/s"]]
    assert src.enabled is True and src.tuning == {}


_SEARCH = [{"kind": "search", "target": "remoteok", "label": "Example",
            "url": "https://example.invalid/s"}]


def test_a_first_run_search_creates_its_source_block_and_is_written():
    """The search creates `sources.remoteok`, so the loader now reads that source's `enabled`
    and `tuning` too: undeclared, the config check set the search aside as touching them."""
    out = _call(_SEARCH, _tick_all)
    assert out["config_written"] is True
    _search_written(out)


def test_a_search_on_an_existing_config_with_no_block_for_its_source_is_written():
    _existing_hunt()
    assert "remoteok" not in Path(config_file()).read_text()
    out = _call(_SEARCH, _tick_all)
    _search_written(out)
