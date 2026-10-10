import io
import pathlib

import pytest

from sluice.cli import _build_parser
from sluice.mcpextra import NOT_INSTALLED
from sluice.mcpinstall import clients, flow, routes
from tests.mcpinstall.fakes import OTHER, Rig


def install(rig, *args, stdin="", interactive=False, extra=True, argv0=None):
    ns = _build_parser().parse_args(["mcp", "install", *args])
    opts = flow.Options(tuple(ns.client or ()), ns.read_only, ns.replace, ns.yes, ns.dry_run)
    out, err = io.StringIO(), io.StringIO()
    rc = flow.run(opts, host=rig.host, deps=rig.deps, argv0=argv0 or rig.launcher,
                  stdin=io.StringIO(stdin), out=out, err=err, interactive=interactive,
                  find_spec=lambda name: object() if extra else None)
    return rc, out.getvalue(), err.getvalue()


def _entry(rig, name):
    return routes.read_state(clients.by_name(name), rig.path(name)).current


def test_a_missing_extra_stops_before_detection(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("claude-code")
    rc, out, err = install(rig, extra=False)
    assert rc == 2 and NOT_INSTALLED in err and fake.calls == []


def test_a_launcher_that_is_cli_py_stops_before_detection(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("claude-code")
    cli_py = tmp_path / "cli.py"
    cli_py.write_text("")
    cli_py.chmod(0o755)
    rc, _, err = install(rig, argv0=str(cli_py))
    assert rc == 2 and "job-sluice" in err and fake.calls == []


def test_nothing_found_exits_0_with_the_link(tmp_path):
    rc, out, _ = install(Rig(tmp_path), "--yes")
    assert rc == 0 and clients.DOCS_URL + "#install-in-your-client" in out


def test_a_named_client_that_is_not_found_fails(tmp_path):
    rc, out, _ = install(Rig(tmp_path), "--client", "opencode", "--yes")
    assert rc == 1 and "opencode: failed" in out


def test_an_unknown_client_is_an_argparse_error_listing_names(capsys):
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["mcp", "install", "--client", "nope"])
    assert "claude-code" in capsys.readouterr().err


def test_non_interactive_registers_every_found_client_with_write(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("claude-code")
    rig.cli("gemini")
    rc, out, _ = install(rig, "--yes")
    assert rc == 0 and "claude-code: registered" in out and "gemini: registered" in out
    assert _entry(rig, "gemini").argv[-1] == "--write"


def test_read_only_registers_without_write(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    install(rig, "--yes", "--read-only")
    assert _entry(rig, "gemini").argv[-1] == "serve"


def test_same_is_unchanged_and_writes_nothing(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("gemini")
    install(rig, "--yes")
    calls = len(fake.calls)
    rc, out, _ = install(rig, "--yes")
    assert rc == 0 and "gemini: unchanged" in out and len(fake.calls) == calls


def test_different_without_replace_is_refused(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.write("gemini", {"mcpServers": {"job-sluice": {"command": "/old/job-sluice",
                                                      "args": ["mcp", "serve"]}}})
    rc, out, _ = install(rig, "--yes")
    assert rc == 1 and "gemini: refused" in out and "--replace" in out
    rc, out, _ = install(rig, "--yes", "--replace")
    assert rc == 0 and "gemini: replaced" in out


def test_interactive_asks_write_then_confirms_a_replace(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.write("gemini", {"mcpServers": {"job-sluice": {"command": "/old/job-sluice",
                                                      "args": ["mcp", "serve"]}}})
    rc, out, _ = install(rig, stdin="n\n\ny\n", interactive=True)
    assert rc == 0 and "gemini: replaced" in out
    assert _entry(rig, "gemini").argv[-1] == "serve"          # answered no to --write


def test_flags_skip_their_questions(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.write("gemini", {"mcpServers": {"job-sluice": {"command": "/old/job-sluice",
                                                      "args": []}}})
    rc, out, _ = install(rig, "--read-only", "--replace", stdin="\n", interactive=True)
    assert "write tools" not in out and "Replace" not in out and rc == 0


def test_client_with_a_tty_still_asks_the_write_question(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    _, out, _ = install(rig, "--client", "gemini", stdin="\n\n", interactive=True)
    assert "write tools" in out


def test_eof_takes_every_default(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.cli("opencode")
    rig.write("opencode", {"mcp": {"servers": {"job-sluice": {"type": "local",
                                                              "command": ["/old/job-sluice"]}}}})
    rc, out, _ = install(rig, stdin="", interactive=True)
    assert "gemini: registered" in out and "opencode: refused" in out    # replace defaults no
    assert _entry(rig, "gemini").argv[-1] == "--write"                   # write defaults yes


def test_dry_run_asks_nothing_and_writes_nothing(tmp_path):
    rig = Rig(tmp_path)
    fake = rig.cli("gemini")
    (tmp_path / "home" / ".cursor").mkdir(parents=True)
    rc, out, _ = install(rig, "--dry-run", stdin="", interactive=True)
    assert fake.calls == [] and not pathlib.Path(rig.path("cursor")).exists()
    assert "would run: gemini mcp add" in out and "would write ~/.cursor/mcp.json" in out
    assert "write tools" not in out and rc == 0


def test_codex_without_an_entry_is_appended(tmp_path):
    rig = Rig(tmp_path)
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    rc, out, _ = install(rig, "--yes")
    assert rc == 0 and "codex: registered" in out


def test_a_different_codex_entry_is_manual_and_fails_only_when_named(tmp_path):
    rig = Rig(tmp_path)
    toml = tmp_path / "home" / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text('[mcp_servers.job-sluice]\ncommand = "/old/job-sluice"\n')
    before = toml.read_bytes()
    rc, out, _ = install(rig, "--yes", "--replace")
    assert rc == 0 and "codex: manual" in out and "[mcp_servers.job-sluice]" in out
    # The flow's own arm, not apply_append's duplicate-table refusal reached by accident.
    assert "does not edit an existing one" in out and "keep the rest" in out
    assert toml.read_bytes() == before and rig.copies_written == []
    rc, _, _ = install(rig, "--yes", "--client", "codex")
    assert rc == 1


def test_an_existing_codex_entry_is_not_offered_in_the_pick_list(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    toml = tmp_path / "home" / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text('[mcp_servers.job-sluice]\ncommand = "/old/job-sluice"\n')
    _, out, _ = install(rig, stdin="\n\n", interactive=True)
    listed = out.split("Register job-sluice in:")[1].split("Press Enter")[0]
    assert "gemini" in listed and "codex" not in listed
    assert "codex: manual" in out


def test_codex_dry_run_previews_the_append_and_writes_nothing(tmp_path):
    rig = Rig(tmp_path)
    toml = tmp_path / "home" / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text('model = "example-model"\n')
    rc, out, _ = install(rig, "--yes", "--dry-run")
    assert rc == 0 and "codex: would append to ~/.codex/config.toml" in out
    assert "    [mcp_servers.job-sluice]" in out
    assert toml.read_text() == 'model = "example-model"\n' and rig.copies_written == []


def test_codex_dry_run_previews_an_inline_table_as_the_run_would(tmp_path):
    rig = Rig(tmp_path)
    toml = tmp_path / "home" / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text('mcp_servers = { other = { command = "x" } }\n')
    rc, out, _ = install(rig, "--yes", "--dry-run", "--client", "codex")
    assert rc == 1 and "codex: manual" in out and "would append" not in out
    assert "inside the `mcp_servers` table" in out and "[mcp_servers.job-sluice]" not in out


def test_every_route_has_a_writer_and_a_preview():
    assert set(flow._APPLY) == set(flow._PREVIEW) == set(clients.ROUTES)
    assert {c.route for c in clients.ROSTER} <= set(clients.ROUTES)


def test_codex_with_a_matching_entry_is_unchanged(tmp_path):
    rig = Rig(tmp_path)
    toml = tmp_path / "home" / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text(f'[mcp_servers.job-sluice]\ncommand = "{rig.launcher}"\n'
                    'args = ["mcp", "serve", "--write"]\n')
    rc, out, _ = install(rig, "--yes")
    assert rc == 0 and "codex: unchanged" in out


def test_one_clean_and_one_failed_leaves_only_the_failed_copy(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.cli("opencode", drop="other")
    rig.write("gemini", {"mcpServers": {"other": OTHER}})
    rig.write("opencode", {"mcp": {"servers": {"other": OTHER}}})
    rc, out, _ = install(rig, "--yes")
    assert rc == 1 and "gemini: registered" in out and "opencode: failed" in out
    assert len(rig.copies()) == 1 and rig.copies()[0].startswith("opencode.json.")


def test_a_failure_prints_the_snippet_to_paste(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini", noop=True)
    _, out, _ = install(rig, "--yes")
    assert "paste this into ~/.gemini/settings.json" in out and '"mcpServers"' in out


def test_the_next_step_is_printed_for_each_registered_client(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("claude-code")
    _, out, _ = install(rig, "--yes")
    assert "/mcp__job-sluice__career_interview" in out


def test_a_pinned_path_reaches_the_entry(tmp_path):
    rig = Rig(tmp_path, env={"SLUICE_CONFIG": "/cfg/sluice.yaml", "SEEN_DB": "/s/seen.db"})
    rig.cli("gemini")
    install(rig, "--yes")
    assert dict(_entry(rig, "gemini").env) == {"SEEN_DB": "/s/seen.db",
                                               "SLUICE_CONFIG": "/cfg/sluice.yaml"}


@pytest.mark.parametrize("name,content", [("opencode", '{"mcp": {'),
                                          ("cursor", '{"mcpServers": {} // c')])
def test_an_unparseable_file_fails_and_runs_nothing(tmp_path, name, content):
    rig = Rig(tmp_path)
    fake = rig.cli(name)
    p = rig.write(name, content)
    rc, out, _ = install(rig, "--yes")
    assert rc == 1 and f"{name}: failed" in out and "could not be read" in out
    assert fake.calls == [] and p.read_text() == content and rig.copies_written == []


def test_dry_run_previews_the_same_refusal_the_run_makes(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.write("gemini", {"mcpServers": {"job-sluice": {
        "command": "/old/job-sluice", "args": [], "env": {"MY_KEY": "v"}}}})
    rc, out, _ = install(rig, "--yes", "--dry-run", "--replace")
    assert rc == 1 and "gemini: refused" in out and "would run" not in out


def test_a_local_scope_claude_code_entry_is_reported(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("claude-code")
    rig.write("claude-code", {"mcpServers": {}, "projects": {
        "/w/p": {"mcpServers": {"job-sluice": {"command": "/x", "args": []}}}}})
    _, out, _ = install(rig, "--yes")
    assert "1 project(s) also have a local-scope job-sluice entry" in out


SENTINEL = "SENTINEL-NOT-A-SECRET-FLOW"


def _planted(rig):
    rig.write("cursor", {"mcpServers": {
        "other": {"command": "/o", "env": {"OTHER_KEY": SENTINEL}},
        "job-sluice": {"command": "/old/job-sluice", "args": ["mcp", "--token", SENTINEL],
                       "env": {"PLANTED_KEY": SENTINEL}}}})
    # Codex's config.toml holds tokens the same way, and its `manual` arm is a route of its own.
    rig.write("codex", '[mcp_servers.other]\ncommand = "/o"\n\n[mcp_servers.other.env]\n'
              f'OTHER_KEY = "{SENTINEL}"\n\n[mcp_servers.job-sluice]\n'
              f'command = "/old/job-sluice"\nargs = ["mcp", "--token", "{SENTINEL}"]\n\n'
              f'[mcp_servers.job-sluice.env]\nPLANTED_CODEX_KEY = "{SENTINEL}"\n')


def test_a_command_shaped_like_a_setting_is_not_printed(tmp_path):
    rig = Rig(tmp_path)
    rig.write("cursor", {"mcpServers": {"job-sluice": {"command": f"API_KEY={SENTINEL}",
                                                      "args": []}}})
    _, out, err = install(rig, "--yes")
    assert SENTINEL not in out + err and "<command>" in out


@pytest.mark.parametrize("args", [["--yes"], ["--yes", "--dry-run"], ["--yes", "--replace"]])
def test_no_planted_value_reaches_any_output(tmp_path, args):
    rig = Rig(tmp_path)
    _planted(rig)
    rc, out, err = install(rig, *args)
    assert SENTINEL not in out + err
    assert "PLANTED_KEY" in out            # control: the planted entry WAS read and reported
    assert "codex: manual" in out and "PLANTED_CODEX_KEY" in out    # and Codex's too


def test_the_interactive_comparison_shows_no_planted_value(tmp_path):
    rig = Rig(tmp_path)
    _planted(rig)
    _, out, err = install(rig, stdin="\n\nn\n", interactive=True)
    assert SENTINEL not in out + err and "PLANTED_KEY" in out and "<other argument>" in out
    assert "PLANTED_CODEX_KEY" in out


def test_the_cli_command_is_wired(tmp_path, monkeypatch, capsys):
    from sluice import cli
    monkeypatch.setattr(cli.sys, "argv", [str(tmp_path / "cli.py")])
    ns = _build_parser().parse_args(["mcp", "install", "--yes"])
    assert ns.func is cli.cmd_mcp_install
    assert cli.cmd_mcp_install(ns, None) == 2    # started as cli.py: refused before detection


def test_the_parser_import_loads_only_the_roster(tmp_path):
    import subprocess, sys
    code = ("import sys, sluice.mcpinstall.clients; "
            "print(sorted(m for m in sys.modules if m.startswith('sluice')))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=tmp_path, check=True, timeout=60).stdout.strip()
    assert got == str(['sluice', 'sluice.mcpinstall', 'sluice.mcpinstall.clients',
                       'sluice.mcpinstall.jsonc', 'sluice.mcpinstall.server'])


def test_an_unexpected_error_in_one_client_still_reports_the_others(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.cli("claude-code")
    (tmp_path / "home" / ".cursor").mkdir(parents=True)

    def boom(*a, **k):
        raise RuntimeError("SENTINEL-NOT-A-SECRET-BOOM")
    monkeypatch.setitem(flow._APPLY, "json", boom)
    rc, out, err = install(rig, "--yes")
    assert rc == 1 and "claude-code: registered" in out
    assert "cursor: failed - unexpected error: RuntimeError" in out
    assert "SENTINEL" not in out + err


def test_dry_run_previews_the_remove_a_claude_code_replace_runs_first(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("claude-code")
    rig.write("claude-code", {"mcpServers": {"job-sluice": {"type": "stdio",
                                                           "command": "/old/job-sluice",
                                                           "args": []}}})
    rc, out, _ = install(rig, "--yes", "--dry-run", "--replace")
    assert rc == 0
    assert out.index("would run: claude mcp remove") < out.index("would run: claude mcp add")


def test_an_already_registered_client_is_listed_unticked(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.cli("opencode")
    install(rig, "--yes", "--client", "gemini")
    _, out, _ = install(rig, stdin="\n\n", interactive=True)
    assert "[ ] gemini (already registered)" in out and "[x] opencode (not registered)" in out
    assert "gemini: unchanged" in out and "opencode: registered" in out


def test_opencode_with_only_a_jsonc_file_registers_in_it(tmp_path):
    rig = Rig(tmp_path)
    folder = tmp_path / "home" / ".config" / "opencode"
    folder.mkdir(parents=True)
    (folder / "opencode.jsonc").write_text('{\n  // mine\n  "mcp": {"servers": {}}\n}\n')
    rig.cli("opencode")
    rc, out, _ = install(rig, "--yes")
    assert rc == 0 and "opencode: registered" in out
    assert not (folder / "opencode.json").exists()
    assert "job-sluice" in (folder / "opencode.jsonc").read_text()


def test_the_cli_hands_every_flag_to_its_own_option(tmp_path, monkeypatch):
    from sluice import cli
    seen = []
    monkeypatch.setattr(flow, "run", lambda opts, **kw: seen.append(opts) or 0)
    for argv in (["--read-only", "--yes", "--client", "cursor"], ["--replace", "--dry-run"]):
        cli.cmd_mcp_install(_build_parser().parse_args(["mcp", "install", *argv]), None)
    assert seen == [
        flow.Options(clients=("cursor",), read_only=True, replace=False, yes=True, dry_run=False),
        flow.Options(clients=(), read_only=False, replace=True, yes=False, dry_run=True)]


def test_a_client_left_out_of_the_pick_list_is_named_in_the_report(tmp_path):
    rig = Rig(tmp_path)
    rig.cli("gemini")
    rig.cli("opencode")
    rc, out, _ = install(rig, stdin="\ngemini\n", interactive=True)
    assert rc == 0 and "gemini: registered" in out
    assert "not selected: opencode" in out and "opencode: " not in out
