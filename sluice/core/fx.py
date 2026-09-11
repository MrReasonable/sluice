"""Currency conversion for the pay floors (#305).

The floors in `sluice.yaml` are denominated in ONE currency (`*_gbp_*`). An advert is
denominated in whatever its market uses. Before this module the two were compared as bare
numbers, so a EUR figure was judged ~15% too generously against a GBP floor and a krona
figure was not recognised as money at all.

THREE RULES SHAPE EVERYTHING HERE, and they come from `triage/classify.py`, which is the
only caller that matters:

1. **No opinion never rejects.** A currency this module cannot value must yield `None`, and
   `_pay_reject` must then abstain. A wrong reject bins a lead the user never sees, which
   is the expensive direction and the bug class that module exists to remove. Every failure
   path here returns `None`; none of them raises, and none of them guesses a rate.

2. **No network on the per-lead path.** Triage runs unattended over thousands of leads. A
   fetch inside the judging loop is a hang waiting to happen, so `to_gbp` NEVER touches the
   network. `refresh()` is separate, explicit, and called at most once per run by a caller
   that wants live rates.

3. **An offline install must behave.** `_PINNED` ships real rates, so a machine with no
   network, no cache and no configuration converts sensibly rather than abstaining on
   everything. Pinned rates go stale; that is why `refresh()` exists and why `age_days()`
   is exposed for a caller that wants to surface staleness.

WHY STALENESS IS CHEAP HERE. A pay floor is a coarse instrument: it asks "is the advertised
ceiling under the number the user set". Exchange rates move a few percent a year, far below
the precision that question needs, so a rate a month old changes essentially no verdicts and
a rate a year old changes few. That is the whole reason a pinned table is an acceptable
fallback rather than a correctness hazard, and it is why nothing here retries or fails hard.
"""
import datetime
import json
import math
import os
import time

from sluice.core.paths import resolve

# GBP per 1 unit of the quoted currency, i.e. multiply a foreign amount by this to get GBP.
# Captured 2026-09-09 from ECB reference rates via api.frankfurter.dev. Deliberately stored
# in this direction: the callers all convert TO the floor's currency, so storing the inverse
# would put a division on every lead and a reciprocal in every test's arithmetic.
#
# THE SET IS THE PUBLISHER'S, NOT A CHOICE MADE HERE. This is every currency that endpoint
# quotes, transcribed whole. An earlier cut of this file shipped nine of them, picked by
# hand, and the pruning was the problem rather than the size: a hand-picked set is a claim
# about which markets matter, and the currencies left out did not merely go unconverted --
# they were not recognised as MONEY at all, so the pay floor never fired for them. That is
# the very defect this module exists to remove, left live for the markets somebody decided
# were not interesting. Transcribing the publisher's list whole means no such decision is
# encoded here, and it is why this table is regenerated wholesale rather than appended to.
#
# These are a FALLBACK, not a source of truth. See the staleness note in the module
# docstring for why a stale rate is tolerable in a pay floor and intolerable in, say, an
# invoice.
_PINNED = {
    "AUD": 0.532595,          # 1 / 1.8776
    "BRL": 0.144862,          # 1 / 6.9031
    "CAD": 0.535418,          # 1 / 1.8677
    "CHF": 0.913409,          # 1 / 1.0948
    "CNY": 0.109902,          # 1 / 9.099
    "CZK": 0.0354258,         # 1 / 28.228
    "DKK": 0.114917,          # 1 / 8.7019
    "EUR": 0.858959,          # 1 / 1.1642
    "GBP": 1.0,               # 1 / 1.0
    "HKD": 0.0940026,         # 1 / 10.638
    "HUF": 0.00236016,        # 1 / 423.7
    "IDR": 4.21834e-05,       # 1 / 23706
    "ILS": 0.244164,          # 1 / 4.0956
    "INR": 0.00775134,        # 1 / 129.01
    "ISK": 0.00611808,        # 1 / 163.45
    "JPY": 0.00480977,        # 1 / 207.91
    "KRW": 0.000551709,       # 1 / 1812.55
    "MXN": 0.0436148,         # 1 / 22.928
    "MYR": 0.181127,          # 1 / 5.521
    "NOK": 0.0802974,         # 1 / 12.4537
    "NZD": 0.431574,          # 1 / 2.3171
    "PHP": 0.0117966,         # 1 / 84.77
    "PLN": 0.199056,          # 1 / 5.0237
    "RON": 0.163471,          # 1 / 6.1173
    "SEK": 0.0770422,         # 1 / 12.9799
    "SGD": 0.583499,          # 1 / 1.7138
    "THB": 0.022424,          # 1 / 44.595
    "TRY": 0.015207,          # 1 / 65.759
    "USD": 0.737191,          # 1 / 1.3565
    "ZAR": 0.0459601,         # 1 / 21.758
}

