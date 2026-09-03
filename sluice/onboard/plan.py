"""Pure planning: answers in, three artefact texts out. No I/O, no prompts, no clock.

That purity is the point. The property this feature lives or dies by -- a run that answers nothing
produces a config that expresses nothing -- is then a table test over a dict rather than something
observable only by driving a wizard and reading files back.

The config is RENDERED FROM THE CATALOGUE rather than being a static template with substitution
holes, which makes "every key the wizard can write appears in the file it writes" true by
construction instead of by review.
"""
import dataclasses
import re
from dataclasses import dataclass

from sluice.core.criteria import DEFAULT_CRITERIA
from sluice.core.protocols import CandidateProfile
from sluice.core.vault import parse_frontmatter
from sluice.onboard.emit import flow_list, scalar
from sluice.onboard.questions import catalogue

_SECTION_BLURB = {
    "Vault": "Where your notes live.",
    # #107: no longer identity -- your name and contact details live in the vault's
    # Candidate Profile note, collected by its own interview, not by a question that
    # writes into this file. What is left under "You" is the employer roster the CV
    # fabrication gate checks a tailored CV against verbatim.
    "You": "Employer names the CV composer must cite verbatim.",
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
    """The artefact TEXTS, plus what the report should say about them.

    No destinations. They were carried here and read by nobody -- measured, two calls with wildly
    different `config_dest`/`profile_dest` produced byte-identical text and notes. `cmd_init` knows
    where it is writing; it does not need this object to tell it. Dropping them also keeps a
    filesystem path out of a module whose first line claims no I/O.

    `candidate_text` is the THIRD artefact (Task 6): unlike the other two, its own construction can
    fail -- `_render_candidate` raises `FrontmatterRoundTripError` rather than ever returning a value
    that would corrupt on the way back in.
    """
    config_text: str
    profile_text: str
    candidate_text: str = ""
    view_text: str = ""
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


class FrontmatterRoundTripError(ValueError):
    """A candidate answer does not survive the `emit.scalar()` + `core/vault.py`'s `_fm_dict`
    PAIRING `_render_candidate` writes and re-reads through -- see that function's docstring for
    the mechanism. Not `_fm_dict` alone: an interior `"` or `\\` round-trips fine through `_fm_dict`
    on its own (nothing about it collides with a strip of surrounding quotes), and only fails
    because `scalar()` had to escape it first.

    Raised by `_render_candidate` rather than by the caller: the corruption is a property of the
    VALUE and that pairing, not of anything the caller did, so there is nothing a caller could
    check first that this function is not already checking.
    """


# The 36 CandidateProfile field names, in declaration order. DERIVED, never hand-listed -- a
# hand-listed copy would drift the moment Task 1's dataclass gains or reorders a field, and the
# drift would be silent: `_render_candidate` would keep emitting the STALE set forever.
_CANDIDATE_FIELD_ORDER = tuple(f.name for f in dataclasses.fields(CandidateProfile))

# answer key (what `collect_candidate` asks for) -> CandidateProfile field name (what the note's
# frontmatter key is). Only the five identity fields have a question -- every other one of the 36
# fields is a vault-note-only field: present in the rendered note (per
# `test_all_thirty_six_keys_are_present_even_when_unanswered`) but with no interview question, so
# a user fills it in directly in Obsidian. `cmd_init` (cli.py) also gates the write on whatever
# this mapping actually put into the rendered text, not on the raw answer dict -- so a future
# question added here with no matching entry below is silently inert rather than corrupting
# anything; see `test_every_candidate_prompt_key_is_mapped_to_a_profile_field`
# (tests/functional/test_init.py) for the standing guard against that drift regardless.
_CANDIDATE_KEY_BY_ANSWER = {
    "cv_forenames": "forenames", "cv_surname": "surname", "cv_email": "email",
    "cv_mobile": "mobile", "cv_linkedin": "linkedin",
}


def _render_candidate(candidate_answers):
    """The Candidate Profile note: every one of the 36 `CandidateProfile` fields present in the
    frontmatter, answered ones carrying their value and the rest present-but-empty -- the spec's
    "undeclared" shape, and why this is `values = {field: "" for field in ...}` rather than only
    emitting the five keys `collect_candidate` actually asks about.

    Every value is rendered through `emit.scalar()` -- the SAME double-quoted, escape-table scalar
    emitter `_render_config` already uses for the main config file -- and the WHOLE note is then
    re-read through `parse_frontmatter`, the real reader `Vault.read_candidate_profile` also uses.
    That PAIRING, not a bespoke check, is the guard. `core/vault.py`'s `_fm_dict` ends in
    `.strip().strip('"').strip("'")`: it strips EVERY leading and trailing quote character, not
    merely one -- `'""Ada""'.strip('"')` is `'Ada'`, executed -- and unescapes NOTHING, so it has no
    idea `scalar()` ever escaped anything. An ordinary value needs none of `scalar()`'s escapes, so
    quoting-then-unstripping is a no-op round trip. A value that DOES need one comes back a
    DIFFERENT string, and the inequality is what gets refused -- three distinct shapes, all caught
    by the same comparison: a value with its OWN leading or trailing quote character collides with
    `_fm_dict`'s strip regardless of `scalar()`; an interior character from `scalar()`'s named
    escape table (`emit._ESCAPES` -- `"` and `\\` are two of its five members, not the whole set) is
    escaped into two literal characters that `_fm_dict` then reads back literally instead of
    restoring; and a control character `scalar()` hex-escapes (`emit._needs_hex`) comes back as the
    literal multi-character escape sequence instead of itself. No separate "reject control
    characters" (or "reject interior quotes") check is needed or added: reusing `scalar()`'s
    existing, already-tested escape table is what makes every one of those three diverge from
    itself on the way back in, for free. Inventing a second escaping scheme here, tuned to make
    hostile values survive, would be the wrong fix -- see `FrontmatterRoundTripError`'s docstring
    and this task's tests.
    """
    answers = candidate_answers or {}
    values = {field: "" for field in _CANDIDATE_FIELD_ORDER}
    for answer_key, field in _CANDIDATE_KEY_BY_ANSWER.items():
        values[field] = (answers.get(answer_key) or "").strip()
    lines = ["---"] + [f"{k}: {scalar(v)}" for k, v in values.items()] + ["---", ""]
    lines += [
        "# Candidate Profile",
        "",
        "The identity and application-form data sluice fills forms with. Edit it in",
        "Obsidian whenever something changes; the next run picks it up with no code",
        "change. Every field above is optional: an empty one is simply never offered",
        "to a form, and sluice never guesses a value it was not given.",
        "",
        "`cv run` needs at least one name part and at least one contact channel",
        "before it will compose. Everything else feeds `apply prep`.",
        "",
        "See also: [[Judging Profile]].",
        "",
    ]
    text = "\n".join(lines)
    # THE round trip: re-read the bytes about to be written through the real reader, and compare
    # per-field against what was asked for, before this function ever returns them. A value that
    # fails here would otherwise compare its OWN corrupted self against itself in cv/engine.py's
    # #99 STRUCTURAL guard -- passing every gate while shipping a wrong name as the PDF's headline.
    parsed = parse_frontmatter(text)
    for field, wanted in values.items():
        got = parsed.get(field, "")
        if got != wanted:
            # Named, not merely described: `scalar()` widens WHAT can fail this check beyond
            # "leading/trailing quotes and control characters" (an interior character from
            # scalar()'s escape table fails too -- see this function's own docstring above), so a
            # fixed sentence naming a character class would go stale the next time that set
            # changes. Showing what was WRITTEN next to what was READ BACK is what stays true
            # regardless -- the user does not have to guess.
            #
            # This echoes the raw answer TWICE (`wanted`, `got`), which is a
            # deliberate departure from `core/candidate.py`'s `age_from_dob` -- it never logs a raw
            # field value, on the stated grounds that a log is a plausible leak site for a
            # sensitive one. The two sites differ on the axis that rule turns on: `age_from_dob`
            # runs per LEAD, unattended, writing into a log file that accumulates quietly over
            # every run; this raises ONCE, synchronously, into a terminal the user is looking at
            # right now, on the value they just typed themselves -- interactive and foreground, not
            # accumulating and unattended. The departure does not close every risk, though: an
            # uncaught traceback is, if anything, MORE likely than a routine log line to end up
            # pasted whole into a public "why does `init` crash" bug report, which is permanent and
            # public where a log file is neither. That residual risk is what the message's own
            # closing sentence names, rather than leaving it undrawn.
            raise FrontmatterRoundTripError(
                f"the answer for {field!r} does not survive sluice's frontmatter reader: wrote "
                f"{wanted!r}, read back {got!r}. Leave it blank here and add it directly to the "
                f"Candidate Profile note in Obsidian afterwards instead -- typed there with no "
                f"surrounding quotes, most values (an interior \" or \\ among them) read back "
                f"exactly as written, because the note's reader only strips a value's OWN "
                f"leading and trailing quote characters. One that begins or ends with a quote "
                f"character cannot be stored in this frontmatter format at all; retype it "
                f"without that one. (This message repeats your answer above -- avoid pasting it "
                f"verbatim into a public bug report.)")
    return text


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


# The Obsidian Bases view over the lead notes (#240), written verbatim: it takes no answers,
# so unlike the other three artefacts there is nothing to render. It lives here anyway so
# `cmd_init` writes all four through one uniform path.
#
# `note["base"] == link("Job Leads.base")` is the membership predicate, and it is the reason
# `core/vault.py` stamps `base: "[[Job Leads.base]]"` into every lead note it creates. The two
# have to agree: change the filename and every note already in the vault falls out of the view.
#
# NEUTRALITY. This ships to every user, so it names no place, employer or role preference --
# the same rule `sluice.yaml.example` and the golden fixtures live under. That is a real
# constraint rather than a theoretical one: a hand-built view of this kind naturally grows a
# tab per city the author is searching, and those tabs are exactly the "hunt geography" #27 is
# about. Views here are keyed on sluice's OWN status vocabulary instead, which is neutral by
# construction because `core/status.py` defines it.
#
# Every filter uses `==` only. Bases supports more, but each construct used here is one
# observed working in a real vault; inventing syntax would fail silently, since a view whose
# filter does not parse renders as an empty table rather than an error.
LEADS_VIEW_TEXT = """filters:
  and:
    - note["base"] == link("Job Leads.base")
properties:
  company:
    displayName: Company
  role:
    displayName: Role
  location:
    displayName: Location
  status:
    displayName: Status
  score:
    displayName: Score
  salary:
    displayName: Salary
  role_type:
    displayName: Type
  source:
    displayName: Source
  last_seen:
    displayName: Last seen
  url:
    displayName: Job URL
views:
  - type: table
    name: All leads
    order:
      - company
      - role
      - location
      - status
      - score
      - salary
      - last_seen
    groupBy:
      property: status
      direction: ASC
    sort:
      - property: score
        direction: DESC
  - type: table
    name: Shortlist
    filters:
      and:
        - status == "shortlist"
    order:
      - company
      - role
      - location
      - score
      - salary
      - url
    sort:
      - property: score
        direction: DESC
  - type: table
    name: Needs review
    filters:
      and:
        - status == "needs_review"
    order:
      - company
      - role
      - location
      - score
      - relevance_notes
  - type: table
    name: Applied
    filters:
      and:
        - status == "applied"
    order:
      - company
      - role
      - location
      - salary
      - last_seen
    sort:
      - property: last_seen
        direction: DESC
"""


def build_plan(answers, *, profile_answers=None, candidate_answers=None, sources=None) -> InitPlan:
    """The artefacts `sluice init` writes, as text.

    `answers` holds only the questions the user actually answered -- a skipped question is ABSENT,
    never present-and-empty, so a blank cannot be mistaken downstream for a deliberate empty list.

    `candidate_answers` feeds `_render_candidate` alone, exactly like `profile_answers` feeds only
    `_render_profile` -- three independent interviews, three independent renders, so a bug in one
    cannot corrupt what another writes. `_render_candidate` can raise `FrontmatterRoundTripError`;
    that is deliberately not caught here, so no artefact is written from a value that would
    corrupt on the way back in. What to do about it belongs to the command that owns the
    terminal, not to this function -- `cmd_init` (cli.py) re-asks the five candidate questions
    rather than giving up; see its own comment for why a retry loop, not a catch-and-continue.
    """
    sources = sources or {}
    return InitPlan(config_text=_render_config(answers, sources),
                    profile_text=_render_profile(profile_answers),
                    candidate_text=_render_candidate(candidate_answers),
                    view_text=LEADS_VIEW_TEXT,
                    notes=_notes(answers))
