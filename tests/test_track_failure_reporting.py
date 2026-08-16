"""A track run that dropped messages must SAY WHICH, and notify -- while still exiting 0.

#140. `RunReport.failures` was a bare `int`. `cmd_track_run` printed `failures=N` and returned
0, and called no `notify()` -- `notify` appears for ingest, triage and cv, and nowhere in
track. Under cron that is: no Telegram line, and a bare count on a stderr stream that is
usually discarded.

The exit code deliberately does NOT change: `docs/USAGE.md` documents exit 1 for a Google
reauth failure ONLY, cron alerting is built on that, and one transient message failure making
every run "fail" is how an alert gets muted. What changes is that the run stops being SILENT.
An earlier version of this line said a dropped-message run "must not exit 0", which
contradicted the test forty lines down asserting exactly that.

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
from sluice.track.engine import RunReport, TrackFailure


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
        # Returns the real contract's outcome. A fake returning None made EVERY test in
        # this file run the "unconfigured" branch while simultaneously "sending", so the
        # digest always claimed no notification was sent and nothing could assert its
        # absence -- a mutant printing that line unconditionally survived.
        monkeypatch.setattr(cli, "notify",
                            lambda body, config=None: (sent.append(body), "sent")[1])
        return cli.cmd_track_run(_Args(), Config()), sent
    return _drive


def test_failures_is_a_list_of_TrackFailure_so_nothing_can_disagree():
    """`failures=N` is `len()` of the same list that carries the detail, and the safe and full
    renderings come off the same object.

    Separate fields would drift: the digest would say 3 and name 1, or the notification would
    scrub a string the digest had already printed in full.
    """
    rep = RunReport()
    assert isinstance(rep.failures, list)
    f = TrackFailure("m1", "RuntimeError: boom", kind="RuntimeError")
    assert f.safe() == "m1 (RuntimeError)"
    assert f.detail() == "m1: RuntimeError: boom"
    # The DEFAULT rendering is the safe one. It used to be the full cause, so every natural
    # `str(f)` / f-string / `_log.info("%s", f)` leaked message content by default -- while
    # the type's own docstring explained at length why that content must not leave the box.
    assert str(f) == f.safe(), "the default rendering must be the safe one"


def test_safe_does_not_parse_the_kind_back_out_of_the_cause():
    """`safe()` used to do `cause.split(":", 1)[0]`, a format contract with nothing guarding
    it. A second producer written the obvious way -- `TrackFailure(mid, str(exc))` -- would
    then emit free text up to the first colon straight to Telegram."""
    f = TrackFailure("m1", "no lead matched Example Co - Staff Engineer: giving up")
    assert f.safe() == "m1 (error)", (
        f"an unset kind must degrade to a placeholder, never to message text: {f.safe()}")
    assert "Example Co" not in f.safe() and "Staff Engineer" not in f.safe()


def test_each_failed_message_is_NAMED_not_just_counted(_run, capsys):
    rep = RunReport(msgs=2, classified=1,
                    failures=[TrackFailure("m1", "RuntimeError: gmail hiccup"),
                              TrackFailure("m2", "ValueError: malformed attachment")])
    code, _sent = _run(rep)
    err = capsys.readouterr().err
    assert "failures=2" in err, "the count must survive the change"
    assert "m1: RuntimeError: gmail hiccup" in err, err
    assert "m2: ValueError: malformed attachment" in err, err


def test_a_run_with_failures_NOTIFIES(_run):
    """Track was the only sub-app that never notified.

    Under cron the stderr digest is usually discarded, so a dropped interview invite reached
    nobody. ingest, triage and cv all notify; this matches them.
    """
    rep = RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")])
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
    code, _sent = _run(RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")]))
    assert code == 0


def test_an_auth_error_still_exits_1(_run):
    code, _sent = _run(RunReport(auth_error=True))
    assert code == 1


def test_the_engine_POPULATES_TrackFailure_correctly():
    """The producer end, which nothing tested.

    Every other test in this file and in test_track_notify_redaction.py constructs its own
    `TrackFailure`, so the dataflow from `engine.run` was unwitnessed -- and it is exactly the
    dataflow whose type change broke the suite for 15 minutes when this field became a list.
    """
    from sluice.track import engine as E
    from sluice.track.config import TrackConfig
    from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault

    class _Boom(OneMsgClient):
        def get_message(self, mid):
            raise RuntimeError("gmail hiccup")

    v, _ = _vault("applied")
    rep = E.run(v, TrackConfig(), _Boom(), FakeBackend("{}"), seen=set(), deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert len(rep.failures) == 1
    f = rep.failures[0]
    assert f.message_id == "m1", "the id must come from the message being processed"
    assert "RuntimeError" in f.cause and "gmail hiccup" in f.cause
    assert f.safe() == "m1 (RuntimeError)", "the outward rendering must carry no cause text"


def test_the_full_cause_does_not_escape_through_repr_or_a_container():
    """`__str__` alone was not enough -- and the commit that made it safe said it was.

    `@dataclass` generates a `__repr__` printing EVERY field, `cause` included. So
    `repr(f)`, `"%s" % [f]` and `"%s" % rep` all rendered the full exception text, and
    `RunReport.failures` is a list -- `_log.warning("%s", rep.failures)` is the most natural
    debug line anyone would write for it.
    """
    from sluice.track.engine import RunReport, TrackFailure

    secret = "HttpError: 400 requesting https://mail.example.invalid/?q=from:x@example.invalid"
    f = TrackFailure("m1", secret, kind="HttpError")
    assert repr(f) == f.safe(), "repr must not be the generated field dump"
    for rendering in (repr(f), "%s" % [f], "%s" % {"f": f}, "%s" % RunReport(failures=[f])):
        assert "mail.example.invalid" not in rendering, rendering
        assert "example.invalid" not in rendering, rendering
    # ...and the detail is still reachable when asked for by name.
    assert secret in f.detail()
