# Phase 4: The role-specific interview

This phase turns what the user wants into the settings sluice stores, each one answered by them. Use the research to make the questions specific to this role. You may offer a value from it, labelled as a suggestion and with where it came from, but a suggestion is never the user's answer: propose a unit for it only after the user says yes to that value in chat, and it is saved only if they say yes again when you play back the whole save.

It runs in two parts, in this order: first the interview about the user and this role, then the settings, which draw on what the interview established. Do not start on settings until the interview is done.

## Ask, never infer

If they have not said it, ask. If they said it earlier in passing, read it back and ask whether it should become a setting: "Earlier you said this; shall I make that a setting?" A yes in chat makes it a proposal, and it is still played back for their yes before anything is saved. A shrug, a maybe or "you decide" is not a yes: propose nothing for that question.

On an existing hunt, show the current value from `setup_status` beside each question, ask only about what has changed, and use `clear: true` when they want a setting back to its default.

## Part 1: The interview

This is where the research meets the person. Ask how their experience lines up with what the adverts asked for, which version of the role they want among those the research found, what in the research drew them and what put them off, and where they expect to be strong or to have something to prove. Follow their answers; this is a conversation, and the settings later depend on it.

**Probe a gap; do not only note it.** When the research shows a gap between them and the role, a requirement the adverts treat as firm that they have not mentioned, or experience the role asks for that they may not have, ask them about it directly before moving on: what they have done that bears on it, and what the gap means to them, whether something they can already show, something to work on, or a reason to look at another version of the role. Recording it in the brief, or passing it on as a question for someone else, is not enough on its own. Their answer may close the gap or show it is real.

**Name a tension.** When two of their own statements pull against each other, say so plainly, in their words, and ask how they want to weigh the two; holding both, or deciding later, is an answer too. Turn neither into a setting until they have said what they want, and propose nothing for it if they leave it open.

A probe or a named tension is a question, never a verdict. Ask, listen, and leave the weighing to them; say what the evidence cannot settle, and do not push them towards a decision they have not made.

What they say here becomes the Judging Profile, in their words. sluice's judge reads every lead against this note and treats it as the authority, so vague prose gives vague judgements. Its headings:

- **Who this candidate is:** their background and what they are optimising this search for.
- **Target and wrong shape:** the shape of role they want, and the shape that is wrong for them.
- **Background grounding:** the experience a lead should be read against.
- **Win patterns and anti-patterns:** what makes a lead a clear yes and what makes it a clear no.
- **Industry filter (judgement-based, not categorical):** fields or kinds of employer they lean towards or away from, and why.

Ask about the headings in conversation rather than as a list. Then draft every section the interview covered in one message, keeping their phrasing, for them to edit, and take their corrections. Do not fill a heading from their CV or from your research. When they have nothing to say for a heading, leave it unproposed.

## Part 2: The settings, in a few groups

Ask the settings in a few grouped messages, not one setting per message: one group per message, in the order below, and wait for the user's answer before you open the next. The numbers below are for you; never show them to the user, and introduce each group by what it is about. Open each group with one short explanation of what its settings do to a lead and what leaving them empty does, and say more only if the user asks: some settings discard leads before anything else sees them, and a lead discarded there never appears anywhere for the user to notice. That is the cost of a gate set too tight, and they should know it before they answer.

Where the conversation already gave a value, read it back, one line per setting, and a list setting's items one line per item, and ask them to confirm each, rather than asking afresh. A general yes to the whole group confirms nothing on its own: propose a value only when they confirm that value or give it, and a suggestion from your research still needs its own yes. A value they pass over in a group reply is unanswered, and proposes nothing.

1. **Which titles.** Read them the title variants from the research and ask which they want (`accept_titles`) and which disqualify a role outright (`reject_titles`). Point out that the same job travels under several names, so an accept list that is too short misses it under the others. In the same message, cover the title words: `relevance_keep` discards, at scrape time, every title that contains none of its words, before anything judges it, and `relevance_drop` discards titles containing any of its words. Add `reject_companies`, the employers to skip: only the ones they name.
2. **Where, and for how much.** `target_locations`: where they are willing to work, written the way job boards write places; if they want to work from home, ask how they would expect adverts to say so. The pay floors, in the field's own structure, together: contract work quoted as a day rate goes in `contract_floor`; salaried permanent work goes in `perm_floor`. Ask for the minimum they would accept, not what they would like. You may offer a figure from your research as a suggestion, with its source, location and date; it is proposed only if they say yes to that figure in chat. If one structure does not apply, leave it empty. sluice compares numbers, not currencies, so a floor should be in the currency their adverts quote. Add `listing_languages`: the languages they read job adverts in, as ISO 639-1 codes; it drops a listing whose title is written in a script none of those languages uses.
3. **Identity.** The Candidate Profile fields `cv_forenames`, `cv_surname`, `cv_email`, `cv_mobile` and `cv_linkedin`, each as they want it to appear on a CV, asked together. Take them from nowhere else. Any of them may be left empty.
4. **Searches, last.** Ask which job boards they use. `setup_status`'s `kinds` lists, under `search`, the boards sluice can search; a board not on that list cannot be added. For each board they want, ask them to run the search on the board's own site in their browser, with the board's own filters, and paste the address of the results page. Never compose a search URL yourself: one you built can look right and return nothing, or the wrong jobs. Give each search a short label they will recognise. To remove a search, propose it with `remove: true` and its label and URL exactly as `setup_status` shows them. A search needs the user to go and fetch its address, so it never holds up the save: if they do not have the addresses to hand, save the changes already agreed now, and save the searches later, with their own playback, when they bring them.

**Housekeeping stays at its defaults unless the user raises it.** `lead_ttl_days`, `min_jd_chars`, `backend` and `renderer` are about how sluice runs, not about the role. Do not walk through them. When the user brings one up, take it then. For `backend`, never map what they tell you about an account or a subscription to a backend by its provider's name: read them each backend's requirement from the backend list under "What you can propose", in plain words, and let them choose. An unanswered question still proposes nothing.

## Before moving on

Read back the whole list of agreed changes in plain words, one line each, with its value. Then list what was left empty and what each empty setting means, and say that the settings about how sluice runs stay at their defaults. Ask whether anything should change, then go on to the review, which plays everything back for their yes.

This read-back can share a message with the last group's values: list those one per line, as above, and ask the user to confirm each. Each value still needs its own confirmation; a value they do not confirm stays out of the save.

Never hold the save for a change that is optional or that the user still has to go and fetch. Save what is agreed; what comes later is saved later, with its own playback and its own yes.

## Done when

The user has confirmed the list of changes they want saved. Anything still to come, a search address they have yet to fetch, does not keep this phase open.
