"""resolve_merge_status: the order-independent N-ary status verdict for #23 dedup.

No total order spans the two lifecycles, and clusters are size >= 2, so the verdict
reads the SET of member statuses. Every case is asserted; the 3-member cases are
asserted over ALL permutations, because a single ordering catches only a left-fold
(a right-fold hits a different intermediate state).
"""
import itertools

import pytest

from sluice.core.status import resolve_merge_status


@pytest.mark.parametrize("statuses,winner,outcome", [
    (["shortlist", "new"], "shortlist", "ok"),          # new is the floor
    (["rejected", "shortlist"], "rejected", "ok"),       # app-owned beats triage
    (["applied", "interview"], "interview", "ok"),        # both live -> ladder rank
    (["offer", "offer"], "offer", "ok"),                 # equal
    (["rejected", "interview"], None, "conflict"),        # terminal + live -> conflict
    (["rejected", "accepted"], None, "conflict"),         # two terminals
    (["shortlist", "dismiss"], None, "conflict"),         # two non-new triage
    (["weird", "shortlist"], None, "conflict"),           # non-canonical + different
    # #169's new triage status, and the row that DISCRIMINATES it. An earlier ruling on
    # this branch held that no merge row could -- true only of the `research` +
    # `unjudgeable` pair traced then (two different non-new triage states either way, so
    # "conflict" whether or not `unjudgeable` is canonical), and false in general: drop
    # `unjudgeable` from status.CANONICAL and this row returns (None, "conflict") via the
    # `s - CANONICAL` guard instead. It pins a real behaviour change, not a tautology --
    # a duplicate cluster carrying an `unjudgeable` twin now RESOLVES to it rather than
    # refusing to dedupe.
    (["new", "unjudgeable"], "unjudgeable", "ok"),        # new is the floor here too
])
def test_pairwise_both_orders(statuses, winner, outcome):
    assert resolve_merge_status(statuses) == (winner, outcome)
    assert resolve_merge_status(list(reversed(statuses))) == (winner, outcome)


@pytest.mark.parametrize("statuses,winner,outcome", [
    (["new", "new", "rejected"], "rejected", "ok"),
    (["shortlist", "dismiss", "applied"], "applied", "ok"),   # app dominates both triage
    (["applied", "interview", "rejected"], None, "conflict"), # terminal + live present
    (["rejected", "accepted", "new"], None, "conflict"),
])
def test_three_member_all_permutations(statuses, winner, outcome):
    for perm in itertools.permutations(statuses):
        assert resolve_merge_status(list(perm)) == (winner, outcome)


def test_all_equal_noncanonical_is_agreement_not_conflict():
    assert resolve_merge_status(["weird", "weird"]) == ("weird", "ok")


def test_all_new_resolves_via_the_all_agree_path():
    # Regression for the dead `if not nonnew: return "new", "ok"` branch removed from
    # resolve_merge_status: an all-"new" cluster is already caught by the top
    # `len(s) == 1` all-agree guard, long before `nonnew` is ever computed, so removing
    # that branch must not change either of these outcomes.
    assert resolve_merge_status(["new", "new"]) == ("new", "ok")
    assert resolve_merge_status(["new"]) == ("new", "ok")
