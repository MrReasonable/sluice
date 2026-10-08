# Career-coach evals

A dev-only harness that plays the `career_interview` coach against model-simulated users and
scores each transcript. It spends the owner's Claude Max allowance and talks to live web
search, so **CI never runs it** and nothing under `tests/` invokes `claude`. The tests cover
argument and command construction, the isolation checks and the rubric only.

## Running

**No eval run happens without the owner's explicit go-ahead.** The evals spend the owner's
Claude Max allowance. The first run is ONE persona; its measured cost (sessions, duration) is
reported before any further run, and `--all` comes only after that report and a fresh go-ahead.

```bash
python -m scripts.coach_eval.run --persona career-changer --out "$(mktemp -d)"
python -m scripts.coach_eval.run --all --out "$(mktemp -d)"
```

Re-grade a SAVED run without re-running the coach, optionally with another grader model:

```bash
python -m scripts.coach_eval.run --regrade DIR --grader-model haiku
python -m scripts.coach_eval.run --persona career-changer --grader-model haiku --out "$(mktemp -d)"
```

`--regrade DIR` takes each `<id>.transcript.txt` + `<id>.events.jsonl` pair in DIR, builds the
grader prompt through the same function a live run uses (`run.grader_prompt`, called by
`run.grade`), and writes
`<id>.regrade-<model>.json` into DIR. It spends only the grader call: no coach, simulated user
or MCP server runs. `--grader-model` (default `sonnet`) applies to live runs and regrades alike,
so graders can be compared on identical transcripts. `--regrade` excludes `--persona`/`--all`.

`--out` must be outside the repository (the run refuses otherwise): transcripts carry web
research and model-played users. Per persona it writes `<id>.transcript.txt` and
`<id>.scorecard.json`, and copies every note and config file the run left in its sandbox into
`<id>.files/` (listed under `files` in the scorecard): with no form to cancel, a run's saves
really land, and those files are the result to open in Obsidian. Each `claude` call has a timeout and a hung one fails the persona loudly; an empty simulated-user reply stops the loop and is recorded as `failure` in the scorecard.

