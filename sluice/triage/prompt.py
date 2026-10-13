"""Judge system prompt, composed at runtime from two parts:

  1. A STABLE scaffold in this file (task framing, input/output schema, scoring
     mechanics, few-shot examples) that rarely changes.
  2. The candidate's CRITERIA (who they are, target/wrong role shape, win
     pattern, anti-patterns, industry filter, comp/location), supplied to
     `build_system_prompt_from` as TEXT by the caller. The engine reads that
     text through the STORE (`build_system_prompt_from(store.read_criteria())`,
     triage/engine.py), so the candidate edits their evolving requirements in
     Obsidian and the next nightly run picks them up with no code change. A
     generic, unopinionated default (below) is the fallback when the store has
     nothing. THIS MODULE NEVER REACHES A FILESYSTEM: the directory-taking
     helpers it used to export put a store-implementation detail on the judge's
     critical path, and a store without a `.dir` would have AttributeError'd
     there.

Nothing in this file expresses a preference: no target role, no culture opinion,
no locations, no pay floor, no employer names. The scaffold supplies mechanics
only, and the fallback criteria say so explicitly rather than substituting an
opinion of their own. A candidate's real preferences live entirely in the vault
Judging Profile, and are never committed here."""
import re

from sluice.core.criteria import DEFAULT_CRITERIA
from sluice.core.protocols import CRITERIA_RELPATH, cv_name

# A deliberate DRIFT PIN, not leftover store surface: tests/test_vault_write_document.py asserts
# this alias and core/vault.py's are the SAME object as core/protocols.py's constant, so the three
# cannot silently disagree about where the Judging Profile lives. It names a path SHAPE and is
# never joined to anything here -- this module reaches no filesystem (see the docstring).
_CRITERIA_RELPATH = CRITERIA_RELPATH

_SCAFFOLD_INTRO = """You are the batched judgment stage of a job-lead triage pipeline.

Your job: read each dossier IN FULL, including the JD markdown, and produce one JSON verdict per lead. Do the cultural reading yourself from the JD body. Any signals block is role-shape metadata only; it tells you nothing about culture, domain fit, or whether the role is the right shape for the candidate.

The profile and criteria below are maintained by the candidate and evolve over time. Treat them as authoritative for who the candidate is and what they want."""

# Imported rather than defined here: `onboard/` also needs it, and a second copy would mean the
# scaffold `sluice init` writes could drift from the fallback the judge uses. The local alias
# mirrors what `_CRITERIA_RELPATH` already does above.
_DEFAULT_CRITERIA = DEFAULT_CRITERIA


_SCAFFOLD_TAIL_HEAD = """## Inputs

Each dossier is JSON. The `jd.markdown` (or `jd`) field is your PRIMARY evidence; read it carefully every time. `glassdoor` (rating, review_count) is a culture sanity check. If the posting is a recruiter listing that hides the client, default to research with "ask recruiter for client name and culture context" unless the JD is rich enough to judge.

A dossier may also carry `followup_answers`: the candidate's own notes of replies to questions an earlier verdict asked them to put. Read them as facts about the role, alongside the JD. A question they answer is no longer open, so do not ask it again in `recommended_next_action`.

Two different failures look alike here and must not be confused:

- The JD field holds something that is NOT a job description at all: a bot-check or CAPTCHA interstitial, a cookie or consent wall, a sign-in page, a 404 or other error body, a search-results or listings index, or navigation chrome with no posting in it. There is nothing to judge, so do NOT score it. Return verdict `unjudgeable`, say in `concerns` what the page actually was, and put `relevance_score` at 0. This routes the lead back for a refetch rather than to a human, who can add nothing a retry will not.
- The JD is a REAL posting that is merely thin, vague or missing detail you would like. That is a judgement you can still make: say what is missing in `concerns` and score conservatively.

## Scoring scale (0 to 100)

- 85 to 100: strong evidence-backed fit against the profile's target shape and win patterns, with culture explicit in the JD, healthy or absent-but-not-negative Glassdoor, and comp and location workable. Verdict shortlist.
- 75 to 84: probable fit. Shortlist if it matches the profile's win patterns with no red flags. Where the JD trips one of the profile's anti-patterns in this band, use research with a specific investigable question.
- 60 to 74: mixed. Verdict research, with a concrete question that would resolve it.
- Below 60: dismiss. Be willing to dismiss. Do not park a lead in research as a hedge; if it scores below 60, dismiss it."""

