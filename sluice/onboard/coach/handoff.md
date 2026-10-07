# Phase 6: Hand-off

Call `doctor` (its default, offline, is enough). It reports what is ready and what still needs action. Read it, then tell the user, in plain words, what is left that this session does not do:

- **Verifying evidence.** sluice writes a CV only from evidence entries the user has verified: their experience, skills and stories. Adding and verifying them is the user's own step, with `job-sluice experience add` and `job-sluice experience verify` (and the same for `skills` and `stories`), or through this server's evidence tools, where every entry is approved by the user in a form. You never verify anything for them.
- **The CV Layout.** The note `Job Applications/CV Layout.md` in their vault sets out the roles on their CV, with dates, and which experience entries each role may draw on. No CV can be composed until it exists.
- **Job-board logins.** Some boards only show results to a logged-in user, in the browser sluice drives. Logging in is something the user does; you cannot do it for them.
- Anything else `doctor` reports as needing action, explained in a sentence each.

## How to come back

Tell them they can return whenever the hunt needs revising: a new direction, a gate that turned out too tight or too loose, a change in circumstances. They start this same conversation again; in Claude Code that is `/mcp__sluice__career_interview`, optionally followed by what they want from the session. If they added the sluice server under a different name, that name replaces `sluice` in the command.

## Closing

Finish with a short summary: what was set up, what was left empty on purpose and what that means, and the one next step that matters most. Keep it brief and accurate; do not promise results.
