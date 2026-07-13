"""Logging + notification.

get_logger gives every module a namespaced stderr logger. notify pushes a line
to Telegram when a token+chat are configured (env first, then Config.notify) and
is a silent no-op otherwise, so unconfigured / offline runs never fail on notify.
The sender is injectable so tests assert without hitting the network.
"""
import json
import logging
import os
import sys
import urllib.request

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"sluice.{name}")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("SLUICE_LOG_LEVEL", "INFO").upper())
        logger.propagate = False  # don't double-log through the root logger
    return logger


def _resolve_telegram(config) -> tuple[str, str] | None:
    """(token, chat_id) from env first, then Config.notify['telegram']; None if
    neither supplies both."""
    token = os.environ.get("SLUICE_TELEGRAM_TOKEN")
    chat = os.environ.get("SLUICE_TELEGRAM_CHAT")
    if config is not None and not (token and chat):
        tele = (getattr(config, "notify", {}) or {}).get("telegram") or {}
        token = token or tele.get("token")
        chat = chat or tele.get("chat_id")
    return (token, chat) if token and chat else None


def _telegram_sender(config):
    creds = _resolve_telegram(config)
    if creds is None:
        return None
    token, chat = creds

    def send(text: str, channel: str | None = None) -> None:
        payload = json.dumps({"chat_id": channel or chat, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except Exception as e:  # notify must never take down a scan
            get_logger("notify").warning("telegram send failed: %s", e)

    return send


def notify(text: str, channel: str | None = None, *, sender=None, config=None) -> bool:
    """Send a notification. Returns True if a sender handled it, False if there
    was nothing configured (silent no-op). `sender` is injectable for tests."""
    sender = sender or _telegram_sender(config)
    if sender is None:
        return False
    sender(text, channel)
    return True
