# `sluice doctor` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `sluice doctor` — a fast, read-only preflight that proves each configured backend (primary and fallback, in every sub-app) is actually usable, reporting `ok`/`degraded`/`dead` and exiting non-zero when a run-blocking backend is dead.

**Architecture:** A pure `sluice/core/doctor.py` holds the dataclasses, the sub-app×role enumeration (deduped so a shared backend is probed once), the role-aware classification rules, and the exit-code logic. `Sluice.doctor(*, offline, probe)` in `core/app.py` is the thin impure layer: it resolves creds via the existing `_provider_creds`, builds each provider directly via the unchanged `make_backend`, and runs an injectable one-token round-trip. `cmd_doctor` in `cli.py` formats the report and returns its exit code. This mirrors the codebase's pure-module + thin-`Sluice`-method + thin-`cmd_*` split (`health.py`, the façade PRs).

**Tech Stack:** Python 3.12+ stdlib only (`shutil.which`, `time.monotonic`). pytest + faker. No new runtime dependency, no new config knob.

**Spec:** `docs/superpowers/specs/2026-07-16-sluice-doctor-design.md`.

## Global Constraints

Copied verbatim from the spec and `CLAUDE.md`. Every task implicitly includes this section.

- **Backends only.** No store/fetcher/renderer/vault/Camofox health in this PR. `doctor` reads existing config; it adds **no new config knob** and **no new runtime dependency** (`sluice/` stays stdlib-only, `yaml` guarded, google libs lazy).
- **`doctor` is a reader.** It changes **no** run-path behaviour and touches **no** invariant. Do **not** edit `make_backend`, `Sluice.backend`, the role/alias tables, any `*/engine.py`, `core/vault.py`, `core/status.py`, `cv/validate.py`, or any `*Config` default. The never-clobber / never-regress / CV-gate / empty-config-abstains / neutrality invariants are untouched.
- **The existing suite passes unchanged.** This feature is purely additive: no existing test may have an assertion edited. Only new tests are added.
- **Lazy adapter resolution (the offline guarantee).** `Sluice.doctor` must never call `self.store()` or `self.fetcher()` — it touches only the backend seam. Heavy imports stay INSIDE the method, never at `app.py` module scope. `sluice doctor` must construct no browser and no store.
- **Fail loudly at construction.** An unknown provider name still raises `BackendError` via the unchanged `make_backend`; `doctor` catches it and reports `dead` rather than crashing. A quiet wrong default is the bug class this codebase engineers out.
- **Cost honesty.** The live round-trip runs **once per distinct backend** (dedup) and uses a tiny prompt. `max_tokens` is **not** tightly capped — the OpenAI-compatible backend treats `finish_reason=length` as a hard error, so an over-tight cap would manufacture a false `dead`.
- **Neutrality: no personal data.** No employer names, locations, hostnames, or absolute paths in `sluice/` or `tests/`. Fixtures stay synthetic.
- **Conventional commits** (`feat(cli): …`, `test(doctor): …`, `docs: …`). End every commit message with:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- **Verification bar per task:** `.venv/bin/ruff check sluice tests` clean AND `.venv/bin/python -m pytest -q` green before commit (ruff 0.15.21 lives in `.venv`, not the `[test]` extra).

---

## File structure

**New files:**
- `sluice/core/doctor.py` — pure: `BackendTarget`, `RoleUse`, `BackendCheck`, `DoctorReport`, `PROBE_PROMPT`, states, `enumerate_targets`, `classify`, `format_roles`. Zero I/O.
- `tests/test_doctor.py` — the whole feature: pure-module unit tests, `Sluice.doctor` integration tests (injected probe + monkeypatched env/`which`), and `cmd_doctor` CLI exit-code tests.

**Modified files:**
- `sluice/core/app.py` — add the `Sluice.doctor(*, offline=False, probe=None) -> DoctorReport` method. Nothing else changes; `_PROVIDER_ENV`/`_provider_creds` are reused as-is.
- `sluice/cli.py` — add `cmd_doctor`, the `_print_doctor` formatter, and the `doctor` subparser; add `sluice doctor` to the module docstring's command list.
- `docs/ARCHITECTURE.md` — document the `doctor` command / the backend-preflight (a reader over the backend seam).

