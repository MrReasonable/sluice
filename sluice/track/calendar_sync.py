"""Idempotent calendar reconciliation. Idempotency is keyed on the iCal VEVENT UID
(stable across reschedules), with a start-proximity fallback for events Google
auto-added from the recruiter's own invite (which carry no sluice tag)."""
from datetime import datetime, timedelta, timezone

from sluice.core.log import get_logger

_log = get_logger("track.calendar_sync")


def floating_start(ics) -> bool:
    """True if we are about to write a calendar entry whose instant we GUESSED.

    `_event_body` stamps `timeZone: "UTC"` for a naive start, so Google reads the wall-clock
    as UTC. For a 15:30 London invite that books 16:30 local -- an hour late, silently. Both
    the warning and the run report's counter key on this one predicate so they cannot drift."""
    return ics.start is not None and ics.start.tzinfo is None


def _warn_if_floating(ics, outcome):
    """Say out loud that an instant was assumed, at the only place that knows a write
    happened. Before `_window_bounds` coerced the list bounds, this population could not
    reach a write at all -- the list call raised first -- so the fix traded a loud HTTP 400
    for a quiet wrong hour. This is what keeps it loud."""
    if outcome not in ("created", "updated") or not floating_start(ics):
        return
    if ics.tzid_unresolved:
        _log.warning(
            "track: uid %s states TZID %r, which this host cannot resolve; %s the calendar "
            "entry at %s ASSUMING UTC -- verify the time before relying on it",
            ics.uid, ics.tzid_unresolved, outcome, ics.start.isoformat())
    else:
        _log.warning(
            "track: uid %s has a floating (zone-less) DTSTART; %s the calendar entry at %s "
            "ASSUMING UTC -- verify the time before relying on it",
            ics.uid, outcome, ics.start.isoformat())


def _aware(dt):
    """Coerce a naive datetime (floating-time / unresolvable TZID) to UTC so
    comparisons and subtractions never mix naive and aware values."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _event_start(ev):
    s = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _uid_of(ev):
    return (ev.get("extendedProperties", {}).get("private", {}) or {}).get("sluice-track-uid")


def _trunc(dt):
    a = _aware(dt)
    return a.replace(microsecond=0) if a else None


def _window_bounds(cfg, ics):
    """The (timeMin, timeMax) pair for a list_events call centred on `ics.start`.
    PRECONDITION: `ics.start` is not None -- `_aware(None)` is None and the arithmetic below
    raises TypeError. Both callers guard; a third must too.

    `_aware` FIRST, before isoformat(): `events.list` requires RFC 3339 (an external contract
    this repo cannot pin in a test), and a NAIVE start serialises without a UTC offset, which
    the API rejects with HTTP 400. That is not a hypothetical -- three routine inputs parse
    naive: an unresolvable TZID (Outlook writes Windows zone names), legal RFC 5545 floating
    time, and a date-only `VALUE=DATE` DTSTART.

    The error escapes reconcile into engine.run's per-message handler, so this run's whole
    message is abandoned -- no calendar event, no status advance, and no dead-letter row.
    Skipping seen.add leaves it RETRYABLE but not retried forever: `_gmail_query` scopes on a
    day-granular `after:` derived from the lastrun watermark, and `app.py` advances that
    watermark on this very run regardless of `rep.failures`. So a deterministic failure gets
    about a day of retries and is then never queried again (the hazard `_load_lastrun`'s own
    docstring describes). That is why engine.run logs it: the log line is the only surviving
    trace.

    Both call sites build this identical pair, so it lives here -- not because one was once
    coerced and the other forgotten (before this helper NEITHER was), but because the file
    already applied `_aware` to the proximity COMPARISON in `_foreign_at_start` and not to the
    bounds, and one shared definition is what stops a future fix landing in one site only."""
    start = _aware(ics.start)
    window = timedelta(days=cfg.calendar_lookahead_days)
    return (start - window).isoformat(), (start + window).isoformat()


def _find_ours(client, cfg, ics):
    """The event WE created for this ics UID (by our sluice-track-uid tag), or None.
    Never returns a foreign event."""
    if ics.start is None:
        return None
    for ev in client.list_events(*_window_bounds(cfg, ics)):
        if _uid_of(ev) == ics.uid:
            return ev
    return None


def _foreign_at_start(client, cfg, ics):
    """True if a NON-sluice event already sits within calendar_match_minutes of ics.start
    (e.g. Google auto-added the recruiter's own invite). Used only to avoid a duplicate
    insert; such events are NEVER mutated or deleted."""
    if ics.start is None:
        return False
    near = timedelta(minutes=cfg.calendar_match_minutes)
    for ev in client.list_events(*_window_bounds(cfg, ics)):
        if _uid_of(ev) is None:
            est = _event_start(ev)
            if est and abs(_aware(est) - _aware(ics.start)) <= near:
                return True
    return False


def _event_body(cfg, lead_slug, ics):
    tz = "UTC"
    if ics.start is not None and ics.start.tzinfo is not None:
        tz = getattr(ics.start.tzinfo, "key", None) or "UTC"
    return {
        "summary": ics.summary or "Interview",
        "location": ics.location or "",
        "description": (ics.url or ""),
        "start": {"dateTime": ics.start.isoformat() if ics.start else None, "timeZone": tz},
        "end": {"dateTime": (ics.end or ics.start).isoformat() if ics.start else None, "timeZone": tz},
        "extendedProperties": {"private": {
            "sluice-track-uid": ics.uid, "sluice-track-lead": lead_slug}},
    }


def sync_event(client, cfg, *, lead_slug, ics, dry_run=False) -> str:
    ours = _find_ours(client, cfg, ics)
    if ics.cancelled:
        if ours:
            if not dry_run:
                client.delete_event(ours["id"])
            return "cancelled"
        return "present"  # never delete a foreign event
    if ours:
        if _trunc(_event_start(ours)) != _trunc(ics.start):
            if not dry_run:
                client.update_event(ours["id"], _event_body(cfg, lead_slug, ics))
            _warn_if_floating(ics, "updated")
            return "updated"
        return "present"
    if _foreign_at_start(client, cfg, ics):
        return "present"  # a foreign event already covers this slot; do NOT insert or touch it
    if not dry_run:
        client.insert_event(_event_body(cfg, lead_slug, ics))
    _warn_if_floating(ics, "created")
    return "created"
