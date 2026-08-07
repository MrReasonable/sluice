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
from sluice.ingest.base import Search


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
        # A real `Search`, not a dict: engine.py reads `search.label` on the failure
        # path, so a dict here would surface an AttributeError that masks the actual
        # cause whenever one of these tests exercises a fetch failure.
        return [Search(label="s", url="https://example.invalid/jobs")]

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
    # `fetch` is the plausible typo, not an arbitrary one: ARCHITECTURE.md labelled this
    # seam `fetch` while the key is `fetcher` (fixed in this same PR, once validation
    # made the mismatch load-bearing). Accepting it would drop the override forever.
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


def test_ingest_without_an_injected_sleep_hands_the_source_the_real_one(tmp_path, monkeypatch):
    # Pins the composition root, not the dataclass default. An earlier version of this
    # test asserted `Ctx(camofox=None).sleep is time.sleep` -- which tests ingest/base.py
    # and stays green under the plausible tidy-up of passing `sleep=self._sleep`
    # unconditionally. That variant sends None into Ctx on every uninjected run and
    # TypeErrors on the first `ctx.sleep(wait)`, so a production-breaking change looked
    # clean. Recording what the SOURCE actually receives is what closes that.
    import time
    _pin(tmp_path, monkeypatch)
    seen = []

    class _Recording(_SleepySource):
        def fetch(self, ctx, search):
            seen.append(ctx.sleep)
            return {"items": []}

    Sluice(Config(), fetcher=object(), store=_FakeStore()).ingest([_Recording()],
                                                                 dry_run=True)
    assert seen == [time.sleep], (
        "a source ran with something other than the real sleep; in production that is "
        "either a scraper reading a half-rendered DOM, or None and a TypeError")


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


# ── 0.5 resolve_host collaborator ─────────────────────────────────────────────

def test_a_typod_collaborator_names_collaborators_and_seams_separately():
    """ARCHITECTURE.md's "Injected collaborators -- the other kind of seam" section
    pre-registered this tightening for "a third collaborator". resolve_host is it.

    The obvious implementation -- widening UnknownAdapter's `known` -- would print
    the collaborators AS SEAMS, erasing the distinction that same section exists to
    draw and implying config keys that do not exist.
    """
    with pytest.raises(plugins.UnknownAdapter) as ei:
        Sluice(Config(), resolve_hosts=lambda h: [])
    msg = str(ei.value)
    assert "resolve_host" in msg and "sleep" in msg and "today" in msg
    assert "fetcher" in msg and "store" in msg
    assert "collaborator" in msg.lower()


def test_collaborators_tuple_matches_the_real_signature():
    """A stale tuple when a fourth collaborator lands would reinstate exactly the
    misdirection this tightening removes."""
    import inspect
    from sluice.core.app import Sluice, _COLLABORATORS
    kwonly = tuple(n for n, p in inspect.signature(Sluice.__init__).parameters.items()
                   if p.kind is p.KEYWORD_ONLY)
    assert _COLLABORATORS == kwonly


def test_an_unknown_seam_message_is_unchanged_for_existing_callers():
    e = plugins.UnknownAdapter("backend", "nope", ["a", "b"])
    assert str(e) == "unknown backend 'nope' (registered: a, b)"


