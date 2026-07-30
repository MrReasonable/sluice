"""Pure planning: answers in, two artefact texts out. No I/O, no prompts, no clock.

That purity is the point. The property this feature lives or dies by -- a run that answers nothing
produces a config that expresses nothing -- is then a table test over a dict rather than something
observable only by driving a wizard and reading files back.

The config is RENDERED FROM THE CATALOGUE rather than being a static template with substitution
holes, which makes "every key the wizard can write appears in the file it writes" true by
construction instead of by review.
"""
from dataclasses import dataclass

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


def _grouped(answers, default_vault):
    """Every catalogue key by its top-level YAML block, in ask order. A question can write more
    than one block (`primary_backend` writes three), so this walks `writes_to`."""
    out = {}
    for q in catalogue(default_vault=default_vault):
        for dotted in q.writes_to:
            parts = dotted.split(".")
            block = parts[0] if len(parts) > 1 else ""
            out.setdefault(block, []).append((parts[-1], q, answers.get(q.key)))
    return out


def _render_sources(sources):
    return []                       # Task 7 replaces this


def _render_profile(_):
    return ""                       # Task 6 replaces this


def _render_config(answers, sources, default_vault):
    lines = [_HEADER]
    grouped = _grouped(answers, default_vault)
    # HOISTED out of the per-block loop: a fan-out question appears in three blocks, and a per-block
    # set emitted its section header, blurb and hint once per block.
    sections_seen = set()

    for block in [""] + [b for b in grouped if b]:
        entries = grouped.get(block, [])
        if not entries:
            continue
        indent = "  " if block else ""
        body = []
        for leaf, q, value in entries:
            if q.section and q.section not in sections_seen:
                sections_seen.add(q.section)
                body.append("")
                body.append(f"{indent}# -- {q.section} " + "-" * max(0, 56 - len(q.section)))
                body += [f"{indent}# {ln}" for ln in _SECTION_BLURB.get(q.section, "").split("\n")
                         if ln]
            body += _render_key(leaf, q, value, indent)
        if block:
            # A bare `triage:` with only comments beneath parses as `{'triage': None}`, and relying
            # on each loader to treat that as an empty mapping is a coupling nobody asked for. So
            # the HEADER is commented when every key under it is unset.
            #
            # ONLY the header. Every line in `body` is already a comment, and re-prefixing them
            # produced `#   # accept_titles:` -- which defeated the scope guard's own matcher on 16
            # of 19 keys while the neutrality half stayed green, so the implementer saw one red test
            # whose message was false. Widening the matcher instead would let a comment ABOUT a key
            # stand in for the key: the matched-by-adjacent-prose bug this repo has already shipped.
            active = any(not _unset(v) for _, _, v in entries)
            lines.append("")
            lines.append(f"{block}:" if active else f"# {block}:")
            lines += body
        else:
            lines += body

    lines += _render_sources(sources)
    return "\n".join(lines).rstrip() + "\n"


def _notes(answers, sources, default_vault):
    """What the config will DO, in plain terms. Written because the shipped example once handed
    every copier an active `relevance_keep` that discarded every title but one, and nothing
    anywhere said so."""
    out = []
    for q in catalogue(default_vault=default_vault):
        value = answers.get(q.key)
        if _unset(value) or value == 0 or not q.consequence:
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        out.append(q.consequence.format(value=shown))
    return tuple(out)


def build_plan(answers, *, config_dest, profile_dest, default_vault,
               profile_answers=None, sources=None) -> InitPlan:
    """The two artefacts `sluice init` writes, as text.

    `answers` holds only the questions the user actually answered -- a skipped question is ABSENT,
    never present-and-empty, so a blank cannot be mistaken downstream for a deliberate empty list.
    """
    sources = sources or {}
    return InitPlan(config_dest=config_dest,
                    config_text=_render_config(answers, sources, default_vault),
                    profile_dest=profile_dest,
                    profile_text=_render_profile(profile_answers),
                    notes=_notes(answers, sources, default_vault))
