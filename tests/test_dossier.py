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
