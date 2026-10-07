# Phase 5: Review

The purpose of this phase is to put the agreed changes in front of the user in a form, and to write only what they tick.

## Before the form

Tell the user a form is coming. Each change sits under its own box, and every box starts unticked. Only the changes they tick are written; leaving a box unticked writes nothing and is a perfectly good answer. Ask them to read each change before ticking it.

## Sending the changes

Call `setup_review` once, with every agreed change in one batch. Each change is one unit: its `kind`, its `target` from `setup_status`'s `kinds`, and its `value`, or `clear: true` to return it to its default. A search also carries `label` and `url`, and `remove: true` to remove it. A list setting takes its items comma-separated in `value`.

Send only what the user agreed to in chat: nothing they declined, nothing they left unanswered, nothing you would have chosen for them.

On a first run without a vault, include the `vault_dir` change and explain that if it is left unticked, nothing else can be written yet.

## After the form

Report every unit's outcome to the user in plain words, never as raw field names.

- `completed`: read `units`. Each unit is `written`, `declined` (left unticked), `conflict`, or `set_aside` with a reason.
- `declined` or `cancelled`: they closed or declined the whole form, and nothing was written. Ask what they would like to do next.
- `set_aside`, at the top of the result: changes that could not be shown or applied, each with its reason. Explain the reason. When it is something you got wrong, a target or a value's shape, correct it and offer to send that change again. When the reason names something to do by hand, tell them what.
- `not_shown`: there were more changes than one form could show. Send exactly those again in a new call. They were never shown, so sending them is not re-sending.
- `conflict`: the file changed between the form being shown and the write, often because the user was editing the note themselves. Call `setup_status` again and offer to re-propose those changes against what is there now.
- `restart_needed`: the config was written, but the server could not reload it. Ask the user to restart the sluice server before any further changes.
- `config_refused`: sluice cannot load the config file. Quote `detail`; it has to be fixed by hand.
- `unsupported_client`: this client cannot show the form, so nothing can be written from this session. Tell the user. A new hunt can be created in a terminal with `job-sluice init`; an existing one is edited by hand in the config file and the vault notes.
- `nothing_to_review`: every change was set aside. Explain each reason.

A unit the user left unticked is their answer. Report it as unchanged and move on: do not ask them to reconsider it, and never send it again unless they ask you to.

## Done when

Every unit's outcome has been reported, and anything that needs sending again has been sent or set down for later.
