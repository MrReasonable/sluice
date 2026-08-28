from sluice.cv import compose as C
from sluice.cv.slop import _PHRASES

# `name` is now a required keyword-only argument on build_prompt/compose (#133/#107 removed
# the "Your Name" signature default -- a shipped identity placeholder with nothing left to
# guard against it, once the #99 sentinel comparison that was its only consumer was deleted).
# One fixture identity for every call in this file that does not care what the name actually
# is, so the placeholder risk is closed by construction rather than merely unreached.
_NAME = "Example Candidate"


def _phrases_named_in(prompt):
    """Which of slop._PHRASES are literally present in a rendered prompt -- checked
    against the REAL list (an import, not a second hand-copied one) so this stays in
    lockstep with whatever compose.py actually renders. Used to pin that the ban-list
    sentence is DERIVED from _PHRASES rather than hand-written (#167): a hand-written
    sentence is exactly how the prompt banned `drove` while _PHRASES never enforced it
    -- banned in prose, unchecked in code, nothing keeping the two in step."""
    return {p for p in _PHRASES if p in prompt}


class FakeBackend:
    def __init__(self, outputs): self.outputs = list(outputs); self.prompts = []
    def complete(self, prompt): self.prompts.append(prompt); return self.outputs.pop(0)

def test_prompt_contains_bundle_jd_and_forbids_em_dashes():
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst", name=_NAME)
    assert "BUNDLE-TEXT" in p and "JD-TEXT" in p
    assert "Acme" in p and "Analyst" in p
    assert "NO em dashes" in p or "No em dashes" in p
    assert "[id]" in p                       # citation instruction present
    assert "\u2014" not in p                  # the prompt itself models no em dashes

def test_prompt_excludes_material_not_given():
    p = C.build_prompt("BUNDLE", "", "Acme", "Analyst", name=_NAME)
    assert "Notion" not in p and "training data" not in p.lower()

def test_retry_prompt_appends_prior_violations():
    p = C.build_prompt("B", "J", "Acme", "Analyst", name=_NAME, prior_violations=["UNCITED BULLET: x", "MISSING EMPLOYER: Driftwave"])
    assert "UNCITED BULLET: x" in p and "MISSING EMPLOYER: Driftwave" in p
    assert "FAILED THE GATE" in p

def test_compose_calls_backend_and_returns_text():
    be = FakeBackend(["CV TEXT"])
    out = C.compose(be, "B", "J", "Acme", "Analyst", name=_NAME)
    assert out == "CV TEXT"
    assert "B" in be.prompts[0]

def test_prompt_is_a_tailoring_task_and_forbids_invention():
    # The observed incident's root cause was the profile rule "lead with what
    # {company} values", which points the profile at the JD (not a permitted source).
    # These are WORDING assertions: they pin that the anti-fabrication instructions
    # are present, not that fabrication cannot occur.
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst", name=_NAME)
    assert "lead with what" not in p                    # the JD-pull is gone
    assert "TAILOR, NOT TO WRITE" in p                  # the task frame
    assert "an invented match is a failure" in p        # the JD-gap omit rule
    assert "Introduce nothing not in the bundle" in p   # hardened profile framing
    assert "you include must remain unchanged" in p     # preservation rule is conditional, not "include everything"
    assert "—" not in p                 # still no em dash (matches the existing guard)
    assert p.count("--") == 1                            # only the (--) rule names the token; no `--` in the prompt's own prose


def test_the_prompt_names_exactly_the_phrases_the_gate_enforces():
    # compose.py banned `drove` while _PHRASES did not hold it: banned in prose,
    # unchecked in code, with nothing keeping the two in step. Equality, not subset --
    # the prompt named INFLECTIONS (spearheaded) while _PHRASES holds STEMS
    # (spearhead), so a subset test would fail on wording that is not in disagreement.
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst", name=_NAME)
    assert _phrases_named_in(p) == set(_PHRASES)


