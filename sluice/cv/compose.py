"""Bounded CV composition. The prompt's entire factual content is the closed verified
bundle + the JD + the format contract; the backend has no other source. On a HARD-gate
failure the engine calls compose again with the violations appended (one retry)."""

_RULES = """CV RULES (follow exactly):

- YOUR TASK IS TO TAILOR, NOT TO WRITE. You are given a candidate's verified facts in the SOURCE BUNDLE. Rephrase, reorder, and emphasise ONLY those facts to fit this specific role. You are not authoring a new CV, and you add nothing that is not already in the bundle.
- The SOURCE BUNDLE is the ONLY permitted source. If a detail is not in the bundle, leave it out. Never infer from general knowledge, from the job ad, or from what the role "should" have. NO FABRICATION of any kind: no employers, roles, dates, titles, numbers, metrics, tools, skills, certifications, achievements, or motivations that are not in the bundle.
- If the role asks for experience, a skill, or a quality the bundle does not contain, DO NOT add it. Omit it. A shorter, honest CV is correct; an invented match is a failure.
- Rephrasing changes wording and emphasis, never facts or numbers. Any number or named fact you include must remain unchanged from the bundle entry it came from.
- Every WORK EXPERIENCE bullet MUST end with a citation [id] naming the bundle entry it came from (several allowed: [id] [id]). No uncited bullets. Any number in a bullet must appear in a cited entry.
- {employer_line}
- NO em dashes anywhere. Use commas, colons, semicolons, periods, or parentheses. No double hyphens (--). En-dash date ranges (12/2025-present) are fine.
- No AI slop (no spearheaded, fostered, drove, leveraged, seamless, passionate about, proven track record). Short sentences. Real metrics only.
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
- university, dates | degree"""


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


def build_prompt(bundle_text, jd, company, role, name="Your Name", contact="",
                  employers=None, prior_violations=None):
    parts = [
        f"Compose a tailored CV for {name} applying for {role} at {company}.",
        "",
        _RULES.format(contact=contact, name_heading=name.upper(),
                     employer_line=_employer_line(employers), role=role),
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
_REQUIRED_HEADERS = {"PROFILE", "WORK EXPERIENCE", "CERTIFICATES", "EDUCATION"}


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


def compose(backend, bundle_text, jd, company, role, name="Your Name", contact="",
            employers=None, prior_violations=None):
    raw = backend.complete(build_prompt(bundle_text, jd, company, role, name,
                                        contact, employers, prior_violations))
    return _unwrap_agent_envelope(raw)
