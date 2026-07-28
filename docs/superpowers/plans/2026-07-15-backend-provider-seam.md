# Backend Provider Seam Implementation Plan (Stage 2)

> **Superseded 2026-07-28:** the regenerate command named below is stale — it is now `npm ci --ignore-scripts && npm run rulesync`, which reads the version AND the target list from `package.json`. Running the `-t '*'` form as written re-creates the ~34 legacy output directories this repo no longer generates. The steps are left as executed; only the command has moved.
>
> **Status: IMPLEMENTED — landed in PR #19 (2026-07-15).** This document is retained as a historical
> record of the plan as it was executed; it is **not** outstanding work. The unchecked `- [ ]` boxes
> below are the original step list, and the "Execution handoff" section describes how the plan *was*
> run (subagent-driven), not a pending action.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register `backend` as the 4th self-registering provider seam (`sluice/backends/`, mirroring `stores`/`fetchers`/`renderers`), so provider construction is a name-keyed registry lookup like every other adapter — with `make_backend` kept as a thin compatibility shim that delegates to the registry, and every existing test green.

**Architecture:** A new `sluice/backends/` package whose four modules (`claude_max`, `anthropic`, `deepseek`, `openai`) each `register(name, factory)` at import; the package `autoload`s its siblings exactly as the other three seam packages do. `make_backend` stops being an `if name == …` ladder and becomes a shim: it keeps the unknown-name guard and the `DEFAULT_MODELS` model-defaulting, then delegates provider construction to `plugins.get("backend", name)`. `Sluice.backend()`'s role logic (`auto`/`primary`/`fallback`, degrade-vs-strict) and its credential resolution (`_PROVIDER_ENV`/`_provider_creds`) are **not** touched — they call `make_backend` exactly as today, so provider construction now flows through the registry transitively. The five engines, `Sluice.backend()`, and every backend class are unchanged.

**Tech Stack:** Python 3.12+ stdlib only (the `sluice/` discipline; `yaml` guarded, google libs lazy). pytest + faker. No new runtime dependencies. `.rulesync/` is canonical; regenerate the AI-tool outputs after editing it.

## Global Constraints

Copied verbatim from `CLAUDE.md`, the design spec, and the two-stage decision. Every task implicitly includes this section.

- **The existing suite passes UNCHANGED — no existing assertion may change.** Unlike Stage 1 (which *moved* a guard suite), Stage 2 preserves `make_backend`'s observable behaviour exactly, so every test in `tests/test_backends.py` (33 `make_backend` references) and `tests/test_backend_selection.py` stays green with **zero edits**. The only permitted test change is **additive** new files. If an existing test needs editing, that is evidence of an unintended behaviour change — stop and reconsider.
- **Fail loudly at construction.** An unknown provider name still raises `BackendError` listing the valid names (never a silent default, never a bare `KeyError`/`UnknownAdapter` leaking to callers). A per-token backend with no `api_key` still raises `BackendError` at construction. A silently-skipped provider plugin (one whose module fails to import — `autoload` swallows that) must fail the build via the registry-completeness test, not ship an empty/partial backend registry.
- **`sluice/` is standard-library only** (except guarded `yaml` and the lazily-imported google libs). The new package imports only from `sluice.core.backends` and `sluice.core.plugins`. No new runtime dependency.
- **Engines, role logic, credentials, and every invariant are untouched.** Do not alter any `*/engine.py`, `core/vault.py`, `core/status.py`, `cv/validate.py`, any `*Config` default, `Sluice.backend()`, `Sluice`'s `_make_primary`/`_make_fallback`/`_make_fallback_strict`/`_provider_creds`/`_PROVIDER_ENV`, or any backend class in `core/backends.py`. This is a registry refactor of `make_backend`'s dispatch, nothing else.
- **`DEFAULT_MODELS` and `DEFAULT_BASE_URLS` stay central in `core/backends.py`.** They are pinned by `test_default_models_cover_every_selector` (exact dict) and imported by `test_backend_selection.py`. Provider modules read from them; they are not distributed into the package. (See "Design decisions" for why.)
- **`.rulesync/` is canonical.** `CLAUDE.md`, `AGENTS.md`, `.claude/` are generated and gitignored. Edit `.rulesync/rules/CLAUDE.md`, then regenerate with `npx rulesync@9.6.3 generate -t '*' -f '*'`.
- **Conventional commits** (`feat(core): …`, `refactor(core): …`, `docs: …`). End every commit message with:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Verification bar per task:** `.venv/bin/ruff check sluice tests` clean AND `.venv/bin/python -m pytest -q` green before commit (ruff 0.15.21 is in `.venv`, not the `[test]` extra). Full suite is ~0.8s, offline, hermetic — always run all of it.

---

## Design decisions

These are the choices this plan makes over the "Deferred to Stage 2" seed; they are the parts a `review-plan` pass should challenge.

