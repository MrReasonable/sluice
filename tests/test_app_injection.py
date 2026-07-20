# tests/test_app_injection.py
"""The composition root's injection points.

Each of these covers a place where `Sluice` accepted (or could accept) an injected
dependency and then dropped it on the floor -- a capability advertised and silently
ignored, which is the quiet-wrong-default class this codebase most consistently
engineers out. They are unit tests deliberately: the e2e harness that consumes these
seams lands later, and a fix whose only witness lives in a future PR is not witnessed.
"""
import pytest

from sluice.core import plugins
from sluice.core.app import Sluice
from sluice.core.backends import BackendError
from sluice.core.config import Config


class _Recorder:
    """Stands in for a backend. Identity is all these tests check."""


class _FakeStore:
    def __init__(self):
        self.upserted = []

    def upsert(self, lead):
        self.upserted.append(lead)
        return "created"

    def ensure_stfolder(self):
        pass


class _SleepySource:
    """A source whose fetch does nothing but wait -- the shape that made a real
    BrowserListSource cost ~5s per search when Ctx.sleep was not threaded."""
    id = "sleepy"
    enabled = True
    kind = "list"

    def searches(self):
        return [{"label": "s", "url": "https://example.invalid/jobs"}]

    def fetch(self, ctx, search):
        ctx.sleep(0.25)
        return {"items": []}

    def parse(self, raw, search):
        return []

    def health_hint(self, raw):
        return {}


def _pin(tmp_path, monkeypatch):
    # Both default to cwd-relative paths, so an unpinned run writes into the repo.
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "seen.db"))
    monkeypatch.setenv("SLUICE_HEALTH", str(tmp_path / "health.db"))


# ── 0.1 backend override ──────────────────────────────────────────────────────

def test_backend_override_is_honoured():
    # Before this, __init__ accepted `backend=` and `backend()` never read
    # `_overrides`, so the override was silently ignored on every call.
    fake = _Recorder()
    assert Sluice(Config(), backend=fake).backend(
        "auto", primary_name="claude-max", primary_model="m", effort="low",
        host=None, claude_path=None, fallback_name="deepseek",
        fallback_model="c") is fake


def test_backend_override_does_not_bypass_the_role_guard():
    # THE placement test. Consulting `_overrides` at the top of backend() would make
    # this return the override instead of raising -- the fix for one quiet-wrong-default
    # installing another. The guard runs first; the override wins only afterwards.
    with pytest.raises(BackendError):
        Sluice(Config(), backend=_Recorder()).backend(
            "primry", primary_name="claude-max", primary_model="m", effort="low",
            host=None, claude_path=None, fallback_name="deepseek", fallback_model="c")


# ── 0.4 override-key validation ───────────────────────────────────────────────

def test_a_misspelled_seam_override_raises_rather_than_being_ignored():
    # `fetch` is the plausible typo, not an arbitrary one: docs/ARCHITECTURE.md calls
    # the seam `fetch` while the key is `fetcher`, so the documented word is the wrong
    # one. Accepting it would drop the override silently forever.
    with pytest.raises(plugins.UnknownAdapter) as e:
        Sluice(Config(), fetch=object())
    assert "fetcher" in str(e.value)   # the error lists the valid names


def test_a_correct_seam_override_is_still_accepted():
    store = _FakeStore()
    assert Sluice(Config(), store=store).store() is store


# ── 0.2 Ctx.sleep ─────────────────────────────────────────────────────────────

def test_ingest_threads_the_injected_sleep(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    waited = []
    s = Sluice(Config(), sleep=waited.append, fetcher=object(), store=_FakeStore())
    s.ingest([_SleepySource()], dry_run=True)
    assert waited == [0.25], "Ctx.sleep was not threaded; the source slept for real"


def test_ingest_without_an_injected_sleep_uses_the_real_one(tmp_path, monkeypatch):
    # The default must stay `time.sleep`: a source that skips its page-settle wait in
    # production is a scraper that reads a half-rendered DOM.
    import time

    from sluice.ingest.base import Ctx
    assert Ctx(camofox=None).sleep is time.sleep


# ── 0.3 the sink's clock ──────────────────────────────────────────────────────

def test_ingest_threads_the_injected_clock(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    store = _FakeStore()

    class _OneLead(_SleepySource):
        def parse(self, raw, search):
            from sluice.core.leads import Lead
            return [Lead(source="sleepy", search="s", title="Analyst",
                         company="Example Foundry", url="https://example.invalid/1")]

    s = Sluice(Config(), sleep=lambda _: None, today=lambda: "2026-01-02",
               fetcher=object(), store=store)
    s.ingest([_OneLead()])
    assert store.upserted, "nothing reached the store"
    assert store.upserted[0].last_seen == "2026-01-02", (
        "VaultSink's today= was not threaded, so nothing above the sink can move the clock")
