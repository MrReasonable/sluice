# Research follow-ups: a worklist, a standing total, and an asked state (#378)

## Problem

`research` is the judge's verdict for a lead it would shortlist once a human answers one or two
questions. Nearly every such verdict ends in a concrete instruction (`Next: ask recruiter: client
name, comp band`). Nothing delivers those instructions as a list, nothing records whether one was
carried out, and the nightly `triage run` (whose default statuses include `research`) re-judges
each lead to the same verdict because nobody asked. The triage push names at most
`cli._PUSH_NAME_CAP` leads per run, shortlist first, so a growing research queue is seen as a small
shifting sample and never as a total. The owner's vault held over a hundred such leads, the oldest
seven weeks old.

## Decisions (owner, 2026-10-12)

- Scope: a worklist command, the standing total in the triage digest, and an "asked, waiting"
  state that the nightly re-judge skips. Ageing out is NOT rebuilt: `leads expire` already
  dismisses a stale `research` lead once `lead_ttl_days` is set; the worklist points at it.
- An answer from the recruiter FEEDS THE JUDGE: it is recorded in the note, recording it clears
  the waiting state, and the next `triage run` judges the lead with its job description plus the
  answers.

## Design

### Data

| What | Where | Written by |
| --- | --- | --- |
| The verdict's `Next:` instruction | frontmatter `triage_next` | `triage/apply.py`, on every verdict |
| Asked, awaiting a reply | frontmatter `followup_asked: YYYY-MM-DD` | `leads mark-asked`; blanked by `leads answer` and by a verdict on a stamped lead |
| The reply | body section(s) `## Follow-up answers` | `leads answer`, or by hand |

**`triage_next`** sits beside `triage_concerns` (#329): its own key, `triage_`-prefixed because it
is replaced on every verdict and must not collide with a key a person might use, written through
`frontmatter_safe`. It is written on every verdict, blank when the model gave no action, so a stale
instruction never outlives the verdict that produced it. `relevance_notes` keeps its `Next:` prose
unchanged. Existing leads carry the text only in that prose; no parser backfills it. Every
`research` lead is re-judged by the next default `triage run`, which writes the key; until then the
worklist prints `(no Next: recorded yet)`.

Block-value preservation for the two new keys is NOT obtained by adding them to
`core/leads.py::FRAMING_KEYS`. That roster is the CV composer's framing (`cv/engine.py`), the
`needs_signoff` framing entries and MCP's framing label, so a research instruction would reach every
CV compose prompt. `triage/apply.py::_HAND_EDITABLE_KEYS` becomes `frozenset(FRAMING_KEYS) |
{"triage_next", "followup_asked"}`, its comment is corrected to say it is the framing roster PLUS
triage-only keys, and `FRAMING_KEYS` is unchanged.

**A verdict clears a stale stamp, and only a stale one.** When the note `apply_verdict` was handed
carried a non-blank `followup_asked`, the verdict's write also sets it to blank, guarded by
`require_unchanged={"followup_asked": <the value read>}` (the guard the engine already uses for
`role_type`). An awaiting `research` lead is never judged (see Triage), so a judged lead carrying a
stamp is one a human moved off `research`; without the blank, a later `research` verdict would leave
the old stamp and hide the lead again with nobody having asked. The guard covers the race the
rationale alone does not: a `leads mark-asked` landing while that lead's fetch and judge are in
flight changes the value, the verdict's write is refused whole (the lead keeps its status and the
fresh stamp, and is skipped next run as awaiting), and the user's action is never erased. A note
whose snapshot stamp is blank gets no `followup_asked` key from a verdict at all, so judged leads do
not all grow an empty key, and a stamp added mid-run on such a note is untouched by the write.

**The answers section.** Each `leads answer` appends:

```
## Follow-up answers

<!-- sluice:followup-answer <sha256 of the date and the text, first 12 hex> -->
YYYY-MM-DD: <text, breaking lines demoted to ###>
```

The heading line is bare, exactly like a hand-typed one, and the per-answer tag sits on its own
line inside the section. `Store.append_body_section`'s idempotence only needs the tag somewhere in
the fresh file, so re-running the same answer on the same day appends nothing, while the same
words given as a reply on a later day (a second "no") are a new answer and are written.

