import json

import pytest

from sluice.core.leads import UNTRUSTED_SCRAPED_CONTENT_WARNING
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
    assert got.company == "Example Co"
    assert got.tier == "tier1"
    assert cache.calls == 0


def test_tier1_miss_falls_through_to_tier2():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got.company is None
    assert cache.calls == 1


def test_both_tiers_miss_returns_none():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got.company is None


def test_get_source_none_skips_tier1_unconditionally():
    cache = _RecordingCache(dossier={
        "structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got.company == "Example Co"       # tier 2 still runs; only tier 1 is unconditionally skipped
    assert got.tier == "tier2"
    assert cache.calls == 1


def test_no_llm_never_calls_the_dossier_cache_even_on_a_tier1_miss():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=True, company_resolve_fetch=True)
    assert got.company is None
    assert cache.calls == 0


def test_company_resolve_fetch_false_never_calls_the_dossier_cache():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=False)
    assert got.company is None
    assert cache.calls == 0


def test_unknown_source_id_abstains_rather_than_raising():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({}), cache, no_llm=False,
                                  company_resolve_fetch=True)
    assert got.company is None


def test_dossier_fetch_exception_abstains_rather_than_propagating():
    cache = _RecordingCache(raises=RuntimeError("boom"))
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got.company is None


def test_extractor_exception_abstains_rather_than_propagating():
    src = _source(raises=RuntimeError("boom"))
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got.company is None
    assert cache.calls == 1    # tier 1's crash must not stop tier 2 from being attempted


def test_jsonld_hiring_org_name_non_string_abstains_rather_than_raising():
    # structured_data is live, board-authored JSON-LD with no schema enforcement: a
    # hiringOrganization.name of a list (or dict/number/bool) makes the plain-string
    # `.strip()` in _hiring_org_from_jsonld raise AttributeError if uncaught.
    cache = _RecordingCache(dossier={
        "structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": ["Example Co"]}}',
        "page_title": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got.company is None


def test_dossier_page_title_non_string_abstains_rather_than_raising():
    # A hand-edited or pre-#109 cache entry can carry a non-string page_title, which
    # makes re.Pattern.match() raise TypeError if uncaught.
    cache = _RecordingCache(dossier={"structured_data": "", "page_title": 12345})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got.company is None


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
    assert got.company is None


@pytest.mark.parametrize("unsafe", _UNSAFE_COMPANIES)
def test_tier2_candidate_with_a_structural_character_is_rejected(unsafe):
    cache = _RecordingCache(dossier={
        "page_title": "",
        "structured_data": json.dumps({"@type": "JobPosting",
                                       "hiringOrganization": {"name": unsafe}})})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got.company is None


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
    # `frontmatter_safe`'s separate `.strip()` clause, not its printability clause.
    src = _source(company_from_url=lambda url: blank)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got.company is None


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
        # `@context` is carried only for shape realism -- `_iter_nodes` never reads
        # it (it looks at `@graph` and `@type`), so the value is a placeholder rather
        # than the real vocabulary URL, per this suite's no-real-URLs rule.
        {"@context": "https://schema.invalid", "@graph": [
            {"@type": "WebPage"},
            {"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}},
        ]},
    ])}
    assert resolve._from_dossier(d) == "Example Co"


def _wrap_in_lists(node, levels):
    """`node` inside `levels` nested arrays -- the shape a board really produces (an
    array of ld+json blocks, each of which may itself be an array), just repeated."""
    for _ in range(levels):
        node = [node]
    return node