1. **`make_backend` becomes a thin shim, NOT retired.** It has 33 direct test references and is the tested by-name factory. Retiring it would rewrite all of `tests/test_backends.py` to call `plugins.get(...)` — pure churn for no behavioural gain, and it would discard a clean, independently useful factory API. The shim keeps `make_backend`'s public signature and observable behaviour identical; only its *body* changes from an `if/elif` ladder to `plugins.get("backend", name)(…)`. `Sluice.backend()`'s helpers keep calling `make_backend`, so "route provider construction through `plugins.get`" is satisfied transitively, with `make_backend` the single point that consults the registry.

2. **Credential resolution stays in `core/app.py` — the "creds helper before `autoload()`" hazard is designed out, not mitigated.** The seed anticipated moving `_provider_creds`/`_PROVIDER_ENV` into the package (hence the ordering warning). This plan does **not** move them, because the degrade-vs-strict decision (`_make_fallback` warns-and-returns-None on a missing key; `_make_fallback_strict` raises) is a **role** concern that must stay in `Sluice.backend()`, and splitting "resolve the key" from "decide what a missing key means" across two modules is worse, not better. Provider factories receive an already-resolved `api_key`/`base_url` (exactly as `make_backend` forwards them today). The only ordering constraint in the new package is the established idiom — define `register` **before** `autoload()` — identical to `stores/__init__.py`.

3. **No `BackendSpec` value object — yet.** The four factories share one explicit 10-parameter keyword signature (the union of construction params `make_backend` forwards), restated in the package docstring, all four factories, and `make_backend`'s call — six times. The honest tradeoff: a params object (a frozen dataclass or a `TypedDict`) *would* remove exactly that repetition and give the factory contract one authored home. It is declined for Stage 2 because (a) the signature is stable and small, (b) introducing the type is churn orthogonal to the seam this stage delivers, and (c) each factory still reads only its own subset, so the duplication is mechanical, not semantic. **Revisit trigger:** if a 5th/6th provider lands, or a construction param is added/removed (forcing an edit across all six sites at once), promote the signature to a `BackendSpec` in the same change. Until then, YAGNI.

4. **`DEFAULT_MODELS`/`DEFAULT_BASE_URLS` stay central; provider metadata is not fully distributed.** Distributing the default-model map into per-provider modules would break `test_default_models_cover_every_selector` (a load-bearing assertion about the four defaults) and churn `test_backend_selection.py`. Accepted, stated limit: adding a 5th provider is *mostly* a plugin — a new module in `sluice/backends/` — but still requires one line in `DEFAULT_MODELS` (and `DEFAULT_BASE_URLS`/`_PROVIDER_ENV` if it is per-token and needs a key). A new test pins `set(available("backend")) == set(DEFAULT_MODELS)` so the registry and the default-model map cannot drift apart. Full metadata distribution is a possible later stage, not this one.

5. **The backend seam does NOT reuse `Sluice._resolve`.** `_resolve(seam, name, cfg)` calls `factory(cfg)` — right for store/fetcher/renderer, which are parameterised only by the config object. A backend is parameterised by more than config (per-role model/effort/host/credentials resolved by `Sluice.backend()`), and the role layer sits above the provider seam. So the backend factory contract is `factory(model, *, api_key, base_url, …)`, and provider construction stays on the `make_backend` path, not the `_resolve` path. This asymmetry is inherent and is documented in the package docstring.

---

## File structure

**New files:**
- `sluice/backends/__init__.py` — the seam package: `SEAM = "backend"`, `register(name, factory)`, `plugins.autoload(...)`. Mirrors `sluice/stores/__init__.py`. Its docstring states the backend factory contract and why the seam does not go through `_resolve`.
- `sluice/backends/claude_max.py` — registers `claude-max` → builds `ClaudeMaxBackend`.
- `sluice/backends/anthropic.py` — registers `anthropic` → builds `AnthropicBackend`.
- `sluice/backends/deepseek.py` — registers `deepseek` → builds `OpenAiCompatibleBackend` at the DeepSeek default endpoint.
- `sluice/backends/openai.py` — registers `openai` → builds `OpenAiCompatibleBackend` at the OpenAI default endpoint.
- `tests/test_backend_registry.py` — the seam's guard suite: registry completeness (the empty-registry guard, mirroring the store contract's module-level `assert`), per-provider factory construction with teeth, and (Task 2) `make_backend` dispatch-through-the-registry.

