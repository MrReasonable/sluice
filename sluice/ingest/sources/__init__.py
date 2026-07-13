"""Source registry + auto-loader.

Each source module calls register(...) at import time. Importing this package
auto-imports every sibling module, so `from sluice.ingest import sources` is
enough to populate the registry. A single broken plugin is logged and skipped
rather than taking the whole registry down.
"""
import importlib
import pkgutil

_REGISTRY: dict = {}


def register(source):
    """Register a source instance under its id (last registration wins)."""
    _REGISTRY[source.id] = source
    return source


def get(id: str):
    """Return the source registered under `id`; raises KeyError if none."""
    return _REGISTRY[id]


def all_sources() -> list:
    return list(_REGISTRY.values())


def _autoload() -> None:
    from sluice.core.log import get_logger

    log = get_logger("sources")
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{mod.name}")
        except Exception as e:  # a broken plugin must not sink the rest
            log.warning("source module %s failed to import: %s", mod.name, e)


_autoload()