_JOBPOSTING = {"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}


def test_a_jsonld_node_at_the_depth_cap_is_still_read():
    # The control for the abstain test below, and it is what makes that test mean
    # anything: a payload this walk was never going to read for some OTHER reason
    # (malformed, wrong @type) would abstain too, and prove nothing about the cap.
    # `_iter_nodes` is entered at depth 0 on the outermost value and increments once
    # per array, so a node wrapped in _MAX_DEPTH arrays sits exactly ON the boundary.
    payload = _wrap_in_lists(_JOBPOSTING, resolve._MAX_DEPTH)
    assert resolve._hiring_org_from_jsonld(json.dumps(payload)) == "Example Co"


def test_a_jsonld_node_one_level_past_the_depth_cap_is_not_read():
    # ONE level deeper than the test above, so the ONLY difference between them is the
    # cap. Derived from `resolve._MAX_DEPTH` rather than written as a literal: the cap
    # is a tuning number, and a copied literal here would go stale the day it changes
    # while still passing, which is how the guard came to have no coverage at all.
    payload = _wrap_in_lists(_JOBPOSTING, resolve._MAX_DEPTH + 1)
    assert resolve._hiring_org_from_jsonld(json.dumps(payload)) is None


def test_an_adversarially_nested_payload_abstains_rather_than_blowing_the_stack():
    # WHY the cap is there, not just where it sits. `_iter_nodes` recurses through
    # `yield from`, so an untrusted payload nested past the interpreter's recursion
    # limit raises RecursionError -- which is not a ValueError/TypeError, so
    # `_hiring_org_from_jsonld`'s own except cannot see it, and it would surface as one
    # scraped page killing a triage batch. Built in Python rather than parsed from a
    # JSON string on purpose: at this depth `json.loads` has its own recursion ceiling
    # that varies by interpreter version, and this test is about the walk, not the parser.
    payload = _wrap_in_lists(_JOBPOSTING, 1500)
    assert list(resolve._iter_nodes(payload)) == []


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


# ── tier 3 (#120): pure functions, tested standalone before they're wired in ──

def test_org_candidates_reads_hiring_org_publisher_and_author_names():
    raw = json.dumps([
        {"@type": "JobPosting",
         "hiringOrganization": {"name": "Example Co"},
         "publisher": {"name": "Example Board"},
         "author": {"name": "Example Poster"}},
    ])
    got = resolve._org_candidates(raw)
    assert got == ["Example Co", "Example Board", "Example Poster"]


def test_org_candidates_reads_a_bare_organization_typed_node():
    raw = json.dumps([{"@type": "Organization", "name": "Example Co"}])
    assert resolve._org_candidates(raw) == ["Example Co"]


def test_org_candidates_drops_duplicates_preserving_first_order():
    raw = json.dumps([
        {"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"},
         "publisher": {"name": "Example Co"}},
    ])
    assert resolve._org_candidates(raw) == ["Example Co"]


def test_org_candidates_caps_at_the_limit():
    nodes = [{"@type": "Organization", "name": f"Example Co {i}"}
            for i in range(resolve._CANDIDATE_LIMIT + 5)]
    got = resolve._org_candidates(json.dumps(nodes))
    assert len(got) == resolve._CANDIDATE_LIMIT


def test_org_candidates_truncates_a_name_at_the_char_cap():
    long_name = "E" * (resolve._CANDIDATE_CHARS + 20)
    raw = json.dumps([{"@type": "Organization", "name": long_name}])
    got = resolve._org_candidates(raw)
    assert len(got[0]) == resolve._CANDIDATE_CHARS


def test_org_candidates_ignores_a_non_string_name():
    raw = json.dumps([{"@type": "Organization", "name": ["not", "a", "string"]}])
    assert resolve._org_candidates(raw) == []


def test_org_candidates_on_malformed_json_returns_empty_not_raises():
    assert resolve._org_candidates("{not valid json") == []


def test_org_candidates_on_blank_input_returns_empty():
    assert resolve._org_candidates("") == []


def test_org_candidates_on_an_adversarially_deep_payload_returns_empty_not_raises():
    # Same reasoning as test_an_adversarially_nested_payload_abstains_rather_than_
    # blowing_the_stack above (json.loads' own recursion ceiling varies by
    # interpreter version, so this nests in Python rather than parsing a JSON
    # string), but pinning _org_candidates' OWN except clause: it only caught
    # (ValueError, TypeError) before this fix, so RecursionError went straight
    # through uncaught -- an adversarially deep board-authored payload would crash
    # the whole triage batch reasoning over tier 3's evidence, not just abstain.
    payload = _wrap_in_lists(_JOBPOSTING, 1500)
    assert resolve._org_candidates(json.dumps(payload)) == []


