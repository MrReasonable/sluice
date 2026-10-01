"""#333: a track message whose classification hit a backend OUTAGE is retried by a later
run, and the run itself fails loudly.

Before #333 `classify()` turned every exception into an `unknown` event (the #40 rule), so
an outage became one "classification failed -- review manually" proposal per message, each
message was marked seen and never classified again, and the run exited 0. The #40 rule still
holds for everything that is NOT a backend outage: a malformed reply is a property of that
message, and a human should look at it.
"""
import os

from sluice import cli
from sluice.cli import _build_parser, cmd_track_run
from sluice.core.app import Sluice
from sluice.core.backends import BackendError, Completion
from sluice.core.config import Config
from sluice.track import engine as E
from sluice.track.classify import classify
from sluice.track.config import TrackConfig
from tests.test_app_operations import _FakeGoogle, _track_config
from tests.test_track_engine import TwoMsgClient, _dl, _vault

_DOWN = "deepseek m: no complete response from https://api.example.invalid within 300s after 3 attempts"


class _Down:
    def __init__(self):
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        raise BackendError(_DOWN)


def _msg():
    return {"message_id": "m1", "thread_id": "t1",
            "headers": {"from": "jobs@example-tidal.invalid", "subject": "Update"},
            "body_text": "an update", "attachments": []}


def test_classify_lets_a_backend_outage_through():
    try:
        classify(_msg(), [], _Down(), TrackConfig())
    except BackendError as e:
        assert str(e) == _DOWN
    else:
        raise AssertionError("a backend outage was swallowed into an event")


def test_classify_still_reports_a_malformed_reply_as_unknown():
    # #40, unchanged: this is a property of the MESSAGE, so a human gets it.
    class _Garbage:
        def complete(self, prompt):
            return Completion("not json at all")

    assert classify(_msg(), [], _Garbage(), TrackConfig()).type == "unknown"


def test_the_run_stops_at_the_outage_and_leaves_every_message_for_the_next_run():
    v, _ = _vault("applied")
    seen, dl, backend = set(), _dl(), _Down()
    rep = E.run(v, TrackConfig(), TwoMsgClient(), backend, seen=seen, deadletter=dl,
                now_iso="2026-07-10T12:00:00+00:00")

    assert rep.backend_error == _DOWN
    assert backend.calls == 1, "the loop must stop, not spend a timeout on every message"
    assert seen == set(), "an unclassified message must stay unseen so a later run retries it"
    assert dl.open_entries() == [], "an outage is not a message for a human to review"
    assert rep.failures == [], "the outage is its own report, not a per-message failure"


def test_an_outage_holds_the_lastrun_watermark(tmp_path, monkeypatch):
    """Advancing it would move Gmail's `after:` past the messages this run never classified,
    and they would leave the query window for good -- the auth_error reasoning, one arm over."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    seen_db = _track_config(tmp_path, monkeypatch)
    monkeypatch.setattr(E, "run", lambda *a, **k: E.RunReport(backend_error=_DOWN))
    Sluice(Config(), backend=object()).track(client=_FakeGoogle(),
                                             now_iso="2026-07-15T00:00:00+00:00")
    assert not os.path.exists(seen_db + ".lastrun")


def test_a_healthy_run_still_advances_the_watermark(tmp_path, monkeypatch):
    # The control: without it, the row above passes for a run that never writes the file.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    seen_db = _track_config(tmp_path, monkeypatch)
    monkeypatch.setattr(E, "run", lambda *a, **k: E.RunReport())
    Sluice(Config(), backend=object()).track(client=_FakeGoogle(),
                                             now_iso="2026-07-15T00:00:00+00:00")
    assert os.path.exists(seen_db + ".lastrun")


def test_the_cli_exits_non_zero_and_notifies(monkeypatch, tmp_path, capsys):
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "track",
                        lambda self, **kw: E.RunReport(msgs=2, backend_error=_DOWN))
    code = cmd_track_run(_build_parser().parse_args(["track", "run"]), Config())
    assert code == 1
    assert "backend unavailable" in capsys.readouterr().err
    assert any("backend unavailable" in m.lower() for m in sent)


def test_a_non_transient_failure_is_the_messages_own_and_never_wedges_the_run():
    """A truncation or a 400 is a property of ONE message's text, so it must take the #40
    `unknown` path for a human -- never the outage arm, which would leave the message unseen,
    hold the watermark, and fail every later run on the same message for good (#139's
    warning, in engine.run's own comment)."""
    class _TooLong:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt):
            self.calls += 1
            raise BackendError("response incomplete (finish_reason=length)", transient=False)

    assert classify(_msg(), [], _TooLong(), TrackConfig()).type == "unknown"

    v, _ = _vault("applied")
    seen, backend = set(), _TooLong()
    rep = E.run(v, TrackConfig(), TwoMsgClient(), backend, seen=seen, deadletter=_dl(),
                now_iso="2026-07-10T12:00:00+00:00")
    assert rep.backend_error == ""
    assert backend.calls == 2, "the second message must still be classified"
    assert seen == {"mA", "mB"}
