"""Read the composer's reply: find its JSON object and check its shape (#364/#365/#368).

Pure, and it never raises: every problem becomes a `REPLY:` finding the engine feeds to its
single retry, naming the field so the model can fix exactly that. The text this module
accepts is exactly the text that renders -- nothing downstream strips or rewrites it.
"""
import json
import re
import unicodedata
from dataclasses import dataclass

from sluice.core.protocols import SECTION_HEADINGS
from sluice.core.safeout import is_control
from sluice.core.tokens import group_reading

# The placeholders the prompt's JSON shape uses (cv/compose.py). `_prefix` can produce
# none of them, so a reply still carrying one is a backend echoing the example back.
PLACEHOLDERS = ("<profile>", "<slot>", "<bullet>", "<id>", "<skill from the list>")
_FENCE_RE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.S)
_HEADINGS = frozenset(h.casefold() for h in SECTION_HEADINGS)


@dataclass(frozen=True)
class Bullet:
    text: str
    cites: tuple


@dataclass(frozen=True)
class Reply:
    profile: str
    roles: dict           # slot id (the prompt's spelling) -> tuple[Bullet, ...]
    skills: tuple
    skills_malformed: bool = False


class _DuplicateKey(ValueError):
    def __init__(self, key):
        super().__init__(key)
        self.key = key


def _no_duplicates(pairs):
    # json.loads keeps the LAST of two equal keys silently; a reply carrying two `roles`
    # would render one of them unchecked by intent. Refuse instead.
    out = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


# A composed CV reply is a few KB (a profile, a handful of bullets per role, a skills list),
# so 60k characters is an order of magnitude above any real one and below the size where a
# hostile reply could make the scan costly. 80 KB of "{" once cost gigabytes.
_MAX_REPLY_CHARS = 60_000
# A JSON object can only begin `{`, optional whitespace, then `"` or `}`; any other brace is
# skipped without a decode attempt.
_OBJECT_START_RE = re.compile(r'\{\s*["}]')
# Every failed raw_decode builds a JSONDecodeError that counts lines up to its position, so
# the number of attempts must be bounded for the scan to be. A genuine reply has a handful
# of candidates (the fences plus the odd fragment in chat); this is far above that.
_MAX_DECODE_ATTEMPTS = 64
_TOO_DEEP = "REPLY: the reply is nested too deeply -- reply with one flat JSON object"


def _used_strings(obj):
    """Every string `parse_reply` reads, reached without recursing into anything else, so
    a hostile extra key cannot cost stack depth and no unread field can veto a reply."""
    if not isinstance(obj, dict):
        return
    if isinstance(obj.get("profile"), str):
        yield obj["profile"]
    roles = obj.get("roles")
    if isinstance(roles, dict):
        for bullets in roles.values():
            if isinstance(bullets, list):
                for b in bullets:
                    if isinstance(b, dict):
                        if isinstance(b.get("text"), str):
                            yield b["text"]
                        if isinstance(b.get("cites"), list):
                            yield from (c for c in b["cites"] if isinstance(c, str))
    skills = obj.get("skills")
    if isinstance(skills, list):
        yield from (s for s in skills if isinstance(s, str))


def _has_placeholder(obj):
    return any(p in s for s in _used_strings(obj) for p in PLACEHOLDERS)


def _candidates(text, decoder):
    """Yield decoded dicts: fenced blocks first, then each plausible object start.

    Never slices a suffix per brace: `raw_decode(text, i)` reads in place, and after a
    successful decode the scan resumes past that object's span, so an object nested in
    another is not rediscovered. Work is bounded by `_MAX_REPLY_CHARS` (checked by the
    caller), the `_OBJECT_START_RE` prefilter and `_MAX_DECODE_ATTEMPTS`, not by luck. A
    duplicate key is yielded as the _DuplicateKey itself so the caller decides."""
    attempts = 0
    for m in _FENCE_RE.finditer(text):
        inner = m.group(1).strip()
        if not inner.startswith("{"):
            continue
        attempts += 1
        if attempts > _MAX_DECODE_ATTEMPTS:
            return
        try:
            obj, _end = decoder.raw_decode(inner)
        except _DuplicateKey as e:
            yield e
        except ValueError:
            pass
        else:
            if isinstance(obj, dict):
                yield obj
    m = _OBJECT_START_RE.search(text)
    while m is not None and attempts < _MAX_DECODE_ATTEMPTS:
        attempts += 1
        pos = m.start()
        try:
            obj, end = decoder.raw_decode(text, pos)
        except _DuplicateKey as e:
            yield e
            end = pos + 1
        except ValueError:
            end = pos + 1
        else:
            if isinstance(obj, dict):
                yield obj
        m = _OBJECT_START_RE.search(text, end)