def test_an_allowed_phrase_is_not_instructed_against_either():
    # Otherwise slop_allow suppresses the hold while the model is still told to avoid the
    # phrase on every compose -- the candidate's own voice composed out regardless.
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst", name=_NAME,
                       slop_allow=["leverage"])
    assert "leverage" not in _phrases_named_in(p)
    # ...and everything else is still named -- an allowed entry must not silently
    # drop the WHOLE ban list, only the one phrase it names.
    assert _phrases_named_in(p) == set(_PHRASES) - {"leverage"}


def test_prompt_forbids_a_preamble_before_the_cv():
    # #99 (3a): a complement to the STRUCTURAL guards in cv/engine.py, not a
    # substitute -- this only reduces how often a preamble appears, it does not
    # catch one that slips through. Wording assertion, matching this file's
    # existing convention: pins that the instruction is present, not that a
    # preamble cannot occur.
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst", name=_NAME)
    assert "no preamble" in p.lower()
    assert "first line of the CV" in p


# ── #28: recover the artefact from an agent's conversational envelope ─────────
# Captured on the real production path (#28, sixth branch): a `claude-max` compose
# call returns an otherwise gate-clean CV wrapped in a markdown-style '---' fence,
# either side of which carries a short conversational aside. compose.py's own
# prompt forbids emitting a bare '---' at all ("no preamble, acknowledgement,
# commentary, separator, or closing remark"), so unqualified `slop.py` correctly
# treats it as a DOUBLE-HYPHEN-DASH violation -- the fix is to recover the real CV
# before it ever reaches the gate, not to weaken the gate.
CV_BODY = "\n".join([
    "JANE ROE", "", "PROFILE", "I build reliable systems.", "", "WORK EXPERIENCE", "",
    "Example Systems", "02/2023-present | Example Location A | Staff Engineer",
    "- Shipped [EF1]", "",
    "CERTIFICATES", "- Example Cert", "EDUCATION", "- Example University",
])


def test_unwrap_envelope_passes_through_text_with_no_fence():
    assert C._unwrap_agent_envelope(CV_BODY) == CV_BODY


def test_unwrap_envelope_strips_a_two_fence_wrapper():
    wrapped = "\n".join([
        "Here's the tailored CV for the Analyst role:", "", "---", "",
        CV_BODY, "",
        "---", "",
        "CV tailored for Acme's Analyst role. All bullets cited from source bundle.",
    ])
    assert C._unwrap_agent_envelope(wrapped) == CV_BODY


def test_unwrap_envelope_strips_a_leading_preamble_with_no_trailing_fence():
    wrapped = "\n".join([
        "Here's the tailored CV for the Analyst role:", "", "---", "", CV_BODY,
    ])
    assert C._unwrap_agent_envelope(wrapped) == CV_BODY


def test_unwrap_envelope_strips_a_trailing_summary_with_no_leading_fence():
    # The gap the original #28 fix candidate missed: it required TWO fences to
    # fire, but a model complying with "no preamble" still appends a closing
    # remark behind a SINGLE fence -- captured on the real production path (#28,
    # comment 6) as `errors: [(95, 'DOUBLE-HYPHEN-DASH', '---')]` with no leading
    # fence anywhere in the text.
    wrapped = "\n".join([
        CV_BODY, "", "---", "",
        "CV tailored for Acme's Analyst role. All bullets cited from source bundle.",
    ])
    assert C._unwrap_agent_envelope(wrapped) == CV_BODY


def test_unwrap_envelope_is_idempotent():
    wrapped = "\n".join(["Here's the CV:", "", "---", "", CV_BODY, "", "---", "", "Done."])
    once = C._unwrap_agent_envelope(wrapped)
    assert C._unwrap_agent_envelope(once) == once


