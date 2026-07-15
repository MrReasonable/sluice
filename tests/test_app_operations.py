import os

from sluice.apply.engine import PrepResult
from sluice.core.app import Sluice
from sluice.core.config import Config


class _FakeTab:
    def create_tab(self, url): return "t1"
    def evaluate(self, tab, js): return {"result": "JD BODY"}
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
    app = Sluice(Config(), fetcher=_FakeTab())
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
