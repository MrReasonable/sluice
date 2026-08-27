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
-- rather than by adding two more names to a list.

A THIRD round then found three more, and the lesson had moved on again: the harvest was
now aimed at the right places and was TOKENISING them wrongly. Cutting a declaration
value at the first `;`/`{`/`}` truncates it mid-string, after which the string extractor
matches nothing and the harvest returns the EMPTY SET -- a silent green, not a partial
catch. `_STYLE_ATTR_RE` assumed a quoted attribute value, so an unquoted one was never a
CSS region at all. And the Jinja pass took quoted literals only, so a bare `{{ 450 }}`
rendered verbatim unseen. Fixed by `_css_declarations` (a quote-aware scan), a third
`_STYLE_ATTR_RE` branch, and `_JINJA_NUMBER_RE`.

What the harvest still does not see is stated in `leftover_content`'s own docstring and
pinned ROW BY ROW by `test_the_no_content_guards_known_residual_is_pinned_not_assumed`,
because a limit described only in prose goes stale exactly as quietly as a mechanism
does -- which is what happened here: that docstring claimed to catch "every string
literal a template author can type... and have drawn on the page" while two of the three
round-3 routes were live.

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

# The string literals inside a CSS declaration value.
#
# There used to be a SECOND, whole-text sweep beside this -- a `content\s*:\s*([^;{}]*)`
# run over the raw file, kept as belt-and-braces for a `content:` written outside any CSS
# context. It is gone, and the reason is measurement rather than tidiness. Round 3 asked
# what it still added once the per-region harvest below became quote-aware, and the answer
# was nothing that renders: swept across every planted row in
# test_renderer_template.py's positive-control table plus an unclosed <style> block and a
# `content:` inside a <script>, it was the SOLE catcher for exactly one input -- a
# `content:` declaration inside an HTML COMMENT, which reaches no page at all and which
# the old comment beside it already called a tolerated false positive. A check whose only
# unique catch is a false positive is not a safety net, it is something that looks like
# one; and left in place it would have needed the same quote-aware fix as everything else
# or gone on lying. The routes it was written for are covered: a `style=...` attribute is
# a CSS REGION (see `_STYLE_ATTR_RE`), and an unclosed <style> block survives `_STYLE_RE`'s
# strip and is caught as ordinary body text.
_CSS_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")

# THE CSS REGIONS a declaration can live in: a <style> block's body, and a `style=...`
# attribute's value. Everything harvested by `_css_declarations` is scoped to these,
# because a generic `prop: "value"` pattern run over the whole document would also match
# ordinary prose and namespaced markup (`xmlns:xlink="..."`) and turn every template into
# a red.
#
# THE UNQUOTED ATTRIBUTE ALTERNATIVE is the ninth bypass this helper has had, found in
# round 3 beside the tokenising bug below: this pattern used to offer only the two QUOTED
# forms, so `<p style=list-style-type:'no agencies'>` -- an unquoted attribute whose value
# then opens a quote of its own -- matched nothing and was never harvested at all. The
# unquoted branch runs to the tag's `>` rather than to the first space: stopping at the
# space would capture `list-style-type:'no` and leave an unbalanced quote, which is the
# same "truncate mid-string and harvest nothing" failure as the value bug below. Running
# to `>` over-reaches into the tag's OTHER attributes, and that is the deliberate
# direction: an over-reach costs a false RED (which a human reads and fixes), while an
# under-reach costs a silent green. The two quoted branches are listed FIRST so a
# conventional `style="..."` still matches them exactly as before.
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_STYLE_ATTR_RE = re.compile(
    r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)'|([^>]+))""", re.S | re.I)

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

