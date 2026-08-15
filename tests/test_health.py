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
