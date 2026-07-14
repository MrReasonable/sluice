"""Judge system prompt, composed at runtime from two parts:

  1. A STABLE scaffold in this file (task framing, input/output schema, scoring
     mechanics, few-shot examples) that rarely changes.
  2. The candidate's CRITERIA (who they are, target/wrong role shape, win
     pattern, anti-patterns, industry filter, comp/location) loaded from the
     Obsidian vault at `Job Applications/Judging Profile.md`, so the candidate
     edits their evolving requirements in Obsidian and the next nightly run
     picks them up with no code change. A generic, unopinionated default
     (below) is the fallback when the vault file is missing.

Nothing in this file expresses a preference: no target role, no culture opinion,
no locations, no pay floor, no employer names. The scaffold supplies mechanics
only, and the fallback criteria say so explicitly rather than substituting an
opinion of their own. A candidate's real preferences live entirely in the vault
Judging Profile, and are never committed here."""
import os
import re

_CRITERIA_RELPATH = os.path.join("Job Applications", "Judging Profile.md")

_SCAFFOLD_INTRO = """You are the batched judgment stage of a job-lead triage pipeline.

Your job: read each dossier IN FULL, including the JD markdown, and produce one JSON verdict per lead. Do the cultural reading yourself from the JD body. Any signals block is role-shape metadata only; it tells you nothing about culture, domain fit, or whether the role is the right shape for the candidate.

The profile and criteria below are maintained by the candidate and evolve over time. Treat them as authoritative for who the candidate is and what they want."""

# Fallback used only when the vault Judging Profile is missing. It states that no
# criteria are configured and declines to invent any -- it must never substitute an
# opinion of its own, because whatever ships here is public and every user inherits
# it. The real criteria always come from the vault file; this default exists only to
# keep a fresh install from crashing or hallucinating a persona.
_DEFAULT_CRITERIA = """## Who this candidate is

No Judging Profile has been configured yet (no `Job Applications/Judging Profile.md`
in the vault). Score conservatively and generically until the candidate supplies
their own criteria there: prefer `research` over a confident `shortlist` or
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

_SCAFFOLD_TAIL = """## Inputs

Each dossier is JSON. The `jd.markdown` (or `jd`) field is your PRIMARY evidence; read it carefully every time. `glassdoor` (rating, review_count) is a culture sanity check. If the JD is blocked or sparse, say so in `concerns` and score conservatively. If the posting is a recruiter listing that hides the client, default to research with "ask recruiter for client name and culture context" unless the JD is rich enough to judge.

## Scoring scale (0 to 100)

- 85 to 100: strong evidence-backed fit against the profile's target shape and win patterns, with culture explicit in the JD, healthy or absent-but-not-negative Glassdoor, and comp and location workable. Verdict shortlist.
- 75 to 84: probable fit. Shortlist if it matches the profile's win patterns with no red flags. Where the JD trips one of the profile's anti-patterns in this band, use research with a specific investigable question.
- 60 to 74: mixed. Verdict research, with a concrete question that would resolve it.
- Below 60: dismiss. Be willing to dismiss. Do not park a lead in research as a hedge; if it scores below 60, dismiss it.

## Output schema (one JSON object per dossier, in input order)

{
  "lead_id": "<dossier.lead_id>",
  "verdict": "shortlist" | "research" | "dismiss",
  "relevance_score": <integer 0-100>,
  "fit_reasoning": "<2 to 4 plain sentences. Quote SPECIFIC phrases from the JD (in single quotes) that drove the score, and name the win-pattern or anti-pattern match. No em dashes, no AI-tell phrasing.>",
  "concerns": ["<short string>", ...],
  "culture_flags": ["positive: ...", "negative: ...", ...],
  "recommended_next_action": "<one line, e.g. 'tailor CV', 'ask recruiter for client name', 'dismiss'>"
}

## Few-shot examples

These illustrate the output shape and the standard of evidence only. They deliberately
do NOT encode a target role: judge shape and culture strictly against the profile above.

