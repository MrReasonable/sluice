"""Idempotent calendar reconciliation. Idempotency is keyed on the iCal VEVENT UID
(stable across reschedules), with a start-proximity fallback for events Google
auto-added from the recruiter's own invite (which carry no sluice tag)."""
from datetime import datetime, timedelta, timezone


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

    `_aware` FIRST, before isoformat(): `events.list` requires RFC 3339, and a NAIVE start
    serialises without a UTC offset, which the API rejects with HTTP 400. That is not a
    hypothetical -- an unresolvable TZID (Outlook writes Windows zone names) and legal
    RFC 5545 floating time both parse naive. The error escapes reconcile into engine.run's
    per-message handler, so the whole message is dropped, and since a failure skips
    seen.add it re-fails on every later run. Both call sites build this identical pair, so
    it lives here: coercing in one and forgetting the other is exactly how it shipped."""
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
            return "updated"
        return "present"
    if _foreign_at_start(client, cfg, ics):
        return "present"  # a foreign event already covers this slot; do NOT insert or touch it
    if not dry_run:
        client.insert_event(_event_body(cfg, lead_slug, ics))
    return "created"
