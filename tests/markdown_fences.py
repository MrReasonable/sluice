"""CommonMark fenced-code-block scanning, for the two guards that read README.md.

WHY THIS EXISTS, and why it is not a regex. Both `tests/test_docs_claims.py` (the Commands
table) and `tests/test_fixture_name_neutrality.py` (the sample lead note) must know which lines
of README sit inside a code fence, so an ILLUSTRATION is never parsed as the real thing. Each
started with its own regex, and CodeRabbit corrected those regexes on three consecutive review
rounds of PR #222 -- one CommonMark rule per round:

  round 4: a closing run must use the SAME delimiter, and runs may be longer than three
  round 5: a fence may be indented up to three spaces, independently on each line
  round 6: a BACKTICK fence's info string may not contain a backtick

Every round the fix was another clause bolted onto the pattern, which is hand-enumerating a
specification from whichever shape a reviewer last thought of. Round 4 tried to stop the drift
by pinning the two patterns to each other; round 5 walked straight past that, because both were
wrong in the same way and a pattern-to-pattern comparison cannot see that.

So the rules are implemented ONCE, here, line by line, with each CommonMark clause its own
named branch -- and both guards call it. A missed rule is now a missing branch someone can read
for, rather than a silent gap in a 90-character pattern.

Two properties the regexes could not offer at all:

  * An unclosed fence is REPORTED as a fact (`unclosed_fence`), not inferred from leftover
    delimiter markers. The old guards searched the stripped text for residual markers, which
    only worked when the malformed opener still looked like one -- a mixed-delimiter closer and
    a backtick-in-info-string opener both left ZERO residual markers while swallowing real
    content.
  * There is one implementation, so the two guards cannot disagree. The agreement test that
    existed to catch that disagreement is gone with the duplication it policed.

Reference: CommonMark 0.31 section 4.5 (fenced code blocks).
"""
import re

# An opening fence: up to three spaces of indent, then a run of at least three backticks or
# tildes, then the info string. Four spaces is an indented code block, not a fence, which is
# why the indent is bounded rather than `\s*`.
_OPEN = re.compile(r"^(?P<indent>[ ]{0,3})(?P<run>`{3,}|~{3,})(?P<info>.*)$")

# CommonMark 2.1: a line ending is a newline, a carriage return, or a carriage return followed
# by a newline. Splitting on "\n" alone leaves a trailing "\r" that `_closes` then rejects,
# because a closing fence may carry nothing but spaces and tabs -- so a well-formed CRLF block
# reported as UNCLOSED. Not reachable from this repo's own callers, which read README through
# `open(..., encoding=...)`/`read_text(...)` and get universal-newline translation for free
# (measured: neither yields a CR), but this module is a general scanner and the next caller may
# hand it text decoded from bytes. Splitting per the spec costs one line and removes the
# question.
_LINE_END = re.compile(r"\r\n|\r|\n")


def _lines(text: str) -> list:
    return _LINE_END.split(text)


def _closes(line: str, char: str, length: int) -> bool:
    """Is `line` a closing fence for an open fence of `length` x `char`?

    CommonMark: the closing fence is indented at most three spaces (independently of the
    opener's indent), uses the SAME character, is AT LEAST as long, and carries nothing but
    optional trailing whitespace -- a closing fence may not have an info string.
    """
    # `` `+|~+ `` and NOT ``[`~]+``: the run must be ONE repeated character. The permissive
    # class accepted ```` ```~ ```` as a four-long backtick closer -- caught by this module's
    # own shape table, which is the whole argument for deriving the rows from the spec rather
    # than from cases anyone happened to remember.
    m = re.match(r"^[ ]{0,3}(?P<run>`+|~+)[ \t]*$", line)
    if m is None:
        return False
    run = m.group("run")
    return run[0] == char and len(run) >= length


def _spans(text: str):
    """[(open_index, close_index_or_None, [body lines])] for every fence in `text`.

    ONE state machine. `strip_fenced_blocks`, `fenced_blocks` and `unclosed_fence` are all
    derived from it -- an earlier cut had `unclosed_fence` walking the lines itself, which is
    the same duplication this module exists to remove, one level down.

    A `close_index` of None means the fence is never closed; CommonMark says it then runs to
    the end of the document, so its body is everything that follows.
    """
    lines = _lines(text)
    spans, char, length, opened, body = [], None, 0, None, []
    for i, line in enumerate(lines):
        if char is None:
            m = _OPEN.match(line)
            # A BACKTICK fence's info string may not contain a backtick -- such a line is
            # ordinary text, not an opener. (A tilde fence's info string may contain anything.)
            if m and not (m.group("run")[0] == "`" and "`" in m.group("info")):
                char, length, opened, body = m.group("run")[0], len(m.group("run")), i, []
        elif _closes(line, char, length):
            spans.append((opened, i, body))
            char, length, opened, body = None, 0, None, []
        else:
            body.append(line)
    if char is not None:                       # unclosed: runs to end of document
        spans.append((opened, None, body))
    return spans


def _fenced_line_indexes(text: str) -> set:
    """Every line index inside a fence, delimiter lines included."""
    n = len(_lines(text))
    inside = set()
    for open_i, close_i, _ in _spans(text):
        inside.update(range(open_i, (close_i + 1) if close_i is not None else n))
    return inside


def strip_fenced_blocks(text: str) -> str:
    """`text` with every fenced block removed, delimiter lines included."""
    lines = _lines(text)
    inside = _fenced_line_indexes(text)
    return "\n".join(line for i, line in enumerate(lines) if i not in inside)


def fenced_blocks(text: str):
    """Yield each fenced block's BODY -- its content, without the delimiter lines.

    An unclosed fence yields whatever it swallowed, exactly as CommonMark reads it. Callers
    that must not act on a malformed document ask `unclosed_fence` first rather than inferring
    anything from what this returns.
    """
    for _, _, body in _spans(text):
        yield ("\n".join(body) + "\n") if body else ""


def unclosed_fence(text: str) -> bool:
    """True when a fence is opened and never closed.

    Callers refuse to parse rather than guessing: an unclosed fence swallows the rest of the
    document, so anything placed after one is invisible to a guard that looked only at what
    survived stripping.
    """
    return any(close_i is None for _, close_i, _ in _spans(text))
