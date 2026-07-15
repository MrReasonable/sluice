import tempfile, pathlib
from types import SimpleNamespace
from sluice.cli import _build_parser, cmd_track_confirm, _track_backend
# _load_lastrun/_save_lastrun moved from cli.py to core/app.py in Task 6 (they are
# Sluice.track()'s file-backed state now, not cli.py wiring).
from sluice.core.app import _load_lastrun, _save_lastrun
from sluice.core.backends import DEFAULT_BASE_URLS


def test_track_run_parses_flags():
    a = _build_parser().parse_args(["track", "run", "--dry-run", "--limit", "5", "--json"])
    assert a.group == "track" and a.cmd == "run" and a.dry_run and a.limit == 5 and a.json


def test_track_confirm_parses_args():
    a = _build_parser().parse_args(["track", "confirm", "--lead", "tidemark", "--to", "offer", "--when", "2026-07-20T10:00"])
    assert a.group == "track" and a.cmd == "confirm"
    assert a.lead == "tidemark" and a.to == "offer" and a.when == "2026-07-20T10:00"


def test_cmd_track_confirm_advances(monkeypatch):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / "Tidemark - Analyst.md").write_text('---\ncompany: "Tidemark"\nrole: "Analyst"\nstatus: interview\n---\n\nBODY\n')
    monkeypatch.setenv("VAULT_DIR", root)
    args = SimpleNamespace(lead="tidemark", to="offer", when=None, dry_run=False)
    assert cmd_track_confirm(args, None) == 0
    assert "status: offer" in (leads / "Tidemark - Analyst.md").read_text()


def test_track_backend_wiring(monkeypatch):
    # Mirrors test_backend_fallback_targets_deepseek_direct in test_triage_cli.py.
    # _track_backend has its own zero coverage: mutating tcfg.cheap_model ->
    # tcfg.claude_max_model when building the OpenAiCompatibleBackend fallback
    # passes the whole suite otherwise. Pin every field the wiring is
    # responsible for so that swap fails here.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)  # exercise the default
    from sluice.core.backends import ClaudeMaxBackend, OpenAiCompatibleBackend
    from sluice.track.config import TrackConfig

    tcfg = TrackConfig()
    be = _track_backend(tcfg)
    assert isinstance(be.primary, ClaudeMaxBackend)
    assert be.primary.model == tcfg.claude_max_model == "claude-sonnet-4-5"
    assert isinstance(be.fallback, OpenAiCompatibleBackend)
    assert be.fallback.model == tcfg.cheap_model == "deepseek-v4-flash"
    # Assert the default endpoint via the constant, not a live URL literal:
    # pins that the provider default is applied and the path appended.
    assert be.fallback.url == DEFAULT_BASE_URLS["deepseek"] + "/chat/completions"
    assert be.fallback.api_key == "sk-test"


def test_lastrun_roundtrip(tmp_path):
    path = str(tmp_path / "nested" / "track-seen.db.lastrun")
    assert _load_lastrun(path) is None
    _save_lastrun(path, "2026-07-09T12:00:00+00:00")
    assert _load_lastrun(path) == "2026-07-09T12:00:00+00:00"


def test_cmd_track_run_lastrun_gating(monkeypatch, tmp_path):
    import os
    from types import SimpleNamespace
    import sluice.track.engine as teng
    from sluice.track.engine import RunReport
    from sluice.cli import cmd_track_run

    seen_db = str(tmp_path / "seen.db")
    cfgp = str(tmp_path / "cfg.yaml")
    with open(cfgp, "w") as f:
        f.write(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", cfgp)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    lastrun = seen_db + ".lastrun"
    # backend mirrors the real parser default now that track honours --backend.
    args = lambda dry: SimpleNamespace(dry_run=dry, limit=None, json=False, backend="auto")

    # success (non-dry-run) -> saves lastrun, rc 0
    monkeypatch.setattr(teng, "run", lambda *a, **k: RunReport(msgs=1, classified=1, auto=1))
    assert cmd_track_run(args(False), None) == 0
    assert os.path.exists(lastrun)
    os.remove(lastrun)

    # dry-run -> no lastrun save
    assert cmd_track_run(args(True), None) == 0
    assert not os.path.exists(lastrun)

    # auth_error -> rc 1, no lastrun save
    monkeypatch.setattr(teng, "run", lambda *a, **k: RunReport(auth_error=True))
    assert cmd_track_run(args(False), None) == 1
    assert not os.path.exists(lastrun)
