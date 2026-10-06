# MCP evidence verification through elicitation — design

Status: revision 2, 2026-10-06, after the first `/review-plan` round (24 findings, 0 Critical).
This is piece 1 of 4 in "drive sluice from Claude Code". The others, each with its own spec:
(2) pipeline tools over MCP, (3) setup tools over MCP, (4) a rewrite of `docs/AI-SETUP.md`
around one Claude Code session.

Three decisions here are the author's provisional rulings on questions the review flagged
for the owner. Each is marked **[RULING]** and can be overridden.

## Why

`docs/AI-SETUP.md` assumes the human sits at a separate terminal. Verifying evidence is the
hardest stop: an agent driving sluice through MCP has to tell the user to leave the session
and run `job-sluice experience verify`, which asks `[y/N]` once per entry. A corpus built
from one CV is easily 20–40 entries.

Verification is the one operation that makes an entry citable by the CV fabrication gate.
Its trust root is that a HUMAN approved the exact bytes, and that root must survive.
MCP elicitation keeps it inside the session: the server asks the CLIENT to put a form in
front of the user, and the model driving the session neither sees the form nor answers it.

## What the user gets

One tool, `verify_evidence(kind, names=None)`, registered only under `mcp serve --write`.

1. The model calls it after proposing entries (`propose_evidence`).
2. The server selects the pending entries for `kind` (all of them, or those `names` picks),
   reads each one's exact stored text, and sets aside any it will not display (D4).
3. The client shows a form: every displayable entry in full, fenced and labelled, with one
   checkbox per entry, ticked by default, and Accept / Decline / Cancel.
4. The user unticks anything wrong and clicks Accept once.
5. Each entry the client returned as an explicit `true` is promoted through
   `Store.verify_evidence(..., reviewed=<the exact text shown>)`. An entry edited since the
   form was built is reported as changed, not verified.

## Decisions

**D1. The trust root is "what the client puts in front of the human", stated as such.**
[RULING] The MCP spec lets a client answer an elicitation however it likes, and the `mcp`
library says a client "might ... automatically generat[e] a response". In Claude Code the
form goes to the user as a dialog. Anything that can answer it instead is CLIENT
configuration: an `Elicitation` hook from the user's settings, a project's tracked
`.claude/settings.json`, or a plugin. Another MCP client may let a model answer. And unlike
the CLI, the model now STARTS verification; it still cannot complete it. This is accepted as
a stated residual and written into `.rulesync/rules/CLAUDE.md` in those words. We do not
claim the client is incapable of auto-answering. To keep a rubber-stamp from being
uninformed, the form's own text states what accepting buys, worded from `cited_by_gate` as
everywhere else (D9).

**D2. Refuse rather than fall back when the server cannot elicit.** Two checks, both before
anything is promoted:

- At call time, the client must have declared the `elicitation` capability with form mode
  (a URL-mode-only client fails this).
- The first form is sent before any promotion happens. If sending it raises the library's
  `MCPError` (which `NoBackChannelError` subclasses), the tool returns
  `unsupported_client`. This is the case where a declared capability still cannot reach the
  user: measured on mcp 2.3.0, a client in the default `auto` mode (the 2026-07-28 protocol)
  declares the capability, but `elicit` raises `NoBackChannelError`. [RULING] We accept that
  this tool is unavailable on that transport until the library gives it a back-channel.

`unsupported_client` writes nothing and its detail says to run `job-sluice <kind> verify`
in a terminal. There is no argument the model could pass instead: the tool's input is
exactly `{kind, names}`, pinned by a test (see Testing).

**D3. Batch, ticked by default, and only an explicit `true` approves.** [RULING] This keeps
the one-click flow and closes the hole all three reviewers found. The form is sent with
`session.elicit_form(message, requested_schema)`: a raw JSON schema in, the client's raw
content dict out. No pydantic model is involved, so nothing on our side fills in a missing
key. The schema gives each `entry_k` `"type": "boolean", "default": true` so the client
renders it ticked. The server approves `entry_k` only when the returned content contains
that key with the value `True` (identity, not truthiness). These approve nothing: a missing
key, `false`, a non-boolean, an unknown key, and an Accept with empty content.

