# Clearing `research` leads parked by the pre-#300 conservative score (#302)

## Problem

Before #300/#301, a judge handed a page that was not a job posting (a bot check, a sign-in
wall, a CV-upload interstitial) scored it conservatively and the lead landed in `research`.
#301 lets the judge say `unjudgeable` instead, but `_VERDICT_REQUIRE`
(`triage/apply.py`) refuses to write `unjudgeable` over `research`, because `status` records
where a lead is and never how it got there: a conservative-score artifact and a research task a
human set by hand are byte-identical at that field.

So the old backlog stays parked. `research` is in `DEFAULT_TRIAGE_STATUSES`, so each nightly
run re-judges those leads, gets `unjudgeable`, and has the write refused, every night, while
the leads sit in the human research queue looking like real decisions.

## What measurement showed (owner's install, read-only, 2026-10-14)

- The refused verdict is invisible to every existing record. `triage/engine.py` appends a judge
  row to the triage audit log only when the write landed (`outcome not in ("skipped",
  "unchanged")`), and `report.unjudgeable_by` counts off the same outcome. A first pass over the
  audit log therefore found zero affected leads. The affected leads showed up instead as
  `research` leads with no audit row since #301 deployed, whose last recorded scores cluster on
  the conservative value and whose cached dossiers hold page chrome.
- No lead judged `unjudgeable` since #301 has later received a real verdict, over about four
  weeks. The blocks behave as permanent, not transient.

## Owner decisions

1. **Identification: keep the refused verdict.** The nightly run already pays for the
   judgement. Recording it costs no extra judge call and reads no model prose. Re-judging on
   demand would spend on every healthy `research` lead to find the few blocked ones.
2. **Destination: `needs_review`.** `unjudgeable` is in `DEFAULT_TRIAGE_STATUSES`, and a cached
   dossier holding page chrome counts as a JD that arrived, so an `unjudgeable` lead goes to the
   judge every night. Moving the backlog there would keep the cost and change only the label.
3. **Opt-in: a `leads` pass, not the #223 two-run gate.** The `leads` passes write over a set the
   TOOL computed, so they report by default and write only on a flag. The two-run gate inside
   `triage run` would apply on the next nightly cron run without the human typing anything.
4. **Stamp clearing: triage's own writers** (plan review, option a). `apply_verdict` and
   `apply_classification` clear the stamp on any write that lands. `leads dismiss`, `leads
   expire` and the reverdict pass do not; that residual is stated in §3.

## Design

### 1. Recording the candidate (nightly triage)

Two triage writers can return `unjudgeable` for a `research` lead and have it refused: the
judge (`apply_verdict`) and the pre-judge "no job description arrived" gate in
`triage/engine.py`, which calls `apply_classification(..., "unjudgeable", ...)` and is refused
by `_DECISION_REQUIRE` the same way. Both record the refusal through one helper in
`triage/apply.py`, `_stamp_blocked(vault, note, today)`. It runs only when the decision is
`unjudgeable`, the main write returned False, and the SNAPSHOT status normalises to `research`:

```python
stamped = vault.update_fields(note.ref, {"triage_blocked": f'"{today}"'},
                              require_status=frozenset({"research"}),
                              require_blank=frozenset({"followup_asked"}),
                              preserve_block_values=frozenset({"triage_blocked"}))
```

`today` is read once per call from the module's `date`, so tests fix it by patching
`sluice.triage.apply.date`.

- It sets one triage-owned key and nothing else: not the status, not the body, no
  `relevance_notes` line. `require_status` is re-read fresh inside the compare-and-set
  transform, so a lead that left `research` while being judged is not stamped.
