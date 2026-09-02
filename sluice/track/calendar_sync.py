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

# The lead an event was created for. Hoisted to a constant when `_ours_at_start` became
# its first READER (#203) -- `_event_body` had been writing the literal since before
# that, so the pair is a writer and a reader, not two readers. Same reason as `_UID_KEY`
# above: two sites that must agree. Drift the reader and the same-slot rule silently
# stops matching, which is the duplicate-insert defect reinstated inside its own fix.
_LEAD_KEY = "sluice-track-lead"

# The ics SEQUENCE the event was last written from (#202). RFC 5545 makes SEQUENCE the
# arbiter between two VEVENTs sharing a UID, and `parse_ics` already reads it -- nothing
# used it. Two invites 96 seconds apart, the second an `Updated:` carrying the corrected
# day, were applied in whatever order the message search returned them, so the OLDER one
# could rewrite the event back to the superseded day and report success.
#
# Stored as a STRING because that is what Google's private properties hold; read back
# through `_seq_of`, which answers None for absent or unparseable. None must mean "cannot
# compare, so apply": every event already in a user's calendar predates this tag, and
# refusing those would freeze them against real reschedules.
_SEQ_KEY = "sluice-track-seq"

# The DTSTAMP the event was last written from (#202) -- RFC 5545's SECOND arbiter, used
# only to break a SEQUENCE tie. Written only when the invite carried one, so "absent"
# survives the round trip and keeps meaning "cannot compare": plenty of senders ship every
# revision as SEQUENCE:0, and for those this is the only thing that orders them.
_STAMP_KEY = "sluice-track-stamp"


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


def _warn_if_floating(cfg, ics, outcome, lead_slug, event_id=None, dry_run=False):
    """Say out loud that an instant was assumed, at the only place that knows a write
    happened. Before `_window_bounds` coerced the list bounds, this population could not
    reach a write at all -- the list call raised first -- so the fix traded a loud HTTP 400
    for a quiet wrong hour. This is what keeps it loud.

    The warning stays even when `calendar_assumed_timezone` is set: a configured zone makes
    the guess BETTER, never certain. The invite still stated no instant.

    Identifies the entry by its Google EVENT ID and the lead, not by the inbound ics UID it
    used to name. A UID is counterparty-supplied text that sometimes encodes the sender's
    domain, and a log line travels further than the mailbox does -- the rule `search_messages`
    already keeps its query out of the log for. `lead_slug` is sluice's OWN identifier rather
    than anything the sender chose, so it carries that hazard not at all, and it is what keeps
    the message identifiable on a dry run where no event id exists yet.

    An event id is also the more useful handle. This message asks the operator to go and VERIFY
    an hour; an event id finds that entry in the calendar UI or the API, whereas a UID
    identifies the invite and cannot be searched for by hand.

    SCOPE of that swap, stated exactly rather than as "the class is closed": the three WARNINGS
    that named a UID -- these two and the duplicate-entry one in `sync_event` -- no longer do.
    `engine.py`'s `_NEEDS_REVIEW_HINT` table still renders the UID into all four dead-letter
    hints, deliberately and not by oversight. Those rows report that sluice could NOT find or
    act on an entry, so there is no event id to offer in the first place, and the UID is the
    operator's only handle back to the invite. The exposure differs too: a hint is written to a
    local dead-letter row the operator reads, where a warning goes to a log stream that gets
    captured and pasted. If that judgement is ever revisited, it is one change across four
    templates and their tests, not a gap someone has to rediscover.

    `event_id` is None on a dry run of a CREATE, where nothing was written and no id exists. A
    dry-run UPDATE does pass one -- the entry being described already exists -- so the tense of
    the verb, not the presence of an id, is what marks a preview."""
    if outcome not in ("created", "updated") or not floating_start(ics):
        return
    zone = assumed_zone(cfg)[1]
    did = f"would have {outcome}" if dry_run else outcome
    where = f"the calendar entry {event_id}" if event_id else "a calendar entry"
    if ics.tzid_unresolved:
        _log.warning(
            "track: an invite for %s states TZID %r, which this host cannot resolve; %s %s at "
            "%s ASSUMING %s -- verify the time before relying on it",
            lead_slug, ics.tzid_unresolved, did, where, ics.start.isoformat(), zone)
    else:
        _log.warning(
            "track: an invite for %s has a floating (zone-less) DTSTART; %s %s at %s "
            "ASSUMING %s -- verify the time before relying on it",
            lead_slug, did, where, ics.start.isoformat(), zone)


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


