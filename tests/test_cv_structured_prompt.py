"""cv/compose.py::build_structured_prompt -- what the composer is asked for (#364 spec §5.1)."""
from sluice.core.layout import Slot
from sluice.core.protocols import LayoutRole
from sluice.cv import compose as C
from sluice.cv.slop import _PHRASES
from tests.conftest import _disjoint, _title_pool

# The role is incidental to every row here, so it is drawn from the same seeded pool the
# `titles` fixture serves rather than typed: the first accept title. A module constant,
# because the `_prompt` helper every row calls takes no fixture.
ROLE = _disjoint(_title_pool())[0][0]

ALPHA = LayoutRole("Example Alpha", "01/2020", "present", title="SYNTHETIC-TITLE-1")
GROUP = LayoutRole("Example Northgate", "08/2001", "03/2015")
SLOTS = (Slot("R1", ALPHA, ("EA1", "EA2"), 5), Slot("R2", GROUP, (), 0))


def _prompt(**over):
    kw = dict(name="Jane Roe", slots=SLOTS, pool=("Example Query", "Examplelang"),
              skills_max=4)
    kw.update(over)
    return C.build_structured_prompt("BUNDLE TEXT", "THE JD", "Example Co", ROLE, **kw)


def test_each_slot_lists_its_heading_dates_cites_and_budget():
    p = _prompt()
    assert "R1: Example Alpha | 01/2020–present | SYNTHETIC-TITLE-1 | may cite: EA1, EA2 | " \
           "up to 5 bullets" in p
    assert "R2: Example Northgate | 08/2001–03/2015 | may cite: none | no bullets" in p


def test_an_uncapped_slot_says_so():
    p = _prompt(slots=(Slot("R1", ALPHA, ("EA1",), None),))
    assert "| any number of bullets" in p


def test_the_skills_pool_is_a_closed_list_with_its_cap():
    p = _prompt()
    assert C._SKILLS_POOL_PROMPT_HEADER in p
    assert "- Example Query\n- Examplelang" in p
    assert "at most 4" in p
    assert '"skills": ["<skill from the list>"]' in p


def test_no_skills_are_asked_for_with_an_empty_pool_or_a_zero_cap():
    for p in (_prompt(pool=()), _prompt(skills_max=0)):
        assert C._SKILLS_POOL_PROMPT_HEADER not in p
        assert '"skills"' not in p
        assert C._NO_SKILLS_RULE_PROMPT.strip() in p


def test_the_reply_shape_is_shown_with_placeholders_only():
    assert '{"profile": "<profile>", "roles": {"<slot>": [{"text": "<bullet>", ' \
           '"cites": ["<id>"]}]}' in _prompt()


def test_the_prompt_never_mentions_a_baseline_cv():
    assert "BASELINE" not in _prompt()


def test_the_prompt_carries_the_whole_ban_list_and_one_double_hyphen():
    p = _prompt()
    assert [ph for ph in _PHRASES if ph not in p] == []
    # The prompt bans "--"; it must not model one beyond the single place it names it.
    assert p.count("--") == 1


def test_the_retry_lists_the_findings_and_the_drops():
    p = _prompt(prior_findings=["UNCITED BULLET: R1 bullet 1: x"],
                prior_drops=["'Example Ghost': not one of your skills"])
    assert C._RETRY_FINDINGS_PROMPT_HEADER in p and "- UNCITED BULLET: R1 bullet 1: x" in p
    assert C._RETRY_DROPS_PROMPT_HEADER in p and "- 'Example Ghost': not one" in p


def test_no_retry_block_on_a_first_attempt():
    p = _prompt()
    assert C._RETRY_FINDINGS_PROMPT_HEADER not in p
    assert C._RETRY_DROPS_PROMPT_HEADER not in p


def test_compose_structured_returns_the_reply_unmodified_and_reports_its_prompt():
    from sluice.core.backends import Completion

    class Backend:
        def complete(self, prompt):
            self.prompt = prompt
            return Completion("```json\n{}\n``` and some chat")

    seen = []
    be = Backend()
    out = C.compose_structured(be, "BUNDLE TEXT", "THE JD", "Example Co", ROLE,
                               name="Jane Roe", slots=SLOTS, on_prompt=seen.append)
    assert out == "```json\n{}\n``` and some chat"
    assert seen == [be.prompt]


# The static CV-prompt guard reads EVERY rule constant the structured prompt renders (#364
# spec §12.2), not one rules string: the text-prompt guard that read `_RULES` alone is gone with
# that constant. The vocabulary is the triage guard's (tests/test_prompt.py), copied rather
# than imported so this file states what a CV prompt may not name.
_FORBIDDEN_PREFERENCES = (
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
)


def _prompt_constants():
    """Every `*PROMPT*`-named constant the structured prompt can render: compose's rules,
    headers and JSON shape, and the bundle's section headers. DISCOVERED, so a constant
    added later is checked with no edit here."""
    from sluice.cv import bundle as B

    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, (tuple, list, frozenset, set)):
            return [s for v in value for s in strings(v)]
        return []

    return {f"{m.__name__}.{n}": " ".join(strings(v)) for m in (C, B)
            for n, v in vars(m).items() if "PROMPT" in n and strings(v)}