**D4. Show in full or not at all.** The check compares against what was shown, so the
display must be exactly the stored bytes, unambiguously delimited:

- **Refused, never shown** (reported under `refused` with a fixed reason, still pending):
  - an entry whose text contains a character `core/safeout.py::is_control` rejects, or any
    Unicode format character (category `Cf`: bidi overrides, zero-width characters);
  - an entry containing the fence prefix (below);
  - an entry longer than `_VERIFY_FORM_BUDGET` on its own. It must go through the CLI;
    showing it alone would still risk a client cutting it short.
- **Fenced.** Each entry is shown between `──── entry_k: <title> ────` and
  `──── end entry_k ────`. The title is subject to the same refusals as the body.
- **Split, never truncated.** Entries are packed in order into forms whose message stays
  within `_VERIFY_FORM_BUDGET` characters. `_VERIFY_FORM_BUDGET` is a module constant, not
  config: it bounds what a client form can carry and is not a user preference. Its initial
  value is 8000 and the live check confirms or lowers it (see Before merge).
- **Forms are shown in order within one call.** Declining or cancelling one form stops; its
  entries and any never-shown entries go to `skipped`. Promotions from earlier accepted
  forms stand, and the report says so (D9).

**D5. Checkbox keys are positional.** Fields are `entry_1 … entry_n` per form, each with the
entry's title as its description. The key → `(title, reviewed text)` map lives only on the
server.

**D6. One promotion loop, shared with the CLI, in three facade methods.**
`Sluice.verify_evidence_interactive` is rebuilt on these, keeping its signature, report
shape and behaviour, including that a non-interactive run reads no entry text:

- `select_pending(kind, only: Sequence[str] | None) -> (titles, not_found)`. This is today's
  filter, widened from one string to a sequence. Each requested name is matched verbatim or
  through `evidence_slug`, and every unmatched name is listed in `not_found`. The CLI passes
  `[args.id]` or `None`.
- `read_for_review(kind, titles) -> (list[(title, text)], failed)`. Reads each entry's text
  fresh through `Store.read_pending_evidence_text`, isolating each failure.
- `promote_reviewed(kind, approved, today=None) -> {"promoted", "unchanged", "failed"}`.
  This returns a FRESH partial report and mutates nothing it was handed. It calls
  `Store.verify_evidence` per entry, isolating each failure. It remains the only path from
  either front-end to `verified:`.

**D7. Where the code lives.** The decision logic sits in pure module-level helpers in
`sluice/mcpserver.py`, which tests drive without mcp, as every existing tool's logic does:

