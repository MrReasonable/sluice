"""The OpenAI chat/completions backend, registered as `openai`.

Same OpenAI-compatible class as deepseek, pointed at the OpenAI default endpoint. Any
other OpenAI-compatible provider (Together, a local server) is reachable by repointing
base_url on this one, or by adding a sibling module.
"""
from sluice.backends import register
from sluice.core.backends import BackendError, DEFAULT_BASE_URLS, OpenAiCompatibleBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=None,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    if not api_key:
        raise BackendError(
            "backend 'openai' requires an api_key (set the provider's API key env var)")
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
    return OpenAiCompatibleBackend(model, api_key=api_key,
                                   base_url=base_url or DEFAULT_BASE_URLS["openai"],
                                   max_tokens=max_tokens, **extra)


register("openai", _make)
