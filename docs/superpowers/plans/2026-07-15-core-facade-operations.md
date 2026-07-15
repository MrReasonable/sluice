# Core Façade Operations Implementation Plan (Stage 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the pipeline operation wiring out of `cli.py` into the `Sluice` composition root — `Sluice` owns backend role-selection, the lazy dossier fetcher, and the track seen/lastrun files, and exposes value-returning `ingest()/triage()/compose_cv()/prep()/record()/track()/track_confirm()/normalize_statuses()` methods — so that "a surface (web UI, TUI, daemon) is a plugin" becomes true, with `cli.py` shrunk to argparse + printing.

**Two-stage decision (2026-07-15, after `review-plan`):** This is **Stage 1** — the spec-faithful façade. The design spec's follow-up (spec §"Status after implementation", line 205) asks only to *move the wiring in* and add the `Sluice.*` operation methods; the spec's non-goal (line 218) says *"backends keep their existing registries; churning them buys nothing."* So Stage 1 does **not** register a backend seam: `make_backend` stays the backend registry and `Sluice.backend()` owns only the role composition. **Stage 2** (a separate, later plan/PR — see the "Deferred to Stage 2" section at the end) will register `backend` as the 4th provider seam for uniformity with store/fetcher/renderer, once the façade has landed.

**Architecture:** Today `Sluice` resolves the store/fetcher/renderer adapters but owns no operations; each `cmd_*` re-builds the backend (`_select_backend` + creds helpers), the lazy dossier fetcher, and the seen/lastrun files, then calls the sub-app engine and prints. This plan moves that wiring into `Sluice` and exposes each operation as a method that **returns the engine's existing report dataclass**, calling `make_backend` directly for provider construction (no new registry). `cli.py` keeps only argparse, one method call per command, printing of the returned report, and `notify()` (a surface concern). The five engines are **not touched**.

**Tech Stack:** Python 3.12+ stdlib only (the `sluice/` discipline; `yaml` guarded, google libs lazy). pytest + faker. No new runtime dependencies.

## Global Constraints

Copied verbatim from the design spec and `CLAUDE.md`. Every task implicitly includes this section.

