from sluice.core.health import HealthStore
from sluice.core.leads import Lead
from sluice.core.config import Config
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault
from sluice.ingest.base import Ctx, Search
from sluice.ingest.engine import _lead_rates, run
from sluice.ingest.sink import VaultSink


class FakeSource:
    def __init__(self, id, rows, *, raise_on_fetch=False):
        self.id = id
        self.enabled = True
        self.kind = "browser"
        self._rows = rows
        self._raise = raise_on_fetch

    def searches(self):
        return [Search("s", "http://x")]

    def fetch(self, ctx, search):
        if self._raise:
            raise RuntimeError("boom")
        return {"result": self._rows, "landed": "http://x", "requested": "http://x"}

    def parse(self, raw, search):
        return [
            Lead(source=self.id, search=search.label, title=r["title"],
                 company=r.get("company", ""), url=r.get("link", ""),
                 location=r.get("location", ""))
            for r in raw["result"]
        ]

    def health_hint(self, raw):
        return {"count": len(raw.get("result", [])),
                "landed_host": "x", "requested_host": "x", "markers": {}}


class _FakeSeen:
    def __init__(self, urls=()):
        self._u = set(urls)

    def load(self):
        return set(self._u)

    def save(self, leads):
        for lead in leads:
            self._u.add(lead.dedup_key)


class _FakeSink:
    def __init__(self):
        self.leads = []

    def write(self, leads):
        self.leads.extend(leads)
        return {"created": len(leads), "updated": 0, "skipped": 0}


def _ctx():
    return Ctx(camofox=None, config=None, sleep=lambda *_: None)


def _health(tmp_path):
    return HealthStore(str(tmp_path / "h.json"))


