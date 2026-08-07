import io
import os

from sluice.apply.engine import PrepResult
from sluice.core.app import Sluice
from sluice.core.config import Config
from tests.harness.config import FIXTURE_ADDR


class _FakeTab:
    def create_tab(self, url): return "t1"

    def evaluate(self, tab, js):
        # The dossier closure now probes the landed url before reading the body
        # (#18); answering both probes with "JD BODY" would read as a url with no
        # scheme and refuse the fetch.
        if js == "location.href":
            return {"result": "https://example.invalid/job"}
        return {"result": "JD BODY"}

    def close_tab(self, tab): return None


class _FakeGoogle:
    """Faithful fake of the client `track.engine.run` drives: the real method names
    (engine.py:50 calls search_messages, :59 calls get_message; calendar_sync.py
    calls list_events/insert_event/update_event/delete_event when an .ics attachment
    is present), all inert. A fake missing a called method surfaces as an
    AttributeError instead of exercising the test's actual assertion (tst-003)."""
    auth_error = False

    def search_messages(self, *a, **k): return []
    def get_message(self, *a, **k): return {}
    def list_events(self, *a, **k): return []
    def insert_event(self, *a, **k): return "evt1"
    def update_event(self, *a, **k): return "evt1"
    def delete_event(self, *a, **k): return None


