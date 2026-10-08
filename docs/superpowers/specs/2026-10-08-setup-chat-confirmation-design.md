# In-session setup: save on a chat yes (design)

Amends `2026-10-07-in-session-setup-career-coach-design.md`. Owner's ruling, 2026-10-08, after the
first real Claude Code session.

## Why

The per-change review form was the worst part of a real session. In tmux the form dialog does not
scroll, so each form holds about one screen: 19 agreed changes took about 8 forms, and a Role Brief
section longer than one screen (a normal "Sources consulted") could never be saved at all. Reviewing
the result in Obsidian is a better check than reading boxes.

The owner was offered a single "save this setup" box, which kept the guard that the model cannot
answer the form for the user. The owner chose chat confirmation only, knowingly. The form remains
the mechanism for `verify_evidence`, which this change does not touch.

## The flow

1. The coach interviews and researches as before.
2. **Playback.** Before saving, the coach plays back everything it will save, in plain words,
   grouped by where it goes: the config settings (each with its value, and what leaving one empty
   means), the Judging Profile sections, the Candidate Profile fields, the Role Brief sections, and
   the searches. Where a value replaces one the user already has, the playback shows both.
3. **An explicit yes.** The coach saves only after the user says yes to that playback. A shrug, a
   maybe or "you decide" is not a yes. A change the user asks for after the playback is played back
   again before saving.
4. **Save.** One `setup_save` call with the agreed changes.
5. **Report.** What was saved, what was not and why, in plain words.
6. **Obsidian.** The coach walks the user through viewing their vault: install Obsidian, choose
   "Open folder as vault", pick the folder they chose for their vault (`job-sluice doctor` in a
   terminal prints it), then open the notes under `Job Applications/`.
7. **Corrections.** The coach invites the user to say what looks wrong, now or in a later session,
   and fixes it the same way: playback, yes, save. A value the save replaced can be put back from
   the save's report.

## The tools

- `setup_status` (read-only, every privilege level) is unchanged, except that it also returns
  `version`: a token identifying the exact state it read (the config text or its absence, each setup
  note's text or its absence, and which vault is in use). The token carries no path.
- `setup_save(changes, version)` replaces `setup_review`. It is registered only under
  `job-sluice mcp serve --write`. It takes the same change shapes.

`setup_save`:

- Validates every change exactly as before (`review.propose`): an unknown target, a malformed value,
  a duplicate, a relative vault path, a control or bidirectional character, or a shape the config
  editor cannot place is set aside with its reason. The form-size limits no longer apply: nothing is
  displayed in a form.
- Refuses the whole save as `stale`, writing nothing, when `version` no longer matches what it reads
  now: the user edited a note or the config (in Obsidian, say) after the coach read them, or a config
  appeared, vanished or changed vault. The coach reads `setup_status` again and plays back anything
  that changed before saving.
- Writes through the same path as before: `Sluice.apply_setup`, config first, behind the config
  check (only declared settings change, each expected value reads back); each note update is
  compare-and-set against the text that was read, and each create is exclusive.
- Returns, per change, `written`, `set_aside` (with reason) or `failed` (with reason), plus
  `restart_needed` when the server could not reload a written config. For every change that
  replaced existing text or a value, the report includes the `previous` text or value, so the coach
  can restore it. No path appears anywhere in the response.

## Retired boards are not offered

The same real session showed `setup_status`'s `kinds.search` listing every registered source,
including the retired ones a source module ships disabled (each carries a `reprobed` date). A search
added to a disabled source never runs. `kinds.search` lists, and `setup_save` accepts a search for,
only a source that `ingest run` would actually run: one its module ships enabled AND the user's
config does not disable (`sources.<id>.enabled: false`) AND `job-sluice ingest disable` has not
switched off -- the one predicate `ingest run` itself uses, `ingest/enabled.py::off_reason`. Config cannot bring back a board its module
ships disabled -- ingest requires both -- so a retired board is never offered, whatever the config
says (corrected during implementation: the first draft of this section assumed config could).

## What goes

The per-change form for setup: form packing, the SEP-2322 input-required round trip and its state
encoding, the `not_shown`, `declined`, `cancelled`, `unsupported_client` and `invalid_state` outcomes,
and the vault digest carried in form state (the version token replaces it). `core/formfit.py` stays:
`verify_evidence` still uses it.

## What stays

Never-clobber (compare-and-set updates, exclusive creates, the stale check), the config check, the
neutrality rules for the coach, the path hygiene of every response, and the rule that no pipeline
stage reads the Role Brief.

## Testing

- `setup_save` end to end through the real server: a first run writes config and notes; an update
  writes and reports `previous`; a stale `version` (a note edited, a config created, removed or
  re-pointed between status and save) writes nothing; set-aside reasons as before; no path in any
  response.
- The playbooks state the playback, the explicit-yes rule, the Obsidian walkthrough and corrections;
  the neutrality sweeps cover the new prose.
- The eval harness can now see writes, since no form is cancelled: a deterministic check that every
  successful `setup_save` came after a user turn and that the saved values were played back, plus
  the sandbox vault's files after the run.
