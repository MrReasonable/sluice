"""Listing-language gate (#312): drop a listing whose TITLE is written in a script none of the user's
languages uses.

A cheap title filter beside `core/relevance.py`, run at the same point in ingest -- before dedup and
long before any LLM call -- so a filtered listing costs nothing. It ships with NO opinion: the
languages come from the root `listing_languages` config key, and an empty list keeps everything.

It classifies SCRIPT, not language. A Han or an Arabic letter is a fact about a title; telling
English from German or French is a guess, and a two-to-six word title gives a word classifier almost
nothing to guess from. Dropping wrongly bins a real lead invisibly, while keeping wrongly costs one
judge call, so every rule below leans toward keeping:

- a title is DROPPED when it carries letters in a script none of the configured languages writes. A
  bilingual title counts (owner's ruling): one written for readers of two scripts is an advert in
  the one this user does not read;
- it takes TWO such letters. A single one -- "μServices", a "Δ1" desk, a Cyrillic "Е" pasted where a
  Latin "E" belonged -- is decoration far more often than language;
- LATIN letters never count against a title (owner's ruling). Technology names are written in Latin
  in every market, so a native-language title routinely carries one ("Java" in a Chinese title,
  "AWS" in a Japanese one); counting them would drop a non-Latin reader's own listings. The cost is
  that a reader of only non-Latin languages cannot filter English titles out;
- symbols, digits and punctuation are never letters, so "£120k", "▪" and "™" never count, and
  neither does script punctuation whose Unicode NAME begins with a script ("ARABIC COMMA");
- a letter in a script this roster does not name is kept rather than guessed at;
- languages sharing a script cannot be told apart: `[en]` keeps a German title. That limit is
  stated in `docs/CONFIGURATION.md` rather than papered over.

Standard library only: `unicodedata` names every character's script in the first word or two of its
name, which is all this needs.
"""
import unicodedata

# Character-name prefixes -> script(s), first match wins. Only entries that change a verdict are
# listed: a full-width Latin letter needs none (Latin never counts, and an unmatched letter is kept
# anyway), and the kana long-vowel mark ("KATAKANA-HIRAGANA ...") already matches "KATAKANA".
_NAME_PREFIXES = (
    ("HALFWIDTH KATAKANA", ("Katakana",)),
    ("HALFWIDTH HANGUL", ("Hangul",)),
    ("LATIN", ("Latin",)),
    ("CJK", ("Han",)),
    ("HIRAGANA", ("Hiragana",)),
    ("KATAKANA", ("Katakana",)),
    ("HANGUL", ("Hangul",)),
    ("CYRILLIC", ("Cyrillic",)),
    ("GREEK", ("Greek",)),
    ("ARABIC", ("Arabic",)),
    ("HEBREW", ("Hebrew",)),
    ("DEVANAGARI", ("Devanagari",)),
    ("BENGALI", ("Bengali",)),
    ("GURMUKHI", ("Gurmukhi",)),
    ("GUJARATI", ("Gujarati",)),
    ("TAMIL", ("Tamil",)),
    ("TELUGU", ("Telugu",)),
    ("KANNADA", ("Kannada",)),
    ("MALAYALAM", ("Malayalam",)),
    ("THAI", ("Thai",)),
    ("GEORGIAN", ("Georgian",)),
    ("ARMENIAN", ("Armenian",)),
    ("ETHIOPIC", ("Ethiopic",)),
)

