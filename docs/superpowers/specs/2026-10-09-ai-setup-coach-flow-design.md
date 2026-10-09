# AI-SETUP around the career coach, and installing the MCP server per client (design)

Piece 3 of "drive sluice from Claude Code" (piece 1: `2026-10-06-mcp-verify-elicitation-design.md`;
piece 2: `2026-10-07-in-session-setup-career-coach-design.md`, amended by
`2026-10-08-setup-chat-confirmation-design.md`). Owner decisions taken in brainstorming on
2026-10-09 are marked **(owner)**.

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
   `/mcp__sl__career_interview I want to move into data engineering` delivered `focus='I'`.
   Claude Code splits prompt arguments on whitespace, maps them positionally, and drops the rest;
   quoting is undocumented.
   - Consequence 1: a user following MCP.md/USAGE.md/handoff.md's "optionally followed by what
     you want" gets a one-word focus, silently.
   - Consequence 2: `scripts/coach_eval/run.py` sends `"/mcp__sluice__career_interview " +
     p.focus`, and every persona's focus is multi-word. Every eval scenario ran with a one-word
     focus (`"a"`, `"taking"`, ...). No eval conclusion about how the coach reads a focus is valid.
3. **Default `claude mcp add` scope is `local`**: the server loads only in the directory it was
   added from. `--scope user` loads it everywhere (docs: mcp.md, "MCP installation scopes").
4. Docs facts used, not measured: `/mcp` lists connected servers; `--env K=V` must not directly
   precede the server name (put `--transport stdio` between); everything after `--` is the server
   command.

## Doc structure (`docs/AI-SETUP.md`)

1. **Preamble.** As today, plus: the main path is Claude Code with sluice's MCP server; an agent
   whose client cannot show MCP prompts follows the appendix.
2. **The three rules.** Wording kept; mechanisms updated. Rule 1: the coach asks the preference
   questions; outside the coach, ask, and leave anything unanswered unset (the "inferring them is
   the bug" text stays, binding both paths). Rule 2: `propose_evidence` and `experience add`
   propose; the `verify_evidence` form or the CLI verifies. Rule 3 unchanged.
3. **Division of labour.** Judging/Candidate Profile rows become "the coach drafts, the human says
   yes to each save". New human-only row: typing the coach's slash command (an agent cannot start
   an MCP prompt; the docs describe only user execution).
4. **The sequence.** `doctor` after each state-changing step and the exit-code guidance, unchanged.
   - **0 Install.** As today; the install must carry the `mcp` extra (or a packaged channel that
     does — verified per channel while writing, not assumed).
   - **1 Register.** `claude mcp get job-sluice` first. If absent, explain what `--write` adds
     and ask, then:
     `claude mcp add --scope user --transport stdio job-sluice -- "$(command -v job-sluice)" mcp serve --write`.
     Absolute path because the docs do not state the PATH a stdio server inherits. No
     `VAULT_DIR`: the coach agrees the vault and saves `vault_dir`. An existing read-only
     registration is reported: the coach can interview and research, not save, until
     re-registered. Links to MCP.md's install section for other clients.
   - **2 Hand over.** The agent stops and tells the user: exit, `claude --continue` in the same
     directory (fact 1), `/mcp` shows `job-sluice` connected, type
     `/mcp__job-sluice__career_interview`, then say what they want in the next message. When the
     coach's hand-off ends and the user says carry on, the agent resumes at step 3. The coach's
     closing says evidence is the user's step; the doc reconciles that by having the agent only
     propose.
   - **3 CV Layout.** Unchanged (no setup tool covers it): interview, create only if absent,
     otherwise show a diff, invent no value.
   - **4 Evidence.** `propose_evidence` in place of shelling out; today's `Tools:`/`Skills:` and
     `--metrics` guidance kept verbatim in substance; then `verify_evidence` for the form, or the
     CLI. The "one verified entry composes" paragraph kept.
   - **5 Backend, 6 Camofox, 7 First run.** As today, except searches belong to the coach: the
     Camofox step says to bring a search address back to the coach rather than editing
     `sources.<id>.searches`.
   - **8 Hand back.** As today, minus what the coach already reported.
