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
from sluice.core.protocols import CandidateProfile, UpsertResult
from sluice.ingest.base import Search


class _Recorder:
    """Stands in for a backend. Identity is all these tests check."""


class _FakeStore:
    def __init__(self):
        self.upserted = []

    def upsert(self, lead):
        self.upserted.append(lead)
        return UpsertResult(outcome="created", slug=f"{lead.company} - {lead.title}")

    def ensure_stfolder(self):
        pass

    def read_candidate_profile(self):
        # MUST-support (#107), not optional -- Sluice.prep now calls this
        # unconditionally, so every _FakeStore subclass needs an answer even when
        # the test using it has nothing to say about candidate data. A blank
        # profile is the documented abstain, the same shape a store with no
        # Candidate Profile note returns for real.
        return CandidateProfile()


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
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
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


    def read_baseline(self):
        # MUST-support Store members (core/protocols.py: "NOT optional like
        # preflight/precheck"), so the double implements them rather than cv/engine.py
        # treating a required member as optional -- the precedent is _FakeStore gaining
        # read_candidate_profile when Sluice.prep began calling it unconditionally.
        return "# CV\n"

    def read_evidence(self, kind, verified_only=True):
        return [{"title": "alpha", "verified": "2026-09-03"}] if kind == "experience" else []
    def read_candidate_profile(self):
        # #107: MUST-support -- test_compose_cv_include_stale_reaches_the_engine
        # bypasses the staleness gate via include_stale, so run_one reaches the
        # identity gate immediately after; a blank/missing answer here would
        # refuse skipped-config before the dossier fetch that test exists to prove
        # is reached, an unrelated config concern that test should not be blocked
        # by. test_compose_cv_threads_the_policy_into_the_engine never reaches this
        # far (it stops at skipped-stale), so this is inert for it either way.
        from tests.test_cv_engine import DEFAULT_CANDIDATE
        return DEFAULT_CANDIDATE


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
    # #107: _StaleNoteStore.read_candidate_profile answers with a fully declared
    # identity (see its own comment), so the pre-spend identity gate (which sits
    # AFTER the staleness gate this test bypasses via include_stale) does not
    # return skipped-config before the dossier fetch this test exists to prove is
    # reached -- an unrelated config concern this test should not be blocked by.
    from tests.conftest import DnsUsedInTests
    monkeypatch.setenv("SLUICE_CONFIG", "")
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

        def read_baseline(self):
            # MUST-support Store members (core/protocols.py: "NOT optional like
            # preflight/precheck"), so the double implements them rather than cv/engine.py
            # treating a required member as optional -- the precedent is _FakeStore gaining
            # read_candidate_profile when Sluice.prep began calling it unconditionally.
            return "# CV\n"

        def read_evidence(self, kind, verified_only=True):
            return [{"title": "alpha", "verified": "2026-09-03"}] if kind == "experience" else []
    return S()


