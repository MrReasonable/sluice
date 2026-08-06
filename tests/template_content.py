"""The "a CV template contributes no CONTENT of its own" check, in ONE place.

Two guards ask this question -- `tests/test_renderer_template.py` about the templates
sluice SHIPS, and `tests/test_no_leaked_files.py` about the worked examples under
`docs/`. They were two copies of the same twenty lines, and a review found the identical
bug in both: stripping `<style>...</style>` WHOLESALE hides text WeasyPrint genuinely
renders. Measured 2026-08-06 -- planting

    .contact::after { content: " -- seeking a remote-first Rust role, no agencies"; }

in the SHIPPED template left all twelve assertions GREEN. That is a shipped role
preference, the exact thing this repo's neutrality rule forbids, walking past the guard
written to catch it. One helper, so the next such fix cannot land in one copy only.

NOT a test module: no `test_` prefix, so pytest does not collect it. Same shape as
`tests/onboard_prose.py`.
"""
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_STYLE_RE = re.compile(r"<style\b.*?</style>", re.S | re.I)
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_TAG_RE = re.compile(r"<[^>]*>", re.S)

# A CSS `content:` declaration, and the string literals inside its value.
#
# Run over the RAW text, deliberately before anything is stripped: `content` is the one
# CSS property that puts WORDS on the page, so deleting the style block first is exactly
# how a shipped preference becomes invisible to a guard that then inspects only markup.
# Searched over the whole file rather than only inside <style>, so a `style="..."`
# attribute -- which `_TAG_RE` would otherwise erase along with the tag -- cannot become
# a second blind spot; the cost of over-reaching (a `content:` written in prose or a
# Jinja comment) is a false RED, which is the safe direction for a neutrality gate.
#
# `(?<![-\w])` so `--my-content:` and `no_content:` are not declarations. `[^;{}]*` stops
# at the end of the declaration rather than running into the next rule.
_CSS_CONTENT_RE = re.compile(r"(?<![-\w])content\s*:\s*([^;{}]*)", re.I)
_CSS_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")

# CSS escapes inside a `content:` literal: `\2022` (hex codepoint, optionally followed by
# one whitespace terminator) or `\<char>`. Decoded so this sweep sees the CHARACTERS
# WeasyPrint draws rather than the source spelling.
#
# This can only LOOSEN the guard, and saying so is the point: an escape always carries
# hex digits or letters, so the RAW spelling is alnum and would be flagged regardless --
# `\52\75\73\74` is caught before decoding just as surely as "Rust" is after. What
# decoding buys is the false positive it removes: `content: "\2022"` is a bullet glyph,
# pure punctuation, and the guard would otherwise red on the digits `2022` and push
# template authors into routing around it. Nothing is lost, because a preference smuggled
# as escapes still decodes to the words it renders as.
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n]?|(.))", re.S)


def _decode_css_escapes(literal: str) -> str:
    def one(m):
        hex_digits, plain = m.groups()
        if hex_digits is None:
            return plain
        try:
            return chr(int(hex_digits, 16))
        except ValueError:      # six hex digits can exceed the Unicode range
            return ""
    return _CSS_ESCAPE_RE.sub(one, literal)


def composer_headings() -> set[str]:
    """The section headings a template may legitimately print.

    DERIVED from `cv/compose.py`'s `_RULES` -- the format block the composer actually
    asks the model for -- never hand-listed, so this cannot drift from what a CV really
    contains. Callers must assert it is non-empty: `set() <= anything` is True, so a
    derivation that silently stopped matching would make every comparison below pass.
    """
    from sluice.cv.compose import _RULES
    return {ln.strip() for ln in _RULES.splitlines()
            if ln.strip() and ln.strip() == ln.strip().upper()
            and all(c.isalpha() or c.isspace() for c in ln.strip())}


def leftover_content(text: str) -> set[str]:
    """Every token of STATIC content `text` contributes that did not come from the CV.

    Three sources are removed because they are not content the template authored: Jinja
    expressions and statements (whose output is the user's own gate-approved CvDocument),
    HTML tags and their attributes, and CSS rules -- with the exception below.

    One source is ADDED: the string literals of every CSS `content:` declaration. Those
    are rendered into the PDF as visible text, so a `::after { content: "..." }` is
    content in every sense that matters even though it lives in a stylesheet.

    A token with no letters and no digits is PUNCTUATION -- the " | " separators between
    dates/location/title, or a decorative bullet glyph. Punctuation is layout, and layout
    is admittedly a shipped opinion (a template must lay SOMETHING out). Dropping it
    keeps the guard aimed at what it can actually check: words the template puts in the
    user's mouth. This is why a `content: ""` or `content: " - "` does not trip it.
    """
    tokens: set[str] = set()

    for declaration in _CSS_CONTENT_RE.findall(text):
        for single, double in _CSS_STRING_RE.findall(declaration):
            # Jinja stripped from the literal too: `content: "{{ document.name }}"` emits
            # the CANDIDATE's name, which is CV content and not the template's own.
            tokens.add(_decode_css_escapes(_JINJA_RE.sub(" ", single or double)))

    stripped = _STYLE_RE.sub(" ", text)
    stripped = _JINJA_RE.sub(" ", stripped)
    stripped = _TAG_RE.sub(" ", stripped)
    tokens |= set(stripped.splitlines())

    return {t for t in (tok.strip() for tok in tokens) if t and any(c.isalnum() for c in t)}


def packaged_data_patterns() -> list[str]:
    """The globs `pyproject.toml` ships inside the `sluice` package, read from the file.

    The shipping side is a GLOB, so the set of files reaching a user's install is open:
    a second template dropped into `sluice/templates/` is packaged automatically and
    would have been guarded by nothing, because the guard read one hardcoded path.
    Reading the patterns here lets the guard enumerate what actually ships AND notice a
    brand-new shipping route it does not know how to inspect.
    """
    with open(REPO / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return list(data["tool"]["setuptools"]["package-data"]["sluice"])


def packaged_templates() -> list[tuple[str, str]]:
    """(name, text) for every `.html.j2` sluice ships, read the way the renderer reads
    them -- `importlib.resources`, not a checkout-relative path."""
    from importlib.resources import files
    root = files("sluice.templates")
    return sorted((e.name, e.read_text(encoding="utf-8"))
                  for e in root.iterdir() if e.name.endswith(".html.j2"))