def _track_config(tmp_path, monkeypatch):
    """Point SLUICE_CONFIG at a track: block under tmp_path. load_track_config's
    seen_db/token_path come ONLY from the YAML file at $SLUICE_CONFIG -- there is
    no TRACK_SEEN_DB env override -- so this is the only way to steer them into
    tmp_path for the test."""
    seen_db = str(tmp_path / "track-seen.db")
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text(f"track:\n  seen_db: {seen_db}\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))
    return seen_db


def test_dossier_cache_fetches_jd_via_the_fetcher_seam(tmp_path, titles):
    app = Sluice(Config(), fetcher=_FakeTab(),
                 resolve_host=lambda h: [FIXTURE_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    d = cache.get_or_build({"url": "https://example.invalid/job",
                            "company": "Acme", "title": titles[0]})
    assert d["jd"]["markdown"] == "JD BODY"


def test_dossier_cache_opens_no_browser_without_a_url(tmp_path, titles):
    class _Boom:
        def create_tab(self, url): raise AssertionError("must not be called")
    cache = Sluice(Config(), fetcher=_Boom()).dossier_cache(str(tmp_path), ttl_days=7)
    assert cache.get_or_build({"company": "Acme", "title": titles[0]})["jd"]["markdown"] == ""


def test_triage_no_llm_builds_no_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    called = []
    monkeypatch.setattr(app, "backend", lambda *a, **k: called.append(k) or None)
    report = app.triage(no_llm=True)                    # deterministic path
    assert hasattr(report, "counts") and report.backend is None
    assert called == [], "no_llm must not construct a backend (lazy/offline guarantee)"


def test_triage_threads_the_triage_config_into_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    seen = {}
    monkeypatch.setattr(app, "backend", lambda role, **kw: seen.update(role=role, **kw))
    app.triage(backend_role="primary")
    assert seen["role"] == "primary"
    assert seen["primary_model"] == "claude-sonnet-4-5"   # triage uses claude_max_model
    assert seen["effort"] == "medium"                     # ...and claude_max_effort
    assert seen["fallback_model"] == "deepseek-v4-flash"  # ...and cheap_model for fallback


def test_compose_cv_unknown_lead_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    monkeypatch.setattr(app, "backend", lambda *a, **k: object())  # avoid real creds
    assert app.compose_cv(lead="no-such-lead", dry_run=True) == []


def test_compose_cv_threads_the_cv_config_into_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    seen = {}
    monkeypatch.setattr(app, "backend", lambda role, **kw: seen.update(**kw) or object())
    app.compose_cv(lead="x", dry_run=True)
    assert seen["primary_model"] == "claude-sonnet-4-5"   # cv uses compose_model
    assert seen["effort"] == "max"                        # ...and compose_effort
    assert seen["fallback_model"] == "deepseek-v4-flash"  # ...and cheap_model for fallback


def test_compose_cv_single_lead_write_race_reports_dossier_failed(monkeypatch):
    """The SAME defect run_batch's catch-all was fixed against one commit ago (a lost
    write race under-reporting cli.py's "N CV(s) composed blind" summary) had a second,
    unenumerated call site: the single-lead `cv --lead` path in compose_cv. run_one
    stamps `dossier_failed` onto the exception it raises before re-raising it (see its
    own comment in cv/engine.py) precisely so a catch further up can read it back;
    compose_cv's `except VaultConflict` must do that too, not default to False."""
    import sluice.cv.engine as cv_engine
    from sluice.core.protocols import VaultConflict

    class _Note:
        """Minimal stand-in for a store-issued note: just enough for slug_matches
        and the ref used in compose_cv's log line."""
        def __init__(self):
            self.fm = {"status": "shortlist", "company": "Acme", "role": "Engineer"}
            self.ref = "Job Applications/Job Leads/Acme - Engineer.md"
            self.slug = "Acme - Engineer"

    class _FakeStore:
        def read_leads(self, statuses=None):
            return [_Note()]

    def _boom(*a, **k):
        # Mirrors what a real run_one does on a downstream failure (a render error,
        # a backend timeout) AFTER a dossier fetch the SSRF guard blocked: stamp
        # dossier_failed onto the exception, then let it propagate.
        e = VaultConflict("lost the write race")
        e.dossier_failed = True
        raise e

    monkeypatch.setattr(cv_engine, "run_one", _boom)
    app = Sluice(Config(), store=_FakeStore())
    monkeypatch.setattr(app, "backend", lambda *a, **k: object())  # avoid real creds

    results = app.compose_cv(lead="Acme", dry_run=True)

    assert len(results) == 1
    assert results[0].status == "error"
    assert results[0].dossier_failed is True, (
        "a dossier blocked by the SSRF guard, followed by a lost write race, must "
        "still surface in the 'N CV(s) composed blind' summary -- not be silently "
        "counted as a plain error")


class _PrecheckStore:
    """The minimum Store surface `run_one` touches before the gate: one shortlist note,
    the experience entries the bundle is built from, and a baseline. Nothing past the
    gate is reached, because the CV under test never clears it."""

    def __init__(self, note):
        self._note = note

    def read_leads(self, statuses=None):
        return [self._note]

    def read_experience_entries(self, verified_only=True):
        from tests.test_cv_engine import ENTRIES
        return ENTRIES

    def read_baseline(self):
        return "BASELINE"


def test_a_dry_run_applies_the_renderers_precheck_exactly_as_a_real_run_does(
        tmp_path, monkeypatch):
    """A dry run must not report a CV clean that a real run refuses.

    `compose_cv` used to pass `renderer=None` for a dry run, and the engine reaches the
    seam's optional grammar hook through `getattr(renderer, "precheck", None)` -- so
    `None` switched the hook off along with the renderer. Measured 2026-08-06 on ONE CV,
    gate-clean and unparseable by the `template` renderer's grammar: the dry run reported
    `status=dry-run, violations=[]` while the real run reported `status=skipped-gate`
    with a `FORMAT:` violation. The dry run IS the cheap preview, and it was false-greening
    exactly the input a real run bins.

    Asserted as EQUALITY between the two runs rather than against a literal, so the
    property is "the dry run and the real run agree" -- which is the claim -- rather than
    "the dry run happens to say this today".
    """
    from tests.test_cv_engine import (ENTRIES, FakeBackend, FakeCache, Note,
                                      PrecheckingRenderer, UNPARSEABLE_CV)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))

    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    app = Sluice(Config(), store=_PrecheckStore(note), renderer=PrecheckingRenderer())
    monkeypatch.setattr(app, "backend", lambda *a, **k: FakeBackend(UNPARSEABLE_CV))
    monkeypatch.setattr(app, "dossier_cache", lambda *a, **k: FakeCache())
    monkeypatch.setattr("sluice.cv.config.load_cv_config",
                        lambda: _precheck_cvcfg(tmp_path))

    dry, = app.compose_cv(lead="Acme", dry_run=True)
    real, = app.compose_cv(lead="Acme", dry_run=False)

    assert real.status == "skipped-gate", (
        "the real run stopped refusing this CV, so the two runs could agree while "
        "checking nothing -- the fixture, not the dry run, is what broke")
    assert any("FORMAT" in v for v in real.violations)
    assert dry.status == real.status
    assert dry.violations == real.violations
    assert ENTRIES, "the bundle had no source entries, so nothing was composed against"


def _precheck_cvcfg(tmp_path):
    """CvConfig with the ENTRIES prefix_map the UNPARSEABLE_CV fixture's [EF1] citations
    need, and output/served dirs under tmp_path so nothing can reach a real one."""
    from sluice.cv.config import CvConfig
    c = CvConfig()
    c.output_dir = str(tmp_path / "cvout")
    c.served_dir = str(tmp_path / "cvserved")
    c.prefix_map = {"Example Foundry": "EF"}
    # #99: off the shipped default (else the new pre-spend config refusal fires
    # before either test below ever reaches the precheck this helper exists to
    # test), and matching UNPARSEABLE_CV's own "JANE ROE" heading so the new
    # header-anchor STRUCTURAL guard does not ALSO fire and mask which check the
    # test is actually about.
    c.name = "Jane Roe"
    return c


def test_a_dry_run_survives_a_renderer_it_cannot_construct(tmp_path, monkeypatch, caplog):
    """...and SAYS the check was skipped, rather than quietly reverting to the old
    false-green.

    The fix above made a dry run resolve the renderer. A renderer whose construction
    fails -- an uninstalled WeasyPrint, a `cv.template` pointing at a file that is not
    there -- is a config problem with nothing to do with this CV, and a preview that
    costs nothing must not die on it. The warning is the load-bearing half: without it
    the degraded dry run is indistinguishable from a checked one, which is the defect
    being fixed rather than a smaller copy of it.
    """
    import logging

    from sluice.core.protocols import RenderError
    from tests.test_cv_engine import FakeBackend, FakeCache, Note, UNPARSEABLE_CV
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))

    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    app = Sluice(Config(), store=_PrecheckStore(note))
    monkeypatch.setattr(app, "backend", lambda *a, **k: FakeBackend(UNPARSEABLE_CV))
    monkeypatch.setattr(app, "dossier_cache", lambda *a, **k: FakeCache())
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: _precheck_cvcfg(tmp_path))

    def _boom(_cvcfg):
        raise RenderError("renderer 'template': cv.template is not a file")
    monkeypatch.setattr(app, "renderer", _boom)

    with caplog.at_level(logging.WARNING):
        result, = app.compose_cv(lead="Acme", dry_run=True)

    assert result.status == "dry-run", "an unbuildable renderer killed the dry run"
    assert any("precheck did NOT run" in r.getMessage() for r in caplog.records), (
        "the dry run silently skipped the format check -- a degraded preview that says "
        "nothing is the bug this whole change exists to remove")


