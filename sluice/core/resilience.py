"""Resilience primitives: retry-with-backoff, hard timeout, and a 429 pre-check.

These wrap each source so one slow, flaky, or rate-limited site can neither stall
nor crash a whole scan. Sleep is injectable so tests run instantly.
"""
import signal
import time
import urllib.error
import urllib.request


def with_retry(fn, tries=3, base=0.5, on=(Exception,), *, sleep=time.sleep):
    """Call fn, retrying on `on` exceptions with exponential backoff
    (base * 2**i between attempts). Re-raises the last exception once `tries`
    is exhausted."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except on as e:
            last = e
            if i < tries - 1 and base:
                sleep(base * (2 ** i))
    raise last


class _Deadline(Exception):
    """Internal SIGALRM marker, translated to TimeoutError at the boundary."""


def run_with_timeout(fn, seconds):
    """Run fn under a SIGALRM deadline; raise TimeoutError if it overruns.
    SIGALRM only fires on the main thread - the engine calls sources there."""
    def _handler(signum, frame):
        raise _Deadline()

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    except _Deadline:
        raise TimeoutError(f"timed out after {seconds}s")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def head_rate_limited(url, timeout=10):
    """HEAD the url; return Retry-After seconds if the server answers 429, else
    None. Network errors are swallowed (treated as 'not rate limited') - the
    browser fetch that follows will surface any real failure."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return None
    except urllib.error.HTTPError as e:
        if e.code != 429:
            return None
        retry_after = e.headers.get("Retry-After") if e.headers else None
        try:
            return int(retry_after) if retry_after is not None else 0
        except (TypeError, ValueError):
            return 0  # a date-form Retry-After - treat as "rate limited, unknown"
    except Exception:
        return None
