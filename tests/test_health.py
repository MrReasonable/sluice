from statistics import median

from sluice.core.health import HealthStore, detect_drift


def test_zero_count_flags_drift():
    assert detect_drift("s", 0, {}, baseline=10) == "zero"


def test_offdomain_redirect_flags():
    assert detect_drift("s", 5, {"requested_host": "cord.co", "landed_host": "cord.com"}, 5) == "redirect"


def test_blocked_flags():
    assert detect_drift("s", 5, {"blocked": True}, 5) == "blocked"


def test_drop_below_40pct_of_baseline():
    assert detect_drift("s", 3, {}, baseline=10) == "drop"   # 3 < 0.4*10
    assert detect_drift("s", 5, {}, baseline=10) is None      # 5 >= 4


def test_precedence_an_explanation_beats_bare_zero():
    """REVERSES the original `zero > redirect` precedence (2026-08-15, user-confirmed).

    The old rule returned "zero" here, discarding the redirect the caller had already
    measured. On 2026-08-15 that cost three heavyweight sources: linkedin, jobserve and
    indeed each reported `drift=zero` for eight-plus runs and auto-retired, when the real and
    single cause was a Camofox profile with no authenticated cookies. "zero" is the one
    classification a human cannot act on, so it must be the LAST resort, not the first.

    An unexplained zero is still "zero" -- see test_zero_count_flags_drift above.
    """
    assert detect_drift("s", 0, {"requested_host": "a", "landed_host": "b"}, 10) == "redirect"


def test_healthy_returns_none():
    assert detect_drift("s", 10, {"requested_host": "x", "landed_host": "x"}, 10) is None


def test_record_persists_and_reloads(tmp_path):
    p = str(tmp_path / "h.json")
    h = HealthStore(p)
    h.record("s", 5, {})
    h.record("s", 7, {})
    assert HealthStore(p).counts("s") == [5, 7]


def test_baseline_is_median_of_last_7(tmp_path):
    h = HealthStore(str(tmp_path / "h.json"))
    for c in [10, 2, 4, 6, 8, 100, 3, 5]:   # 8 runs; last 7 = [2,4,6,8,100,3,5]
        h.record("s", c, {})
    assert h.baseline("s") == median([2, 4, 6, 8, 100, 3, 5])  # 5


def test_auto_retire_after_three_zero_runs(tmp_path):
    h = HealthStore(str(tmp_path / "h.json"))
    for _ in range(3):
        h.record("s", 0, {})
    assert h.should_retire("s", threshold=3)


def test_not_retired_when_a_recent_run_had_hits(tmp_path):
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 0, {})
    h.record("s", 5, {})   # recovered
    h.record("s", 0, {})
    assert not h.should_retire("s", threshold=3)


def test_error_run_counts_as_dead(tmp_path):
    h = HealthStore(str(tmp_path / "h.json"))
    for _ in range(3):
        h.record("s", 0, {"error": "boom"})
    assert h.should_retire("s", threshold=3)


def test_rate_high_water_is_a_sticky_max_not_derived_from_the_capped_window(tmp_path):
    # THE choice that makes #156's `blank` detector durable rather than self-silencing.
    # Seed one good run then six blank ones -- if the high-water were derived from the
    # capped `runs` window (as `baseline` is), it would decay toward 0 once the good run
    # scrolled out; a SEPARATE sticky field must not.
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 25, {"company_rate": 0.96})
    for _ in range(6):
        h.record("s", 25, {"company_rate": 0.0})
    assert h.rate_highs("s")["company_rate"] == 0.96


def test_rate_high_water_survives_past_the_30_run_retention_cap(tmp_path):
    # The regression test for the self-silencing bug the sticky design exists to avoid:
    # derived-from-window would go permanently silent once the single healthy run ages
    # out of the last 30. 40 runs total -- one good, then 39 more than the window keeps.
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 25, {"company_rate": 0.96})
    for _ in range(39):
        h.record("s", 25, {"company_rate": 0.0})
    assert len(h.counts("s", n=100)) == HealthStore._KEEP, "the rolling window IS capped"
    assert h.rate_highs("s")["company_rate"] == 0.96, (
        "the high-water decayed once the healthy run left the retained window"
    )


