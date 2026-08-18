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
                    company="Acme", url=r.get("link", ""))
                for r in raw["result"]
            ]

    rows = [{"title": f"T{i}", "company": "", "link": f"http://x/{i}"} for i in range(8)]
    src = _RepairingSource("demo", rows)
    ctx = _ctx()
    from sluice.ingest.engine import _run_source

    fresh, result = [], type("R", (), {"fetched": 0, "status": "ok", "error": None})()
    _, signals = _run_source(src, ctx, set(), fresh, result, fetch_timeout=5, retries=1)
    assert signals["company_rate"] == 1.0, "measured the raw row (blank), not the repaired lead"


# ---- the circuit breaker: a degraded run must not write its leads (#156) ----------------

class _SignalingSource(FakeSource):
    """A `FakeSource` whose `health_hint` reports whatever signals a test hands it,
    rather than the real hosts/degraded-marker plumbing -- isolates the BREAKER (does
    `result.drift` in `BREAKER_REASONS` actually withhold the write?) from the DETECTOR
    wiring already covered elsewhere in this file and in `test_health_wrong_page.py`."""

    def __init__(self, id, rows, signals):
        super().__init__(id, rows)
        self._signals = signals

    def health_hint(self, raw):
        hint = super().health_hint(raw)
        hint.update(self._signals)
        return hint


