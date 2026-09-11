"""Deterministic pre-gate: keep | reject | needs_review, with a plain-language
reason. Runs before any dossier build or LLM call, so obvious cases cost nothing.
The gate stays conservative: it rejects only high-confidence disqualifiers from the
user's own configured lists and hands anything ambiguous to the LLM, because
false-negatives are what the audit catches. It ships with no lists of its own, so an
unconfigured gate abstains rather than applying somebody else's idea of a good role.
"""
import re
from typing import NamedTuple

from sluice.core import fx
from sluice.core.leads import is_placeholder_company
from sluice.core.roletype import (
    CONTRACT,
    PERMANENT,
    boundaried,
    normalise_role_type,
    trusted_provenance,
)

# Boards advertise pay in wildly inconsistent shapes: "£60k", "£30,000-£40,000",
# "up to £75k", "£500/day", "£50,000 + 10% bonus". The old parser stripped every
# non-digit and concatenated whatever was left, which broke the floors in BOTH
# directions:
#   "£30,000-£40,000"  -> 3000040000, sailing over any floor (the gate silently did
#                         nothing, so a configured floor still showed you £30k jobs)
#   "£1.5k/day"        -> 15, binned by any floor (the gate silently rejected a lead
#                         the user wanted -- the failure that actually costs)
# Parse the money tokens properly instead, and abstain when nothing credible parses.
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")

# A bare digit run is NOT money. A salary field reading "Ref 50000" or "postcode 1234"
# would otherwise parse as an advertised salary and be REJECTED by a floor -- binning a
# lead whose pay was never stated at all. That is the fails-closed direction, i.e. exactly
# the bug class this module exists to remove, coming back in through the parser.
#
# So a number only counts as money when it carries MONEY CONTEXT: a currency symbol, or a
# k suffix (which is itself unambiguous). Anything else is ignored, and a string with no
# money in it yields no opinion -- and no opinion never rejects.
# What a currency marker MEANS, as ISO 4217. #305: the parser used to recognise money only
# by a few symbols or a k suffix, so a Nordic or Polish posting -- "SEK 900 000",
# "900 000 kr" -- carried no money context and parsed as nothing. Combined with this
# module's "no opinion never rejects" rule, that meant every posting in those markets
# passed the pay floor whatever it paid. Abstaining on what cannot be seen is right; not
# seeing it was the bug.
#
# TWO HALVES, and both are complete on purpose. The ISO half is DERIVED from
# `fx.known_currencies()` and never hand-listed -- see that function for why a second list
# is a defect in both directions. The VERNACULAR half below covers every currency in that
# table that is commonly written some other way, and completing it was a review finding
# rather than the original design: the first cut carried exactly two families, `kr` and
# the zloty, which were the two this feature happened to be built for. That is the same
# defect `fx._PINNED`'s own comment argues against one file away -- a hand-picked set is a
# claim about which markets matter, and an omitted spelling is not merely unconverted, it
# is not seen as MONEY, so the floor never fires for that market. Applying the argument to
# the table and not to the alphabet left most of the table unreadable in its own notation.
# Deliberately no COUNT: the number depends on what you consider a distinctive form, it was
# written here as "thirteen" and computed to something else, and the comment three lines
# below bans exactly this construct.
#
# WHAT "COMPLETE" MEANS HERE, stated precisely because an earlier cut over-claimed it.
# The map covers the currencies in `fx._PINNED` that have a distinctive written form, and
# `tests/test_classify.py` pins the exact roster so a deletion cannot pass silently. It is
# NOT "every currency that is ever written some other way": a few are deliberately ISO-only
# because their common mark is a bare capital letter or is shared with a currency already
# spoken for -- the rand's bare "R" is the clearest, left out because a lone capital letter
# beside a number matches far too much ordinary advert text. Those currencies are still
# money via their ISO code.
#
# AMBIGUOUS SYMBOLS RESOLVE TO THE STRONGEST CANDIDATE, as one rule rather than a series
# of special cases. Several symbols name more than one currency and no regex settles
# which, so the only choice available is which way to be WRONG. Reading the strongest
# means an unmarked figure is OVER-valued, and an over-valued advert clears a floor it may
# not deserve to -- the permissive direction this module takes everywhere, because a lead
# the user sees and discards costs a glance while a lead binned by a guess is never seen.
# `kr` is Danish rather than Swedish, Norwegian or Icelandic on those grounds; a bare `$`
# is US rather than Canadian, Australian, Singaporean, Hong Kong, New Zealand or Mexican;
# `¥` is Chinese rather than Japanese. `tests/test_classify.py` asserts that rule against
# `fx`'s own table rather than trusting this comment.
#
# An explicit ISO code always wins, and a DISAMBIGUATED symbol beats its bare form, so
# "CA$ 150,000" is Canadian and never read as US dollars.
_VERNACULAR_MARKERS = {
    # Disambiguated dollar forms, longest-first at match time (see `_marker_pattern`).
    "CA$": "CAD", "A$": "AUD", "S$": "SGD", "HK$": "HKD", "NZ$": "NZD",
    "US$": "USD", "R$": "BRL", "Mex$": "MXN",
    # Bare symbols, ambiguous ones resolved by the rule above.
    "£": "GBP", "$": "USD", "€": "EUR", "¥": "CNY",
    "₹": "INR", "₩": "KRW", "₺": "TRY", "₪": "ILS", "₱": "PHP", "฿": "THB",
    # Written-word forms. Matched case-sensitively, like the ISO codes and for the same
    # reason: a lowercase "try" or "lei" in prose is a word far more often than a currency.
    "kr": "DKK", "zł": "PLN", "zl": "PLN", "Kč": "CZK", "Ft": "HUF",
    "Rp": "IDR", "RM": "MYR", "lei": "RON", "Lei": "RON",
}
# Keyed on the EXACT spelling the pattern matches, never a case-folded one. The alternation
# is BUILT from these keys and the matched text is read back through them, so one map with
# one casing is the only shape where the two cannot disagree. An earlier cut lower-cased the
# ISO keys and then derived the pattern from them, which quietly made every ISO code
# lowercase-only -- "SEK 900 000" stopped being money -- while the case-sensitive vernacular
# spellings became unresolvable in the other direction.
_CURRENCY_MARKERS = {
    **_VERNACULAR_MARKERS,
    # The ISO codes name themselves, upper-case. Last, so no vernacular spelling shadows one.
    **{code: code for code in fx.known_currencies()},
}

