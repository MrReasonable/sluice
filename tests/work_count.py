"""Count the digit reads a group-chain pass makes, for the linear-time guards.

WHY COUNT WORK AND NOT TIME. These guards used to assert a wall-clock bound (`< 0.5` s on a
15000-group chain). That bound measured the host and the tracer, not the algorithm: CI runs
the suite under `--cov` with branch coverage, which slows this kind of per-character loop by
an order of magnitude, and on a shared runner the same linear call took 1.2-1.3 s and
failed. Every read of a character in a chain goes through core/tokens.py::digit_value, so
counting its calls measures the work itself: the same number on any host, traced or not.

The count is taken on EVERY binding of the function in a loaded `sluice` module, not on a
hand-listed pair: cv/reply.py imports it by name, so patching only core/tokens.py would
miss every read reply.py makes and undercount, which fails open.
"""
import sys

import sluice.cv.reply  # noqa: F401 -- loaded so its binding is counted too
from sluice.core import tokens as T


class WorkBoundExceeded(AssertionError):
    pass


def bound_digit_reads(monkeypatch, limit):
    """Patch every `sluice` binding of digit_value with a counting wrapper; return a list
    whose one element is the running count. The wrapper RAISES once the count passes
    `limit`, so a quadratic pass fails in a moment instead of running for hours on a chain
    this long. Clears group_reading's per-string memo first, so the pass is counted cold."""
    original = T.digit_value
    count = [0]

    def counting(*args):
        count[0] += 1
        if count[0] > limit:
            raise WorkBoundExceeded(f"more than {limit} digit reads")
        return original(*args)

    patched = []
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "sluice" or name.startswith("sluice.")):
            continue
        for attr, value in list(vars(module).items()):
            if value is original:
                monkeypatch.setattr(module, attr, counting)
                patched.append(name)
    # Scope, not violations: the two modules the guards drive must both have been reached,
    # or the count silently covers less than the pass it claims to bound.
    assert {"sluice.core.tokens", "sluice.cv.reply"} <= set(patched), patched
    # getattr, not a bare call: with no memo there is nothing to clear, and a bare call would
    # make removing the memo fail here, on an AttributeError, rather than on the count.
    getattr(T._readings, "cache_clear", lambda: None)()
    return count
