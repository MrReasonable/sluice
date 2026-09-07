r"""Escape characters that would drive the operator's TERMINAL rather than print to it (#280).

`sluice` prints scraped board text, LLM output about a composed CV, and Gmail message fields
verbatim. A terminal is not a display surface: a `\x1b` byte in that text is an instruction --
it can recolour, reposition the cursor, clear the screen, set the window title, and on a
permissive terminal write the clipboard via OSC 52.

The character class was MEASURED against a real PyYAML parser rather than reasoned about; see
`is_control` for the measurements. This module is now its one home and `emit.py` imports from
here. The terminal set is that class MINUS `\n` and `\t`:

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
import sys
import traceback
from contextlib import contextmanager

# Written as escapes, never as literals: U+2028/U+2029 are invisible in an editor, and a literal
# one actually SPLITS the source line -- Python treats it as a line break.
_TERMINAL_KEEP = ("\n", "\t")


def is_control(ch: str) -> bool:
    r"""Is `ch` a control character a reader is entitled to reject?

    C0 (< 0x20), DEL, the WHOLE C1 block (0x80-0x9f -- not just NEL at 0x85), lone surrogates
    (no valid encoding), and the two Unicode line separators.

    This character class was MEASURED against a real PyYAML parser rather than reasoned about.
    Without the `r` prefix on this docstring, every `\x..` would be interpreted, holding six
    real control characters. Measured, which is the only reason it was noticed.

    The five standard YAML escapes (`\\`, `"`, `\n`, `\r`, `\t`) cover only themselves.
    Measured against PyYAML, an unescaped `\x1b`, `\x07`, `\x0b` or `\x00` makes a config file
    unreadable to every later sluice command (`ReaderError`), and `\x85` silently round-trips to
    a space -- a value corruption with nothing raising.

    The reachable path is ordinary rather than adversarial: `cv_employers` is prompted as free
    text and names are pasted out of a CV or PDF, where `\x0b` and `\x0c` are routine extraction
    artefacts.

    C1 was previously represented by `\x85` alone, which is the only one PyYAML treats as a line
    break -- but the rest are still control characters a reader is entitled to reject, and
    escaping them costs nothing. Lone surrogates (0xD800-0xDFFF) have no valid YAML representation,
    so an unescaped one writes a config every later sluice command rejects with ReaderError.
    Reachable from any paste of mis-decoded text.
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


class _Escaped:
    """A text stream that escapes the threat set on the way out.

    Delegation is by `__getattr__` and must stay GENUINE rather than a partial `TextIOBase`
    subclass: `mcp`'s stdio transport reaches through for `.buffer.fileno()`, and a wrapper
    without `.buffer` kills `job-sluice mcp serve` at startup (see `test_the_wrapper_delegates_
    buffer_and_fileno`). Nothing in `sluice/` itself reaches through -- measured with a
    must-be-present control -- so the requirement comes entirely from that third party.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, text: str) -> int:
        self._stream.write(escape_for_terminal(text))
        # The PRE-escape length: callers that check a return value are asking how much of
        # THEIR string was accepted, not how many bytes the escaping happened to produce.
        return len(text)

    def writelines(self, lines) -> None:
        # Overridden rather than delegated: `__getattr__` would hand this to the inner stream
        # and every line would bypass the escaping.
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextmanager
def installed():
    r"""Escape everything printed to stdout/stderr for the duration, restoring on every exit.

    Covers every `print` site with no call-site change, so a print added later is protected by
    construction rather than by remembering to call a helper.

    The traceback is escaped HERE, by catching, rather than by a `sys.excepthook`. A hook
    installed and restored alongside the wrapper is inert: the `finally` restores it during
    unwinding, BEFORE the interpreter calls it, and the traceback then reaches the terminal raw.
    Measured -- a `RuntimeError` carrying a scraped title delivered a live screen-clear that way.
    An uncaught exception therefore becomes `SystemExit(1)`, which prints no second traceback.

    `SystemExit` and `KeyboardInterrupt` are re-raised untouched: neither carries derived text,
    and both have exit behaviour a caller depends on (argparse's `--help` is a `SystemExit(0)`).
    """
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Escaped(saved_out), _Escaped(saved_err)
    try:
        yield
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
