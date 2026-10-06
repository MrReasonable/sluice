"""An unannotated vault never asks the composer for a SKILLS section, and the ordinary
WORK-bullet composition path runs exactly as before.

The property the skills feature's safety rests on, proven at the composition root rather
than assumed from the unit tests against a FAKE vault. #168's prompt change shipped a real
incident in miniature once: a rule about skills went out UNCONDITIONAL for a time, so a
vault with nothing annotated still saw it, and a compliant model silently stripped every
technology name from every WORK bullet with nothing to catch it. Since #364/#365/#368 the
composer picks skills only from a closed pool -- the verified entries' `Tools:` and
verified Skills Inventory names (#364 spec §4.4) -- so with neither, the pool is empty and the
prompt must carry the no-skills rule and no pool at all. The lead must still render
cleanly on the first attempt, with no SKILLS section.

The seeded Experience Library entry carries no `Tools:` value (harness default) and the
harness seeds no skill notes, so the pool is empty.
"""
from sluice.cv.compose import _NO_SKILLS_RULE_PROMPT, _SKILLS_POOL_PROMPT_HEADER
from sluice.cv.document import to_text
from sluice.ingest import sources as _sources

from tests.harness import PASSING_REPLY, ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]


def test_an_unannotated_vault_never_requests_a_skills_section(tmp_path, monkeypatch):
    # build_harness's DEFAULT_EXPERIENCE, unmodified -- no `tools=` override, so the
    # seeded entry's `Tools:` frontmatter value is blank.
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_REPLY},
                              default_verdict="shortlist")
    app = h.sluice(backend)
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))
    results = app.compose_cv(all_shortlist=True)

    compose_prompts = [p for p in backend.prompts
                       if p.startswith("Compose a tailored CV for")]
    assert compose_prompts, "the compose call never happened; this test would pass vacuously"
    # No pool reached the model, and the rule saying so did -- `in`, not a count, because
    # either one going wrong is the whole regression this test guards.
    for prompt in compose_prompts:
        assert _SKILLS_POOL_PROMPT_HEADER not in prompt
        assert _NO_SKILLS_RULE_PROMPT in prompt
    assert len(compose_prompts) == 1            # rendered on the FIRST attempt, no retry

    assert len(results) == 1
    r = results[0]
    assert (r.status, r.violations) == ("rendered", [])
    [doc] = h.recorder.rendered
    assert doc.skills == [] and "SKILLS" not in to_text(doc)