# What an AMOUNT looks like. Three spellings, and they are mutually exclusive rather than
# one permissive class: a comma-grouped number, a space-grouped one (Nordic and Central
# European boards write "900 000", and a non-breaking or narrow no-break space is a valid
# thousands separator that scraped markup can carry), or a plain run of digits.
#
# THE GROUPS ARE EXACTLY THREE DIGITS AND THE TWO STYLES NEVER MIX. That is the guard, not
# a tidiness point. An earlier cut wrote the amount as `\d[\d,\s ]*`, one class covering
# every separator, which let an amount swallow whatever number came after it -- and it
# failed in both directions at once. "£45,000 25 days holiday" parsed as 4,500,025 and
# cleared a floor it should have failed, silently switching the floor off for an ordinary
# sterling advert; "£120,000\n2 roles" parsed as nothing at all, because `\s` matched the
# newline and `float()` then raised on a string no amount of separator-stripping could
# repair. Requiring whole three-digit groups is what makes "45,000" stop at "45,000".
# WHICH CONVENTIONS THIS READS (#311): all of them, because it assumes none. Grouping by
# comma, by dot, and by plain/non-breaking/narrow no-break space are all read, and whether
# a `.` or `,` is grouping or a decimal mark is decided by PLACEMENT, in
# `_group_aware_float` below, rather than by a locale nobody knows.
#
# This replaced an en-GB-shaped grammar under which "60.000" read as sixty -- below the
# credibility floor, so the pay floor silently never fired for German, Spanish, Italian,
# Dutch, Portuguese, Brazilian, Turkish, Indonesian, Danish or Czech adverts. A per-locale
# table was the wrong shape for the same reason a locale guess is: you never know an
# advert's locale, but you can always see where its separators sit.
_SEP = r"[ \u00a0\u202f]"
def _group_aware_float(raw: str) -> float:
    """Read a number without knowing its locale (#311).

    `60.000` is sixty thousand in half of Europe and Latin America, and sixty in en-GB.
    The parser used to assume en-GB, so every advert in the other convention parsed as a
    two-digit number, fell below the credibility floor, and abstained -- the pay floor
    silently never fired on those markets.

    Placement settles it without a locale table, in four rules, and the ONLY ambiguous
    shape is the last one:

    - a space (plain, non-breaking, narrow no-break) is always grouping; no locale writes
      a decimal mark as a space, so these are stripped first and never reconsidered
    - both `.` and `,` present -> the LAST one is the decimal mark, the other groups
    - one kind, repeated -> grouping (`1.100.000`); a decimal mark occurs at most once
    - one separator with exactly three digits after it -> grouping

    That last rule is the judgement call, and it reads `60.000` as sixty thousand. It is
    right because no salary is quoted to three decimal places, while "sixty thousand" is
    an utterly ordinary thing for an advert to say. It costs nothing elsewhere: `1.50`
    has two digits after the separator and still reads as one-fifty, `60.5` as sixty and
    a half.

    Raises ValueError on anything `float()` will not take, which the caller treats as "no
    opinion" -- `_AMOUNT` should never hand this such a string, so that path is defensive.
    """
    s = raw.strip()
    for space in (" ", " ", " "):
        s = s.replace(space, "")

    dot, comma = s.rfind("."), s.rfind(",")
    if dot >= 0 and comma >= 0:
        decimal, group = (".", ",") if dot > comma else (",", ".")
        s = s.replace(group, "").replace(decimal, ".")
    elif dot >= 0 or comma >= 0:
        sep = "." if dot >= 0 else ","
        if s.count(sep) > 1 or len(s) - max(dot, comma) - 1 == 3:
            s = s.replace(sep, "")          # grouping
        else:
            s = s.replace(sep, ".")         # decimal mark
    return float(s)


