# Backend Conformance Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assert the portable backend contract — empty/whitespace → raise, transport failure → raise, valid → return-as-text — against every registered provider in one parametrized suite, so a new provider passes it or does not ship, then prune the per-class tests it subsumes.

**Architecture:** One new file `tests/conformance/test_backend_contract.py`, mirroring `test_store_contract.py`: enumerate `Sluice.available("backend")`, a fail-loudly guard, three per-provider payload tables (the injection asymmetry — `runner=` for claude-max, `http=` for the HTTP providers — is why it is more than a bare parametrize), a completeness test tying the tables to the registry (the anti-drift teeth), and three parametrized properties. Then prune the six now-subsumed per-class empty/transport tests from `tests/test_backends.py`. Plus one terse `docs/ARCHITECTURE.md` note.

**Tech Stack:** Python stdlib + pytest. No new dependency. Fully offline/hermetic (fake runner/poster injected).

## Global Constraints

- **TEST-ONLY + one doc note.** ZERO `sluice/` production change. A provider found to violate a property is a SEPARATE fix PR — this suite's job is to *surface* it. `docs/` is not `sluice/`.
- **Neutrality:** no personal data. Use `"test-model"` / `"test-key"` sentinels and `"HELLO"` payloads; no employer/host/path literals.
- **Enumerate from the file, do not hand-list** (THE LESSON). Task 2 re-derives the prune set from `tests/test_backends.py` before deleting.
- **Mutation witnesses run by NODE ID**, against the NEW conformance test in isolation (a per-class test also catching a mutant witnesses nothing about the new test). **Mutate by MOVING or DELETING, never ADDING.**
- **Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` ONCE before any witness** so a mutated `sluice/core/backends.py` cannot run stale bytecode and lie green.
- **Restore a witnessed `backends.py` mutation via Edit (exact restore), NOT `git checkout`** — the conformance file and the ARCHITECTURE note are uncommitted working-tree changes a `git checkout` in that file would not touch, but the discipline (restore from a saved copy, never git-checkout across uncommitted work) is the memory rule; a final `git diff --stat` must show `backends.py` untouched before commit.
- **Verified facts (checked against source, do not re-derive):** empty-path message contains `"no text"` for all four providers (`backends.py:188,231,272`); transport-path message contains `"failed"` for all four (`backends.py:161,236,277`); openai/deepseek/anthropic factories REQUIRE a non-empty `api_key` and default `base_url` when empty; claude-max ignores `api_key`. `Sluice.available("backend")` → `['anthropic', 'claude-max', 'deepseek', 'openai']`.

---

### Task 1: The backend conformance suite + ARCHITECTURE.md note

**Files:**
- Create: `tests/conformance/test_backend_contract.py`
- Modify: `docs/ARCHITECTURE.md` (backend-seam bullet, ~:211-216)
- (Witness-only, restored: `sluice/core/backends.py`)

**Interfaces:**
- Consumes: `Sluice.available("backend")` (`sluice/core/app.py`); `make_backend(name, model, *, api_key, http, runner, ...)`, `BackendError` (`sluice/core/backends.py`).
- Produces: the conformance suite (13 cases). Task 2 relies on its docstrings carrying the migrated FallbackBackend rationale and on it being the sole cover for the empty/transport properties after the prune.

- [ ] **Step 1: Write the conformance suite file**

Create `tests/conformance/test_backend_contract.py`:

```python
"""The backend contract, asserted against EVERY registered provider.

This is to the backend seam what test_store_contract.py is to the store seam: the
PORTABLE contract -- what is true of every provider, not of one -- in a single
parametrized suite, so a new provider passes it or does not ship.

The drift this prevents has already happened twice in one class. ClaudeMaxBackend shipped
WITHOUT the empty-response guard both siblings had, and its transport wrapper
(except -> BackendError) was pinned by no test. Both are properties FallbackBackend depends
on: it catches BackendError ONLY, so an empty response handed back as "" -- or a raw OSError
escaping the primary -- would feed a useless string downstream / CRASH the run instead of
degrading to the fallback. A per-class test named ONE implementation; this names the
CONTRACT, so the next provider inherits it.

The asymmetry that makes this more than a bare parametrize: backends inject differently.
ClaudeMaxBackend takes runner= (a subprocess); the HTTP backends take http= (a poster). So
each property carries a small per-provider payload table keyed by provider name, and a
completeness test ties every table to the registry -- a new provider that registers but is
not added to the tables fails LOUDLY (the anti-drift teeth; mirrors #63).

Test-only: sluice/ is untouched. A provider found to VIOLATE a property is a separate fix
PR -- this suite's job is to surface it.
"""
import pytest

