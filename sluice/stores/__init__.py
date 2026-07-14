"""Store plugins. A module here registers a Store implementation at import time.

Mirrors `ingest/sources/`: importing the package auto-imports every sibling, so the
registry is populated by `from sluice import stores`. A broken plugin is logged and
skipped rather than sinking the registry -- but its name is then absent, so asking for
it raises `UnknownAdapter` rather than silently getting a different store. Which is the
point: a quiet wrong store means writing the user's data somewhere they did not ask for.
"""
from sluice.core import plugins

SEAM = "store"


def register(name: str, factory) -> None:
    plugins.register(SEAM, name, factory)


plugins.autoload(__import__(__name__, fromlist=["_"]))
