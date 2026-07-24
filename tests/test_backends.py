import pytest
from sluice.core.backends import (
    BackendError, ClaudeMaxBackend, FallbackBackend, OpenAiCompatibleBackend,
    AnthropicBackend, make_backend, DEFAULT_MODELS, _redact,
)


class _Fake:
    def __init__(self, out=None, raise_=False):
        self.out, self.raise_, self.calls = out, raise_, 0
    def complete(self, prompt):
        self.calls += 1
        if self.raise_:
            raise BackendError("down")
        return self.out


def test_fallback_uses_primary_when_ok():
    p, f = _Fake(out="P"), _Fake(out="F")
    fb = FallbackBackend(p, f)
    assert fb.complete("x") == "P"
    assert (p.calls, f.calls, fb.last_backend) == (1, 0, "primary")


def test_fallback_switches_on_primary_error():
    p, f = _Fake(raise_=True), _Fake(out="F")
    fb = FallbackBackend(p, f)
    assert fb.complete("x") == "F"
    assert (p.calls, f.calls, fb.last_backend) == (1, 1, "fallback")


def test_fallback_propagates_when_both_fail():
    # The truncation guard (finish_reason==length / stop_reason==max_tokens now
    # raising BackendError instead of returning a partial) makes this double-
    # failure mode reachable in practice: primary down AND fallback truncated.
    # Both must be tried, and the error must propagate rather than be swallowed.
    p, f = _Fake(raise_=True), _Fake(raise_=True)
    fb = FallbackBackend(p, f)
    with pytest.raises(BackendError):
        fb.complete("x")
    assert (p.calls, f.calls) == (1, 1)


def test_claudemax_runner_nonzero_raises():
    class R:  # fake completed-process
        returncode, stdout, stderr = 1, "", "boom"
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=lambda *a, **k: R())
    with pytest.raises(BackendError):
        be.complete("x")


def test_claudemax_transport_failure_raises_backend_error():
    # The wrapper ONE LINE above the empty guard, and the same drift this file's other backends
    # do not have: both siblings pin it (test_openai_compatible_transport_error_raises) and
    # claude-max did not. It matters because FallbackBackend catches BackendError *only*, so a
    # timeout or an ssh failure escaping as a raw OSError would CRASH the run instead of
    # degrading to the fallback -- the exact opposite of what the module docstring promises.
    def boom(*a, **k):
        raise OSError("ssh: connect to host port 22: Connection refused")
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=boom)
    with pytest.raises(BackendError, match="invocation failed"):
        be.complete("x")


@pytest.mark.parametrize("stdout", ["", "   \n  "])
def test_claudemax_empty_stdout_on_exit_zero_raises(stdout):
    # exit 0 with no text is a FAILED call that looks like a successful one -- the shape both
    # siblings already refuse (test_openai_compatible_empty_content_raises,
    # test_anthropic_empty_content_raises). Without this, complete() returns "" and the caller
    # consumes it as a real completion.
    #
    # Whitespace-only is parametrised, not decorative: `.strip()` runs BEFORE the check, so a
    # guard written as `if not proc.stdout` -- or one that drops the .strip() -- passes the ""
    # case and lets "   \n  " straight through. The whitespace param is the load-bearing one and
    # kills a strict SUPERSET: "" uniquely witnesses nothing, because both params are byte-
    # identical ("") by the time the guard sees them. It stays for the obvious reason -- it is the
    # case a reader expects to see -- not because it earns its keep as a mutant.
    class R:
        returncode, stderr = 0, ""
    R.stdout = stdout
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=lambda *a, **k: R())
    with pytest.raises(BackendError, match="no text"):
        be.complete("x")


def test_openai_compatible_parses_choice():
    def http(url, data, headers, timeout):
        assert url == "http://x/api/v1/chat/completions"
        return '{"choices":[{"message":{"content":"HELLO"},"finish_reason":"stop"}]}'
    be = OpenAiCompatibleBackend("m", base_url="http://x/api/v1", api_key="k", http=http)
    assert be.complete("prompt") == "HELLO"


def test_openai_compatible_includes_max_tokens_when_set():
    seen = {}
    def http(url, data, headers, timeout):
        import json
        seen["body"] = json.loads(data)
        return '{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}'
    OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http,
                            max_tokens=1024).complete("p")
    assert seen["body"]["max_tokens"] == 1024