def test_unwrap_envelope_leaves_a_mid_document_fence_untouched():
    # A stray '---' deep inside real content (e.g. the composer used it as an
    # ad-hoc divider) must NOT be treated as an envelope boundary: there is real
    # CV material on both sides of it, not a short conversational aside, and
    # guessing wrong here would silently discard a genuine section (CERTIFICATES,
    # EDUCATION) -- exactly the failure class the fabrication gate exists to
    # prevent. Left untouched for slop.py to flag on its own merits.
    lines = CV_BODY.splitlines()
    work_idx = lines.index("WORK EXPERIENCE")
    corrupted = "\n".join(lines[:work_idx + 1] + ["---"] + lines[work_idx + 1:])
    assert C._unwrap_agent_envelope(corrupted) == corrupted


def test_unwrap_envelope_leaves_a_fence_immediately_before_a_real_section_untouched():
    # A single fence sitting right before a short, entirely legitimate final
    # section (EDUCATION, itself only two lines here) must not be mistaken for a
    # trailing envelope postamble merely because the remaining text is short.
    lines = CV_BODY.splitlines()
    edu_idx = lines.index("EDUCATION")
    corrupted = "\n".join(lines[:edu_idx] + ["---"] + lines[edu_idx:])
    assert C._unwrap_agent_envelope(corrupted) == corrupted


def test_unwrap_envelope_never_strips_a_genuine_final_work_entry():
    # Critical finding (sluice-invariant-reviewer, local /review-pr on this
    # branch): a real final WORK EXPERIENCE entry -- company line, a
    # pipe-separated meta line, one cited bullet -- is exactly 3 non-blank
    # lines and contains none of _REQUIRED_HEADERS's literal tokens, so the
    # original "short and header-free" heuristic misread it as a disposable
    # trailing aside and deleted it in full. validate() has no WORK EXPERIENCE
    # completeness check, so a truncated CV like this would have cleared the
    # gate silently -- a real, possibly the most JD-relevant, job history
    # entry vanishing from a CV sent under the user's name with no error.
    text = "\n".join([
        "JANE ROE", "", "PROFILE", "I build reliable systems.", "", "WORK EXPERIENCE", "",
        "Example Systems", "02/2023-present | Example Location A | Staff Engineer",
        "- Shipped [EF1]", "",
        "---",
        "Example Foundry",
        "01/2020-01/2023 | Example Location B | Senior Engineer",
        "- Shipped real work [EF2]",
    ])
    assert C._unwrap_agent_envelope(text) == text


def test_unwrap_envelope_never_strips_a_section_whose_header_already_printed():
    # High finding (sluice-reviewer, same review round): the aside check only
    # scans the chunk AFTER the fence for header keywords, with no memory of a
    # header already emitted before it. A fence placed right after a section's
    # own header line, but before that section's short body, is misread as a
    # trailing postamble -- e.g. "...EDUCATION\n---\n- Example University" --
    # silently deleting the real education entry. This falsified this
    # function's own "one accepted gap" claim; fixed by refusing to strip any
    # chunk that looks like structured CV content (a bullet line), not merely
    # one that is short and header-free.
    lines = CV_BODY.splitlines()
    edu_header_idx = lines.index("EDUCATION")
    corrupted = "\n".join(lines[:edu_header_idx + 1] + ["---"] + lines[edu_header_idx + 1:])
    assert C._unwrap_agent_envelope(corrupted) == corrupted


def test_unwrap_envelope_never_strips_an_en_dash_marked_education_entry():
    # Critical finding (sluice-invariant-reviewer, round 2 of local /review-pr
    # on this branch): the fix for the previous round's WORK-entry finding only
    # recognized an ASCII '-' bullet, but cv/parse.py's own _TRAILING_MARKERS
    # ("-", "•", "*", "–", "—") accepts an en dash or em dash as
    # an equally real, gate-clean CERTIFICATES/EDUCATION marker (parse.py's own
    # comment cites the en dash as a real production-observed format). A fence
    # right after the EDUCATION header, followed by an en-dash-marked entry,
    # reproduced the identical silent-deletion bug through a marker
    # _looks_like_cv_content simply hadn't been told about.
    lines = CV_BODY.splitlines()
    edu_header_idx = lines.index("EDUCATION")
    en_dash_entry = "– Example University"
    corrupted = "\n".join(lines[:edu_header_idx + 1] + ["---", en_dash_entry])
    assert C._unwrap_agent_envelope(corrupted) == corrupted


