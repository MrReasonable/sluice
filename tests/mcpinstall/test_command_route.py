import pathlib
import subprocess
import sys

import pytest

from sluice.mcpinstall import clients, routes, server
from tests.mcpinstall.fakes import OTHER, Rig

SPEC = server.ServerSpec(("/opt/x/job-sluice", "mcp", "serve", "--write"),
                         (("SLUICE_CONFIG", "/cfg/sluice.yaml"),))
COMMAND = ["claude-code", "opencode", "gemini"]


def _apply(rig, name, spec=SPEC):
    c = clients.by_name(name)
    state = routes.read_state(c, rig.path(name))
    assert not isinstance(state, routes.Unreadable), state
    return routes.apply_command(c, state, spec, rig.deps, rig.host.which)


def _seed(rig, name, servers):
    c = clients.by_name(name)
    doc = servers
    for key in reversed(c.table):
        doc = {key: doc}
    rig.write(name, doc)


@pytest.mark.parametrize("name", COMMAND)
def test_a_clean_register_keeps_other_servers_and_deletes_its_copy(tmp_path, name):
    rig = Rig(tmp_path)
    fake = rig.cli(name)
    _seed(rig, name, {"other": OTHER})
    out = _apply(rig, name)
    assert out.kind == "registered", out
    assert fake.copy_present == [True]          # the copy existed when the client ran
    assert rig.copies_written and rig.copies() == []


@pytest.mark.parametrize("name", COMMAND)
def test_a_client_that_exits_0_and_writes_nothing_has_failed(tmp_path, name):
    rig = Rig(tmp_path)
    rig.cli(name, noop=True)
    out = _apply(rig, name)
    assert out.kind == "failed" and "readback did not show the entry" in out.reason


