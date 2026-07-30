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
    return f'"{text}"'


def flow_list(values) -> str:
    """A flow sequence. Flow rather than block style so a value fits on one template LINE, which
    keeps the surrounding comment attached to the key it explains."""
    return "[" + ", ".join(scalar(v) for v in values) + "]"
