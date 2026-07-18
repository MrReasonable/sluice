from sluice.core.health import HealthStore
from sluice.core.leads import Lead
from sluice.core.config import Config
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault
from sluice.ingest.base import Ctx, Search
from sluice.ingest.engine import run
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
                 company=r.get("company", ""), url=r["link"])
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
