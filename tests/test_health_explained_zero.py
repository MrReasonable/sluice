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


def test_three_zeros_explained_by_a_REDIRECT_still_retire(tmp_path):
    """A redirect is reported but is NOT a reprieve, unlike auth/blocked/unreachable.

    The distinction is whether an operator action brings the source back. An expired login or
    a downed browser does; a board that has MOVED does not, and no amount of further looking
    changes the evidence. This repo's entire auto-retire history is that case -- `hired.py`
    (hired.com 302s to lhh.com after the LHH acquisition) and `hackajob.py` (search 404s) were
    both retired BY HAND after exactly this. Exempting redirect would mean the automatic rule
    could never reach that conclusion again, and a relocated board would burn a browser slot
    on every run forever.

    REVERSES an earlier draft of this branch, which lumped redirect in with the recoverable
    reasons. `detect_drift` still reports "redirect", so the run report loses nothing.
    """
    h = _store(tmp_path, [_run(0, requested_host="a.com", landed_host="b.com")] * 3)
    assert h.should_retire("s") is True


def test_an_apex_to_www_hop_is_not_treated_as_a_redirect_at_all(tmp_path):
    """Nine registered sources request an apex host (cord.com, hired.com, remoteok.com, ...).

    An apex -> www hop is normal, permanent and benign. Reporting it as drift would mark those
    sources as broken on every single run, and -- back when redirect deferred retirement --
    would have handed each of them an indefinite reprieve the moment the board truly died.
    """
    assert detect_drift("s", 0, {"requested_host": "cord.com",
                                 "landed_host": "www.cord.com"}, 10) == "zero"
    h = _store(tmp_path, [_run(0, requested_host="cord.com", landed_host="www.cord.com")] * 3)
    assert h.should_retire("s") is True, "a benign www hop must not shield a dead board"


def test_a_hard_error_with_no_yield_still_counts_as_dead(tmp_path):
    # An exception is not an "explanation" in the sense that matters: there is nothing to go
    # and fix on the page, the source simply failed. Keep retiring it.
    h = _store(tmp_path, [_run(0, error="boom")] * 3)
    assert h.should_retire("s") is True


def test_a_camofox_outage_is_explained_and_does_not_retire_every_source(tmp_path):
    """The single most common "could not read": the browser server is down.

    `fetch` has always recorded `{"error": "no-tab"}` for it, and `health_hint` built a fresh
    dict that dropped it -- so the clearest explanation in the system reached the classifier as
    an unexplained zero. One outage therefore retired ALL ~23 sources at once, six lines from
    the code that exists to prevent exactly that.

    Recoverable: bring the server back and every source returns.
    """
    assert detect_drift("s", 0, {"fetch_error": "no-tab"}, 10) == "unreachable"
    h = _store(tmp_path, [_run(0, fetch_error="no-tab")] * 3)
    assert h.should_retire("s") is False


def test_a_broken_auth_probe_is_visible_but_grants_no_reprieve(tmp_path):
    """Two separable decisions the first draft collapsed into one.

    Not claiming "logged out" off a broken probe is right -- that would shield a genuinely
    dead source. But not SAYING the probe broke is a different call, and it means LinkedIn
    renaming a class silently disables this whole guard while every dashboard stays green.

    So the error is reported and is NOT an explanation: it must not defer retirement.
    """
    assert detect_drift("s", 0, {"auth_probe_error": "SyntaxError"}, 10) == "zero"
    h = _store(tmp_path, [_run(0, auth_probe_error="SyntaxError")] * 3)
    assert h.should_retire("s") is True


def test_an_explanation_does_not_outrank_a_run_that_PRODUCED_rows():
    """`auth` must not turn a 200-row success into reported drift.

    The first draft checked the explanation before any count, so a stale signal from one
    search of a multi-search source made a healthy run report drift and fire a notification.
    """
    assert detect_drift("s", 200, {"auth": "missing"}, 50) is None
    assert detect_drift("s", 200, {"fetch_error": "no-tab"}, 50) is None
    # A redirect or block alongside rows stays reportable -- pre-existing behaviour.
    assert detect_drift("s", 200, {"requested_host": "a.com", "landed_host": "b.com"}, 50) == "redirect"


def test_explained_streak_replaces_the_signal_retirement_used_to_carry(tmp_path):
    """Suppressing retirement is only safe if something else accumulates.

    Retirement never actually stopped a source running (`engine.py` mutates the in-memory
    registry and nothing persists it), so its real value was being the one DURABLE signal an
    operator saw days later. Exempting explained failures without a replacement would swap a
    loud wrong answer for a silent permanent one.
    """
    h = _store(tmp_path, [_run(0, auth="missing")] * 4)
    assert h.explained_streak("s") == ("auth", 4)
    # A healthy run breaks the streak.
    h.record("s", 50, {})
    assert h.explained_streak("s") == (None, 0)


def test_explained_streak_stops_at_a_CHANGE_of_reason(tmp_path):
    # A source flipping auth -> redirect -> auth is a different, noisier problem than one
    # wedged on the same thing since Tuesday, and the count should not blur them.
    h = _store(tmp_path, [_run(0, auth="missing"),
                          _run(0, requested_host="a.com", landed_host="b.com"),
                          _run(0, auth="missing")])
    assert h.explained_streak("s") == ("auth", 1)


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