**Untouched (do not edit):** `core/backends.py` (incl. `make_backend`), `Sluice.backend` and the role/alias tables, all five `*/engine.py`, `core/vault.py`, `core/status.py`, `cv/validate.py`, every `*Config` default, **and `.rulesync/` (the canonical human-gated tree — the new command is self-documenting via `--help`, the `cli.py` docstring, and `docs/ARCHITECTURE.md`, so no rulesync edit is needed for this PR)**.

---

### Task 1: pure `core/doctor.py` — targets, classification, report

**Files:**
- Create: `sluice/core/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: nothing (pure; the three sub-app config objects are passed in by the caller).
- Produces:
  - States: `OK = "ok"`, `DEGRADED = "degraded"`, `DEAD = "dead"`; `PROBE_PROMPT: str`.
  - `RoleUse(subapp: str, role: str)` — a `(sub-app, role)` reference (`role` ∈ `{"primary","fallback"}`).
  - `BackendTarget(provider: str, model: str, host: str, claude_path: str, uses: list[RoleUse])` with a property `is_primary: bool` (any use is a primary).
  - `BackendCheck(target: BackendTarget, state: str, detail: str, elapsed: float | None = None)`.
  - `DoctorReport(checks: list[BackendCheck])` with `exit_code(*, strict: bool = False) -> int`.
  - `enumerate_targets(triage_cfg, cv_cfg, track_cfg) -> list[BackendTarget]` — sub-app×role, deduped by `(provider, model, host)`, preserving first-seen order.
  - `classify(target, *, known, needs_key, key_present, key_var, cli_present, offline, probe_error) -> BackendCheck`.
  - `format_roles(uses: list[RoleUse]) -> str` — e.g. `"primary · triage, cv, track"`.

- [ ] **Step 1: Write the failing tests for the pure module**

Create `tests/test_doctor.py`:

```python
"""sluice doctor: the pure enumeration/classification core, the Sluice.doctor
wiring (with an injected probe so it stays offline), and the cmd_doctor exit
codes. Everything here is hermetic -- no network, no browser, no real LLM."""
from dataclasses import dataclass

import pytest

from sluice.core import doctor
from sluice.core.doctor import (
    DEAD, DEGRADED, OK, BackendCheck, BackendTarget, DoctorReport, RoleUse,
    classify, enumerate_targets, format_roles,
)


# ── fakes: minimal config objects with just the fields enumerate reads ────────
@dataclass
class _Triage:
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"


@dataclass
class _Cv:
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    compose_model: str = "claude-sonnet-4-5"
    compose_host: str = ""
    compose_claude_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"


@dataclass
class _Track:
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"


def _target(provider="deepseek", model="deepseek-v4-flash", host="",
            claude_path="claude", roles=(("triage", "fallback"),)):
    return BackendTarget(provider=provider, model=model, host=host,
                         claude_path=claude_path,
                         uses=[RoleUse(s, r) for s, r in roles])


# ── enumerate_targets ─────────────────────────────────────────────────────────
def test_enumerate_dedupes_shared_backends():
    # Defaults: all three sub-apps share claude-max@sonnet-4-5 (primary) and
    # deepseek@v4-flash (fallback) -> exactly two targets, each with three uses.
    targets = enumerate_targets(_Triage(), _Cv(), _Track())
    assert len(targets) == 2
    by_provider = {t.provider: t for t in targets}
    assert set(by_provider) == {"claude-max", "deepseek"}
    assert {u.subapp for u in by_provider["claude-max"].uses} == {"triage", "cv", "track"}
    assert all(u.role == "primary" for u in by_provider["claude-max"].uses)
    assert by_provider["claude-max"].is_primary is True
    assert by_provider["deepseek"].is_primary is False


def test_enumerate_splits_on_per_subapp_model_override():
    # A cv-only model override must NOT collapse into triage/track's claude-max
    # target -- that is the "live model id, per sub-app" guarantee.
    cv = _Cv(compose_model="claude-opus-4-1")
    targets = enumerate_targets(_Triage(), cv, _Track())
    claude = [t for t in targets if t.provider == "claude-max"]
    assert len(claude) == 2
    models = {t.model for t in claude}
    assert models == {"claude-sonnet-4-5", "claude-opus-4-1"}


