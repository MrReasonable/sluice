"""A cancel we could not resolve must not be reported as "nothing to do".

#138. `sync_event` returned `"present"` for BOTH "there was no event of ours to delete" and
"we could not determine whether there was". They are not the same fact, and conflating them
is unrecoverable: `reconcile` set `action="calendar"`, which matches none of `engine.run`'s
applied/proposed branches, so no dead-letter row was written -- and then `seen.add(mid)` ran.

Net effect: the run reports `failures=0 calendar_added=0`, the cancelled interview stays in
the calendar, and because the id is now in `seen` the message is NEVER reprocessed. That is
strictly worse than a failure, which at least retries.

`_find_ours` returns None for at least three reasons that are not "nothing to delete":
  1. `ics.start is None` -- a METHOD:CANCEL VEVENT carrying only a UID is legal, and
     `parse_ics` yields exactly that.
  2. the event sits outside the lookahead window (a cancel of a long-rescheduled interview).
  3. the events list was truncated (#137, fixed separately -- but the classes are independent).
"""
from datetime import datetime, timezone

from sluice.track.calendar_sync import sync_event
from sluice.track.config import TrackConfig
from sluice.track.ics import IcsEvent, parse_ics
from tests.test_track_google_client import FakeGoogleClient


def _tagged(uid, start_iso, event_id="ev1"):
    return {"id": event_id, "start": {"dateTime": start_iso},
            "extendedProperties": {"private": {"sluice-track-uid": uid}}}


def _cancel(uid="u1", start=None):
    e = IcsEvent(uid=uid, summary="Screen", start=start)
    e.method = "CANCEL"
    return e


def test_a_cancel_with_no_DTSTART_is_UNRESOLVED_not_present():
    """The legal shape that broke it: `METHOD:CANCEL` + `UID`, no DTSTART.

    `_find_ours` bails on `ics.start is None` before it can look, so the old code answered
    "nothing of ours" to a question it never asked -- while our tagged event sat right there.
    """
    ics = parse_ics("BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    "END:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.cancelled and ics.start is None, "fixture must be the no-DTSTART cancel"
    c = FakeGoogleClient(events=[_tagged("u1", "2026-07-15T10:00:00+00:00")])
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "unresolved"
    assert not c.deleted, "must not guess a deletion either"


def test_a_cancel_that_finds_our_event_still_cancels():
    c = FakeGoogleClient(events=[_tagged("u1", "2026-07-15T10:00:00+00:00")])
    ics = _cancel(start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "cancelled"
    assert c.deleted == ["ev1"]


def test_a_cancel_with_a_start_and_genuinely_nothing_of_ours_is_still_present():
    """The honest "nothing to do" case must keep its quiet answer.

    We looked, in a window we could actually search, and there was no tagged event. Reporting
    `unresolved` here would send a dead-letter row for every cancellation of an interview we
    never put in the calendar -- crying wolf, which is how a real signal gets ignored.
    """
    c = FakeGoogleClient(events=[])
    ics = _cancel(start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "present"
    assert not c.deleted


def test_a_foreign_event_is_still_never_deleted():
    # The safety property this whole module exists for, unchanged.
    c = FakeGoogleClient(events=[{"id": "foreign",
                                  "start": {"dateTime": "2026-07-15T10:00:00+00:00"}}])
    ics = _cancel(start=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc))
    assert sync_event(c, TrackConfig(), lead_slug="example-lead", ics=ics) == "present"
    assert not c.deleted


def test_an_unresolved_cancel_is_PROPOSED_so_a_human_sees_it(tmp_path):
    """`reconcile` must route it somewhere durable.

    `action="calendar"` matched none of engine.run's branches, so nothing was recorded and
    `seen.add` ran regardless -- the message was consumed with the work undone. `proposed` is
    the existing path for "we could not act, a human must", and it already writes a
    dead-letter row with a `track dismiss --id` lever.
    """
    import pathlib

    from sluice.core.vault import Vault
    from sluice.track import reconcile as R
    from sluice.track.classify import Event

    root = tmp_path / "v"
    leads = root / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Example Tidal - EM.md").write_text(
        '---\ncompany: "Example Tidal"\nrole: "EM"\nstatus: interview\n---\n\nBODY\n')
    v = Vault(str(root))
    notes = {n.slug: n for n in v.read_leads() if n.slug == "Example Tidal - EM"}

    ics = parse_ics("BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    "END:VEVENT\r\nEND:VCALENDAR\r\n")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics)
    res = R.reconcile(ev, notes, v, TrackConfig(),
                      FakeGoogleClient(events=[_tagged("u1", "2026-07-15T10:00:00+00:00")]))
    assert res.calendar == "unresolved"
    assert res.action == "proposed", "an unresolved cancel must reach a human, not vanish"
    assert res.proposal, "the dead-letter row needs a proposal string"
    assert pathlib.Path(leads / "Example Tidal - EM.md").read_text().count("status: interview") == 1, \
        "a cancellation must still never advance or regress the status"


def test_the_unresolved_cancel_hint_does_NOT_offer_to_advance_the_lead():
    """Routing to `proposed` inherits `_PROPOSE_TARGET[ev.type]`, and `ev.type` for a
    cancelled interview invite is "interview".

    So the operator was told an interview was cancelled and handed a copy-pasteable
    `confirm --to interview` -- a command that RUNS and books the thing that was cancelled.
    `engine.py` already holds the standard for this: an unrunnable command is worse than an
    honest "look at this yourself". A command that runs and is wrong is worse still.
    """
    import pathlib

    from sluice.core.vault import Vault
    from sluice.track import engine as E
    from sluice.track.config import TrackConfig
    from tests.test_track_engine import FakeBackend, OneMsgClient, _dl

    import tempfile
    root = pathlib.Path(tempfile.mkdtemp())
    leads = root / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Example Tidal - Analyst.md").write_text(
        '---\ncompany: "Example Tidal"\nrole: "Analyst"\nstatus: interview\n---\n\nBODY\n')
    v = Vault(str(root))

    class _CancelClient(OneMsgClient):
        def get_message(self, mid):
            msg = super().get_message(mid)
            msg["attachments"] = [{
                "filename": "invite.ics", "mime": "text/calendar",
                "data": (b"BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                         b"END:VEVENT\r\nEND:VCALENDAR\r\n")}]
            return msg

    dl = _dl()
    E.run(v, TrackConfig(), _CancelClient(),
          FakeBackend('{"lead": "Example Tidal - Analyst", "type": "interview", '
                      '"confidence": 0.9, "when": null, "links": [], "materials": [], '
                      '"summary": "cancelled"}'),
          seen=set(), deadletter=dl, now_iso="2026-07-10T12:00:00+00:00")

    rows = [e for e in dl.open_entries() if e.message_id == "m1"]
    assert rows, "an unresolved cancel must leave a durable row"
    hint = rows[0].hint
    assert "--to interview" not in hint, (
        f"the row hands the operator a command that BOOKS the cancelled interview: {hint}")
    assert "u1" in hint, f"the hint should name the UID so the entry can be found: {hint}"
