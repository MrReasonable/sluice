# Backend conformance suite — design

- **Date**: 2026-07-25
- **Status**: `/review-plan` complete (0 Critical / 0 High / 2 Low); both Low findings folded
  (arc-001 ARCHITECTURE.md note; inv-001/tst-001 `match=` message-pins). Ready for writing-plans.
- **Origin**: issue #39 (raised independently by the architect `arc-001` and test-engineer `tst-002`
  reviewing PR #37). The backend seam has four registered providers / three classes, each
  re-deriving the same contract with no shared suite — the exact condition
  `docs/ARCHITECTURE.md:95-99` gives for why the *store* seam earned its conformance suite. It has
  already materialised twice at the backend seam (claude-max shipped without the empty-response guard
  both siblings had; the transport wrapper was pinned by no test), both fixed by hand in PR #37.
- **User decisions (2026-07-25)**: (1) **add a positive property** (a valid response is returned as
  its text) beyond the issue's two negative ones; (2) **prune** the per-class empty/transport tests
  that the conformance suite subsumes.

## Goal

Assert the *portable* backend contract — what is true of **every** registered provider, not what is
true of one — in a single parametrized suite, so a new provider passes it or does not ship. Mirror
`tests/conformance/test_store_contract.py`, which is "the reason the store seam is safe to open".

## Background — why this is not premature abstraction

The issue's case is decisive: the drift the suite prevents has **already happened twice** in one class
(claude-max's missing empty-response guard; the un-pinned `except Exception -> BackendError` transport
wrapper that `FallbackBackend` depends on). An ABC/Protocol is explicitly rejected — it can only pin a
*signature*, which duck-typing already gives; it cannot express "raises on empty", which is the entire
contract that drifted. The suite is the only thing that can.

The asymmetry that makes this more than a `parametrize`: backends inject differently. `ClaudeMaxBackend`
takes `runner=` (a subprocess), the HTTP backends take `http=` (a poster). Stores build uniformly from
a seed; backends need a per-provider fake whose *shape* differs (a fake completed-process vs a JSON
string). Hence a small per-provider payload table rather than a bare parametrize.

## Design

One new file: `tests/conformance/test_backend_contract.py`. Two edits: prune the now-subsumed per-class
tests from `tests/test_backends.py`, and add a terse `docs/ARCHITECTURE.md` note on the suite in the
backend-seam bullet (~:211-216), mirroring how the store bullet foregrounds `test_store_contract.py`
(~:124) — so the docs surface the backend contract the same way (arc-001). No production change —
`sluice/` is untouched (`docs/` is not `sluice/`).

### Registry enumeration + fail-loudly guard (store-suite lesson)

```python
_BACKENDS = Sluice.available("backend")   # ["anthropic", "claude-max", "deepseek", "openai"]
# A parametrize over [] skips every test and exits 0 — the suite that is "the reason the seam is
# safe" would report success having tested nothing, and autoload swallows a broken plugin's
# ImportError, so an empty registry is a realistic accident. Fail loudly. (test_store_contract.py:39)
assert _BACKENDS, "no backend is registered: the contract suite would pass vacuously"
pytestmark = pytest.mark.parametrize("name", _BACKENDS)
```

### Per-provider payload tables + completeness guard (the anti-drift teeth)

Each table maps a provider name to a **thunk returning the injected-kwargs dict** for `make_backend`
— `{"runner": …}` for claude-max, `{"http": …}` for the HTTP providers. Three tables, one per
property:

- `_EMPTY[name]()` — a fake that yields an **empty-or-whitespace** response.
- `_TRANSPORT[name]()` — a fake that **raises** a transport error (`OSError`/`TimeoutExpired`).
- `_VALID[name]()` — a fake that yields a valid `"HELLO"` response.

```python
# A NEW provider that registers but is not added to these tables must fail the build LOUDLY — that
# is the whole point (the drift #39 exists to stop). Mirrors #63's registry-completeness guard.
for _t, _n in ((_EMPTY, "_EMPTY"), (_TRANSPORT, "_TRANSPORT"), (_VALID, "_VALID")):
    assert set(_t) == set(_BACKENDS), \
        f"{_n} is out of sync with the backend registry: {set(_BACKENDS) ^ set(_t)}"
```

**Payload shapes** (whitespace, not `""`, for the empty case — it also pins the `.strip()`-before-check
edge that `test_claudemax_empty_stdout_on_exit_zero_raises` carried, now extended to all four providers):

| provider | inject | empty (`_EMPTY`) | valid (`_VALID`) | transport (`_TRANSPORT`) |
| --- | --- | --- | --- | --- |
| claude-max | `runner=` | `_Proc(returncode=0, stdout="   \n", stderr="")` | `_Proc(0, "HELLO\n", "")` | `runner` raises `OSError` |
| openai / deepseek | `http=` | `{"choices":[{"message":{"content":"   \n"},"finish_reason":"stop"}]}` | `…content":"HELLO"…` | `http` raises `OSError` |
| anthropic | `http=` | `{"stop_reason":"end_turn","content":[{"type":"text","text":"   \n"}]}` | `…"text":"HELLO"…` | `http` raises `OSError` |

`_Proc` is a tiny fake completed-process (`returncode`/`stdout`/`stderr`). The empty JSON uses
`finish_reason="stop"` / `stop_reason="end_turn"` so the empty-response guard fires — **not** the
truncation guard (which is out of scope; see Non-goals). Construction is uniform through the seam:

```python
def _backend(name, table):
    # api_key is required by the per-token factories and ignored by claude-max, so pass one uniformly.
    return make_backend(name, "test-model", api_key="test-key", **table[name]())
```

