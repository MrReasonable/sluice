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

`--out` must be outside the repository (the run refuses otherwise): transcripts carry web
research and model-played users. Per persona it writes `<id>.transcript.txt` and
`<id>.scorecard.json`. Each `claude` call has a timeout and a hung one fails the persona loudly; an empty simulated-user reply stops the loop and is recorded as `failure` in the scorecard.

Models (owner's budget ruling, 2026-10-07): coach `sonnet`, simulated user and grader `haiku`.
Personas live in `personas/*.json`; each has a turn cap (`max_turns`, default 12).

## What is scored

Deterministic (`rubric.deterministic`, over the tool calls in the stream): `setup_status`
called before the first `setup_review`; every `setup_review` input parses; a proposed brief
records its sources; no change targets `verified`; no tool call was denied or errored
(`no_tool_denied`); the coach stays within its cap of coach messages (one per client
invocation). Every check counts only SUCCESSFUL calls, meaning a `tool_use` whose `tool_result`
is not `is_error`. Raw events are saved beside the scorecard as `<id>.events.jsonl`.
A failing deterministic check is an input to playbook work, not a build failure.

Graded by the Haiku grader (1-5 each): `asked_before_proposing`, `role_specific_questions`,
`coaching_quality`, plus free-text notes. These are indicative, not gating. A grader reply that is not JSON marks the scorecard `failure`.

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
   `propose_evidence`, `setup_review`, `setup_status`, `verify_evidence`. The server
   showed `status: connected`. So `--tools` restricts only the built-in tools; it does not
   narrow an MCP server's. Left like that the coach could call `cv_run` and the other backend
   callers, which default to the claude-max backend and would shell out to `claude` on the
   owner's allowance. See measurement 3.
2. With `--tools ""` (and no MCP config) the init event listed `[]`: the simulated user and
   the grader have no tools.

Re-run the probe, update the roster in `run.py` and add the new version with its date to
`isolation.MEASURED_VERSIONS` whenever Claude Code is upgraded.

### Measurement 3: `--disallowedTools` narrows MCP tools (2.1.292, 2026-10-07)

`--disallowedTools` removes tools from availability (`--allowedTools` only pre-approves). The
coach keeps `setup_status`, `setup_review` and `doctor` (the hand-off playbook calls `doctor`
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

`setup_review` is an input-required elicitation. The first run never reached it (every call was
denied), so how the headless client handles that form is NOT yet observed; the harness does not
answer it on the user's behalf, and the next run's `events.jsonl` is where to look.

Grader root cause: not reproduced, because only a 23 KB prompt sent on stdin was probed (the
model returned a codeword placed at its very end, so stdin delivers it intact). The old path put
the whole transcript in argv beside `-p`, and the reply ("I'm ready.") shows the model saw no
transcript. The user-simulator and grader prompts now go on stdin, and the reply is read from the
`result` event.
