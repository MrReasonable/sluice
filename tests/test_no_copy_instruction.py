"""No shipped doc may instruct a `cp sluice.yaml.example`. The file ships ACTIVE gates -- measured,
`is_relevant("Senior Software Engineer")` is False against a verbatim copy -- so an instruction to
copy it hands a stranger a closed gate with nothing saying so. `sluice init` exists to replace it."""
import glob
import re

# The harm is "a stranger is told to put this file into place", not "a line starts with cp".
# Witnessed: the prose form ("Copy `sluice.yaml.example` to ...") and `cat sluice.yaml.example >`
# both passed the old `^\s*cp\b` matcher while a literal `cp` line was correctly caught.
_INSTRUCTS_A_COPY = re.compile(
    r"^(?![^\n]*\bnot\b)[^\n]*(?:^\s*cp\b|\bcopy\b|^\s*cat\b[^\n]*>)[^\n]*sluice\.yaml\.example"
    r"|^(?![^\n]*\bnot\b)[^\n]*sluice\.yaml\.example[^\n]*(?:\bcopy\b|>)",
    re.M | re.I)

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
        hit = _INSTRUCTS_A_COPY.search(text)
        assert not hit, f"{path} instructs a copy of the example config: {hit.group(0)[:80]!r}"
    assert checked >= 5, f"the sweep read only {checked} files"          # SCOPE


def test_the_matcher_catches_every_form_the_instruction_takes():
    """POSITIVE CONTROL. Without it the sweep above passes because the pattern matches nothing --
    which is exactly what the previous `^\\s*cp\\b` version did for two of these three."""
    for offender in ("cp sluice.yaml.example ~/.config/sluice/config.yaml",
                     "Copy `sluice.yaml.example` to your config directory.",
                     "cat sluice.yaml.example > config.yaml",
                     "  cp -n sluice.yaml.example sluice.local.yaml"):
        assert _INSTRUCTS_A_COPY.search(offender), f"not caught: {offender!r}"


def test_the_matcher_does_not_fire_on_the_warning_against_copying():
    """NEGATIVE CONTROL. The docs must be able to SAY not to copy it -- that is the whole
    remediation -- without tripping the guard that enforces it."""
    for allowed in ("Do **not** copy `sluice.yaml.example` into place.",
                    "`sluice.yaml.example` documents every knob.",
                    "New tunables go in the dataclass and sluice.yaml.example."):
        assert not _INSTRUCTS_A_COPY.search(allowed), f"false positive: {allowed!r}"
