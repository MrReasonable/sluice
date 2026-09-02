"""What happens to work we could NOT complete, once it leaves `sync_event`.

Wave 1 taught `sync_event` to say `unresolved` and wave 1's review found that saying it was
the easy half. Five of seven reviewers independently traced the same gap: `reconcile` learned
what `unresolved` means in the CANCEL branch and not in the SCHEDULING branch, so a refused
insert advanced the status, booked nothing, wrote no row, and let `seen.add` consume the
message. `failures=0 calendar_added=0` -- indistinguishable from a message carrying no invite.

The unit tests for that path stopped at `sync_event`'s return value, which is precisely why
they could not see it. Everything here drives `engine.run` end to end.
"""
import json
import pathlib
from datetime import datetime, timezone

from sluice.track import engine as E
from sluice.track.config import TrackConfig
from sluice.track.deadletter import EV_TYPE_CALENDAR, EV_TYPE_FAILURE, Entry
from tests.test_track_engine import FakeBackend, OneMsgClient, _dl, _vault

_ICS = (b"BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
        b"DTSTART:20260715T100000Z\r\nSUMMARY:Screen\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")

_INTERVIEW = json.dumps({"lead": "Example Tidal - Analyst", "type": "interview",
                         "confidence": 0.9, "when": None, "links": [], "materials": [],
                         "summary": "invite"})


class _TruncatedClient(OneMsgClient):
    """A client whose calendar window is short and SAYS so, carrying an invite."""

    def __init__(self, truncated=True):
        super().__init__()
        self.truncated = truncated

    def get_message(self, mid):
        msg = super().get_message(mid)
        msg["attachments"] = [{"filename": "invite.ics", "mime": "text/calendar", "data": _ICS}]
        return msg


def _run(client, backend_json=_INTERVIEW, status="applied", seen=None, dl=None, day="10"):
    v, path = _vault(status)
    dl = dl or _dl()
    seen = seen if seen is not None else set()
    rep = E.run(v, TrackConfig(), client, FakeBackend(backend_json), seen=seen, deadletter=dl,
                now_iso=f"2026-07-{day}T12:00:00+00:00")
    return rep, dl, seen, pathlib.Path(path).read_text()


def test_a_refused_insert_is_RECORDED_even_though_the_status_advanced():
    """The critical one. `action` came out as `applied`, which records nothing."""
    rep, dl, seen, note = _run(_TruncatedClient())
    assert rep.calendar_added == 0, "nothing was booked"
    assert "status: interview" in note, "the interview is real, so the advance is right"
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, ("the calendar entry was never created and NOTHING recorded it -- "
                  "seen.add consumed the message and the interview is not in the calendar")
    assert rows[0].proposal == "calendar-unresolved"
    # The row must be IDENTIFIABLE, which is what this line has always been about. It used to
    # require the ics UID inside the hint; the UID is counterparty-supplied text that can carry
    # the sender's domain, and these rows are printed to stderr AND persisted indefinitely.
    # Nothing is lost by dropping it: `cmd_track_run` renders the row as
    # `{lead} <{message_id}>: {proposal} :: {hint}`, so it is identified by sluice's own
    # identifiers -- both better handles than an opaque UID -- before the hint even starts.
    assert rows[0].lead == "Example Tidal - Analyst", rows[0].lead
    assert rows[0].message_id == "m1"
    assert "m1" in rows[0].hint, "the hint must still carry the id its own command needs"
    assert "u1" not in rows[0].hint, f"the hint leaked the inbound invite id: {rows[0].hint}"


def test_the_refused_insert_hint_does_not_claim_the_entry_exists():
    _rep, dl, _seen, _note = _run(_TruncatedClient())
    hint = [e for e in dl.open_entries() if e.message_id == "m1"][0].hint
    assert "by hand" in hint, hint


def test_a_COMPLETE_window_still_books_and_records_nothing():
    # The quiet path stays quiet: crying wolf on every invite is how a real row gets ignored.
    rep, dl, _seen, _note = _run(_TruncatedClient(truncated=False))
    assert rep.calendar_added == 1
    assert [e for e in dl.open_entries() if e.message_id == "m1"] == []


