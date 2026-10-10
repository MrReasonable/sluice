"""The MCP clients `mcp install` knows, as DATA: where each keeps its user-level server table on
each platform, how it is found, what its `job-sluice` entry looks like, and (command route) the
argv that adds and removes it. Measured 2026-10-10; the spec's measurement tables are the
evidence for every value here.

Not a registered seam: the roster is a plain tuple, and `--client`'s choices derive from it.
Three routes (see routes.py): `command` runs the client's own add (Claude Code, opencode,
Gemini); `json` edits a strict-JSON file (VS Code, whose add drops keys and comments; Cursor,
whose add writes nothing; Claude Desktop, which has none); `append` adds a NEW table at the end
of a TOML file and edits nothing (Codex, whose add drops other servers' unknown fields, in a
file the standard library cannot edit). Every route READS the client's file, never a client
command.

Every path is built from an injected `Host`, never from the process, so one test run covers the
macOS, Linux and Windows tables."""
import json
import ntpath
import os
import posixpath
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sluice.mcpinstall.jsonc import ReadError
from sluice.mcpinstall.server import SERVER_NAME, SLUICE_ARGS, ServerSpec

DOCS_URL = "https://github.com/MrReasonable/sluice/blob/main/docs/MCP.md"


@dataclass(frozen=True)
class Host:
    home: str
    env: Mapping[str, str]
    platform: str                    # a `sys.platform` value
    which: Callable[[str], str | None]
    isdir: Callable[[str], bool]
    isfile: Callable[[str], bool]

    @property
    def path(self):
        return ntpath if self.platform == "win32" else posixpath

    def join(self, *parts: str) -> str:
        return self.path.join(*parts)


def host_from_os() -> Host:
    return Host(home=os.path.expanduser("~"), env=os.environ, platform=sys.platform,
                which=shutil.which, isdir=os.path.isdir, isfile=os.path.isfile)


ROUTES = ("command", "json", "append")


@dataclass(frozen=True)
class Client:
    name: str                 # the `--client` value
    title: str                # its docs/MCP.md heading
    anchor: str               # that heading's anchor
    route: str                # "command", "json" or "append"
    executable: str           # its CLI ("" for none): run by the command route, looked for by detect
    reader: str               # "json", "jsonc" or "toml"
    table: tuple[str, ...]    # keys from the file's top level to its server table
    remove_before_add: bool   # its add refuses an existing name (measured: Claude Code only)
    next_step: str            # what to do after registering: docs/MCP.md's text
    measured: str

    def __post_init__(self):
        # flow.py dispatches on the route by key; an unknown one must stop here, not fall
        # through to some other route's writer.
        if self.route not in ROUTES:
            raise ValueError(f"{self.name}: route {self.route!r} is not one of "
                             + ", ".join(ROUTES))


ROSTER = (
    Client("claude-code", "Claude Code", "claude-code", "command", "claude", "json",
           ("mcpServers",), True,
           "Restart Claude Code (`claude --continue` resumes the conversation, from the same "
           f"directory); the coach is `/mcp__{SERVER_NAME}__career_interview`.",
           "Claude Code 2.1.296 on macOS, 2026-10-10"),
    Client("vscode", "VS Code", "vs-code", "json", "code", "json", ("servers",), False,
           "Start it from the Command Palette (MCP: List Servers, then job-sluice, then "
           f"Start); the coach is `/mcp.{SERVER_NAME}.career_interview`.",
           "VS Code 1.141.0 on macOS, 2026-10-10"),
    Client("opencode", "opencode", "opencode", "command", "opencode", "jsonc",
           ("mcp", "servers"), False,
           f"Restart opencode; the coach is `/{SERVER_NAME}:career_interview`.",
           "opencode 2.0.25 on macOS, 2026-10-10"),
    Client("cursor", "Cursor", "cursor", "json", "cursor", "json", ("mcpServers",), False,
           "Switch job-sluice on in Settings, Tools & MCP, and start a new chat; the coach is "
           f"`/{SERVER_NAME}/career_interview`.",
           "Cursor 3.24.9 on macOS, 2026-10-10"),
    Client("claude-desktop", "Claude Desktop", "claude-desktop", "json", "", "json",
           ("mcpServers",), False,
           "Quit and reopen Claude Desktop; the coach is in the + menu as "
           "`career_interview_text`.",
           "Claude Desktop 2.19675.1 on macOS, 2026-10-09"),
    Client("codex", "Codex", "codex", "append", "codex", "toml", ("mcp_servers",), False,
           "Restart Codex. Codex shows no MCP prompts, so run the coach as AI-SETUP.md's "
           "appendix describes.",
           "Codex 0.162.1 on macOS, 2026-10-10"),
    Client("gemini", "Gemini CLI", "gemini-cli", "command", "gemini", "jsonc",
           ("mcpServers",), False,
           "Restart Gemini CLI in a folder you have marked as trusted; the coach is "
           "`/career_interview`.",
           "Gemini CLI 0.63.0 on macOS, 2026-10-10"),
)
NAMES = tuple(c.name for c in ROSTER)