def test_unwrap_envelope_leaves_a_three_fence_document_untouched_in_the_middle():
    # Low finding (sluice-test-engineer, round 2): pins that a genuine interior
    # '---' divider survives even when the document ALSO has short asides on
    # both outer sides -- three fences, not two. The first and last fences are
    # still the only ones consulted for stripping; the interior one must never
    # be mistaken for either boundary.
    wrapped = "\n".join([
        "Here's the tailored CV:", "", "---", "",
        CV_BODY[:CV_BODY.index("CERTIFICATES")].rstrip(),
        "---",
        CV_BODY[CV_BODY.index("CERTIFICATES"):],
        "", "---", "", "Done.",
    ])
    result = C._unwrap_agent_envelope(wrapped)
    assert "Here's the tailored CV" not in result
    assert "Done." not in result
    assert "---" in result, "the genuine interior divider must survive"


def test_unwrap_envelope_may_leave_a_bulleted_aside_unstripped():
    # Medium finding (sluice-invariant-reviewer, round 2), pinned as an
    # accepted, safe-direction limitation rather than fixed: a genuine
    # conversational aside formatted as its own markdown bullet list defeats
    # _looks_like_cv_content's bullet check, the same way it protects a real
    # CV entry. This degrades safely rather than silently -- the leftover '---'
    # fence still trips slop.py's DOUBLE-HYPHEN-DASH rule, so the CV still
    # fails the gate and retries/skips rather than shipping with the aside
    # baked in. Never observed on the real production path (every captured
    # aside has been plain prose); see
    # test_unwrap_envelope_may_strip_a_real_header_when_a_fence_splits_it_from_profile
    # for the sibling accepted gap on the opposite side of this same tradeoff.
    wrapped = "\n".join([CV_BODY, "", "---", "", "- Let me know if you'd like edits."])
    assert C._unwrap_agent_envelope(wrapped) == wrapped


def test_unwrap_envelope_may_strip_a_real_header_when_a_fence_splits_it_from_profile():
    # Deliberately accepted gap, not a bug, and pinned here so it cannot regress
    # into something worse unnoticed: the header block (contact + name) is the
    # one part of a genuine CV that carries none of _REQUIRED_HEADERS's keywords
    # -- the exact gap cv/engine.py's own #99/#100 STRUCTURAL guards exist to
    # cover, because no shape test can tell a genuine name line from a stray
    # aside in isolation. A fence positioned between the name and PROFILE --
    # never observed on the real production path; every captured case wraps the
    # WHOLE CV, not a sub-slice of its own header -- is therefore misread as a
    # leading aside and stripped. This is safe rather than silent: the result has
    # no header line at all before PROFILE, which engine.py's own STRUCTURAL
    # guard (header line-count) still rejects, forcing the ordinary retry -- a
    # wasted attempt, never a CV that silently ships without its own name (see
    # test_a_header_stripped_between_name_and_profile_still_fails_closed in
    # test_cv_engine.py for the end-to-end proof).
    lines = CV_BODY.splitlines()
    name_idx = lines.index("JANE ROE")
    corrupted = "\n".join(lines[:name_idx + 1] + ["---"] + lines[name_idx + 1:])
    result = C._unwrap_agent_envelope(corrupted)
    assert "JANE ROE" not in result


def test_compose_strips_an_agent_envelope_before_returning():
    wrapped = "\n".join([
        "Here's the tailored CV:", "", "---", "", CV_BODY, "", "---", "", "Done.",
    ])
    be = FakeBackend([wrapped])
    out = C.compose(be, "B", "J", "Acme", "Analyst", name=_NAME)
    assert out == CV_BODY