def test_the_refused_insert_row_is_a_CALENDAR_row_not_a_status_proposal():
    """`clear_lead` fires on any later advance for the same lead, unfiltered by kind."""
    _rep, dl, _seen, _note = _run(_TruncatedClient())
    assert [e for e in dl.open_entries() if e.message_id == "m1"][0].ev_type == EV_TYPE_CALENDAR


def test_an_unrelated_later_advance_does_NOT_delete_the_calendar_row():
    """The row says "remove it from your calendar by hand". A rejection email arriving weeks
    later advanced the same lead, `clear_lead` deleted every row for that lead, and the
    message was already in `seen` -- so the instruction was gone and never re-derived."""
    dl = _dl()
    dl.record(Entry(message_id="m9", lead="Example Tidal - Analyst", candidates="",
                    ev_type=EV_TYPE_CALENDAR, proposal="cancel-unresolved",
                    hint="remove it by hand", first_seen="2026-07-10", times_surfaced=1))
    assert dl.clear_lead("Example Tidal - Analyst") == 0, "a calendar row is not a status proposal"
    assert [e.message_id for e in dl.open_entries()] == ["m9"]


def test_clear_lead_STILL_clears_an_ordinary_status_proposal():
    # The behaviour the filter must not break.
    dl = _dl()
    dl.record(Entry(message_id="m8", lead="Example Tidal - Analyst", candidates="",
                    ev_type="interview", proposal="interview", hint="confirm",
                    first_seen="2026-07-10", times_surfaced=1))
    assert dl.clear_lead("Example Tidal - Analyst") == 1
    assert dl.open_entries() == []


# ---- a failure row must not outlive its own resolution -----------------------------------

