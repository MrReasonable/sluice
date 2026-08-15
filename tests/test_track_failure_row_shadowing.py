"""A stale failure row must never shadow the real proposal for the same message.

Found by review of #139, with executed evidence. The dead-letter table is keyed on
`message_id` and `record` is `INSERT OR IGNORE`. Before #139 a failure wrote NO row, so a
collision was impossible; #139 introduced one.

Run 1: a transient error (one 503 is enough) writes an `ev_type="failure"` row for `m1`.
Run 2: the message now classifies fine, `reconcile` proposes, engine builds the real Entry
with its runnable `confirm` hint -- and `INSERT OR IGNORE` drops it silently. Then
`seen.add(mid)` runs, so the message is never re-queried.

Net: the digest shows `processing failed: ...` forever and the actual interview proposal is
gone permanently. `_dl_write` cannot help -- the write "succeeded" as far as SQLite is
concerned, so nothing raises and `deadletter_error` is never set.
"""
from sluice.track import engine as E
from sluice.track.config import TrackConfig
from sluice.track.deadletter import Entry
from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault


class _Flaky(OneMsgClient):
    """Fails on the first run, succeeds on the second -- one transient error."""

    def __init__(self, fail_first=True):
        super().__init__()
        self._fail = fail_first

    def get_message(self, mid):
        if self._fail:
            self._fail = False
            raise RuntimeError("transient 503")
        return super().get_message(mid)


def test_a_stale_failure_row_does_not_block_the_real_proposal():
    v, _ = _vault("applied")
    dl = _dl()
    client = _Flaky()

    E.run(v, TrackConfig(), client, FakeBackend("{}"), seen=set(), deadletter=dl,
          now_iso="2026-07-10T12:00:00+00:00")
    rows = {e.message_id: e for e in dl.open_entries()}
    assert rows["m1"].ev_type == "failure", "run 1 should record the failure"

    # Run 2: the message succeeds and produces a real proposal.
    E.run(v, TrackConfig(), client,
          FakeBackend('{"lead": "Example Tidal - Analyst", "type": "interview", '
                      '"confidence": 0.9, "when": null, "links": [], "materials": [], '
                      '"summary": "invite"}'),
          seen=set(), deadletter=dl, now_iso="2026-07-11T12:00:00+00:00")

    rows = {e.message_id: e for e in dl.open_entries()}
    assert "m1" in rows, "the row vanished entirely"
    assert rows["m1"].ev_type != "failure", (
        "the stale failure row shadowed the real proposal -- the interview is now "
        "unrecoverable, since seen.add ran and the message is never re-queried")


def test_record_does_not_silently_drop_a_differing_row(tmp_path):
    """The store built to prevent silent loss must not lose a write silently.

    `INSERT OR IGNORE` returning rowcount 0 means a DIFFERENT row already held that key. That
    is a hole under every caller, not only #139's -- so it is fixed in the store rather than
    worked around at one call site.
    """
    import pytest

    dl = _dl()
    dl.record(Entry(message_id="m1", lead="a", candidates="", ev_type="failure",
                    proposal="failed", hint="boom", first_seen="2026-07-10", times_surfaced=1))
    with pytest.raises(Exception) as exc:
        dl.record(Entry(message_id="m1", lead="b", candidates="", ev_type="interview",
                        proposal="confirm", hint="different", first_seen="2026-07-11",
                        times_surfaced=1))
    assert "m1" in str(exc.value)


def test_an_identical_re_record_is_still_a_quiet_no_op():
    """A deterministic failure re-recording the SAME row every run must stay silent.

    Raising there would turn one broken message into an exception per run -- the opposite of
    the durability #139 is for.
    """
    dl = _dl()
    e = Entry(message_id="m1", lead="", candidates="", ev_type="failure", proposal="failed",
              hint="boom", first_seen="2026-07-10", times_surfaced=1)
    dl.record(e)
    dl.record(e)   # must not raise
    assert len([r for r in dl.open_entries() if r.message_id == "m1"]) == 1


def test_BOTH_record_sites_clear_a_stale_failure_row():
    """The clear must live with the record, not beside one of them.

    `engine.run` records a dead-letter row in TWO places: the `res.action == "proposed"`
    branch, and the quiet-receipt / twin-hit / LLM-fallback branch above it. The first fix
    put the stale-failure clear on only the second one -- so a message that failed once and
    then classified as an unresolvable RECEIPT hit the new `record()` raise instead, setting
    `deadletter_error` on EVERY subsequent run.

    That never advances the lastrun watermark, so `_gmail_query`'s `after:` widens without
    bound and every run re-fetches a larger set: exactly the failure the #139 comment argues
    against, reintroduced by the fix for the collision.
    """
    # An IN-FLIGHT lead (the quiet-receipt branch requires one) that `match_receipt` cannot
    # resolve by domain, but which the LLM names -- the routine shape the reviewer identified.
    v, _ = _vault("applied")
    dl = _dl()
    client = _Flaky()

    E.run(v, TrackConfig(), client, FakeBackend("{}"), seen=set(), deadletter=dl,
          now_iso="2026-07-10T12:00:00+00:00")
    assert [e for e in dl.open_entries() if e.ev_type == "failure"], "run 1 records the failure"

    rep = E.run(v, TrackConfig(), client,
                FakeBackend('{"lead": "Example Tidal", "type": "receipt", "confidence": 0.9, '
                            '"when": null, "links": [], "materials": [], "summary": "ta"}'),
                seen=set(), deadletter=dl, now_iso="2026-07-11T12:00:00+00:00")

    assert rep.deadletter_error is False, (
        "the stale failure row collided with the receipt row, so the watermark is now held "
        "on every run and the Gmail window grows without bound")
    rows = {e.message_id: e for e in dl.open_entries()}
    assert rows["m1"].ev_type != "failure", "the receipt row never replaced the stale failure"