5. **Other MCP clients.** One paragraph: the server command is the same everywhere; registration
   per client is in MCP.md; no MCP prompts → the appendix; no form elicitation → `job-sluice
   experience verify`.
6. **Appendix: without MCP prompts.** Today's `init` step (with the tty caveat), Judging Profile
   and Candidate Profile interview steps, condensed, rejoining at step 3.
7. **Things that look like bugs.** Unchanged, plus: `setup_save` reporting `stale` because the
   user edited a note or the config mid-session (re-read with `setup_status`, play back again).

## Per-client install (`docs/MCP.md`)

A new "Install in your client" section, the single home for registration snippets; AI-SETUP
step 1 carries Claude Code's line verbatim. Each entry: the snippet or config block, where that
config lives, and a measured capability line: MCP prompts (the coach) yes/no, form elicitation
(the `verify_evidence` form) yes/no, each "no" naming its fallback. MCP.md's existing
`claude mcp add` lines gain `--scope user`.

ChatGPT gets one sentence stated as a fact about sluice: sluice serves over stdio only, so a client
that connects only to remote servers cannot use it.

### Measurement protocol

For each client: register a server whose `SLUICE_CONFIG`, `VAULT_DIR`, `XDG_STATE_HOME`,
`XDG_CACHE_HOME` and `XDG_CONFIG_HOME` all point into a scratch sandbox (memory: a hand-written
registration that redirects only the first two reads the owner's real cache and state). Record:
connects; tools listed (read-only and `--write`); coach prompt reachable and how it is invoked;
`verify_evidence` form shown or the CLI fallback returned. Remove the registration afterwards and
confirm with the client's own listing. Record the client version beside each row.

- Claude Code, opencode: headless, by the implementer.
- Claude Desktop, Cursor, VS Code, Codex, Gemini CLI: the implementer writes the config; the GUI
  check is done by the owner or by screen-driving with the owner's OK, decided in the plan. Gemini
  CLI is not installed and is installed for the measurement; Codex has `~/.codex` but no CLI on
  PATH, so which Codex surface is measured is settled in the plan.
- A row that could not be measured is left out, not guessed.

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
- `scripts/coach_eval/run.py` + personas: the persona's `focus` becomes its first message after
  the slash command (the field is kept as the opening line, so personas keep their intent).
- Tests: `test_mcp_contract.py` pins `arguments == []`; `test_coach_prompt.py`'s focus
  parametrisation and multi-line-quoting test are removed with the mechanism.
- Commit type `fix(mcp)`, no `!`: nothing imports sluice, and text after the command is already
  dropped today.

## README

"Let an AI set it up for you": in Claude Code you get the career coach in-session; other agents
follow the same doc through its fallback. "Three stay yours" is unchanged — the coach's saves need
a chat yes, a consent rule rather than a fourth reserved act.

## Tests (`tests/test_ai_setup_contract.py` unless stated)

Existing bindings stay (rule headings and count, no bulk verify flag, verify only at `--write`, no
`verified` field, Camofox commands). New:

- The doc names `career_interview`, and `build_server` registers a prompt by that name at both
  privilege levels (scope: the prompt list is non-empty).
- The prompt declares no arguments.
- AI-SETUP's `claude mcp add` line appears verbatim in MCP.md's install section (scope: both
  extracted through `_shell_blocks`, positive control first).
- Every client snippet in MCP.md's install section runs `mcp serve`, and each `--write` entry
  carries `--write` (scope: the snippet count equals the number of client headings in the
  section).
- AI-SETUP and MCP.md say `--scope user` on every `claude mcp add`.

Each new assertion gets a scope check and is witnessed red by a mutation (move or delete, never
add) before the PR goes up.

## Out of scope

- `job-sluice mcp install` (piece 3b, next).
- Multi-hunt (after 3b).
- ChatGPT / any remote transport; a Claude Desktop `.mcpb` extension.
- Any eval run (needs the owner's go-ahead; the harness fix is code only).
