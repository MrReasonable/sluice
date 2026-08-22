import json
from datetime import datetime, timedelta
from sluice.core.dossier import DossierCache, slim


def _clock(dt):
    return lambda: dt


def test_miss_then_hit_and_ttl(tmp_path):
    calls = []
    def fetcher(lead):
        calls.append(lead["company"])
        return {"jd": {"markdown": "x" * 9000}, "glassdoor": {"rating": "3.9"}}

    now = datetime(2026, 7, 7, 12, 0, 0)
    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=fetcher, clock=_clock(now))
    lead = {"company": "Acme", "role": "Analyst", "location": "Palmerburgh",
            "role_type": "permanent"}

    d1 = dc.get_or_build(lead)
    assert d1["company"] == "Acme" and d1["glassdoor"]["rating"] == "3.9"
    assert calls == ["Acme"]                       # miss -> fetched

    dc2 = DossierCache(str(tmp_path), ttl_days=7, fetcher=fetcher, clock=_clock(now))
    dc2.get_or_build(lead)
    assert calls == ["Acme"]                       # fresh hit -> not re-fetched

    later = _clock(now + timedelta(days=8))
    dc3 = DossierCache(str(tmp_path), ttl_days=7, fetcher=fetcher, clock=later)
    dc3.get_or_build(lead)
    assert calls == ["Acme", "Acme"]               # stale -> re-fetched


def test_slim_strips_and_truncates():
    d = {"lead_snapshot": {"a": 1}, "jd": {"markdown": "y" * 9000}, "company": "Z"}
    s = slim(d)
    assert "lead_snapshot" not in s
    assert len(s["jd"]["markdown"]) <= 4000
    assert s["company"] == "Z"


def test_cache_key_prefers_a_stable_url_hash_over_the_company_role_slug():
    dc = DossierCache("/unused", ttl_days=7, fetcher=lambda lead: {})
    before = dc.cache_key({"company": "", "role": "Staff Engineer",
                           "url": "https://x.invalid/y"})
    after = dc.cache_key({"company": "Example Co", "role": "Staff Engineer",
                          "url": "https://x.invalid/y"})
    assert before == after
    assert before.startswith("url-")


def test_cache_key_still_prefers_lead_id_over_url():
    dc = DossierCache("/unused", ttl_days=7, fetcher=lambda lead: {})
    assert dc.cache_key({"lead_id": "abc123", "url": "https://x.invalid/y"}) == "abc123"


def test_cache_key_falls_back_to_slug_with_no_url_or_lead_id():
    dc = DossierCache("/unused", ttl_days=7, fetcher=lambda lead: {})
    assert dc.cache_key({"company": "Example Co", "role": "Staff Engineer"}) == \
        "example-co-staff-engineer"


def test_slim_excludes_page_title_and_structured_data():
    d = {"lead_snapshot": {"a": 1}, "jd": {"markdown": "y"}, "company": "Z",
        "page_title": "Staff Engineer at Example Co | Example Board",
        "structured_data": '{"@type": "JobPosting"}'}
    s = slim(d)
    assert "page_title" not in s
    assert "structured_data" not in s


def test_get_or_build_captures_page_title_and_structured_data(tmp_path):
    def fetcher(lead):
        return {"jd": {"markdown": "x"}, "glassdoor": {},
                "page_title": "Staff Engineer at Example Co | Example Board",
                "structured_data": '{"@type": "JobPosting"}'}
    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=fetcher,
                      clock=_clock(datetime(2026, 7, 7)))
    d = dc.get_or_build({"company": "", "role": "Staff Engineer", "url": "https://x.invalid/y"})
    assert d["page_title"] == "Staff Engineer at Example Co | Example Board"
    assert d["structured_data"] == '{"@type": "JobPosting"}'


def _cache(tmp_path, jd_markdown, *, min_jd_chars=0):
    return DossierCache(str(tmp_path), ttl_days=7,
                        fetcher=lambda lead: {"jd": {"markdown": jd_markdown}, "glassdoor": {}},
                        clock=_clock(datetime(2026, 7, 8)), min_jd_chars=min_jd_chars)


def test_an_empty_jd_never_arrives_whatever_the_floor(tmp_path):
    # Empty is a FACT, not a judgement, so it fails at every floor including the
    # shipped 0 -- that is what makes `min_jd_chars: 0` a real fix rather than an
    # inert one (spec decision 3).
    for floor in (0, 200):
        dc = _cache(tmp_path, "   \n  ", min_jd_chars=floor)
        assert dc.jd_arrived(dc.get_or_build({"lead_id": f"empty-{floor}"})) is False


def test_a_short_jd_arrives_at_floor_zero_and_not_above_it(tmp_path):
    dc0 = _cache(tmp_path, "x" * 35, min_jd_chars=0)
    assert dc0.jd_arrived(dc0.get_or_build({"lead_id": "short-0"})) is True
    dc200 = _cache(tmp_path, "x" * 35, min_jd_chars=200)
    assert dc200.jd_arrived(dc200.get_or_build({"lead_id": "short-200"})) is False


