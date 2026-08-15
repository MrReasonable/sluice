"""A zero with a KNOWN CAUSE must keep its cause, and must not auto-retire the source.

2026-08-15 incident: linkedin, jobserve and indeed all reported `drift=zero` for eight-plus
consecutive runs and auto-retired themselves. The single underlying cause was that the
scanner ran against a Camofox profile with no authenticated cookies, so every page came back
logged-out/challenged with no rows.

Two properties of the health layer turned one recoverable config mistake into permanent loss
of three heavyweight sources:

  1. `detect_drift` tested `count == 0` FIRST and returned bare "zero", discarding the
     redirect/blocked signals it had already been handed. The digest could therefore only
     say "zero", never "you were redirected" or "you were challenged".
  2. `_is_dead` treated every zero as death, so a source that could not be READ retired
     exactly like a source with genuinely no jobs -- removing the one signal that would have
     prompted a fix.

Broken is not the same as dead. A source we could not read is a source to FIX.
"""
from sluice.core.health import HealthStore, detect_drift


def _run(count, **signals):
    return {"count": count, "signals": signals}


# ---- 1. an explained zero keeps its explanation -----------------------------------------

def test_zero_caused_by_a_redirect_reports_the_redirect_not_bare_zero():
    # The whole point of capturing requested/landed host is to explain a bad run. Reporting
    # "zero" here throws away the only actionable half of what we know.
    assert detect_drift("s", 0, {"requested_host": "jobserve.com",
                                 "landed_host": "affiliate.jobserve.com"}, 10) == "redirect"


def test_zero_caused_by_a_block_reports_blocked_not_bare_zero():
    assert detect_drift("s", 0, {"blocked": True}, 10) == "blocked"


def test_zero_caused_by_missing_auth_reports_auth():
    # The 2026-08-15 case: the page loaded fine and had jobs on it, but the profile carried
    # no session so the authenticated markup never rendered.
    assert detect_drift("s", 0, {"auth": "missing"}, 10) == "auth"


def test_an_unexplained_zero_is_still_zero():
    # No signal to offer -> the honest answer is still "zero". This is the case the original
    # behaviour handled correctly and must keep handling.
    assert detect_drift("s", 0, {}, 10) == "zero"


def test_a_healthy_run_is_unaffected():
    assert detect_drift("s", 10, {}, 10) is None


# ---- 2. an explained zero does not retire the source ------------------------------------

def _store(tmp_path, runs):
    h = HealthStore(str(tmp_path / "health.json"))
    for r in runs:
        h.record("s", r["count"], r["signals"])
    return h


def test_three_unexplained_zeros_still_retire(tmp_path):
    # The rule that exists for a genuinely dead source must survive this change.
    h = _store(tmp_path, [_run(0), _run(0), _run(0)])
    assert h.should_retire("s") is True


def test_three_zeros_explained_by_a_block_do_NOT_retire(tmp_path):
    # This is the incident. Retiring here deletes the evidence: the source stops running, so
    # it stops reporting the block, so nobody learns the profile was wrong.
    h = _store(tmp_path, [_run(0, blocked=True)] * 3)
    assert h.should_retire("s") is False


def test_three_zeros_explained_by_missing_auth_do_NOT_retire(tmp_path):
    h = _store(tmp_path, [_run(0, auth="missing")] * 3)
    assert h.should_retire("s") is False


def test_three_zeros_explained_by_a_redirect_do_NOT_retire(tmp_path):
    h = _store(tmp_path, [_run(0, requested_host="a.com", landed_host="b.com")] * 3)
    assert h.should_retire("s") is False


def test_a_hard_error_with_no_yield_still_counts_as_dead(tmp_path):
    # An exception is not an "explanation" in the sense that matters: there is nothing to go
    # and fix on the page, the source simply failed. Keep retiring it.
    h = _store(tmp_path, [_run(0, error="boom")] * 3)
    assert h.should_retire("s") is True


def test_a_source_that_RETURNED_ROWS_is_never_dead_even_if_a_search_errored(tmp_path):
    # `_run_source` REASSIGNS `signals` per search instead of merging, so a source whose LAST
    # search raised while earlier ones succeeded reports a positive count AND an error. An
    # earlier draft short-circuited `_is_dead` on `error` and would have retired a source that
    # had just returned 50 rows. Found by a surviving mutant, not by reading.
    h = _store(tmp_path, [_run(50, error="one search timed out")] * 3)
    assert h.should_retire("s") is False


def test_a_mix_of_explained_and_unexplained_zeros_does_not_retire(tmp_path):
    # Any explained run in the window means we cannot conclude the source is dead.
    h = _store(tmp_path, [_run(0), _run(0, blocked=True), _run(0)])
    assert h.should_retire("s") is False