def test_openai_compatible_omits_max_tokens_when_unset():
    seen = {}
    def http(url, data, headers, timeout):
        import json
        seen["body"] = json.loads(data)
        return '{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}'
    OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http).complete("p")
    assert "max_tokens" not in seen["body"]


def test_openai_compatible_truncation_raises():
    def http(url, data, headers, timeout):
        return '{"choices":[{"message":{"content":"partial"},"finish_reason":"length"}]}'
    be = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http)
    with pytest.raises(BackendError):
        be.complete("prompt")


def test_openai_compatible_posts_model_prompt_and_auth():
    # The live per-token fallback: pin model, prompt, and Bearer auth so a
    # mutation sending the wrong model / dropping the prompt / mangling the
    # header can't sail through (its Anthropic sibling is already pinned).
    seen = {}
    def http(url, data, headers, timeout):
        import json
        seen["headers"], seen["body"] = headers, json.loads(data)
        return '{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}'
    OpenAiCompatibleBackend("m", base_url="http://x", api_key="k",
                            http=http).complete("prompt")
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["body"]["model"] == "m"
    assert seen["body"]["messages"] == [{"role": "user", "content": "prompt"}]


def test_openai_compatible_content_filter_raises():
    # A blocked/partial completion (finish_reason=content_filter) must fail
    # loudly, not return partial text as a complete answer.
    def http(url, data, headers, timeout):
        return '{"choices":[{"message":{"content":"partial"},"finish_reason":"content_filter"}]}'
    be = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http)
    with pytest.raises(BackendError):
        be.complete("prompt")


def test_openai_compatible_empty_content_raises():
    # Empty content on an otherwise-clean stop is not a valid CV/verdict; the
    # fallback must raise so run_batch records an error, matching AnthropicBackend.
    def http(url, data, headers, timeout):
        return '{"choices":[{"message":{"content":"   "},"finish_reason":"stop"}]}'
    be = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http)
    with pytest.raises(BackendError):
        be.complete("prompt")


def test_openai_compatible_transport_error_raises():
    # A transport/HTTP failure must surface as BackendError, never a raw OSError,
    # so FallbackBackend and run_batch can rely on the backend contract.
    def http(*a, **k):
        raise OSError("network down")
    be = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=http)
    with pytest.raises(BackendError):
        be.complete("prompt")


def test_claudemax_default_effort_is_max():
    be = ClaudeMaxBackend("m")
    ct = be.cmd_template
    assert ct[ct.index("--effort") + 1] == "max"


def test_claudemax_effort_override():
    be = ClaudeMaxBackend("m", effort="medium")
    ct = be.cmd_template
    assert ct[ct.index("--effort") + 1] == "medium"


def test_anthropic_posts_and_parses_text():
    seen = {}
    def http(url, data, headers, timeout):
        import json
        seen["url"], seen["headers"], seen["body"] = url, headers, json.loads(data)
        return '{"stop_reason":"end_turn","content":[{"type":"text","text":"HELLO"}]}'
    out = AnthropicBackend("claude-sonnet-4-5", api_key="sk-1",
                           base_url="https://api.anthropic.com", http=http,
                           max_tokens=1024).complete("prompt")
    assert out == "HELLO"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-1"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"] == {"model": "claude-sonnet-4-5", "max_tokens": 1024,
                            "messages": [{"role": "user", "content": "prompt"}]}


def test_anthropic_joins_multiple_text_blocks_with_newline():
    # A "\n".join -> "".join mutation survives unless two text blocks are
    # asserted to come back separated.
    def http(url, data, headers, timeout):
        return ('{"stop_reason":"end_turn","content":['
                '{"type":"text","text":"A"},'
                '{"type":"text","text":"B"}]}')
    assert AnthropicBackend("m", api_key="k", http=http).complete("x") == "A\nB"


def test_anthropic_skips_thinking_block_before_text():
    def http(url, data, headers, timeout):
        return ('{"stop_reason":"end_turn","content":['
                '{"type":"thinking","thinking":"hmm"},'
                '{"type":"text","text":"ANSWER"}]}')
    assert AnthropicBackend("m", api_key="k", http=http).complete("x") == "ANSWER"


def test_anthropic_empty_content_raises():
    def http(url, data, headers, timeout):
        return '{"stop_reason":"refusal","content":[]}'
    with pytest.raises(BackendError):
        AnthropicBackend("m", api_key="k", http=http).complete("x")