- `_display_refusal(title, text) -> reason | None`
- `_pack_forms(entries, budget) -> list[form]`
- `_form_schema(form)`
- `_approved_from(content, form) -> list[(title, text)]` (D3's rule)
- `_verify_report(...)` (outcome accounting)

The registered tool is the only `async` function. It runs `select_pending` and
`read_for_review` in a worker thread, sends each form, and passes each accepted form's
approvals to `promote_reviewed`, also in a worker thread. `anyio` (an `mcp` dependency) is
imported inside `build_server`, beside `mcp`, and nowhere else. The `build_server`
docstring's statement that tools are synchronous and threaded is updated to name the one
exception.

**D8. `names` filters only.** It narrows the queue and never pre-approves anything. Unknown
names come back in `not_found`.

**D9. Report and wording.** The tool returns:

```
{"outcome": "completed" | "stopped" | "unsupported_client" | "nothing_pending",
 "stopped_by": "decline" | "cancel" | "error" | null,
 "promoted": [title...], "unchanged": [...], "skipped": [...],
 "refused": [[title, reason]...], "failed": [[title, reason]...],
 "not_found": [...], "detail": str}
```

`outcome` never reads as "nothing happened" while `promoted` is non-empty. `detail` states
the counts in one sentence and, when `stopped`, says that the earlier forms' promotions
stand. Any `failed` or `refused` reason is drawn from a fixed vocabulary keyed on exception
type ("already verified", "could not be read", "a symlink was refused", "contains
characters a form cannot show safely", "too long for a form"). It is never `str(exc)`,
because the vault's symlink refusals carry the absolute vault path. The citability wording
used in the form text and in `detail` comes from `verify_outcome`, which moves from
`sluice/evidence/commands.py` to `sluice/core/protocols.py` beside `EvidenceKind` so both
front-ends can import it. The tool result never contains an entry's body.

## Testing

Each behaviour starts as a failing test. Fixtures use the seeded faker titles and bodies
from `tests/conftest.py`; the vault is `tmp_path`. Elicitation is driven through
`mcp.Client(..., mode="legacy", elicitation_callback=...)`, with the mode pinned because the
default `auto` mode cannot elicit (D2). "Writes nothing" is always measured on disk:
`read_evidence(kind)` and the pending listing are compared before and after, never inferred
from the report.

**Pure helpers (no mcp):**
- `_approved_from`: approves only `True`; rejects a missing key, `false`, `1`, `"true"`,
  an unknown key, a title-shaped key, and `{}`.
- `_display_refusal`: refuses each of ESC, CR, DEL, a C1 character, U+202E, U+200B, and
  the fence prefix, in the body and in the title; passes tab and newline.
- `_pack_forms`: preserves order; never splits an entry; an entry over budget alone is
  refused, not packed; total characters per form stay within budget.
- `_form_schema`: keys are exactly `entry_1..n`, even when titles contain spaces,
  punctuation, or text shaped like a key.

**Facade:**
- `select_pending` with several names, some unmatched → `not_found` lists exactly those.
- `read_for_review` isolates one unreadable entry and still returns the rest.
- `promote_reviewed` isolates one failing entry, and returns a fresh dict on each call.
- A non-interactive CLI run never calls `read_pending_evidence_text`; a spy store
  asserts this.
- The existing `tests/test_evidence_cli.py` suite stays green, unchanged.

**Tool, end to end through `mcp.Client`:**
- Accept with everything ticked → all promoted. One explicitly `false` → only the others.
- Accept with `{}` → nothing promoted, on disk. Accept omitting one key → that entry not
  promoted.
- Decline, cancel → nothing promoted, on disk.
- The CAS row: the callback rewrites one entry on disk before accepting. That entry is
  `unchanged` and still pending; the rest are promoted.
- Bytes shown: the callback captures each form's message and asserts every displayed
  entry's stored text appears verbatim between its own fences.
- Split: a corpus over budget gives more than one form. Declining the second leaves the
  first's promotions on disk, `outcome == "stopped"`, and `detail` says the earlier
  promotions stand.
- No elicitation capability (legacy mode, no callback) → `unsupported_client`, nothing
  written. `auto` mode with a callback → `unsupported_client`, nothing written. This is the
  D2 back-channel arm.
- The tool's result contains no entry body and not the vault path. Plant a symlinked
  entry: the `refused`/`failed` reason is the fixed string and has no path.

**Roster and guards.** The existing sweeps are not narrowed to an allowed name. They become
exact-set checks per level:
- At read-only, no verify-shaped tool at all, as today.
- At `--write`, the verify-shaped set equals exactly `{"verify_evidence"}`.

The tool's registered input schema is pinned to exactly the properties `{kind, names}`, so
a `confirm`/`approve`/`yes` argument cannot appear under the allowed name. Updated in step:
- `_EVIDENCE_WRITE_SHAPED_NAMES` and `_EXPECTED_EVIDENCE_WRITE_TOOLS` in
  `tests/test_mcpserver.py`;
- the exact roster `==` and the `"verify" in n` clause in
  `tests/functional/test_mcp_contract.py`;
- `tests/test_ai_setup_contract.py::test_no_mcp_tool_can_verify_evidence_at_any_write_level`,
  whose anti-vacuity plant moves to a name the guard still forbids.

`test_mcp_imported_nowhere_outside_build_server` widens to `anyio`, with a synthetic
module-scope `import anyio` row that proves it fires.

**Mutation witnesses** (delete or move, never add; commit before each):
1. Delete the capability check → the no-capability row goes red.
2. Delete the `MCPError` arm around the first form → the `auto`-mode row goes red.
3. Replace `_approved_from`'s `is True` with a `.get(key, True)` default → the `{}` and
   omitted-key rows go red.
4. Move `reviewed` from the captured text to a fresh re-read after the form → the CAS row
   goes red. That row must fail for this reason, not a `TypeError`.
5. Key the schema by title → the `_form_schema` row goes red.
6. Delete the `Cf` arm of `_display_refusal` → the U+202E and U+200B rows go red.
7. Return `str(exc)` as the reason → the symlink-path row goes red.

## Prose that has to change in the same PR

Found by grepping for the CLAIM, not the code:

- `.rulesync/rules/CLAUDE.md`, "Citability has ONE writer":
  - the "nothing that VERIFIES, at any level" sentence;
  - the "do not read this one as licence to add the verify tool" sentence;
  - the passage naming "an MCP verify tool" as a new trust root, which is rewritten to say
    this IS one, rebuilt with D2–D5, with D1's residual stated.

  Then regenerate.
- `sluice/mcpserver.py`:
  - the module docstring;
  - the `list_evidence` and `propose_evidence` docstrings and registered descriptions;
  - the `build_server` docstring (D7);
  - `_PROPOSE_EVIDENCE_PENDING_DETAIL`, a runtime string the agent receives, which must now
    name `verify_evidence` alongside the CLI command.
- `sluice/cli.py`: the `mcp serve --write` help text.
- `sluice/core/app.py`: the `verify_evidence_interactive` docstring's "a bulk flag is the
  `--verified` hole" argument. It is restated: the CLI still has no bulk flag, and the MCP
  batch is not one, because every entry is displayed in full and approved individually.
- `docs/AI-SETUP.md`: rule 2 and the division-of-labour table, minimally (piece 4 rewrites
  the file). Also `tests/test_ai_setup_contract.py`'s pinned wording.
- `docs/ARCHITECTURE.md`: the "standing property" sentence and the MCP roster.
- `docs/USAGE.md`: "not possible through MCP" and the `--verified`-hole sentence.

## Before merge

The live check runs against a scratch vault (`job-sluice init --no-input --vault
<tmp dir>`, with `SLUICE_CONFIG` pointing at a temporary config). Never the real vault, and
none of its output goes in the PR. Steps:

1. `claude mcp add` the branch's server with `--write`. Propose three synthetic entries,
   one of them sized just under `_VERIFY_FORM_BUDGET`.
2. Call `verify_evidence`, untick one, accept. Exactly two are promoted. The near-budget
   entry is visible to its last character, and checkboxes show ticked by default.
3. Record which transport mode Claude Code uses (D2). If it already uses the `auto`
   protocol, this tool cannot work there today, and that is escalated before merge rather
   than shipped.

Also confirm `session.elicit_form`, `NoBackChannelError`'s base class, and
`check_client_capability` at the `mcp>=2.0.0` floor and at the `[test]` pin `2.2.0`. Raise
the floor if any is missing.

## Out of scope

- Any other elicitation (the setup interview, `cv_signoff`): pieces 3 and onward. No shared
  elicitation helper until piece 3 needs one.
- Permission allowlisting in Claude Code's settings: piece 4's docs.
- Changelog: `feat(mcp): ...`, not breaking. No existing tool or config changes meaning.
