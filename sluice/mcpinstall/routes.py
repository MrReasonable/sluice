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
from sluice.mcpinstall.clients import (DOCS_URL, Client, add_argv, check_scope, entry_value,
                                       extra_fields, parse_entry, redact_argv, remove_argv,
                                       server_table, snippet)
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
    can echo an entry's environment, and those hold other tools' credentials). On Windows a
    `.cmd` shim is run through `cmd.exe`, which parses `%` and `&` in its arguments; that is
    unmeasured (every Windows row says so), and readback catches an argv it mangled."""
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
    chain, node = [rest], rest
    for key in client.table:
        node = node.get(key) if isinstance(node, dict) else None
        chain.append(node)
    if isinstance(node, dict):
        node.pop(SERVER_NAME, None)
    # An EMPTY table on the entry's own path is the entry's absence: opencode's add turns
    # `"mcp": {}` into `"mcp": {"servers": {...}}`, which is no other setting changing. A table
    # there holding anything else is kept, so a key the add drops beside it is still caught.
    for parent, key, child in reversed(list(zip(chain, client.table, chain[1:]))):
        if child is None or (isinstance(child, dict) and not child):
            if isinstance(parent, dict):
                parent.pop(key, None)
        else:
            break
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
    # Only the JSON route re-saves the whole file, so only its files must hold numbers that
    # survive json.dumps unchanged.
    return jsonc.load(data, jsonc=client.reader == "jsonc", exact=client.route == "json")


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
    except RecursionError:
        return Unreadable("it is nested too deeply to read")
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
    except Exception as exc:      # noqa: BLE001 -- see _unexpected
        return _unexpected(client, exc, copy, state.path, deps)
    if not wrote:
        return Outcome(client.name, "failed", CHANGED, _kept(copy, state.path, deps))
    try:
        return _verify(client, state, spec, copy, deps, kind)
    except Exception as exc:      # noqa: BLE001 -- see _unexpected
        return _unexpected(client, exc, copy, state.path, deps)


def _unexpected(client: Client, exc: Exception, copy: str | None, path: str,
                deps: Deps) -> Outcome:
    """An error nothing here anticipated, AFTER the copy was taken: this client ends `failed`
    with its copy kept and named, instead of the exception ending the run and the report with
    it (an earlier client's write would then go unreported). Only the type is printed: a
    message can quote the file, whose values can be credentials. `Exception`, not
    `BaseException`: Ctrl-C still stops the run."""
    return Outcome(client.name, "failed", f"unexpected error: {type(exc).__name__}",
                   _kept(copy, path, deps))


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
    try:
        data = (json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False)
                + "\n").encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate (`"\ud800"`, which json reads) has no UTF-8 form: write it escaped,
        # which is the same document.
        data = (json.dumps(doc, indent=2, allow_nan=False) + "\n").encode("utf-8")
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
    copy, failure = _take_copy(state, deps,
                               same=lambda now: _same_value(_scope_of(client, now), before))
    if failure:
        return Outcome(client.name, "failed", failure)
    try:
        return _command_after_copy(client, state, spec, deps, exe, before, copy, kind)
    except Exception as exc:      # noqa: BLE001 -- see _unexpected
        return _unexpected(client, exc, copy, state.path, deps)


def _command_after_copy(client: Client, state: FileState, spec: ServerSpec, deps: Deps, exe: str,
                        before, copy: str | None, kind: str) -> Outcome:
    replacing = isinstance(state.current, Entry)
    deps.before_recheck()
    # The add reads and writes the file itself, so this is the last moment to notice a change.
    # Parsed content is compared, not bytes, and for Claude Code only the server table: a
    # running session rewrites the rest of ~/.claude.json constantly (`check_scope`).
    now = read_state(client, state.path)
    if (isinstance(now, Unreadable) or (now.raw is None) != (state.raw is None)
            or not _same_value(_scoped(client, now.doc, now.table), before)):
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
