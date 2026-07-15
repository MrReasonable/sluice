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

Backend ROLE-selection (`Sluice.backend`) has made the same move: `cli.py`'s
`_select_backend` and its provider-construction helpers now live here as
`Sluice.backend`, so a surface resolves a judge backend the same way it resolves a
store or a fetcher. The lazy dossier fetcher and the seen/lastrun file handling still
live in `cli.py`, and the operations themselves (triage, compose, prep, record, track)
are still driven from there. So a web UI written today would resolve its adapters
(store, fetcher, renderer, backend) through `Sluice` and then have to duplicate the
rest of `cli.py`'s wiring. Moving that wiring in -- and adding `Sluice.triage(...)`,
`.compose_cv(...)` and friends as value-returning methods -- is the follow-up that makes
"a surface is a plugin" true rather than nearly-true. It is deliberately a separate change:
it touches every command, and it deserves its own review.

Adapters are resolved LAZILY, on first use. That preserves the property `cli.py`'s
inside-the-function imports were protecting: an offline command must never construct a
browser, a store, or an LLM backend just by existing. `sluice triage run --no-llm` still
touches no backend; `sluice ingest list-sources` still touches no vault.
"""
import os

from sluice.core import plugins
from sluice.core.config import Config
from sluice.core.log import get_logger

_log = get_logger("app")

_STORE_SEAM = "store"
_FETCHER_SEAM = "fetcher"
_RENDERER_SEAM = "renderer"

# ── backend construction ─────────────────────────────────────────────────────
# Per-token providers authenticate with an API key and take an optional base_url
# override, both from the environment. claude-max is deliberately absent: it
# shells the flat-rate CLI and needs no credentials.
_PROVIDER_ENV = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
}


def _provider_creds(name):
    """(api_key, base_url) for a backend name, from the environment. An unset
    *_BASE_URL yields "", which make_backend reads as "use the provider default"."""
    key_var, url_var = _PROVIDER_ENV.get(name, ("", ""))
    if not key_var:
        return "", ""  # claude-max: flat-rate CLI, no credentials to resolve
    return os.environ.get(key_var, ""), os.environ.get(url_var, "")


def _make_primary(name, model, *, effort, host, claude_path):
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    return make_backend(name, model, api_key=api_key, base_url=base_url,
                        effort=effort, claude_host=host, claude_path=claude_path)


def _make_fallback(name, model):
    """Build the fallback leg, or None when its credentials are absent.

    A missing key is not fatal: running primary-only (a claude-max setup with no
    per-token key configured) is legitimate and must keep working. But it *is* a
    degraded state -- the run has no safety net if the primary dies -- so warn
    loudly at build time rather than letting it surface as a 401 at the exact
    moment the primary goes down. When the fallback is explicitly *selected*
    (`--backend fallback`) there is nothing to degrade to, so make_backend's
    missing-key error is allowed to propagate; see Sluice.backend."""
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    if name in _PROVIDER_ENV and not api_key:
        _log.warning(
            "fallback backend '%s' has no API key (%s unset): running with no "
            "fallback -- a primary failure will now fail the run",
            name, _PROVIDER_ENV[name][0])
        return None
    return make_backend(name, model, api_key=api_key, base_url=base_url)


def _make_fallback_strict(name, model):
    from sluice.core.backends import make_backend
    api_key, base_url = _provider_creds(name)
    return make_backend(name, model, api_key=api_key, base_url=base_url)


class Sluice:
    """Resolve the configured adapters and expose the pipeline operations.

    `overrides` lets a caller (a test, or a surface with its own wiring) inject a
    pre-built adapter and skip the registry. It is the seam's test seam.
    """

    # `--backend` names a ROLE, not a provider: which of the two configured backends
    # to use. The old provider-flavoured values stay as aliases so existing crons and
    # muscle memory keep working now that selection is config-driven.
    _BACKEND_ROLES = ("auto", "primary", "fallback")
    _BACKEND_ALIASES = {"claude-max": "primary", "deepseek": "fallback"}

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

    def backend(self, role, *, primary_name, primary_model, effort, host, claude_path,
                fallback_name, fallback_model):
        """cli.py's old _select_backend, moved verbatim in behaviour. auto degrades to
        bare primary (with a warning) when the fallback has no creds; fallback is strict.
        make_backend stays the provider factory -- an unknown provider name raises
        BackendError there, unchanged.

        Unlike store/fetcher/renderer this is not cached on self: each sub-app's config
        supplies different primary/fallback fields (triage's medium effort vs cv's max,
        for instance), so there is no single per-Sluice "the" backend to memoize."""
        from sluice.core.backends import BackendError, FallbackBackend
        role = self._BACKEND_ALIASES.get(role, role or "auto")
        # argparse guards the CLI, but this method is called directly too. Without
        # this an unrecognised choice ("primry") would match neither branch below and
        # land silently in `auto` -- the same quiet-wrong-default this method exists to
        # remove, and the opposite of make_backend's fail-at-construction rule.
        if role not in self._BACKEND_ROLES:
            raise BackendError(
                f"unknown backend choice '{role}' (expected "
                f"{', '.join([*self._BACKEND_ROLES, *self._BACKEND_ALIASES])})")
        if role == "fallback":
            # Explicitly asked for it, so a missing key is fatal, not degradable.
            return _make_fallback_strict(fallback_name, fallback_model)
        primary = _make_primary(primary_name, primary_model, effort=effort, host=host,
                                claude_path=claude_path)
        if role == "primary":
            return primary
        fallback = _make_fallback(fallback_name, fallback_model)
        return FallbackBackend(primary, fallback) if fallback else primary

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
