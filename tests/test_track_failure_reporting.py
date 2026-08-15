"""A track run that dropped messages must SAY WHICH, notify, and not exit 0.

#140. `RunReport.failures` was a bare `int`. `cmd_track_run` printed `failures=N` and returned
0, and called no `notify()` -- `notify` appears for ingest, triage and cv, and nowhere in
track. Under cron that is: exit 0, no Telegram line, and a bare count on a stderr stream that
is usually discarded.

`sluice/triage/engine.py` already had the right shape -- `failures` is a LIST of messages and
`cmd_triage_run` prints each one -- so this is matching an in-repo precedent rather than
inventing a convention.

Deliberately NOT changing the exit code for a partial failure alone: `docs/USAGE.md` documents
"exit 1 only on a Google reauth failure", cron alerting is built on that, and a single
transient message failure making every run "fail" is the kind of noise that gets an alert
muted. What changes is that the run is no longer SILENT about it.
"""
import pytest

from sluice import cli
from sluice.core.config import Config
from sluice.track.engine import RunReport


class _Args:
    dry_run = False
    backend = None


@pytest.fixture
def _run(monkeypatch):
    sent = []

    def _drive(rep):
        class _Sluice:
            def __init__(self, config):
                pass

            def track(self, **kw):
                return rep

        monkeypatch.setattr("sluice.core.app.Sluice", _Sluice)
        monkeypatch.setattr(cli, "notify", lambda body, config=None: sent.append(body))
        return cli.cmd_track_run(_Args(), Config()), sent
    return _drive


def test_failures_is_a_list_so_the_count_and_the_detail_cannot_disagree():
    """`failures=N` printed from `len()` of the same list that carries the detail.

    Two fields would drift; the digest would say 3 and name 1.
    """
    rep = RunReport()
    assert isinstance(rep.failures, list)


def test_each_failed_message_is_NAMED_not_just_counted(_run, capsys):
    rep = RunReport(msgs=2, classified=1,
                    failures=["m1: gmail hiccup", "m2: malformed attachment"])
    code, _sent = _run(rep)
    err = capsys.readouterr().err
    assert "failures=2" in err, "the count must survive the change"
    assert "m1: gmail hiccup" in err and "m2: malformed attachment" in err, err


def test_a_run_with_failures_NOTIFIES(_run):
    """Track was the only sub-app that never notified.

    Under cron the stderr digest is usually discarded, so a dropped interview invite reached
    nobody. ingest, triage and cv all notify; this matches them.
    """
    rep = RunReport(msgs=1, failures=["m1: boom"])
    _code, sent = _run(rep)
    assert sent, "a run that dropped a message must notify"
    assert "m1" in sent[0], f"the notification must name what was dropped: {sent[0]}"


def test_a_clean_run_does_not_notify(_run):
    # Notifying on every run is how a notification stops being read.
    _code, sent = _run(RunReport(msgs=3, classified=3))
    assert sent == []


def test_a_partial_failure_still_exits_0(_run):
    """docs/USAGE.md documents exit 1 ONLY for a reauth failure, and cron alerting relies on
    it. A transient single-message failure making every run "fail" is how an alert gets muted
    -- which would cost more than it buys. The change here is that the run is no longer
    SILENT, not that it starts failing.
    """
    code, _sent = _run(RunReport(msgs=1, failures=["m1: boom"]))
    assert code == 0


def test_an_auth_error_still_exits_1(_run):
    code, _sent = _run(RunReport(auth_error=True))
    assert code == 1
