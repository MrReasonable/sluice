"""#9: the pure age rule.

Every case here is a mutation target -- see the witness table in
docs/superpowers/specs/2026-07-27-lead-staleness-design.md. Two of them are only
meaningful with the fixture they carry, and both are called out inline.
"""
import pytest

from sluice.core.leads import StalenessPolicy

TODAY = "2026-07-27"


def _p(ttl=0, today=TODAY, include_stale=False):
    return StalenessPolicy(ttl_days=ttl, today=today, include_stale=include_stale)


def test_older_than_ttl_is_stale():
    assert _p(ttl=90).is_stale("2026-01-01") is True


def test_exactly_ttl_days_old_is_not_yet_stale():
    # Strictly greater: the boundary is a mutation target (`>` -> `>=`).
    assert _p(ttl=90).is_stale("2026-04-28") is False      # exactly 90 days
    assert _p(ttl=90).is_stale("2026-04-27") is True       # 91 days


def test_unconfigured_ttl_abstains_on_an_ANCIENT_lead():
    # The fixture MUST be ancient. With last_seen == today, the expression surviving
    # deletion of the `ttl_days <= 0` guard is `0 > 0` -> False, so the mutant lives and
    # the test certifies nothing. This guard is the 672ad2a blast radius -- an
    # unconfigured install must expire nothing -- so its witness has to be able to fire.
    assert _p(ttl=0).is_stale("2020-01-01") is False


def test_negative_ttl_abstains():
    # `<= 0`, not `== 0`: a hand-built policy with a negative value must abstain rather
    # than treat every lead as stale.
    assert _p(ttl=-1).is_stale("2020-01-01") is False


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", "2026-13-01", "2026-02-30"])
def test_unparseable_or_absent_last_seen_abstains(bad):
    # A missing date is not evidence of age. Notes predating the field, and hand-created
    # notes, both exist in real vaults; binning them because a field failed to parse is
    # the 672ad2a shape at the data level.
    assert _p(ttl=90).days(bad) is None
    assert _p(ttl=90).is_stale(bad) is False


def test_unparseable_today_abstains_rather_than_raising():
    # A bad injected clock must abstain for the same reason bad stored data must.
    assert _p(ttl=90, today="garbage").days("2020-01-01") is None
    assert _p(ttl=90, today="garbage").is_stale("2020-01-01") is False


def test_future_last_seen_is_not_stale():
    # Clock skew or a hand edit. Falls out of `>` rather than needing its own branch.
    assert _p(ttl=90).is_stale("2027-01-01") is False


def test_days_counts_whole_days():
    assert _p().days("2026-07-20") == 7


def test_include_stale_makes_blocks_false_while_is_stale_stays_true():
    p = _p(ttl=90, include_stale=True)
    assert p.is_stale("2020-01-01") is True
    assert p.blocks("2020-01-01") is False


def test_blocks_is_true_for_a_stale_lead_by_default():
    assert _p(ttl=90).blocks("2020-01-01") is True


def test_default_policy_abstains():
    # A call site that forgets to pass a policy must fail SAFE: the failure this guards
    # is binning a lead the user still wants.
    assert StalenessPolicy().is_stale("2020-01-01") is False
    assert StalenessPolicy().blocks("2020-01-01") is False


def test_policy_is_frozen():
    with pytest.raises(Exception):
        _p(ttl=90).ttl_days = 1


def test_today_must_be_a_string_and_the_error_names_the_fix():
    # `Sluice`'s `today` collaborator is a zero-arg CALLABLE, so `today=self._today`
    # binds a FUNCTION. That reaches date.fromisoformat(<function>) -> TypeError, which
    # the ValueError guard does NOT catch, turning the designed fail-safe abstain into a
    # traceback on three commands. Fail at construction, naming the fix.
    with pytest.raises(TypeError, match="callable"):
        StalenessPolicy(ttl_days=90, today=lambda: TODAY)
