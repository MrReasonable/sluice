"""`sluice/core/fx.py` -- the rate table, the cache, and the one network call (#305).

The module shipped with no test file of its own: coverage over the whole suite reached 48%
with all of `refresh()` unexecuted, so the rate INVERSION -- the single arithmetic step the
whole feature rests on -- could be deleted with the suite green. These tests are written
around the mutations that survived.

Everything here is offline. `refresh()` takes its rate source INJECTED, so these tests
hand it a fake object rather than patching a network library; nothing in this file reaches
the network. The provider that really does talk to a service has its own file,
`tests/test_rates_frankfurter.py` -- that split mirrors the seam, and it is the reason the
tests for a response's base and inversion are no longer here.
"""
import json
import time

import pytest

from sluice.core import fx


@pytest.fixture
def cache(tmp_path, monkeypatch, _reset_fx_cache):
    """A rate cache under tmp_path, with the module's memo cleared around the test.

    Depends on `conftest`'s `_reset_fx_cache` explicitly. That fixture is autouse, so this
    changes nothing about when it runs -- but it was measured INERT twice, because every
    test here also pinned `_cache` itself, so removing `autouse=True` reddened nothing. A
    declared dependency makes the suite notice if it is deleted, which is the point of
    having it.
    """
    path = tmp_path / "fx-rates.json"
    monkeypatch.setenv("SLUICE_FX_CACHE", str(path))
    monkeypatch.setattr(fx, "_cache", None)
    return path


def _write(path, rates, fetched_at):
    path.write_text(json.dumps({"fetched_at": fetched_at, "rates": rates}), encoding="utf-8")


# ── the pinned table ────────────────────────────────────────────────────────────────────

def test_the_pinned_table_is_the_publishers_set_not_a_curated_one():
    """A hand-picked table is a claim about which markets matter, and the currencies left
    out are not merely unconverted -- `classify` derives its money-recognition alphabet
    from this table, so an omitted code is not seen as MONEY at all and the pay floor never
    fires for that market. That is #305's own defect, aimed at whoever was left out.

    Asserted as a floor on the SIZE plus the presence of markets an earlier nine-entry cut
    omitted, rather than as an exact roster: the publisher adds and drops currencies, and a
    frozen list here would fail the build for their editorial decisions. What must not
    happen is a return to a short hand-picked set.
    """
    assert len(fx._PINNED) >= 25, (
        "the pinned table has shrunk towards a hand-picked set; see the comment above it")
    for code in ("CAD", "AUD", "CZK", "INR", "JPY", "SGD", "ZAR"):
        assert code in fx._PINNED, f"{code} is unvaluable, so classify cannot see it as money"


def test_every_pinned_rate_is_a_usable_positive_number():
    # A zero or negative rate reaches `to_gbp` as a multiplier and turns a real salary into
    # nothing. `rate()` refuses one, so a bad entry abstains rather than rejecting -- but
    # the shipped table must not contain one in the first place.
    for code, value in fx._PINNED.items():
        assert fx._is_usable_rate(value), f"{code} has an unusable rate"
        assert isinstance(value, float), f"{code} is not a float"
    # `value > 0` alone does NOT cover this: `inf > 0` is True, so an infinite pinned rate
    # passed the old assertion, reached `rate()` unfiltered -- the one input nothing else
    # filters -- and made `to_gbp` raise OverflowError on the per-lead path.
    import math
    assert all(math.isfinite(v) for v in fx._PINNED.values())
    assert fx._PINNED["GBP"] == 1.0, "the floor's own currency must convert to itself"


def test_rate_is_none_for_a_currency_nobody_can_value():
    assert fx.rate("ZZZ") is None
    assert fx.rate("") is None
    assert fx.rate("eur") == fx.rate("EUR"), "a code is case-folded before lookup"


# ── conversion ──────────────────────────────────────────────────────────────────────────

def test_a_named_currency_is_converted_and_an_unnamed_one_is_not():
    # `None` is "the advert did not say", not "assume sterling in the helper": the caller
    # owns that policy, exactly as it owns what an unmarked pay BASIS means.
    assert fx.to_gbp(100_000, "GBP") == 100_000
    assert fx.to_gbp(100_000, None) == 100_000
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])
    assert fx.to_gbp(100_000, "EUR") < 100_000, "the euro is worth less than a pound here"


def test_an_unvaluable_amount_returns_none_rather_than_a_guess():
    assert fx.to_gbp(100_000, "ZZZ") is None
    assert fx.to_gbp(None, "EUR") is None