_GROUPING = rf"(?:{_SEP}|[.,])"
_AMOUNT = (
    # Whole three-digit groups, with at most a decimal tail after them. The groups stay
    # EXACTLY three digits and the tail is the only place a shorter run is allowed --
    # that is the guard described above, and widening it is what let an amount swallow
    # the number that followed it.
    rf"\d{{1,3}}(?:{_GROUPING}\d{{3}})+(?:[.,]\d+)?"  # 30,000  60.000  900 000  1.100.000,50
    r"|\d+[.,]\d+"                                    # 60.5  60,5  1.50
    r"|\d+"                                           # 60  30000
)

# ONE marker alternation, used in BOTH positions. Spelling the two branches separately is
# what let them drift: the pre-amount branch accepted the ASCII zloty and the post-amount
# one did not, so an advert written the other way round carried no money context and the
# pay floor stopped applying to it. Deriving one alternation from `_CURRENCY_MARKERS`
# makes that asymmetry unspellable rather than merely tested for.
#
# The ISO codes are matched UPPERCASE and unflagged on purpose: a lowercase "usd" or "try"
# in running prose is a word far more often than a currency, and `try` is an ordinary
# English verb. Compiling this pattern with `re.I` would make every one of them a
# currency; `tests/test_classify.py` pins the case policy so that widening cannot happen
# silently. The alternation is built from `_CURRENCY_MARKERS`' own keys, so matched text
# reproduces a key verbatim and resolves by direct lookup -- no case folding happens, and
# an earlier version of this comment said it did.
#
# ORDER IS LONGEST-FIRST as a belt-and-braces measure, NOT because anything currently
# depends on it. Python's alternation takes the first arm that matches rather than the
# longest, so a bare "$" ahead of "CA$" would be the classic hazard -- but `_marker_pattern`
# already disambiguates that pair by boundary (`\bCA\$` cannot match where `\$` does), and
# measured, no marker in the table is a prefix of another, so reversing this sort changes
# zero parses. It is kept because a future marker COULD be a prefix of another and the sort
# costs nothing; it is described honestly because an earlier version of this comment
# asserted the ordering was load-bearing, which was not true of any marker present.
def _marker_pattern(marker: str) -> str:
    r"""One alternation arm for `marker`, bounded only where a boundary means anything.

    `\b` asserts a word/non-word transition, so putting one beside a symbol asserts the
    OPPOSITE of what is meant -- `\b\$` demands a word character immediately before the
    dollar sign. The boundary is therefore derived from the marker's own first and last
    characters rather than from a hand-kept list of which markers are "words".
    """
    pat = re.escape(marker)
    if marker[0].isalnum():
        pat = r"\b" + pat
    if marker[-1].isalnum():
        pat = pat + r"\b"
    return pat


_MARKER_ALT = "|".join(
    _marker_pattern(m) for m in sorted(_CURRENCY_MARKERS, key=len, reverse=True))

_MONEY_RE = re.compile(
    # a marker BEFORE the amount: £60k, $120,000, SEK 900 000, zł 250 000, kr 450 000
    rf"(?P<pre>{_MARKER_ALT})\s*(?P<pre_amt>{_AMOUNT})\s*(?P<pre_k>[kK])?\b"
    # ...or AFTER it, which is how much of Europe writes it: 900 000 kr, 45 000 EUR.
    # A LOOKAHEAD, so the marker is matched but NOT consumed. Consuming it let a number to
    # the LEFT of a salary steal that salary's only money context: "Ref 12345 GBP-symbol
    # 60,000" bound 12345 to sterling, ate the symbol, and left the real figure invisible
    # -- so the advert was REJECTED on 12345, while "Grade 7 <sym>50,000" read a ceiling of
    # 7 and switched the floor off entirely. Both directions, from one greedy consume.
    # Leaving the marker in place lets the pre-branch match it too, so both readings are
    # emitted and `_salary_ceiling`'s `max` picks the LARGER -- which is the real salary
    # whenever the stray is smaller, and is not otherwise: "Job ID 4523891 $150,000" yields
    # a ceiling of 4,523,891. That direction over-values, so it clears a floor rather than
    # manufacturing a reject, which is the side this module errs on. An earlier version of
    # this comment claimed `max` picks the real one, full stop, which is false.
    #
    # The obvious alternative, refusing a post-marker followed by a digit `(?!\s*\d)`, is
    # WRONG and was measured: it turns "900 000 kr 12 month contract" into 12 kr, because
    # the genuine binding is refused and only the spurious one survives.
    rf"|(?P<post_amt>{_AMOUNT})\s*(?P<post_k>[kK])?\s*(?=(?P<post>{_MARKER_ALT}))"
    # ...or a bare k suffix, which is its own context and carries no currency
    rf"|(?P<k_amt>{_AMOUNT})\s*(?P<k>[kK])\b"
)

# Boards routinely write a range with ONE symbol: "£30,000-40,000". The upper bound then
# carries no money context of its own, so _MONEY_RE alone reads the ceiling as 30,000 and a
# £35k floor REJECTS a role paying up to £40k -- fails-closed, the expensive direction. A
# bare number is money when it is the tail of a range whose head was money.
# Shares `_AMOUNT` with `_MONEY_RE` rather than spelling a second number grammar. When it
# did not, the two drifted the moment one was widened: `_MONEY_RE` learned the space
# separator and this did not, so "€90 000 - 110 000" matched a tail of "- 110", the
# ceiling stayed at the BOTTOM of the band, and the advert was rejected against a floor its
# real top cleared -- the precise failure the comment above says this expression exists to
# prevent, reintroduced by widening only half of it.
_RANGE_TAIL_RE = re.compile(
    rf"\s*(?:-|–|—|to)\s*(?P<amt>{_AMOUNT})\s*(?P<k>[kK])?\b", re.I)

