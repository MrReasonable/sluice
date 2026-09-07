"""Log records are escaped by their Formatter, not by the stream wrapper (#280).

`sluice/core/vault.py`'s rename failure interpolates a lead slug with %s, and a slug can hold a
control character when a human placed the note by hand. Every `*log.<level>` call site in
`sluice/` is on this channel.
"""
import io
import logging

from sluice.core.log import get_logger


def _logger_writing_to(sink, name):
    """A logger writing to `sink`, keeping the formatter `get_logger` attached.

    Reusing the SHIPPED formatter is the point: building one here would pass even if
    `get_logger` attached nothing. Read it BEFORE swapping the handler -- the formatter lives
    on the handler, so stripping first leaves nothing to read.

    `propagate` is flipped to True on the way out -- measured, not decorative. A stdlib
    `Logger` is never removed from `logging.Logger.manager.loggerDict` once created, so a
    probe built by an earlier test in this file is still registered, still non-propagating,
    when a LATER test runs. pytest's own log-capture machinery
    (`_pytest.logging.catching_logs.__enter__`) attaches a `LogCaptureHandler` to every
    NON-PROPAGATING logger it finds still in that dict at the start of each test's
    setup/call/teardown -- so without this, the derived-roster guard below (which sweeps
    every `sluice.*` logger with a handler) finds this probe carrying TWO handlers, one of
    them pytest's own, and fails on a logger this file itself created rather than on
    anything `get_logger` shipped. Flipping `propagate` removes it from that sweep's
    target set without touching the handler or formatter under test.
    """
    log = get_logger(name)
    shipped = _shipped_formatter(log)
    log.handlers = [h for h in log.handlers if not isinstance(h, logging.StreamHandler)]
    handler = logging.StreamHandler(sink)
    handler.setFormatter(shipped)
    log.addHandler(handler)
    log.propagate = True
    return log


def _shipped_formatter(log):
    for h in log.handlers:
        if h.formatter is not None:
            return h.formatter
    raise AssertionError(f"get_logger attached no formatter to {log.name}")


def test_a_log_record_is_escaped_by_its_formatter():
    sink = io.StringIO()
    log = _logger_writing_to(sink, "test_escape_probe")
    log.warning("could not rename %s -> %s", "lead\x1b[2J", "other\x9b[31m")
    out = sink.getvalue()
    assert "\x1b" not in out and "\x9b" not in out
    assert "\\x1b[2J" in out and "\\x9b[31m" in out


def test_tab_and_newline_survive_a_log_record():
    sink = io.StringIO()
    log = _logger_writing_to(sink, "test_keep_probe")
    # Both characters, because both are deliberate exemptions and the test is named for
    # both: the tab carries the audit_flags/voice_flags field contract, the newline is the
    # documented residual. Asserting only the tab let the newline half drift unguarded.
    log.warning("a\tb\nc")
    assert "a\tb\nc" in sink.getvalue()


def test_every_sluice_logger_with_a_handler_formats_through_the_escaper():
    """Guard 5. The roster is DERIVED -- a hand-written count was wrong once already -- but a
    derived roster needs a FLOOR, or a mis-keyed filter enumerates nothing and passes: measured,
    the `sluice.*` roster is EMPTY at test start and only populates once `sluice.cli` is
    imported. So assert the floor first, then the property.
    """
    import sluice.cli  # noqa: F401  -- what populates the roster

    from sluice.core.log import _EscapingFormatter

    named = {n: o for n, o in logging.root.manager.loggerDict.items()
             if n.startswith("sluice.") and isinstance(o, logging.Logger)}
    with_handlers = {n: o for n, o in named.items() if o.handlers}

    # The floor, hand-written: this set is not allowed to be empty, and this member is not
    # allowed to be missing. Without these two lines a broken filter is indistinguishable from
    # a clean sweep, because `all([])` is True.
    assert with_handlers, "enumerated no sluice loggers -- the filter is broken, not the tree"
    assert "sluice.cli" in with_handlers

    checked = 0
    for name, log in with_handlers.items():
        for handler in log.handlers:
            # `type(...) is`, not `isinstance` -- across the FULL suite, hundreds of other
            # test files import `sluice.core.plugins` and its siblings before this test
            # runs, so those loggers are already registered and non-propagating by then.
            # pytest's own log-capture machinery (`_pytest.logging.catching_logs.__enter__`,
            # entered fresh for every later test's setup/call/teardown, and once for the
            # whole session by `pytest_runtestloop`) attaches ITS OWN handler to every such
            # logger it finds -- `LogCaptureHandler`, a `StreamHandler` SUBCLASS, and
            # `_LiveLoggingStreamHandler`/`_LiveLoggingNullHandler` alongside it. Measured:
            # running this file next to `tests/test_log.py` and
            # `tests/functional/test_cli_contract.py` left `sluice.core.plugins` carrying a
            # `ColoredLevelFormatter`-backed contaminant this way. `get_logger` never
            # installs anything but the literal base class, so restricting to exactly that
            # type scopes the guard to what SHIPS rather than to pytest's own harness noise
            # riding along on the same non-propagating logger.
            if type(handler) is not logging.StreamHandler:
                continue
            checked += 1
            assert isinstance(handler.formatter, _EscapingFormatter), (
                f"{name} formats through {type(handler.formatter).__name__}, "
                "so its records reach stderr unescaped")
    # Same fail-open shape as the floor above: filtering by exact type could itself be
    # broken (e.g. if `get_logger` ever switched to a subclass) and silently check nothing.
    assert checked, "found no get_logger-installed handler to check -- the type filter is broken"
