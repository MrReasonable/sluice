"""track handlers through the real main(argv). Re-homed from tests/test_track_cli.py.

The `run` gating is exercised through the one legitimate sub-engine stub: track's real
run needs a live Gmail + LLM, but the handler's post-engine logic -- the four-branch
lastrun watermark contract -- is deterministic and belongs here. The below-CLI pieces
(the _lastrun helpers, Sluice.track_dismiss's dict returns and its selector guard) move
to tests/test_track_app.py; this file keeps the three dispatch-level behaviours.
"""
import os

import pytest

from sluice.cli import _build_parser
from sluice.track.deadletter import DeadLetterDb, Entry, deadletter_path
from tests.harness import ScriptedBackend


# ── parser wiring (kept) ─────────────────────────────────────────────────────
def test_track_run_parses_flags():
    a = _build_parser().parse_args(["track", "run", "--dry-run", "--backend", "primary"])
    assert a.group == "track" and a.cmd == "run" and a.dry_run and a.backend == "primary"


def test_track_confirm_parses_args():
    a = _build_parser().parse_args(
        ["track", "confirm", "--lead", "example-telemetry", "--to", "offer",
         "--when", "2026-07-20T10:00"])
    assert a.group == "track" and a.cmd == "confirm"
    assert a.lead == "example-telemetry" and a.to == "offer" and a.when == "2026-07-20T10:00"


def test_track_dismiss_parses_mutually_exclusive_required():
    a = _build_parser().parse_args(["track", "dismiss", "--id", "m1"])
    assert a.group == "track" and a.cmd == "dismiss" and a.id == "m1"
    a2 = _build_parser().parse_args(["track", "dismiss", "--lead", "example-telemetry", "--dry-run"])
    assert a2.lead == "example-telemetry" and a2.dry_run
    with pytest.raises(SystemExit):        # both -> mutually exclusive
        _build_parser().parse_args(["track", "dismiss", "--id", "m1", "--lead", "x"])
    with pytest.raises(SystemExit):        # neither -> required
        _build_parser().parse_args(["track", "dismiss"])


# ── the handlers ─────────────────────────────────────────────────────────────
def test_track_confirm_advances(cli):
    h, run = cli()
    leads = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    note = os.path.join(leads, "Example Telemetry - Analyst.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write('---\ncompany: "Example Telemetry"\nrole: "Analyst"\nstatus: interview\n---\n\nB\n')
    rc, _out, _err = run(["track", "confirm", "--lead", "example-telemetry", "--to", "offer"])
    assert rc == 0
    with open(note, encoding="utf-8") as f:
        assert "status: offer" in f.read()      # interview -> offer, a forward ladder move


def test_track_run_lastrun_gating(cli, monkeypatch):
    # The four-branch watermark contract, via the engine stub. A ScriptedBackend
    # override keeps Sluice.track's backend build off the real providers; the stub
    # means it is never actually called.
    import sluice.track.engine as teng
    from sluice.track.engine import RunReport

    h, run = cli(backend=ScriptedBackend())
    lastrun = h.paths["track_seen_db"] + ".lastrun"

    # success (non-dry-run) -> lastrun saved, rc 0
    monkeypatch.setattr(teng, "run", lambda *a, **k: RunReport(msgs=1, classified=1, auto=1))
    assert run(["track", "run"])[0] == 0
    assert os.path.exists(lastrun)
    os.remove(lastrun)

    # dry-run -> no lastrun save
    assert run(["track", "run", "--dry-run"])[0] == 0
    assert not os.path.exists(lastrun)

    # auth_error -> rc 1, no lastrun save
    monkeypatch.setattr(teng, "run", lambda *a, **k: RunReport(auth_error=True))
    assert run(["track", "run"])[0] == 1
    assert not os.path.exists(lastrun)

    # deadletter_error -> rc 0 (not an auth failure) but STILL no lastrun (F3): the
    # message never persisted, so advancing the watermark past it would drop it.
    monkeypatch.setattr(teng, "run", lambda *a, **k: RunReport(deadletter_error=True))
    assert run(["track", "run"])[0] == 0
    assert not os.path.exists(lastrun)


def test_track_dismiss_dry_run_then_real(cli):
    # cmd_track_dismiss dispatch: args -> Sluice.track_dismiss pass-through and the
    # rc contract, which neither the parser-only nor the Sluice-method-only tests
    # cover (test_track_cli.py's "finding 3").
    h, run = cli()
    dl = DeadLetterDb(deadletter_path(h.paths["track_seen_db"]))
    dl.record(Entry("m1", "Example Telemetry - Analyst", "", "rejection", "x", "h", "2026-07-10", 1))

    assert run(["track", "dismiss", "--id", "m1", "--dry-run"])[0] == 0
    assert len(dl.open_entries()) == 1          # dry-run threaded through: deleted nothing

    assert run(["track", "dismiss", "--id", "m1"])[0] == 0
    assert dl.open_entries() == []              # non-dry-run actually clears the row