# Below these, a parse is not a real offer -- it is a mis-parse. Abstain rather than
# reject: a wrong reject bins a lead the user never sees, the expensive direction.
#
# Parsing facts, not preferences, and the property that makes that claim checkable is
# that they are MONOTONE: each appears only inside its branch's reject CONJUNCTION, so it
# can turn a reject into an abstain and never the reverse. A number that cannot
# manufacture a reject cannot encode an opinion about which jobs are good.
_MIN_CREDIBLE_HOURLY_RATE = 5
_MIN_CREDIBLE_DAY_RATE = 50
_MIN_CREDIBLE_WEEKLY_RATE = 100
_MIN_CREDIBLE_SALARY = 1000

# How boards SPELL a pay basis (#223 §2.3). Parsing facts, not preferences: they encode
# how a rate is written, never which rate is desirable, so there is no `*Config` field
# and no `sluice.yaml.example` entry for either. ENGLISH/UK-BOARD idiom -- a board in
# another market spells its bases differently and falls through to step 4, which is a
# visible gap rather than a silent misread.
#
# Matched via `roletype.boundaried`, never plain containment: bare `pd` and `pa` inside
# an ordinary word is #128's bug class through a new door. That helper asserts a word
# boundary only on an edge the pattern HAS one for, which is what lets `/day` and `p.a.`
# sit in the same tuple as `pd` -- `_word_match` above cannot, because it asserts both
# edges unconditionally and "£500/day" then fails the assertion on the `0`.
#
# Compiled once at import, like `roletype`'s own JD sets: the gate runs every one of them
# over every lead of every run. No COUNT here on purpose -- this comment said "all
# fourteen of these" and the very next commit doubled the table, which is the third stale
# count in this branch's own prose. The number is the drift surface; `_BASES` is the
# answer, and it is two lines down.
_HOUR_MARKERS = ("/hour", "/hr", "per hour", "hourly", "an hour", "a hour", "p/h", "ph")
_DAY_MARKERS = ("/day", "per day", "day rate", "a day", "daily", "per diem", "p/d", "pd")
_WEEK_MARKERS = ("/week", "/wk", "per week", "weekly", "a week", "p/w", "pw")
# MONTHLY is recognised but has NO row in `_BASES`, and that asymmetry is the fix rather
# than an oversight. `_pay_basis` returning a name `_BASES` does not hold makes
# `_pay_reject` abstain, which is what a monthly advert needs: there is no monthly floor to
# judge it against, and the alternative -- what happened before this -- was falling through
# to the ANNUAL branch and judging a month's pay as a year's, a twelvefold error always in
# the reject direction.
#
# ENGLISH ONLY, and that is deliberate after an earlier cut shipped ten non-English
# spellings. They were the wrong answer twice over. Half were ASCII transliterations no
# board writes, so the real spellings fell through and were rejected at a twelfth of their
# worth -- the accented Polish, Swedish and Norwegian forms all failed while the
# transliterations I had invented passed. And the SET was a claim about which markets
# matter: same currency and figure, the French spelling kept and the Italian one rejected.
# A phrase list in languages the author does not read is unbounded, unverifiable, and every
# gap in it is a wrong reject.
#
# What makes English-only safe is `_unmarked_basis`: an advert whose figure is not in the
# floors' currency and whose basis this list does not recognise ABSTAINS rather than
# defaulting to annual. So a Polish-language advert quoting zloty is covered by the
# currency, and an English-language advert quoting krona -- the common shape for
# international roles -- is covered by these markers. The residual is narrow and stated: a
# non-English advert quoting STERLING monthly still defaults to annual, because neither
# guard fires on it.
#
# Giving month a real `_BASES` row with its own floor is the other option, and it is a
# FEATURE: a new config key, a credibility floor and a default of 0. Abstaining is what the
# module already does for a basis it cannot judge, so it is what this fix does.
_MONTH_MARKERS = ("/month", "/mo", "per month", "monthly", "a month", "per calendar month",
                  "pcm", "p/m", "per mth", "/mth")

_ANNUAL_MARKERS = ("per annum", "p.a.", "pa", "/year", "per year", "a year", "annually")

# basis -> (its markers, its credibility floor, the config key holding its floor, the
# noun the reject message uses). ONE table, so a basis cannot exist with no floor to be
# judged against, and cannot be judged against another basis's floor -- which is exactly
# the harm an earlier draft of §2.3 declined hourly/weekly support to avoid. That draft
# read the harm as "do not parse these"; it was really "do not reuse the DAY floor".
_BASES = {
    "hour": (_HOUR_MARKERS, _MIN_CREDIBLE_HOURLY_RATE, "contract_floor_gbp_hour",
             "Hourly rate"),
    "day": (_DAY_MARKERS, _MIN_CREDIBLE_DAY_RATE, "contract_floor_gbp_day", "Day rate"),
    "week": (_WEEK_MARKERS, _MIN_CREDIBLE_WEEKLY_RATE, "contract_floor_gbp_week",
             "Weekly rate"),
    "annual": (_ANNUAL_MARKERS, _MIN_CREDIBLE_SALARY, "perm_floor_gbp", "Salary"),
}
_BASIS_RE = {name: tuple(boundaried(m) for m in markers)
             for name, (markers, _f, _k, _n) in _BASES.items()}
