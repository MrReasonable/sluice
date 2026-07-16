"""Location identity (#25): the comparison #5 keys every note split on.

Synthetic place names throughout (Palmerburgh/Clarkefurt, the tests/test_demash.py convention).
The rule was derived from the real board payloads in tests/fixtures/, but naming those cities here
would encode one person's job-hunt geography in tests/. The SHAPES carry the regression risk; the
specific cities do not -- these seven synthetic shapes reproduce the real corpus's 15-of-21
token-subset failure exactly. The derivation lives in
docs/superpowers/specs/2026-07-16-location-identity-evidence.py.
"""
from sluice.core.leads import _norm_location


def test_norm_location_casefolds_collapses_and_strips():
    assert _norm_location("  Palmerburgh  ") == "palmerburgh"
    assert _norm_location("PALMERBURGH   EC4Y") == "palmerburgh ec4y"
    # bool("   ") is True, so a blank that did not normalize to "" would let whitespace dirt
    # read as evidence of a difference. An empty side must abstain instead.
    assert _norm_location("   ") == ""
    assert _norm_location("") == ""


def test_norm_location_treats_real_board_punctuation_as_separators():
    # Both characters are real: one board renders "<city>\xa0∙ Choose area".
    assert _norm_location("Palmerburgh\xa0∙ Choose area") == "palmerburgh choose area"
    assert (_norm_location("Palmerburgh, Westland, North Clarke (Hybrid)")
            == "palmerburgh westland north clarke hybrid")


def test_norm_location_folds_accents():
    # Asserts the EXACT STRING, not the token count: a token-count assertion is GREEN under both
    # single mutations and catches neither. Deleting the NFKD fold makes
    # _compare_locations("Zürich", "Zurich") return DIFFERENT -- it SPLITS.
    assert _norm_location("Zürich") == "zurich"
    assert _norm_location("Zurich") == "zurich"


def test_norm_location_keeps_non_ascii_letters_whole():
    # The ONLY guard for `\W` vs `[^a-z0-9]`. "ø" has no NFKD decomposition -- it is a distinct
    # letter, not an accented "o" -- so the character class is the only live variable here.
    # Under [^a-z0-9] this shreds to "k benhavn": two junk tokens where there was one word.
    assert _norm_location("København") == "københavn"
    assert len(_norm_location("København").split()) == 1