def test_nothing_here_raises_on_a_bad_input():
    # Rule 1 of the module docstring: every failure path returns None, because an exception
    # on the per-lead path takes down a run over one malformed advert.
    #
    # `is None`, not `in (None, 50_000)`. The disjunction accepted either answer, so an
    # unrecognised code silently converting AT PAR -- the exact harm a bad rate causes --
    # would have satisfied it.
    for currency in ("", "  ", "ZZZ", "12", "€"):
        assert fx.to_gbp(50_000, currency) is None, (
            f"{currency!r} is not a currency this module can value")


# ── the cache ───────────────────────────────────────────────────────────────────────────

def test_a_fresher_cache_overrides_the_pinned_table(cache):
    _write(cache, {"EUR": 0.5}, time.time())
    assert fx.to_gbp(100_000, "EUR") == 50_000


def test_a_cache_older_than_the_release_does_not_override_it(cache):
    """The cache is preferred for being FRESHER, never for being a cache.

    Without the comparison it won unconditionally and forever: enable the refresh once,
    turn it off, and that snapshot outranked every later release's table for good. Measured
    at a five-year-old cache turning EUR 100,000 into GBP 20,000 -- a wrong reject by a very
    large margin, and exactly what rule 3 of the module docstring promises cannot happen.
    """
    _write(cache, {"EUR": 0.2}, fx._PINNED_AT - 5 * 365 * 86400)
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])

    # ...and `age_days` must agree that the pinned table is the one answering. It did not:
    # validating the timestamp alone, it reported an AGE for a cache `rate()` was ignoring,
    # so `triage/engine.py` -- which reads this to decide whether a fetch is worth making --
    # would see "cached recently, no need to refresh" while every conversion came from the
    # pinned table. One question, asked through one helper, so the two cannot diverge.
    assert fx.age_days() is None, (
        "age_days reported an age for a cache rate() does not use")


def test_a_fresher_cache_still_falls_back_to_pinned_for_a_code_it_omits(cache):
    # The endpoint dropping a currency is not a reason to stop valuing it.
    _write(cache, {"EUR": 0.5}, time.time())
    assert fx.to_gbp(100_000, "CAD") == round(100_000 * fx._PINNED["CAD"])


def test_a_cache_with_a_bad_rate_falls_back_rather_than_converting_by_it(cache):
    _write(cache, {"EUR": 0, "USD": "nonsense"}, time.time())
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])
    assert fx.to_gbp(100_000, "USD") == round(100_000 * fx._PINNED["USD"])


@pytest.mark.parametrize("body", ["", "{", "[]", '{"rates": "not a dict"}', "null"])
def test_a_malformed_cache_degrades_to_the_pinned_table(cache, body):
    cache.write_text(body, encoding="utf-8")
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])
    assert fx.age_days() is None


def test_reading_rates_creates_nothing_on_disk(cache):
    # A store that creates a file merely by being read disarms every "this path moved"
    # notice keyed on the file not existing -- the #81 bug class.
    assert fx.to_gbp(100_000, "EUR") is not None
    assert not cache.exists()


def test_age_days_is_none_when_there_is_no_cache(cache):
    # None means "the pinned table is in use", which is what makes a first fetch worth
    # making -- `triage/engine.py` treats it as stale for exactly that reason.
    assert fx.age_days() is None


def test_age_days_reports_how_old_the_cache_is(cache, monkeypatch):
    # `_PINNED_AT` pushed back, so this measures AGE REPORTING alone. Left at its real
    # value the test silently changes meaning as the release date recedes: a cache stamped
    # "three days ago" is older than the pinned table during the release week, so it is
    # correctly ignored and the age is None. The cutoff has its own test below.
    monkeypatch.setattr(fx, "_PINNED_AT", 0.0)
    _write(cache, {"EUR": 0.5}, time.time() - 3 * 86400)
    age = fx.age_days()
    assert age is not None and 2.9 < age < 3.1


def test_the_table_is_read_once_per_process_and_the_memo_is_test_visible(cache):
    """`_load` memoises, so a file written after the first read is not picked up.

    That is deliberate -- a triage run reads the table once, not once per lead -- but it
    makes any test of this half order-dependent unless the memo is cleared, so `conftest`
    clears it between tests and this pins the behaviour that makes that necessary.
    """
    assert fx.age_days() is None                      # first read: no file, memoised
    _write(cache, {"EUR": 0.5}, time.time())
    assert fx.age_days() is None, "the memo is expected to hide the late write"
    fx._cache = None
    assert fx.age_days() is not None, "clearing the memo re-reads"
    assert fx.to_gbp(100_000, "EUR") == 50_000, "and the re-read table is the one in use"


# ── refresh ─────────────────────────────────────────────────────────────────────────────
#
# `refresh` is given a RateSource. These fakes ARE the seam contract: a table of
# GBP-per-unit, or None. Nothing here knows what any service's payload looks like.

