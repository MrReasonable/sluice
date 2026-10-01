# One Backend Per Stage (#333) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the cross-provider fallback. Each LLM stage (triage, cv, track, plus triage's tier-3 resolve) uses exactly one configured backend, retries transient failures on that same backend, then fails loudly with a non-zero exit.

**Architecture:** `FallbackBackend` is deleted and replaced by `RetryingBackend` (`core/backends.py`), which wraps ONE provider and retries `BackendError(transient=True)` with exponential backoff. `Sluice.backend()` drops the role layer. It takes the stage's `provider`/`model` plus an optional one-run `override` provider name, and always returns a `RetryingBackend`. Each stage turns a post-retry `BackendError` into a `backend_error`/`backend-unavailable` outcome that exits non-zero. Track and cv also lose their two fail-open paths.

**Tech Stack:** Python 3.12+ stdlib, pytest, PyYAML (guarded).

**Spec:** GitHub issue #333 (`gh issue view 333`) plus the owner's decisions recorded below. Where they disagree, the decisions win: the issue proposed warn-for-one-release, and the owner chose to raise.

## Owner decisions (2026-10-01): these override the issue text

1. **Retries:** 2 same-backend retries (3 attempts), ONE root key `backend_retries: int = 2`, exponential backoff 2s then 4s.
2. **Retired keys RAISE at load** and name the replacement. There is no deprecation window.
3. **Tier-3 resolve gets a dedicated backend:** `triage.resolve_backend: ""` / `triage.resolve_model: ""`, where empty means triage's own backend/model.
4. **Rename the surviving keys** to `backend` + `model` in each of `triage:`, `cv:` and `track:`.
5. **`--backend` (CLI) / `backend` (MCP `cv_run`) take PROVIDER names only.** `auto`/`primary`/`fallback` raise with a migration message. An override naming a provider other than the configured one uses that provider's `DEFAULT_MODELS` entry. Omitting the override uses the stage's configured backend.

## Global Constraints