# ...plus the basis with no floor. Kept in the SAME lookup `_pay_basis` sweeps, so a
# monthly advert is NAMED and therefore cannot fall through to the annual default, and
# kept OUT of `_BASES`, so naming it makes `_pay_reject` abstain.
_BASIS_RE["month"] = tuple(boundaried(m) for m in _MONTH_MARKERS)


def _pay_basis(salary: str, role_type: str, source: str) -> str | None:
    """A key of `_BASIS_RE`, `"ambiguous"`, or None for "the lead does not say" (#223 §2.3).

    `_BASIS_RE` is a SUPERSET of `_BASES`: it also holds `"month"`, which has no floor and
    therefore makes `_pay_reject` abstain. Naming `_BASIS_RE` rather than `_BASES` here is
    the point -- saying "a key of `_BASES`" while returning `"month"` is what hid the
    abstention widening below from review.

    One consequence, stated because nothing else states it: a month word ANYWHERE in the
    salary field counts as a named basis, so "60,000 per annum, paid monthly" names two and
    abstains where it was previously judged as annual. That is the existing `ambiguous`
    rule doing what it always did, in the permissive direction, but it does mean the floor
    stops applying to an annual advert that merely mentions a month.

    The order is the whole design. The SALARY's own markers are read first, so the
    posting's own words beat everything; only an UNMARKED salary consults `role_type`, and
    only when the note records the value as observed on the posting or declared by the
    user. An `assumed` one is the tool's guess about which search ran, which is the defect
    #223 exists to close.

    **An advert naming TWO bases abstains**, the same rule `observe_role_type` follows for
    a JD carrying evidence both ways. First-match-wins would be an arbitrary precedence
    between two things the advert actually said, and the cost is not symmetric: the day
    branch's reject window sits exactly where an hourly figure lands, so a wrong pick
    manufactures a reject.

    None falls through to the caller's EXISTING annual branch, byte-for-byte today's
    behaviour for a bare amount. Deliberately not improved by a guess.

    **There is no magnitude step, and that omission is measured rather than stylistic.**
    An earlier draft selected the basis from low/high thresholds, by analogy with
    `_MIN_CREDIBLE_DAY_RATE`. The analogy is false: those constants appear only inside the
    reject CONJUNCTION, so they are monotone -- they can only turn a reject into an
    abstain. A step that selects the BRANCH is bidirectional, and the day branch's reject
    window `[_MIN_CREDIBLE_DAY_RATE, contract_floor_gbp_day)` is exactly where small
    unmarked numbers land, so a low threshold MANUFACTURES rejects: £300 and £450 both
    keep on the annual branch and reject on the day one.
    """
    text = (salary or "").lower()
    named = [name for name, patterns in _BASIS_RE.items()
             if any(r.search(text) for r in patterns)]
    if len(named) > 1:
        return "ambiguous"
    if named:
        return named[0]
    if trusted_provenance(source):
        if role_type == CONTRACT:
            return "day"
        if role_type == PERMANENT:
            return "annual"
    return None


def _unmarked_basis(salary: str) -> str | None:
    """What an advert that names NO pay basis should be judged as.

    `"annual"` for a figure in the floors' own currency -- byte-for-byte the pre-#223
    behaviour, and the verdict every sterling lead in an existing vault was judged under.

    `None` for a figure in any OTHER currency, which `_pay_reject` turns into an
    abstention. That asymmetry is the point, and it is measured rather than cautious. The
    default exists because an unmarked sterling figure is overwhelmingly an annual salary;
    that reasoning does not carry to an advert this parser may simply have failed to read.
    Monthly quoting is ordinary in many markets, the marker that says so is a phrase in
    that market's language, and a phrase list is unbounded -- #305 shipped ten non-English
    spellings, half of them transliterations no board writes, and every gap in such a list
    is a twelvefold WRONG REJECT: "45 000 kr per manad" abstained while "45 000 kr per
    manad" with its real diacritics was binned at a twelfth of its worth.

    So the currency, which the parser HAS resolved, stands in for the confidence the basis
    list cannot provide. The cost is stated rather than hidden: a foreign advert quoting a
    bare amount with no basis at all is no longer judged, so the floor reaches fewer
    foreign leads than it otherwise would. That is the direction this module fails in --
    a lead the user sees and discards costs a glance, one binned unseen costs the job.
    """
    ceiling = _salary_ceiling(salary)
    if ceiling is None or ceiling.currency in (None, "GBP"):
        return "annual"
    return None