def test_resolve_host_defaults_to_the_production_resolver(tmp_path, monkeypatch):
    """Without this, a wiring that ALWAYS used a fake would ship green.

    The previous version of this test asserted only `_resolve_host is None` and
    `callable(urlguard._resolve)` -- both trivially true, and both still true of a
    closure that never calls the module resolver at all. A regression that dropped
    the `self._resolve_host or urlguard._resolve` fallback in `dossier_cache`
    (core/app.py) entirely would still pass it. Drive a real cache miss through the
    PRODUCTION wiring -- `resolve_host=None`, the shape every `cli.py` construction
    uses, e.g. bare `Sluice(config)` -- and assert `urlguard._resolve` is the thing
    that actually gets called, with the lead's host. A fake Fetcher keeps this
    offline; the session-wide DNS guard in conftest.py is never reached because the
    sentinel replaces `_resolve` before any socket call would happen.
    """
    from sluice.core import urlguard
    from tests.harness.config import FIXTURE_ADDR

    assert Sluice(Config())._resolve_host is None
    assert Sluice(Config(), resolve_host=None)._resolve_host is None

    calls = []

    def _sentinel(host):
        calls.append(host)
        return [FIXTURE_ADDR]

    monkeypatch.setattr(urlguard, "_resolve", _sentinel)

    class _Tab:
        """A fake Fetcher -- no browser is touched by this test."""
        def create_tab(self, url):
            return "tab-1"

        def evaluate(self, tid, js):
            if js == "location.href":
                return {"result": "https://jobs.invalid/x"}
            return {"result": "JD BODY"}

        def close_tab(self, tid):
            pass

    # Bare `Sluice(config, fetcher=...)` -- resolve_host left at its default (None),
    # exactly like every real `cli.py` construction.
    app = Sluice(Config(), fetcher=_Tab())
    cache = app.dossier_cache(str(tmp_path), ttl_days=7)
    dossier = cache.get_or_build({"url": "https://jobs.invalid/x",
                                 "company": "Acme", "role": "Engineer"})

    assert dossier["jd"]["markdown"] == "JD BODY"
    assert calls, ("urlguard._resolve was never reached -- the production fallback "
                  "(`self._resolve_host or urlguard._resolve`) is dead")
    assert all(h == "jobs.invalid" for h in calls)


# ── #9: the staleness policy is built in the composition root ────────────────

def test_staleness_reads_config_and_CALLS_the_today_collaborator():
    """The wiring nothing else in the suite can see.

    Every other staleness test pins the OFF state, which a permanently-zero knob would
    also satisfy -- so dropping `ttl_days=self.config.lead_ttl_days` here would leave the
    whole feature inert with a green suite. This is the only test that catches it.

    It also pins that the clock is CALLED, not bound: `today` is a zero-arg callable, and
    binding it would reach date.fromisoformat(<function>) inside a gate.
    """
    s = Sluice(Config(lead_ttl_days=30), today=lambda: "2026-07-27")
    p = s.staleness()
    assert p.ttl_days == 30
    assert p.today == "2026-07-27"
    assert p.include_stale is False
    assert p.is_stale("2026-01-01") is True


def test_staleness_include_stale_is_per_invocation_not_config():
    s = Sluice(Config(lead_ttl_days=30), today=lambda: "2026-07-27")
    assert s.staleness(include_stale=True).blocks("2026-01-01") is False
    assert s.staleness().blocks("2026-01-01") is True


def test_staleness_defaults_off_with_an_unconfigured_config():
    s = Sluice(Config(), today=lambda: "2026-07-27")
    assert s.staleness().is_stale("2020-01-01") is False


# ── #9: the policy actually reaches each consumer ────────────────────────────
# Every OTHER staleness test pins the off state or drives an engine directly with an
# explicit policy, and a permanently-inert knob satisfies both. These are the only tests
# that redden if `policy=` is dropped between Sluice and an engine.

class _StaleNoteStore(_FakeStore):
    """A store holding exactly one ancient shortlist lead."""
    def __init__(self, **fm):
        super().__init__()
        base = {"status": "shortlist", "company": "Example Ltd", "role": "Example Role",
                "last_seen": "2026-01-01", "url": "https://example.invalid/1"}
        base.update(fm)
        self._note = type("N", (), {"fm": base, "ref": "r1",
                                    "slug": "Example Ltd - Example Role",
                                    "status": base["status"], "body": ""})()

    def read_leads(self, statuses=None):
        return [self._note]


def test_compose_cv_threads_the_policy_into_the_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SLUICE_CONFIG", "")
    s = Sluice(Config(lead_ttl_days=30), store=_StaleNoteStore(),
               backend=_Recorder(), renderer=object(), today=lambda: "2026-07-27")
    results = s.compose_cv(lead="Example Ltd - Example Role", dry_run=True)
    assert [r.status for r in results] == ["skipped-stale"]