# ── classify ──────────────────────────────────────────────────────────────────
def test_classify_unknown_provider_is_dead_even_offline():
    t = _target(provider="gpt5", roles=(("triage", "fallback"),))
    c = classify(t, known=False, needs_key=True, key_present=False,
                 key_var="", cli_present=None, offline=True, probe_error=None)
    assert c.state == DEAD
    assert "gpt5" in c.detail


def test_classify_keyless_primary_is_dead():
    t = _target(provider="deepseek", roles=(("triage", "primary"),))
    c = classify(t, known=True, needs_key=True, key_present=False,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == DEAD
    assert "DEEPSEEK_API_KEY" in c.detail


def test_classify_keyless_fallback_is_degraded():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=False,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == DEGRADED
    assert "DEEPSEEK_API_KEY" in c.detail
    assert "primary-only" in c.detail


def test_classify_offline_ok_when_static_checks_pass():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=True,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=True,
                 probe_error=None)
    assert c.state == OK
    assert "offline" in c.detail


def test_classify_offline_claude_cli_missing_is_dead():
    t = _target(provider="claude-max", model="claude-sonnet-4-5",
                roles=(("triage", "primary"),))
    c = classify(t, known=True, needs_key=False, key_present=False,
                 key_var="", cli_present=False, offline=True, probe_error=None)
    assert c.state == DEAD
    assert "claude" in c.detail.lower()


def test_classify_live_probe_error_is_dead():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=True,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error="HTTP 401 from api.deepseek.com: bad key")
    assert c.state == DEAD
    assert "401" in c.detail


def test_classify_live_ok():
    t = _target(provider="deepseek", roles=(("triage", "fallback"),))
    c = classify(t, known=True, needs_key=True, key_present=True,
                 key_var="DEEPSEEK_API_KEY", cli_present=None, offline=False,
                 probe_error=None)
    assert c.state == OK


# ── DoctorReport.exit_code ────────────────────────────────────────────────────
def _check(state):
    return BackendCheck(target=_target(), state=state, detail="")


def test_exit_code_dead_is_nonzero():
    rep = DoctorReport(checks=[_check(OK), _check(DEAD)])
    assert rep.exit_code() == 1


def test_exit_code_degraded_is_zero_by_default_nonzero_strict():
    rep = DoctorReport(checks=[_check(OK), _check(DEGRADED)])
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 1


def test_exit_code_all_ok_is_zero():
    rep = DoctorReport(checks=[_check(OK), _check(OK)])
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 0


def test_format_roles_groups_by_role_in_order():
    uses = [RoleUse("triage", "primary"), RoleUse("cv", "primary"),
            RoleUse("track", "primary")]
    assert format_roles(uses) == "primary · triage, cv, track"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.core.doctor'` (or import errors).

- [ ] **Step 3: Write `sluice/core/doctor.py`**

