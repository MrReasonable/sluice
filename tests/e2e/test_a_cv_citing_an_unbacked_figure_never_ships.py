"""A CV citing a figure absent from the bundle never ships.

The fabrication gate's NUMERIC arm, end to end (distinct from the structural-drift
arm that `test_a_clean_lead_reaches_rejected` exercises). The composed CV has the
correct WORK EXPERIENCE header, so the citation gate RUNS; its one and only
violation is a bullet citing "42", a figure in no cited bundle entry (the single
[EF1] entry allows {3, 8}). The engine retries once -- the retry re-keys the same
canned CV, since compose appends violations past the prompt's first line -- then
skips. Exactly one violation is load-bearing: any other failure would keep the CV
skipped-gate under the numeric-check mutation and the witness would go inert.
"""
from sluice.ingest import sources as _sources

from tests.harness import ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]

# PASSING_CV with ONE bullet changed to cite 42 -- absent from the cited [EF1]
# entry (metrics "3 8"). Everything else is clean: reverse-chronological, every
# bullet cited, no AI-slop tokens, correct header. So the ONLY violation is 42.
NUMERIC_VIOLATION_CV = "\n".join([
    "JANE ROE", "",
    "WORK EXPERIENCE", "",
    "Example Systems",
    "02/2023–present | Remote | Staff Engineer",
    "- Cut deploy time by 42 percent [EF1]",
    "",
    "Example Analytics",
    "06/2020–01/2023 | Remote | Senior Engineer",
    "- Grew the team from 3 to 8 engineers [EF1]",
    "",
    "CERTIFICATES", "- CSM",
    "EDUCATION", "- Example University, 2015 | BSc",
])


def test_a_cv_citing_an_unbacked_figure_never_ships(tmp_path, monkeypatch):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    backend = ScriptedBackend(cv_by_company={"Example Foundry": NUMERIC_VIOLATION_CV},
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

    assert len(results) == 1
    r = results[0]
    assert r.status == "skipped-gate"
    assert composes == 2                       # composed once, retried exactly once, then skipped
    # EXACTLY one violation, and it is the invented 42 -- so the numeric-check
    # mutation is the only thing that can make this lead render; no unrelated gate
    # failure is masking it (a bare `any(...)` would tolerate a second violation).
    assert len(r.violations) == 1
    assert "INVENTED METRIC" in r.violations[0] and "42" in r.violations[0]
    assert h.recorder.rendered == []           # nothing was ever rendered
