# sluice/core/stem.py
"""Porter (1980) suffix stripping, so JD keywords match evidence across word forms.

Hand-written because `sluice/` is standard-library only (CLAUDE.md), and because the
alternatives were measured and are worse: an ad-hoc suffix list is unprincipled
(`deployment` -> `deploym` but `deployments` -> `deplo`), and every common-prefix
threshold tried had false positives AND misses. See the #165 design's D7.

Certified against Martin Porter's published 23,531-word vocabulary at 100.0000%
(tests/data/porter_vocabulary.txt). That corpus buys a ONE-TIME validation of this
implementation against the reference; it is not drift detection of the reference.

Consumers: `cv/bundle.py:rank` and `core/doctor.py`. Deliberately NOT
`core/relevance.py`, whose keep/drop lists are a user-specified ingest gate applied
before dedup -- widening that match silently changes which leads are discarded, the
672ad2a failure.
"""
import re

_VOWELS = "aeiou"
_WORD_RE = re.compile(r"[a-z]+")


def _is_consonant(w, i):
    """Porter's definition: y is a consonant unless preceded by a consonant."""
    c = w[i]
    if c in _VOWELS:
        return False
    if c == "y":
        return i == 0 or not _is_consonant(w, i - 1)
    return True


def _measure(stem_):
    """Porter's m: the count of VC sequences in [C](VC)^m[V]."""
    s = "".join("c" if _is_consonant(stem_, i) else "v" for i in range(len(stem_)))
    m, i = 0, 0
    while i < len(s) and s[i] == "c":
        i += 1
    while i < len(s):
        while i < len(s) and s[i] == "v":
            i += 1
        if i >= len(s):
            break
        while i < len(s) and s[i] == "c":
            i += 1
        m += 1
    return m


def _has_vowel(stem_):
    return any(not _is_consonant(stem_, i) for i in range(len(stem_)))


def _double_consonant(w):
    return len(w) >= 2 and w[-1] == w[-2] and _is_consonant(w, len(w) - 1)


def _cvc(w):
    """*o: ends consonant-vowel-consonant where the last is not w, x or y."""
    if len(w) < 3:
        return False
    return (_is_consonant(w, len(w) - 3) and not _is_consonant(w, len(w) - 2)
            and _is_consonant(w, len(w) - 1) and w[-1] not in "wxy")


# Two entries here are the REFERENCE IMPLEMENTATION's documented departures from the
# 1980 paper, not transcription slips: `bli -> ble` stands in place of the paper's
# `abli -> able`, and `logi -> log` is absent from the paper entirely. Measured: without
# them, agreement with the published vocabulary is 99.932%, and every one of the 16
# failures is one of these two (`apology`, `assembly`, `horribly`, `possibly`, ...).
_STEP2 = [("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
          ("izer", "ize"), ("bli", "ble"), ("alli", "al"), ("entli", "ent"),
          ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
          ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
          ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
          ("logi", "log")]
_STEP3 = [("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
          ("ical", "ic"), ("ful", ""), ("ness", "")]
_STEP4 = ["al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
          "ment", "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize"]


def stem(word):
    """The Porter stem of one word. Lowercases; words of 2 letters or fewer are returned
    unchanged, as the algorithm specifies."""
    w = word.lower()
    if len(w) <= 2:
        return w
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]
    applied_1b = False
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed") and _has_vowel(w[:-2]):
        w, applied_1b = w[:-2], True
    elif w.endswith("ing") and _has_vowel(w[:-3]):
        w, applied_1b = w[:-3], True
    if applied_1b:
        if w.endswith(("at", "bl", "iz")):
            w += "e"
        elif _double_consonant(w) and w[-1] not in "lsz":
            w = w[:-1]
        elif _measure(w) == 1 and _cvc(w):
            w += "e"
    if w.endswith("y") and _has_vowel(w[:-1]):
        w = w[:-1] + "i"
    # Steps 2 and 3: the LONGEST matching suffix wins, never the first listed.
    for table in (_STEP2, _STEP3):
        best = None
        for suf, rep in table:
            if w.endswith(suf) and (best is None or len(suf) > len(best[0])):
                best = (suf, rep)
        if best and _measure(w[:-len(best[0])]) > 0:
            w = w[:-len(best[0])] + best[1]
    best4 = None
    for suf in _STEP4:
        if w.endswith(suf) and (best4 is None or len(suf) > len(best4)):
            best4 = suf
    if best4:
        candidate = w[:-len(best4)]
        if _measure(candidate) > 1 and (best4 != "ion" or candidate.endswith(("s", "t"))):
            w = candidate
    if w.endswith("e"):
        m = _measure(w[:-1])
        if m > 1 or (m == 1 and not _cvc(w[:-1])):
            w = w[:-1]
    if _measure(w) > 1 and _double_consonant(w) and w.endswith("l"):
        w = w[:-1]
    return w


def tokens(text):
    """Lowercased alphabetic runs. The tokeniser both sides of a match must share -- a
    keyword stemmed against an unstemmed haystack matches nothing."""
    return _WORD_RE.findall((text or "").lower())


def stem_all(text):
    """The set of stems in `text`. The comparable form for keyword matching."""
    return {stem(t) for t in tokens(text)}