class _FlakyOnce(_TruncatedClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._fail = True

    def get_message(self, mid):
        if self._fail:
            self._fail = False
            raise RuntimeError("transient 503")
        return super().get_message(mid)


def test_a_failure_row_is_cleared_when_the_retry_ADVANCES_the_lead():
    """`applied` records nothing, so the clear used to miss it entirely.

    One transient Gmail 500 left a row that survived its own resolution, re-surfacing every
    run with `times_surfaced` climbing, for a message now in `seen` and never reprocessed.
    `clear_lead` structurally cannot reach it -- the failure Entry is written with `lead=""`.
    """
    dl = _dl()
    client = _FlakyOnce(truncated=False)
    _rep, _dl1, _seen, _note = _run(client, dl=dl, day="10")
    assert [e.ev_type for e in dl.open_entries() if e.message_id == "m1"] == [EV_TYPE_FAILURE]

    _rep2, _dl2, _seen2, note = _run(client, dl=dl, day="11")
    assert "status: interview" in note, "run 2 must actually advance"
    assert [e for e in dl.open_entries() if e.ev_type == EV_TYPE_FAILURE] == [], (
        "the failure row outlived the retry that resolved it")


def test_a_failure_row_is_cleared_when_the_retry_is_pure_NOISE():
    """`skipped` records nothing either -- the second of the two missed outcomes."""
    dl = _dl()

    class _FlakyNoise(OneMsgClient):
        def __init__(self):
            super().__init__()
            self._fail = True

        def get_message(self, mid):
            if self._fail:
                self._fail = False
                raise RuntimeError("transient 503")
            return super().get_message(mid)

    client = _FlakyNoise()
    _run(client, backend_json="{}", dl=dl, day="10")
    assert [e.ev_type for e in dl.open_entries() if e.message_id == "m1"] == [EV_TYPE_FAILURE]

    _run(client, backend_json="{}", dl=dl, day="11")
    assert [e for e in dl.open_entries() if e.ev_type == EV_TYPE_FAILURE] == [], (
        "a message that processed has resolved its failure, whatever the outcome was")


# ---- re-recording a message must not wedge the watermark ---------------------------------

def test_a_DIFFERING_proposal_row_on_re_processing_does_not_stall_the_watermark():
    """`record`'s raise, applied to the engine re-recording its OWN message.

    `_save_seen` runs after the whole run, so any crash between a record and that save leaves
    an open row for an un-`seen` message. Re-processing then re-derives the row, and a
    `proposal` string embedding a model confidence differs between runs -- the routine shape.
    That hit the raise: `deadletter_error` on every run, so `_save_lastrun` never advanced and
    `_gmail_query`'s `after:` widened without bound, unrecoverable without hand-editing sqlite.
    """
    dl = _dl()
    dl.record(Entry(message_id="m1", lead="Example Tidal - Analyst", candidates="",
                    ev_type="interview", proposal="interview (conf 0.55)",
                    hint="stale hint from a run that never saved seen",
                    first_seen="2026-07-09", times_surfaced=4))

    rep, _dl2, _seen, _note = _run(OneMsgClient(), backend_json=json.dumps(
        {"lead": "", "type": "interview", "confidence": 0.55, "when": None, "links": [],
         "materials": [], "summary": "invite"}), dl=dl, day="10")

    assert rep.deadletter_error is False, (
        "the engine re-recording its own message wedged the lastrun watermark permanently")
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert len(rows) == 1, "one message, one row"


def test_replace_PRESERVES_first_seen_and_times_surfaced():
    """An UPDATE, not a delete-and-reinsert.

    `first_seen` is how long a row has been open and `times_surfaced` is how often it has been
    shown; both are the store's own bookkeeping, not the caller's to reset. Resetting them
    would make an old unresolved row look brand new every time it was re-derived.
    """
    dl = _dl()
    dl.record(Entry(message_id="m1", lead="a", candidates="", ev_type="interview",
                    proposal="old", hint="old", first_seen="2026-07-01", times_surfaced=9))
    dl.record(Entry(message_id="m1", lead="b", candidates="", ev_type="offer",
                    proposal="new", hint="new", first_seen="2026-07-20", times_surfaced=1),
              replace=True)
    row = dl.open_entries()[0]
    assert (row.proposal, row.hint, row.ev_type, row.lead) == ("new", "new", "offer", "b")
    assert row.first_seen == "2026-07-01", "first_seen is the store's, not the caller's"
    assert row.times_surfaced == 9, "times_surfaced moves under bump_surfaced only"


def test_replace_is_OPT_IN_so_a_foreign_collision_still_raises():
    """The guard exists for a caller clobbering a row it did not write. Only the engine
    re-recording its own message is entitled to replace."""
    import pytest

    dl = _dl()
    dl.record(Entry(message_id="m1", lead="a", candidates="", ev_type="interview",
                    proposal="p", hint="h", first_seen="2026-07-01", times_surfaced=1))
    with pytest.raises(ValueError) as exc:
        dl.record(Entry(message_id="m1", lead="b", candidates="", ev_type="offer",
                        proposal="q", hint="i", first_seen="2026-07-01", times_surfaced=1))
    assert "m1" in str(exc.value)


# ---- the startless arm ---------------------------------------------------------------------

def test_a_startless_NON_cancel_is_unresolved_rather_than_present():
    """`present` is defined as "we searched a complete window and there was nothing of ours".

    Nothing was searched -- there is no DTSTART to build a window from. Unreachable today
    because `reconcile` guards the only non-cancel call site, so this pins the VALUE rather
    than a live path: the wrong one sits waiting for a third caller.
    """
    from sluice.track.calendar_sync import sync_event
    from sluice.track.ics import IcsEvent
    from tests.test_track_google_client import FakeGoogleClient

    c = FakeGoogleClient(events=[])
    ics = IcsEvent(uid="u1", summary="Screen", start=None)
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "unresolved"
    assert c.listed == [], "it must not have searched at all"


def test_a_startless_CANCEL_is_still_unresolved():
    from sluice.track.calendar_sync import sync_event
    from sluice.track.ics import IcsEvent
    from tests.test_track_google_client import FakeGoogleClient

    ics = IcsEvent(uid="u1", summary="Screen", start=None)
    ics.method = "CANCEL"
    assert sync_event(FakeGoogleClient(events=[]), TrackConfig(),
                      lead_slug="example-lead", ics=ics) == "unresolved"


def test_a_real_start_still_reaches_the_calendar():
    # The guard must not swallow the ordinary path.
    from sluice.track.calendar_sync import sync_event
    from sluice.track.ics import IcsEvent
    from tests.test_track_google_client import FakeGoogleClient

    c = FakeGoogleClient(events=[])
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "created"


# ---- a foreign event covering the slot is not "nothing to do" ------------------------------

class _ForeignClient(_TruncatedClient):
    """A window holding one UNTAGGED event near the invite's start.

    `calendar_match_minutes` ships at 30, so this is not an exotic fixture: any ordinary
    appointment within half an hour of the interview lands here.
    """

    def __init__(self):
        super().__init__(truncated=False)
        self.events = [{"id": "dentist",
                        "start": {"dateTime": "2026-07-15T10:20:00+00:00"}}]


def test_a_foreign_event_blocking_the_insert_REACHES_a_human():
    """Executed by review: the interview was never booked, the status advanced, the note got
    an interview_date, and the digest read `calendar_added=0 failures=0 open=0` -- identical
    to a message carrying no invite at all."""
    rep, dl, seen, note = _run(_ForeignClient())
    assert rep.calendar_added == 0
    assert "status: interview" in note, "the interview is real; the advance stays"
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, "an unbooked interview left no trace anywhere"
    assert rows[0].proposal == "calendar-foreign"
    assert rows[0].ev_type == EV_TYPE_CALENDAR


def test_we_still_never_touch_the_foreign_event():
    # The safety property. Reporting it must not turn into acting on it.
    client = _ForeignClient()
    _run(client)
    assert not client.inserted and not client.updated and not client.deleted


def test_an_event_OUTSIDE_the_match_window_does_not_block_the_booking():
    """The boundary `_ForeignClient` sits inside, from the other side.

    This was a byte-identical copy of `test_a_COMPLETE_window_still_books_and_records_nothing`
    -- same fixture, same assertions -- so it pinned nothing the other did not.
    `calendar_match_minutes` ships at 30; an event well outside that must NOT suppress the
    insert, or the `foreign` outcome becomes a blanket refusal to ever book anything on a
    busy calendar.
    """
    class _FarAway(_TruncatedClient):
        def __init__(self):
            super().__init__(truncated=False)
            # 4 hours out: far outside the 30-minute proximity window.
            self.events = [{"id": "lunch",
                            "start": {"dateTime": "2026-07-15T14:00:00+00:00"}}]

    rep, dl, _seen, _note = _run(_FarAway())
    assert rep.calendar_added == 1, "an unrelated event hours away blocked the booking"
    assert [e for e in dl.open_entries() if e.message_id == "m1"] == []


def test_a_dry_run_records_no_calendar_row_either():
    """`--dry-run` must reach nothing durable. The failure row's dry-run guard was covered;
    the `needs_review` calendar row is the newest write path and was not."""
    v, _path = _vault("applied")
    dl = _dl()
    E.run(v, TrackConfig(), _TruncatedClient(), FakeBackend(_INTERVIEW), seen=set(),
          deadletter=dl, now_iso="2026-07-10T12:00:00+00:00", dry_run=True)
    assert dl.open_entries() == [], "a preview run wrote a dead-letter row"


def test_a_failure_over_an_open_PROPOSAL_row_does_not_stall_the_watermark():
    """The third record site's guard, which compared against the wrong thing.

    Round 3 widened `_open_ev_type` from failure-rows-only to every kind, routed the two
    proposal sites through `_record_replacing`, and left the failure site's guard as
    `!= EV_TYPE_FAILURE`. So when the open row was a PROPOSAL the guard passed, `record` ran
    without `replace`, and raised on the differing row: `deadletter_error` every run,
    `_save_lastrun` skipped forever, `after:` frozen while now advances.

    Entry condition is ordinary -- `_save_seen` runs after `run()` returns, so any kill or
    timeout between recording a proposal and that save leaves exactly this state.
    """
    dl = _dl()
    dl.record(Entry(message_id="m1", lead="Example Tidal - Analyst", candidates="",
                    ev_type="interview", proposal="interview (conf 0.88)",
                    hint='job-sluice track confirm --lead "Example Tidal - Analyst" --to interview',
                    first_seen="2026-07-09", times_surfaced=3))

    class _Poison(OneMsgClient):
        def get_message(self, mid):
            raise RuntimeError("deterministic poison")

    rep, _dl2, seen, _note = _run(_Poison(), dl=dl, day="10")
    assert rep.deadletter_error is False, (
        "a failure landing on an open proposal row wedged the watermark permanently")
    assert len(rep.failures) == 1, "the failure must still be reported"
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert len(rows) == 1 and rows[0].ev_type == "interview", (
        "the existing proposal carries a runnable confirm hint and must not be replaced "
        'by a bare "failed"')
    assert "m1" not in seen, "a failed message must stay retryable"


def test_a_failing_CLEAR_does_not_report_a_successful_message_as_failed():
    """The clear is housekeeping, so it must not be able to fail the message.

    `_clear_stale_row` runs at the very end of a message that SUCCEEDED -- the status advance
    and the calendar write have already landed. Routing it through `_dl_write`'s re-raise put
    it in the per-message `except`, which appended a `TrackFailure`, pushed a Telegram alert
    naming a message that worked, and skipped `seen.add`. The re-processed message then found
    the lead already advanced, `can_advance` refused, and the run filed a proposal whose hint
    was a `confirm` command `can_transition` refuses forever -- the un-runnable-hint shape #49
    exists to prevent.

    A failed CLEAR loses nothing: the row survives, re-surfaces, and `track dismiss --id`
    still works. `deadletter_error` is still set, so the watermark still holds.
    """
    dl = _dl()
    client = _FlakyOnce(truncated=False)
    _run(client, dl=dl, day="10")
    assert [e.ev_type for e in dl.open_entries() if e.message_id == "m1"] == [EV_TYPE_FAILURE]

    def _boom(_message_id):
        raise RuntimeError("readonly database")

    dl.clear_id = _boom
    rep, _dl2, seen, note = _run(client, dl=dl, day="11")

    assert "status: interview" in note, "the message processed and the write landed"
    assert rep.failures == [], (
        "tidying up after a SUCCESSFUL message reported it as a failed one: "
        f"{[f.detail() for f in rep.failures]}")
    assert "m1" in seen, "a successful message must be consumed, not re-processed forever"
    # The docstring's other half, which this stopped short of asserting: swallowing the RAISE
    # must not also swallow the SIGNAL. `deadletter_error` still holds the watermark, so a
    # store that cannot be written does not quietly advance past un-persisted work.
    assert rep.deadletter_error is True, (
        "a failing dead-letter write must still hold the lastrun watermark")


def test_every_needs_review_REASON_has_a_hint():
    """ENUMERATED from `reconcile`'s source, not from a list written here.

    `_NEEDS_REVIEW_HINT[reason]` raises KeyError at the record site, which is loud and local
    -- but only once that reason actually occurs in production. Deriving the reasons from the
    producer means a new one added without a hint fails here instead, and a hand-written
    expectation in this file would just be the same omission twice.
    """
    import inspect
    import re

    from sluice.track import reconcile as R
    from sluice.track.engine import _NEEDS_REVIEW_HINT

    src = inspect.getsource(R.reconcile)
    # Each `needs_review = f"<prefix>-{r.calendar}"` is paired with the outcome tuple of
    # the `if r.calendar in (...)` it sits under, walking in source order.
    #
    # It used to cross EVERY prefix with EVERY outcome found anywhere in the function.
    # That over-approximates once the two arms stop listing the same outcomes: the cancel
    # path returns before the scheduling arm, so it cannot emit `unorderable`, and the
    # blanket cross demanded a `cancel-unorderable` hint for a reason nothing can produce.
    # Writing that hint would have put a sentence in the table describing a situation that
    # never happens -- the exact kind of untrue prose the table exists to prevent.
    #
    # Pairing is STRICTLY tighter than the cross product, so nothing this used to catch
    # escapes: a prefix still demands a hint for every outcome ITS OWN guard admits.
    expected, current = set(), None
    for line in src.splitlines():
        m_guard = re.search(r'r\.calendar in \(([^)]*)\)', line)
        if m_guard:
            current = {v.strip().strip('"') for v in m_guard.group(1).split(",") if v.strip()}
            continue
        m_reason = re.search(r'needs_review\s*=\s*f"([a-z]+)-\{', line)
        if m_reason:
            assert current, f"a reason with no preceding outcome guard: {line.strip()!r}"
            expected |= {f"{m_reason.group(1)}-{v}" for v in current}
    assert expected, "the scan found no needs_review assignment -- it has drifted from the code"
    missing = expected - set(_NEEDS_REVIEW_HINT)
    assert not missing, f"reasons reconcile can emit with no hint: {sorted(missing)}"


def test_no_needs_review_hint_offers_a_runnable_confirm():
    """The property the deleted carve-out protected, asserted over the whole table.

    `ev.type` for a cancelled interview invite is still "interview", so a hint built from
    `_PROPOSE_TARGET` would hand the operator `confirm --to interview` -- a command that RUNS
    and books the thing that was just cancelled. Checked for every reason rather than for the
    one that had the bug.
    """
    from sluice.track.engine import _NEEDS_REVIEW_HINT

    for reason, template in _NEEDS_REVIEW_HINT.items():
        assert "--to " not in template, f"{reason} offers a status advance: {template}"
        assert "dismiss --id" in template, f"{reason} has no lever to clear it: {template}"


_INTERVIEW_BODY_DISAGREES = json.dumps(
    {"lead": "Example Tidal - Analyst", "type": "interview", "confidence": 0.9,
     # The header (_ICS above) says 2026-07-15. The body says three days later, which is
     # the shape #202 was filed on -- and there the HEADER was the wrong one.
     "when": "2026-07-18T10:00:00+00:00", "links": [], "materials": [],
     "summary": "invite"})


def test_a_header_body_date_conflict_is_RECORDED_and_books_nothing():
    """#202 defect 2, end to end -- the half a unit test on `reconcile` cannot see.

    `needs_review` is rendered through `_NEEDS_REVIEW_HINT[reason]`, a direct lookup that
    raises KeyError at the record site for a reason with no hint. A new reason is
    therefore not wired until the table knows it, and reconcile's own tests never reach
    that code. This is the same gap this module's docstring was written about.
    """
    rep, dl, seen, note = _run(_TruncatedClient(truncated=False),
                               backend_json=_INTERVIEW_BODY_DISAGREES)
    assert rep.calendar_added == 0, "booked an appointment on a disputed date"
    assert "status: interview" in note, "the interview is real, so the advance is right"
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, "the disputed date was never recorded anywhere"
    assert rows[0].proposal == "calendar-date-conflict"
    assert rows[0].hint, "a reason with no hint is the KeyError this table exists to force"
    # Same rule as every other row here: never hand the operator a runnable command that
    # would book the very thing whose date is in dispute.
    assert "confirm --to" not in rows[0].hint


def test_every_literal_needs_review_reason_has_a_hint():
    """The trap that caught this branch, closed so it cannot catch the next one.

    `_NEEDS_REVIEW_HINT[reason]` is a direct lookup, and the table's own header calls that
    deliberate: a reason with no hint raises KeyError, "loud, local and immediate". Loud
    it is -- but only on the path that RENDERS the row, which no test of `reconcile`
    reaches. So `calendar-date-conflict` was added, four unit tests went green, and every
    real run of it aborted the message.

    Sweeps reconcile.py's own AST for the constant reasons it assigns, rather than
    restating a list here that would go stale exactly when someone adds one. The two
    f-string reasons (`cancel-{outcome}`, `calendar-{outcome}`) are not constants and are
    covered by the end-to-end tests above.
    """
    import ast
    import inspect

    from sluice.track import engine as _E
    from sluice.track import reconcile as _R

    tree = ast.parse(inspect.getsource(_R))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Attribute) and tgt.attr == "needs_review"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                found.add(node.value.value)

    # Assert on the SCOPE first: a sweep that discovers nothing satisfies every assertion
    # over it, and this one walks a module whose shape it does not control.
    assert found, ("found no constant needs_review assignment in reconcile.py -- the sweep "
                   "is matching nothing and would certify any table at all")
    missing = sorted(found - set(_E._NEEDS_REVIEW_HINT))
    assert not missing, (
        f"needs_review reason(s) {missing} have no hint; _NEEDS_REVIEW_HINT is a direct "
        "lookup, so every message carrying one dies with KeyError at the record site")


