"""A CV listing a skill absent from the bundle never ships.

The property is unchanged from #168's: an unbacked skill never reaches a CV. Only the
mechanism moved (#364/#365/#368, spec §6.2): the composer now picks skills from a CLOSED
list -- the verified entries' `Tools:` and `Skills` items and verified Skills Inventory names --
and a pick off that list is DROPPED and reported, never rendered, rather than refusing the
whole CV.
So the lead renders, without the pick, on its first attempt.

Driven through the real `Sluice.compose_cv` composition root with a FAKE BACKEND emitting a
genuinely unbacked pick, the scenario #213's review found untested anywhere below the unit
level. The vault is ANNOTATED: the one seeded Experience Library entry declares
`Tools: "Example Query"`, so the pool is non-empty and skills ARE requested -- an
annotated vault whose pool never reached the prompt would be indistinguishable, at every
other seam, from one where it did. The reply picks "Example Ghost", a name the pool never
offers (already reviewed on tests/test_fixture_name_neutrality.py's
`_REVIEWED_SKILL_VALUES`).
"""
import json

from sluice.cv.compose import _SKILLS_POOL_PROMPT_HEADER
from sluice.ingest import sources as _sources

from tests.harness import ScriptedBackend, build_harness
from tests.harness.config import DEFAULT_EXPERIENCE

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]

# DEFAULT_EXPERIENCE's one entry, ANNOTATED with a `Tools:` value -- the same entry every
# other e2e/functional CV scenario seeds, widened by only the one field. "Example Query"
# is already on the reviewed roster.
ANNOTATED_EXPERIENCE = [{**DEFAULT_EXPERIENCE[0], "tools": "Example Query"}]

# PASSING_REPLY's one cited bullet, picking a skill the pool does not offer.
UNBACKED_PICK_REPLY = json.dumps({
    "profile": "I build reliable systems.",
    "roles": {"R1": [{"text": "Grew the team from 3 to 8 engineers", "cites": ["EF1"]}]},
    "skills": ["Example Ghost"]})


def test_a_cv_citing_an_unbacked_skill_never_ships(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS,
                      experience=ANNOTATED_EXPERIENCE)
    backend = ScriptedBackend(cv_by_company={"Example Foundry": UNBACKED_PICK_REPLY},
                              default_verdict="shortlist")
    app = h.sluice(backend)
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))

    # Snapshot compose calls so the no-retry contract is PINNED, not assumed: a drop never
    # causes a retry (#364 spec §6.2), so one compose is all a hard-clean reply costs.
    composes_before = sum(p.startswith("Compose a tailored CV for") for p in backend.prompts)
    results = app.compose_cv(all_shortlist=True)
    composes = sum(p.startswith("Compose a tailored CV for") for p in backend.prompts) - composes_before

    # The ANNOTATED vault must have actually OFFERED its tool -- the real `Vault` reading a
    # real note's `Tools:` frontmatter through `EVIDENCE_KINDS` is a different path from a
    # fake vault's hand-built `fields` dict, and a break anywhere in that real chain would
    # silently leave the pool empty with the outcome below UNCHANGED (an empty pool drops
    # the pick too). This is the one assertion that can tell the two apart.
    assert any(_SKILLS_POOL_PROMPT_HEADER in p and "- Example Query" in p
               for p in backend.prompts)

    assert len(results) == 1
    r = results[0]
    assert (r.status, composes) == ("rendered", 1)
    # EXACTLY the one drop, naming the pick and why.
    assert r.skills_dropped == ["'Example Ghost': not one of your skills"]
    # The unbacked pick never reached the document the renderer was handed.
    assert [d.skills for d in h.recorder.rendered] == [[]]
