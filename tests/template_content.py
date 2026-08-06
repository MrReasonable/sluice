"""The "a CV template contributes no LITERAL content of its own" check, in ONE place.

Two guards ask this question -- `tests/test_renderer_template.py` about the templates
sluice SHIPS, and `tests/test_no_leaked_files.py` about the worked examples under
`docs/`. They were two copies of the same twenty lines, and a review found the identical
bug in both: stripping `<style>...</style>` WHOLESALE hides text WeasyPrint genuinely
renders. Measured 2026-08-06 -- planting

    .contact::after { content: " -- seeking a remote-first Rust role, no agencies"; }

in the SHIPPED template left every assertion in both files GREEN. That is a shipped role
preference, the exact thing this repo's neutrality rule forbids, walking past the guard
written to catch it. One helper, so the next such fix cannot land in one copy only --
and `test_the_no_content_strip_still_has_exactly_one_definition` now enforces the ONE
rather than leaving it to a comment.

That fix was not enough, and the reason is worth carrying: it denylisted the SYNTAX SHAPE
it had just been shown, and a second review round then found two more shapes past it. A
Jinja STRING LITERAL (`{{ "..." }}`) was deleted along with the field references, and
`content:` turned out to be one of several CSS properties that draw words. Both are fixed
below by DEFAULTING TO HARVEST -- every CSS property unless exempted, every Jinja literal
-- rather than by adding two more names to a list. What the harvest still cannot see is
stated in `leftover_content`'s own docstring and pinned by a test, because a limit
described only in prose goes stale exactly as quietly as a mechanism does.

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
# Run over the RAW text, deliberately before anything is stripped: `content` puts WORDS
# on the page, so deleting the style block first is exactly how a shipped preference
# becomes invisible to a guard that then inspects only markup.
# Searched over the whole file rather than only inside <style>, so a `style="..."`
# attribute -- which `_TAG_RE` would otherwise erase along with the tag -- cannot become
# a second blind spot; the cost of over-reaching (a `content:` written in prose or a
# Jinja comment) is a false RED, which is the safe direction for a neutrality gate.
#
# `content` is NOT the only such property, and treating it as one was the second bug found
# in this helper -- see `_CSS_DECL_RE` below, which generalises it. This whole-text sweep
# is kept as well as that one, not instead of it: it is the belt-and-braces that reaches a
# `content:` written outside any CSS context at all, and narrowing coverage while fixing a
# coverage bug is the wrong direction.
#
# `(?<![-\w])` so `--my-content:` and `no_content:` are not declarations. `[^;{}]*` stops
# at the end of the declaration rather than running into the next rule.
_CSS_CONTENT_RE = re.compile(r"(?<![-\w])content\s*:\s*([^;{}]*)", re.I)
_CSS_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")

# THE CSS REGIONS a declaration can live in: a <style> block's body, and a `style="..."`
# attribute's value. Everything harvested by `_CSS_DECL_RE` is scoped to these, because
# a generic `prop: "value"` pattern run over the whole document would also match ordinary
# prose and namespaced markup (`xmlns:xlink="..."`) and turn every template into a red.
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_STYLE_ATTR_RE = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.S | re.I)

# ANY CSS declaration, property captured. Harvesting from every property by DEFAULT, with
# a named exemption list, is the point: the previous version denylisted one property name
# (`content`) and so was blind to every other route a literal string has onto the page --
# measured 2026-08-06 through the real jinja2 and the shipped template, `list-style-type:
# "..."` (a word before every bullet), `string-set: hdr "..."` paired with `@top-center {
# content: string(hdr) }` (a running header on every page), `quotes: "..."` and
# `bookmark-label: "..."` (into the PDF outline) all rendered and all passed the guard.
# Defaulting to HARVEST means a CSS property nobody here has heard of is caught the day it
# is used, rather than the day someone remembers to add it to a list.
_CSS_DECL_RE = re.compile(r"(?<![-\w])([-\w]+)\s*:\s*([^;{}]*)")

# The exemptions, and both are "this quoted string NAMES A RESOURCE, it is never drawn".
#
# `font-family`/`font`/`src` carry font NAMES, and both templates in this repo already use
# them (`"DejaVu Sans"`, `"Times New Roman"`), so without this the guard would red on a
# clean tree -- which is not a stricter guard, it is a guard people delete. A preference
# smuggled as `font-family: "no agencies please", serif` puts nothing on the page: the name
# does not resolve and the fallback is used.
#
# `url(...)` is exempted by FUNCTION rather than by property, so it covers `background`,
# `list-style-image`, `@font-face src` and anything else without naming them -- the same
# default-open-to-harvest reasoning as above, applied to the one syntax that is always a
# resource reference.
_CSS_RESOURCE_PROPS = frozenset({"font-family", "font", "src"})
_CSS_URL_RE = re.compile(r"url\(\s*(?:'[^']*'|\"[^\"]*\"|[^)]*)\)", re.I)

# A Jinja EXPRESSION or STATEMENT, with its body captured. `{# ... #}` is deliberately
# absent: a comment is discarded by the template engine and reaches no page.
#
# The bug this closes: `leftover_content` used to delete `{{ ... }}`/`{% ... %}` WHOLESALE,
# on the premise that a Jinja block's output is the user's own gate-approved CvDocument.
# That premise holds for a field reference and fails for a STRING LITERAL. Measured
# 2026-08-06 through the real jinja2 and the shipped template -- `{{ "seeking a
# remote-first Rust role, no agencies" }}` planted after the `<h1>` line renders VERBATIM
# into the HTML, and all nine assertions in tests/test_renderer_template.py stayed green.
# `{% set x = "..." %}` and `{{ document.name | default('...') }}` are the same shape.
_JINJA_CODE_RE = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.S)

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


def _css_regions(text: str) -> list[str]:
    """Every stretch of `text` that is CSS: <style> bodies, then style="..." values."""
    regions = _STYLE_BLOCK_RE.findall(text)
    regions += [dq or sq for dq, sq in _STYLE_ATTR_RE.findall(text)]
    return regions


def leftover_content(text: str) -> set[str]:
    """Every LITERAL STRING `text` contributes that did not come from the CV.

    WHAT THIS CATCHES, stated as the guarantee it is rather than as the wider one it
    keeps being read as: every string literal a template author can type directly into
    the template's own source and have drawn on the page. Three routes, all harvested
    before anything is stripped, because stripping first is what hid two of them:

      * body text -- whatever survives removing Jinja code, HTML tags and CSS;
      * a quoted value in any CSS declaration inside a <style> block or a style="..."
        attribute (`content`, `list-style-type`, `string-set`, `quotes`,
        `bookmark-label`, and every property nobody here has thought of), with font names
        and `url(...)` references exempted -- see `_CSS_RESOURCE_PROPS`;
      * a quoted literal inside `{{ ... }}` or `{% ... %}`. Jinja CODE is otherwise
        deleted, because a field reference's output is the user's own gate-approved
        CvDocument -- but a literal's output is the template author's words, and deleting
        the block wholesale took those with it.

    WHAT IT STRUCTURALLY CANNOT CATCH, and this is not a gap to be closed by adding one
    more pattern -- these routes read their text from somewhere OTHER than a literal in
    the template's own markup, so a string harvest cannot see them by construction:

      * `content: attr(data-x)` paired with `data-x="..."` on an element. The words live
        in an HTML ATTRIBUTE, and attribute values are erased with their tag by `_TAG_RE`;
        harvesting them instead would mean flagging every `class` and `href` in the file.
      * an `alt` or `value` attribute WeasyPrint falls back to when it cannot resolve the
        element -- same reason, same place.
      * anything a template pulls from outside its own source.

    So this function does NOT prove a template contributes no content. It proves a
    template contributes no LITERAL content, which is the checkable part, and the residual
    above is a reviewer's job. Overstating a guarantee is this repo's named defect class
    and the reason this docstring is the length it is.

    A token with no letters and no digits is PUNCTUATION -- the " | " separators between
    dates/location/title, or a decorative bullet glyph. Punctuation is layout, and layout
    is admittedly a shipped opinion (a template must lay SOMETHING out). Dropping it
    keeps the guard aimed at what it can actually check: words the template puts in the
    user's mouth. This is why a `content: ""` or `content: " - "` does not trip it.
    """
    tokens: set[str] = set()

    def harvest(value: str) -> None:
        for single, double in _CSS_STRING_RE.findall(value):
            # Jinja stripped from the literal too: `content: "{{ document.name }}"` emits
            # the CANDIDATE's name, which is CV content and not the template's own. A
            # literal INSIDE that Jinja is caught by the Jinja pass below, so nothing is
            # lost by removing the block here.
            tokens.add(_decode_css_escapes(_JINJA_RE.sub(" ", single or double)))

    for declaration in _CSS_CONTENT_RE.findall(text):
        harvest(declaration)


    for region in _css_regions(text):
        for prop, value in _CSS_DECL_RE.findall(region):
            if prop.lower() in _CSS_RESOURCE_PROPS:
                continue
            harvest(_CSS_URL_RE.sub(" ", value))

    for expression, statement in _JINJA_CODE_RE.findall(text):
        for single, double in _CSS_STRING_RE.findall(expression or statement):
            tokens.add(single or double)

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