# A Jinja OUTPUT expression whose WHOLE body is a bare number: `{{ 450 }}`.
#
# The harvest above takes QUOTED literals only, so a number typed straight into an
# expression -- a pay floor, a years-of-experience count -- rendered verbatim and was
# never seen. Measured 2026-08-06 through the real jinja2 against the shipped template.
# A number is template-author content exactly as a word is.
#
# DELIBERATELY ONLY THE WHOLE-BODY FORM, and the narrowness is the point rather than an
# oversight. `{{ }}` is the one delimiter that writes to the page (which is also why
# `{# #}` is absent above), but harvesting every digit inside one would red on
# `{{ document.work[0].title }}` and `{{ document.profile | truncate(200) }}` -- ordinary
# template constructs that draw no number at all. A guard that reds on a healthy template
# is not a stricter guard, it is one people delete, which is the reasoning
# `_CSS_RESOURCE_PROPS` already records. A body that is nothing but a number has no
# reading other than "print this number". What that leaves uncovered is a number inside a
# COMPOUND expression; it is named in `leftover_content`'s residual list and pinned by a
# row in test_the_no_content_guards_known_residual_is_pinned_not_assumed, so the limit
# cannot go stale in prose.
_JINJA_NUMBER_RE = re.compile(r"\{\{\s*(\d[\d_]*(?:\.\d+)?)\s*\}\}")

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

    DERIVED from `cv/parse.py`'s own grammar -- the sections the parser models -- never
    from `_RULES`. Reading `_RULES` STATICALLY misses the conditional SKILLS block
    (compose.py's `{skills_block}`, only interpolated when `build_prompt` is called with
    `skills_requested=True`), which would reject that heading permanently and turn a
    genuine, gate-clean SKILLS section into a leak-sweep false positive the moment a
    template renders it (#168 Task 13). RENDERING `_RULES` instead -- an earlier revision
    of this fix -- is worse: `{name_heading}` is `name.upper()` on its own line, so a
    rendered set of "every all-caps alphabetic line" ADMITS THE SUBSTITUTED CANDIDATE
    NAME into what is the ALLOWLIST for three template no-content guards plus the
    shipped-file leak sweep. Four reviewers independently measured that a template could
    then print that literal name with every negative guard green. Anchoring on the
    parser's grammar is immune to both failure modes: it is independent of `_RULES`
    entirely (so it is not self-certifying against the very prompt it allowlists for),
    and it carries `SKILLS` automatically the day the parser accepts the section (#168
    Task 7), with no dependency on whether any given compose call actually requested it.

    Callers must assert it is non-empty: `set() <= anything` is True, so a derivation
    that silently stopped matching would make every comparison below pass.
    """
    from sluice.cv.parse import _TRAILING_SECTIONS
    return {"PROFILE", "WORK EXPERIENCE"} | set(_TRAILING_SECTIONS)


def _css_regions(text: str) -> list[str]:
    """Every stretch of `text` that is CSS: <style> bodies, then style=... values."""
    regions = _STYLE_BLOCK_RE.findall(text)
    regions += [dq or sq or bare for dq, sq, bare in _STYLE_ATTR_RE.findall(text)]
    return regions


def _css_declarations(region: str) -> list[tuple[str, str]]:
    """Every `property: value` declaration in a stretch of CSS, tokenised QUOTE-AWARE.

    THE BUG THIS REPLACES, and it is the third in this guard family: the previous version
    captured a declaration value with the regex `[^;{}]*`, cutting it at the first `;`,
    `{` or `}`. Those three are ORDINARY CHARACTERS inside a CSS string token -- verified
    against real CSS with tinycss2, WeasyPrint's own tokeniser: a semicolon or a brace
    between quotes is part of one declaration value, not a terminator. So

        content: "seeking; no agencies"

    was captured as `"seeking` -- a fragment with ONE quote in it. `_CSS_STRING_RE` then
    matched no complete pair against that fragment, so the harvest returned the EMPTY SET
    rather than a partial catch. Measured 2026-08-06: five shapes bypassed the guard
    entirely that way (a whole-text `content:`, a `style=""` attribute value, a
    `list-style-type`, a `string-set` feeding a running header, and the brace form), and
    with one planted in the shipped template the whole guard suite -- 77 assertions across
    test_renderer_template.py, test_no_leaked_files.py and test_packaging.py -- was green.

    So the terminator is decided by a scan that knows where the strings are: a quote opens
    a run that only its own matching quote closes, a backslash inside that run escapes the
    next character (which is how `content: "no \\" agencies"` keeps its value in one
    piece), and only an UNQUOTED `;`, `{` or `}` ends the declaration.

    A chunk with no unquoted `:` is not a declaration and is skipped -- that is what
    selectors and at-rule preludes are. A chunk's FIRST unquoted colon splits it, so
    `a:hover` yields the harmless pair `("a", "hover")` exactly as the old regex did.

    The property is whatever precedes that colon, not a `[-\\w]+` token found inside the
    chunk. That is a small widening in the safe direction: `--my-blurb: "no agencies"` is
    now the property `--my-blurb` and IS harvested, where the old pattern's `(?<![-\\w])`
    lookbehind existed to make sure a custom property was NOT read as a bare `content`.
    A custom property genuinely can put words on the page (`content: var(--my-blurb)`),
    so harvesting it is correct rather than merely tolerable.
    """
    decls: list[tuple[str, str]] = []
    start, colon, quote, i = 0, -1, "", 0
    while i < len(region):
        ch = region[i]
        if quote:
            if ch == "\\":
                i += 2          # the escaped character cannot close the run
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == ":" and colon < 0:
            colon = i
        elif ch in ";{}":
            if colon >= 0:
                decls.append((region[start:colon], region[colon + 1:i]))
            start, colon = i + 1, -1
        i += 1
    # The tail. A `style="..."` attribute value carries no trailing `;`, and neither does
    # the last declaration of a block written without one, so a scanner that only emitted
    # on a terminator would drop precisely the shortest and most likely spellings.
    if colon >= 0:
        decls.append((region[start:colon], region[colon + 1:]))
    return [(p.strip(), v.strip()) for p, v in decls if p.strip()]


def leftover_content(text: str) -> set[str]:
    """Every LITERAL STRING `text` contributes that did not come from the CV.

    WHAT THIS CATCHES, stated as the guarantee it is rather than as the wider one it
    keeps being read as. FOUR routes, all harvested before anything is stripped, because
    stripping first is what hid two of them:

      * body text -- whatever survives removing Jinja code, HTML tags and CSS;
      * a quoted value in any CSS declaration inside a <style> block or a style=...
        attribute (`content`, `list-style-type`, `string-set`, `quotes`,
        `bookmark-label`, and every property nobody here has thought of), with font names
        and `url(...)` references exempted -- see `_CSS_RESOURCE_PROPS`. The declaration
        value is tokenised QUOTE-AWARE (`_css_declarations`), so a `;` or a brace inside
        the string does not truncate it;
      * a quoted literal inside `{{ ... }}` or `{% ... %}`. Jinja CODE is otherwise
        deleted, because a field reference's output is the user's own gate-approved
        CvDocument -- but a literal's output is the template author's words, and deleting
        the block wholesale took those with it;
      * a `{{ ... }}` output expression whose whole body is a bare NUMBER -- see
        `_JINJA_NUMBER_RE`. A number is template-author content as much as a word is.

    WHAT IT DOES NOT CATCH. The first three read their text from somewhere OTHER than a
    literal in the template's own markup, so a string harvest cannot see them by
    construction; the fourth is a deliberately drawn line rather than a structural limit,
    and is listed here because a limit stated only in prose goes stale exactly as quietly
    as a mechanism does. Every one of these is pinned by a row in
    `test_the_no_content_guards_known_residual_is_pinned_not_assumed`:

      * `content: attr(data-x)` paired with `data-x="..."` on an element. The words live
        in an HTML ATTRIBUTE, and attribute values are erased with their tag by `_TAG_RE`;
        harvesting them instead would mean flagging every `class` and `href` in the file.
      * an `alt` or `value` attribute WeasyPrint falls back to when it cannot resolve the
        element -- same reason, same place.
      * anything a template pulls from outside its own source.
      * a number inside a COMPOUND Jinja expression (`{{ 450 if document.work else 0 }}`).
        Only the whole-body form is harvested, because harvesting every digit inside a
        `{{ }}` would red on a subscript or a `truncate(200)` -- see `_JINJA_NUMBER_RE`.

    So this function does NOT prove a template contributes no content. It proves a
    template contributes no LITERAL content, which is the checkable part, and the residual
    above is a reviewer's job. Overstating a guarantee is this repo's named defect class
    and the reason this docstring is the length it is -- it has now been overstated twice,
    the second time by claiming "every string literal a template author can type directly
    into the template's own source and have drawn on the page" while both the CSS
    truncation bug above and this numeric route were live.

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

    for region in _css_regions(text):
        for prop, value in _css_declarations(region):
            if prop.lower() in _CSS_RESOURCE_PROPS:
                continue
            harvest(_CSS_URL_RE.sub(" ", value))

    for expression, statement in _JINJA_CODE_RE.findall(text):
        for single, double in _CSS_STRING_RE.findall(expression or statement):
            tokens.add(single or double)

    tokens |= set(_JINJA_NUMBER_RE.findall(text))

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
