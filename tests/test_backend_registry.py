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
