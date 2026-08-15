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
