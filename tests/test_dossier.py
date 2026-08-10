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
    lead = {"company": "Acme", "role": "Analyst", "location": "London",
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


def test_get_or_build_loads_a_legacy_cached_dossier_missing_the_new_fields(tmp_path):
    # A pre-#109 cache entry never wrote page_title/structured_data at all.
    legacy = {"schema_version": 2, "lead_id": "legacy-co-role", "company": "Legacy",
             "position": "Role", "location": "", "role_type": "",
             "lead_snapshot": {}, "jd": {"markdown": ""}, "glassdoor": {},
             "built_at": datetime(2026, 7, 7).isoformat()}
    (tmp_path / "legacy-co-role.json").write_text(json.dumps(legacy))
    dc = DossierCache(str(tmp_path), ttl_days=7,
                      fetcher=lambda lead: {"jd": {}, "glassdoor": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    d = dc.get_or_build({"lead_id": "legacy-co-role"})
    assert d["company"] == "Legacy"
    assert d.get("page_title") is None    # never written; get_or_build must not raise
