"""The unbundled-term check (#194): a STYLE-tier detector for a term the composer invented.

A token in PROFILE prose or a WORK bullet that is SHAPED like a name and appears nowhere in
what the composer was shown (`cv/bundle.py::mention_vocab`) is reported. A finding drives
the composer's one retry and never bins a lead (#167's STYLE tier); it escalates to a
sign-off hold only under the opt-in `cv.style_hold`. Pure and import-light, like
`cv/slop.py` and `cv/voice.py` -- the engine owns which lines are scanned.

Design and measurement: docs/superpowers/specs/2026-10-02-unbundled-term-design.md.
"""
from sluice.cv.bundle import _WORD_RE
from sluice.cv.validate import _CITE_RE

# A token after one of these reads as sentence-initial, where a leading capital carries no
# signal. Punctuation-based on purpose: a capital after an abbreviation's period
# ("Example Ltd. Examplequery") is skipped -- an under-fire, the direction a retry-driving
# check should err.
_SENTENCE_END = (".", "!", "?", ":")
# Bullet markers stripped before tokenising. DEFENSIVE ONLY: the first-token rule in
# `candidates` already covers a leading marker (`_WORD_RE` never matches one and none is in
# `_SENTENCE_END`), so removing the strip changes nothing -- do not spend a witness on it.
_MARKERS = "-•*–— "


def candidates(line):
    """The tokens of `line` SHAPED like a name, in order.

    Citations are stripped with render's exact `_CITE_RE`, so the check sees what the
    reader sees; then a leading bullet marker (defensive only, as the
    first-token rule already covers it). Tokenised with `_WORD_RE`, the ONE tokeniser the
    #168 rows use (`cv/validate.py::_tokens` is its `findall`; positions are needed here).

    A candidate contains NO digit -- any digit belongs to the numeric gate, which already
    licenses or refuses `120ms`, `+15`, `#1` and `p99`; that class sank the morphology rule
    #168 measured and rejected -- and has at least one of:

    (i) an uppercase letter after its first character;
    (ii) a trailing `#` or `+`, on a token that also contains a letter -- `_WORD_RE`
         yields a lone `+` or `#` ("sales + support") as a token of its own, and with no
         letter it names nothing;
    (iii) a leading uppercase letter, when not sentence-initial.

    No arm for an all-caps token at sentence start: (i) already admits every all-caps token
    of two or more letters, and a separate arm was measured as an equivalent mutant.
    """
    text = _CITE_RE.sub("", line).lstrip(_MARKERS)
    out, first = [], True
    for m in _WORD_RE.finditer(text):
        tok = m.group()
        initial = first or text[:m.start()].rstrip().endswith(_SENTENCE_END)
        first = False
        if any(c.isdigit() for c in tok):
            continue
        if (any(c.isupper() for c in tok[1:])
                or (tok.endswith(("#", "+")) and any(c.isalpha() for c in tok))
                or (tok[:1].isupper() and not initial)):
            out.append(tok)
    return out


def unbundled_terms(lines, vocab):
    """`(line no, term, snippet)` for each candidate in `lines` absent from `vocab`.

    `lines` is the engine's scoped `(line no, text)` list; `vocab` is
    `cv/bundle.py::mention_vocab`'s case-folded set. A candidate is suppressed when its
    case-folded form, or that form with ONE trailing `s` removed, is in `vocab` -- the fold
    can only SUPPRESS, so it cannot widen what is reported. Reported once per (line, term).

    Fails loudly on a wrong `vocab` shape, naming the type and never the value: a `str`
    would answer `in` by SUBSTRING and silently suppress every finding, which reads exactly
    like a clean CV.
    """
    if not isinstance(vocab, (set, frozenset)):
        raise TypeError(f"unbundled_terms() takes a set of case-folded str, not "
                        f"{type(vocab).__name__} -- build it with "
                        "cv.bundle.mention_vocab(bundle)")
    bad = next((t for t in vocab if not isinstance(t, str)), None)
    if bad is not None:
        raise TypeError(f"unbundled_terms() takes a set of str, but it holds a "
                        f"{type(bad).__name__} -- build it with "
                        "cv.bundle.mention_vocab(bundle)")
    found = []
    for ln, line in lines:
        seen = set()
        for tok in candidates(line):
            folded = tok.casefold()
            if (tok in seen or folded in vocab
                    or (folded.endswith("s") and folded[:-1] in vocab)):
                continue
            seen.add(tok)
            found.append((ln, tok, line.strip()[:50]))
    return found
