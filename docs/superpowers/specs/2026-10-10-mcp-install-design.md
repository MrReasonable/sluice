# `job-sluice mcp install` — design

Piece 3b of the AI-setup work. Approved in brainstorming on 2026-10-10; revised the same day
after `/review-plan` (16 findings, all folded in below; three owner rulings recorded under
Decisions).

## Goal

One command that finds the MCP clients installed on this machine and registers the job-sluice
server in each, at user scope, so neither a human nor the AI-SETUP agent hand-edits a client's
config format.

**Who it is for:** a human at a terminal first (detect, tick, confirm); the same command takes
flags so AI-SETUP's step 1 can call it non-interactively.

**Success:** after `job-sluice mcp install`, every selected client lists `job-sluice` with the
command and environment this install resolved, verified by reading the client's registration
back, never by a client's exit code alone, and nothing else in that client's server table has
changed.

## Decisions (owner, 2026-10-10)

| Question | Decision |
|---|---|
| Main user | Both, human first; flags for the agent |
| Scope | User scope only. A client with no user-level registration is reported, not written |
| Write tools | Asked once, default **yes**. Non-interactive: on unless `--read-only` |
| Existing entry | Same: `unchanged`, nothing written. Different: show old and new, ask; non-interactive refuses unless `--replace` |
| Mechanism | Approach A: the client's own add command where one works; a strict-JSON edit where none does; print the snippet when neither is safe |
| What makes a run non-interactive | `--yes`, or no TTY on stdin. `--client` only narrows the list (review ruling) |
| Platforms | macOS, Linux and Windows (review ruling) |
| Environment | Pin sluice's relocating path variables into the entry; report any other env key by NAME only (review ruling) |

## Measurements behind the mechanism (2026-10-10, macOS)

Measured against throwaway profiles (`--user-data-dir`, `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR`),
never the real configs.

| Client | Version | Route | Re-adding an existing name |
|---|---|---|---|
| Claude Code | 2.1.296 | `claude mcp add --scope user job-sluice -- <argv>` | refuses, exit 1, `already exists in user config`. Replace = `claude mcp remove --scope user job-sluice`, then add. `claude mcp get job-sluice` exits 1 with `No MCP server named "job-sluice"` when absent |
| VS Code | 1.141.0 | `code --add-mcp '{"name":"job-sluice","command":...,"args":[...]}'` writes `<user data>/User/mcp.json` under `servers` | overwrites |
| opencode | 2.0.25 | `opencode mcp add --global job-sluice -- <argv>` writes `mcp.servers["job-sluice"]` (`type: local`, `command: [argv]`) | overwrites |
| Cursor | 3.24.9 | `cursor --add-mcp` **exits 0 and writes nothing**, so Cursor is a JSON edit of `~/.cursor/mcp.json` | — |
| Claude Desktop | — | no command; JSON edit of its config file | — |
| Codex | not installed here | `codex mcp add` — measured during implementation | — |
| Gemini CLI | not installed here | `gemini mcp add -s user` — measured during implementation | — |

The Cursor row is why success is decided by readback.

**Still to measure, before each command adapter ships:** whether the client's add preserves
other servers, other keys and (VS Code, opencode) comments in the file it rewrites; how each add
command takes environment values (`-e`/`--env` and the JSON `env` key); how `claude mcp get`
prints an entry's environment. Each result becomes a column here, and a client whose add command
drops other entries is moved to the JSON route or the snippet route instead.

**Windows** ships unmeasured (owner ruling): path rows below come from each client's own
documentation, the `measured` field on every Windows row says "unmeasured on Windows", and the
report prints that beside the outcome. What keeps an unmeasured row safe is the same thing that
keeps every row safe: copy before any write, compare-and-set, readback, and the collateral check
(below), so a wrong path or a misbehaving command ends `failed`, never a false `registered`.

## Per-platform locations

Resolved through injected `home`, `env` and `platform` (see Testability), never read from the
process directly by an adapter.

| Client | macOS | Linux | Windows |
|---|---|---|---|
| Claude Code | `claude` on PATH | same | same (`claude.cmd`/`.exe` via `shutil.which`) |
| VS Code (user `mcp.json`, read-only) | `~/Library/Application Support/Code/User/mcp.json` | `$XDG_CONFIG_HOME/Code/User/mcp.json` (default `~/.config`) | `%APPDATA%\Code\User\mcp.json` |
| opencode (global config, read-only) | `$XDG_CONFIG_HOME/opencode/opencode.json[c]` (default `~/.config`) | same | same rule under the user profile |
| Cursor | `~/.cursor/mcp.json` | same | `%USERPROFILE%\.cursor\mcp.json` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | `Unsupported` (no official Linux build) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Codex | `$CODEX_HOME/config.toml`, default `~/.codex/config.toml` (read-only) | same | same |
| Gemini CLI | `~/.gemini/settings.json` (read-only) | same | same |