def _private(ev):
    """`ev`'s private extended properties, or {} -- the ONE spelling of this access.

    Four readers walk the same three levels, each of which can be absent or None on a real
    Google payload. Repeating it is the "two sites must agree" hazard this module already
    hoists key names to avoid, one level down.
    """
    return ((ev.get("extendedProperties") or {}).get("private") or {})


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
    tag = _private(ev).get(_UID_KEY)
    return tag or None


def _seq_of(ev):
    """The revision we last wrote this event from, or None when it cannot be read.

    None on BOTH absent and unparseable, and both mean "apply" at the call site -- an
    event we cannot date is one we have no grounds to refuse an update for. That is the
    same direction `ics.py` takes with an unparseable SEQUENCE on the wire.
    """
    raw = _private(ev).get(_SEQ_KEY)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _stamp_of(ev):
    """The DTSTAMP we last wrote this event from, or None when it cannot be read.

    None on absent, empty and malformed alike -- all three mean the tie cannot be broken,
    and the call site applies rather than refuses.
    """
    raw = _private(ev).get(_STAMP_KEY)
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _revision_delta(ics, ours):
    """How `ics` orders against the revision `ours` was written from: -1 older, 0 same,
    +1 newer -- or None when the two CANNOT be compared.

    RFC 5545 orders revisions of one UID by SEQUENCE, then DTSTAMP. None is returned for
    every case where that order cannot be established. What the CALLER does with None
    depends on why it could not: it applies -- refusing on an unanswerable question is how
    a real reschedule gets discarded -- EXCEPT where the invite's own SEQUENCE was
    unreadable and the start has moved, which is surfaced as `unorderable` instead. An
    earlier version of this sentence said every caller treats None as "apply"; the arm
    below falsifies it.

    None is not interchangeable with a real 0, and it arises where the order genuinely
    cannot be established: the event carries no recorded revision (it predates the tag);
    or SEQUENCE ties -- or is unreadable, so it cannot be compared at all -- and DTSTAMP
    is missing from either side, leaving nothing to break the tie.

    Deliberately NO COUNT of those. This sentence said "three" while the code had two,
    within one commit of the arm being removed, which is this repo's most-repeated
    finding shape applied to a function whose whole job is arbitration. A number here is
    a drift surface with no reader that benefits from it.

    An unreadable SEQUENCE does NOT return early: the coerced 0 must not be compared (it
    would rank a mangled line as the earliest revision and lose to everything), but
    DTSTAMP can still settle the order, and abstaining before asking it is how a
    superseded invite got applied.
    """
    if not ics.sequence_unreadable:
        seen = _seq_of(ours)
        if seen is None:
            return None
        if ics.sequence != seen:
            return 1 if ics.sequence > seen else -1
    # Falls through on a TIE, and on an unreadable SEQUENCE -- which is a reason to skip
    # the SEQUENCE compare, never a reason to skip the arbitration. Returning early here
    # abstained on a tie DTSTAMP could settle, so a late copy of the original invite
    # (`SEQUENCE:1.0`, older DTSTAMP) read as uncomparable, fell through to `moved`, and
    # moved a corrected interview back to the old day.
    prev, now = _stamp_of(ours), ics.dtstamp
    if prev is None or now is None:
        return None
    # UTC, NOT `assumed_zone(cfg)`. RFC 5545 §3.8.7.2 specifies DTSTAMP in UTC and iTIP
    # requires it, so one arriving without a `Z` is malformed and UTC is the only reading
    # with a basis behind it. That is the opposite of DTSTART, where floating time is
    # legal and the configured zone is the right guess -- the two fields look alike and
    # take different answers. Reading this one in the configured zone made the SAME pair
    # of revisions order one way under `UTC` and the other under `Asia/Dubai`, which is
    # the host-timezone defect from one function over, wearing a config for a hat.
    a, b = _aware(now, timezone.utc), _aware(prev, timezone.utc)
    return 0 if a == b else (1 if a > b else -1)


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


def _cancel_all(client, mine, unsettled, dry_run) -> str:
    """Remove every entry of ours, and say honestly whether that was the whole job.

    ONE definition for BOTH cancel arms -- the windowed one and the startless one. They carried
    two copies of this rule and only one copy got the truncation guard, so on identical evidence
    a startless cancel claimed `cancelled` while a windowed one said `unresolved`. The version
    that claimed completeness set no `needs_review`, wrote no dead-letter row, and let
    `seen.add` consume the message: a second entry for a cancelled interview, with nothing
    anywhere pointing at it.

    That is the third time on this branch that a rule living in two places got updated in one,
    so it now lives in one place. PRECONDITION: `mine` is non-empty -- an empty removal is not a
    cancellation, and both callers decide that for themselves because their fallbacks differ."""
    if not dry_run:
        for ev in mine:
            client.delete_event(ev["id"])
    # `unresolved` AFTER the deletes, never instead of them. The work we could do is real and
    # worth doing; what we cannot do is claim it was complete, because a truncated tag query
    # leaves another copy possible off-page. `cancelled` is a positive claim of full removal.
    return "unresolved" if unsettled else "cancelled"


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


