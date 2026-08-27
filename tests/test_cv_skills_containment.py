# tests/test_cv_skills_containment.py
"""#168, Task 3: `section_spans` learns to collect a SKILLS region as a CONTIGUOUS
BULLET RUN. This module pins the five preservation cases the design review named --
three independent reviewers found a Critical defect in an earlier version of exactly
this mechanism, so each case below is measured, not merely plausible.

Task 3 only teaches `section_spans` to COLLECT the region; nothing here validates what
a skill line CLAIMS (no citation check, no number check) -- that is Tasks 4-6. These
tests are scoped to `section_spans` itself, never to `validate()`'s violation list.
"""
from sluice.cv import validate as V

# The base fixture every test but the last builds from. `{tail}` lets each test append
# content AFTER the SKILLS section without re-typing the WORK EXPERIENCE / SKILLS
# preamble five times.
_CV = """PROFILE
I did the work.

WORK EXPERIENCE

Example Alpha
01/2020-01/2024 | LOCATION | Engineer
- Ran the rebuild [AL1]

SKILLS
- Example Query
- Example Framework
{tail}"""


def test_skills_lines_are_collected_into_their_own_region():
    _p, work, skills = V.section_spans(_CV.format(tail=""))
    assert [t.strip() for _n, t in skills] == ["- Example Query", "- Example Framework"]
    assert [t.strip() for _n, t in work] == ["- Ran the rebuild [AL1]"]


def test_a_publications_bullet_after_skills_is_still_citation_checked():
    """Direction 1, measured on main: this bullet IS citation-checked today. A SKILLS
    region that simply cleared `in_work` would swallow it, and a fabricated figure in it
    would never be number-checked."""
    tail = "\nPUBLICATIONS\n- Wrote a paper that cut cost by 92%\n"
    _p, work, _s = V.section_spans(_CV.format(tail=tail))
    assert any("92%" in t for _n, t in work)


def test_a_publications_bullet_after_certificates_is_still_uncited_clean():
    """Direction 2, measured on main: this bullet is NOT citation-checked today. The
    obvious repair for direction 1 -- revert to the WORK region on any unmodelled header
    -- regresses this one. Both directions are non-negotiable."""
    tail = ("\nCERTIFICATES\n- Example Practitioner\n"
            "\nPUBLICATIONS\n- Wrote a paper that cut cost by 92%\n")
    _p, work, _s = V.section_spans(_CV.format(tail=tail))
    assert not any("92%" in t for _n, t in work)


def test_a_blank_line_inside_the_run_does_not_eject_the_remaining_skills():
    """A blank between skill entries is ordinary formatting -- `cv/parse.py` tolerates it in
    three places. An earlier revision ended the run at the first NON-BULLET line, which put
    the following bullets back in WORK as UNCITED BULLET; since section 4.4 forbids
    per-skill `[id]`s, the retry's cheapest compliance was to ADD one, which is work-clean
    and still renders as a skill. This test pinned that launder as intended before the fix.
    """
    tail = ""
    cv = _CV.format(tail=tail).replace("- Example Framework", "\n- Example Framework")
    _p, work, skills = V.section_spans(cv)
    assert [t.strip() for _n, t in skills] == ["- Example Query", "- Example Framework"]
    assert not any("Example Framework" in t for _n, t in work)


def test_a_skills_section_after_certificates_is_still_collected():
    """The fourth case, and the only one that can observe the no-region bypass: the three
    others all keep `in_work` true. `CERTIFICATES` clears it, so an earlier revision put
    these lines in NO region at all while `parse_cv` still returned them as skills.

    Built as its OWN literal rather than through `_CV`/`tail`: the shared fixture above
    already carries its own SKILLS section (see `_CV` itself), and appending a second one
    past it via `tail` would leave that FIRST section active before CERTIFICATES ever
    clears anything -- a confound that defeats the isolation this case needs (measured:
    doing it that way collects THREE entries, not one, none of them wrong but none of
    them what this case is testing).
    """
    cv = ("PROFILE\nI did the work.\n\nWORK EXPERIENCE\n\nExample Alpha\n"
          "01/2020-01/2024 | LOCATION | Engineer\n- Ran the rebuild [AL1]\n\n"
          "CERTIFICATES\n- Example Practitioner\n\nSKILLS\n\n- Example Query\n")
    _p, _work, skills = V.section_spans(cv)
    assert [t.strip() for _n, t in skills] == ["- Example Query"]
