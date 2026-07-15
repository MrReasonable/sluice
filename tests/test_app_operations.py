from sluice.core.app import Sluice
from sluice.core.config import Config


class _FakeTab:
    def create_tab(self, url): return "t1"
    def evaluate(self, tab, js): return {"result": "JD BODY"}
    def close_tab(self, tab): return None


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
