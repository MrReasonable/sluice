"""How much text one checkbox in a client's review form can show in full.

Measured on Claude Code 2.1.29x (see the comment above DESC_MAX_CHARS). The sizes serve the
one review form left, evidence verify (`sluice/mcpserver.py`). `hides_text` is also in-session
setup's control and bidirectional character check (`sluice/onboard/review.py`), so the two
refuse the same characters. Pure: no I/O.
"""
from sluice.core.safeout import is_control

# How Claude Code 2.1.29x shows an input-required form, measured 2026-10-06 with a probe
# server: the MESSAGE folds after three lines ("... (+N more lines)") with no way to
# expand it, while each checkbox DESCRIPTION is shown in full as plain text -- any number
# of lines, but cut with "..." at about 2,000 characters. And the dialog does not scroll
# in every terminal (tmux), so a form should fit about one screen. Hence: a one-line
# message, each entry's text in its own description, a character cap under the cut, and
# forms packed to a conservative screen estimate (the server cannot know the width).
# Client facts, not user preferences, so constants rather than config.
DESC_MAX_CHARS = 1900   # under the ~2,000-character cut, so nothing is ever hidden
FORM_COLS = 80          # assumed terminal width for the wrap estimate
DESC_WIDTH = FORM_COLS - 8  # descriptions are indented under their checkbox
FORM_LINES = 30         # display lines of entries per form: about one screen


def describe(title: str, body: str) -> str:
    """What sits under an entry's checkbox: its title, then its exact stored text."""
    return f"{title}\n{body}"


# Bidi overrides and isolates reorder how a line DISPLAYS, so the human would read the
# stored text in a different order. Zero-width characters are deliberately absent: they
# are common in pasted text and change nothing a reader sees.
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def hides_text(text: str) -> bool:
    """A character that can make the terminal show something other than the stored
    bytes -- a carriage return or escape sequence can overwrite what is displayed, a bidi
    override reorders it. The control class is core/safeout.py's, the one CLI output is
    escaped against, minus newline and tab, which are ordinary text in an entry."""
    return any((is_control(ch) and ch not in "\n\t") or ch in BIDI_CONTROLS
               for ch in text)


def entry_lines(title: str, body: str) -> int:
    """Estimated display lines for one checkbox: its label, its wrapped description,
    and the blank line after it."""
    wrapped = sum(max(1, -(-len(line) // DESC_WIDTH))
                  for line in describe(title, body).split("\n"))
    return wrapped + 2