- `preserve_block_values` leaves a hand-typed multi-line `triage_blocked` alone (#329).
- It is the existing writer, so it is not a new CodeQL sink.
- `require_blank={"followup_asked"}` keeps a lead the human marked asked while it was being
  judged from being stamped: it is an active research task, and `unpark` would not list it
  anyway.
- `new`, `unjudgeable` and every other non-`research` snapshot never reach it, so a refused
  write on a `shortlist` or `dismiss` lead costs nothing extra. A `new` lead a human hand-sets
  to `research` while it is being judged is therefore not stamped on that run; the next nightly
  run judges it at `research` and stamps it.
- An `applied`-or-later lead is turned away by `_guarded` before any write.
- An `awaiting_reply` lead (`research` with `followup_asked` set) is filtered out by the engine
  before judging (`followups.awaiting_reply`), so it is never judged and never stamped.
- A `--dry-run` never calls `apply_verdict`, so it stamps nothing.
- The key is `triage_`-prefixed for the reason `triage_concerns` is: a person might already use
  a bare name for their own notes.

**What counts as a stamp.** Everywhere below, "a stamp" means a `triage_blocked` value that is
an ISO date on one line (`date.fromisoformat` accepts it after stripping). Anything else -- a
hand-typed word, a block scalar such as `triage_blocked: |` (which reads as `|`, not blank) --
is the person's own text: the report does not list it and no clear writes over it. One pure
helper, `core/leads.py::blocked_stamp(fm) -> str` (in `core/` because the `Sluice` facade reads
it too, and `core/` may not import a sub-app), returns the raw value when it is a stamp and
`""` otherwise, and every reader below goes through it.

**When the outcome is `"blocked"`.** A True stamp write returns `"blocked"`. A False is
ambiguous: a refused guard, a byte-identical same-day re-stamp, or `preserve_block_values`
abstaining. The store has no single-note read, and a whole-vault re-read per refused lead is
too dear for the nightly loop, so a False is classified from the SNAPSHOT: `"blocked"` when the
snapshot's `triage_blocked` already equals `today` (a second run on the same day), else
`"unchanged"`. The outcome only feeds a count and a digest line, never a write, so the
residual is bounded and stated: a lead stamped earlier today that leaves `research` mid-judge
is counted once more in that run's digest. `leads unpark` re-reads before listing or moving
anything, so it never acts on the count.

**Clearing.** In `apply_verdict` and `apply_classification`, any write that lands blanks a
present stamp in the same write, following the #378 `followup_asked` stale-stamp rule. (The
`apply_classification` clear gives that writer a `require_unchanged` it has never had.)

- a snapshot with no stamp (blank, or not a date) writes no `triage_blocked` key at all, so a
  judged lead that was never stamped gains no key and a hand-typed value is never touched;
- a stamped snapshot sets `triage_blocked: ""` under `require_unchanged={"triage_blocked":
  stamp}`, MERGED with the existing `followup_asked` entry rather than replacing it (both keys
  in one dict when both are present);
- `triage_blocked` joins `_HAND_EDITABLE_KEYS`, so a multi-line hand-typed value is left alone.

`Sluice.record_answer`'s second write, which blanks `followup_asked` to return an answered
lead to triage, also blanks `triage_blocked` WHEN the snapshot carries a stamp (no key is added
otherwise), with `triage_blocked` added to that call's `preserve_block_values` beside
`followup_asked`: an answered research task is a human's live
decision, and must not come back into `unpark`'s listing on a stamp from before the question.

So a stamp means the latest triage write to the lead was a refused `unjudgeable` verdict. A
blocked lead is re-stamped nightly, so the date stays current.

**Engine wiring.** `apply_verdict` and `apply_classification` gain a third outcome,
`"blocked"`. `triage/engine.py` has five hand-written checks naming `skipped` and `unchanged`;
the three at the judge and pre-judge sites learn it, and the two in the classify pass are left
alone (below). At the judge site:

- the counting key: `key = "skipped" if outcome in ("skipped", "unchanged", "blocked")`, so a
  blocked lead counts under `skipped`, never under `unjudgeable` and never in
  `unjudgeable_by["judge"]` (no lead became `unjudgeable`), and is not surfaced;
- the audit condition: `if outcome not in ("skipped", "unchanged", "blocked")`, so no judge row
  claims a decision that never landed.

At the pre-judge gate site, the counting key becomes `"skipped" if outcome in ("skipped",
"unchanged", "blocked") else "unjudgeable"`, so `unjudgeable_by["pre_gate"]` stays exact. The
classify pass never decides `unjudgeable`, so it never sees `"blocked"` and is left unchanged.

`TriageReport` gains its own field, `blocked_research: int = 0`, incremented when the outcome
is `"blocked"` at either site. It is a cause breakdown like `unjudgeable_by`, not an outcome
row, so it does not go in `counts`.

**Digest.** `cli.py::_format_triage_digest` gains one line when `blocked_research` is non-zero:
`N research lead(s) could not be judged from their page; review with: job-sluice leads unpark`. The
zero case prints nothing, like every other row.

### 2. The pass: `job-sluice leads unpark`

A `Sluice` facade pair modelled on `expire_report`/`expire`:

