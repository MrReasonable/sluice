# Phase 4: The role-specific interview

This phase turns what the user wants into the settings sluice stores, one question at a time, each one answered by them. Use the research to make the questions specific to this role. You may offer a value from it, labelled as a suggestion and with where it came from, but a suggestion is never the user's answer: propose a unit for it only after the user says yes to that value in chat, and it stays unset unless the user also ticks it in the review form.

## Ask, never infer

If they have not said it, ask. If they said it earlier in passing, read it back and ask whether it should become a setting: "Earlier you said this; shall I put that in the form?" A yes in chat makes it a proposal, and it is still theirs to tick. A shrug, a maybe or "you decide" is not a yes: propose nothing for that question.

Before each gate, explain in a sentence what it does to a lead and what leaving it empty does. Some gates discard leads before anything else sees them, and a lead discarded there never appears anywhere for the user to notice. That is the cost of a gate set too tight, and they should know it before they answer.

On an existing hunt, show the current value from `setup_status` beside each question, ask only about what has changed, and use `clear: true` when they want a setting back to its default.

## The questions, in order

Cover what applies; skip what they do not care about.

1. **Titles.** Read them the title variants from the research and ask which they want (`accept_titles`) and which disqualify a role outright (`reject_titles`). Point out that the same job travels under several names, so an accept list that is too short misses it under the others.
2. **Title words.** `relevance_keep` discards, at scrape time, every title that contains none of its words, before anything judges it. `relevance_drop` discards titles containing any of its words. Explain both plainly and ask whether they want either.
3. **Locations.** `target_locations`: where they are willing to work, in the forms job boards use. If they want to work from home, ask how they would expect adverts to say so.
4. **Pay floors, in the field's own structure.** Use what the research found about how this field pays. Contract work quoted as a day rate goes in `contract_floor`; salaried permanent work goes in `perm_floor`. Ask for the minimum they would accept, not what they would like. You may offer a figure from your research as a suggestion, with its source, location and date; it is proposed only if they say yes to that figure in chat. If they would consider both structures, ask for both; if one does not apply, leave it empty. sluice compares numbers, not currencies, so the floor should be in the currency their adverts quote.
5. **Employers to skip.** `reject_companies`: only the ones they name.
6. **Languages.** `listing_languages`: the languages they read job adverts in, as ISO 639-1 codes. Explain that this drops a listing whose title is written in a script none of those languages uses.
7. **Housekeeping.** `lead_ttl_days`, `min_jd_chars`, `backend` and `renderer` are about how sluice runs, not about the role. Mention them briefly and set one only when the user wants to. For `backend`, ask which provider they have an account or key for; do not pick one for them.
8. **The Judging Profile, in their words.** sluice's judge reads every lead against this note and treats it as the authority, so vague prose gives vague judgements. Take its headings one at a time:
   - **Who this candidate is:** their background and what they are optimising this search for.
   - **Target and wrong shape:** the shape of role they want, and the shape that is wrong for them.
   - **Background grounding:** the experience a lead should be read against.
   - **Win patterns and anti-patterns:** what makes a lead a clear yes and what makes it a clear no.
   - **Industry filter (judgement-based, not categorical):** fields or kinds of employer they lean towards or away from, and why.

   Ask each heading as a question. Draft the section from their answer, keeping their phrasing, and read it back for them to edit. Do not fill a heading from their CV or from your research. When they have nothing to say for a heading, leave it unproposed.
9. **Identity.** The Candidate Profile fields `cv_forenames`, `cv_surname`, `cv_email`, `cv_mobile` and `cv_linkedin`, each as they want it to appear on a CV. Ask for each one; take them from nowhere else. Any of them may be left empty.
10. **Searches, last.** Ask which job boards they use. `setup_status`'s `kinds` lists, under `search`, the boards sluice can search; a board not on that list cannot be added. For each board they want, ask them to run the search on the board's own site in their browser, with the board's own filters, and paste the address of the results page. Never compose a search URL yourself: one you built can look right and return nothing, or the wrong jobs. Give each search a short label they will recognise. To remove a search, propose it with `remove: true` and its label and URL exactly as `setup_status` shows them.

## Before moving on

Read back the whole list of agreed changes in plain words, one line each, with its value. Then list what was left empty and what each empty setting means. Ask whether anything should change before the form.

## Done when

The user has confirmed the list of changes they want to see in the form.
