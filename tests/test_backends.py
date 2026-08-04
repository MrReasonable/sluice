import subprocess
import traceback
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


def test_anthropic_truncation_raises():
    def http(url, data, headers, timeout):
        return '{"stop_reason":"max_tokens","content":[{"type":"text","text":"cut"}]}'
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


def test_redact_has_no_claude_exemption():
    # _redact redacts EVERY non-empty token it is handed, including 'claude' -- the
    # default-binary exemption is role-based and lives in _scrub, not here (see the
    # _scrub tests). This pins that the exemption did not sneak back into _redact.
    assert _redact("claude: error", {"claude": "<host>"}) == "<host>: error"


def test_redact_keeps_empty_host():
    # A local run leaves host empty -> nothing to strip.
    assert _redact("some diagnostic", {"": "<host>"}) == "some diagnostic"


def test_redact_short_host_scrubbed_as_whole_token():
    # A genuinely short host (2 chars) MUST still be scrubbed -- the token-aware match
    # replaces it where it stands alone, so no length floor is needed to leave it exposed.
    assert _redact("ssh: connect to host db port 22", {"db": "<host>"}) \
        == "ssh: connect to host <host> port 22"


def test_redact_short_host_not_matched_inside_a_word():
    # ...but token-awareness means the same short host does NOT mangle a longer word that
    # merely contains it: `db` inside `db2`/`database` is left intact (a plain substring
    # replace -- the pre-token-aware form -- would have corrupted both).
    assert _redact("db2 and database down", {"db": "<host>"}) == "db2 and database down"


def test_scrub_omits_default_claude_path():
    # Role-based exemption: _scrub does NOT redact the default claude_path 'claude' (the CLI's
    # own binary name, non-sensitive) -- it omits it from the secret map, so a legit 'claude'
    # token in a diagnostic survives, while the configured host is still scrubbed.
    be = ClaudeMaxBackend("m", host="host.example.invalid", claude_path="claude")
    assert be._scrub("claude: error reaching host.example.invalid") \
        == "claude: error reaching <host>"


def test_scrub_redacts_host_named_claude():
    # ...but the exemption is for the default PATH role only. A host literally NAMED 'claude'
    # is a configured, sensitive value, so _scrub DOES redact it (over-redacting a coincidental
    # 'claude' token is the safe choice). This closes the former value-based residual.
    be = ClaudeMaxBackend("m", host="claude", claude_path="claude")
    assert be._scrub("cannot reach claude now") == "cannot reach <host> now"


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


# RFC-reserved synthetic fixtures -- non-real host/path, chosen so no real value lands here.
_SYNTH_HOST = "host.example.invalid"            # RFC 6761/2606 reserved: can never resolve
_SYNTH_PATH = "/home/example/.local/bin/claude"  # 'example' is the placeholder user


def _claude(runner, *, host=_SYNTH_HOST, claude_path=_SYNTH_PATH):
    # host=/claude_path= (NOT an explicit cmd_template) so the auto-built argv carries
    # them AND self.host/self.claude_path match -- the way make_backend couples them in
    # production. That coupling is what the neutrality guarantee is scoped to.
    return ClaudeMaxBackend("m", host=host, claude_path=claude_path, runner=runner)


def test_claudemax_timeout_scrubs_host_and_path_from_message():
    # A hung remote host times out (TimeoutExpired.cmd = the argv), routing to the
    # invocation-failed branch -- the leak route a :96/:113-only fix would miss.
    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 1)  # cmd is self.cmd_template
    with pytest.raises(BackendError) as ei:
        _claude(boom).complete("x")
    msg = str(ei.value)
    assert _SYNTH_HOST not in msg and _SYNTH_PATH not in msg
    assert "<host>" in msg and "<path>" in msg


def test_claudemax_timeout_chain_carries_no_secret():
    # rev-001: str(BackendError) is scrubbed, but `from e` would leave the raw TimeoutExpired
    # CHAINED -- and track/classify.py logs a failed complete() with _log.exception, which
    # renders the whole chain, re-leaking the argv the message just scrubbed. `from None` must
    # sever the chain so no traceback-logging sink surfaces the host/path. This asserts on the
    # FULL formatted chain (what _log.exception emits), not just str(err).
    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(BackendError) as ei:
        _claude(boom).complete("x")
    err = ei.value
    chain = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    assert _SYNTH_HOST not in chain and _SYNTH_PATH not in chain
    # ...and pin the mechanism directly: `from None` clears __cause__ and suppresses the
    # implicit context, which is what keeps a traceback-rendering sink from re-leaking it.
    assert err.__cause__ is None and err.__suppress_context__ is True


