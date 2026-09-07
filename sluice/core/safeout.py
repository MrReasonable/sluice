r"""Escape characters that would drive the operator's TERMINAL rather than print to it (#280).

`sluice` prints scraped board text, LLM output about a composed CV, and Gmail message fields
verbatim. A terminal is not a display surface: a `\x1b` byte in that text is an instruction --
it can recolour, reposition the cursor, clear the screen, set the window title, and on a
permissive terminal write the clipboard via OSC 52.

The character class is `onboard/emit.py::_needs_hex`'s, which was MEASURED against a real
PyYAML parser rather than reasoned about; this module is now its one home and `emit.py` imports
from here. The terminal set is that set MINUS `\n` and `\t`:

- `\t` is not a terminal-control character. It advances to the next tab stop and can do nothing
  else -- it cannot recolour, reposition arbitrarily, hide output or reach the clipboard. It is
  also load-bearing: `cv/audit.py` and `cv/voice.py` emit tab-separated records and `cmd_cv_run`
  prints them with the tabs intact as columns. A blanket control strip would destroy that.
- `
` is the one character a STREAM cannot judge. `cli.py` legitimately prints "\nNext:", and an
  injected newline inside a scraped title is byte-identical. Escaping it here would turn every
  deliberate blank line into a literal `
`. The residual is stated in the design doc: an
  injected newline forges an output LINE, which is bounded -- it cannot hide prior output,
  recolour or reposition, all of which need CR or ESC and all of which this module closes.

Backslash is deliberately NOT escaped. YAML must round-trip so `emit.py` escapes it; terminal
output does not, and escaping it would mangle every path sluice prints. That choice is also what
makes `escape_for_terminal` IDEMPOTENT, which matters because a log record passes through both
the Formatter and the wrapped stream.
"""
# Written as escapes, never as literals: U+2028/U+2029 are invisible in an editor, and a literal
# one actually SPLITS the source line -- Python treats it as a line break.
_TERMINAL_KEEP = ("\n", "\t")


def is_control(ch: str) -> bool:
    r"""Is `ch` a control character a reader is entitled to reject?

    C0 (< 0x20), DEL, the WHOLE C1 block (0x80-0x9f -- not just NEL at 0x85), lone surrogates
    (no valid encoding), and the two Unicode line separators. Moved here from
    `onboard/emit.py::_needs_hex`, whose docstring carries the measurement that produced it.
    """
    o = ord(ch)
    return (o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F or 0xD800 <= o <= 0xDFFF
            or ch in ("\u2028", "\u2029"))


def hex_escape(ch: str) -> str:
    r"""The narrowest escape form that can hold `ch`.

    `\xNN` takes exactly two hex digits, so it cannot express U+2028: `\x2028` reads back as
    `\x20` followed by a literal "28" -- a silent corruption, and the same class of bug as the
    raw character it was meant to fix. Measured, which is how the two-digit assumption was caught.
    """
    o = ord(ch)
    if o <= 0xFF:
        return f"\\x{o:02x}"
    return f"\\u{o:04x}" if o <= 0xFFFF else f"\\U{o:08x}"


def escape_for_terminal(text: str) -> str:
    r"""`text` with every threat-set character replaced by its `\xNN`/`\uNNNN` escape.

    ESCAPE rather than strip: stripping silently modifies the evidence an operator is looking
    at, and a scraped payload that contained an escape sequence is a fact worth seeing.
    """
    return "".join(
        hex_escape(ch) if is_control(ch) and ch not in _TERMINAL_KEEP else ch
        for ch in text
    )
