# sluice/cv/slop.py
"""Deterministic slop linter. Em-dash (U+2014) and literal '--' are ERRORS (HARD
block). En-dash (U+2013) is fine in date ranges. AI-tell phrases are advisory warns."""
import re

HARD = [("EM-DASH", re.compile("\u2014")), ("DOUBLE-HYPHEN-DASH", re.compile(r"--"))]
_PHRASES = [
    "spearhead", "leverage", "foster", "in order to", "it's worth noting", "delve",
    "tapestry", "underscore", "seamless", "cutting-edge", "game-chang", "elevate",
    "realm", "testament to", "boasts", "world-class", "unlock", "empower",
    "streamline", "furthermore", "moreover", "showcasing", "meticulous", "plethora",
    "myriad", "pivotal", "embark", "wealth of experience", "proven track record",
    "results-driven", "detail-oriented", "team player", "synergy", "holistic",
    "not just", "passionate about", "best-in-class", "dive into",
    "at the end of the day", "needle-mov",
    # #167 (Task 17): compose.py's prompt banned this in prose while this list never
    # enforced it -- banned in prose, unchecked in code, nothing keeping the two in
    # step. Low-risk to add: the STYLE tier only HOLDS (never blocks) and
    # cv.style_hold is off by default.
    "drove",
]


def _stem_pattern(stem):
    """Compile ONE stem's own case-insensitive alternation: its literal text, plus --
    for a stem ENDING IN 'e' -- its e-dropped '-ing' inflection.

    Measured against this shipped list while reviewing Task 12 (#167, Task 17): the
    single combined-alternation regex this replaced did LITERAL substring matching,
    so a stem ending in 'e' matched every OTHER inflection ("leverage" is a substring
    of "leveraged") but never its own gerund -- English drops the terminal 'e' before
    adding '-ing' ("leverage" -> "leveraging", never "leverageing"), so "leverage" is
    not a substring of "leveraging". A stem NOT ending in 'e' needs no second branch:
    its '-ing' form already contains it as a literal substring ("foster" in
    "fostering"), which is also why "needle-mov" and "game-chang" above are
    deliberately truncated rather than spelled with a trailing 'e' -- they already
    catch every inflection by construction.
    """
    alts = [re.escape(stem)]
    if stem.endswith("e"):
        alts.append(re.escape(stem[:-1] + "ing"))
    return re.compile("(?i)(" + "|".join(alts) + ")")


# One compiled pattern PER STEM, not one combined alternation (see check_phrases:
# reporting which STEM matched -- not the matched substring itself -- is what makes
# cv.slop_allow's stem-keyed suppression correctly cover every inflection a pattern
# here catches, the '-ing' form included).
_PHRASE_PATTERNS = [(stem, _stem_pattern(stem)) for stem in _PHRASES]

# Stems that have LEFT `_PHRASES`, mapped to what replaced them ("" = removed outright).
#
# #181. `cv.slop_allow` is validated by membership against `_PHRASES` and RAISES on a
# miss, which is right -- a typo'd entry is otherwise SILENTLY inert, and the style hold
# it was meant to suppress just recurs forever with nothing pointing at the entry. But
# that raise quietly made `_PHRASES` a config compatibility surface: the day a stem is
# renamed, a working `sluice.yaml` stops loading and every `cv` command dies, over a lint
# heuristic the user has no stake in.
#
# This is the graveyard that keeps a rename from doing that, and it is deliberately NOT
# the same shape as `plugins._RETIRED`, which still RAISES. The difference is whether a
# substitution is safe. Accepting a retired ADAPTER name would run an implementation the
# user did not select -- there is no safe substitute for it. A retired STEM has an exact
# one: the same suppression under a new spelling, so accepting it is a correct migration
# rather than a quiet wrong default. The raise is a typo catcher here, not a correctness
# requirement, and a typo is still caught because it matches NEITHER table.
#
# `_PHRASES` therefore stays private and stays tunable. It must track model-output drift
# (#167 added `drove` in its own PR), and versioning a lint heuristic would ossify exactly
# the thing that needs to keep moving. `tests/test_slop_phrase_retirement.py`
# ratchets the set so a
# REMOVAL cannot land without an entry here.
_RETIRED_PHRASES: dict[str, str] = {}


def check_hard(text: str):
    """The BLOCKING tier: em dash and literal '--', over the WHOLE document.

    Deliberately UNSCOPED, unlike check_phrases below -- an em dash in an employer
    line is always fixable without inventing anything (retype the dash), so there is
    no reason to exempt any line from it. A phrase complaint about the same employer
    line would only be answerable by renaming the employer, which is why that check
    (check_phrases) instead takes a caller-scoped subset of lines.
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for label, rx in HARD:
            if rx.search(line):
                out.append((i, label, line.strip()[:80]))
    return out


def check_phrases(lines, *, allow=()):
    """The STYLE tier, over the (lineno, text) pairs the caller chose to scope it to.

    Takes LINES rather than a document, deliberately: this module stays pure and
    dependency-free (stdlib `re` only), and the PROFILE/WORK scoping that keeps this
    check off employer/fact lines lives in cv/engine.py, which already owns the tier
    policy and the `slop_allow` list. Importing cv/validate.py here to do that split
    would invert the layering for no gain -- this function has NO opinion about which
    lines it receives; it matches whatever it is handed. See tests/test_cv_slop.py for
    why handing it an unscoped line (e.g. an employer name) would be a caller bug, not
    a bug here.

    `allow` is matched case-insensitively against the REPORTED stem (lower-cased on
    both sides): cv.slop_allow entries are validated at config load (Task 11) to be
    members of _PHRASES, but the casing of a config entry vs. the text's casing of the
    same stem are independent, so both sides must be normalized before comparing.

    Reports the STEM that matched, never the matched TEXT (#167, Task 17) -- e.g. an
    "-ing" hit on the "leverage" pattern is reported as "leverage", not "leveraging".
    This is what makes `allow` correctly suppress every inflection a pattern here
    catches: `slop_allow` entries are STEMS by construction (the config-load
    validation above), so comparing stem-to-stem is trivially correct, whereas
    comparing the ALLOWED stem against the matched TEXT would mean `slop_allow:
    ["leverage"]` fails to suppress a "leveraging" hit.
    """
    lowered = {a.lower() for a in allow}
    out = []
    for lineno, line in lines:
        for stem, rx in _PHRASE_PATTERNS:
            if stem.lower() in lowered:
                continue
            for _m in rx.finditer(line):
                out.append((lineno, stem, line.strip()[:80]))
    return out


def check_text(text: str):
    """Back-compat wrapper: (hard errors, phrase warns over EVERY line, unscoped).

    Retained only for the fixture-cleanliness guards in tests/test_cv_slop.py,
    tests/test_cv_engine.py and tests/test_cv_parse.py, which use it to assert a
    fixture is slop-clean end to end. Production code reads check_hard/check_phrases
    directly so the engine can scope the phrase tier (see check_phrases above).
    """
    return check_hard(text), check_phrases(list(enumerate(text.splitlines(), 1)))
