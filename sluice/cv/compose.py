"""Bounded CV composition. The prompt's entire factual content is the closed verified
bundle + the JD + the format contract; the backend has no other source. On a gate failure
-- HARD, or a scoped STYLE finding (#167) -- the engine calls compose again with the
findings appended (one retry)."""
from sluice.cv.slop import _PHRASES

_RULES = """CV RULES (follow exactly):

- YOUR TASK IS TO TAILOR, NOT TO WRITE. You are given a candidate's verified facts in the SOURCE BUNDLE. Rephrase, reorder, and emphasise ONLY those facts to fit this specific role. You are not authoring a new CV, and you add nothing that is not already in the bundle.
- The SOURCE BUNDLE is the ONLY permitted source. If a detail is not in the bundle, leave it out. Never infer from general knowledge, from the job ad, or from what the role "should" have. NO FABRICATION of any kind: no employers, roles, dates, titles, numbers, metrics, tools, skills, certifications, achievements, or motivations that are not in the bundle.
- If the role asks for experience, a skill, or a quality the bundle does not contain, DO NOT add it. Omit it. A shorter, honest CV is correct; an invented match is a failure.
- Rephrasing changes wording and emphasis, never facts or numbers. Any number or named fact you include must remain unchanged from the bundle entry it came from.
- Every WORK EXPERIENCE bullet MUST end with a citation [id] naming the bundle entry it came from (several allowed: [id] [id]). No uncited bullets. Any number in a bullet must appear in a cited entry.
- The SKILLS INVENTORY section is FRAMING, not a source. Use it to choose which experience entries to lead with and how to describe them. Never cite it, never quote a number from it, and never introduce a claim that rests on it alone: every fact in the CV must still come from the BASELINE CV or a VERIFIED EXPERIENCE ENTRY.
{skills_attribution_rule}- Every line of the SKILLS section must come from the SOURCE BUNDLE. Do not add a skill the bundle does not contain.
- {employer_line}
- NO em dashes anywhere. Use commas, colons, semicolons, periods, or parentheses. No double hyphens (--). En-dash date ranges (12/2025-present) are fine.
- No AI slop (avoid these words/phrases and any inflection of them: {banned_phrases}). Short sentences. Real metrics only.
- Profile: "I" voice, 2 to 3 sentences. Compose it ONLY from facts in the SOURCE BUNDLE, ordered and emphasised for {role}. Introduce nothing not in the bundle. No motivations, aspirations, or company-specific claims. Any number in the profile must appear in the SOURCE BUNDLE.
- Output ONLY the CV, nothing else: no preamble, acknowledgement, commentary, separator, or closing remark before the contact block or after the last section. The first non-blank line of your reply is the first line of the CV.

Output the CV in EXACTLY this format (what cv_render_v2.py parses):
{contact}

{name_heading}

PROFILE
(2 to 3 sentences)

WORK EXPERIENCE

<Company>
MM/YYYY-MM/YYYY | LOCATION | Role
- bullet ending with a citation [id]

CERTIFICATES
- cert

EDUCATION
- university, dates | degree
{skills_block}"""

# Row 1's prompt rule, and the SECOND thing `skills_requested` gates -- not just the
# format-contract block below. It shipped UNCONDITIONAL and that was a Critical: measured
# with `skills_requested=False`, this sentence still rendered, and on a vault where no
# entry carries a `Skills:` value -- the shipped default, and every install on upgrade day
# -- "only if an entry that bullet cites lists that skill" is satisfiable by naming NO
# skill at all. A compliant model strips every technology name out of every WORK bullet,
# and NOTHING catches it: that is a COMPLIANCE change, not a gate violation, so
# `validate()` sees a clean CV and the run reports success while the CVs go out weaker.
#
# Gated on the SAME condition the engine already computes for the block below
# (`any(es.skills for es in sources.entries.values())`, cv/engine.py) rather than on a
# second test of its own, for the reason SC5 gives there: the request condition and the
# gate's abstain condition must be unable to disagree.
#
# Row 1 (cv/validate.py's MISATTRIBUTED SKILL) ABSTAINS PER-ENTRY on exactly this
# condition -- `if all(sources.entries[c].skills for c in cites ...)` -- so a conditional
# rule is what makes the prompt state the rule the gate will actually apply. Row 2
# (UNSOURCED SKILL) is the one that is genuinely unconditional (it fails closed on an
# un-annotated vault), which is why its bullet above stays outside this gating.
#
# Its trailing newline, not a leading one, is what makes the empty case collapse cleanly:
# the placeholder sits at column 0 of its own line in `_RULES`, so an empty value leaves
# the SKILLS INVENTORY bullet and the row 2 bullet adjacent with no blank line between.
_SKILLS_ATTRIBUTION_PROMPT_RULE = (
    "- You may name a skill in a WORK EXPERIENCE bullet only if an entry that bullet "
    "cites lists that skill. If no cited entry lists it, leave it out.\n")