def _ours_at_start(events, cfg, ics, lead_slug):
    """True if an event WE created for THIS lead already sits at ics.start under a
    DIFFERENT invite id (#203).

    `_foreign_at_start` is the mirror of this and deliberately does not cover it: it
    fires only for events carrying no tag of ours. So N messages on one thread with N
    distinct UIDs each read as "ours under another identity" -- `_find_ours` matched
    none of them, nothing suppressed the insert, and one slot collected N events while
    `calendar_added` reported the inflated count as success. The UID identity stays
    authoritative for updates and cancellations; this is a SECOND, weaker identity of
    (lead, instant) that governs the INSERT alone.

    Scoped by lead, because two applications can genuinely hold one slot and suppressing
    there would silently drop a real interview.

    Compares the START INSTANT, not `calendar_match_minutes` proximity -- the same
    comparison the update arm already makes. Proximity would swallow a genuine reschedule
    inside the window and leave the calendar showing the OLD time with nothing said; a
    visible second event at the new time is the failure to prefer. It also deliberately
    ignores `end`: requiring both would let a duration-only change insert a second event
    at the same instant, which is the duplicate being removed.
    """
    tz = assumed_zone(cfg)[0]
    want = _trunc(ics.start, tz)
    for ev in events:
        if _uid_of(ev) is None:
            continue                    # untagged -- `_foreign_at_start`'s business
        priv = _private(ev)
        if priv.get(_LEAD_KEY) != lead_slug:
            continue
        if want is not None and _trunc(_event_start(ev), tz) == want:
            return True
    return False


def _event_body(cfg, lead_slug, ics, prior_seq=None):
    # An AWARE start carries its own offset in `dateTime`, so its zone is a fact, not a
    # guess -- `timezone.utc` (a `Z`-suffixed DTSTART) has no `.key` and is UTC by
    # definition, and stamping the CONFIGURED zone on it instead would misbook a genuinely
    # UTC invite. Only a naive start falls through to the assumption.
    assumed = assumed_zone(cfg)[1]
    if ics.start is None or ics.start.tzinfo is None:
        tz = assumed
    else:
        tz = getattr(ics.start.tzinfo, "key", None) or "UTC"
    private = {_UID_KEY: _uid_tag(ics.uid), _LEAD_KEY: lead_slug}
    if not ics.sequence_unreadable:
        private[_SEQ_KEY] = str(ics.sequence)
    elif prior_seq is not None:
        # An unreadable SEQUENCE must not become the baseline every LATER invite is judged
        # against. `parse_ics` coerces `SEQUENCE:1.0` to 0 so one mangled line cannot sink
        # an invite, and `sequence_unreadable` stops that 0 being COMPARED -- writing it
        # put it back, one run later and wearing the authority of a recorded value.
        # Measured: an event at seq=5 took a mangled invite, had its tag rewritten to
        # "0", and a genuinely superseded SEQUENCE:3 then read as newer and moved the
        # appointment to the stale day.
        #
        # So the stored value is carried forward, and on an INSERT (no prior) the key is
        # omitted entirely -- exactly what `_STAMP_KEY` below already does rather than
        # invent one. Absent reads back as None, which `_revision_delta` treats as "cannot
        # compare", which is the truth.
        private[_SEQ_KEY] = str(prior_seq)
    if ics.dtstamp is not None:
        # Only when the invite HAD one. Writing a placeholder would make every event look
        # comparable and order revisions by a time nobody sent.
        private[_STAMP_KEY] = ics.dtstamp.isoformat()
    return {
        "summary": ics.summary or "Interview",
        "location": ics.location or "",
        "description": (ics.url or ""),
        "start": {"dateTime": ics.start.isoformat() if ics.start else None, "timeZone": tz},
        "end": {"dateTime": (ics.end or ics.start).isoformat() if ics.start else None, "timeZone": tz},
        "extendedProperties": {"private": private},
    }


