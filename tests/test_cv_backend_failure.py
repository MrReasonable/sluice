"""#333: cv fails loudly when its backend is down, and an audit that could not run HOLDS the
CV for sign-off rather than serving it as if it had passed.

Before #333 the advisory audit failed OPEN -- a backend error yielded no flags, so a
possibly-fabricated CV was written send-ready -- and a batch kept composing lead after lead
against a backend that had already failed, a full retry budget per lead.
"""
import json

import pytest

import sluice.cv.render as _render_mod
from sluice.core.backends import BackendError, Completion
from sluice.cv.engine import run_batch, run_one
from tests.test_cv_engine import (_COMPOSE, CLEAN_REPLY, ENTRIES, FakeCache, FakeRenderer,
                                  FakeVault, Note, _cfg)

_DOWN = "claude-max claude-sonnet-4-5: claude-max invocation failed: timed out after 3 attempts"


class _ComposeThenAuditDown:
    """Composes cleanly, then every audit call fails -- the backend dropped between them."""

    def __init__(self, transient=True):
        self.calls = 0
        self.transient = transient

    def complete(self, prompt):
        self.calls += 1
        if prompt.startswith(_COMPOSE):
            return Completion(CLEAN_REPLY)
        raise BackendError(_DOWN, transient=self.transient)


class _AlwaysDown:
    def __init__(self, transient=True):
        self.calls = 0
        self.transient = transient

    def complete(self, prompt):
        self.calls += 1
        raise BackendError(_DOWN, transient=self.transient)


@pytest.fixture
def served(monkeypatch):
    monkeypatch.setattr(_render_mod, "render", lambda *a, **k: "/tmp/x/CV.pdf")
    monkeypatch.setattr(_render_mod, "serve", lambda *a, **k: "CV_deadbeef.pdf")


def _lead():
    return Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})


def test_an_audit_that_could_not_run_holds_the_cv(served):
    note = _lead()
    v = FakeVault(ENTRIES, notes=[note])
    r = run_one(note, v, _cfg(), _ComposeThenAuditDown(), FakeCache(), renderer=FakeRenderer())

    assert r.status == "needs-signoff"
    assert note.ref not in v.written, "an unaudited CV must not be written send-ready"
    claims = json.loads(v.fields[note.ref]["needs_signoff"])
    assert any(c.startswith("unaudited\t") and _DOWN in c for c in claims), claims


