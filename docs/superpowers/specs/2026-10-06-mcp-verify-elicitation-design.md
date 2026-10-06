# MCP evidence verification through elicitation — design

Status: draft for review, 2026-10-06. This is piece 1 of 4 in "drive sluice from Claude Code".
The others, each with its own spec: (2) pipeline tools over MCP, (3) setup tools over MCP,
(4) a rewrite of `docs/AI-SETUP.md` around one Claude Code session.

## Why

`docs/AI-SETUP.md` assumes the human sits at a separate terminal. Verifying evidence is the
hardest stop: an agent driving sluice through MCP has to tell the user to leave the session
and run `job-sluice experience verify`, which asks `[y/N]` once per entry. A corpus built
from one CV is easily 20–40 entries.

Verification is the one operation that makes an entry citable by the CV fabrication gate.
Its trust root is that a HUMAN approved the exact bytes, and that root must survive.
MCP elicitation gives us a way to keep it inside the session: the server asks the CLIENT to
put a form in front of the user, and the model driving the session neither sees the form
nor answers it.

## What the user gets

One tool, `verify_evidence(kind, names=None)`, registered only under `mcp serve --write`.

1. The model calls it after proposing entries (`propose_evidence`).
2. The server collects the pending entries for `kind` (all of them, or the ones `names`
   picks) and reads each one's exact stored text.
3. The client shows ONE form: the full text of every entry, a checkbox per entry ticked
   by default, and Accept / Decline / Cancel buttons.
4. The user unticks anything wrong and clicks Accept once.
5. Each ticked entry is promoted through `Store.verify_evidence(..., reviewed=<the exact
   text shown>)`. An entry edited since the form was built is skipped and reported, not
   verified.

That is one click for a typical corpus, not one per entry.

## Decisions

**D1. The trust root is "what the client puts in front of the human", stated as such.**
The MCP spec lets a client answer an elicitation however it likes; the `mcp` library's own
docstring says a client "might ... automatically generat[e] a response". We do not claim
more than that. In Claude Code, the form goes to the user as a dialog, and the only
automatic answerer is an `Elicitation` hook the user wrote themselves. That is the user's
own config acting for them, which is within the boundary `.rulesync/rules/CLAUDE.md`
already draws ("what is inside that vault is the user's").

**D2. Refuse rather than fall back when the client cannot elicit.** If the client did not
declare the `elicitation` capability at initialise time, the tool writes nothing and
returns `{"outcome": "unsupported_client", "detail": "... run `job-sluice <kind> verify`
in a terminal"}`. There is no "confirm" argument the model could pass instead, because
that would let the model answer for the human (option B, rejected).

**D3. Batch, not per-entry.** The old "no bulk" rule exists so that nothing becomes citable
unless a human saw it. A form showing every byte keeps that property. What stays
forbidden: verifying anything the form did not display IN FULL.

**D4. Never truncate.** The CAS compares against what was shown, so a truncated display
would certify unseen bytes. The batch is split into several forms when the combined text
exceeds a size budget (a module constant). An entry larger than the budget on its own
gets a form to itself. Forms are shown one after another inside the same tool call.
Declining or cancelling one form abandons that form and every later one; anything already
accepted stays promoted, and the report says which.

**D5. Checkbox keys are positional, not titles.** Form fields are `entry_1 … entry_n`, each
with the entry's title as its description. A title is user- or model-supplied text and
does not belong in a schema key. The mapping from key back to `(title, reviewed text)`
lives only on the server and never round-trips through the client.

**D6. One promotion loop, shared with the CLI.** `Sluice.verify_evidence_interactive` is
split into two facade methods, and the CLI path is rebuilt on them:

- `pending_for_review(kind, only=None) -> (list[(title, reviewed_text)], report)`: reads
  the pending set, applies the `only` filter (keeping its `not_found` reporting), and
  isolates per-entry read failures into `report["failed"]`. This is today's loop with the
  confirm step taken out.
- `promote_reviewed(kind, approved, report, today=None) -> report`: calls
  `store.verify_evidence` for each `(title, reviewed)` and records `promoted` /
  `unchanged` / `failed` per entry, isolated the same way as today.

