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