def test_writes_relevant_leads_to_sink(tmp_path):
    src = FakeSource("demo", [{"title": "Banker", "link": "http://x/1"},
                              {"title": "Software Engineer", "link": "http://x/2"}])
    sink = _FakeSink()
    report = run([src], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert len(sink.leads) == 2
    assert report.written["created"] == 2


def test_urlless_leads_differing_in_location_both_survive_dedup(tmp_path):
    # #23 end-to-end: two url-less leads sharing title+company but at DIFFERENT cities must
    # BOTH reach the sink. The engine's read-key must not collapse them before the store's
    # #5 split runs -- otherwise the second city is silently dropped one layer too early.
    src = FakeSource("demo", [
        {"title": "Eng Mgr", "company": "Acme", "link": "", "location": "Palmerburgh"},
        {"title": "Eng Mgr", "company": "Acme", "link": "", "location": "Clarkefurt"},
    ])
    sink = _FakeSink()
    run([src], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert len(sink.leads) == 2
    assert {lead.location for lead in sink.leads} == {"Palmerburgh", "Clarkefurt"}


def test_source_error_is_isolated(tmp_path):
    bad = FakeSource("bad", [], raise_on_fetch=True)
    good = FakeSource("good", [{"title": "Banker", "link": "http://x/1"}])
    sink = _FakeSink()
    report = run([bad, good], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert [l.source for l in sink.leads] == ["good"]   # bad didn't abort the run
    bad_result = next(r for r in report.sources if r.source_id == "bad")
    assert bad_result.status == "error"
    assert bad_result.error == "boom"


def test_zero_rows_flags_drift(tmp_path):
    report = run([FakeSource("empty", [])], _ctx(), _FakeSink(), _FakeSeen(),
                 _health(tmp_path), retries=1)
    assert report.degraded == [("empty", "zero")]
    assert report.sources[0].status == "ok"   # returned zero != errored


def test_irrelevant_titles_dropped(tmp_path, titles):
    # The gate only filters what the USER configured it to filter, so the test
    # supplies its own synthetic lists rather than asserting on real preferences.
    accept, reject = titles
    keep_title, drop_title = accept[0].title(), reject[0].title()
    src = FakeSource("demo", [{"title": drop_title, "link": "http://x/1"},
                              {"title": keep_title, "link": "http://x/2"}])
    sink = _FakeSink()
    cfg = Config(relevance_keep=[accept[0]], relevance_drop=[reject[0]])
    run([src], Ctx(camofox=None, config=cfg, sleep=lambda *_: None),
        sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert [lead.title for lead in sink.leads] == [keep_title]


def test_unconfigured_gate_drops_nothing(tmp_path, titles):
    # Shipping no lists must never silently filter on somebody else's taste.
    accept, reject = titles
    src = FakeSource("demo", [{"title": reject[0].title(), "link": "http://x/1"},
                              {"title": accept[0].title(), "link": "http://x/2"}])
    sink = _FakeSink()
    run([src], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert len(sink.leads) == 2


def test_already_seen_dropped(tmp_path):
    src = FakeSource("demo", [{"title": "Banker", "link": "http://x/1"}])
    sink = _FakeSink()
    run([src], _ctx(), sink, _FakeSeen(urls=["http://x/1"]), _health(tmp_path), retries=1)
    assert sink.leads == []


def test_duplicate_across_sources_deduped(tmp_path):
    a = FakeSource("a", [{"title": "Banker", "link": "http://x/1"}])
    b = FakeSource("b", [{"title": "Banker", "link": "http://x/1"}])
    sink = _FakeSink()
    run([a, b], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert len(sink.leads) == 1   # second source's dup dropped


def test_auto_retire_after_three_zero_runs(tmp_path):
    h = _health(tmp_path)
    src, report = None, None
    for _ in range(3):
        src = FakeSource("empty", [])
        report = run([src], _ctx(), _FakeSink(), _FakeSeen(), h, retries=1)
    assert report.sources[0].retired is True
    assert src.enabled is False


def test_one_unwriteable_lead_does_not_stop_a_later_source(tmp_path, monkeypatch):
    # #24 blast radius: an OSError writing source A's lead must not abort the run
    # or skip source B. With Task 3's per-lead guard, source B is still written.
    src_a = FakeSource("aaa", [{"title": "Banker", "link": "http://x/1", "company": "Aye"}])
    src_b = FakeSource("bbb", [{"title": "Banker", "link": "http://x/2", "company": "Bee"}])

    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    sink = VaultSink(vault, seen, today=lambda: "2026-07-07")

    real_upsert = vault.upsert

    def flaky(lead):
        if lead.url == "http://x/1":       # source A's lead only
            raise OSError("simulated store refusal")
        return real_upsert(lead)

    monkeypatch.setattr(vault, "upsert", flaky)
    report = run([src_a, src_b], _ctx(), sink, seen, _health(tmp_path), retries=1)

    leads_dir = tmp_path / "vault" / "Job Applications" / "Job Leads"
    assert (leads_dir / "Bee - Banker.md").exists()      # later source still written
    assert not (leads_dir / "Aye - Banker.md").exists()  # failed lead not written
    assert report.written["skipped"] == 1


# ---- _lead_rates: #156's `blank` producer -----------------------------------------------

def _leads(n, *, company=True, link=True):
    return [Lead(source="s", search="s", title=f"T{i}",
                company="Acme" if company else "",
                url=f"http://x/{i}" if link else "")
            for i in range(n)]


def test_lead_rates_below_the_row_floor_reports_nothing():
    # The row floor gate: below it, a rate is NOISE (a single comma-less title on a
    # small carousel swings a 1-2 row source's rate from 0 to 1), so no key is emitted
    # at all -- "no rate computed" and "a rate of 0.0" must stay distinguishable.
    assert _lead_rates(_leads(7, company=False)) == {}


def test_lead_rates_at_and_above_the_row_floor_computes():
    assert _lead_rates(_leads(8, company=False))["company_rate"] == 0.0
    assert _lead_rates(_leads(8))["company_rate"] == 1.0


def test_lead_rates_measures_company_and_link_independently():
    leads = _leads(8, company=True, link=False)
    rates = _lead_rates(leads)
    assert rates["company_rate"] == 1.0
    assert rates["link_rate"] == 0.0


def test_lead_rates_on_a_partial_fixture():
    leads = _leads(6, company=True) + _leads(4, company=False)
    assert _lead_rates(leads)["company_rate"] == 0.6


# ---- the signal flows end-to-end through run(), on PARSED leads, not raw rows -----------

def test_blank_flows_end_to_end_through_a_real_run(tmp_path):
    # A source whose extractor STILL returns titled rows -- so parse() does not filter
    # them out -- but has lost every company. Seeded history gives it a high-water high
    # enough, and low enough streak, to arm the detector; then the run under test IS the
    # second low run.
    h = _health(tmp_path)
    h.record("blanked", 10, {"company_rate": 0.9})   # healthy history: sets the high-water
    h.record("blanked", 10, {"company_rate": 0.0})   # first low run: the streak's start

    rows = [{"title": f"Nav {i}", "company": "", "link": f"http://x/{i}"} for i in range(10)]
    src = FakeSource("blanked", rows)
    report = run([src], _ctx(), _FakeSink(), _FakeSeen(), h, retries=1)
    assert report.sources[0].drift == "blank"


def test_blank_measures_leads_parse_repairs_not_the_raw_row():
    # A source whose `parse` override RECOVERS the company from something the raw row
    # lacks (naukrigulf's real shape, #156) must be measured on the repaired value.
    class _RepairingSource(FakeSource):
        def parse(self, raw, search):
            return [
                Lead(source=self.id, search=search.label, title=r["title"],
                    company="Recovered Co", url=r.get("link", ""))
                for r in raw["result"]
            ]

    rows = [{"title": f"T{i}", "company": "", "link": f"http://x/{i}"} for i in range(8)]
    src = _RepairingSource("demo", rows)
    ctx = _ctx()
    from sluice.ingest.engine import _run_source

    fresh, result = [], type("R", (), {"fetched": 0, "status": "ok", "error": None})()
    _, signals = _run_source(src, ctx, set(), fresh, result, fetch_timeout=5, retries=1)
    assert signals["company_rate"] == 1.0, "measured the raw row (blank), not the repaired lead"