class _Source:
    """A RateSource whose answer the test dictates."""

    def __init__(self, answer=None, raises=None):
        self.answer, self.raises, self.calls = answer, raises, []

    def fetch(self, timeout):
        self.calls.append(timeout)
        if self.raises is not None:
            raise self.raises
        return self.answer


def test_refresh_persists_what_the_source_returns(cache):
    assert fx.refresh(_Source({"EUR": 0.5})) is True
    assert cache.exists()
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert stored["rates"]["EUR"] == pytest.approx(0.5)
    assert fx.to_gbp(100_000, "EUR") == 50_000
    assert fx.age_days() < 1


def test_refresh_passes_its_timeout_through_to_the_source(cache):
    src = _Source({"EUR": 0.5})
    fx.refresh(src, timeout=3)
    assert src.calls == [3], "the caller's patience is the caller's to set"


@pytest.mark.parametrize("answer", [None, {}, "not a dict", 42, []])
def test_refresh_returns_false_when_the_source_cannot_answer(cache, answer):
    """`None` is the contract's failure value, and an EMPTY table is treated as one too.

    A provider must not express failure as `{}` -- persisting it would overwrite a good
    cache with nothing -- so this refuses it rather than trusting every provider to have
    read the contract.
    """
    assert fx.refresh(_Source(answer)) is False
    assert not cache.exists(), "a failed refresh must not overwrite a good cache"
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])


def test_refresh_survives_a_source_that_breaks_its_contract_and_raises(cache):
    # The seam says answer None, never raise. A provider is third-party code by design, so
    # a raise costs a lost refresh, never a lost triage run.
    assert fx.refresh(_Source(raises=RuntimeError("plugin is broken"))) is False
    assert not cache.exists()
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])


def test_refresh_drops_an_unusable_rate_rather_than_storing_it(cache):
    # A provider is supposed to have filtered these, but a zero or negative multiplier
    # reaches `to_gbp` and turns a real salary into nothing, so it is refused here too.
    assert fx.refresh(_Source({"EUR": 0.5, "AAA": 0, "BBB": -1,
                               "CCC": "nonsense", "DDD": None, "EEE": True})) is True
    stored = json.loads(cache.read_text(encoding="utf-8"))["rates"]
    assert "EUR" in stored
    for code in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        assert code not in stored, f"{code} would have been an unusable multiplier"