def test_anthropic_truncation_raises():
    def http(url, data, headers, timeout):
        return '{"stop_reason":"max_tokens","content":[{"type":"text","text":"cut"}]}'
    with pytest.raises(BackendError):
        AnthropicBackend("m", api_key="k", http=http).complete("x")


def test_anthropic_transport_error_raises():
    def http(*a, **k):
        raise OSError("network down")
    with pytest.raises(BackendError):
        AnthropicBackend("m", api_key="k", http=http).complete("x")


def test_make_backend_selects_class_and_default_base_url():
    assert isinstance(make_backend("claude-max", "m"), ClaudeMaxBackend)
    an = make_backend("anthropic", "m", api_key="k")
    assert isinstance(an, AnthropicBackend) and an.url == "https://api.anthropic.com/v1/messages"
    ds = make_backend("deepseek", "m", api_key="k")
    assert isinstance(ds, OpenAiCompatibleBackend) and ds.url == "https://api.deepseek.com/chat/completions"
    oa = make_backend("openai", "m", api_key="k")
    assert isinstance(oa, OpenAiCompatibleBackend) and oa.url == "https://api.openai.com/v1/chat/completions"


def test_make_backend_base_url_override_wins():
    be = make_backend("anthropic", "m", api_key="k", base_url="http://local:1234")
    assert be.url == "http://local:1234/v1/messages"


def test_make_backend_unknown_name_raises():
    with pytest.raises(BackendError):
        make_backend("bogus", "m")


def test_make_backend_claude_max_forwards_kwargs():
    # host/claude_path/effort/timeout must be forwarded, not hardcoded --
    # a mutation hardcoding any of these would sail through otherwise.
    be = make_backend("claude-max", "m", claude_host="h", claude_path="/p",
                      effort="low", timeout=99)
    assert be.host == "h"
    assert be.claude_path == "/p"
    assert be.timeout == 99
    ct = be.cmd_template
    assert ct[:2] == ["ssh", "h"]
    assert ct[ct.index("--effort") + 1] == "low"


def test_make_backend_anthropic_forwards_kwargs():
    be = make_backend("anthropic", "m", api_key="k", max_tokens=123, timeout=77)
    assert be.max_tokens == 123
    assert be.timeout == 77


def test_make_backend_deepseek_forwards_kwargs():
    be = make_backend("deepseek", "m", api_key="k", max_tokens=55, timeout=88)
    assert be.max_tokens == 55
    assert be.timeout == 88


def test_make_backend_default_max_tokens_uncapped_for_openai_capped_for_anthropic():
    # With no max_tokens, OpenAI-compatible backends stay uncapped (None ->
    # omitted from the request), matching direct construction so config-driven
    # selection never silently caps a fallback. Anthropic falls through to its
    # own required default (the API mandates max_tokens).
    assert make_backend("deepseek", "m", api_key="k").max_tokens is None
    assert make_backend("openai", "m", api_key="k").max_tokens is None
    assert make_backend("anthropic", "m", api_key="k").max_tokens == 8192


def test_default_models_cover_every_selector():
    assert DEFAULT_MODELS == {
        "claude-max": "claude-sonnet-4-5", "anthropic": "claude-sonnet-4-5",
        "deepseek": "deepseek-v4-flash", "openai": "gpt-4o"}


# DeepSeek retired the `deepseek-chat` / `deepseek-reasoner` aliases on
# 2026-07-24; they hard-fail after that. cheap_model is the per-token fallback
# that runs precisely when the flat-rate primary is down, so a stale alias here
# is a dead fallback at the worst moment. Pin all three sub-app defaults to the
# one map so they cannot drift apart or drift back.
def test_cheap_model_defaults_track_default_models_and_avoid_retired_aliases():
    from sluice.cv.config import CvConfig
    from sluice.track.config import TrackConfig
    from sluice.triage.config import TriageConfig

    retired = {"deepseek-chat", "deepseek-reasoner"}
    for cfg in (TriageConfig(), CvConfig(), TrackConfig()):
        assert cfg.cheap_model == DEFAULT_MODELS["deepseek"]
        assert cfg.cheap_model not in retired


# ── PR-B: make_backend hardening ─────────────────────────────────────────────

