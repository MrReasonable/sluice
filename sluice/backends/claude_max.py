"""The flat-rate `claude --print` CLI backend, registered as `claude-max`.

Needs no API key: it shells the flat-rate CLI. `runner` and `timeout` are omitted when
None so ClaudeMaxBackend's own defaults apply -- make_backend always forwards a concrete
runner, but keeping the factory independently constructible matters for the seam's own
guard suite, which resolves factories through `plugins.get` and bypasses make_backend.
"""
from sluice.backends import register
from sluice.core.backends import ClaudeMaxBackend


def _make(model, *, api_key="", base_url="", http=None, runner=None, timeout=None,
          max_tokens=None, claude_host="", claude_path="claude", effort="max",
          provider=""):
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
    # See the deepseek/openai/anthropic siblings: omit when unset so the class default
    # applies. This backend reports no token COUNTS (flat-rate, text mode), but it still
    # labels the call with its provider and model so the usage log can record that it
    # happened -- see ClaudeMaxBackend.complete.
    if provider:
        extra["provider"] = provider
    return ClaudeMaxBackend(model, host=claude_host, claude_path=claude_path,
                            effort=effort, **extra)


# What this backend needs before it can run, in the user's terms. It needs no API key, so the
# key-derived line every per-token backend gets (core.app.api_key_env) would state nothing; the
# career coach's prompt reads this attribute instead (sluice/onboard/coach/__init__.py::
# _backend_requirements), and tests/test_coach_prompt.py fails if a key-less backend lacks one.
# A requirement that went unstated read as "needs nothing" beside the per-token lines.
_make.requirement = ("no API key; the `claude` command-line program, installed and signed in "
                     "on the machine where the sluice server runs, or on the machine a "
                     "configured claude host names, which sluice reaches over ssh.")

register("claude-max", _make)