def test_no_hint_interpolates_anything_but_the_message_id():
    """`_NEEDS_REVIEW_HINT`'s header states TWO rules. Only one of them was executable.

    "No hint may offer `confirm --to <status>`" is asserted by the row tests above. "None
    may name the inbound iCalendar UID" was prose only -- and it guards the more expensive
    mistake: these rows are printed to stderr AND persisted in the dead-letter store
    indefinitely, so counterparty-supplied text placed there outlives the mailbox it came
    from. A UID sometimes encodes the sender's domain.

    Asserting the interpolation SET rather than hunting for the word "uid" is what makes it
    a real guard: any future `{ics_uid}`, `{subject}` or `{sender}` fails here, including
    ones nobody thought to grep for. `{mid}` is sluice's own identifier and is already on
    the rendered line.
    """
    import string

    from sluice.track.engine import _NEEDS_REVIEW_HINT

    assert _NEEDS_REVIEW_HINT, "no hints to check -- the sweep is inert"
    for reason, template in sorted(_NEEDS_REVIEW_HINT.items()):
        fields = {name for _lit, name, _spec, _conv
                  in string.Formatter().parse(template) if name}
        assert fields <= {"mid"}, (
            f"{reason}'s hint interpolates {sorted(fields - {'mid'})}; a hint may carry "
            "only sluice's own message id, never counterparty-supplied text")


