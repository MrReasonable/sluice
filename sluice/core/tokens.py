"""The ONE tokeniser and term matcher the CV gate and `doctor` share (#364/#365/#368).

In core/, not cv/: `core/doctor.py` must answer the same questions the gate answers (does
this decoy match that tool?), and core/ may not import a sub-app. A second copy in doctor
is how the two would come to disagree -- the bug class this repo's #30 incident names.
"""
import functools
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

# Sentence punctuation FOLLOWED BY WHITESPACE between two tokens ends a phrase (spec §6.1), so
# a two-word decoy never matches across a sentence break ("at Example. Zephyr checks") yet
# still matches "Example;Zephyr", where nothing separates the words on the page. A dot
# INSIDE a token (`Node.js`) is part of the token and never reaches this set.
_BREAK = frozenset(".;:!?")


def tokens(text):
    return WORD_RE.findall(text or "")


def _breaks(gap):
    return any(c in _BREAK and gap[i + 1:i + 2].isspace() for i, c in enumerate(gap))


def segments(text):
    """Runs of adjacent tokens as `(token, start, end)`, split at sentence punctuation."""
    text = text or ""
    out, cur, prev_end = [], [], None
    for m in WORD_RE.finditer(text):
        if prev_end is not None and _breaks(text[prev_end:m.start()]):
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
    case relies on that, and treating the gap as significant would hide it.

    Residual, by the spec's rule and not an oversight: tool ATTRIBUTION is case-sensitive
    (`case_sensitive=True` in cv/validate.py::check_selection), so a tool written in a
    different case from its declared spelling is not attributed at all."""
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


# Characters that may separate a thousands group: comma, ASCII space, NBSP, narrow NBSP and
# thin space. Any other space between digits is refused upstream (cv/reply.py), so it never
# needs a reading here.
_GROUP_SEPS = frozenset(",  \u00a0\u202f\u2009")


def _digit(seq, i):
    return unicodedata.digit(seq[i], None) if 0 <= i < len(seq) else None


def group_reading(seq, i):
    """How the separator at `seq[i]` is read: "group" when it separates a thousands group,
    "label" when it is in the group shape but the run before it is a lone letter's label
    digit (read as two figures), else None. The ONE rule for every separator (comma, ASCII
    space, NBSP, U+202F, U+2009), used by figures() to read a number and by cv/reply.py to
    judge the model's text, so the two cannot disagree on any separator.

    The shape: `seq[i]` is a group separator; 1-3 digits before it; exactly 3 digits after
    it, then no digit. And the run before it must be able to head (or continue) a number:
      - not the fraction of a decimal: a run after `.` never groups ("1.5 100-node" is 1.5
        and 100);
      - not after a comma that is not itself a group ("3,8 000"): a comma decimal is no
        group's head either -- while "1,000 000" still chains, its comma being a group;
      - "label", not "group" (space separators only), when glued to ONE letter that follows no other letter (`Q3`,
        `p99`, `v2`): a lone letter on a digit run is overwhelmingly a label, so "In Q3 120
        customers" reads as 3 and 120 whatever the space. Two or more letters (`USD3`,
        `GBP3`) are a currency or unit code on the number itself, so "USD3 100" groups like
        any other number.

    The label exemption is for SPACE separators only: a comma group is read as one number
    whatever letter precedes the run ("R10,000", "x1,000"), since a comma is never
    ambiguous and figures() already joins it.

    Accepted residual: a lone-letter prefix on a number that IS grouped by a SPACE ("x1 000",
    "a multiple of x1 000", and equally "R12<NBSP>500" with NBSP, U+202F or U+2009) is read
    as two figures, and cv/reply.py does not refuse any such spelling -- the letter rule
    cannot tell that from `Q3 120`.

    An ASCII space is the AMBIGUOUS separator ("Led 3 100-person teams" is three teams or
    3100), so figures() reads an ASCII-space group both ways and cv/reply.py refuses one in
    the model's text; the other separators are unambiguous and read joined only."""
    if not isinstance(seq, str):
        seq = "".join(seq)
    if not (0 <= i < len(seq) and seq[i] in _GROUP_SEPS):
        return None
    return _readings(seq).get(i)