```python
"""sluice doctor: prove the configured backends are actually usable.

Pure, zero-I/O core. The impure half -- resolving creds, building a provider,
running a one-token round-trip -- lives in `Sluice.doctor` (core/app.py); the
formatting and exit-code plumbing live in `cli.py`. This module owns only the
rules: what backends are configured (enumeration), and given a set of resolved
facts about one of them, is it ok / degraded / dead (classification).

The classification is ROLE-AWARE, and that is the whole point. The default
install ships a keyless `deepseek` fallback, which `_make_fallback` already
treats as a sanctioned degrade to primary-only -- so a keyless *fallback* is
`degraded` (exit 0), while a keyless *primary* (a run cannot happen) is `dead`.
A backend whose credentials ARE present but whose round-trip fails is `dead`
regardless of role: that is the silently-non-functional fallback this tool
exists to catch -- the one you believe in and never test until the primary
dies.
"""
from dataclasses import dataclass, field

# The three states, as bare strings so callers (cli formatter, exit_code) and
# tests share one vocabulary without importing an enum.
OK = "ok"
DEGRADED = "degraded"
DEAD = "dead"

# The round-trip prompt. Tiny on purpose -- a per-token backend costs a token or
# two to answer it, and the answer is discarded (only "did complete() raise?"
# matters). Deliberately NOT paired with a tight max_tokens cap: the
# OpenAI-compatible backend treats finish_reason=length as a hard error, so
# capping the completion would manufacture a false `dead`.
PROBE_PROMPT = "Reply with the single word: ok"


@dataclass(frozen=True)
class RoleUse:
    """One (sub-app, role) pair that references a backend target. A single
    target can be referenced by several -- e.g. the shared deepseek fallback is
    used by triage, cv, and track."""
    subapp: str
    role: str  # "primary" | "fallback"


@dataclass
class BackendTarget:
    """One distinct configured backend, after deduping identical
    (provider, model, host) across sub-apps and roles. `claude_path` is only
    meaningful for the claude-max CLI; `host` is "" for a local backend."""
    provider: str
    model: str
    host: str
    claude_path: str
    uses: list = field(default_factory=list)  # list[RoleUse]

    @property
    def is_primary(self) -> bool:
        """True if ANY use is a primary. A backend that serves as a primary
        anywhere must satisfy the strict primary rule (a keyless primary is
        dead), even if it is also used as a fallback elsewhere."""
        return any(u.role == "primary" for u in self.uses)


@dataclass
class BackendCheck:
    target: BackendTarget
    state: str
    detail: str
    elapsed: float | None = None  # round-trip seconds, when one was run


@dataclass
class DoctorReport:
    checks: list  # list[BackendCheck]

    def exit_code(self, *, strict: bool = False) -> int:
        """Non-zero iff a run-blocking backend is dead. `--strict` additionally
        fails on any degraded backend (the cron mode that enforces a believed-in
        fallback)."""
        if any(c.state == DEAD for c in self.checks):
            return 1
        if strict and any(c.state == DEGRADED for c in self.checks):
            return 1
        return 0


def enumerate_targets(triage_cfg, cv_cfg, track_cfg) -> list:
    """Every sub-app × role backend, deduped by (provider, model, host).

    Apply is absent: it is offline by contract and has no backend. The fallback
    leg carries host="" and claude_path="claude" because that is exactly how
    `_make_fallback` builds it -- it does NOT forward the primary's host/path --
    so doctor probes what a real run would actually build.

    Effort is deliberately NOT part of the dedup key: it changes cost/quality,
    not whether the backend works, so triage(medium)+cv(max) fold into one
    claude-max probe. A per-sub-app MODEL override does split, preserving the
    per-sub-app "is this a live model id" check.
    """
    specs = [
        # (subapp, role, provider, model, host, claude_path)
        ("triage", "primary", triage_cfg.primary_backend, triage_cfg.claude_max_model,
         triage_cfg.claude_max_host, triage_cfg.claude_max_path),
        ("triage", "fallback", triage_cfg.fallback_backend, triage_cfg.cheap_model, "", "claude"),
        ("cv", "primary", cv_cfg.primary_backend, cv_cfg.compose_model,
         cv_cfg.compose_host, cv_cfg.compose_claude_path),
        ("cv", "fallback", cv_cfg.fallback_backend, cv_cfg.cheap_model, "", "claude"),
        ("track", "primary", track_cfg.primary_backend, track_cfg.claude_max_model,
         track_cfg.claude_max_host, track_cfg.claude_max_path),
        ("track", "fallback", track_cfg.fallback_backend, track_cfg.cheap_model, "", "claude"),
    ]
    by_key: dict = {}  # (provider, model, host) -> BackendTarget, insertion-ordered
    for subapp, role, provider, model, host, claude_path in specs:
        key = (provider, model, host)
        target = by_key.get(key)
        if target is None:
            target = BackendTarget(provider=provider, model=model, host=host,
                                   claude_path=claude_path)
            by_key[key] = target
        target.uses.append(RoleUse(subapp, role))
    return list(by_key.values())


def classify(target, *, known, needs_key, key_present, key_var, cli_present,
             offline, probe_error) -> BackendCheck:
    """The rules table, as a pure function of already-resolved facts.

    - known:       provider name is in the backend registry (else a config typo)
    - needs_key:   this provider authenticates with an API key (claude-max: no)
    - key_present: that key was resolved in THIS process
    - key_var:     the env var name, for the detail message
    - cli_present: for a local (no-host) claude-max checked offline, whether the
                   `claude` binary is on PATH; None when not applicable
    - offline:     config-only mode (no round-trip was attempted)
    - probe_error: the BackendError message if a live round-trip ran and failed,
                   else None
    """
    if not known:
        return BackendCheck(target, DEAD, f"unknown backend '{target.provider}'")
    if needs_key and not key_present:
        if target.is_primary:
            return BackendCheck(target, DEAD, f"{key_var} unset")
        return BackendCheck(target, DEGRADED, f"{key_var} unset — primary-only")
    if offline:
        if cli_present is False:
            return BackendCheck(
                target, DEAD, f"CLI '{target.claude_path}' not on PATH")
        return BackendCheck(target, OK, "(offline: not round-tripped)")
    if probe_error is not None:
        return BackendCheck(target, DEAD, probe_error)
    return BackendCheck(target, OK, "round-trip ok")


def format_roles(uses: list) -> str:
    """Group a target's uses by role for display, primaries first:
    "primary · triage, cv, track; fallback · cv"."""
    by_role: dict = {}
    for u in uses:
        by_role.setdefault(u.role, []).append(u.subapp)
    parts = []
    for role in ("primary", "fallback"):
        subs = by_role.get(role)
        if subs:
            parts.append(f"{role} · {', '.join(subs)}")
    return "; ".join(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: PASS (all pure-module tests green).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check sluice tests
git add sluice/core/doctor.py tests/test_doctor.py
git commit -m "$(cat <<'EOF'
feat(core): pure backend-preflight core for sluice doctor (#4)

enumerate_targets (sub-app×role, deduped), the role-aware classify rules
(keyless fallback degrades, keyed-but-broken is dead), and DoctorReport.exit_code.
No I/O; the impure build+probe lands next.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 2: `Sluice.doctor()` — resolve, build, probe

**Files:**
- Modify: `sluice/core/app.py` (add one method; reuse the existing `_PROVIDER_ENV`/`_provider_creds`)
- Test: `tests/test_doctor.py` (append the integration section)

**Interfaces:**
- Consumes: `enumerate_targets`, `classify`, `DoctorReport`, `PROBE_PROMPT` from `core/doctor.py` (Task 1); `_PROVIDER_ENV`, `_provider_creds` (module-level in `app.py`, unchanged); `make_backend`, `BackendError`, `DEFAULT_MODELS` from `core/backends.py` (unchanged); `load_triage_config`/`load_cv_config`/`load_track_config`.
- Produces: `Sluice.doctor(*, offline: bool = False, probe=None) -> DoctorReport`. `probe` is `callable(backend) -> None` (raises `BackendError` on failure); defaults to `lambda b: b.complete(PROBE_PROMPT)`. Never calls `self.store()` or `self.fetcher()`.

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_doctor.py`:

