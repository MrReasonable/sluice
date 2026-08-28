"""An unannotated vault never asks the composer for a SKILLS section, and the
ordinary WORK-bullet composition path runs exactly as it did before #168.

The property the whole feature's safety rests on, proven at the composition root
rather than assumed from the unit tests that already cover the wiring
(`test_the_composer_is_asked_for_a_skills_section_when_an_entry_declares_one` and
its mirror in tests/test_cv_engine.py, both against a FAKE vault). #168's prompt
change shipped a real incident in miniature once already: `_SKILLS_ATTRIBUTION_PROMPT_RULE`
went out UNCONDITIONAL for a time, so a vault with no `Skills:` anywhere still saw
the row-1 prompt rule, satisfiable by naming no skill at all -- a compliant model
silently stripped every technology name from every WORK bullet, and nothing caught
it because `validate()` reported a clean CV (see cv/compose.py's own comment on
`_SKILLS_ATTRIBUTION_PROMPT_RULE`). This is the end-to-end version of the guard
against that regression: an install that has annotated nothing must see NEITHER
`_SKILLS_PROMPT_BLOCK` (the format-contract's own SKILLS example) NOR
`_SKILLS_ATTRIBUTION_PROMPT_RULE` (the row-1 prompt rule) in the real prompt the
real composition root builds, and the lead must still render cleanly on the first
attempt -- no retry, because nothing about #168 disturbs a vault it was never told
about.

The seeded Experience Library entry carries no `Skills:` value (harness default,
unchanged since before #168), so `sources.entries[...].skills` is empty for the
only entry in play and `skills_requested` -- computed once in cv/engine.py from
that same value -- must be False.
"""
from sluice.cv.compose import _SKILLS_ATTRIBUTION_PROMPT_RULE, _SKILLS_PROMPT_BLOCK
from sluice.ingest import sources as _sources

from tests.harness import PASSING_CV, ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]


def test_an_unannotated_vault_never_requests_a_skills_section(tmp_path, monkeypatch):
    # build_harness's DEFAULT_EXPERIENCE, unmodified -- no `skills=` override, so the
    # seeded entry's `Skills:` frontmatter value is blank exactly as it was before
    # #168 added the field.
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_CV},
                              default_verdict="shortlist")
    app = h.sluice(backend)
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))
    results = app.compose_cv(all_shortlist=True)

    compose_prompts = [p for p in backend.prompts
                       if p.startswith("Compose a tailored CV for")]
    assert compose_prompts, "the compose call never happened; this test would pass vacuously"
    # NEITHER half of the gated prompt content reached the model -- `in`, not a
    # count, because either one leaking is the whole regression this test guards.
    for prompt in compose_prompts:
        assert _SKILLS_PROMPT_BLOCK not in prompt
        assert _SKILLS_ATTRIBUTION_PROMPT_RULE not in prompt
    assert len(compose_prompts) == 1            # rendered on the FIRST attempt, no retry

    assert len(results) == 1
    r = results[0]
    assert r.status == "rendered"
    assert r.violations == []                   # the gate ran and found nothing to say
    assert h.recorder.rendered == [PASSING_CV]   # exactly the canned CV, untouched