def test_text_with_a_lone_surrogate_does_not_raise():
    # A lone (unpaired) UTF-16 surrogate: JSON's own `json` module tolerantly
    # reads/writes this even though the JSON spec disallows it, so it can round-trip
    # through the dossier cache intact and reach _text as an ordinary Python str.
    # str.encode("utf-8") defaults to errors="strict" and RAISES UnicodeEncodeError
    # on exactly this -- _text's own docstring claims it degrades rather than
    # raises, which was false before this fix.
    value = "Example \ud800 Co"
    got = resolve._text(value, 300)
    assert isinstance(got, str)


def test_build_resolve_prompt_carries_title_candidates_and_jd():
    d = {"page_title": "Senior Engineer | Example Board",
        "structured_data": json.dumps({"@type": "Organization", "name": "Example Co"}),
        "jd": {"markdown": "We build things at Example Co."}}
    prompt = resolve._build_resolve_prompt(d)
    assert "Senior Engineer | Example Board" in prompt
    assert "Example Co" in prompt
    assert "We build things at Example Co." in prompt


def test_build_resolve_prompt_first_line_is_fixed():
    d = {"page_title": "Anything", "structured_data": "", "jd": {}}
    prompt = resolve._build_resolve_prompt(d)
    assert prompt.startswith(resolve._RESOLVE_PROMPT_HEAD.splitlines()[0])


def test_resolve_prompt_head_carries_the_shared_untrusted_content_warning():
    # Pins the shared constant's presence directly -- reverting rule 5 to its old,
    # independently-worded copy (the one that had silently dropped "whatever it says
    # about itself") must redden this test, not just be caught by chance elsewhere.
    assert UNTRUSTED_SCRAPED_CONTENT_WARNING in resolve._RESOLVE_PROMPT_HEAD


def test_build_resolve_prompt_returns_none_when_everything_is_blank():
    assert resolve._build_resolve_prompt({"page_title": "", "structured_data": "", "jd": {}}) is None


def test_build_resolve_prompt_tolerates_a_missing_jd_key():
    d = {"page_title": "Something", "structured_data": ""}
    assert resolve._build_resolve_prompt(d) is not None


def test_build_resolve_prompt_tolerates_a_non_dict_jd():
    d = {"page_title": "Something", "structured_data": "", "jd": "not a dict"}
    assert resolve._build_resolve_prompt(d) is not None


def test_build_resolve_prompt_caps_the_title_at_its_limit():
    sentinel = "SENTINEL_PAST_CAP"
    title = "A" * resolve._TITLE_LIMIT + sentinel
    d = {"page_title": title, "structured_data": "", "jd": {}}
    assert sentinel not in resolve._build_resolve_prompt(d)


def test_build_resolve_prompt_caps_the_jd_at_its_limit():
    sentinel = "SENTINEL_PAST_CAP"
    jd = "A" * resolve._JD_LIMIT + sentinel
    d = {"page_title": "", "structured_data": "", "jd": {"markdown": jd}}
    assert sentinel not in resolve._build_resolve_prompt(d)


def test_company_from_reply_accepts_a_clean_one_line_answer():
    assert resolve._company_from_reply("Example Co") == "Example Co"


def test_company_from_reply_strips_surrounding_whitespace():
    assert resolve._company_from_reply("  Example Co  \n") == "Example Co"


@pytest.mark.parametrize("reply", ["NONE", "none", "None.", " NONE \n", "none!"])
def test_company_from_reply_abstains_on_none_in_any_casing_or_punctuation(reply):
    assert resolve._company_from_reply(reply) is None


def test_company_from_reply_abstains_on_an_empty_reply():
    assert resolve._company_from_reply("") is None
    assert resolve._company_from_reply("   \n  ") is None


def test_company_from_reply_abstains_on_a_multi_line_reply():
    assert resolve._company_from_reply("Example Co\nSecond line") is None


def test_company_from_reply_abstains_on_a_non_string_reply():
    assert resolve._company_from_reply(None) is None
    assert resolve._company_from_reply(123) is None


def test_company_from_reply_accepts_an_answer_at_the_length_cap():
    answer = "E" * resolve._MAX_COMPANY_CHARS
    assert resolve._company_from_reply(answer) == answer


def test_company_from_reply_rejects_an_answer_one_character_past_the_length_cap():
    answer = "E" * (resolve._MAX_COMPANY_CHARS + 1)
    assert resolve._company_from_reply(answer) is None


