"""The ONE grammar of a lead note's `## Job description` section (#369).

A lead with no posting to fetch (a referral, an InMail) carries its job description in the
note body instead, under `HEADING`. Two sides read this grammar -- the reader, deciding where
the section stops, and the writer (`Sluice.create_lead`), deciding what in pasted text must be
neutralised so the reader does not stop early. They share `breaks_section` and live in one
module because two copies of "which line ends the section" would drift, and a drift silently
truncates the posting the composer and the judge are shown.

Pure and stdlib-only, beside `core/names.py` and `core/tokens.py` for the same reason they
sit in `core/`: triage and cv both import it, and neither may import the other.

Fenced code is skipped by the reader, as CommonMark (and so Obsidian) renders it: a
`## Job description` line inside a fence is an example, not the section, and reading it as one
would let a note with no posting skip the blind hold. A `## ` line inside a fence does not end
the section either. The writer still demotes such a line, which the reader no longer needs but
which costs nothing, so a written section reads the same under either rule.

Residual, stated: setext headings (a line underlined with `===`/`---`) are not parsed. That is
harmless -- the reader does not treat one as a break, so nothing is truncated.
"""
import re

HEADING = "## Job description"
# Why a tag as well as the heading: `Store.append_body_section`'s idempotence is "the tag
# appears anywhere in the fresh file". The heading text alone could appear in a frontmatter
# title or a pasted body, turning the append into a no-op on a note with no section. An HTML
# comment is invisible in Obsidian's reading view, and is what `track/reconcile.py`'s tags
# already look like.
TAG = "<!-- sluice:job-description -->"

# A CommonMark ATX heading of level 1 or 2: up to three leading spaces, one or two `#`, then a
# space, a tab, or end of line. Four spaces is an indented code block, `###` is a level-3
# heading inside the posting, and `#tag` is an Obsidian tag -- none of those end the section.
_BREAK = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")
_HASHES = re.compile(r"^( {0,3})#{1,2}")


# A CommonMark fence: up to three spaces, then three or more backticks or tildes. A backtick
# fence's info string may not contain a backtick (that line is inline code, not a fence).
_FENCE = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")


def _lines(text: str) -> list:
    # CRLF and lone CR both normalised, so a note saved on another platform reads the same.
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def breaks_section(line: str) -> bool:
    """True for a level-1 or level-2 ATX heading: the line that ends the section."""
    return bool(_BREAK.match(line))


def _is_heading(line: str) -> bool:
    bare = line.rstrip()
    if bare.endswith(TAG):
        bare = bare[: -len(TAG)].rstrip()
    return bare.casefold() == HEADING.casefold()


def _outside_fences(lines: list) -> list:
    """For each line, False when it is a fence line or inside fenced code. A fence closes on a
    run of the SAME character at least as long as the opener with nothing after it; an
    unclosed fence runs to the end of the body, as CommonMark has it."""
    flags, opener = [], ""
    for line in lines:
        m = _FENCE.match(line)
        if not opener:
            if m:
                opener = m.group(1)
            flags.append(not m)
            continue
        if m and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener) \
                and not line[m.end():].strip():
            opener = ""
        flags.append(False)
    return flags


def read(body: str) -> tuple:
    """`(text, count)`: the first section's text, stripped, and how many `HEADING` lines the
    body holds -- so the CALLER can warn about duplicates. `("", 0)` with no section. A
    hand-typed heading without the tag reads the same as a written one; a heading with any
    other text on its line (`## Job description:`) is not the section."""
    lines = _lines(body)
    outside = _outside_fences(lines)
    starts = [i for i, line in enumerate(lines) if outside[i] and _is_heading(line)]
    if not starts:
        return "", 0
    out = []
    for i in range(starts[0] + 1, len(lines)):
        if outside[i] and breaks_section(lines[i]):
            break
        out.append(lines[i])
    return "\n".join(out).strip(), len(starts)


def render(text: str) -> str:
    """The section to append: the tagged heading, a blank line, then `text` with every
    breaking line demoted to level 3 (its leading `#` run replaced by `###`, indentation
    kept). Heading levels change; nothing is refused. A `## Job description` line inside the
    text is demoted like any other, so a written note carries exactly one section."""
    demoted = [_HASHES.sub(r"\1###", line) if breaks_section(line) else line
               for line in _lines(text)]
    return f"{HEADING} {TAG}\n\n" + "\n".join(demoted)
