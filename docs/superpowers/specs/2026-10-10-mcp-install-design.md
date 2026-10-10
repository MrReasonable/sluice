# `job-sluice mcp install` — design

Piece 3b of the AI-setup work. Approved in brainstorming on 2026-10-10.

## Goal

One command that finds the MCP clients installed on this machine and registers the job-sluice
server in each, at user scope, so a human does not hand-edit eight config formats and the
AI-SETUP agent does not either.

**Who it is for:** a human at a terminal first (detect, tick, confirm); the same command takes
flags so AI-SETUP's step 1 can call it non-interactively.

**Success:** after `job-sluice mcp install`, every selected client lists `job-sluice` with the
command this install resolved, verified by reading the client's registration back — never by a
client's exit code alone.

## Decisions (owner, 2026-10-10)

| Question | Decision |
|---|---|
| Main user | Both, human first; flags for the agent |
| Scope | User scope only. A client with no user-level registration is reported, not written |
| Write tools | Asked once, default **yes**. Non-interactive: on unless `--read-only` |
| Existing entry | Same: `unchanged`, nothing written. Different: show old and new, ask; non-interactive refuses unless `--replace` |
| Mechanism | Approach A: the client's own add command where one works; strict-JSON edit where none does; print the snippet when neither is safe |

## Measurements behind the mechanism (2026-10-10)

Measured against throwaway profiles (`--user-data-dir`, `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR`),
never the real configs.

| Client | Version | Route | Re-adding an existing name |
|---|---|---|---|
| Claude Code | 2.1.296 | `claude mcp add --scope user job-sluice -- <argv>` | refuses, exit 1 (`already exists in user config`). Replace = `claude mcp remove --scope user job-sluice` then add. `claude mcp get job-sluice` exits 1 when absent |
| VS Code | 1.141.0 | `code --add-mcp '{"name":"job-sluice","command":...,"args":[...]}'` writes `<user data>/User/mcp.json` under `servers` | overwrites |
| opencode | 2.0.25 | `opencode mcp add --global job-sluice -- <argv>` writes `mcp.servers["job-sluice"]` (`type: local`, `command: [argv]`) | overwrites |
| Cursor | 3.24.9 | `cursor --add-mcp` **exits 0 and writes nothing** (no file under the profile, `~/.cursor/mcp.json` untouched). So Cursor is a JSON edit of `~/.cursor/mcp.json` | — |
| Claude Desktop | — | no command; JSON edit of its config file (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) | — |
| Codex | not installed here | `codex mcp add` — to measure during implementation | — |
| Gemini CLI | not installed here | `gemini mcp add -s user` — to measure during implementation | — |

The Cursor row is why success is decided by readback: an exit code proved worthless for one of
the eight.

## The command

```
job-sluice mcp install [--client NAME ...] [--read-only] [--replace] [--yes] [--dry-run]
```

1. **Resolve the server command** once into a `ServerSpec`: the absolute path of the running
   `job-sluice`, then `mcp serve`, then `--write` unless read-only. If the `mcp` extra is not
   importable, stop with the existing install hint before detecting anything — a registration
   that cannot start fails inside the client, where its cause is never shown.
2. **Detect** each client in a fixed roster: `found` (with its evidence — a command on `PATH`, a
   config directory), `not found`, or `unsupported` (reason plus the MCP.md anchor).
3. **Read** each found client's current `job-sluice` entry: `absent`, `same`, or `different`
   (showing its current command).
4. **Choose.**
   - Interactive (a TTY, no `--yes`): a checklist of found clients, absent and different ticked,
     same shown as already registered; one `--write` question, default yes, saying what the
     write tools can do; a confirm per different entry.
   - Non-interactive (`--yes`, or `--client` given, or no TTY): no prompts; write tools on unless
     `--read-only`; a different entry is `refused` unless `--replace`.
5. **Write** through each adapter, then **read back**. Outcome per client: `registered`,
   `replaced`, `unchanged`, `refused`, or `failed` (with the reason and the snippet to paste).
6. **Finish** with each client's restart step and its slash command, the same text MCP.md gives.
   Exit non-zero when any selected client ends `failed` or `refused`.

`--dry-run` stops after step 4 and prints exactly what would be run or written.

