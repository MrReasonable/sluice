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

# The private extended property our events are tagged with. ONE definition, because there are
# now THREE sites that have to agree: `_event_body` writes it, `_uid_of` reads it back, and
# `_find_ours_by_tag` asks Google to filter on it server-side. A literal repeated across those
# three drifts silently, and which site drifts decides how bad it is: drift the QUERY and the
# lookup stops matching, i.e. #146 reinstated inside its own fix; drift the WRITE and every
# invite stops recognising its own entry on the very next run, which is worse -- a fresh
# duplicate every run rather than only after a long reschedule.
#
# Google silently DROPS a private-property key over 44 characters, so a rename that overshoots
# would not error, it would simply stop tagging. Pinned by a test rather than asserted here.
_UID_KEY = "sluice-track-uid"

# Google silently TRUNCATES a private-property value past 1024 characters. `parse_ics` puts no
# bound on a UID (it is `value.strip()` off a third-party invite), so a longer one would be
# stored cut short while `_uid_of` and the tag query both searched for the whole string --
# our own event unfindable by window OR tag, and a fresh duplicate inserted every single run,
# forever. That is strictly worse than #146, which at least needs a long reschedule to fire.
#
# Truncating on BOTH sides makes the stored value and the searched value the same string by
# construction, so the round trip closes at whatever length Google actually keeps. The trade:
# two UIDs sharing a 1024-character prefix would now collide. That is the right way round --
# a collision needs two invites agreeing for 1024 characters and differing after, while the
# status quo fails for a SINGLE long UID and fails on every run.
_UID_VALUE_MAX = 1024


def _uid_tag(uid):
    """The tag value for `uid` -- what we WRITE and what we SEARCH FOR, one definition."""
    return (uid or "")[:_UID_VALUE_MAX]


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
    """Our tag on `ev`, or None if it carries none. An EMPTY tag counts as none.

    `parse_ics` admits a VEVENT with a DTSTART and no UID -- `ics.py`'s final line is
    `ev.uid or ev.start` -- so `_event_body` can write `sluice-track-uid: ""`. An empty string
    is not an identity, and reading it back as a real tag made `_find_ours` match `"" == ""`.
    The +/- lookahead window used to contain that; a query with NO time bound does not. One
    firm's UID-less invite would resolve onto another firm's UID-less event and rewrite its
    summary, start and lead -- or DELETE it on a cancel -- reporting `updated`/`cancelled` with
    nothing routed to a human. Absent identity must mean abstain, never match-everything.

    Returning None also tells `_foreign_at_start` the truth: an event whose tag is empty is
    indistinguishable from an untagged one, so it counts as foreign and SUPPRESSES a duplicate
    insert at the same slot rather than being quietly double-booked."""
    tag = (ev.get("extendedProperties", {}).get("private", {}) or {}).get(_UID_KEY)
    return tag or None


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
    """EVERY event WE created for this ics UID (by our sluice-track-uid tag), in order.
    Never returns a foreign event.

    A LIST, not the first hit. "The first match" was a guess dressed as an answer: `events.list`
    is called with no `orderBy`, so which of several arrives first is unspecified, and the
    population that has several is exactly the one this module is being fixed for -- an operator
    carrying #146's orphan AND its duplicate has two events under this tag. On a cancel,
    deleting the first and reporting `cancelled` asserts the interview is gone while the other
    entry is still in the calendar. The caller decides what more-than-one means; this function's
    job is to stop hiding it.

    Runs over BOTH result sets -- the window scan and the tag query -- and that is what keeps
    Google's server-side `privateExtendedProperty` filter a narrowing hint rather than an
    authority. However that filter behaves, an event is UPDATED OR DELETED only after this
    local comparison agrees the UID is ours. (Not "reaches `sync_event`": the whole window
    result reaches it and is handed to `_foreign_at_start` untouched. What the comparison gates
    is the write.)

    An invite with no UID identifies nothing, so it matches nothing.

    That guard is REDUNDANT BY CONSTRUCTION today and is kept deliberately, which is worth
    saying out loud rather than leaving for someone to rediscover: `_uid_of` already maps an
    empty tag to None and `None == ""` is False, so the loop below could not match a UID-less
    ics anyway. A mutation witness confirms it -- deleting this guard alone kills no test, and
    so does deleting `_uid_of`'s normalisation alone. Each covers for the other.

    It stays because the two are guarding different things and only one of them is obvious.
    `_uid_of`'s normalisation exists so an empty-tagged event reads as UNTAGGED (see there);
    that it also happens to stop this comparison matching is a side effect. A future change to
    `_uid_of` that looked purely cosmetic would silently re-open a cross-lead clobber, and this
    line is what makes that impossible rather than merely unlikely."""
    if not ics.uid:
        return []
    want = _uid_tag(ics.uid)
    return [ev for ev in events if _uid_of(ev) == want]