VS Code Insiders and VSCodium are out of scope (each a separate user folder and command).

## The command

```
job-sluice mcp install [--client NAME ...] [--read-only] [--replace] [--yes] [--dry-run]
```

`--client` takes a name from the adapter roster (`claude-code`, `vscode`, `opencode`, `cursor`,
`claude-desktop`, `codex`, `gemini`), repeatable; argparse `choices` derived from the roster
rejects an unknown name and lists the valid ones (fail loudly). Without `--client`, every client
in the roster is considered.

1. **Resolve the server command** once into a `ServerSpec`:
   - argv: `os.path.abspath(sys.argv[0])` — absolute, symlinks NOT followed, so a Homebrew or
     pipx launcher path survives an upgrade (a resolved path would name a versioned directory
     that the next upgrade deletes). Then `mcp serve`, then `--write` unless read-only.
   - env: every sluice relocating variable set in install's own environment, made absolute
     (`expanduser` + `abspath`), because a client launched from a desktop never sees the shell's
     exports and its server would otherwise open a different config or vault while install said
     `registered`. The set is DERIVED, not hand-listed: every `env_var=` passed to
     `core/paths.py::resolve`, plus `VAULT_DIR` and the XDG base variables `paths.py` reads. A
     guard test enumerates those call sites and fails if the pinned set and the derived set
     differ. Credentials (API keys, `SLUICE_TELEGRAM_*`) are never pinned.
   - If the `mcp` extra is not importable (`importlib.util.find_spec("mcp")`), stop before
     detecting anything, exit **2** (the code `mcp serve` uses for the same condition), with the
     same message. The message moves to one constant in a new leaf module, `sluice/mcpextra.py`,
     imported by both `mcpserver.build_server` and `mcpinstall`, so it has one
     home and importing it loads nothing heavy.
2. **Detect** each considered client: `Found(evidence)`, `NotFound`, or `Unsupported(reason)`
   (platform, or a client with no user-level registration), each with its MCP.md anchor.
3. **Read** each found client's current entry: `Absent`, `Entry(argv, env)`, or
   `Unreadable(reason)` (unparseable file, wrong shape, a client command that failed for any
   reason other than its own "not found" text, a timeout). `Unreadable` ends `failed` and nothing
   runs for that client. `Entry` compares to the spec as `same` (argv equal and every pinned key
   equal) or `different`.
4. **Choose.**
   - Interactive (a TTY and no `--yes`): a checklist of found clients, absent and different
     ticked, same shown as already registered; the `--write` question (default yes) unless
     `--read-only` already answered it; a confirm per different entry unless `--replace` already
     answered it.
   - Non-interactive (`--yes`, or no TTY): no prompts. Write tools on unless `--read-only`. A
     different entry is `refused` unless `--replace`.
   - A client named by `--client` that is `NotFound` or `Unsupported` ends `failed` with the
     reason (it was asked for and cannot be done), not silently skipped.
5. **Write** through each adapter (see Write safety), then **read back**. Outcome per client:
   `registered`, `replaced`, `unchanged`, `refused`, or `failed` (reason plus the snippet to
   paste). A readback that does not show the spec is `failed`, whatever the client's exit code.
6. **Finish** with each client's restart step and its slash command, the same text MCP.md gives.
   Exit non-zero when any client ends `failed` or `refused`. Exit 0 when nothing was selected
   because nothing was found, with a line saying so and the MCP.md link.

`--dry-run` asks nothing: it runs steps 1–3, applies the flags as a non-interactive run would,
prints exactly what would be run or written per client, and writes nothing.

**What is printed** (neutrality): an entry's argv and the values of pinned sluice keys (sluice's
own paths, shown so the user sees what the server will open); any other env key by NAME only;
never a client's raw stdout/stderr — a failure is reported as a classified reason (`timed out`,
`exited 1`, `not found on PATH`, `readback did not show the entry`), because a client's output
can echo an entry's environment and those hold third-party credentials. Backup copies are named
relative to sluice's state folder, never as an absolute path.

## Write safety