def test_whitespace_cannot_pass_a_floor(tmp_path):
    # Stripped on BOTH sides of the comparison: 300 spaces must not clear a floor of 200.
    dc = _cache(tmp_path, " " * 300, min_jd_chars=200)
    assert dc.jd_arrived(dc.get_or_build({"lead_id": "spaces"})) is False


def test_a_malformed_jd_field_fails_rather_than_raising(tmp_path):
    # Same degrade-to-failure posture triage/resolve.py:_text already takes on this field.
    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=lambda lead: {"glassdoor": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    assert dc.jd_arrived({"jd": None}) is False
    assert dc.jd_arrived({"jd": {"markdown": 42}}) is False
    assert dc.jd_arrived({}) is False


def test_a_jd_that_did_not_arrive_is_not_persisted(tmp_path):
    dc = _cache(tmp_path, "", min_jd_chars=0)
    dc.get_or_build({"lead_id": "nothing"})
    assert not (tmp_path / "nothing.json").exists()


def test_the_not_persisted_path_returns_the_FRESH_dossier(tmp_path):
    # The caller must be able to answer jd_arrived on what it is holding, so the
    # rejected cached entry is never what comes back.
    #
    # built_at is deliberately far outside the 7-day TTL (clock is 2026-07-08): a
    # same-day-ish built_at would make `_fresh()` serve this file straight from disk
    # via get_or_build's early return, never reaching the fetcher or the write-gate
    # this test exists to pin -- measured: with built_at one day old (the pattern the
    # pre-existing legacy-dossier test below uses on purpose), this assertion fails
    # with the STALE on-disk company, because the code never gets past `_fresh`.
    (tmp_path / "stale.json").write_text(json.dumps({
        "schema_version": 2, "lead_id": "stale", "company": "Example Old Co",
        "position": "", "location": "", "role_type": "", "lead_snapshot": {},
        "jd": {"markdown": ""}, "glassdoor": {},
        "built_at": datetime(2026, 6, 1).isoformat()}))
    dc = _cache(tmp_path, "", min_jd_chars=0)
    d = dc.get_or_build({"lead_id": "stale", "company": "Example New Co"})
    assert d["company"] == "Example New Co"


def test_a_cached_entry_whose_jd_never_arrived_is_refetched(tmp_path):
    # Fresh BY TIME (1 day old, ttl 7) but empty by content. Without this the fix does
    # nothing to an existing deployment's cache for a full TTL.
    (tmp_path / "poisoned.json").write_text(json.dumps({
        "schema_version": 2, "lead_id": "poisoned", "company": "Example Stale Co",
        "position": "", "location": "", "role_type": "", "lead_snapshot": {},
        "jd": {"markdown": ""}, "glassdoor": {},
        "built_at": datetime(2026, 7, 7).isoformat()}))
    calls = []

    def _fetch(lead):
        calls.append(lead)
        return {"jd": {"markdown": "A real job description, at last."}, "glassdoor": {}}

    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=_fetch,
                      clock=_clock(datetime(2026, 7, 8)))
    d = dc.get_or_build({"lead_id": "poisoned"})
    assert calls, "a poisoned entry must be refetched, not served"
    assert d["jd"]["markdown"].startswith("A real job description")


def test_a_healthy_cached_entry_is_still_served_without_refetching(tmp_path):
    (tmp_path / "healthy.json").write_text(json.dumps({
        "schema_version": 2, "lead_id": "healthy", "company": "Example Co",
        "position": "", "location": "", "role_type": "", "lead_snapshot": {},
        "jd": {"markdown": "A real job description."}, "glassdoor": {},
        "built_at": datetime(2026, 7, 7).isoformat()}))
    calls = []
    dc = DossierCache(str(tmp_path), ttl_days=7,
                      fetcher=lambda lead: calls.append(lead) or {"jd": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    dc.get_or_build({"lead_id": "healthy"})
    assert calls == [], "a healthy entry must not be refetched"


def test_get_or_build_loads_a_legacy_cached_dossier_missing_the_new_fields(tmp_path):
    # A pre-#109 cache entry never wrote page_title/structured_data at all.
    # "legacy" is the SEMANTIC label here (the era the entry was written in), which
    # is why it stays in the test and lead_id names -- but it is also a real firm's
    # name, so the company VALUE takes the suite's `Example …` placeholder form.
    legacy = {"schema_version": 2, "lead_id": "legacy-co-role",
             "company": "Example Legacy Co",
             "position": "Role", "location": "", "role_type": "",
             "lead_snapshot": {}, "jd": {"markdown": "A legacy job description."}, "glassdoor": {},
             "built_at": datetime(2026, 7, 7).isoformat()}
    (tmp_path / "legacy-co-role.json").write_text(json.dumps(legacy))
    dc = DossierCache(str(tmp_path), ttl_days=7,
                      fetcher=lambda lead: {"jd": {}, "glassdoor": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    d = dc.get_or_build({"lead_id": "legacy-co-role"})
    assert d["company"] == "Example Legacy Co"
    assert d.get("page_title") is None    # never written; get_or_build must not raise
