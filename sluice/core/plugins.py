"""The adapter registry: one name-keyed lookup per seam.

`docs/ARCHITECTURE.md` names four adapter seams (backend, store, renderer, fetch).
Until now three of them had no registry, so `cli.py` constructed the implementations
itself -- twelve times -- and a second implementation of any seam meant editing
`cli.py` rather than adding a plugin. This generalises the pattern
`ingest/sources/__init__.py` already proves: a module registers itself at import, the
package auto-imports its siblings, and one broken plugin is logged and skipped rather
than sinking the registry.

Selection is a dict lookup keyed by the name in config, NOT a broadcast to every
registered implementation. There is exactly one live store, one live fetcher, one live
renderer, and which one it is must be answerable by reading config -- not by reasoning
about import order. An unknown name raises and lists the valid names, matching
`make_backend`'s guard: a quiet wrong default is the bug class this codebase most
consistently engineers out, and for the store seam specifically a wrong default means
writing the user's data somewhere they did not ask for.
"""
import importlib
import pkgutil

from sluice.core.log import get_logger

_log = get_logger("plugins")

# seam -> {name -> factory}. A factory takes the loaded config and returns the adapter.
_REGISTRY: dict[str, dict[str, object]] = {}


class UnknownAdapter(KeyError):
    """Raised at construction for a name no plugin registered under this seam."""

    def __init__(self, seam: str, name: str, known):
        self.seam, self.name = seam, name
        known_names = ", ".join(sorted(known)) or "(none registered)"
        # KeyError's str() re-quotes its arg, so carry the message explicitly.
        self.message = f"unknown {seam} '{name}' (registered: {known_names})"
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def register(seam: str, name: str, factory) -> None:
    """Register `factory` as the `name` implementation of `seam`.

    Last registration wins, deliberately: it lets a local plugin shadow a shipped one
    without editing core. Re-registering the same name is logged, because silently
    swapping the store out from under someone would be exactly the quiet-wrong-default
    failure this module exists to prevent.
    """
    impls = _REGISTRY.setdefault(seam, {})
    if name in impls:
        _log.warning("re-registering %s '%s' (previous implementation replaced)", seam, name)
    impls[name] = factory


def get(seam: str, name: str):
    """The factory registered for (seam, name). Raises UnknownAdapter, listing the
    valid names, rather than falling back to a default."""
    impls = _REGISTRY.get(seam, {})
    if name not in impls:
        raise UnknownAdapter(seam, name, impls)
    return impls[name]


def available(seam: str) -> list[str]:
    return sorted(_REGISTRY.get(seam, {}))


def autoload(package) -> None:
    """Import every non-underscore module in `package` so its plugins self-register.

    A single broken plugin is logged and skipped rather than taking the whole registry
    down with it -- the same rule the source registry already follows. A broken store
    plugin still surfaces loudly at construction, because its name will then be absent
    from the registry and `get` raises.
    """
    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{package.__name__}.{mod.name}")
        except Exception as e:  # a broken plugin must not sink the rest
            _log.warning("plugin module %s failed to import: %s", mod.name, e)
