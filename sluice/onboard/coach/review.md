# Phase 5: Review

The purpose of this phase is to put the agreed changes in front of the user in a form, and to write only what they tick.

## Before the form

Tell the user a form is coming. Each change sits under its own box, and every box starts unticked. Only the changes they tick are written; leaving a box unticked writes nothing and is a perfectly good answer. Ask them to read each change before ticking it.

## Sending the changes

Send every agreed change to `setup_save` as soon as the user has confirmed them, in one batch, in the order the user asked for them, or in the order they were agreed when the user has no preference. Do not wait for a change that is optional or that the user still has to go and fetch, a search address above all: send what is agreed now, and send what arrives later in a later call of its own. A later call carries only new changes, so it is not a re-send. Each change is one unit: its `kind`, its `target` from `setup_status`'s `kinds`, and its `value`, or `clear: true` to return it to its default. A search also carries `label` and `url`, and `remove: true` to remove it. A list setting takes its items comma-separated in `value`.

One form shows about a screen of text. sluice fills it from the front of the batch, in order, letting a later short change take room a long one could not use, and returns the changes that did not fit as `not_shown`. A long piece of text, a Role Brief or Judging Profile section, can take much of a form by itself, so a batch that carries several of them will need more than one form. Tell the user that before the first form. After each form, tell them how many changes are still to come.

Send only what the user agreed to in chat: nothing they declined, nothing they left unanswered, nothing you would have chosen for them.

Send exactly the text the user approved. Do not merge, shorten, reorder or reword an approved section or value on the way to the form. If it has to change, because it is too long for a form or for any other reason, show them the new text and get their yes to it before you send it.

Do what the user asks about the form, and what you told them you would do. If they ask for a smaller form, or for some changes before others, send that. If you described a plan for the batches, follow it; if the plan has to change, say so and why before you send anything. When they are stopping after the research and agreed to save it, the batch is the Role Brief sections they said yes to, and nothing else.

On a first run without a vault, include the `vault_dir` change and explain that if it is left unticked, nothing else can be written yet.

## After the form

Report every unit's outcome to the user in plain words, never as raw field names.

The result's `outcome` is one of these:

- `completed`: read `changes`. Each is `written`, `set_aside` with a reason, or `failed` with a reason (the file could not be read or written, or sluice could not load the result); a written change that replaced something carries `previous`.
- `stale`: something changed after `setup_status` read it, and nothing was written. Call `setup_status` again.
- `config_refused`: sluice cannot load the config file. Quote `detail`; it has to be fixed by hand.

A unit the user left unticked is their answer. Report it as unchanged and move on: do not ask them to reconsider it, and never send it again unless they ask you to.

## Done when

Every unit's outcome has been reported, and anything that needs sending again has been sent or set down for later.