from sluice.core.app import Sluice
from sluice.core.backends import BackendError, make_backend

_BACKENDS = Sluice.available("backend")   # ['anthropic', 'claude-max', 'deepseek', 'openai']

# A parametrize over [] skips every test and exits 0 -- the suite that is "the reason the
# seam is safe" would report success having tested nothing, and plugins.autoload swallows a
# broken plugin's ImportError, so an empty registry is a realistic accident. Fail loudly.
# (Mirrors test_store_contract.py's module-level fail-loudly assert.)
assert _BACKENDS, "no backend is registered: the contract suite would pass vacuously"


class _Proc:
    """A minimal fake completed-process for the claude-max runner: exactly the three
    attributes ClaudeMaxBackend.complete reads."""
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _runner_returning(proc):
    return lambda *a, **k: proc


def _runner_raising():
    def runner(*a, **k):
        # A hung host / refused ssh surfaces here as OSError -- the transport-failure shape.
        raise OSError("ssh: connect to host port 22: Connection refused")
    return runner


def _http_returning(payload):
    def http(url, data, headers, timeout):
        return payload
    return http


def _http_raising():
    def http(*a, **k):
        raise OSError("network down")
    return http


# Whitespace (not "") for the empty case: it also pins the .strip()-before-check edge that
# test_claudemax_empty_stdout_on_exit_zero_raises carried, now extended to all four providers.
# finish_reason=stop / stop_reason=end_turn so the EMPTY guard fires, NOT the truncation guard
# (out of scope -- #28; see the module docstring / spec Non-goals).
_OPENAI_EMPTY = '{"choices":[{"message":{"content":"   \\n"},"finish_reason":"stop"}]}'
_OPENAI_VALID = '{"choices":[{"message":{"content":"HELLO"},"finish_reason":"stop"}]}'
_ANTHROPIC_EMPTY = '{"stop_reason":"end_turn","content":[{"type":"text","text":"   \\n"}]}'
_ANTHROPIC_VALID = '{"stop_reason":"end_turn","content":[{"type":"text","text":"HELLO"}]}'

# Each table maps a provider name -> a THUNK returning the injected-kwargs dict for
# make_backend: {"runner": ...} for claude-max, {"http": ...} for the HTTP providers. A thunk
# (not a value) so every test gets a fresh fake. openai and deepseek are the same class, so
# they share a payload.
_EMPTY = {
    "claude-max": lambda: {"runner": _runner_returning(_Proc(0, "   \n", ""))},
    "openai": lambda: {"http": _http_returning(_OPENAI_EMPTY)},
    "deepseek": lambda: {"http": _http_returning(_OPENAI_EMPTY)},
    "anthropic": lambda: {"http": _http_returning(_ANTHROPIC_EMPTY)},
}
_VALID = {
    "claude-max": lambda: {"runner": _runner_returning(_Proc(0, "HELLO\n", ""))},
    "openai": lambda: {"http": _http_returning(_OPENAI_VALID)},
    "deepseek": lambda: {"http": _http_returning(_OPENAI_VALID)},
    "anthropic": lambda: {"http": _http_returning(_ANTHROPIC_VALID)},
}
_TRANSPORT = {
    "claude-max": lambda: {"runner": _runner_raising()},
    "openai": lambda: {"http": _http_raising()},
    "deepseek": lambda: {"http": _http_raising()},
    "anthropic": lambda: {"http": _http_raising()},
}


def _backend(name, table):
    # api_key is required by the per-token factories and ignored by claude-max, so pass one
    # uniformly. base_url is left to default -- the injected fake http ignores the URL.
    return make_backend(name, "test-model", api_key="test-key", **table[name]())


def test_payload_tables_cover_the_registry():
    """The anti-drift teeth (#39's whole point; mirrors #63's registry-completeness guard). A
    NEW provider that registers but is not added to these tables would silently ESCAPE the
    contract suite -- the exact drift #39 exists to stop. Every table covers every registered
    provider, exactly. A standalone test (not a module-level assert) so a dropped entry reddens
    by node id rather than as a blunt collection error."""
    for table, tname in ((_EMPTY, "_EMPTY"), (_VALID, "_VALID"), (_TRANSPORT, "_TRANSPORT")):
        assert set(table) == set(_BACKENDS), \
            f"{tname} is out of sync with the backend registry: {set(_BACKENDS) ^ set(table)}"


