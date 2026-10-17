"""The one grammar of a lead note's body sections: `## Job description` (#369) and `## Follow-up answers` (#378).

A lead with no posting to fetch (a referral, an InMail) carries its job description in the
note body instead, under `HEADING`. Two sides read this grammar -- the reader, deciding where
the section stops, and the writer (`Sluice.create_lead`), deciding what in pasted text must be
neutralised so the reader does not stop early. They share `breaks_section` and live in one
module because two copies of "which line ends the section" would drift, and a drift silently
truncates the posting the composer and the judge are shown. `core/followups.py` reads its own
section through `sections()` below, so a fence or a level-1/2 heading means the same thing in
both.

Pure and stdlib-only, beside `core/names.py` and `core/tokens.py` for the same reason they
sit in `core/`: triage and cv both import it, and neither may import the other. `leads prep`
(#195: `core/prepbrief.py` and the facade) reads it too.

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


def lines(text: str) -> list:
    """`text` split into lines, CRLF and lone CR both normalised, so a note saved on another
    platform reads the same. Public (#195) so `core/prepbrief.py` splits by the very rule the
    fence scanner reads, rather than reaching into a private helper or keeping a copy."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


# The name every caller in this module already uses; kept so the public name is an addition,
# not a rename.
_lines = lines


def breaks_section(line: str) -> bool:
    """True for a level-1 or level-2 ATX heading: the line that ends the section."""
    return bool(_BREAK.match(line))


def _scan_fences(lines: list) -> tuple:
    """`(flags, opener)`: for each line, False when it is a fence line or inside fenced code;
    and the opener still open at the end ("" when none). A fence closes on a run of the SAME
    character at least as long as the opener with nothing after it; an unclosed fence runs
    to the end of the body, as CommonMark has it."""
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
    return flags, opener


def _outside_fences(lines: list) -> list:
    return _scan_fences(lines)[0]


def open_fence(body: str) -> bool:
    """True when `body` ENDS inside an unclosed code fence, so anything appended after it would
    be swallowed into the fence (#195: `leads prep --write` refuses then, rather than appending
    a brief no reader would see). One scanner: `_scan_fences`' still-open opener, never a
    second copy of the fence grammar."""
    return bool(_scan_fences(_lines(body))[1])


def _is_heading(line: str, heading: str, tag: str) -> bool:
    bare = line.rstrip()
    # Guarded on a non-empty tag: `bare[:-len("")]` is `bare[:0]`, the empty string, so an
    # unguarded strip of an empty tag would match no heading at all.
    if tag and bare.endswith(tag):
        bare = bare[: -len(tag)].rstrip()
    return bare.casefold() == heading.casefold()


def sections(body: str, heading: str, tag: str = "") -> list:
    """The text of EVERY section headed `heading` (optionally followed by `tag` on the heading
    line), each stripped, in body order. A section ends at the next level-1 or level-2 heading
    outside fenced code -- including another `heading`, so consecutive sections stay apart."""
    lines = _lines(body)
    outside = _outside_fences(lines)
    found = []
    for start, line in enumerate(lines):
        if not (outside[start] and _is_heading(line, heading, tag)):
            continue
        text = []
        for i in range(start + 1, len(lines)):
            if outside[i] and breaks_section(lines[i]):
                break
            text.append(lines[i])
        found.append("\n".join(text).strip())
    return found


def read(body: str) -> tuple:
    """`(text, count)`: the first section's text, stripped, and how many `HEADING` lines the
    body holds -- so the CALLER can warn about duplicates. `("", 0)` with no section. A
    hand-typed heading without the tag reads the same as a written one; a heading with any
    other text on its line (`## Job description:`) is not the section."""
    found = sections(body, HEADING, TAG)
    return (found[0], len(found)) if found else ("", 0)


def demote(text: str) -> str:
    """`text` with every breaking line demoted to level 3 (its leading `#` run replaced by
    `###`, indentation kept), so pasted text cannot end the section it is written into."""
    return "\n".join(_HASHES.sub(r"\1###", line) if breaks_section(line) else line
                     for line in _lines(text))


def neutralise(text: str) -> str:
    """`text` made safe to write as one section: `demote`d, then, when it leaves a fence
    open, closed with that same opener. Without the close, an unclosed fence runs to the end
    of the BODY, so every later section's heading reads as fenced code and is skipped -- a
    `## Follow-up answers` written after a pasted job description would never reach the
    judge (#378)."""
    out = demote(text)
    opener = _scan_fences(_lines(out))[1]
    return f"{out}\n{opener}" if opener else out


_SHORT_HEAD = "the job description in the note is shorter than min_jd_chars ("
_SHORT_TAIL = ")"


def short_section_reason(min_jd_chars: int) -> str:
    """Why a section with text still did not count: it is under `min_jd_chars`. ONE phrase
    for both readers -- cv records it in `needs_signoff`, triage in its `unjudgeable`
    reason -- so a reword cannot split two explanations of one fact."""
    return f"{_SHORT_HEAD}{min_jd_chars}{_SHORT_TAIL}"


def is_short_section_reason(text: str) -> bool:
    """True only for a phrase `short_section_reason` returns: the fixed words around an ASCII
    integer. `core/leads.py::split_blind` uses it to tell sluice's own reason from text
    typed under the same tag in a hand-editable `needs_signoff`."""
    if not (text.startswith(_SHORT_HEAD) and text.endswith(_SHORT_TAIL)):
        return False
    return re.fullmatch(r"[0-9]+", text[len(_SHORT_HEAD):-len(_SHORT_TAIL)]) is not None


def render(text: str) -> str:
    """The section to append: the tagged heading, a blank line, then `text` neutralised (see
    `neutralise`). Heading levels change and an open fence is closed; nothing is refused. A
    `## Job description` line inside the text is demoted like any other, so a written note
    carries exactly one section."""
    return f"{HEADING} {TAG}\n\n" + neutralise(text)