def test_claudemax_nonzero_exit_scrubs_host_keeps_diagnostic():
    class R:
        returncode, stdout = 1, ""
        stderr = f"ssh: Could not resolve hostname {_SYNTH_HOST}: nodename nor servname provided"
    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R()).complete("x")
    msg = str(ei.value)
    assert _SYNTH_HOST not in msg
    assert "<host>" in msg
    assert "Could not resolve hostname" in msg  # the diagnostic survives the scrub


def test_claudemax_empty_response_regains_scrubbed_diagnostic():
    class R:
        returncode, stdout = 0, "   \n"  # whitespace-only stdout -> empty after strip
        stderr = f"warning: quota low on {_SYNTH_HOST}"
    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R()).complete("x")
    msg = str(ei.value)
    assert "no text" in msg                       # existing contract preserved
    assert _SYNTH_HOST not in msg
    assert "warning: quota low on <host>" in msg  # diagnostic regained, scrubbed


def test_claudemax_missing_binary_scrubs_path():
    class R:
        returncode, stdout = 127, ""
        stderr = f"bash: {_SYNTH_PATH}: No such file or directory"
    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R()).complete("x")
    msg = str(ei.value)
    assert _SYNTH_PATH not in msg
    assert "<path>" in msg


def test_claudemax_redacts_before_truncating():
    # The [:200] lives at the call site, NOT in _scrub, so this MUST drive complete().
    # The host STRADDLES index 200 of proc.stderr (starts at 180): redact-then-slice
    # removes it whole; slice-then-redact cuts it mid-token, leaving a fragment
    # str.replace(full_host) can never match. Assert a distinctive >=13-char PREFIX
    # (not the full substring) so the surviving fragment reddens the swapped order.
    straddle_host = "HOST-SENTINEL-EXAMPLE.invalid"  # ~29 chars, distinctive, synthetic

    class R:
        returncode, stdout = 1, ""
        stderr = "." * 180 + straddle_host  # host occupies indices 180-208, straddles 200

    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R(), host=straddle_host).complete("x")
    assert "HOST-SENTINEL" not in str(ei.value)


def test_claudemax_empty_response_redacts_before_truncating():
    # tst-001: symmetric to the test above but for the EMPTY-RESPONSE branch (exit 0, no
    # stdout), whose scrubbed-stderr append uses the same redact-then-truncate ordering
    # (self._scrub(proc.stderr).strip()[:200]). Without this the branch's [:200] is
    # unwitnessed -- a slice-then-redact swap on it alone stayed green. Host straddles 200.
    straddle_host = "HOST-SENTINEL-EXAMPLE.invalid"

    class R:
        returncode, stdout = 0, "   \n"  # whitespace-only -> reaches the empty-response branch
        stderr = "." * 180 + straddle_host

    with pytest.raises(BackendError) as ei:
        _claude(lambda *a, **k: R(), host=straddle_host).complete("x")
    msg = str(ei.value)
    assert "no text" in msg              # still the empty-response branch
    assert "HOST-SENTINEL" not in msg


# ── #28: the default argv, the timeout, and an exit that says nothing ────────────
#
# All three come from issue #28. The composition bug it leads with did NOT reproduce
# (six arms through the real build_prompt/render_bundle/validate returned gate-passing
# CVs), but these three are code facts independent of how the agent behaves.


def test_claudemax_default_argv_is_pinned_exactly():
    """`--permission-mode bypassPermissions` grants the agent unrestricted tools, and
    NOTHING pinned it: deleting the pair outright left all 2078 tests green (witnessed).
    Every pre-existing argv assertion reads `--effort` and stops there.

    Equality, not membership. The flags are positional pairs built from literals with no
    legitimate variance, so a reorder is as much a change as a deletion -- and a
    membership check would pass a template that had grown a second, contradictory
    `--permission-mode`. Updating this literal is the deliberate edit; that is the point.
    """
    assert ClaudeMaxBackend("m").cmd_template == [
        "claude", "--print", "--model", "m", "--effort", "max",
        "--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash",
        "--permission-mode", "bypassPermissions",
    ]


