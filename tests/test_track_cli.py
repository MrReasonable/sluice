import tempfile, pathlib
from types import SimpleNamespace
import pytest
from sluice.cli import _build_parser, cmd_track_confirm, cmd_track_dismiss
# _load_lastrun/_save_lastrun moved from cli.py to core/app.py in Task 6 (they are
# Sluice.track()'s file-backed state now, not cli.py wiring).
from sluice.core.app import _load_lastrun, _save_lastrun
from sluice.track.deadletter import DeadLetterDb, deadletter_path, Entry


def test_track_run_parses_flags():
    a = _build_parser().parse_args(["track", "run", "--dry-run", "--backend", "primary"])
    assert a.group == "track" and a.cmd == "run" and a.dry_run and a.backend == "primary"


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

    # deadletter_error -> rc 0 (not an auth failure) but STILL no lastrun save (F3):
    # a dead-letter write failure means the message never persisted, so advancing
    # the watermark past it would drop it out of next run's Gmail `after:` window.
    monkeypatch.setattr(teng, "run", lambda *a, **k: RunReport(deadletter_error=True))
    assert cmd_track_run(args(False), None) == 0
    assert not os.path.exists(lastrun)


def test_track_dismiss_parses_mutually_exclusive_required():
    a = _build_parser().parse_args(["track", "dismiss", "--id", "m1"])
    assert a.group == "track" and a.cmd == "dismiss" and a.id == "m1"
    a2 = _build_parser().parse_args(["track", "dismiss", "--lead", "tidemark", "--dry-run"])
    assert a2.lead == "tidemark" and a2.dry_run
    with pytest.raises(SystemExit):                       # both -> mutually exclusive
        _build_parser().parse_args(["track", "dismiss", "--id", "m1", "--lead", "x"])
    with pytest.raises(SystemExit):                       # neither -> required
        _build_parser().parse_args(["track", "dismiss"])


def test_track_dismiss_by_id_and_by_lead(monkeypatch, tmp_path):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    seen_db = str(tmp_path / "track-seen.db")
    cfgp = str(tmp_path / "cfg.yaml")
    pathlib.Path(cfgp).write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", cfgp)
    dl = DeadLetterDb(deadletter_path(seen_db))
    dl.record(Entry("m1", "Tidemark - Analyst", "", "rejection", "x", "h", "2026-07-10", 1))
    dl.record(Entry("m2", "", "A,B", "unknown", "y", "h", "2026-07-10", 1))
    app = Sluice(Config())
    # dry-run reports the count, deletes nothing
    assert app.track_dismiss(message_id="m1", dry_run=True) == {"cleared": 1, "dry_run": True}
    assert len(dl.open_entries()) == 2
    # real dismiss by id (the only lever for the no-lead entry m2 is --id)
    assert app.track_dismiss(message_id="m1") == {"cleared": 1, "dry_run": False}
    assert {e.message_id for e in dl.open_entries()} == {"m2"}
    assert app.track_dismiss(message_id="m2")["cleared"] == 1
    assert dl.open_entries() == []


def test_track_dismiss_requires_exactly_one_selector(monkeypatch, tmp_path):
    # Finding 1/2 (review pass): neither-given must fail loudly rather than
    # silently matching zero rows (clear_lead(None) -> `WHERE lead = NULL`,
    # never true); both-given must fail loudly rather than letting the dry-run
    # branch (union of id-or-lead) and the real branch (id-only) disagree.
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    seen_db = str(tmp_path / "track-seen.db")
    cfgp = str(tmp_path / "cfg.yaml")
    pathlib.Path(cfgp).write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", cfgp)
    app = Sluice(Config())
    with pytest.raises(ValueError):
        app.track_dismiss(message_id=None, lead=None)
    with pytest.raises(ValueError):
        app.track_dismiss(message_id="m1", lead="tidemark")


def test_cmd_track_dismiss_dry_run_then_real(monkeypatch, tmp_path):
    # Mirrors test_cmd_track_confirm_advances: exercises the args -> Sluice
    # pass-through and the process-exit contract at the cmd_track_dismiss
    # level, which neither the argparse-only nor the Sluice-method-only
    # dismiss tests above cover (finding 3).
    seen_db = str(tmp_path / "track-seen.db")
    cfgp = str(tmp_path / "cfg.yaml")
    pathlib.Path(cfgp).write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", cfgp)
    dl = DeadLetterDb(deadletter_path(seen_db))
    dl.record(Entry("m1", "Tidemark - Analyst", "", "rejection", "x", "h", "2026-07-10", 1))

    dry_args = SimpleNamespace(id="m1", lead=None, dry_run=True)
    assert cmd_track_dismiss(dry_args, None) == 0
    assert len(dl.open_entries()) == 1        # dry-run threaded through: deleted nothing

    real_args = SimpleNamespace(id="m1", lead=None, dry_run=False)
    assert cmd_track_dismiss(real_args, None) == 0
    assert dl.open_entries() == []            # non-dry-run actually clears the row