def by_name(name: str) -> Client:
    for c in ROSTER:
        if c.name == name:
            return c
    raise ValueError(f"unknown client {name!r}; valid names are {', '.join(NAMES)}")


@dataclass(frozen=True)
class Found:
    evidence: str


@dataclass(frozen=True)
class NotFound:
    pass


@dataclass(frozen=True)
class Unsupported:
    reason: str


def _xdg_config(host: Host) -> str:
    value = host.env.get("XDG_CONFIG_HOME") or ""
    return value if host.path.isabs(value) else host.join(host.home, ".config")


def _appdata(host: Host) -> str:
    return host.env.get("APPDATA") or host.join(host.home, "AppData", "Roaming")


def config_path(client: Client, host: Host) -> "str | Unsupported":
    j, env, home = host.join, host.env, host.home
    mac, win = host.platform == "darwin", host.platform == "win32"
    support = j(home, "Library", "Application Support")
    if client.name == "claude-code":
        return j(env.get("CLAUDE_CONFIG_DIR") or home, ".claude.json")
    if client.name == "vscode":
        root = support if mac else _appdata(host) if win else _xdg_config(host)
        return j(root, "Code", "User", "mcp.json")
    if client.name == "opencode":
        if env.get("OPENCODE_CONFIG_DIR"):
            return j(env["OPENCODE_CONFIG_DIR"], "opencode.json")
        folder = j(_xdg_config(host), "opencode")
        jsonc, plain = j(folder, "opencode.jsonc"), j(folder, "opencode.json")
        return jsonc if host.isfile(jsonc) and not host.isfile(plain) else plain
    if client.name == "cursor":
        return j(home, ".cursor", "mcp.json")
    if client.name == "claude-desktop":
        if not (mac or win):
            return Unsupported("Claude Desktop has no Linux build")
        return j(support if mac else _appdata(host), "Claude", "claude_desktop_config.json")
    if client.name == "codex":
        return j(env.get("CODEX_HOME") or j(home, ".codex"), "config.toml")
    if client.name == "gemini":
        return j(env.get("GEMINI_CLI_HOME") or home, ".gemini", "settings.json")
    raise ValueError(f"no location for client {client.name!r}")


def detect(client: Client, host: Host) -> "Found | NotFound | Unsupported":
    path = config_path(client, host)
    if isinstance(path, Unsupported):
        return path
    if client.executable and host.which(client.executable):
        return Found(f"`{client.executable}` is on PATH")
    # A JSON or append client is written through its file, so its settings folder is evidence
    # enough. A command client needs its CLI: that is what writes.
    if client.route != "command" and host.isdir(host.path.dirname(path)):
        return Found("its settings folder exists")
    return NotFound()


def server_table(client: Client, doc: dict) -> dict:
    node = doc
    for key in client.table:
        node = node.get(key, {})
        if not isinstance(node, dict):
            raise ReadError("its server table is not an object")
    return node


def _env_key(client: Client) -> str:
    return "environment" if client.name == "opencode" else "env"


def _shape(client: Client) -> frozenset:
    """The keys of the entry install writes for this client (`entry_value`)."""
    if client.name == "opencode":
        return frozenset({"type", "command", "environment"})
    if client.name in ("claude-code", "vscode"):
        return frozenset({"type", "command", "args", "env"})
    return frozenset({"command", "args", "env"})


def extra_fields(client: Client, value: dict) -> list[str]:
    """Keys a user (or the client) put on an entry beyond what install writes: `autoApprove`,
    `cwd`, `trust`, `timeout`, ... A replace keeps them (JSON route) or refuses (command route),
    never drops them, since the copy that held them is deleted on a clean write."""
    return sorted(k for k in value if k not in _shape(client))


def check_scope(client: Client) -> str:
    """What the collateral check compares: the whole file, except for Claude Code, whose
    ~/.claude.json a running session rewrites constantly outside `mcpServers`."""
    return "table" if client.name == "claude-code" else "file"


def local_entries(client: Client, doc: dict) -> int:
    """Claude Code's LOCAL-scope `job-sluice` entries (under `projects` in ~/.claude.json): each
    takes precedence over the user-scope entry in its project, so the report says so."""
    if client.name != "claude-code" or not isinstance(doc.get("projects"), dict):
        return 0
    return sum(1 for project in doc["projects"].values()
               if isinstance(project, dict) and isinstance(project.get("mcpServers"), dict)
               and SERVER_NAME in project["mcpServers"])


