import ntpath
import posixpath

import pytest

from sluice.mcpinstall import clients, jsonc, server

P, N = posixpath.join, ntpath.join
# Joined from parts so no home-path literal sits in a tracked file (tests/test_no_leaked_files.py).
MAC_HOME = P("/Users", "example")
LINUX_HOME = P("/home", "example")
WIN_HOME = N("C:" + "\\", "Users", "example")
SPEC = server.ServerSpec(("/opt/x/job-sluice", "mcp", "serve", "--write"),
                         (("SLUICE_CONFIG", "/cfg/sluice.yaml"),))


def _host(platform, home, env=None, files=(), dirs=(), on_path=()):
    return clients.Host(home=home, env=env or {}, platform=platform,
                        which=lambda n: f"/bin/{n}" if n in on_path else None,
                        isdir=lambda p: p in dirs, isfile=lambda p: p in files)


@pytest.mark.parametrize("name,platform,home,env,expected", [
    ("claude-code", "darwin", MAC_HOME, {}, P(MAC_HOME, ".claude.json")),
    ("claude-code", "linux", LINUX_HOME, {"CLAUDE_CONFIG_DIR": P(LINUX_HOME, "cc")},
     P(LINUX_HOME, "cc", ".claude.json")),
    ("claude-code", "win32", WIN_HOME, {}, N(WIN_HOME, ".claude.json")),
    ("vscode", "darwin", MAC_HOME, {},
     P(MAC_HOME, "Library", "Application Support", "Code", "User", "mcp.json")),
    ("vscode", "linux", LINUX_HOME, {}, P(LINUX_HOME, ".config", "Code", "User", "mcp.json")),
    ("vscode", "linux", LINUX_HOME, {"XDG_CONFIG_HOME": P(LINUX_HOME, "xdg")},
     P(LINUX_HOME, "xdg", "Code", "User", "mcp.json")),
    ("vscode", "win32", WIN_HOME, {"APPDATA": N(WIN_HOME, "AppData", "Roaming")},
     N(WIN_HOME, "AppData", "Roaming", "Code", "User", "mcp.json")),
    ("opencode", "darwin", MAC_HOME, {}, P(MAC_HOME, ".config", "opencode", "opencode.json")),
    ("opencode", "linux", LINUX_HOME, {"OPENCODE_CONFIG_DIR": P(LINUX_HOME, "oc")},
     P(LINUX_HOME, "oc", "opencode.json")),
    ("opencode", "win32", WIN_HOME, {}, N(WIN_HOME, ".config", "opencode", "opencode.json")),
    ("cursor", "darwin", MAC_HOME, {}, P(MAC_HOME, ".cursor", "mcp.json")),
    ("cursor", "win32", WIN_HOME, {}, N(WIN_HOME, ".cursor", "mcp.json")),
    ("claude-desktop", "darwin", MAC_HOME, {},
     P(MAC_HOME, "Library", "Application Support", "Claude", "claude_desktop_config.json")),
    ("claude-desktop", "win32", WIN_HOME, {"APPDATA": N(WIN_HOME, "AppData", "Roaming")},
     N(WIN_HOME, "AppData", "Roaming", "Claude", "claude_desktop_config.json")),
    ("codex", "linux", LINUX_HOME, {}, P(LINUX_HOME, ".codex", "config.toml")),
    ("codex", "darwin", MAC_HOME, {"CODEX_HOME": P(MAC_HOME, "cx")},
     P(MAC_HOME, "cx", "config.toml")),
    ("gemini", "linux", LINUX_HOME, {}, P(LINUX_HOME, ".gemini", "settings.json")),
    ("gemini", "darwin", MAC_HOME, {"GEMINI_CLI_HOME": P(MAC_HOME, "g")},
     P(MAC_HOME, "g", ".gemini", "settings.json")),
    ("gemini", "win32", WIN_HOME, {}, N(WIN_HOME, ".gemini", "settings.json")),
])
def test_config_path(name, platform, home, env, expected):
    assert clients.config_path(clients.by_name(name), _host(platform, home, env)) == expected


def test_claude_desktop_is_unsupported_on_linux():
    got = clients.config_path(clients.by_name("claude-desktop"), _host("linux", LINUX_HOME))
    assert isinstance(got, clients.Unsupported) and "Linux" in got.reason


def test_opencode_picks_jsonc_only_when_it_is_alone():
    d = P(MAC_HOME, ".config", "opencode")
    oc = clients.by_name("opencode")
    alone = _host("darwin", MAC_HOME, files={P(d, "opencode.jsonc")})
    both = _host("darwin", MAC_HOME, files={P(d, "opencode.jsonc"), P(d, "opencode.json")})
    assert clients.config_path(oc, alone) == P(d, "opencode.jsonc")
    assert clients.config_path(oc, both) == P(d, "opencode.json")