### The three portable properties

```python
def test_empty_or_whitespace_response_returns_nothing_so_raises(name):
    """complete() never hands back a falsy string. An empty OR whitespace-only response is a FAILED
    call wearing a successful one's clothes; it must raise BackendError so FallbackBackend degrades
    to the fallback (it catches BackendError only). claude-max shipped WITHOUT this guard and stayed
    green its whole life because only bespoke per-class tests covered it (#39)."""
    # match= pins the message, not just the type: all four providers say "no text" on
    # the empty path, so it restores the specificity the pruned per-class claude-max test
    # carried at zero per-provider cost (inv-001/tst-001).
    with pytest.raises(BackendError, match="no text"):
        _backend(name, _EMPTY).complete("prompt")

def test_transport_failure_surfaces_as_BackendError_not_a_raw_exception(name):
    """A transport failure (OSError/TimeoutExpired from the runner/poster) must surface as
    BackendError, never the raw exception. This is the ONE property FallbackBackend depends on: it
    catches BackendError only, so a timeout escaping raw would CRASH the run instead of degrading —
    the exact second drift PR #37 fixed one line above the first."""
    # All four providers say "...failed" on the transport path (invocation/call failed),
    # so match= restores the pruned claude-max transport test's message-pin (inv-001/tst-001).
    with pytest.raises(BackendError, match="failed"):
        _backend(name, _TRANSPORT).complete("prompt")

def test_a_valid_response_is_returned_as_its_text(name):
    """The positive half: a well-formed non-empty response comes back as its text, unchanged. Without
    this, a backend that raised on EVERYTHING would pass both negative properties while being wholly
    broken — the two 'raises' tests cannot tell a strict backend from a dead one."""
    assert _backend(name, _VALID).complete("prompt") == "HELLO"
```

### Prune the subsumed per-class tests (`tests/test_backends.py`)

The user chose to prune the duplicates. **Enumerate from the file, do not hand-list** (THE LESSON).
Prune candidates (each asserts only the now-shared property — verified per-test at implementation):

- `test_claudemax_transport_failure_raises_backend_error` — its docstring rationale ("FallbackBackend
  catches BackendError only, so a timeout escaping raw would CRASH the run") **migrates into the
  conformance transport test's docstring** rather than being lost.
- `test_claudemax_empty_stdout_on_exit_zero_raises` — the whitespace edge is preserved by the
  conformance empty payload being whitespace.
- `test_openai_compatible_empty_content_raises`, `test_openai_compatible_transport_error_raises`
- `test_anthropic_empty_content_raises`, `test_anthropic_transport_error_raises`

**Kept** (provider-specific, NOT covered by conformance):

- `test_claudemax_runner_nonzero_raises` — a nonzero **exit code** is a claude-max-only property (only
  the subprocess backend has one); it is neither "empty" nor "transport". Keep.
- Truncation (`test_anthropic_truncation_raises`, the openai `finish_reason` guard), the
  multi-text-block join, the thinking-block skip, URL construction, the `make_backend` forwarding
  tests, and every #41 redaction test (`…scrubs_host…`, `…regains_scrubbed_diagnostic`,
  `…redacts_before_truncating`, `…timeout_chain_carries_no_secret`, the `_redact`/`_scrub` units). Keep.

Any prune candidate found to assert something beyond the shared property at implementation time is
**kept** and noted, not pruned blindly.

## Testing / verification

This IS the test change. Verification is that the new suite is non-vacuous and load-bearing:

- Every parametrized case runs against all four providers (assert the collected count reflects
  4 × 3 properties, not a vacuous skip).
- **Mutation witnesses** (run after the checked-hash `compileall`; each reddens a named parametrized
  case by node id, per provider):
  - remove the `not text: raise` guard in a backend's `complete()` → the empty test reddens for that
    provider (this is literally the guard claude-max once lacked).
  - remove the `except … raise BackendError(...) from …` transport wrap → the transport test reddens.
  - break a valid parse → the positive test reddens.
  - **delete a provider from a payload table** → the **completeness guard** reddens (the anti-drift
    property itself is witnessed).
  - the fail-loudly guard: witnessed by reasoning (a `[]` registry is not reachable in-suite), noted.
- `ruff check sluice tests`; `python -m pytest` green; record the net test-count delta (adds
  4×3 conformance cases, removes the six pruned per-class tests).

## Non-goals

- **The truncation guard.** `finish_reason`/`stop_reason` is real for the two HTTP backends and absent
  for claude-max *in text mode* (it exists under `--output-format json`; adopting that is #28 / a
  separate change). A conformance suite asserting it would fail claude-max for a property it does not
  currently have. Kept as provider-specific per-class tests.
- **An ABC/Protocol.** Explicitly rejected by the issue — pins a signature, not "raises on empty".
- **Any `sluice/` production change.** Test-only. If a provider is found to violate a property, that is
  a separate fix PR; this suite's job is to *surface* it.
- **FallbackBackend as a conformance participant.** It is a composite, not a registered provider (not
  in `Sluice.available("backend")`), and its contract is the opposite (degrade, don't raise). It is
  the consumer this suite protects, not a participant.

## Process

Proportionate (test-only, like #63): brainstorm → `/review-plan` → subagent SDD (Task 1: the
conformance suite; Task 2: prune the duplicates + verify no lost edge) → `/review-pr` before push →
CodeRabbit cloud → merge gate.

Commit shape: `test(backends): conformance suite for the backend seam (#39)`.
