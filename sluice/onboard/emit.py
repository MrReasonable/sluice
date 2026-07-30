"""Emit YAML scalars by hand.

The config `sluice init` writes is a TEMPLATE WITH COMMENTS -- the guidance under each key is most
of its value -- so `yaml.safe_dump` cannot produce it and a round-tripping loader like ruamel is
barred by the standard-library-only rule.

Strings are ALWAYS double-quoted, never bare and never single-quoted. A bare scalar changes meaning
with its content (`yes`/`on` load as booleans, `2024` as an int, a leading `#` starts a comment, a
`:` splits a mapping), and single-quoted YAML has one escape (`''`) that covers neither backslashes
nor control characters. The double-quoted form has a total escape grammar, so this is safe rather
than lucky -- which the tests prove by loading every emission back with a real parser instead of
inspecting the string.
"""

# Double-quoted YAML understands JSON's escapes. `\` FIRST: escaping it after `"` would re-escape
# the backslashes this table itself introduces.
_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t"))


def _needs_hex(ch: str) -> bool:
    r"""Characters the five named escapes above do not cover, and that a YAML reader REJECTS raw.

    RAW docstring on purpose: without the `r` prefix, every `\x..` written below is interpreted, so
    this docstring itself held six real control characters. Measured, which is the only reason it
    was noticed.

    These five were once described as "a total escape grammar", which was false and untested: the
    corpus contained no control character. Measured against PyYAML, an unescaped `\x1b`, `\x07`,
    `\x0b` or `\x00` makes the config `sluice init` just wrote unreadable to every later sluice
    command (`ReaderError`), and `\x85` silently round-trips to a space -- a value corruption with
    nothing raising.

    The reachable path is ordinary rather than adversarial: `cv_contact` is prompted as "Contact
    block for the CV (email, phone, links)?", i.e. text pasted out of a CV or a PDF, where `\x0b`
    and `\x0c` are routine extraction artefacts.
    """
    # Written as escapes, never as literals: U+2028/U+2029 are invisible in an editor, and a
    # literal one here actually SPLIT this source line -- Python treats it as a line break.
    return ord(ch) < 0x20 or ch in ("\x7f", "\x85", "\u2028", "\u2029")


def _hex_escape(ch: str) -> str:
    r"""The narrowest escape form that can hold `ch`.

    `\xNN` takes exactly two hex digits, so it cannot express U+2028: `\x2028` reads back as `\x20`
    followed by a literal "28" -- a silent corruption, and the same class of bug as the raw
    character it was meant to fix. Measured, which is how the two-digit assumption was caught.
    """
    o = ord(ch)
    if o <= 0xFF:
        return f"\\x{o:02x}"
    return f"\\u{o:04x}" if o <= 0xFFFF else f"\\U{o:08x}"


def scalar(value) -> str:
    """One YAML scalar for `value`.

    `bool` is checked BEFORE `int` because it subclasses it -- the same ordering trap as
    `lead_ttl_days`' validator (#75). Without it `True` emits as `1`.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    # AFTER the named table, never before: the table has already doubled every backslash, so the
    # escape sequences introduced here keep their single escaping backslash.
    text = "".join(_hex_escape(ch) if _needs_hex(ch) else ch for ch in text)
    return f'"{text}"'


def flow_list(values) -> str:
    """A flow sequence. Flow rather than block style so a value fits on one template LINE, which
    keeps the surrounding comment attached to the key it explains."""
    return "[" + ", ".join(scalar(v) for v in values) + "]"
