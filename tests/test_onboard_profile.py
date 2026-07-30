"""The round-1 CRITICAL. `build_system_prompt_from` falls back to `_DEFAULT_CRITERIA` only when the
criteria are missing or EMPTY, and the scaffold is always non-empty -- so a scaffold of bare
headings permanently strips the judge's abstain instructions, while the surrounding scaffold still
tells it to treat the profile as authoritative and not to hedge into research. Running the
onboarding command would make an unconfigured install STOP abstaining."""
import re

from sluice.onboard.plan import PROFILE_HEADINGS, build_plan, default_sections
from sluice.triage.prompt import _DEFAULT_CRITERIA, build_system_prompt_from

ABSTAIN_MARKERS = ("prefer `research`", "do not score on role shape",
                   "Do not assume a culture preference", "never invent or assume")


def _profile(**kw):
    return build_plan({}, config_dest="/example/c.yaml", profile_dest="/example/p.md",
                      default_vault="/example/vault", **kw).profile_text


def test_an_unanswered_profile_still_carries_every_abstain_instruction():
    """THE regression this task exists for."""
    prompt = build_system_prompt_from(_profile())
    for marker in ABSTAIN_MARKERS:
        assert marker in prompt, f"the scaffold dropped: {marker!r}"


def test_an_unanswered_profile_is_not_treated_as_configured():
    assert "No Judging Profile has been configured yet" in build_system_prompt_from(_profile())


def test_an_answered_heading_replaces_the_default_prose_for_that_heading_only():
    text = _profile(profile_answers={"target_shape": "Example target shape."})
    assert "Example target shape." in text
    # ...and the OTHER headings keep their defaults, so answering one does not disarm the rest.
    assert "Do not assume a culture preference" in text


def test_the_headings_are_DERIVED_from_the_scaffold_not_restated():
    """v1 hand-copied five headings and pinned them by equality against the source. Splitting the
    source removes the duplicate entirely -- there is no second list to drift."""
    assert PROFILE_HEADINGS == tuple(default_sections())
    scaffold = re.findall(r"^#{2,3} .+$", _DEFAULT_CRITERIA, re.M)
    assert list(PROFILE_HEADINGS) == scaffold
    assert len(PROFILE_HEADINGS) == 5                      # SCOPE: a split that found nothing
    assert all(default_sections()[h].strip() for h in PROFILE_HEADINGS)   # ...or empty bodies


def test_every_heading_appears_in_the_written_profile():
    text = _profile()
    for heading in PROFILE_HEADINGS:
        assert heading in text


def test_the_profile_carries_no_frontmatter():
    """`_strip_frontmatter` drops a leading `---` block, so emitting one writes something the judge
    is guaranteed never to see."""
    assert not _profile().startswith("---")


def test_the_scaffold_prompts_name_no_exemplar():
    from sluice.onboard.questions import expresses_a_preference
    text = _profile()
    assert text.strip()                                     # SCOPE
    # The DEFAULT prose is `_DEFAULT_CRITERIA`, already governed by its own guard; sweep only the
    # HTML-comment prompts this module adds.
    for prompt in re.findall(r"<!--(.*?)-->", text, re.S):
        assert not expresses_a_preference(prompt)