- Retired keys and their replacements. Each raises `ValueError` at load, naming the replacement and never echoing the value:
  - `triage:` `primary_backend`→`backend`, `claude_max_model`→`model`, `fallback_backend`→(none, removed), `cheap_model`→`resolve_model` (tier 3 only) / removed
  - `track:` `primary_backend`→`backend`, `claude_max_model`→`model`, `fallback_backend`, `cheap_model`→removed
  - `cv:` `primary_backend`→`backend`, `compose_model`→`model`, `fallback_backend`, `cheap_model`→removed, `audit_model`→removed (it is a DEAD key today: declared on `CvConfig`, read by nothing; the audit always ran on cv's backend)
- Defaults are unchanged in substance: `backend: "claude-max"`, `model: "claude-sonnet-4-5"` in all three blocks.
- Root `backend_retries`: validator rejects `bool` BEFORE `int` (PyYAML `yes`→`True`), rejects negatives, and lives beside `backend_timeout` in `core/config.py::load_config`, which must name it explicitly (root fields are dead unless `load_config` names them).
- Untouched: `claude_max_effort`, `claude_max_host`, `claude_max_path`, `compose_effort`, `compose_host`, `compose_claude_path`, `compose_timeout`.
- `sluice/` stays stdlib-only. No line numbers in comments (`tests/test_citation_drift.py`). Comments explain WHY, at the surrounding density.
- Never state a COUNT in prose (docs, comments, commit bodies) that a test cannot derive.
- Commit types: the config/CLI break is `feat(backends)!:`. Everything else is `fix(...)`/`refactor(...)`/`docs(...)`, with no `!` on internal-seam-only commits (CLAUDE.md: nothing imports `sluice` as a library).
- Run `ruff check sluice tests scripts` and `.venv/bin/python -m pytest` green at the end of every task. Before Task 1, run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`.

## Review Focus

1. **A config carrying BOTH an old and a new key** (e.g. `primary_backend` and `backend`) must still raise on the old one. Raising only when the new key is absent would let a dead key sit there looking live. Pin it in Task 2.
2. **`--backend claude-max` when the configured backend is ALREADY claude-max** must keep the configured model (not jump to `DEFAULT_MODELS`). It is the commonest override in existing crons. Pin it in Task 3.
3. **A non-transient error (a missing key, HTTP 401) must NOT be retried.** Retrying it triples a guaranteed failure and sleeps 6s for nothing. Pin it in Task 1 with an injected sleep that records its calls.
4. **Spend from failed attempts must be recorded.** Each failed attempt that billed must reach the usage log as `served: false`, both when a later attempt succeeds and when all fail. Pin it in Task 1.
5. **A cv batch must stop at the first post-retry backend failure** instead of retrying every remaining lead three times. Pin it in Task 6 with a backend that counts calls.

---

### Task 1: `RetryingBackend` and `BackendError.transient`

**Files:**
- Modify: `sluice/core/backends.py` (add `transient` to `BackendError`; add `RetryingBackend`; mark non-transient raise sites; leave `FallbackBackend` in place, since Task 3 deletes it)
- Modify: `sluice/backends/anthropic.py`, `sluice/backends/openai.py`, `sluice/backends/deepseek.py` (missing-key raise → `transient=False`)
- Test: `tests/test_backend_retry.py` (new)

**Interfaces:**
- Produces: `BackendError(*args, usage=None, unserved_usage=(), transient=True)`. `RetryingBackend(inner, *, retries: int, label: str, sleep=time.sleep, base_delay: float = 2.0)` with `.complete(prompt) -> Completion` and `.label: str`.

- [ ] **Step 1: Write failing tests** in `tests/test_backend_retry.py`:

```python
import pytest
from sluice.core.backends import BackendError, Completion, RetryingBackend, Usage


class Scripted:
    """Raises/returns the scripted outcomes in order and counts calls."""
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0
    def complete(self, prompt):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def _u(n):
    return Usage(provider="p", model="m", input_tokens=n, output_tokens=n)


def test_transient_failure_is_retried_on_the_same_backend_then_succeeds():
    slept = []
    inner = Scripted(BackendError("timeout"), Completion("ok"))
    out = RetryingBackend(inner, retries=2, label="p m", sleep=slept.append).complete("x")
    assert out.text == "ok" and inner.calls == 2 and slept == [2.0]


def test_backoff_is_exponential_and_bounded_by_retries():
    slept = []
    inner = Scripted(*[BackendError("down")] * 3)
    with pytest.raises(BackendError) as ei:
        RetryingBackend(inner, retries=2, label="p m", sleep=slept.append).complete("x")
    assert inner.calls == 3 and slept == [2.0, 4.0]
    assert "p m" in str(ei.value) and "3 attempts" in str(ei.value)


def test_non_transient_error_is_not_retried():
    slept = []
    inner = Scripted(BackendError("HTTP 401", transient=False))
    with pytest.raises(BackendError) as ei:
        RetryingBackend(inner, retries=2, label="p m", sleep=slept.append).complete("x")
    assert inner.calls == 1 and slept == [] and ei.value.transient is False


def test_zero_retries_means_one_attempt():
    inner = Scripted(BackendError("down"))
    with pytest.raises(BackendError):
        RetryingBackend(inner, retries=0, label="p m", sleep=lambda s: None).complete("x")
    assert inner.calls == 1


def test_spend_of_failed_attempts_rides_on_the_success_as_unserved():
    inner = Scripted(BackendError("trunc", usage=_u(5)), Completion("ok", usage=_u(7)))
    out = RetryingBackend(inner, retries=2, label="p m", sleep=lambda s: None).complete("x")
    assert out.usage == _u(7) and out.unserved_usage == (_u(5),)


def test_spend_of_every_failed_attempt_rides_on_the_final_error():
    inner = Scripted(BackendError("a", usage=_u(1)), BackendError("b"),
                     BackendError("c", usage=_u(3)))
    with pytest.raises(BackendError) as ei:
        RetryingBackend(inner, retries=2, label="p m", sleep=lambda s: None).complete("x")
    spent = ((ei.value.usage,) if ei.value.usage else ()) + ei.value.unserved_usage
    assert sorted(u.input_tokens for u in spent) == [1, 3]


def test_a_non_backend_error_propagates_untouched_and_unretried():
    inner = Scripted(TypeError("our bug"))
    with pytest.raises(TypeError):
        RetryingBackend(inner, retries=2, label="p m", sleep=lambda s: None).complete("x")
    assert inner.calls == 1
```

Check the real `Usage`/`Completion` constructor signatures in `core/backends.py` first and adapt the field names in the test if they differ. Do not change the dataclasses.

- [ ] **Step 2: Run them and confirm they fail** with `.venv/bin/python -m pytest tests/test_backend_retry.py -v` (expect ImportError on `RetryingBackend`).

- [ ] **Step 3: Implement.** In `BackendError.__init__`, add a keyword `transient: bool = True` and store it. The default is True because every transport failure (timeout, connection reset, 5xx, 429) is the retryable case, and a raise site that forgets to classify fails toward one bounded retry rather than toward no retry. Add the class after the provider classes:

```python
class RetryingBackend:
    """ONE provider, retried on itself (#333). It never switches provider.

    Replaces FallbackBackend. A failed call is retried only when the error says it may be
    TRANSIENT -- a timeout, a dropped connection, a 5xx, a 429 -- because a missing key or a
    401 fails identically every time, and retrying it would triple a certain failure and
    sleep for nothing. Spend from attempts that billed and then failed is carried forward
    rather than dropped: on a later success it rides as `unserved_usage`, and on final
    failure it rides on the raised error, which is the only carrier `MeteredBackend` has
    when there is no return value (the same reasoning FallbackBackend's legs used).
    """

    def __init__(self, inner, *, retries: int, label: str, sleep=time.sleep,
                 base_delay: float = 2.0):
        self.inner, self.retries, self.label = inner, retries, label
        self._sleep, self._base_delay = sleep, base_delay

    def complete(self, prompt: str) -> "Completion":
        spent: list = []
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                out = self.inner.complete(prompt)
            except BackendError as e:
                if e.usage is not None:
                    spent.append(e.usage)
                spent.extend(e.unserved_usage)
                last = attempt == attempts - 1
                if not e.transient or last:
                    tried = f" after {attempt + 1} attempts" if attempt else ""
                    raise BackendError(
                        f"{self.label}: {e}{tried}",
                        usage=spent[0] if spent else None,
                        unserved_usage=tuple(spent[1:]),
                        transient=e.transient) from e
                delay = self._base_delay * (2 ** attempt)
                _log.warning("%s failed (%s); retrying the same backend in %.0fs",
                             self.label, e, delay)
                self._sleep(delay)
                continue
            if spent:
                out = replace(out, unserved_usage=out.unserved_usage + tuple(spent))
            return out
```

Add `import time` if it is absent. With `retries=2`, "3 attempts" appears in the message: `attempt` is 2 on the last pass, so the f-string reads `after 3 attempts`.

- [ ] **Step 4: Classify the raise sites.** Mark every `raise BackendError(` in `core/backends.py` and `sluice/backends/*.py` that fails identically on retry as `transient=False`:
  - the missing-API-key raises in `sluice/backends/{anthropic,openai,deepseek}.py`
  - `make_backend`'s unknown-name raise and its `UnknownAdapter` re-raise
  - `_urlopen`'s HTTPError raise: `transient=e.code in (408, 429) or e.code >= 500`
  - the `stop_reason=max_tokens` / non-stop `finish_reason` truncation raises (the same prompt truncates again)
  - ClaudeMaxBackend's option-like host/path refusal

  Leave timeouts, short bodies, connection errors, subprocess failures and unparseable replies transient. Read each site before deciding, and list the classification in the commit body without a count. Add one test per non-transient class that drives the REAL code path (an `http` stub raising `urllib.error.HTTPError(url, 401, ...)` into `make_backend("openai", api_key="k", http=...)`, a missing key, a truncation reply) and asserts `ei.value.transient is False`. Add one test asserting a 503 is `transient is True`.

- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/test_backend_retry.py tests/test_backends.py tests/conformance -q` and expect PASS. Then run the full suite.

- [ ] **Step 6: Mutation witness.** Commit first. Then move `if not e.transient or last:` to `if last:`, confirm the non-transient tests go red, and restore. Do the same for deleting `spent.extend(e.unserved_usage)`.

- [ ] **Step 7: Commit** `feat(backends): retry a transient failure on the same backend`.

---

### Task 2: Config: root `backend_retries`, per-stage `backend`/`model`, tier-3 keys, retired keys raise

**Files:**
- Modify: `sluice/core/config.py` (add the `backend_retries` field, its validator in `load_config`, and the helper `refuse_retired_backend_keys(block, data)` beside `refuse_retired_dossier_dir`)
- Modify: `sluice/triage/config.py`, `sluice/cv/config.py`, `sluice/track/config.py` (rename fields; drop retired ones; add `resolve_backend`/`resolve_model` to triage; call the helper first thing after `sub_app_block`)
- Test: `tests/test_backend_config.py` (new)

This task also ADDS the new names alongside the reads in `core/app.py`/`core/doctor.py`, which still use the OLD attribute names. To keep the suite green, update every reader of a renamed field to the new attribute in this same task: `grep -rn "primary_backend\|claude_max_model\|compose_model\|cheap_model\|fallback_backend\|audit_model" sluice`. Do it mechanically: `tcfg.primary_backend`→`tcfg.backend`, `.claude_max_model`/`.compose_model`→`.model`. Where `fallback_backend`/`cheap_model` are still read (`Sluice.backend` callers, `doctor.enumerate_targets`), pass `tcfg.resolve_backend or tcfg.backend` / `tcfg.resolve_model or tcfg.model` for triage, and `cfg.backend`/`cfg.model` for cv/track. The fallback machinery then degenerates to same-provider-twice until Task 3 deletes it. That intermediate state is never released. Update `sluice/onboard/questions.py`'s two backend questions: rename `primary_backend` to `backend` writing `triage.backend`/`cv.backend`/`track.backend`, and DELETE the `fallback_backend` question. Update `sluice.yaml.example` and `docs/CONFIGURATION.md` keys in this task, because `tests/test_docs_claims.py`/the CONFIGURATION↔`*Config` guard sweeps both directions and goes red otherwise.

**Interfaces:**
- Produces: `Config.backend_retries: int = 2`. `TriageConfig.backend`, `.model`, `.resolve_backend: str = ""`, `.resolve_model: str = ""`. `CvConfig.backend`, `.model`. `TrackConfig.backend`, `.model`. `refuse_retired_backend_keys(block: str, data: dict) -> None`.

- [ ] **Step 1: Write failing tests** (`tests/test_backend_config.py`). Use the suite's existing pattern for writing a YAML file and pointing `SLUICE_CONFIG` at it; copy the helper from `tests/test_backend_timeout_config.py`.

```python
import pytest

RETIRED = {
    "triage": {"primary_backend": "backend", "claude_max_model": "model",
               "fallback_backend": None, "cheap_model": "resolve_model"},
    "track": {"primary_backend": "backend", "claude_max_model": "model",
              "fallback_backend": None, "cheap_model": None},
    "cv": {"primary_backend": "backend", "compose_model": "model",
           "fallback_backend": None, "cheap_model": None, "audit_model": None},
}
LOADERS = {"triage": "sluice.triage.config:load_triage_config",
           "track": "sluice.track.config:load_track_config",
           "cv": "sluice.cv.config:load_cv_config"}


@pytest.mark.parametrize("block,key", [(b, k) for b, ks in RETIRED.items() for k in ks])
def test_every_retired_backend_key_raises_naming_its_replacement(block, key, write_config):
    write_config({block: {key: "SECRET-VALUE"}})
    with pytest.raises(ValueError) as ei:
        _load(block)
    msg = str(ei.value)
    assert f"{block}.{key}" in msg and "SECRET-VALUE" not in msg
    if RETIRED[block][key]:
        assert f"{block}.{RETIRED[block][key]}" in msg


@pytest.mark.parametrize("block", RETIRED)
def test_a_retired_key_raises_even_beside_its_replacement(block, write_config):
    old = next(iter(RETIRED[block]))
    write_config({block: {old: "x", "backend": "deepseek"}})
    with pytest.raises(ValueError):
        _load(block)


def test_the_new_keys_load(write_config):
    write_config({"triage": {"backend": "deepseek", "model": "m1",
                             "resolve_backend": "openai", "resolve_model": "m2"}})
    t = _load("triage")
    assert (t.backend, t.model, t.resolve_backend, t.resolve_model) == \
           ("deepseek", "m1", "openai", "m2")


@pytest.mark.parametrize("bad", [True, "yes", -1, 1.5])
def test_backend_retries_rejects_non_int(bad, write_config):
    write_config({"backend_retries": bad})
    with pytest.raises(ValueError, match="backend_retries"):
        _load_root()


def test_backend_retries_defaults_to_two_and_accepts_zero(write_config):
    write_config({})
    assert _load_root().backend_retries == 2
    write_config({"backend_retries": 0})
    assert _load_root().backend_retries == 0
```

Add a sweep test that derives the retired set from the helper's own table (export it as `RETIRED_BACKEND_KEYS: dict[str, dict[str, str | None]]` in `core/config.py`) and asserts that every key in it is ABSENT from the matching `*Config` dataclass's fields. This catches a re-added field that the loop's `hasattr` filter would otherwise silently accept. Also assert it is non-empty for all three blocks (anti-vacuity).

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement** the helper:

```python
# Retired by #333 (one backend per stage). The value is the replacement key under the
# same block, or None when the setting is gone outright. Raised on rather than dropped:
# every sub-app loader filters unknown keys with `hasattr`, so a dropped `fallback_backend`
# would leave a user believing a fallback still protects them.
RETIRED_BACKEND_KEYS = {
    "triage": {"primary_backend": "backend", "claude_max_model": "model",
               "fallback_backend": None, "cheap_model": "resolve_model"},
    "track": {"primary_backend": "backend", "claude_max_model": "model",
              "fallback_backend": None, "cheap_model": None},
    "cv": {"primary_backend": "backend", "compose_model": "model",
           "fallback_backend": None, "cheap_model": None, "audit_model": None},
}


def refuse_retired_backend_keys(block: str, data: dict) -> None:
    """Raise on the first #333-retired backend key in a sub-app block.

    Never echoes the value, matching `refuse_retired_dossier_dir`. There is no
    deprecation window on the owner's ruling: a stage that silently stopped
    having a fallback is the exact surprise #333 removes."""
    for key, new in RETIRED_BACKEND_KEYS[block].items():
        if key in data:
            if new:
                hint = f"Rename it to `{block}.{new}`."
            elif key == "audit_model":
                hint = "The audit runs on cv's own backend; delete the key."
            else:
                hint = ("sluice no longer falls back to a second provider: each stage "
                        "uses one backend and retries it (`backend_retries`). Delete "
                        "the key.")
            raise ValueError(f"{block}.{key} was retired in #333. {hint}")
```

Note `cheap_model`→`resolve_model` applies to triage only, because only triage used `cheap_model` for something that survives (tier 3). Add the `backend_retries` validator in `load_config` following `lead_ttl_days`' exact shape: `bool` first, then `int`, then `>= 0`.

- [ ] **Step 4: Update the readers, onboarding, example and CONFIGURATION.md** as described in the Files note. In `docs/CONFIGURATION.md`, document `backend_retries` (worst case per call = attempts × timeout + 6s of backoff), `resolve_backend`/`resolve_model`, and the renamed keys. Remove every `fallback_backend`/`cheap_model`/`audit_model` row.

- [ ] **Step 5: Run the full suite.** Expect existing tests that write `primary_backend:` etc. into YAML to fail. Update each one to the new key, or delete it where it tested only the fallback key. Use `grep -rln "primary_backend\|fallback_backend\|cheap_model\|claude_max_model\|compose_model" tests` and work through the list; do not hand-pick.

- [ ] **Step 6: Commit** `feat(config)!: rename stage backend keys and retire the fallback keys`. The body explains the migration: rename, then delete fallback keys. Keep any breaking-change trailer token out of column one in prose.

---

### Task 3: `Sluice.backend()` without roles; `--backend` as a provider override; delete `FallbackBackend`

**Files:**
- Modify: `sluice/core/app.py` (`Sluice.backend`, delete `_make_fallback`/`_make_fallback_strict`/`_BACKEND_ROLES`/`_BACKEND_ALIASES`, fold `_make_primary` into one builder; update `triage()`, `compose_cv()`, `track()` call sites; rename the `backend_role` params to `backend_override`)
- Modify: `sluice/core/backends.py` (delete `FallbackBackend`; reword the `Completion`/`BackendError` docstrings that cite it, stating that `RetryingBackend` is the multi-attempt carrier)
- Modify: `sluice/core/usage.py` (`MeteredBackend.last_backend`→`label` proxy reading `getattr(self.inner, "label", None)`; fix docstrings naming FallbackBackend)
- Modify: `sluice/cli.py` (`_BACKEND_CHOICES`/`_BACKEND_HELP`; a `type=` validator for `--backend` on `triage run`, `cv run`, `track run`; default `None`; the `_complete_*` completer if one exists for backends; the `report.backend` docstring near the triage digest)
- Modify: `sluice/mcpserver.py` (`_BackendRole` → `Literal[<provider names>] | None = None`, with the comment updated)
- Modify: `sluice/triage/engine.py`, `sluice/cv/engine.py` (`getattr(backend, "last_backend", None)` → `getattr(backend, "label", None)`)
- Test: `tests/test_backend_selection.py` (rewrite), plus every test the grep below lists

**Interfaces:**
- Consumes: `RetryingBackend`, `Config.backend_retries`, the Task 2 fields.
- Produces: `Sluice.backend(*, provider: str, model: str, effort: str, host: str, claude_path: str, timeout=None, override: str | None = None) -> RetryingBackend` (or the injected override seam). `report.backend`/`CvResult.backend` now hold `"<provider> <model>"`. Facade params: `triage(..., backend_override=None)`, `compose_cv(..., backend_override=None)`, `track(..., backend_override=None)`.

- [ ] **Step 1: Write failing tests** in `tests/test_backend_selection.py`, replacing the role tests:

```python
import pytest
from sluice.core.app import Sluice
from sluice.core.backends import BackendError, RetryingBackend

KW = dict(provider="deepseek", model="m-cfg", effort="medium", host="", claude_path="claude")


def test_configured_backend_is_wrapped_for_retry(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    b = Sluice().backend(**KW)
    assert isinstance(b, RetryingBackend) and b.retries == 2
    assert b.label == "deepseek m-cfg"


def test_override_to_the_configured_provider_keeps_the_configured_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert Sluice().backend(**KW, override="deepseek").label == "deepseek m-cfg"


def test_override_to_another_provider_uses_that_providers_default_model(monkeypatch):
    from sluice.core.backends import DEFAULT_MODELS
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    b = Sluice().backend(**KW, override="openai")
    assert b.label == f"openai {DEFAULT_MODELS['openai']}"


@pytest.mark.parametrize("role", ["auto", "primary", "fallback"])
def test_retired_role_names_raise_with_a_migration_hint(role):
    with pytest.raises(BackendError, match="omit --backend"):
        Sluice().backend(**KW, override=role)


def test_unknown_override_raises_listing_providers():
    with pytest.raises(BackendError, match="deepseek"):
        Sluice().backend(**KW, override="deepsek")


def test_missing_key_raises_and_never_degrades(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(BackendError):
        Sluice().backend(**KW)


def test_no_code_path_constructs_a_second_provider(monkeypatch):
    """Acceptance (#333): nothing builds a provider other than the one named."""
    built = []
    import sluice.core.backends as cb
    real = cb.make_backend
    monkeypatch.setattr(cb, "make_backend", lambda n, *a, **k: built.append(n) or real(n, *a, **k))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    Sluice().backend(**KW)
    assert built == ["deepseek"]
```

Add a sweep asserting `FallbackBackend` no longer exists anywhere in `sluice/` (an AST/`grep` over `sluice/**/*.py` for the name, with a must-be-present control: the same sweep finds `RetryingBackend`). Add CLI tests: `job-sluice cv run --backend auto ...` exits 2 with the migration message on stderr, `--backend deepseek` is accepted by the parser, and omitting it passes `backend_override=None`.

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement `Sluice.backend`:**

```python
    # Retired by #333 along with the fallback: `--backend` now names a PROVIDER for one run.
    _RETIRED_ROLES = ("auto", "primary", "fallback")

    def backend(self, *, provider, model, effort, host, claude_path, timeout=None,
                override=None):
        """The ONE backend a stage uses, retried on itself and never swapped (#333).

        `override` is the one-run `--backend` provider. Naming the provider the stage
        is already configured with keeps its configured model -- `--backend claude-max`
        on a claude-max install must not silently change model. Naming a different
        provider uses that provider's default model, since the stage's model id belongs
        to another provider's namespace."""
        from sluice.core.backends import (BackendError, DEFAULT_MODELS, RetryingBackend,
                                          make_backend)
        if override in self._RETIRED_ROLES:
            raise BackendError(
                f"--backend {override} was retired in #333: sluice no longer has roles or "
                f"a fallback. omit --backend to use the stage's configured backend, or name "
                f"a provider ({', '.join(DEFAULT_MODELS)})")
        if override and override not in DEFAULT_MODELS:
            raise BackendError(
                f"unknown backend '{override}' (expected {', '.join(DEFAULT_MODELS)})")
        if _BACKEND_SEAM in self._overrides:
            return self._overrides[_BACKEND_SEAM]
        if override and override != provider:
            provider, model = override, ""
        if timeout is None:
            timeout = self.config.backend_timeout
        api_key, base_url = _provider_creds(provider)
        inner = make_backend(provider, model, api_key=api_key, base_url=base_url,
                             timeout=timeout, claude_host=host, claude_path=claude_path,
                             effort=effort)
        return RetryingBackend(inner, retries=self.config.backend_retries,
                               label=f"{provider} {model or DEFAULT_MODELS[provider]}",
                               sleep=self._sleep or time.sleep)
```

Keep the existing comments' reasoning wherever it still holds: the injected-seam check sits AFTER validation, timeout is resolved here, and `backend()` is uncached. `_make_primary` may already pass `effort`; mirror what it does. Then update the three call sites:
- `triage()`: the judge gets `self.backend(provider=tcfg.backend, model=tcfg.model, effort=..., host=..., claude_path=..., override=backend_override)`. Tier 3 gets `provider=tcfg.resolve_backend or tcfg.backend`, `model=tcfg.resolve_model or (tcfg.model if not tcfg.resolve_backend else "")` with NO override: the one-run override is for the judge, and tier 3 has its own key. A construction failure for tier 3 now RAISES as `ValueError` (the user opted in with `company_resolve_llm: true`; a silently disabled tier 3 is the fail-quiet shape #333 removes). Update its docstring.
- `compose_cv()`: `provider=cvcfg.backend, model=cvcfg.model, ..., timeout=cvcfg.compose_timeout, override=backend_override`.
- `track()`: likewise with `tcfg.backend`/`tcfg.model`.

CLI validator:

```python
def _backend_override(value):
    """`--backend` names a provider for this run (#333). The retired role names get the
    migration message rather than argparse's bare "invalid choice"."""
    from sluice.core.backends import DEFAULT_MODELS
    if value in ("auto", "primary", "fallback"):
        raise argparse.ArgumentTypeError(
            f"'{value}' was retired in #333 (no roles, no fallback): omit --backend to use "
            f"the configured backend, or name a provider: {', '.join(DEFAULT_MODELS)}")
    if value not in DEFAULT_MODELS:
        raise argparse.ArgumentTypeError(
            f"unknown backend '{value}' (expected {', '.join(DEFAULT_MODELS)})")
    return value
```

`core.backends` imports only stdlib at module scope, so importing it inside the validator is cheap. Confirm this with `python -X importtime -c "import sluice.cli"` before and after.

- [ ] **Step 4:** Run `grep -rn "backend_role\|last_backend\|FallbackBackend\|_make_fallback\|_BACKEND_ROLES\|_BACKEND_ALIASES\|_BACKEND_CHOICES" sluice tests` and fix every hit. Rewrite tests that asserted fallback behaviour to assert the single-backend behaviour, or delete them where nothing survives. `tests/test_usage_wiring.py` rosters stage literals and `.complete(` sites: re-derive its targets by running it and reading the failure, since `triage-resolve` survives as a stage and only the role changed.

- [ ] **Step 5: Run the full suite + ruff.**

- [ ] **Step 6: Mutation witness:** commit, then swap `provider, model = override, ""` to `provider = override` and confirm `test_override_to_another_provider_uses_that_providers_default_model` goes red. Restore.

- [ ] **Step 7: Commit** `feat(backends)!: one backend per stage; --backend names a provider`.

---

### Task 4: Triage fails loudly on a post-retry backend failure

**Files:**
- Modify: `sluice/triage/judge.py` (a `BackendError` aborts the remaining batches; parse-failure retry unchanged)
- Modify: `sluice/triage/engine.py` (`TriageReport.backend_error: str = ""`; set it from the judge abort and from the tier-3 breaker trip)
- Modify: `sluice/cli.py` (`cmd_triage_run` and `_format_triage_digest`: print/notify the backend failure, exit 1)
- Test: `tests/test_triage_backend_failure.py` (new)

**Interfaces:**
- Produces: `class JudgeAborted(Exception)` in `triage/judge.py` with `.verdicts: list` (verdicts from batches that completed) and `.cause: BackendError`. `TriageReport.backend_error: str`.

- [ ] **Step 1: Failing tests:**

```python
from sluice.core.backends import BackendError, Completion
from sluice.triage.judge import JudgeAborted, judge


class Down:
    calls = 0
    def complete(self, p):
        Down.calls += 1
        raise BackendError("deepseek m: down after 3 attempts")


def test_backend_error_aborts_the_remaining_batches():
    Down.calls = 0
    ds = [{"lead_id": f"l{i}"} for i in range(10)]
    try:
        judge(ds, Down(), batch_size=5)
    except JudgeAborted as e:
        assert e.verdicts == [] and "down" in str(e.cause)
    else:
        raise AssertionError("expected JudgeAborted")
    assert Down.calls == 1  # not 2 per batch, not every batch


def test_a_parse_failure_still_gets_its_one_retry_and_skips_only_its_batch():
    replies = iter(["nonsense", "nonsense",
                    '[{"lead_id":"l5","verdict":"dismiss","relevance_score":1,'
                    '"fit_reasoning":"","concerns":[],"culture_flags":[],'
                    '"recommended_next_action":""}]'])
    class B:
        def complete(self, p):
            return Completion(next(replies))
    out = judge([{"lead_id": f"l{i}"} for i in range(6)], B(), batch_size=5)
    assert [v["lead_id"] for v in out] == ["l5"]
```

Add an engine-level test that drives `triage.engine.run` with the down backend (copy the fixture pattern from `tests/test_triage_engine.py`) and asserts `report.backend_error` is non-empty. Add a CLI test asserting `job-sluice triage run` exits 1 and that stderr and the notify body both contain `backend unavailable`. Add a test that a tripped tier-3 breaker sets `backend_error` too.

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement.** In `judge()`, split the `except`: `except BackendError as e: raise JudgeAborted(verdicts, e) from e`, which is raised after the backend's own retries and so is final for this run. Keep the existing `except Exception` arm for parse failures. In the engine, wrap the `judge(...)` call: on `JudgeAborted`, take `e.verdicts` and set `report.backend_error = str(e.cause)`. The existing unjudged-count failure line then reports the rest. On the tier-3 breaker trip, also set `report.backend_error` if it is empty. In `cmd_triage_run`, after the existing summary/digest, `return 1` when `report.backend_error`. In `_format_triage_digest`, add a first-class line, `Backend unavailable: <msg>. Unjudged leads keep their status and the next run retries them.`, ahead of the `returned NOTHING` arm, which stays for the parse-failure case.

- [ ] **Step 4: Run the suite and do a mutation witness** (delete `report.backend_error = ...`; the CLI exit test goes red).

- [ ] **Step 5: Commit** `fix(triage): exit non-zero when the judge's backend is unavailable`.

---

### Task 5: Track: a backend failure is a run failure, not a manual-review proposal

**Files:**
- Modify: `sluice/track/classify.py` (re-raise `BackendError`; keep `unknown` for every other exception, the #40 reasoning)
- Modify: `sluice/track/engine.py` (`RunReport.backend_error: str = ""`; on `BackendError` from classify: record it, do NOT `seen.add`, do NOT dead-letter, `break` the loop)
- Modify: `sluice/core/app.py` (`track()`: hold the lastrun watermark when `rep.backend_error`, beside `auth_error`)
- Modify: `sluice/cli.py` (`cmd_track_run`: print + notify + exit 1 on `rep.backend_error`)
- Test: `tests/test_track_backend_failure.py` (new)

**Interfaces:**
- Produces: `RunReport.backend_error: str`.

- [ ] **Step 1: Failing tests.** Reuse the fake Gmail client and vault fixtures from `tests/test_track_engine.py`; read that file first and copy its builder. Assert:
  - `classify()` with a backend raising `BackendError` raises it, and with a backend returning garbage still returns `type == "unknown"` (the #40 row must stay green)
  - `run(...)` with two messages and a down backend: `rep.backend_error` is set, the backend is called ONCE (the loop broke), neither message id is in `seen`, `deadletter.open_entries()` gains no row, and `rep.failures` is empty
  - `Sluice.track()` with `rep.backend_error` does not advance the lastrun file. Mirror the existing auth_error watermark test.
  - `job-sluice track run` exits 1 and notifies `backend unavailable`

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement.** In `classify`, add `except BackendError: raise` immediately BEFORE `except Exception:`, with a comment: a backend failure is not a message's property; leaving it unseen lets the next run classify it, and turning it into an `unknown` proposal is the fail-open path #333 closes. In `engine.run`, add `except BackendError as exc: rep.backend_error = str(exc); break` beside `except GoogleAuthError`, ahead of the generic `except Exception`. It must come BEFORE that arm, because Python takes the first matching `except`. In `app.track()`, extend the `_save_lastrun` gate to `not (rep.auth_error or rep.backend_error or rep.deadletter_error or rep.search_truncated)` and update the docstring that explains the gate.

- [ ] **Step 4: Run the suite; mutation witness** (move the `except BackendError` arm below `except Exception` and confirm the engine test goes red).

- [ ] **Step 5: Commit** `fix(track): retry a message whose classification hit a backend outage`.

---

### Task 6: cv fails loudly, and an audit that could not run holds the CV

**Files:**
- Modify: `sluice/cv/engine.py` (audit failure → a `unaudited\t<error>` blocker when `require_signoff`; `run_batch` stops on `BackendError`; new status `backend-unavailable`)
- Modify: `sluice/core/app.py` (`compose_cv` single-lead path: catch `BackendError` from `run_one` → `CvResult(ref, "backend-unavailable", error=str(e))`)
- Modify: `sluice/cli.py` (`cmd_cv_run`: exit 1 if any result is `backend-unavailable`, printing its message; `_print_signoff_claims`: render a `unaudited\t` entry as "the advisory audit could not run: <error>")
- Modify: `sluice/mcpserver.py` (`cv_run`: a `backend-unavailable` result raises the tool error the other failure statuses raise. Read how `cv_run` maps statuses first; mcp 2.1.1 discards exception messages, see `domain_mcp_server.md`, so return the error in the response shape the tool already uses for failures)
- Test: `tests/test_cv_backend_failure.py` (new)

**Interfaces:**
- Produces: CvResult status `"backend-unavailable"` and a `CvResult.error: str = ""` field (add it only if CvResult has no equivalent; check first). Claims entry prefix `unaudited\t`.

- [ ] **Step 1: Failing tests** (copy `tests/test_cv_engine.py`'s fixture builder for a lead + vault + scripted backend):
  - compose succeeds, audit raises `BackendError`, `require_signoff=True`, `served_dir` set: result `needs-signoff`, `tailored_cv` NOT written, `needs_signoff` claims contain an entry starting `unaudited\t`
  - same with `require_signoff=False`: `rendered` (the user switched sign-off off; the hold is keyed on that flag exactly like `unsupported`)
  - the voice check failing still treats as clean (unchanged; voice is the opt-in STYLE tier, not fabrication). Pin that it is unchanged.
  - `run_batch` over three shortlist leads with a backend whose compose raises `BackendError`: one `backend-unavailable` result, the backend called once (Review Focus 5), and the other two leads absent from results (not `error`)
  - single-lead `compose_cv(lead=...)` with a down backend returns `[CvResult(..., "backend-unavailable")]` and does not raise
  - `job-sluice cv run --lead x` exits 1 with the backend message on stderr
  - MCP `cv_run` with a down backend reports the failure (mirror an existing failure-status test in `tests/test_mcpserver.py`)

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement.** Audit arm:

```python
        # The audit is advisory to the MODEL, but whether it RAN is not advisory (#333).
        # An audit that could not run has checked nothing, so under require_signoff the CV
        # is held exactly as an `unsupported` flag would hold it, instead of being served
        # as if it had passed. The draft still renders: it cleared the HARD gate.
        audit_unavailable = ""
        try:
            _report, audit_flags = run_audit(...)
        except Exception as e:
            _log.warning("advisory audit failed for %s: %s", note.ref, e)
            audit_flags, audit_unavailable = [], str(e)
```

and `blockers = ((unsupported_claims(audit_flags) + ([f"unaudited\t{audit_unavailable}"] if audit_unavailable else [])) if cvcfg.require_signoff else []) + style_blockers`. Rewrite the existing "Fail-open: an audit backend error already yields no flags above" comment: it is now false. In `run_batch`, find the per-lead `except Exception` and add `except BackendError as e:` BEFORE it. That arm appends `CvResult(note.ref, "backend-unavailable", ...)` and `break`s. Add `backend-unavailable` to the `CvResult` status docstring. If `cli.py` has a failed-status set (`_FAILED` or similar), add it there too. Grep the status vocabulary, and if a test enumerates CvResult statuses, extend that test by its own mechanism.

A compose retry that raises `BackendError` while a retained hard-clean draft exists keeps today's behaviour: it ships the retained draft, and the audit then most likely fails too and holds it via the new arm. State that in the comment next to the retained-draft path.

- [ ] **Step 4: Run the suite; mutation witnesses** for the audit blocker and the batch `break`.

- [ ] **Step 5: Commit** `fix(cv): hold an unaudited CV and stop a batch when the backend is down`.

---

### Task 7: doctor probes each stage's one backend, with no role awareness

**Files:**
- Modify: `sluice/core/doctor.py` (`enumerate_targets`: one spec per stage, plus `("triage", "resolve", ...)` only when `triage_cfg.company_resolve_llm`; delete `_fallback_host_path`; `RoleUse` keeps its shape, with `role` holding `"backend"` or `"resolve"`; the capability-blocking loop drops the `u.role == "primary"` filter so EVERY use blocks its sub-app; rewrite the module docstring's role-aware paragraph and the verdict docstring's "Backend rows block only where the target is that sub-app's PRIMARY" paragraph)
- Modify: `sluice/core/app.py` (`Sluice.doctor` if it reads roles or builds fallback probes)
- Modify: `sluice/cli.py` (`format_roles`, or wherever doctor prints `primary: triage, cv`)
- Test: `tests/test_doctor.py`, `tests/test_doctor_verdict.py` (update), plus new rows

Doctor keeps `misconfigured` (SETUP: key not supplied) distinct from `unavailable` (DEAD: key present, round-trip failed). That answers the issue's open question 3 with the states doctor already has; no new state is added.

- [ ] **Step 1: Failing tests:** a keyless `deepseek` configured for triage only puts triage in SETUP (not DEGRADED); `company_resolve_llm: true` with `resolve_backend: openai` and no `OPENAI_API_KEY` blocks triage; `company_resolve_llm: false` probes no resolve target; three stages sharing one backend produce ONE target whose `uses` are the three stages in triage/cv/track order. Sweep: no `RoleUse.role` value outside `{"backend", "resolve"}` is produced by `enumerate_targets` across a few configs.
- [ ] **Step 2: Run them and confirm they fail. Step 3: implement. Step 4: run the suite; mutation witness** (re-add the `role == "backend"` filter and confirm the resolve-blocks-triage test goes red).
- [ ] **Step 5: Commit** `refactor(doctor): probe each stage's single backend`.

---

### Task 8: Documentation and rules sweep

**Files:**
- Modify: `docs/USAGE.md`, `docs/CONFIGURATION.md` (finish), `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`, `docs/ARCHITECTURE.md`, `README.md` (if it mentions roles/fallback), `docker-compose.yml` comments, `sluice.yaml.example` (finish), `.rulesync/rules/CLAUDE.md` (then regenerate: `npm ci --ignore-scripts && npm run rulesync`), `.rulesync/subagents/sluice-architect.md`
- Do NOT edit `docs/superpowers/specs|plans/` (historical) or `CHANGELOG.md` (release-please owns it; the migration note goes into the release PR, see below)

- [ ] **Step 1:** Run `grep -rn -i "fallback\|cheap_model\|primary_backend\|claude_max_model\|compose_model\|audit_model\|--backend\|auto|primary" README.md docs/*.md docker-compose.yml sluice.yaml.example .rulesync sluice/onboard`. Note that "fallback" also names unrelated things (health's `fallback` drift reason, `_first_degraded`, the XDG fallback rung, the Windows tz table). Read each hit and change only those about the LLM backend fallback. Grep the CLAIM too: "worst case per lead is 6x", "degrades to primary-only", "role", "leg".
- [ ] **Step 2:** Rewrite the CLAUDE.md paragraphs "Backends are selected by role, not provider" (replace with: one backend per stage, same-backend retry via `backend_retries`, `--backend` = one-run provider override, retired keys raise) and the `Completion` paragraph's `FallbackBackend.last_backend` counter-example (keep the argument and drop the deleted class; `RetryingBackend` is now where multi-attempt spend rides). Fix the adapter-seams bullet's "role layer (auto/primary/fallback, in `Sluice.backend()`)" sentence. Assert nothing that has not been run.
- [ ] **Step 3:** Run the full suite (`tests/test_docs_claims.py` sweeps USAGE/README against the parser) and ruff. Regenerate rulesync and check `git status` shows no generated file staged.
- [ ] **Step 4: Commit** `docs: describe one backend per stage (#333)`.

**Release-PR changelog note** (paste into the release-please PR's entry when it opens; also check the tag and release body afterwards):

> **Breaking config change (#333): no more fallback backend.** Each of `triage:`, `cv:` and `track:` now names ONE backend, and sluice retries it (root `backend_retries`, default 2) instead of switching provider. A config still carrying a retired key will not load; the error names the fix. Rename `primary_backend` → `backend`, `claude_max_model` (triage/track) and `compose_model` (cv) → `model`, and triage's `cheap_model` → `resolve_model` if you use `company_resolve_llm`. Delete `fallback_backend`, track/cv `cheap_model`, and `cv.audit_model`. `--backend auto|primary|fallback` is gone: omit the flag, or name a provider (`--backend deepseek`) for one run. A stage whose backend stays down now exits non-zero; track leaves the affected messages unseen for the next run, and cv holds a CV whose audit could not run for sign-off.

---

## Self-review notes

- Issue acceptance → tasks: no second provider (T3 sweep and the `make_backend` spy); non-zero exit in every stage + MCP (T4, T5, T6); track retry and cv hold (T5, T6); retired keys (T2, raising per the owner's decision); no fallback in docs/example/compose/rulesync/onboarding (T2, T8); doctor not role-classified (T7).
- Open question 3 (doctor state) is answered in T7 without a new state.
- Each task leaves the suite green: T2 keeps the fallback machinery compiling by pointing it at the same provider until T3 deletes it.