@pytest.mark.parametrize("name", _BACKENDS)
def test_empty_or_whitespace_response_returns_nothing_so_raises(name):
    """complete() never hands back a falsy string. An empty OR whitespace-only response is a
    FAILED call wearing a successful one's clothes; it must raise BackendError so
    FallbackBackend degrades to the fallback (it catches BackendError only). claude-max shipped
    WITHOUT this guard and stayed green its whole life because only bespoke per-class tests
    covered it (#39). match= pins the message, not just the type: all four providers say
    "no text", so it restores the pruned per-class tests' specificity at zero per-provider
    cost (inv-001/tst-001)."""
    with pytest.raises(BackendError, match="no text"):
        _backend(name, _EMPTY).complete("prompt")


@pytest.mark.parametrize("name", _BACKENDS)
def test_transport_failure_surfaces_as_BackendError_not_a_raw_exception(name):
    """A transport failure (OSError/TimeoutExpired from the runner/poster) must surface as
    BackendError, never the raw exception. This is the ONE property FallbackBackend depends on:
    it catches BackendError ONLY, so a timeout or an ssh failure escaping raw would CRASH the
    run instead of degrading to the fallback -- the exact opposite of what the module docstring
    promises, and the second drift PR #37 fixed one line above the first. (This docstring
    carries the rationale migrated from the pruned
    test_claudemax_transport_failure_raises_backend_error.) All four providers say "...failed"
    on this path, so match= restores that test's message-pin."""
    with pytest.raises(BackendError, match="failed"):
        _backend(name, _TRANSPORT).complete("prompt")


@pytest.mark.parametrize("name", _BACKENDS)
def test_a_valid_response_is_returned_as_its_text(name):
    """The positive half: a well-formed non-empty response comes back as its text, unchanged.
    Without this, a backend that raised on EVERYTHING would pass both negative properties while
    being wholly broken -- the two 'raises' tests cannot tell a strict backend from a dead
    one."""
    assert _backend(name, _VALID).complete("prompt") == "HELLO"
```

- [ ] **Step 2: Run the suite — expect PASS and a non-vacuous collection**

Run: `python -m pytest tests/conformance/test_backend_contract.py -v`
Expected: **13 passed** — `test_payload_tables_cover_the_registry` (1) + `test_empty…`, `test_transport…`, `test_a_valid…` each × `[anthropic] [claude-max] [deepseek] [openai]` (12). Production already conforms, so it is green on write; the teeth come from the witnesses below. If fewer than 13 cases collect, the parametrize went vacuous — stop and fix.

- [ ] **Step 3: Content-address the bytecode caches (once, before any witness)**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
Expected: exit 0, no output. This is what stops a size-preserving `backends.py` mutation from running stale `.pyc` and lying green.

- [ ] **Step 4: Witness the empty guard — claude-max**

Edit `sluice/core/backends.py`, in `ClaudeMaxBackend.complete`, DELETE the empty guard (leave `return text`):

old_string:
```python
        if not text:
            detail = self._scrub(proc.stderr).strip()[:200]
            raise BackendError(
                f"claude-max returned no text (exit 0, {len(proc.stdout)} chars of whitespace"
                + (f"; stderr: {detail}" if detail else "") + ")"
            )
        return text
```
new_string:
```python
        return text
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_empty_or_whitespace_response_returns_nothing_so_raises[claude-max]" -q`
Expected: **FAIL** (returns `""` instead of raising). Then REVERT the Edit exactly (restore the deleted block) and re-run: Expected **PASS**.

- [ ] **Step 5: Witness the empty guard — openai/deepseek**

Edit `sluice/core/backends.py`, in `OpenAiCompatibleBackend.complete`, DELETE the empty guard:

old_string:
```python
            text = choice["message"]["content"].strip()
            if not text:
                raise BackendError(
                    f"openai-compatible returned no text (finish_reason={reason})")
            return text
```
new_string:
```python
            text = choice["message"]["content"].strip()
            return text
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_empty_or_whitespace_response_returns_nothing_so_raises[openai]" "tests/conformance/test_backend_contract.py::test_empty_or_whitespace_response_returns_nothing_so_raises[deepseek]" -q`
Expected: **2 FAILED**. Then REVERT the Edit exactly and re-run: Expected **2 passed**.

