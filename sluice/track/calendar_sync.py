"""Idempotent calendar reconciliation. Idempotency is keyed on the iCal VEVENT UID
(stable across reschedules), with a start-proximity fallback for events Google
auto-added from the recruiter's own invite (which carry no sluice tag)."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from sluice.core.log import get_logger

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_log = get_logger("track.calendar_sync")


@lru_cache(maxsize=None)
def _resolve_zone(name):
    """(tzinfo, effective_name) for the configured assume-this zone.

    Falls back to UTC rather than raising: a typo in config must not start dropping every
    invite, which is the failure this whole module has just been fixed for. lru_cache so the
    warning fires ONCE per process rather than per event -- a misconfiguration is one fact,
    not one fact per message."""
    if not name or name == "UTC":
        return timezone.utc, "UTC"
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name), name
        except Exception:
            _log.warning(
                "track: calendar_assumed_timezone %r is not a timezone this host can resolve; "
                "falling back to UTC. Zone-less invites will be booked in UTC.", name)
    return timezone.utc, "UTC"


def assumed_zone(cfg):
    return _resolve_zone(getattr(cfg, "calendar_assumed_timezone", "") or "UTC")


def floating_start(ics) -> bool:
    """True if we are about to write a calendar entry whose instant we GUESSED.

    `_event_body` stamps `timeZone: "UTC"` for a naive start, so Google reads the wall-clock
    as UTC. For a 15:30 London invite that books 16:30 local -- an hour late, silently. Both
    the warning and the run report's counter key on this one predicate so they cannot drift."""
    return ics.start is not None and ics.start.tzinfo is None


def _warn_if_floating(cfg, ics, outcome):
    """Say out loud that an instant was assumed, at the only place that knows a write
    happened. Before `_window_bounds` coerced the list bounds, this population could not
    reach a write at all -- the list call raised first -- so the fix traded a loud HTTP 400
    for a quiet wrong hour. This is what keeps it loud.

    The warning stays even when `calendar_assumed_timezone` is set: a configured zone makes
    the guess BETTER, never certain. The invite still stated no instant."""
    if outcome not in ("created", "updated") or not floating_start(ics):
        return
    zone = assumed_zone(cfg)[1]
    if ics.tzid_unresolved:
        _log.warning(
            "track: uid %s states TZID %r, which this host cannot resolve; %s the calendar "
            "entry at %s ASSUMING %s -- verify the time before relying on it",
            ics.uid, ics.tzid_unresolved, outcome, ics.start.isoformat(), zone)
    else:
        _log.warning(
            "track: uid %s has a floating (zone-less) DTSTART; %s the calendar entry at %s "
            "ASSUMING %s -- verify the time before relying on it",
            ics.uid, outcome, ics.start.isoformat(), zone)


def _aware(dt, tz=timezone.utc):
    """Coerce a naive datetime (floating-time / unresolvable TZID) to `tz` so comparisons and
    subtractions never mix naive and aware values.

    `tz` MUST be the same zone `_event_body` stamps, or the entry we booked and the entry we
    later look for describe different instants: `_trunc(_event_start(ours)) != _trunc(start)`
    on every run, so sync_event reports `updated` and issues a real update_event forever."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=tz)


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


def _trunc(dt, tz=timezone.utc):
    a = _aware(dt, tz)
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
    start = _aware(ics.start, assumed_zone(cfg)[0])
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
    tz = assumed_zone(cfg)[0]
    for ev in client.list_events(*_window_bounds(cfg, ics)):
        if _uid_of(ev) is None:
            est = _event_start(ev)
            if est and abs(_aware(est, tz) - _aware(ics.start, tz)) <= near:
                return True
    return False


def _event_body(cfg, lead_slug, ics):
    # An AWARE start carries its own offset in `dateTime`, so its zone is a fact, not a
    # guess -- `timezone.utc` (a `Z`-suffixed DTSTART) has no `.key` and is UTC by
    # definition, and stamping the CONFIGURED zone on it instead would misbook a genuinely
    # UTC invite. Only a naive start falls through to the assumption.
    assumed = assumed_zone(cfg)[1]
    if ics.start is None or ics.start.tzinfo is None:
        tz = assumed
    else:
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
    """One of: created | updated | cancelled | present | unresolved.

    `unresolved` is the answer to a question we could not ASK -- distinct from `present`,
    which means we looked and there was nothing of ours. Conflating them cost a cancelled
    interview its deletion and then consumed the message: reconcile mapped the old `present`
    to an action engine.run ignored, so no dead-letter row was written and `seen.add` ran
    anyway (#138)."""
    ours = _find_ours(client, cfg, ics)
    if ics.cancelled:
        if ours:
            if not dry_run:
                client.delete_event(ours["id"])
            return "cancelled"
        if ics.start is None:
            # We never searched. `_find_ours` bails on a missing start, so "no event of ours"
            # here is not a finding -- and a bare `METHOD:CANCEL` + `UID` VEVENT, with no
            # DTSTART, is legal and is exactly what some senders emit. Saying `present` to
            # this is asserting a fact from an unasked question.
            return "unresolved"
        return "present"  # never delete a foreign event
    if ours:
        # Same zone the body was stamped with, or the instant we booked and the instant we
        # compare differ by that offset and every run reports `updated` and re-writes it.
        tz = assumed_zone(cfg)[0]
        if _trunc(_event_start(ours), tz) != _trunc(ics.start, tz):
            if not dry_run:
                client.update_event(ours["id"], _event_body(cfg, lead_slug, ics))
            _warn_if_floating(cfg, ics, "updated")
            return "updated"
        return "present"
    if _foreign_at_start(client, cfg, ics):
        return "present"  # a foreign event already covers this slot; do NOT insert or touch it
    if not dry_run:
        client.insert_event(_event_body(cfg, lead_slug, ics))
    _warn_if_floating(cfg, ics, "created")
    return "created"