def entry_value(client: Client, spec: ServerSpec, extra_env: Mapping[str, str] = {}) -> dict:
    """The `job-sluice` entry in the shape the client itself writes (measured)."""
    env = {**dict(extra_env), **spec.env_dict}
    if client.name == "opencode":
        value = {"type": "local", "command": list(spec.argv)}
    else:
        value = {"command": spec.argv[0], "args": list(spec.argv[1:])}
        if client.name in ("claude-code", "vscode"):
            value = {"type": "stdio", **value}
    if env:
        value[_env_key(client)] = dict(sorted(env.items()))
    return value


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def parse_entry(client: Client, value) -> tuple[tuple[str, ...], dict]:
    bad = ReadError(f"its job-sluice entry is not in the shape {client.title} writes")
    if not isinstance(value, dict):
        raise bad
    if client.name == "opencode":
        if not _strings(value.get("command")) or not value["command"]:
            raise bad
        argv = tuple(value["command"])
    else:
        args = value.get("args", [])
        if not isinstance(value.get("command"), str) or not _strings(args):
            raise bad
        argv = (value["command"], *args)
    env = value.get(_env_key(client), {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise bad
    return argv, dict(env)


def add_argv(client: Client, spec: ServerSpec) -> list[str]:
    """The measured add command. Element 0 is the bare executable name; the route swaps in the
    path `which` resolved, so a Windows `.cmd` shim launches."""
    pairs = [f"{k}={v}" for k, v in spec.env]
    if client.name == "claude-code":
        # `-e` is variadic: after the name, ended by `--` (measured).
        env = [a for p in pairs for a in ("-e", p)]
        return ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, *env, "--", *spec.argv]
    if client.name == "opencode":
        env = [a for p in pairs for a in ("--env", p)]
        return ["opencode", "mcp", "add", "--global", *env, SERVER_NAME, "--", *spec.argv]
    if client.name == "gemini":
        # `-e` is an array that swallows bare words, so `-s user` follows it; `-s user` exactly
        # once (doubled, it wrote project scope); no `--` (with one, the add fails). Measured.
        env = [a for p in pairs for a in ("-e", p)]
        return ["gemini", "mcp", "add", *env, "-s", "user", SERVER_NAME, *spec.argv]
    raise ValueError(f"{client.name} has no add command install runs")


def remove_argv(client: Client) -> list[str]:
    if client.name == "claude-code":
        return ["claude", "mcp", "remove", "--scope", "user", SERVER_NAME]
    raise ValueError(f"{client.name} has no remove command install runs")


def toml_str(value: str) -> str:
    """A TOML basic string. `json.dumps` is not one: it writes a character outside the BMP as
    a surrogate pair (an emoji in a launcher path, say), which TOML refuses, and the append
    route WRITES this text into the user's config.toml."""
    out = []
    for ch in value:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def snippet(client: Client, spec: ServerSpec, *, inline: bool = False) -> str:
    """The entry as text to paste (and, for Codex, to append). `inline` gives Codex's entry
    as one `job-sluice = { ... }` line, for a file whose `mcp_servers` an appended table cannot
    extend."""
    value = entry_value(client, spec)
    if client.reader == "toml":
        command = toml_str(value["command"])
        args = "[" + ", ".join(toml_str(a) for a in value["args"]) + "]"
        env = value.get("env") or {}
        if inline:
            pairs = [f"command = {command}", f"args = {args}"]
            if env:
                pairs.append("env = { " + ", ".join(f"{k} = {toml_str(v)}"
                                                    for k, v in env.items()) + " }")
            return f"{SERVER_NAME} = {{ " + ", ".join(pairs) + " }"
        lines = [f"[mcp_servers.{SERVER_NAME}]", f"command = {command}", f"args = {args}"]
        if env:
            lines += ["", f"[mcp_servers.{SERVER_NAME}.env]"]
            lines += [f"{k} = {toml_str(v)}" for k, v in env.items()]
        return "\n".join(lines)
    doc = value
    for key in reversed(client.table + (SERVER_NAME,)):
        doc = {key: doc}
    # Default ASCII escaping: this text is PRINTED, and a raw DEL or C1 character would be
    # rewritten by the terminal wrapper into an escape the JSON cannot parse
    # (core/safeout.py; apply/packet.py::render_json has the same rule).
    return json.dumps(doc, indent=2)


def redact_argv(argv, display=None) -> list[str]:
    """An EXISTING entry's argv as the report may print it: the executable (shortened by
    `display`, and hidden when it carries an `=`, the shape of `KEY=value`), sluice's own
    vocabulary, and `<other argument>` for the rest (a user can put a token in an argument)."""
    exe = "<command>" if "=" in argv[0] else (display(argv[0]) if display else argv[0])
    return [exe, *(a if a in SLUICE_ARGS else "<other argument>" for a in argv[1:])]


def display_path(path: str, host: Host) -> str:
    home = host.home.rstrip(host.path.sep)
    if path.startswith(home + host.path.sep):
        return "~" + host.path.sep + path[len(home) + 1:]
    return path


def measured_note(client: Client, host: Host) -> str:
    return "unmeasured on Windows" if host.platform == "win32" else ""