**Modified files:**
- `sluice/core/backends.py` — `make_backend` rewritten to delegate to the registry (Task 2). Its docstring and the module docstring updated. `DEFAULT_MODELS`, `DEFAULT_BASE_URLS`, `BackendError`, and all backend classes are **unchanged**.
- `sluice/core/app.py` — `_import_plugins` extended to import `sluice.backends` for the `"backend"` seam, and `_BACKEND_SEAM` added, so `Sluice.available("backend")` works (Task 1). `Sluice.backend()` and the backend helpers are **unchanged**.
- `sluice/core/plugins.py` — docstring updated: all four adapter seams are now registry-backed (Task 3).
- `docs/ARCHITECTURE.md` — the backend bullet in "Adapter-selector seams" and the plugin-core prose updated: backend is now a `sluice/backends/` registry seam, provider-by-name, role selection above it (Task 3).
- `.rulesync/rules/CLAUDE.md` — the "four adapter seams … no runtime selector" line reconciled: the backend seam now has four provider implementations selected by name (Task 3), then regenerate.
- `docs/superpowers/specs/2026-07-14-pluggable-core-design.md` — the non-goal "Sources and backends keep their existing registries" annotated as superseded for the backend seam by Stage 2 (Task 3).

**Untouched (do not edit):** all five `*/engine.py`, `core/vault.py`, `core/status.py`, `cv/validate.py`, every `*Config`, `Sluice.backend()` and its credential/role helpers, every backend class, `core/protocols.py`, `tests/test_backends.py`, `tests/test_backend_selection.py`.

---

### Task 1: the `sluice/backends/` seam package + the four provider plugins + registry guard suite

**Files:**
- Create: `sluice/backends/__init__.py`, `sluice/backends/claude_max.py`, `sluice/backends/anthropic.py`, `sluice/backends/deepseek.py`, `sluice/backends/openai.py`
- Modify: `sluice/core/app.py` (extend `_import_plugins`)
- Create test: `tests/test_backend_registry.py`

**Interfaces:**
- Consumes: `plugins.register/get/available/autoload/UnknownAdapter` (`core/plugins.py`, unchanged); `ClaudeMaxBackend`, `AnthropicBackend`, `OpenAiCompatibleBackend`, `DEFAULT_BASE_URLS`, `BackendError` (`core/backends.py`, unchanged); `Sluice.available` (`core/app.py`).
- Produces: the `"backend"` seam registry, populated with `{claude-max, anthropic, deepseek, openai}`. **The backend factory contract** (used by `make_backend` in Task 2): a factory is called as
  `factory(model, *, api_key="", base_url="", http=None, runner=None, timeout=300, max_tokens=None, claude_host="", claude_path="claude", effort="max") -> backend`.
  Every factory accepts this full signature (the union `make_backend` forwards) and reads only its subset. `http`/`runner` use the forward-or-omit idiom (omit when `None` so the class default applies), matching how `make_backend` already handles `max_tokens`.

- [ ] **Step 1: Write the failing registry guard test**

```python
# tests/test_backend_registry.py
"""Guard suite for the `backend` provider seam (Stage 2).

Mirrors tests/conformance/test_store_contract.py's stance: the moment provider
construction is a registry lookup, "every provider is registered" and "each factory
builds the right backend" become properties of the SEAM, asserted here -- not
properties anyone can assume. `plugins.autoload` deliberately swallows a broken
plugin's ImportError, so an empty or partial registry is a realistic accident, not a
hypothetical. Fail loudly on it.
"""
import pytest

from sluice.core.app import Sluice
from sluice.core.backends import (
    AnthropicBackend, BackendError, ClaudeMaxBackend, DEFAULT_BASE_URLS,
    DEFAULT_MODELS, OpenAiCompatibleBackend,
)
from sluice.core import plugins

_BACKENDS = Sluice.available("backend")

# A parametrize over an EMPTY list skips every test and exits 0: the suite that is "the
# reason the backend seam is safe" would report success having tested nothing, and a
# provider whose module fails to import (autoload swallows it) would never be noticed.
assert _BACKENDS, "no backend registered: the seam would pass vacuously and ship empty"


def test_registry_covers_every_provider_and_matches_default_models():
    # Completeness AND non-drift: a provider added to sluice/backends/ but forgotten in
    # DEFAULT_MODELS (or vice versa) desyncs make_backend's guard from its dispatch. Pin
    # them equal so neither can drift silently.
    assert set(_BACKENDS) == {"claude-max", "anthropic", "deepseek", "openai"}
    assert set(_BACKENDS) == set(DEFAULT_MODELS)


def _factory(name):
    return plugins.get("backend", name)


def test_claude_max_factory_builds_claudemax_and_forwards_effort_host():
    be = _factory("claude-max")("m", claude_host="h", claude_path="/p", effort="low")
    assert isinstance(be, ClaudeMaxBackend)
    assert be.host == "h" and be.claude_path == "/p"
    assert be.cmd_template[:2] == ["ssh", "h"]
    assert be.cmd_template[be.cmd_template.index("--effort") + 1] == "low"


def test_anthropic_factory_builds_anthropic_at_default_endpoint_and_defaults_max_tokens():
    be = _factory("anthropic")("m", api_key="k")
    assert isinstance(be, AnthropicBackend)
    assert be.url == DEFAULT_BASE_URLS["anthropic"] + "/v1/messages"
    assert be.max_tokens == 8192  # None -> class default, never a silent cap


def test_deepseek_factory_builds_openai_compatible_at_default_endpoint():
    be = _factory("deepseek")("m", api_key="k")
    assert isinstance(be, OpenAiCompatibleBackend)
    assert be.url == DEFAULT_BASE_URLS["deepseek"] + "/chat/completions"
    assert be.max_tokens is None  # uncapped, matching direct construction


def test_openai_factory_honours_base_url_override():
    be = _factory("openai")("m", api_key="k", base_url="http://local:1234/v1")
    assert be.url == "http://local:1234/v1/chat/completions"


def test_per_token_factory_missing_key_is_fatal_at_construction():
    # claude-max needs no key; the per-token providers must reject an empty one HERE,
    # so a misconfiguration surfaces at construction, not as a 401 mid-run.
    for name in ("anthropic", "deepseek", "openai"):
        with pytest.raises(BackendError, match="requires an api_key"):
            _factory(name)("m", api_key="")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backend_registry.py -v`
