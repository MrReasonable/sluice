"""The terminal-output escaping policy (#280).

Rows here pin the FOUR properties the design calls load-bearing: the character set,
idempotence, statelessness, and the two characters deliberately left alone. Each is
falsifiable by a MOVE/DELETE mutant on `_TERMINAL_KEEP` or on `is_control`'s ranges.
"""
import io
import sys

import pytest

from sluice.core import safeout
from sluice.core.safeout import escape_for_terminal, hex_escape, installed, is_control


@pytest.mark.parametrize("ch", [
    "\x00", "\x07", "\x08", "\x0b", "\x0c", "\r", "\x1b", "\x1f",   # C0 minus \n \t
    "\x7f",                                                          # DEL
    "\x80", "\x9b", "\x9f",                                          # C1, incl. CSI
    "\u2028", "\u2029",                                              # line separators
    "\ud800", "\udfff",                                              # lone surrogates
])
def test_every_threat_character_is_escaped(ch):
    out = escape_for_terminal(f"a{ch}b")
    assert ch not in out, f"{ch!r} survived escaping"
    assert out.startswith("a") and out.endswith("b")


@pytest.mark.parametrize("ch", ["\n", "\t"])
def test_newline_and_tab_pass_through_untouched(ch):
    """The stated residual (\\n) and the audit_flags/voice_flags field contract (\\t).

    Asserted so a later 'tightening' of the character set is a deliberate decision that
    reddens here, rather than an accident that silently breaks tab-separated columns.
    """
    assert escape_for_terminal(f"a{ch}b") == f"a{ch}b"


def test_escaping_is_idempotent():
    """A log record passes through BOTH the Formatter and the wrapped stream, so a second
    pass must change nothing. Holds because the escaped form is backslash, x/u and hex
    digits -- none in the threat set -- and because backslash is deliberately NOT escaped."""
    once = escape_for_terminal("title: Engineer\x1b[2J\x9b end")
    assert escape_for_terminal(once) == once


def test_escaping_is_stateless_per_chunk():
    """`print` calls `write()` twice -- once for the text, once for the newline -- so a
    line-oriented implementation would be broken by construction."""
    assert escape_for_terminal("a\x1b") + escape_for_terminal("\n") == \
        escape_for_terminal("a\x1b\n")


def test_hex_escape_uses_four_digits_above_one_byte():
    """`\\xNN` takes exactly two hex digits, so it cannot express U+2028: `\\x2028` reads
    back as `\\x20` followed by a literal '28'."""
    assert hex_escape("\x1b") == "\\x1b"
    assert hex_escape("\u2028") == "\\u2028"


def test_is_control_rejects_ordinary_text():
    assert not any(is_control(c) for c in "Senior Engineer (remote) - 45k+ / cafe")


def test_print_is_escaped_while_installed(capsys):
    with installed():
        print("title: Engineer\x1b[2J")
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\\x1b[2J" in out


def test_streams_are_restored_on_normal_exit():
    before = (sys.stdout, sys.stderr)
    with installed():
        assert sys.stdout is not before[0]
    assert (sys.stdout, sys.stderr) == before


def test_streams_are_restored_when_the_body_raises():
    before = (sys.stdout, sys.stderr)
    with pytest.raises(SystemExit):
        with installed():
            raise RuntimeError("boom")
    assert (sys.stdout, sys.stderr) == before


def test_an_uncaught_exception_prints_an_escaped_traceback_then_exits_one(capsys):
    """The hole round 1's own try/finally opened: `finally` runs during unwinding, so the
    streams are restored BEFORE the interpreter prints an uncaught traceback -- which then
    carries whatever scraped value is interpolated into the exception message straight to the
    terminal. Measured before the fix: a live ESC[2J screen clear."""
    with pytest.raises(SystemExit) as ei:
        with installed():
            raise RuntimeError("scraped title: Engineer\x1b[2J\x9b[31m")
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x9b" not in err
    assert "\\x1b[2J" in err and "\\x9b[31m" in err


def test_system_exit_passes_through_unchanged():
    """argparse raises SystemExit on --help and on a bad command. It carries no derived text
    and has established exit-code behaviour, so it must not be rewritten to SystemExit(1)."""
    with pytest.raises(SystemExit) as ei:
        with installed():
            raise SystemExit(2)
    assert ei.value.code == 2


def test_keyboard_interrupt_propagates_as_itself():
    """The row that falsifies the `except BaseException` comment. Ctrl-C carries no derived
    text and its exit behaviour is the shell's business, so it is re-raised rather than
    converted -- and swapping the guard to catch it would redden here."""
    with pytest.raises(KeyboardInterrupt):
        with installed():
            raise KeyboardInterrupt


def test_nesting_restores_the_outer_wrapper_not_the_original():
    original = sys.stdout
    with installed():
        outer = sys.stdout
        with installed():
            assert sys.stdout is not outer
        assert sys.stdout is outer
    assert sys.stdout is original


def test_the_wrapper_delegates_buffer_and_fileno():
    """`mcp`'s stdio transport calls `stream.buffer.fileno()`. With genuine delegation it dups
    fd 1 and serves the wire from a private binary duplicate, bypassing this wrapper entirely.
    WITHOUT `.buffer`, `_claim_fd`'s fallback `return stream.buffer, None` raises unguarded and
    `job-sluice mcp serve` dies at startup while the in-memory contract test stays green."""
    wrapped = safeout._Escaped(sys.__stdout__)
    assert wrapped.buffer is sys.__stdout__.buffer
    assert wrapped.fileno() == sys.__stdout__.fileno()
    assert wrapped.encoding == sys.__stdout__.encoding


def test_writelines_is_escaped_too():
    """`__getattr__` would delegate `writelines` straight to the inner stream, bypassing the
    escaping. It is overridden for that reason."""
    sink = io.StringIO()
    safeout._Escaped(sink).writelines(["a\x1b", "b\n"])
    assert sink.getvalue() == "a\\x1bb\n"