- [ ] **Step 6: Witness the empty guard — anthropic**

Edit `sluice/core/backends.py`, in `AnthropicBackend.complete`, DELETE the empty guard:

old_string:
```python
            if not text:
                raise BackendError(
                    f"anthropic returned no text (stop_reason={data.get('stop_reason')})")
            return text
```
new_string:
```python
            return text
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_empty_or_whitespace_response_returns_nothing_so_raises[anthropic]" -q`
Expected: **FAIL**. Then REVERT exactly and re-run: Expected **PASS**.

- [ ] **Step 7: Witness the transport wrap — claude-max**

Edit `sluice/core/backends.py`, in `ClaudeMaxBackend.complete`, replace the wrap with a raw re-raise:

old_string:
```python
            raise BackendError(f"claude-max invocation failed: {self._scrub(str(e))}") from None
```
new_string:
```python
            raise
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_transport_failure_surfaces_as_BackendError_not_a_raw_exception[claude-max]" -q`
Expected: **FAIL** (raw `OSError`, not `BackendError`). Then REVERT exactly and re-run: Expected **PASS**.

- [ ] **Step 8: Witness the transport wrap — openai/deepseek and anthropic**

Edit `sluice/core/backends.py`, in `OpenAiCompatibleBackend.complete`:

old_string:
```python
        except Exception as e:
            raise BackendError(f"openai-compatible call failed: {e}") from e
```
new_string:
```python
        except Exception as e:
            raise
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_transport_failure_surfaces_as_BackendError_not_a_raw_exception[openai]" "tests/conformance/test_backend_contract.py::test_transport_failure_surfaces_as_BackendError_not_a_raw_exception[deepseek]" -q`
Expected: **2 FAILED**. Then REVERT exactly.

Repeat for anthropic — Edit `AnthropicBackend.complete`:

old_string:
```python
        except Exception as e:
            raise BackendError(f"anthropic call failed: {e}") from e
```
new_string:
```python
        except Exception as e:
            raise
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_transport_failure_surfaces_as_BackendError_not_a_raw_exception[anthropic]" -q`
Expected: **FAIL**. Then REVERT exactly and re-run both node ids: Expected **PASS**.

- [ ] **Step 9: Witness the positive property**

Edit `sluice/core/backends.py`, in `OpenAiCompatibleBackend.complete`, break the valid parse (return a constant, not the text):

old_string:
```python
            text = choice["message"]["content"].strip()
            if not text:
                raise BackendError(
                    f"openai-compatible returned no text (finish_reason={reason})")
            return text
```
new_string:
```python
            text = choice["message"]["content"].strip()
            if not text:
                raise BackendError(
                    f"openai-compatible returned no text (finish_reason={reason})")
            return "WRONG"
```

(This one ADDS a distinct return value in place of `return text` — a replacement/move of the returned expression, not an added guard beside the original; the original `return text` is gone.)

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_a_valid_response_is_returned_as_its_text[openai]" -q`
Expected: **FAIL** (`"WRONG" != "HELLO"`). Then REVERT exactly and re-run: Expected **PASS**.

- [ ] **Step 10: Witness the completeness guard (the anti-drift teeth)**

Edit `tests/conformance/test_backend_contract.py`, drop `anthropic` from `_EMPTY`:

old_string:
```python
    "deepseek": lambda: {"http": _http_returning(_OPENAI_EMPTY)},
    "anthropic": lambda: {"http": _http_returning(_ANTHROPIC_EMPTY)},
}
_VALID = {
```
new_string:
```python
    "deepseek": lambda: {"http": _http_returning(_OPENAI_EMPTY)},
}
_VALID = {
```

Run: `python -m pytest "tests/conformance/test_backend_contract.py::test_payload_tables_cover_the_registry" -q`
Expected: **FAIL** (`_EMPTY is out of sync with the backend registry: {'anthropic'}`). Then REVERT this Edit in the TEST file exactly (restore the `anthropic` line) and re-run: Expected **PASS**.

- [ ] **Step 11: Confirm production is clean, then add the ARCHITECTURE.md note**

Run: `git diff --stat sluice/core/backends.py`
Expected: **no output** — every witness reverted; `sluice/` is untouched.

Edit `docs/ARCHITECTURE.md`, append a terse suite mention to the backend-seam bullet:

