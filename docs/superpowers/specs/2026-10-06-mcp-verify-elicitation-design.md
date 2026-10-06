# MCP evidence verification through elicitation — design

Status: revision 4, approved 2026-10-06. This is piece 1 of "drive sluice from Claude Code".
The order is 1 (this), then setup in-session (`init` interview, the Judging and Candidate
Profiles, config and searches through tools), then a rewrite of `docs/AI-SETUP.md` around
a single Claude Code session. Pipeline commands as MCP tools come last and are optional:
Claude Code can already run them through its shell, so they do not remove a terminal
hop. After the first three pieces, the user leaves the session only for job-board logins,
Google OAuth consent, and submitting applications.

## What this guards against, and what it doesn't

The verify step exists so that the MODEL cannot accidentally make its own claims citable.
That covers an invented figure, a rounded metric, or an achievement the user never had.
Each of those would let the CV gate certify a lie sent under the user's name. The guard
needs exactly two things:

- **A human sees each entry's text before it becomes citable.**
- **The model cannot say "yes" on the human's behalf.**

It is not a hardened security boundary. It does not defend against a hostile client, a
tampered protocol message, or a user scripting their own approvals. Those are the user's
own tools acting for the user. Revisions 1–3 of this spec hardened against them, and that
work is dropped here deliberately.

## Why

`docs/AI-SETUP.md` makes the user leave the Claude Code session and run
`job-sluice experience verify`, which asks `[y/N]` once per entry. One CV easily yields
20–40 entries. The aim is one dialog, inside the session.

## What the probe found

The probe was a throwaway server on mcp 2.3.0, attached to Claude Code 2.1.291 with
`claude -p --mcp-config <scratch file>`.

- Claude Code speaks protocol `2026-07-28`.
- A **server-pushed** elicitation (`session.elicit_form`) fails with `NoBackChannelError`,
  because that protocol has no back-channel.
- An **input-required result** (SEP-2322) works. The tool returns an
  `InputRequiredResult` carrying a form plus an opaque `request_state`. Claude Code asks
  the user, then retries the same call with the answer and the state echoed back.
  Headless, with no one to ask, it answered `cancel`, which promotes nothing.
- `mcp.Client` in its default `auto` mode drives the same loop in memory, so it can be
  tested offline.
- The mechanism exists at mcp 2.0.0, 2.1.0, 2.2.0 and 2.3.0, so the `>=2.0.0` floor stands.

## Design

**The tool.** `verify_evidence(kind, names=None)`, registered only under
`mcp serve --write`. Its input is exactly `kind` and `names`, with no `confirm`, `yes` or
`approve` argument, so the model has no way to answer for the user. `names` only narrows
which pending entries are offered. Unknown names are reported back.

**First leg (no answer yet).**

1. If the client can't take an input-required elicitation, return `unsupported_client`
   with "run `job-sluice <kind> verify` in a terminal", and write nothing. That means
   either a protocol older than `2026-07-28`, or a client that declared no elicitation
   support. Older clients can't parse the result, so they never receive one.
2. Read the pending entries and their exact text.
3. Build one form:
   - every entry's full text, each in its own code block so markdown in a body shows
     literally;
   - one checkbox per entry (`entry_1 … entry_n`), ticked by default;
   - a sentence saying what accepting does, using the existing `cited_by_gate` wording.
4. If the form would get too long, show what fits and report how many remain. The next
   call shows the rest. An entry too long for a form on its own is left for the CLI.
5. Return an `InputRequiredResult` whose `request_state` is plain JSON: `kind`, plus
   `(entry_k, title, sha256 of the text shown)` per entry.

**Second leg (the client retries with the answer).**

- Decline, cancel, or a missing or unreadable state → nothing is written.
- An entry is approved only if the answer holds an explicit `true` for its key. A missing
  key or an empty answer approves nothing. This is the one guard against a client quietly
  filling in the ticked defaults.
- For each approved entry, the server re-reads the current text. If its hash matches what
  was shown, the entry is promoted through `Store.verify_evidence(..., reviewed=text)`, the
  existing single writer with its existing compare-and-set. If the text changed since the
  form was shown, the entry is reported as changed and left pending.

**Report.** The tool returns `outcome`, `promoted`, `changed`, `skipped`, `failed`,
`remaining` and `not_found`, plus a one-line `detail`. That gives the model enough to tell
the user what happened. Failure reasons use the existing `_evidence_failure_reason`.

**Where code goes.**

- The tool and a few small pure helpers live in `sluice/mcpserver.py`: build the form,
  read the approvals, encode and decode the state.
- One facade method in `sluice/core/app.py`, `promote_reviewed(kind, approved)`, wraps
  `Store.verify_evidence` per entry, isolating failures one at a time, as the CLI loop
  already does.
- The CLI's `verify_evidence_interactive` is untouched.
- `mcp_types` is imported inside `build_server` only, like `mcp`.
- `verify_outcome`'s wording becomes reachable from `mcpserver.py`. The isolation-sweep
  allowlist, or a re-export through `core/app.py`, is whichever the plan finds simplest.

## Testing

All offline: `mcp.Client` in memory with an `elicitation_callback`, synthetic faker
entries, and a `tmp_path` vault. Promotions are checked on disk, not taken from the
report.

- Accept with everything ticked → all promoted.
- Accept with one entry unticked → the others promoted.
- Decline, and cancel → nothing promoted.
- Accept with an empty answer → nothing promoted.
- An entry edited on disk between the two legs → reported changed and not promoted.
- A long corpus → `remaining > 0`, and a second call shows the rest.
- Unsupported clients (a legacy-mode client, and a client with no elicitation callback)
  → `unsupported_client`, nothing written.
- The form message contains each entry's text in full.
- The registered input schema is exactly `{kind, names}`.
- The MCP roster guards are updated to expect exactly this one verify tool under `--write`
  and none without it, rather than being loosened to "anything named verify".

Mutation-check two rows, per the repo's method:

- Delete the explicit-`true` check → the empty-answer row goes red.
- Delete the hash comparison → the edited-entry row goes red. If the store's own
  compare-and-set still catches it, say so in the test.

## Prose that has to change in the same PR

Every statement that "MCP has no verify tool" becomes false. The plan's first task greps
for that claim across `sluice/`, `docs/`, `.rulesync/`, `README.md` and `tests/`, and
works from the output. Known so far:

- the "Citability has ONE writer" section of `.rulesync/rules/CLAUDE.md` (then
  regenerate);
- `sluice/mcpserver.py`'s docstrings and `_PROPOSE_EVIDENCE_PENDING_DETAIL`;
- the `mcp serve --write` help text;
- rule 2 and the division-of-labour table in `docs/AI-SETUP.md`, along with its contract
  test;
- `docs/ARCHITECTURE.md` and `docs/USAGE.md`.

## Before merge

Run one interactive check in Claude Code, against a scratch vault (`job-sluice init
--no-input --vault <tmp dir>`, with a temporary `SLUICE_CONFIG`), never the real one:

1. Propose three synthetic entries, one containing some markdown.
2. Call `verify_evidence`, untick one, and accept.
3. Confirm:
   - exactly the ticked entries are promoted;
   - the markdown shows literally;
   - the boxes start ticked.

## Out of scope

- Legacy-protocol clients.
- Any other elicitation (the setup interview, `cv_signoff`), which belongs to piece 3.
- The Claude Code permission allowlist, which belongs to piece 4's docs.
- Changelog: `feat(mcp): ...`, not breaking.