def _merge_by_id(*groups):
    """One de-duplicated list of events, first occurrence winning.

    The window scan and the tag query overlap by design -- an entry sitting at the invite's
    current time is found by both -- so the union has to be taken by identity or a cancel would
    issue `delete_event` twice for the same id and the second call would 404 out of a run that
    had already done the work."""
    seen, out = set(), []
    for group in groups:
        for ev in group:
            key = ev.get("id")
            if key in seen:
                continue
            seen.add(key)
            out.append(ev)
    return out


def _find_ours_by_tag(client, ics) -> tuple:
    """`(mine, unsettled)` from a UID-tag query, unbounded by time. A LIST, like `_find_ours`.

    FETCHES. Named to say so: `_find_ours` beside it is pure and takes a list, a split this
    module paid for (`_window`'s docstring counts the 400 sequential round trips that made it
    worth doing), and a helper that reads like its sibling while opening a connection is how
    that split gets eroded by the next caller. This is the impure twin of `_window`.

    `unsettled` means "the question was not answered, and absence here is not evidence" -- the
    same thing `truncated` means to `_window`, deliberately a separate word because the causes
    differ: a short page versus a query never asked. The CALLER folds it in; see `sync_event`.

    WHEN it is asked is a deliberate asymmetry, and the two arms are asymmetric because their
    failures are:

      - SCHEDULING asks only when the window found nothing of ours. The window already answers
        the steady state (an invite booked at a time that has not moved), so this stays off the
        hot path. Leaving a stale orphan elsewhere is misleading but visible, and nothing is
        destroyed.
      - CANCELLING asks ALWAYS, even when the window found an entry. Completeness is the whole
        point of a removal: deleting the entry at the current time while another of ours sits
        at the old one reports `cancelled` -- a positive claim the interview is gone -- and
        leaves a cancelled interview on the calendar. That is #138's harm exactly, arriving
        through a new door. Cancels are rare, so always asking costs little.

    A SUPPLEMENT to the window scan, never a replacement -- the shape that makes it safe to
    build on a contract this repo cannot execute. `privateExtendedProperty` is confirmed to
    EXIST (see `find_events_by_private_property`) but nothing offline confirms it MATCHES, and
    a filter matching nothing returns an empty list rather than an error. Every way it can be
    wrong is answered structurally rather than trusted:

      - matches too little (the empty list): `mine` stays empty, nothing is added to
        `truncated`, and `sync_event` continues down the exact path it took before this
        function existed -- the bug we already ship, never a new one.
      - matches too much: `_find_ours` re-checks the UID locally on every returned event, so
        the server-side filter is a narrowing HINT and the local comparison is the authority.
        A filter that ignored the constraint entirely could not hand us somebody else's event.
        The cost of that mode is a full-calendar read up to `calendar_max_events`, which is the
        argument FOR sharing that generous cap rather than giving this query a tight one: a
        tight cap would return `truncated` on every invite, become `unresolved`, and nothing
        would ever book.
      - REJECTED (an unknown parameter is a TypeError, a malformed value a 400): the exception
        propagates. That is louder and more expensive than the other two -- a rejected filter
        means nothing books at all -- but it is the right direction and the one this repo
        already chose for the seam (#142: transport errors become retryable per-message
        failures, not swallowed). Catching it here would fall through to an INSERT, which for
        an out-of-window event is precisely the silent duplicate #146 is about.

    So the honest guarantee is not "the worst case is today's behaviour" -- a rejected query is
    worse than today. It is that NO failure mode of this filter silently WRITES.

    An invite with no UID identifies nothing, and must never become a query for the empty
    string: that asks Google for every UID-less event sluice ever created, anywhere. See
    `_uid_of` for what matching on it costs."""
    if not ics.uid:
        return [], False
    want = _uid_tag(ics.uid)
    tagged, tag_truncated = client.find_events_by_private_property(_UID_KEY, want)
    return _find_ours(tagged, ics), tag_truncated


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
            _UID_KEY: _uid_tag(ics.uid), "sluice-track-lead": lead_slug}},
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

    An event that exists but sits OUTSIDE the +/- calendar_lookahead_days window used to be
    folded into `present` -- indistinguishable from absence, so a reschedule beyond that window
    inserted a duplicate and orphaned the original at the old time (#146). It is no longer
    guessed at: when the window scan finds nothing of ours, `_find_ours_by_tag` asks Google for
    the sluice-track-uid tag directly, with no time bounds, and a tag is not something a
    reschedule can move.

    HONEST LIMIT, and it has moved rather than closed. That tag query rests on
    `privateExtendedProperty`, which Google documents (with a worked example) and whose presence
    is confirmed against the discovery document -- but which nothing in this repo can EXECUTE.
    Unexecuted, not unspecified: the distinction matters to whoever decides the ratchet below
    can be lifted. So the lookup is a supplement to the window scan and never a replacement.
    An empty tag query leaves `sync_event` on precisely the path it took before, which is this
    same limit and not a new one.

    Two things that paragraph must not be read as promising. A rejected query -- an unknown
    parameter, a 4xx, a transport failure -- propagates and abandons the message, which is
    WORSE than the old behaviour and is accepted deliberately (see `_find_ours_by_tag`); loud
    beats a silent duplicate. And a tag HIT on a truncated window now writes where it used to
    abstain, because a positive UID identification from a separately complete query genuinely
    does beat a short read. What holds unconditionally is narrower and worth stating exactly:
    no failure mode of this filter silently WRITES.

    Whoever executes the filter against a live calendar can then let a clean tag query clear
    `truncated` too -- see `_find_ours_by_tag` for what that would buy and what it would cost."""
    if ics.start is None:
        # No start, so no window can be built. A bare `METHOD:CANCEL` + `UID` VEVENT is legal
        # RFC 5545 and lands here, and `reconcile`'s cancel path calls us with no start guard
        # (unlike its scheduling path), so this is reached in production rather than in theory.
        #
        # A CANCEL is now answerable anyway, which it was not before #146: a tag lookup needs
        # no window at all -- the UID alone identifies the entry -- so "we cannot even build a
        # window" stopped being a reason to give up. This was cause #1 of the three that make
        # `_find_ours` come back empty (see tests/test_track_cancel_unresolved.py); the other
        # two are already closed, and leaving this one open would have meant answering
        # `unresolved` to a question we had just acquired the means to answer, sending the
        # operator to delete by hand.
        if ics.cancelled:
            mine, _unsettled = _find_ours_by_tag(client, ics)
            if mine:
                if not dry_run:
                    for ev in mine:
                        client.delete_event(ev["id"])
                return "cancelled"
            # NOT `present` when the tag query comes back empty, even though it searched
            # without a window and "nothing of ours" is what `present` means. That would be
            # #138 rebuilt on an assumption: `present` is a positive claim, and the only
            # evidence for it here is a filter nobody has executed against a live calendar. If
            # it silently matches nothing, every startless cancel would answer "nothing to do"
            # while the cancelled interview stayed in the operator's calendar and `seen.add`
            # consumed the message -- which is precisely the failure this arm was given
            # `unresolved` for.
            #
            # So the lookup can only IMPROVE this branch, never weaken it: a hit cancels, and
            # anything else falls back to the answer that was already here. That also makes
            # `unsettled` moot, hence the discard -- both of its values lead here.
            return "unresolved"
        # The non-cancel arm still cannot proceed -- `_event_body` has no instant to write.
        # It used to answer `present`, a positive claim about a search that never happened.
        # Unreachable today because `reconcile` guards `ics.start is not None` before the only
        # non-cancel call site, but the honest value costs nothing and the wrong one sits
        # waiting for a third caller.
        return "unresolved"
    events, truncated = _window(client, cfg, ics)
    mine = _find_ours(events, ics)
    # The window is centred on the start that may have just CHANGED, so a long reschedule moves
    # our own event out of it. Ask for the tag instead, which no reschedule can move.
    #
    # A cancel asks even when the window already found something, because a removal has to be
    # COMPLETE to be reported as one: deleting the entry at the current time while another of
    # ours sits at the old one would answer `cancelled` with a cancelled interview still in the
    # calendar. Scheduling asks only on the miss, keeping the steady state to one round trip.
    tag_unsettled = False
    if not mine or ics.cancelled:
        far, tag_unsettled = _find_ours_by_tag(client, ics)
        # Union by id: an entry at the invite's current time is found by BOTH searches, and
        # without this a cancel would issue `delete_event` twice for it and the second call
        # would 404 out of a run that had already done the work.
        mine = _merge_by_id(mine, far)
        # The one-way ratchet, kept at the site that branches on it rather than inside the
        # helper. A tag query that could not settle the question WIDENS `truncated`; a clean
        # one never narrows it. Clearing it would license an insert on the strength of the
        # matching behaviour nobody has executed against a live calendar -- an unverified
        # external contract converted into a silent duplicate, the exact harm this removes.
        #
        # `not mine and ...` is right for the SCHEDULING arm, which needs one positive
        # identification and nothing more. It is NOT sufficient for a cancel, which needs
        # completeness -- so that arm reads `tag_unsettled` directly rather than this flag.
        truncated = truncated or (not mine and tag_unsettled)
    if ics.cancelled:
        if mine:
            # ALL of them. The interview is cancelled, so every entry we created for this UID
            # is stale -- and an operator arriving with #146's orphan plus its duplicate is
            # precisely who this branch meets. Deleting one and reporting `cancelled` is the
            # #138 conflation rebuilt: a positive claim of removal over an incomplete one.
            if not dry_run:
                for ev in mine:
                    client.delete_event(ev["id"])
            if tag_unsettled:
                # We removed everything we could SEE, and still cannot call it done. The tag
                # query was short, so another entry under this UID may be off-page -- and
                # `cancelled` is exactly the positive claim of complete removal that the
                # paragraph above refuses to make on incomplete evidence.
                #
                # The `truncated` flag cannot carry this: it is computed as
                # `not mine and tag_unsettled`, which is deliberately False here BECAUSE we
                # found something, and the `if mine:` branch returns before it is ever read.
                # Reported after the deletes, not instead of them: the work we could do is
                # still worth doing, and a human is told the rest is unconfirmed.
                return "unresolved"
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
    if len(mine) > 1:
        # Several entries carry this UID and only a human can say which is real. Moving an
        # arbitrary one is a guess dressed as an answer -- `events.list` is called with no
        # `orderBy`, so "the first" is whatever Google happened to return -- and it would
        # leave the others behind while reporting success. The cancel arm above can delete
        # them all because removal is unambiguous; a reschedule cannot.
        # Names the Google EVENT IDS, not the ics UID. Better on both counts. They are what
        # the operator actually needs -- the message says "delete the stale entries", and an
        # event id is what identifies one in the calendar UI or the API, whereas the UID
        # identifies the invite and cannot be searched for by hand. And an inbound UID is
        # attacker-and-counterparty-supplied text that sometimes encodes the sender's domain,
        # which is precisely the leak `search_messages` keeps its query out of the log for:
        # a log line travels further than the mailbox does. A Google event id is opaque and
        # says nothing about who the interview is with.
        _log.warning(
            "track: %d calendar entries of ours carry the same invite id (%s) -- refusing to "
            "guess which one the reschedule applies to. Delete the stale entries and the next "
            "run will reconcile it.", len(mine), ", ".join(sorted(
                str(ev.get("id")) for ev in mine)))
        return "unresolved"
    if mine:
        ours = mine[0]
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
