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


def check_text(text: str):
    errors, warns = [], []
    for i, line in enumerate(text.splitlines(), 1):
        for label, rx in HARD:
            if rx.search(line):
                errors.append((i, label, line.strip()[:80]))
        for m in _PHRASE_RE.finditer(line):
            warns.append((i, m.group(1), line.strip()[:80]))
    return errors, warns
