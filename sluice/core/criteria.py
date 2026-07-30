"""The shipped, opinion-free judging criteria: what the judge uses when the user has not
written their own.

It lives in `core/` alongside `CRITERIA_RELPATH` (which is in `core/protocols.py`, the
contract naming WHERE the document lives) --
because two packages now need it and neither owns it. `triage/prompt.py` uses it as the
missing-file fallback; `onboard/plan.py` splits it on its own headings to build the scaffold
`sluice init` writes, so that an unanswered heading still carries these abstain instructions
(without that, running the onboarding command would make an unconfigured install STOP abstaining).

It was previously a leading-underscore private in `triage/prompt.py`, imported across a package
boundary at module scope -- the only such import in `sluice/`, and inconsistent with this same
change promoting `CRITERIA_RELPATH` to `core/protocols.py` for exactly the reason. A triage author
editing this prose had no local signal that `sluice init` parses its heading structure; now the
prose and the contract that names it sit together.

Nothing here expresses a preference. Whatever ships in this file is public and every user inherits
it, so it states that no criteria are configured and declines to invent any.
"""

# Fallback used only when the vault Judging Profile is missing. It states that no
# criteria are configured and declines to invent any -- it must never substitute an
# opinion of its own, because whatever ships here is public and every user inherits
# it. The real criteria always come from the vault file; this default exists only to
# keep a fresh install from crashing or hallucinating a persona.
DEFAULT_CRITERIA = """## Who this candidate is

No judging criteria have been supplied yet -- what follows is the shipped default,
not anything the candidate wrote. Score conservatively and generically until the
candidate supplies their own criteria in their Judging Profile: prefer `research` over a confident `shortlist` or
`dismiss` whenever the fit depends on personal history, target role shape, company
size, culture, comp or location preferences this default has no way to know.

### Target and wrong shape

Not configured. This default deliberately expresses NO opinion about which roles the
candidate wants or does not want. That belongs in their vault Judging Profile, never
in shipped code. Until it is configured, do not score on role shape: note in
`concerns` that no role-shape criteria are available and prefer `research`.

### Background grounding

This default has no information about the candidate's employment history, seniority,
or ambitions, so never invent or assume past employers, skills, achievements, or a
desired role. Judge only from what the JD itself states.

## Win patterns and anti-patterns

Not configured. Do not assume a culture preference. Report the culture signals the JD
actually contains in `culture_flags`, neutrally, and let the candidate's own criteria
(once configured) decide whether they are positive or negative.

## Industry filter (judgement-based, not categorical)

No industry preferences are configured by default. Do not filter on sector alone; note
any sector concerns in `concerns`. When the JD is ambiguous about what the product does,
default to research with a specific question rather than guessing from sector keywords."""
