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


def test_the_cli_reports_a_triage_subapp_config_error_instead_of_crashing(
        tmp_path, monkeypatch, capsys):
    """load_triage_config() runs LAZILY inside Sluice.triage()/Sluice.doctor(), not
    inside main()'s own load_config() -- so a malformed triage: block previously
    reached the user as a raw traceback instead of the SAME "usage error, not a
    crash" shape a malformed ROOT config key already gets. #120's own
    company_resolve_llm cross-field check is what a real install is most likely to
    trip (turning tier 3 on and forgetting company_resolve_fetch), so this proves
    the general fix -- widening main()'s dispatch wrap, not a triage-specific
    patch -- with that concrete case."""
    from sluice.cli import main
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("triage:\n  company_resolve_llm: true\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    rc = main(["triage", "run", "--no-llm"])
    err = capsys.readouterr().err

    assert rc == 2, "a malformed sub-app config key is a usage error, not a crash"
    assert "Traceback" not in err
    assert "company_resolve_llm" in err and "company_resolve_fetch" in err


def test_the_cli_reports_the_same_triage_config_error_via_doctor(tmp_path, monkeypatch, capsys):
    """doctor is the command whose whole job is diagnosing exactly this -- it must
    get the same clean message, not a traceback, from the SAME widened catch."""
    from sluice.cli import main
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("triage:\n  company_resolve_llm: true\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    rc = main(["doctor", "--offline"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "Traceback" not in err
    assert "company_resolve_llm" in err


def test_cmd_triage_run_prints_the_resolved_by_tier_counts_and_the_llm_call_count(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    report = TriageReport(counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=0, backend=None, failures=[],
                          resolved={"tier1": 0, "tier2": 1, "tier3": 3}, llm_calls=9)
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "resolved={'tier1': 0, 'tier2': 1, 'tier3': 3}" in err
    assert "llm_calls=9" in err
