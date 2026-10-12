"""DossierCache's supplied arm (#369): a JD from the lead note wins over the cache and the
fetcher, and is never written to the cache."""
import json
import os
from datetime import datetime

import pytest

from sluice.core.dossier import DossierCache

_LEAD = {"company": "Example Foundry", "role": "Analyst", "url": "https://example.invalid/1"}
_A = "Cached posting text A, long enough to pass the floor set below."
_B = "Supplied posting text B, from the lead note itself, also long enough."
_NOW = datetime(2026, 10, 11, 12, 0, 0)


class _CountingFetcher:
    def __init__(self, markdown=""):
        self.calls, self.markdown = 0, markdown

    def __call__(self, lead):
        self.calls += 1
        return {"jd": {"markdown": self.markdown}}


def _cache(tmp_path, fetcher, floor=10):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                        clock=lambda: _NOW, min_jd_chars=floor)


def _seed_fresh_entry(cache, markdown):
    os.makedirs(cache.dir, exist_ok=True)
    path = os.path.join(cache.dir, f"{cache.cache_key(_LEAD)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "jd": {"markdown": markdown},
                   "built_at": _NOW.isoformat()}, f)
    return path


def test_supplied_jd_beats_a_fresh_cache_entry_and_leaves_it_untouched(tmp_path):
    fetcher = _CountingFetcher()
    cache = _cache(tmp_path, fetcher)
    path = _seed_fresh_entry(cache, _A)
    # CONTROL (tst-102): the entry really is fresh -- without a supplied JD the cache
    # serves A and never fetches. Without this, the row below could pass on a stale entry.
    assert cache.get_or_build(_LEAD)["jd"]["markdown"] == _A
    assert fetcher.calls == 0
    before = open(path, "rb").read()

    d = cache.get_or_build(_LEAD, supplied_jd=_B)

    assert d["jd"]["markdown"] == _B
    assert fetcher.calls == 0
    assert open(path, "rb").read() == before


def test_supplied_jd_on_an_empty_cache_fetches_nothing_and_writes_nothing(tmp_path):
    fetcher = _CountingFetcher()
    cache = _cache(tmp_path, fetcher)
    d = cache.get_or_build(_LEAD, supplied_jd=_B)
    assert d["jd"]["markdown"] == _B
    assert fetcher.calls == 0
    assert not os.path.exists(cache.dir) or os.listdir(cache.dir) == []


@pytest.mark.parametrize("floor, arrived", [(1000, False), (10, True)])
def test_min_jd_chars_applies_to_a_supplied_jd(tmp_path, floor, arrived):
    cache = _cache(tmp_path, _CountingFetcher(), floor=floor)
    assert cache.jd_arrived(cache.get_or_build(_LEAD, supplied_jd=_B)) is arrived


def test_both_arms_build_the_same_key_set(tmp_path):
    fetched = _cache(tmp_path, _CountingFetcher(_A)).get_or_build(_LEAD)
    supplied = _cache(tmp_path / "other", _CountingFetcher()).get_or_build(
        _LEAD, supplied_jd=_B)
    assert set(supplied) == set(fetched)


def test_no_supplied_jd_is_todays_behaviour(tmp_path):
    fetcher = _CountingFetcher(_A)
    cache = _cache(tmp_path, fetcher)
    assert cache.get_or_build(_LEAD)["jd"]["markdown"] == _A
    assert fetcher.calls == 1
    assert len(os.listdir(cache.dir)) == 1