```python
# ── Sluice.doctor (impure wiring, with an injected probe so it stays offline) ──
from sluice.core.app import Sluice           # noqa: E402
from sluice.core.backends import (           # noqa: E402
    BackendError, ClaudeMaxBackend, OpenAiCompatibleBackend,
)


def _ok_probe(backend):
    """A round-trip that always succeeds -- returns nothing, raises nothing."""
    return None


def _deepseek_dies_probe(backend):
    """Succeed for claude-max, fail for the per-token (deepseek) backend --
    the exact 'keyed but silently non-functional fallback' scenario."""
    if isinstance(backend, OpenAiCompatibleBackend):
        raise BackendError("HTTP 401 from api.deepseek.com: invalid key")
    return None


def test_doctor_live_all_ok(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rep = Sluice().doctor(probe=_ok_probe)
    states = {c.target.provider: c.state for c in rep.checks}
    assert states == {"claude-max": OK, "deepseek": OK}
    assert rep.exit_code() == 0


def test_doctor_live_keyed_fallback_broken_is_dead(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rep = Sluice().doctor(probe=_deepseek_dies_probe)
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["claude-max"] == OK
    assert states["deepseek"] == DEAD          # believed-in fallback, actually dead
    assert rep.exit_code() == 1                # dead -> non-zero even without --strict


def test_doctor_keyless_fallback_is_degraded_not_probed(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []
    rep = Sluice().doctor(probe=lambda b: calls.append(b))
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["deepseek"] == DEGRADED
    # the keyless fallback is classified WITHOUT a round-trip (nothing to test)
    assert all(not isinstance(b, OpenAiCompatibleBackend) for b in calls)
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 1


def test_doctor_offline_skips_probe_and_checks_claude_cli(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    calls = []
    rep = Sluice().doctor(offline=True, probe=lambda b: calls.append(b))
    assert calls == []                          # offline never round-trips
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["claude-max"] == OK           # CLI present
    assert states["deepseek"] == DEGRADED       # keyless fallback
    assert rep.exit_code() == 0


def test_doctor_offline_reports_dead_when_claude_cli_absent(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    rep = Sluice().doctor(offline=True, probe=_ok_probe)
    states = {c.target.provider: c.state for c in rep.checks}
    assert states["claude-max"] == DEAD
    assert rep.exit_code() == 1


def test_doctor_never_builds_a_store_or_browser(monkeypatch):
    # The offline guarantee: doctor touches only the backend seam.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(Sluice, "store",
                        lambda self: pytest.fail("doctor resolved a store"))
    monkeypatch.setattr(Sluice, "fetcher",
                        lambda self: pytest.fail("doctor resolved a fetcher"))
    Sluice().doctor(probe=_ok_probe)            # must not fail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q -k "doctor_live or doctor_offline or doctor_keyless or doctor_never"`