@pytest.mark.parametrize("candidate", [
    "Confidential", "confidential.", "CONFIDENTIAL", "Undisclosed", "Unknown", "N/A",
    "Not Disclosed", "Private", "Private Company", "Stealth", "Stealth Startup",
    "Various", "Various Clients", "Client", "The Client", "Our Client",
    "Recruitment Agency", "Recruiter", "Agency",
])
def test_is_non_answer_catches_the_deny_list_in_any_casing(candidate):
    assert resolve._is_non_answer(candidate) is True


def test_is_non_answer_accepts_a_real_company_name():
    assert resolve._is_non_answer("Example Co") is False


def test_is_board_name_refuses_the_leads_own_source_id():
    fm = {"url": "https://jobs.example-invalid.test/x", "source": "examplecareers"}
    assert resolve._is_board_name("examplecareers", fm) is True
    assert resolve._is_board_name("ExampleCareers", fm) is True


def test_is_board_name_refuses_the_leads_url_host_label():
    fm = {"url": "https://boards.example-careers.invalid/x", "source": "other-id"}
    assert resolve._is_board_name("example-careers", fm) is True


def test_is_board_name_accepts_a_real_employer_name():
    fm = {"url": "https://boards.example-careers.invalid/x", "source": "example-careers"}
    assert resolve._is_board_name("Example Co", fm) is False


def test_is_board_name_known_limitation_does_not_catch_a_multi_word_board_name():
    """Documents (pins, does NOT attempt to fix) a known, deliberately accepted gap
    in _is_board_name -- see its own docstring for the full rationale. The guard is an
    EXACT match against the source id or the host's second-level label, so a
    multi-word board name whose human-readable form doesn't match its slug
    ("Example Remote Board" against an "exampleremoteboard" source/host) is NOT
    caught.
    Normalizing punctuation/spaces before comparing was considered and rejected:
    it would also reject a CORRECT answer whenever a real employer's own careers
    page happens to be hosted at a domain containing their own name. This test
    exists to make the gap visible and INTENTIONAL to a future reader, not to
    invite a narrow normalization fix made without the same design consideration."""
    fm = {"url": "https://exampleremoteboard.invalid/x", "source": "exampleremoteboard"}
    assert resolve._is_board_name("Example Remote Board", fm) is False


# ── tier 3 wired into resolve_company ──────────────────────────────────────────

def _backend(replies):
    """A resolve_backend double: `replies` is consumed in order. A BackendError
    INSTANCE is raised, not returned; anything else is returned as the reply.
    Exhausting the list raises AssertionError -- a mis-counted fixture must fail
    loudly, not silently return an abstain that reads as a real one."""
    from sluice.core.backends import BackendError as _BE

    class _Backend:
        def __init__(self):
            self._replies = list(replies)
            self.calls = []

        def complete(self, prompt):
            self.calls.append(prompt)
            if not self._replies:
                raise AssertionError("resolve backend double: no more scripted replies")
            reply = self._replies.pop(0)
            if isinstance(reply, _BE):
                raise reply
            return reply
    return _Backend()


_LLM_FM = {"url": "https://example.invalid/jobs/1", "source": "example-board"}
_NONEMPTY_DOSSIER = {"page_title": "Senior Engineer | Example Board", "structured_data": ""}


def test_tier3_names_the_company_when_both_earlier_tiers_abstain():
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend(["Example Co"])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company == "Example Co"
    assert got.tier == "tier3"
    assert got.llm_called is True
    assert got.llm_error is False
    assert len(backend.calls) == 1


def test_a_tier2_hit_never_spends_an_llm_call():
    cache = _RecordingCache(dossier={
        "structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""})
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.tier == "tier2"
    assert got.llm_called is False
    assert backend.calls == []


def test_a_tier1_hit_never_spends_an_llm_call():
    src = _source(company_from_url=lambda url: "Example Co")
    cache = _RecordingCache()
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True,
                                  company_resolve_llm=True, resolve_backend=backend)
    assert got.tier == "tier1"
    assert got.llm_called is False
    assert backend.calls == []


def test_company_resolve_llm_off_never_calls_the_backend():
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=False,
                                  resolve_backend=backend)
    assert got.company is None
    assert got.llm_called is False
    assert backend.calls == []