def test_a_date_conflict_on_a_lead_that_cannot_advance_counts_no_proposal():
    """END TO END, because the unit test on `reconcile` cannot see this -- the same gap
    this module's docstring was written about, arriving through a new door.

    A date conflict leaves `calendar` at `none`, so a lead that cannot advance took the
    `proposed` arm as well and the run counted a proposal. The ROW count never differed
    (`_record_replacing` replaces by message_id, so the calendar row silently overwrote
    the proposal row) -- what differed is `rep.proposed`, a digest counter reporting work
    that no row records. Asserting the counter is the only way to see it.
    """
    rep, dl, seen, note = _run(_TruncatedClient(truncated=False),
                               backend_json=_INTERVIEW_BODY_DISAGREES, status="interview")
    assert rep.calendar_added == 0, "booked an appointment on a disputed date"
    assert rep.proposed == 0, (
        "counted a proposal for a message whose only row is the calendar one")
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert len(rows) == 1, f"one message, one row -- got {[r.proposal for r in rows]}"
    assert rows[0].ev_type == EV_TYPE_CALENDAR
    assert rows[0].proposal == "calendar-date-conflict"


_ICS_UNORDERABLE = (b"BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    b"SEQUENCE:1.0\r\nDTSTART:20260718T100000Z\r\nSUMMARY:Screen\r\n"
                    b"END:VEVENT\r\nEND:VCALENDAR\r\n")


