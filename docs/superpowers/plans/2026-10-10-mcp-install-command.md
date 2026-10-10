# `job-sluice mcp install` Implementation Plan (PR 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command, `job-sluice mcp install`, that finds the MCP clients on this machine and
registers the job-sluice server in each at user scope, proven by reading each client's config
file back.

**Architecture:** A stdlib-only package `sluice/mcpinstall/`: `jsonc.py` (readers), `server.py`
(the launcher, the pinned environment, `ServerSpec`), `clients.py` (the roster as data: paths per
platform, detection, entry shapes, add/remove argv), `routes.py` (read state, the JSON route's
compare-and-set write, the command route's copy/recheck/run, the append route's pre-parsed
append for Codex, readback and the collateral check),
`flow.py` (choose, dry-run, report, exit code). `cli.py::cmd_mcp_install` builds the real
host/deps and calls `flow.run`. Every OS touchpoint is injected, so no test reaches a real client.

**Tech Stack:** Python 3.12+ stdlib (`json`, `tomllib`, `subprocess`, `ntpath`/`posixpath`),
`sluice.core.atomicfile.replace_if` and `sluice.core.backup.write_copy` (PR 1), pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-mcp-install-design.md` (amended 2026-10-10 with
the client measurements, commit `043dbda7`). Read its Decisions table first: five rows were
added from the measurements and they change the routes.

## Global Constraints

- `sluice/` is standard-library only; nothing in `sluice/mcpinstall/` imports the `mcp` package
  (the mcpserver sweep in `tests/test_mcpserver.py` enforces "no mcp import" outside
  `build_server`; `importlib.util.find_spec("mcp")` is a string lookup, not an import).
- Server name `job-sluice`. Roster names, exactly: `claude-code`, `vscode`, `opencode`, `cursor`,
  `claude-desktop`, `codex`, `gemini`.
- Routes: command = claude-code, opencode, gemini; JSON = vscode, cursor, claude-desktop;
  append = codex (a new table appended to `config.toml` when it has no entry; an existing
  entry that differs is `manual`, the snippet printed and nothing written).
- Every registration is READ from the client's config file; a client command only writes.
- Outcomes, exactly: `registered`, `replaced`, `unchanged`, `refused`, `failed`, `manual`.
- Exit codes: 2 when the `mcp` extra is missing or the launcher is not an installed `job-sluice`
  (before detecting anything); 1 when any client ends `failed` or `refused`, or `manual` for a
  client named by `--client`; else 0.
- Never print a client's raw output (the production runner sends it to `DEVNULL`), an env value
  of a key outside `PINNED_ENV`, or an existing entry's argv element outside `{mcp, serve,
  --write}` beyond element 0. Copies are named relative to sluice's state folder.
- Copies go to `mcp_install_backups/` in sluice's XDG state folder (0o700), carry the file's
  mode, and are deleted when that client's write ends clean.
- Every path in a test is built from a placeholder home that is itself JOINED from parts
  (`posixpath.join("/Users", "example")`, `ntpath.join("C:" + "\\", "Users", "example")`), so no
  home-path literal appears in any tracked file, this plan included: `tests/test_no_leaked_files.py`
  greps every tracked file, and Task 3 adds its Windows form.
- No line numbers in comments (`tests/test_citation_drift.py`); cite `file.py::symbol`.
- Conventional commits; every commit ends with the trailer
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Before any mutation witness: `.venv/bin/python -m compileall -q -f --invalidation-mode
  checked-hash sluice tests scripts`, and commit first. Mutate by MOVING or DELETING.
- This repo's pytest config is quiet: `-q` prints no summary. Read results with
  `.venv/bin/python -m pytest <target> -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed"`.

## Review Focus

1. A Claude Code session running while install runs (the AI-SETUP case) rewrites
   `~/.claude.json` outside `mcpServers`: install must still register — pinned by Task 5's
   `test_an_edit_outside_the_server_table_does_not_stop_the_command_route`.
2. A config file that is a symlink into a dotfiles repo: the JSON route replaces the TARGET and
   the link survives — Task 4's `test_a_symlinked_file_has_its_target_replaced`.
3. A launcher path with spaces and non-ASCII characters (an emoji included) reaches every
   client argv, JSON entry and TOML entry intact — Task 4's
   `test_a_path_with_spaces_and_non_ascii_round_trips`, Task 5's
   `test_the_add_argv_carries_a_spaced_launcher_as_one_element`, Task 3's
   `test_the_codex_snippet_escapes_any_path_codex_can_read` and Task 6's
   `test_a_non_ascii_launcher_is_appended_as_valid_toml`.
4. stdin at EOF in an interactive run (a piped empty stdin forced interactive in a test, or Ctrl-D)
   takes each question's default and never loops — Task 7's
   `test_eof_takes_every_default`.
5. opencode with only `opencode.jsonc` present: install reads and the add edits the `.jsonc`;
   with both present, `.json` — Task 3's `test_opencode_picks_jsonc_only_when_it_is_alone`.

---

## File Structure

| File | Responsibility |
|---|---|
| `sluice/mcpinstall/__init__.py` | Package docstring only (importing a submodule loads nothing else) |
| `sluice/mcpinstall/jsonc.py` | `ReadError`, `strip_jsonc`, `load(data, *, jsonc)`: strict and JSONC readers |
| `sluice/mcpinstall/server.py` | `SERVER_NAME`, `SLUICE_ARGS`, `PINNED_ENV`, `ServerSpec`, `LauncherError`, `resolve_launcher`, `build_spec` |
| `sluice/mcpinstall/clients.py` | `Host`, `host_from_os`, `Client`, `ROUTES`, `ROSTER`, `NAMES`, `Found`/`NotFound`/`Unsupported`, `config_path`, `detect`, `server_table`, `entry_value`, `parse_entry`, `add_argv`, `remove_argv`, `toml_str`, `snippet`, `redact_argv`, `display_path`, `measured_note` |
| `sluice/mcpinstall/routes.py` | `Absent`/`Entry`/`Unreadable`/`FileState`/`Outcome`/`Deps`, `read_state`, `matches`, `apply_json`, `apply_command`, `appended`, `cannot_append`, `edit_by_hand`, `apply_append`, `run_quietly`, `BACKUP_FOLDER` |
| `sluice/mcpinstall/flow.py` | `Options`, `run` |
| `sluice/cli.py` | `cmd_mcp_install`, the `mcp install` parser |
| `tests/mcpinstall/__init__.py`, `conftest.py`, `fakes.py` | Sandbox fixture, rigs, fake client CLIs |
| `tests/mcpinstall/test_jsonc.py`, `test_server.py`, `test_clients.py`, `test_json_route.py`, `test_command_route.py`, `test_append_route.py`, `test_flow.py` | Tests per module |
| `tests/test_no_leaked_files.py` | Windows home-path form |
| `tests/test_mcp_install_docs.py`, `tests/test_ai_setup_contract.py` | Doc pins |
| `docs/MCP.md`, `docs/AI-SETUP.md`, `docs/USAGE.md`, `README.md`, `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md` | Docs |

---

### Task 1: Readers (`jsonc.py`) and the test package

**Files:**
- Create: `sluice/mcpinstall/__init__.py`, `sluice/mcpinstall/jsonc.py`
- Create: `tests/mcpinstall/__init__.py` (empty; check `tests/functional/` has one and mirror it), `tests/mcpinstall/test_jsonc.py`

**Interfaces:**
- Produces: `class ReadError(ValueError)` (its `str()` is a printable reason with no file
  content); `strip_jsonc(text: str) -> str`; `load(data: bytes, *, jsonc: bool) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcpinstall/test_jsonc.py
"""The readers `mcp install` reads client config files with. Strict JSON is what install
writes; JSONC is read-only, for the clients whose files carry comments (opencode, Gemini)."""
import json
import random

import pytest

from sluice.mcpinstall import jsonc


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1} // trailing', {"a": 1}),
    ('// top\n{"a": 1}', {"a": 1}),
    ('{"a": /* inline */ 1}', {"a": 1}),
    ('{"a": "http://x//y"}', {"a": "http://x//y"}),
    ('{"a": "/* not a comment */"}', {"a": "/* not a comment */"}),
    ('{"a": [1, 2,],}', {"a": [1, 2]}),
    ('{"a": 1, // c\n}', {"a": 1}),
    ('{"a": "x\\"//y"}', {"a": 'x"//y'}),
])
def test_jsonc_drops_comments_and_trailing_commas_outside_strings(text, expected):
    assert jsonc.load(text.encode(), jsonc=True) == expected


def test_an_unterminated_block_comment_is_refused():
    with pytest.raises(jsonc.ReadError):
        jsonc.load(b'{"a": 1} /* open', jsonc=True)


def _random_json(rng, depth=0):
    kind = rng.choice(["obj", "list", "str", "num", "bool", "null"] if depth < 3
                      else ["str", "num", "bool", "null"])
    if kind == "obj":
        return {f"k{i}{rng.choice(['', '//', '/*', ',', '}'])}": _random_json(rng, depth + 1)
                for i in range(rng.randint(0, 4))}
    if kind == "list":
        return [_random_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if kind == "str":
        return rng.choice(["", "plain", "a//b", "/*x*/", "q\"uote", "tab\t", "c,]"])
    if kind == "num":
        return rng.choice([0, -3, 2.5, 10**12])
    return rng.choice([True, False, None])


def test_on_comment_free_json_it_reads_exactly_what_json_loads_reads():
    rng = random.Random(20261010)
    for _ in range(300):
        doc = {"root": _random_json(rng)}
        text = json.dumps(doc, indent=rng.choice([None, 2]))
        assert jsonc.load(text.encode(), jsonc=True) == json.loads(text)


@pytest.mark.parametrize("data,reason", [
    (b"\xef\xbb\xbf{}", "byte-order mark"),
    (b'{"a": "\xff"}', "not UTF-8"),
    (b"[1, 2]", "top level is not an object"),
    (b'{"a": 1, "a": 2}', "a key appears twice"),
    (b'{"a": }', "not valid JSON"),
    (b'{"a": 1} // c', "comments or trailing commas"),
])
def test_strict_refusals(data, reason):
    with pytest.raises(jsonc.ReadError) as exc:
        jsonc.load(data, jsonc=False)
    assert reason in str(exc.value)


def test_a_refusal_never_quotes_the_file():
    with pytest.raises(jsonc.ReadError) as exc:
        jsonc.load(b'{"token": "SENTINEL-NOT-A-SECRET-1", }', jsonc=False)
    assert "SENTINEL" not in str(exc.value)


@pytest.mark.parametrize("reader", [True, False])
def test_an_empty_file_is_an_empty_document(reader):
    assert jsonc.load(b"  \n", jsonc=reader) == {}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_jsonc.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'sluice.mcpinstall'`.

- [ ] **Step 3: Implement**

```python
# sluice/mcpinstall/__init__.py
"""`job-sluice mcp install`: register the job-sluice MCP server in the AI clients installed on
this machine, at user scope, and prove it by reading each client's config file back.

Stdlib only, and nothing from the `mcp` package. This file imports nothing, so the parser can
import `clients` for `--client`'s choices without loading the rest. `flow.py` is the entry
point; the spec is docs/superpowers/specs/2026-10-10-mcp-install-design.md."""
```

```python
# sluice/mcpinstall/jsonc.py
"""The readers for the client config files `mcp install` reads.

Strict JSON is the only thing install WRITES, so the JSON route reads strictly and refuses
what it could not re-save without loss: a byte-order mark, non-UTF-8 bytes, comments, a
duplicate key (`json.loads` keeps the last, so a re-save would silently drop the other).

JSONC is read-only: opencode and Gemini keep comments in their files, and install still has to
read their server table for the collateral check. Comments and trailing commas are dropped
outside strings and the rest goes to `json.loads`.

A `ReadError`'s text is printed, so it names the PROBLEM and never quotes the file, whose
values can be another tool's credentials."""
import json


class ReadError(ValueError):
    """A file install will not read as a server table; `str()` is the printable reason."""


def strip_jsonc(text: str) -> str:
    """`text` with `//` and `/* */` comments and trailing commas removed, outside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ReadError("it has an unterminated /* comment")
            out.append(" ")
            i = end + 2
            continue
        out.append(c)
        i += 1
    return _drop_trailing_commas("".join(out))