# When _PINNED was captured, as data rather than as the prose above it. `rate()` compares a
# cache's own `fetched_at` against this, so a release always overrides a cache older than
# itself. Without it the cache won unconditionally and forever: enable the refresh once,
# turn it off, and that snapshot outranked every later release's table for good -- measured
# at a five-year-old cache turning EUR 100,000 into GBP 20,000, which is a wrong reject by a
# very large margin and exactly what rule 3 in the module docstring promises cannot happen.
_PINNED_AT = datetime.datetime(2026, 9, 9, tzinfo=datetime.timezone.utc).timestamp()

# How long a rate source is given to answer. Here rather than on the provider because it
# is a property of the CALLER's patience -- one slow request at the start of a run -- not
# of any one service.
_TIMEOUT = 8

_cache: dict | None = None


def _cache_path() -> str:
    """Where the fetched table lives.

    Through `paths.resolve`, not a hand-rolled XDG lookup. Two guards in this repo exist
    to catch exactly the shortcut an earlier draft of this file took: reading
    `XDG_STATE_HOME` directly made `tests/test_homebrew_formula.py` fail, because the
    formula's `test do` block sweeps only the variables it knows sluice reads, and it made
    `tests/test_path_tilde.py` fail, because a module that builds paths has to follow the
    normalisation convention the roster tracks. Going through the one helper satisfies both
    by construction rather than by being added to two lists.

    `SLUICE_FX_CACHE` is the per-file override every other store here has, which is also
    what keeps a test off a real user's state directory.
    """
    return resolve(env_var="SLUICE_FX_CACHE", config_value="",
                   kind="state", name="fx-rates.json")