def test_make_backend_defaults_model_from_default_models():
    # A config may name a backend without naming a model; DEFAULT_MODELS is the
    # single place a provider's default lives, so it must be what fills the gap.
    assert make_backend("deepseek", api_key="k").model == DEFAULT_MODELS["deepseek"]
    assert make_backend("anthropic", api_key="k").model == DEFAULT_MODELS["anthropic"]
    assert make_backend("claude-max").model == DEFAULT_MODELS["claude-max"]


def test_make_backend_explicit_model_beats_the_default():
    assert make_backend("deepseek", "some-other-model", api_key="k").model == "some-other-model"


def test_make_backend_requires_api_key_for_per_token_backends():
    # Fail at construction, not as an opaque 401 halfway through a run -- and for
    # the fallback that would mean discovering it only once the primary is down.
    for name in ("deepseek", "anthropic", "openai"):
        with pytest.raises(BackendError, match="requires an api_key"):
            make_backend(name, "m", api_key="")


def test_make_backend_claude_max_needs_no_api_key():
    # The flat-rate CLI authenticates itself; demanding a key here would break it.
    assert make_backend("claude-max", "m").model == "m"


def test_make_backend_unknown_name_still_raises():
    with pytest.raises(BackendError, match="unknown backend"):
        make_backend("not-a-backend", "m", api_key="k")


# ── PR-B: FallbackBackend chains the primary error ───────────────────────────

def test_fallback_double_failure_reports_both_causes_and_chains_primary():
    primary = _Fake(raise_=True)
    fallback = _Fake(raise_=True)
    be = FallbackBackend(primary, fallback)
    with pytest.raises(BackendError) as ei:
        be.complete("p")
    # Both legs are named, and the primary's exception stays attached as the cause
    # -- the primary going down is what put us here, so losing it hides the story.
    assert "both backends failed" in str(ei.value)
    assert ei.value.__cause__ is not None
    assert primary.calls == 1 and fallback.calls == 1


# ── PR-B: _urlopen surfaces the provider's error body ────────────────────────

def test_urlopen_surfaces_http_error_body():
    import io
    import urllib.error
    import urllib.request
    from sluice.core import backends

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"message":"Model Not Exist"}}'))

    orig = urllib.request.urlopen
    urllib.request.urlopen = boom
    try:
        with pytest.raises(BackendError) as ei:
            backends._urlopen("https://api.example.com/v1/chat/completions",
                              b"{}", {}, 30)
    finally:
        urllib.request.urlopen = orig
    # Without reading the body this collapses to "HTTP Error 400: Bad Request",
    # which is exactly the message that hides a retired model id.
    assert "Model Not Exist" in str(ei.value)
    assert "400" in str(ei.value)


def test_redact_strips_host_to_label():
    # host.example.invalid is RFC-reserved (can never resolve) -- a non-real fixture.
    out = _redact("ssh: Could not resolve hostname host.example.invalid: nope",
                  {"host.example.invalid": "<host>"})
    assert out == "ssh: Could not resolve hostname <host>: nope"


def test_redact_strips_configured_path_to_label():
    # 'example' is the conventional placeholder user -- a non-real absolute path.
    out = _redact("bash: /home/example/.local/bin/claude: No such file",
                  {"/home/example/.local/bin/claude": "<path>"})
    assert out == "bash: <path>: No such file"


def test_redact_keeps_default_claude():
    # The default claude_path is exactly 'claude'; stripping it would corrupt the
    # very CLI diagnostics we are trying to preserve. Guarded by value != "claude".
    assert _redact("claude: error: usage", {"claude": "<path>"}) == "claude: error: usage"


def test_redact_keeps_empty_host():
    # A local run leaves host empty -> nothing to strip.
    assert _redact("some diagnostic", {"": "<host>"}) == "some diagnostic"


def test_redact_keeps_short_value():
    # A <3-char value is too generic; replacing it would mangle common substrings.
    assert _redact("a banana", {"an": "<host>"}) == "a banana"


def test_redact_overlap_scrubs_both_longest_first():
    # host is a substring of the path; the dict lists the SHORTER (host) key FIRST,
    # matching _scrub's own {self.host: ..., self.claude_path: ...} construction order.
    # Longest-first replacement catches the path whole before the host can fragment it.
    # Synthetic values (RFC-reserved).
    host = "h7.example.invalid"
    path = "/opt/h7.example.invalid/bin/claude"
    text = f"connect {host} failed; exec {path} missing"
    out = _redact(text, {host: "<host>", path: "<path>"})
    assert host not in out and path not in out
    assert out == "connect <host> failed; exec <path> missing"