**Every file a write touches is copied first, whichever route writes it.** For a command adapter
whose config file is known (VS Code's user `mcp.json`, the opencode global config, Claude Code's
`~/.claude.json` or `$CLAUDE_CONFIG_DIR/.claude.json`, Codex's `config.toml`, Gemini's
`settings.json`), the file's current bytes are copied before the command runs; no copy, no run.
The copy goes through `core/backup.write_copy` into `mcp_install_backups/` in sluice's XDG state
folder (0o700, via `paths.resolve(kind="state", ...)`), carrying the file's mode — these files
hold credentials. Copies are never pruned and nothing in sluice reads them back.

**Collateral check.** Before and after each write, the adapter reads the client's whole server
table (not just `job-sluice`). If any other server's entry was removed or changed, the outcome is
`failed` with the changed server NAMES and the copy to restore from, even though `job-sluice`
was registered. For Claude Code only the user-scope `mcpServers` table is compared, because the
rest of `~/.claude.json` is rewritten by any running session.

**Claude Code replace** (remove then add): the old entry's argv and env-key names are captured
before the remove; if the add fails, the report shows that argv and those names (values never) and
the copy that holds the full old entry.

**Command adapters** run the client with `subprocess.run([...], shell=False)`, the executable
resolved by `shutil.which` (so a Windows `.cmd` shim launches), stdin closed, output captured and
never printed, and a timeout. Environment values are passed as the client's own flag
(`-e KEY=VALUE`, `--env KEY=VALUE`) or JSON `env` key, per the measurement column.

**JSON adapters** (Cursor, Claude Desktop) handle strict JSON only:

1. Read bytes. A directory, an unreadable file, non-UTF-8 bytes, a BOM, comments, a parse error,
   duplicate keys (parsed with `object_pairs_hook` that refuses them, since `json.loads` keeps the
   last and a re-save would silently drop the other), a top-level value that is not an object, a
   server table that is not an object, or an existing `job-sluice` value that is not an object →
   `failed` with the reason and the snippet; nothing written, no copy.
2. An empty file or a missing file is a create: the server table is the only content.
3. Set only `mcpServers["job-sluice"]`. Every other key is preserved. On replace, an existing
   `env` key that is not a pinned sluice key is KEPT (the user put it there); its name is
   reported. The file is re-serialised with `indent=2`, so formatting may change and content does
   not; the report says so.
4. Copy the read bytes (`write_copy`), then replace only if the file still hashes to the read
   bytes. A change between read and copy, or between copy and replace, ends `failed` with nothing
   replaced; a copy made before the second window is left in place and named in the report
   (copies are never deleted, by design). A missing file is created exclusively.

