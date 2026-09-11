"""The `frankfurter` rate source (#305).

Everything this provider knows is a fact about ONE service -- its endpoint, its
`{"base", "rates"}` shape, and that it quotes units-per-base so a GBP base has to be
inverted. That is why these tests live beside it rather than in `tests/test_fx.py`: none
of it is true of every provider, and `core/fx.py` no longer knows any of it.

Offline throughout: `urlopen` is faked. The provider is the only thing in the seam that
would make a request, so it is the only thing that needs faking.
"""
import json

import pytest

from sluice.core import plugins
from sluice.rates import SEAM
from sluice.rates.frankfurter import Frankfurter


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(payload, seen=None):
    def _open(req, timeout=None):
        if seen is not None:
            seen.append((getattr(req, "full_url", req), timeout))
        return _Resp(payload)
    return _open


@pytest.fixture
def urlopen(monkeypatch):
    import sluice.rates.frankfurter as mod

    def _install(payload, seen=None):
        monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen(payload, seen))
    return _install


# ── registration ────────────────────────────────────────────────────────────────────────

def test_the_provider_registers_itself_under_the_rates_seam():
    import sluice.rates  # noqa: F401  (import triggers registration)
    assert "frankfurter" in plugins.available(SEAM)


def test_the_factory_makes_no_request_at_construction(urlopen):
    # `Sluice.rates()` resolves this seam like any other, and resolving a seam must stay
    # as offline as resolving the store or the renderer. If construction fetched, merely
    # building the app object would reach the internet.
    def _explode(req, timeout=None):
        raise AssertionError("constructing a rate source must not make a request")
    import sluice.rates as rates_pkg
    import sluice.rates.frankfurter as mod
    monkey = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = _explode
    try:
        factory = plugins.get(SEAM, "frankfurter")
        source = factory(None)
    finally:
        mod.urllib.request.urlopen = monkey
    assert hasattr(source, "fetch")
    assert rates_pkg.SEAM == "rates"


# ── the seam contract ───────────────────────────────────────────────────────────────────

def test_fetch_returns_gbp_per_unit_not_the_quote_it_was_given(urlopen):
    """The service answers units-per-GBP; the seam requires GBP-per-unit.

    Dropping the inversion is the highest-consequence single-character change in this
    file: a fetched EUR rate would become 1.164 instead of 0.859, so a EUR 90,000 advert
    would read as GBP 104,760 and clear a GBP 100,000 floor it should have failed. The
    seam puts this here, in the provider, precisely because WHICH direction a service
    quotes in is a fact about that service.
    """
    urlopen({"base": "GBP", "rates": {"EUR": 1.25}})
    rates = Frankfurter().fetch(timeout=1)
    assert rates["EUR"] == pytest.approx(0.8), "1 / 1.25, not 1.25"
    assert rates["GBP"] == 1.0, "the base currency converts to itself"


@pytest.mark.parametrize("base", [None, "", "EUR", "usd", 42, ["GBP"], {"cur": "GBP"}])
def test_fetch_refuses_a_response_quoted_against_another_base(urlopen, base):
    """VALIDATE before normalising. What a request ASKS for and what a reply CONTAINS are
    different facts: a service that changed its default, or a proxy answering from a cache
    of another query, would make every rate the reciprocal of the wrong thing -- persisted,
    and then used to compute pay-floor rejects. A MISSING base is refused too, because
    assuming the value you are verifying is not a check."""
    payload = {"rates": {"EUR": 1.25}}
    if base is not None:
        payload["base"] = base
    urlopen(payload)
    assert Frankfurter().fetch(timeout=1) is None


def test_fetch_accepts_the_base_however_it_is_cased(urlopen):
    # The check must not become a failure mode of its own: "gbp" is the same base.
    urlopen({"base": "gbp", "rates": {"EUR": 2.0}})
    assert Frankfurter().fetch(timeout=1) is not None


def test_fetch_drops_an_unusable_quote_rather_than_dividing_by_it(urlopen):
    urlopen({"base": "GBP", "rates": {"EUR": 2.0, "AAA": 0, "BBB": -1,
                                      "CCC": "nonsense", "DDD": None, "EEE": True}})
    rates = Frankfurter().fetch(timeout=1)
    assert "EUR" in rates
    for code in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        assert code not in rates, f"{code} would have been a division by a bad quote"


@pytest.mark.parametrize("payload", [
    {"base": "GBP"},
    {"base": "GBP", "rates": {}},
    {"base": "GBP", "rates": "not a dict"},
    {"base": "GBP", "rates": {"AAA": 0, "BBB": -1}},   # nothing usable survives filtering
])
def test_fetch_returns_none_for_a_response_carrying_no_usable_rates(urlopen, payload):
    # None, never a one-entry {"GBP": 1.0} table: an almost-empty table would be persisted
    # and would then outrank the pinned one for every currency it does not name.
    urlopen(payload)
    assert Frankfurter().fetch(timeout=1) is None


def test_fetch_returns_none_rather_than_raising_when_the_service_is_unreachable(monkeypatch):
    import sluice.rates.frankfurter as mod

    def _boom(req, timeout=None):
        raise OSError("no route to host")
    monkeypatch.setattr(mod.urllib.request, "urlopen", _boom)
    assert Frankfurter().fetch(timeout=1) is None


def test_fetch_returns_none_on_a_body_that_is_not_json(monkeypatch):
    import sluice.rates.frankfurter as mod

    class _Bad:
        def read(self):
            return b"<html>down for maintenance</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: _Bad())
    assert Frankfurter().fetch(timeout=1) is None


# ── the request it makes ────────────────────────────────────────────────────────────────

def test_it_asks_the_service_for_the_base_it_inverts_from(urlopen):
    # The inversion is only correct because the request names base=GBP. If the two ever
    # disagree, every rate is silently reciprocal.
    seen = []
    urlopen({"base": "GBP", "rates": {"EUR": 2.0}}, seen)
    Frankfurter().fetch(timeout=7)
    assert seen and "base=GBP" in seen[0][0]
    assert seen[0][1] == 7, "the caller's timeout must reach the request"


@pytest.mark.parametrize("body", ["[]", "null", '"a string"', "42", "true"])
def test_fetch_returns_none_for_valid_json_that_is_not_an_object(monkeypatch, body):
    """`[]` and `null` decode fine, and `payload.get` then raises AttributeError.

    `fx.refresh` catches it so nothing crashes, but the seam contract says a provider
    ANSWERS `None` rather than raising -- and swallowing it a layer up loses the warning
    this file logs, so a service returning a bare array looks identical to a network
    failure.
    """
    import sluice.rates.frankfurter as mod

    class _Body:
        def read(self):
            return body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: _Body())
    assert Frankfurter().fetch(timeout=1) is None

