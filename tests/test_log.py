from sluice.core.config import Config
from sluice.core.log import get_logger, notify


def test_get_logger_is_namespaced_and_usable():
    log = get_logger("engine")
    assert log.name == "sluice.engine"
    log.info("no raise")  # must not blow up


def test_notify_calls_injected_sender():
    calls = []
    # An injected sender must return a bool, like the real one -- a fake returning None
    # now reads as "failed", which is the whole point of the three-state outcome.
    def _send(text, channel):
        calls.append((text, channel))
        return True
    assert notify("hello", sender=_send) == "sent"
    assert calls == [("hello", None)]


def test_notify_passes_channel_through():
    calls = []
    notify("hi", channel="chan1",
           sender=lambda text, channel: calls.append((text, channel)) or True)
    assert calls == [("hi", "chan1")]


def test_notify_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SLUICE_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("SLUICE_TELEGRAM_CHAT", raising=False)
    assert notify("hello") == "unconfigured"


def test_notify_uses_config_telegram(monkeypatch):
    monkeypatch.delenv("SLUICE_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("SLUICE_TELEGRAM_CHAT", raising=False)
    cfg = Config(notify={"telegram": {"token": "t0k", "chat_id": "42"}})
    sent = {}

    def fake_urlopen(req, timeout=0):
        sent["url"] = req.full_url
        sent["body"] = req.data

        class _R:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b"{}"

        return _R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert notify("ping", config=cfg) == "sent"
    assert "bott0k/sendMessage" in sent["url"]
    assert b'"chat_id": "42"' in sent["body"]


def test_notify_reports_FAILED_when_the_transport_rejects_it(monkeypatch):
    """The state that did not exist before, and the one that matters most.

    `_telegram_sender.send` swallows every transport error by design, so `notify` returned
    True for a revoked token, a wrong chat_id, a 4xx, a DNS failure or Telegram being down.
    The caller could not distinguish that from delivery, on exactly the run that needed the
    alert to arrive. Nothing raises -- notify must never take down a scan -- but the outcome
    is now reportable.
    """
    cfg = Config(notify={"telegram": {"token": "t0k", "chat_id": "42"}})

    def boom(req, timeout=0):
        raise OSError("network unreachable")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert notify("ping", config=cfg) == "failed"


def test_a_delivered_notification_is_not_reported_as_undelivered(monkeypatch):
    # The inverse, which nothing asserted: a working channel must stay silent.
    cfg = Config(notify={"telegram": {"token": "t0k", "chat_id": "42"}})

    class _R:
        def __enter__(self_): return self_
        def __exit__(self_, *a): return False
        def read(self_): return b"{}"

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _R())
    assert notify("ping", config=cfg) == "sent"