def extract_json(text):
    """The reply's JSON object, or a list of REPLY findings.

    Tries each fenced block, then each `{` in the text. Prefers the first object carrying
    both `profile` and `roles` and no example placeholder, so a stray `{}` or an echoed
    fragment of the prompt's shape in the chat before the reply cannot beat it; an echo is
    returned only when nothing cleaner exists, so it reaches the placeholder finding."""
    text = text or ""
    if len(text) > _MAX_REPLY_CHARS:
        return ["REPLY: the reply is too long -- reply with one short JSON object"]
    try:
        stripped = text.strip()
        if stripped.startswith("["):
            try:
                if isinstance(json.loads(stripped), list):
                    return ["REPLY: the reply must be one JSON object, not a list"]
            except ValueError:
                pass
        decoder = json.JSONDecoder(object_pairs_hook=_no_duplicates)
        full, partial, duplicate = [], None, None
        for cand in _candidates(text, decoder):
            if isinstance(cand, _DuplicateKey):
                duplicate = duplicate or cand
            elif "profile" in cand and "roles" in cand:
                if not _has_placeholder(cand):
                    return cand
                full.append(cand)
            elif partial is None and ("profile" in cand or "roles" in cand):
                partial = cand
    except RecursionError:
        return [_TOO_DEEP]
    if full:
        return full[0]
    # A duplicate inside a chat fragment must not refuse a valid reply, so it is reported
    # only when no candidate carried the full shape.
    if duplicate is not None:
        return [f"REPLY: duplicate key {duplicate.key!r} -- give each key once"]
    if partial is not None:
        return partial
    return ["REPLY: no JSON object in the reply -- reply with one JSON object and nothing else"]


# Characters that render as nothing yet pass is_control, and between two digits split a
# figure the PDF shows whole. Covered: category Cf; the default-ignorables that are not Cf
# (listed below); and category Cn, which takes in the unassigned default-ignorables
# (U+2065, U+FFF0-U+FFF8, unassigned points in U+E0000-U+E0FFF). No real CV text uses an
# unassigned code point, so refusing Cn costs nothing. Not covered: assigned visible or
# whitespace characters, which render and so cannot hide a split.
_EXTRA_INVISIBLE = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
                    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0),
                    (0xE0100, 0xE01EF))


def _is_invisible(c):
    return unicodedata.category(c) in ("Cf", "Cn") or any(lo <= ord(c) <= hi
                                                  for lo, hi in _EXTRA_INVISIBLE)


# The micro sign and Greek mu, allowed as the first letter of a word only (see _lookalike).
_UNIT_MU = frozenset("\u00b5\u03bc")


def _lookalike(text):
    """The first character that lets a word dodge a whole-term match, or None.

    A LETTER whose NFKC form is ASCII letters (full-width Latin, mathematical alphanumerics,
    the fi ligature), or a letter of any non-Latin script inside a word that also holds
    Latin. Not
    every NFKC change: the micro sign, NBSP, an ellipsis, a trademark sign, m2 and a
    decomposed accent are faithful copies of evidence and never hide a name."""
    for c in text:
        if unicodedata.category(c).startswith("L"):
            folded = unicodedata.normalize("NFKC", c)
            if folded != c and folded.isascii() and folded.isalpha():
                return c
    for word in re.findall(r"[^\W\d_]+", text):
        # The micro sign and Greek mu are allowed only as a word's FIRST letter (a unit
        # prefix: "200\u00b5s" is digits then "\u00b5s"), so "K\u00b5becorp" is refused.
        # Any other non-Latin letter beside a Latin one is refused, whatever its script.
        # Accepted residual: Latin small capitals (U+1D0F etc.) are Latin script and
        # NFKC-stable, so a word using them is not seen.
        letters = word[1:] if word[0] in _UNIT_MU else word
        # `\w` also matches a superscript or other No character (m\u00b2): only letters count.
        names = [(c, unicodedata.name(c, "").split(" ", 1)[0]) for c in letters
                 if unicodedata.category(c).startswith("L")]
        if any(n == "LATIN" for _c, n in names):
            for c, n in names:
                if n != "LATIN":
                    return c
    return None