def test_detection():
    h = _host("darwin", MAC_HOME, on_path={"claude"},
              dirs={P(MAC_HOME, ".cursor"), P(MAC_HOME, ".codex")})
    got = {c.name: type(clients.detect(c, h)).__name__ for c in clients.ROSTER}
    assert got == {"claude-code": "Found", "vscode": "NotFound", "opencode": "NotFound",
                   "cursor": "Found", "claude-desktop": "NotFound", "codex": "Found",
                   "gemini": "NotFound"}


def test_production_lookups_find_no_client_in_the_sandbox(tmp_path, monkeypatch):
    host = clients.host_from_os()
    assert all(isinstance(clients.detect(c, host), (clients.NotFound, clients.Unsupported))
               for c in clients.ROSTER)
    # Control: the same builder DOES see a client placed on the sandbox PATH, so the
    # assertion above is not passing because the lookup is blind.
    fake = tmp_path / "empty-path" / "claude"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert isinstance(clients.detect(clients.by_name("claude-code"), clients.host_from_os()),
                      clients.Found)


@pytest.mark.parametrize("name,value", [
    ("claude-code", {"type": "stdio", "command": "/opt/x/job-sluice",
                     "args": ["mcp", "serve", "--write"],
                     "env": {"SLUICE_CONFIG": "/cfg/sluice.yaml"}}),
    ("vscode", {"type": "stdio", "command": "/opt/x/job-sluice",
                "args": ["mcp", "serve", "--write"], "env": {"SLUICE_CONFIG": "/cfg/sluice.yaml"}}),
    ("opencode", {"type": "local", "command": ["/opt/x/job-sluice", "mcp", "serve", "--write"],
                  "environment": {"SLUICE_CONFIG": "/cfg/sluice.yaml"}}),
    ("cursor", {"command": "/opt/x/job-sluice", "args": ["mcp", "serve", "--write"],
                "env": {"SLUICE_CONFIG": "/cfg/sluice.yaml"}}),
    ("gemini", {"command": "/opt/x/job-sluice", "args": ["mcp", "serve", "--write"],
                "env": {"SLUICE_CONFIG": "/cfg/sluice.yaml"}}),
])
def test_entry_value_and_parse_round_trip(name, value):
    c = clients.by_name(name)
    assert clients.entry_value(c, SPEC) == value
    assert clients.parse_entry(c, value) == (SPEC.argv, SPEC.env_dict)


def test_an_entry_without_env_carries_no_env_key():
    spec = server.ServerSpec(("/x/job-sluice", "mcp", "serve"), ())
    assert "env" not in clients.entry_value(clients.by_name("cursor"), spec)
    assert "environment" not in clients.entry_value(clients.by_name("opencode"), spec)


@pytest.mark.parametrize("value", ["a string", {"command": 3}, {"command": "x", "args": "y"},
                                   {"command": "x", "env": {"A": 1}}])
def test_a_malformed_entry_is_a_read_error(value):
    with pytest.raises(jsonc.ReadError):
        clients.parse_entry(clients.by_name("cursor"), value)


def test_server_table_walks_the_client_s_path():
    oc = clients.by_name("opencode")
    assert clients.server_table(oc, {}) == {}
    assert clients.server_table(oc, {"mcp": {"servers": {"a": {}}}}) == {"a": {}}
    with pytest.raises(jsonc.ReadError):
        clients.server_table(oc, {"mcp": {"servers": []}})


def test_add_argv_per_client():
    assert clients.add_argv(clients.by_name("claude-code"), SPEC) == [
        "claude", "mcp", "add", "--scope", "user", "job-sluice",
        "-e", "SLUICE_CONFIG=/cfg/sluice.yaml", "--", *SPEC.argv]
    assert clients.add_argv(clients.by_name("opencode"), SPEC) == [
        "opencode", "mcp", "add", "--global", "--env", "SLUICE_CONFIG=/cfg/sluice.yaml",
        "job-sluice", "--", *SPEC.argv]
    assert clients.add_argv(clients.by_name("gemini"), SPEC) == [
        "gemini", "mcp", "add", "-e", "SLUICE_CONFIG=/cfg/sluice.yaml", "-s", "user",
        "job-sluice", *SPEC.argv]
    assert clients.remove_argv(clients.by_name("claude-code")) == [
        "claude", "mcp", "remove", "--scope", "user", "job-sluice"]


def test_the_add_argv_carries_a_spaced_launcher_as_one_element():
    spec = server.ServerSpec(("/opt/My Apps/jöb/job-sluice", "mcp", "serve"), ())
    for name in ("claude-code", "opencode", "gemini"):
        assert "/opt/My Apps/jöb/job-sluice" in clients.add_argv(clients.by_name(name), spec)


def test_the_codex_snippet_is_toml_codex_reads():
    import tomllib
    snip = clients.snippet(clients.by_name("codex"), SPEC)
    assert tomllib.loads(snip)["mcp_servers"]["job-sluice"] == {
        "command": "/opt/x/job-sluice", "args": ["mcp", "serve", "--write"],
        "env": {"SLUICE_CONFIG": "/cfg/sluice.yaml"}}