# The gated SKILLS section's format-contract example. Interpolated into `_RULES` only
# when `build_prompt` is called with `skills_requested=True` -- see that function.
# SC5's request-abstain: an entry-free vault (no `Skills:` field anywhere in the
# Experience Library) must never see this text, or the model complies with a section
# the hard gate (row 2, cv/validate.py) can license nothing for, earning a guaranteed
# HARD violation on every single lead and burning the one retry for free.
#
# Named `_SKILLS_PROMPT_BLOCK`, not `_SKILLS_BLOCK`: tests/test_prompt_neutrality.py's
# constant discovery (`_prompt_symbols_in`) admits a module-level constant only when
# `PROMPT` is in its name. That buys a SECOND, independent sweep route for whatever this
# block ever says -- one that survives a later regression in `_render`'s override
# precedence (see that file's `_render`), rather than depending solely on it.
#
# Its single leading newline is what produces a blank line before the SKILLS header once
# spliced after "- university, dates | degree\n" above (a trailing "\n" there plus this
# constant's own leading "\n"), matching the blank-line-before-header convention every
# other section in this format contract already follows.
_SKILLS_PROMPT_BLOCK = """
SKILLS
- skill
"""


def _employer_line(employers):
    """The employer-completeness instruction. With a configured list, name it
    explicitly (matches the validate() gate's per-employer check); with none
    configured, ask the model to cover the bundle instead of a fixed roster,
    since the completeness gate itself is skipped when unconfigured."""
    if employers:
        names = ", ".join(employers)
        return (f"Include all {len(employers)} employers, reverse chronological: "
                f"{names}.")
    return "Include every employer present in the SOURCE BUNDLE, reverse chronological."


def _banned_phrases_sentence(slop_allow=None):
    """Render the ban-list FROM slop._PHRASES rather than a hand-written duplicate
    (#167). Before this, the prompt banned `drove` in prose while _PHRASES never
    enforced it -- banned in prose, unchecked in code, nothing keeping the two in
    step -- and the reverse gap (a stem `_PHRASES` enforces but the prose never
    names) was equally possible and equally silent. Rendering FROM the one list the
    deterministic gate reads is what makes the two identical by construction, pinned
    by tests/test_cv_compose.py::test_the_prompt_names_exactly_the_phrases_the_gate_enforces.

    Renders `_PHRASES` STEMS ("spearhead"), not the INFLECTIONS ("spearheaded") the
    old hand-written sentence used -- an equality test against the enforced list must
    compare like with like, or it would fail on wording that was never actually in
    disagreement (see that test's own comment).

    `_PHRASES - slop_allow`, not `_PHRASES` alone: a phrase the candidate has
    explicitly allowed (cv/config.py's `cv.slop_allow`, validated there to be a real
    stem) must not still be instructed against on every compose -- otherwise
    slop_allow only suppresses the STYLE HOLD while the candidate's own voice is
    composed out of the draft anyway, half of #167's fix left inert. Case-insensitive
    on both sides for the same reason slop.check_phrases' own `allow` matching is:
    the config value and _PHRASES' casing are independent.
    """
    allowed = {p.lower() for p in (slop_allow or ())}
    return ", ".join(p for p in _PHRASES if p.lower() not in allowed)


