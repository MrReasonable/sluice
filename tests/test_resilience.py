import time

import pytest

from sluice.core.resilience import head_rate_limited, run_with_timeout, with_retry


def test_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("x")
        return "ok"

    assert with_retry(flaky, tries=3, base=0, on=(ValueError,)) == "ok"
    assert calls["n"] == 3


def test_reraises_after_exhaustion():
    with pytest.raises(ValueError):
        with_retry(lambda: (_ for _ in ()).throw(ValueError()), tries=2, base=0, on=(ValueError,))


def test_backoff_uses_injected_sleep():
    slept = []

    def always_fail():
        raise ValueError()

    with pytest.raises(ValueError):
        with_retry(always_fail, tries=3, base=0.5, on=(ValueError,), sleep=slept.append)
    assert slept == [0.5, 1.0]  # base*2**0, base*2**1; no sleep after the last try


def test_does_not_retry_unlisted_exception():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise KeyError()

    with pytest.raises(KeyError):
        with_retry(boom, tries=3, base=0, on=(ValueError,))
    assert calls["n"] == 1


def test_run_with_timeout_returns_value():
    assert run_with_timeout(lambda: 7, seconds=1) == 7


def test_run_with_timeout_raises_on_overrun():
    with pytest.raises(TimeoutError):
        run_with_timeout(lambda: time.sleep(2), seconds=0.2)


def test_head_rate_limited_returns_retry_after(monkeypatch):
    import urllib.error

    def raise429(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "30"}, None)

    monkeypatch.setattr("urllib.request.urlopen", raise429)
    assert head_rate_limited("http://x") == 30


def test_head_rate_limited_none_on_ok(monkeypatch):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _R())
    assert head_rate_limited("http://x") is None