# Between two digits these read as what they show: a decimal or a group (. ,), a range
# (- and the en dash, "Hired 3\u20135 engineers"), a date (/) or a ratio (:). figures() reads
# the digits either side of a range, date or ratio as separate figures, which is the
# reading the page gives too.
_DIGIT_SEPARATORS = frozenset(".,-/:\u2013")


def _is_digit(text, i):
    return 0 <= i < len(text) and unicodedata.digit(text[i], None) is not None


def _bad_comma(text):
    """A comma between two digits that core/tokens.py::group_reading does not read as a
    group ("2,5x"): figures() would read 25, the PDF shows two and a half. A comma after a
    letter's digit ("Q3,120", "R10,000") is a group like any other: both sides read 3120."""
    return any(c == "," and _is_digit(text, i - 1) and _is_digit(text, i + 1)
               and group_reading(text, i) != "group" for i, c in enumerate(text))


def _odd_separator(text):
    """A lone character between two digits that figures() cannot join, or None.

    "8\u00b7" + "3" shows the PDF one number while figures() reads 8 and 3, both licensed
    separately. Allowed: alphanumerics, _DIGIT_SEPARATORS, ASCII space (an ambiguous
    ASCII-space group is _space_group's to refuse) and any other whitespace only where
    core/tokens.py::group_reading reads it as a group ("10\u00a0000") or a label's gap
    ("Q3\u00a0120", read as 3 and 120, as the page shows it). A circled digit is
    not refused here: unicodedata.digit gives it a value, so figures() already reads it."""
    for i in range(1, len(text) - 1):
        c = text[i]
        if not (_is_digit(text, i - 1) and _is_digit(text, i + 1)):
            continue
        if c.isspace():
            # ASCII space always passes; other whitespace only as a group or a label's gap.
            if c != " " and group_reading(text, i) is None:
                return c
        elif not c.isalnum() and c not in _DIGIT_SEPARATORS:
            return c
    return None


def _space_group(text):
    """The first ASCII-space-grouped number in `text` (as ASCII digits), or None.

    "Led 3 100-person teams" is three teams or three thousand one hundred, and the gate
    cannot know which the model meant, so core/tokens.py::figures reads the shape both
    ways and the model's text may not show it at all. It refuses an ASCII space exactly
    where core/tokens.py::group_reading reads "group", the same test figures() applies
    before reading a number twice. Other adjacency stays allowed: "Q3 2023"
    and "In 2023 100 engineers" (a four-digit run never heads a group), "Grew 1.5
    100-node" (a decimal's fraction never heads one), "In Q3 120 customers" (a lone
    letter's digit is read as a label -- group_reading states the "x1 000" residual)."""
    for i in range(len(text)):
        if text[i] != " " or group_reading(text, i) != "group":
            continue
        start = i
        while (_is_digit(text, start - 1)
               or group_reading(text, start - 1) == "group"):
            start -= 1
        end = i
        while group_reading(text, end) == "group":
            end += 4
        return "".join(str(unicodedata.digit(c)) if _is_digit(text, k) else c
                       for k, c in enumerate(text[start:end], start))
    return None