def _salary_amounts(s: str) -> list[tuple[int, str | None]]:
    """Every money amount in `s`, with k-notation expanded ("60k" -> 60000).

    Percentages are stripped first, so "£50,000 + 10% bonus" does not contribute a
    spurious 10.
    """
    if not s:
        return []
    def _to_int(raw: str, k: str) -> int | None:
        try:
            value = _group_aware_float(raw)
        except ValueError:  # pragma: no cover - regex only yields parseable numbers
            return None
        # round(), not int(): int() truncates a float product toward zero, and 2.01 is not
        # exactly representable, so int(2.01 * 1000) is 2009. A rate sitting on a floor
        # would flip keep->reject on representation error, not on the pay.
        return round(value * 1000) if k else int(value)

    out: list[tuple[int, str | None]] = []
    text = _PERCENT_RE.sub(" ", s)
    for m in _MONEY_RE.finditer(text):
        g = m.groupdict()
        marker = g["pre"] or g["post"]
        # #305: the currency the marker names, or None for a bare k figure. None is not
        # "assume sterling" -- it is "the posting did not say", and the caller decides what
        # that means, exactly as it already decides what an unmarked pay BASIS means.
        currency = _CURRENCY_MARKERS.get(marker) if marker else None
        raw = g["pre_amt"] or g["post_amt"] or g["k_amt"]
        k = g["pre_k"] or g["post_k"] or g["k"]
        v = _to_int(raw, k)
        if v is not None:
            out.append((v, currency))
        # ...and the tail of a single-symbol range ("£30,000-40,000") is money too, in the
        # SAME currency as the head: a range names its currency once.
        #
        # Resume AFTER the post marker, not at the end of the match. The post branch matches
        # its marker by lookahead, so `m.end()` sits ON the marker and the tail pattern --
        # which expects a dash next -- could never match. "120,000 USD - 150,000" therefore
        # lost its upper bound entirely and was REJECTED on 120,000 while the real top
        # cleared the floor: the exact "ceiling stayed at the BOTTOM of the band" harm
        # `_RANGE_TAIL_RE` exists to prevent, reintroduced by the fix for marker-stealing.
        # The pre-marked spelling was unaffected, which is why the range tests did not see
        # it -- every one of them marks the head.
        tail = _RANGE_TAIL_RE.match(text, m.end("post") if g["post"] else m.end())
        if tail:
            tv = _to_int(tail.group("amt"), tail.group("k"))
            if tv is not None:
                out.append((tv, currency))
    return out


class Ceiling(NamedTuple):
    """What `_salary_ceiling` found: the same figure twice, in two currencies.

    `gbp` is what the floor is compared against; `advertised` and `currency` are what the
    advert actually said, which is what a reject message has to quote -- naming a converted
    number beside a GBP floor would read as a straight comparison and hide the conversion
    entirely. Named rather than a bare triple because two of the three fields are integers
    that are equal on every sterling lead, so a positional mix-up would be invisible in
    exactly the cases the suite is fullest of.
    """
    gbp: int
    advertised: int
    currency: str | None


def _salary_ceiling(s: str) -> "Ceiling | None":
    """The TOP of the advertised pay, or None for no opinion.

    The top, not the bottom, because a floor check must fail OPEN. Rejecting an
    "£80,000-£120,000" lead against a £90k floor on the strength of its lower bound
    would bin a job the user wants; only when even the best case is under the floor is
    the reject safe. `None` means "no opinion" and never rejects.

    #305: "top" is decided on the CONVERTED value and never on the printed number, and the
    distinction is not academic -- across a mixed-currency advert the biggest number and
    the biggest amount of money are different rows. "$180,000 US / SEK 1,400,000 SE" has
    its ceiling in dollars (£132,696) while the krona figure is the larger NUMBER
    (£107,800), so choosing by number chose the bottom of the band and rejected against a
    floor the advert cleared. Both figures are returned because the reject message has to
    quote the advert's own, and the caller must not have to convert a second time.
    """
    amounts = _salary_amounts(s)
    if not amounts:
        return None
    # The floors are denominated in GBP and the advert is not. Convert HERE, once, so the
    # comparison below and the `max` above it are both in one currency.
    valued = [(fx.to_gbp(amount, currency), amount, currency)
              for amount, currency in amounts]
    # ONE unvaluable figure abstains for the whole advert rather than being skipped. The
    # ceiling is the largest of a set, and a set with an unknown member has no known
    # largest: dropping the unknown would quietly compare against whatever remained and
    # could reject on a figure that was never the top. Abstaining is the direction this
    # module fails in everywhere else.
    if any(gbp is None for gbp, _advertised, _currency in valued):
        return None
    return Ceiling(*max(valued, key=lambda row: row[0]))