old_string:
```
  (`auto`/`primary`/`fallback`) sits ABOVE the provider seam, in `Sluice.backend()`:
  the config picks which provider fills each role, the role picks which backend runs.
```
new_string:
```
  (`auto`/`primary`/`fallback`) sits ABOVE the provider seam, in `Sluice.backend()`:
  the config picks which provider fills each role, the role picks which backend runs.
  `tests/conformance/test_backend_contract.py` asserts the portable contract over every
  registered provider — an empty/whitespace response and a transport failure both raise
  `BackendError` (the property `FallbackBackend` relies on), and a valid response returns as
  its text — so a new provider passes it or does not ship, exactly as the store bullet's
  conformance suite does.
```

- [ ] **Step 12: Full quality bar green**

Run: `ruff check sluice tests`
Expected: exit 0, `All checks passed!`

Run: `python -m pytest -q 2>&1 | tail -2`
Expected: **897 passed** (884 baseline + 13). Fully offline.

- [ ] **Step 13: Commit**

```bash
git add tests/conformance/test_backend_contract.py docs/ARCHITECTURE.md
git commit -m "test(backends): conformance suite for the backend seam (#39)

Assert the portable backend contract against every registered provider in one
parametrized suite (mirrors test_store_contract.py): empty/whitespace -> raise
(match=no text), transport failure -> raise (match=failed), valid -> return as text.
Per-provider payload tables (runner= for claude-max, http= for the HTTP providers)
+ a completeness test tying every table to the registry (anti-drift teeth). Empty
uses whitespace to pin the .strip()-before-check edge for all four providers.

Test-only: sluice/ is untouched. Plus a terse ARCHITECTURE.md note on the suite.
Mutation-witnessed by node id (empty/transport guards per class, the positive
parse, and the completeness guard each redden their named case)."
```

---

### Task 2: Prune the six subsumed per-class tests

**Files:**
- Modify: `tests/test_backends.py` (delete six functions; enumerate first)
- (Witness-only, restored: `sluice/core/backends.py`)

**Interfaces:**
- Consumes: the conformance suite from Task 1 (now the sole cover for the empty/transport properties).
- Produces: a `tests/test_backends.py` with only provider-SPECIFIC tests remaining.

- [ ] **Step 1: Re-enumerate the empty/transport tests from the file (do not hand-list)**

Run: `grep -n "^def test_\|^@pytest" tests/test_backends.py`

Reconcile the output against these two sets. **PRUNE** (each asserts ONLY the now-shared empty/transport property, verified in this plan):
- `test_claudemax_transport_failure_raises_backend_error`
- `test_claudemax_empty_stdout_on_exit_zero_raises` (parametrized `["", "   \n  "]` — 2 cases)
- `test_openai_compatible_empty_content_raises`
- `test_openai_compatible_transport_error_raises`
- `test_anthropic_empty_content_raises`
- `test_anthropic_transport_error_raises`

