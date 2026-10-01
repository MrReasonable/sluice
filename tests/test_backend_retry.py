"""#333: a backend is retried on ITSELF, never swapped for another provider.

`RetryingBackend` replaced `FallbackBackend`. These rows pin the three things a retry
wrapper gets wrong quietly: retrying an error that can only fail again, sleeping more
than the bound says, and losing the spend of an attempt that billed and then failed.
"""
import urllib.error

import pytest

from sluice.core.backends import (BackendError, Completion, RetryingBackend, Usage,
                                  make_backend)


class Scripted:
    """Raises or returns the scripted outcomes in order, counting calls."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def complete(self, prompt):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, BaseException):
            raise o
        return o


def _u(n):
    return Usage(provider="p", model="m", input_tokens=n, output_tokens=n)


def _wrap(inner, retries=2, slept=None):
    sleep = slept.append if slept is not None else (lambda s: None)
    return RetryingBackend(inner, retries=retries, label="p m", sleep=sleep)


def test_transient_failure_is_retried_on_the_same_backend_then_succeeds():
    slept = []
    inner = Scripted(BackendError("timeout"), Completion("ok"))
    out = _wrap(inner, slept=slept).complete("x")
    assert out.text == "ok" and inner.calls == 2 and slept == [2.0]


def test_backoff_is_exponential_and_bounded_by_retries():
    slept = []
    inner = Scripted(*[BackendError("down")] * 3)
    with pytest.raises(BackendError) as ei:
        _wrap(inner, slept=slept).complete("x")
    assert inner.calls == 3 and slept == [2.0, 4.0]
    # The final message names WHICH backend and that it was retried -- a bare "down"
    # reaching a digest would not say whether retries happened at all.
    assert "p m" in str(ei.value) and "3 attempts" in str(ei.value)


def test_non_transient_error_is_not_retried():
    slept = []
    inner = Scripted(BackendError("HTTP 401", transient=False))
    with pytest.raises(BackendError) as ei:
        _wrap(inner, slept=slept).complete("x")
    assert inner.calls == 1 and slept == [] and ei.value.transient is False


def test_zero_retries_means_one_attempt():
    inner = Scripted(BackendError("down"))
    with pytest.raises(BackendError):
        _wrap(inner, retries=0).complete("x")
    assert inner.calls == 1


def test_spend_of_failed_attempts_rides_on_the_success_as_unserved():
    inner = Scripted(BackendError("trunc", usage=_u(5)), Completion("ok", usage=_u(7)))
    out = _wrap(inner).complete("x")
    assert out.usage == _u(7) and out.unserved_usage == (_u(5),)


def test_spend_of_every_failed_attempt_rides_on_the_final_error():
    inner = Scripted(BackendError("a", usage=_u(1)), BackendError("b"),
                     BackendError("c", usage=_u(3), unserved_usage=(_u(4),)))
    with pytest.raises(BackendError) as ei:
        _wrap(inner).complete("x")
    spent = ((ei.value.usage,) if ei.value.usage else ()) + ei.value.unserved_usage
    assert sorted(u.input_tokens for u in spent) == [1, 3, 4]


def test_a_non_backend_error_propagates_untouched_and_unretried():
    inner = Scripted(TypeError("our bug"))
    with pytest.raises(TypeError):
        _wrap(inner).complete("x")
    assert inner.calls == 1


# ── raise-site classification, through the REAL provider code paths ──────────

@pytest.mark.parametrize("code,transient", [(400, False), (401, False), (403, False),
                                            (404, False), (408, True), (429, True),
                                            (500, True), (503, True)])
def test_http_status_classifies_transience(code, transient, monkeypatch):
    # Patched BELOW `_urlopen`, never by injecting `http=`: the classification lives in
    # `_urlopen`'s HTTPError arm, and a stubbed `http` raising HTTPError would reach the
    # provider's generic `except Exception` instead and certify nothing about it.
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "nope", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    b = make_backend("openai", api_key="k")
    with pytest.raises(BackendError) as ei:
        b.complete("x")
    assert ei.value.transient is transient


@pytest.mark.parametrize("name", ["openai", "deepseek", "anthropic"])
def test_missing_api_key_is_not_transient(name):
    with pytest.raises(BackendError) as ei:
        make_backend(name, api_key="")
    assert ei.value.transient is False


def test_unknown_backend_name_is_not_transient():
    with pytest.raises(BackendError) as ei:
        make_backend("nope")
    assert ei.value.transient is False


@pytest.mark.parametrize("name,body", [
    ("openai", '{"choices":[{"message":{"content":"  "},"finish_reason":"stop"}]}'),
    ("anthropic", '{"content":[],"stop_reason":"end_turn"}'),
])
def test_an_http_reply_with_no_text_is_not_transient(name, body):
    # A refusal of THIS prompt: re-sending it gets the same refusal, and a transient label
    # would let one message's refusal wedge track as an "outage" every run.
    b = make_backend(name, api_key="k", http=lambda url, data, headers, timeout: body)
    with pytest.raises(BackendError, match="no text") as ei:
        b.complete("x")
    assert ei.value.transient is False


def test_a_claude_max_empty_reply_stays_transient():
    # Unlike the HTTP providers', an exit-0 empty reply from the CLI has been observed under
    # contention and cleared on its own -- the case a same-backend retry exists for.
    class _P:
        returncode, stdout, stderr = 0, "  ", ""
    b = make_backend("claude-max", runner=lambda *a, **k: _P())
    with pytest.raises(BackendError, match="no text") as ei:
        b.complete("x")
    assert ei.value.transient is True


@pytest.mark.parametrize("base_url", ["//user:SECRET@api.example.invalid/v1",
                                      "api.example.invalid/v1?key=SECRET"])
def test_an_unusable_base_url_is_a_config_error_that_names_no_secret(base_url):
    """urllib's own refusal of a schemeless url quotes the WHOLE url, userinfo and query
    included -- and since #333 a backend error reaches stderr, the push digest's neighbour
    lines and the MCP `error` field. It is a configuration fault, so retrying it is futile."""
    b = make_backend("openai", api_key="k", base_url=base_url)
    with pytest.raises(BackendError) as ei:
        b.complete("x")
    assert "SECRET" not in str(ei.value)
    assert ei.value.transient is False