def test_compose_cv_include_stale_reaches_the_engine(tmp_path, monkeypatch):
    # The lead gets far enough to attempt a DOSSIER FETCH, which is precisely what the
    # gate would have prevented -- so tripping the suite's DNS guard is the proof that
    # --include-stale was threaded. DnsUsedInTests subclasses BaseException exactly so
    # it cannot be swallowed by an `except Exception` on the way out.
    #
    # #99: off the shipped default, or the new pre-spend config refusal (which sits
    # AFTER the staleness gate this test bypasses via include_stale) would return
    # skipped-config before the dossier fetch this test exists to prove is reached
    # -- an unrelated config concern this test should not be blocked by.
    from sluice.cv.config import CvConfig
    from tests.conftest import DnsUsedInTests
    monkeypatch.setenv("SLUICE_CONFIG", "")
    monkeypatch.setattr("sluice.cv.config.load_cv_config",
                        lambda: CvConfig(name="Jane Roe"))
    s = Sluice(Config(lead_ttl_days=30), store=_StaleNoteStore(),
               backend=_Recorder(), renderer=object(), today=lambda: "2026-07-27")
    with pytest.raises(DnsUsedInTests):
        s.compose_cv(lead="Example Ltd - Example Role", dry_run=True,
                     include_stale=True)


def _stale_apply_store():
    """A store with one ancient shortlist lead carrying a complete apply packet, so the
    ONLY thing that can refuse it is staleness."""
    class S(_FakeStore):
        def __init__(self):
            super().__init__()
            fm = {"status": "shortlist", "company": "Example Ltd", "role": "Example Role",
                  "url": "https://example.invalid/1", "last_seen": "2026-01-01",
                  "tailored_cv": "CV_deadbeef.pdf (2026-01-02)"}
            self._note = type("N", (), {"fm": fm, "ref": "r1", "status": "shortlist",
                                        "slug": "Example Ltd - Example Role",
                                        "body": ""})()

        def read_leads(self, statuses=None):
            return [self._note]
    return S()


def test_prep_dry_run_and_real_run_BOTH_report_stale(monkeypatch):
    """The core/app.py:630 regression.

    Sluice.prep has THREE branches into selection, and the dry-run single-lead one calls
    select_one DIRECTLY, bypassing prep_one. Assert the shared OUTCOME, not merely that
    the two agree: "they agree" is also satisfied by the both-inert state, where dropping
    the policy from BOTH branches leaves them agreeing on `staged`.
    """
    monkeypatch.setenv("SLUICE_CONFIG", "")
    s = Sluice(Config(lead_ttl_days=30), store=_stale_apply_store(),
               today=lambda: "2026-07-27")
    dry = s.prep(lead="Example Ltd - Example Role", dry_run=True)[0]
    real = s.prep(lead="Example Ltd - Example Role")[0]
    assert dry.status == "skipped" and dry.reason == "stale"
    assert real.status == "skipped" and real.reason == "stale"


def test_prep_all_shortlist_reports_stale(monkeypatch):
    monkeypatch.setenv("SLUICE_CONFIG", "")
    s = Sluice(Config(lead_ttl_days=30), store=_stale_apply_store(),
               today=lambda: "2026-07-27")
    results = s.prep(all_shortlist=True)
    assert [(r.status, r.reason) for r in results] == [("skipped", "stale")]


def test_prep_include_stale_reaches_all_three_branches(monkeypatch):
    monkeypatch.setenv("SLUICE_CONFIG", "")
    s = Sluice(Config(lead_ttl_days=30), store=_stale_apply_store(),
               today=lambda: "2026-07-27")
    for kw in ({"lead": "Example Ltd - Example Role", "dry_run": True},
               {"lead": "Example Ltd - Example Role"},
               {"all_shortlist": True}):
        r = s.prep(include_stale=True, **kw)[0]
        assert r.reason != "stale", f"--include-stale was not threaded into {kw}"
