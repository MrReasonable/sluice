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


def test_precedence_zero_beats_redirect():
    assert detect_drift("s", 0, {"requested_host": "a", "landed_host": "b"}, 10) == "zero"


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