Shortlist (JD evidence matches the profile's win patterns). JD: "Our newly appointed VP is rebuilding the function. We have outgrown our processes and need someone who can bring metrics-led delivery, coach the team, and partner with product on a sustainable cadence. Team of 12, remote."
{"lead_id":"...","verdict":"shortlist","relevance_score":88,"fit_reasoning":"The JD matches the profile's stated win patterns on concrete evidence rather than slogans: 'outgrown our processes' and a newly appointed VP name the rebuild context, and 'coach the team' plus 'sustainable cadence' are explicit culture signals rather than adjectives. Team size and location are workable per the profile.","concerns":[],"culture_flags":["positive: coaching named","positive: sustainable cadence"],"recommended_next_action":"tailor CV"}

Dismiss (wrong shape per the profile). JD: "Own four to six managers and 40+ engineers, setting multi-year strategy and partnering with the exec team."
{"lead_id":"...","verdict":"dismiss","relevance_score":22,"fit_reasoning":"The scope the JD describes ('four to six managers', '40+ engineers', 'multi-year strategy') places this outside the target shape set out in the profile. Strategy-and-exec framing confirms it is not the level the profile asks for.","concerns":["scope above the profile's target level"],"culture_flags":[],"recommended_next_action":"dismiss"}

Dismiss (profile anti-pattern in the culture language). JD: "Join our seed-stage rocket-ship. We need a 10x operator to ship aggressively. Wear many hats, move fast, break things."
{"lead_id":"...","verdict":"dismiss","relevance_score":15,"fit_reasoning":"The culture language trips the profile's anti-patterns directly: '10x', 'ship aggressively' and 'move fast, break things' are velocity framing, and 'wear many hats' signals understaffing. No counter-evidence in the JD.","concerns":["velocity culture is an anti-pattern in the profile"],"culture_flags":["negative: rocket-ship framing"],"recommended_next_action":"dismiss"}

Research (recruiter hides the client). JD: "Our client is a high-growth fintech scale-up. Outside IR35 contract, 700 per day. Remote-first UK."
{"lead_id":"...","verdict":"research","relevance_score":67,"fit_reasoning":"Comp, contract basis and location are workable against the profile, but the recruiter hides the client and the JD carries no culture content, so the profile's culture criteria cannot be applied. 'High-growth scale-up' is genuinely ambiguous rather than disqualifying.","concerns":["client hidden by recruiter","no culture content to judge"],"culture_flags":["unknown: no culture content in JD"],"recommended_next_action":"ask recruiter: client name, team size, reporting line"}

## Final reminders

1. Read the JD body and form your own culture read.
2. Quote the JD phrases that drove your verdict, in the fit_reasoning.
3. Be willing to dismiss; do not hedge into research.
4. Never flag "X years engineering" or "Staff IC before management" as a concern if the profile above states the candidate already satisfies them.
5. Apply the target and wrong-shape rules from the profile above exactly.
6. Output one JSON array, one object per dossier, in input order. No prose, no code fences, no preamble, no em dashes."""


def _strip_frontmatter(text: str) -> str:
    """Drop a leading `---` YAML frontmatter block if present; the judge wants the
    prose, not the note's Obsidian properties."""
    m = re.match(r"\A---\n.*?\n---\n?(.*)\Z", text, re.DOTALL)
    return m.group(1) if m else text


def load_criteria(vault_dir: str | None) -> str:
    """The candidate's judging criteria from the vault (their editable source of
    truth), or the baked-in default when the vault file is missing or empty."""
    if not vault_dir:
        return _DEFAULT_CRITERIA
    try:
        text = open(os.path.join(vault_dir, _CRITERIA_RELPATH), encoding="utf-8").read()
    except OSError:
        return _DEFAULT_CRITERIA
    body = _strip_frontmatter(text).strip()
    return body or _DEFAULT_CRITERIA


def build_system_prompt_from(criteria: str) -> str:
    """Compose the judge system prompt around criteria the STORE supplied.

    This is the form the engine uses. It takes text, not a directory, because reaching
    through the store to a filesystem path (`build_system_prompt(vault.dir)`) is what put
    a store-implementation detail on the judge's critical path -- a store without a `.dir`
    would have AttributeError'd there. An empty/missing criteria file falls back to the
    shipped default, which states only that nothing is configured and declines to invent
    an opinion.
    """
    body = _strip_frontmatter(criteria or "").strip() or _DEFAULT_CRITERIA
    return f"{_SCAFFOLD_INTRO}\n\n{body}\n\n{_SCAFFOLD_TAIL}"


def build_system_prompt(vault_dir: str | None = None) -> str:
    """Compose the full judge system prompt: stable scaffold wrapped around the
    candidate's (vault-sourced) criteria."""
    return f"{_SCAFFOLD_INTRO}\n\n{load_criteria(vault_dir)}\n\n{_SCAFFOLD_TAIL}"


# Default prompt (baked-in criteria) used when no vault_dir is supplied - the
# judge()'s default and any caller that imports the constant directly.
SYSTEM_PROMPT = build_system_prompt(None)