def test_claudemax_ssh_argv_is_pinned_exactly():
    """The ssh form must keep the same flags after the host, in the same order.

    `["ssh", host] + base` is one concatenation, so a base-only assertion cannot see a
    regression that reorders around the host -- and this is the form that runs when
    `cv.compose_host` is set, i.e. the one a user is most likely to have in production.
    """
    be = ClaudeMaxBackend("m", host="host.invalid", claude_path="/opt/bin/claude")
    assert be.cmd_template == [
        "ssh", "host.invalid", "/opt/bin/claude", "--print", "--model", "m",
        "--effort", "max",
        "--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash",
        "--permission-mode", "bypassPermissions",
    ]


def test_claude_max_factory_coalesces_an_explicit_none_timeout():
    """`timeout=None` reaches `subprocess.run(timeout=None)`, which waits FOREVER.

    The factory omits the argument when None so the class default applies. Written first
    as a factory-local `_DEFAULT_TIMEOUT` constant, which was INERT: `make_backend`
    coalesces None before any factory runs, so rebinding it changed nothing on the seam
    path. This asserts the DIRECT path, which is the only one where the factory's
    handling of None is observable at all.
    """
    from sluice.backends.claude_max import _make
    be = _make("m", timeout=None)
    assert isinstance(be.timeout, (int, float)) and be.timeout > 0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
def test_http_factories_also_omit_a_none_timeout(provider):
    """The three siblings, on the DIRECT path make_backend never sees.

    They had no such handling while a comment claimed they did: `_make(timeout=None)`
    returned a backend with `timeout=None`, which reaches `urlopen(timeout=None)` and
    blocks on the socket default. `tests/test_backend_registry.py` resolves factories
    through `plugins.get`, so this path is exercised by the seam's own guard suite.
    """
    from sluice.core import plugins
    import sluice.backends  # noqa: F401  -- triggers self-registration
    be = plugins.get("backend", provider)("m", api_key="k", timeout=None)
    assert isinstance(be.timeout, (int, float)) and be.timeout > 0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
def test_make_backend_coalesces_an_explicit_none_timeout(provider):
    """The seam-level coalesce, witnessed on the providers that have no coalesce of
    their own -- which is the only way to see it at all.

    Written first against `claude-max` and it was INERT: that factory coalesces too, so
    the assertion passed with the seam-level line deleted. Mutation caught it (the mutant
    was killed by nothing), and the fix is a case that reaches PAST the first line of
    defence. These three end at `urlopen(timeout=None)`, which blocks on the socket
    default -- the same wait-forever as `subprocess.run(timeout=None)` by another route,
    and the reason this coalesce is not claude-max's business alone.
    """
    be = make_backend(provider, "m", api_key="k", timeout=None)
    assert isinstance(be.timeout, (int, float)) and be.timeout > 0


def test_claude_max_backend_from_the_seam_also_coalesces():
    """The claude-max path through the seam, kept as its own case rather than folded into
    the parametrize above: it passes for a DIFFERENT reason (its factory coalesces), so
    merging them would hide which line of defence each case actually exercises.
    """
    assert make_backend("claude-max", "m", timeout=None).timeout > 0


def test_claudemax_nonzero_exit_with_empty_stderr_still_says_something():
    """Observed in production: `BackendError: claude-max exit 1:` -- and nothing after
    the colon, because the CLI exited non-zero writing nothing to stderr.

    The operator learns only that something failed. Assert on the DISCRIMINATING content
    (that the message carries guidance beyond the bare prefix), not merely that a
    BackendError was raised: `test_claudemax_runner_nonzero_raises` above already covers
    the type, so a type-only assertion here would be witnessed by a test that predates
    this one and would pass with the guidance deleted.
    """
    class R:
        returncode, stdout, stderr = 1, "", "   "   # whitespace-only: same harm as empty
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=lambda *a, **k: R())
    with pytest.raises(BackendError) as ei:
        be.complete("x")
    msg = str(ei.value)
    assert "exit 1" in msg
    assert not msg.rstrip().endswith(":"), "message trails off after the colon"
    assert "no stderr" in msg.lower(), "does not say the CLI reported nothing"


def test_claudemax_nonzero_exit_still_surfaces_real_stderr():
    """The empty-stderr guidance must not displace a real diagnostic when there is one."""
    class R:
        returncode, stdout, stderr = 2, "", "quota exceeded for this account"
    be = ClaudeMaxBackend("m", cmd_template=["claude"], runner=lambda *a, **k: R())
    with pytest.raises(BackendError) as ei:
        be.complete("x")
    assert "quota exceeded for this account" in str(ei.value)


