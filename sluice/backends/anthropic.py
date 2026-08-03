"""The direct Anthropic Messages API backend, registered as `anthropic`.

Per-token: an empty api_key is fatal at construction (a deferred key becomes an opaque
401 mid-run). max_tokens is omitted when None so AnthropicBackend's own required default
(8192) applies -- the Anthropic API mandates the field.
"""
from sluice.backends import register
from sluice.core.backends import AnthropicBackend, BackendError, DEFAULT_BASE_URLS


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=None,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    if not api_key:
        raise BackendError(
            "backend 'anthropic' requires an api_key (set the provider's API key env var)")
    extra = {} if http is None else {"http": http}
    # OMIT when None so the class default applies -- the seam's existing
    # 'no preference' idiom, the same one `http`/`runner`/`max_tokens` use.
    # Without it an explicit None reaches `urlopen(timeout=None)`, which
    # blocks on the socket default: the same wait-forever as
    # `subprocess.run(timeout=None)`, by a different call. Reachable
    # directly -- tests/test_backend_registry.py resolves factories through
    # `plugins.get`, bypassing make_backend's coalesce entirely.
    if timeout is not None:
        extra["timeout"] = timeout
    mt = {} if max_tokens is None else {"max_tokens": max_tokens}
    return AnthropicBackend(model, api_key=api_key,
                            base_url=base_url or DEFAULT_BASE_URLS["anthropic"],
                            **extra, **mt)


register("anthropic", _make)
