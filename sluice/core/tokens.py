"""The ONE tokeniser and term matcher the CV gate and `doctor` share (#364/#365/#368).

In core/, not cv/: `core/doctor.py` must answer the same questions the gate answers (does
this decoy match that tool?), and core/ may not import a sub-app. A second copy in doctor
is how the two would come to disagree -- the bug class this repo's #30 incident names.
"""
import re
import unicodedata

# The ONE tokeniser. `cv/validate.py` imports this rather than redefining it -- two copies
# let the vocabulary the gate BUILDS drift from the one it SEARCHES with.
#
# A dot is part of a token only BETWEEN alphanumerics, or leading one -- never trailing.
# The original `[A-Za-z0-9#+.]+` folded a sentence-final period into the token before it,
# and only `.` did that, which broke all three consumers at once (measured, on a bullet
# reading `Migrated to Examplestore3.` against `Skills: Examplestore3`):
#
#   row 1 / span removal -> the declared skill tokenises `Examplestore3` while the prose
#                           tokenises `Examplestore3.`, so the span never matches, the
#                           digit survives extraction, and the gate reports
#                           `INVENTED METRIC ['3']` on a name the user really declared.
#                           Same shape in PROFILE prose -> `INVENTED PROFILE METRIC 3`.
#   row 2               -> a skill sourced from an entry BODY that happens to end a
#                          sentence (`...ran on Example Widget.`) reads as `UNSOURCED
#                          SKILL`, which is false, and the only actionable answer is to
#                          delete a true skill.
#
# Both refusals are answerable only by deleting or corrupting true content -- the shape
# CLAUDE.md's LOCATION-field incident names -- and `S3`, `p99`, `OAuth2` and `Log4j` all
# sit in an ordinary bullet that ends with a full stop. The internal dot MUST survive:
# `Node.js` and `ASP.NET` are one token each, and splitting them would make a two-token
# needle that no `Skills:` value could match. The leading dot survives so `.NET` stays
# distinguishable from a bare `NET` (see `TOKEN_RULE_RE` below).
#
# `802.11ac` is deliberately NOT cited as an example here, though it has the same token
# shape: `TOKEN_RULE_RE` refuses it as a `Skills:` value outright (digit-leading), so it
# can never be a needle, and citing it would read as if this rule made it expressible. It
# is still governed by this pattern on the OTHER side -- as an EMITTED skill in a SKILLS
# section, row 2 matches it against source text, where it must stay one token or a body
# mentioning it could never source it. The two directions are not the same mechanism, and
# an example that only holds for one of them belongs with that one.
WORD_RE = re.compile(r"\.?[A-Za-z0-9#+]+(?:\.[A-Za-z0-9#+]+)*")

# EVERY TOKEN of a `Skills:` item must begin with a letter, or with a DOT then a letter.
# Span removal (cv/validate.py) makes this the first field that SUBTRACTS from the hard
# numeric gate, so an unconstrained value is a laundering path.
#
# PER TOKEN, and that is the whole guard: an ITEM-level check (`^[A-Za-z]` against the
# comma-separated item) accepts `Result 92`, because the item begins with `R` -- and removal
# then blanks `92` from every bullet citing the entry, which is the exact path this rule
# exists to close. A per-token rule refuses `Result 92`, `92x`, `120ms` and a bare `92`
# alike, while accepting `Example Widget3`, where the digit is INSIDE a letter-led token.
#
# The `\.?` admits `.NET` and `.NET Core`, which the letter-only rule refused outright --
# a shipped technology nobody could express, with no answer to the refusal but to misspell
# it. Safe on this rule's own terms: the harm it guards is a token whose DIGITS span removal
# would then blank, and a dot-then-letter token carries none. (`WORD_RE` above keeps a
# LEADING dot on a token for exactly this reason, while dropping a trailing one.)
#
# WHAT STAYS REFUSED, stated rather than implied, because the shapes are real and a user
# meeting one gets an error rather than a gap they can reason about: any token that leads
# with a DIGIT. `ISO 9001`, `Web 2.0`, `Section 508`, `3D modelling`, `5S` and `802.11ac`
# are all refused, and that is NOT a case this rule merely fails to reach -- it is
# structurally indistinguishable from the metric shorthand the rule exists to refuse
# (`Result 92` is the same shape as `ISO 9001`), so admitting one admits the other and
# re-opens the laundering path. A letter-led metric shorthand IS reachable and is a
# separate, stated residual (spec section 14): `p99` still licenses removing `99` for its
# own entry, and tightening further (two leading alphabetic characters) would kill
# legitimate short names.
TOKEN_RULE_RE = re.compile(r"^\.?[A-Za-z]")

# Sentence punctuation between two tokens ends a phrase, so a two-word decoy never matches
# across a sentence break ("at Example. Zephyr checks"). A dot INSIDE a token (`Node.js`)
# is part of the token and never reaches this set.
_BREAK = frozenset(".;:!?")


def tokens(text):
    return WORD_RE.findall(text or "")


def segments(text):
    """Runs of adjacent tokens as `(token, start, end)`, split at sentence punctuation."""
    text = text or ""
    out, cur, prev_end = [], [], None
    for m in WORD_RE.finditer(text):
        if prev_end is not None and any(c in _BREAK for c in text[prev_end:m.start()]):
            out.append(cur)
            cur = []
        cur.append((m.group(), m.start(), m.end()))
        prev_end = m.end()
    if cur:
        out.append(cur)
    return out