## Components

New module `sluice/mcpinstall.py`: stdlib only, nothing from the `mcp` package, imported lazily
inside `cli.py::cmd_mcp_install` (the `mcp` importability check in step 1 is a `find_spec`, not
an import, so it loads nothing).

- `ServerSpec` — the resolved argv. Built once and handed to every adapter, so two clients
  cannot be given different commands.
- One adapter class per client, in a roster tuple. Each has `name`, `doc_anchor` (its MCP.md
  heading), `measured` (client and version, date), and:
  - `detect() -> Found | NotFound | Unsupported`
  - `current() -> Absent | Entry(argv)` — reads, never writes
  - `plan(spec, replace) -> Action` — the exact argv to run or JSON to write; what `--dry-run`
    prints and what tests assert on without running anything
  - `apply(action)`, then `current()` again as the readback

**Command adapters** (Claude Code, VS Code, opencode, and Codex/Gemini once measured) run the
client's command with `subprocess.run([...], shell=False)`, a timeout, and captured output.
Claude Code's replace is remove-then-add; when the add fails after the remove, the report gives
the old command so it can be re-added by hand. VS Code's `current()` reads its user `mcp.json`
read-only, tolerating JSONC comments for reading only.

**JSON adapters** (Cursor, Claude Desktop) handle strict JSON only:

1. Parse. Comments, a parse error, or a server table that is not an object → `failed` with the
   reason and the snippet; nothing written.
2. Set only `mcpServers["job-sluice"]`; every other key is preserved. The file is re-serialised
   with `indent=2`, so formatting may change and content does not; the report says so.
3. Keep a copy first through `core/backup.write_copy`, in sluice's state folder
   (`mcp_install_backups/`, 0o700), carrying the file's mode — these files hold credentials. No
   copy, no replace.
4. Replace only when the bytes still hash to what was read (compare-and-set), resolving a
   symlink the way `core/config.py::write_config_text` does so a link into a dotfiles repo
   survives. A missing file is created exclusively.

**Never written:** a project-scope file, any key other than `job-sluice`, any client not
selected.

## Docs

- `docs/MCP.md` "Install in your client" opens with `job-sluice mcp install`; each per-client
  entry stays as the manual route.
- `docs/AI-SETUP.md` step 1 replaces the hand-written `claude mcp add` block with
  `job-sluice mcp install --client claude-code --yes` (plus `--read-only` if the user declines
  write tools). The probe, the Docker branch and the "exits 2" stop stay. `test_ai_setup_contract.py`
  pins move with it.
- `docs/USAGE.md` and README's Commands table gain the subcommand (the doc guards require it).
- One roster: `tests/test_mcp_install_docs.py` asserts every adapter's `doc_anchor` resolves to
  an MCP.md heading and that the adapter roster and the MCP.md client roster are the same set.

## Tests

Offline, against a temp `HOME`, a temp XDG tree, and a fake `PATH`.

- `plan()` per adapter: the exact argv or exact JSON.
- Fake client commands on a temp `PATH` that record argv and keep state — including one that
  behaves like Cursor (exits 0, writes nothing), witnessing that readback, not the exit code,
  decides the outcome.
- JSON edits: an unrelated key and a credential-shaped value survive byte-equal; JSONC,
  malformed input, and a file changed between read and write each give `failed` with the file
  unchanged and no copy left behind; the copy carries the file's mode; a symlinked file has its
  target replaced.
- Flow: `same` → `unchanged`, nothing written; `different` without `--replace` → `refused`,
  non-zero exit; with it → `replaced`; a missing `mcp` extra stops before detection;
  `--dry-run` writes nothing; the `--write` question defaults yes; `--read-only` turns it off;
  a Claude Code add failing after its remove reports the old command.
- Mutation witnesses for the readback, the compare-and-set, and copy-before-replace.

## Measured before it ships

Codex and Gemini are installed into a temp prefix and measured exactly as the others were. One
that cannot be measured ships as a print-the-snippet entry, never as an unmeasured writer. After
the code lands, one real install against the owner's actual clients, checked by opening each.

## Out of scope

Docker (runs on the host; a container is not detectable), project scope, uninstall (each
client's own remove covers it), the deb/rpm MCP package, multi-hunt.
