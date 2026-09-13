"""A run through the pipeline can afterwards say what it cost (#308).

This is the RUNTIME half of the metering guarantee. `tests/test_usage_wiring.py` proves the
wiring is declared -- every `.complete(` call site is rostered against a stage. It cannot
prove the wiring FIRES: `meter` wraps where a backend is handed to a stage, and a wrap aimed
at the wrong object, or a log that resolved somewhere nothing reads, leaves the code looking
exactly right and the report empty. A missing row and a free call are indistinguishable in
the report, so there is no other signal.

So this drives the real composition root -- real `Sluice`, real engines, real `Vault` on
tmp_path, fakes only at the I/O boundaries -- across triage, cv and track, and then asserts on
the file the run wrote and on what `job-sluice usage` says about it. The acceptance criterion
#308 states is the last two assertions: given a run, say what it cost and see the cache hit
rate, without inference.
"""
import json
import os

from sluice.cli import main
from sluice.ingest import sources as _sources

from tests.harness import (
    FakeGoogleClient,
    PASSING_CV,
    ScriptedBackend,
    build_harness,
)

BOARD_URL = "https://remoteok.example/harness"

ROWS = [
    {"title": "Staff Engineer", "company": "Example Foundry",
     "link": "https://remoteok.example/jobs/1", "salary": ""},
]


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_a_run_reports_what_it_spent(tmp_path, monkeypatch, capsys):
    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    backend = ScriptedBackend(
        cv_by_company={"Example Foundry": PASSING_CV},
        default_verdict="shortlist",
        track_response=[("REJECTION-SIGNAL",
                         {"lead": "Example Foundry", "type": "rejection",
                          "confidence": 0.95, "when": None, "links": [],
                          "materials": [], "summary": "not moving forward"})])
    app = h.sluice(backend, today=lambda: "2026-07-09")
    usage_path = h.paths["usage"]

    # Nothing yet -- and nothing CREATED yet either. A log that springs into existence before
    # the first call would make "no calls recorded" unreachable on a fresh install.
    assert not os.path.exists(usage_path)

    app.ingest([_sources.get("remoteok")])
    # Ingest spends no LLM call at all, and that is worth pinning: a metering wrap accidentally
    # placed on the ingest path would invent rows for a stage that never calls a backend.
    assert not os.path.exists(usage_path)

    app.triage(statuses=("new",))
    app.compose_cv(all_shortlist=True)
    app.track(client=FakeGoogleClient({"msg-1": {
        "headers": {"from": "noreply@example.invalid",
                    "subject": "Update on your application REJECTION-SIGNAL"},
        "body_text": "Thank you for applying. We are not moving forward.",
        "thread_id": "th-1", "attachments": []}}),
        now_iso="2026-07-15T00:00:00+00:00")

    rows = _rows(usage_path)
    stages = {r["stage"] for r in rows}
    # The stages a DEFAULT pipeline reaches. `cv-voice` needs `cv.voice_check` on and
    # `triage-resolve` needs `company_resolve_llm` on, so both are off here and are witnessed
    # by their own tests (see `_STAGES` in tests/test_usage_wiring.py); `doctor-probe` needs a
    # live round trip this run never makes.
    assert stages == {"triage-judge", "cv-compose", "cv-audit", "track-classify"}

    # Every row identifies its call -- the property `Completion.usage` exists for. A row that
    # could not say which provider and model it went to is not an answer to "where did the
    # tokens go", and it is the shape a bare `usage=None` from a backend produces.
    assert all(r["provider"] and r["model"] for r in rows)
    assert all(isinstance(r["input_tokens"], int) for r in rows)

    # The per-lead stages carry the lead; the batching stage does not, because the judge sends
    # several dossiers in one call and there is no single lead to name.
    by_stage = {}
    for r in rows:
        by_stage.setdefault(r["stage"], []).append(r)
    assert all("lead" in r for r in by_stage["cv-compose"])
    assert all("lead" not in r for r in by_stage["triage-judge"])

    # ── the acceptance criterion, through the command a user actually runs ──
    assert main(["usage", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["path"] == usage_path
    assert report["total"]["calls"] == len(rows)
    assert report["total"]["input_tokens"] > 0
    # "Which stage is expensive" is answerable: compose sends the whole bundle plus the job
    # description, so it dwarfs the audit over the composed CV alone. Asserted as a RELATION
    # between two stages rather than against a magic number, so it says something about the
    # pipeline rather than about the fixture's sizes.
    assert (report["by_stage"]["cv-compose"]["input_tokens"]
            > report["by_stage"]["cv-audit"]["input_tokens"])
    # And the hit rate is a real number rather than null: the scripted backend reports a
    # cache_read of 0, which is a measured zero. Null here would mean nothing was measured.
    assert report["total"]["hit_rate"] == 0.0