_SCAFFOLD_TAIL_REST = """

## Output schema (one JSON object per dossier, in input order)

{
  "lead_id": "<dossier.lead_id>",
  "verdict": "shortlist" | "research" | "dismiss" | "unjudgeable",
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

Unjudgeable (the fetched page is not a posting). JD: "Verify you are human. Enable JavaScript and cookies to continue. Troubleshooting: error 1020, Ray ID 8ab3f9. Performance and security by a CDN."
{"lead_id":"...","verdict":"unjudgeable","relevance_score":0,"fit_reasoning":"The JD field holds a bot-check interstitial, not a job posting: 'Verify you are human', 'Enable JavaScript and cookies' and a CDN error reference are the whole body. There is no posting text to judge, so no score would mean anything.","concerns":["JD body is a bot-check page, not a job description"],"culture_flags":[],"recommended_next_action":"refetch"}

## Final reminders

1. Read the JD body and form your own culture read.
2. Quote the JD phrases that drove your verdict, in the fit_reasoning.
3. Be willing to dismiss; do not hedge into research.
4. Never flag "X years engineering" or "Staff IC before management" as a concern if the profile above states the candidate already satisfies them.
5. Apply the target and wrong-shape rules from the profile above exactly.
6. Output one JSON array, one object per dossier, in input order. No prose, no code fences, no preamble, no em dashes.
7. If the JD field is not a job posting at all, return `unjudgeable` rather than scoring it. A score on page chrome is a guess wearing a number, and it costs a human a review that only a refetch can resolve."""


def _strip_frontmatter(text: str) -> str:
    """Drop a leading `---` YAML frontmatter block if present; the judge wants the
    prose, not the note's Obsidian properties."""
    m = re.match(r"\A---\n.*?\n---\n?(.*)\Z", text, re.DOTALL)
    return m.group(1) if m else text


def _strip_html_comments(text: str) -> str:
    """Drop `<!-- ... -->` blocks before the judge sees the criteria.

    These are addressed to the USER, not to the model. `sluice init` scaffolds five of them
    ("Replace the paragraph above with ..."), and before this they reached the system prompt
    verbatim -- inside a scaffold that tells the model to treat the criteria as authoritative. So
    a fresh install handed the judge five instructions written for a human, one of which says the
    profile "treats it as authoritative for who you are". A user's own notes-to-self in their
    Judging Profile are the same shape and equally not criteria.

    BOTH syntaxes. HTML comments are the Markdown form, and `%%...%%` is Obsidian's -- which
    matters because this document's own header tells the user to "edit it in Obsidian whenever your
    search changes". A claim that HTML comments were "the whole class" was measured false: a
    `%% note to self %%` survived and reached the judge as criteria.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return re.sub(r"%%.*?%%", "", text, flags=re.DOTALL)

# Split at the two places #196 inserts the candidate record's rules, and rejoined here so
# every existing reader of the scaffold sees the exact pre-#196 text. Named constants
# rather than a string splice: a splice on a drifted anchor no-ops silently.
_SCAFFOLD_TAIL = _SCAFFOLD_TAIL_HEAD + _SCAFFOLD_TAIL_REST

# #196. The section is named "Candidate record", not "evidence": the scaffold already uses
# that word for the JD ("your PRIMARY evidence", "evidence-backed fit"), and the judge must
# not read a requirement for CANDIDATE evidence into those lines. The closing sentence is
# scoped to exactly those scaffold lines, because the coverage rule below does use
# "verified evidence" for the record (the product's term, and the concern string).
_CANDIDATE_RECORD_HEADER_PROMPT = """## Candidate record

The candidate's own verified record, one line per entry. It describes the CANDIDATE, never the role, so never quote it as JD text. Where the instructions below call the JD your "evidence" (its PRIMARY evidence, an evidence-backed fit), they still mean the JD."""