@functools.lru_cache(maxsize=4)
def _readings(text):
    """{separator index: "group" | "label"} for every separator in `text`, in ONE left to
    right pass. A comma chain ("1,000,000,...") makes each link's answer depend on the
    link before it, so answering one separator by walking back along the chain made every
    caller's sweep quadratic (a long chain from a vault entry, which has no size cap). The
    answer to the earlier link is already in `out`, so each separator is judged from a
    bounded look at its neighbours: linear in the text, however long the chain."""
    out = {}
    for i, c in enumerate(text):
        if c not in _GROUP_SEPS:
            continue
        before = 0
        while before < 4 and _digit(text, i - 1 - before) is not None:
            before += 1
        if not (1 <= before <= 3 and all(_digit(text, i + k) is not None for k in (1, 2, 3))
                and _digit(text, i + 4) is None):
            continue
        head = i - 1 - before
        if head < 0:
            out[i] = "group"
        elif text[head] == ".":
            continue
        elif text[head] == ",":
            # A comma head must itself be a group ("3,8 000" is not one), and by now its
            # own answer is recorded.
            if out.get(head) == "group":
                out[i] = "group"
        # A comma is never ambiguous, so only a SPACE separator can be a label's gap.
        elif (c != "," and unicodedata.category(text[head]).startswith("L")
                and not (head > 0 and unicodedata.category(text[head - 1]).startswith("L"))):
            out[i] = "label"
        else:
            out[i] = "group"
    return out


def figures(text, *, remove=()):
    """The figures in `text`, each normalised to ASCII, after blanking `remove`.

    A digit is any character `unicodedata.digit` gives a value: full-width, Arabic-Indic
    and superscript alike, so a figure cannot dodge the gate by script. One figure is a run
    of 1-3 digits followed by groups of (a group separator + exactly 3 digits), not followed
    by another digit, with the separators removed (`50,000`, `1,000,000`, `10<NBSP>000`);
    group_reading() decides every separator, and a space it does not read as "group"
    separates figures
    (`Q3 2023`). An ASCII-space group is AMBIGUOUS ("Led 3 100-person teams"), so it
    contributes BOTH readings: the joined figure and each run on its own (`10 000` gives
    10000, 10 and 000). This loop consults group_reading() at every separator after every
    digit run it reads, so it reads a number twice exactly where group_reading() gives an
    ASCII space "group" -- the same positions cv/reply.py refuses in the model's text. On the entry side
    the two readings license either one the user may have meant; on the bullet side the
    refusal means no bullet relies on them. Digits joined by a single `.` or `,` that is
    not a group are one figure too (`8.3`; `3,8` reads 38), `.` kept and `,` dropped.

    Blanked spans become spaces, and a ONE-character span between two digits would become an
    ASCII space that can then group them ("3X100" blanked at X reads 3100). That is
    unreachable through cv/validate.py::check_selection, the one caller passing `remove`: its
    spans come from find_term(), which never matches a name glued to a digit on either side
    (the tokeniser keeps an ASCII-glued name inside one longer token, and find_term's
    touching-alphanumeric check refuses one glued to a digit of another script).

    Accepted residuals, stated rather than hidden: a comma-decimal ("2,5x") reads as the
    integer 25 here, and cv/reply.py is what refuses it; decimals match EXACTLY, so "3.50"
    does not license "3.5" (a refusal); and a declared tool ending in a digit can split a
    comma figure (Examplelang3,8), since blanking the name leaves the 8 alone."""
    chars = list(text or "")
    for start, end in remove:
        for i in range(start, end):
            chars[i] = " "
    n = len(chars)
    joined = "".join(chars)  # group_reading memoises per string, so one string for the pass

    def digit_at(i):
        return unicodedata.digit(chars[i], None) if i < n else None

    def read(i):
        out = []
        while digit_at(i) is not None:
            out.append(str(digit_at(i)))
            i += 1
        return "".join(out), i

    found, i = set(), 0
    while i < n:
        if digit_at(i) is None:
            i += 1
            continue
        first, i = read(i)
        # The runs split at ASCII-space groups only: the second reading of an ambiguous
        # number. Any other group, and a `.`/`,` join, extends the current run.
        # Parts are collected and joined ONCE: `+=` on a growing string inside this loop
        # made a long comma/NBSP chain super-linear.
        whole, runs = [first], [[first]]
        while i < n:
            if group_reading(joined, i) == "group":
                group, nxt = read(i + 1)
                if chars[i] == " ":
                    runs.append([group])
                else:
                    runs[-1].append(group)
            # A single `.` or `,` BETWEEN digits joins them, so `8.3` is not the two
            # licensed figures 8 and 3. A trailing one (sentence end) never joins.
            elif chars[i] in ".," and digit_at(i + 1) is not None:
                part, nxt = read(i + 1)
                group = ("." if chars[i] == "." else "") + part
                runs[-1].append(group)
            else:
                break
            whole.append(group)
            i = nxt
        found.add("".join(whole))
        if len(runs) > 1:
            found.update("".join(r) for r in runs)
    return frozenset(found)


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
