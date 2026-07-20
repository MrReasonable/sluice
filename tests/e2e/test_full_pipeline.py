"""One run through the whole composition root: ingest -> triage -> cv -> apply ->
track, asserting a USER-VISIBLE outcome at every hop. Fakes sit only at the true
I/O boundaries (browser client, renderer, LLM backend, Google client); the real
`Sluice`, the real `Vault` on `tmp_path`, and every sub-app's real engine run in
between.

The run deliberately carries one lead whose composed CV FAILS the fabrication
gate, so the recording renderer can witness that no CV was ever rendered for it
(the gate's whole point), while a second, clean lead completes the walk to a
`rejected` terminal.
"""
import os

from sluice.ingest import sources as _sources

from tests.harness import (
    FakeGoogleClient,
    GATE_FAILING_CV,
    PASSING_CV,
    ScriptedBackend,
    build_harness,
)

BOARD_URL = "https://remoteok.example/harness"

# Two synthetic leads. The first composes a clean CV and walks to the end; the
# second's composed CV drifts its WORK EXPERIENCE header and is HARD-gated.
ROWS = [
    {"title": "Staff Engineer", "company": "Example Foundry",
     "link": "https://remoteok.example/jobs/1", "salary": ""},
    {"title": "Senior Engineer", "company": "Example Telemetry",
     "link": "https://remoteok.example/jobs/2", "salary": ""},
]


def _statuses(vault):
    return {n.fm["company"]: n.status for n in vault.read_leads()}


def test_full_pipeline_walk(tmp_path, monkeypatch):
    cwd = os.getcwd()
    repo_root_before = set(os.listdir(cwd))

    h = build_harness(tmp_path, monkeypatch, board_url=BOARD_URL, rows=ROWS)
    backend = ScriptedBackend(
        cv_by_company={"Example Foundry": PASSING_CV,
                       "Example Telemetry": GATE_FAILING_CV},
        # Every kept lead is shortlisted, so both reach the CV hop.
        default_verdict="shortlist",
        # The rejection email that ends the walk, matched by its subject marker.
        track_response=[("REJECTION-SIGNAL",
                         {"lead": "Example Foundry", "type": "rejection",
                          "confidence": 0.95, "when": None, "links": [],
                          "materials": [], "summary": "not moving forward"})])
    app = h.sluice(backend, today=lambda: "2026-07-09")
    src = _sources.get("remoteok")

    # ── ingest: two brand-new leads land as notes ──
    report = app.ingest([src])
    assert report.written["created"] == 2
    assert report.sources[0].status == "ok"
    assert _statuses(h.vault) == {"Example Foundry": "new", "Example Telemetry": "new"}

    # ── triage: the deterministic gate keeps both, the (stubbed) judge shortlists ──
    treport = app.triage(statuses=("new",))
    assert treport.counts["shortlist"] == 2
    assert _statuses(h.vault) == {"Example Foundry": "shortlist",
                                  "Example Telemetry": "shortlist"}

    # ── cv: the clean lead renders; the gate-failing lead is NEVER rendered ──
    results = {r.lead.split(" - ")[0].split("/")[-1]: r
               for r in app.compose_cv(all_shortlist=True)}
    assert results["Example Foundry"].status == "rendered"
    assert results["Example Telemetry"].status == "skipped-gate"
    # THE fabrication-gate assertion, at the composition-root level: the recorder
    # saw exactly the one clean CV -- exact equality, so it catches both a
    # spurious extra render and the drifted CV being rendered past the gate.
    assert h.recorder.rendered == [PASSING_CV]
    # The rendered lead now carries a tailored_cv marker; the gate-failing one does not.
    tailored = {n.fm["company"]: n.fm.get("tailored_cv", "") for n in h.vault.read_leads()}
    assert tailored["Example Foundry"] and not tailored["Example Telemetry"]

    # ── apply: shortlist -> applied, the only transition apply may make ──
    prep = app.prep(lead="example-foundry")
    assert prep[0].status == "staged"
    rec = app.record(lead="example-foundry")
    assert rec["ok"] is True
    assert _statuses(h.vault)["Example Foundry"] == "applied"
    # The gate-failing lead never became applyable.
    assert _statuses(h.vault)["Example Telemetry"] == "shortlist"

    # ── track: a rejection email advances the applied lead to the terminal ──
    messages = {"msg-1": {
        "headers": {"from": "noreply@example.invalid",
                    "subject": "Update on your application REJECTION-SIGNAL"},
        "body_text": "Thank you for applying. We are not moving forward.",
        "thread_id": "th-1", "attachments": []}}
    trep = app.track(client=FakeGoogleClient(messages),
                     now_iso="2026-07-15T00:00:00+00:00")
    assert trep.auto == 1
    assert _statuses(h.vault)["Example Foundry"] == "rejected"

    # ── nothing leaked into the repo: every cwd-relative default was pinned ──
    assert set(os.listdir(cwd)) == repo_root_before