def test_rate_high_water_ignores_a_zero_count_run(tmp_path):
    # A zero-yield run carries no rate to have been high OR low -- see `_lead_rates`'s
    # count>0 discipline in ingest/engine.py. Confirmed at the STORE layer too: a stray
    # `company_rate` key on a count==0 record (should never happen, but health is
    # best-effort) must not corrupt the high-water.
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 0, {"company_rate": 0.99})
    assert h.rate_highs("s") == {}


def test_rate_highs_is_empty_with_no_history(tmp_path):
    # The abstain case: a source with no recorded runs at all has nothing to compare
    # against, and MUST read as "cannot evaluate", never as a high-water of 0.0.
    assert HealthStore(str(tmp_path / "h.json")).rate_highs("s") == {}


def test_prior_rate_reads_the_most_recently_recorded_run(tmp_path):
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 25, {"company_rate": 0.9})
    h.record("s", 25, {"company_rate": 0.1})
    assert h.prior_rate("s", "company_rate") == 0.1


def test_prior_rate_is_none_with_no_history(tmp_path):
    assert HealthStore(str(tmp_path / "h.json")).prior_rate("s", "company_rate") is None


def test_prior_rate_is_none_when_the_prior_run_carried_no_such_signal(tmp_path):
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 3, {})   # under the row floor upstream -- no rate key at all
    assert h.prior_rate("s", "company_rate") is None


def test_prior_rate_ignores_a_zero_count_run_even_with_a_stray_signal(tmp_path):
    # Review follow-up: `rate_highs`/`record` already apply this count>0 discipline
    # (see `test_rate_high_water_ignores_a_zero_count_run` above), but `prior_rate` read
    # `runs[-1]` unconditionally -- a stray rate key on a zero-count run (should never
    # happen, but health is best-effort) would supply the "prior run was also low" half
    # of `blank`'s 2-consecutive-run streak, and `blank` is in BREAKER_REASONS.
    h = HealthStore(str(tmp_path / "h.json"))
    h.record("s", 25, {"company_rate": 0.9})
    h.record("s", 0, {"company_rate": 0.0})
    assert h.prior_rate("s", "company_rate") is None


def test_the_existing_median_baseline_DOES_decay_unlike_the_sticky_high_water(tmp_path):
    # Contrast test, pinning the CLAIM that motivated the sticky design in the first
    # place: `baseline`'s median-of-7 follows a sustained rot down, so a fifty-to-ten
    # decline never trips `drop` on any single step once seven runs have banked at the
    # lower level. If this weren't true there would be no reason for `rate_highs` to be
    # anything other than another `baseline`-shaped median.
    h = HealthStore(str(tmp_path / "h.json"))
    for c in [50, 50, 50, 50, 50, 50, 50, 30, 20, 15, 10, 10, 10, 10]:
        h.record("s", c, {})
    assert h.baseline("s") == 10.0, "the median has fully followed the decline"


def test_health_report_reflects_the_real_registry_sorted_by_id(tmp_path):
    """AT LEAST TWO real sources, so the sort claim is falsifiable -- with one element,
    a sorted list and an unsorted list are byte-identical and this would pass vacuously
    even with the sort call deleted."""
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.ingest import sources as registry

    ids = sorted(s.id for s in registry.all_sources())
    assert len(ids) >= 2, "the real source registry enumerated fewer than two sources"
    first, second = ids[0], ids[-1]

    # HealthStore() resolves via SLUICE_HEALTH, sandboxed into tmp_path by the autouse
    # _pin_paths fixture in tests/conftest.py -- no explicit path needed.
    h = HealthStore()
    h.record(first, 5)
    h.record(second, 0)
    h.record(second, 0)
    h.record(second, 0)  # three zero runs -> should_retire

    report = Sluice(Config()).health_report()
    got = [s for s in report if s.id in (first, second)]
    assert [s.id for s in got] == sorted(s.id for s in got), \
        "health_report() must be sorted by source id"

    by_id = {s.id: s for s in report}
    assert by_id[first].baseline == 5.0
    assert by_id[first].recent == [5]
    assert by_id[first].should_retire is False
    assert by_id[second].should_retire is True
    assert all(isinstance(s.kind, str) and s.kind for s in report), \
        "every SourceHealth must carry its source's real kind"


