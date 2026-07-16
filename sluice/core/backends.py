"""LLM judge backends, flat-rate first and self-healing.

ClaudeMaxBackend shells `claude --print` on a configured host (flat-rate, the
primary): set `host` to ssh there, or leave it empty to run `claude_path`
locally. AnthropicBackend calls the Anthropic Messages API directly, and
OpenAiCompatibleBackend calls an OpenAI-compatible chat/completions endpoint
(per-token, the fallback). FallbackBackend tries the primary and, if it errors
(primary host down, timeout, nonzero exit), falls back automatically so a run
is never blocked. `make_backend` builds any backend by name -- delegating the
per-provider construction to the `backend` seam registry (`sluice/backends/`) so
selection is config-driven and a new provider is a drop-in module. The subprocess
runner and HTTP poster are injected, so everything is tested offline.
"""
import json
import subprocess
import urllib.error
import urllib.request

from sluice.core.log import get_logger

_log = get_logger("core.backends")

DEFAULT_MODELS = {
    "claude-max": "claude-sonnet-4-5",
    "anthropic": "claude-sonnet-4-5",
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o",
}

# Each per-token provider's default API root, overridable per-deployment via the
# provider's *_BASE_URL env var. Named here rather than inlined at each call site
# so the endpoint has one definition to audit and change -- and so tests can pin
# the default without restating a live URL. claude-max is absent: it shells the
# flat-rate CLI and has no endpoint.
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}


class BackendError(Exception):
    pass


def _urlopen(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        # The provider's actual complaint -- unknown model id, bad key, rate limit
        # -- is in the response *body*. urllib does not put it in str(e), so
        # without this every 4xx/5xx collapses to "HTTP Error 400: Bad Request"
        # and the real cause is lost. Read it once and attach it.
        try:
            detail = e.read().decode(errors="replace").strip()
        except Exception:  # body already consumed / not readable; fall back to the reason
            detail = ""
        raise BackendError(
            f"HTTP {e.code} from {url}: {detail[:500] or e.reason}") from e


class ClaudeMaxBackend:
    def __init__(self, model, *, host: str = "", claude_path: str = "claude",
                 cmd_template=None, runner=subprocess.run,
                 timeout=300, effort="max"):
        # cmd_template is the argv up to (but not including) the prompt on stdin.
        # host/claude_path are ignored once cmd_template is supplied explicitly.
        self.model = model
        self.host = host
        self.claude_path = claude_path
        if cmd_template is not None:
            self.cmd_template = cmd_template
        else:
            base = [
                claude_path, "--print",
                "--model", model, "--effort", effort,
                "--permission-mode", "bypassPermissions",
            ]
            # Empty host runs claude_path locally; a configured host (e.g.
            # "<your-claude-host>") shells out over ssh instead. claude is
            # commonly NOT on a remote host's non-interactive PATH, so
            # claude_path should be the absolute path in that case.
            self.cmd_template = ["ssh", host] + base if host else base
        self.runner = runner
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        try:
            proc = self.runner(self.cmd_template, input=prompt,
                               capture_output=True, text=True, timeout=self.timeout)
        except Exception as e:  # timeout, ssh failure, missing binary
            raise BackendError(f"claude-max invocation failed: {e}") from e
        if proc.returncode != 0:
            raise BackendError(f"claude-max exit {proc.returncode}: {proc.stderr[:200]}")
        text = proc.stdout.strip()
        # Exit 0 with no text is a FAILED call wearing a successful one's clothes. Both
        # siblings already refuse it (OpenAiCompatibleBackend, AnthropicBackend); claude-max
        # was the outlier, returning "" for the caller to notice by itself. Raising here means
        # the same underlying condition triggers the documented fallback whichever provider
        # hits it, instead of one raising and one handing back a useless string.
        #
        # Only the EMPTY half of the siblings' pair is portable: their other guard keys on
        # finish_reason/stop_reason to catch a truncation, and a CLI exposes no such field.
        # That is a real absence, not an oversight to fix later.
        if not text:
            raise BackendError(
                f"claude-max returned no text (exit 0, {len(proc.stdout)} chars of whitespace)"
            )
        return text


class OpenAiCompatibleBackend:
    """Any OpenAI-compatible chat/completions endpoint (DeepSeek, OpenAI, Together,
    a local server, ...). Provider is just a base_url + key. `max_tokens` is sent
    only when set; an incomplete response (finish_reason other than stop, e.g.
    length or content_filter) or empty content is a hard error, not a partial."""

    def __init__(self, model, *, api_key, base_url, http=_urlopen, timeout=300,
                 max_tokens=None):
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.http = http
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> str:
        body = {"model": self.model,
                "messages": [{"role": "user", "content": prompt}]}
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        try:
            data = json.loads(self.http(self.url, json.dumps(body).encode(),
                                        headers, self.timeout))
            choice = data["choices"][0]
            reason = choice.get("finish_reason")
            # Only a natural stop (or an endpoint that omits the field) is a
            # complete answer. length/content_filter/etc. are partials and must
            # fail loudly, not slip through as a truncated CV -- mirror the
            # AnthropicBackend guards below.
            if reason not in (None, "stop"):
                raise BackendError(
                    f"openai-compatible response incomplete (finish_reason={reason})")
            text = choice["message"]["content"].strip()
            if not text:
                raise BackendError(
                    f"openai-compatible returned no text (finish_reason={reason})")
            return text
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"openai-compatible call failed: {e}") from e