def sync_event(client, cfg, *, lead_slug, ics, dry_run=False) -> str:
    """One of: created | updated | cancelled | present | unresolved | unorderable | foreign.

    `unorderable` (#202) is the newest and the narrowest: an entry of OURS sits at the
    slot, this invite moves it, and nothing can say which revision is later -- the
    invite's own SEQUENCE is unreadable and DTSTAMP either agrees or is absent. Distinct
    from `unresolved`, which means we could not establish what is there at all; here we
    know exactly what is there, and only which of two times is right is in doubt.

    `unresolved` is the answer to a question we could not ASK, asked over a window we know was
    incomplete, or -- since #146 -- asked and ACTED ON without being able to confirm the action
    was complete. That third producer is new and is the one to hold in mind: a cancel deletes
    every entry it can identify and still answers `unresolved` when the tag query was
    truncated, because another copy may sit off-page.

    It is spelled out because leaving it out already cost something. `_NEEDS_REVIEW_HINT`
    branched on the old two-clause definition and told the operator "nothing was deleted" for
    an outcome that had just deleted things -- a false statement in the one place a human
    reads. The hint was fixed; a contract that still licensed it would only produce the next
    one. `foreign` means we looked, found an event at that slot that sluice did NOT
    create -- routinely the sender's own invite, auto-added by Google -- and deliberately left
    it alone; the calendar work is therefore unfinished and a human has to look. Both are
    distinct from `present`, which means we searched a complete window and there was nothing
    of ours, or an entry of ours already sits at that instant -- under this UID, or since
    #203 under any UID for the same lead.

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
            mine, unsettled = _find_ours_by_tag(client, ics)
            if mine:
                # `_cancel_all` rather than an inline copy: this arm needs the truncation rule
                # MORE than the windowed one does -- there the tag query supplements a window
                # scan, here it is the only evidence there is -- and it is the arm that got the
                # rule LAST when the two were separate.
                return _cancel_all(client, mine, unsettled, dry_run)
            # NOT `present` when the tag query comes back empty, even though it searched
            # without a window and "nothing of ours" is what `present` means. That would be
            # #138 rebuilt on an assumption: `present` is a positive claim, and the only
            # evidence for it here is a filter nobody has executed against a live calendar. If
            # it silently matches nothing, every startless cancel would answer "nothing to do"
            # while the cancelled interview stayed in the operator's calendar and `seen.add`
            # consumed the message -- which is precisely the failure this arm was given
            # `unresolved` for.
            #
            # So the lookup can only IMPROVE this branch, never weaken it.
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
            #
            # `tag_unsettled`, NOT `truncated`: the latter is computed as
            # `not mine and tag_unsettled`, deliberately False here BECAUSE we found something,
            # and this branch returns before it would be read anyway.
            return _cancel_all(client, mine, tag_unsettled, dry_run)
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
        if _ours_at_start(events, cfg, ics, lead_slug):
            # An entry of OURS holds this instant under a different UID (#203's same-slot
            # rule is exactly what puts one there: it suppresses the second UID's insert,
            # so the event at the slot carries the FIRST). `present` says "nothing of
            # ours, and nothing else at that slot", which is then false, and the message
            # is consumed with the cancelled interview still in the calendar.
            #
            # Not deleted: the UID does not match, so nothing shows that entry belongs to
            # THIS invite, and a wrong delete removes a real appointment. Reported instead,
            # which is what `unresolved` is for on this arm -- the cancel could not be
            # completed and a human has to look.
            return "unresolved"
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
        # The ids attach to "entries", not to "invite id" -- word order is load-bearing here.
        # Written the other way round the parenthetical sat immediately after "the same invite
        # id" and read as NAMING that invite id, which is the exact reading this warning was
        # rewritten to remove, and no assertion on the ids being present could have caught it.
        _log.warning(
            "track: %d calendar entries of ours (%s) carry the same invite id -- refusing to "
            "guess which one the reschedule applies to. Delete the stale entries and the next "
            "run will reconcile it.", len(mine), ", ".join(sorted(
                str(ev.get("id")) for ev in mine)))
        return "unresolved"
    if mine:
        ours = mine[0]
        rev = _revision_delta(ics, ours)
        if rev is not None and rev < 0:
            # A SUPERSEDED revision (#202). RFC 5545 orders revisions of one UID, so this
            # invite is older than what the event already holds and applying it would
            # rewrite a corrected time back to the stale one -- the worst shape this tool
            # has, because a confidently wrong appointment stops you looking.
            #
            # `present`, not a new outcome: the calendar already holds the newer truth, so
            # there is nothing to do and nothing for a human to settle. Discarding a
            # superseded invite is routine on any thread that carries an update.
            #
            # Scoped to the SCHEDULING arm. The cancel path above is deliberately
            # unchanged: a stale CANCEL deleting a rescheduled event is a real hazard of
            # this same class, but removal there is reasoned about for COMPLETENESS and is
            # a separate change rather than a side effect of this one.
            return "present"
        # Same zone the body was stamped with, or the instant we booked and the instant we
        # compare differ by that offset and every run reports `updated` and re-writes it.
        tz = assumed_zone(cfg)[0]
        moved = _trunc(_event_start(ours), tz) != _trunc(ics.start, tz)
        if moved and ics.sequence_unreadable and rev in (None, 0):
            # Nothing ORDERS these -- the invite's own SEQUENCE is unreadable, so the
            # only appeal is DTSTAMP, and it either says nothing or says the two objects
            # were created at the same moment, which for two different start times is
            # contradictory rather than conclusive. `rev == 0` therefore joins `None`
            # here, and is NOT the same case as an equal READABLE sequence, which still
            # applies: there the sender stated a revision and merely never increments it,
            # a known habit rather than evidence against itself.
            #
            # The instant has MOVED, so applying and refusing
            # are both guesses with a real cost: one moves a correct appointment, the
            # other discards a real reschedule. The honest answer is to write nothing AND
            # say so.
            #
            # `unorderable`, NOT `unresolved`, even though both mean "a human must look".
            # This arm sits inside `if mine:` with a moved start, so an entry of OURS is
            # provably at the OLD instant -- while `calendar-unresolved` means the entry
            # could not be created or verified and tells the operator to add it by hand.
            # Following that here books a duplicate and leaves the stale one. One reason
            # covering two situations becomes a false statement in the place a human
            # actually reads, which `_NEEDS_REVIEW_HINT`'s header records happening twice
            # already.
            #
            # Scoped to an UNREADABLE sequence, NOT to every uncomparable pair, so an
            # event carrying no recorded revision -- every entry booked before this branch
            # -- still applies on an ordinary invite rather than filing a row on its first
            # reschedule.
            #
            # Stated precisely, because the loose version of this claim is false: the
            # scoping is on the INCOMING invite, and says nothing about the stored side.
            # A legacy event meeting an unreadable-SEQUENCE invite that moves the start
            # DOES land here. That is the right answer for it -- nothing can order the two
            # -- but "legacy events still apply" is not what this condition guarantees.
            return "unorderable"
        if moved or rev == 1:
            # `rev == 1` as well as `moved`, or the recorded revision goes STALE. It used
            # to be written only by the two arms that change the start, so an ordinary
            # same-instant revision -- a corrected location or title -- returned without
            # advancing it, and the next genuinely superseded invite then cleared a bar
            # that had never moved. Measured: seq0 booked, seq5 at the same instant, then
            # seq1 carrying the old day won and moved the appointment back.
            #
            # Reported as `updated` rather than `present` because a write genuinely
            # happened: the body carries the summary, location and url this revision may
            # have changed, and answering "nothing to do" after calling `update_event` is
            # the same positive-claim-over-a-different-action shape as #138.
            #
            # ACCEPTED COST, measured rather than assumed: RFC 5545 makes DTSTAMP the
            # iCalendar OBJECT's creation time, so an unchanged re-send of the same event
            # carries the same SEQUENCE and a FRESH DTSTAMP. That reads as strictly newer,
            # so it writes and `calendar_added` counts it, even though nothing visible
            # changed. Refreshing the tag anyway is the deliberate choice: leaving it at
            # the older stamp lets a stale invite whose own stamp sits between the two
            # read as newer and move the appointment -- a wrong time, against a digest
            # count that is merely flattering.
            if not dry_run:
                client.update_event(
                    ours["id"], _event_body(cfg, lead_slug, ics, prior_seq=_seq_of(ours)))
            # The id is known here whether or not we wrote -- it is the entry we FOUND -- so a
            # dry run can still name what it would have touched.
            _warn_if_floating(cfg, ics, "updated", lead_slug, ours.get("id"), dry_run)
            return "updated"
        return "present"
    if _ours_at_start(events, cfg, ics, lead_slug):
        # An entry of ours already holds this instant under another UID (#203). Ahead of
        # both branches below: this is a POSITIVE identification of our own event at the
        # slot, so it settles the question that `truncated` would otherwise defer and it
        # outranks `foreign`, which reports calendar work left unfinished. Here the work
        # is done -- the appointment is booked at the right time.
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
    # `insert_event` returns the new id, which until now was discarded. It is the only handle
    # that exists for an entry we just created, and the warning below needs one to be
    # actionable. None on a dry run, where nothing was written and there is nothing to verify.
    new_id = client.insert_event(_event_body(cfg, lead_slug, ics)) if not dry_run else None
    _warn_if_floating(cfg, ics, "created", lead_slug, new_id, dry_run)
    return "created"