def test_cv_prompt_expresses_no_role_or_culture_preference():
    # neu-001: the triage guard test_shipped_prompt_expresses_no_role_or_culture_
    # preference (tests/test_prompt.py) covers only the TRIAGE prompt, not this CV
    # _RULES. Mirror it here so the hardened CV prompt cannot grow an opinion about
    # which jobs are good. Check the STATIC shipped rules, NOT build_prompt's output
    # -- that interpolates the caller's company/role/JD/bundle, and a real JD could
    # legitimately contain "startup" and must not trip this guard.
    #
    # Which is why this guard alone is no longer enough: #167 moved the ban list out of
    # _RULES and into a `{banned_phrases}` placeholder rendered from slop._PHRASES, so
    # everything below this line now covers ZERO of the ~40 stems that actually reach the
    # model. tests/test_prompt_neutrality.py sweeps the RENDERED text of every shipped
    # prompt (built from fixed SYNTHETIC arguments, so no caller data is in scope there
    # either) and covers the list this one cannot see. Both are load-bearing: that sweep
    # can only carry terms every prompt can honour, so the fuller list stays here.
    rules = C._RULES.lower()
    forbidden = [
        # company type / industry
        "startup", "enterprise", "faang", "unicorn", "well-funded",
        # work style / location
        "remote-first", "fast-paced", "onsite", "relocation",
        # compensation
        "salary", "equity", "compensation", "six-figure",
        # role shapes (from the triage guard's vocabulary)
        "engineering manager", "team lead", "tech lead", "scrum master",
        # culture rubric / hype
        "dora", "kanban", "rockstar", "ninja",
    ]
    leaked = [t for t in forbidden if t in rules]
    assert not leaked, f"the shipped CV prompt names a job/culture preference: {leaked}"


def test_the_prompt_forbids_quoting_a_number_from_the_skills_section():
    """#165, D3. Without this the model is told the bundle is 'the ONLY permitted source'
    and shown `Proficiency: 8 years`, which it will reasonably use -- earning INVENTED
    PROFILE METRIC and, if the retry repeats it, a skipped lead. The trap is ours to
    close, because we are the ones putting an unusable number in front of it."""
    prompt = C.build_prompt("BUNDLE", "JD", "Example Co", "Role", name=_NAME)
    assert "SKILLS INVENTORY" in prompt
    assert "never quote a number from it" in prompt


def test_the_prompt_permits_the_same_sources_the_derived_negative_does():
    """The derived negative (cv/bundle.py:_DERIVED_NEGATIVE_PROMPT) and this rule appear in
    the SAME prompt. A source named by one and not the other is a contradiction the
    composer can only resolve by dropping content."""
    from sluice.cv.bundle import _DERIVED_NEGATIVE_PROMPT
    prompt = C.build_prompt("BUNDLE", "JD", "Example Co", "Role", name=_NAME)
    for source in ("BASELINE CV", "VERIFIED EXPERIENCE ENTRY"):
        assert source in prompt, source
    assert "BASELINE CV" in _DERIVED_NEGATIVE_PROMPT