Expected: FAIL — `AttributeError: 'Sluice' object has no attribute 'doctor'`.

- [ ] **Step 3: Add `Sluice.doctor` to `sluice/core/app.py`**

Add this method to the `Sluice` class, immediately before the `# ── introspection ──` section (i.e. after `track_confirm`). Keep all imports inside the method (the offline discipline):

```python
    def doctor(self, *, offline=False, probe=None):
        """Preflight every configured backend (primary + fallback, per sub-app):
        is the provider known, is a model resolved, are the credentials present
        in THIS process, and -- unless `offline` -- does a one-token round-trip
        succeed? Returns a DoctorReport whose `exit_code` is non-zero when a
        run-blocking backend is dead.

        Backends only: this method touches neither `self.store()` nor
        `self.fetcher()`, so `sluice doctor` never constructs a browser or a
        store. `probe` is the test seam -- a `callable(backend) -> None` that
        raises `BackendError` on failure; it defaults to the real round-trip.
        The provider is built DIRECTLY via `make_backend` (not the role
        composite), so there is no `FallbackBackend` to disentangle, and it is
        built ONLY when there is something testable -- a known provider whose
        credentials are satisfied -- so a keyless per-token backend is
        classified from config alone, never by catching a construction error."""
        import shutil
        import time

        from sluice.core import doctor as _doctor
        from sluice.core.backends import DEFAULT_MODELS, BackendError, make_backend
        from sluice.cv.config import load_cv_config
        from sluice.track.config import load_track_config
        from sluice.triage.config import load_triage_config

        targets = _doctor.enumerate_targets(
            load_triage_config(), load_cv_config(), load_track_config())
        if probe is None:
            probe = lambda b: b.complete(_doctor.PROBE_PROMPT)  # noqa: E731

        checks = []
        for t in targets:
            known = t.provider in DEFAULT_MODELS
            needs_key = t.provider in _PROVIDER_ENV
            key_var = _PROVIDER_ENV.get(t.provider, ("", ""))[0]
            api_key, base_url = _provider_creds(t.provider)
            key_present = bool(api_key)
            # A local (no-host) backend that needs no key IS the claude-max CLI;
            # for the offline mode we can only check the binary exists on PATH.
            cli_present = None
            if known and not needs_key and not t.host:
                cli_present = shutil.which(t.claude_path) is not None
            # Round-trip ONLY when live AND buildable+testable: known provider,
            # creds satisfied. Everything else is classified from config alone.
            probe_error = None
            elapsed = None
            if not offline and known and (not needs_key or key_present):
                try:
                    backend = make_backend(
                        t.provider, t.model, api_key=api_key, base_url=base_url,
                        claude_host=t.host, claude_path=t.claude_path)
                    start = time.monotonic()
                    probe(backend)
                    elapsed = time.monotonic() - start
                except BackendError as e:
                    probe_error = str(e)
            check = _doctor.classify(
                t, known=known, needs_key=needs_key, key_present=key_present,
                key_var=key_var, cli_present=cli_present, offline=offline,
                probe_error=probe_error)
            check.elapsed = elapsed
            checks.append(check)
        return _doctor.DoctorReport(checks=checks)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: PASS (pure + integration).

- [ ] **Step 5: Run the full suite to prove nothing regressed**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — the prior count (485) plus the new doctor tests, nothing edited.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check sluice tests
git add sluice/core/app.py tests/test_doctor.py
git commit -m "$(cat <<'EOF'
feat(core): Sluice.doctor resolves, builds, and probes each backend (#4)

Reuses _provider_creds + make_backend to check creds-in-process and run an
injectable one-token round-trip per distinct backend. Touches only the backend
seam -- never a store or browser. Live/offline/keyless/keyed-broken covered.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 3: `cmd_doctor` + argparse + formatter

**Files:**
- Modify: `sluice/cli.py` (add `cmd_doctor`, `_print_doctor`, the `doctor` subparser, and the docstring line)
- Test: `tests/test_doctor.py` (append the CLI section)

**Interfaces:**
- Consumes: `Sluice.doctor` (Task 2); `format_roles`, `DEAD`, `DEGRADED`, `OK` from `core/doctor.py`.
- Produces: `cmd_doctor(args, config) -> int` (returns `report.exit_code(strict=args.strict)`); a `doctor` subparser with `--offline` and `--strict`; `_print_doctor(report, *, offline) -> None`.

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/test_doctor.py`:

```python
# ── cmd_doctor / argparse (offline; live exit codes are covered via Sluice) ───
from sluice.cli import main                    # noqa: E402


def test_cli_doctor_offline_degraded_fallback_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    rc = main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "claude-max" in out and "deepseek" in out
    assert "degraded" in out


def test_cli_doctor_strict_fails_on_degraded(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    assert main(["doctor", "--offline", "--strict"]) == 1


def test_cli_doctor_offline_dead_when_claude_missing_exits_nonzero(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert main(["doctor", "--offline"]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q -k cli_doctor`
Expected: FAIL — argparse errors with `invalid choice: 'doctor'` (the subcommand does not exist yet).

- [ ] **Step 3: Add `cmd_doctor` + `_print_doctor` to `sluice/cli.py`**

Add after `cmd_track_confirm` (before the `# ── argument parsing ──` section):

```python
# ── doctor ────────────────────────────────────────────────────────────────────
def cmd_doctor(args, config) -> int:
    from sluice.core.app import Sluice

    report = Sluice(config).doctor(offline=args.offline)
    _print_doctor(report, offline=args.offline)
    return report.exit_code(strict=args.strict)


def _print_doctor(report, *, offline) -> None:
    """One line per distinct backend, annotated with the sub-app roles it serves.
    Written to stdout, like `health`/`list-sources` -- doctor's output IS the
    answer the operator asked for, not a run side-report."""
    from sluice.core.doctor import DEAD, DEGRADED, OK, format_roles

    print(f"sluice doctor  ({'offline' if offline else 'live round-trip'})\n")
    for c in report.checks:
        t = c.target
        elapsed = f"  ({c.elapsed:.1f}s)" if c.elapsed is not None else ""
        print(f"{t.provider:11} {t.model:20} {c.state:9} "
              f"{format_roles(t.uses)}  {c.detail}{elapsed}")
    n_ok = sum(1 for c in report.checks if c.state == OK)
    n_deg = sum(1 for c in report.checks if c.state == DEGRADED)
    n_dead = sum(1 for c in report.checks if c.state == DEAD)
    print(f"\n{n_ok} ok, {n_deg} degraded, {n_dead} dead")
```

- [ ] **Step 4: Register the `doctor` subparser**

In `_build_parser`, add immediately before `return p` (after the `health` parser):

```python
    doctor = top.add_parser("doctor", help="preflight the configured backends")
    doctor.add_argument("--offline", action="store_true",
                        help="config-only checks; no round-trip")
    doctor.add_argument("--strict", action="store_true",
                        help="exit non-zero on degraded (e.g. a keyless fallback) too")
    doctor.set_defaults(func=cmd_doctor)
```

- [ ] **Step 5: Add `sluice doctor` to the module docstring**