def test_prep_all_shortlist_on_empty_vault_returns_a_prep_result_list(tmp_path, monkeypatch):
    # No "Job Applications/Job Leads" dir at all -- Vault.read_leads tolerates a
    # missing dir and returns []. all_shortlist must still come back as a (possibly
    # empty) list of PrepResult, never None and never raise, so an empty vault is a
    # legitimate "nothing to do" rather than an error.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    results = app.prep(all_shortlist=True)
    assert isinstance(results, list)
    assert all(isinstance(r, PrepResult) for r in results)


def test_record_unknown_lead_is_not_ok(tmp_path, monkeypatch):
    # apply is offline: record() must resolve straight to "no match" against the
    # store with no backend/dossier involved, dry_run or not.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    out = app.record(lead="ghost", dry_run=True)
    assert out["ok"] is False


def test_track_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    seen_db = _track_config(tmp_path, monkeypatch)
    app = Sluice(Config())
    monkeypatch.setattr(app, "backend", lambda *a, **k: object())  # avoid real creds
    rep = app.track(dry_run=True, client=_FakeGoogle(),
                    now_iso="2026-07-15T00:00:00+00:00")
    assert hasattr(rep, "msgs")
    assert not os.path.exists(seen_db)


def test_track_threads_the_track_config_into_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _track_config(tmp_path, monkeypatch)
    app = Sluice(Config())
    seen = {}
    monkeypatch.setattr(app, "backend", lambda role, **kw: seen.update(role=role, **kw) or object())
    app.track(dry_run=True, client=_FakeGoogle(), now_iso="2026-07-15T00:00:00+00:00")
    assert seen["primary_model"] == "claude-sonnet-4-5"   # track uses claude_max_model
    assert seen["effort"] == "medium"                     # ...and claude_max_effort
    assert seen["fallback_model"] == "deepseek-v4-flash"  # ...and cheap_model for fallback