def _text_findings(where, text):
    if "[" in text or "]" in text:
        return [f'REPLY: {where} contains a bracket -- put entry ids in "cites", never in '
                "the text"]
    if any(is_control(c) for c in text):
        return [f"REPLY: {where} contains a line break or control character -- write it as "
                "one line"]
    # Invisible format characters (zero-width space, joiners, bidi overrides, soft hyphen)
    # pass is_control, and one between two digits splits a figure the PDF shows whole:
    # "2<ZWSP>5" renders as 25 while core/tokens.py::figures reads 2 and 5.
    if any(_is_invisible(c) for c in text):
        return [f"REPLY: {where} contains an invisible formatting character -- write plain "
                "text"]
    # A number written without 0-9 (a circled or Roman numeral, a CJK numeral, a vulgar
    # fraction) has no digit value, so core/tokens.py::figures cannot see it.
    # Only Nl/No: a CJK ideograph (Lo) is a letter, so an employer name holding one passes.
    if any(unicodedata.category(c) in ("Nl", "No") and unicodedata.digit(c, None) is None
           for c in text):
        return [f"REPLY: {where} writes a number without digits -- write numbers with the "
                "digits 0-9"]
    if _bad_comma(text):
        return [f"REPLY: {where} writes a decimal with a comma -- use a point, e.g. 2.5"]
    sep = _odd_separator(text)
    if sep is not None:
        return [f"REPLY: {where} separates digits with U+{ord(sep):04X} -- use . or , between "
                "digits"]
    shown = _space_group(text)
    if shown is not None:
        return [f"REPLY: {where} writes {shown} with a space -- write "
                f"{shown.replace(' ', ',')} if it is one number, or reword so two numbers are "
                "not side by side"]
    # Spacing and a trailing colon do not make a heading content.
    if " ".join(text.split()).rstrip(": ").casefold() in _HEADINGS:
        return [f"REPLY: {where} is a section heading, not content"]
    # Full-width forms and other compatibility look-alikes, or one word mixing Latin with
    # any other script, dodge the whole-term matches (MISATTRIBUTED TOOL, FABRICATED). A word
    # wholly in one script is legitimate text and is left alone.
    bad = _lookalike(text)
    if bad is not None:
        return [f"REPLY: {where} uses a look-alike character U+{ord(bad):04X} -- write it as "
                "a plain letter"]
    return []


def _placeholder_findings(obj):
    found = {p for text in _used_strings(obj) for p in PLACEHOLDERS if p in text}
    return [f"REPLY: the reply still carries the example placeholder {p} -- replace it "
            "with real content" for p in sorted(found)]


def parse_reply(obj, slot_ids):
    """A typed Reply, or REPLY findings naming each problem's field.

    `skills` alone is never refused: a malformed list is flagged and dropped, because the
    SKILLS section is framing-grade and framing never costs a lead (spec §5.2)."""
    if not isinstance(obj, dict):
        return ["REPLY: the reply must be one JSON object"]
    findings = _placeholder_findings(obj)
    profile = obj.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        findings.append("REPLY: profile missing or empty -- give a 2 to 3 sentence profile")
        profile = ""
    else:
        findings += _text_findings("profile", profile)
    # Matched case-insensitively: a model writing "r1" for R1 has made no real mistake.
    by_fold = {s.casefold(): s for s in slot_ids}
    roles, seen = {}, {}
    raw_roles = obj.get("roles")
    if not isinstance(raw_roles, dict):
        findings.append('REPLY: "roles" missing or not an object -- map each slot id to its '
                        "bullets")
    else:
        for key, bullets in raw_roles.items():
            slot = by_fold.get(str(key).strip().casefold())
            if slot is None:
                findings.append(f"REPLY: unknown slot {key!r} -- use only the slot ids given")
                continue
            if slot in seen:
                findings.append(f"REPLY: slot {slot} given twice (as {seen[slot]!r} and "
                                f"{key!r}) -- give each slot once")
                continue
            seen[slot] = key
            if not isinstance(bullets, list):
                findings.append(f"REPLY: {slot} must be a list of bullets")
                continue
            kept = []
            for n, b in enumerate(bullets, 1):
                where = f"{slot} bullet {n}"
                if not isinstance(b, dict):
                    findings.append(f'REPLY: {where} must be an object with "text" and "cites"')
                    continue
                text, cites = b.get("text"), b.get("cites")
                if not isinstance(text, str) or not text.strip():
                    findings.append(f"REPLY: {where} has no text")
                    continue
                if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
                    findings.append(f'REPLY: {where} "cites" must be a list of entry ids')
                    continue
                bad = _text_findings(where, text)
                if bad:
                    findings += bad
                    continue
                kept.append(Bullet(text.strip(), tuple(c.strip() for c in cites)))
            roles[slot] = tuple(kept)
    raw_skills = obj.get("skills")
    skills, malformed = (), False
    if raw_skills is not None:
        if isinstance(raw_skills, list) and all(isinstance(s, str) for s in raw_skills):
            skills = tuple(s.strip() for s in raw_skills if s.strip())
        else:
            malformed = True
    if findings:
        return findings
    return Reply(profile.strip(), roles, skills, malformed)
