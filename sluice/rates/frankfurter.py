"""api.frankfurter.dev, registered as the `frankfurter` rate source.

Keyless and ECB-backed, which is why it is the shipped default: no account, no key in
anyone's environment, and a published reference rate rather than a broker's quote.

Everything this file knows is a fact about THIS service -- that it takes a `base` query
parameter, answers `{"base": ..., "rates": {...}}`, and quotes units-per-base, so a GBP
base has to be inverted to give the GBP-per-unit the seam requires. None of that is true
of every provider, which is why none of it lives in `core/fx.py` any more.
"""
import json
import math
import urllib.request

from sluice.core.log import get_logger
from sluice.rates import register

_log = get_logger("rates.frankfurter")

# Asked for GBP as the base so the response needs inverting exactly once, here.
_ENDPOINT = "https://api.frankfurter.dev/v1/latest?base=GBP"
_BASE = "GBP"


class Frankfurter:
    """Fetch GBP-per-unit rates from frankfurter.

    Takes no construction arguments. An earlier cut accepted `endpoint` and `base`, which
    nothing ever passed -- `_make` ignores the config it is handed -- so they were a
    settable-looking surface that could not be set. Making the endpoint configurable is a
    deliberate decision with a security dimension (a url this process fetches, persists and
    computes rejects from), not something to leave half-built in a constructor signature.
    """

    endpoint = _ENDPOINT
    base = _BASE

    def fetch(self, timeout: int) -> dict | None:
        """The seam's one method: GBP-per-unit, or None when this service cannot answer.

        Returns None rather than raising on every failure -- an offline machine, a DNS
        hiccup, a changed response shape -- because rates are an optimisation over the
        pinned table and no lead's verdict is worth an exception here.
        """
        try:
            req = urllib.request.Request(self.endpoint, headers={"User-Agent": "sluice/fx"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
        except Exception as exc:      # noqa: BLE001 - every failure is the same answer
            _log.debug("rate fetch failed: %s", exc)
            return None

        # `[]` and `null` are valid JSON, so the decode above succeeds and `payload.get`
        # then raises AttributeError from OUTSIDE that try. `fx.refresh` catches it, so
        # nothing crashes -- but the seam contract says a provider ANSWERS None rather than
        # raising, and swallowing it one layer up loses the reason this file would have
        # logged.
        if not isinstance(payload, dict):
            _log.warning("rate source answered %s, expected an object", type(payload).__name__)
            return None

        # VALIDATE before normalising. The inversion below is only correct if the reply is
        # quoted against the base we asked for, and what a request ASKS for and what a
        # reply CONTAINS are different facts: a service that changed its default, or a
        # proxy answering from a cache of another query, would make every rate the
        # reciprocal of the wrong thing -- persisted, and then used to compute pay-floor
        # rejects. A missing base is refused too, because assuming the value you are
        # verifying is not a check.
        base = payload.get("base")
        if not isinstance(base, str) or base.upper() != self.base:
            _log.warning("rate source answered in base %r, expected %s", base, self.base)
            return None
        quoted = payload.get("rates")
        if not isinstance(quoted, dict) or not quoted:
            return None

        # A non-positive, non-finite or non-numeric quote is DROPPED rather than inverted:
        # dividing by it is how a bad feed becomes a wrong verdict. Inside a try because
        # `float()` on an oversized int raises OverflowError, so a response that passes
        # every type check above can still fail to convert -- and the seam contract says a
        # provider ANSWERS None rather than raising.
        rates = {self.base: 1.0}
        try:
            for code, per_base in quoted.items():
                if (isinstance(per_base, (int, float)) and not isinstance(per_base, bool)
                        and math.isfinite(per_base) and per_base > 0):
                    rates[str(code).upper()] = 1.0 / float(per_base)
        except Exception as exc:      # noqa: BLE001 - same answer as any other failure
            _log.warning("rate source quote could not be converted: %s", exc)
            return None
        # Only the base survived, so the response carried nothing usable. `None`, not a
        # one-entry table: an almost-empty table would be persisted and would then outrank
        # the pinned one for every currency it does not name.
        return rates if len(rates) > 1 else None


def _make(config):
    return Frankfurter()


register("frankfurter", _make)
