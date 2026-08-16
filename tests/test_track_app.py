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
    lead = "Example Telemetry - Analyst"      # two entries share it; m2 has none
    dl.record(Entry("m1", lead, "", "rejection", "x", "h", "2026-07-10", 1))
    dl.record(Entry("m3", lead, "", "rejection", "x", "h", "2026-07-10", 1))
    dl.record(Entry("m2", "", "A,B", "unknown", "y", "h", "2026-07-10", 1))
    app = Sluice(Config())
    # by id, dry-run: reports the count, deletes nothing
    assert app.track_dismiss(message_id="m1", dry_run=True) == {"cleared": 1, "dry_run": True}
    assert len(dl.open_entries()) == 3
    # by lead: clears EVERY entry under that lead (m1 + m3); dry-run counts, deletes nothing
    assert app.track_dismiss(lead=lead, dry_run=True) == {"cleared": 2, "dry_run": True}
    assert len(dl.open_entries()) == 3
    assert app.track_dismiss(lead=lead) == {"cleared": 2, "dry_run": False}
    assert {e.message_id for e in dl.open_entries()} == {"m2"}
    # by id: the only lever for the no-lead entry m2
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


def test_load_seen_raises_on_an_unreadable_store_rather_than_reading_it_empty(tmp_path):
    """`except OSError: return set()` conflated MISSING with UNREADABLE.

    track's dedup store already refuses to start when it has been relocated, so
    shrugging at one that is present but unreadable was incoherent. The compounding is
    narrower than "any unreadable file", though: at mode 000 the SAVE fails too, so
    nothing is rewritten. It bites at mode 0222, where the read fails and the write
    would have succeeded -- measured, after a reviewer falsified the broader claim.

    A directory is used rather than `chmod 000`, which does not deny root and would make
    this row pass for the wrong reason in a container.
    """
    from sluice.core.app import _load_seen

    unreadable = tmp_path / "track-seen.db"
    unreadable.mkdir()
    with pytest.raises(OSError):
        _load_seen(str(unreadable))


def test_load_seen_still_reads_a_missing_store_as_empty(tmp_path):
    # The other arm: absent is the ordinary first-run state and must stay silent.
    from sluice.core.app import _load_seen

    assert _load_seen(str(tmp_path / "nope.db")) == set()


def test_dismiss_by_LEAD_clears_a_calendar_row_too(tmp_path, monkeypatch):
    """`--lead` is a HUMAN saying "nothing here needs action", and is entitled to clear
    everything. `clear_lead` defaults to status-proposals-only for the engine's auto-advance
    clears, and passing that default here silently stopped `--lead` reaching calendar and
    failure rows -- with nothing telling the operator to use `--id`."""
    from sluice.track.deadletter import EV_TYPE_CALENDAR

    seen_db = _cfg(tmp_path, monkeypatch)
    dl = DeadLetterDb(deadletter_path(seen_db))
    dl.record(Entry(message_id="m1", lead="Example Tidal - EM", candidates="",
                    ev_type="interview", proposal="interview", hint="confirm",
                    first_seen="2026-07-10", times_surfaced=1))
    dl.record(Entry(message_id="m2", lead="Example Tidal - EM", candidates="",
                    ev_type=EV_TYPE_CALENDAR, proposal="cancel-unresolved",
                    hint="remove it by hand", first_seen="2026-07-10", times_surfaced=1))

    out = Sluice(Config()).track_dismiss(lead="Example Tidal - EM")
    assert out["cleared"] == 2, "a human dismissal must clear the calendar row too"
    assert dl.open_entries() == []


def test_the_dismiss_DRY_RUN_count_matches_the_real_delete(tmp_path, monkeypatch):
    """`app.py`'s own comment claims the two "can never disagree", and filtering `clear_lead`
    unconditionally made them disagree: the preview said 2, the real command cleared 1, and
    nothing named the row that survived."""
    from sluice.track.deadletter import EV_TYPE_CALENDAR, EV_TYPE_FAILURE

    seen_db = _cfg(tmp_path, monkeypatch)
    dl = DeadLetterDb(deadletter_path(seen_db))
    for mid, kind in (("m1", "interview"), ("m2", EV_TYPE_CALENDAR), ("m3", EV_TYPE_FAILURE)):
        dl.record(Entry(message_id=mid, lead="Example Tidal - EM", candidates="",
                        ev_type=kind, proposal="p", hint="h",
                        first_seen="2026-07-10", times_surfaced=1))

    preview = Sluice(Config()).track_dismiss(lead="Example Tidal - EM", dry_run=True)
    real = Sluice(Config()).track_dismiss(lead="Example Tidal - EM")
    assert preview["cleared"] == real["cleared"], (
        f"preview said {preview['cleared']}, the real run cleared {real['cleared']}")
