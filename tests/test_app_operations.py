import io
import os

from sluice.apply.engine import PrepResult
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.protocols import Store
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
    (`engine.run` calls search_messages then get_message; `calendar_sync.sync_event`
    calls list_events/find_events_by_private_property/insert_event/update_event/
    delete_event when an .ics attachment is present), all inert. A fake missing a called
    method surfaces as an AttributeError instead of exercising the test's actual
    assertion (tst-003).

    Named by FUNCTION, not by line number. The two line numbers this used to cite had both
    rotted -- they pointed at a hint string and a dataclass docstring -- and a reference that
    silently stops being true is worse than no reference."""
    auth_error = False

    def search_messages(self, *a, **k): return [], False
    def get_message(self, *a, **k): return {}
    def list_events(self, *a, **k): return [], False
    def find_events_by_private_property(self, *a, **k): return [], False
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
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    d = cache.get_or_build({"url": "https://example.invalid/job",
                            "company": "Acme", "title": titles[0]})
    assert d["jd"]["markdown"] == "JD BODY"


def test_dossier_cache_opens_no_browser_without_a_url(tmp_path, titles):
    class _Boom:
        def create_tab(self, url): raise AssertionError("must not be called")
    cache = Sluice(Config(), fetcher=_Boom()).dossier_cache(str(tmp_path), ttl_days=7,
                                                              min_jd_chars=0)
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
    calls = []
    # A LIST of calls, not a last-call-wins dict (#120): a second `self.backend()`
    # call -- the gated tier-3 resolution backend, built after the judge's -- would
    # otherwise silently overwrite a dict-shaped spy's `role` key and this
    # assertion would keep passing for the WRONG reason. The list makes each
    # call's own arguments inspectable regardless of how many `self.backend()`
    # calls a future change adds.
    monkeypatch.setattr(app, "backend", lambda role, **kw: calls.append((role, kw)))
    app.triage(backend_role="primary")
    assert calls[0][0] == "primary"
    assert calls[0][1]["primary_model"] == "claude-sonnet-4-5"   # triage uses claude_max_model
    assert calls[0][1]["effort"] == "medium"                     # ...and claude_max_effort
    assert calls[0][1]["fallback_model"] == "deepseek-v4-flash"  # ...and cheap_model for fallback
    assert len(calls) == 1   # company_resolve_llm defaults to False -- no second call


def _triage_llm_config(tmp_path, monkeypatch):
    """Point SLUICE_CONFIG at a triage: block with both #120 resolution knobs on,
    the same shape _track_config above uses for track's own seen_db/token_path."""
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text("triage:\n  company_resolve_fetch: true\n  company_resolve_llm: true\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))


def test_triage_builds_no_resolution_backend_when_the_llm_tier_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    calls = []
    monkeypatch.setattr(app, "backend", lambda role, **kw: calls.append((role, kw)) or None)
    app.triage()      # company_resolve_llm defaults to False -- no config file at all
    assert len(calls) == 1, "the LLM tier is off; only the judge backend should be built"


def test_triage_builds_the_resolution_backend_on_the_fallback_role_whatever_backend_was_asked_for(
        tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    _triage_llm_config(tmp_path, monkeypatch)
    app = Sluice(Config())
    calls = []
    monkeypatch.setattr(app, "backend", lambda role, **kw: calls.append((role, kw)) or None)
    app.triage(backend_role="primary")
    assert [role for role, kw in calls] == ["primary", "fallback"]
    assert calls[1][1]["fallback_model"] == "deepseek-v4-flash"   # cheap_model, always


def test_no_llm_threads_no_resolution_backend_into_the_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    _triage_llm_config(tmp_path, monkeypatch)     # the knob is ON...
    app = Sluice(Config())
    called = []
    monkeypatch.setattr(app, "backend", lambda *a, **k: called.append(k) or None)
    report = app.triage(no_llm=True)              # ...but --no-llm wins
    assert called == [], "no_llm must not construct ANY backend, judge or resolution"
    assert hasattr(report, "resolved")


def test_a_resolution_backend_that_fails_to_construct_degrades_rather_than_crashes(
        tmp_path, monkeypatch):
    from sluice.core.backends import BackendError
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    _triage_llm_config(tmp_path, monkeypatch)
    app = Sluice(Config())
    def _backend(role, **kw):
        if role == "fallback":
            raise BackendError("no api key")
        return None       # the judge role succeeds
    monkeypatch.setattr(app, "backend", _backend)
    report = app.triage()      # must not raise
    assert hasattr(report, "counts")


def test_triage_threads_get_source_into_engine_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    seen = {}
    def fake_run(vault, cfg, backend, cache, audit, **kw):
        seen.update(kw)
        from sluice.triage.engine import TriageReport
        return TriageReport()
    monkeypatch.setattr("sluice.triage.engine.run", fake_run)
    app.triage(no_llm=True)
    from sluice.ingest import sources
    assert seen["get_source"] is sources.get


def test_triage_threads_the_resolve_backend_into_engine_run(tmp_path, monkeypatch):
    """#120 whole-branch review: mutating `engine.run(..., resolve_backend=...)`
    (app.py) to `resolve_backend=None` currently survives the full suite unchanged
    -- meaning a future edit that silently drops or breaks this threading would
    leave tier 3 dark in production (config on, resolved/llm_calls all zero) with
    an all-green test suite. Distinct sentinels per role so the judge's backend and
    the resolution backend cannot be confused for each other by accident."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    _triage_llm_config(tmp_path, monkeypatch)
    app = Sluice(Config())
    judge_sentinel = object()
    resolve_sentinel = object()
    monkeypatch.setattr(
        app, "backend",
        lambda role, **kw: resolve_sentinel if role == "fallback" else judge_sentinel)
    seen = {}
    def fake_run(vault, cfg, backend, cache, audit, **kw):
        seen["judge_backend"] = backend
        seen.update(kw)
        from sluice.triage.engine import TriageReport
        return TriageReport()
    monkeypatch.setattr("sluice.triage.engine.run", fake_run)
    app.triage(backend_role="primary")
    assert seen["judge_backend"] is judge_sentinel
    assert seen["resolve_backend"] is resolve_sentinel


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

    def read_evidence(self, kind, verified_only=True):
        from tests.test_cv_engine import ENTRIES
        return ENTRIES if kind == "experience" else []

    def read_baseline(self):
        return "BASELINE"

    def read_candidate_profile(self):
        # #107: MUST-support on the real Store contract, so run_one calls this
        # unconditionally before the gate this fake exists to exercise -- a fake
        # missing it would AttributeError before either test under it ever reaches
        # the precheck/render-construction behaviour they actually assert on.
        from tests.test_cv_engine import DEFAULT_CANDIDATE
        return DEFAULT_CANDIDATE


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
    need, and output/served dirs under tmp_path so nothing can reach a real one.

    No identity override here (#133/#107: CvConfig no longer HAS name/contact fields
    to override) -- both tests below construct `Sluice(..., store=_PrecheckStore(note),
    ...)`, and `_PrecheckStore.read_candidate_profile()` already returns
    `test_cv_engine.DEFAULT_CANDIDATE` ("Jane Roe" / "+1 555 0100"), matching
    UNPARSEABLE_CV's own "JANE ROE" heading. That is what keeps the pre-spend
    skipped-config refusal and the #99/#100 header-anchor STRUCTURAL guard both
    quiet, so either test below actually reaches the precheck/render-construction
    behaviour it exists to check."""
    from sluice.cv.config import CvConfig
    c = CvConfig()
    c.output_dir = str(tmp_path / "cvout")
    c.served_dir = str(tmp_path / "cvserved")
    c.prefix_map = {"Example Foundry": "EF"}
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


def test_the_facade_method_names_stay_disjoint_from_the_store_member_names():
    """tests/test_mcpserver.py's isolation sweep matches a CALL by attribute name
    only. A facade method sharing a Store write method's name would be swept as a
    direct store write the moment mcpserver.py called it -- so add_evidence,
    list_evidence and verify_evidence_interactive must never collide with
    propose_evidence/read_evidence/read_pending_evidence/verify_evidence, the way
    create_lead/sign_off_cv already differ from upsert/sign_off."""
    store_members = {n for n in vars(Store) if not n.startswith("_")}
    # Enumerate, don't hand-list (this repo's standing rule) -- but a derivation
    # that silently finds nothing certifies nothing either, the same "all([]) is
    # True" trap CLAUDE.md names for a negative sweep. Guard the derivation itself.
    assert store_members, (
        "Store protocol introspection found no members -- the derivation above is "
        "broken, not the protocol; this guard would silently pass vacuously")
    facade = {"add_evidence", "list_evidence", "verify_evidence_interactive"}
    assert facade & store_members == set()
    # Bind the hand-listed literal to something REAL: deleting or renaming any of
    # the three facade methods must fail this test, not merely leave it agreeing
    # with itself. Without this, the whole test passed before the methods existed
    # (caught in review) and would pass again after they were removed.
    assert all(hasattr(Sluice, n) for n in facade)


def test_verify_interactive_promotes_only_what_the_human_accepts(tmp_path, monkeypatch):
    class _Asker:
        interactive = True

        def __init__(self, answers):
            self.answers, self.shown = list(answers), []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return self.answers.pop(0)

    # VAULT_DIR, not Config(vault_dir=...): stores/vault.py's _make reads the env
    # var AHEAD of the config field (test_vault_dir_env_var_beats_the_config_key),
    # and conftest's autouse fixture already exports VAULT_DIR for every test --
    # so a Config(vault_dir=...) argument here would be silently overridden and
    # read as isolation it does not actually provide. This is the idiom every
    # other test in this file already uses (see test_normalize_statuses_dry_run_
    # on_empty_vault above).
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    s.add_evidence(kind="skills", name="beta", fields={"Proficiency": "Q"})
    asker = _Asker([True, False])
    report = s.verify_evidence_interactive(kind="skills", asker=asker, today="2026-08-22")
    assert report["promoted"] == ["alpha"]
    assert report["skipped"] == ["beta"]
    assert len(asker.shown) == 2
    # `only` was never passed here, so `not_found` (#164, Ruling R11) must stay empty
    # -- it names a MISMATCH, not merely "the report doesn't mention this title".
    assert report["not_found"] == []


def test_verify_interactive_promotes_nothing_without_a_terminal(tmp_path, monkeypatch):
    class _NoInput:
        """Mirrors Task 7's real NoInputAsker shape: interactive=False AND a
        confirm() that exists and answers False. The gate's ONE unique effect is
        that the human is never asked at all -- confirm_calls stays 0 -- so the
        fake must be ABLE to answer, and record whether it was asked, or a
        mutant that deletes the `if not report["interactive"]` gate and instead
        falls through into the loop produces a BYTE-IDENTICAL report (every
        pending entry ends up "skipped" either way) and survives. A fake with no
        confirm() at all only pins that this fake's SHAPE is exercised, not that
        the human is never prompted -- proven by executing exactly that mutant
        against this test with the two fakes side by side (see the fix report).
        """
        interactive = False

        def __init__(self):
            self.shown = []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return False

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    asker = _NoInput()
    report = s.verify_evidence_interactive(kind="skills", asker=asker, today="2026-08-22")
    assert report["interactive"] is False and report["promoted"] == []
    # The load-bearing assertion: the human is never asked at all under a
    # non-interactive asker, not merely that nothing ends up promoted.
    assert asker.shown == []
    assert len(s.list_evidence(kind="skills", pending=True)) == 1
    assert report["not_found"] == []  # no `only` was passed -- there's nothing to miss


def test_verify_interactive_not_found_is_reported_even_without_a_terminal(tmp_path, monkeypatch):
    """Ruling R11 (#164): `not_found` is decided by the `only` FILTER, before
    `report["interactive"]` is even consulted -- a non-interactive asker must not
    swallow the "you named an id that isn't pending" signal the way it swallows
    promotion itself. Mirrors the class above's `_NoInput` fake so the same
    non-interactive shape is exercised here."""
    class _NoInput:
        interactive = False

        def __init__(self):
            self.shown = []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return False

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    asker = _NoInput()
    report = s.verify_evidence_interactive(kind="skills", asker=asker, only="ghost",
                                           today="2026-08-22")
    assert report["not_found"] == ["ghost"]
    assert report["skipped"] == []  # "ghost" never existed to BE skipped
    assert asker.shown == []


def test_verify_interactive_only_filters_without_ever_auto_promoting(tmp_path, monkeypatch):
    """`only` FILTERS which pending entries are offered for review; it must never
    act as an auto-yes. Pins three things: only the named entry is offered (the
    other is never shown to the asker at all); the un-offered entry is left
    untouched in the inbox; and the offered entry still requires a real confirm
    to be promoted -- declining it leaves it pending, not auto-verified."""
    class _Asker:
        interactive = True

        def __init__(self, answer):
            self.answer, self.shown = answer, []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return self.answer

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    s.add_evidence(kind="skills", name="beta", fields={"Proficiency": "Q"})

    # only="alpha": beta must never be offered, and must survive untouched.
    asker = _Asker(True)
    report = s.verify_evidence_interactive(kind="skills", asker=asker, only="alpha",
                                           today="2026-08-22")
    assert report["promoted"] == ["alpha"]
    assert len(asker.shown) == 1  # beta was never shown to the asker
    pending_titles = {e["title"] for e in s.list_evidence(kind="skills", pending=True)}
    assert pending_titles == {"beta"}  # untouched, still in the inbox
    assert report["not_found"] == []  # "alpha" DID match -- not a not-found case

    # only="beta", declined: not an auto-yes -- it stays pending.
    asker2 = _Asker(False)
    report2 = s.verify_evidence_interactive(kind="skills", asker=asker2, only="beta",
                                            today="2026-08-22")
    assert report2["promoted"] == []
    assert report2["skipped"] == ["beta"]
    assert report2["not_found"] == []  # "beta" matched and was offered -- just declined
    assert {e["title"] for e in s.list_evidence(kind="skills", pending=True)} == {"beta"}

    # A NAME THAT MATCHES NOTHING (Ruling R11, #164): the filtered queue is empty, but
    # the report is no longer indistinguishable from "nothing was pending at all" --
    # `not_found` names the id, which is what lets a caller print "no pending entry
    # matching 'ghost'" instead of a silent, all-empty success report.
    asker3 = _Asker(True)
    report3 = s.verify_evidence_interactive(kind="skills", asker=asker3, only="ghost",
                                            today="2026-08-22")
    assert report3 == {"promoted": [], "skipped": [], "unchanged": [], "failed": [],
                       "not_found": ["ghost"], "interactive": True}
    assert asker3.shown == []


def test_verify_interactive_only_accepts_a_name_not_only_its_reduced_slug(tmp_path, monkeypatch):
    """Task 7 review, IMPORTANT 3: `only` is documented (and typed by a user) as the
    same NAME `add --name` took, but a pending entry's `title` is already the REDUCED
    slug `propose_evidence` filed it under -- so before this fix, a name containing
    spaces or mixed case could never match its own stored entry, and `--id "Beta
    Thing"` after `add --name "Beta Thing"` silently reported not_found. Proposed
    exactly the way a real --name value would be typed; `only` is given in that same
    un-reduced shape, not pre-slugified by the test."""
    class _Asker:
        interactive = True

        def __init__(self):
            self.shown = []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return True

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="Beta Thing", fields={"Proficiency": "P"})
    asker = _Asker()
    report = s.verify_evidence_interactive(kind="skills", asker=asker, only="Beta Thing",
                                           today="2026-08-22")
    assert report["promoted"] == ["beta-thing"]  # the slug propose_evidence actually filed it at
    assert report["not_found"] == []
    assert len(asker.shown) == 1


def test_verify_interactive_only_matches_the_title_list_pending_actually_displays(tmp_path,
                                                                                  monkeypatch):
    """#164 whole-branch review, IMPORTANT 2 -- the facade half.

    `... list --pending` prints `entry["title"]`, the entry's real basename. For an entry
    a human dropped into `_inbox/` themselves -- a first-class workflow for this tool --
    that title is whatever they named the file, and NO reduction of it produces the
    stored value: before this fix `--id "My Entry"` reduced to `my-entry`, matched
    nothing, and reported `not_found` against a title the very same command had just
    displayed. So the filter now matches the title verbatim as well as reduced.

    Asserted through the same facade the CLI calls, and all the way to a promotion, so a
    filter that matched but a lookup that then re-reduced (the store-side half of the
    same bug) still fails here.
    """
    class _Asker:
        interactive = True

        def __init__(self):
            self.shown = []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return True

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    inbox = os.path.join(str(tmp_path), "Job Applications", "Skills Inventory", "_inbox")
    os.makedirs(inbox)
    with open(os.path.join(inbox, "My Entry.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: P\n---\nBody text.\n")
    displayed = [e["title"] for e in s.list_evidence(kind="skills", pending=True)]
    assert displayed == ["My Entry"], "precondition: this is what `list --pending` prints"

    asker = _Asker()
    report = s.verify_evidence_interactive(kind="skills", asker=asker, only="My Entry",
                                           today="2026-08-22")
    assert report["not_found"] == [], "--id did not match the title the listing displays"
    assert report["promoted"] == ["My Entry"]
    assert len(asker.shown) == 1
    assert [e["title"] for e in s.list_evidence(kind="skills")] == ["My Entry"]


def test_verify_interactive_isolates_a_failing_entry_and_still_offers_the_rest(tmp_path,
                                                                                monkeypatch):
    """#164 review, H2 -- the starvation this closes, reproduced at the facade.

    Measured before per-item isolation, with pending ['alpha', 'mike', 'november'] and
    `alpha` already taken in the citable set: `verify_evidence`'s exclusive create raised
    FileExistsError, it unwound past this loop, `report` was discarded whole, and
    `november` was never offered. Every later run aborted at the same entry, so the ONE
    path to citability starved permanently and `cv run` kept reporting `skipped-gate` --
    the fabrication verdict this feature exists to prevent.

    Three assertions, and each fails a different way against the old behaviour: the two
    healthy entries reach the human AND are promoted (the batch continued); the failing
    one is reported by NAME with a reason (the report survived); and the reason is words,
    not `[Errno 17]` (what actually reached the terminal before).
    """
    class _Asker:
        interactive = True

        def __init__(self):
            self.shown = []

        def confirm(self, prompt):
            self.shown.append(prompt)
            return True

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    base = os.path.join(str(tmp_path), "Job Applications", "Experience Library")
    os.makedirs(os.path.join(base, "_inbox"))
    # `alpha` verified already, and a same-named entry hand-dropped into the inbox --
    # both by hand, because `add` now refuses that clash up front (H2b). A human editing
    # the vault is a first-class workflow here, so this state is genuinely reachable.
    with open(os.path.join(base, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Alpha\nverified: 2026-01-01\n---\nAlready citable.\n")
    with open(os.path.join(base, "_inbox", "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Alpha\n---\nA clashing proposal.\n")
    s.add_evidence(kind="experience", name="mike", fields={"Company": "Beta"})
    s.add_evidence(kind="experience", name="november", fields={"Company": "Gamma"})

    asker = _Asker()
    report = s.verify_evidence_interactive(kind="experience", asker=asker,
                                           today="2026-08-22")

    assert report["promoted"] == ["mike", "november"], "the batch aborted at the failure"
    assert len(asker.shown) == 3, "an entry after the failing one was never offered"
    assert [t for t, _ in report["failed"]] == ["alpha"]
    [(_, reason)] = report["failed"]
    assert "already exists" in reason
    assert "Errno" not in reason, "an errno reached the caller instead of a named reason"


def test_list_evidence_excludes_an_unverified_entry_from_the_citable_listing(tmp_path,
                                                                              monkeypatch):
    """#164 review, H5. `Sluice.list_evidence(pending=False)` passes
    `verified_only=True` to the store, and flipping that flag to False survived every
    test in the suite: the only unverified entries any fixture had sat in `_inbox/`,
    which `read_evidence` cannot see at EITHER setting, so the flag was asserted by
    location rather than by the filter it names.

    An unverified `.md` sitting directly in the kind directory is not exotic -- it is
    the ordinary state of a note a human wrote in Obsidian and has not run `verify`
    over, and `verify_evidence` is the only thing that ever stamps the key. What this
    flag decides is what the MCP `list_evidence` tool reports to an LLM as citable, so
    a flipped flag hands a composer entries the CV fabrication gate would reject and
    calls them evidence.

    Asserted BOTH ways round, because a filter that returns nothing at all would
    satisfy the exclusion half alone.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    base = os.path.join(str(tmp_path), "Job Applications", "Skills Inventory")
    os.makedirs(base)
    with open(os.path.join(base, "citable.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: P\nverified: 2026-01-01\n---\nReviewed.\n")
    with open(os.path.join(base, "hand-written.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: Q\n---\nNever run through verify.\n")

    titles = [e["title"] for e in s.list_evidence(kind="skills")]
    assert titles == ["citable"], "an unverified entry was listed as citable"


def test_verify_interactive_abstains_when_the_entry_is_edited_while_it_is_being_reviewed(
        tmp_path, monkeypatch):
    """#164 review, H4 -- the review-then-promote compare-and-set, driven end to end.

    Nothing in the suite ever produced a non-empty `report["unchanged"]` before this,
    so the whole abstention arm above the store was inert: re-deriving `reviewed` AT the
    store call -- which is exactly the bug the CAS exists to prevent, since it would
    promote whatever is on disk NOW rather than what the human approved -- survived
    mutation with the suite green.

    The arm is reachable on the real path, not a contrivance: the human sits inside
    `asker.confirm` reading the entry while Obsidian is free to save over it, which is
    what this asker does. `docs/ARCHITECTURE.md` records the same threat model for
    `_cas_write` (#16): the primary writer sluice races is a human in their editor.

    Both halves are asserted. `unchanged` naming the entry is the report half; the entry
    still sitting in the inbox, unstamped, is the half that would catch a promotion that
    reported the abstention and wrote anyway.
    """
    class _EditingAsker:
        """Answers yes -- but rewrites the entry first, the way an editor saving over it
        mid-review would."""
        interactive = True

        def __init__(self, target):
            self.target, self.shown = target, []

        def confirm(self, prompt):
            self.shown.append(prompt)
            with open(self.target, "a", encoding="utf-8") as fh:
                fh.write("\nan edit made while the human was reading\n")
            return True

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    inbox = os.path.join(str(tmp_path), "Job Applications", "Skills Inventory", "_inbox")

    asker = _EditingAsker(os.path.join(inbox, "alpha.md"))
    report = s.verify_evidence_interactive(kind="skills", asker=asker, today="2026-08-22")

    assert report["unchanged"] == ["alpha"], "the abstention was not reported"
    assert report["promoted"] == [] and report["failed"] == []
    assert s.list_evidence(kind="skills") == [], "unreviewed content became citable"
    assert [e["title"] for e in s.list_evidence(kind="skills", pending=True)] == ["alpha"]


def test_verify_interactive_isolates_an_entry_that_vanished_mid_review(tmp_path, monkeypatch):
    """The READ half of the same isolation, and it needs its own row: the entry text is
    fetched OUTSIDE the `verify_evidence` call, so an isolation wrapped around the store
    call alone would leave this arm aborting the batch exactly as before.

    The deletion happens from inside `confirm` -- i.e. while the human is sitting at the
    prompt for the PREVIOUS entry -- because that is the only way this state is reachable:
    `read_pending_evidence` reads every entry's body to build the listing, so an entry
    already unreadable when the queue was built fails the whole listing instead (which is
    correct, and is `test_verify_names_an_unreadable_pending_entry_instead_of_crashing`'s
    territory). Deleting a note in Obsidian mid-review is the real-world shape of this.
    """
    class _DeletingAsker:
        """Answers yes, and deletes `victim` the first time it is asked."""
        interactive = True

        def __init__(self, victim):
            self.victim, self.shown = victim, []

        def confirm(self, prompt):
            self.shown.append(prompt)
            if os.path.exists(self.victim):
                os.unlink(self.victim)
            return True

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    s.add_evidence(kind="skills", name="bravo", fields={"Proficiency": "Q"})
    inbox = os.path.join(str(tmp_path), "Job Applications", "Skills Inventory", "_inbox")

    asker = _DeletingAsker(os.path.join(inbox, "bravo.md"))
    report = s.verify_evidence_interactive(kind="skills", asker=asker, today="2026-08-22")

    assert report["promoted"] == ["alpha"], "the batch aborted on the vanished entry"
    assert [t for t, _ in report["failed"]] == ["bravo"]
    [(_, reason)] = report["failed"]
    assert "no longer in the inbox" in reason
    assert "Errno" not in reason
    # The human is never asked about an entry whose bytes could not be read -- confirming
    # content nobody could be shown is what the compare-and-set exists to prevent.
    assert len(asker.shown) == 1


def test_verify_interactive_only_still_reports_not_found_for_an_unreducible_value(tmp_path,
                                                                                 monkeypatch):
    """The fallback half of the same fix: a value that reduces to nothing at all (here,
    pure punctuation) must not raise `evidence_slug`'s ValueError out of this filter --
    it degrades to comparing the raw value, which simply matches nothing, the same
    `not_found` outcome as any other non-matching id."""
    class _Asker:
        interactive = True

        def confirm(self, prompt):
            return True

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    s = Sluice(Config())
    s.add_evidence(kind="skills", name="alpha", fields={"Proficiency": "P"})
    report = s.verify_evidence_interactive(kind="skills", asker=_Asker(), only="###",
                                           today="2026-08-22")
    assert report["not_found"] == ["###"]
    assert report["promoted"] == []
