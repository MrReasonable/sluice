# AI-SETUP around the career coach, and installing the MCP server per client (design)

Status: revision 3, 2026-10-09. Two `/review-plan` rounds (four reviewers each) folded in; the
tables at the end map every finding.

Piece 3 of "drive sluice from Claude Code" (piece 1: `2026-10-06-mcp-verify-elicitation-design.md`;
piece 2: `2026-10-07-in-session-setup-career-coach-design.md`, amended by
`2026-10-08-setup-chat-confirmation-design.md`). Owner decisions taken on 2026-10-09 are marked
**(owner)**.

## Goal

A new user pastes README's one line ("Read `docs/AI-SETUP.md` and set sluice up for me") into
Claude Code and ends up in the career coach, then comes out with a CV Layout, proposed evidence
waiting for their tick, and a first run. Agents without MCP prompts still reach a working setup
through a fallback. Every MCP client sluice documents has install instructions that were measured,
not assumed.

## Decisions

- **Claude Code first, CLI fallback (owner).** One file. The main path brackets the coach;
  today's `init` + interview steps survive as an appendix for agents with no MCP prompt support,
  so README's "Codex, Gemini CLI, whichever you use" stays true.
- **One doc, not two (owner).** Rules 1-3 and the after-coach steps exist once.
- **Per-client install instructions, all measured (owner).** Claude Code, opencode, Claude
  Desktop, Cursor, VS Code, Codex, Gemini CLI. A client nobody measured gets no entry.
- **Docker is measured as an MCP server in this piece (owner).** The image already installs the
  `mcp` extra (`Dockerfile`), and the compose file pins `VAULT_DIR`.
- **`--write` at `--scope user` (owner).** The write tools (`setup_save`, `create_lead`,
  `propose_evidence`, `verify_evidence`, ...) are then present in every Claude Code session on the
  machine. Accepted because every write keeps its own gate (a chat yes for setup, the review form
  for verification, the stale/CAS checks, never-clobber); the agent says this plainly when it asks
  the user about `--write`.
- **`job-sluice mcp install` is piece 3b, next after this (owner).** Detect installed clients,
  prompt per client, register with backups and never-clobber of their config files. Its own spec;
  it consumes this piece's measured per-client data. Multi-hunt moves after 3b.
- **The `focus` prompt argument is removed** (see Measured facts). Folded into this PR.

## Measured facts (Claude Code 2.1.295, 2026-10-09)

1. **A server added with `claude mcp add` mid-session is not loaded by that session.** Registered
   a sandboxed probe from inside a running session: `claude mcp list` showed it connected, the
   session's tool set never gained it. Removed afterwards. The Claude Code docs are silent on
   in-session pickup; the documented recovery is exit + `claude --continue`, which resumes only
   conversations from the current directory.
2. **An MCP prompt argument receives the first whitespace-separated token only.** Headless probe
   (`--restricted --strict-mcp-config`, sandboxed server wrapped to log the argument): typing
   `/mcp__sl__career_interview I want to change roles` (a neutral probe phrase) delivered
   `focus='I'`, which
   the prompt then quoted as the user's focus; the remaining words were dropped. Quoting is
   undocumented.
   - Consequence 1: a user following MCP.md/USAGE.md/handoff.md's "optionally followed by what
     you want" gets a one-word focus, silently.
   - Consequence 2: `scripts/coach_eval/run.py` sends `"/mcp__sluice__career_interview " +
     p.focus`, and every persona's focus is multi-word. Every eval scenario ran with a one-word
     focus. No eval conclusion about how the coach reads a focus is valid.
3. **Default `claude mcp add` scope is `local`**: the server loads only in the directory it was
   added from. `--scope user` loads it everywhere (docs: mcp.md, "MCP installation scopes").
4. Docs facts used, not measured: `/mcp` lists connected servers; `--env K=V` must not directly
   precede the server name; everything after `--` is the server command; the option order
   `--scope user --transport stdio <name> -- <cmd>` matches `claude mcp add --help`.

What CI can and cannot hold: CI pins the shape of what the docs SAY (snippet structure, scope,
the server command, the prompt's name and arguments). The capability rows (does a client show
prompts, does it show the form) and facts 1-2 are dated, versioned measurements; nothing in CI
detects a client changing its behaviour. Each row in MCP.md carries the client version and date
it was measured, so a reader can tell how old it is.

## Doc structure (`docs/AI-SETUP.md`)

