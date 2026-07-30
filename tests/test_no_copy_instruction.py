"""No shipped doc may instruct a `cp sluice.yaml.example`. The file ships ACTIVE gates -- measured,
`is_relevant("Senior Software Engineer")` is False against a verbatim copy -- so an instruction to
copy it hands a stranger a closed gate with nothing saying so. `sluice init` exists to replace it."""
import glob
import re

# The harm is "a stranger is told to put this file into place", not "a line starts with cp".
# Witnessed: the prose form ("Copy `sluice.yaml.example` to ...") and `cat sluice.yaml.example >`
# both passed an earlier `^\s*cp\b` matcher while a literal `cp` line was correctly caught.
_COPY_VERB = re.compile(r"\bcp\b|\bcopy\b|\bcat\b[^\n]*>", re.I)

# A NEGATED copy -- the docs must be able to say "do not copy this", which is the whole
# remediation. Markdown emphasis is allowed inside the negation (`Do **not** copy`).
_NEGATED_COPY = re.compile(
    r"\b(?:do\s+[*`]*not[*`]*|don't|never|no\s+need\s+to)[\s*`]+(?:cp|copy)\b", re.I)


def _instructs_a_copy(line: str) -> bool:
    """Written as a function, not one clever regex.

    The regex version exempted any line containing the word "not" ANYWHERE, so
    "This does not apply here. Copy sluice.yaml.example into place." sailed through -- measured.
    Deleting the negated phrases and then re-checking for a surviving verb is the same rule
    stated in a way a reader can actually falsify.
    """
    if "sluice.yaml.example" not in line or not _COPY_VERB.search(line):
        return False
    return bool(_COPY_VERB.search(_NEGATED_COPY.sub("", line)))


# What a USER reads. `docs/*.md` alone resolved to one file that was already hand-listed, so the
# old glob contributed nothing to the SCOPE count.
#
# `docs/superpowers/` is deliberately OUT: specs and plans are historical records of how the
# decision was reached, and they necessarily quote the instruction they exist to remove. Sweeping
# them would force a design doc to lie about its own history -- and they are not shipped prose, so
# no stranger is ever handed a closed gate by one.
_SHIPPED_PROSE = ["README.md", "sluice.yaml.example"]
_SHIPPED_PROSE += glob.glob("docs/*.md")
_SHIPPED_PROSE += glob.glob(".rulesync/**/*.md", recursive=True)


def test_no_shipped_doc_tells_anyone_to_copy_the_example():
    """The file ships ACTIVE gates -- measured, `is_relevant("Senior Software Engineer")` is False
    against a verbatim copy -- so an instruction to copy it hands a stranger a closed gate with
    nothing saying so.

    README's own "Do **not** copy" sentence is allowed by the negative lookahead for `not`, which
    is deliberate and narrow: allow-listing it explicitly beats letting it pass by accident, and a
    sentence that says to copy it while containing the word "not" elsewhere is not a shape any of
    these docs has."""
    checked = 0
    for path in sorted(set(_SHIPPED_PROSE)):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        checked += 1
        hits = [ln for ln in text.splitlines() if _instructs_a_copy(ln)]
        assert not hits, f"{path} instructs a copy of the example config: {hits[0][:90]!r}"
    assert checked >= 5, f"the sweep read only {checked} files"          # SCOPE


def test_the_matcher_catches_every_form_the_instruction_takes():
    """POSITIVE CONTROL. Without it the sweep above passes because the pattern matches nothing --
    which is exactly what the previous `^\\s*cp\\b` version did for two of these three."""
    for offender in ("cp sluice.yaml.example ~/.config/sluice/config.yaml",
                     "Copy `sluice.yaml.example` to your config directory.",
                     "cat sluice.yaml.example > config.yaml",
                     "  cp -n sluice.yaml.example sluice.local.yaml",
                     # The word "not" elsewhere on the line must not buy an exemption.
                     "This does not apply here. Copy sluice.yaml.example into place.",
                     "Whether or not you use XDG, cp sluice.yaml.example into place."):
        assert _instructs_a_copy(offender), f"not caught: {offender!r}"


def test_the_matcher_does_not_fire_on_the_warning_against_copying():
    """NEGATIVE CONTROL. The docs must be able to SAY not to copy it -- that is the whole
    remediation -- without tripping the guard that enforces it."""
    for allowed in ("Do **not** copy `sluice.yaml.example` into place.",
                    "Never copy sluice.yaml.example into your config directory.",
                    # The exact form the rules file uses -- a backtick between negation and verb.
                    "**Do NOT `cp sluice.yaml.example` into place -- it closes your gates.**",
                    "`sluice.yaml.example` documents every knob.",
                    "New tunables go in the dataclass and sluice.yaml.example."):
        assert not _instructs_a_copy(allowed), f"false positive: {allowed!r}"
