import tempfile, pathlib
from types import SimpleNamespace
from sluice.cli import _build_parser, cmd_track_confirm
# _load_lastrun/_save_lastrun moved from cli.py to core/app.py in Task 6 (they are
# Sluice.track()'s file-backed state now, not cli.py wiring).
from sluice.core.app import _load_lastrun, _save_lastrun


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


# The former _track_backend field-routing test (primary/fallback model, fallback
# url/key) moved to tests/test_backend_selection.py (generic role/provider
# construction) and
# tests/test_app_operations.py::test_track_threads_the_track_config_into_the_backend
# (track's specific config-field mapping into Sluice.backend's kwargs), now that
# track's backend construction is Sluice.backend() rather than a cli.py wrapper.


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
