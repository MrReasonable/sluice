"""A per-message failure must be DURABLE, not merely logged.

#139. `except Exception: rep.failures += 1` deliberately skipped `seen.add` so the message
would retry. That is necessary but not sufficient, and the gap is the whole issue:

`run()` only ever iterates ids returned by `_gmail_query`, which scopes on a day-granular
`after:` derived from the lastrun watermark -- and `app.py` advances that watermark on the
very run where the failure happened (`rep.failures` is not in its gate, unlike `auth_error`
and `deadletter_error`). So a deterministic failure gets roughly a day of retries and is then
never queried again: absent from `seen`, absent from the dead-letter store, absent from every
report. After ~2 runs it ceases to exist.

The fix is NOT to hold the watermark. One permanently-poisoned message would freeze it
forever and the `after:` window would grow without bound, re-fetching an ever-larger set every
run -- and now that #137 lifted the page cap, that is a much bigger fetch, not a capped one.
It also would not reliably work: the failing message is the OLDEST thing in a widening window.

Instead the failure itself becomes durable, using the machinery #49 already built: a
dead-letter row re-surfaces every run via `bump_surfaced()` and carries a `track dismiss --id`
lever, so it survives the watermark moving on.
"""
from sluice.track import engine as E
from sluice.track.config import TrackConfig
from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault


class _Boom(OneMsgClient):
    """Fails every time, with a DIFFERENT cause each call.

    A constant cause makes the once-per-message guard untestable: `message_id` is the table's
    PRIMARY KEY, so a second identical row is impossible whatever the code does, and the
    "not written twice" assertion below passed with the guard deleted. Varying the cause makes
    the second write a DIFFERING row, which is the case the guard actually decides.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    def get_message(self, mid):
        self.calls += 1
        raise RuntimeError(f"gmail hiccup {self.calls}")


def _run(v, dl, client=None, **kw):
    return E.run(v, TrackConfig(), client or _Boom(), FakeBackend("{}"), seen=set(),
                 deadletter=dl, now_iso="2026-07-10T12:00:00+00:00", **kw)


def test_a_failed_message_is_recorded_in_the_dead_letter_store(tmp_path):
    v, _ = _vault("applied")
    dl = _dl()
    rep = _run(v, dl)
    assert len(rep.failures) == 1
    rows = {e.message_id: e for e in dl.open_entries()}
    assert "m1" in rows, "a failure that survives only in a log line is not durable"
    assert "gmail hiccup" in rows["m1"].hint, "the row must carry the cause, not just the id"


def test_the_failure_row_is_actionable(tmp_path):
    """A row nobody can clear is a permanent nag, which trains people to ignore the digest."""
    v, _ = _vault("applied")
    dl = _dl()
    _run(v, dl)
    entry = [e for e in dl.open_entries() if e.message_id == "m1"][0]
    assert entry.proposal, "needs a proposal string so the digest can name it"
    assert dl.clear_id("m1") == 1, "`track dismiss --id` must be able to clear it"
    assert not [e for e in dl.open_entries() if e.message_id == "m1"]


def test_a_failed_message_still_stays_out_of_seen(tmp_path):
    """The retry contract is kept, not replaced.

    The dead-letter row makes the failure durable EVEN IF the watermark moves past it; it
    does not remove the cheaper recovery of simply retrying while the message is still in
    the window.
    """
    v, _ = _vault("applied")
    seen = set()
    E.run(v, TrackConfig(), _Boom(), FakeBackend("{}"), seen=seen, deadletter=_dl(),
          now_iso="2026-07-10T12:00:00+00:00")
    assert "m1" not in seen


def test_a_dry_run_records_nothing(tmp_path):
    # Same posture as every other dead-letter write in this engine: a preview must not
    # mutate durable state.
    v, _ = _vault("applied")
    dl = _dl()
    rep = _run(v, dl, dry_run=True)
    assert len(rep.failures) == 1
    assert not [e for e in dl.open_entries() if e.message_id == "m1"]


def test_a_repeatedly_failing_message_keeps_ITS_FIRST_row_and_does_not_error(tmp_path):
    """A deterministic failure fails again next run, while its row is still open.

    Re-recording would multiply one broken message into a growing pile of identical rows --
    the digest gets noisier the longer the problem goes unfixed, which is backwards.

    The row COUNT alone cannot witness that: `message_id` is the PRIMARY KEY, so two rows are
    impossible regardless. What the guard actually decides is what happens on a DIFFERING
    second write -- without it, `record` raises on the changed cause, `deadletter_error` is
    set, and the lastrun watermark is held on every subsequent run. So the load-bearing
    assertions are the error flag and which cause survived.
    """
    v, _ = _vault("applied")
    dl = _dl()
    client = _Boom()
    _run(v, dl, client=client)
    rep2 = _run(v, dl, client=client)
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert len(rows) == 1, f"expected one row for a repeatedly-failing message, got {len(rows)}"
    assert rep2.deadletter_error is False, (
        "a re-failing message set deadletter_error, which holds the watermark forever")
    assert "gmail hiccup 1" in rows[0].hint, (
        "the FIRST failure's row must stand -- rewriting it every run resets nothing useful "
        "and churns the store")


def test_a_deadletter_write_failure_still_holds_the_watermark(tmp_path):
    """The #49 asymmetry must survive: if we cannot PERSIST the failure, do not advance.

    This is the one case where holding the watermark is right -- the failure is not durable,
    so the message must stay in the query window.
    """
    v, _ = _vault("applied")

    dl = _dl()

    def _boom(entry):
        raise OSError("disk full")

    dl.record = _boom
    rep = _run(v, dl)
    assert rep.deadletter_error is True, "an unpersistable failure must hold the watermark"
