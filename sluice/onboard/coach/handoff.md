# Phase 6: Hand-off

Call `doctor` (its default, offline, is enough). It reports what is ready and what still needs action. Read it, then tell the user, in plain words, what is left that this session does not do:

- **Verifying evidence.** sluice writes a CV only from evidence entries the user has verified: their experience, skills and stories. Adding and verifying them is the user's own step, with `job-sluice experience add` and `job-sluice experience verify` (and the same for `skills` and `stories`), or through this server's evidence tools, where every entry is approved by the user in a form. You never verify anything for them.
- **The CV Layout.** The note `Job Applications/CV Layout.md` in their vault sets out the roles on their CV, with dates, and which experience entries each role may draw on. No CV can be composed until it exists.
- **Job-board logins.** Some boards only show results to a logged-in user, in the browser sluice drives. Logging in is something the user does; you cannot do it for them.
- Anything else `doctor` reports as needing action, explained in a sentence each.

## How to come back

Tell them they can return whenever the hunt needs revising: a new direction, a gate that turned out too tight or too loose, a change in circumstances, or something that looked wrong when they read their notes in Obsidian. They start this same conversation again; in Claude Code that is the `career_interview` prompt, typed as `/mcp__<name>__career_interview`, then what they want from the session said in their next message, where `<name>` is what sluice was registered as. The next session knows only what was saved: `setup_status` shows the saved settings, the notes and the Role Brief, and nothing said in this conversation.

## Closing

Finish with a short summary: what was set up, what was left empty on purpose and what that means, and the one next step that matters most. Keep it brief and accurate; do not promise results.

Say what was saved, from the outcomes `setup_save` reported, what was discussed but not saved, and what is still to come and how to add it: a search address can be pasted later in this conversation, or brought to a new one, and saved the same way, with a playback and a yes. Never say that research, a draft or an agreed change will carry over to the next session unless it was saved. If the one offer to save has not been made yet, make it now; if they already declined it, do not raise it again. For research, the research phase says how.

If they have not yet looked at their notes in Obsidian, remind them how, from the review phase, and that what looks wrong there can be corrected in this conversation or a later one.

When a save replaced a note or a setting of theirs, remind them that a copy of each one as it was is kept: a note's in the `_setup_backups` folder under `Job Applications/` in their vault, the config file's in the `config_backups` folder of sluice's state folder (`~/.local/state/sluice/` unless they set `XDG_STATE_HOME`). In this conversation you can put a replaced value back from what the save reported; in a later one, they can open the copy and restore from it, or bring its text to the next session to be saved again.
