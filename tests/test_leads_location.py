"""Location identity (#25): the comparison #5 keys every note split on.

Synthetic place names throughout (Palmerburgh/Clarkefurt, the tests/test_demash.py convention).
The rule was derived from the real board payloads in tests/fixtures/, but naming those cities here
would encode one person's job-hunt geography in tests/. The SHAPES carry the regression risk; the
specific cities do not -- these seven synthetic shapes reproduce the real corpus's 15-of-21
token-subset failure exactly. The derivation lives in
docs/superpowers/specs/2026-07-16-location-identity-evidence.py.
"""
import itertools

import pytest

from sluice.core.leads import DIFFERENT, SAME, UNKNOWN, _compare_locations, _norm_location


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
    # single mutations and catches neither. Deleting the NFKD fold makes the accented and bare
    # spellings share no token, so _compare_locations returns DIFFERENT -- it SPLITS.
    # Synthetic, per this module's docstring: 'ä' carries the property under test (it NFKD-
    # decomposes to 'a' + a combining mark); a real city would add geography the shape does not need.
    assert _norm_location("Pälmerburgh") == "palmerburgh"
    assert _norm_location("Palmerburgh") == "palmerburgh"


def test_norm_location_folds_a_capital_that_only_nfkd_reveals():
    # The ONLY guard for the ORDER of the fold: NFKD before casefold. 663 codepoints decompose to
    # an uppercase letter, which a casefold that already ran can never reach. Neither fold test
    # above witnesses this -- 'ü' and 'ø' are lowercase under either ordering, so both stay GREEN
    # with the two calls swapped. Asserts the exact string; a token count is green under the
    # mutant.
    assert _norm_location("№5") == "no5"
    # The consequence, and why this is not cosmetic: swapped, it fails toward DIFFERENT.
    assert _compare_locations("№5", "No5") == SAME


def test_norm_location_keeps_non_ascii_letters_whole():
    # The ONLY guard for `\W` vs `[^a-z0-9]`. "ø" has no NFKD decomposition -- it is a distinct
    # letter, not an accented "o" -- so the character class is the only live variable here.
    # Under [^a-z0-9] this shreds to "k benburgh": two junk tokens where there was one word.
    # Synthetic, per this module's docstring: the "ø" is the property under test, not the city.
    assert _norm_location("Købenburgh") == "købenburgh"
    assert len(_norm_location("Købenburgh").split()) == 1


# The seven shapes the real corpus renders a single city in. These reproduce the corpus's
# token-subset failure exactly: subset splits 15 of these 21 pairs, overlap splits 0.
_SAME_CITY_SHAPES = [
    "Palmerburgh",
    "Palmerburgh EC4Y",
    "Hybrid work in Palmerburgh",
    "Palmerburgh\xa0∙ Choose area",
    "Palmerburgh Area, North Clarke (Hybrid)",
    "Palmerburgh, Westland, North Clarke (Hybrid)",
    "Palmerburgh, Westland, North Clarke (Remote)",
]


def test_every_rendering_of_one_city_is_never_a_split():
    # THE test. Boards decorate a city differently on every re-post, so neither side of most
    # pairs is a subset of the other -- token-subset splits 15 of these 21 and manufactures a
    # duplicate note per cross-board re-post. Overlap keys on the shared city token instead.
    for a, b in itertools.combinations(_SAME_CITY_SHAPES, 2):
        assert _compare_locations(a, b) == SAME, f"{a!r} vs {b!r} would split a re-post"


def test_genuinely_different_cities_are_the_only_split():
    assert _compare_locations("Palmerburgh", "Clarkefurt") == DIFFERENT
    assert _compare_locations("Palmerburgh EC4Y", "Clarkefurt (Hybrid)") == DIFFERENT


def test_compare_locations_is_symmetric():
    assert (_compare_locations("Palmerburgh", "Clarkefurt")
            == _compare_locations("Clarkefurt", "Palmerburgh"))
    assert (_compare_locations("Palmerburgh EC4Y", "Palmerburgh")
            == _compare_locations("Palmerburgh", "Palmerburgh EC4Y"))


def test_compare_locations_is_reflexive_for_anything_with_a_surviving_token():
    # The qualifier is required, not pedantry: "" and a noise-emptied value are both UNKNOWN.
    assert _compare_locations("Palmerburgh", "Palmerburgh") == SAME
    assert _compare_locations("", "") == UNKNOWN