def test_a_client_that_drops_another_server_fails_naming_it_and_keeps_the_copy(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("opencode", drop="other")
    _seed(rig, "opencode", {"other": OTHER})
    out = _apply(rig, "opencode")
    assert out.kind == "failed" and "other" in out.reason
    assert rig.copies() == rig.copies_written and len(rig.copies()) == 1


def test_a_client_that_writes_another_file_fails_on_readback(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini", write_to=str(tmp_path / "elsewhere.json"))
    out = _apply(rig, "gemini")
    assert out.kind == "failed" and "readback did not show the entry in ~/" in out.reason


def test_a_clean_replace_removes_then_adds_for_claude_code(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("claude-code")
    _seed(rig, "claude-code", {"job-sluice": {"type": "stdio", "command": "/old/job-sluice",
                                              "args": ["mcp", "serve"]}})
    out = _apply(rig, "claude-code")
    assert out.kind == "replaced", out
    assert [c[1] for c in fake.calls] == ["remove", "add"] and rig.copies() == []


def test_a_failed_add_after_the_remove_reports_the_old_entry_redacted(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("claude-code", fail=1)
    _seed(rig, "claude-code", {"job-sluice": {
        "type": "stdio", "command": "/old/job-sluice",
        "args": ["mcp", "serve", "--token", "SENTINEL-NOT-A-SECRET-4"]}})
    out = _apply(rig, "claude-code")
    text = "\n".join((out.reason,) + out.details)
    assert out.kind == "failed" and "exited 1" in out.reason
    assert "/old/job-sluice mcp serve <other argument> <other argument>" in text
    assert "SENTINEL" not in text and rig.copies()


def test_a_replace_over_foreign_env_keys_is_refused_naming_them(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("gemini")
    _seed(rig, "gemini", {"job-sluice": {"command": "/old/job-sluice", "args": [],
                                         "env": {"MY_API_KEY": "SENTINEL-NOT-A-SECRET-5"}}})
    out = _apply(rig, "gemini")
    assert out.kind == "refused" and "MY_API_KEY" in out.reason and "SENTINEL" not in out.reason
    assert fake.calls == [] and rig.copies_written == []


def test_a_replace_over_other_settings_is_refused_naming_them(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("gemini")
    _seed(rig, "gemini", {"job-sluice": {"command": "/old/job-sluice", "args": [],
                                         "trust": True, "timeout": 5}})
    out = _apply(rig, "gemini")
    assert out.kind == "refused" and "timeout, trust" in out.reason and fake.calls == []


def test_an_add_that_drops_an_unrelated_setting_fails_and_keeps_the_copy(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("opencode", drop_top="theme")
    rig.write("opencode", {"theme": "dark", "mcp": {"servers": {}}})
    out = _apply(rig, "opencode")
    assert out.kind == "failed" and "theme" in out.reason and len(rig.copies()) == 1


def test_a_settings_edit_between_the_copy_and_the_run_stops_a_whole_file_client(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("gemini")
    rig.write("gemini", {"theme": "dark", "mcpServers": {}})
    rig.after_copy = lambda: rig.write("gemini", {"theme": "light", "mcpServers": {}})
    out = _apply(rig, "gemini")
    assert (out.kind, out.reason) == ("failed", routes.CHANGED) and fake.calls == []


def test_an_edit_between_the_copy_and_the_run_stops_it(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("opencode")
    _seed(rig, "opencode", {"other": OTHER})
    rig.after_copy = lambda: _seed(rig, "opencode", {"other": OTHER, "late": OTHER})
    out = _apply(rig, "opencode")
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert fake.calls == [] and rig.copies() == rig.copies_written


def test_a_file_that_appears_before_the_run_stops_it_and_is_left_alone(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("claude-code")
    created = {}

    def create():
        created["bytes"] = rig.write("claude-code", {"mcpServers": {"theirs": OTHER}}).read_bytes()

    rig.deps.before_recheck = create
    out = _apply(rig, "claude-code")
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert fake.calls == [] and rig.copies_written == []
    assert pathlib.Path(rig.path("claude-code")).read_bytes() == created["bytes"]


def test_an_edit_outside_the_server_table_does_not_stop_the_command_route(tmp_path):
    """A running Claude Code session rewrites ~/.claude.json outside `mcpServers` constantly."""
    rig = Rig(tmp_path)
    rig.cli("claude-code")
    rig.write("claude-code", {"mcpServers": {}, "numStartups": 1})
    rig.after_copy = lambda: rig.write("claude-code", {"mcpServers": {}, "numStartups": 2})
    assert _apply(rig, "claude-code").kind == "registered"


def test_a_client_gone_from_path_fails(tmp_path):
    rig = Rig(tmp_path)
    assert "not found on PATH" in _apply(rig, "opencode").reason


def test_a_hang_is_a_timeout(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.runner.hang.add(str(rig.bin / "gemini"))
    out = _apply(rig, "gemini")
    assert out.kind == "failed" and "timed out" in out.reason


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script")
def test_the_production_runner_times_out_and_prints_nothing(tmp_path, capfd):
    script = tmp_path / "slow"
    script.write_text(f"#!{sys.executable}\nimport sys, time\n"
                      "print('SENTINEL-NOT-A-SECRET-6', file=sys.stderr)\n"
                      "print('SENTINEL-NOT-A-SECRET-6')\ntime.sleep(5)\n")
    script.chmod(0o755)
    with pytest.raises(subprocess.TimeoutExpired):
        routes.run_quietly([str(script)], 0.5)
    fail = tmp_path / "fail"
    fail.write_text(f"#!{sys.executable}\nimport sys\n"
                    "print('SENTINEL-NOT-A-SECRET-6', file=sys.stderr)\nsys.exit(3)\n")
    fail.chmod(0o755)
    assert routes.run_quietly([str(fail)], 5) == 3
    captured = capfd.readouterr()
    assert "SENTINEL" not in captured.out + captured.err


def test_an_unparseable_opencode_file_is_unreadable_and_nothing_runs(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("opencode")
    rig.write("opencode", '{"mcp": {"servers": {')
    state = routes.read_state(clients.by_name("opencode"), rig.path("opencode"))
    assert isinstance(state, routes.Unreadable)
    assert fake.calls == []


def test_the_runner_refuses_anything_outside_the_test_folder(tmp_path):
    rig = Rig(tmp_path)
    with pytest.raises(AssertionError):
        rig.runner(["/usr/bin/true"], 1)


def test_an_empty_server_parent_is_not_a_collateral_change(tmp_path):
    """opencode's add creates `mcp.servers` under an existing `"mcp": {}`; that is the entry's
    own path, not another setting changing."""
    rig = Rig(tmp_path)
    rig.cli("opencode")
    rig.write("opencode", {"theme": "dark", "mcp": {}})
    out = _apply(rig, "opencode")
    assert out.kind == "registered", out
    assert rig.copies() == []


def test_an_unexpected_error_from_the_runner_keeps_and_names_the_copy(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    _seed(rig, "gemini", {"other": OTHER})

    def boom(argv, timeout):
        raise RuntimeError("SENTINEL-NOT-A-SECRET-BOOM")
    rig.deps.run = boom
    out = _apply(rig, "gemini")
    assert out.kind == "failed" and "unexpected error: RuntimeError" in out.reason
    assert "SENTINEL" not in out.reason and rig.copies() == rig.copies_written


def test_a_replace_the_client_does_not_apply_has_failed(tmp_path):
    """Readback must find THIS run's entry, not merely an entry: a client that exits 0 and leaves
    the old one in place has not replaced it."""
    rig = Rig(tmp_path)
    rig.cli("gemini", noop=True)
    _seed(rig, "gemini", {"job-sluice": {"command": "/old/job-sluice", "args": ["mcp", "serve"]}})
    out = _apply(rig, "gemini")
    assert out.kind == "failed" and "readback did not show the entry" in out.reason
    assert rig.copies() == rig.copies_written and len(rig.copies()) == 1