Expected: FAIL at collection — `Sluice.available("backend")` raises `UnknownAdapter` (seam `"backend"` is not handled by `_import_plugins`, and the package does not exist).

- [ ] **Step 3: Extend `_import_plugins` to know the backend seam**

In `sluice/core/app.py`, add the seam constant next to the others and teach `_import_plugins` to import the new package. Edit the constants block (near line 38):

```python
_STORE_SEAM = "store"
_FETCHER_SEAM = "fetcher"
_RENDERER_SEAM = "renderer"
_BACKEND_SEAM = "backend"
```

Then edit `_import_plugins` (near line 431) to add the backend branch and list it in the error:

```python
def _import_plugins(seam: str) -> None:
    """Import the package whose modules register under `seam`."""
    if seam == _STORE_SEAM:
        import sluice.stores  # noqa: F401  (import triggers registration)
    elif seam == _FETCHER_SEAM:
        import sluice.fetchers  # noqa: F401
    elif seam == _RENDERER_SEAM:
        import sluice.renderers  # noqa: F401
    elif seam == _BACKEND_SEAM:
        import sluice.backends  # noqa: F401
    else:
        raise plugins.UnknownAdapter(
            "seam", seam,
            [_STORE_SEAM, _FETCHER_SEAM, _RENDERER_SEAM, _BACKEND_SEAM])
```

(Do **not** touch `Sluice.backend()` or the `_make_*`/`_provider_creds` helpers.)

- [ ] **Step 4: Create the seam package `__init__.py`**

```python
# sluice/backends/__init__.py
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
```

- [ ] **Step 5: Create the four provider modules**

```python
# sluice/backends/claude_max.py
"""The flat-rate `claude --print` CLI backend, registered as `claude-max`.

Needs no API key: it shells the flat-rate CLI. `runner` is omitted when None so
ClaudeMaxBackend's subprocess.run default applies -- make_backend always forwards a
concrete runner, but keeping the factory independently constructible matters for the
seam's own guard suite.
"""
from sluice.backends import register
from sluice.core.backends import ClaudeMaxBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    extra = {} if runner is None else {"runner": runner}
    return ClaudeMaxBackend(model, host=claude_host, claude_path=claude_path,
                            effort=effort, timeout=timeout, **extra)


register("claude-max", _make)
```

```python
# sluice/backends/anthropic.py
"""The direct Anthropic Messages API backend, registered as `anthropic`.

Per-token: an empty api_key is fatal at construction (a deferred key becomes an opaque
401 mid-run). max_tokens is omitted when None so AnthropicBackend's own required default
(8192) applies -- the Anthropic API mandates the field.
"""
from sluice.backends import register
from sluice.core.backends import AnthropicBackend, BackendError, DEFAULT_BASE_URLS


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    if not api_key:
        raise BackendError(
            "backend 'anthropic' requires an api_key (set the provider's API key env var)")
    extra = {} if http is None else {"http": http}
    mt = {} if max_tokens is None else {"max_tokens": max_tokens}
    return AnthropicBackend(model, api_key=api_key,
                            base_url=base_url or DEFAULT_BASE_URLS["anthropic"],
                            timeout=timeout, **extra, **mt)


register("anthropic", _make)
```