The replace itself is a general compare-and-set writer extracted from
`core/config.py::write_config_text` into `core/` (symlink resolved and its TARGET replaced in the
target's directory so a dotfiles link survives; temp file plus `os.replace`; mode kept), with a
caller-supplied temp prefix and a caller-supplied freshness check (`write_config_text` keeps its
text sha; `mcpinstall` compares raw bytes, since a BOM or non-UTF-8 file is refused before it
gets there). `write_config_text` becomes a thin caller of
it, and `mcpinstall` is the second. One writer, so no new CodeQL sink and no second copy of the
symlink and ownership logic.

**Never written:** a project-scope file, any key other than `job-sluice` (and, for JSON adapters,
its own value), any client not selected.

## Components

- `sluice/mcpinstall.py` — stdlib only, nothing from the `mcp` package, imported lazily inside
  `cli.py::cmd_mcp_install`.
- `ServerSpec` — argv plus pinned env. Built once and handed to every adapter.
- Two mechanisms, `CommandRoute` and `JsonRoute`, and each client as thin DATA over one of them:
  name, MCP.md anchor, per-platform locations, the measured field, and (command route) the argv
  templates for get/add/remove plus how it parses `get`. Not a registered seam: the roster is a
  plain tuple, and argparse `choices` derives from it.
- `Unsupported`, `NotFound`, `Found`, `Absent`, `Entry`, `Unreadable` and the outcome vocabulary
  as small frozen dataclasses or literals.

## Testability (no test can reach a real client)

- Every OS touchpoint is injected: `which`, `run` (the subprocess call), `env`, `home`,
  `platform`, `argv0`, the timeout, and the state folder. Production passes the real ones from
  `cli.py`; tests pass fakes.
- An autouse fixture for the install tests sets `PATH` to an empty temp directory and deletes
  `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `VSCODE_IPC_HOOK_CLI`, `APPDATA`, `USERPROFILE` and the XDG
  variables beyond what `conftest.py` already pins. A test asserts that under it every roster
  client detects `NotFound` (not `Found` because this machine has `claude` and `code`).
- The default `run` used in tests raises if handed an executable outside the test's temp
  directory.

## Tests

Offline, against the sandbox above.

- `plan()` per client and platform: the exact argv (with env flags) or exact JSON, for macOS,
  Linux and Windows rows, so CI on Linux still covers the Windows and macOS path tables.
- Fake client commands in the temp PATH that record argv and keep state:
  - a fake `claude` that exits 0 and changes nothing → `failed` (readback decides; this is the
    witness that the exit code is not trusted);
  - a fake add that drops another server → `failed` naming that server, copy named (collateral);
  - a fake `get` that exits 1 with an unrelated error → `Unreadable`, nothing run;
  - a fake that hangs past an injected sub-second timeout → `failed: timed out`;
  - a fake removed between detect and apply → `failed: not found on PATH`;
  - Claude Code remove-then-add with a failing add → report shows the old argv and env NAMES.
- JSON route: an unrelated key and a sentinel credential (`SENTINEL-NOT-A-SECRET-…`) survive
  byte-equal in the parsed result; a user env key on the old entry is kept and named; every
  refusal row in Write safety step 1 → `failed`, file unchanged, no copy; empty and missing file
  → created; a change before the copy → no copy, nothing replaced; a change after the copy → copy
  kept and named, nothing replaced; the copy carries the file's mode; a symlinked file has its
  target replaced and the link survives; a path with spaces and non-ASCII characters.
- Flow: `same` → `unchanged`, nothing written; `different` non-interactive without `--replace` →
  `refused`, non-zero; with it → `replaced`; interactive `--replace`/`--read-only` skip their
  questions; `--client` with a TTY still asks the `--write` question; a `--client` that is
  `NotFound` → `failed`, non-zero; an unknown `--client` → argparse error listing names; nothing
  found → exit 0 with the link; a missing `mcp` extra → exit 2 before detection; `--dry-run`
  asks nothing and writes nothing (every fake's call log shows only reads).
- Server path: a launcher reached through a symlink yields the launcher's path, not its target,
  and a re-run reads `same`.
- Pinned env: set `SLUICE_CONFIG=~/x.yaml` → the entry carries the absolute path; an unset
  variable is not pinned; the derived-roster guard (above).
- Neutrality: with a sentinel value in another server's env and in a fake client's stderr, no
  output stream contains the sentinel.

**Mutation witnesses**, each a named mutant that must turn the named test red:
1. Delete the readback comparison (treat exit 0 as success) → the exits-0-changes-nothing test.
2. Move the compare-and-set check after the replace → the changed-after-copy test.
3. Delete the copy-before-run call → a test asserting the copy exists before the fake's first
   write (the fake records whether the copy was present when it ran).
4. Delete the collateral comparison → the drops-another-server test.
5. Remove `Unreadable` and fall through to `Absent` → the unrelated-`get`-error test.

## Docs

- `docs/MCP.md` "Install in your client" opens with `job-sluice mcp install`; each per-client
  entry stays as the manual route. Example output uses a placeholder path, never a real one.
- `docs/AI-SETUP.md` step 1 replaces the hand-written `claude mcp add` block with
  `job-sluice mcp install --client claude-code --yes` (plus `--read-only` if the user declines
  write tools). The probe, the Docker branch and the "exits 2" stop stay;
  `test_ai_setup_contract.py` pins move with it.
- `docs/USAGE.md` and README's Commands table gain the subcommand (the doc guards require it).
- `docs/ARCHITECTURE.md` and `.rulesync/rules/CLAUDE.md`'s Architecture paragraph name the new
  module: what it writes (other tools' config files), the copy-first rule, and that it imports
  nothing heavy. `npm run rulesync` after.
- Roster guard in `tests/test_mcp_install_docs.py`: every adapter's anchor resolves to an MCP.md
  heading, and the adapter roster equals the MCP.md client headings EXCLUDING the Docker heading,
  which is excluded by name with the reason (it has no adapter: a container is not detectable
  from the host).

## Measured before it ships

Codex and Gemini are installed into a temp prefix and measured as the others were, plus the
"still to measure" columns for every command adapter. One that cannot be measured on macOS ships
as a snippet-only entry rather than an unmeasured writer (Windows rows are the owner's stated
exception, above). After the code lands, one real install against the owner's actual clients,
checked by opening each.

## Out of scope

Docker, project scope, uninstall (each client's own remove covers it), VS Code Insiders and
VSCodium, the deb/rpm MCP package, multi-hunt.