class AnthropicBackend:
    """Direct Anthropic Messages API client (no `claude` CLI needed). The response
    content is a list of typed blocks (text, thinking, tool_use); we join every
    text block. Empty text content (as a refusal produces) and a truncation
    (stop_reason==max_tokens) are both hard errors, never a silent partial."""

    _VERSION = "2023-06-01"

    def __init__(self, model, *, api_key, base_url=DEFAULT_BASE_URLS["anthropic"],
                 http=_urlopen, timeout=300, max_tokens=8192):
        self.model = model
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.api_key = api_key
        self.http = http
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> str:
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"content-type": "application/json",
                   "x-api-key": self.api_key,
                   "anthropic-version": self._VERSION}
        try:
            data = json.loads(self.http(self.url, json.dumps(body).encode(),
                                        headers, self.timeout))
            if data.get("stop_reason") == "max_tokens":
                raise BackendError("anthropic response truncated (stop_reason=max_tokens)")
            text = "\n".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text" and b.get("text")).strip()
            if not text:
                raise BackendError(
                    f"anthropic returned no text (stop_reason={data.get('stop_reason')})")
            return text
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"anthropic call failed: {e}") from e


class FallbackBackend:
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback
        self.last_backend = None

    def complete(self, prompt: str) -> str:
        try:
            out = self.primary.complete(prompt)
            self.last_backend = "primary"
            return out
        except BackendError as e:
            _log.warning("primary backend failed, falling back: %s", e)
            try:
                out = self.fallback.complete(prompt)
            except BackendError as fe:
                # Both legs are down. Report both causes: the fallback's error alone
                # is the less interesting half (the primary going down is what put us
                # here), and chaining from the primary keeps its traceback attached.
                raise BackendError(
                    f"both backends failed: primary={e}; fallback={fe}") from e
            self.last_backend = "fallback"
            return out


def make_backend(name, model="", *, http=_urlopen, runner=subprocess.run, timeout=300,
                 api_key="", base_url="", max_tokens=None,
                 claude_host="", claude_path="claude", effort="max"):
    """Build one backend by name, delegating provider construction to the `backend`
    seam registry (`sluice/backends/`).

    A thin compatibility shim, deliberately: this is the tested, config-driven by-name
    factory every caller (and `Sluice.backend`'s role helpers) already uses. It keeps
    two responsibilities here, above the registry, so behaviour is unchanged:

    - the unknown-name guard raises `BackendError` listing the valid names (never a
      silent default, and never the bare `UnknownAdapter`/`KeyError` the registry would
      raise -- callers assert `BackendError`), and
    - `model` defaults to `DEFAULT_MODELS[name]` when omitted, so the default-model map
      stays the single place a provider's default model lives.

    Everything provider-specific -- which class, which default endpoint, whether a key is
    required -- now lives in the provider's module under `sluice/backends/`, reached via
    `plugins.get("backend", name)`. The caller (which knows `name`) still resolves and
    passes the right api_key/base_url; each factory reads only what it needs.
    """
    from sluice.core import plugins
    import sluice.backends  # noqa: F401  -- import triggers factory self-registration

    if name not in DEFAULT_MODELS:
        raise BackendError(
            f"unknown backend '{name}' (expected {', '.join(DEFAULT_MODELS)})")
    model = model or DEFAULT_MODELS[name]
    try:
        factory = plugins.get("backend", name)
    except plugins.UnknownAdapter as e:
        # name is in DEFAULT_MODELS but its plugin module failed to import (autoload
        # swallows a broken plugin's ImportError, leaving the name unregistered). Surface
        # loudly as BackendError -- the fail-at-construction contract callers rely on --
        # rather than the KeyError-flavoured UnknownAdapter. The registry-completeness
        # test is what stops this reaching a user in the first place.
        raise BackendError(str(e)) from e
    return factory(model, api_key=api_key, base_url=base_url, http=http, runner=runner,
                   timeout=timeout, max_tokens=max_tokens, claude_host=claude_host,
                   claude_path=claude_path, effort=effort)
