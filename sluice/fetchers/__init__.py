"""Fetcher plugins: the impure I/O boundary an ingest source drives a tab through.

`Source.fetch` receives one of these on its Ctx; `Source.parse` never does. That split is
what makes every parser testable offline against a golden fixture, so a fetcher plugin
must not leak into parse.
"""
from sluice.core import plugins

SEAM = "fetcher"


def register(name: str, factory) -> None:
    plugins.register(SEAM, name, factory)


plugins.autoload(__import__(__name__, fromlist=["_"]))