In the `sluice/cli.py` top docstring command list, add a line under `sluice health`:

```
  sluice health                             per-source baseline + retire state
  sluice doctor [--offline] [--strict]      preflight configured backends (live round-trip)
```

- [ ] **Step 6: Run the CLI tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check sluice tests
git add sluice/cli.py tests/test_doctor.py
git commit -m "$(cat <<'EOF'
feat(cli): sluice doctor command — preflight the configured backends (#4)

cmd_doctor + --offline/--strict + a grouped stdout report; exit code is
report.exit_code(strict). Offline CLI tests pin the exit-code plumbing; live
paths are covered at the Sluice.doctor layer with an injected probe.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

### Task 4: docs + final verification

**Files:**
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:** none (documentation + a manual smoke check).

**Note:** `.rulesync/` is deliberately NOT touched (see File structure). The new command is
self-documenting via `--help`, the `cli.py` module docstring (Task 3), and `docs/ARCHITECTURE.md`
below. Keeping this PR out of the canonical human-gated tree is intentional.

- [ ] **Step 1: Document the command in `docs/ARCHITECTURE.md`**

Locate the backend section (`grep -n -i "backend" docs/ARCHITECTURE.md`) and add a short paragraph noting that `sluice doctor` is a read-only preflight over the backend seam: it enumerates every configured backend (primary + fallback, per sub-app), reports `ok`/`degraded`/`dead`, and exits non-zero when a run-blocking backend is dead — a keyless fallback degrades (sanctioned primary-only), a keyed-but-broken one is dead. Live round-trip by default; `--offline` for config-only; `--strict` to fail on degraded. Match the file's existing prose density and heading style.

- [ ] **Step 2: Manual smoke check (proves the wired command runs offline)**

Run: `.venv/bin/python -m sluice.cli doctor --offline; echo "exit=$?"`
Expected: the grouped report prints; exit is `0` if `claude` is on this machine's PATH, else `1` (claude-max primary dead — a correct verdict). Also run `.venv/bin/python -m sluice.cli doctor --help` and confirm `--offline`/`--strict` appear.

- [ ] **Step 3: Full verification bar**

Run: `.venv/bin/ruff check sluice tests && .venv/bin/python -m pytest -q`
Expected: ruff clean, all tests green.

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
docs: document sluice doctor (backend preflight) (#4)

Add the doctor command to the ARCHITECTURE.md backend section. .rulesync/ is
left untouched (canonical/human-gated); the command is self-documenting via
--help and the cli.py docstring.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage:**
- "For each configured backend (primary and fallback), in each sub-app" → `enumerate_targets` (Task 1), tested for dedup + per-sub-app split.
- "is a model resolved, and is it a live model id" → model carried per target; live-id detection is the round-trip failing (`classify` `probe_error` → dead), documented as the honest mechanism; offline cannot detect it (stated).
- "credentials actually present in this process" → `_provider_creds` + `key_present` (Task 2).
- "minimal round-trip" → `PROBE_PROMPT` + injectable `probe` (Task 2).
- "claude-max: CLI exists / host reachable" → `shutil.which` offline (Task 2/classify); live = the round-trip (subprocess failure → dead).
- "ok/degraded/dead + exit non-zero if a configured backend is dead" → `classify` + `DoctorReport.exit_code`; role-aware per the accepted design (keyless fallback degraded, keyed-broken dead).
- "live opt-out via --offline" → default live; `--offline` flag (Task 3).
- `--strict` (design) → exit_code(strict) + flag (Task 3).

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; every test step shows the assertion.

**3. Type consistency:** `enumerate_targets(triage_cfg, cv_cfg, track_cfg)` and the field names read (`claude_max_model`/`compose_model`/`cheap_model`, `claude_max_host`/`compose_host`, `claude_max_path`/`compose_claude_path`) match `sluice/*/config.py`. `classify(...)` kwargs match its call in `Sluice.doctor`. `BackendCheck`/`DoctorReport`/`format_roles`/state constants are used identically across Tasks 1–3. `make_backend(name, model, *, api_key, base_url, claude_host, claude_path)` matches `core/backends.py`. `_PROVIDER_ENV`/`_provider_creds` match `core/app.py`.
