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
]
_PHRASE_RE = re.compile("(?i)(" + "|".join(re.escape(p) for p in _PHRASES) + ")")


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

    `allow` is matched case-insensitively against the matched STEM (lower-cased on
    both sides): cv.slop_allow entries are validated at config load (Task 11) to be
    members of _PHRASES, but the casing of a config entry vs. the text's casing of the
    same stem are independent, so both sides must be normalized before comparing.
    """
    lowered = {a.lower() for a in allow}
    out = []
    for lineno, line in lines:
        for m in _PHRASE_RE.finditer(line):
            if m.group(1).lower() not in lowered:
                out.append((lineno, m.group(1), line.strip()[:80]))
    return out


def check_text(text: str):
    """Back-compat wrapper: (hard errors, phrase warns over EVERY line, unscoped).

    Retained only for the fixture-cleanliness guards in tests/test_cv_slop.py,
    tests/test_cv_engine.py and tests/test_cv_parse.py, which use it to assert a
    fixture is slop-clean end to end. Production code reads check_hard/check_phrases
    directly so the engine can scope the phrase tier (see check_phrases above).
    """
    return check_hard(text), check_phrases(list(enumerate(text.splitlines(), 1)))