```python
# sluice/backends/deepseek.py
"""The DeepSeek OpenAI-compatible chat/completions backend, registered as `deepseek`.

Per-token: an empty api_key is fatal at construction. max_tokens stays None (uncapped)
unless set, matching direct construction so a config-driven fallback is never silently
capped.
"""
from sluice.backends import register
from sluice.core.backends import BackendError, DEFAULT_BASE_URLS, OpenAiCompatibleBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    if not api_key:
        raise BackendError(
            "backend 'deepseek' requires an api_key (set the provider's API key env var)")
    extra = {} if http is None else {"http": http}
    return OpenAiCompatibleBackend(model, api_key=api_key,
                                   base_url=base_url or DEFAULT_BASE_URLS["deepseek"],
                                   timeout=timeout, max_tokens=max_tokens, **extra)


register("deepseek", _make)
```

```python
# sluice/backends/openai.py
"""The OpenAI chat/completions backend, registered as `openai`.

Same OpenAI-compatible class as deepseek, pointed at the OpenAI default endpoint. Any
other OpenAI-compatible provider (Together, a local server) is reachable by repointing
base_url on this one, or by adding a sibling module.
"""
from sluice.backends import register
from sluice.core.backends import BackendError, DEFAULT_BASE_URLS, OpenAiCompatibleBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    if not api_key:
        raise BackendError(
            "backend 'openai' requires an api_key (set the provider's API key env var)")
    extra = {} if http is None else {"http": http}
    return OpenAiCompatibleBackend(model, api_key=api_key,
                                   base_url=base_url or DEFAULT_BASE_URLS["openai"],
                                   timeout=timeout, max_tokens=max_tokens, **extra)


register("openai", _make)
```

- [ ] **Step 6: Run to verify green**

Run: `.venv/bin/python -m pytest tests/test_backend_registry.py -v && .venv/bin/ruff check sluice tests`
Expected: all registry tests PASS; ruff clean. (`make_backend` is not yet touched, so `tests/test_backends.py` and `tests/test_backend_selection.py` are unaffected — run the full suite to confirm nothing else moved.)

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (previous count + the six new tests).

- [ ] **Step 7: Commit**

```bash
git add sluice/backends/ sluice/core/app.py tests/test_backend_registry.py
git commit -m "feat(core): register backend as the 4th self-registering provider seam

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: route `make_backend` through the registry (the compatibility shim)

**Files:**
- Modify: `sluice/core/backends.py` (`make_backend` body + docstrings)
- Modify: `tests/test_backend_registry.py` (add the dispatch test)

**Interfaces:**
- Consumes: the `"backend"` seam registry from Task 1; `plugins.get`/`UnknownAdapter`; `DEFAULT_MODELS` (unchanged).
- Produces: `make_backend(name, model="", *, http=_urlopen, runner=subprocess.run, timeout=300, api_key="", base_url="", max_tokens=None, claude_host="", claude_path="claude", effort="max") -> backend` — **identical public signature and observable behaviour to today**, now delegating provider construction to `plugins.get("backend", name)`.

- [ ] **Step 1: Write the failing tests — dispatch, and the exception-translation path**

The second test pins a branch that is otherwise unreachable in normal operation (the four providers are always registered — Task 1 pins that), and so has no other test. `plugins.UnknownAdapter` is a `KeyError` subclass; the shim must surface it as `BackendError` (the fail-at-construction contract every caller — `Sluice._make_*`, the role tests — relies on), never leak the raw `KeyError`. A later "simplification" collapsing the `try/except` would leak `UnknownAdapter` with nothing red unless this test exists. `pytest.raises(BackendError)` does **not** catch a leaked `UnknownAdapter`, which is exactly what gives this test teeth.

```python
# add to tests/test_backend_registry.py
def test_make_backend_routes_provider_construction_through_the_registry(monkeypatch):
    # The shim's whole point: make_backend no longer branches on name itself, it asks the
    # registry. Spy on plugins.get and prove make_backend consults it. Before the rewrite
    # make_backend never calls plugins.get, so `calls` stays empty and this fails (red).
    from sluice.core import backends, plugins
    calls = []
    real_get = plugins.get

    def spy(seam, name):
        calls.append((seam, name))
        return real_get(seam, name)

    monkeypatch.setattr(plugins, "get", spy)
    be = backends.make_backend("claude-max", "m")
    assert ("backend", "claude-max") in calls
    assert type(be).__name__ == "ClaudeMaxBackend"


def test_make_backend_translates_a_missing_plugin_to_backenderror(monkeypatch):
    # A provider name that is valid (in DEFAULT_MODELS) but whose plugin module failed to
    # import leaves the registry without a factory: plugins.get raises UnknownAdapter (a
    # KeyError). The shim must surface BackendError -- the fail-at-construction contract
    # every caller relies on -- not leak the KeyError. Unreachable for the four registered
    # providers, so this dedicated test is the only thing pinning the translation branch.
    from sluice.core import backends, plugins

    def raise_unknown(seam, name):
        raise plugins.UnknownAdapter(seam, name, [])

    monkeypatch.setattr(plugins, "get", raise_unknown)
    with pytest.raises(backends.BackendError):
        backends.make_backend("claude-max", "m")
