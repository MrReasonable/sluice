"""Backend provider plugins: one module per LLM provider, self-registering on import.

Mirrors `sluice/stores/`, `sluice/fetchers/`, `sluice/renderers/`: importing the package
auto-imports every sibling, so the registry is populated by `import sluice.backends`. A
broken plugin is logged and skipped rather than sinking the registry -- but its name is
then absent, so `make_backend` (which guards against DEFAULT_MODELS) and the
registry-completeness test both surface the gap loudly instead of shipping a partial set.

This is registration, not relocation: the backend classes and the `make_backend` shim
stay in `core/backends.py`, where their history and comments live. These modules only give
each class a name the registry can dispatch on.

**Backend factory contract.** Unlike the store/fetcher/renderer seams -- whose factories
take the loaded config object and are resolved through `Sluice._resolve(seam, name, cfg)`
-- a backend is parameterised by more than the config: the per-role model, effort, host,
and resolved credentials that `Sluice.backend()` computes. So a backend factory is NOT a
`factory(config)` and does NOT go through `_resolve`; it takes the resolved construction
params and returns a backend:

    factory(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
            max_tokens=None, claude_host="", claude_path="claude", effort="max") -> backend

Every factory accepts this full signature (the union `make_backend` forwards) and reads
only its own subset. `http`/`runner` are omitted when None so the backend class default
applies -- the same forward-or-omit idiom `make_backend` uses for `max_tokens`. Role
selection (auto/primary/fallback) and credential resolution stay above this seam, in
`Sluice.backend()`; a factory only ever sees an already-resolved key.
"""
from sluice.core import plugins

SEAM = "backend"


def register(name: str, factory) -> None:
    plugins.register(SEAM, name, factory)


plugins.autoload(__import__(__name__, fromlist=["_"]))
