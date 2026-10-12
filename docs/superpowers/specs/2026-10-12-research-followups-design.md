# Research follow-ups: a worklist, a standing total, and an asked state (#378)

## Problem

`research` is the judge's verdict for a lead it would shortlist once a human answers one or two
questions. Nearly every such verdict ends in a concrete instruction (`Next: ask recruiter: client
name, comp band`). Nothing delivers those instructions as a list, nothing records whether one was
carried out, and the nightly `triage run` (whose default statuses include `research`) re-judges
each lead to the same verdict because nobody asked. The triage push names at most
`cli._PUSH_NAME_CAP` leads per run, shortlist first, so a growing research queue is seen as a small
shifting sample and never as a total. The owner's vault held 118 such leads, the oldest 50 days.

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
| The verdict's `Next:` instruction | frontmatter `triage_next` | `triage/apply.py`, replaced on every verdict |
| Asked, awaiting a reply | frontmatter `followup_asked: YYYY-MM-DD` | `leads asked`; cleared by `leads answer` |
| The reply | body section(s) `## Follow-up answers` | `leads answer`, or by hand |

`triage_next` follows `triage_concerns` (#329) exactly: its own key, `triage_`-prefixed because it
is replaced on every verdict and must not collide with a key a person might use, written through
`frontmatter_safe` and `preserve_block_values` like its sibling. It is written for every verdict,
blank when the model gave no action, so a stale instruction never outlives the verdict that
produced it. `relevance_notes` keeps its `Next:` prose unchanged. Existing leads carry the text
only in that prose; no parser backfills it. Every `research` lead is re-judged by the next default
`triage run`, which writes the key; until then the worklist prints `(no Next: recorded yet)`.

The answers section is read by a reader in `core/followups.py` that shares `core/jdsection.py`'s
fence handling and `breaks_section` rule (the same grammar, generalised to take the heading, so a
fence or a level-1/2 heading means the same thing in both sections). Unlike the job description,
which takes the FIRST section, answers concatenate EVERY `## Follow-up answers` section in body
order: each `leads answer` appends its own section, and a reader that took one would silently
drop every later reply.

### Commands (all under `leads`)

**`leads followups [--json]`** -- read-only. Every lead at `research`, in two groups: `Never
asked` and `Asked, waiting`. Each row: slug, company and role, age in days from `first_seen`, and
`triage_next`; waiting rows add days since `followup_asked`. Newest first within a group. The
footer names the total and, when `lead_ttl_days` is 0, says `leads expire` with `lead_ttl_days`
ages these out. No `--dry-run` (it writes nothing) and no flag to act, matching the leads-pass
rule that a report needs no preview flag. The facade method behind it, `Sluice.followups()`, is
the ONE reader the digest uses too, so the command and the push cannot count differently.

**`leads asked <slug>`** -- stamps `followup_asked` with today's date. Writes unconditionally on
every call (like `leads dismiss`): what it writes is the user's own action. `require_status`
`{"research"}` inside the CAS transform; a lead at any other status is refused by name and
nothing is written. Re-stamping an asked lead updates the date (asked again).

**`leads answer <slug> --text TEXT`** -- records a reply and returns the lead to the queue. Two
writes, in this order:

1. `Store.append_body_section(ref, tag, section)` -- the existing idempotent body writer. The
   section is `## Follow-up answers <tag>` then the date and the text, rendered with the same
   heading demotion as `jdsection.render` so pasted text cannot end the section early. `tag` is
   `<!-- sluice:followup-answer <sha256 of the text, 12 hex> -->`, so re-running the same
   command appends nothing.
2. `update_fields(ref, {"followup_asked": ""}, require_status={"research"})`.

Not one CAS write: that would need a body-append parameter on `update_fields`, a new write shape
for a case the two existing writers already cover. The order makes every partial state safe and
recoverable by re-running the same command: if (2) fails, the answer is in the note, the lead
stays skipped, the command exits non-zero naming the step, and a re-run appends nothing and
retries the clear. The status guard is checked before (1) from the snapshot to give a clean
refusal, and enforced CAS-fresh in (2). Text is required and non-empty; a blank `--text` is a
usage error.

An unknown slug to `asked` or `answer` is refused by name, and nothing is written.

### Triage

- **Skip.** In the triage engine's selection, a lead at `research` whose `followup_asked` is
  non-blank is not sent to classify or judge. It is counted under a new `counts` key,
  `awaiting_reply`, rendered in `_TRIAGE_FILTERED_WORDS` as `{n} awaiting a reply`. Only
  `research` is skipped: a stale stamp on a lead whose status a human changed by hand does not
  hide it from triage.
- **Answers reach the judge.** When a lead's body has answers, the engine adds
  `followup_answers` (the concatenated text) to that lead's dossier COPY after the cache, the same
  place per-lead fields are added, so the cache key and cached JD are unchanged. `slim()` keeps
  the field. The system prompt gains one paragraph: `followup_answers`, when present, are the
  candidate's own notes of replies to earlier research questions; treat them as facts about the
  role alongside the JD. The paragraph names no role, culture or preference
  (`test_shipped_prompt_expresses_no_role_or_culture_preference` still applies).
- **Digest.** After a run, `cmd_triage_run` calls `Sluice.followups()` and, when the research
  queue is non-empty, adds one line separate from the per-run names:
  `118 research leads awaiting follow-up (40 asked), oldest 50 days.` It is computed from the
  vault, not from the run, so the name cap cannot hide it and a run with `--status new` still
  reports it. It prints on `--dry-run` too: the queue is a fact about the vault, and reading
  it writes nothing. A failure to read it is logged and the line omitted; it
  never fails the run, which has already written.

### Out of scope

- MCP tools for asked/answer (the CLI covers the owner's workflow; `get_lead` already shows the
  frontmatter keys).
- A research-specific TTL (existing `lead_ttl_days` covers ageing).
- Backfilling `triage_next` from `relevance_notes` prose.

## Testing

- `triage/apply.py`: `triage_next` written, replaced, blanked when the action is empty, dropped
  with a warning when unsafe, preserved when hand-typed as a block value.
- `core/followups.py`: multiple sections concatenated in order; fenced headings ignored; a `## `
  heading ends a section; a heading with trailing text is not the section.
- `leads asked`: stamps; refuses a non-research lead and writes nothing; the CAS re-check holds
  when status changes between snapshot and write (race test with the existing harness pattern);
  other keys and the body byte-for-byte intact.
- `leads answer`: appends and clears; a re-run appends nothing; a failed clear exits non-zero
  with the answer kept; pasted `## ` lines demoted; refusal on a non-research lead writes nothing.
- Engine: an asked research lead is not judged and is counted `awaiting_reply`; a non-research
  lead with a stale stamp is judged; answers appear in the judge prompt and not in the cache key.
- Digest: the standing line renders from `followups()`, with singular/plural, omitted when empty,
  present on `--status new` and on a dry run; a read failure omits it without failing the run.
- `leads followups`: grouping, ordering, ages, the `--json` shape, the `lead_ttl_days` hint.
- Docs: USAGE, README commands table, CONFIGURATION (no new config keys -- stated), ARCHITECTURE
  (the two keys and the section), TROUBLESHOOTING ("research leads keep coming back").