# ── #169 §2: per-source unjudgeable rate ──────────────────────────────────────

def _write_source_note(leads_dir, *, source, status, company, role="Analyst"):
    """A raw lead note carrying a `source` field -- `read_leads` reads it back via
    `LeadNote.fm`, and `health_report`'s per-source rate keys off exactly that field
    (`sluice/core/leads.py`'s `Lead.source`, mirrored into the note)."""
    leads_dir.mkdir(parents=True, exist_ok=True)
    (leads_dir / f"{company} - {role}.md").write_text(
        f'---\ncompany: "{company}"\nrole: "{role}"\nstatus: {status}\nsource: {source}\n'
        "---\n\nbody\n")


def test_the_unjudgeable_rate_counts_numerator_and_denominator_at_the_SAME_stage(tmp_path):
    """The rate's two terms must come from the SAME lifecycle stage.

    `read_leads()` unfiltered is ALL-TIME (dismiss, applied, every terminal included), so
    an all-time denominator would dilute a source that is 100% broken TODAY by its entire
    history -- the classification could then structurally never fire on the case it exists
    to detect. Both terms come from the shared `DEFAULT_TRIAGE_STATUSES` selection instead:
    one point in the LIFECYCLE, not merely one point in time (the #156 mistake -- a ratio's
    numerator and denominator drawn from different populations -- in a new costume)."""
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    from sluice.ingest import sources as registry

    ids = sorted(s.id for s in registry.all_sources())
    assert len(ids) >= 2, "the real source registry enumerated fewer than two sources"
    broken, quiet = ids[0], ids[-1]

    leads = tmp_path / "vault" / "Job Applications" / "Job Leads"
    _write_source_note(leads, source=broken, status="new", company="Alpha")
    for i in range(3):
        _write_source_note(leads, source=broken, status="unjudgeable", company=f"Beta{i}")
    for i in range(3):
        # dismiss is TRIAGE_OWNED but outside DEFAULT_TRIAGE_STATUSES -- a real source's
        # entire dismissed history must not dilute today's rate, or a source that is 100%
        # broken today could never read as such once it has accumulated any history.
        _write_source_note(leads, source=quiet, status="dismiss", company=f"Gamma{i}")

    report = {h.id: (h.unjudgeable, h.selected)
              for h in Sluice(Config()).health_report(include_leads=True)}
    assert report[broken] == (3, 4)
    assert report[quiet] == (0, 0), "dismissed leads are not in the selection set"


class _CountingVault:
    """A minimal Store double recording `read_leads` calls. Nothing else about the Store
    contract (writes, preflight) matters to what the two tests below check."""
    def __init__(self):
        self.read_leads_calls = 0

    def read_leads(self, statuses=None):
        self.read_leads_calls += 1
        return []


def test_health_report_does_no_vault_io_by_default():
    """`job-sluice health` and the MCP `health` tool both call `health_report()` often and
    cheaply; the default must keep that cost. In the real implementation `self.store()` is
    reached only inside the `include_leads` branch, so this double is never even
    constructed -- this test pins the outward promise a caller actually depends on
    (zero reads), not the internal mechanism that happens to deliver it."""
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    vault = _CountingVault()
    Sluice(Config(), store=vault).health_report()
    assert vault.read_leads_calls == 0


def test_health_report_include_leads_walks_the_vault_exactly_once():
    """A vault with N registered sources must cost ONE `read_leads()` pass, not N -- the
    rates dict is built up front and looked up per source inside `_one`, never re-walked."""
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    vault = _CountingVault()
    Sluice(Config(), store=vault).health_report(include_leads=True)
    assert vault.read_leads_calls == 1