# #128: a WORD-BOUNDARY match, not `pat in text`. Plain substring containment treats
# "engineer" as present inside "engineering" -- a strict character-for-character
# prefix -- so every reject pattern ending in "engineer" (machine learning engineer,
# security engineer, mobile engineer, staff engineer, solutions engineer, sales
# engineer, network engineer, project engineer, ...) silently matched inside a role
# that was actually titled "... Engineering Manager" or "... Engineering Lead": a
# different word, wearing the first 8 letters of "engineer" as a coincidence of
# English spelling, not a real match. The accept-list override did not save these
# either -- it only excuses a reject pattern that is ITSELF a substring of the
# matched accept phrase, and "security engineer" is not a substring of "engineering
# manager", so a role carrying BOTH an accepted title and this collision still got
# killed. Real leads lost to this before ever reaching the LLM judge: "Ads Conversion
# Modeling, Machine Learning Engineering Manager", "Software Security Engineering
# Manager, Secure Frameworks", "mobile engineering manager", "Staff Engineering Lead".
# Same shape independently affects the bare `vp` pattern, which currently matches
# inside "svp" for the identical reason.
#
# `\b` requires a transition between a \w character and a non-\w one (or a string
# edge), so `\bengineer\b` fails to match inside "engineering": the "r" ending
# "engineer" and the "i" starting "ing" are both word characters, with no boundary
# between them. A genuine standalone "Senior Software Engineer" is unaffected --
# "engineer" there is followed by a space or end-of-string, both real boundaries.
#
# `\b` itself breaks for a PATTERN that starts or ends in a non-word character --
# "c++", "c#", "sr.", ".net" -- because `\b` requires a WORD-to-non-word transition,
# and a pattern edge that is already non-word (the second "+" in "c++", the "."
# in "sr.") can never be one side of that transition. `\bc\+\+\b` verified live to
# fail against "C++ Developer": the boundary before "c" holds (space -> word), but
# there is no boundary after the second "+" (non-word "+" next to non-word " ").
# Lookaround assertions check ABSENCE of an adjacent word character on each side
# instead of a transition, so they hold regardless of which side of the pattern is
# itself non-word -- verified live to match all four cases above, and confirmed to
# still refuse "engineer" inside "engineering" and "vp" inside "svp" identically to
# the `\b` form (there the pattern's own edges are word characters, so both forms
# agree).
def _word_match(pat: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(pat) + r"(?!\w)", text) is not None


def _legacy_pay_basis(lead: dict) -> str:
    """The branch the PRE-#223 gate would have selected, spelled out verbatim.

    Kept -- and kept EXACT rather than approximated -- for one caller: `reverdict_notice`
    below. Every approximation tried during implementation under-reported in the
    direction of the harm. A probe that simply forces this lead's `role_type` to be
    TRUSTED misses two populations: `Contract-to-perm`, which the old SUBSTRING test sent
    down the day branch and the closed set now folds to blank, and an annual-marked
    salary on a contract-labelled lead, because §2.3's marker step runs before the
    provenance one. Both flip verdict silently, which is precisely what the notice
    exists to stop.

    Delete this, and `reverdict_notice`, once no vault predating #223 is plausible.
    """
    role_type = (lead.get("role_type") or "").lower()
    salary = (lead.get("salary") or "").lower()
    if "contract" in role_type or "/day" in salary or "per day" in salary:
        return "day"
    return "annual"


def _pay_reject(salary: str, basis: str, cfg) -> tuple[str, str] | None:
    """The floors' verdict for a STATED basis, or None for no opinion.

    Both branches abstain on an implausible parse rather than trusting it. The perm
    branch always had its credibility guard; the contract branch did not, which is why a
    "£1.5k/day" lead was silently rejected while the identical mistake on the perm side
    was harmless.

    Split out from `classify` so the re-verdict notice can ask the same floors the same
    question under the OTHER basis, rather than re-implementing them and drifting.

    A basis outside `_BASES` -- `"ambiguous"`, or a `None` the caller did not default --
    yields NO OPINION rather than falling back to a floor. Defaulting an unknown basis to
    `annual` here is how `£2,000 per week` came to be judged as a sub-90,000 salary; the
    caller defaults an UNMARKED salary deliberately and visibly, and nothing else should.
    """
    parsed = _salary_ceiling(salary)
    if parsed is None or basis not in _BASES:
        return None
    # #305: already in the floors' currency. `_salary_ceiling` converts, because it has to
    # convert anyway to know WHICH figure is the ceiling, and it owns the one abstention
    # for a currency nothing can value -- an unknown currency is exactly the "no opinion"
    # this module already returns for a bare number or an unknown basis, and for the same
    # reason: a wrong reject bins a lead unseen. Converting again here would leave that
    # guard sitting on a branch nothing could reach, which is what it did on the first cut
    # of #305 -- the test named for it never got past `_salary_ceiling`.
    #
    # A `None` currency (a bare "90k") converts to itself, preserving the verdict every
    # existing sterling lead in a vault was judged under.
    amount, advertised, currency = parsed
    _markers, credible, key, noun = _BASES[basis]
    floor = getattr(cfg, key, 0)
    if amount >= credible and amount < floor:
        # The message reports the CONVERTED figure, because the floor it is being compared
        # against is denominated in the floor's currency. Naming the advertised number
        # beside a GBP floor would read as a straight comparison and hide the conversion.
        shown = (f"{amount}" if currency in (None, "GBP")
                 else f"{amount} (from {advertised} {currency})")
        return "reject", f"{noun} below floor: {shown} < {floor}"
    return None