1. **Preamble.** As today, plus: the main path is Claude Code with sluice's MCP server; an agent
   whose client cannot show MCP prompts follows the appendix.
2. **The three rules.** Wording kept; mechanisms updated. Rule 1: the coach asks the preference
   questions; outside the coach, ask, and leave anything unanswered unset (the "inferring them is
   the bug" text stays, binding both paths). Rule 2: `propose_evidence` and `experience add`
   propose; the human verifies, by ticking the `verify_evidence` form or by running
   `job-sluice experience verify` themselves. Rule 3 unchanged.
3. **Division of labour.** Judging/Candidate Profile rows become "the coach drafts, the human says
   yes to each save". The "Verify evidence: **never** (you may open the `verify_evidence` form) /
   **only they can**" row is kept verbatim. New human-only row: typing the coach's slash command
   (an agent cannot start an MCP prompt; the docs describe only user execution).
4. **The sequence.** `doctor` after each state-changing step and the exit-code guidance, unchanged.
   - **0 Install.** The coach path needs a channel carrying the `mcp` extra: uv/pipx/pip with
     `[mcp]`, Homebrew, or Docker. The `.deb`/`.rpm` packages cannot carry it (`docs/INSTALL.md`).
     On those the agent offers two choices: use the appendix, or SWITCH channel (remove the
     package, install from uv/pipx/Homebrew with `[mcp]`) **(owner)**. Never both side by side:
     two `job-sluice` executables would share one config and state at possibly different
     versions, so a key one version refuses breaks the other, and `command -v` could register
     the one without `mcp`.
   - **1 Register.** `claude mcp get job-sluice` first. If absent, explain what `--write` adds,
     including that its tools will be present in every Claude Code session, and ask. Then, by
     channel:
     - host install (uv, pipx, pip, Homebrew): a runnable two-line block,
       ```bash
       JOB_SLUICE=$(command -v job-sluice)
       claude mcp add --scope user --transport stdio job-sluice -- "$JOB_SLUICE" mcp serve --write
       ```
       preceded by two checks: `JOB_SLUICE` is non-empty, and a probe shows this executable
       can serve MCP, i.e. has the `mcp` extra. The probe must be measured to DISCRIMINATE an
       install with the extra from one without (the plan picks it; `mcp serve --help` likely
       cannot, since argparse answers before the lazy `mcp` import). Either check failing stops
       the step with a report, before anything is registered. Absolute path because the docs do
       not state the PATH a stdio server inherits.
     - Docker: the compose file already defines an `mcp` service (`stdin_open: true`, command
       `mcp serve`, no `--write`), so the registration overrides its command:
       ```bash
       claude mcp add --scope user --transport stdio job-sluice -- docker compose -f "$SLUICE_COMPOSE" run --rm -T mcp mcp serve --write
       ```
       where `SLUICE_COMPOSE` is the absolute path of the user's compose file, set on the line
       before. `-T` because a stdio server must not get a TTY; measured before it ships. The
       compose file pins `VAULT_DIR`, so the coach reports the vault as decided by the
       environment.

     No `VAULT_DIR` on a host registration: the coach agrees the vault and saves `vault_dir`. An
     existing read-only registration is reported: the coach can interview and research, not
     save, until re-registered. Links to MCP.md's install section for other clients.
   - **2 Hand over.** The agent stops and tells the user: exit, `claude --continue` in the same
     directory (fact 1), `/mcp` shows `job-sluice` connected, type
     `/mcp__job-sluice__career_interview`, then say what they want in the next message. When the
     coach's hand-off ends and the user says carry on, the agent resumes at step 3. The coach's
     closing says evidence is the user's step; the doc reconciles that by having the agent only
     propose, and the human verify.
   - **3 Vault check, then CV Layout.** Before writing anything, the agent confirms which vault
     the server uses: call `setup_status`. Stop while `vault.is_default` is true, or while the
     configured `vault_dir` is neither absolute nor `~`-anchored (a hand-typed relative value
     passes `is_default` but resolves against the server's launch directory): anything written
     would land where nothing else reads it. The remedy is the coach's own (`DEFAULT_VAULT` in
     `sluice/onboard/review.py`, `open.md`): set `vault_dir` in the config by hand, then
     restart the server; the doc states that one remedy and no other. Otherwise get the path
     the way the coach's review phase does: the `vault_dir` the user agreed, said back verbatim,
     or `job-sluice doctor` run with the same `VAULT_DIR` the server was registered with. On
     Docker the host vault is the `SLUICE_VAULT` value, resolved against the compose file's
     directory (`./vault` there when unset). Then the CV Layout interview, unchanged: create
     only if absent, otherwise show a diff, invent no value.
   - **4 Evidence.** With the MCP server: `propose_evidence`. Without it (the appendix path):
     `job-sluice experience add`, as today. Today's `Tools:`/`Skills:` and `--metrics` guidance
     kept in substance for both. Then **stop and hand the decision back**: call
     `verify_evidence` so the user ticks the form, or have them run `job-sluice experience
     verify`. The "one verified entry composes" paragraph kept.
   - **5 Backend, 6 Camofox, 7 First run.** As today, except searches belong to the coach on the
     main path: the user re-types `/mcp__job-sluice__career_interview` and hands the coach a
     search address, which it saves with a playback and a yes. On the appendix path, the
     appendix's `sources.<id>.searches` instruction applies. The `EXAMPLE-SEARCH(n/m)` tag
     paragraph stays.
   - **Docker: one environment.** On the Docker channel every CLI command after the hand-over
     (`doctor`, `experience add`/`verify`, `ingest`, `triage`, `cv`, `leads add`) runs through
     the same compose project, `docker compose -f "$SLUICE_COMPOSE" run --rm job-sluice ...`,
     never a host binary: a host `ingest run` would read a config with no `vault_dir`, write
     leads into a stray `./vault` and record them in the host's `seen.db`, suppressing them for
     good once the real vault is in use (the #81 harm). `experience verify` runs without `-T`,
     since it asks `[y/N]` on a terminal.
   - **8 Hand back.** As today, minus what the coach already reported.
5. **Other MCP clients.** One paragraph: the server command is the same everywhere; registration
   per client is in MCP.md; no MCP prompts → the appendix; no form elicitation → the human runs
   `job-sluice experience verify`.
6. **Appendix: without MCP prompts.** Today's `init` step (with the tty caveat), Judging Profile
   and Candidate Profile interview steps, condensed, and today's `sources.<id>.searches`
   instruction (the coach owns searches only on the main path); rejoins at step 3's CV Layout.
   The vault check is skipped only when no MCP server is registered, and every later command
   runs in the same environment (`SLUICE_CONFIG`, `VAULT_DIR`) that ran `init`; the appendix
   says both.
7. **Things that look like bugs.** Unchanged, plus: `setup_save` reporting `stale` because the
   user edited a note or the config mid-session (re-read with `setup_status`, play back again).

## Per-client install (`docs/MCP.md`)

A new "Install in your client" section, the single home for registration snippets; AI-SETUP
step 1 carries the Claude Code host line verbatim. One `###` heading per client, named exactly:
Claude Code, Docker (Claude Code), opencode, Claude Desktop, Cursor, VS Code, Codex, Gemini CLI.
Each entry: the snippet or config block, where that config lives (`~`- or `%APPDATA%`-relative,
never an absolute home path), and a capability line: MCP prompts (the coach) yes/no and form
elicitation (the `verify_evidence` form) yes/no, each "no" naming its fallback, plus the client
version and date measured. A client that could not be measured is left out, not guessed.

**Snippets are written, not copied.** Every snippet's command is ONE placeholder token held in a
single test constant per format: `"$JOB_SLUICE"` in shell (set by the `command -v` line above
it), and one fixed string value in JSON/TOML config blocks. The Docker entry's host paths (the
compose `-f` value, the vault mount) are placeholders too (`"$SLUICE_COMPOSE"`,
`SLUICE_VAULT`). No env block beyond documented placeholder keys. Never transcribed from a
measured config, which can carry the measuring machine's paths and other registered servers.

**One Claude Code form.** MCP.md's existing bare `claude mcp add job-sluice -- job-sluice mcp
serve` lines become the scoped, placeholder form, so the doc never shows two Claude Code
registrations.

**`--scope user` everywhere.** Every `claude mcp add` in a shipped doc gains `--scope user`:
AI-SETUP, MCP.md, README (also the PyPI description), USAGE.md's career-coach paragraph, and the
`build_server` comment in `sluice/mcpserver.py`.

ChatGPT gets one sentence stated as a fact about sluice: sluice serves over stdio only, so a client
that connects only to remote servers cannot use it.

### Measurement protocol

For each client: register a server whose `SLUICE_CONFIG`, `VAULT_DIR`, `XDG_STATE_HOME`,
`XDG_CACHE_HOME` and `XDG_CONFIG_HOME` all point into a scratch sandbox (a registration that
redirects only the first two reads the real cache and state). Before editing any client config
file, copy it; after the measurement, restore from the copy and diff to confirm byte-identical
(prefer a client-supported isolated profile or config directory where one exists, and a client's
own CLI over a hand edit). Record: connects; tools listed (read-only and `--write`); coach prompt
reachable and how it is invoked; `verify_evidence` form shown or the CLI fallback returned. For
Docker: a `docker compose ... run` registration with stdin attached and no TTY, measured the same
way against a sandboxed compose project.

Who drives each GUI client (the owner, or screen-driving with the owner's OK), and how a client not
installed locally is obtained, is settled in the plan.

## The `focus` fix

Remove the `career_interview` prompt's argument. The coach's opening already asks what the user
came for.

- `sluice/mcpserver.py`: the prompt takes no argument; its description stops offering one.
- `sluice/onboard/coach/__init__.py`: `assemble_prompt` loses `focus`; `FOCUS_NOTE` and its
  quoting go (and their entry in `tests/onboard_prose.py`).
- `sluice/onboard/coach/open.md`: "The focus" section becomes: ask what they came for, and let
  the answer choose the path (discovery vs. one setting), checking the reading before acting.
- `sluice/onboard/coach/handoff.md`, `docs/MCP.md`, `docs/USAGE.md`: drop "optionally followed
  by what you want"; say to type the command, then say what you want.
- `scripts/coach_eval/run.py` + personas: the first turn sends the bare slash command; the
  persona's `focus` (field kept, so personas keep their intent and `test_personas_are_synthetic`
  keeps sweeping it) is the user's next message, verbatim.
- Commit type `fix(mcp)`, no `!`: nothing imports sluice.

## README

"Let an AI set it up for you": in Claude Code you get the career coach in-session; other agents
follow the same doc through its fallback. "Three stay yours" is unchanged — the coach's saves need
a chat yes, a consent rule rather than a fourth reserved act. The MCP server section's
`claude mcp add` gains `--scope user`.

## Tests

Existing bindings in `tests/test_ai_setup_contract.py` stay (rule headings and count, no bulk
verify flag, verify only at `--write`, no `verified` field, Camofox commands; AI-SETUP's fences
stay `bash` so `_shell_blocks` keeps seeing them). New or changed:

- **Prompt.** The doc names `career_interview`; `build_server` registers a prompt by that name at
  both privilege levels (scope: the prompt list is non-empty); it declares no arguments
  (`tests/functional/test_mcp_contract.py`).
- **Coach neutrality kept.** `test_coach_prompt.py::test_the_assembled_prompt_names_no_preference`
  stays, parametrised on `write` only; only the focus axis and the multi-line focus quoting test
  go, with the mechanism.
- **Eval harness** (`tests/test_coach_eval.py`): the first turn's message is exactly the bare
  slash command, and the persona's focus arrives verbatim as the next user message.
- **Human verifies.** Sliced by heading: the division-of-labour table's verify row, matched by
  its cells (never / only they can), and step 4's "have them run `job-sluice experience verify`"
  inside step 4's section. Witnessed by deleting only that row or sentence.
- **Vault check.** Inside step 3's section only: `setup_status`, `vault.is_default` and a stop
  instruction. Witnessed by deleting only that sentence.
- **Docker one-environment.** Inside the Docker paragraph: every post-hand-over command shown
  runs through `docker compose -f "$SLUICE_COMPOSE" run`.
- **Install section, parsed per format.** Each client entry's block is parsed the way that
  client reads it: `shlex` for shell, `json` for JSON, JSON-with-comments stripped explicitly for
  VS Code, `tomllib` for TOML. Assertions:
  - the command EQUALS that format's placeholder constant (positive, so the bare `job-sluice`
    form fails; witnessed by restoring it);
  - for Docker, the prefix tokens equal `docker compose -f "$SLUICE_COMPOSE" run --rm -T <svc>`
    exactly, with `<svc>` read from `docker-compose.yml` as the service whose command is
    `["mcp", "serve"]`; the sluice argv is what follows;
  - the sluice argv is `mcp serve` plus `--write` exactly where a hand-written
    `{client: write}` roster says, and validates against the real `_build_parser()`;
  - no value anywhere in the block (docker/compose flags included) is an absolute path by
    `posixpath.isabs` OR `ntpath.isabs` (drive letter, UNC), nor `~`-prefixed; one witnessed
    row per form.
  Scope: the client headings found equal the roster BY NAME.
- **One Claude Code line.** AI-SETUP's host registration line appears verbatim in MCP.md's
  Claude Code entry.
- **Scope.** Matched as an INVOCATION (`claude mcp add` followed by a flag or a server name),
  in fenced shell blocks AND inline code spans, never as raw text, so MCP.md's prose mention
  ("the name you gave `claude mcp add`") is exempt by shape, not by file. Inputs: every doc on
  `test_docs_claims.py`'s `_DOCS` roster plus `sluice/mcpserver.py` explicitly. Each invocation
  carries `--scope user`; scope check per file: AI-SETUP, MCP.md, README, USAGE.md and
  mcpserver.py each yield at least one invocation.

Each new assertion gets a scope check and is witnessed red by a mutation (move or delete, never
add) before the PR goes up.

## Out of scope

- `job-sluice mcp install` (piece 3b, next).
- Multi-hunt (after 3b).
- ChatGPT / any remote transport; a Claude Desktop `.mcpb` extension.
- A `.deb`/`.rpm` companion package carrying the `mcp` extra (owner: its own later piece). The
  native packages run on the system Python with distro-packaged dependencies, and neither family
  packages `mcp`, so a companion must vendor `mcp` and its tree (some of it compiled), built per
  architecture and possibly per distro Python, and sluice then owns those libraries' security
  updates on that channel. Until then the doc offers the appendix, or switching channel.
- Any eval run (needs the owner's go-ahead; the harness fix is code only).

## Changes from revision 1

| Finding | Resolution |
|---|---|
| INV-1 (post-coach writes, unknown vault) | Step 3 opens with a vault check; test pins it. |
| INV-2 (who verifies) | Verify row and "have them run" kept verbatim; test pins them. |
| INV-3 (real client configs edited) | Copy, restore, byte-identical diff; prefer isolated profiles and client CLIs. |
| INV-4 (searches) | Appendix keeps `sources.<id>.searches`; main path re-types the coach command. |
| INV-5 (user-scope write exposure) | Owner: keep; the agent states it when asking. |
| NEU-1 (snippets carrying real paths) | Placeholders, never copied from a measured config; test rejects absolute paths. |
| NEU-2 (leak test) | Kept, parametrised on `write`. |
| NEU-3 (machine detail in a public spec) | Removed; scheduling is the plan's. |
| SR-1 (Docker, deb/rpm) | Owner: Docker measured; host line refuses on an empty lookup; deb/rpm → appendix. |
| SR-2 (unscoped `claude mcp add` elsewhere) | README, USAGE, the mcpserver comment; test sweeps `_DOCS`. |
| SR-3 (no-`!` reason) | Rests on "nothing imports sluice". |
| TE-1 (snippet test format-blind) | Per-format parsing, write roster, headings by name. |
| TE-2 (leak test, harness untested) | Leak test kept; harness test added. |
| TE-3 (command sweep, two forms, what CI holds) | argv validated against the parser; one Claude Code form; CI vs measurement stated. |

## Changes from revision 2

| Finding | Resolution |
|---|---|
| INV-R2-1 (Docker: host commands split vault from dedup) | Docker one-environment rule; host vault = `SLUICE_VAULT`; test pins it. |
| INV-R2-2 (two remedies) | Step 3 defers to the coach's `DEFAULT_VAULT` remedy (hand-edit, restart). |
| INV-R2-3 (relative `vault_dir`) | Treated as unresolved in step 3; appendix states its scoping. |
| NEU2-1 (Windows paths) | `ntpath.isabs` + `posixpath.isabs`, `~`; one witnessed row per form. |
| NEU2-2 (role phrase) | Neutral probe phrase. |
| NEU2-3 (Docker host paths) | Compose path and vault mount are placeholders; check covers the whole argv. |
| SR2-1 (Docker vs the real compose file) | `mcp` service named, `-T`, command override; Docker prefix rule in the test. |
| SR2-2 (two `job-sluice` on PATH) | Owner: switch channel, never side by side; discriminating `mcp` probe before registering. |
| SR2-3 (MCP-only steps 4/7 on the appendix path) | `experience add` branch; appendix searches pointer; vault-check scoping. |
| TE-R2-1 (placeholder splits; Docker argv) | One-token placeholder constants, command EQUALS it; Docker prefix pinned. |
| TE-R2-2 (`--scope` sweep engine) | Invocation shape in fences and inline code; `mcpserver.py` explicit; per-file scope. |
| TE-R2-3 (unanchored prose pins) | Sliced by heading / table row; witnessed by deleting only that text. |