def test_prep_dry_run_and_real_run_BOTH_report_stale(monkeypatch):
    """The core/app.py::prep regression.

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


# ── #107/#133: the candidate profile and the clock are each resolved ONCE per
# prep() call, not once per lead ────────────────────────────────────────────

def _app(store, **kw):
    return Sluice(Config(), store=store, **kw)


class _CountingStore:
    """Wraps a real Vault and counts `read_candidate_profile` calls. A contents-only
    assertion on the resulting packets looks identical whether the profile was read
    once for the whole prep() call or once per eligible lead -- counting is the only
    way to tell the two apart, and `--all-shortlist` is exactly where the difference
    is real: a per-lead re-fetch is N vault reads, and could let two leads in one
    batch disagree if the note changes mid-run. Every other method is proxied
    straight through via __getattr__, so this store is otherwise indistinguishable
    from the Vault it wraps."""
    def __init__(self, inner):
        self._inner = inner
        self.candidate_reads = 0

    def read_candidate_profile(self):
        self.candidate_reads += 1
        return self._inner.read_candidate_profile()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _vault_with_shortlist(tmp_path, n, candidate_profile_fm=None):
    """A real Vault with `n` shortlisted, apply-eligible leads: a resolvable
    tailored_cv, an http(s) url, and (with Config()'s default lead_ttl_days=0,
    staleness OFF) never stale. Eligible, not merely present, is what matters here
    -- an ineligible lead is skipped before build_packet is ever called, which
    would make a per-lead profile re-fetch invisible to these tests.

    `served_dir`/`camofox_upload_dir` are two of the deliberate cwd-relative
    exceptions (core/paths.py) -- callers must `monkeypatch.chdir(tmp_path)` before
    calling `.prep()` so `./cv-served` resolves under the sandboxed tmp_path rather
    than wherever pytest happened to be invoked from.

    Slugs: the first lead is always "example-lead" (a fixed single-lead target for
    these tests' `lead=` cases); the rest are "example-extra-N", chosen so
    slug_matches' substring rule never pairs "example-lead" against one of them --
    "example-lead" is not a substring of "example-extra-1", so a single-lead
    `prep(lead="example-lead")` call stays unambiguous regardless of `n`.

    `candidate_profile_fm`, when given, is written verbatim as the frontmatter
    body of Job Applications/Candidate Profile.md (CANDIDATE_PROFILE_RELPATH).
    Only the test that checks the packet's CONTENT (not just the read count)
    supplies one -- every other caller leaves it None, so
    Vault.read_candidate_profile() abstains with the same all-blank
    CandidateProfile it always has."""
    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH
    from sluice.core.vault import Vault
    root = tmp_path / "vault"
    leads_dir = root / "Job Applications" / "Job Leads"
    leads_dir.mkdir(parents=True)
    served = tmp_path / "cv-served"
    served.mkdir()
    (served / "CV_deadbeef.pdf").write_bytes(b"%PDF-1.4\nx")
    for i in range(n):
        slug = "example-lead" if i == 0 else f"example-extra-{i}"
        fm = (
            'company: "Example Co"\nrole: "Example Role"\nstatus: shortlist\n'
            f'url: "https://example.invalid/{i}"\n'
            'tailored_cv: CV_deadbeef.pdf (2026-07-09)\n'
        )
        (leads_dir / f"{slug}.md").write_text("---\n" + fm + "---\n\nBODY\n")
    if candidate_profile_fm is not None:
        profile_path = root / CANDIDATE_PROFILE_RELPATH
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text("---\n" + candidate_profile_fm + "---\n\nbody\n")
    return Vault(str(root))


def test_the_candidate_profile_is_read_exactly_once_per_prep_call(tmp_path, monkeypatch):
    """--all-shortlist is the discriminating case: preview_all loops N eligible
    leads, so if profile/today were (wrongly) re-read inside that loop rather than
    resolved once by Sluice.prep and passed in, this count would read 4, not 1 --
    1 from Sluice.prep's own read plus one per eligible lead re-fetched.

    Also asserts the SCOPE: the count alone is satisfied just as well by a fixture
    that (through a served_dir/tailored_cv/slug drift) yields zero eligible leads,
    which would make the loop this test exists to guard never run at all. Pinning
    all three leads as "previewed" is what proves the loop actually executed."""
    monkeypatch.chdir(tmp_path)
    store = _CountingStore(_vault_with_shortlist(tmp_path, n=3))
    app = _app(store)
    results = app.prep(all_shortlist=True)
    assert [r.status for r in results] == ["previewed"] * 3
    assert store.candidate_reads == 1


@pytest.mark.parametrize("kwargs", [
    {"lead": "example-lead"},
    {"lead": "example-lead", "dry_run": True},
    {"all_shortlist": True},
])
def test_every_prep_call_path_reads_the_profile_once(tmp_path, monkeypatch, kwargs):
    monkeypatch.chdir(tmp_path)
    store = _CountingStore(_vault_with_shortlist(tmp_path, n=2))
    results = _app(store).prep(**kwargs)
    # SCOPE, not just the count: a fixture that silently yields zero eligible
    # leads (e.g. slug_matches unexpectedly pairing "example-lead" with
    # "example-extra-1", or the tailored_cv/served_dir wiring drifting) would
    # satisfy candidate_reads == 1 just as well by never reaching a lead at all.
    assert results[0].status in {"staged", "previewed"}
    assert store.candidate_reads == 1


def test_the_clock_callable_is_invoked_once_not_twice(tmp_path, monkeypatch):
    """prep() already resolves the clock inside self.staleness() to build the
    frozen StalenessPolicy. Resolving it a SECOND time beside that call -- rather
    than reading policy.today back -- could straddle midnight and give one
    prep() call two different dates."""
    monkeypatch.chdir(tmp_path)
    calls = []
    def clock():
        calls.append(1)
        return "2026-08-19"
    app = _app(_vault_with_shortlist(tmp_path, n=1), today=clock)
    app.prep(all_shortlist=True)
    assert len(calls) == 1


def test_the_real_profile_and_clock_reach_the_packet(tmp_path, monkeypatch):
    """The three tests above pin how many times the profile is read and the
    clock is called; two of them (since I1's fix) also pin that the fixture
    genuinely produces eligible leads -- but none of the three checks WHICH
    value reached the packet.
    A mutation that quietly restores Task 4's placeholder behaviour (a blank
    CandidateProfile() at all three of Sluice.prep's branch sites, or the
    epoch date in place of policy.today) would leave every one of them green:
    the store is still read exactly once, and the clock is still called
    exactly once -- just with the answer thrown away downstream. This is the
    one test that reads the packet's CONTENT, closing both halves at once: a
    declared passthrough field pins the profile, and `age` pins the clock
    (only `age_from_dob` consumes `today`, so it is the one value that can
    witness which date actually reached build_packet)."""
    monkeypatch.chdir(tmp_path)
    profile_fm = 'town: "Example Town"\ndate_of_birth: 1990-06-15\n'
    store = _vault_with_shortlist(tmp_path, n=1, candidate_profile_fm=profile_fm)
    app = _app(store, today=lambda: "2026-08-19")
    results = app.prep(lead="example-lead")
    assert results[0].status == "staged"
    pkt = results[0].packet
    assert pkt["town"] == "Example Town"
    assert pkt["age"] == 36
