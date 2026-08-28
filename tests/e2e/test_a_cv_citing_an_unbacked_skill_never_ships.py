"""A CV citing a skill absent from the bundle never ships.

The fabrication gate's SKILLS arm (#168), end to end -- the sibling of
`test_a_cv_citing_an_unbacked_figure_never_ships`'s numeric arm, and the scenario
#213's review found untested anywhere below the unit level: every seam (prompt
gating, the two containment rows, the doctor wiring, the CLI display) was
independently unit-tested and confirmed load-bearing by mutation, but nothing drove
a FAKE BACKEND emitting a genuinely fabricated SKILLS line through the real
`Sluice.compose_cv` composition root the way this scenario does.

The vault is ANNOTATED: the one seeded Experience Library entry declares
`Skills: "Example Query"`, so the composed CV's SKILLS section is GATED (row 2,
`UNSOURCED SKILL` in cv/validate.py) rather than silently ungated -- an annotated
vault whose containment check never ran would be indistinguishable, at every OTHER
seam, from one where it fired correctly. The composed CV's one SKILLS line is
"Example Ghost", a name the bundle never declares (already reviewed on
tests/test_fixture_name_neutrality.py's `_REVIEWED_SKILL_VALUES`, invented for an
unrelated #168 fixture and reused here). The engine retries once -- the retry
re-keys the same canned CV, since compose appends violations past the prompt's
first line -- then skips. Exactly one violation is load-bearing: any other failure
would keep the CV skipped-gate under a row-2 mutation and the witness would go
inert.
"""
from sluice.cv.compose import _SKILLS_PROMPT_BLOCK
from sluice.ingest import sources as _sources

from tests.harness import PASSING_CV, ScriptedBackend, build_harness
from tests.harness.config import DEFAULT_EXPERIENCE

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]

# DEFAULT_EXPERIENCE's one entry, ANNOTATED with a `Skills:` value -- the same entry
# every other e2e/functional CV scenario seeds, widened by only the one field #168
# added. "Example Query" is already on the reviewed roster.
ANNOTATED_EXPERIENCE = [{**DEFAULT_EXPERIENCE[0], "skills": "Example Query"}]

# PASSING_CV plus a SKILLS section naming "Example Ghost" -- a value the bundle's
# source text (the entry's own `Skills:` value, its body, and the baseline) never
# contains, so row 2 must refuse it as UNSOURCED. Appended, not spliced into
# PASSING_CV's own structure, so the CV is otherwise byte-identical to the passing
# baseline and this is the ONLY violation.
SKILLS_VIOLATION_CV = PASSING_CV + "\n\nSKILLS\n- Example Ghost\n"


def test_a_cv_citing_an_unbacked_skill_never_ships(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS,
                      experience=ANNOTATED_EXPERIENCE)
    backend = ScriptedBackend(cv_by_company={"Example Foundry": SKILLS_VIOLATION_CV},
                              default_verdict="shortlist")
    app = h.sluice(backend)
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))

    # Snapshot compose calls so the retry-once contract is PINNED, not assumed: a
    # gate failure composes once, feeds the violations back, composes a SECOND time,
    # then skips (cv/engine.py's `for _ in range(2)`). Without this the test would
    # still pass if the retry were removed.
    composes_before = sum(p.startswith("Compose a tailored CV for") for p in backend.prompts)
    results = app.compose_cv(all_shortlist=True)
    composes = sum(p.startswith("Compose a tailored CV for") for p in backend.prompts) - composes_before

    # The ANNOTATED vault must have actually REQUESTED a SKILLS section -- the real
    # `Vault` reading a real note's `Skills:` frontmatter through `EVIDENCE_KINDS`
    # is a different path from the FAKE vault stubs `test_cv_engine.py`'s own
    # request-wiring test hands a `fields` dict directly, and a break anywhere in
    # that real chain (the field never declared, the frontmatter parsed wrong, the
    # entry never reaching `bundle_sources`) would silently fall back to
    # `skills_requested=False` with the outcome below UNCHANGED, since row 2 always
    # runs regardless of what was requested -- this is the one assertion that can
    # tell the two apart.
    assert any(_SKILLS_PROMPT_BLOCK in p for p in backend.prompts)

    assert len(results) == 1
    r = results[0]
    assert r.status == "skipped-gate"
    assert composes == 2                       # composed once, retried exactly once, then skipped
    # EXACTLY one violation, and it is the invented skill -- so the row-2 UNSOURCED
    # SKILL check is the only thing that can make this lead render; no unrelated
    # gate failure is masking it (a bare `any(...)` would tolerate a second
    # violation).
    assert len(r.violations) == 1
    assert "UNSOURCED SKILL" in r.violations[0] and "Example Ghost" in r.violations[0]
    assert h.recorder.rendered == []           # nothing was ever rendered