def test_no_structured_prompt_constant_names_a_job_or_culture_preference():
    """Static on purpose: `build_structured_prompt`'s output interpolates the caller's job
    ad, which may legitimately say "startup". tests/test_prompt_neutrality.py sweeps the
    RENDERED prompt, but can only carry terms every shipped prompt honours, so this fuller
    list stays here."""
    found = _prompt_constants()
    # SCOPE first: a discovery that matched nothing would pass the check below vacuously.
    assert {"sluice.cv.compose._STRUCTURED_RULES_PROMPT", "sluice.cv.compose._JSON_SHAPE_PROMPT",
            "sluice.cv.compose._ROLE_SLOTS_PROMPT_HEADER",
            "sluice.cv.compose._RETRY_FINDINGS_PROMPT_HEADER"} <= found.keys(), sorted(found)
    leaked = {name: hits for name, text in found.items()
              if (hits := [t for t in _FORBIDDEN_PREFERENCES if t in text.lower()])}
    assert not leaked, f"a shipped CV prompt constant names a job or culture preference: {leaked}"


def test_the_prompt_carries_the_jd_the_company_the_role_and_the_em_dash_rule():
    p = _prompt()
    assert "THE JD" in p and "Example Co" in p and ROLE in p
    assert "NO em dashes" in p
    assert "\u2014" not in p                  # the prompt itself models no em dash
    assert "Notion" not in p and "training data" not in p.lower()


def test_the_prompt_is_a_tailoring_task_and_forbids_invention():
    # WORDING assertions: they pin that the anti-fabrication instructions are present, not
    # that fabrication cannot occur. The profile rule once said "lead with what {company}
    # values", which points the profile at the JD, which is not a permitted source.
    p = _prompt()
    assert "lead with what" not in p                    # the JD-pull is gone
    assert "TAILOR, NOT TO WRITE" in p                  # the task frame
    assert "an invented match is a failure" in p        # the JD-gap omit rule
    assert "you include must remain unchanged" in p     # preservation is conditional
    assert "no preamble" in p.lower()                   # the reply is the JSON object alone


def test_the_prompt_frames_the_inventory_and_lets_no_number_rest_on_it():
    """#165, D3, retargeted. The model is shown inventory fields such as `Proficiency: 8
    years`, which it will reasonably use unless told the entries are the only source of a
    figure: an unusable number earns INVENTED PROFILE METRIC and, if the retry repeats it, a
    skipped lead. The trap is ours to close, because we put the number in front of it."""
    p = _prompt()
    assert "SKILLS INVENTORY is FRAMING" in p
    assert "never rest a claim in the profile or a bullet on it alone" in p
    assert "Any number in the profile must appear in a VERIFIED EXPERIENCE ENTRY" in p


def test_an_allowed_phrase_is_not_instructed_against_either():
    # Otherwise slop_allow suppresses the hold while the model is still told to avoid the
    # phrase on every compose -- the candidate's own voice composed out regardless.
    def named(prompt):
        return {ph for ph in _PHRASES if ph in prompt}

    assert "leverage" not in named(_prompt(slop_allow=["leverage"]))
    # ...and everything else is still named: an allowed entry must not silently drop the
    # WHOLE ban list, only the one phrase it names.
    assert named(_prompt(slop_allow=["leverage"])) == set(_PHRASES) - {"leverage"}


def test_each_gated_prompt_rule_is_its_own_bullet_in_the_rules_list():
    """The rules list is a list of BULLETS, and each gated rule is spliced into it by a bare
    `{placeholder}` at column 0. That shape is load-bearing: deleting a rule constant's
    TRAILING newline merges it with the bullet that follows (demoting the NEXT rule out of
    the list), and deleting the leading `- ` stops the rule being a bullet at all. The
    constants' own comments state the trailing-newline mechanism as a REASON, so this is the
    row that falsifies it.

    The two gated rules are the triage framing and the skills pick; both are named, with the
    scope asserted first, because a discovery loop that matches nothing satisfies every
    assertion made over it."""
    gated = {"_TRIAGE_FRAMING_PROMPT_RULE": C._TRIAGE_FRAMING_PROMPT_RULE,
             "_STRUCTURED_SKILLS_RULE_PROMPT": C._STRUCTURED_SKILLS_RULE_PROMPT.format(cap=", at most 4")}
    p = _prompt(triage_framing=("concerns: FRAMING-FOR-THE-GUARD",))
    lines = p.splitlines()
    for name, text in sorted(gated.items()):
        own = text.strip("\n").splitlines()
        assert own and own[0].startswith("- "), f"{name} is not a bullet"
        # Whole LINES of the prompt, not a substring of one: a lost trailing newline still
        # leaves the text present, so `in p` cannot see the defect this test exists for.
        for line in own:
            assert line in lines, f"{name} does not occupy whole prompt lines: {line!r}"
    # ...and the bullet that FOLLOWS the splice point is still its own bullet: a lost newline
    # merges the two lines and demotes the NEXT rule, not the rule whose newline vanished.
    bullets = [ln.lstrip("- ") for ln in lines if ln.startswith("- ")]
    assert any(b.startswith("NO em dashes anywhere") for b in bullets), (
        "the em-dash rule was absorbed into the bullet above it")
    assert p.count("--") == 1, "a gated rule introduced a double hyphen into the prompt"