`verify_evidence_interactive` keeps its signature and report shape and becomes "prepare,
ask per entry, promote", so the CLI's behaviour does not change. The MCP tool is "prepare,
one form per batch, promote". There is no second writer: both reach the store only through
`Store.verify_evidence`.

**D7. Async at the tool, thread for the store.** The tool function is `async` because
`ctx.elicit` is. The store reads and writes run through `anyio.to_thread.run_sync`, keeping
the module's existing rule that blocking work never runs on the event loop.

**D8. `names` filters only.** Like the CLI's `--id`, it narrows the queue and never
pre-approves anything. An unknown name is reported in `not_found`, never silently absorbed.

**D9. Report.** The tool returns
`{"outcome": "ok" | "declined" | "cancelled" | "unsupported_client" | "nothing_pending",
"promoted": [...], "unchanged": [...], "skipped": [...], "failed": [[title, reason]...],
"not_found": [...]}`. `skipped` holds the entries the user unticked, plus every entry on a
form that was declined or cancelled, or that was never shown because an earlier form was.
Wording about what verification buys is keyed on `cited_by_gate`, as everywhere else.

## Testing

All offline, through `mcp.Client` in memory with an `elicitation_callback`:

- **Roster.** At `--write`, exactly one verify-shaped tool, named `verify_evidence`. At
  read-only, none. The existing substring sweeps are narrowed to allow only that exact name
  at `--write`, and continue to forbid every other `verify`/`propose`-shaped name at either
  level.
- **No elicitation capability →** `unsupported_client` and zero writes, checked by
  comparing the vault's file listing before and after.
- **Accept with all ticked →** every entry promoted. **Accept with some unticked →** only
  the ticked ones. **Decline / Cancel →** nothing.
- **CAS.** The callback edits an entry on disk before accepting; that entry is reported
  `unchanged` and stays unverified. This is the row that proves the check binds to the
  bytes shown.
- **The bytes shown are the bytes compared.** The callback captures the form message and
  asserts it contains each `reviewed` text verbatim and in full.
- **Size split.** A corpus over the budget produces more than one form; declining the
  second leaves the first's promotions in place and puts the rest under `skipped`.
- **CLI unchanged.** The existing `tests/test_evidence_cli.py` suite stays green against the
  refactored facade.
- Mutation witnesses per the repo's method: delete the capability check, delete the
  `reviewed=` threading, and replace the positional keys with titles; each must go red.

## Prose that has to change in the same PR

Every place that currently says MCP has no verify tool (found by grepping for the claim,
not the code). `test_ai_setup_contract.py` pins the AI-SETUP wording, so it changes with it.

- `.rulesync/rules/CLAUDE.md`, the "Citability has ONE writer" section: the MCP tool is a
  second ENTRY POINT to the one writer, gated on elicitation. The list of refusals has to be
  restated as the ones this tool keeps (D2–D5).
- `sluice/mcpserver.py`: the module docstring and the `list_evidence` / `propose_evidence`
  docstrings, including the descriptions registered with the server.
- `docs/AI-SETUP.md` rule 2 and its division-of-labour table, plus
  `tests/test_ai_setup_contract.py`. A minimal change only; piece 4 rewrites the file.
- `docs/ARCHITECTURE.md` and `docs/USAGE.md`, wherever they describe the MCP tool roster.

## Before merge

The automated tests prove our side of the protocol; they cannot prove what Claude Code
renders. Before merge, run one live check: `claude mcp add` the branch's server, propose
two entries, call `verify_evidence`, untick one, accept, and confirm that exactly one
entry was promoted. This also checks that Claude Code renders boolean fields as
checkboxes with their defaults. If it does not, D3's "ticked by default" is revisited
before merge.

Also check that `ctx.elicit` exists with this signature at the `mcp>=2.0.0` floor as well
as at the `[test]` pin (`2.2.0`). If it doesn't, raise the floor.

## Out of scope

- Any other elicitation (setup interview, `cv_signoff`): pieces 3 and onward.
- Permission allowlisting in Claude Code's settings: piece 4's docs.
- Changelog: `feat(mcp): ...`, not breaking — no existing tool or config changes meaning.
