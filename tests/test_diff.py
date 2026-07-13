from scripts.diff_vs_legacy import _key, diff


def test_key_strips_fragment_but_keeps_query():
    assert _key({"url": "https://a/1#frag"}) == "https://a/1"
    assert _key({"link": "https://a/1?jobId=2"}) == "https://a/1?jobId=2"


def test_key_falls_back_to_title_company_for_urlless():
    assert _key({"title": "Analyst", "company": "Acme"}) == "h:analyst|acme"
    assert _key({"title": ""}) == ""


def test_diff_reports_overlap_and_gaps():
    jp = [{"url": "https://a/1", "source": "cord"},
          {"url": "https://a/2", "source": "cord"}]
    lg = [{"link": "https://a/1", "source": "cord"},
          {"link": "https://a/3", "source": "reed"}]
    d = diff(jp, lg)
    assert d["sluice_total"] == 2
    assert d["legacy_total"] == 2
    assert d["overlap"] == 1
    assert d["sluice_only"] == ["https://a/2"]
    assert d["legacy_only"] == ["https://a/3"]     # the coverage gap
    assert d["per_source"]["cord"] == {"sluice": 2, "legacy": 1}


def test_diff_no_gap_when_sluice_superset():
    jp = [{"url": "https://a/1"}, {"url": "https://a/2"}]
    lg = [{"link": "https://a/1"}]
    d = diff(jp, lg)
    assert d["legacy_only"] == []                   # no gap: sluice covers legacy
    assert d["sluice_only"] == ["https://a/2"]