def _load() -> dict:
    """The cached table, or `{}` when there is not a usable one.

    Swallows every error on purpose. A malformed, unreadable or half-written cache must
    degrade to the pinned table, never take a triage run down: the rates are an
    optimisation over `_PINNED`, and no lead's verdict is worth an exception here.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("rates"), dict):
            _cache = data
            return _cache
    except Exception:
        pass
    _cache = {}
    return _cache


def _is_usable_rate(value) -> bool:
    """Is `value` a multiplier `to_gbp` can safely use?

    ONE predicate, three callers -- `_usable` (both the read and write paths) and `rate()`
    -- because they were drifting. `rate()` carried its own `isinstance(...) or value <= 0`
    pair and so trusted `_PINNED` implicitly: a non-finite entry there returned `inf` and
    `to_gbp` raised OverflowError on the PER-LEAD path, taking a run down. The cached half
    was filtered and the pinned half was not, which is exactly the asymmetry that made the
    read path a bug in the first place.

    `bool` is excluded explicitly because it subclasses `int`, so a JSON `true` would load
    as the rate 1.0 and convert an advert at par, silently. Non-finite is excluded
    explicitly too, and is NOT covered by `value > 0`: `nan > 0` is False so a NaN is
    dropped by luck, but `inf > 0` is True.
    """
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _usable(rates: dict) -> dict:
    """`rates` keyed by upper-case code, keeping only positive finite multipliers.

    A zero, negative or non-numeric multiplier reaches `to_gbp` and turns a real salary
    into nothing, so it is dropped here as well as in the provider -- a provider is
    third-party code by design, and this is the last point before the value is persisted.
    `bool` is excluded explicitly because it subclasses `int`, so a JSON `true` would
    otherwise load as the rate 1.0 and convert an advert at par, silently. Non-finite is
    excluded explicitly too, and NOT covered by `value > 0`: `nan > 0` is False so a NaN is
    dropped by luck, but `inf > 0` is True, and an infinite multiplier reaches `round()` in
    `to_gbp` as an OverflowError.

    May RAISE, and BOTH callers keep it inside a try for that reason: `float()` on an
    oversized int is an OverflowError, so a dict that satisfies every type check here can
    still fail to convert. The callers are `refresh` (the write path) and `_fresher_rates`
    (the read path) -- one filter serving both directions, which is what stops them
    drifting apart.
    """
    return {str(code).upper(): float(value) for code, value in rates.items()
            if _is_usable_rate(value)}


def _fresher_rates() -> dict:
    """The cached rate table, but ONLY when it is newer than the one pinned in this
    release. An older cache returns `{}` and `rate()` falls through to `_PINNED`."""
    data = _load()
    fetched = data.get("fetched_at")
    if (not isinstance(fetched, (int, float)) or isinstance(fetched, bool)
            or not math.isfinite(fetched) or fetched < _PINNED_AT):
        return {}
    cached = data.get("rates")
    if not isinstance(cached, dict):
        return {}
    # Filtered on READ as well as on write. `refresh` cleans what IT persists, but the file
    # is a file: a user can edit it, a disk can half-write it, and `json` parses `NaN` and
    # `true` happily. Unfiltered, a cached NaN reached `to_gbp` and raised ValueError from
    # `round()` on the PER-LEAD path -- a whole triage run lost to one corrupt entry -- and
    # a cached `true` became the rate 1.0, converting an advert at par with nothing said.
    # `_usable` is the same filter the write path uses, so the two cannot disagree.
    try:
        return _usable(cached)
    except Exception:
        return {}


def age_days() -> float | None:
    """How old the table IN USE is, or None when the pinned rates are the ones answering.

    Asks the same question `rate()` does, via the same helper, so the two cannot disagree.
    They did: this used to validate the timestamp and nothing else, so a cache older than
    the release reported an age while `rate()` ignored it -- and `triage/engine.py` reads
    this to decide whether a refresh is worth making, so it would see "cached two days ago,
    no need to fetch" while every conversion came from the pinned table.

    None therefore means "the pinned table is answering", which is exactly when a first
    fetch is most useful, and the engine treats it as stale for that reason.
    """
    if not _fresher_rates():
        return None
    fetched = _load().get("fetched_at")
    # `max(0.0, ...)`: a clock that moved backwards, or a cache written by a machine whose
    # clock is ahead, would otherwise report a NEGATIVE age and read as impossibly fresh --
    # which is the direction that suppresses a refresh rather than causing one.
    return max(0.0, (time.time() - fetched) / 86400.0)


def rate(currency: str) -> float | None:
    """GBP per 1 unit of `currency`, or None when it is unknown.

    Whichever table is NEWER wins, per code: a cache the user fetched after this release
    was cut, else `_PINNED`. A cache is not preferred merely for being a cache -- it is
    preferred for being fresher, and one captured before `_PINNED_AT` is ignored outright
    rather than left to outrank the table shipped beside this code. A fresher cache that
    simply does not quote a code still falls back to the pinned value for it, because the
    endpoint dropping a currency is not a reason to stop valuing it.

    `None` is the honest answer for a currency nobody has a rate for, and the caller turns
    that into an abstention.
    """
    if not currency:
        return None
    code = currency.strip().upper()
    value = _fresher_rates().get(code)
    if not _is_usable_rate(value):
        value = _PINNED.get(code)
    if not _is_usable_rate(value):
        return None
    return float(value)


def known_currencies() -> frozenset[str]:
    """The codes the PINNED table can value -- the parser's money alphabet.

    NOT everything `rate()` can answer for, and the gap is deliberate rather than an
    oversight. A cache fresher than this release may quote codes `_PINNED` does not, and
    `rate()` will value them; they are still absent here, so `classify` does not read them
    as money. The alternative is worse: `_MONEY_RE` is compiled once at import, so an
    alphabet derived from cache contents would make the same advert parse differently on
    two machines, or on the same machine either side of a refresh. A currency that
    converts but is not recognised abstains, which is this codebase's safe direction; the
    fix for a genuinely missing currency is to re-pin the table in a release.

    `triage/classify.py` builds its money-recognition alphabet from this rather than
    keeping a second hand-written list, because the two lists failing to agree breaks the
    module in one of its two bad directions. A code the parser recognises but this table
    cannot value parses as money and then abstains, so the floor silently stops applying;
    a code this table CAN value but the parser does not recognise is not seen as money at
    all, which is #305 itself, aimed at whichever markets the hand-written list omitted.
    Deriving one from the other means neither drift is possible.
    """
    return frozenset(_PINNED)


def to_gbp(amount, currency: str | None) -> int | None:
    """`amount` of `currency` in GBP, rounded, or None when it cannot be valued.

    A `None` currency means the advert named no currency at all (a bare "90k"). That is NOT
    an error and NOT an assumption of sterling: it is returned unchanged so the CALLER can
    apply its own long-standing rule about unmarked figures, exactly as it already decides
    what an unmarked pay BASIS means. Deciding it here would bury a policy choice in a
    conversion helper.
    """
    if amount is None:
        return None
    if currency is None:
        return int(amount)
    r = rate(currency)
    if r is None:
        return None
    return round(amount * r)


def refresh(source, timeout: int = _TIMEOUT) -> bool:
    """Ask `source` for rates and cache them. True when the cache was updated.

    `source` is a `RateSource` (`core/protocols.py`) -- INJECTED, never looked up here, so
    this module holds no opinion about which service answers or what its payload looks
    like. It receives a table already normalised to GBP per unit and is responsible only
    for persisting it.

    EXPLICIT and never called from `to_gbp`: rule 2 in the module docstring. A caller that
    wants live rates calls this once at the start of a run, where one slow request costs
    one delay rather than one per lead.

    Returns False rather than raising on any failure. The seam contract says a provider
    answers `None` rather than raising, but a provider is third-party code by design, so a
    raise is caught here too and treated as the same "could not answer": an offline
    machine or a broken plugin must leave the previous behaviour exactly as it was.
    """
    global _cache
    try:
        rates = source.fetch(timeout)
        if not isinstance(rates, dict):
            return False
        usable = _usable(rates)
    except Exception:
        # A provider that raises is breaking its contract, but the cost of trusting it not
        # to is a triage run lost to somebody else's plugin. The NORMALISATION is inside
        # this try for the same reason and it is not theoretical: `float()` on an
        # oversized int raises OverflowError, so a provider answering `{"EUR": 10**400}`
        # -- a contract-abiding dict of numbers -- killed the run from a comprehension
        # sitting outside the guard, before any lead was judged.
        return False
    # Covers the EMPTY table too, which the seam contract forbids as a way of expressing
    # failure: nothing usable survived, so there is nothing to persist and a good cache
    # must not be overwritten with it. Checked once, here, rather than also as `not rates`
    # above -- that clause was unreachable, since an empty dict filters to an empty
    # `usable` and lands on this line anyway. A mutation witness found it: deleting it
    # changed no behaviour and killed no test, which is what a redundant guard looks like.
    if not usable:
        return False
    try:
        path = _cache_path()
        # `or "."`: a bare filename in SLUICE_FX_CACHE gives an empty dirname, and
        # `makedirs("")` raises FileNotFoundError, so the refresh failed silently for a
        # path that was perfectly writable.
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "rates": usable}, fh)
        os.replace(tmp, path)
    except Exception:
        return False
    _cache = None          # force a re-read, so the new table is what the next call sees
    return True
