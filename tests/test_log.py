import os

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


def test_the_suite_does_not_read_the_developers_SLUICE_LOG_LEVEL(monkeypatch):
    """#144. `get_logger` sets a logger's level exactly once, guarded on
    `if not logger.handlers`, so an exported `SLUICE_LOG_LEVEL` is baked into every
    `sluice.*` logger at import -- before any test runs. Combined with `propagate = False`,
    a bare `caplog.at_level("WARNING")` then captures nothing, and 34 tests across ten files
    went red on a developer machine that happened to export it.

    conftest's autouse fixture normalises BOTH halves. This asserts the second one, which is
    the half a `delenv` alone would miss.
    """
    import logging

    sluice_loggers = [obj for name, obj in logging.Logger.manager.loggerDict.items()
                      if name.startswith("sluice.") and isinstance(obj, logging.Logger)]
    assert sluice_loggers, "no sluice logger exists yet -- this assertion would be vacuous"
    # `!= INFO`, not `> INFO`. The harness normalises to exactly INFO, so anything else is an
    # inherited level -- and the one-sided form passed under `SLUICE_LOG_LEVEL=DEBUG`, where
    # every logger keeps the ambient value while the assertion reads clean. A test that only
    # catches the noisy half of a symmetric property is the half nobody notices is missing.
    wrong = {lg.name: logging.getLevelName(lg.level) for lg in sluice_loggers
             if lg.level != logging.INFO}
    assert not wrong, (
        f"these loggers kept a level from the ambient environment: {wrong}. "
        "A test asserting on their records would capture the wrong set -- silently nothing "
        "when the level is raised, and unrelated DEBUG noise when it is lowered.")
    # ...and the RESET must have gone through `setLevel`, which clears `Logger._cache`.
    # Assigning `.level` directly leaves `isEnabledFor` answering from the stale cache, so a
    # logger can read INFO and still drop a WARNING depending on what ran before it.
    stale = [lg.name for lg in sluice_loggers if not lg.isEnabledFor(logging.WARNING)]
    assert not stale, (
        f"these loggers report INFO but still suppress WARNING: {stale}. "
        "The level was assigned rather than set, so Logger._cache is stale.")
    assert "SLUICE_LOG_LEVEL" not in os.environ, (
        "the env var itself must be scrubbed, or a logger created DURING a test inherits it")


def test_a_namespaced_logger_still_honours_the_env_var_outside_the_suite(monkeypatch):
    """The scrub is a TEST-harness decision, not a behaviour change. `get_logger` must still
    read the variable, or operators lose the only lever they have over log volume."""
    import logging

    monkeypatch.setenv("SLUICE_LOG_LEVEL", "ERROR")
    name = "sluice.test_env_level_probe"
    logging.Logger.manager.loggerDict.pop(name, None)   # force a first-call creation
    lg = get_logger("test_env_level_probe")
    try:
        assert lg.level == logging.ERROR
    finally:
        logging.Logger.manager.loggerDict.pop(name, None)


def test_setLevel_is_required_because_direct_assignment_leaves_the_cache_stale():
    """Why conftest resets levels with `setLevel()` and not `logger.level = ...`.

    `Logger.isEnabledFor` answers from `Logger._cache`, which `setLevel` clears and a plain
    attribute assignment does not. So a logger whose cache was warmed while the ambient level
    was CRITICAL keeps dropping WARNINGs after its level reads INFO -- and whether that bites
    depends on whether anything logged before the reset, i.e. on TEST ORDER.

    Asserted on a throwaway logger with the cache deliberately WARMED, because the suite's own
    loggers have a cold cache by the time any test runs: reverting conftest to direct
    assignment leaves every other test green, which is exactly how this would have shipped.
    """
    import logging

    lg = logging.getLogger("sluice.test_cache_probe")
    try:
        lg.setLevel(logging.CRITICAL)
        assert lg.isEnabledFor(logging.WARNING) is False   # warms the cache

        lg.level = logging.INFO                            # what conftest must NOT do
        assert lg.isEnabledFor(logging.WARNING) is False, (
            "if this ever passes, CPython changed and the setLevel requirement can be revisited")

        lg.setLevel(logging.INFO)                          # what conftest does
        assert lg.isEnabledFor(logging.WARNING) is True
    finally:
        logging.Logger.manager.loggerDict.pop("sluice.test_cache_probe", None)


def test_the_harness_normalises_to_exactly_INFO_not_merely_quietly():
    """The two-sided half of the property.

    The first version of the check above used `lg.level > logging.INFO`, which passes under
    `SLUICE_LOG_LEVEL=DEBUG` while every logger keeps the ambient value -- a test that catches
    only the noisy direction of a symmetric property. Pinned against the constant the harness
    actually promises, so lowering the normalisation target fails here rather than silently
    changing what the whole suite captures.
    """
    import logging


    # A logger that EXISTED BEFORE the fixture ran. Creating a fresh one via `get_logger`
    # asserted `get_logger`'s own hardcoded default instead, so lowering conftest's
    # normalisation target left this green -- an equivalent mutant, and a docstring claiming
    # a witness it did not have. `sluice.track.ics` is created at import.
    import sluice.track.ics  # noqa: F401  -- imported for its module-level get_logger call

    lg = logging.getLogger("sluice.track.ics")
    assert lg.handlers, "expected a logger the fixture would have seen, not a fresh one"
    assert lg.level == logging.INFO, logging.getLevelName(lg.level)