# D2 of the #196 spec: a gap is a concern, never a score. It names the two existing lines
# it qualifies, so neither can be read against it.
_RECORD_COVERAGE_PROMPT = """## Using the candidate record

Where the candidate record supports a requirement the JD states, that supports the fit and may raise the score. Where the JD states a requirement the record does not cover, add "no verified evidence for <requirement>" to `concerns`. Such a gap is never on its own a reason to lower `relevance_score` or to move the verdict down a band, because the record may simply be incomplete. "Be willing to dismiss" applies to what the JD and the profile say, not to gaps in the record. The thin-JD instruction to score conservatively is about what the JD leaves out, not about the record. Final reminder 4 still applies: do not raise a gap concern for anything the profile already says the candidate satisfies."""

# Unnumbered, after the numbered reminders, so the list's numbering is untouched.
_RECORD_REMINDER_PROMPT = """A requirement the candidate record does not cover is a concern, never a lower score."""


def _one_line(value) -> str:
    # A hand-typed block list or multi-line value must not start a new prompt line: it
    # could forge a heading. Vault text is the user's own; this guards accidental
    # structure, not a hostile author.
    return " ".join(str(value or "").split())


def _record_block(record) -> str:
    """The candidate-record section, or "" when no kind has an entry -- the abstain path.
    Generic over kinds: heading from the kind's relpath basename (EvidenceKind has no label
    of its own), name from `cv_name`, then each non-empty `judge_fields` member in order."""
    sections = []
    for spec, entries in record or ():
        if not entries:
            continue
        lines = [f"### {spec.relpath.rsplit('/', 1)[-1]}"]
        for entry in entries:
            fields = entry.get("fields") or {}
            parts = [_one_line(cv_name(entry))]
            parts += [f"{key}: {_one_line(fields.get(key))}" for key in spec.judge_fields
                      if _one_line(fields.get(key))]
            lines.append("- " + " | ".join(parts))
        sections.append("\n".join(lines))
    if not sections:
        return ""
    return _CANDIDATE_RECORD_HEADER_PROMPT + "\n\n" + "\n\n".join(sections)


def build_system_prompt_from(criteria: str, record=None) -> str:
    """Compose the judge system prompt around criteria the STORE supplied.

    This is the form the engine uses. It takes text, not a directory, because reaching
    through the store to a filesystem path (`build_system_prompt(vault.dir)`) is what put
    a store-implementation detail on the judge's critical path -- a store without a `.dir`
    would have AttributeError'd there. An empty/missing criteria file falls back to the
    shipped default, which states only that nothing is configured and declines to invent
    an opinion.

    `record` (#196) is `(EvidenceKind, entries)` pairs the ENGINE read through the store;
    this module still does no I/O and reads no registry. With no entries the prompt is the
    pre-#196 text byte for byte, which is what makes `triage.evidence` abstain.
    """
    body = _strip_html_comments(_strip_frontmatter(criteria or "")).strip() or _DEFAULT_CRITERIA
    block = _record_block(record)
    if not block:
        return f"{_SCAFFOLD_INTRO}\n\n{body}\n\n{_SCAFFOLD_TAIL}"
    return (f"{_SCAFFOLD_INTRO}\n\n{body}\n\n{block}\n\n{_SCAFFOLD_TAIL_HEAD}\n\n"
            f"{_RECORD_COVERAGE_PROMPT}{_SCAFFOLD_TAIL_REST}\n\n{_RECORD_REMINDER_PROMPT}")


# The baked-in default prompt -- `judge()`'s default, and what any caller importing the constant
# gets. Built from the shipped criteria DIRECTLY rather than through a vault_dir-taking helper: the
# engine reads criteria through the STORE (`build_system_prompt_from(store.read_criteria())`,
# triage/engine.py), and the directory-taking forms that used to live here had no non-test caller
# left. Keeping them kept alive the exact reach-through-the-store-to-a-path shape the seam refactor
# removed, on a module a second store must never make assumptions about.
SYSTEM_PROMPT = build_system_prompt_from(_DEFAULT_CRITERIA)