def test_refresh_writes_a_cache_named_by_a_bare_filename(tmp_path, monkeypatch):
    """`os.path.dirname("fx.json")` is "", and `os.makedirs("")` raises FileNotFoundError,
    so the refresh failed silently for a path that was perfectly writable."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLUICE_FX_CACHE", "fx-rates.json")
    monkeypatch.setattr(fx, "_cache", None)
    assert fx.refresh(_Source({"EUR": 0.5})) is True
    assert (tmp_path / "fx-rates.json").exists()


def test_a_refresh_is_visible_to_the_very_next_conversion(cache):
    """`refresh` must drop the read memo, or the run it was called for never sees its work.

    SURVIVING MUTANT before this test: deleting `_cache = None` at the end of `refresh`
    reddened nothing in the whole suite, while `refresh` returned True, wrote the file, and
    every later conversion answered from the PINNED table. A run that fetched fresh rates
    was then judged entirely on stale ones, reporting success throughout.

    The memoising read is what makes the order matter, and it is the ENGINE's order:
    `age_days()` is consulted first, to decide whether a fetch is worth making, and that
    read is what caches the pre-fetch table. A test that refreshes without reading first
    passes either way -- which is exactly why the existing one did.
    """
    _write(cache, {"EUR": 0.9}, time.time())
    assert fx.age_days() is not None            # the engine's first read, which memoises
    assert fx.to_gbp(100_000, "EUR") == 90_000

    assert fx.refresh(_Source({"EUR": 0.5})) is True
    assert fx.to_gbp(100_000, "EUR") == 50_000, (
        "the conversion after a refresh is still answering from the pre-refresh table")


def test_refresh_survives_a_rate_no_float_can_hold(cache):
    """A provider can satisfy every type check and still be unconvertible.

    `float()` on an oversized int raises OverflowError, and the normalisation used to sit
    OUTSIDE `refresh`'s try -- so a dict of perfectly well-typed numbers killed the run
    before any lead was judged, from a function whose docstring promises it never raises.
    """
    assert fx.refresh(_Source({"EUR": 10 ** 400})) is False
    assert not cache.exists()
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])


@pytest.mark.parametrize("literal,code", [("NaN", "EUR"), ("Infinity", "EUR"),
                                          ("-Infinity", "EUR"), ("true", "EUR"),
                                          ("\"0.5\"", "EUR"), ("null", "EUR")])
def test_a_corrupt_cached_rate_falls_back_instead_of_being_used(cache, literal, code):
    """The READ path filters too, not just the write path.

    `refresh` cleans what IT persists, but the cache is a FILE: a user can edit it, a disk
    can half-write it, and `json` parses `NaN` and `true` without complaint. Unfiltered,
    each of these reached `rate()`:

      NaN       -> `nan > 0` is False so the comparison guard passed it through, and
                   `to_gbp` then raised ValueError from `round()` -- on the PER-LEAD path,
                   so one corrupt entry took down a whole triage run
      Infinity  -> `inf > 0` is True, so it passed every guard and reached `round()` too
      true      -> `bool` subclasses `int`, so it loaded as the rate 1.0 and converted an
                   advert at par, silently and with no error anywhere

    Falling back to the pinned rate is the safe answer: a stale rate moves no verdict, and
    a raise or a 1:1 conversion both do.
    """
    cache.write_text(
        '{"fetched_at": %f, "rates": {"%s": %s, "CAD": 0.5}}' % (time.time(), code, literal),
        encoding="utf-8")
    assert fx.rate(code) == pytest.approx(fx._PINNED[code])
    assert fx.to_gbp(100_000, code) == round(100_000 * fx._PINNED[code])
    # ...and a VALID neighbour in the same corrupt file is still honoured, so the fallback
    # is per-entry rather than discarding the whole table.
    assert fx.to_gbp(100_000, "CAD") == 50_000


@pytest.mark.parametrize("stamp", ["NaN", "Infinity", "true", '"yesterday"'])
def test_a_cache_with_an_unusable_timestamp_is_ignored(cache, stamp):
    # `fetched_at` decides whether the cache outranks the release, so a value that cannot
    # be compared must not be treated as "newer". `Infinity` would win every comparison
    # forever; `true` compares as 1.0 and would lose, but silently and for the wrong reason.
    cache.write_text('{"fetched_at": %s, "rates": {"EUR": 0.2}}' % stamp, encoding="utf-8")
    assert fx.to_gbp(100_000, "EUR") == round(100_000 * fx._PINNED["EUR"])
    assert fx.age_days() is None


def test_a_lowercase_code_from_a_provider_is_still_readable_afterwards(cache):
    """Codes are upper-cased on the way in, or the cache is written unreadable.

    SURVIVING MUTANT before this test, and it needed BOTH sites mutated together --
    `_usable`'s `str(code).upper()` and the provider's -- because each masks the other, so
    a single-site mutant looks equivalent and misleads.

    Without it a provider quoting lowercase codes writes a cache `rate()` can never read,
    while `refresh()` returns True: the same silent-success shape as forgetting to drop the
    read memo, and just as invisible.
    """
    assert fx.refresh(_Source({"eur": 0.5, "Usd": 0.25})) is True
    assert fx.rate("EUR") == 0.5
    assert fx.rate("USD") == 0.25
    assert fx.to_gbp(100_000, "eur") == 50_000, "lookup folds case at the read end too"


def test_the_money_alphabet_does_not_depend_on_what_this_machine_has_cached(cache):
    """`known_currencies()` is the PINNED table, never the cache.

    SURVIVING MUTANT before this test: widening it to include cached codes left the suite
    green. It is not an equivalent change -- a cached `XYZ` enters the alphabet -- and it
    makes `_MONEY_RE`, which is compiled once at import, a function of one machine's cache
    file. The same advert would then parse differently on two machines, or on one machine
    either side of a refresh.
    """
    before = fx.known_currencies()
    _write(cache, {"EUR": 0.5, "XYZ": 0.25}, time.time())
    assert fx.rate("XYZ") == 0.25, "the cache can still VALUE a code it introduces"
    assert fx.known_currencies() == before, (
        "the alphabet moved because of a cache file; it must be the pinned table alone")
    assert "XYZ" not in fx.known_currencies()


def test_a_cache_stamped_in_the_future_reports_no_negative_age(cache):
    # A clock that moved backwards, or a cache written by a machine running ahead, would
    # otherwise report a NEGATIVE age -- which reads as impossibly fresh and is the
    # direction that SUPPRESSES a refresh rather than causing one.
    _write(cache, {"EUR": 0.5}, time.time() + 5 * 86400)
    assert fx.age_days() == 0.0


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), True, 0, -1, "x"])
def test_an_unusable_pinned_rate_abstains_instead_of_reaching_to_gbp(monkeypatch, cache, bad):
    """`rate()` filters `_PINNED` too, not just the cache.

    The cached half was filtered and the pinned half was not, so `rate()` trusted the table
    implicitly -- and an infinite entry returned `inf`, which `to_gbp` turned into an
    OverflowError on the PER-LEAD path. One shared predicate now answers for both.
    """
    monkeypatch.setitem(fx._PINNED, "ZZZ", bad)
    assert fx.rate("ZZZ") is None
    assert fx.to_gbp(100_000, "ZZZ") is None

