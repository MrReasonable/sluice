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

Each negative property is exercised over the DISTINCT SHAPES its contract names, not one
representative: the empty property over a WHITESPACE-only and a truly-BLANK response; the
transport property over a TIMEOUT and a generic transport ERROR. A fixture that only tested
one shape would let the property's own name ("empty OR whitespace"; "OSError/TimeoutExpired")
outrun what it verifies -- the exact assert-vs-claim gap this repo engineers out.

Test-only: sluice/ is untouched. A provider found to VIOLATE a property is a separate fix
PR -- this suite's job is to surface it.
"""
import subprocess

import pytest

from sluice.core.app import Sluice
from sluice.core.backends import BackendError, make_backend

_BACKENDS = Sluice.available("backend")   # ['anthropic', 'claude-max', 'deepseek', 'openai']

# A parametrize over [] skips every test and exits 0 -- the suite that is "the reason the
# seam is safe" would report success having tested nothing, and plugins.autoload swallows a
# broken plugin's ImportError, so an empty registry is a realistic accident. Fail loudly.
# (Mirrors test_store_contract.py's module-level fail-loudly assert.)
assert _BACKENDS, "no backend is registered: the contract suite would pass vacuously"

# The two shapes each negative property is exercised over (see the module docstring).
_EMPTY_KINDS = ("whitespace", "blank")
_TRANSPORT_KINDS = ("timeout", "error")


class _Proc:
    """A minimal fake completed-process for the claude-max runner: exactly the three
    attributes ClaudeMaxBackend.complete reads."""
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _runner_returning(proc):
    return lambda *a, **k: proc


def _raising_runner(exc):
    def runner(*a, **k):
        raise exc
    return runner


def _http_returning(payload):
    def http(url, data, headers, timeout):
        return payload
    return http


def _raising_http(exc):
    def http(*a, **k):
        raise exc
    return http


# Empty-response shapes. WHITESPACE (not "") is the mutation-load-bearing one -- it also pins
# the .strip()-before-check edge (a guard written `if not stdout` or one that drops .strip()
# lets it through), the edge test_claudemax_empty_stdout_on_exit_zero_raises carried, now
# extended to all four providers. BLANK is the truly-empty shape the property's name claims:
# "" for the string backends, and for anthropic a structurally DISTINCT content:[] (a refusal
# returns no text blocks at all -- not a subset of a whitespace block, a different parse path).
# finish_reason=stop / stop_reason=end_turn so the EMPTY guard fires, NOT the truncation guard
# (out of scope -- #28; see the module docstring / spec Non-goals).
_OPENAI_EMPTY_WS = '{"choices":[{"message":{"content":"   \\n"},"finish_reason":"stop"}]}'
_OPENAI_EMPTY_BLANK = '{"choices":[{"message":{"content":""},"finish_reason":"stop"}]}'
_OPENAI_VALID = '{"choices":[{"message":{"content":"HELLO"},"finish_reason":"stop"}]}'
_ANTHROPIC_EMPTY_WS = '{"stop_reason":"end_turn","content":[{"type":"text","text":"   \\n"}]}'
_ANTHROPIC_EMPTY_BLANK = '{"stop_reason":"end_turn","content":[]}'
_ANTHROPIC_VALID = '{"stop_reason":"end_turn","content":[{"type":"text","text":"HELLO"}]}'


def _timeout(name):
    # claude-max shells a subprocess, so a hung host raises subprocess.TimeoutExpired (str carries
    # the argv -- the leak path #41's scrub exists for). The HTTP backends time out at the socket,
    # which urllib surfaces as TimeoutError (an OSError subclass). Both must reach `except Exception`
    # and become BackendError.
    if name == "claude-max":
        return {"runner": _raising_runner(subprocess.TimeoutExpired(cmd=["claude"], timeout=1))}
    return {"http": _raising_http(TimeoutError("timed out"))}


def _error(name):
    # A generic transport ERROR: a refused ssh / a dropped network, both OSError.
    if name == "claude-max":
        return {"runner": _raising_runner(OSError("ssh: connect to host port 22: Connection refused"))}
    return {"http": _raising_http(OSError("network down"))}


# Each table maps a key -> a THUNK returning the injected-kwargs dict for make_backend:
# {"runner": ...} for claude-max, {"http": ...} for the HTTP providers. A thunk (not a value)
# so every test gets a fresh fake. The negative tables are keyed by (name, kind) so every
# provider is exercised over both shapes; _VALID is keyed by name (one positive shape suffices).
# openai and deepseek are the same class, so they share a payload.
_EMPTY = {
    ("claude-max", "whitespace"): lambda: {"runner": _runner_returning(_Proc(0, "   \n", ""))},
    ("claude-max", "blank"): lambda: {"runner": _runner_returning(_Proc(0, "", ""))},
    ("openai", "whitespace"): lambda: {"http": _http_returning(_OPENAI_EMPTY_WS)},
    ("openai", "blank"): lambda: {"http": _http_returning(_OPENAI_EMPTY_BLANK)},
    ("deepseek", "whitespace"): lambda: {"http": _http_returning(_OPENAI_EMPTY_WS)},
    ("deepseek", "blank"): lambda: {"http": _http_returning(_OPENAI_EMPTY_BLANK)},
    ("anthropic", "whitespace"): lambda: {"http": _http_returning(_ANTHROPIC_EMPTY_WS)},
    ("anthropic", "blank"): lambda: {"http": _http_returning(_ANTHROPIC_EMPTY_BLANK)},
}
_TRANSPORT = {
    (name, kind): (lambda n=name, k=kind: _timeout(n) if k == "timeout" else _error(n))
    for name in _BACKENDS for kind in _TRANSPORT_KINDS
}
_VALID = {
    "claude-max": lambda: {"runner": _runner_returning(_Proc(0, "HELLO\n", ""))},
    "openai": lambda: {"http": _http_returning(_OPENAI_VALID)},
    "deepseek": lambda: {"http": _http_returning(_OPENAI_VALID)},
    "anthropic": lambda: {"http": _http_returning(_ANTHROPIC_VALID)},
}


def _build(name, thunk):
    # api_key is required by the per-token factories and ignored by claude-max, so pass one
    # uniformly. base_url is left to default -- the injected fake http/runner ignores it.
    return make_backend(name, "test-model", api_key="test-key", **thunk())


def test_payload_tables_cover_the_registry():
    """The anti-drift teeth (#39's whole point; mirrors #63's registry-completeness guard). A
    NEW provider that registers but is not added to these tables would silently ESCAPE the
    contract suite -- the exact drift #39 exists to stop. Every table covers every registered
    provider (the negative tables over every (provider, shape) pair), exactly. A standalone
    test (not a module-level assert) so a dropped entry reddens by node id rather than as a
    blunt collection error."""
    assert set(_VALID) == set(_BACKENDS), \
        f"_VALID is out of sync with the backend registry: {set(_BACKENDS) ^ set(_VALID)}"
    assert set(_EMPTY) == {(n, k) for n in _BACKENDS for k in _EMPTY_KINDS}, \
        f"_EMPTY is out of sync: {set(_EMPTY) ^ {(n, k) for n in _BACKENDS for k in _EMPTY_KINDS}}"
    assert set(_TRANSPORT) == {(n, k) for n in _BACKENDS for k in _TRANSPORT_KINDS}, \
        f"_TRANSPORT is out of sync: {set(_TRANSPORT) ^ {(n, k) for n in _BACKENDS for k in _TRANSPORT_KINDS}}"


@pytest.mark.parametrize("kind", _EMPTY_KINDS)
@pytest.mark.parametrize("name", _BACKENDS)
def test_empty_or_whitespace_response_returns_nothing_so_raises(name, kind):
    """complete() never hands back a falsy string. An empty OR whitespace-only response is a
    FAILED call wearing a successful one's clothes; it must raise BackendError so
    FallbackBackend degrades to the fallback (it catches BackendError only). claude-max shipped
    WITHOUT this guard and stayed green its whole life because only bespoke per-class tests
    covered it (#39). Exercised over BOTH shapes the name claims -- `whitespace` (also pins the
    .strip()-before-check) and `blank` (the truly-empty response; anthropic's is a distinct
    content:[] refusal). match= pins the message, not just the type: all four providers say
    "no text", so it restores the pruned per-class tests' specificity at zero cost (inv-001/tst-001)."""
    with pytest.raises(BackendError, match="no text"):
        _build(name, _EMPTY[(name, kind)]).complete("prompt")


@pytest.mark.parametrize("kind", _TRANSPORT_KINDS)
@pytest.mark.parametrize("name", _BACKENDS)
def test_transport_failure_surfaces_as_BackendError_not_a_raw_exception(name, kind):
    """A transport failure (from the runner/poster) must surface as BackendError, never the raw
    exception. This is the ONE property FallbackBackend depends on: it catches BackendError
    ONLY, so a timeout or an ssh failure escaping raw would CRASH the run instead of degrading
    to the fallback -- the exact opposite of what the module docstring promises, and the second
    drift PR #37 fixed one line above the first. (This docstring carries the rationale migrated
    from the pruned test_claudemax_transport_failure_raises_backend_error.) Exercised over both
    a `timeout` (subprocess.TimeoutExpired for the CLI backend -- the hung-host case -- and a
    socket TimeoutError for the HTTP ones) and a generic `error` (OSError). All four providers
    say "...failed" on this path, so match= restores that test's message-pin."""
    with pytest.raises(BackendError, match="failed"):
        _build(name, _TRANSPORT[(name, kind)]).complete("prompt")


@pytest.mark.parametrize("name", _BACKENDS)
def test_a_valid_response_is_returned_as_its_text(name):
    """The positive half: a well-formed non-empty response comes back as its text, unchanged.
    Without this, a backend that raised on EVERYTHING would pass both negative properties while
    being wholly broken -- the two 'raises' tests cannot tell a strict backend from a dead
    one."""
    assert _build(name, _VALID[name]).complete("prompt") == "HELLO"
