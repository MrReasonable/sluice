"""Track's below-CLI pieces, re-homed from tests/test_track_cli.py: the lastrun
watermark helpers, and Sluice.track_dismiss's dict returns + dead-letter state +
its exactly-one-selector guard. These live below the handler (no argparse, no
dispatch), so they belong here rather than in the functional tier.
"""
import pathlib

import pytest

from sluice.core.app import Sluice, _load_lastrun, _save_lastrun
from sluice.core.config import Config
from sluice.track.deadletter import DeadLetterDb, Entry, deadletter_path


def test_lastrun_roundtrip(tmp_path):
    path = str(tmp_path / "nested" / "track-seen.db.lastrun")
    assert _load_lastrun(path) is None
    _save_lastrun(path, "2026-07-09T12:00:00+00:00")
    assert _load_lastrun(path) == "2026-07-09T12:00:00+00:00"


def _cfg(tmp_path, monkeypatch):
    seen_db = str(tmp_path / "track-seen.db")
    cfgp = str(tmp_path / "cfg.yaml")
    pathlib.Path(cfgp).write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", cfgp)
    return seen_db


def test_track_dismiss_by_id_and_by_lead(tmp_path, monkeypatch):
    seen_db = _cfg(tmp_path, monkeypatch)
    dl = DeadLetterDb(deadletter_path(seen_db))
    dl.record(Entry("m1", "Example Telemetry - Analyst", "", "rejection", "x", "h", "2026-07-10", 1))
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


def test_track_dismiss_requires_exactly_one_selector(tmp_path, monkeypatch):
    # Neither-given must fail loudly rather than silently matching zero rows
    # (clear_lead(None) -> `WHERE lead = NULL`, never true); both-given must fail
    # loudly rather than letting the dry-run branch (id-or-lead union) and the real
    # branch (id-only) disagree.
    _cfg(tmp_path, monkeypatch)
    app = Sluice(Config())
    with pytest.raises(ValueError):
        app.track_dismiss(message_id=None, lead=None)
    with pytest.raises(ValueError):
        app.track_dismiss(message_id="m1", lead="example-telemetry")
