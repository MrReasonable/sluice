# Phase 5: Review and save

The purpose of this phase is to show the user everything you are about to save, save it only when they say yes, and then help them check the result in their own vault.

## The playback

Before anything is saved, call `setup_status` again, so you play back against what is there now, and keep the `version` it returns: `setup_save` needs it.

Then play back everything you are about to save, in plain words, in one message, grouped by where it goes:

- **Your sluice settings.** Each setting with its value, and for each one you are leaving empty, what leaving it empty means: an empty gate passes every lead.
- **The Judging Profile.** Each section, in full, in the user's words.
- **The Candidate Profile.** Each field, as it will appear on a CV.
- **The Role Brief.** Each section, in full, with every source under "Sources consulted".
- **Searches.** Each board, with its label and the address the user pasted.

Where a value or a section replaces one the user already has, show both: what is there now, from `setup_status`, and what will replace it. Leave out a group that has nothing in it.

Play back exactly the text you will save. Do not merge, shorten, reorder or reword an agreed section or value between the playback and the save. If it has to change, play the new text back and get a yes to it.

On a first run without a vault, the `vault_dir` setting is in the playback, and nothing else can be saved until it is.

## An explicit yes

Ask the user whether to save all of this. Save only when they say yes to that playback. A shrug, a maybe, "you decide" or silence is not a yes; ask again, or ask what they would like to change. If they ask for a change, make it and play back again what changed before saving. If they say no, nothing is saved; ask what they would like to do.

Do not hold the save for a change that is optional or that the user still has to go and fetch, a search address above all: save what is agreed now, and save what arrives later the same way, with its own playback and its own yes.

Do what the user asks about the save, and what you told them you would do. If they want some changes saved before others, save those first, each save with its own playback and yes. If you described a plan, follow it; if it has to change, say so and why before you save anything.

When they are stopping after the research and agreed to save it, the playback is the Role Brief sections they said yes to, and nothing else.

## Saving

Call `setup_save` once with the agreed changes and the `version` from your last `setup_status`. Each change is one unit: its `kind`, its `target` from `setup_status`'s `kinds`, and its `value`, or `clear: true` to return it to its default. A search also carries `label` and `url`, and `remove: true` to remove it. A list setting takes its items comma-separated in `value`. Send only what the user said yes to: nothing they declined, nothing they left unanswered, nothing you would have chosen for them.

## Reporting what happened

Report the result to the user in plain words, never as raw field names: what was saved, what was not, and why. The result's `outcome` is one of these:

- `completed`: read `changes`. Each is `written`; `set_aside` with a reason (sluice would not save it as given: a target or a value it cannot take, a duplicate, a relative vault path, a hidden control or direction character, a config shape it cannot edit, or a note that cannot be read); or `failed` with a reason (a file could not be read or written, or sluice could not load the result). When the reason is something you got wrong, a target or a value's shape, correct it and offer to save that change again, with its own playback. When the reason names something to do by hand, tell them what. A `failed` change was not saved; say what the reason names and offer to save it again once that is fixed.
- `stale`: something changed after `setup_status` read it, and nothing at all was saved. Most often the user edited a note in Obsidian, or the config file changed. Call `setup_status` again, play back anything that differs from what they agreed to, and save only after a new yes.
- `config_refused`: sluice cannot load the config file. Quote `detail`; it has to be fixed by hand.
- `failed`, with a `reason` and no `changes`: the save stopped unexpectedly. Call `setup_status` to see what was saved, and tell the user.

Beside `outcome`:

- `previous`, on a written change: the value or text it replaced. Keep it; it is how a change is undone.
- `not_restorable`, on a written change: it replaced a value of theirs that cannot be put back through `setup_save`. Keep what it says; it names the setting to edit by hand.
- `artefacts`: what a first run created that no change stands for, the default Judging Profile and the Leads view. When one is not `written`, tell the user which and why.
- `restart_needed`: empty unless the config was saved but the server could not reload it. When it is set, ask the user to restart the sluice server before anything else is saved.

A save changes what `setup_status` reads, so call it again before the next save.

## Looking at it in Obsidian

The user's notes live in a folder on their computer, their vault, and Obsidian is how they read and edit it. Walk them through it, one step at a time, at their pace:

1. Install Obsidian, if they do not have it, from its own website.
2. In Obsidian, choose "Open folder as vault".
3. Pick the folder their vault is in. On a first run where they named it in this conversation, it is the `vault_dir` they agreed to: say it back to them exactly as they typed it. Otherwise, `job-sluice doctor` in a terminal prints it, but only when that terminal uses the same `VAULT_DIR` the sluice server was registered with; a different one, or none, can print another folder, so check with them which one the server uses.
4. Open the notes under `Job Applications/`: the Judging Profile, the Candidate Profile and the Role Brief.

Ask them to read each note and say what looks wrong or missing.

## Corrections

Invite corrections now and in any later session. Fix each one the same way: play back the change, hear a yes, save it. To put back something a save replaced, send its `previous` as the new value. A change that came back with `not_restorable` cannot be put back through `setup_save`: tell them what it says, the setting to edit by hand in their sluice config file, and send nothing. A change that came back with neither replaced nothing of theirs, so it goes back with `clear: true`. Do this only when they ask.

They can also edit any note in Obsidian themselves at any time. If they do so during this conversation, the next save comes back `stale`; read `setup_status` again and play back what changed.

## Done when

The user has heard what was saved and what was not, knows how to open their vault in Obsidian, and has been invited to correct anything that looks wrong.