def test_a_fallback_run_withholds_its_leads_from_the_sink(tmp_path):
    rows = [{"title": "T1", "link": "http://x/1"}, {"title": "T2", "link": "http://x/2"}]
    src = _SignalingSource("demo", rows, {"degraded": "anchor-fallback"})
    sink = _FakeSink()
    report = run([src], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    result = report.sources[0]
    assert result.drift == "fallback"
    assert sink.leads == [], "a fallback run's leads reached the sink anyway"
    assert report.written == {"created": 0, "updated": 0, "skipped": 0}
    assert result.withheld == 2 and result.fresh == 2


def test_a_login_run_withholds_its_leads_from_the_sink(tmp_path):
    rows = [{"title": f"T{i}", "link": f"http://x/{i}"} for i in range(5)]
    src = _SignalingSource("demo", rows,
                          {"requested_path": "/jobs", "landed_path": "/login"})
    sink = _FakeSink()
    report = run([src], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    result = report.sources[0]
    assert result.drift == "login"
    assert sink.leads == []
    assert result.withheld == 5


def test_a_blank_run_withholds_its_leads_from_the_sink(tmp_path):
    h = _health(tmp_path)
    h.record("blanked", 10, {"company_rate": 0.9})
    h.record("blanked", 10, {"company_rate": 0.0})
    rows = [{"title": f"Nav {i}", "company": "", "link": f"http://x/{i}"} for i in range(10)]
    src = FakeSource("blanked", rows)
    sink = _FakeSink()
    report = run([src], _ctx(), sink, _FakeSeen(), h, retries=1)
    result = report.sources[0]
    assert result.drift == "blank"
    assert sink.leads == []
    assert result.withheld == 10


def test_a_drop_run_STILL_writes_its_leads(tmp_path):
    # `drop` is a bare row-count comparison, the LOWEST-confidence signal -- deliberately
    # NOT in BREAKER_REASONS. Suppressing a real day's leads on a false `drop` would be
    # worse than the late report, per the plan's residual note.
    h = _health(tmp_path)
    for _ in range(7):
        h.record("dropped", 100, {})
    rows = [{"title": "T1", "link": "http://x/1"}]
    src = FakeSource("dropped", rows)
    sink = _FakeSink()
    report = run([src], _ctx(), sink, _FakeSeen(), h, retries=1)
    result = report.sources[0]
    assert result.drift == "drop"
    assert sink.leads != [], "drop must not withhold -- it is the lowest-confidence signal"
    assert result.withheld == 0


class _RaisingHealth:
    """A health store whose `record()` always raises -- exercises `_update_health`'s except
    arm (review-found on PR #155): a health-pipeline failure must not let BREAKER_REASONS'
    own detector fail OPEN and write the run through unclassified. `baseline`/`rate_highs`/
    `prior_rate`/`should_retire` are never reached once `record` raises first, but are
    implemented anyway so a future reordering inside `_update_health` doesn't AttributeError
    this fake instead of exercising the path under test."""

    def baseline(self, source_id):
        return 0.0

    def rate_highs(self, source_id):
        return {}

    def prior_rate(self, source_id, key):
        return None

    def record(self, source_id, count, signals):
        raise RuntimeError("disk full")

    def should_retire(self, source_id):
        return False


def test_a_health_pipeline_failure_withholds_rather_than_writes_unclassified(tmp_path):
    rows = [{"title": "T1", "link": "http://x/1"}, {"title": "T2", "link": "http://x/2"}]
    src = FakeSource("demo", rows)
    sink = _FakeSink()
    report = run([src], _ctx(), sink, _FakeSeen(), _RaisingHealth(), retries=1)
    result = report.sources[0]
    assert result.drift is None, "drift must stay unclassified, not synthesized"
    assert result.health_error == "disk full"
    assert sink.leads == [], "a run whose own health check failed reached the sink anyway"
    assert report.written == {"created": 0, "updated": 0, "skipped": 0}
    assert result.withheld == 2 and result.fresh == 2


def test_a_healthy_run_reports_zero_withheld(tmp_path):
    rows = [{"title": "T1", "link": "http://x/1"}]
    src = FakeSource("demo", rows)
    report = run([src], _ctx(), _FakeSink(), _FakeSeen(), _health(tmp_path), retries=1)
    assert report.sources[0].withheld == 0


def test_a_withheld_leads_key_never_enters_seen_db(tmp_path):
    # THE self-healing property: a withheld lead is never passed to sink.write(), so
    # seendb.save() never runs for it, so it never enters seen.db, so the NEXT run
    # re-fetches and re-evaluates it from scratch the moment the rot clears -- no
    # special-case recovery path needed. Proven against the REAL SeenDb, not a fake, so
    # the claim is about the actual persisted store, not this test's own bookkeeping.
    rows = [{"title": "T1", "link": "http://x/1", "company": "Acme"}]
    src = _SignalingSource("demo", rows, {"degraded": "anchor-fallback"})
    vault = Vault(str(tmp_path / "vault"))
    seen = SeenDb(str(tmp_path / "seen.db"))
    sink = VaultSink(vault, seen, today=lambda: "2026-07-07")
    report = run([src], _ctx(), sink, seen, _health(tmp_path), retries=1)

    # Non-vacuity: prove a lead was actually FOUND and withheld, not that nothing ever
    # existed to write in the first place -- the latter would satisfy every assertion
    # below even with the breaker deleted outright.
    assert report.sources[0].fresh == 1 and report.sources[0].withheld == 1

    leads_dir = tmp_path / "vault" / "Job Applications" / "Job Leads"
    # `not leads_dir.exists()` is itself the strongest possible witness here: nothing --
    # not even the DIRECTORY -- was ever written, because the withheld lead never reached
    # `sink.write()` at all.
    assert not leads_dir.exists() or not any(leads_dir.iterdir()), (
        "the withheld lead was written to the vault anyway"
    )
    assert seen.load() == set(), "the withheld lead's key reached seen.db anyway"


def test_a_withheld_lead_does_not_suppress_a_healthy_siblings_identical_lead(tmp_path):
    # A real cross-source silent-loss shape: the SAME job posted on two boards. Source A is
    # broken (fallback) and would have withheld its own copy anyway -- but before this fix,
    # A's copy still claimed the shared dedup key in the RUN-LOCAL seen_keys set while being
    # parsed (before A's own drift was even known), so B's identical, perfectly healthy
    # lead was silently dropped as "already seen this run" and reached nowhere -- exactly
    # the silent-lead-loss failure #156 exists to close, reintroduced one layer over.
    a = _SignalingSource("a", [{"title": "Same Job", "link": "http://shared/1", "company": ""}],
                        {"degraded": "anchor-fallback"})
    b = FakeSource("b", [{"title": "Same Job", "link": "http://shared/1", "company": "Acme"}])
    sink = _FakeSink()
    report = run([a, b], _ctx(), sink, _FakeSeen(), _health(tmp_path), retries=1)
    assert report.sources[0].drift == "fallback" and report.sources[0].withheld == 1
    assert report.sources[1].drift is None and report.sources[1].withheld == 0
    assert [lead.source for lead in sink.leads] == ["b"], (
        "source b's healthy, identical-url lead was suppressed by a's withheld copy"
    )


def test_a_degraded_marker_on_an_earlier_search_survives_a_clean_later_one(tmp_path):
    # The multi-search stickiness gap: `signals` is reassigned per search, so without
    # persisting `degraded` the same way `explained` already is, a source whose FIRST
    # search fell back and whose LAST search came back clean would report no `fallback` at
    # all for the run -- the marker silently overwritten. Shipped sources use one search
    # each today, but `sources.<id>.searches` is the documented way to configure a real
    # list, so this is the exact setup the docs steer an operator toward.
    class _TwoSearchSource(FakeSource):
        def searches(self):
            return [Search("first", "http://x/1"), Search("second", "http://x/2")]

        def fetch(self, ctx, search):
            if search.label == "first":
                return {"result": [{"title": "T1", "link": "http://x/1a", "degraded": "anchor-fallback"}],
                       "landed": "http://x", "requested": "http://x"}
            return {"result": [{"title": "T2", "link": "http://x/2a"}],
                   "landed": "http://x", "requested": "http://x"}

        def health_hint(self, raw):
            # Mirrors `BrowserListSource.health_hint`'s real `_first_degraded` promotion --
            # the base `FakeSource.health_hint` this class inherits from doesn't look at
            # rows at all, so a test relying on it alone would prove nothing about the real
            # producer's behaviour.
            hint = super().health_hint(raw)
            for row in raw.get("result", []):
                if row.get("degraded"):
                    hint["degraded"] = row["degraded"]
                    break
            return hint

    src = _TwoSearchSource("demo", [])
    report = run([src], _ctx(), _FakeSink(), _FakeSeen(), _health(tmp_path), retries=1)
    assert report.sources[0].drift == "fallback"


def test_rates_are_aggregated_over_every_search_not_the_last_one(tmp_path):
    # The row-floor's OTHER multi-search hole: a 2-search source returning 5 leads per
    # search never clears `_RATE_ROW_FLOOR` (8) on EITHER search alone, even though the
    # run's real total (10) comfortably clears it in aggregate. Per-search computation
    # would silently under-cover exactly the multi-search setup the docs steer people
    # toward; this proves the aggregate, not the last search's snapshot, is what feeds
    # `blank`.
    class _TwoSearchSource(FakeSource):
        def searches(self):
            return [Search("first", "http://x/1"), Search("second", "http://x/2")]

        def fetch(self, ctx, search):
            n = 0 if search.label == "first" else 5
            rows = [{"title": f"T{n + i}", "link": f"http://x/{n + i}", "company": ""}
                    for i in range(5)]
            return {"result": rows, "landed": "http://x", "requested": "http://x"}

    src = _TwoSearchSource("demo", [])
    ctx = _ctx()
    from sluice.ingest.engine import _run_source

    fresh, result = [], type("R", (), {"fetched": 0, "status": "ok", "error": None})()
    _, signals = _run_source(src, ctx, set(), fresh, result, fetch_timeout=5, retries=1)
    assert signals["company_rate"] == 0.0, (
        "the aggregate rate over all 10 parsed leads should have been computed"
    )


def test_a_login_wall_on_an_earlier_search_survives_a_clean_later_one(tmp_path):
    # Found by review (the same multi-search stickiness gap as `degraded`/rates above,
    # but for `login`, which had never been given the same treatment): `requested_path`/
    # `landed_path` were last-search-wins in `signals`, so a source whose FIRST search
    # landed on a login wall and whose LAST search came back clean reported no `login` at
    # all -- the run's real drift silently overwritten by an unrelated later search.
    class _TwoSearchSource(FakeSource):
        def searches(self):
            return [Search("first", "http://x/jobs"), Search("second", "http://x/jobs")]

        def fetch(self, ctx, search):
            landed = "http://x/login" if search.label == "first" else "http://x/jobs"
            return {"result": [{"title": f"T-{search.label}", "link": f"http://x/{search.label}"}],
                   "landed": landed, "requested": search.url}

        def health_hint(self, raw):
            # Mirrors the real `_path()` helper in `ingest/base.py` -- the base
            # `FakeSource.health_hint` this class inherits from reports only hosts, never
            # paths, so a test relying on it alone would prove nothing about the real
            # producer's behaviour.
            from urllib.parse import urlparse

            hint = super().health_hint(raw)
            hint["landed_path"] = urlparse(raw.get("landed", "")).path
            hint["requested_path"] = urlparse(raw.get("requested", "")).path
            return hint

    src = _TwoSearchSource("demo", [])
    report = run([src], _ctx(), _FakeSink(), _FakeSeen(), _health(tmp_path), retries=1)
    result = report.sources[0]
    assert result.drift == "login"
    # The breaker follows the (now-correct) classification: both searches' leads were
    # withheld, not just the one that actually landed on the wall.
    assert result.withheld == 2
