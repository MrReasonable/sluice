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
    raises TypeError. ONE caller now (`_window`), and the guard sits one frame further out in
    `sync_event`; `_find_ours` and `_foreign_at_start` take events and no longer fetch. A
    second caller must either guard or route through `sync_event`.

    `_aware` FIRST, before isoformat(): `events.list` requires RFC 3339 (an external contract
    this repo cannot pin in a test), and a NAIVE start serialises without a UTC offset, which
    the API rejects with HTTP 400. That is not a hypothetical -- three routine inputs parse
    naive: an unresolvable TZID (Outlook writes Windows zone names), legal RFC 5545 floating
    time, and a date-only `VALUE=DATE` DTSTART.

    The error escapes reconcile into engine.run's per-message handler, so this run's whole
    message is abandoned -- no calendar event and no status advance. Skipping seen.add leaves
    it RETRYABLE but not retried forever: `_gmail_query` scopes on a day-granular `after:`
    derived from the lastrun watermark, and `app.py` advances that watermark on this very run
    regardless of `rep.failures`. So a deterministic failure gets about a day of retries and
    is then never queried again (the hazard `_load_lastrun`'s own docstring describes).

    It is no longer trace-less, which this paragraph used to claim: #139 made the failure
    durable, so it now leaves an `ev_type=failure` dead-letter row, a named entry in
    `rep.failures`, a digest line and a notification.

    It exists as a helper -- rather than inline in its single caller -- not because one site was
    coerced and the other forgotten (before this helper NEITHER was), but because the file
    already applied `_aware` to the proximity COMPARISON in `_foreign_at_start` and not to the
    bounds, and one shared definition is what stops a future fix landing in one site only."""
    start = _aware(ics.start, assumed_zone(cfg)[0])
    window = timedelta(days=cfg.calendar_lookahead_days)
    return (start - window).isoformat(), (start + window).isoformat()


def _window(client, cfg, ics) -> tuple:
    """`(events, truncated)` for this ics's window -- fetched ONCE per sync_event.

    `_find_ours` and `_foreign_at_start` used to make the identical call with identical
    bounds. That was 2 requests per invite before #137 lifted the page cap and up to 20
    after, so a 20-invite run could issue 400 sequential round trips against a 90-day window
    that `singleEvents=True` expands recurrences into.

    `truncated` comes back because a truncated result set cannot answer "is there an event of
    ours" -- absence in a short read is not evidence of absence.

    `list_events` returns the pair unconditionally, so there is nothing to probe for here. The
    version that probed (`return_truncated=True` inside `try/except TypeError`) was broken: a
    `**kwargs` client swallows an unknown kwarg instead of raising, so the except never ran and
    the bare list fell through to this unpack. See `list_events` for the executed evidence."""
    return client.list_events(*_window_bounds(cfg, ics))


def _find_ours(events, ics):
    """The event WE created for this ics UID (by our sluice-track-uid tag), or None.
    Never returns a foreign event."""
    for ev in events:
        if _uid_of(ev) == ics.uid:
            return ev
    return None


def _foreign_at_start(events, cfg, ics):
    """True if a NON-sluice event already sits within calendar_match_minutes of ics.start
    (e.g. Google auto-added the recruiter's own invite). Used only to avoid a duplicate
    insert; such events are NEVER mutated or deleted."""
    near = timedelta(minutes=cfg.calendar_match_minutes)
    tz = assumed_zone(cfg)[0]
    for ev in events:
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
    """One of: created | updated | cancelled | present | unresolved | foreign.

    `unresolved` is the answer to a question we could not ASK, or asked over a window we know
    was incomplete. `foreign` means we looked, found an event at that slot that sluice did NOT
    create -- routinely the sender's own invite, auto-added by Google -- and deliberately left
    it alone; the calendar work is therefore unfinished and a human has to look. Both are
    distinct from `present`, which means we searched a complete window and there was nothing
    of ours.

    Keeping this list current is load-bearing, not tidiness: it is the contract every caller
    branches on, and the paragraph below records what a value matching NO branch in reconcile
    cost the last time it happened. Conflating them cost a cancelled interview its deletion and
    then consumed the message: reconcile mapped the old `present` to an action engine.run
    ignored, so no dead-letter row was written and `seen.add` ran anyway (#138).

    HONEST LIMIT: one cause remains folded into `present`. An event that exists but sits
    OUTSIDE the +/- calendar_lookahead_days window is indistinguishable from absence without a
    UID-keyed query, so it is not guessed at here. That is issue #146 (a reschedule beyond
    `calendar_lookahead_days` orphans our event and inserts a duplicate) -- tracked, not
    merely noted, so nobody re-derives it from scratch.

    Google's `events.list` accepts a `privateExtendedProperty` filter, which would let
    `_find_ours` search by the sluice-track-uid tag with no time window at all -- worth
    confirming and building on, and it would make `unresolved` rare rather than routine."""
    if ics.start is None:
        # We cannot even build a window, so nothing was searched. A bare `METHOD:CANCEL` +
        # `UID` VEVENT is legal RFC 5545 and lands here.
        #
        # `unresolved` for BOTH arms. The non-cancel arm used to answer `present`, which this
        # docstring defines as "we searched a complete window and there was nothing of ours"
        # -- a positive claim about a search that never happened. Unreachable today, because
        # `reconcile` guards `ics.start is not None` before the only non-cancel call site, but
        # the honest value costs nothing and the wrong one sits waiting for a third caller.
        return "unresolved"
    events, truncated = _window(client, cfg, ics)
    ours = _find_ours(events, ics)
    if ics.cancelled:
        if ours:
            if not dry_run:
                client.delete_event(ours["id"])
            return "cancelled"
        if truncated:
            # We looked, but the window was SHORT and we know it -- our event may be one of
            # the ones that did not fit. Saying `present` here asserts a fact from an
            # incomplete search, which is how a cancelled interview stayed in the calendar
            # with `seen.add` consuming the message and no trace left anywhere.
            return "unresolved"
        if _foreign_at_start(events, cfg, ics):
            # Something we did not create sits at that slot -- routinely the recruiter's own
            # invite, which Google auto-adds from the mail. We must never delete a foreign
            # event, and `present` ("we searched and there was nothing of ours") is true but
            # useless here: the operator's calendar still shows a cancelled interview and
            # nothing tells them. Distinct value so it can reach a human.
            return "foreign"
        return "present"  # nothing of ours, and nothing else at that slot
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
    if _foreign_at_start(events, cfg, ics):
        # A foreign event covers this slot, so we do NOT insert or touch it -- that safety
        # property is right and unchanged. Reporting it as `present` was not: the interview
        # was never booked, the status still advanced, and `seen.add` consumed the message.
        # `calendar_match_minutes` defaults to 30, so ANY untagged event within half an hour
        # -- a standup, a dentist appointment -- suppresses the booking silently.
        return "foreign"
    if truncated:
        # Our own entry may be off-page, so inserting would DUPLICATE it. Refusing and
        # surfacing beats silently double-booking an interview.
        return "unresolved"
    if not dry_run:
        client.insert_event(_event_body(cfg, lead_slug, ics))
    _warn_if_floating(cfg, ics, "created")
    return "created"