def test_normalize_statuses_dry_run_on_empty_vault(tmp_path, monkeypatch):
    # Empty vault (no "Job Applications/Job Leads" dir at all) -- normalize_statuses
    # must still return a well-formed summary rather than raise, same "empty is a
    # legitimate no-op" contract the other operations give.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    app = Sluice(Config())
    summary = app.normalize_statuses(dry_run=True)
    assert {"changed", "unchanged", "unknown"} <= summary.keys()
    assert summary["changed"] == 0 and summary["unchanged"] == 0


def test_ingest_dry_run_writes_nothing(tmp_path, monkeypatch):
    # dry_run must route to JsonSink, never VaultSink -- so it must NOT construct
    # VaultSink or call self.store(). This test monkeypatches store() to raise if
    # called, then runs ingest with a source that yields a lead (so sink.write runs).
    # If store() were called, the AssertionError would fire, proving the bug.
    # fetcher=object() proves the override seam is used rather than a real Camofox.
    from sluice.core.leads import Lead
    from sluice.ingest.base import Search

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "seen.db"))
    monkeypatch.setenv("SLUICE_HEALTH", str(tmp_path / "health.json"))

    class _TestSource:
        id = "test"
        enabled = True
        kind = "browser"

        def searches(self):
            return [Search("test search", "http://example.invalid")]

        def fetch(self, ctx, search):
            return {"result": [{"title": "Engineer", "link": "http://x/1"}],
                    "landed": "http://x", "requested": "http://x"}

        def parse(self, raw, search):
            return [Lead(source=self.id, search=search.label, title="Engineer",
                        company="TestCorp", url="http://x/1")]

        def health_hint(self, raw):
            return {"count": 1, "landed_host": "x", "requested_host": "x", "markers": {}}

    app = Sluice(Config(), fetcher=object())
    # Monkeypatch store to raise if called -- discriminates that JsonSink is used
    def raising_store():
        raise AssertionError("dry_run must not construct VaultSink / call store()")
    monkeypatch.setattr(app, "store", raising_store)

    out = io.StringIO()
    report = app.ingest([_TestSource()], dry_run=True, out=out)

    # Assertions: dry_run completed without calling store(), wrote 1 lead to JsonSink
    assert report.sources[0].status == "ok"
    assert report.written == {"created": 1, "updated": 0, "skipped": 0}
    assert out.getvalue() != ""  # JSON lines were written to out
    assert not os.path.exists(os.path.join(str(tmp_path), "Job Applications", "Job Leads"))
