import json

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


def test_jsonld_hiring_org_name_non_string_abstains_rather_than_raising():
    # structured_data is live, board-authored JSON-LD with no schema enforcement: a
    # hiringOrganization.name of a list (or dict/number/bool) makes the plain-string
    # `.strip()` in _hiring_org_from_jsonld raise AttributeError if uncaught.
    cache = _RecordingCache(dossier={
        "structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": ["Example Co"]}}',
        "page_title": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_dossier_page_title_non_string_abstains_rather_than_raising():
    # A hand-edited or pre-#109 cache entry can carry a non-string page_title, which
    # makes re.Pattern.match() raise TypeError if uncaught.
    cache = _RecordingCache(dossier={"structured_data": "", "page_title": 12345})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


# The last five are the class sluice's OWN frontmatter parser cannot see: `_fm_dict`/
# `_fm_value` split on "\n" specifically and match with a `(?m)` regex, so a VT/FF/FS/NUL
# or a NEL round-trips through this suite untouched. A REAL YAML parser -- what the note
# is actually read with once it reaches the candidate's editor -- does not: measured
# against PyYAML 6.0.3, the four control characters raise `ReaderError: unacceptable
# character`, and U+0085 NEL is SILENTLY folded to a space, which is the worse arm
# because nothing anywhere reports it. U+2028/U+2029 survived PyYAML here but are
# line/paragraph separators the YAML spec's own character productions exclude, so a
# different reader may split on them; `str.isprintable()` rejects the whole class in one
# check, which is the side to err on for a value scraped off an untrusted page.
_UNSAFE_COMPANIES = ['Example "Co"', "Example\nCo", "Example\rCo", "Example\\Co",
                     "Example\x0bCo", "Example\x0cCo", "Example\x1cCo", "Example\x00Co",
                     "Example\x85Co"]


@pytest.mark.parametrize("unsafe", _UNSAFE_COMPANIES)
def test_tier1_candidate_with_a_structural_character_is_rejected(unsafe):
    src = _source(company_from_url=lambda url: unsafe)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None


@pytest.mark.parametrize("unsafe", _UNSAFE_COMPANIES)
def test_tier2_candidate_with_a_structural_character_is_rejected(unsafe):
    cache = _RecordingCache(dossier={
        "page_title": "",
        "structured_data": json.dumps({"@type": "JobPosting",
                                       "hiringOrganization": {"name": unsafe}})})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


@pytest.mark.parametrize("blank", ["   ", " "])
def test_tier1_candidate_that_is_only_whitespace_is_rejected(blank):
    # Both values are what the SHIPPED wellfound extractor actually returns, measured:
    # `slug.replace("-", " ").title()` turns a `/company/---` path segment into "   "
    # and `/company/-` into " ", and its own trailing `or None` sees a non-empty string
    # in each case. Written through as `company: "   "` that is strictly worse than
    # abstaining -- classify.py's blank-company branch tests `.strip()`, so the lead
    # keeps landing on needs_review while require_blank now refuses to ever correct it,
    # and meanwhile the note shows a human a company that is not one.
    #
    # Both are PRINTABLE, so `str.isprintable()` does not catch them: this pins
    # `_safe`'s separate `.strip()` clause, not its printability clause.
    src = _source(company_from_url=lambda url: blank)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_from_dossier_reads_jobposting_jsonld():
    d = {"structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_finds_a_jobposting_that_is_not_the_first_block():
    # THE shape this feature was built for. A real board routinely emits a site-wide
    # Organization or BreadcrumbList schema in an ld+json tag BEFORE the page's own
    # JobPosting one, so `_LD_JSON_JS` collects every tag into an array rather than
    # taking document.querySelector's first match -- which on exactly those pages
    # captured the wrong block and made tier 2 abstain.
    d = {"page_title": "", "structured_data": json.dumps([
        {"@type": "BreadcrumbList", "itemListElement": []},
        {"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}},
    ])}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_finds_a_jobposting_inside_a_later_blocks_graph():
    # The two shapes compose: the array of blocks is one level, and any single block
    # may itself be a `@graph` container, putting the JobPosting TWO levels down. A
    # flat one-level walk over the array reads the @graph wrapper's own (absent)
    # @type and abstains.
    d = {"page_title": "", "structured_data": json.dumps([
        {"@type": "Organization", "name": "Example Board"},
        {"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage"},
            {"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}},
        ]},
    ])}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_skips_a_block_that_failed_to_parse_in_the_page():
    # `_LD_JSON_JS` maps an unparseable block to null rather than dropping the whole
    # capture, so a single malformed tag next to a good one must not cost the good one.
    d = {"page_title": "", "structured_data": json.dumps([
        None, {"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}])}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_reads_an_empty_capture_array_as_an_abstain():
    # A page with no ld+json at all now yields "[]" from the probe, not "".
    assert resolve._from_dossier({"page_title": "", "structured_data": "[]"}) is None


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