def test_a_missing_resolve_backend_leaves_tier3_off_without_raising():
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=None)
    assert got.company is None
    assert got.llm_called is False


def test_no_llm_never_reaches_tier3():
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=True,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert got.llm_called is False
    assert backend.calls == []


def test_a_failed_dossier_fetch_never_spends_an_llm_call():
    cache = _RecordingCache(raises=RuntimeError("boom"))
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert got.llm_called is False
    assert backend.calls == []


def test_a_non_dict_dossier_abstains_rather_than_raising():
    """#120 whole-branch review: DossierCache.get_or_build does a bare json.loads()
    on a cache hit with no type check, so a cache file whose JSON top level is a
    list, string, or number returns that value verbatim. Such a `dossier` is not
    None, so it survives tier 2's own `except Exception` (it was already assigned
    before `_from_dossier`'s AttributeError fires) and, pre-fix, reached
    `_build_resolve_prompt(dossier)` OUTSIDE any try/except -- where `dossier.get`
    raises AttributeError. engine.run has no try around resolve_company, so that
    would kill the WHOLE triage batch over one malformed cache entry, directly
    contradicting this tier's own "failure can never take down the batch" promise.
    The gate must abstain on ANY non-dict, not just None."""
    cache = _RecordingCache(dossier=["not", "a", "dict"])
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert backend.calls == [], "must never spend a backend call reasoning over garbage evidence"


def test_blank_evidence_never_spends_an_llm_call():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    backend = _backend([])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert got.llm_called is False
    assert backend.calls == []


def test_a_backend_error_in_tier3_abstains_rather_than_propagating():
    from sluice.core.backends import BackendError
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend([BackendError("down")])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert got.llm_called is True
    assert got.llm_error is True


def test_a_non_backend_error_in_tier3_propagates_rather_than_being_swallowed():
    # THE test that actually distinguishes resolve.py's narrow `except BackendError`
    # from a broad `except Exception` -- mutation-verified: widening the tier-3
    # except clause to `except Exception` left the ENTIRE prior suite green, which
    # means nothing proved the distinction mattered before this test existed.
    # tests/harness/backend.py's ScriptedBackend deliberately raises AssertionError
    # (never BackendError) on an unrecognised prompt specifically so a mis-wired
    # tier-3 call is LOUD in every e2e/functional test that reaches it -- this
    # mirrors that exact failure shape directly. Must go RED if the except clause
    # is ever widened past BackendError.
    class _MiswiredBackend:
        def complete(self, prompt):
            raise AssertionError("resolve backend double: no more scripted replies")

    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    with pytest.raises(AssertionError):
        resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                company_resolve_fetch=True, company_resolve_llm=True,
                                resolve_backend=_MiswiredBackend())


def test_tier3_makes_exactly_one_backend_call_and_never_retries():
    # A regression pin, not a mutation-killed test: there is no retry construct to
    # delete, so this only guards against one being ADDED later.
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend(["not a single clean line\nof output"])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert len(backend.calls) == 1


def test_tier3_refuses_a_deny_listed_answer():
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend(["Confidential"])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None
    assert got.llm_called is True


def test_tier3_refuses_the_leads_own_source_id_as_an_answer():
    fm = {"url": "https://example.invalid/jobs/1", "source": "example-board"}
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend(["example-board"])
    got = resolve.resolve_company(fm, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None


@pytest.mark.parametrize("unsafe", _UNSAFE_COMPANIES)
def test_tier3_candidate_with_a_structural_character_is_rejected(unsafe):
    cache = _RecordingCache(dossier=_NONEMPTY_DOSSIER)
    backend = _backend([unsafe])
    got = resolve.resolve_company(_LLM_FM, None, cache, no_llm=False,
                                  company_resolve_fetch=True, company_resolve_llm=True,
                                  resolve_backend=backend)
    assert got.company is None


def test_the_scripted_backends_resolve_prefix_still_matches_the_real_prompt():
    from tests.harness.backend import _RESOLVE
    prompt = resolve._build_resolve_prompt(_NONEMPTY_DOSSIER)
    assert prompt.startswith(_RESOLVE)
