# `job-sluice mcp install` — design

Piece 3b of the AI-setup work. Approved in brainstorming on 2026-10-10; revised the same day
after two `/review-plan` rounds, every finding folded in below. Rulings are recorded under
Decisions.

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
| Reading JSONC for the collateral check | A read-only JSONC reader (comments, trailing commas); sluice never writes JSONC (review ruling, round 2) |
| Delivery | Two PRs: the shared writer first, then the command (review ruling, round 2) |
| Backup retention | A copy is deleted when its client's write ends CLEAN; kept only when the outcome is `failed` (owner, 2026-10-10, on a side agent's note that copies of other tools' configs accumulate their credentials) |
| A replace over an entry carrying env keys install cannot carry | JSON route keeps them; a command route REFUSES the replace and names the keys, since its add command would drop them (author ruling, round 2) |

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
prints an entry's environment; which variables relocate opencode's config (e.g. an `OPENCODE_CONFIG`
override), from its docs and a probe. Each result is recorded here as BEHAVIOUR (`keeps other
servers: yes`, a flag's spelling), never as pasted client output, which can carry home paths and
env values. A client whose add command drops other entries is moved to the JSON route or the
snippet route instead.

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
   - argv: the `job-sluice` LAUNCHER, absolute, symlinks NOT followed, so a Homebrew or pipx
     launcher path survives an upgrade (a resolved path would name a versioned directory that
     the next upgrade deletes). It is `os.path.abspath(sys.argv[0])` only when that names an
     executable file whose basename is `job-sluice` (on Windows, `job-sluice.exe` or `.cmd`);
     otherwise — `python -m sluice.cli` makes it `cli.py`, which a client cannot start — the
     command stops, exit 2, saying to run the installed `job-sluice`. Then `mcp serve`, then
     `--write` unless read-only.
   - env: every variable in `mcpinstall.PINNED_ENV` that is set in install's own environment,
     made absolute (`expanduser` + `abspath`), because a client launched from a desktop never
     sees the shell's exports and its server would otherwise open a different config or vault
     while install said `registered`. `PINNED_ENV` is a LITERAL tuple in production; the guard
     test DERIVES the expected set from source — every `env_var=` keyword passed to
     `core/paths.py::resolve` under any local binding (found through each file's own
     `ImportFrom` nodes, so `core/app.py`'s `resolve as _resolve_path` is seen), plus every
     `os.environ` read of `VAULT_DIR` and the XDG base variables `paths.py` reads — and asserts
     the two are equal. It also asserts scope: the derived set contains `SLUICE_CONFIG`,
     `SEEN_DB` and `DOSSIER_DIR`, so a walk that finds nothing cannot pass. The same shape as
     `tests/test_path_sandbox.py::test_the_sandbox_covers_every_path_env_var`. Credentials (API
     keys, `SLUICE_TELEGRAM_*`) are never pinned.
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
   runs for that client. `Entry` compares to the spec as `same` (argv equal, and every
   `PINNED_ENV` key equal on BOTH sides: a key the old entry carries and this install does not
   pin makes it `different`, since the server would still open the old path) or `different`.
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

**What is printed** (neutrality): the argv install REGISTERS, in full (it is built from sluice's
own launcher path and vocabulary); an EXISTING entry's argv only element by element, where the
executable path and the elements in sluice's own argument vocabulary (`mcp`, `serve`, `--write`)
are shown and every other element is shown as `<other argument>` — an entry a user wrote can carry
a token as an argument; the values of pinned sluice keys (sluice's own paths, shown so the user
sees what the server will open); any other env key by NAME only;
never a client's raw stdout/stderr — a failure is reported as a classified reason (`timed out`,
`exited 1`, `not found on PATH`, `readback did not show the entry`), because a client's output
can echo an entry's environment and those hold third-party credentials. Backup copies are named
relative to sluice's state folder, never as an absolute path.

## Write safety

**Every file a write touches is copied first, whichever route writes it.** For a command adapter
whose config file is known (VS Code's user `mcp.json`, the opencode global config, Claude Code's
`~/.claude.json` or `$CLAUDE_CONFIG_DIR/.claude.json`, Codex's `config.toml`, Gemini's
`settings.json`), the file's current bytes are copied before the command runs; no copy, no run.
A file that does not exist yet has nothing to copy: the run proceeds, the "before" table is
empty, and the after-check below still applies.
The copy goes through `core/backup.write_copy` into `mcp_install_backups/` in sluice's XDG state
folder (0o700, via `paths.resolve(kind="state", ...)`), carrying the file's mode — these files
hold credentials, and that is why a copy does not outlive its purpose: it exists to restore
from if THIS write does damage, so it is deleted the moment the write is proven clean — the
readback shows the spec, the same-file check passes and the collateral check finds nothing
changed. Only a client that ends `failed` keeps its copy, and the report names it (relative to
the state folder) and says it holds that tool's configuration, credentials included, to delete
once restored. Every run therefore adds at most one copy per failed client and none per clean
one, so rotating a key is not undone by a pile of old copies. Deletion is a plain unlink of the
copy this run created (by the name `write_copy` returned, in sluice's own private folder), not a
secure erase. Nothing in sluice reads a copy back.

**Collateral check.** Before and after each write, the adapter reads the client's whole server
table (not just `job-sluice`) from its config file, through a reader named per client: JSON
(`~/.claude.json`, Gemini), JSONC (VS Code, opencode — a read-only reader that drops `//` and
`/* */` comments and trailing commas outside strings, then hands the text to `json.loads`;
sluice never writes JSONC), or TOML (Codex, stdlib `tomllib`). If any other server's entry was
removed or changed, the outcome is `failed` with the changed server NAMES, even though
`job-sluice` was registered. For Claude Code only the user-scope `mcpServers` table is compared,
because the rest of `~/.claude.json` is rewritten by any running session; for the same reason
the report never says to restore the copy wholesale — it says to restore the NAMED servers'
entries from it, since a whole-file restore would revert unrelated changes made since.

**The file must be the one the client wrote.** After a command adapter's add, `job-sluice`
must appear, with the spec, in the SAME file the copy and the collateral check read. If the
command reports success and the readback shows the entry but that file does not, the outcome is
`failed: wrote somewhere other than <file relative to home>` — the computed path is wrong (an
unmeasured Windows row, an override variable), so the collateral check checked nothing. This is
what makes an unmeasured row safe rather than merely labelled.

**Env keys on a replace.** The JSON route keeps every env key on the old entry that is not in
`PINNED_ENV` and names it. A command route cannot carry them (its add command takes only what
install passes, and passing a user's API key back through argv would expose it in the process
list), so a replace over an entry carrying such keys is `refused` with the key NAMES and the
instruction to re-add them by hand or move them; `--replace` does not override this.

**Claude Code replace** (remove then add): the old entry's argv and env-key names are captured
before the remove; if the add fails, the report shows that argv, redacted by the rule in "What is
printed", and those names (values never), and the copy that holds the full old entry.

**Freshness on the command route.** A client's add command does not compare-and-set: it reads the
file and writes it back. So immediately before running it, the adapter re-reads the file and
compares it with the bytes it copied; if they differ (someone edited the file after the copy),
the outcome is `failed: the file changed while install was running`, nothing is run, and the copy
is kept. A file that was ABSENT at the first read has absence as its expected state: if it exists
at this re-read, the outcome is the same `failed`, nothing is run, and there is no copy to keep
(the "before" table was empty; the file someone created is left as they wrote it). What remains is the window while the client's own command runs, which no outside check
can close: an edit landing there may be overwritten by the client's write. The collateral check
afterwards catches that edit when it touched another server; when it touched the `job-sluice`
entry itself, the readback shows install's entry, which is the outcome the user asked for. This
residual is stated in the report's help text and in `docs/MCP.md`, the same posture as
`core/vault.py::_cas_write`'s micro-window.

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
   replaced; a copy made before the second window is kept and named, since that run ended
   `failed` (see retention above). A missing file is created exclusively.

The write itself is one general writer, `core/atomicfile.py::replace_if(path, data: bytes, *,
fresh, tmp_prefix)`, extracted from `core/config.py::write_config_text` in its own PR (see
Delivery). `fresh=None` is an exclusive create (parent directory first, the inode-checked
cleanup of a partial moved with it); otherwise `fresh(current_bytes) -> bool` decides. The
per-path in-process lock (`_config_write_lock` today) moves into the module and is taken by every
caller, including `keep_config_copy`. It carries over the symlink rule (resolved, and its TARGET
replaced in the target's directory so a dotfiles link survives), the temp file plus `os.replace`,
and the kept mode, with a caller-supplied temp prefix and a caller-supplied freshness check (`write_config_text` keeps its
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
  directory. `write_copy` is injected too, so a test can land a change to the file between the
  copy and the replace.
- The sandbox's variable list also covers `LOCALAPPDATA` and opencode's config override
  variables (as measured). The "every client is NotFound" test runs `detect()` with
  PRODUCTION's lookups (the same `which`, `env` and `home` builder `cli.py` uses, pointed at the
  sandbox), not the test fakes; its control is a temp-PATH fake `claude` that the same builder
  DOES find, so the test is shown able to see a client before it asserts none.

## Tests

Offline, against the sandbox above.

- `plan()` per client and platform: the exact argv (with env flags) or exact JSON, for macOS,
  Linux and Windows rows, so CI on Linux still covers the Windows and macOS path tables. Every
  row is built from ONE placeholder home (a fixed synthetic user under each platform's root),
  never a real one; `tests/test_no_leaked_files.py`'s home-path check gains the Windows form
  (`<drive>:\Users\`), proven by a known-bad line it must catch when run through the same
  engine that runs the sweep, with that one placeholder allow-listed by exact value.
- The JSONC reader: comments in every position (line, block, inside a string — kept), trailing
  commas, a `//` inside a URL string; and a property check that on comment-free JSON it returns
  exactly what `json.loads` returns.
- Launcher: `argv0` naming `cli.py` → exit 2, nothing detected; a launcher reached through a
  symlink yields the launcher's path.
- Fake client commands in the temp PATH that record argv and keep state:
  - a fake `claude` that exits 0 and changes nothing → `failed` (readback decides; this is the
    witness that the exit code is not trusted);
  - a fake add that drops another server → `failed` naming that server, copy kept and named
    (collateral);
  - a clean register and a clean replace (JSON route and command route) → the copy taken for
    that write no longer exists afterwards, and the state folder holds no copy for that client;
  - two clients in one run, one clean and one `failed` → only the failed client's copy remains;
  - a fake `get` that exits 1 with an unrelated error → `Unreadable`, nothing run;
  - a fake that hangs past an injected sub-second timeout → `failed: timed out`;
  - an edit to the file between the copy and the client run (landed by the injected `write_copy`
    returning after it writes) → `failed: the file changed while install was running`, the fake's
    call log shows no add, the copy kept;
  - a file absent at the first read that appears before the client run (created by an injected
    hook between the read and the run) → the same `failed`, no add in the fake's call log, the
    created file left byte-equal, no copy;
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
- Neutrality: sentinel values placed in another server's env, in a fake client's stderr, as an
  ARGUMENT of the existing `job-sluice` entry, and under a NON-pinned env key of that entry appear
  in no output stream, on
  every path that prints an entry: the old/new comparison, `--dry-run`, the refused-replace
  report and the Claude Code failed-add report. Control: the same run shows the sentinel's KEY
  NAME in the output, proving the planted entry was read.
- Env on replace: JSON route keeps and names a non-pinned key; a command route refuses with the
  name even under `--replace`; an old entry pinning `SLUICE_CONFIG` that this install does not
  pin reads `different`.
- Wrong file: a fake add that writes its entry somewhere other than the computed file, while its
  `get` shows it → `failed: wrote somewhere other than …`.

**Mutation witnesses**, each a named mutant that must turn the named test red:
1. Delete the readback comparison (treat exit 0 as success) → the exits-0-changes-nothing test.
2. Move the compare-and-set check after the replace → the changed-after-copy test.
3. Delete the copy-before-run call → a test asserting the copy exists before the fake's first
   write (the fake records whether the copy was present when it ran).
4. Delete the collateral comparison → the drops-another-server test.
5. Remove `Unreadable` and fall through to `Absent` → the unrelated-`get`-error test.
6. Print env values instead of names → the job-sluice-entry sentinel test.
7. Delete the same-file check → the wrong-file test.
8. Drop one name from `PINNED_ENV` → the roster guard; and stop iterating it (pin only
   `SLUICE_CONFIG`) → a behaviour test setting `SEEN_DB`.

9. Delete the post-success deletion → the clean-register test (a copy remains).
10. Move the deletion ahead of the collateral check → the drops-another-server test (its copy
    is gone).

Witness 2 is caught first by `tests/test_config_write.py`'s existing sha-match test, which says
nothing about the new one, so the new test is witnessed with the existing one deselected.

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

## Delivery

**PR 1, `refactor(core)`:** `core/atomicfile.py::replace_if` with its own tests — the temp file
is removed when the replace fails, the temp file is created in the TARGET's directory (proven
across a symlink whose link and target are in different directories), the lock serialises two
threads (a `threading.Barrier`), exclusive create, fresh-check abstain. `write_config_text` and
`keep_config_copy` call it; `tests/test_config_write.py` and the `setup_save` tests in
`tests/test_mcpserver.py` pass unchanged. `sluice/mcpextra.py` holds the "mcp not installed"
message, imported by `mcpserver.build_server`. No behaviour change.

**PR 2, `feat(mcp)`:** everything else in this spec.

## Measured before it ships

Codex and Gemini are installed into a temp prefix and measured as the others were, plus the
"still to measure" columns for every command adapter. One that cannot be measured on macOS ships
as a snippet-only entry rather than an unmeasured writer (Windows rows are the owner's stated
exception, above). After the code lands, one real install against the owner's actual clients,
checked by opening each.

## Out of scope

Docker, project scope, uninstall (each client's own remove covers it), VS Code Insiders and
VSCodium, the deb/rpm MCP package, multi-hunt.