def _token_equal(hay, needle, *, case_sensitive, lower_accepts_capital):
    if not case_sensitive:
        return hay.casefold() == needle.casefold()
    if hay == needle:
        return True
    # A lowercase declared name (`coaching`) also accepts its sentence-initial capital;
    # a capitalised one (`Go`) never accepts a lowercase word (`go-live`).
    return (lower_accepts_capital and needle.islower()
            and hay == needle[:1].upper() + needle[1:])


def find_term(text, term, *, case_sensitive=False, lower_accepts_capital=False):
    """Every whole-term occurrence of `term` in `text`, as a tuple of TOKEN spans each.

    Whole-term means the term's token SEQUENCE inside one segment, with no alphanumeric
    character of ANY script touching either end -- so a name is never found inside a
    longer token, and an ASCII token run is never found inside a word that continues in
    another script. Token spans, not one span, because a caller that removes a match
    (figure scanning) must never remove the gap BETWEEN tokens, where a figure can sit.

    Residual, by design: only the two OUTER ends are checked for a touching alphanumeric.
    Whatever sits BETWEEN a multi-token term's tokens (a non-ASCII word, a comma, a
    newline) does not break the match, because the tokeniser drops it -- the full-width-figure
    case relies on that, and treating the gap as significant would hide it."""
    text = text or ""
    needle = tokens(term)
    if not needle:
        return []
    found = []
    for seg in segments(text):
        for i in range(len(seg) - len(needle) + 1):
            window = seg[i:i + len(needle)]
            if not all(_token_equal(h[0], n, case_sensitive=case_sensitive,
                                    lower_accepts_capital=lower_accepts_capital)
                       for h, n in zip(window, needle)):
                continue
            start, end = window[0][1], window[-1][2]
            if (start > 0 and text[start - 1].isalnum()) or (
                    end < len(text) and text[end].isalnum()):
                continue
            found.append(tuple((s, e) for _t, s, e in window))
    return found


def figures(text, *, remove=()):
    """The digit runs in `text`, each normalised to ASCII, after blanking `remove`.

    A digit is any character `unicodedata.digit` gives a value: full-width, Arabic-Indic
    and superscript alike, so a figure cannot dodge the gate by script. Blanked spans become
    spaces, so removing a name can never join two digit runs into a new figure."""
    chars = list(text or "")
    for start, end in remove:
        for i in range(start, end):
            chars[i] = " "
    out, run = set(), []
    for c in chars + [" "]:
        d = unicodedata.digit(c, None)
        if d is not None:
            run.append(str(d))
        elif run:
            out.add("".join(run))
            run = []
    return frozenset(out)


def tool_items(entry, field="Tools"):
    """The `Tools:` items one evidence entry declares; a blank value declares none.

    Raises on a value the gate could not safely use: an item with no name at all, or a
    token that leads with a digit (see TOKEN_RULE_RE)."""
    raw = (entry.get("fields") or {}).get(field, "") or ""
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        # An Obsidian list property (`Tools:` with `- a` lines) arrives as a YAML list.
        if not all(isinstance(p, str) for p in raw):
            raise ValueError(f"{field} must be text or a list of text items")
        parts = raw
    else:
        raise ValueError(f"{field} must be text or a list of text items, "
                         f"not {type(raw).__name__}")
    items = [t.strip() for t in parts if t.strip()]
    for item in items:
        toks = tokens(item)
        if not toks:
            raise ValueError(f"{field} item {item!r} is invalid: it contains no name at "
                             f"all -- leave {field}: blank instead")
        for tok in toks:
            if not TOKEN_RULE_RE.match(tok):
                raise ValueError(
                    f"{field} item {item!r} is invalid: every token must begin with a "
                    f"letter, or a dot then a letter -- {tok!r} does not. A digit-led name "
                    "is refused because span removal would let a figure vanish with it")
    return items


def decoy_problem(decoy):
    """Why a `fabrication_decoys` entry can never match, or None when it can.

    A decoy the tokeniser cannot fully see would be a configured ban that silently never
    fires: text that tokenises to nothing (another script), or a character the matcher
    drops (a hyphen, a `%`, an accented letter)."""
    if not isinstance(decoy, str) or not decoy.strip():
        return "is empty"
    covered = set()
    for m in WORD_RE.finditer(decoy):
        covered.update(range(m.start(), m.end()))
    if not covered:
        return "has no letters or digits the matcher can see"
    if any(not c.isspace() and i not in covered for i, c in enumerate(decoy)):
        return ("contains a character the matcher drops -- use letters, digits, '#', '+' "
                "and inner dots only (write a hyphen as a space)")
    return None


def validate_decoys(decoys):
    """Raise if any decoy can never match. Names each by POSITION, never by value: a decoy
    is often a name the user wants kept off their CV, and an error travels further than the
    config file it came from."""
    problems = [(i, p) for i, d in enumerate(decoys or ()) if (p := decoy_problem(d))]
    if problems:
        raise ValueError("cv.fabrication_decoys: "
                         + "; ".join(f"entry {i + 1} {p}" for i, p in problems))
