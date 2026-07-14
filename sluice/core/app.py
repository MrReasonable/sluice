"""The composition root: `Sluice(config)`.

This is the half of the plugin story a registry cannot provide.

**Adapter plugins** are things core CALLS (store, fetcher, renderer, backend). A registry
covers those. **Surface plugins** are things that CALL core -- a web UI, a TUI, a daemon.
A registry does nothing for them. They need a programmatic API to drive, and until now
sluice had none: its API was `cli.py` functions with the signature `(args, config)` that
constructed their own `Vault()` and `Camofox()` and printed to stderr. A web UI could not
drive that, so "a web UI is a plugin" would have been a lie.

`Sluice` is the FIRST HALF of that API: it resolves every adapter the config names, so a
surface no longer has to construct a `Vault()` or a `Camofox()` for itself.

It is NOT yet the whole API, and the docs must not pretend otherwise. The backend
construction (`_build_backend`, `_select_backend`), the lazy dossier fetcher, and the
seen/lastrun file handling all still live in `cli.py`, and the operations themselves
(triage, compose, prep, record, track) are still driven from there. So a web UI written
today would resolve its adapters through `Sluice` and then have to duplicate the rest of
`cli.py`'s wiring. Moving that wiring in -- and adding `Sluice.triage(...)`,
`.compose_cv(...)` and friends as value-returning methods -- is the follow-up that makes
"a surface is a plugin" true rather than nearly-true. It is deliberately a separate change:
it touches every command, and it deserves its own review.

Adapters are resolved LAZILY, on first use. That preserves the property `cli.py`'s
inside-the-function imports were protecting: an offline command must never construct a
browser, a store, or an LLM backend just by existing. `sluice triage run --no-llm` still
touches no backend; `sluice ingest list-sources` still touches no vault.
"""
from sluice.core import plugins
from sluice.core.config import Config
from sluice.core.log import get_logger

_log = get_logger("app")

_STORE_SEAM = "store"
_FETCHER_SEAM = "fetcher"
_RENDERER_SEAM = "renderer"


class Sluice:
    """Resolve the configured adapters and expose the pipeline operations.

    `overrides` lets a caller (a test, or a surface with its own wiring) inject a
    pre-built adapter and skip the registry. It is the seam's test seam.
    """

    def __init__(self, config=None, **overrides):
        # A composition root with no config uses the code defaults, exactly as the
        # adapters did when cli.py constructed them bare. Callers (and tests) that pass
        # None must get a working Sluice, not an AttributeError deep inside a factory.
        self.config = config if config is not None else Config()
        self._overrides = {k: v for k, v in overrides.items() if v is not None}
        self._cache: dict = {}

    # ── adapter resolution ───────────────────────────────────────────────────
    def _resolve(self, seam: str, name: str, cfg):
        if seam in self._overrides:
            return self._overrides[seam]
        if seam not in self._cache:
            # Import the plugin package so its members self-register. Done here rather
            # than at module scope so that merely importing `core.app` stays free of
            # browser/vault/backend imports.
            _import_plugins(seam)
            factory = plugins.get(seam, name)   # raises UnknownAdapter, listing valid names
            self._cache[seam] = factory(cfg)
        return self._cache[seam]

    def store(self):
        """The configured Store. Defaults to `vault`, today's only implementation."""
        return self._resolve(_STORE_SEAM, getattr(self.config, "store", "vault"), self.config)

    def fetcher(self):
        """The configured Fetcher. Constructed on first use, so an offline command that
        never fetches never opens a browser."""
        return self._resolve(_FETCHER_SEAM, getattr(self.config, "fetcher", "camofox"),
                             self.config)

    def renderer(self, cvcfg):
        """The configured Renderer. Takes the cv config because that is where its knobs
        live (`cv.renderer`, `cv.render_script`, ...)."""
        return self._resolve(_RENDERER_SEAM, getattr(cvcfg, "renderer", "script"), cvcfg)

    # ── introspection ────────────────────────────────────────────────────────
    @staticmethod
    def available(seam: str) -> list:
        _import_plugins(seam)
        return plugins.available(seam)


def _import_plugins(seam: str) -> None:
    """Import the package whose modules register under `seam`."""
    if seam == _STORE_SEAM:
        import sluice.stores  # noqa: F401  (import triggers registration)
    elif seam == _FETCHER_SEAM:
        import sluice.fetchers  # noqa: F401
    elif seam == _RENDERER_SEAM:
        import sluice.renderers  # noqa: F401
    else:
        raise plugins.UnknownAdapter("seam", seam,
                                     [_STORE_SEAM, _FETCHER_SEAM, _RENDERER_SEAM])