class _OursAtOldTimeClient(OneMsgClient):
    """Our own event sits at the OLD instant; the invite moves it but cannot be ordered."""

    def __init__(self):
        super().__init__()
        self.events = [{"id": "ev1", "start": {"dateTime": "2026-07-15T10:00:00+00:00"},
                        "extendedProperties": {"private": {
                            "sluice-track-uid": "u1", "sluice-track-lead": "Example Tidal - Analyst",
                            "sluice-track-seq": "3"}}}]

    def get_message(self, mid):
        msg = super().get_message(mid)
        msg["attachments"] = [{"filename": "invite.ics", "mime": "text/calendar",
                               "data": _ICS_UNORDERABLE}]
        return msg


def test_an_unorderable_reschedule_does_not_borrow_the_add_it_by_hand_hint():
    """The `unresolved` arm added for #202 fires INSIDE `if mine:` with a moved start, so
    an entry of ours provably sits at the old instant. It borrowed
    `calendar-unresolved`, whose hint says the entry "could NOT be created or verified --
    add it by hand". Following that books a DUPLICATE and leaves the stale entry.

    `_NEEDS_REVIEW_HINT`'s own header records why that matters: one sentence covering two
    reasons became a false statement the moment the second arrived. This is the third
    time, so the case gets its own reason rather than a re-worded shared one.
    """
    rep, dl, seen, note = _run(_OursAtOldTimeClient())
    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, "an unorderable reschedule recorded nothing"
    assert rows[0].proposal == "calendar-unorderable"
    hint = rows[0].hint
    assert "add it by hand" not in hint, (
        "told the operator to create an entry that already exists at the old time")
    assert "old" in hint.lower() or "existing" in hint.lower(), (
        "the hint must say an entry of ours is already there, at a time now in doubt")
    assert rep.calendar_added == 0