```

(`tests/test_backend_registry.py` already imports `pytest` from Task 1.)

- [ ] **Step 2: Run to verify red**

Run: `.venv/bin/python -m pytest tests/test_backend_registry.py -k "routes or translates" -v`
Expected: BOTH FAIL. `routes`: `assert ("backend", "claude-max") in calls` fails; `calls == []` because today's `make_backend` builds the class inline without touching `plugins.get`. `translates`: today's `make_backend` never calls `plugins.get`, so the monkeypatch is inert and `make_backend("claude-max", "m")` succeeds — `pytest.raises(BackendError)` fails because nothing was raised.

- [ ] **Step 3: Rewrite `make_backend` as the shim**

Replace the body of `make_backend` in `sluice/core/backends.py` (currently lines 210–257, the `if name == …` ladder) with the delegating shim. Keep the signature line exactly as-is; replace everything from the guard down:

```python
def make_backend(name, model="", *, http=_urlopen, runner=subprocess.run, timeout=300,
                 api_key="", base_url="", max_tokens=None,
                 claude_host="", claude_path="claude", effort="max"):
    """Build one backend by name, delegating provider construction to the `backend`
    seam registry (`sluice/backends/`).

    A thin compatibility shim, deliberately: this is the tested, config-driven by-name
    factory every caller (and `Sluice.backend`'s role helpers) already uses. It keeps
    two responsibilities here, above the registry, so behaviour is unchanged:

    - the unknown-name guard raises `BackendError` listing the valid names (never a
      silent default, and never the bare `UnknownAdapter`/`KeyError` the registry would
      raise -- callers assert `BackendError`), and
    - `model` defaults to `DEFAULT_MODELS[name]` when omitted, so the default-model map
      stays the single place a provider's default model lives.

    Everything provider-specific -- which class, which default endpoint, whether a key is
    required -- now lives in the provider's module under `sluice/backends/`, reached via
    `plugins.get("backend", name)`. The caller (which knows `name`) still resolves and
    passes the right api_key/base_url; each factory reads only what it needs.
    """
    from sluice.core import plugins
    import sluice.backends  # noqa: F401  -- import triggers factory self-registration

    if name not in DEFAULT_MODELS:
        raise BackendError(
            f"unknown backend '{name}' (expected {', '.join(DEFAULT_MODELS)})")
    model = model or DEFAULT_MODELS[name]
    try:
        factory = plugins.get("backend", name)
    except plugins.UnknownAdapter as e:
        # name is in DEFAULT_MODELS but its plugin module failed to import (autoload
        # swallows a broken plugin's ImportError, leaving the name unregistered). Surface
        # loudly as BackendError -- the fail-at-construction contract callers rely on --
        # rather than the KeyError-flavoured UnknownAdapter. The registry-completeness
        # test is what stops this reaching a user in the first place.
        raise BackendError(str(e)) from e
    return factory(model, api_key=api_key, base_url=base_url, http=http, runner=runner,
                   timeout=timeout, max_tokens=max_tokens, claude_host=claude_host,
                   claude_path=claude_path, effort=effort)
```

- [ ] **Step 4: Update the module docstring**

In `sluice/core/backends.py`, update the module docstring's `make_backend` sentence (near line 8–10) so it no longer implies an inline ladder:

Change:
```
is never blocked. `make_backend` builds any backend by name so selection can
be config-driven. The subprocess runner and HTTP poster are injected, so
everything is tested offline.
```
to:
```
is never blocked. `make_backend` builds any backend by name -- delegating the
per-provider construction to the `backend` seam registry (`sluice/backends/`) so
selection is config-driven and a new provider is a drop-in module. The subprocess
runner and HTTP poster are injected, so everything is tested offline.
```

- [ ] **Step 5: Run to verify green — this is the belt-and-suspenders step**

Run: `.venv/bin/python -m pytest tests/test_backends.py tests/test_backend_selection.py tests/test_backend_registry.py -v`
Expected: ALL pass, **with no edits to `test_backends.py` or `test_backend_selection.py`**. This proves the shim preserves `make_backend`'s observable behaviour: class selection, default endpoints, model defaulting, kwargs forwarding, api_key validation, unknown-name → `BackendError`, and the role-selection path in `Sluice.backend()` (which flows through `make_backend`).

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sluice tests`
Expected: full suite green; ruff clean. Confirm no import cycle: `.venv/bin/python -c "import sluice.core.backends; print(sluice.core.backends.make_backend('claude-max','m').model)"` prints `m`.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/backends.py tests/test_backend_registry.py
git commit -m "refactor(core): make_backend delegates provider construction to the seam registry

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: reconcile the docs the seam makes stale (+ regenerate rulesync)

