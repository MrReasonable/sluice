"""docs/MCP.md's "Install in your client" section: every entry's registration, parsed the way
its client reads it.

Each entry was MEASURED on a real client (the capability line carries the version and date);
CI cannot re-measure a client, so what is pinned here is the shape of what the doc tells a user
to type or paste: the command is a placeholder (never a real path, which would be the
measuring machine's), the sluice argv is a real `job-sluice` command, and `--write` appears
exactly where the roster says.
"""
import glob
import json
import ntpath
import os
import posixpath
import re
import shlex
import tomllib

import pytest

from tests.test_docs_claims import _DOCS, _shell_blocks

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP = os.path.join(_ROOT, "docs", "MCP.md")

SHELL_PLACEHOLDER = "$JOB_SLUICE"          # after shlex: the quotes are gone
CONFIG_PLACEHOLDER = "<output of: command -v job-sluice>"
ASSIGNMENT = "JOB_SLUICE=$(command -v job-sluice)"
DOCKER_PREFIX = ["docker", "compose", "-f", "$SLUICE_COMPOSE", "run", "--rm", "-T"]

# Hand-written, never derived from the doc: (fence language, carries --write).
# Edit this when Task 3's measurements leave a client out.
CLIENTS = {
    "Claude Code": ("bash", True),
    "Docker (Claude Code)": ("bash", True),
    "opencode": ("json", True),
    "Claude Desktop": ("json", True),
    "Cursor": ("json", True),
    "VS Code": ("jsonc", True),
    "Codex": ("toml", True),
    "Gemini CLI": ("json", True),
}


def _doc():
    with open(_MCP, encoding="utf-8") as fh:
        return fh.read()


