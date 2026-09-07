"""The terminal-output escaping policy (#280).

Rows here pin the FOUR properties the design calls load-bearing: the character set,
idempotence, statelessness, and the two characters deliberately left alone. Each is
falsifiable by a MOVE/DELETE mutant on `_TERMINAL_KEEP` or on `is_control`'s ranges.
"""
import pytest

from sluice.core.safeout import escape_for_terminal, hex_escape, is_control


@pytest.mark.parametrize("ch", [
    "\x00", "\x07", "\x08", "\x0b", "\x0c", "\r", "\x1b", "\x1f",   # C0 minus \n \t
    "\x7f",                                                          # DEL
    "\x80", "\x9b", "\x9f",                                          # C1, incl. CSI
    " ", " ",                                              # line separators
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
    assert hex_escape(" ") == "\\u2028"


def test_is_control_rejects_ordinary_text():
    assert not any(is_control(c) for c in "Senior Engineer (remote) - 45k+ / cafe")