Models (owner's budget ruling, 2026-10-07): coach `sonnet`, simulated user `haiku`, grader
`sonnet`. The grader was `haiku` at first. It moved to `sonnet` the same day, on the owner's
decision after regrading runs 2-5 with both and cross-checking them in a different model
family. Haiku had scored `asked_before_proposing` on runs 2 and 3, where the save tool (then
`setup_review`) was never called.
Personas live in `personas/*.json`; each has a turn cap (`max_turns`, default 12). The three scenario personas (`hesitant`, `edited-mid-session`, `retired-board`) take 20:
measured 2026-10-08, all three reached the last settings group at message 12, before the
playback their scenario is about, because each adds turns of its own (a second save question,
a stale save and a re-read, a refused board). A run costs about $0.04 a coach message.

## Scenarios

Three personas script a turn of events into the run, named by the optional `scenario` field
(`personas.SCENARIOS`; a persona without one runs exactly as before, and a misspelt one is
refused when the personas load). Each keeps the default cap of 12 coach messages. Run one like
any persona, and like any run only with the owner's go-ahead:

```bash
python -m scripts.coach_eval.run --persona hesitant --out "$(mktemp -d)"
python -m scripts.coach_eval.run --persona edited-mid-session --out "$(mktemp -d)"
python -m scripts.coach_eval.run --persona retired-board --out "$(mktemp -d)"
```

- `hesitant`: the simulated user is told to answer the FIRST save question with exactly
  `Maybe. I'm not sure yet.` (`personas.HESITANT_LINE`) and to say yes when asked again.
  `no_save_on_maybe`: no successful `setup_save` call (a `stale` one included: it is still an
  attempt) in the coach message answering that line. `saved_after_yes`: a `setup_save` whose
  outcome is `completed` comes in a later coach message.
- `edited-mid-session`: straight after the first coach message in which `setup_status`
  succeeded, the harness itself appends `Edited by hand during the session.`
  (`personas.EDIT_MARKER`) to the Judging Profile in the vault the sandboxed server reads
  (`run.sandbox_vault`: the env vault, else the config's `vault_dir`, else `./vault` in the
  server's working directory), creating it if absent. It does this once.
  `stale_reported`: every `setup_save` sent with a version read before the edit came back
  `stale` (not exercised when the coach re-read `setup_status` before its first save after the
  edit, since that save was never due one). `status_reread_after_stale`: a successful
  `setup_status` comes after the first `stale` result and before the next save call.
  `edit_survives`: the copied note in `<id>.files/` still holds the marker.
- `retired-board`: the simulated user asks for a search on "a job board you used to use"; the
  harness names it in the user's prompt as the registry's first board that ships disabled
  (`run.retired_board_id`, read at run time and printed in the scorecard as `retired_board`; the
  run refuses when none is disabled). `retired_not_written`: no `setup_save` row on that board
  reports `written`, and the copied config has no key or whole value equal to its id. Not
  exercised until the board's id appears in a user message or a save.

The scenario checks are on every scorecard, and read `not exercised` without their scenario
or when the scripted event never happened (the user never said the line, no save followed the
edit).

## Cross-checking the grader

A grader from the same family as the coach can share its blind spots, so at a milestone (a
playbook change worth trusting, or before shipping) compare it with a model from another
family. This is not done for every run. The other model gets exactly the prompt the grader got:

```bash
python -m scripts.coach_eval.run --print-grader-prompt DIR
```

For each saved run in DIR (`<id>.transcript.txt` with `<id>.events.jsonl`), this writes
`<id>.grade-me.txt` beside it, built by `run.grader_prompt`, the same function `run.grade`
sends to the grader. It makes no call at all. Paste the file into the other model, and compare
its scores and notes with `<id>.scorecard.json` or a `--regrade` card. The file holds the
transcript, so it stays outside the repository with the rest of the run. `--print-grader-prompt`
excludes `--persona`, `--all` and `--regrade`.

## What is scored

Deterministic (`rubric.deterministic`, over the tool calls in the stream): `setup_status`
called before the first `setup_save`; every `setup_save` input parses; every `setup_save`
call that sends a Role Brief section also sends its sources section; no change targets
`verified`; every `setup_save` came after a user turn (`save_after_user_turn`: never in the
coach's first message, which answers the opening slash command); every saved value was played
back in an EARLIER coach message (`saves_played_back`: a value, a search's url, or for a clear
its target, found in what the coach said before the message that saved it); no tool call was
denied or errored (`no_tool_denied`); the coach stays within its cap of coach messages (one per
client invocation). Every check counts only SUCCESSFUL calls, meaning a `tool_use` whose
`tool_result` is not `is_error`. Raw events are saved beside the scorecard as
`<id>.events.jsonl`. A failing deterministic check is an input to playbook work, not a build
failure. `saves_played_back` matches text, so a coach that reformats a long section for chat
fails it while having played it back: read the transcript before acting on that one.

Each check's `result` is `pass`, `fail` or `not exercised`. A check whose subject never
happened is `not exercised`, never `pass`: `status_before_save`, `schema_valid`,
`no_verified`, `save_after_user_turn` and `saves_played_back` need at least one successful
`setup_save` call, and `brief_cites_sources` needs a brief section sent in one. The scorecard's
`setup_save_reached` says whether a save was reached at all. Run 2 (career-changer) ended with
no call to the save tool (then `setup_review`) and its four checks read `pass`, which is
indistinguishable from a run whose proposals were all clean.

Graded by the grader (sonnet by default) (1-5 each): `asked_before_proposing`, `role_specific_questions`,
`coaching_quality`, plus free-text notes. These are indicative, not gating. The grader sees only
the conversation, so its prompt states what `setup_save` actually received (from the tool
calls), and `asked_before_proposing` (was every saved value played back and given an explicit
yes) is scored only from those calls: with none, the grader is told to write `not exercised`.
The deterministic `setup_save_reached` is copied into `llm_graded` beside the grades
(overwriting anything the grader wrote there), and when it is false the harness itself sets
`asked_before_proposing` to `not exercised`, whatever the grader returned. Run 3 scored
`asked_before_proposing` 4 with no call to the save tool at all. A grader reply that is not
JSON marks the scorecard `failure`.

## Isolation

The coach client runs `claude --restricted --strict-mcp-config` from an empty directory, so no
project context, settings, plugin or other MCP server loads. The MCP server is started through
`serve.py`, which rebuilds its environment (`isolation.server_env`), refuses to start if any
path variable could still reach the developer's real config, vault or dedup stores
(`isolation.isolation_problems`), and only then exec's `job-sluice mcp serve --write` from an
empty directory inside the sandbox. Every client turn's `system/init` event is checked
(`isolation.check_init_event`): the Claude Code version must be one that was measured, the
only MCP server must be `sluice` and connected, the tool list must be exactly the expected
set, and no non-builtin plugin may be loaded.

No tool in the sandbox can spend: `server_env` unsets every provider credential and base-url
variable (derived from the backend registry in `sluice.core.app`) and removes every PATH
directory in which `claude` resolves, and `serve.py` refuses to start if either is still
present. So no provider key and no `claude` binary reach the server, whatever arguments the
coach passes (`doctor(offline=False)` included). A credential obtained some other way, such as
a keychain or a config file outside the sandbox, is outside this guarantee.

### Re-measurement probe

Run from an EMPTY directory (never from the repository), with `<venv>` the virtualenv holding
`job-sluice`. These are the only two `claude` calls the measurement needs:

```bash
cd "$(mktemp -d)"
cat > mcp.json <<'JSON'
{"mcpServers": {"sluice": {"command": "<venv>/bin/job-sluice", "args": ["mcp", "serve", "--write"]}}}
JSON
claude --version
claude --restricted --strict-mcp-config --tools WebSearch -p 'Reply OK.' \
  --mcp-config mcp.json --output-format stream-json --verbose < /dev/null \
  | python3 -c "import sys,json;[print(json.dumps({k:d.get(k) for k in ('mcp_servers','tools')})) for d in (json.loads(l) for l in sys.stdin if l.startswith('{')) if d.get('subtype')=='init']"
claude --restricted --strict-mcp-config --tools "" -p 'Reply OK.' \
  --output-format stream-json --verbose < /dev/null \
  | python3 -c "import sys,json;[print(d.get('tools')) for d in (json.loads(l) for l in sys.stdin if l.startswith('{')) if d.get('subtype')=='init']"
```

### Measurement: Claude Code 2.1.292, 2026-10-07

1. With `--tools WebSearch`, the init event listed `WebSearch` AND every tool the connected
   server exposes: `mcp__sluice__apply_record`, `create_lead`, `cv_run`, `cv_signoff`,
   `dismiss_lead`, `doctor`, `get_lead`, `health`, `list_evidence`, `list_leads`,
   `propose_evidence`, `setup_review` (the save tool's name then), `setup_status`,
   `verify_evidence`. The server
   showed `status: connected`. So `--tools` restricts only the built-in tools; it does not
   narrow an MCP server's. Left like that the coach could call `cv_run` and the other backend
   callers, which default to the claude-max backend and would shell out to `claude` on the
   owner's allowance. See measurement 3.
2. With `--tools ""` (and no MCP config) the init event listed `[]`: the simulated user and
   the grader have no tools.

Re-run the probe, update the roster in `run.py` and add the new version with its date to
`isolation.MEASURED_VERSIONS` whenever Claude Code is upgraded.

### Measurement 2: Claude Code 2.1.294, 2026-10-08

The re-measurement probe above, unchanged in shape from 2.1.292: the server connects, `--tools
WebSearch` narrows only the built-in tools (every `mcp__sluice__*` tool is still listed, now with
`setup_save` in place of `setup_review`), and `--tools ""` lists no tools.

### Measurement 3: `--disallowedTools` narrows MCP tools (2.1.292, 2026-10-07)

`--disallowedTools` removes tools from availability (`--allowedTools` only pre-approves). The
coach keeps `setup_status`, the save tool (`setup_save`; `setup_review` when this was measured)
and `doctor` (the hand-off playbook calls `doctor`
with its offline default; a coach passing `offline=False` would make live backend calls, which
is the one residual spend path and the reason the playbook says to use the default). Probe, with
`--disallowedTools` naming `mcp__sluice__` + each of apply_record, create_lead, cv_run,
cv_signoff, dismiss_lead, health, list_evidence, list_leads, propose_evidence, verify_evidence,
get_lead, joined by commas:

```
{"mcp_servers": [{"name": "sluice", "status": "connected", "source": "dynamic"}], "tools": ["WebSearch", "mcp__sluice__doctor", "mcp__sluice__setup_review", "mcp__sluice__setup_status"]}
```

`COACH_EXPECTED_TOOLS` is exactly that set, so the init check fails if a removed tool returns.
The disallowed list is not hand-trusted: a test derives the server's roster from its real
`list_tools()` under `--write` and requires kept + disallowed to equal it.

### Measurement 4: approval, and the long-prompt path (2.1.292, 2026-10-07)

The first real run showed `--tools`/`--disallowedTools` control availability only: headless mode
denied `setup_status` and `WebSearch`, the coach said so, and the old checks still scored green
because they counted the denied calls. `coach_args` now also passes `--allowedTools` with exactly
`COACH_EXPECTED_TOOLS`. Probe (coach flags, sandboxed `serve.py`, haiku, asked to call
`setup_status` once): the `tool_result` carried the status JSON (`config_exists: false`, ...) and
the reply was `OK`. A denial is a `tool_result` with `is_error: true`.

`setup_review` was then an input-required elicitation. The first run never reached it (every
call was denied); the harness does not answer it on the user's behalf. See Measurement 5 for
what the headless client did with it.

Grader root cause: not reproduced, because only a 23 KB prompt sent on stdin was probed (the
model returned a codeword placed at its very end, so stdin delivers it intact). The old path put
the whole transcript in argv beside `-p`, and the reply ("I'm ready.") shows the model saw no
transcript. The user-simulator and grader prompts now go on stdin, and the reply is read from the
`result` event.

### Measurement 5: the headless client cancels the form (2.1.292, 2026-10-07)

Eval run 5 (vault-env) was the first to reach `setup_review`. The headless `claude -p` client
has no UI to show the form, so both calls came back `outcome: cancelled` with `detail: nothing
was written`: the units the form held read `declined`, the rest of the batch `not_shown`. So under this harness, a run that reaches the
form can NEVER show a write. The evidence that the form path works is `setup_review_reached`
(the tool was called successfully) together with `schema_valid` (every batch it sent parses),
plus `status_before_review`, `brief_cites_sources` and `no_verified` over what it sent. A
`written` outcome is not scorable here, and its absence is not a failure.

Superseded 2026-10-08: setup is now saved by `setup_save` on a yes in chat, with no form
(docs/superpowers/specs/2026-10-08-setup-chat-confirmation-design.md), so a run's saves land in
its sandbox and `save_after_user_turn`, `saves_played_back` and the copied files score them.
The tool roster in Measurement 3 was taken under the old name; `COACH_EXPECTED_TOOLS` is derived
from the live server, so re-run that probe before the next eval.
