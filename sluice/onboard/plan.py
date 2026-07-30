"""Pure planning: answers in, two artefact texts out. No I/O, no prompts, no clock.

That purity is the point. The property this feature lives or dies by -- a run that answers nothing
produces a config that expresses nothing -- is then a table test over a dict rather than something
observable only by driving a wizard and reading files back.

The config is RENDERED FROM THE CATALOGUE rather than being a static template with substitution
holes, which makes "every key the wizard can write appears in the file it writes" true by
construction instead of by review.
"""
import re
from dataclasses import dataclass

from sluice.core.criteria import DEFAULT_CRITERIA
from sluice.onboard.emit import flow_list, scalar
from sluice.onboard.questions import catalogue

_SECTION_BLURB = {
    "Vault": "Where your notes live.",
    "You": "Identity used when composing a tailored CV.",
    "Want": "What you are looking for. EVERY key here is optional, and an unset gate passes every\n"
            "lead through rather than filtering on a value you did not choose.",
    "Cost": "Cheap filters applied at scrape time, before anything expensive runs.",
    "Providers": "Which model fills each role. API keys come from the environment, never this\n"
                 "file.",
}

_HEADER = """\
# sluice configuration, written by `sluice init`.
#
# Every key is optional and falls back to a code default. A COMMENTED key is unset, and an unset
# preference gate abstains -- it passes every lead through rather than filtering on a value you did
# not choose. Uncomment a key to turn that gate on.
#
# This file holds personal material, so keep it out of any public repo. Secrets (API keys, private
# hostnames) belong in the environment, not here.
#
# `sluice.yaml.example` in the repo documents every knob, including the ones this wizard does not
# ask about.
"""


@dataclass(frozen=True)
class InitPlan:
    config_dest: str
    config_text: str
    profile_dest: str
    profile_text: str
    notes: tuple = ()


def _unset(value):
    return value is None or value == [] or value == ""


def _render_value(value):
    return flow_list(value) if isinstance(value, list) else scalar(value)


def _render_key(leaf, q, value, indent):
    out = []
    if q.hint:
        out += [f"{indent}# {line}" for line in q.hint.split("\n")]
    if _unset(value):
        # Commented, because an unset key is how a gate abstains. The `<- uncomment` marker matches
        # the convention `sluice.yaml.example` already uses.
        out.append(f"{indent}# {leaf}:   # <- uncomment and set YOUR OWN")
    else:
        out.append(f"{indent}{leaf}: {_render_value(value)}")
    return out


def _grouped(answers):
    """Every catalogue key by its top-level YAML block, in ask order. A question can write more
    than one block (`primary_backend` writes three), so this walks `writes_to`.

    No `default_vault`: nothing here reads `q.default`. Threading it through was provably inert --
    two `build_plan` calls differing only in that argument returned byte-identical text -- and it is
    load-bearing only at `catalogue()`, where `TtyAsker` reads it. Same dead-parameter shape review
    round 1 caught on the sibling `sources=`, one layer down."""
    out = {}
    for q in catalogue():
        for dotted in q.writes_to:
            parts = dotted.split(".")
            block = parts[0] if len(parts) > 1 else ""
            out.setdefault(block, []).append((parts[-1], q, answers.get(q.key)))
    return out


def _render_sources(sources):
    """`sources:` is a mapping keyed by source id, shaped unlike every other block, so it renders
    separately rather than being forced through `_grouped`."""
    out = ["", "# -- Sources " + "-" * 56,
           "# Which boards to scrape, and the searches to run on each. A source with no `searches`",
           "# override runs its own neutral example search."]
    if not sources:
        out += ["# sources:",
                "#   example_source:",
                "#     searches:",
                '#       - ["Example search", "https://example.invalid/jobs"]']
        return out
    out.append("sources:")
    for sid in sorted(sources):
        spec = sources[sid]
        # Through scalar(): a source id is a registry key, but nothing downstream forces it to
        # be YAML-safe, and an unquoted mapping key with a `:` or `#` in it breaks the file.
        out.append(f"  {scalar(sid)}:")
        out.append(f"    enabled: {scalar(bool(spec.get('enabled', True)))}")
        searches = spec.get("searches") or []
        if searches:
            out.append("    searches:")
            out += [f"      - [{scalar(label)}, {scalar(url)}]" for label, url in searches]
    return out