def reverdict_notice(lead: dict, cfg) -> str | None:
    """What #223 changes for THIS lead's pay verdict, or None when it changes nothing.

    A note written before #223 carries no `role_type_source` key and reads as `assumed`,
    so the gate stops consulting its `role_type` (§2.1). On an accumulated vault that is
    a BATCH of leads changing verdict at once, on the first run after an upgrade -- and
    `dismiss` is not in `DEFAULT_TRIAGE_STATUSES`, so a lead dismissed that way is never
    re-selected and the user never sees it again.

    Compares the PAY GATE's own verdict under each basis, which is the thing that
    actually moves. A lead an earlier gate already rejects is not affected: `classify`
    returns the FIRST reject it finds, so a current reject whose reason is not the
    current pay reject means the pay branch never decided this lead at all.
    """
    salary = lead.get("salary") or ""
    was = _legacy_pay_basis(lead)
    # The SAME default `classify` applies, not a second copy of it: the notice compares
    # what the gate would decide, so a divergence here would report a change that never
    # happens (or miss one that does).
    _now = _pay_basis(salary, normalise_role_type(lead.get("role_type")),
                      lead.get("role_type_source") or "")
    now = _now if _now else _unmarked_basis(salary)
    if was == now:
        return None
    before, after = _pay_reject(salary, was, cfg), _pay_reject(salary, now, cfg)
    if before == after:
        return None
    verdict, why = classify(lead, cfg)
    if verdict == "reject" and (after is None or why != after[1]):
        return None
    # The BASIS and the DIRECTION, deliberately without the reason string's numbers.
    # `_pay_reject`'s reason carries the advertised amount and the user's configured
    # floor, and this line is printed once per affected lead to STDERR -- on the
    # unattended install the notice exists for, that is a system journal another user on
    # the box may be able to read. `perm_floor_gbp` is the user's own pay expectation,
    # which is exactly the kind of thing a private job hunt should not leak into a shared
    # log. CodeQL flags the flow (`py/clear-text-logging-sensitive-data`); it is right
    # that this is the wrong sink, and the numbers are in the lead's own note anyway.
    return (f"pay was judged as {was}, now judged as {now}: "
            f"{'reject' if before else 'keep'} -> {'reject' if after else 'keep'}")


def classify(lead: dict, cfg) -> tuple[str, str]:
    role = (lead.get("role") or "").lower()
    company = (lead.get("company") or "").strip()
    location = (lead.get("location") or "").lower()
    salary = (lead.get("salary") or "")
    # Folded on READ as well as on write (#223 §2.2). A note's frontmatter is a file a
    # human edits in Obsidian, so `Contract` typed by hand must reach the same branch
    # `contract` does -- and `Contract-to-perm`, which the old substring test sent down
    # the contract branch on its first eight characters, must reach neither.
    role_type = normalise_role_type(lead.get("role_type"))
    # The PERSISTED key, never re-derived from config (§2.5). Asking whether the source's
    # search is configured TODAY would consult a legacy value written before #223 ran and
    # recreate the false KEEP this whole issue is about.
    role_type_source = lead.get("role_type_source") or ""

    # The accept list exists to stop a BROAD reject pattern from killing a good
    # title: a bare "manager" reject must not disqualify an accepted "<x> manager".
    # So a reject pattern is ignored only when it is part of the accepted phrase.
    #
    # It must NOT go further and wave the role through wholesale. A title can carry
    # an accept token AND an unrelated disqualifier ("<accepted role> / <rejected
    # role>"), and those mixed titles are exactly what the gate exists to catch.
    # A blanket accept-wins rule let every one of them through.
    matched_accepts = [t for t in cfg.accept_titles if _word_match(t, role)]

    for pat in cfg.reject_titles:
        if _word_match(pat, role) and not any(_word_match(pat, acc) for acc in matched_accepts):
            return "reject", f"Role not a fit: {pat}"

    if any(c in company.lower() for c in cfg.reject_companies):
        return "reject", f"Company skipped: {company}"

    if any(b in location for b in cfg.reject_locations):
        return "reject", "Location outside target geography"

    # Guarded on target_locations being set. Without the guard an empty list makes
    # `not any(...)` true for every located lead, so an unconfigured install would
    # reject EVERY job that names a location -- the opposite of abstaining.
    if cfg.target_locations and location and not any(
            t in location for t in cfg.target_locations):
        return "reject", "Location outside target geography"

    # Pay floors. `None` from `_pay_basis` means the lead states no basis, and falls
    # through to the annual branch -- byte-for-byte the pre-#223 behaviour for a bare
    # unmarked amount.
    # `_unmarked_basis`, not a bare `or "annual"`: an advert naming no basis defaults to
    # annual only when its figure is in the floors' own currency. See that function.
    basis = _pay_basis(salary, role_type, role_type_source)
    pay = _pay_reject(salary, basis if basis else _unmarked_basis(salary), cfg)
    if pay:
        return pay

    # `is_placeholder_company` catches blank AND every honest non-answer a board or
    # a legacy/foreign note carries ("Unknown", "Confidential", "N/A", ...) --
    # not just the bare "unknown" sentinel this used to check by itself. Kept LAST,
    # after every reject check above: a placeholder company must not short-circuit
    # a role/location/pay reject the lead would otherwise earn.
    if is_placeholder_company(company):
        return "needs_review", "Blank/placeholder company; visit URL to identify"

    return "keep", ""
