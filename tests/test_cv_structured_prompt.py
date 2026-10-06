"""cv/compose.py::build_structured_prompt -- what the composer is asked for (spec §5.1)."""
from sluice.core.layout import Slot
from sluice.core.protocols import LayoutRole
from sluice.cv import compose as C
from sluice.cv.slop import _PHRASES

ALPHA = LayoutRole("Example Alpha", "01/2020", "present", title="SYNTHETIC-TITLE-1")
GROUP = LayoutRole("Example Northgate", "08/2001", "03/2015")
SLOTS = (Slot("R1", ALPHA, ("EA1", "EA2"), 5), Slot("R2", GROUP, (), 0))


def _prompt(**over):
    kw = dict(name="Jane Roe", slots=SLOTS, pool=("Example Query", "Examplelang"),
              skills_max=4)
    kw.update(over)
    return C.build_structured_prompt("BUNDLE TEXT", "THE JD", "Example Co", "Analyst", **kw)


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
    out = C.compose_structured(be, "BUNDLE TEXT", "THE JD", "Example Co", "Analyst",
                               name="Jane Roe", slots=SLOTS, on_prompt=seen.append)
    assert out == "```json\n{}\n``` and some chat"
    assert seen == [be.prompt]


# tests/test_cv_compose.py's static guard reads `_RULES` alone, and goes with it in Task 19
# (spec §12.2: the static CV-prompt guard points at every rule constant the new prompt
# renders). Its vocabulary is copied here, never imported, because that row is deleted then.
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