**Files:**
- Modify: `sluice/core/plugins.py` (docstring), `docs/ARCHITECTURE.md`, `.rulesync/rules/CLAUDE.md`, `docs/superpowers/specs/2026-07-14-pluggable-core-design.md`
- Regenerate: `CLAUDE.md`, `AGENTS.md`, `.claude/` (gitignored outputs) via rulesync

No code changes and no new behaviour, so no new test — the deliverable is that the four documents no longer contradict the shipped seam. Verify with a full suite + ruff run so the doc-only commit is still gated.

> **USER-GATED (Steps 3 + 5).** Steps 3 and 5 edit `.rulesync/rules/CLAUDE.md` — the canonical, human-governed rules source the review agents read as ground truth — and regenerate its outputs. Per hard rule 15, an executing subagent must NOT self-apply these; they require explicit user sign-off (obtained before execution begins). Steps 1, 2, and 4 (the `docs/`, `plugins.py`, and spec edits) are not gated.

- [ ] **Step 1: Update `sluice/core/plugins.py`'s docstring**

The opening lines (1–9) say three seams had no registry. All four now do. Change:
```
`docs/ARCHITECTURE.md` names four adapter seams (backend, store, renderer, fetch).
Until now three of them had no registry, so `cli.py` constructed the implementations
itself -- twelve times -- and a second implementation of any seam meant editing
`cli.py` rather than adding a plugin. This generalises the pattern
```
to:
```
`docs/ARCHITECTURE.md` names four adapter seams (backend, store, renderer, fetch).
All four are now registry-backed: store/renderer/fetch gained a registry when this
module landed, and the backend seam joined them (`sluice/backends/`) once provider
construction moved off `make_backend`'s inline ladder. A second implementation of any
seam is now a drop-in plugin, not an edit to `cli.py`. This generalises the pattern
```

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`**

Rewrite the **backend** bullet in "Adapter-selector seams" (lines 105–107). Change:
```
- **backend**: `core/backends.py`; selected by ROLE (`primary` / `fallback`)
  rather than by provider, via `make_backend(name, ...)`. Keeps its own
  registry, which already worked.
```
to:
```
- **backend**: `sluice/backends/`, selected by provider name through the adapter
  registry (`make_backend` is now a thin shim over `plugins.get("backend", name)`).
  Implementations: `claude-max` (flat-rate `claude --print` CLI), `anthropic` (direct
  Messages API), `deepseek` and `openai` (OpenAI-compatible). Role selection
  (`auto`/`primary`/`fallback`) sits ABOVE the provider seam, in `Sluice.backend()`:
  the config picks which provider fills each role, the role picks which backend runs.
```

Then update the plugin-core prose (lines 81–83) so the package list includes backends. Change:
```
Implementations live in `sluice/stores/`, `sluice/fetchers/` and
`sluice/renderers/`, each self-registering on import exactly as
`ingest/sources/` already did.
```
to:
```
Implementations live in `sluice/stores/`, `sluice/fetchers/`, `sluice/renderers/`
and `sluice/backends/`, each self-registering on import exactly as
`ingest/sources/` already did.
```

- [ ] **Step 3: Reconcile `.rulesync/rules/CLAUDE.md`**

Update the seams convention (lines 127–129). Change:
```
- The four adapter seams (backend, store, renderer, fetch) each have exactly one implementation today
  and no runtime selector, because there is nothing yet to select between. Route new implementations
  through those seams rather than around them.
```
to (the wording must NOT claim all four resolve identically — the backend seam is name-keyed like the others but differs in shape, per Design decision 5; overstating uniformity here plants exactly the mental model DD5 dismantles):
```
- The four adapter seams (backend, store, renderer, fetch) are each a name-keyed registry resolved via
  `plugins.get`. The backend seam has four provider implementations (claude-max/anthropic/deepseek/openai)
  selected by name; store, renderer, and fetch have one each today and no runtime selection is exercised
  yet. The backend seam differs in shape, though: a role layer (auto/primary/fallback, in
  `Sluice.backend()`) sits above the provider lookup, and its factory takes resolved construction params
  (model/key/base_url), not the config object -- so it does not go through `Sluice._resolve` the way the
  other three do. Route new implementations through those seams (a self-registering module) rather than
  around them.
```

- [ ] **Step 4: Annotate the spec's superseded non-goal**

In `docs/superpowers/specs/2026-07-14-pluggable-core-design.md`, the non-goal (line 226) reads:
```
- Sources and backends keep their existing registries. They work; churning them buys nothing.
```
Append a superseded note (do not delete the original — it was true for Stage 1):
```
- Sources and backends keep their existing registries. They work; churning them buys nothing.
  _(Superseded for backends by Stage 2, 2026-07-15: the backend provider registry was unified
  into `core/plugins.py` via `sluice/backends/`; `make_backend` became a shim over it. Sources
  keep their own registry.)_
