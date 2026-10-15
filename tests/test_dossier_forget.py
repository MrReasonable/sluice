"""#402: `DossierCache.forget` -- one caller-driven eviction -- and `get_or_build` surviving an
entry evicted between its freshness check and its read (a `cv run` racing a triage run)."""
import os
from datetime import datetime

import pytest

from sluice.core.dossier import DossierCache

LEAD = {"url": "https://example.invalid/job/1", "company": "Example Ltd", "role": "Example Role"}
JD = "an example posting " * 20


def _cache(tmp_path, calls):
    def fetcher(lead):
        calls.append(lead["url"])
        return {"jd": {"markdown": JD}}
    return DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                        clock=lambda: datetime(2031, 3, 14))


def test_forget_removes_that_leads_entry_and_no_other(tmp_path):
    calls = []
    cache = _cache(tmp_path, calls)
    other = {**LEAD, "url": "https://example.invalid/job/2"}
    cache.get_or_build(LEAD)
    cache.get_or_build(other)
    assert os.path.exists(cache._path(LEAD)) and os.path.exists(cache._path(other))
    cache.forget(LEAD)
    assert not os.path.exists(cache._path(LEAD))
    assert os.path.exists(cache._path(other))
    cache.get_or_build(LEAD)           # the next read is a miss, so it fetches again
    assert calls == [LEAD["url"], other["url"], LEAD["url"]]


def test_forget_on_a_missing_entry_is_silent(tmp_path):
    _cache(tmp_path, []).forget(LEAD)  # no cache dir, no entry: already the goal state


def test_forget_lets_any_other_os_error_through(tmp_path, monkeypatch):
    cache = _cache(tmp_path, [])
    cache.get_or_build(LEAD)

    def refuse(path):
        raise PermissionError(path)
    monkeypatch.setattr("sluice.core.dossier.os.remove", refuse)
    with pytest.raises(PermissionError):
        cache.forget(LEAD)


def test_an_entry_evicted_after_the_freshness_check_is_a_miss(tmp_path, monkeypatch):
    calls = []
    cache = _cache(tmp_path, calls)
    cache.get_or_build(LEAD)
    real_fresh = cache._fresh

    def fresh_then_evicted(path):
        ok = real_fresh(path)
        os.remove(path)                # the race: another process forgets it right here
        return ok
    monkeypatch.setattr(cache, "_fresh", fresh_then_evicted)
    d = cache.get_or_build(LEAD)
    assert d["jd"]["markdown"] == JD
    assert calls == [LEAD["url"], LEAD["url"]]
