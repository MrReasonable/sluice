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
  weeks, and most come from one board. The blocks behave as permanent, not transient.

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

## Design

### 1. Recording the candidate (nightly triage)

In `triage/apply.py::apply_verdict`, when the clamped status is `unjudgeable` and the main
write returns `unchanged`, a second write runs:

```python
vault.update_fields(note.ref, {"triage_blocked": f'"{today}"'},
                    require_status=frozenset({"research"}))
```

- It sets one triage-owned key and nothing else: not the status, not the body, no
  `relevance_notes` line. `require_status` is re-read fresh inside the compare-and-set
  transform, so a lead that left `research` while being judged is not stamped.
- It is the existing writer, so it is not a new CodeQL sink.
- `new` and `unjudgeable` leads never reach it: their main write lands.
- An `applied`-or-later lead is turned away by `_guarded` before any write.
- A `--dry-run` never calls `apply_verdict`, so it stamps nothing.
- The key is `triage_`-prefixed for the reason `triage_concerns` is: a person might already use
  a bare name for their own notes.

**Clearing.** Any verdict that LANDS blanks a present stamp, in the same write, the way the
#378 `followup_asked` stale-stamp rule does. So a stamp always means the latest judgement of the
lead said the page was not a posting. A blocked lead is re-stamped nightly, so the date stays
current.

`apply_verdict` gains a third outcome for this arm, `"blocked"`, so the engine can count it
without a second status read. It is grouped with `skipped` everywhere `skipped` is consumed
today (counts, `surfaced`, the audit condition), except that it also increments a new
`report.counts["blocked_research"]`. It does NOT increment `unjudgeable_by["judge"]`: no lead
became `unjudgeable`.

**Digest.** `cli.py::_format_triage_digest` gains one line when `blocked_research` is non-zero:
`N research lead(s) look blocked by their board; review with: job-sluice leads unpark`. The
zero case prints nothing, like every other row.

### 2. The pass: `job-sluice leads unpark`

A `Sluice` facade pair modelled on `expire_report`/`expire`:

- `unpark_report()` reads `read_leads({"research"})` and returns those carrying a non-blank
  `triage_blocked`, each with slug, company, role, stamp date and url host. It changes nothing.
- `unpark(slugs=None)` re-derives the report, narrows to `slugs` if given (an unknown slug is
  reported, not ignored), and for each lead calls:

  ```python
  update_fields(ref, {"status": "needs_review", "triage_blocked": '""'},
                append_note=f"{tag} unpark: the board served a non-posting page; moved from "
                            "research for a human to look at", note_tag=tag,
                require_status=frozenset({"research"}),
                require_unchanged={"triage_blocked": <stamp from the report>})
  ```

  A lead re-judged (stamp cleared), re-stamped, or moved off `research` since the report
  abstains and is reported by name as `abstained`. `VaultConflict` is caught for that lead and
  reported, never fatal to the sweep.

CLI: `job-sluice leads unpark` prints the report. `--apply [SLUG ...]` moves either all of the
reported leads or only the ones named, using `nargs="*", default=None` and an `is not None`
dispatch (the shape `leads expire --expire` documents; a bare flag parses to a falsy `[]`).
`--json` gives machine-readable output. There is no `--dry-run`, because the default is the dry
run. Exit 0 unless the store errors or a named slug matches nothing.

`needs_review` is triage-owned, so never-regress permits `research -> needs_review`.

### 3. Never-clobber: residuals, stated

- A `research` the owner set by hand on a lead whose board is also blocked will be stamped and
  listed. The listing and the explicit `--apply` are the guard; this is why the pass does not
  run unattended.
- If the owner hand-sets `research` on a lead AFTER a stamp landed, the stamp stays until the
  next landed verdict. `require_unchanged` cannot tell that apart, so the human reading the list
  is the backstop.

## Out of scope

- Leads already at `unjudgeable` that never heal are judged every night too. That is the same
  recurring cost through a different status and its own decision (e.g. a give-up threshold),
  to be filed separately.
- The audit log's silence about refused verdicts. The stamp is the record this feature needs;
  auditing refusals in general is a separate change.

## Testing

All offline, with the judge backend stubbed:

- The stamp lands on a `research` lead given an `unjudgeable` verdict. It does not land on
  `new`/`unjudgeable` (the main write lands) or on a guarded lead.
- A later landed verdict blanks it. A dry run writes nothing.
- A status changed to non-`research` between the snapshot and the write is not stamped. The
  change is raced in with `tests/conftest.py::racing_read` (the shape
  `test_a_lead_that_becomes_applied_MID_SWEEP_is_not_dismissed` uses), never by editing a
  snapshot, since a guard on the snapshot is byte-identical to no guard.
- `blocked_research` counts and the digest line; zero prints nothing.
- `unpark` report changes nothing (bytes compared). `--apply` moves, appends the note and clears
  the stamp. It abstains on a fresh status change and on a stamp change, and narrows to named
  slugs; an unknown slug is reported.
- Each guard (`require_status` on the stamp, `require_unchanged` and `require_status` on the
  move, the clear-on-landed-verdict) gets a move-or-delete mutation witness.
- Docs: `docs/USAGE.md`, the README commands table, `docs/ARCHITECTURE.md`'s `leads` passes
  and triage paragraphs. The doc-claims tests pin the first two.