def client_entries(text):
    """`### <name>` sections under `## Install in your client`, up to the next `## `."""
    m = re.search(r"^## Install in your client\n(.*?)(?=^## )", text, re.M | re.S)
    assert m, "docs/MCP.md has no '## Install in your client' section"
    parts = re.split(r"^### (.+)\n", m.group(1), flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _fenced(section, lang):
    out, inside, buf = [], None, []
    for ln in section.split("\n"):
        f = re.match(r"^```(\w*)\s*$", ln)
        if f and inside is None:
            inside, buf = f.group(1).lower(), []
        elif ln.strip() == "```" and inside is not None:
            if inside == lang:
                out.append("\n".join(buf))
            inside = None
        elif inside is not None:
            buf.append(ln)
    return out


def _strip_jsonc(s):
    # Whole-line `//` comments only: VS Code's mcp.json allows them, and a value never starts
    # a line with `//` in these snippets.
    return "\n".join(ln for ln in s.split("\n") if not ln.lstrip().startswith("//"))


def _server(obj):
    """The one sluice server entry inside a parsed config, whatever the client's top key."""
    for top in ("mcpServers", "servers", "mcp", "mcp_servers"):
        if top in obj:
            table = obj[top]
            # opencode v2 nests its servers one level down (measured: the v1 `mcp.<name>`
            # shape reads as "No MCP servers configured").
            if top == "mcp":
                table = table["servers"]
            (entry,) = table.values()
            return entry
    raise AssertionError(f"no server table in {sorted(obj)}")


def _argv(name, section):
    lang, _ = CLIENTS[name]
    if lang == "bash":
        (block,) = _shell_blocks(section)
        lines = [ln for ln in block.split("\n") if ln.strip()]
        add = [ln for ln in lines if ln.startswith("claude mcp add")]
        assert len(add) == 1, f"{name}: expected one `claude mcp add` line, got {add}"
        toks = shlex.split(add[0])
        cmd = toks[toks.index("--") + 1:]
        if name.startswith("Docker"):
            assert any(ln.startswith('SLUICE_COMPOSE="$PWD/') for ln in lines), name
            assert cmd[:len(DOCKER_PREFIX)] == DOCKER_PREFIX, (name, cmd)
            svc = cmd[len(DOCKER_PREFIX)]
            assert svc == _compose_mcp_service(), (name, svc)
            return cmd[0], cmd[len(DOCKER_PREFIX) + 1:], toks
        assert ASSIGNMENT in lines, f"{name}: the block must set JOB_SLUICE before using it"
        return cmd[0], cmd[1:], toks
    (block,) = _fenced(section, lang)
    obj = tomllib.loads(block) if lang == "toml" else json.loads(
        _strip_jsonc(block) if lang == "jsonc" else block)
    entry = _server(obj)
    command = entry["command"]
    if isinstance(command, list):        # opencode: command is the whole argv
        return command[0], command[1:], command
    return command, list(entry.get("args", [])), [command, *entry.get("args", [])]


def _compose_mcp_service():
    """The compose service whose command is `["mcp", "serve"]`, read from the shipped file."""
    with open(os.path.join(_ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^  ([a-z-]+):\n(?:    .*\n)*?    command: \[\"mcp\", \"serve\"\]", text, re.M)
    assert m, "docker-compose.yml has no service with command [\"mcp\", \"serve\"]"
    return m.group(1)


def test_the_install_section_has_exactly_the_rostered_clients():
    assert set(client_entries(_doc())) == set(CLIENTS)


@pytest.mark.parametrize("name", sorted(CLIENTS))
def test_each_entry_registers_the_placeholder_and_a_real_sluice_command(name):
    from sluice.cli import _build_parser
    section = client_entries(_doc())[name]
    exe, argv, everything = _argv(name, section)
    if name.startswith("Docker"):
        assert exe == "docker"
    elif CLIENTS[name][0] == "bash":
        assert exe == SHELL_PLACEHOLDER, (name, exe)
    else:
        assert exe == CONFIG_PLACEHOLDER, (name, exe)
    assert argv[:2] == ["mcp", "serve"], (name, argv)
    assert ("--write" in argv) == CLIENTS[name][1], (name, argv)
    _build_parser().parse_args(argv)          # raises SystemExit on an unknown flag
    for v in everything:
        assert not (posixpath.isabs(v) or ntpath.isabs(v) or v.startswith("~")), (name, v)


@pytest.mark.parametrize("name", sorted(CLIENTS))
def test_each_entry_states_what_was_measured(name):
    # Whitespace-normalised: the sentence wraps across lines in the doc.
    section = " ".join(client_entries(_doc())[name].split())
    assert re.search(r"Measured with .+? on \d{4}-\d{2}-\d{2}", section), name
    assert "prompts" in section and "form" in section, name


@pytest.mark.parametrize("value", ["/opt/bin/job-sluice", "C:\\Tools\\job-sluice.exe",
                                   "\\\\server\\share\\job-sluice", "~/bin/job-sluice"])
def test_the_path_check_rejects_every_absolute_form(value):
    assert posixpath.isabs(value) or ntpath.isabs(value) or value.startswith("~")


_ADD = re.compile(r"claude mcp add(?=\s+[-A-Za-z])")
# Files expected to carry at least one invocation. Scope: a sweep that found none would pass.
_CARRIERS = {"README.md", "docs/MCP.md", "docs/AI-SETUP.md"}


def _invocations(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        text = fh.read()
    if rel.endswith(".py"):
        candidates = [text]
    else:
        candidates = _shell_blocks(text) + re.findall(r"`([^`\n]+)`", text)
    out = []
    for c in candidates:
        c = c.replace("\\\n", " ")
        for ln in c.split("\n"):
            m = _ADD.search(ln)
            if m:
                out.append(ln[m.start():])
    return out


def _scanned():
    return sorted(set(_DOCS) | {os.path.relpath(p, _ROOT) for p in
                               glob.glob(os.path.join(_ROOT, "sluice", "**", "*.py"),
                                         recursive=True)})


def test_every_claude_mcp_add_is_user_scoped_and_uses_one_command_form():
    found = {}
    for rel in _scanned():
        for inv in _invocations(rel):
            found.setdefault(rel, []).append(inv)
            toks = shlex.split(inv)
            assert "--scope" in toks and toks[toks.index("--scope") + 1] == "user", (rel, inv)
            if "--" in toks:
                assert toks[toks.index("--") + 1] in (SHELL_PLACEHOLDER, "docker"), (rel, inv)
    missing = _CARRIERS - set(found)
    assert not missing, f"no `claude mcp add` invocation found in {sorted(missing)}"