def default_sections() -> dict:
    """`DEFAULT_CRITERIA` split on its own headings: heading -> the shipped prose under it.

    DERIVED, so there is no second copy of the heading list to drift. v1 hand-wrote the five and
    pinned them by equality against this source; splitting the source removes the duplicate
    instead of testing for it.
    """
    parts = re.split(r"^(#{2,3} .+)$", DEFAULT_CRITERIA, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


PROFILE_HEADINGS = tuple(default_sections())

# heading -> (answer key, the prompt shown when it is unanswered). The prompts ask what the judge
# needs and propose no answer: a wizard suggesting "a startup, or an enterprise?" would ship an
# opinion exactly as a default would.
_PROFILE_PROMPTS = {
    "## Who this candidate is": (
        "who", "Replace the paragraph above with your background and what you are optimising\n"
               "this search for. The judge treats it as authoritative for who you are."),
    "### Target and wrong shape": (
        "target_shape", "Replace the paragraph above with the shape of role you want and the\n"
                        "shape that is wrong. Scope, level and titles are all fair game -- the\n"
                        "judge reads this as prose."),
    "### Background grounding": (
        "grounding", "Replace the paragraph above with history the judge should assume you\n"
                     "already satisfy, so it stops raising those as concerns."),
    "## Win patterns and anti-patterns": (
        "patterns", "Replace the paragraph above with wording in a job ad that attracts you and\n"
                    "wording that repels you. Quote what you actually see."),
    "## Industry filter (judgement-based, not categorical)": (
        "industry", "Replace the paragraph above with sectors you will and will not work in.\n"
                    "Leave it as-is if you have no sector view."),
}


def _render_profile(profile_answers):
    """Every heading present. An UNANSWERED heading keeps `DEFAULT_CRITERIA`'s own prose.

    That is the round-1 Critical. `build_system_prompt_from` falls back to `DEFAULT_CRITERIA` only
    when the criteria text is missing or EMPTY, and this file is never empty -- so emitting bare
    headings would permanently strip the four instructions telling the judge to abstain ("prefer
    `research`", "do not score on role shape", "do not assume a culture preference", "never invent
    past employers") while the surrounding scaffold still tells it to treat the profile as
    authoritative and to be willing to dismiss. An unconfigured install would stop abstaining: the
    672ad2a class, delivered by the feature built to fix onboarding.

    Carrying the default prose means the shipped abstain instructions stay live until a human
    replaces them -- the default IS used when the user does not answer.

    No frontmatter: `_strip_frontmatter` drops a leading `---` block before the judge sees it.
    """
    sections = default_sections()
    out = ["# Judging Profile", "",
           "The criteria sluice judges every lead against. Edit it in Obsidian whenever your",
           "search changes; the next run picks it up with no code change.",
           "",
           "Nothing here is shipped by sluice as an opinion about which jobs are good. The text",
           "below each heading is the neutral default: it tells the judge to abstain where it has",
           "no information. Replace it with your own and the judge starts using yours.",
           ""]
    for heading in PROFILE_HEADINGS:
        key, prompt = _PROFILE_PROMPTS[heading]
        answer = (profile_answers or {}).get(key)
        out += [heading, ""]
        if answer:
            out += [answer.strip(), ""]
        else:
            out += [sections[heading], "", "<!--", prompt, "-->", ""]
    return "\n".join(out).rstrip() + "\n"


def _render_config(answers, sources):
    lines = [_HEADER]
    grouped = _grouped(answers)
    # Keyed on (section, BLOCK), not on section alone. A bare section key hoisted each header to
    # whichever block happened to hold its first question, and the two blurbs that carry SAFETY
    # information were the ones it stranded: `-- Want` rendered at root above `lead_ttl_days` alone,
    # so "EVERY key here is optional, and an unset gate passes every lead through" -- the abstain
    # doctrine's only appearance beside a gate -- never reached the six triage gates it describes;
    # and "API keys come from the environment, never this file" rendered under `cv:` only, missing
    # both other blocks that take a provider key.
    #
    # This still fixes what the hoist was added for. The triple emission came from ONE fan-out
    # question writing three blocks; per (section, block) it contributes one key to each, so its
    # header appears once per block rather than three times in one.
    sections_seen = set()

    for block in [""] + [b for b in grouped if b]:
        entries = grouped.get(block, [])
        if not entries:
            continue
        indent = "  " if block else ""
        body = []
        for leaf, q, value in entries:
            if q.section and (q.section, block) not in sections_seen:
                sections_seen.add((q.section, block))
                body.append("")
                body.append(f"{indent}# -- {q.section} " + "-" * max(0, 56 - len(q.section)))
                body += [f"{indent}# {ln}" for ln in _SECTION_BLURB.get(q.section, "").split("\n")
                         if ln]
            body += _render_key(leaf, q, value, indent)
        if block:
            # The header is ACTIVE even when every key beneath it is unset, and that is load-bearing
            # for the file's own headline instruction.
            #
            # It used to render commented (`# triage:`) on the theory that a bare `triage:` parsing
            # as `{'triage': None}` was "a coupling nobody asked for". That reasoning was simply
            # wrong: all four loaders already do `(yaml.safe_load(f) or {}).get(BLOCK) or {}`, so a
            # null block has always been fine -- verified against all four.
            #
            # What the commented header DID do was make the file lie. Every key under it renders at
            # indent 2, so a user following `# <- uncomment and set YOUR OWN` on a nested key got an
            # indented key with no parent and a PyYAML ParserError pointing at line 1 rather than
            # the line they edited. Measured: 16 of 19 keys were in that state, and the header prose
            # "Uncomment a key to turn that gate on" was false for every one of them.
            #
            # Body lines are NOT re-prefixed. Every line in `body` is already a comment, and
            # double-commenting produced `#   # accept_titles:`, which defeated the scope guard's
            # own matcher on 16 of 19 keys while the neutrality half stayed green.
            lines.append("")
            lines.append(f"{block}:")
            lines += body
        else:
            lines += body

    lines += _render_sources(sources)
    return "\n".join(lines).rstrip() + "\n"


def _notes(answers):
    """What the config will DO, in plain terms. Written because the shipped example once handed
    every copier an active `relevance_keep` that discarded every title but one, and nothing
    anywhere said so."""
    out = []
    for q in catalogue():
        value = answers.get(q.key)
        if _unset(value) or value == 0 or not q.consequence:
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        out.append(q.consequence.format(value=shown))
    return tuple(out)


def build_plan(answers, *, config_dest, profile_dest,
               profile_answers=None, sources=None) -> InitPlan:
    """The two artefacts `sluice init` writes, as text.

    `answers` holds only the questions the user actually answered -- a skipped question is ABSENT,
    never present-and-empty, so a blank cannot be mistaken downstream for a deliberate empty list.
    """
    sources = sources or {}
    return InitPlan(config_dest=config_dest,
                    config_text=_render_config(answers, sources),
                    profile_dest=profile_dest,
                    profile_text=_render_profile(profile_answers),
                    notes=_notes(answers))
