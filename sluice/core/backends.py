"""LLM judge backends, flat-rate first and self-healing.

ClaudeMaxBackend shells `claude --print` on a configured host (flat-rate, the
primary): set `host` to ssh there, or leave it empty to run `claude_path`
locally. AnthropicBackend calls the Anthropic Messages API directly, and
OpenAiCompatibleBackend calls an OpenAI-compatible chat/completions endpoint
(per-token, the fallback). FallbackBackend tries the primary and, if it errors
(primary host down, timeout, nonzero exit), falls back automatically so a run
is never blocked. `make_backend` builds any backend by name so selection can
be config-driven. The subprocess runner and HTTP poster are injected, so
everything is tested offline.
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
        return proc.stdout.strip()


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
    """Build one backend by name so selection can be config-driven. The caller
    (which knows `name`) resolves and passes the right api_key/base_url; each
    branch reads only what it needs.

    `model` may be omitted, in which case it defaults to DEFAULT_MODELS[name] --
    so a config that names a backend but no model is still buildable, and the
    map is the single place a provider's default model lives.

    `api_key` is *required* for the per-token backends and validated here, at
    construction. Deferring it to call time turns a plain misconfiguration into
    an opaque 401 halfway through a run -- and for the fallback specifically,
    that surfaces only when the primary is already down. claude-max needs no key
    (it shells the flat-rate CLI).

    `max_tokens` defaults to None so OpenAI-compatible backends (deepseek/openai)
    stay uncapped, matching direct construction -- config-driven selection must
    not silently cap a fallback. The Anthropic API requires max_tokens, so the
    anthropic branch falls through to AnthropicBackend's own default when unset."""
    if name not in DEFAULT_MODELS:
        raise BackendError(
            f"unknown backend '{name}' (expected {', '.join(DEFAULT_MODELS)})")
    model = model or DEFAULT_MODELS[name]

    if name == "claude-max":
        return ClaudeMaxBackend(model, host=claude_host, claude_path=claude_path,
                                effort=effort, runner=runner, timeout=timeout)

    # Every remaining backend is per-token and authenticates with a key.
    if not api_key:
        raise BackendError(
            f"backend '{name}' requires an api_key (set the provider's API key env var)")

    if name == "anthropic":
        extra = {} if max_tokens is None else {"max_tokens": max_tokens}
        return AnthropicBackend(model, api_key=api_key,
                                base_url=base_url or DEFAULT_BASE_URLS["anthropic"],
                                http=http, timeout=timeout, **extra)
    if name == "deepseek":
        return OpenAiCompatibleBackend(model, api_key=api_key,
                                       base_url=base_url or DEFAULT_BASE_URLS["deepseek"],
                                       http=http, timeout=timeout, max_tokens=max_tokens)
    # openai -- the only name left, guaranteed by the DEFAULT_MODELS check above.
    return OpenAiCompatibleBackend(model, api_key=api_key,
                                   base_url=base_url or DEFAULT_BASE_URLS["openai"],
                                   http=http, timeout=timeout, max_tokens=max_tokens)
