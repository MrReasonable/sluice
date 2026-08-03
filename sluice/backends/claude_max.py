"""The flat-rate `claude --print` CLI backend, registered as `claude-max`.

Needs no API key: it shells the flat-rate CLI. `runner` is omitted when None so
ClaudeMaxBackend's subprocess.run default applies -- make_backend always forwards a
concrete runner, but keeping the factory independently constructible matters for the
seam's own guard suite.
"""
from sluice.backends import register
from sluice.core.backends import ClaudeMaxBackend


# This provider shells an AGENT over a (possibly remote) CLI at `--effort max`, so it is
# the slowest of the four by a wide margin -- measured 46-111s for a real composition
# against a 24-entry bundle, locally. The number lives here rather than being spelled at
# each call site so there is one thing to change when that stops being true.
_DEFAULT_TIMEOUT = 300


def _make(model, *, api_key="", base_url="", http=None, runner=None,
          timeout=_DEFAULT_TIMEOUT, max_tokens=None, claude_host="",
          claude_path="claude", effort="max"):
    extra = {} if runner is None else {"runner": runner}
    # COALESCE None, do not lean on the signature default. A default only applies when the
    # argument is OMITTED, and `make_backend` has a `timeout` parameter a caller can pass
    # through as None -- which would reach `subprocess.run(timeout=None)` and wait FOREVER.
    # A hung compose host then hangs the whole run with nothing to end it, which is worse
    # than any wrong-but-finite value. `is None` rather than falsiness on purpose: 0 is a
    # nonsensical timeout but it is an explicit one, and silently rewriting it to 300 would
    # hide the config error instead of letting it surface.
    return ClaudeMaxBackend(model, host=claude_host, claude_path=claude_path,
                            effort=effort,
                            timeout=_DEFAULT_TIMEOUT if timeout is None else timeout,
                            **extra)


register("claude-max", _make)
