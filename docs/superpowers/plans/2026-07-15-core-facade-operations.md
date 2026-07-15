# Core Façade Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the pipeline operation wiring out of `cli.py` into the `Sluice` composition root — register `backend` as the 4th adapter seam, add value-returning `Sluice.ingest()/.triage()/.compose_cv()/.prep()/.record()/.track()/.track_confirm()/.normalize_statuses()` methods, and shrink every `cmd_*` in `cli.py` to argparse + printing — so that "a surface (web UI, TUI, daemon) is a plugin" becomes true instead of nearly-true.

**Architecture:** Today `Sluice` resolves the store/fetcher/renderer adapters but owns none of the operations; each `cmd_*` in `cli.py` re-builds the backend (role selection), the lazy dossier fetcher, and the seen/lastrun files, then calls the sub-app engine and prints. This plan makes `Sluice` own that wiring and expose each operation as a method that **returns the engine's existing report dataclass**. `cli.py` keeps only argparse, one method call per command, printing of the returned report, and `notify()` (a surface concern). The five engines are **not touched** — they already take their dependencies as parameters; this is a wiring change, not a logic change.

**Tech Stack:** Python 3.12+ stdlib only (the `sluice/` discipline; `yaml` guarded, google libs lazy). pytest + faker for tests. No new runtime dependencies.

## Global Constraints

Copied verbatim from the design spec (`docs/superpowers/specs/2026-07-14-pluggable-core-design.md`) and `CLAUDE.md`. Every task's requirements implicitly include this section.

- **The existing suite passes unchanged.** This is a wiring change; a test that has to be *edited* to accommodate it is evidence of a behaviour change that needs justifying. New tests are additive. (Exception: a test that reaches into a `cli.py` private helper being *moved* may need its import path updated — see Task notes; that is a move, not a behaviour change.)
- **Fail loudly at construction.** An unknown backend/adapter/provider name RAISES and lists the valid names; never a silent fall-through to a default. Construction failures surface at build time, not first call.
- **Lazy adapter resolution.** Constructing `Sluice` must construct no store, browser, or backend. An offline command (`ingest list-sources`, `triage run --no-llm`) must never touch Camofox or an LLM backend. Adapters resolve on first use, cached.
- **`sluice/` is standard-library only** (except `yaml` under a guarded import and the google libs imported lazily inside functions). HTTP via `urllib`. No new runtime dependency.
- **Empty config abstains.** No preference gate may flip from abstain to match-nothing. Backend/adapter *names* are not preference gates (they fail loud on unknown), but do not touch the preference-gate defaults.
- **Never-clobber / never-regress / CV-gate / neutrality invariants are untouched by this PR** — it moves wiring, not write logic. Do not alter any engine, `core/vault.py`, `core/status.py`, `cv/validate.py`, or any preference default.
- **Selection is by name, in config, with a code default that preserves today's behaviour exactly:** `store: vault`, `fetcher: camofox`, `cv.renderer: script`. Backend roles: `auto|primary|fallback` with deprecated aliases `claude-max→primary`, `deepseek→fallback`.
- **Conventional commits** (`feat(core): …`, `refactor(cli): …`). End every commit message with the trailer:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Verification bar per task:** `.venv/bin/ruff check sluice tests` clean AND `.venv/bin/python -m pytest -q` green before commit. (ruff 0.15.21 lives in `.venv`; it is NOT in the `[test]` extra.)

---

## Design decision: how `backend` becomes the 4th seam

**This is the one non-mechanical decision in the plan and the thing to confirm before execution.**

`store`/`fetcher`/`renderer` are simple: config names ONE implementation, `factory(config)` builds it. `backend` is different: config names a **pair** (`primary_backend`, `fallback_backend`), and a runtime role (`--backend auto|primary|fallback`) chooses which of the pair is live, with per-sub-app models/effort/host. So the seam splits into two layers:

- **Provider layer (the registry).** The `backend` seam registers the four **providers** by name — `claude-max`, `anthropic`, `deepseek`, `openai` — in a new self-registering `sluice/backends/` package, mirroring `sluice/stores|fetchers|renderers/`. Each provider factory takes a `BackendSpec` and returns a backend object. The factory encodes the per-provider credential logic currently split across `_provider_creds`/`_make_primary`/`_make_fallback` (claude-max needs no key; the others read `*_API_KEY`/`*_BASE_URL` from the env). `config.primary_backend`/`config.fallback_backend` select provider names from this seam — exactly parallel to `store: vault`.
- **Role layer (owned by `Sluice`).** `Sluice.backend(role, *, primary, fallback)` composes the selected providers per role (`auto` → `FallbackBackend`, degrading to bare primary when the fallback has no creds; `primary` → bare primary; `fallback` → the fallback alone, strict). This is the current `_select_backend` logic, moved verbatim in behaviour.

`core/backends.py` (`make_backend`, the backend classes, `FallbackBackend`) is **kept unchanged** — the provider factories call `make_backend`, so its existing tests stay green. The seam is a thin, faithful layer on top: it makes backend config-selectable and discoverable via `Sluice.available("backend")`, consistent with the other three seams, without rewriting the provider construction that already works.

**Rejected alternative:** rewriting `make_backend` into the registry directly. It buys nothing, risks the "suite passes unchanged" bar, and duplicates dispatch logic.

---

## File structure

**New files:**
- `sluice/backends/__init__.py` — the seam package: `register(name, factory)` + `autoload` on import, mirroring `sluice/stores/__init__.py`. Also houses the shared env-credential helper `provider_creds(name)`.
- `sluice/backends/providers.py` — registers `claude-max`, `anthropic`, `deepseek`, `openai`, each `factory(spec: BackendSpec) -> backend`, delegating to `make_backend`.
- `tests/test_backends_seam.py` — the provider seam + `Sluice.backend()` role selection.
- `tests/test_app_operations.py` — the façade operation methods (`triage`, `compose_cv`, `prep`, `record`, `track`, `track_confirm`, `ingest`, `normalize_statuses`), driven with adapter/backend overrides so they stay offline.

