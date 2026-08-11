"""#112: `sluice triage run` at the CLI layer -- cmd_triage_run must surface the actual
triage.engine failure MESSAGES on stderr, not just their count. `report.failures` already
carries actionable strings (dossier fetch errors, judge/lead_id mismatches, and
company-resolve conflicts); a bare `failures=N` gives a user no way to act on them short of
re-running under a debugger."""
from sluice.cli import _build_parser, cmd_triage_run
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.triage.engine import TriageReport


def test_cmd_triage_run_prints_each_failure_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    report = TriageReport(counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=0, backend=None,
                          failures=["dossier Example Co - Analyst.md: connection refused",
                                    "judge 'ghost-lead': no note matches this lead_id "
                                    "(the model likely paraphrased the echoed slug)"])
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "dossier Example Co - Analyst.md: connection refused" in err
    assert ("judge 'ghost-lead': no note matches this lead_id "
            "(the model likely paraphrased the echoed slug)") in err


def test_cmd_triage_run_prints_nothing_extra_when_no_failures(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    report = TriageReport(counts={"keep": 1, "shortlist": 0, "research": 0, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=0, backend=None, failures=[])
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert "failures=0" in capsys.readouterr().err
