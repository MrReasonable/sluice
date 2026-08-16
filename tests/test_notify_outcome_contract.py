"""Every sub-app that notifies must REPORT what happened to the notification.

`notify` gained a three-state outcome because `_telegram_sender` swallows transport errors:
a revoked token, a wrong chat_id, a 4xx or a dead network all read as delivered, on exactly
the run whose alert mattered. Track consumed the new outcome first and the other three did
not, so the fix was routed through one `_notify_reporting` helper.

That helper had no tests at all, and five separate mutants survived the whole suite: both of
its branches, and each of the three sub-apps reverted to a bare `notify(...)`. The commit
claiming "all four sub-apps now report the outcome" was unfalsifiable.

Parameterised over the COMMANDS rather than written four times, so a fifth sub-app that
notifies joins this by existing.
"""
import pytest

from sluice import cli
from sluice.core.config import Config


class _Args:
    """Any flag, defaulting to None.

    Hand-listing the flags each command reads is the same fix-one-instance trap this file is
    about: the list goes stale the moment a command gains an option, and the failure looks
    like an unrelated AttributeError. None is falsy, so `args.dry_run` and friends behave as
    unset, and `args.status or "default"` picks its own default.
    """

    def __getattr__(self, _name):
        return None


class _Report:
    """A report stub that answers ANY field with an empty tuple.

    Same reasoning as `_Args`: hand-listing what each digest happens to print today makes this
    file fail with an unrelated AttributeError the next time a digest gains a counter, and the
    fields are not what is under test here -- the notify outcome is. `()` is falsy, has a
    length, iterates, and formats, which covers every use a digest line makes of one.
    """

    def __getattr__(self, _name):
        return ()


def _drive_ingest(monkeypatch):
    class _Rep(_Report):
        degraded = True
        sources = [type("S", (), {"source_id": "example-source", "drift": "zero",
                                  "retired": False, "status": "ok"})()]

    monkeypatch.setattr("sluice.core.app.Sluice.ingest", lambda self, *a, **k: _Rep())
    monkeypatch.setattr(cli, "_print_report", lambda r: None)
    return lambda: cli.cmd_run(_Args(), Config())


def _drive_triage(monkeypatch):
    class _Rep(_Report):
        counts = {"shortlist": 1}
        backend = "fake"

    monkeypatch.setattr("sluice.core.app.Sluice.triage", lambda self, *a, **k: _Rep())
    return lambda: cli.cmd_triage_run(_Args(), Config())


def _drive_cv(monkeypatch):
    class _R(_Report):
        status = "rendered"
        served = "example-lead"

    monkeypatch.setattr("sluice.core.app.Sluice.compose_cv", lambda self, *a, **k: [_R()])
    return lambda: cli.cmd_cv_run(_Args(), Config())


def _drive_track(monkeypatch):
    from sluice.track.engine import RunReport, TrackFailure

    rep = RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom",
                                                   kind="RuntimeError")])
    monkeypatch.setattr("sluice.core.app.Sluice.track", lambda self, *a, **k: rep)
    return lambda: cli.cmd_track_run(_Args(), Config())


_DRIVERS = {"ingest": _drive_ingest, "triage": _drive_triage,
            "cv": _drive_cv, "track": _drive_track}


@pytest.mark.parametrize("app", sorted(_DRIVERS))
def test_a_notification_the_transport_REJECTED_is_reported(app, monkeypatch, capsys):
    """The state that was invisible before. Nothing raises -- notify must never take down a
    scan -- so the only way an operator learns is if the digest says so."""
    run = _DRIVERS[app](monkeypatch)
    monkeypatch.setattr(cli, "notify", lambda body, config=None: "failed")
    run()
    err = capsys.readouterr().err.lower()
    assert "could not be delivered" in err, (
        f"{app} swallowed a failed notification: {err!r}")


@pytest.mark.parametrize("app", sorted(_DRIVERS))
def test_a_DELIVERED_notification_is_not_reported_as_a_problem(app, monkeypatch, capsys):
    """The inverse, which nothing asserted -- so a digest that ALWAYS warned would have been
    indistinguishable from a correct one."""
    run = _DRIVERS[app](monkeypatch)
    monkeypatch.setattr(cli, "notify", lambda body, config=None: "sent")
    run()
    err = capsys.readouterr().err.lower()
    assert "could not be delivered" not in err, err
    assert "no notification sent" not in err, err


@pytest.mark.parametrize("app", sorted(_DRIVERS))
def test_every_notifying_sub_app_goes_through_the_shared_helper(app, monkeypatch):
    """Asserted by OBSERVING the call, not by reading the source.

    Reverting any one sub-app to a bare `notify(...)` survived every other test here, because
    each of those only checks its own command. This is the conformance half: the helper is the
    single place the outcome->message mapping lives, and the next sub-app to adopt it must not
    copy the mapping instead.
    """
    seen = {}
    real = cli._notify_reporting

    def _spy(body, *, config, label, unconfigured_note=None):
        seen["label"] = label
        return real(body, config=config, label=label, unconfigured_note=unconfigured_note)

    run = _DRIVERS[app](monkeypatch)
    monkeypatch.setattr(cli, "_notify_reporting", _spy)
    monkeypatch.setattr(cli, "notify", lambda body, config=None: "sent")
    run()
    assert seen.get("label"), f"{app} notified without going through _notify_reporting"


def test_the_unconfigured_note_is_opt_in(monkeypatch, capsys):
    """Track's failure alert says so when no token is configured -- the alert channel does not
    exist and the operator should know. Triage's and cv's routine success digests do not, or
    every run gains a line beside the digest they already print, which is how a line stops
    being read."""
    run = _DRIVERS["track"](monkeypatch)
    monkeypatch.setattr(cli, "notify", lambda body, config=None: "unconfigured")
    run()
    assert "no notification sent" in capsys.readouterr().err.lower()

    run = _DRIVERS["triage"](monkeypatch)
    monkeypatch.setattr(cli, "notify", lambda body, config=None: "unconfigured")
    run()
    assert "no notification sent" not in capsys.readouterr().err.lower()