```

- [ ] **Step 5: Regenerate the AI-tool outputs from the canonical rulesync source**

Run: `npx rulesync@9.6.3 generate -t '*' -f '*'`
Expected: regenerates `CLAUDE.md`, `AGENTS.md`, `.claude/…` (all gitignored). This keeps the generated `CLAUDE.md` in step with the `.rulesync/` edit; editing only the generated file would be drift.

- [ ] **Step 6: Full verification**

Run: `.venv/bin/ruff check sluice tests && .venv/bin/python -m pytest -q`
Expected: ruff clean; full suite green (unchanged count from Task 2 — this task adds no tests).

- [ ] **Step 7: Commit** (stage only the canonical source + tracked docs; the regenerated outputs are gitignored)

```bash
git add sluice/core/plugins.py docs/ARCHITECTURE.md .rulesync/rules/CLAUDE.md \
        docs/superpowers/specs/2026-07-14-pluggable-core-design.md
git commit -m "docs: reconcile the backend seam across architecture, rulesync, and spec

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

## Self-review

**Spec/seed coverage** (against the plan's "Deferred to Stage 2" seed):
- "self-registering `sluice/backends/` package registering claude-max/anthropic/deepseek/openai" → Task 1 (package + four modules + guard suite).
- "define the creds helper BEFORE `autoload()`" → addressed by **Design decision 2**: creds stay in `core/app.py`, so the hazard is designed out; the only ordering constraint (`register` before `autoload`) matches the existing seam idiom.
- "add a test asserting `available("backend")` is non-empty so a silently-skipped provider fails the build" → Task 1, the module-level `assert _BACKENDS` plus `test_registry_covers_every_provider_and_matches_default_models`.
- "route `Sluice.backend()`'s provider construction through `plugins.get("backend", name)`; keep `make_backend` + its tests green (thin shim vs retire — decide in the plan)" → **Design decision 1** (shim) + Task 2. `Sluice.backend`'s helpers keep calling `make_backend`, which now consults the registry.
- "reconcile the spec's backend non-goal and `.rulesync/rules/CLAUDE.md`'s four-seams line" → Task 3 (+ `ARCHITECTURE.md`, `plugins.py` docstring, rulesync regenerate).
- "decide whether a `BackendSpec` value object earns its place" → **Design decision 3**: no.

**Placeholder scan:** every code step shows complete code; every run step gives an exact command and expected result. No TBD/TODO/"add error handling"/"similar to Task N".

**Type/name consistency:** the backend factory contract signature `(model, *, api_key, base_url, http, runner, timeout, max_tokens, claude_host, claude_path, effort)` is identical in the package docstring (Task 1 Step 4), all four factories (Task 1 Step 5), and `make_backend`'s delegation call (Task 2 Step 3). `_BACKEND_SEAM = "backend"` matches `SEAM = "backend"` and the `plugins.get("backend", …)` / `Sluice.available("backend")` string. The guard reuses `BackendError`, `DEFAULT_MODELS`, `DEFAULT_BASE_URLS`, `UnknownAdapter` exactly as they exist in `core/backends.py` / `core/plugins.py`.

**Behavioural-safety check:** `make_backend`'s existing tests (`tests/test_backends.py`, 33 refs) and `tests/test_backend_selection.py` are asserted green *with no edits* at Task 2 Step 5 — the definition of "observable behaviour unchanged". The one new exception path (`UnknownAdapter` → `BackendError`) is unreachable for the four registered providers (pinned equal to `DEFAULT_MODELS` by Task 1) and only fires on a broken install, where surfacing `BackendError` is strictly better than a raw `KeyError` — and it is pinned by its own dedicated test (`test_make_backend_translates_a_missing_plugin_to_backenderror`, Task 2 Step 1), because a branch nothing else reaches is a branch a later refactor can silently break.

**Review-plan findings folded in (2026-07-15):** TE-01 (Medium, missing-tests) → the translation test above. arch-01 (Medium, docs-drift) → the `.rulesync` wording in Task 3 Step 3 no longer claims "all four resolve the same way". GEN-1 (Low) → the second `ARCHITECTURE.md` edit now quotes its before-text. arch-03 (Low) → DD3 now names the duplication tradeoff and a revisit trigger. NEUT-1 / arch-02 (Low, corroborated ×3) → the `.rulesync` steps are marked USER-GATED. The plan was otherwise clean: no Critical, no High; invariant, neutrality, and generalist reviewers passed it, and the architect endorsed the seam boundary, the dependency direction, the shim-not-retire call, and the no-BackendSpec call.

---

## Execution handoff

Two options: **(1) Subagent-Driven (recommended)** — fresh subagent per task, two-stage (spec + quality) review between tasks, exactly the flow that landed Stage 1; **(2) Inline** — task-by-task in this session with checkpoints. This plan should first go through a `/review-plan` pass (its design decisions are the intended target of that review); revise per findings, then execute.