def test_with_signoff_switched_off_an_unaudited_cv_still_renders(served):
    # The hold is keyed on `cv.require_signoff` exactly like an `unsupported` flag: a user
    # who switched sign-off off asked for no hold on the audit's account.
    note = _lead()
    v = FakeVault(ENTRIES, notes=[note])
    cfg = _cfg()
    cfg.require_signoff = False
    r = run_one(note, v, cfg, _ComposeThenAuditDown(), FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"


def test_a_failed_voice_check_is_still_treated_as_clean(served):
    # Unchanged on purpose: voice is the opt-in STYLE tier, not fabrication. Pinned so the
    # audit fix above cannot be widened to it by accident.
    class _VoiceDown:
        def complete(self, prompt):
            if prompt.startswith(_COMPOSE):
                return Completion(CLEAN_REPLY)
            if "auditing" in prompt:
                return Completion("supported\tx\tEF1")
            raise BackendError(_DOWN)

    note = _lead()
    v = FakeVault(ENTRIES, notes=[note])
    cfg = _cfg()
    cfg.voice_check = True
    r = run_one(note, v, cfg, _VoiceDown(), FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered" and r.voice_flags == []


def _three_leads():
    return [Note({"status": "shortlist", "company": "Example Foundry", "role": f"Analyst {i}"},
                 path=f"Job Applications/Job Leads/Example Foundry - Analyst {i}.md")
            for i in range(3)]


def test_a_batch_stops_at_the_first_backend_outage():
    backend = _AlwaysDown()
    v = FakeVault(ENTRIES, notes=_three_leads())
    results = run_batch(v, _cfg(), backend, FakeCache(), renderer=FakeRenderer())

    assert [r.status for r in results] == ["backend-unavailable"]
    assert results[0].error == _DOWN
    assert backend.calls == 1, "the remaining leads must not each spend a retry budget"


def test_a_non_transient_failure_stays_per_lead():
    # A truncation or a 400 is a property of THAT lead's prompt, not an outage; the batch
    # carries on to the others, as it always did.
    backend = _AlwaysDown(transient=False)
    v = FakeVault(ENTRIES, notes=_three_leads())
    results = run_batch(v, _cfg(), backend, FakeCache(), renderer=FakeRenderer())
    assert [r.status for r in results] == ["error"] * 3


def test_the_single_lead_path_reports_an_outage_as_a_result(tmp_path, monkeypatch):
    # cv.output_dir defaults to ./cv-output, relative to the cwd by design; without this
    # the run's prompt and run.json land in whatever directory pytest was started from.
    monkeypatch.chdir(tmp_path)
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    # The engine's own FakeVault as the store: it declares a candidate, a CV Layout and a
    # verified entry, so compose_cv's preconditions pass and the lead (no url, so no page
    # visit) reaches the backend.
    store = FakeVault(ENTRIES, notes=[_lead()])
    app = Sluice(Config(), store=store, backend=_AlwaysDown(), renderer=FakeRenderer())
    results = app.compose_cv(lead="Acme")
    assert [r.status for r in results] == ["backend-unavailable"]
    assert results[0].error == _DOWN
    assert store.written == {}


def test_the_single_lead_path_reports_a_non_transient_error_as_a_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # The lead's own failure (a truncation, a 400) is an `error` result in run_batch's own
    # vocabulary, with its reason -- never a traceback out of `cv run --lead`, and never an
    # outage, which would tell the user to wait for a backend that is up.
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config(), store=FakeVault(ENTRIES, notes=[_lead()]),
                 backend=_AlwaysDown(transient=False), renderer=FakeRenderer())
    results = app.compose_cv(lead="Acme")
    assert [r.status for r in results] == ["error"]
    assert results[0].error == _DOWN


def test_the_cli_exits_non_zero_when_a_named_lead_errored(monkeypatch, tmp_path, capsys):
    from sluice.cli import _build_parser, cmd_cv_run
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.cv.engine import CvResult

    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [
        CvResult("Job Applications/Job Leads/X.md", "error", error=_DOWN)])
    code = cmd_cv_run(_build_parser().parse_args(["cv", "run", "--lead", "x"]), Config())
    assert code == 1
    assert _DOWN in capsys.readouterr().err


def test_the_cli_exits_non_zero_on_an_outage(monkeypatch, tmp_path, capsys):
    from sluice.cli import _build_parser, cmd_cv_run
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.cv.engine import CvResult

    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [
        CvResult("Job Applications/Job Leads/X.md", "backend-unavailable", error=_DOWN)])
    code = cmd_cv_run(_build_parser().parse_args(["cv", "run", "--all-shortlist"]), Config())
    assert code == 1
    assert _DOWN in capsys.readouterr().err


def test_the_mcp_tool_reports_the_outage(tmp_path, monkeypatch):
    from sluice.core.vault import Vault
    from sluice.mcpserver import cv_run
    from tests.conftest import make_composable
    from tests.test_mcpserver import _cv_app, _seed

    v = Vault(str(tmp_path))
    make_composable(v)
    slug = _seed(tmp_path, status="shortlist")
    app = _cv_app(v)
    monkeypatch.setattr(app, "compose_cv", lambda **kw: [
        __import__("sluice.cv.engine", fromlist=["CvResult"]).CvResult(
            "x", "backend-unavailable", error=_DOWN)])
    out = cv_run(app, slug)
    assert out["outcome"] == "backend-unavailable" and out["error"] == _DOWN