**Modified files:**
- `sluice/core/protocols.py` — add `BackendSpec` (frozen dataclass) and a `Backend` Protocol stub.
- `sluice/core/app.py` — add `_BACKEND_SEAM`, extend `_import_plugins`; add `provider()`, `backend()`, the dossier-cache builder, the track seen/lastrun helpers, and the eight operation methods.
- `sluice/cli.py` — delete the backend role-selection block, `_dossier_fetcher`, and the seen/lastrun helpers; rewrite each `cmd_*` to call the matching `Sluice` method and print. Keep argparse, `notify()`, the disabled/health overlay, and `test-source`.
- `sluice.yaml.example` — document the `backend` seam names alongside `store`/`fetcher`.
- `docs/ARCHITECTURE.md` — describe the operation façade; remove the "resolves adapters but not operations" caveat.
- `docs/superpowers/specs/2026-07-14-pluggable-core-design.md` — flip the "Status after implementation" section to done.

**Untouched (do not edit):** all five `*/engine.py`, `core/vault.py`, `core/status.py`, `core/backends.py`, `cv/validate.py`, every `*Config` preference default.

---

### Task 1: `BackendSpec` + `Backend` protocol

**Files:**
- Modify: `sluice/core/protocols.py`
- Test: `tests/test_backends_seam.py` (create)