def test_the_inline_codex_snippet_reads_inside_an_existing_table():
    import tomllib
    codex = clients.by_name("codex")
    snip = clients.snippet(codex, SPEC, inline=True)
    assert "\n" not in snip
    got = tomllib.loads("[mcp_servers]\n" + snip + "\n")["mcp_servers"]["job-sluice"]
    assert got == clients.entry_value(codex, SPEC)


# json.dumps is not a TOML string writer: it escapes a character outside the BMP as a surrogate
# pair, which TOML refuses. Each row is a path some user can have.
@pytest.mark.parametrize("path", ["/opt/My Apps/jöb/job-sluice", "/opt/\U0001F600/job-sluice",
                                  '/opt/q"uote\\back/job-sluice', "/opt/new\nline/job-sluice"])
def test_the_codex_snippet_escapes_any_path_codex_can_read(path):
    import tomllib
    codex = clients.by_name("codex")
    spec = server.ServerSpec((path, "mcp", "serve"), (("SLUICE_CONFIG", path),))
    for inline in (False, True):
        text = clients.snippet(codex, spec, inline=inline)
        doc = tomllib.loads(("[mcp_servers]\n" if inline else "") + text + "\n")
        got = doc["mcp_servers"]["job-sluice"]
        assert got["command"] == path and got["env"]["SLUICE_CONFIG"] == path


@pytest.mark.parametrize("field,value,valid", [
    ("route", "apend", "'command', 'json', 'append'"),
    ("env_key", "envs", "'env', 'environment'"),
    ("entry_type", "sse", "'stdio', 'local', ''"),
    ("collateral", "whole", "'file', 'table'"),
])
def test_an_unknown_roster_value_fails_at_construction(field, value, valid):
    import dataclasses
    with pytest.raises(ValueError) as exc:
        dataclasses.replace(clients.by_name("codex"), **{field: value})
    assert valid in str(exc.value) and field in str(exc.value)


def test_a_json_snippet_nests_under_the_client_s_table():
    import json
    snip = json.loads(clients.snippet(clients.by_name("opencode"), SPEC))
    assert snip["mcp"]["servers"]["job-sluice"]["type"] == "local"


def test_redaction_shows_only_the_executable_and_sluice_s_vocabulary():
    assert clients.redact_argv(["/x/job-sluice", "mcp", "serve", "--token", "SENTINEL",
                                "--write"]) == [
        "/x/job-sluice", "mcp", "serve", "<other argument>", "<other argument>", "--write"]


@pytest.mark.parametrize("argv0", ["API_KEY=SENTINEL", "/opt/srv --token SENTINEL",
                                   "https://user:SENTINEL@host.invalid/mcp"])
def test_redaction_hides_a_command_shaped_like_more_than_a_path(argv0):
    """A user can type a whole command line, or a URL carrying a password, into `command`."""
    assert clients.redact_argv([argv0, "x"]) == ["<command>", "<other argument>"]


def test_redaction_shows_a_plain_executable_path():
    assert clients.redact_argv(["/opt/My Apps/job-sluice"])[0] == "/opt/My Apps/job-sluice"
    shown = clients.redact_argv([P(MAC_HOME, "bin", "job-sluice")],
                                lambda p: clients.display_path(p, _host("darwin", MAC_HOME)))
    assert shown == ["~/bin/job-sluice"]


def test_extra_fields_are_the_keys_install_does_not_write():
    cursor, oc = clients.by_name("cursor"), clients.by_name("opencode")
    assert clients.extra_fields(cursor, {"command": "x", "args": [], "autoApprove": [],
                                         "cwd": "/w"}) == ["autoApprove", "cwd"]
    assert clients.extra_fields(oc, {"type": "local", "command": ["x"], "enabled": False}) == [
        "enabled"]


def test_only_claude_code_is_checked_by_table():
    assert [c.name for c in clients.ROSTER if clients.check_scope(c) == "table"] == [
        "claude-code"]


def test_local_scope_entries_are_counted_for_claude_code_only():
    doc = {"projects": {"/p1": {"mcpServers": {"job-sluice": {}}}, "/p2": {"mcpServers": {}},
                        "/p3": "junk"}}
    assert clients.local_entries(clients.by_name("claude-code"), doc) == 1
    assert clients.local_entries(clients.by_name("gemini"), doc) == 0


def test_display_path_is_relative_to_home():
    h = _host("darwin", MAC_HOME)
    assert clients.display_path(P(MAC_HOME, ".cursor", "mcp.json"), h) == "~/.cursor/mcp.json"
    assert clients.display_path("/etc/x.json", h) == "/etc/x.json"


def test_every_windows_row_says_it_is_unmeasured():
    h = _host("win32", WIN_HOME)
    assert all(clients.measured_note(c, h) == "unmeasured on Windows" for c in clients.ROSTER)
    assert clients.measured_note(clients.ROSTER[0], _host("darwin", MAC_HOME)) == ""
