"""The flat-rate `claude --print` CLI backend, registered as `claude-max`.

Needs no API key: it shells the flat-rate CLI. `runner` is omitted when None so
ClaudeMaxBackend's subprocess.run default applies -- make_backend always forwards a
concrete runner, but keeping the factory independently constructible matters for the
seam's own guard suite.
"""
from sluice.backends import register
from sluice.core.backends import ClaudeMaxBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=300,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    extra = {} if runner is None else {"runner": runner}
    return ClaudeMaxBackend(model, host=claude_host, claude_path=claude_path,
                            effort=effort, timeout=timeout, **extra)


register("claude-max", _make)