# `name` is KEYWORD-ONLY and REQUIRED, not defaulted to a placeholder. It used to default to
# "Your Name" purely for callers (this module's own unit tests) that did not care about
# identity for what they were testing -- but with the #99/#100 sentinel check that once
# compared a composed header against that exact literal now GONE (#133/#107 -- identity ground
# truth moved to the vault, and cv/engine.py compares against the derived cv_name/cv_contact
# instead), nothing anywhere compared against "Your Name" any more: a shipped identity
# placeholder with no guard behind it, the shape this codebase engineers out elsewhere.
# Required-with-no-default makes the unreachable path unconstructible rather than merely
# unreached -- this module's own tests now pass a fixture identity explicitly (see
# tests/test_cv_compose.py's `_NAME`). `contact` keeps its `""` default: an EMPTY contact block
# is the neutral, already-abstain-shaped value this codebase uses throughout (an unset field
# renders nothing), not a placeholder that could misrepresent anyone -- there is no equivalent
# risk to close for it. The one production caller, cv/engine.py's run_one, always passes both
# explicitly anyway, already non-blank (guaranteed by the skipped-config refusal that runs
# before compose is ever reached).
def build_prompt(bundle_text, jd, company, role, *, name, contact="",
                  employers=None, prior_violations=None, slop_allow=None,
                  skills_requested=False):
    parts = [
        f"Compose a tailored CV for {name} applying for {role} at {company}.",
        "",
        _RULES.format(contact=contact, name_heading=name.upper(),
                     employer_line=_employer_line(employers), role=role,
                     banned_phrases=_banned_phrases_sentence(slop_allow),
                     skills_attribution_rule=(
                         _SKILLS_ATTRIBUTION_PROMPT_RULE if skills_requested else ""),
                     skills_block=_SKILLS_PROMPT_BLOCK if skills_requested else ""),
        "",
        "=== THE ROLE (JD) ===",
        jd or "(no JD text captured; compose from the bundle for a general fit)",
        "",
        "=== SOURCE BUNDLE (the ONLY permitted source) ===",
        bundle_text,
    ]
    if prior_violations:
        parts += ["",
                  "=== YOUR PREVIOUS DRAFT FAILED THE GATE. Fix these and re-emit the FULL CV: ===",
                  *[f"- {v}" for v in prior_violations]]
    return "\n".join(parts)


# ── #28: recover the artefact from an agent's conversational envelope ─────────
# `claude --print` is an agent, not a completion endpoint (#28's own finding), and
# one of its documented failure shapes is otherwise gate-clean output wrapped in a
# short conversational aside on either side, delimited by a markdown-style '---'
# line. _RULES above forbids emitting a bare separator at all ("no preamble,
# acknowledgement, commentary, separator, or closing remark"), so any line that is
# nothing but hyphens is necessarily either an envelope boundary or a formatting
# slip -- never legitimate CV content. `slop.py`'s unqualified DOUBLE-HYPHEN-DASH
# rule correctly rejects it either way; this recovers the artefact BEFORE that gate
# ever sees it rather than weakening the gate to tolerate it.
#
# "SKILLS" (#168 Task 8) only WIDENS what `_is_envelope_aside` treats as real CV content
# -- it can never cause a genuine CV to fail closed, only stop a genuine SKILLS section
# from being misread as a stray aside. Measured on a realistic full-wrap #28 envelope
# with this entry present: a bulleted SKILLS section is left intact already, because
# SC7's bulleted shape (see `_looks_like_cv_content`) already carries the protection --
# so no separate "envelope survival" guard is added here; one would be an equivalent
# mutant; it would pass whether or not this line was correct.
_REQUIRED_HEADERS = {"PROFILE", "WORK EXPERIENCE", "CERTIFICATES", "EDUCATION", "SKILLS"}


# Deliberately a LITERAL duplicate of cv/parse.py's _TRAILING_MARKERS, not an
# import of it: compose.py runs before any renderer or parser is chosen, and
# reaching into parse.py's grammar from here would be the same cross-layer
# coupling cv/engine.py's own STRUCTURAL guards already refuse for the
# identical reason (see their comment: "the engine may guard what the prompt
# required; only a renderer may guard what its own layout needs" --
# recomputed rather than imported). The two lists drifting apart is the
# accepted cost of that separation; a marker parse.py adds and this list
# doesn't reproduces the exact bug this constant exists to close (round 2 of
# this branch's own /review-pr: an en-dash-marked EDUCATION entry, a real
# gate-clean format parse.py's own comment cites as production-observed, was
# silently stripped when this list only knew the ASCII '-').
_BULLET_MARKERS = ("-", "•", "*", "–", "—")


