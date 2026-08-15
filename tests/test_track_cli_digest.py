"""The track digest's guessed-instant warning, at the CLI boundary.

`reconcile`/`engine` are covered by their own tests; this pins the LINE THE HUMAN READS.
Under cron the log stream is usually discarded, so this warning is the only surviving signal
that a calendar entry's hour was assumed rather than stated — and nothing exercised
`cmd_track_run` at all before this file.
"""
import pytest

from sluice import cli
from sluice.core.config import Config
from sluice.track.engine import RunReport


class _Args:
    dry_run = False
    backend = None


@pytest.fixture
def _report(monkeypatch):
    """Drive cmd_track_run off a synthetic RunReport, so no Google client is built."""
    def _run(rep, *, dry_run=False):
        monkeypatch.setattr(cli, "load_config", lambda *a, **k: Config(), raising=False)

        class _Sluice:
            def __init__(self, config):
                pass

            def track(self, **kw):
                return rep

        monkeypatch.setattr("sluice.core.app.Sluice", _Sluice)
        args = _Args()
        args.dry_run = dry_run
        return args
    return _run


def _stderr(capsys):
    return capsys.readouterr().err


def test_the_warning_fires_and_names_the_config_key(_report, capsys):
    rep = RunReport(msgs=1, classified=1, calendar_added=1, calendar_assumed_tz=1)
    assert cli.cmd_track_run(_report(rep), Config()) == 0
    err = _stderr(capsys)
    assert "WARNING" in err, err
    assert "1 calendar entry booked" in err, err
    # The remedy has to be reachable from the warning itself.
    assert "track.calendar_assumed_timezone" in err, err


def test_a_dry_run_says_WOULD_be_booked(_report, capsys):
    # The counter deliberately counts a preview's would-be writes (matching `calendar_added`
    # beside it), so only the VERB may differ. Saying "booked" for a preview sends the reader
    # hunting for a calendar entry that was never created.
    rep = RunReport(msgs=1, classified=1, calendar_added=1, calendar_assumed_tz=1)
    assert cli.cmd_track_run(_report(rep, dry_run=True), Config()) == 0
    err = _stderr(capsys)
    assert "would be booked" in err, err
    assert "entry booked" not in err, f"a dry run must not claim a booking: {err}"


def test_the_plural_matches_the_count(_report, capsys):
    rep = RunReport(msgs=2, classified=2, calendar_added=2, calendar_assumed_tz=2)
    assert cli.cmd_track_run(_report(rep), Config()) == 0
    assert "2 calendar entries booked" in _stderr(capsys)


def test_no_warning_when_no_instant_was_guessed(_report, capsys):
    # The ordinary run must stay quiet, or the line stops meaning anything.
    rep = RunReport(msgs=1, classified=1, calendar_added=1, calendar_assumed_tz=0)
    assert cli.cmd_track_run(_report(rep), Config()) == 0
    err = _stderr(capsys)
    assert "WARNING" not in err, err
    assert "calendar_added=1" in err, "the ordinary digest line should still print"
