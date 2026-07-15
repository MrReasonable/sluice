"""The DeepSeek OpenAI-compatible chat/completions backend, registered as `deepseek`.

Per-token: an empty api_key is fatal at construction. max_tokens stays None (uncapped)
unless set, matching direct construction so a config-driven fallback is never silently
capped.
"""
from sluice.backends import register
from sluice.core.backends import BackendError, DEFAULT_BASE_URLS, OpenAiCompatibleBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    if not api_key:
        raise BackendError(
            "backend 'deepseek' requires an api_key (set the provider's API key env var)")
    extra = {} if http is None else {"http": http}
    return OpenAiCompatibleBackend(model, api_key=api_key,
                                   base_url=base_url or DEFAULT_BASE_URLS["deepseek"],
                                   timeout=timeout, max_tokens=max_tokens, **extra)


register("deepseek", _make)
