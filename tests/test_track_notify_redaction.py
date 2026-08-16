"""What leaves the machine must not carry the contents of your mail.

#140 added a Telegram notification for a run that dropped messages -- and the natural body
("id: cause") sends an arbitrary exception string to a third party. That is a different
disclosure tier from the same string reaching stderr or a gitignored local `.db`:

- `classify` hands the message BODY to a backend, so a backend error can echo a prompt
  excerpt -- real email content.
- `reconcile` -> `sync_event` -> `insert_event` sends `_event_body`, which carries the
  interview `summary` and the meeting `url`.
- a `googleapiclient.HttpError` renders the request URI, and `_gmail_query` builds `q=` from
  `cfg.gmail_extra_query` -- the operator's own job-hunt domains and addresses.

`sluice/track/config.py` already states the principle for its own errors: "an exception
message travels further (logs, bug reports) than the file does". Telegram is further still.

So the notification says WHAT failed and WHERE to look; the full cause stays local, in the
stderr digest and the dead-letter row -- which `deadletter.py`'s docstring already accounts
for as private runtime state.
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

    def _drive(rep, outcome="sent"):
        class _Sluice:
            def __init__(self, config):
                pass

            def track(self, **kw):
                return rep

        monkeypatch.setattr("sluice.core.app.Sluice", _Sluice)
        # Returns the real contract's OUTCOME string, and the outcome is a parameter.
        #
        # Two bugs lived in the old shape. The fake returned None, so every test in this file
        # ran the "nothing configured" branch while simultaneously recording a send -- the
        # digest always claimed no notification was sent, and a mutant printing that line
        # unconditionally survived. And because this fixture patches `cli.notify` INSIDE
        # `_drive`, a test that patched it beforehand had its patch silently overwritten,
        # which is how the unconfigured test below ended up asserting the fixture's behaviour
        # rather than its own.
        monkeypatch.setattr(cli, "notify",
                            lambda body, config=None: (sent.append(body), outcome)[1])
        return cli.cmd_track_run(_Args(), Config()), sent
    return _drive


_LEAKY = TrackFailure(
    message_id="m1",
    cause=("HttpError: 400 when requesting "
           "https://gmail.googleapis.com/v1/users/me/messages?q=after:2026/07/01+"
           "from:recruiter@example-employer.invalid — 'Interview: Staff Engineer at Example Co'"))


def test_the_notification_does_not_carry_the_exception_text(_run):
    _code, sent = _run(RunReport(msgs=1, failures=[_LEAKY]))
    assert sent, "a run that dropped a message must still notify"
    body = sent[0]
    for leaked in ("q=after:", "recruiter@example-employer.invalid",
                   "Interview: Staff Engineer", "gmail.googleapis.com"):
        assert leaked not in body, f"notification leaked {leaked!r}: {body}"


def test_the_notification_still_says_what_failed_and_where_to_look(_run):
    """Redaction must not turn the alert into noise.

    "something failed" with no id is unactionable, and an operator who cannot act on an alert
    learns to ignore it -- which is the failure #140 set out to fix.
    """
    _code, sent = _run(RunReport(msgs=1, failures=[_LEAKY]))
    body = sent[0]
    assert "m1" in body, f"the message id is what makes it actionable: {body}"
    assert "1" in body, "the count must survive"


def test_the_LOCAL_digest_keeps_the_full_cause(_run, capsys):
    """Local stderr is a different tier from a third-party service.

    Scrubbing the local diagnostic too would leave nowhere to actually debug from.
    """
    _run(RunReport(msgs=1, failures=[_LEAKY]))
    err = capsys.readouterr().err
    assert "HttpError" in err and "400 when requesting" in err, "the operator's own terminal keeps the detail"


def test_a_clean_run_still_notifies_nothing(_run):
    _code, sent = _run(RunReport(msgs=3, classified=3))
    assert sent == []


_MANY = [TrackFailure(f"m{i}", "RuntimeError: boom") for i in range(500)]


def test_the_notify_body_is_bounded(_run):
    """#137 raised the message cap 50 -> 500, so a run can now produce 500 failures.

    At ~35 chars each that is an ~18KB body, which Telegram rejects -- and
    `_telegram_sender` swallows every send error by design ("notify must never take down a
    scan"). So on a MASS failure -- backend down, token expired mid-run, a Gmail schema change
    -- the alert silently does not arrive, which is exactly when it matters most.
    """
    _code, sent = _run(RunReport(msgs=500, failures=_MANY))
    assert sent
    body = sent[0]
    assert len(body) < 1500, f"unbounded notify body ({len(body)} chars) will be dropped"
    assert "500" in body, "the total must survive truncation -- it is the headline"
    assert "more" in body, "must say that the list was cut, not silently show 10 of 500"


def test_a_notify_that_goes_NOWHERE_is_reported_locally(_run, capsys):
    """`notify()` reports "unconfigured", and every caller used to ignore it.

    So on an install with no Telegram token, the fix for "the stderr digest is discarded" was
    a second silent channel. USAGE.md asserted flatly that a failing run "Telegram-notifies",
    which is false there.
    """
    _run(RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")]),
         outcome="unconfigured")
    err = capsys.readouterr().err
    assert "not configured" in err or "no notification" in err.lower(), (
        f"a notification that went nowhere must say so locally: {err}")


def test_a_notify_the_TRANSPORT_rejected_is_reported_locally(_run, capsys):
    """The third outcome, which had no representation at all.

    `_telegram_sender` swallows transport errors, so a revoked token or a dead network read
    as delivered and the digest said nothing. An operator would reasonably conclude the alert
    channel worked when it never had.
    """
    _run(RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")]),
         outcome="failed")
    err = capsys.readouterr().err
    assert "could not be delivered" in err.lower(), (
        f"an undelivered alert must say so locally: {err}")


def test_a_DELIVERED_notification_is_not_reported_as_undelivered(_run, capsys):
    """The inverse, which nothing asserted -- so a digest that ALWAYS cried "not sent" was
    indistinguishable from a correct one."""
    _run(RunReport(msgs=1, failures=[TrackFailure("m1", "RuntimeError: boom")]))
    err = capsys.readouterr().err.lower()
    assert "no notification" not in err and "could not be delivered" not in err, err


def test_a_held_watermark_is_reported(_run, capsys):
    """`deadletter_error` was consumed at exactly one place -- the `_save_lastrun` gate -- and
    printed nowhere.

    A read-only dead-letter store (disk full, permissions, a stale sidecar after a path
    migration) therefore silently declines to advance the watermark on every run, widening
    the Gmail window without bound, while the digest looks entirely normal and the command
    exits 0.
    """
    _run(RunReport(msgs=1, deadletter_error=True))
    err = capsys.readouterr().err
    assert "watermark" in err.lower(), f"a held watermark must be visible: {err}"