def _drop_trailing_commas(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _no_duplicates(pairs):
    doc = {}
    for key, value in pairs:
        if key in doc:
            raise ReadError("a key appears twice in one object, and a re-save would drop one")
        doc[key] = value
    return doc


def load(data: bytes, *, jsonc: bool) -> dict:
    """Parse a config file's bytes. An empty (or whitespace-only) file is an empty document,
    the state a create starts from. Raises `ReadError` with a printable reason."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise ReadError("it starts with a byte-order mark, which install does not write")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadError("it is not UTF-8") from None
    if not text.strip():
        return {}
    source = strip_jsonc(text) if jsonc else text
    try:
        doc = json.loads(source, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as exc:
        if not jsonc:
            try:
                json.loads(strip_jsonc(text))
            except (json.JSONDecodeError, ReadError):
                pass
            else:
                raise ReadError("it has comments or trailing commas, and install writes plain "
                                "JSON only") from None
        raise ReadError(f"it is not valid JSON (line {exc.lineno})") from None
    if not isinstance(doc, dict):
        raise ReadError("its top level is not an object")
    return doc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_jsonc.py -rA 2>&1 | grep -E "passed|failed"`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add sluice/mcpinstall tests/mcpinstall
git commit -F - <<'EOF'
feat(mcp): strict and JSONC readers for client config files

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 2: The server command (`server.py`)

**Files:**
- Create: `sluice/mcpinstall/server.py`, `tests/mcpinstall/test_server.py`

**Interfaces:**
- Produces: `SERVER_NAME = "job-sluice"`; `SLUICE_ARGS: frozenset[str]` = `{"mcp", "serve",
  "--write"}`; `PINNED_ENV: tuple[str, ...]`; `@dataclass(frozen=True) ServerSpec(argv:
  tuple[str, ...], env: tuple[tuple[str, str], ...])` with property `env_dict -> dict`;
  `class LauncherError(ValueError)`; `resolve_launcher(argv0: str, *, windows: bool) -> str`;
  `build_spec(launcher: str, env: Mapping[str, str], write: bool) -> ServerSpec`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcpinstall/test_server.py
import ast
import os
import pathlib

import pytest

import sluice
from sluice.core import paths
from sluice.mcpinstall import server
from tests.conftest import PATH_ENV_VARS


def _exe(path: pathlib.Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_the_launcher_is_argv0_made_absolute(tmp_path, monkeypatch):
    _exe(tmp_path / "bin" / "job-sluice")
    monkeypatch.chdir(tmp_path)
    assert server.resolve_launcher("bin/job-sluice", windows=False) == str(
        tmp_path / "bin" / "job-sluice")


def test_a_launcher_reached_through_a_symlink_keeps_the_link_path(tmp_path):
    target = _exe(tmp_path / "Cellar" / "4.3.1" / "bin" / "job-sluice")
    link = tmp_path / "bin" / "job-sluice"
    link.parent.mkdir()
    link.symlink_to(target)
    assert server.resolve_launcher(str(link), windows=False) == str(link)


@pytest.mark.parametrize("name", ["cli.py", "__main__.py", "python3"])
def test_anything_but_the_installed_launcher_is_refused(tmp_path, name):
    with pytest.raises(server.LauncherError) as exc:
        server.resolve_launcher(_exe(tmp_path / name), windows=False)
    assert "job-sluice" in str(exc.value) and str(tmp_path) not in str(exc.value)


def test_a_launcher_that_is_not_executable_is_refused(tmp_path):
    p = tmp_path / "job-sluice"
    p.write_text("")
    p.chmod(0o644)
    with pytest.raises(server.LauncherError):
        server.resolve_launcher(str(p), windows=False)


def test_the_spec_runs_the_launcher_with_mcp_serve_and_write():
    spec = server.build_spec("/opt/x/job-sluice", {}, write=True)
    assert spec.argv == ("/opt/x/job-sluice", "mcp", "serve", "--write")
    assert server.build_spec("/opt/x/job-sluice", {}, write=False).argv[-1] == "serve"


def test_a_pinned_path_is_made_absolute_and_an_unset_one_is_not_pinned():
    spec = server.build_spec("/opt/x/job-sluice", {"SLUICE_CONFIG": "~/x.yaml",
                                                   "OPENAI_API_KEY": "SENTINEL-NOT-A-SECRET-2"},
                             write=True)
    assert spec.env_dict == {"SLUICE_CONFIG": os.path.join(os.path.expanduser("~"), "x.yaml")}


def test_every_pinned_name_is_carried_not_only_the_config():
    env = {k: f"/state/{k.lower()}" for k in server.PINNED_ENV}
    assert set(server.build_spec("/x/job-sluice", env, write=True).env_dict) == set(
        server.PINNED_ENV)


def _derived_pinned_env() -> set:
    """Every env var sluice reads a relocating path from, derived from source: `env_var=`
    keywords passed to `core/paths.py::resolve` under ANY local binding (aliases included),
    every `os.environ` read of VAULT_DIR, and the XDG base variables `paths._ROOTS` reads."""
    pkg = pathlib.Path(sluice.__file__).parent
    found = set()
    for py in sorted(pkg.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        funcs = {"resolve"} if py.name == "paths.py" and py.parent.name == "core" else set()
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sluice.core.paths":
                funcs |= {a.asname or a.name for a in node.names if a.name == "resolve"}
            if isinstance(node, ast.ImportFrom) and node.module == "sluice.core":
                mods |= {a.asname or a.name for a in node.names if a.name == "paths"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_resolve = (isinstance(f, ast.Name) and f.id in funcs) or (
                isinstance(f, ast.Attribute) and f.attr == "resolve"
                and isinstance(f.value, ast.Name) and f.value.id in mods)
            if is_resolve:
                for kw in node.keywords:
                    if kw.arg == "env_var" and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        found.add(kw.value.value)
            if isinstance(f, ast.Attribute) and f.attr == "get" and ast.unparse(
                    f.value) == "os.environ" and node.args and isinstance(
                    node.args[0], ast.Constant) and node.args[0].value == "VAULT_DIR":
                found.add("VAULT_DIR")
    return found | {var for var, _ in paths._ROOTS.values()}


def test_pinned_env_is_every_relocating_path_variable_sluice_reads():
    derived = _derived_pinned_env()
    # SCOPE: a walk that found nothing, or missed an import form, cannot pass. The regex
    # sweep's roster is a second, independent engine over the same call sites.
    assert {"SLUICE_CONFIG", "SEEN_DB", "DOSSIER_DIR", "VAULT_DIR"} <= derived
    assert set(PATH_ENV_VARS) <= derived
    assert set(server.PINNED_ENV) == derived


def test_no_credential_is_ever_pinned():
    assert not any("KEY" in k or "TOKEN" in k or "TELEGRAM" in k for k in server.PINNED_ENV)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_server.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: collection ERROR, `cannot import name 'server'`.

- [ ] **Step 3: Implement**

```python
# sluice/mcpinstall/server.py
"""What `mcp install` registers: the installed `job-sluice` launcher, `mcp serve`, `--write`
unless read-only, and sluice's relocating path variables as the install saw them.

The launcher is `abspath(argv0)` and NOT `realpath`: a Homebrew or pipx launcher is a symlink
into a versioned folder the next upgrade deletes, so the link is what survives.

The pinned variables exist because a client started from a desktop never sees the shell's
exports: without them its server would open a different config or vault from the one the user
was running when install said `registered`. `PINNED_ENV` is a literal; the test
`tests/mcpinstall/test_server.py::test_pinned_env_is_every_relocating_path_variable_sluice_reads`
derives the same set from source and fails when they differ. Credentials are never pinned."""
import os
from collections.abc import Mapping
from dataclasses import dataclass

SERVER_NAME = "job-sluice"
# sluice's own argument vocabulary: the only elements of an EXISTING entry's argv the report
# prints in clear (besides the executable), since an entry a user wrote can carry a token.
SLUICE_ARGS = frozenset({"mcp", "serve", "--write"})

PINNED_ENV = (
    "DOSSIER_DIR", "SEEN_DB", "SLUICE_CONFIG", "SLUICE_DISABLED", "SLUICE_FX_CACHE",
    "SLUICE_HEALTH", "SLUICE_USAGE", "TRIAGE_AUDIT", "VAULT_DIR",
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
)


@dataclass(frozen=True)
class ServerSpec:
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]   # sorted by name

    @property
    def env_dict(self) -> dict:
        return dict(self.env)


class LauncherError(ValueError):
    """Install was not started as the installed `job-sluice`; `str()` is printable."""


def resolve_launcher(argv0: str, *, windows: bool) -> str:
    path = os.path.abspath(argv0)
    names = ("job-sluice", "job-sluice.exe", "job-sluice.cmd") if windows else ("job-sluice",)
    # Unmeasured on Windows: a console-script launcher may report itself without `.exe`.
    if windows and not os.path.splitext(path)[1] and os.path.isfile(path + ".exe"):
        path += ".exe"
    base = os.path.basename(path).lower() if windows else os.path.basename(path)
    if base not in names or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise LauncherError(
            f"this was started as {os.path.basename(argv0)!r}, which a client cannot start; "
            "run the installed `job-sluice mcp install` instead")
    return path


def build_spec(launcher: str, env: Mapping[str, str], write: bool) -> ServerSpec:
    argv = (launcher, "mcp", "serve") + (("--write",) if write else ())
    pinned = tuple(sorted((k, os.path.abspath(os.path.expanduser(env[k])))
                          for k in PINNED_ENV if env.get(k)))
    return ServerSpec(argv, pinned)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_server.py -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"`
Expected: all passed. If the roster test fails, read the symmetric difference it prints: a name
in source and not in `PINNED_ENV` is added to `PINNED_ENV`; never narrow the derivation.

- [ ] **Step 5: Commit, then witness spec mutant 8**

```bash
git add sluice/mcpinstall/server.py tests/mcpinstall/test_server.py
git commit -F - <<'EOF'
feat(mcp): the server command mcp install registers

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Witness 8a: delete `"SEEN_DB", ` from `PINNED_ENV` → `test_pinned_env_is_every_relocating_path_variable_sluice_reads` FAILS. Restore (`git checkout sluice/mcpinstall/server.py`).
Witness 8b: in `build_spec` replace `for k in PINNED_ENV` with `for k in ("SLUICE_CONFIG",)` → `test_every_pinned_name_is_carried_not_only_the_config` FAILS. Restore.

---

### Task 3: The client roster (`clients.py`), the sandbox, the leak gate

**Files:**
- Create: `sluice/mcpinstall/clients.py`, `tests/mcpinstall/conftest.py`, `tests/mcpinstall/test_clients.py`
- Modify: `tests/test_no_leaked_files.py` (the Windows form)

**Interfaces:**
- Consumes: `server.SERVER_NAME`, `server.SLUICE_ARGS`, `server.ServerSpec`, `jsonc.ReadError`.
- Produces:
  - `@dataclass(frozen=True) Host(home: str, env: Mapping[str, str], platform: str, which: Callable[[str], str | None], isdir: Callable[[str], bool], isfile: Callable[[str], bool])` with `.path` (ntpath on `"win32"`, else posixpath) and `.join(*parts)`; `host_from_os() -> Host`.
  - `ROUTES = ("command", "json", "append")`; `@dataclass(frozen=True) Client(name, title, anchor, route, executable, reader, table: tuple[str, ...], remove_before_add: bool, next_step, measured)`, raising `ValueError` naming `ROUTES` for any other route; `ROSTER: tuple[Client, ...]`; `NAMES: tuple[str, ...]`; `by_name(name) -> Client`.
  - `Found(evidence: str)`, `NotFound()`, `Unsupported(reason: str)`; `config_path(client, host) -> str | Unsupported`; `detect(client, host) -> Found | NotFound | Unsupported`.
  - `server_table(client, doc: dict) -> dict` (raises `ReadError`); `entry_value(client, spec, extra_env: Mapping = {}) -> dict`; `parse_entry(client, value) -> tuple[tuple[str, ...], dict]` (raises `ReadError`).
  - `add_argv(client, spec) -> list[str]`, `remove_argv(client) -> list[str]` (element 0 is `client.executable`; the caller swaps in the resolved path).
  - `toml_str(value: str) -> str` (a TOML basic string); `snippet(client, spec, *, inline=False) -> str` (`inline=True`: Codex's entry as one `job-sluice = { ... }` line); `redact_argv(argv, display=None) -> list[str]`; `display_path(path, host) -> str`; `measured_note(client, host) -> str`.
  - `check_scope(client) -> str` (`"table"` for Claude Code, else `"file"`); `extra_fields(client, value: dict) -> list[str]` (an entry's keys beyond the shape install writes); `local_entries(client, doc: dict) -> int` (Claude Code local-scope `job-sluice` entries in the same file).

- [ ] **Step 1: Write the sandbox fixture**

```python
# tests/mcpinstall/conftest.py
"""Sandbox for the install tests, on top of tests/conftest.py's path pins.

PATH is an empty folder, so a real `claude` or `code` on this machine is never found, and every
variable that relocates a client's config is removed, so nothing resolves into a real profile.
`test_clients.py::test_production_lookups_find_no_client_in_the_sandbox` proves it."""
import pytest

CLIENT_ENV = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "VSCODE_IPC_HOOK_CLI", "APPDATA",
              "LOCALAPPDATA", "USERPROFILE", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG",
              "GEMINI_CLI_HOME", "XDG_DATA_HOME")


@pytest.fixture(autouse=True)
def _client_sandbox(tmp_path, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for var in CLIENT_ENV:
        monkeypatch.delenv(var, raising=False)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/mcpinstall/test_clients.py
import ntpath
import os
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


def test_an_unknown_route_fails_at_construction():
    import dataclasses
    with pytest.raises(ValueError, match="command, json, append"):
        dataclasses.replace(clients.by_name("codex"), route="apend")


def test_a_json_snippet_nests_under_the_client_s_table():
    import json
    snip = json.loads(clients.snippet(clients.by_name("opencode"), SPEC))
    assert snip["mcp"]["servers"]["job-sluice"]["type"] == "local"


def test_redaction_shows_only_the_executable_and_sluice_s_vocabulary():
    assert clients.redact_argv(["/x/job-sluice", "mcp", "serve", "--token", "SENTINEL",
                                "--write"]) == [
        "/x/job-sluice", "mcp", "serve", "<other argument>", "<other argument>", "--write"]


def test_redaction_hides_a_command_shaped_like_a_setting():
    assert clients.redact_argv(["API_KEY=SENTINEL", "x"]) == ["<command>", "<other argument>"]
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
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_clients.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: collection ERROR, `cannot import name 'clients'`.

- [ ] **Step 4: Implement**

```python
# sluice/mcpinstall/clients.py
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
           "directory); the coach is `/mcp__job-sluice__career_interview`.",
           "Claude Code 2.1.296 on macOS, 2026-10-10"),
    Client("vscode", "VS Code", "vs-code", "json", "code", "json", ("servers",), False,
           "Start it from the Command Palette (MCP: List Servers, then job-sluice, then "
           "Start); the coach is `/mcp.job-sluice.career_interview`.",
           "VS Code 1.141.0 on macOS, 2026-10-10"),
    Client("opencode", "opencode", "opencode", "command", "opencode", "jsonc",
           ("mcp", "servers"), False,
           "Restart opencode; the coach is `/job-sluice:career_interview`.",
           "opencode 2.0.25 on macOS, 2026-10-10"),
    Client("cursor", "Cursor", "cursor", "json", "cursor", "json", ("mcpServers",), False,
           "Switch job-sluice on in Settings, Tools & MCP, and start a new chat; the coach is "
           "`/job-sluice/career_interview`.",
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
    return json.dumps(doc, indent=2, ensure_ascii=False)


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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"`
Expected: all passed.

- [ ] **Step 6: Add the leak gate's Windows form**

Nothing is allow-listed: the tests join their placeholder from parts, so ANY Windows home path
`git grep` finds outside this guard's own file is a failure, and no second (Python) pattern is
needed to decide which hits are allowed. The separators are any run of `\` or `/`: a JSON path
inside a Python string carries four backslashes, and a path can mix a backslash with a slash. Every
home-shaped string below is concatenated so that this plan and the test file stay clean under
the gate they describe.

First the failing tests, appended after `test_the_allowance_is_scoped_to_the_file_that_needs_it`:

```python
def test_no_windows_home_path_is_tracked():
    out = _git("grep", "-n", "-I", "-i", "-E", _WIN_GREP,
               *(("--",) + _GATE_PATHSPEC if _GATE_PATHSPEC else ()), allow=(0, 1))
    hits = [ln for ln in out.splitlines() if not ln.startswith("tests/test_no_leaked_files.py:")]
    assert not hits, f"absolute Windows home path in tracked files: {hits}"


def test_the_windows_gate_catches_every_separator_form_through_git(tmp_path):
    """Run through the engine the gate uses: a pattern that works in Python `re` and not in
    `git grep -E` would certify a blind gate (this file's own history)."""
    import subprocess
    bs = "\\"
    planted = [f"C:{bs}Users{bs}one", f"C:{bs * 2}Users{bs * 2}two",
               f"C:{bs * 4}Users{bs * 4}three", f"C:{bs}Users/four", f"c:{bs}users{bs}five"]
    control = f"C:{bs}Program Files{bs}x"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("\n".join(planted + [control]) + "\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    out = subprocess.run(["git", "-C", str(tmp_path), "grep", "-n", "-I", "-i", "-E", _WIN_GREP],
                         capture_output=True, text=True).stdout
    found = {int(line.split(":", 2)[1]) for line in out.splitlines()}
    assert found == set(range(1, len(planted) + 1)), out
```

And make `test_the_gate_actually_uses_the_declared_pathspec` check EVERY gate call, not the
first (its body today slices from the first `out = _git("grep"`):

```python
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    calls = src.split("out = _git(\"grep\"")[1:]
    assert len(calls) >= 2, "expected the POSIX gate call and the Windows gate call"
    for call in calls:
        call = call[:call.index("allow=(0, 1))")]
        assert "_GATE_PATHSPEC" in call, (
            "a gate no longer derives its pathspec from _GATE_PATHSPEC, so the "
            "completeness guard below constrains nothing")
        assert '"--", "' not in call, f"a literal pathspec is hardcoded at the call site: {call}"
```

Run them (FAIL: `_WIN_GREP` undefined; the pathspec test finds one call), then add beside
`_WIDE_HOME_PATH_RE`:

```python
# The Windows form, for `mcp install`'s Windows path tables: a drive, Users and the account
# name, the separators any run of backslashes or slashes (a JSON path in a Python string has
# four, and a path can mix a backslash with a slash). Nothing is allow-listed: tests join their placeholder from
# parts, so every hit outside this file is a leak.
_WIN_GREP = r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/[:space:]'\"<>]"
```

Run: `.venv/bin/python -m pytest tests/test_no_leaked_files.py -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"` → all passed.
Witness: change both `[\\/]+` in `_WIN_GREP` to `\\` → `test_the_windows_gate_catches_every_separator_form_through_git` FAILS (only the single-backslash and lowercase lines remain). Restore.
Witness: in `toml_str`, delete the `elif ord(ch) < 0x20 ...` arm and its append → the `new\nline` row of `test_the_codex_snippet_escapes_any_path_codex_can_read` FAILS. Restore. Then make `toml_str`'s body `return json.dumps(value)` → the emoji row FAILS. Restore.
Witness: delete `Client.__post_init__` → `test_an_unknown_route_fails_at_construction` FAILS. Restore.

- [ ] **Step 7: Commit**

```bash
git add sluice/mcpinstall/clients.py tests/mcpinstall tests/test_no_leaked_files.py
git commit -F - <<'EOF'
feat(mcp): the client roster mcp install writes to, per platform

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 4: Reading state and the JSON route (`routes.py`, part 1)

**Files:**
- Create: `sluice/mcpinstall/routes.py`, `tests/mcpinstall/fakes.py`, `tests/mcpinstall/test_json_route.py`
- Modify: `tests/test_paths.py` and `tests/test_path_tilde.py` (the new state-folder name, exactly as `config_backups` is handled in each: `grep -n config_backups tests/test_paths.py tests/test_path_tilde.py` and mirror every hit for `mcp_install_backups`; the reason is the same, the folder is new so no older location can hold copies)

**Interfaces:**
- Consumes: Task 3's `Client`, `server_table`, `entry_value`, `parse_entry`, `display_path`; `core.atomicfile.replace_if`; `core.backup.write_copy` (injected).
- Produces:
  - `Absent()`, `Entry(argv: tuple[str, ...], env: tuple[tuple[str, str], ...])`, `Unreadable(reason: str)`, `FileState(path, raw: bytes | None, doc: dict, table: dict, current: Absent | Entry)`, `Outcome(client: str, kind: str, reason: str = "", details: tuple[str, ...] = (), paste: tuple[str, str] | None = None)` (`paste`: the hand-guidance and text a `manual` outcome prints in place of the snippet).
  - `@dataclass Deps(run: Callable[[list[str], float], int], write_copy: Callable[..., str], backup_dir: str, display: Callable[[str], str], timeout: float = 60.0, before_recheck: Callable[[], None] = <no-op>)`.
  - `read_state(client, path) -> FileState | Unreadable`; `matches(entry: Entry, spec) -> bool`; `apply_json(client, state, spec, deps) -> Outcome`; `_write_checked(client, state, data, spec, deps, kind) -> Outcome` (copy, compare-and-set, readback: shared with Task 6); `_canon(value) -> str`, `_load(client, data) -> dict`; `BACKUP_FOLDER = "mcp_install_backups"`; `backup_dir() -> str`; `CHANGED` (the reason string).

- [ ] **Step 1: Write the shared test rig**

```python
# tests/mcpinstall/fakes.py
"""Rigs and fake client CLIs for the install tests. A fake edits its config file the way the
measured client does (the spec's measurement table) and records every call; the runner refuses
to run anything outside the test's own folder."""
import json
import os
import pathlib
import subprocess
from dataclasses import dataclass, field

from sluice.core import backup
from sluice.mcpinstall import clients, jsonc, routes


class FakeClient:
    def __init__(self, name, config_path, *, noop=False, drop=None, drop_top=None,
                 write_to=None, fail=None, fail_verb="add"):
        self.name, self.path = name, config_path
        self.noop, self.drop, self.drop_top, self.write_to = noop, drop, drop_top, write_to
        self.fail, self.fail_verb = fail, fail_verb
        self.calls, self.copy_present = [], []

    def _doc(self, path):
        try:
            return jsonc.load(pathlib.Path(path).read_bytes(), jsonc=True)
        except FileNotFoundError:
            return {}

    def _table(self, doc):
        node = doc
        for key in clients.by_name(self.name).table:
            node = node.setdefault(key, {})
        return node

    def run(self, args):
        self.calls.append(list(args))
        verb = args[1]
        if self.fail is not None and verb == self.fail_verb:
            return self.fail
        if self.noop:
            return 0
        target = self.write_to or self.path
        doc = self._doc(target)
        table = self._table(doc)
        if verb == "remove":
            if "job-sluice" not in table:
                return 1
            del table["job-sluice"]
        else:
            env, argv = self._parse_add(args)
            if self.name == "claude-code" and "job-sluice" in table:
                return 1          # measured: refuses an existing name
            value = (
                {"type": "local", "command": argv} if self.name == "opencode" else
                {"type": "stdio", "command": argv[0], "args": argv[1:]}
                if self.name == "claude-code" else {"command": argv[0], "args": argv[1:]})
            if env:
                value["environment" if self.name == "opencode" else "env"] = env
            table["job-sluice"] = value
        if self.drop:
            table.pop(self.drop, None)
        if self.drop_top:
            doc.pop(self.drop_top, None)
        pathlib.Path(target).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(target).write_text(json.dumps(doc, indent=2))
        return 0

    def _parse_add(self, args):
        rest, env = list(args[2:]), {}
        if self.name == "claude-code":
            assert rest[:3] == ["--scope", "user", "job-sluice"], rest
            rest = rest[3:]
            while rest[0] == "-e":
                k, v = rest[1].split("=", 1)
                env[k], rest = v, rest[2:]
            assert rest[0] == "--", rest
            return env, rest[1:]
        if self.name == "opencode":
            assert rest[0] == "--global", rest
            rest = rest[1:]
            while rest[0] == "--env":
                k, v = rest[1].split("=", 1)
                env[k], rest = v, rest[2:]
            assert rest[:2] == ["job-sluice", "--"], rest
            return env, rest[2:]
        while rest[0] == "-e":                        # gemini
            k, v = rest[1].split("=", 1)
            env[k], rest = v, rest[2:]
        assert rest[:3] == ["-s", "user", "job-sluice"], rest
        return env, rest[3:]


class FakeRunner:
    def __init__(self, root, backup_dir):
        self.root, self.backup_dir, self.fakes, self.hang = str(root), backup_dir, {}, set()

    def __call__(self, argv, timeout):
        if not argv[0].startswith(self.root + os.sep):
            raise AssertionError(
                f"install tried to run {os.path.basename(argv[0])!r} outside the test folder")
        fake = self.fakes[argv[0]]
        fake.copy_present.append(os.path.isdir(self.backup_dir)
                                 and bool(os.listdir(self.backup_dir)))
        if argv[0] in self.hang:
            raise subprocess.TimeoutExpired(argv, timeout)
        return fake.run(argv[1:])


@dataclass
class Rig:
    tmp: pathlib.Path
    platform: str = "darwin"
    env: dict = field(default_factory=dict)

    def __post_init__(self):
        self.home = str(self.tmp / "home")
        self.bin = self.tmp / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.backup_dir = str(self.tmp / "state" / routes.BACKUP_FOLDER)
        self.runner = FakeRunner(self.tmp, self.backup_dir)
        self.host = clients.Host(
            home=self.home, env=self.env, platform=self.platform,
            which=lambda n: str(self.bin / n) if (self.bin / n).exists() else None,
            isdir=os.path.isdir, isfile=os.path.isfile)
        self.copies_written = []
        self.deps = routes.Deps(run=self.runner, write_copy=self._write_copy,
                                backup_dir=self.backup_dir,
                                display=lambda p: clients.display_path(p, self.host),
                                timeout=5.0)
        self.after_copy = None
        launcher = self.tmp / "launcher" / "job-sluice"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
        self.launcher = str(launcher)

    def _write_copy(self, *args, **kwargs):
        name = backup.write_copy(*args, **kwargs)
        self.copies_written.append(name)
        if self.after_copy:
            self.after_copy()
        return name

    def path(self, name) -> str:
        return clients.config_path(clients.by_name(name), self.host)

    def write(self, name, doc_or_text) -> pathlib.Path:
        p = pathlib.Path(self.path(name))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(doc_or_text if isinstance(doc_or_text, str)
                     else json.dumps(doc_or_text, indent=2))
        return p

    def read(self, name) -> dict:
        return jsonc.load(pathlib.Path(self.path(name)).read_bytes(), jsonc=True)

    def cli(self, name, **knobs) -> FakeClient:
        c = clients.by_name(name)
        exe = self.bin / c.executable
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        fake = FakeClient(name, self.path(name), **knobs)
        self.runner.fakes[str(exe)] = fake
        return fake

    def copies(self) -> list:
        return sorted(os.listdir(self.backup_dir)) if os.path.isdir(self.backup_dir) else []


OTHER = {"command": "/opt/other/bin/srv", "args": ["--flag"],
         "env": {"OTHER_TOKEN": "SENTINEL-NOT-A-SECRET-OTHER"}}
```

- [ ] **Step 2: Write the failing JSON-route tests**

```python
# tests/mcpinstall/test_json_route.py
import json
import os
import stat

import pytest

from sluice.mcpinstall import clients, routes, server
from tests.mcpinstall.fakes import OTHER, Rig

SPEC = server.ServerSpec(("/opt/x/job-sluice", "mcp", "serve", "--write"),
                         (("SLUICE_CONFIG", "/cfg/sluice.yaml"),))
CURSOR = clients.by_name("cursor")


def _apply(rig, name="cursor", spec=SPEC):
    c = clients.by_name(name)
    state = routes.read_state(c, rig.path(name))
    assert not isinstance(state, routes.Unreadable), state
    return routes.apply_json(c, state, spec, rig.deps)


def test_a_missing_file_is_created_with_only_the_entry(tmp_path):
    rig = Rig(tmp_path)
    out = _apply(rig)
    assert out.kind == "registered", out
    assert rig.read("cursor") == {"mcpServers": {"job-sluice": clients.entry_value(CURSOR, SPEC)}}
    assert rig.copies() == []


def test_an_empty_file_is_a_create(tmp_path):
    rig = Rig(tmp_path)
    rig.write("cursor", "")
    assert _apply(rig).kind == "registered"


def test_other_keys_and_a_credential_survive_and_the_clean_copy_is_deleted(tmp_path):
    rig = Rig(tmp_path)
    rig.write("cursor", {"mcpServers": {"other": OTHER}, "theme": {"x": 1}})
    out = _apply(rig)
    assert out.kind == "registered", out
    doc = rig.read("cursor")
    assert doc["mcpServers"]["other"] == OTHER and doc["theme"] == {"x": 1}
    assert len(rig.copies_written) == 1 and rig.copies() == []


def test_a_replace_keeps_and_names_a_user_env_key(tmp_path):
    rig = Rig(tmp_path)
    old = {"command": "/old/job-sluice", "args": ["mcp", "serve"],
           "env": {"MY_API_KEY": "SENTINEL-NOT-A-SECRET-3", "SLUICE_CONFIG": "/old.yaml"}}
    rig.write("cursor", {"mcpServers": {"job-sluice": old}})
    out = _apply(rig)
    assert out.kind == "replaced", out
    env = rig.read("cursor")["mcpServers"]["job-sluice"]["env"]
    assert env == {"MY_API_KEY": "SENTINEL-NOT-A-SECRET-3", "SLUICE_CONFIG": "/cfg/sluice.yaml"}
    assert any("MY_API_KEY" in d for d in out.details)
    assert not any("SENTINEL" in d for d in out.details)


def test_a_replace_keeps_and_names_the_entry_s_other_settings(tmp_path):
    rig = Rig(tmp_path)
    old = {"command": "/old/job-sluice", "args": [], "autoApprove": ["list_leads"], "cwd": "/w"}
    rig.write("cursor", {"mcpServers": {"job-sluice": old}})
    out = _apply(rig)
    entry = rig.read("cursor")["mcpServers"]["job-sluice"]
    assert out.kind == "replaced" and entry["autoApprove"] == ["list_leads"] and entry["cwd"] == "/w"
    assert any("autoApprove, cwd" in d for d in out.details)


def test_the_backup_folder_name_is_the_one_the_path_sweeps_read():
    assert os.path.basename(routes.backup_dir()) == routes.BACKUP_FOLDER


@pytest.mark.parametrize("content,reason", [
    ("\ufeff{}", "byte-order mark"),
    ('{"mcpServers": {}} // c', "comments"),
    ('{"a": 1, "a": 2}', "twice"),
    ("[]", "top level"),
    ('{"mcpServers": []}', "server table"),
    ('{"mcpServers": {"job-sluice": "x"}}', "shape"),
    ("{", "not valid JSON"),
])
def test_every_refusal_reads_unreadable_and_writes_nothing(tmp_path, content, reason):
    rig = Rig(tmp_path)
    p = rig.write("cursor", content)
    before = p.read_bytes()
    got = routes.read_state(CURSOR, str(p))
    assert isinstance(got, routes.Unreadable) and reason in got.reason
    assert p.read_bytes() == before and rig.copies() == []


def test_non_utf8_bytes_are_unreadable(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", "")
    p.write_bytes(b'{"a": "\xff"}')
    assert isinstance(routes.read_state(CURSOR, str(p)), routes.Unreadable)


def test_a_folder_is_unreadable(tmp_path):
    rig = Rig(tmp_path)
    os.makedirs(rig.path("cursor"))
    assert "folder" in routes.read_state(CURSOR, rig.path("cursor")).reason


def test_a_change_before_the_copy_leaves_no_copy_and_replaces_nothing(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", {"mcpServers": {}})
    state = routes.read_state(CURSOR, str(p))
    p.write_text('{"mcpServers": {"late": {}}}')
    out = routes.apply_json(CURSOR, state, SPEC, rig.deps)
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert rig.copies_written == [] and "job-sluice" not in rig.read("cursor")["mcpServers"]


def test_a_change_after_the_copy_keeps_and_names_the_copy(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", {"mcpServers": {}})
    state = routes.read_state(CURSOR, str(p))
    rig.after_copy = lambda: p.write_text('{"mcpServers": {"late": {}}}')
    out = routes.apply_json(CURSOR, state, SPEC, rig.deps)
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert rig.copies() == rig.copies_written
    assert any(f"{routes.BACKUP_FOLDER}/{rig.copies_written[0]}" in d for d in out.details)
    assert "job-sluice" not in rig.read("cursor")["mcpServers"]


def test_the_copy_carries_the_file_s_mode(tmp_path):
    rig = Rig(tmp_path)
    p = rig.write("cursor", {"mcpServers": {}})
    p.chmod(0o600)
    state = routes.read_state(CURSOR, str(p))
    rig.after_copy = lambda: p.write_text("{}")       # fail, so the copy is kept to inspect
    routes.apply_json(CURSOR, state, SPEC, rig.deps)
    mode = stat.S_IMODE(os.stat(os.path.join(rig.backup_dir, rig.copies()[0])).st_mode)
    assert mode == 0o600


def test_a_symlinked_file_has_its_target_replaced(tmp_path):
    rig = Rig(tmp_path)
    real = tmp_path / "dotfiles" / "mcp.json"
    real.parent.mkdir()
    real.write_text('{"mcpServers": {}}')
    link = tmp_path / "home" / ".cursor" / "mcp.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert _apply(rig).kind == "registered"
    assert link.is_symlink() and "job-sluice" in json.loads(real.read_text())["mcpServers"]


def test_a_path_with_spaces_and_non_ascii_round_trips(tmp_path):
    rig = Rig(tmp_path)
    spec = server.ServerSpec(("/opt/My Apps/jöb/job-sluice", "mcp", "serve"), ())
    assert _apply(rig, spec=spec).kind == "registered"
    entry = rig.read("cursor")["mcpServers"]["job-sluice"]
    assert entry["command"] == "/opt/My Apps/jöb/job-sluice"


def test_vscode_writes_its_servers_table(tmp_path):
    rig = Rig(tmp_path)
    assert _apply(rig, "vscode").kind == "registered"
    assert rig.read("vscode")["servers"]["job-sluice"]["type"] == "stdio"


def test_matches_compares_argv_and_every_pinned_key_on_both_sides():
    entry = routes.Entry(SPEC.argv, (("SLUICE_CONFIG", "/cfg/sluice.yaml"), ("X_KEY", "v")))
    assert routes.matches(entry, SPEC)
    assert not routes.matches(routes.Entry(SPEC.argv, ()), SPEC)
    bare = server.ServerSpec(SPEC.argv, ())
    assert not routes.matches(entry, bare)     # the old entry pins a path this run does not
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_json_route.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: collection ERROR, `cannot import name 'routes'`.

- [ ] **Step 4: Implement**

```python
# sluice/mcpinstall/routes.py
"""Reading and writing one client's `job-sluice` registration.

Every route READS the client's config file, never a client command (`claude mcp get` starts the
server and prints env values; opencode's list is a stale daemon), so the readback is the same
file the copy and the collateral check read.

Before any write the file's current bytes are copied (`core/backup.py::write_copy`) into
`mcp_install_backups/` in sluice's state folder, carrying the file's mode: no copy, no write.
The copy is deleted once the write is proven clean (readback shows the spec, and no other
server changed), and kept, and named, only when the client ends `failed`. These files hold other
tools' credentials, so a copy does not outlive its purpose.

The JSON and append routes write through `core/atomicfile.py::replace_if`, replacing only while
the file still holds the bytes read. The append route (Codex) adds a new table after the file's
last byte and edits nothing; the bytes it would write are parsed first, and written only when
they read as the old document plus exactly the new entry. The command route cannot compare-and-set (the client's add reads and
writes the file itself), so it re-reads the server table just before running the add and stops
when it changed; the window while the add runs is the residual no outside check closes, the
same posture as `core/vault.py::_cas_write`."""
import copy as _copy
import json
import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from sluice.core.atomicfile import replace_if
from sluice.mcpinstall import jsonc
from sluice.mcpinstall.clients import (Client, check_scope, entry_value, extra_fields,
                                       parse_entry, redact_argv, server_table)
from sluice.mcpinstall.server import PINNED_ENV, SERVER_NAME, ServerSpec

BACKUP_FOLDER = "mcp_install_backups"
CHANGED = "the file changed while install was running"
_TMP_PREFIX = ".job-sluice-mcp-"
_GONE = object()


@dataclass(frozen=True)
class Absent:
    pass


@dataclass(frozen=True)
class Entry:
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Unreadable:
    reason: str


@dataclass(frozen=True)
class FileState:
    path: str
    raw: bytes | None
    doc: dict
    table: dict
    current: Absent | Entry


@dataclass(frozen=True)
class Outcome:
    client: str
    kind: str          # registered, replaced, unchanged, refused, failed, manual
    reason: str = ""
    details: tuple[str, ...] = ()
    # (instruction, text) when a `manual` outcome needs other hand-guidance than "paste the
    # snippet": the snippet itself would break some files (the append route).
    paste: tuple[str, str] | None = None


def _no_op() -> None:
    return None


@dataclass
class Deps:
    run: Callable[[list[str], float], int]
    write_copy: Callable[..., str]
    backup_dir: str
    display: Callable[[str], str]
    timeout: float = 60.0
    # Called between the copy and the freshness re-read on the command route; a test lands a
    # change there. Production passes nothing.
    before_recheck: Callable[[], None] = field(default=_no_op)


def run_quietly(argv: list[str], timeout: float) -> int:
    """The production runner: no shell, stdin closed, output discarded unread (a client's output
    can echo an entry's environment, and those hold other tools' credentials)."""
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=False).returncode


def backup_dir() -> str:
    """`mcp_install_backups` in sluice's XDG state folder, the way `core/config.py::
    config_copy_dir` places `config_backups`. The name is a LITERAL because the path sweeps read
    `name=` statically; `tests/mcpinstall/test_json_route.py` pins it to `BACKUP_FOLDER`."""
    from sluice.core.paths import resolve
    return resolve(env_var=None, config_value="", kind="state", name="mcp_install_backups")


def _scoped(client: Client, doc: dict, table: dict):
    """What freshness and the collateral check compare for this client (`check_scope`)."""
    return table if check_scope(client) == "table" else doc


def _without_entry(client: Client, doc: dict) -> dict:
    rest = _copy.deepcopy(doc)
    node = rest
    for key in client.table:
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(SERVER_NAME, None)
    return rest


def _load(client: Client, data: bytes) -> dict:
    if client.reader == "toml":
        import tomllib
        try:
            return tomllib.loads(data.decode("utf-8"))
        except UnicodeDecodeError:
            raise jsonc.ReadError("it is not UTF-8") from None
        except tomllib.TOMLDecodeError:
            raise jsonc.ReadError("it is not valid TOML") from None
    return jsonc.load(data, jsonc=client.reader == "jsonc")


def _scope_of(client: Client, data: bytes | None):
    """The part of the file freshness compares, from raw bytes; `_GONE` when it does not parse."""
    if data is None:
        return {}
    try:
        doc = _load(client, data)
        return _scoped(client, doc, server_table(client, doc))
    except jsonc.ReadError:
        return _GONE


def read_state(client: Client, path: str) -> "FileState | Unreadable":
    if os.path.isdir(path):
        return Unreadable("it is a folder, not a file")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        raw = None
    except OSError:
        return Unreadable("it cannot be read")
    try:
        doc = {} if raw is None else _load(client, raw)
        table = server_table(client, doc)
        value = table.get(SERVER_NAME)
        if value is None:
            current = Absent()
        else:
            argv, env = parse_entry(client, value)
            current = Entry(argv, tuple(sorted(env.items())))
    except jsonc.ReadError as exc:
        return Unreadable(str(exc))
    return FileState(path, raw, doc, table, current)


def matches(entry: Entry, spec: ServerSpec) -> bool:
    """Same argv, and every pinned key equal on BOTH sides: a key the old entry pins and this
    run does not would still send the server to the old path."""
    mine, theirs = spec.env_dict, dict(entry.env)
    return entry.argv == spec.argv and all(mine.get(k) == theirs.get(k) for k in PINNED_ENV)


def _kept(copy: str | None, path: str, deps: Deps) -> tuple[str, ...]:
    if not copy:
        return ()
    return (f"the copy of {deps.display(path)} taken before this run is kept as "
            f"{BACKUP_FOLDER}/{copy} in sluice's state folder. It holds that tool's "
            "configuration, credentials included: restore the servers named above from it if "
            "you need to (not the whole file, which would undo later changes), then delete it.",)


def _drop_copy(copy: str | None, deps: Deps) -> tuple[str, ...]:
    if not copy:
        return ()
    try:
        os.unlink(os.path.join(deps.backup_dir, copy))
    except OSError:
        return (f"the copy {BACKUP_FOLDER}/{copy} could not be deleted; it holds that tool's "
                "configuration, credentials included, so delete it by hand",)
    return ()


def _take_copy(state: FileState, deps: Deps, same: Callable[[bytes], bool]):
    """Copy the file as it is NOW, after checking it is still the file read. Returns
    (copy name or None, failure reason or None)."""
    try:
        with open(state.path, "rb") as f:
            now = f.read()
    except FileNotFoundError:
        now = None
    except OSError:
        return None, "the file could not be read again before the copy"
    if (now is None) != (state.raw is None) or (now is not None and not same(now)):
        return None, CHANGED
    if now is None:
        return None, None
    real = os.path.realpath(state.path)
    try:
        mode = stat.S_IMODE(os.stat(real).st_mode)
        os.makedirs(deps.backup_dir, mode=0o700, exist_ok=True)
        name = deps.write_copy(deps.backup_dir, os.path.basename(real) + ".", ".bak", now, mode)
    except OSError:
        return None, "a copy of the file could not be written, so nothing was changed"
    return name, None


def _canon(value) -> str:
    """A parsed value as comparable text. `==` is wrong here: a TOML or JSON `nan` never equals
    itself, so a file holding one would read as changed by every write; `default=str` covers
    TOML's dates."""
    return json.dumps(value, sort_keys=True, default=str)


def _same_value(a, b) -> bool:
    return a is not _GONE and b is not _GONE and _canon(a) == _canon(b)


def _verify(client: Client, state: FileState, spec: ServerSpec, copy: str | None, deps: Deps,
            kind: str) -> Outcome:
    after = read_state(client, state.path)
    where = deps.display(state.path)
    if isinstance(after, Unreadable):
        return Outcome(client.name, "failed", f"readback could not read {where}: {after.reason}",
                       _kept(copy, state.path, deps))
    if not (isinstance(after.current, Entry) and matches(after.current, spec)):
        return Outcome(client.name, "failed", f"readback did not show the entry in {where}",
                       _kept(copy, state.path, deps))
    # Something the write REMOVED or CHANGED is a failure; something it ADDED is not (a client
    # may add its own bookkeeping key). Servers are named first; for a file checked whole, the
    # top-level keys that changed are named when no server did.
    changed = sorted(k for k in state.table
                     if k != SERVER_NAME
                     and not _same_value(after.table.get(k, _GONE), state.table[k]))
    if not changed and check_scope(client) == "file":
        before, now = _without_entry(client, state.doc), _without_entry(client, after.doc)
        changed = sorted(k for k in before if not _same_value(now.get(k, _GONE), before[k]))
    if changed:
        return Outcome(client.name, "failed",
                       "other settings in the file changed: " + ", ".join(changed),
                       _kept(copy, state.path, deps))
    return Outcome(client.name, kind, "", _drop_copy(copy, deps))


def _write_checked(client: Client, state: FileState, data: bytes, spec: ServerSpec, deps: Deps,
                   kind: str) -> Outcome:
    """The write every file-writing route shares (JSON and append): copy, replace only while
    the file still holds the bytes read, then readback and the collateral check."""
    copy, failure = _take_copy(state, deps, same=lambda now: now == state.raw)
    if failure:
        return Outcome(client.name, "failed", failure)
    fresh = None if state.raw is None else (lambda current: current == state.raw)
    try:
        wrote = replace_if(state.path, data, fresh=fresh, tmp_prefix=_TMP_PREFIX)
    except OSError:
        return Outcome(client.name, "failed", f"{deps.display(state.path)} could not be written",
                       _kept(copy, state.path, deps))
    if not wrote:
        return Outcome(client.name, "failed", CHANGED, _kept(copy, state.path, deps))
    return _verify(client, state, spec, copy, deps, kind)


def apply_json(client: Client, state: FileState, spec: ServerSpec, deps: Deps) -> Outcome:
    replacing = isinstance(state.current, Entry)
    kind = "replaced" if replacing else "registered"
    kept_env = {k: v for k, v in state.current.env if k not in PINNED_ENV} if replacing else {}
    old = state.table.get(SERVER_NAME, {}) if replacing else {}
    kept_fields = extra_fields(client, old)
    doc = _copy.deepcopy(state.doc)
    node = doc
    for key in client.table:
        node = node.setdefault(key, {})
    # Start from the old entry's other fields (`autoApprove`, `cwd`, ...): the user put them
    # there, and the copy that held them is deleted once this write is proven clean.
    node[SERVER_NAME] = {**{k: old[k] for k in kept_fields},
                         **entry_value(client, spec, kept_env)}
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    out = _write_checked(client, state, data, spec, deps, kind)
    if out.kind == kind:
        notes = ("the file was re-saved as two-space-indented JSON: its formatting may have "
                 "changed, its content has not",)
        if kept_env:
            notes += ("kept the old entry's other environment keys: "
                      + ", ".join(sorted(kept_env)),)
        if kept_fields:
            notes += ("kept the old entry's other settings: " + ", ".join(kept_fields),)
        out = replace(out, details=out.details + notes)
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"`
Expected: all passed.

- [ ] **Step 6: Commit, then witness spec mutants 2, 9 and 10 (JSON arm)**

```bash
git add sluice/mcpinstall/routes.py tests/mcpinstall
git commit -F - <<'EOF'
feat(mcp): read a client's registration and write the JSON route

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Witness 2: in `_write_checked`, change `fresh = None if state.raw is None else (lambda current: current == state.raw)` to `fresh = None if state.raw is None else (lambda current: True)` (the check moved out of the replace) → `test_a_change_after_the_copy_keeps_and_names_the_copy` FAILS. Run with `tests/test_config_write.py` deselected (`--deselect tests/test_config_write.py`), per the spec's note. Restore.
Witness 9: in `_verify`, replace `_drop_copy(copy, deps)` with `()` → `test_other_keys_and_a_credential_survive_and_the_clean_copy_is_deleted` FAILS. Restore.

---

### Task 5: The command route (`routes.py`, part 2)

**Files:**
- Modify: `sluice/mcpinstall/routes.py` (add `apply_command`)
- Create: `tests/mcpinstall/test_command_route.py`

**Interfaces:**
- Consumes: Task 3's `add_argv`, `remove_argv`, `redact_argv`; Task 4's state, `_take_copy`, `_verify`, `_kept`.
- Produces: `apply_command(client, state, spec, deps, which: Callable[[str], str | None]) -> Outcome`; `refusal(client, state) -> Outcome | None` (the command route's replace refusal, shared with `--dry-run`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcpinstall/test_command_route.py
import json
import os
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
    assert out.kind == "refused" and "trust, timeout" in out.reason and fake.calls == []


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_command_route.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: most FAIL with `AttributeError: module 'sluice.mcpinstall.routes' has no attribute 'apply_command'`; `test_the_production_runner_times_out_and_prints_nothing`, `test_an_unparseable_opencode_file_is_unreadable_and_nothing_runs` and `test_the_runner_refuses_anything_outside_the_test_folder` PASS (they exercise Task 4 code).

- [ ] **Step 3: Implement** — append to `sluice/mcpinstall/routes.py`, and add `add_argv, remove_argv` to its `clients` import:

```python
def _run(deps: Deps, argv: list[str], client: Client, verb: str) -> str:
    """Run one client command; "" on success, else a classified reason (never its output)."""
    try:
        code = deps.run(argv, deps.timeout)
    except subprocess.TimeoutExpired:
        return f"`{client.executable} mcp {verb}` timed out"
    except OSError:
        return f"`{client.executable}` could not be started"
    return "" if code == 0 else f"`{client.executable} mcp {verb}` exited {code}"


def refusal(client: Client, state: FileState) -> "Outcome | None":
    """A command route's add writes a fresh entry, so a replace over anything it would drop is
    refused, naming it: env keys outside `PINNED_ENV` (passing a user's key back through argv
    would also put it in the process list) and other fields such as `autoApprove` or `trust`.
    `--dry-run` asks the same question, so a preview never promises a write the run refuses."""
    if not isinstance(state.current, Entry):
        return None
    foreign = sorted(k for k, _ in state.current.env if k not in PINNED_ENV)
    fields = extra_fields(client, state.table.get(SERVER_NAME, {}))
    if not (foreign or fields):
        return None
    named = (["environment keys: " + ", ".join(foreign)] if foreign else []) + (
        ["settings: " + ", ".join(fields)] if fields else [])
    return Outcome(client.name, "refused",
                   "the existing entry carries what install's add would drop ("
                   + "; ".join(named) + ")",
                   ("re-add them by hand after registering, or move them out of the entry and "
                    "run install again",))


def apply_command(client: Client, state: FileState, spec: ServerSpec, deps: Deps,
                  which: Callable[[str], str | None]) -> Outcome:
    replacing = isinstance(state.current, Entry)
    kind = "replaced" if replacing else "registered"
    refused = refusal(client, state)
    if refused:
        return refused
    exe = which(client.executable)
    if not exe:
        return Outcome(client.name, "failed", f"`{client.executable}` not found on PATH")
    before = _scoped(client, state.doc, state.table)
    copy, failure = _take_copy(state, deps, same=lambda now: _scope_of(client, now) == before)
    if failure:
        return Outcome(client.name, "failed", failure)
    deps.before_recheck()
    # The add reads and writes the file itself, so this is the last moment to notice a change.
    # Parsed content is compared, not bytes, and for Claude Code only the server table: a
    # running session rewrites the rest of ~/.claude.json constantly (`check_scope`).
    now = read_state(client, state.path)
    if (isinstance(now, Unreadable) or (now.raw is None) != (state.raw is None)
            or _scoped(client, now.doc, now.table) != before):
        return Outcome(client.name, "failed", CHANGED, _kept(copy, state.path, deps))
    steps = [("add", add_argv(client, spec))]
    if replacing and client.remove_before_add:
        steps.insert(0, ("remove", remove_argv(client)))
    for verb, argv in steps:
        reason = _run(deps, [exe, *argv[1:]], client, verb)
        if reason:
            details = _kept(copy, state.path, deps)
            if verb == "add" and len(steps) == 2:
                old_env = [k for k, _ in state.current.env]
                details = (
                    "the entry it replaced, now removed, ran: "
                    + " ".join(redact_argv(state.current.argv, deps.display)),
                    "with environment keys: " + (", ".join(old_env) or "none"),
                ) + details
            return Outcome(client.name, "failed", reason, details)
    return _verify(client, state, spec, copy, deps, kind)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"`
Expected: all passed.

- [ ] **Step 5: Commit, then witness spec mutants 1, 3, 4, 5, 7 and 10**

```bash
git add sluice/mcpinstall/routes.py tests/mcpinstall/test_command_route.py
git commit -F - <<'EOF'
feat(mcp): register through a client's own add command, proven by readback

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Each mutant is applied alone, its named test must FAIL, then `git checkout sluice/mcpinstall/routes.py`:
- 1 (exit code trusted): in `_verify`, delete the `if not (isinstance(after.current, Entry) and matches(after.current, spec)):` block → `test_a_client_that_exits_0_and_writes_nothing_has_failed`.
- 3 (no copy before run): in `apply_command`, replace `copy, failure = _take_copy(...)` with `copy, failure = None, None` → `test_a_clean_register_keeps_other_servers_and_deletes_its_copy` (`copy_present == [False]`).
- 4 (no collateral): in `_verify`, delete both `changed = ...` computations' bodies, leaving `changed = []` → `test_a_client_that_drops_another_server_fails_naming_it_and_keeps_the_copy`.
- 11 (file-scoped clients checked by table only): in `clients.check_scope`, return `"table"` for every client → `test_an_add_that_drops_an_unrelated_setting_fails_and_keeps_the_copy` and `test_a_settings_edit_between_the_copy_and_the_run_stops_a_whole_file_client`.
- 5 (Unreadable falls through to Absent): in `read_state`, change `return Unreadable(str(exc))` to `return FileState(path, raw, {}, {}, Absent())` → `test_an_unparseable_opencode_file_is_unreadable_and_nothing_runs` and Task 4's refusal rows.
- 7 (the readback no longer reads the computed file): in `_verify`, replace `after = read_state(client, state.path)` with `after = state` → `test_a_clean_register_keeps_other_servers_and_deletes_its_copy` (a stale readback can only FAIL, so only a test expecting success sees it; `test_a_client_that_writes_another_file_fails_on_readback` stays green under this mutant and is a behaviour pin, per the spec).
- 10 (deletion before the collateral check): move `_drop_copy(copy, deps)` to run before `changed = ...` (call it, then return `Outcome(...)` without it) → `test_a_client_that_drops_another_server_fails_naming_it_and_keeps_the_copy` (its copy is gone).

---

### Task 6: The append route (Codex)

**Files:**
- Modify: `sluice/mcpinstall/routes.py` (add `appended`, `cannot_append`, `edit_by_hand`, `apply_append`; import `DOCS_URL` and `snippet` from `clients`)
- Create: `tests/mcpinstall/test_append_route.py`

**Interfaces:**
- Consumes: Task 3's `snippet` (and `inline=True`), `entry_value`, `extra_fields`, `DOCS_URL`; Task 4's `_load`, `_canon`, `_write_checked`, `Outcome.paste`.
- Produces:
  - `appended(client, state, spec, text=None) -> bytes | None` — pure: the bytes an append would write, or `None` when they would not read as the old document plus exactly the new entry. `text` replaces the snippet (a test hands another).
  - `cannot_append(client, state, spec, deps) -> Outcome` — the `manual` outcome for a file `appended` refuses, with the inline entry as its paste text.
  - `edit_by_hand(client, state, spec, deps) -> Outcome` — the `manual` outcome for an existing entry that differs, naming (never showing) its other env keys and fields.
  - `apply_append(client, state, spec, deps) -> Outcome` — called only when `state.current` is `Absent`; outcome `registered`, `failed` or `manual`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcpinstall/test_append_route.py
"""Codex: install adds a NEW `[mcp_servers.job-sluice]` table at the end of config.toml and edits
nothing (owner's ruling, 2026-10-10). `codex mcp add` re-serialises the whole server table,
dropping comments and other servers' unknown fields, so it is not used."""
import pathlib
import tomllib

from sluice.mcpinstall import clients, routes, server
from tests.mcpinstall.fakes import Rig

SPEC = server.ServerSpec(("/opt/x/job-sluice", "mcp", "serve", "--write"),
                         (("SLUICE_CONFIG", "/cfg/sluice.yaml"),))
CODEX = clients.by_name("codex")
SENTINEL = "SENTINEL-NOT-A-SECRET-APPEND"

EXISTING = (
    '# my settings\n'
    'model = "example-model"   # a trailing comment\n'
    '\n'
    '[mcp_servers.other]\n'
    'command = "/opt/other/bin/srv"\n'
    'custom_field = "kept"   # a field codex mcp add would drop\n'
)


def _toml(rig, text=None) -> pathlib.Path:
    p = pathlib.Path(rig.path("codex"))
    p.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        p.write_bytes(text.encode() if isinstance(text, str) else text)
    return p


def _state(rig):
    state = routes.read_state(CODEX, rig.path("codex"))
    assert isinstance(state, routes.FileState), state
    return state


def _apply(rig, spec=SPEC):
    state = _state(rig)
    assert isinstance(state.current, routes.Absent)
    return routes.apply_append(CODEX, state, spec, rig.deps)


def _said(out) -> str:
    return " ".join((out.reason, *out.details, *(out.paste or ())))


def test_an_absent_entry_is_appended_and_every_existing_byte_kept(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, EXISTING)
    out = _apply(rig)
    assert out.kind == "registered", out
    after = p.read_bytes()
    assert after.startswith(EXISTING.encode())
    doc = tomllib.loads(after.decode())
    assert doc["mcp_servers"]["other"]["custom_field"] == "kept"
    assert doc["mcp_servers"]["job-sluice"] == clients.entry_value(CODEX, SPEC)
    assert len(rig.copies_written) == 1 and rig.copies() == []


def test_a_file_without_a_final_newline_still_appends_cleanly(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, 'model = "example-model"')
    assert _apply(rig).kind == "registered"
    assert p.read_bytes().startswith(b'model = "example-model"\n')
    assert tomllib.loads(p.read_text())["model"] == "example-model"


def test_a_crlf_file_keeps_every_byte(tmp_path):
    rig = Rig(tmp_path)
    old = EXISTING.replace("\n", "\r\n").encode()
    p = _toml(rig, old)
    assert _apply(rig).kind == "registered"
    assert p.read_bytes().startswith(old)
    assert tomllib.loads(p.read_bytes().decode())["mcp_servers"]["other"]["custom_field"] == "kept"


def test_an_empty_file_gets_just_the_entry(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, b"")
    assert _apply(rig).kind == "registered"
    assert p.read_text() == clients.snippet(CODEX, SPEC) + "\n"


def test_a_missing_file_is_created(tmp_path):
    rig = Rig(tmp_path)
    assert _apply(rig).kind == "registered"
    doc = tomllib.loads(pathlib.Path(rig.path("codex")).read_text())
    assert doc == {"mcp_servers": {"job-sluice": clients.entry_value(CODEX, SPEC)}}


def test_a_non_ascii_launcher_is_appended_as_valid_toml(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, EXISTING)
    path = "/opt/My Apps/jöb \U0001F600/job-sluice"
    spec = server.ServerSpec((path, "mcp", "serve"), (("SLUICE_CONFIG", path),))
    assert _apply(rig, spec).kind == "registered"
    assert tomllib.loads(p.read_text())["mcp_servers"]["job-sluice"]["command"] == path


def test_a_nan_elsewhere_does_not_block_the_append(tmp_path):
    rig = Rig(tmp_path)
    _toml(rig, EXISTING.replace('model = "example-model"', "ratio = nan"))
    assert _apply(rig).kind == "registered"


def test_an_inline_server_table_is_manual_and_nothing_is_written(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, 'mcp_servers = { other = { command = "x", env = { T = "%s" } } }\n'
              % SENTINEL)
    before = p.read_bytes()
    out = _apply(rig)
    assert out.kind == "manual" and p.read_bytes() == before and rig.copies_written == []
    # The guidance is the inline form, which DOES read inside that table; the table form the
    # other outcomes print would make this file unreadable.
    instruction, text = out.paste
    assert "[mcp_servers" not in text
    doc = tomllib.loads('mcp_servers = { other = { command = "x" }, %s }\n' % text)
    assert doc["mcp_servers"]["job-sluice"] == clients.entry_value(CODEX, SPEC)
    assert SENTINEL not in _said(out)


def test_an_appended_text_that_changes_anything_else_is_refused(tmp_path):
    rig = Rig(tmp_path)
    _toml(rig, EXISTING)
    state = _state(rig)
    assert routes.appended(CODEX, state, SPEC) is not None          # control
    extra = clients.snippet(CODEX, SPEC) + '\n\n[sneaky]\nk = 1'
    assert routes.appended(CODEX, state, SPEC, text=extra) is None   # parses, but adds [sneaky]


def test_a_change_after_the_copy_keeps_the_copy_and_appends_nothing(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, EXISTING)
    state = _state(rig)
    rig.after_copy = lambda: p.write_text(EXISTING + 'late = 1\n')
    out = routes.apply_append(CODEX, state, SPEC, rig.deps)
    assert (out.kind, out.reason) == ("failed", routes.CHANGED)
    assert "job-sluice" not in p.read_text() and rig.copies() == rig.copies_written


def test_a_failed_readback_keeps_and_names_the_copy(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    _toml(rig, EXISTING)
    state = _state(rig)
    monkeypatch.setattr(routes, "read_state", lambda c, path: routes.Unreadable("gone"))
    out = routes.apply_append(CODEX, state, SPEC, rig.deps)
    assert out.kind == "failed" and "readback could not read" in out.reason
    assert rig.copies() == rig.copies_written and len(rig.copies()) == 1
    assert any(rig.copies_written[0] in d for d in out.details)


def test_an_existing_entry_is_edited_by_hand_with_its_other_keys_named(tmp_path):
    rig = Rig(tmp_path)
    p = _toml(rig, '[mcp_servers.job-sluice]\ncommand = "/old/job-sluice"\n'
              'args = ["--token", "%s"]\nstartup_timeout_sec = 30\n\n'
              '[mcp_servers.job-sluice.env]\nMY_KEY = "%s"\n' % (SENTINEL, SENTINEL))
    before = p.read_bytes()
    state = _state(rig)
    out = routes.edit_by_hand(CODEX, state, SPEC, rig.deps)
    said = _said(out)
    assert out.kind == "manual" and "does not edit an existing one" in out.reason
    assert "MY_KEY" in said and "startup_timeout_sec" in said and "keep" in said
    assert SENTINEL not in said and p.read_bytes() == before
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_append_route.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: every test FAILS with `AttributeError: module 'sluice.mcpinstall.routes' has no attribute` (`apply_append`, `appended` or `edit_by_hand`).

- [ ] **Step 3: Implement** — add `DOCS_URL` and `snippet` to `routes.py`'s `clients` import, then append:

```python
def appended(client: Client, state: FileState, spec: ServerSpec,
             text: str | None = None) -> bytes | None:
    """The bytes an append would write: the file as it is, then a new entry table. `None` when
    they would not read as the old document plus exactly the new entry: an inline
    `mcp_servers = {...}` table, for one, makes an appended table invalid TOML, and writing it
    would leave the user's file unreadable to Codex. Pure, so `--dry-run` asks the same question
    the write does."""
    old = state.raw or b""
    gap = b"" if not old else (b"\n" if old.endswith(b"\n") else b"\n\n")
    table = snippet(client, spec) if text is None else text
    data = old + gap + (table + "\n").encode("utf-8")
    expected = _copy.deepcopy(state.doc)
    node = expected
    for key in client.table:
        node = node.setdefault(key, {})
    node[SERVER_NAME] = entry_value(client, spec)
    try:
        after = _load(client, data)
    except jsonc.ReadError:
        return None
    return data if _canon(after) == _canon(expected) else None


def cannot_append(client: Client, state: FileState, spec: ServerSpec, deps: Deps) -> Outcome:
    where = deps.display(state.path)
    return Outcome(
        client.name, "manual",
        f"a table appended to {where} would not read as just the job-sluice entry "
        "(`mcp_servers` written as an inline table, for one), so nothing was written",
        paste=(f"add this inside the `mcp_servers` table in {where} by hand "
               f"(see {DOCS_URL}#{client.anchor})", snippet(client, spec, inline=True)))


def edit_by_hand(client: Client, state: FileState, spec: ServerSpec, deps: Deps) -> Outcome:
    """An existing entry that differs: install appends, it never edits TOML in place. The other
    env keys and settings are NAMED, so a user pasting the values does not drop them; their
    values are never printed (a key can hold a token)."""
    where = deps.display(state.path)
    other_env = sorted(k for k, _ in state.current.env if k not in PINNED_ENV)
    fields = extra_fields(client, state.table.get(SERVER_NAME, {}))
    details = ()
    if other_env:
        details += ("keep its other environment keys: " + ", ".join(other_env),)
    if fields:
        details += ("keep its other settings: " + ", ".join(fields),)
    return Outcome(
        client.name, "manual",
        f"install adds a {client.title} entry but does not edit an existing one", details,
        paste=(f"in {where}'s job-sluice entry, set these values by hand and keep the rest "
               f"(see {DOCS_URL}#{client.anchor})", snippet(client, spec)))


def apply_append(client: Client, state: FileState, spec: ServerSpec, deps: Deps) -> Outcome:
    """Add a NEW entry table at the end of a TOML file, editing no existing byte (Codex, by the
    owner's ruling: its own add re-serialises the server table and drops what it does not know,
    and the standard library cannot edit TOML). Only for an absent entry; then the copy,
    compare-and-set and readback every other write has."""
    data = appended(client, state, spec)
    if data is None:
        return cannot_append(client, state, spec, deps)
    return _write_checked(client, state, data, spec, deps, "registered")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"`
Expected: all passed.

- [ ] **Step 5: Commit, then witness spec mutants 12 and 13**

```bash
git add sluice/mcpinstall/routes.py tests/mcpinstall/test_append_route.py
git commit -F - <<'EOF'
feat(mcp): register in Codex by appending a new entry table

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Witness 12: in `appended`, delete the `try: after = ... except ...: return None` block and make the last line `return data` → `test_an_inline_server_table_is_manual_and_nothing_is_written` FAILS (the file is written and left invalid; the outcome is `failed`, not `manual`). Restore.
Witness 13: in `appended`, change the last line to `return data` (the parse stays, the comparison goes) → `test_an_appended_text_that_changes_anything_else_is_refused` FAILS while the inline test stays green. Restore.
Witness 14: in `_canon`, return `value` instead of the `json.dumps(...)` text → `test_a_nan_elsewhere_does_not_block_the_append` FAILS. Restore.
Witness 15: in `edit_by_hand`, delete the `if other_env:` line and the line under it → `test_an_existing_entry_is_edited_by_hand_with_its_other_keys_named` FAILS. Restore.

---

### Task 7: The flow and the command line (`flow.py`, `cli.py`)

**Files:**
- Create: `sluice/mcpinstall/flow.py`, `tests/mcpinstall/test_flow.py`
- Modify: `sluice/cli.py` (`cmd_mcp_install` beside `cmd_mcp_serve`; the `install` parser beside `serve`)

**Interfaces:**
- Consumes: everything above; `sluice.mcpextra.NOT_INSTALLED`.
- Produces: `@dataclass(frozen=True) Options(clients: tuple[str, ...], read_only: bool, replace: bool, yes: bool, dry_run: bool)`; `run(opts, *, host, deps, argv0, stdin, out, err, interactive, find_spec) -> int`; `cli.cmd_mcp_install(args, config) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/mcpinstall/test_flow.py
import io
import json
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcpinstall/test_flow.py -rA 2>&1 | grep -E "^(PASSED|FAILED|ERROR)|passed|failed|error"`
Expected: collection ERROR, `cannot import name 'flow'`.

- [ ] **Step 3: Implement `flow.py`**

```python
# sluice/mcpinstall/flow.py
"""`job-sluice mcp install`, end to end: check the extra and the launcher, detect, ask (or apply
the flags), read, write, read back, report. Every OS touchpoint arrives injected from
`cli.py::cmd_mcp_install`, so tests drive the whole command against fakes.

Non-interactive is `--yes`, a stdin that is not a terminal, or `--dry-run`; `--client` only
narrows the list. What is printed follows the spec's neutrality rule: the argv install
registers in full, an existing entry's argv redacted, pinned values, any other env key by NAME
only, never a client's output."""
import json
from dataclasses import dataclass, replace

from sluice import mcpextra
from sluice.mcpinstall import clients, routes, server
from sluice.mcpinstall.server import PINNED_ENV


@dataclass(frozen=True)
class Options:
    clients: tuple[str, ...]
    read_only: bool
    replace: bool
    yes: bool
    dry_run: bool


class _Ask:
    """Prompts on the terminal. An empty line or end of input takes the default, so a closed
    stdin never loops."""

    def __init__(self, stdin, out):
        self.stdin, self.out = stdin, out

    def _line(self) -> str:
        return (self.stdin.readline() or "").strip()

    def yes_no(self, prompt: str, default: bool) -> bool:
        print(f"{prompt} [{'Y/n' if default else 'y/N'}]", file=self.out)
        answer = self._line().lower()
        return default if not answer else answer in ("y", "yes")

    def pick(self, rows) -> set:
        names = [name for name, _, _ in rows]
        print("Register job-sluice in:", file=self.out)
        for name, label, ticked in rows:
            print(f"  [{'x' if ticked else ' '}] {name} ({label})", file=self.out)
        print("Press Enter for the ticked ones, or type names separated by commas:",
              file=self.out)
        while True:
            answer = self._line()
            if not answer:
                return {name for name, _, ticked in rows if ticked}
            picked = {p.strip() for p in answer.split(",") if p.strip()}
            unknown = sorted(picked - set(names))
            if not unknown:
                return picked
            print(f"  not in the list: {', '.join(unknown)}", file=self.out)


def _env_lines(env: dict) -> list:
    pinned = [f"{k}={v}" for k, v in sorted(env.items()) if k in PINNED_ENV]
    other = sorted(k for k in env if k not in PINNED_ENV)
    lines = []
    if pinned:
        lines.append("env: " + " ".join(pinned))
    if other:
        lines.append("other env keys: " + ", ".join(other))
    return lines


def _why_missing(detected) -> str:
    if isinstance(detected, clients.Unsupported):
        return detected.reason
    return "not found on this machine"


def run(opts: Options, *, host, deps, argv0, stdin, out, err, interactive, find_spec) -> int:
    if find_spec("mcp") is None:
        print(f"job-sluice: {mcpextra.NOT_INSTALLED}", file=err)
        return 2
    try:
        launcher = server.resolve_launcher(argv0, windows=host.platform == "win32")
    except server.LauncherError as exc:
        print(f"job-sluice: {exc}", file=err)
        return 2
    ask = _Ask(stdin, out) if interactive and not opts.yes and not opts.dry_run else None

    considered = [c for c in clients.ROSTER if not opts.clients or c.name in opts.clients]
    found, outcomes = [], []
    for c in considered:
        detected = clients.detect(c, host)
        if isinstance(detected, clients.Found):
            found.append(c)
        elif c.name in opts.clients:
            outcomes.append(routes.Outcome(c.name, "failed", _why_missing(detected)))
    if not found:
        _report(outcomes, {}, None, host, out)
        print("No MCP client was found on this machine. To register one by hand: "
              f"{clients.DOCS_URL}#install-in-your-client", file=out)
        return _exit(outcomes, opts)

    write = not opts.read_only
    if ask and not opts.read_only:
        write = ask.yes_no("Register it with the write tools (the coach's saves, evidence "
                           "proposals and review, leads, CVs)?", True)
    spec = server.build_spec(launcher, host.env, write)
    print("Registering: " + " ".join(spec.argv), file=out)
    for line in _env_lines(spec.env_dict):
        print("  " + line, file=out)

    rows = [(c, clients.config_path(c, host)) for c in found]
    states = {c.name: routes.read_state(c, path) for c, path in rows}
    def _writable(c):
        st = states[c.name]
        # Codex's existing entry is never edited (install only appends), so it is not offered.
        return (isinstance(st, routes.FileState) and not _same(st, spec)
                and not (c.route == "append" and isinstance(st.current, routes.Entry)))
    writable = [c for c, _ in rows if _writable(c)]
    selected = {c.name for c in writable}
    if ask and writable:
        selected = ask.pick([(c.name, "different entry" if isinstance(
            states[c.name].current, routes.Entry) else "not registered", True)
            for c in writable])

    paths = dict((c.name, p) for c, p in rows)
    for c, path in rows:
        state = states[c.name]
        where = deps.display(path)
        if isinstance(state, routes.Unreadable):
            outcomes.append(routes.Outcome(c.name, "failed",
                                           f"{where} could not be read: {state.reason}"))
        elif _same(state, spec):
            outcomes.append(routes.Outcome(c.name, "unchanged"))
        elif c.route == "append" and isinstance(state.current, routes.Entry):
            outcomes.append(routes.edit_by_hand(c, state, spec, deps))
        elif c.name in selected:
            outcomes.append(_one(c, state, spec, opts, ask, deps, host, out))
        if isinstance(state, routes.FileState) and outcomes and outcomes[-1].client == c.name:
            shadows = clients.local_entries(c, state.doc)
            if shadows:
                last = outcomes.pop()
                outcomes.append(replace(last, details=last.details + (
                    f"{shadows} project(s) also have a local-scope job-sluice entry, which takes "
                    "precedence in that project: remove it there with `claude mcp remove "
                    "job-sluice -s local`",)))
    _report(outcomes, paths, spec, host, out)
    return _exit(outcomes, opts)


def _same(state, spec) -> bool:
    return isinstance(state, routes.FileState) and isinstance(
        state.current, routes.Entry) and routes.matches(state.current, spec)


def _one(c, state, spec, opts, ask, deps, host, out) -> routes.Outcome:
    if isinstance(state.current, routes.Entry):
        print(f"{c.name} already has a job-sluice entry with different settings:", file=out)
        print("  now: " + " ".join(clients.redact_argv(state.current.argv, deps.display)),
              file=out)
        for line in _env_lines(dict(state.current.env)):
            print("       " + line, file=out)
        print("  new: " + " ".join(spec.argv), file=out)
        for line in _env_lines(spec.env_dict):
            print("       " + line, file=out)
        if not (opts.replace or (ask and ask.yes_no(f"Replace {c.name}'s entry?", False))):
            return routes.Outcome(c.name, "refused",
                                  "an entry with different settings exists; run with --replace "
                                  "to replace it")
    if c.route == "command":
        refused = routes.refusal(c, state)
        if refused:
            return refused
    # Keyed by route, never an if/else chain ending in a default: a route with no writer is a
    # KeyError here (and `clients.Client` refuses an unknown one at import), not some other
    # route's writer run on this client's file.
    if opts.dry_run:
        return _PREVIEW[c.route](c, state, spec, deps, out)
    return _APPLY[c.route](c, state, spec, deps, host)


def json_one_line(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _preview_command(c, state, spec, deps, out) -> routes.Outcome:
    print(f"{c.name}: would run: " + " ".join(clients.add_argv(c, spec)), file=out)
    return routes.Outcome(c.name, "dry-run")


def _preview_json(c, state, spec, deps, out) -> routes.Outcome:
    print(f"{c.name}: would write {deps.display(state.path)}: "
          + json_one_line(clients.entry_value(c, spec)), file=out)
    return routes.Outcome(c.name, "dry-run")


def _preview_append(c, state, spec, deps, out) -> routes.Outcome:
    # The same pre-parse the write makes, so the preview never promises an append the run
    # would refuse.
    if routes.appended(c, state, spec) is None:
        return routes.cannot_append(c, state, spec, deps)
    print(f"{c.name}: would append to {deps.display(state.path)}:", file=out)
    for line in clients.snippet(c, spec).splitlines():
        print("    " + line, file=out)
    return routes.Outcome(c.name, "dry-run")


_PREVIEW = {"command": _preview_command, "json": _preview_json, "append": _preview_append}
_APPLY = {
    "command": lambda c, state, spec, deps, host: routes.apply_command(c, state, spec, deps,
                                                                       host.which),
    "json": lambda c, state, spec, deps, host: routes.apply_json(c, state, spec, deps),
    "append": lambda c, state, spec, deps, host: routes.apply_append(c, state, spec, deps),
}


def _report(outcomes, paths, spec, host, out) -> None:
    for o in outcomes:
        if o.kind == "dry-run":
            continue
        c = clients.by_name(o.client)
        note = clients.measured_note(c, host)
        head = f"{o.client}: {o.kind}" + (f" - {o.reason}" if o.reason else "")
        print(head + (f" [{note}]" if note else ""), file=out)
        for line in o.details:
            print("  " + line, file=out)
        if o.kind in ("registered", "replaced", "unchanged"):
            print("  next: " + c.next_step, file=out)
        elif o.kind in ("failed", "manual") and spec is not None and o.client in paths:
            if o.paste:
                instruction, text = o.paste
            else:
                where = clients.display_path(paths[o.client], host)
                instruction = f"paste this into {where} instead (see {clients.DOCS_URL}#{c.anchor})"
                text = clients.snippet(c, spec)
            print(f"  {instruction}:", file=out)
            for line in text.splitlines():
                print("    " + line, file=out)


def _exit(outcomes, opts) -> int:
    bad = any(o.kind in ("failed", "refused")
              or (o.kind == "manual" and o.client in opts.clients) for o in outcomes)
    return 1 if bad else 0
```

Note for the executor: `_one` returns an `Outcome` of kind `"dry-run"`, which `_report` and
`_exit` skip; it never reaches the user as an outcome name.

- [ ] **Step 4: Wire the CLI** — in `sluice/cli.py`, after `cmd_mcp_serve`:

```python
def cmd_mcp_install(args, config) -> int:
    """`job-sluice mcp install`: everything lives in `sluice/mcpinstall/`; this builds the real
    host and dependencies it is handed, so its tests can hand it fakes instead."""
    import importlib.util

    from sluice.core import backup
    from sluice.mcpinstall import clients, flow, routes

    host = clients.host_from_os()
    deps = routes.Deps(
        run=routes.run_quietly, write_copy=backup.write_copy,
        backup_dir=routes.backup_dir(),
        display=lambda path: clients.display_path(path, host))
    opts = flow.Options(tuple(args.client or ()), args.read_only, args.replace, args.yes,
                        args.dry_run)
    return flow.run(opts, host=host, deps=deps, argv0=sys.argv[0], stdin=sys.stdin,
                    out=sys.stdout, err=sys.stderr, interactive=sys.stdin.isatty(),
                    find_spec=importlib.util.find_spec)
```

And in `_build_parser`, after `mcp_serve.set_defaults(func=cmd_mcp_serve)`:

```python
    # The roster module imports nothing beyond the mcpinstall package's own stdlib modules
    # (tests/mcpinstall/test_flow.py::test_the_parser_import_loads_only_the_roster pins it),
    # so the choices can come from it on every invocation.
    from sluice.mcpinstall.clients import NAMES as _MCP_CLIENTS
    mcp_install = mcp_group.add_parser(
        "install", help="register the MCP server in the AI clients installed on this machine")
    mcp_install.add_argument(
        "--client", action="append", choices=_MCP_CLIENTS, metavar="NAME",
        help=f"only this client (repeatable): {', '.join(_MCP_CLIENTS)}")
    mcp_install.add_argument("--read-only", action="store_true",
                             help="register without the write tools")
    mcp_install.add_argument("--replace", action="store_true",
                             help="replace an existing job-sluice entry with different settings")
    mcp_install.add_argument("--yes", action="store_true",
                             help="ask nothing: write tools on unless --read-only, and refuse "
                                  "to replace an entry unless --replace")
    mcp_install.add_argument("--dry-run", action="store_true",
                             help="print what would be run or written, and change nothing")
    mcp_install.set_defaults(func=cmd_mcp_install)
```

Add this test to `tests/mcpinstall/test_flow.py` (it must FAIL if `clients.py` grows an import of
`routes`, `flow` or anything outside the package):

```python
def test_the_parser_import_loads_only_the_roster(tmp_path):
    import subprocess, sys
    code = ("import sys, sluice.mcpinstall.clients; "
            "print(sorted(m for m in sys.modules if m.startswith('sluice')))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=tmp_path, check=True, timeout=60).stdout.strip()
    assert got == str(['sluice', 'sluice.mcpinstall', 'sluice.mcpinstall.clients',
                       'sluice.mcpinstall.jsonc', 'sluice.mcpinstall.server'])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcpinstall -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"`
Expected: all passed. Then the whole suite (CLI guards sweep the parser):
`.venv/bin/python -m pytest > "$SCRATCH/full.txt" 2>&1; echo rc=$?; grep -E "[0-9]+ passed" "$SCRATCH/full.txt" | tail -1`.
Expected failures at this point ONLY in the doc guards (`tests/test_docs_claims.py`: USAGE and
README must document `mcp install`) — Task 8 fixes those. Any other failure is a defect here
(the path sweeps were satisfied in Task 4, which added `backup_dir`).

- [ ] **Step 6: Commit, then witness spec mutant 6**

```bash
git add sluice/mcpinstall/flow.py sluice/cli.py tests/mcpinstall/test_flow.py
git commit -F - <<'EOF'
feat(mcp): job-sluice mcp install

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Witness 6: in `flow._env_lines`, change `other = sorted(k for k in env if k not in PINNED_ENV)` to `other = sorted(f"{k}={env[k]}" for k in env if k not in PINNED_ENV)` → `test_no_planted_value_reaches_any_output` FAILS. Restore.
Witness 16: in `run`, delete the `elif c.route == "append" and isinstance(state.current, routes.Entry):` arm and the line under it → `test_a_different_codex_entry_is_manual_and_fails_only_when_named` FAILS (no Codex outcome at all). Restore.
Witness 17: in `_preview_append`, delete the `if routes.appended(...) is None:` check and its return → `test_codex_dry_run_previews_an_inline_table_as_the_run_would` FAILS. Restore.
Witness 18: in `_report`, delete the `if o.paste:` arm (keep the `else` body, unindented) → `test_codex_dry_run_previews_an_inline_table_as_the_run_would` FAILS (the table form is printed). Restore.

---

### Task 8: Docs and their guards

**Files:**
- Modify: `docs/MCP.md`, `docs/AI-SETUP.md`, `docs/USAGE.md`, `README.md`, `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`
- Modify: `tests/test_mcp_install_docs.py`, `tests/test_ai_setup_contract.py`

**Interfaces:**
- Consumes: `clients.ROSTER` (`title`, `anchor`, `next_step`), `clients.DOCS_URL`.

- [ ] **Step 1: Write the failing doc guards** — append to `tests/test_mcp_install_docs.py`:

```python
from sluice.mcpinstall import clients as _install_clients

# Docker has no adapter: a container is not detectable from the host.
_NOT_INSTALLABLE = {"Docker (Claude Code)"}


def test_the_install_roster_is_mcp_md_s_client_headings():
    headings = set(client_entries(_doc())) - _NOT_INSTALLABLE
    assert {c.title for c in _install_clients.ROSTER} == headings


@pytest.mark.parametrize("client", _install_clients.ROSTER, ids=lambda c: c.name)
def test_each_install_anchor_resolves_and_its_slash_command_is_the_doc_s(client):
    from tests.test_doc_links_from_code import _anchors   # the repo's heading-anchor rule
    assert client.anchor in _anchors(_doc())
    section = client_entries(_doc())[client.title]
    for command in re.findall(r"`(/[^`]+)`", client.next_step):
        assert command in section, (client.name, command)


def test_install_is_the_first_route_mcp_md_gives():
    section = _doc().split("## Install in your client", 1)[1].split("\n### ", 1)[0]
    assert "job-sluice mcp install" in section
```

If `tests/test_doc_links_from_code.py` names its anchor helper differently, import that helper
(read the file; it is the rule `test_doc_links_from_code.py` already applies to runtime URLs) —
do not write a second slug rule.

Rewrite `tests/test_ai_setup_contract.py::test_step_1_registration_is_mcp_md_s_claude_code_entry_verbatim`
and `test_step_1_guards_the_registration`:

```python
def test_step_1_registers_with_mcp_install():
    from tests.test_docs_claims import _shell_blocks
    step1 = _section(_doc(), "### 1. Register")
    lines = [ln for b in _shell_blocks(step1) for ln in b.split("\n")
             if "mcp install" in ln]
    assert lines == ['"$JOB_SLUICE" mcp install --client claude-code --yes'], lines


def test_step_1_guards_the_registration():
    step1 = _section(_doc(), "### 1. Register").lower()
    for phrase in ("claude mcp get job-sluice", "claude mcp remove job-sluice -s <scope>",
                   "--read-only", "every claude code session", "starts with `/`",
                   "mcp serve </dev/null", "exits 2", "refused", "--replace", "vault_dir"):
        assert phrase in step1, phrase
```

Run both files → FAIL (MCP.md and AI-SETUP.md not yet changed).

- [ ] **Step 2: Edit the docs**

`docs/MCP.md`, at the top of "## Install in your client", before the existing first paragraph:

```markdown
The quickest route is to let sluice do it:

```bash
job-sluice mcp install
```

It finds the clients below that are installed on this machine, asks which to register in and
whether to give them the write tools, and registers `job-sluice` at user scope in each,
reading every client's config file back to prove it. It copies a file before changing it and
deletes the copy once the change is proven; a failed client keeps its copy and is named. Run it
from the installed `job-sluice` (it stops, exit 2, when started any other way or without the
`mcp` extra). `--client NAME` (repeatable) narrows the list, `--yes` asks nothing,
`--read-only` drops the write tools, `--replace` replaces an entry with different settings, and
`--dry-run` prints what it would do. For Codex, install appends a new entry to the end of
`config.toml` and edits nothing else (its own add command drops fields of other servers). It
writes nothing, and prints what to add by hand instead, when Codex already has an entry that
differs (set its values and keep its other settings) or when `mcp_servers` is written as an
inline table (add the entry inside it). A client's add command rewrites its file while it runs, so an
edit you make to that file in that moment can be lost; install checks the file just before.

The entries below are the manual route.
```

`docs/AI-SETUP.md` step 1: replace the last bash block (the `claude mcp add` one) and its
lead-in sentence with:

```markdown
Then register it (add `--read-only` if they said no to the write tools):

```bash
"$JOB_SLUICE" mcp install --client claude-code --yes
```

It registers at user scope, which makes the server available in every Claude Code session, and
reads Claude Code's config back to prove it. If an entry with different settings exists it is
refused and the command exits non-zero: tell the user what it printed, and run it again with
`--replace` only if they agree.
```

Keep the paragraph about `claude mcp get` (the existing-registration check), and change its
"remove it ... before adding" sentence to: "If the existing one is at user scope, the install
below refuses it until you pass `--replace`. At local or project scope, install cannot replace
it (it writes user scope only, and that entry would still win in its project): remove it with
`claude mcp remove job-sluice -s <scope>` first. Never add a second."

Change the step's last paragraph's "Do not add a `VAULT_DIR`" sentence to: "Do not add a
`VAULT_DIR` yourself: the coach agrees the vault with the user and saves it. If the user's
shell already exports one, install carries it into the registration, since it is the vault
every command they run already uses; say so, and that unsetting it is how to let the saved
vault apply."

`docs/USAGE.md`, after the `mcp serve` section, a `### \`job-sluice mcp install [--client NAME
...] [--read-only] [--replace] [--yes] [--dry-run]\`` section summarising: what it detects
(the roster), user scope only, readback, copy-and-delete, the outcomes and exit codes (copy the
Global Constraints lines), non-interactive rules, `--dry-run`, Codex appended when absent and
manual (nothing written, the values to set printed) when an entry exists or when `mcp_servers`
is an inline table an appended table cannot extend.

`README.md` Commands table: the `job-sluice mcp` row becomes
`run a Model Context Protocol server over stdio (\`serve\`, plus \`--write\` for the write tools),
or register it in the AI clients on this machine (\`install\`)`; in the MCP server section
replace the `claude mcp add` example's lead-in with `job-sluice mcp install` first and keep the
manual line after "or by hand:".

`docs/ARCHITECTURE.md`: a `sluice/mcpinstall/` paragraph beside the mcpserver one: it writes
OTHER tools' config files (user scope only); copy first via `core/backup.py::write_copy`,
deleted on a clean write; the JSON and append routes through `core/atomicfile.py::replace_if`
(the append route, Codex, adds a table after the last byte, written only when the bytes parse
as the old document plus exactly the new entry, and never edits an existing entry); command
route re-reads the table before the add; every registration read from the file; stdlib only and no
`mcp` import; `clients.py` loads on every invocation for `--client`'s choices, the rest only in
`cli.py::cmd_mcp_install`.

`.rulesync/rules/CLAUDE.md` Architecture paragraph. It opens by COUNTING the command packages
("plus two COMMAND packages, neither a sixth sub-app") and says no pipeline sub-app "imports
either"; a third makes both stale, and a count here is the drift class this file warns about,
so remove the count rather than bump it: "plus COMMAND packages, none a sixth sub-app:
`sluice/onboard/` for `job-sluice init`, (#164) `sluice/evidence/` for the nine `job-sluice
{experience,skills,stories} {add,list,verify}` handlers, and `sluice/mcpinstall/` for
`job-sluice mcp install`. No pipeline sub-app -- ingest, triage, cv, apply, track -- imports any
of them." Then, after the sentence naming `sluice/mcpserver.py` as a further importer of
`sluice.onboard`, add: "`sluice/mcpinstall/` writes other tools' MCP config files at user
scope: it copies each before changing it, reads every registration back from the file, and
imports nothing from the `mcp` package; `_build_parser` imports its `clients` module for
`--client`'s choices, which loads only the package's own stdlib modules." Make the same
count-free change to the comment above `_SUB_APPS` in `tests/test_core_layering.py`, which
quotes that sentence, and check `docs/ARCHITECTURE.md` for a count of command packages
(`grep -n "COMMAND\|command package" docs/ARCHITECTURE.md`). Then:

```bash
npm ci --ignore-scripts && npm run rulesync
```

- [ ] **Step 3: Run the guards and the suite**

Run: `.venv/bin/python -m pytest tests/test_mcp_install_docs.py tests/test_ai_setup_contract.py tests/test_docs_claims.py tests/test_doc_links_from_code.py -rA 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed"` → all passed.
Then the full suite twice (`.venv/bin/python -m pytest` and `env PATH=/usr/bin:/bin .venv/bin/python -m pytest`), and `.venv/bin/ruff check sluice tests scripts`. All green.

- [ ] **Step 4: Commit**

```bash
git add docs README.md .rulesync/rules/CLAUDE.md tests/test_mcp_install_docs.py tests/test_ai_setup_contract.py tests/test_core_layering.py
git commit -F - <<'EOF'
docs(mcp): job-sluice mcp install first, the manual entries after

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
```

---

### Task 9: Finish

- [ ] **Step 1:** Run every commit's suite (`git rebase -x '.venv/bin/python -m pytest -x -q' origin/main` — budget about 2.5 minutes per commit), ruff, and `env PATH=/usr/bin:/bin` at the tip.
- [ ] **Step 2:** Final whole-branch review (executing-plans' fresh reviewer), then `/review-pr`, fold every finding, then push and open the PR with `feat(mcp)` in the title, push-notify, and take it through CodeRabbit.
- [ ] **Step 3 (after merge, owner's go-ahead required):** one real `job-sluice mcp install --dry-run`, then a real install against the owner's own clients, each opened to confirm `job-sluice` connects. It writes their real configs, so it waits for an explicit yes.