# ISO 639-1 code -> the scripts that language is written in. This is the roster `listing_languages`
# is validated against, so an unknown code fails loudly at load instead of silently meaning nothing.
# A language in two scripts lists both (Serbian; Punjabi's Gurmukhi and Arabic-script Shahmukhi;
# Japanese's kanji and kana; Korean's occasional hanja), because a reader of it reads both --
# missing one drops that reader's own listings.
_LATIN = ("Latin",)
LANGUAGE_SCRIPTS = {
    **{code: _LATIN for code in (
        "af", "ca", "cs", "cy", "da", "de", "en", "es", "et", "eu", "fi", "fr", "ga", "gl", "hr",
        "hu", "id", "is", "it", "lt", "lv", "ms", "mt", "nl", "no", "pl", "pt", "ro", "sk", "sl",
        "sq", "sv", "sw", "tl", "tr", "vi")},
    "sr": ("Cyrillic", "Latin"),
    **{code: ("Cyrillic",) for code in ("be", "bg", "kk", "mk", "mn", "ru", "uk")},
    "el": ("Greek",),
    "he": ("Hebrew",),
    **{code: ("Arabic",) for code in ("ar", "fa", "ur")},
    **{code: ("Devanagari",) for code in ("hi", "mr", "ne")},
    "bn": ("Bengali",),
    "pa": ("Gurmukhi", "Arabic"),
    "gu": ("Gujarati",),
    "ta": ("Tamil",),
    "te": ("Telugu",),
    "kn": ("Kannada",),
    "ml": ("Malayalam",),
    "th": ("Thai",),
    "ka": ("Georgian",),
    "hy": ("Armenian",),
    "am": ("Ethiopic",),
    "zh": ("Han",),
    "ja": ("Han", "Hiragana", "Katakana"),
    "ko": ("Hangul", "Han"),
}

# Below this many letters in unreadable scripts a title is kept: one stray Greek or Cyrillic letter
# is decoration or a homoglyph far more often than it is the advert's language.
_MIN_UNREADABLE_LETTERS = 2


def parse_listing_languages(value) -> list:
    """`listing_languages` from config: a list of ISO 639-1 codes, lower-cased, each in the roster.

    The ONE validator, called by the config loader and by the `init` wizard alike, so the wizard
    cannot write a value the loader then refuses. An unknown code RAISES listing the valid ones:
    dropped silently, `[english]` would leave a gate the user believes is on with nothing in it.
    The message names the entry's POSITION, never its text -- `_str_list`'s rule (#176): a config
    error reaches stderr, logs and pasted tracebacks, and whatever was typed into the list is the
    user's own. `None` means unset, and is `[]`."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            f"listing_languages must be a YAML list of language codes, got "
            f"{type(value).__name__}")
    for i, c in enumerate(value, 1):
        if isinstance(c, bool):
            # YAML 1.1 reads a bare `no` -- Norwegian's code -- as false. Said rather than coerced
            # back: coercion would also accept a genuine `false`, and the user should know why.
            raise ValueError(
                f"listing_languages: entry {i} loaded as a true/false value. YAML reads a bare "
                f"`no` (Norwegian) as false -- quote it: 'no'")
        if not isinstance(c, str):
            raise ValueError(
                f"listing_languages: entry {i} must be a language code, got "
                f"{type(c).__name__}")
    codes = [c.strip().lower() for c in value]
    unknown = [str(i) for i, c in enumerate(codes, 1) if c not in LANGUAGE_SCRIPTS]
    if unknown:
        raise ValueError(
            f"listing_languages: entry {', '.join(unknown)} is not a known language code. "
            f"Use ISO 639-1 codes: {', '.join(sorted(LANGUAGE_SCRIPTS))}")
    return codes


def _scripts(char: str) -> tuple:
    """The script(s) a LETTER belongs to, or () when this roster does not know it."""
    name = unicodedata.name(char, "")
    for prefix, scripts in _NAME_PREFIXES:
        if name.startswith(prefix):
            return scripts
    return ()


def is_readable(title: str, languages) -> bool:
    """False when `title` carries enough letters in scripts none of `languages` writes.

    An empty `languages` keeps everything -- an unconfigured gate is a pass-through, never a filter
    based on somebody else's languages -- and so does a list naming no code the roster knows: the
    loader validates codes, but a caller that bypassed it must fail toward keeping, not toward
    dropping every lead it sees."""
    known = [code for code in languages or () if code in LANGUAGE_SCRIPTS]
    if not known:
        return True
    readable = {"Latin"} | {s for code in known for s in LANGUAGE_SCRIPTS[code]}
    unreadable = 0
    for char in title or "":
        if not char.isalpha():
            continue
        scripts = _scripts(char)
        # `scripts` empty: a script the roster does not know -- keep (abstain), never count.
        if scripts and not readable.intersection(scripts):
            unreadable += 1
            if unreadable >= _MIN_UNREADABLE_LETTERS:
                return False
    return True