def test_absent_evidence_abstains_rather_than_splitting():
    # These reach UNKNOWN via an empty INPUT. The other route -- noise subtraction emptying a
    # side -- is a different code path, covered only by the two noise-emptying tests below.
    assert _compare_locations("Palmerburgh", "") == UNKNOWN
    assert _compare_locations("Palmerburgh", "   ") == UNKNOWN
    assert _compare_locations("", "") == UNKNOWN


def test_noise_is_normalized_and_tokenized_not_used_raw():
    # The region is TWO words deliberately: with a one-word region a multi-word noise entry
    # strips nothing and the arity assertion could not pass against a correct implementation.
    # Raw `noise` yields a knob that silently does nothing -- it fails toward merge, which is
    # safe, but silently, which is the failure class this codebase engineers out.
    a, b = "Palmerburgh, North Clarke", "Clarkefurt, North Clarke"
    assert _compare_locations(a, b) == SAME                          # shares the region
    assert _compare_locations(a, b, {"North Clarke"}) == DIFFERENT   # arity: multi-word entry
    assert _compare_locations(a, b, {"NORTH CLARKE"}) == DIFFERENT   # case
    assert _compare_locations(a, b, {"north", "clarke"}) == DIFFERENT


def test_noise_as_a_bare_str_raises():
    # #5's `location_noise_words: Remote` (a YAML scalar, not a list) is an ordinary user error.
    # The key does not exist yet -- this guard is what lets #5 add it without re-deriving the shape.
    # Iterating a str yields single-letter tokens that strip nothing: inert, and silent.
    with pytest.raises(TypeError, match="not a str"):
        _compare_locations("Palmerburgh", "Clarkefurt", noise="Palmerburgh")


def test_bare_remote_versus_a_named_city_abstains_rather_than_splitting():
    # #119 -- supersedes the 2026-07-16 "accepted cost" decision this test used to pin (user
    # reconfirmed the reversal 2026-08-11 after the failure mode was reported for real: a role
    # advertised as remote-friendly, cross-posted to a board that instead records the employer's
    # HQ city, never clustered as a duplicate no matter how many times dedupe ran). "Remote" is
    # evidence of NO FIXED LOCATION, not evidence of a location that conflicts with a named city
    # -- so a side whose token set, after noise subtraction, is EXACTLY {"remote"} now abstains
    # (UNKNOWN) instead of actively splitting (DIFFERENT). Configuring "remote" as noise still
    # abstains the same way, unchanged.
    assert _compare_locations("Remote", "Palmerburgh") == UNKNOWN
    assert _compare_locations("Palmerburgh", "Remote") == UNKNOWN            # symmetric
    assert _compare_locations("Remote", "Palmerburgh", {"remote"}) == UNKNOWN


def test_remote_versus_remote_is_still_same():
    assert _compare_locations("Remote", "Remote") == SAME


def test_remote_with_a_country_is_unaffected_and_still_compares_normally():
    # THE regression the superseded decision's own comment warned a naive fix (a global noise
    # strip on "remote") would cause: "Remote, US" vs "Remote, UK" share only the "remote" token,
    # so stripping it unconditionally would turn a SAME pair into a SPLIT. This fix is narrower
    # than that -- it applies only when a side's token set is EXACTLY {"remote"} with nothing
    # else -- so a listing that names "remote" AND a country keeps comparing on ordinary overlap:
    # shared tokens still merge...
    assert _compare_locations("Remote, US", "Remote, UK") == SAME
    # ...and a genuine mismatch still splits, exactly as before -- "Remote, US" is more specific
    # than bare "Remote" and a real conflict there IS evidence of difference.
    assert _compare_locations("Remote, US", "Palmerburgh") == DIFFERENT


def test_noise_emptying_both_sides_abstains_rather_than_splitting():
    # The ONLY test reaching UNKNOWN via subtraction rather than empty input, and therefore the
    # only witness for moving the empty check BEFORE noise subtraction. That mutant returns
    # DIFFERENT here -- splitting two IDENTICAL locations, the worst verdict available.
    assert _compare_locations("Remote", "Remote", {"remote"}) == UNKNOWN


def test_multi_word_countries_sharing_a_token_merge_at_default():
    # The structural miss: 18 of the 30 real ones. Any two multi-word country names sharing a
    # token merge until that token is configured as noise. Merge direction, so it matches today.
    a, b = "Palmerburgh - North Clarke Republic", "Clarkefurt, North Clarke Kingdom"
    assert _compare_locations(a, b) == SAME
    assert _compare_locations(a, b, {"North Clarke"}) == DIFFERENT