# ── #168 Task 8: the prompt asks for the section ──────────────────────────────
def test_the_skills_block_is_absent_when_no_entry_declares_skills():
    """SC5's request abstain. An unconditional block against a gate that can license
    nothing was the review's first Critical: prompt demands the section, every line
    violates, one retry, skipped-gate -- on every lead, on every vault at upgrade.

    Checked against the exact gated block (`C._SKILLS_PROMPT_BLOCK`), not a blanket
    "SKILLS" substring: `_RULES` already carries an unconditional "SKILLS INVENTORY"
    framing bullet (#165, a wholly separate, always-on feature -- the informational
    corpus, not the per-entry `Skills:` field this task gates), and the row 2 rule Task 8
    itself adds, which IS always present. Row 2 (`UNSOURCED SKILL`) is unconditional in
    the gate -- `cv/validate.py` runs it on every SKILLS line, fail-closed, whatever the
    vocabulary -- so its prompt rule is unconditional too. Row 1 (`MISATTRIBUTED SKILL`)
    is NOT: it abstains per-entry (`if all(sources.entries[c].skills for c in cites ...)`),
    so its prompt rule is gated on `skills_requested` alongside the block, and this
    docstring used to claim the opposite for BOTH rows -- an un-annotated vault then read
    a rule it could only satisfy by naming no skill anywhere (see
    `test_the_row_1_attribution_rule_is_absent_when_no_entry_declares_skills`). The
    always-present bullets legitimately contain the word "SKILLS", so a bare substring
    assertion here would fail on shipped text this task neither owns nor is meant to
    touch. Measured: the blanket-substring form of this test, taken verbatim from the
    plan, fails on the unmodified `_RULES` bullet alone even before this task's own new
    rules exist."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=False)
    assert C._SKILLS_PROMPT_BLOCK not in p


def test_the_skills_block_is_present_when_an_entry_declares_skills():
    """Mirror of the abstain test above, checked the same precise way. A blanket
    "SKILLS" substring check here would be VACUOUS -- always true regardless of
    `skills_requested`, because of the same pre-existing #165 framing bullet -- so it
    would witness nothing about this flag; a mutant that always sent `skills_block=""`
    would still pass it."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=True)
    assert C._SKILLS_PROMPT_BLOCK in p


def test_the_prompt_states_the_rule_row_2_enforces():
    """A `_RULES` rule permitting what a containment row forbids is the mutant the guard
    table names, and the design shipped it twice. This test READS the rule text --
    the literal row 2 sentence, not a generic "SOURCE BUNDLE"/"SKILLS" substring pair,
    both of which are already present in the prompt's other, unrelated bullets and so
    would pass without this rule existing at all."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=True)
    assert ("Every line of the SKILLS section must come from the SOURCE BUNDLE" in p)


def test_the_row_1_attribution_rule_is_absent_when_no_entry_declares_skills():
    """SC5's request abstain, for the ROW 1 RULE rather than the format block.

    The rule shipped unconditional. On a vault where no entry carries a `Skills:` value
    -- the shipped default, and every install the day #168 lands -- "you may name a skill
    in a WORK EXPERIENCE bullet only if an entry that bullet cites lists that skill" is
    satisfiable ONLY by naming no skill at all, so a compliant model strips every
    technology name out of every bullet. The gate cannot see that: it is a COMPLIANCE
    change, not a violation, so `validate()` returns clean and the run reports success
    while the CVs go out weaker. The block-level abstain above does not cover it -- that
    test asserts on `_SKILLS_PROMPT_BLOCK`, which this rule is not part of.

    Asserted as "no rule that FORBIDS naming a skill", by checking the constant is absent
    AND that the surviving prompt still carries no conditional-permission sentence: a
    bare `_SKILLS_ATTRIBUTION_PROMPT_RULE not in p` alone would pass on a mutant that
    merely re-worded the same instruction inline."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=False)
    assert C._SKILLS_ATTRIBUTION_PROMPT_RULE not in p
    assert "only if an entry" not in p
    # Row 2 is genuinely unconditional (the gate row fails closed), so it must survive --
    # without this the test would also pass on a mutant that gated the WRONG rule.
    assert "Every line of the SKILLS section must come from the SOURCE BUNDLE" in p


def test_the_row_1_attribution_rule_is_present_when_an_entry_declares_skills():
    """The positive control for the abstain above: gated, not deleted. Without this row a
    mutant that dropped the rule entirely would leave the abstain test green while row 1
    of the gate refused CVs the prompt never warned the model about."""
    p = C.build_prompt("BUNDLE", "JD", "Co", "Role", name="EXAMPLE CANDIDATE",
                       skills_requested=True)
    assert C._SKILLS_ATTRIBUTION_PROMPT_RULE in p
    assert "only if an entry that bullet cites lists that skill" in p
