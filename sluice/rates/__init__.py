"""Rate-source plugins: where `core/fx.py` gets exchange rates from.

A provider owns its own endpoint, its own response shape, and the direction it has to
invert to answer in GBP per unit. `core/fx.py` owns the cache, the pinned fallback and
the precedence between them, and never learns what any service's payload looks like.

That split is the point. Before it, the endpoint was a module constant in `core/fx.py`
with its base check and inversion inline, so pointing sluice at a different service --
including the self-hosted replacement the module's own comment held out as the reason the
dependency was safe -- meant editing shared code. It also put one provider's facts (that
frankfurter answers `{base, rates}` in units-per-base) in the file every future provider
would share.
"""
from sluice.core import plugins

SEAM = "rates"


def register(name: str, factory) -> None:
    plugins.register(SEAM, name, factory)


plugins.autoload(__import__(__name__, fromlist=["_"]))