def _looks_like_cv_content(line):
    """A bullet (any of _BULLET_MARKERS) or a pipe-separated meta line
    ("dates | LOCATION | Role") -- the two shapes every real entry under WORK
    EXPERIENCE, CERTIFICATES and EDUCATION is built from (see _RULES's format
    block above). Neither shape occurs in the short conversational asides
    captured on the real production path (#28): a model's preamble or closing
    remark is plain prose, never a bulleted or pipe-delimited line.

    This is what closes two real content-loss findings from this branch's own
    /review-pr round, both confirmed by execution: a genuine final WORK entry
    (company line + this meta line + this bullet) is exactly as short and as
    header-free as a real conversational aside, and so is a section's body
    when a fence lands right after that section's OWN header line rather than
    before it (the header itself is on the wrong side of the fence to be
    re-seen by the header check below). Checking for the entry's own shape,
    not merely the section header's presence, catches both without needing to
    remember what appeared on the other side of the fence.

    Accepted tradeoff, not a bug: a genuine conversational aside formatted as
    its own bullet (e.g. "- Let me know if you'd like edits.") is
    indistinguishable from a real bullet by shape alone and so is left
    unstripped too. This degrades safely rather than silently -- the leftover
    fence still trips slop.py's DOUBLE-HYPHEN-DASH rule, so the CV still fails
    the gate and retries/skips rather than shipping with the aside baked in.
    See test_unwrap_envelope_may_leave_a_bulleted_aside_unstripped.
    """
    return line.startswith(_BULLET_MARKERS) or " | " in line


def _is_envelope_aside(lines, max_lines=3):
    """A short remark with none of the CV's own section headers or entry shapes
    in it -- the shape of the preamble/postamble sentences captured on the real
    production path (#28), never a genuine section boundary. Bounding the
    length is what keeps this from swallowing a real section that merely
    happens to be short (e.g. a two-line EDUCATION entry, which also fails the
    entry-shape check below, but a section-free run of unrelated short lines
    would not)."""
    non_blank = [ln.strip() for ln in lines if ln.strip()]
    if not non_blank or len(non_blank) > max_lines:
        return False
    if any(ln.upper() in _REQUIRED_HEADERS for ln in non_blank):
        return False
    return not any(_looks_like_cv_content(ln) for ln in non_blank)


def _unwrap_agent_envelope(text):
    """Strip a short conversational aside from the start and/or end of `text`,
    each introduced by a fence line of three or more hyphens (#28).

    Fires only on the two documented shapes -- a short aside before the first
    fence, a short aside after the last fence, or both -- leaving anything else
    untouched: a fence with real CV content on both sides (e.g. used mid-document
    as an ad-hoc divider) is not a case this function can safely resolve, and
    guessing wrong would silently discard a genuine section. The gate is left to
    reject an unresolved fence on its own merits.

    Known accepted gap: the header block (contact + name) carries none of
    _REQUIRED_HEADERS's keywords, so a fence positioned between the name and
    PROFILE -- never observed on the real production path, but not something
    this function can rule out -- is misread as a leading aside and stripped
    along with the real name. Safe rather than silent: cv/engine.py's own #99
    STRUCTURAL guard rejects the resulting headerless CV and forces a retry.
    See test_unwrap_envelope_may_strip_a_real_header_when_a_fence_splits_it_from_profile.
    """
    lines = text.splitlines()
    fence_idxs = [i for i, ln in enumerate(lines)
                  if len(ln.strip()) >= 3 and set(ln.strip()) == {"-"}]
    if not fence_idxs:
        return text

    start, end = 0, len(lines)
    first = fence_idxs[0]
    if _is_envelope_aside(lines[:first]):
        start = first + 1

    last = fence_idxs[-1]
    if last >= start and _is_envelope_aside(lines[last + 1:]):
        end = last

    if start == 0 and end == len(lines):
        return text
    return "\n".join(lines[start:end]).strip()


# `name`/`contact` match `build_prompt`'s signature exactly, for the identical reason -- see
# that function's comment. Worth restating here specifically because THIS is the function
# cv/engine.py's run_one actually calls (build_prompt is an internal helper compose() forwards
# to), so a reader arriving at the real call site is exactly who that comment exists to reach.
def compose(backend, bundle_text, jd, company, role, *, name, contact="",
            employers=None, prior_violations=None, slop_allow=None,
            skills_requested=False):
    raw = backend.complete(build_prompt(bundle_text, jd, company, role, name=name,
                                        contact=contact, employers=employers,
                                        prior_violations=prior_violations,
                                        slop_allow=slop_allow,
                                        skills_requested=skills_requested))
    return _unwrap_agent_envelope(raw)