- **The existing suite passes unchanged.** A test that must be *edited* is evidence of a behaviour change to justify. The permitted edits are: (a) additive new tests; (b) the backend-selection guard suite is **relocated** to target `Sluice.backend()` with **every assertion preserved** (Task 1 — a move of *where* the logic lives, not *what* it does); (c) import-path updates for moved private helpers (Task 8's `_load_seen` etc.). Nothing else may change an assertion.
- **Fail loudly at construction.** Unknown backend role raises `BackendError` listing valid roles; unknown provider name still raises `BackendError` via the unchanged `make_backend` (do NOT change the exception class an existing test asserts). Never a silent fall-through to a default.
- **Lazy adapter resolution.** Constructing `Sluice` constructs no store, browser, or backend. Offline commands (`ingest list-sources`, `triage run --no-llm`) never touch Camofox or an LLM. Heavy imports stay INSIDE the `Sluice` methods, never at `app.py` module scope; `Sluice.__init__` builds nothing.
- **`sluice/` is standard-library only** (except guarded `yaml` and the lazily-imported google libs). No new runtime dependency.
- **Never-clobber / never-regress / CV-gate / neutrality / empty-config-abstains are untouched** — this moves wiring, not write logic. Do not alter any `*/engine.py`, `core/vault.py`, `core/status.py`, `cv/validate.py`, or any `*Config` preference default.
- **Selection by name, config-driven, defaults preserve today's behaviour exactly.** Backend roles `auto|primary|fallback` with deprecated aliases `claude-max→primary`, `deepseek→fallback`.
- **Conventional commits** (`refactor(triage): …`). End every commit message with:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Verification bar per task:** `.venv/bin/ruff check sluice tests` clean AND `.venv/bin/python -m pytest -q` green before commit (ruff 0.15.21 is in `.venv`, not the `[test]` extra).

---

## File structure

**New files:**
- `tests/test_app_operations.py` — the façade operation methods, driven with adapter/backend overrides + spies so they stay offline and assert the config→backend mapping.

**Moved/rewritten test:**
- `tests/test_cli_backend_selection.py` → the same behavioural assertions, retargeted at `Sluice.backend()` (renamed `tests/test_backend_selection.py`). Every case preserved: auto-degrades-to-primary, fallback-strict-is-fatal, unknown-name/role-raises, legacy aliases, base_url override.

**Modified files:**
- `sluice/core/app.py` — add `Sluice.backend()` + the private `_provider_creds`/`_make_primary`/`_make_fallback`/`_make_fallback_strict` helpers and the `_BACKEND_ROLES`/`_BACKEND_ALIASES` tables (moved from `cli.py`); add `dossier_cache()`, the track seen/lastrun module helpers, and the eight operation methods.
- `sluice/cli.py` — delete the moved backend/dossier/seen helpers; rewrite each `cmd_*` to call the matching `Sluice` method and print. Keep argparse (with `_BACKEND_CHOICES` redefined as a **literal** so nothing is stranded), `notify()`, the disabled/health overlay, `_print_report`/`_format_degraded`, and `cmd_test_source`.
- `docs/ARCHITECTURE.md` — describe the operation façade; remove the "resolves adapters but not operations" caveat.
- `sluice/core/app.py` module docstring — remove the "backend construction, dossier fetcher, seen/lastrun still in cli.py" caveat.
- `docs/superpowers/specs/2026-07-14-pluggable-core-design.md` — flip "Status after implementation" to done (façade owns the operations); the backend non-goal stays true (no seam added).

**Untouched (do not edit):** all five `*/engine.py`, `core/vault.py`, `core/status.py`, `core/backends.py` (incl. `make_backend`), `cv/validate.py`, every `*Config` preference default, `.rulesync/`.

---

### Task 1: move backend role-selection into `Sluice.backend()` + relocate its guard tests

**Files:**
- Modify: `sluice/core/app.py`, `sluice/cli.py`
- Move+rewrite test: `tests/test_cli_backend_selection.py` → `tests/test_backend_selection.py`

**Interfaces:**
- Consumes: `make_backend`, `BackendError`, `FallbackBackend` from `core/backends.py` (unchanged).
- Produces `Sluice.backend(role, *, primary_name, primary_model, effort, host, claude_path, fallback_name, fallback_model) -> backend`. Same behaviour as today's `cli._select_backend`: `auto` → `FallbackBackend(primary, fallback)`, degrading to bare primary (with a warning) when the fallback has no creds; `primary` → bare primary; `fallback` → the fallback alone, strict (missing key fatal); unknown role → `BackendError`; aliases `claude-max→primary`, `deepseek→fallback`.

- [ ] **Step 1: Move the existing guard test onto the new API (write it against `Sluice.backend`, expect red)**

First read `tests/test_cli_backend_selection.py` to capture EVERY assertion. Recreate each as a `Sluice(Config()).backend(role, primary_name=..., ...)` call in `tests/test_backend_selection.py`, preserving the behaviour exactly. Example (reproduce the full set from the original — do not drop a case):

```python
# tests/test_backend_selection.py
import pytest
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.backends import BackendError

def _b(role="auto", **kw):
    base = dict(primary_name="claude-max", primary_model="m", effort="max", host="",
                claude_path="claude", fallback_name="deepseek", fallback_model="cheap")
    base.update(kw)
    return Sluice(Config()).backend(role, **base)

def test_auto_degrades_to_bare_primary_without_a_fallback_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert _b("auto").__class__.__name__ == "ClaudeMaxBackend"

def test_auto_builds_a_fallback_pair_with_a_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert _b("auto").__class__.__name__ == "FallbackBackend"

def test_primary_role_ignores_the_fallback():
    assert _b("primary").__class__.__name__ == "ClaudeMaxBackend"

def test_fallback_role_missing_key_is_fatal(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(BackendError):
        _b("fallback")

def test_alias_claude_max_is_primary():
    assert _b("claude-max").__class__.__name__ == "ClaudeMaxBackend"

def test_unknown_role_raises_rather_than_defaulting_to_auto():
    with pytest.raises(BackendError):
        _b("primry")

def test_unknown_provider_name_still_raises_backenderror():
    # make_backend is unchanged, so a bad PROVIDER name keeps raising BackendError
    # (not a new KeyError) -- this pins that the exception class did not drift.
    with pytest.raises(BackendError):
        _b("primary", primary_name="bogus")
```

Port any remaining cases from the original (base_url override, deepseek/anthropic construction) verbatim. Then `git rm tests/test_cli_backend_selection.py`.

- [ ] **Step 2: Run to verify red**

Run: `.venv/bin/python -m pytest tests/test_backend_selection.py -v`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'backend'`.

- [ ] **Step 3: Move the helpers into `core/app.py` and implement `Sluice.backend`**

Move `_PROVIDER_ENV`, `_provider_creds`, `_make_primary`, `_make_fallback`, `_make_fallback_strict`, `_BACKEND_ROLES`, `_BACKEND_ALIASES` from `cli.py` into `core/app.py` (module-level for the env map/creds; the roles/aliases as `Sluice` class attributes). Then:

```python
    _BACKEND_ROLES = ("auto", "primary", "fallback")
    _BACKEND_ALIASES = {"claude-max": "primary", "deepseek": "fallback"}

    def backend(self, role, *, primary_name, primary_model, effort, host, claude_path,
                fallback_name, fallback_model):
        """cli.py's old _select_backend, moved verbatim in behaviour. auto degrades to
        bare primary (with a warning) when the fallback has no creds; fallback is strict.
        make_backend stays the provider factory -- an unknown provider name raises
        BackendError there, unchanged."""
        from sluice.core.backends import BackendError, FallbackBackend
        role = self._BACKEND_ALIASES.get(role, role or "auto")
        if role not in self._BACKEND_ROLES:
            raise BackendError(
                f"unknown backend choice '{role}' (expected "
                f"{', '.join([*self._BACKEND_ROLES, *self._BACKEND_ALIASES])})")
        if role == "fallback":
            return _make_fallback_strict(fallback_name, fallback_model)
        primary = _make_primary(primary_name, primary_model, effort=effort, host=host,
                                claude_path=claude_path)
        if role == "primary":
            return primary
        fallback = _make_fallback(fallback_name, fallback_model)
        return FallbackBackend(primary, fallback) if fallback else primary
```

(`_make_primary`/`_make_fallback`/`_make_fallback_strict`/`_provider_creds`/`_PROVIDER_ENV` are the exact bodies from `cli.py:227-316` — move them verbatim; they already call `make_backend`.)

In `cli.py`, replace the stranded `_BACKEND_CHOICES = [*_BACKEND_ROLES, *_BACKEND_ALIASES]` with a **literal** so argparse still has its choices without the moved tables:

```python
# cli.py -- KEEP for argparse; roles/aliases now live in Sluice.
_BACKEND_CHOICES = ["auto", "primary", "fallback", "claude-max", "deepseek"]
_BACKEND_HELP = (
    "which configured backend to use: auto (primary, falling back), primary, or "
    "fallback. claude-max/deepseek are deprecated aliases for primary/fallback.")
```

- [ ] **Step 4: Run to verify green**

Run: `.venv/bin/python -m pytest tests/test_backend_selection.py -v && .venv/bin/ruff check sluice tests`
Expected: PASS; ruff clean. (`cli.py` no longer references the moved names — grep to confirm: `git grep -n "_select_backend\|_make_primary\|_provider_creds" sluice/` returns only `sluice/core/app.py`. The per-command backend wrappers `_build_backend`/`_build_compose_backend`/`_track_backend` are removed in the operation tasks that replace their callers, or now, if nothing else references them.)

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py sluice/cli.py tests/test_backend_selection.py
git rm tests/test_cli_backend_selection.py
git commit -m "refactor(core): move backend role-selection into Sluice.backend()

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: `Sluice.dossier_cache()`

**Files:** Modify `sluice/core/app.py`; create `tests/test_app_operations.py`.

**Interfaces:** `Sluice.dossier_cache(dossier_dir, ttl_days) -> DossierCache` whose fetcher resolves `self.fetcher()` lazily on the first cache miss (moved from `cli._dossier_fetcher`).

- [ ] **Step 1: Write the failing test** (use the seeded-faker title fixture, not a bare stub, per neutrality nit)

```python
# tests/test_app_operations.py
from sluice.core.app import Sluice
from sluice.core.config import Config

class _FakeTab:
    def create_tab(self, url): return "t1"
    def evaluate(self, tab, js): return {"result": "JD BODY"}
    def close_tab(self, tab): return None

def test_dossier_cache_fetches_jd_via_the_fetcher_seam(tmp_path, titles):
    app = Sluice(Config(), fetcher=_FakeTab())
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    d = cache.get_or_build({"url": "https://example.invalid/job",
                            "company": "Acme", "title": titles[0]})
    assert d["jd"]["markdown"] == "JD BODY"

def test_dossier_cache_opens_no_browser_without_a_url(tmp_path, titles):
    class _Boom:
        def create_tab(self, url): raise AssertionError("must not be called")
    cache = Sluice(Config(), fetcher=_Boom()).dossier_cache(str(tmp_path), ttl_days=7)
    assert cache.get_or_build({"company": "Acme", "title": titles[0]})["jd"]["markdown"] == ""
```

(Confirm the `titles` fixture exists in `tests/conftest.py`; if the name differs, use the actual seeded-faker title fixture.)

- [ ] **Step 2: Run to verify it fails** — `.venv/bin/python -m pytest tests/test_app_operations.py -k dossier -v` → `AttributeError: dossier_cache`.

- [ ] **Step 3: Implement** (body moved from `cli._dossier_fetcher`, wrapped in `DossierCache`)

```python
    def dossier_cache(self, dossier_dir, ttl_days):
        """A DossierCache whose fetcher is resolved lazily on the first cache miss, so a
        --no-llm or fully-cached run never opens a browser. JD text read via
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

- [ ] **Step 4: Run to verify it passes** — `.venv/bin/python -m pytest tests/test_app_operations.py -k dossier -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/core/app.py tests/test_app_operations.py
git commit -m "feat(core): move the lazy dossier fetcher into Sluice.dossier_cache

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: `Sluice.triage()` + rewire `cmd_triage_run`

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:** `Sluice.triage(*, statuses=("new","research"), limit=None, dry_run=False, no_llm=False, backend_role="auto") -> TriageReport`. Builds `load_triage_config()`, `AuditLog(TRIAGE_AUDIT)`, the backend (unless `no_llm`) via `self.backend(backend_role, primary_name=tcfg.primary_backend, primary_model=tcfg.claude_max_model, effort=tcfg.claude_max_effort, host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path, fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)`, and `self.dossier_cache(DOSSIER_DIR, tcfg.ttl_days)`; calls `triage.engine.run(...)`. Returns `TriageReport`. No print/notify.

- [ ] **Step 1: Write the failing tests** — one behavioural, one that pins the config→backend mapping (spy on `Sluice.backend`, per tst-002/tst-004)

```python
# add to tests/test_app_operations.py
def test_triage_no_llm_builds_no_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    called = []
    monkeypatch.setattr(app, "backend", lambda *a, **k: called.append(k) or None)
    report = app.triage(no_llm=True)                    # deterministic path
    assert hasattr(report, "counts") and report.backend is None
    assert called == [], "no_llm must not construct a backend (lazy/offline guarantee)"

def test_triage_threads_the_triage_config_into_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    seen = {}
    monkeypatch.setattr(app, "backend", lambda role, **kw: seen.update(role=role, **kw))
    app.triage(backend_role="primary")
    assert seen["role"] == "primary"
    assert seen["primary_model"] == "claude-sonnet-4-5"   # triage uses claude_max_model
    assert seen["fallback_model"] == "deepseek-v4-flash"  # ...and cheap_model for fallback
```

- [ ] **Step 2: Run to verify red** — `.venv/bin/python -m pytest tests/test_app_operations.py -k triage -v`.

- [ ] **Step 3: Implement `Sluice.triage`**

```python
    def triage(self, *, statuses=("new", "research"), limit=None, dry_run=False,
               no_llm=False, backend_role="auto"):
        import os
        from sluice.triage.audit import AuditLog
        from sluice.triage.config import load_triage_config
        from sluice.triage.engine import run as _triage_run
        tcfg = load_triage_config()
        audit = AuditLog(os.environ.get("TRIAGE_AUDIT", "./triage-audit.jsonl"))
        backend = None if no_llm else self.backend(
            backend_role, primary_name=tcfg.primary_backend,
            primary_model=tcfg.claude_max_model, effort=tcfg.claude_max_effort,
            host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path,
            fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)
        cache = self.dossier_cache(os.environ.get("DOSSIER_DIR", "./dossiers"),
                                   tcfg.ttl_days)
        return _triage_run(self.store(), tcfg, backend, cache, audit,
                           statuses=tuple(statuses), limit=limit,
                           dry_run=dry_run, no_llm=no_llm)
```

- [ ] **Step 4: Rewire `cmd_triage_run`**

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

- [ ] **Step 5: Run tests** — `.venv/bin/python -m pytest tests/test_app_operations.py -k triage -v && .venv/bin/python -m pytest -q` → PASS; full suite green.

- [ ] **Step 6: Commit** — `refactor(triage): move triage wiring into Sluice.triage()` (+ trailer).

---

### Task 4: `Sluice.compose_cv()` + rewire `cmd_cv_run`

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:** `Sluice.compose_cv(*, lead=None, all_shortlist=False, limit=None, dry_run=False, no_serve=False, backend_role="auto") -> list[CvResult]`. `load_cv_config()`; `cvcfg.served_dir=""` when `no_serve`; `renderer = None if dry_run else self.renderer(cvcfg)` (byte-identical to `cli.py:376` — verified by the invariant reviewer to keep the gate safe). Backend via `self.backend(backend_role, primary_name=cvcfg.primary_backend, primary_model=cvcfg.compose_model, effort=cvcfg.compose_effort, host=cvcfg.compose_host, claude_path=cvcfg.compose_claude_path, fallback_name=cvcfg.fallback_backend, fallback_model=cvcfg.cheap_model)`. `self.dossier_cache(cvcfg.dossier_dir, cvcfg.ttl_days)`. `all_shortlist` → `run_batch`; else filter `store.read_leads({"shortlist"})` by `slug_matches(n, lead)` → `run_one` on the first match, or `[]` if none. Returns `list[CvResult]`.

- [ ] **Step 1: Write the failing tests** (empty-on-no-match + the cv config→backend mapping)

```python
# add to tests/test_app_operations.py
def test_compose_cv_unknown_lead_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    monkeypatch.setattr(app, "backend", lambda *a, **k: object())  # avoid real creds
    assert app.compose_cv(lead="no-such-lead", dry_run=True) == []

def test_compose_cv_threads_the_cv_config_into_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config()); seen = {}
    monkeypatch.setattr(app, "backend", lambda role, **kw: seen.update(**kw) or object())
    app.compose_cv(lead="x", dry_run=True)
    assert seen["primary_model"] == "claude-sonnet-4-5"   # cv uses compose_model
    assert seen["effort"] == "max"                        # ...and compose_effort
```

- [ ] **Step 2: Run to verify red.**
- [ ] **Step 3: Implement `Sluice.compose_cv`** (as in the interface; renderer resolved only when `not dry_run`).
- [ ] **Step 4: Rewire `cmd_cv_run`** — call `compose_cv(...)`, map `[]` + `not all_shortlist` to the existing `"cv: no shortlist lead matching '{lead}'"` stderr + exit 1, then print each result and notify on rendered, exactly as today.
- [ ] **Step 5: Run** — `-k compose_cv` then full suite → green.
- [ ] **Step 6: Commit** — `refactor(cv): move cv wiring into Sluice.compose_cv()` (+ trailer).

---

### Task 5: `Sluice.prep()` + `Sluice.record()` + rewire the apply commands

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- `Sluice.prep(*, lead=None, all_shortlist=False, limit=None, dry_run=False) -> list[PrepResult]`. `all_shortlist` → `engine.preview_all(store, cfg, limit=limit)`. `dry_run` (single) → `select.select_one` + `packet.build_packet(cv_staged=False)`, wrapped as one `PrepResult(lead, "previewed"|"skipped", packet=..., reason=...)`. Else → `[engine.prep_one(store, cfg, lead)]`.
- `Sluice.record(*, lead, ats=None, url=None, dry_run=False) -> dict` → `engine.record_one(self.store(), load_apply_config(), lead, ats=ats, url=url, dry_run=dry_run)`.

**Preserve observable output (tst/rev scope-gap + inv note):** before writing the CLI rewrite, `grep -n "apply-prep" tests/` and read the assertions. Today `cmd_apply_prep` dry-run prints `apply-prep: {lead} dry-run` (`cli.py:430`) and staged prints `apply-prep: {lead} staged`. The rewrite must print the SAME strings. Do NOT emit `apply-prep: {lead} previewed dry-run`. Confirm `PrepResult.status` ∈ `{staged, previewed, skipped, failed}` (from `apply/engine.py:12-19`) so the `previewed/skipped/staged` gating and tallies match the engine's real vocabulary.

- [ ] **Step 1: Write the failing tests** — `prep(all_shortlist=True)` returns `list[PrepResult]`; `record(lead="ghost", dry_run=True)["ok"] is False`.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement `prep` (with the dry-run `PrepResult` wrapping) and `record`.**
- [ ] **Step 4: Rewire `cmd_apply_prep`/`cmd_apply_record`** — preserving every stderr string byte-for-byte (dry-run → `apply-prep: {lead} dry-run`; staged → `apply-prep: {lead} staged`; refusal → `apply-prep: {lead} {status} ({reason})`).
- [ ] **Step 5: Run** — `-k "prep or record"` then full suite → green (existing apply CLI tests unchanged).
- [ ] **Step 6: Commit** — `refactor(apply): move apply prep/record wiring into Sluice` (+ trailer).

---

### Task 6: `Sluice.track()` + `Sluice.track_confirm()` + seen/lastrun handling + rewire

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- `Sluice.track(*, dry_run=False, backend_role="auto", client=None, now_iso=None) -> track RunReport`. `load_track_config()`; `seen = _load_seen(tcfg.seen_db)`; `since_iso = _load_lastrun(tcfg.seen_db + ".lastrun")`; `client = client or RealGoogleClient(tcfg.token_path)` (Google client is injected here, not a seam); backend from the track config; `now_iso = now_iso or datetime.now(timezone.utc).isoformat()`; `track.engine.run(...)`; on non-dry-run, `_save_seen` + `_save_lastrun`. Returns the engine `RunReport`.
- `Sluice.track_confirm(*, lead, to, when=None, dry_run=False) -> dict` → `track.engine.confirm(...)`.
- Move `_load_seen/_save_seen/_load_lastrun/_save_lastrun` from `cli.py` to `core/app.py` (module-level).

- [ ] **Step 1: Write the failing test** — dry-run persists nothing. **The fake must match the real client API** (tst-003): `track.engine.run` calls `client.search_messages(...)` (engine.py:50), not `messages_since`. Inspect `track/engine.py` and `track/google_client.py` for the exact methods called, then:

```python
# add to tests/test_app_operations.py
class _FakeGoogle:
    auth_error = False
    def search_messages(self, *a, **k): return []      # the method engine.run actually calls
    def get_message(self, *a, **k): return {}
    # add any other method track.engine.run invokes; keep all inert

def test_track_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRACK_SEEN_DB", str(tmp_path / "track-seen.db"))  # match the cfg source
    app = Sluice(Config())
    monkeypatch.setattr(app, "backend", lambda *a, **k: object())
    rep = app.track(dry_run=True, client=_FakeGoogle(),
                    now_iso="2026-07-15T00:00:00+00:00")
    assert hasattr(rep, "msgs")
    assert not (tmp_path / "track-seen.db").exists()
```

(Verify `load_track_config`'s `seen_db`/`token_path` source — env name vs config default — and match it.)

- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** `track`, `track_confirm`, and move the four seen/lastrun helpers verbatim.
- [ ] **Step 4: Rewire `cmd_track_run`/`cmd_track_confirm`** to call the methods and print exactly as today. If any existing CLI test monkeypatched `cli._load_seen` etc., update the import to `sluice.core.app._load_seen` (a move; note it in the commit).
- [ ] **Step 5: Run** — `-k track` then full suite → green.
- [ ] **Step 6: Commit** — `refactor(track): move track wiring + seen/lastrun into Sluice` (+ trailer).

---

### Task 7: `Sluice.ingest()` + `Sluice.normalize_statuses()` + rewire

**Files:** Modify `sluice/core/app.py`, `sluice/cli.py`; extend `tests/test_app_operations.py`.

**Interfaces:**
- `Sluice.ingest(sources, *, dry_run=False, json_sink=False, out=None) -> ingest RunReport`. `Ctx(camofox=self.fetcher(), config=self.config)`, `SeenDb()`, `HealthStore(SLUICE_HEALTH)`; `JsonSink(out or sys.stdout)` when `dry_run or json_sink` else `VaultSink(self.store(), seen)`; `ingest.engine.run(sources, ctx, sink, seen, health)`. Source selection stays in `cli.py`.
- `Sluice.normalize_statuses(*, dry_run=False) -> dict` → `self.store().normalize_all_statuses(dry_run=dry_run)`.

- [ ] **Step 1: Write the failing test** — `normalize_statuses(dry_run=True)` returns a dict with `{changed, unchanged, unknown}`.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement both** (heavy ingest imports inside the method).
- [ ] **Step 4: Rewire `cmd_run`** (selection stays in the CLI; pass the selected `srcs` to `ingest(...)`) and `cmd_triage_normalize`.
- [ ] **Step 5: Run** — `-k "ingest or normalize"` then full suite → green.
- [ ] **Step 6: Commit** — `refactor(ingest): move ingest + normalize wiring into Sluice` (+ trailer).

---

### Task 8: delete the dead cli.py wiring, update docs

**Files:** Modify `sluice/cli.py`, `docs/ARCHITECTURE.md`, `sluice/core/app.py` (docstring), `docs/superpowers/specs/2026-07-14-pluggable-core-design.md`.

- [ ] **Step 1: Delete now-dead `cli.py` code** — `_dossier_fetcher`, `_build_backend`, `_build_compose_backend`, `_track_backend`, and `_dossier_dir`/`_audit_path` if unused. (The backend role helpers and seen/lastrun helpers were already moved in Tasks 1/6.) KEEP `_BACKEND_CHOICES`/`_BACKEND_HELP` (argparse), `_health_path`, the disabled overlay + `_selected`, `_print_report`/`_format_degraded`, `cmd_test_source`, and the four ingest introspection commands.
- [ ] **Step 2: Prove nothing dangling** — `.venv/bin/ruff check sluice tests` (F821/F401 catches strays) and `git grep -n "_dossier_fetcher\|_build_backend\|_track_backend\|_load_seen\|_select_backend" sluice/` returns only `sluice/core/app.py`.
- [ ] **Step 3: Update `docs/ARCHITECTURE.md`** and the `core/app.py` module docstring — the façade now owns backend construction, the dossier cache, and seen/lastrun, and exposes the operation methods; remove the "still in cli.py" caveat. Flip the spec's "Status after implementation" to done. **The spec's backend non-goal (line 218) stays true — Stage 1 added no seam; do not edit it.**
- [ ] **Step 4: Full verification** — `.venv/bin/ruff check sluice tests && .venv/bin/python -m pytest -q` → clean + green.
- [ ] **Step 5: Commit** — `refactor(cli): shrink cli.py to argparse + printing; docs catch up` (+ trailer).

---

## Deferred to Stage 2 (separate plan + PR)

Per the 2026-07-15 decision, **Stage 2** registers `backend` as the 4th provider seam (uniformity with store/fetcher/renderer), after Stage 1 lands. Its own plan will:
- Add a self-registering `sluice/backends/` package registering `claude-max`/`anthropic`/`deepseek`/`openai` (define the creds helper BEFORE `autoload()`, and add a `test asserting available("backend")` is non-empty so a silently-skipped provider fails the build — the empty-registry fragility the architect flagged).
- Route `Sluice.backend()`'s provider construction through `plugins.get("backend", name)` instead of `make_backend` directly, deciding whether `make_backend` becomes a thin compatibility shim or is retired (keep its tests green either way).
- Reconcile the docs the seam makes stale: the spec's backend non-goal (line 218) and `.rulesync/rules/CLAUDE.md`'s "four seams … no runtime selector."
- Decide whether a `BackendSpec` value object earns its place at that point (unnecessary for Stage 1).

Stage 2 is a genuine design choice with a real trade-off (uniformity vs. a redundant second name-dispatch + a new empty-registry failure mode); it deserves its own `review-plan` pass.

---

## Self-review

**Spec coverage:** move backend/dossier/seen wiring into Sluice → Tasks 1, 2, 6; operation methods for every command → Tasks 3–7; shrink cli.py → each rewire + Task 8. Engines untouched → no engine in any Modify list. "Register backend as the 4th seam" → explicitly deferred to Stage 2 per the user's staging (Stage 1 is faithful to the spec's non-goal).

**Review findings addressed:** Critical guard-test-weakened → Task 1 *moves* the backend-selection guard suite preserving every assertion (not a delete). config-drift → Task 1 redefines `_BACKEND_CHOICES` as a literal. config→backend mapping untested → Tasks 3/4 add spy tests asserting the per-sub-app config fields. `_FakeGoogle` wrong method → Task 6 uses `search_messages`. Task 5 weak assertion → Tasks 3 spy asserts `backend` is never called under `no_llm`. apply-prep output drift → Task 5 preserves the exact stderr strings. dependency-order & empty-registry & unknown-provider exception class & BackendSpec necessity → all resolved by dropping the seam (no seam, `make_backend` unchanged so a bad provider name still raises `BackendError`). Neutrality title nit → Task 2 uses the `titles` fixture.

**Open items to verify against live code (call-outs, not placeholders):** the full assertion set in `test_cli_backend_selection.py` to port (Task 1); the `titles` fixture name in `conftest.py` (Task 2); `load_track_config`'s `seen_db`/`token_path` source and `track.engine.run`'s exact client methods (Task 6); the exact `apply-prep` stderr assertions (Task 5).

---

## Execution handoff

Two options: **(1) Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks; **(2) Inline** — task-by-task in this session with checkpoints. This revised plan directly implements the `review-plan` findings, so a full re-review is optional; a focused re-check of Task 1 (the guard-test move) is worthwhile before or during execution.