def test_the_seam_coalesces_for_a_factory_that_does_not(monkeypatch):
    """The seam-level coalesce's real job, and the only case that can witness it.

    Every shipped factory now omits `timeout` when None, so deleting the seam line leaves
    the whole suite green -- measured. That does not make it dead code: the backend seam
    is a REGISTRY, and a provider added later whose factory forwards the argument blindly
    would land at `subprocess.run(timeout=None)` / `urlopen(timeout=None)` again. This
    stands in for that provider, so the seam's guarantee is asserted independently of
    whether any current factory happens to also handle it.
    """
    from sluice.core.backends import DEFAULT_TIMEOUT

    seen = {}

    def naive_factory(model, **kw):     # forwards blindly: no omit-when-None idiom
        seen["timeout"] = kw["timeout"]
        return object()

    monkeypatch.setattr("sluice.core.plugins.get", lambda seam, name: naive_factory)
    make_backend("claude-max", "m", timeout=None)
    assert seen["timeout"] == DEFAULT_TIMEOUT, (
        "make_backend handed None straight to a factory that forwards it blindly")


# ── argument injection via config-controlled argv values (CWE-88) ────────────────


@pytest.mark.parametrize("host", [
    "-oProxyCommand=id",        # the live one: ssh runs it LOCALLY, before connecting
    "-obatchmode=yes",
    "--",
    "-",
], ids=["proxycommand", "option", "ddash", "dash"])
def test_a_host_that_ssh_would_read_as_an_option_is_refused(host):
    """`["ssh", host]` puts a config value where ssh expects a DESTINATION, and ssh reads
    any `-`-prefixed argument as an OPTION instead. `-oProxyCommand=<cmd>` then executes
    <cmd> on THIS machine before any connection is attempted.

    That is the semantic escape: the field means "a machine to reach" and the value means
    "a command to run", and nothing in between says so. It also sits UNDER the
    `--disallowedTools` deny-list rather than behind it -- ssh runs before claude exists,
    so a claude-level control cannot constrain it.
    """
    with pytest.raises(BackendError) as ei:
        ClaudeMaxBackend("m", host=host)
    assert "host" in str(ei.value)


@pytest.mark.parametrize("path", ["-c", "-cimport os", "--version"])
def test_a_claude_path_that_would_be_read_as_an_option_is_refused(path):
    """Same escape one position along. Locally it lands at argv[0]; over ssh it becomes
    the remote command, where a leading `-` is read as an option by whatever runs it."""
    with pytest.raises(BackendError) as ei:
        ClaudeMaxBackend("m", claude_path=path)
    assert "claude_path" in str(ei.value)


def test_ordinary_hosts_and_paths_still_construct():
    """The guard must not reject the values people actually configure -- including a
    hyphen INSIDE the value, which is ordinary in hostnames."""
    be = ClaudeMaxBackend("m", host="build-box-01.invalid", claude_path="/opt/bin/claude")
    assert be.cmd_template[0:3] == ["ssh", "build-box-01.invalid", "/opt/bin/claude"]
    assert ClaudeMaxBackend("m").cmd_template[0] == "claude"


def test_the_refusal_is_a_BackendError_so_doctor_reports_dead_rather_than_crashing():
    """Type matters here, not just that it raises.

    `Sluice.doctor` wraps construction in `except BackendError` and turns it into a `dead`
    check. A ValueError would escape that handler and take down the whole command -- and
    `doctor` is precisely what someone runs to find out that their host is misconfigured.
    Asserting the type is what pins this; a bare `pytest.raises(Exception)` would not.
    """
    # No `issubclass(BackendError, Exception)` assertion: that is trivially true of every
    # exception class and cannot fail. The `raises` below is what carries the claim --
    # swapping the raise to ValueError reds this node, because ValueError is not caught by
    # `pytest.raises(BackendError)` any more than it is by doctor's handler.
    with pytest.raises(BackendError):
        ClaudeMaxBackend("m", host="-oProxyCommand=id")


@pytest.mark.parametrize("kw", [{"host": "-oProxyCommand=id"}, {"claude_path": "-c"}],
                         ids=["host", "claude_path"])
def test_the_guard_is_not_bypassed_by_an_explicit_cmd_template(kw):
    """The comment claims the check is UNCONDITIONAL. Nothing falsified that.

    Every other case here omits `cmd_template`, so moving the guard into the branch that
    builds the argv left the whole suite green -- a surviving mutant, and the claim was
    prose with no row behind it. An explicit template makes these fields unused for the
    argv, but the contract is that they are never option-like, and a contract with a
    reachable bypass is not one.
    """
    with pytest.raises(BackendError):
        ClaudeMaxBackend("m", cmd_template=["echo"], **kw)
