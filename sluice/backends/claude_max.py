"""The flat-rate `claude --print` CLI backend, registered as `claude-max`.

Needs no API key: it shells the flat-rate CLI. `runner` and `timeout` are omitted when
None so ClaudeMaxBackend's own defaults apply -- make_backend always forwards a concrete
runner, but keeping the factory independently constructible matters for the seam's own
guard suite, which resolves factories through `plugins.get` and bypasses make_backend.
"""
from sluice.backends import register
from sluice.core.backends import ClaudeMaxBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=None,
          max_tokens=None, claude_host="", claude_path="claude", effort="max"):
    extra = {} if runner is None else {"runner": runner}
    # OMIT when None rather than coalescing to a number spelled here. An earlier version
    # of this factory carried its own `_DEFAULT_TIMEOUT = 300` and claimed to be "one
    # thing to change" -- it was INERT: make_backend coalesces None before the factory is
    # ever called, so rebinding it changed nothing and a maintainer raising it for slow
    # composes would have got a silent no-op. The value now has exactly one home
    # (`core.backends.DEFAULT_TIMEOUT`, which is ClaudeMaxBackend's own default), and this
    # branch exists for the DIRECT construction path, where an explicit None would
    # otherwise reach `subprocess.run(timeout=None)` and wait forever.
    if timeout is not None:
        extra["timeout"] = timeout
    return ClaudeMaxBackend(model, host=claude_host, claude_path=claude_path,
                            effort=effort, **extra)


register("claude-max", _make)
