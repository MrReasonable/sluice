import pytest

from sluice.triage import resolve


class _RecordingCache:
    def __init__(self, dossier=None, raises=None):
        self.calls = 0
        self._dossier = dossier or {}
        self._raises = raises

    def get_or_build(self, fm):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._dossier


def _source(company_from_url=None, raises=None):
    class _Source:
        pass
    src = _Source()
    if raises is not None:
        def _boom(url):
            raise raises
        src.company_from_url = _boom
    elif company_from_url is not None:
        src.company_from_url = company_from_url
    return src


def _get_source(mapping):
    def _get(sid):
        if sid not in mapping:
            raise KeyError(sid)
        return mapping[sid]
    return _get


FM = {"url": "https://example.invalid/jobs/1", "source": "example-board"}


def test_tier1_hit_never_calls_the_dossier_cache():
    src = _source(company_from_url=lambda url: "Example Co")
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got == "Example Co"
    assert cache.calls == 0


def test_tier1_miss_falls_through_to_tier2():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None
    assert cache.calls == 1


def test_both_tiers_miss_returns_none():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_get_source_none_skips_tier1_unconditionally():
    cache = _RecordingCache(dossier={
        "structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got == "Example Co"       # tier 2 still runs; only tier 1 is unconditionally skipped
    assert cache.calls == 1


def test_no_llm_never_calls_the_dossier_cache_even_on_a_tier1_miss():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=True, company_resolve_fetch=True)
    assert got is None
    assert cache.calls == 0


def test_company_resolve_fetch_false_never_calls_the_dossier_cache():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=False)
    assert got is None
    assert cache.calls == 0


def test_unknown_source_id_abstains_rather_than_raising():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({}), cache, no_llm=False,
                                  company_resolve_fetch=True)
    assert got is None


def test_dossier_fetch_exception_abstains_rather_than_propagating():
    cache = _RecordingCache(raises=RuntimeError("boom"))
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_extractor_exception_abstains_rather_than_propagating():
    src = _source(raises=RuntimeError("boom"))
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None
    assert cache.calls == 1    # tier 1's crash must not stop tier 2 from being attempted


@pytest.mark.parametrize("unsafe", ['Example "Co"', "Example\nCo", "Example\rCo"])
def test_tier1_candidate_with_a_structural_character_is_rejected(unsafe):
    src = _source(company_from_url=lambda url: unsafe)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None


@pytest.mark.parametrize("unsafe", ['Example "Co"', "Example\nCo", "Example\rCo"])
def test_tier2_candidate_with_a_structural_character_is_rejected(unsafe):
    cache = _RecordingCache(dossier={"page_title": f"Staff Engineer at {unsafe} | Board",
                                     "structured_data": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_from_dossier_reads_jobposting_jsonld():
    d = {"structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_reads_a_title_pattern_when_structured_data_is_absent():
    d = {"structured_data": "", "page_title": "Staff Engineer at Example Co | Example Board"}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_prefers_jsonld_when_both_present_and_disagree():
    d = {"structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "JSON-LD Co"}}',
        "page_title": "Staff Engineer at Title Co | Example Board"}
    assert resolve._from_dossier(d) == "JSON-LD Co"


@pytest.mark.parametrize("title", [
    "We are hiring at Example Co",       # "at" present, not the "role at Company | Board" shape
    "Example Co hiring engineers now",   # "hiring" present, not "is hiring a/an ..." shape
])
def test_from_dossier_title_pattern_near_miss_abstains(title):
    d = {"structured_data": "", "page_title": title}
    assert resolve._from_dossier(d) is None


def test_from_dossier_both_absent_returns_none():
    assert resolve._from_dossier({"structured_data": "", "page_title": ""}) is None


def test_from_dossier_malformed_jsonld_returns_none_not_raises():
    d = {"structured_data": "{not valid json", "page_title": ""}
    assert resolve._from_dossier(d) is None