- `unpark_report()` reads `read_leads({"research"})` and returns, for each lead whose
  `blocked_stamp` is non-empty and that is NOT `awaiting_reply`, a row with `slug`, `ref`, `company`, `role`,
  `stamp` (the raw stored value, exactly as `require_unchanged` will compare it) and `host`
  (the url's host). It changes nothing.
- `unpark(slugs=None)` re-derives the report and returns `(rows, results)` from that one read,
  so the CLI never reads twice and the two halves of its output cannot disagree. With `slugs`, it narrows through `index_by_slug`
  (duplicate-slug handling as in `expire`): a slug matching nothing reports `no-match`, a slug
  two rows claim reports `ambiguous`, and the other named slugs still move. Each chosen lead is
  written with:

  ```python
  tag = f"[unpark {today}]"   # today from the facade's injected clock, Sluice(today=...)
  update_fields(r.ref, {"status": "needs_review", "triage_blocked": '""'},
                append_note=f"{tag} triage could not judge this lead from its page (not a "
                            "posting, or too little text); moved from research for a human "
                            "to look at", note_tag=tag,
                require_status=frozenset({"research"}),
                require_blank=frozenset({"followup_asked"}),
                require_unchanged={"triage_blocked": r.stamp},
                preserve_block_values=frozenset({"triage_blocked"}))
  ```

  Outcomes per lead: `moved`; `abstained` (the write returned False: the lead was re-judged,
  re-stamped, moved off `research`, or marked asked since the report); `conflict` (a
  `VaultConflict`, isolated to that lead); `unreadable` (an `OSError` on that lead's ref,
  isolated as in `expire`); `no-match`; `ambiguous`.

CLI: `job-sluice leads unpark` prints the report. `--apply [SLUG ...]` moves either all of the
reported leads or only the ones named, using `nargs="*", default=None` and an `is not None`
dispatch (the shape `leads expire --expire` documents; a bare flag parses to a falsy `[]`).
There is no `--dry-run`, because the default is the dry run.

`--json` prints `{"leads": [{"slug", "company", "role", "stamp", "host"}, ...]}` for the report.
With `--apply` it prints the rows `unpark` acted on (narrowed to the named slugs when slugs are
given) as `"leads"`, plus `"results": [{"slug", "outcome"}, ...]`.

Exit codes follow `cmd_leads_expire`'s `_FAILED` rule: the report exits 0; `--apply` exits 1
when any result is `abstained`, `conflict`, `unreadable`, `no-match` or `ambiguous`, so an
apply that wrote nothing it was asked to write never reads as success. An empty report with
`--apply` writes nothing and exits 0, since nothing was asked for.

`needs_review` is triage-owned, so never-regress permits `research -> needs_review`.

### 3. Never-clobber: residuals, stated

- A `research` the owner set by hand on a lead whose board is also blocked will be stamped and
  listed. The listing and the explicit `--apply` are the guard; this is why the pass does not
  run unattended.
- A lead moved off `research` by `leads dismiss`, `leads expire` or the reverdict pass keeps its
  stamp. If a human later hand-sets it back to `research`, it is listed with that old stamp
  until the next nightly verdict either clears or refreshes it. The listing shows the stamp
  date, and the human reading it is the backstop.

## Out of scope

- Leads already at `unjudgeable` that never heal are judged every night too. That is the same
  recurring cost through a different status and its own decision (e.g. a give-up threshold),
  to be filed separately.
- The audit log's silence about refused verdicts. The stamp is the record this feature needs;
  auditing refusals in general is a separate change.

## Testing

Every behaviour below starts from a failing test. All offline, with the judge backend stubbed
before anything runs. Fixtures are synthetic: roles from `tests/conftest.py`'s seeded `titles`,
companies as `Example <Word>` literals from the reviewed roster in
`tests/test_fixture_name_neutrality.py` (never `faker`'s company generator, whose output no
roster can review), and only `example.com`/`example.org` hosts in urls and host assertions.
Every date-dependent triage row fixes today by patching `sluice.triage.apply.date`; facade and
CLI rows inject it with `Sluice(today=...)`, which is where `unpark`'s tag date comes from.

**Stamp (apply layer).**
- An `unjudgeable` verdict on a `research` lead stamps today's date and returns `"blocked"`;
  status, body and `relevance_notes` are byte-unchanged.
- The pre-judge gate's refused `unjudgeable` on a `research` lead with no job description
  stamps it too, and counts under `blocked_research`, not `unjudgeable_by["pre_gate"]`.
- It does not stamp a `new` or `unjudgeable` lead (the main write lands) or a guarded lead.
- Race: a `new` lead raced to `research` mid-judge is NOT stamped. This is the row that
  witnesses the snapshot-`research` gate; a `shortlist` row cannot, because the stamp's own
  `require_status` refuses it either way.
- A second run on the same day returns `"blocked"` again (the snapshot arm) and leaves the
  bytes unchanged. A lead whose snapshot has no stamp and whose stamp write is refused returns
  `"unchanged"`. A lead whose snapshot carries an OLDER stamp and is raced to `applied`
  mid-judge also returns `"unchanged"`: the row that fails if the snapshot arm tests "has a
  stamp" instead of "has today's stamp".
- A multi-line hand-typed `triage_blocked` is left alone and the outcome is `"unchanged"`.
- Race: the lead is moved to `applied` mid-judge with `tests/conftest.py::racing_read`, and the
  test asserts the race fired. Neither write lands. `applied` is chosen because the main
  verdict cannot land there either, so deleting the stamp's `require_status` turns the row red.
- Race: a lead marked asked (`followup_asked` set) mid-judge is not stamped, and the test
  asserts the race fired.

**Clearing.**
- A landed verdict on a stamped lead blanks the stamp; a landed classification does too.
- A judged lead with no stamp gains no `triage_blocked` key.
- A lead carrying both a stale `followup_asked` and a stamp clears both. Two separate race
  rows, one per key: a re-stamp of `followup_asked` on a DIFFERENT date mid-judge refuses the
  write, and so does a re-stamp of `triage_blocked`. The `followup_asked` row is the one that
  fails if the new entry replaces the old one instead of joining it.
- `apply_classification`'s new `require_unchanged`: a stamped lead whose stamp is re-dated
  mid-classify is not written.
- Recording a follow-up answer blanks a stamped `triage_blocked` as well as `followup_asked`;
  on a lead with no stamp it adds no `triage_blocked` key; a multi-line hand-typed value is
  left alone.
- A non-date `triage_blocked` (a word, and a `|` block scalar) is neither listed by the report
  nor cleared by a landed verdict.

**Engine.**
- A blocked lead (judge site and pre-judge site each) counts `skipped == 1`, `counts["unjudgeable"] == 0`,
  `unjudgeable_by["judge"] == 0`, `blocked_research == 1`, surfaces nothing and appends no audit
  row. Each of the three hand-written checks gets its own mutation witness (remove `"blocked"`
  from one tuple at a time).
- Digest: the line prints for a non-zero count and nothing prints for zero.

**Facade and CLI.**
- The report changes nothing (note bytes compared before and after), carries the raw stamp, and
  excludes an `awaiting_reply` lead. An empty report prints an empty listing and exits 0.
- No flag writes nothing. A bare `--apply` moves every listed lead, appends the note and clears
  the stamp. `--apply SLUG` moves only that lead; an unknown slug alongside a valid one reports
  `no-match`, moves the valid one, and exits 1. Duplicate slugs report `ambiguous`.
- A lead re-stamped with a DIFFERENT date after the report abstains (a same-day re-stamp is
  byte-identical and cannot exercise the guard). A lead moved off `research` mid-sweep, and one
  marked asked mid-sweep, each abstain, raced in with `racing_read`. Each abstain exits 1.
- One lead raising `VaultConflict` reports `conflict` while the others still move.
- `--json` matches the shape in §2, with and without `--apply`, and `--apply SLUG --json` lists
  only the named lead.

Each guard (`require_status` and `require_blank` on the stamp; the snapshot-`research` gate;
the `== today` arm; `blocked_stamp`'s date check;
`require_status`, `require_blank` and `require_unchanged` on the move; the clear in each of the
two triage writers and in `record_answer` (its presence condition and its
`preserve_block_values` entry); the `awaiting_reply` filter in the report) gets a
move-or-delete mutation witness. Two protections are defence in depth with no reachable test,
and the plan says so rather than claiming a witness: `triage_blocked` in `_HAND_EDITABLE_KEYS`
and the move's `preserve_block_values`. Both are reached only when the snapshot held a date
stamp and the fresh value has since become multi-line, and in that case the write's
`require_unchanged` against the snapshot stamp refuses first.

**Docs.** `docs/USAGE.md`; the README commands table; `docs/ARCHITECTURE.md`'s `leads` passes
and triage paragraphs; `.rulesync/rules/CLAUDE.md`'s `leads` passes paragraph (add `leads
unpark (--apply [SLUG ...])`); the comment above `_VERDICT_REQUIRE` in `triage/apply.py`,
which names the reverdict gate as the migration route and must name `leads unpark` instead;
and the #118 comment in `apply_classification` ("`wrote=False` here is always a genuine no-op,
never a race"), which the new `require_unchanged` makes false.
The doc-claims tests pin the first two. No doc, commit message or PR body names a board, a
lead count or a company from the owner's install.