**Interfaces:**
- Produces: `BackendSpec(name: str, model: str = "", effort: str = "max", claude_host: str = "", claude_path: str = "claude", max_tokens: int | None = None)` — a frozen dataclass carrying everything a provider factory needs beyond env creds. `Backend` Protocol with `chat(...)`/`last_backend` left as structural documentation (the engines only read `getattr(backend, "last_backend", None)`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backends_seam.py
from sluice.core.protocols import BackendSpec

def test_backendspec_is_frozen_and_defaults_preserve_todays_behaviour():
    s = BackendSpec(name="claude-max")
    assert (s.model, s.effort, s.claude_host, s.claude_path, s.max_tokens) == \
           ("", "max", "", "claude", None)
    import dataclasses, pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "deepseek"   # a spec must not be mutated after construction
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backends_seam.py::test_backendspec_is_frozen_and_defaults_preserve_todays_behaviour -v`
Expected: FAIL with `ImportError: cannot import name 'BackendSpec'`.

- [ ] **Step 3: Add `BackendSpec` and the `Backend` protocol to `core/protocols.py`**

```python
# append to sluice/core/protocols.py
from dataclasses import dataclass

@dataclass(frozen=True)
class BackendSpec:
    """Everything a backend provider factory needs beyond env credentials. `effort`,
    `claude_host`, `claude_path` are claude-max knobs ignored by per-token providers;
    keeping them on one spec lets `Sluice.backend()` build either leg uniformly."""
    name: str
    model: str = ""
    effort: str = "max"
    claude_host: str = ""
    claude_path: str = "claude"
    max_tokens: int | None = None


class Backend(Protocol):
    """What every engine calls. Engines also read the optional `last_backend` attribute
    (which FallbackBackend sets) to report which leg served a request."""
    def chat(self, system: str, user: str) -> str: ...
```

(If `Protocol` is not yet imported in `protocols.py`, add `from typing import Protocol` — verify the existing imports first.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_backends_seam.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/protocols.py tests/test_backends_seam.py
git commit -m "feat(core): add BackendSpec + Backend protocol for the backend seam

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: the `backend` provider seam (`sluice/backends/`)

**Files:**
- Create: `sluice/backends/__init__.py`, `sluice/backends/providers.py`
- Test: `tests/test_backends_seam.py` (extend)

**Interfaces:**
- Consumes: `BackendSpec` (Task 1); `make_backend`, `BackendError` from `sluice/core/backends.py` (unchanged).
- Produces: `sluice.backends.register(name, factory)`, `sluice.backends.provider_creds(name) -> tuple[str, str]`. Registered seam names: `claude-max`, `anthropic`, `deepseek`, `openai`. Each factory: `factory(spec: BackendSpec) -> backend`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_backends_seam.py
from sluice.core.app import Sluice
from sluice.core import plugins

def test_all_four_providers_register_under_the_backend_seam():
    assert set(Sluice.available("backend")) == {"claude-max", "anthropic", "deepseek", "openai"}

def test_claude_max_provider_needs_no_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    factory = plugins.get("backend", "claude-max")
    b = factory(BackendSpec(name="claude-max", model="claude-sonnet-4-5"))
    assert b.__class__.__name__ == "ClaudeMaxBackend"

def test_per_token_provider_missing_key_raises_at_construction(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from sluice.core.backends import BackendError
    import pytest
    with pytest.raises(BackendError):
        plugins.get("backend", "deepseek")(BackendSpec(name="deepseek"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backends_seam.py -k providers_register -v`
Expected: FAIL — `UnknownAdapter`/empty `available("backend")` because nothing registers the seam yet (and `Sluice.available("backend")` currently raises `UnknownAdapter` from `_import_plugins`; Task 3 wires that — for now the failure is expected).

- [ ] **Step 3: Create the seam package**

```python
# sluice/backends/__init__.py
"""The backend provider seam: name -> provider factory, mirroring sluice/stores/.

`core/backends.py` (make_backend, the backend classes) stays where it is; this package
only gives each provider a registered name so config selects it the same way it selects a
store, and so Sluice.available("backend") lists them. Role composition (auto/primary/
fallback over a configured pair) is NOT here -- that is Sluice.backend()'s job, because a
role is a runtime choice over two configured providers, not a registered implementation.
"""
import os
import pkgutil

from sluice.core.plugins import autoload, register  # noqa: F401 (re-exported)

# Per-token providers authenticate with an API key and take an optional base_url override,
# both from the env. claude-max is absent: it shells the flat-rate CLI and needs no creds.
_PROVIDER_ENV = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
}


def provider_creds(name):
    """(api_key, base_url) for a provider, from the env. An unset *_BASE_URL yields "",
    which make_backend reads as 'use the provider default'."""
    key_var, url_var = _PROVIDER_ENV.get(name, ("", ""))
    if not key_var:
        return "", ""   # claude-max: flat-rate CLI, no credentials to resolve
    return os.environ.get(key_var, ""), os.environ.get(url_var, "")


autoload(__import__(f"{__name__}.providers", fromlist=["providers"]).__package__ and
         __import__("sluice.backends", fromlist=["providers"]))
```

Simplify the autoload to the same idiom the other packages use — check `sluice/stores/__init__.py` and copy it exactly. It should be:

```python
# replace the autoload line above with the exact idiom from stores/__init__.py, e.g.:
from sluice.core import plugins as _plugins
_plugins.autoload(__import__(__name__, fromlist=["_"]))
```

```python
# sluice/backends/providers.py
"""Registers the four backend providers under the `backend` seam."""
from sluice.backends import provider_creds, register
from sluice.core.backends import make_backend


def _make_claude_max(spec):
    # No credentials: claude-max shells the flat-rate CLI.
    return make_backend("claude-max", spec.model, effort=spec.effort,
                        claude_host=spec.claude_host, claude_path=spec.claude_path,
                        max_tokens=spec.max_tokens)


def _make_per_token(name):
    def factory(spec):
        api_key, base_url = provider_creds(name)
        # make_backend raises BackendError if api_key is missing -- fail loud at
        # construction, exactly as _make_fallback_strict did.
        return make_backend(name, spec.model, api_key=api_key, base_url=base_url,
                            max_tokens=spec.max_tokens)
    return factory


register("claude-max", _make_claude_max)
register("anthropic", _make_per_token("anthropic"))
register("deepseek", _make_per_token("deepseek"))
register("openai", _make_per_token("openai"))
```

**Note:** verify `make_backend`'s `register`-vs-import order — `sluice/backends/__init__.py` must import `providers` (via `autoload`) so the four `register()` calls run. Match `stores/__init__.py`'s pattern precisely so a broken provider is logged-and-skipped, not fatal.

- [ ] **Step 4: Run test to verify it passes** (after Task 3 wires `_import_plugins`)

Run: `.venv/bin/python -m pytest tests/test_backends_seam.py -v`
Expected: the `provider_creds`/factory tests PASS; `test_all_four_providers_register…` PASSES once Task 3 lands. If executing strictly in order, temporarily assert via `import sluice.backends; plugins.available("backend")` instead of `Sluice.available` until Task 3.

- [ ] **Step 5: Commit**

```bash
git add sluice/backends/__init__.py sluice/backends/providers.py tests/test_backends_seam.py
git commit -m "feat(core): register the four LLM providers under a backend seam

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: teach `Sluice` the backend seam + role selection

**Files:**
- Modify: `sluice/core/app.py`
- Test: `tests/test_backends_seam.py` (extend)

**Interfaces:**
- Consumes: the `backend` seam (Task 2), `BackendSpec` (Task 1), `FallbackBackend`/`BackendError` from `core/backends.py`.
- Produces on `Sluice`:
  - `provider(name, spec) -> backend` — resolve ONE provider via the seam (raises `UnknownAdapter` listing names).
  - `backend(role, *, primary: BackendSpec, fallback: BackendSpec) -> backend` — role composition. `role` in `auto|primary|fallback` (+ aliases `claude-max→primary`, `deepseek→fallback`); unknown role raises `BackendError`.
  - Extend `_import_plugins` to import `sluice.backends` for `_BACKEND_SEAM = "backend"`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_backends_seam.py
from sluice.core.protocols import BackendSpec

_P = BackendSpec(name="claude-max", model="m")
_F = BackendSpec(name="deepseek", model="cheap")

def test_role_auto_builds_a_fallback_pair_when_the_fallback_has_a_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    b = Sluice(Config()).backend("auto", primary=_P, fallback=_F)
    assert b.__class__.__name__ == "FallbackBackend"

def test_role_auto_degrades_to_bare_primary_when_fallback_key_absent(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    b = Sluice(Config()).backend("auto", primary=_P, fallback=_F)
    assert b.__class__.__name__ == "ClaudeMaxBackend"   # bare primary, no safety net

def test_role_primary_ignores_the_fallback(monkeypatch):
    b = Sluice(Config()).backend("primary", primary=_P, fallback=_F)
    assert b.__class__.__name__ == "ClaudeMaxBackend"

def test_alias_claude_max_maps_to_primary():
    b = Sluice(Config()).backend("claude-max", primary=_P, fallback=_F)
    assert b.__class__.__name__ == "ClaudeMaxBackend"

def test_unknown_role_raises_rather_than_defaulting_to_auto():
    from sluice.core.backends import BackendError
    import pytest
    with pytest.raises(BackendError):
        Sluice(Config()).backend("primry", primary=_P, fallback=_F)

def test_role_fallback_missing_key_is_fatal(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from sluice.core.backends import BackendError
    import pytest
    with pytest.raises(BackendError):
        Sluice(Config()).backend("fallback", primary=_P, fallback=_F)
```

(Import `Config` from `sluice.core.config` at the top of the test module.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backends_seam.py -k role -v`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'backend'`.

- [ ] **Step 3: Implement in `core/app.py`**

Add the seam constant and extend `_import_plugins`:

```python
_BACKEND_SEAM = "backend"      # add alongside _STORE_SEAM/_FETCHER_SEAM/_RENDERER_SEAM
```

```python
# in _import_plugins, add before the else/raise:
    elif seam == _BACKEND_SEAM:
        import sluice.backends  # noqa: F401
```

(And add `_BACKEND_SEAM` to the list passed to `UnknownAdapter` in the final `else`.)

Add the two methods to `Sluice`:

```python
    _BACKEND_ROLES = ("auto", "primary", "fallback")
    _BACKEND_ALIASES = {"claude-max": "primary", "deepseek": "fallback"}

    def provider(self, name, spec):
        """Resolve ONE backend provider via the seam. Raises UnknownAdapter listing the
        registered provider names, never a silent default -- writing to the wrong LLM is
        the same quiet-wrong-default the store seam guards against."""
        _import_plugins(_BACKEND_SEAM)
        return plugins.get(_BACKEND_SEAM, name)(spec)   # raises UnknownAdapter

    def backend(self, role, *, primary, fallback):
        """Compose the configured primary/fallback providers per role. This is cli.py's
        old _select_backend, moved verbatim in behaviour: auto degrades to bare primary
        when the fallback has no creds (and _make_per_token warns); fallback is strict."""
        from sluice.core.backends import BackendError, FallbackBackend
        role = self._BACKEND_ALIASES.get(role, role or "auto")
        if role not in self._BACKEND_ROLES:
            raise BackendError(
                f"unknown backend choice '{role}' (expected "
                f"{', '.join([*self._BACKEND_ROLES, *self._BACKEND_ALIASES])})")
        if role == "fallback":
            return self.provider(fallback.name, fallback)   # strict: missing key is fatal
        primary_b = self.provider(primary.name, primary)
        if role == "primary":
            return primary_b
        fallback_b = self._optional_fallback(fallback)
        return FallbackBackend(primary_b, fallback_b) if fallback_b else primary_b

    def _optional_fallback(self, spec):
        """The fallback leg, or None when its credentials are absent (a legitimate
        primary-only setup). Warns loudly -- a run with no safety net is degraded, and a
        401 at the moment the primary dies is worse than a build-time warning."""
        from sluice.backends import _PROVIDER_ENV, provider_creds
        api_key, _ = provider_creds(spec.name)
        if spec.name in _PROVIDER_ENV and not api_key:
            _log.warning(
                "fallback backend '%s' has no API key (%s unset): running with no "
                "fallback -- a primary failure will now fail the run",
                spec.name, _PROVIDER_ENV[spec.name][0])
            return None
        return self.provider(spec.name, spec)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_backends_seam.py -v`
Expected: PASS (all provider + role tests).

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_backends_seam.py
git commit -m "feat(core): Sluice owns backend role selection over the provider seam

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: dossier-cache builder on `Sluice`

**Files:**
- Modify: `sluice/core/app.py`
- Test: `tests/test_app_operations.py` (create)

**Interfaces:**
- Consumes: the `fetcher` seam (already on `Sluice`), `DossierCache` from `core/dossier.py`.
- Produces: `Sluice.dossier_cache(dir: str, ttl_days: int) -> DossierCache` — builds a `DossierCache` whose fetcher is the lazy closure (moved from `cli._dossier_fetcher`), resolving `self.fetcher()` only on the first cache miss.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_operations.py
from sluice.core.app import Sluice
from sluice.core.config import Config

class _FakeTab:
    def create_tab(self, url): return "t1"
    def evaluate(self, tab, js): return {"result": "JD BODY"}
    def close_tab(self, tab): return None

def test_dossier_cache_fetches_jd_via_the_fetcher_seam(tmp_path):
    app = Sluice(Config(), fetcher=_FakeTab())     # override the fetcher seam
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    d = cache.get_or_build({"url": "https://example.invalid/job", "company": "Acme", "title": "X"})
    assert d["jd"]["markdown"] == "JD BODY"

def test_dossier_cache_does_not_open_a_browser_without_a_url(tmp_path):
    class _Boom:
        def create_tab(self, url): raise AssertionError("must not be called")
    app = Sluice(Config(), fetcher=_Boom())
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    d = cache.get_or_build({"company": "Acme", "title": "X"})   # no url
    assert d["jd"]["markdown"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k dossier -v`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'dossier_cache'`.

- [ ] **Step 3: Implement in `core/app.py`**

```python
    def dossier_cache(self, dossier_dir, ttl_days):
        """A DossierCache whose fetcher is resolved lazily on the first cache miss, so a
        --no-llm or fully-cached run never opens a browser. JD text is read via
        evaluate(document.body.innerText) -- the same {"result": ...} shape ingest uses."""
        from sluice.core.dossier import DossierCache
        cam = {}

        def fetch(lead: dict) -> dict:
            md, url = "", lead.get("url")
            if url:
                if "client" not in cam:
                    cam["client"] = self.fetcher()
                c = cam["client"]
                tid = c.create_tab(url)
                if tid:
                    res = c.evaluate(tid, "document.body.innerText")
                    md = res.get("result") if isinstance(res, dict) else ""
                    c.close_tab(tid)
            return {"jd": {"markdown": md or ""}, "glassdoor": {}}

        return DossierCache(dossier_dir, ttl_days, fetcher=fetch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k dossier -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_app_operations.py
git commit -m "feat(core): move the lazy dossier fetcher into Sluice.dossier_cache

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: `Sluice.triage()` + rewire `cmd_triage_run`

**Files:**
- Modify: `sluice/core/app.py`, `sluice/cli.py`
- Test: `tests/test_app_operations.py` (extend)

**Interfaces:**
- Consumes: `Sluice.backend()` (Task 3), `Sluice.dossier_cache()` (Task 4), `Sluice.store()`.
- Produces: `Sluice.triage(*, statuses=("new","research"), limit=None, dry_run=False, no_llm=False, backend_role="auto") -> TriageReport`. Builds `load_triage_config()`, the `AuditLog(TRIAGE_AUDIT)`, the backend (unless `no_llm`) from the triage config's `primary_backend`/`claude_max_*`/`fallback_backend`/`cheap_model`, and the dossier cache, then calls `triage.engine.run(...)`. Returns the engine's `TriageReport` (`counts`, `judged`, `backend`, `failures`). Does NOT print or notify.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_app_operations.py
def test_triage_no_llm_returns_a_report_without_building_a_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "dossiers"))
    app = Sluice(Config())
    report = app.triage(no_llm=True)                 # deterministic path, no backend
    assert hasattr(report, "counts") and hasattr(report, "judged")
    assert report.backend is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k triage -v`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'triage'`.

- [ ] **Step 3: Implement `Sluice.triage`**

```python
    def triage(self, *, statuses=("new", "research"), limit=None, dry_run=False,
               no_llm=False, backend_role="auto"):
        import os
        from sluice.triage.audit import AuditLog
        from sluice.triage.config import load_triage_config
        from sluice.triage.engine import run as _triage_run
        from sluice.core.protocols import BackendSpec

        tcfg = load_triage_config()
        audit = AuditLog(os.environ.get("TRIAGE_AUDIT", "./triage-audit.jsonl"))
        backend = None if no_llm else self.backend(
            backend_role,
            primary=BackendSpec(tcfg.primary_backend, tcfg.claude_max_model,
                                tcfg.claude_max_effort, tcfg.claude_max_host,
                                tcfg.claude_max_path),
            fallback=BackendSpec(tcfg.fallback_backend, tcfg.cheap_model))
        cache = self.dossier_cache(os.environ.get("DOSSIER_DIR", "./dossiers"),
                                   tcfg.ttl_days)
        return _triage_run(self.store(), tcfg, backend, cache, audit,
                           statuses=tuple(statuses), limit=limit,
                           dry_run=dry_run, no_llm=no_llm)
```

- [ ] **Step 4: Rewire `cmd_triage_run` in `cli.py`**

```python
def cmd_triage_run(args, config) -> int:
    from sluice.core.app import Sluice
    statuses = tuple(s.strip() for s in (args.status or "new,research").split(",") if s.strip())
    report = Sluice(config).triage(statuses=statuses, limit=args.limit,
                                   dry_run=args.dry_run, no_llm=args.no_llm,
                                   backend_role=args.backend)
    print(f"triage: {report.counts} judged={report.judged} "
          f"backend={report.backend} failures={len(report.failures)}", file=sys.stderr)
    notify(f"sluice triage: {report.counts} (backend {report.backend})", config=config)
    return 0
```

- [ ] **Step 5: Run the triage tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k triage -v && .venv/bin/python -m pytest -q`
Expected: PASS; full suite green (existing triage CLI tests still pass — behaviour unchanged).

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_app_operations.py
git commit -m "refactor(triage): move triage wiring into Sluice.triage()

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: `Sluice.compose_cv()` + rewire `cmd_cv_run`

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- Produces: `Sluice.compose_cv(*, lead=None, all_shortlist=False, limit=None, dry_run=False, no_serve=False, backend_role="auto") -> list[CvResult]`. Loads `load_cv_config()`; when `no_serve`, sets `cvcfg.served_dir = ""`. Resolves the renderer via `self.renderer(cvcfg)` **unless `dry_run`** (dry-run must require no renderer). Builds the compose backend from the cv config (`compose_model`/`compose_effort`/`compose_host`/`compose_claude_path`). Builds `self.dossier_cache(cvcfg.dossier_dir, cvcfg.ttl_days)`. For `all_shortlist` → `run_batch(...)`; else filters `self.store().read_leads({"shortlist"})` by `slug_matches(n, lead)` and calls `run_one(...)` on the first match. Returns `list[CvResult]`. Raises `LookupError` (or returns `[]` — see below) when a `--lead` matches nothing.

  **Decision:** on no `--lead` match, `compose_cv` returns `[]`; `cmd_cv_run` maps `[]` to the existing `"cv: no shortlist lead matching …"` stderr line + exit 1. Keeps the façade side-effect-free (no printing) and preserves today's CLI behaviour.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_app_operations.py
def test_compose_cv_unknown_lead_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    assert app.compose_cv(lead="no-such-lead", dry_run=True) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k compose_cv -v`
Expected: FAIL — no attribute `compose_cv`.

- [ ] **Step 3: Implement `Sluice.compose_cv`**

```python
    def compose_cv(self, *, lead=None, all_shortlist=False, limit=None, dry_run=False,
                   no_serve=False, backend_role="auto"):
        from sluice.core.leads import slug_matches
        from sluice.core.protocols import BackendSpec
        from sluice.cv.config import load_cv_config
        from sluice.cv.engine import run_batch, run_one

        cvcfg = load_cv_config()
        if no_serve:
            cvcfg.served_dir = ""
        renderer = None if dry_run else self.renderer(cvcfg)
        backend = self.backend(
            backend_role,
            primary=BackendSpec(cvcfg.primary_backend, cvcfg.compose_model,
                                cvcfg.compose_effort, cvcfg.compose_host,
                                cvcfg.compose_claude_path),
            fallback=BackendSpec(cvcfg.fallback_backend, cvcfg.cheap_model))
        cache = self.dossier_cache(cvcfg.dossier_dir, cvcfg.ttl_days)
        store = self.store()
        if all_shortlist:
            return run_batch(store, cvcfg, backend, cache, renderer=renderer,
                             limit=limit, dry_run=dry_run)
        notes = [n for n in store.read_leads({"shortlist"}) if slug_matches(n, lead)]
        if not notes:
            return []
        return [run_one(notes[0], store, cvcfg, backend, cache,
                        renderer=renderer, dry_run=dry_run)]
```

- [ ] **Step 4: Rewire `cmd_cv_run`**

```python
def cmd_cv_run(args, config) -> int:
    from sluice.core.app import Sluice
    results = Sluice(config).compose_cv(lead=args.lead, all_shortlist=args.all_shortlist,
                                        limit=args.limit, dry_run=args.dry_run,
                                        no_serve=args.no_serve, backend_role=args.backend)
    if not results and not args.all_shortlist:
        print(f"cv: no shortlist lead matching '{args.lead}'", file=sys.stderr)
        return 1
    for r in results:
        print(f"cv: {r.status} {r.lead} served={r.served} "
              f"violations={len(r.violations)} audit_flags={len(r.audit_flags)}",
              file=sys.stderr)
    rendered = [r for r in results if r.status == "rendered"]
    if rendered:
        notify("sluice cv: " + "; ".join(
            f"{r.served} (audit flags: {len(r.audit_flags)})" for r in rendered),
            config=config)
    return 0
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k compose_cv -v && .venv/bin/python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_app_operations.py
git commit -m "refactor(cv): move cv wiring into Sluice.compose_cv()

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 7: `Sluice.prep()` + `Sluice.record()` + rewire the apply commands

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- Produces:
  - `Sluice.prep(*, lead=None, all_shortlist=False, limit=None, dry_run=False) -> list[PrepResult]`. `all_shortlist` → `engine.preview_all(store, cfg, limit=limit)`. `dry_run` (single lead) → `select.select_one` + `packet.build_packet(cv_staged=False)`, returned as a one-element `[PrepResult(lead=..., status="previewed"|"skipped", packet=..., reason=...)]` so the CLI can render uniformly. Else → `[engine.prep_one(store, cfg, lead)]`. The apply config comes from `load_apply_config()`. No backend, no dossier.
  - `Sluice.record(*, lead, ats=None, url=None, dry_run=False) -> dict` — thin: `engine.record_one(self.store(), load_apply_config(), lead, ats=ats, url=url, dry_run=dry_run)`.

  **Decision on `prep` dry-run shape:** wrap the dry-run `select_one`+`packet` path into a `PrepResult` so `prep()` always returns `list[PrepResult]` and the CLI has one rendering path. Map `select_one` returning `(None, reason)` → `PrepResult(lead, "skipped", reason=reason)`; a built packet → `PrepResult(lead, "previewed", packet=pkt)`. (This unifies the three CLI branches into one return type without changing observable output.)

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_app_operations.py
from sluice.apply.engine import PrepResult

def test_prep_all_shortlist_returns_prepresults(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    out = Sluice(Config()).prep(all_shortlist=True)
    assert isinstance(out, list) and all(isinstance(r, PrepResult) for r in out)

def test_record_refuses_unknown_lead(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    out = Sluice(Config()).record(lead="ghost", dry_run=True)
    assert out["ok"] is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k "prep or record" -v`
Expected: FAIL — no attributes `prep`/`record`.

- [ ] **Step 3: Implement `Sluice.prep` and `Sluice.record`**

```python
    def prep(self, *, lead=None, all_shortlist=False, limit=None, dry_run=False):
        from sluice.apply import engine, packet, select
        from sluice.apply.config import load_apply_config
        cfg = load_apply_config()
        store = self.store()
        if all_shortlist:
            return engine.preview_all(store, cfg, limit=limit)
        if dry_run:
            note, reason = select.select_one(store, lead, cfg)
            if note is None:
                return [engine.PrepResult(lead=lead, status="skipped", reason=reason)]
            pkt = packet.build_packet(note, cfg, cv_staged=False)
            return [engine.PrepResult(lead=lead, status="previewed", packet=pkt)]
        return [engine.prep_one(store, cfg, lead)]

    def record(self, *, lead, ats=None, url=None, dry_run=False):
        from sluice.apply import engine
        from sluice.apply.config import load_apply_config
        return engine.record_one(self.store(), load_apply_config(), lead,
                                 ats=ats, url=url, dry_run=dry_run)
```

- [ ] **Step 4: Rewire `cmd_apply_prep` and `cmd_apply_record`**

```python
def cmd_apply_prep(args, config) -> int:
    from sluice.core.app import Sluice
    from sluice.apply import packet
    results = Sluice(config).prep(lead=args.lead, all_shortlist=args.all_shortlist,
                                  limit=args.limit, dry_run=args.dry_run)
    if args.all_shortlist:
        for r in results:
            if r.status == "previewed":
                print(packet.render_json(r.packet) if args.json else packet.render_text(r.packet))
        eligible = sum(1 for r in results if r.status == "previewed")
        skipped = sum(1 for r in results if r.status == "skipped")
        print(f"apply-preview: eligible={eligible} skipped={skipped}", file=sys.stderr)
        return 0
    r = results[0]
    if r.status in ("staged", "previewed"):
        print(packet.render_json(r.packet) if args.json else packet.render_text(r.packet))
        print(f"apply-prep: {r.lead} {r.status}"
              f"{' dry-run' if args.dry_run else ''}", file=sys.stderr)
        return 0
    print(f"apply-prep: {r.lead} {r.status} ({r.reason})", file=sys.stderr)
    return 1


def cmd_apply_record(args, config) -> int:
    from sluice.core.app import Sluice
    out = Sluice(config).record(lead=args.lead, ats=args.ats, url=args.url,
                                dry_run=args.dry_run)
    if out["ok"]:
        f = out["fields"]
        print(f"apply-record: {args.lead} -> applied "
              f"(ats={f['ats']} cv={f['applied_cv']})", file=sys.stderr)
        return 0
    print(f"apply-record: {args.lead} refused (status={out['reason']})", file=sys.stderr)
    return 1
```

**Verify:** confirm the pre-refactor `cmd_apply_prep` stderr wording for the dry-run/staged cases against existing CLI tests (there may be a `tests/test_cli*` asserting exact strings). If a test asserts the exact old dry-run line (`apply-prep: {lead} dry-run`), match it — adjust the f-string above so the observable output is byte-identical.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k "prep or record" -v && .venv/bin/python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_app_operations.py
git commit -m "refactor(apply): move apply prep/record wiring into Sluice

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 8: `Sluice.track()` + `Sluice.track_confirm()` + seen/lastrun handling + rewire

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- Produces:
  - `Sluice.track(*, dry_run=False, backend_role="auto", client=None, now_iso=None) -> track RunReport`. Loads `load_track_config()`. Reads `seen` from the plaintext `tcfg.seen_db` and `since_iso` from `tcfg.seen_db + ".lastrun"` (helpers moved from cli). Builds `client = client or RealGoogleClient(tcfg.token_path)` (the `client` param is the test seam — a Google client is not a registered adapter seam, so it is injected here, mirroring `overrides`). Builds the backend from the track config. `now_iso = now_iso or datetime.now(timezone.utc).isoformat()`. Calls `track.engine.run(...)`. On a non-dry-run, saves `seen` and `lastrun`. Returns the engine's `RunReport`.
  - `Sluice.track_confirm(*, lead, to, when=None, dry_run=False) -> dict` — thin: `track.engine.confirm(self.store(), load_track_config(), lead, to, when=when, dry_run=dry_run)`.
- Moves `_load_seen/_save_seen/_load_lastrun/_save_lastrun` from `cli.py` into `core/app.py` as module-level helpers (or private methods).

  **`now_iso` seam:** expose `now_iso` as a parameter (default = real clock) so a test can pin it without monkeypatching `datetime`, and so `datetime.now` is not called at import.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_app_operations.py
class _FakeGoogle:
    def __init__(self): self.auth_error = False
    def messages_since(self, *a, **k): return []      # match RealGoogleClient's read API
    # add whatever no-op methods track.engine.run calls; keep it inert

def test_track_dry_run_writes_no_seen_file(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "track-seen.db"))  # if track cfg reads env
    app = Sluice(Config())
    rep = app.track(dry_run=True, client=_FakeGoogle(), now_iso="2026-07-15T00:00:00+00:00")
    assert hasattr(rep, "msgs")
    assert not (tmp_path / "track-seen.db").exists()   # dry-run persists nothing
```

**Note:** inspect `track/engine.run`'s use of `client` and `load_track_config`'s `seen_db`/`token_path` source (env vs config) before finalising the fake and the env var name; match the real read API so the fake is a faithful stand-in.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k track -v`
Expected: FAIL — no attribute `track`.

- [ ] **Step 3: Implement `Sluice.track`, `Sluice.track_confirm`, and the seen/lastrun helpers**

```python
# module-level helpers in core/app.py (moved verbatim from cli.py)
def _load_seen(path):
    try:
        with open(path) as f:
            return set(line.strip() for line in f if line.strip())
    except OSError:
        return set()

def _save_seen(path, seen):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(sorted(seen)))

def _load_lastrun(path):
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None

def _save_lastrun(path, iso):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(iso)
```

```python
    def track(self, *, dry_run=False, backend_role="auto", client=None, now_iso=None):
        from datetime import datetime, timezone
        from sluice.core.protocols import BackendSpec
        from sluice.track.config import load_track_config
        from sluice.track.engine import run as _track_run
        from sluice.track.google_client import RealGoogleClient

        tcfg = load_track_config()
        lastrun_path = tcfg.seen_db + ".lastrun"
        seen = _load_seen(tcfg.seen_db)
        since_iso = _load_lastrun(lastrun_path)
        client = client if client is not None else RealGoogleClient(tcfg.token_path)
        backend = self.backend(
            backend_role,
            primary=BackendSpec(tcfg.primary_backend, tcfg.claude_max_model,
                                tcfg.claude_max_effort, tcfg.claude_max_host,
                                tcfg.claude_max_path),
            fallback=BackendSpec(tcfg.fallback_backend, tcfg.cheap_model))
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        rep = _track_run(self.store(), tcfg, client, backend, seen=seen,
                         now_iso=now_iso, since_iso=since_iso, dry_run=dry_run)
        if not dry_run:
            _save_seen(tcfg.seen_db, seen)
            _save_lastrun(lastrun_path, now_iso)
        return rep

    def track_confirm(self, *, lead, to, when=None, dry_run=False):
        from sluice.track.config import load_track_config
        from sluice.track.engine import confirm
        return confirm(self.store(), load_track_config(), lead, to, when=when,
                       dry_run=dry_run)
```

- [ ] **Step 4: Rewire `cmd_track_run` and `cmd_track_confirm`**

```python
def cmd_track_run(args, config) -> int:
    from sluice.core.app import Sluice
    rep = Sluice(config).track(dry_run=args.dry_run, backend_role=args.backend)
    if rep.auth_error:
        print("track: google reauth needed (token refresh failed)", file=sys.stderr)
        return 1
    print(f"track: msgs={rep.msgs} classified={rep.classified} auto={rep.auto} "
          f"proposed={rep.proposed} calendar_added={rep.calendar_added} "
          f"failures={rep.failures}", file=sys.stderr)
    for p in rep.proposals:
        print(f"  PROPOSAL {p}", file=sys.stderr)
    return 0


def cmd_track_confirm(args, config) -> int:
    from sluice.core.app import Sluice
    out = Sluice(config).track_confirm(lead=args.lead, to=args.to, when=args.when,
                                       dry_run=args.dry_run)
    if out["ok"]:
        print(f"track-confirm: {args.lead} {out['from']} -> {out['to']}", file=sys.stderr)
        return 0
    print(f"track-confirm: {args.lead} refused ({out['reason']})", file=sys.stderr)
    return 1
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k track -v && .venv/bin/python -m pytest -q`
Expected: PASS; full suite green. (If an existing CLI test monkeypatched `cli._load_seen` etc., update its import to `sluice.core.app._load_seen` — a move, not a behaviour change; note it in the commit.)

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_app_operations.py
git commit -m "refactor(track): move track wiring + seen/lastrun into Sluice

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 9: `Sluice.ingest()` + `Sluice.normalize_statuses()` + rewire

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- Produces:
  - `Sluice.ingest(sources, *, dry_run=False, json_sink=False, out=sys.stdout) -> ingest RunReport`. Builds `Ctx(camofox=self.fetcher(), config=self.config)`, `SeenDb()`, `HealthStore(SLUICE_HEALTH)`. Chooses `JsonSink(out)` when `dry_run or json_sink`, else `VaultSink(self.store(), seen)`. Calls `ingest.engine.run(sources, ctx, sink, seen, health)`. Returns `RunReport`. (Source SELECTION — the enabled/disabled overlay — stays in `cli.py`; the façade takes an explicit `sources` list, so a web UI passes whichever sources it wants.)
  - `Sluice.normalize_statuses(*, dry_run=False) -> dict` — thin: `self.store().normalize_all_statuses(dry_run=dry_run)`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_app_operations.py
def test_normalize_statuses_delegates_to_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    summary = Sluice(Config()).normalize_statuses(dry_run=True)
    assert set(summary) >= {"changed", "unchanged", "unknown"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k normalize -v`
Expected: FAIL — no attribute `normalize_statuses`.

- [ ] **Step 3: Implement both methods**

```python
    def ingest(self, sources, *, dry_run=False, json_sink=False, out=None):
        import sys
        from sluice.core.health import HealthStore
        from sluice.core.seendb import SeenDb
        from sluice.ingest.base import Ctx
        from sluice.ingest.engine import run as _ingest_run
        from sluice.ingest.sink import JsonSink, VaultSink
        import os

        ctx = Ctx(camofox=self.fetcher(), config=self.config)
        seen = SeenDb()
        health = HealthStore(os.environ.get("SLUICE_HEALTH", "./sluice_health.json"))
        if dry_run or json_sink:
            sink = JsonSink(out or sys.stdout)   # dry-run never writes vault or seen.db
        else:
            sink = VaultSink(self.store(), seen)
        return _ingest_run(sources, ctx, sink, seen, health)

    def normalize_statuses(self, *, dry_run=False):
        return self.store().normalize_all_statuses(dry_run=dry_run)
```

- [ ] **Step 4: Rewire `cmd_run` and `cmd_triage_normalize`**

```python
def cmd_run(args, config) -> int:
    from sluice.core.app import Sluice
    disabled = _load_disabled()
    srcs = _selected(args, config, disabled)       # selection stays in the CLI
    if not srcs:
        _log.warning("no enabled sources selected")
        return 1
    report = Sluice(config).ingest(srcs, dry_run=args.dry_run,
                                   json_sink=(args.sink == "json"))
    _print_report(report)
    if report.degraded:
        notify(_format_degraded(report), config=config)
    return 0


def cmd_triage_normalize(args, config) -> int:
    from sluice.core.app import Sluice
    summary = Sluice(config).normalize_statuses(dry_run=args.dry_run)
    print(f"status normalize: changed={summary['changed']} "
          f"unchanged={summary['unchanged']} "
          f"conflicts={summary.get('conflicts', [])} "
          f"unknown={sorted(set(summary['unknown']))}"
          f"{' (dry-run)' if args.dry_run else ''}")
    return 0
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_app_operations.py -k "ingest or normalize" -v && .venv/bin/python -m pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_app_operations.py
git commit -m "refactor(ingest): move ingest + normalize wiring into Sluice

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 10: delete the dead cli.py wiring, update docs + example

**Files:** Modify `sluice/cli.py`, `sluice.yaml.example`, `docs/ARCHITECTURE.md`, `docs/superpowers/specs/2026-07-14-pluggable-core-design.md`.

**Interfaces:** none new — this removes now-unused code and squares the docs with reality.

- [ ] **Step 1: Delete the moved helpers from `cli.py`**

Remove, now that nothing references them (grep first — see Step 2): `_PROVIDER_ENV`, `_BACKEND_ROLES`, `_BACKEND_ALIASES`, `_BACKEND_HELP` is KEPT (argparse still needs `_BACKEND_CHOICES`/`_BACKEND_HELP` for the `--backend` flag), `_provider_creds`, `_make_primary`, `_make_fallback`, `_make_fallback_strict`, `_select_backend`, `_build_backend`, `_build_compose_backend`, `_track_backend`, `_dossier_fetcher`, `_load_seen`, `_save_seen`, `_load_lastrun`, `_save_lastrun`, and the now-unused `_dossier_dir`/`_audit_path` if nothing else uses them.

**KEEP in cli.py:** `_BACKEND_CHOICES` + `_BACKEND_HELP` (argparse `choices=`/`help=` for `--backend`), `_health_path`, `_load_disabled`/`_save_disabled`/`_is_enabled`/`_selected` (source-selection overlay), `_print_report`/`_format_degraded`, `cmd_test_source`, `cmd_list_sources`/`cmd_enable`/`cmd_disable`/`cmd_health`.

- [ ] **Step 2: Prove nothing dangling**

Run: `.venv/bin/python -c "import ast,sys; ast.parse(open('sluice/cli.py').read())"` then
`.venv/bin/ruff check sluice tests` (ruff F811/F401/unused will catch a stranded reference or import).
Expected: ruff clean. Also grep: `git grep -n "_select_backend\|_dossier_fetcher\|_build_backend\|_build_compose_backend\|_track_backend\|_load_seen" sluice/` should return only `sluice/core/app.py`.

- [ ] **Step 3: Update `sluice.yaml.example`** — document the backend seam names next to `store`/`fetcher`:

```yaml
# Which implementation fills each seam. Selection is by NAME; an unknown name raises at
# construction and lists the valid ones. `backend` names below select LLM PROVIDERS; the
# --backend flag (auto|primary|fallback) chooses which of the two is live per run.
store: vault
fetcher: camofox
# primary_backend / fallback_backend live in the triage:/cv:/track: blocks (per-sub-app
# models differ); valid provider names: claude-max, anthropic, deepseek, openai.
```

- [ ] **Step 4: Update `docs/ARCHITECTURE.md` and the spec** — describe the operation façade (`Sluice` now owns backend construction, the dossier cache, seen/lastrun, and exposes `ingest/triage/compose_cv/prep/record/track/track_confirm/normalize_statuses`); remove the "resolves adapters but not operations" caveat in `core/app.py`'s module docstring; flip the spec's "Status after implementation" to "done — the façade owns the operations; a surface drives `Sluice` without duplicating `cli.py`."

- [ ] **Step 5: Full verification**

Run: `.venv/bin/ruff check sluice tests && .venv/bin/python -m pytest -q`
Expected: ruff clean; full suite green (same count as before + the new `test_backends_seam.py`/`test_app_operations.py`).

- [ ] **Step 6: Commit**

```bash
git add sluice/cli.py sluice.yaml.example docs/ARCHITECTURE.md docs/superpowers/specs/2026-07-14-pluggable-core-design.md sluice/core/app.py
git commit -m "refactor(cli): shrink cli.py to argparse + printing; docs catch up

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

## Self-review

**Spec coverage:**
- "register backend as the 4th seam" → Tasks 1–3 (seam + role selection). ✓
- "move `_build_backend`, `_dossier_fetcher`, seen/lastrun into Sluice" → Tasks 3, 4, 8. ✓
- "add `Sluice.triage()/.compose_cv()/.prep()/.record()/.track()`" → Tasks 5–9 (plus `track_confirm`, `ingest`, `normalize_statuses` for completeness — every command has a home). ✓
- "shrink cli.py to argparse + printing" → each rewire task + Task 10 deletion. ✓
- "engines do not change" → no engine file is in any task's Modify list. ✓
- "existing suite passes unchanged" → every rewire task ends with the full suite green; the only permitted test edits are import-path updates for moved private helpers (Task 8 note). ✓
- "fail loud on unknown name" → `provider()` raises `UnknownAdapter`; `backend()` raises `BackendError` on unknown role (Task 3 tests). ✓
- "lazy resolution / offline commands untouched" → `triage(no_llm=True)` builds no backend (Task 5 test); `dossier_cache` opens no browser without a url (Task 4 test); adapters still resolve on first use. ✓

**Placeholder scan:** the `sluice/backends/__init__.py` autoload line is the one spot flagged to "copy the exact idiom from `stores/__init__.py`" — the executing engineer MUST open that file and match it (registration ordering is load-bearing: the package import must trigger `providers`' `register()` calls). Every other step has concrete code.

**Type consistency:** `BackendSpec(name, model, effort, claude_host, claude_path, max_tokens)` is used identically in Tasks 3/5/6/8. `PrepResult`, `CvResult`, `TriageReport`, track `RunReport` field names match the explorer inventory. `backend(role, *, primary, fallback)` signature is consistent across all call sites.

**Open items for the executing engineer to verify against live code (called out inline, not placeholders):**
1. The exact `autoload` idiom in `stores/__init__.py` (Task 2).
2. `load_track_config`'s `seen_db`/`token_path` source and `track/engine.run`'s `client` API, to make `_FakeGoogle` faithful (Task 8).
3. Any existing `tests/test_cli*` that asserts exact stderr strings for apply-prep dry-run or monkeypatches a moved `cli` helper (Tasks 7, 8) — match byte-for-byte / update the import.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-15-core-facade-operations.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks, fast iteration. Best here because each task is an independently reviewable slice and the repo's review gate (CodeRabbit + the sluice-* agents) is strong.
2. **Inline Execution** — execute tasks in this session with checkpoints.

Before execution, this plan should itself go through the repo's `review-plan` skill (invariant + neutrality + reviewer + test-engineer + architect), since it touches every command and the backend seam is a genuine design decision.