The reader is a generalisation of `core/jdsection.py`, not a copy: a public
`jdsection.sections(body, heading, tag="") -> list[str]` returns the text of EVERY section whose
heading line is `heading` (optionally followed by the fixed `tag`), with the existing fence and
`breaks_section` rules. The tag strip is guarded on a non-empty tag (`bare[:-0]` is the empty
string, so an unguarded strip would match no heading at all). `read()` becomes `sections(body, HEADING, TAG)` reduced to the first text
and the count, its behaviour unchanged and pinned by the existing jdsection tests. The module
docstring changes from "the ONE grammar of `## Job description`" to "the one grammar of a lead
note's body sections". `core/followups.py::answers(body)` calls `sections(body, ANSWERS_HEADING)`,
drops the tag lines (`^<!-- sluice:followup-answer [0-9a-f]{12} -->$`), and joins every section in
body order: each answer is its own section, and taking the first would silently drop every later
reply. `render_answer(text, date)` lives beside it and uses `jdsection`'s demotion, so the writer
and the reader share one grammar.

**One predicate.** `core/followups.py::awaiting_reply(fm) -> bool` is status `research` (after
`status.normalize`) and `followup_asked` non-blank after stripping. Both the worklist grouping and
the triage skip call it, so the two cannot disagree. Residual, stated: a hand-typed BLOCK value
under `followup_asked` reads blank (the store's single-line read), so such a lead is judged rather
than skipped. That is the visible direction; nothing writes over the block value, since every
writer of the key passes it in `preserve_block_values`.

### Facade and commands

The writers live on `Sluice`, like `dismiss_lead` and `create_lead`, so a later MCP tool drives the
same code. Both resolve `lead` by EXACT store-issued slug the way `dismiss_lead` does. Dates come from
`Sluice`'s injected `today` callable (`core/app.py::_today` by default), never the wall clock
directly. Each returns a frozen result dataclass beside `DismissResult` (`AskResult`,
`AnswerResult`), carrying `slug`, `outcome` and, where it applies, the fresh `status`. Outcome
names reuse `dismiss_lead`'s where the meaning is the same.

| Outcome | `mark_asked` | `record_answer` | Exit |
| --- | --- | --- | --- |
| `not_found` / `ambiguous` | slug resolves to no note / to several (the candidates named) | same | 1 |
| `refused_status` | the lead is not at `research` (snapshot, or fresh after a `False`) | not at `research` before step 1, nothing written | 1 |
| `refused_block_value` | `followup_asked` holds a hand-typed block value | same, at step 2 (the answer is kept) | 1 |
| `conflict` | `VaultConflict` on the write | `VaultConflict` at step 1 (nothing written) or step 2 (answer kept, stamp not cleared) | 1 |
| `answered_not_cleared` | -- | the status left `research` between step 1 and step 2; the answer is kept and the outcome names the new status | 1 |
| `asked` / `answered` | stamp written | answer appended (or already present from a same-day re-run) and the stamp blank | 0 |
| `unchanged` | already stamped today | -- | 0 |

`refused_block_value` is reached by ELIMINATION, never by reading vault internals from
`core/app.py`: a block value reads blank through `LeadNote.fm`, so when a write returns `False` and
the fresh note is still at `research` with a value that is not the one written, the only remaining
cause is `preserve_block_values` abstaining, and the outcome says so. After any `False`, the FRESH
STATUS is checked FIRST: a lead that left `research` is `refused_status` (or
`answered_not_cleared`) whatever `followup_asked` holds, so a never-asked lead that moved to
`applied` mid-command is never reported as answered.

**`Sluice.mark_asked(lead)`** writes `followup_asked = today` via `update_fields` with
`require_status={"research"}` and `preserve_block_values={"followup_asked"}`. A different earlier
date is overwritten: asked again.

**`Sluice.record_answer(lead, text)`** refuses a blank `text` (a usage error at the CLI) and, from
the snapshot, a lead not at `research`, writing nothing. Then two writes, in this order:

1. `Store.append_body_section(ref, tag, render_answer(text, today))`. `False` means this tag is
   already in the note -- the same answer recorded the same day -- and is not an error.
2. `update_fields(ref, {"followup_asked": ""}, require_status={"research"},
   preserve_block_values={"followup_asked"})`, its `False` classified as above.

Not one compare-and-set write: that would need a body-append parameter on `update_fields`, a new
write shape for a case the two existing writers already cover. Every partial state is safe and a
re-run of the same command completes it. Residual, stated: step 1 is guarded by the snapshot only
(`append_body_section` has no status guard), so a lead that enters the application lifecycle in the
instant between the snapshot and step 1 gets the user's own answer appended to its body. No status
changes, step 2 abstains, and the outcome is `answered_not_cleared` naming the new status.

**`leads followups [--json]`** -- read-only, over `Sluice.followups()`. Every lead at `research`,
in two groups, `Never asked` and `Asked, waiting` (by `awaiting_reply`). Each row: slug, company and
role, age in days from `first_seen`, and `triage_next`; waiting rows add days since
`followup_asked`. An unparseable date prints its age as `?` and is never raised. Newest first
within a group. The footer gives the total and, when `lead_ttl_days` is 0, says `leads expire` with
`lead_ttl_days` ages these out. No `--dry-run` (it writes nothing), matching the rule that a
report needs no preview flag.

**`leads mark-asked --lead SLUG`** and **`leads answer --lead SLUG --text TEXT`** -- thin
front-ends over the two facade methods, `--lead` with the same help text as `leads dismiss`
(required, exact store-issued slug). The verb is `mark-asked`, not `asked`: every other `leads`
subcommand is an imperative (`add`, `dedupe`, `dismiss`, `answer`), and a bare `ask` would read as
sluice contacting someone. Both write unconditionally on every call, like `leads dismiss` and `leads
add`: what they write is the user's own action or words. Each prints its outcome and exits per the
table.

### Triage

- **Skip.** In the engine's selection, a lead for which `awaiting_reply(fm)` holds is not sent to
  classify or judge. It is counted under a new `counts` key, `awaiting_reply`, rendered in
  `_TRIAGE_FILTERED_WORDS` as `{n} skipped while you wait for a reply`. Only `research` is skipped.
  That row counts THIS run's skips; the standing line below counts the whole queue, so the two
  are worded apart rather than both saying "awaiting".
- **Answers reach the judge.** For a lead whose body has answers, the engine adds
  `followup_answers` to THAT LEAD'S OWN dossier copy, after `get_or_build` and after `_fan`, on
  both paths (fetched and section-sourced). Never before `get_or_build`, which would persist the
  answers into the shared cache file, and never onto a result shared between leads with one cache
  key. `slim()` keeps the field. The system prompt gains one paragraph stating the mechanics only:
  `followup_answers`, when present, are the candidate's own notes of replies to earlier research
  questions, to be read as facts about the role alongside the JD. It carries no example answer
  content -- no rate, location, contract type or role -- since the prompt-neutrality guard's term
  list is narrow and would not catch one.
- **Digest.** After a run, `cmd_triage_run` calls `Sluice.followups()` and, when the research
  queue is non-empty, adds one line separate from the per-run names, e.g. `Research queue:
  12 leads, 5 marked asked; oldest 31 days.` It is computed from the vault, not the run, so the
  name cap cannot hide it and a run with `--status new` still reports it. It prints on `--dry-run`
  too: the queue is a fact about the vault, and reading it writes nothing. A failure to read it is
  logged and the line omitted; it never fails the run, which has already written.

### MCP

No new tools. `get_lead` already returns the keys and the body, but
`mcpserver.py::_GET_LEAD_CONTENT_WARNING` labels everything outside `FRAMING_KEYS` as third-party
scraped content; it gains one sentence naming the `## Follow-up answers` section as the user's own
notes, the mislabel #329 treated as a defect.

### Out of scope

- MCP tools for asked/answer (the facade methods make them a thin addition later).
- A research-specific TTL (existing `lead_ttl_days` covers ageing).
- Backfilling `triage_next` from `relevance_notes` prose.

## Testing

Fixtures are synthetic: titles from the seeded-faker `titles` fixture, synthetic company names, and
every count asserted is built by the test itself.

- `triage/apply.py`: `triage_next` written, replaced, blanked when the action is empty, dropped
  with a warning when unsafe, preserved when hand-typed as a block value; `followup_asked` blanked
  by a verdict; `triage_next` and `followup_asked` are NOT in `FRAMING_KEYS` and never reach the
  compose prompt; the existing write-loop roster guard in `tests/test_apply.py` carries the
  carve-out.
- `core/jdsection.py`: `read()` unchanged under the existing tests; `sections()` returns every
  section in order, honours fences, stops at a level-1/2 heading.
- `core/followups.py`: a `render_answer` output round-trips through `answers()`; several answers
  concatenate in body order; tag lines are dropped; a heading with other trailing text is not the
  section; `awaiting_reply` on each status, blank, whitespace and stamped values.
- `Sluice.mark_asked`: stamps the injected date; refuses a non-research lead, an unknown slug and an
  ambiguous slug, writing nothing; same-day re-run is `unchanged`; preserves a block-valued
  `followup_asked`; status changed between snapshot and write refuses (the monkeypatched shape of
  `tests/test_leads_dismiss.py::test_cas_proof_refuses_on_a_status_that_changed_between_resolve_and_write`);
  other keys and the body byte-for-byte intact.
- Outcomes: every row of the outcome table for both methods, with the CLI exit code; a
  `VaultConflict` at each write; a never-asked lead whose status leaves `research` between the
  snapshot and step 2 is `answered_not_cleared`, not `answered` (witness: swap the status check and
  the blank check); the same text answered on two different days is written twice, and on the
  same day once.
- `Sluice.record_answer`: appends and clears; a re-run appends nothing and succeeds; a never-asked
  lead succeeds; a blank text and a non-research lead write nothing; pasted `## ` lines demoted;
  status leaving `research` between step 1 and step 2 gives `answered-not-cleared`, non-zero, the
  answer kept and `followup_asked` and status untouched (witness: delete step 2's
  `require_status`).
- Verdict and stamp: a verdict on a stamped note blanks the stamp; a verdict on an unstamped note
  writes no `followup_asked` key; `leads mark-asked` landing between the engine's read and its
  write survives, with the verdict refused (witness: delete `require_unchanged`); a block-valued
  `followup_asked` survives a verdict (`test_verdict_preserves_a_block_valued_followup_asked`,
  witness: drop `followup_asked` from `_HAND_EDITABLE_KEYS`).
- `jdsection.sections`: the default empty tag still matches a bare heading.
- MCP: `get_lead` on a note with a `## Follow-up answers` section returns a warning naming that
  heading, asserted on the literal heading text rather than against the module constant (witness:
  delete the new sentence).
- Engine: an awaiting lead is not judged and is counted `awaiting_reply`; a non-research lead with
  a stale stamp is judged; the stamp -> hand move to `new` -> re-judged `research` sequence leaves
  the lead judged next run; answers reach the judge prompt for a fetched lead and for a
  section-sourced lead; `test_followup_answers_never_reach_the_dossier_cache_file` reads the cache
  JSON on disk (witness: move the insertion ahead of `get_or_build`); two leads sharing a url, only
  the answered one carries answers.
- Digest: the standing line renders from `followups()`, singular and plural, omitted when empty,
  present on `--status new` and on a dry run; a read failure omits it without failing the run.
- `leads followups`: grouping, ordering, ages from the injected date, `?` for an unparseable date,
  a non-research lead with a stale stamp excluded, the `--json` shape, the `lead_ttl_days` hint.
- Docs: USAGE, README commands table, CONFIGURATION (no new config keys -- stated), ARCHITECTURE
  (the keys, the section, the jdsection generalisation, and the unconditional-writer paragraph
  reworded as the property "writes what the user typed" with no count), `.rulesync/rules/CLAUDE.md`
  (the same reword, then regenerate), TROUBLESHOOTING ("research leads keep coming back").
