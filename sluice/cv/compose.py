"""Bounded CV composition. The prompt's entire factual content is the closed verified
bundle + the JD + the format contract; the backend has no other source. On a HARD-gate
failure the engine calls compose again with the violations appended (one retry)."""

_RULES = """CV RULES (follow exactly):

- YOUR TASK IS TO TAILOR, NOT TO WRITE. You are given a candidate's verified facts in the SOURCE BUNDLE. Rephrase, reorder, and emphasise ONLY those facts to fit this specific role. You are not authoring a new CV, and you add nothing that is not already in the bundle.
- The SOURCE BUNDLE is the ONLY permitted source. If a detail is not in the bundle, leave it out. Never infer from general knowledge, from the job ad, or from what the role "should" have. NO FABRICATION of any kind: no employers, roles, dates, titles, numbers, metrics, tools, skills, certifications, achievements, or motivations that are not in the bundle.
- If the role asks for experience, a skill, or a quality the bundle does not contain, DO NOT add it. Omit it. A shorter, honest CV is correct; an invented match is a failure.
- Rephrasing changes wording and emphasis, never facts or numbers. Every number and named fact survives unchanged from the bundle entry it came from.
- Every WORK EXPERIENCE bullet MUST end with a citation [id] naming the bundle entry it came from (several allowed: [id] [id]). No uncited bullets. Any number in a bullet must appear in a cited entry.
- {employer_line}
- NO em dashes anywhere. Use commas, colons, semicolons, periods, or parentheses. No double hyphens (--). En-dash date ranges (12/2025-present) are fine.
- No AI slop (no spearheaded, fostered, drove, leveraged, seamless, passionate about, proven track record). Short sentences. Real metrics only.
- Profile: "I" voice, 2 to 3 sentences. Compose it ONLY from facts in the SOURCE BUNDLE, ordered and emphasised for {role}. Introduce nothing not in the bundle. No motivations, aspirations, or company-specific claims. Any number in the profile must appear in the SOURCE BUNDLE.

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


def compose(backend, bundle_text, jd, company, role, name="Your Name", contact="",
            employers=None, prior_violations=None):
    return backend.complete(build_prompt(bundle_text, jd, company, role, name,
                                         contact, employers, prior_violations))
