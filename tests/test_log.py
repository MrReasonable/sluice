from sluice.core.config import Config
from sluice.core.log import get_logger, notify


def test_get_logger_is_namespaced_and_usable():
    log = get_logger("engine")
    assert log.name == "sluice.engine"
    log.info("no raise")  # must not blow up


def test_notify_calls_injected_sender():
    calls = []
    assert notify("hello", sender=lambda text, channel: calls.append((text, channel)))
    assert calls == [("hello", None)]


def test_notify_passes_channel_through():
    calls = []
    notify("hi", channel="chan1", sender=lambda text, channel: calls.append((text, channel)))
    assert calls == [("hi", "chan1")]


def test_notify_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SLUICE_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("SLUICE_TELEGRAM_CHAT", raising=False)
    assert notify("hello") is False


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
    assert notify("ping", config=cfg) is True
    assert "bott0k/sendMessage" in sent["url"]
    assert b'"chat_id": "42"' in sent["body"]
