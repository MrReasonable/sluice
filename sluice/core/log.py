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

    def send(text: str, channel: str | None = None) -> bool:
        """True if Telegram accepted it. Still never raises -- notify must not take down a
        scan -- but the caller can now tell "delivered" from "swallowed", which it could not
        before: every transport error looked exactly like success."""
        payload = json.dumps({"chat_id": channel or chat, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            return True
        except Exception as e:  # notify must never take down a scan
            get_logger("notify").warning("telegram send failed: %s", e)
            return False

    return send


def notify(text: str, channel: str | None = None, *, sender=None, config=None) -> str:
    """Send a notification; returns the OUTCOME.

    `"sent"` | `"unconfigured"` | `"failed"`. Three states, not two: the bool this used to
    return was True the moment a sender EXISTED, and `_telegram_sender.send` swallows every
    transport error, so a revoked token, a wrong chat_id, a 4xx, a DNS failure or Telegram
    being down all reported success. The one run that most needed the alert was the one where
    nobody could tell it had not arrived.

    A str rather than a bool because the two failure modes want different words: nothing is
    configured (install-time, expected, quiet) versus configured and broken (an operator has
    to look).

    CAUTION: all three values are TRUTHY. An earlier version of this docstring claimed the
    old `False` return's falsiness was "deliberately not preserved, so `if not notify(...)`
    fails loudly" -- which is exactly backwards: a surviving `if not notify(...)` now takes
    the success path in every state, including "failed". Compare against the STRING, never
    against truthiness. Every call site in `sluice/` was swept when this changed.

    `sender` is injectable for tests -- a fake must return a bool, like the real one."""
    sender = sender or _telegram_sender(config)
    if sender is None:
        return "unconfigured"
    return "sent" if sender(text, channel) else "failed"