**KEEP** (provider-SPECIFIC — assert more than the shared property):
- `test_claudemax_runner_nonzero_raises` — a nonzero EXIT code is claude-max-only (only the subprocess backend has one); neither "empty" nor "transport".
- `test_openai_compatible_truncation_raises`, `test_openai_compatible_content_filter_raises`, `test_anthropic_truncation_raises` — the `finish_reason`/`stop_reason` guard, out of conformance scope (#28).
- The parse/forwarding/URL tests (`…parses_choice`, `…joins_multiple_text_blocks`, `…skips_thinking_block`, `…posts_and_parses_text`, `…posts_model_prompt_and_auth`, `make_backend…`) and **every #41 redaction test** (`test_redact_*`, `test_scrub_*`, `test_claudemax_*scrub*`/`*redact*`/`*timeout*`, including `test_claudemax_empty_response_regains_scrubbed_diagnostic` — it asserts the scrubbed diagnostic, not the bare raise).

If enumeration surfaces any empty/transport test NOT in the prune list, or a prune candidate that asserts more than the shared property, STOP and reconcile — do not delete blindly.

- [ ] **Step 2: Confirm no lost edge (per candidate)**

Each pruned case is subsumed by a conformance case that exercises the same-or-stronger path:
- **claudemax empty** (`["", "   \n  "]`): the whitespace param is the load-bearing one (its own comment: `""` is byte-identical to whitespace by the time the guard sees it and "uniquely witnesses nothing"). Conformance uses whitespace `"   \n"` → same-or-stronger; the `.strip()` mutation reddens conformance `[claude-max]` (witnessed Task 1 Step 4).
- **claudemax transport**: its FallbackBackend-catches-BackendError-only rationale is migrated verbatim into the conformance transport docstring; conformance `[claude-max]` reddens on the wrap mutation (Task 1 Step 7).
- **openai empty / transport**: identical shape (whitespace content + `finish_reason=stop`; `http` raises `OSError`); conformance `[openai]`/`[deepseek]` reddens (Steps 5, 8).
- **anthropic empty**: per-class uses `content:[]`; conformance uses a whitespace text block, which additionally exercises the join+`.strip()` (strictly stronger — the `.strip()` mutation reddens conformance `[anthropic]`, Step 6). `content:[]` is a subset of "no text".
- **anthropic transport**: identical (`http` raises `OSError`); conformance `[anthropic]` reddens (Step 8).

- [ ] **Step 3: Delete the six functions**

Edit `tests/test_backends.py` — remove each of the six functions (and the `@pytest.mark.parametrize` decorator on the claude-max empty one) with its leading comment block. Delete exactly:

```python
def test_claudemax_transport_failure_raises_backend_error():
    # The wrapper ONE LINE above the empty guard, and the same drift this file's other backends
    # do not have: both siblings pin it (test_openai_compatible_transport_error_raises) and
    # claude-max did not. It matters because FallbackBackend catches BackendError *only*, so a
    # timeout or an ssh failure escaping as a raw OSError would CRASH the run instead of
    # degrading to the fallback -- the exact opposite of what the module docstring promises.
    def boom(*a, **k):
        raise OSError("ssh: connect to host port 22: Connection refused")
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=boom)
    with pytest.raises(BackendError, match="invocation failed"):
        be.complete("x")
```

```python
@pytest.mark.parametrize("stdout", ["", "   \n  "])
def test_claudemax_empty_stdout_on_exit_zero_raises(stdout):
    # exit 0 with no text is a FAILED call that looks like a successful one -- the shape both
    # siblings already refuse (test_openai_compatible_empty_content_raises,
    # test_anthropic_empty_content_raises). Without this, complete() returns "" and the caller
    # consumes it as a real completion.
    #
    # Whitespace-only is parametrised, not decorative: `.strip()` runs BEFORE the check, so a
    # guard written as `if not proc.stdout` -- or one that drops the .strip() -- passes the ""
    # case and lets "   \n  " straight through. The whitespace param is the load-bearing one and
    # kills a strict SUPERSET: "" uniquely witnesses nothing, because both params are byte-
    # identical ("") by the time the guard sees them. It stays for the obvious reason -- it is the
    # case a reader expects to see -- not because it earns its keep as a mutant.
    class R:
        returncode, stderr = 0, ""
    R.stdout = stdout
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=lambda *a, **k: R())
    with pytest.raises(BackendError, match="no text"):
        be.complete("x")
```

```python
def test_openai_compatible_empty_content_raises():
    # Empty content on an otherwise-clean stop is not a valid CV/verdict; the
    # fallback must raise so run_batch records an error, matching AnthropicBackend.
    def http(url, data, headers, timeout):
        return '{"choices":[{"message":{"content":"   "},"finish_reason":"stop"}]}'
    be = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http)
    with pytest.raises(BackendError):
        be.complete("prompt")
```

```python
def test_openai_compatible_transport_error_raises():
    # A transport/HTTP failure must surface as BackendError, never a raw OSError,
    # so FallbackBackend and run_batch can rely on the backend contract.
    def http(*a, **k):
        raise OSError("network down")
    be = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http)
    with pytest.raises(BackendError):
        be.complete("prompt")
```

```python
def test_anthropic_empty_content_raises():
    def http(url, data, headers, timeout):
        return '{"stop_reason":"refusal","content":[]}'
    with pytest.raises(BackendError):
        AnthropicBackend("m", api_key="k", http=http).complete("x")
```

```python
def test_anthropic_transport_error_raises():
    def http(*a, **k):
        raise OSError("network down")
    with pytest.raises(BackendError):
        AnthropicBackend("m", api_key="k", http=http).complete("x")
```

- [ ] **Step 4: Run — the kept tests pass, the count drops by 7**

Run: `python -m pytest tests/test_backends.py -q 2>&1 | tail -2`
Expected: PASS. The file drops 7 cases; the retained per-class, forwarding and #41 redaction tests still pass.

Run: `ruff check tests`
Expected: exit 0 (no import became unused — the deleted functions share `ClaudeMaxBackend`/`OpenAiCompatibleBackend`/`AnthropicBackend`/`BackendError`/`pytest` with many retained tests).

- [ ] **Step 5: Post-prune witness — confirm no coverage hole**

Run: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`

Edit `sluice/core/backends.py` — DELETE the claude-max empty guard exactly as in Task 1 Step 4 (`if not text: … raise …` → `return text`).

Run: `python -m pytest tests/test_backends.py tests/conformance/test_backend_contract.py -q`
Expected: **THREE** cases red — the conformance `test_empty_or_whitespace_response_returns_nothing_so_raises[claude-max]` AND the two KEPT #41 diagnostic tests (`test_claudemax_empty_response_regains_scrubbed_diagnostic`, `…_redacts_before_truncating`), which assert the scrubbed message *content* and so also need the raise to fire. The property is therefore covered by conformance **and** (incidentally) by those two — no coverage hole; conformance is not the *sole* witness. (An earlier draft of this step wrongly predicted a single red — corrected here; the point the witness proves is "no lost edge," not "sole cover.") Then REVERT the Edit exactly and re-run: Expected **PASS**.

- [ ] **Step 6: Whole-suite green + commit**

Run: `git diff --stat sluice/core/backends.py`
Expected: **no output** (`sluice/` untouched).

Run: `python -m pytest -q 2>&1 | tail -2`
Expected: **890 passed** (897 after Task 1 − 7 pruned).

```bash
git add tests/test_backends.py
git commit -m "test(backends): prune the six per-class tests the conformance suite subsumes (#39)

Enumerated from tests/test_backends.py, not hand-listed. Removed the six empty/
transport tests now covered portably by test_backend_contract.py (7 cases): the
claude-max transport + whitespace-empty pair, and the openai/anthropic empty +
transport pair each. Kept every provider-SPECIFIC test -- nonzero-exit (claude-max
only), truncation/finish_reason/content_filter, the parse/forwarding/URL tests, and
all #41 redaction tests. The pruned claude-max transport rationale (FallbackBackend
catches BackendError only) lives in the conformance transport docstring. Post-prune
witness confirms the conformance suite is now the sole cover with no lost edge."
```

---

## Self-Review

**Spec coverage:**
- New file `tests/conformance/test_backend_contract.py` → Task 1. ✔
- Registry enumeration + fail-loudly guard → Task 1 Step 1 (`_BACKENDS` + module assert). ✔
- Per-provider payload tables + completeness guard → Task 1 Step 1 (`_EMPTY`/`_VALID`/`_TRANSPORT` + `test_payload_tables_cover_the_registry`). ✔
- Three portable properties, with the folded `match=` pins → Task 1 Step 1. ✔
- Prune the six subsumed per-class tests, enumerate-don't-hand-list → Task 2. ✔
- Migrate the claude-max transport rationale into the conformance docstring → baked into Task 1 Step 1 transport docstring; verified in Task 2 Step 2. ✔
- arc-001 ARCHITECTURE.md note → Task 1 Step 11. ✔
- Mutation witnesses (empty/transport/valid/completeness) by node id → Task 1 Steps 4-10; post-prune re-witness → Task 2 Step 5. ✔
- Non-goals (truncation, ABC, sluice/ change, FallbackBackend as participant) → honoured: no truncation property, no ABC, backends.py restored, FallbackBackend absent from `_BACKENDS`. ✔

**Placeholder scan:** no TBD/TODO; every step has exact code, exact commands, and expected output. ✔

**Type/name consistency:** `_backend(name, table)`, `_BACKENDS`, `_EMPTY`/`_VALID`/`_TRANSPORT`, `_Proc`, `_http_returning`/`_http_raising`/`_runner_returning`/`_runner_raising`, `make_backend`, `BackendError` used consistently across steps. Node ids match the function names and the `_BACKENDS` param values. ✔

**Note on the completeness guard (design divergence, +1 case):** the design sketched the completeness check as a module-level `for`/assert (0 cases). This plan promotes it to a standalone test `test_payload_tables_cover_the_registry` (1 case) so a dropped table entry reddens by NODE ID rather than as a blunt collection error — the same property, cleanly witnessable (memory: "mutation-witnessed by node id"). Net count is therefore +13/−7 (final **890**), one above the design's +12/−7 sketch.
