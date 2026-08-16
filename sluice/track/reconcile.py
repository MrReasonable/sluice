"""Tiered reconciliation of one classified Event against the vault + calendar.
Auto-applies only high-confidence/structured signals under the never-regress guard;
everything else is proposed. Additive actions (calendar, materials, stamp) run on a
confident lead match even when the status change is only proposed."""
from dataclasses import dataclass
from datetime import date

from sluice.core import status as _status
from sluice.core.vault import frontmatter_safe
from sluice.track.calendar_sync import floating_start, sync_event

_SCHEDULE_TARGET = {"phone_screen": "phone_screen", "interview": "interview"}


@dataclass
class ReconcileResult:
    lead: str = ""
    action: str = "skipped"       # applied | proposed | calendar | skipped
    status_from: "str | None" = None
    status_to: "str | None" = None
    calendar: str = "none"
    # A calendar entry was booked for an instant we GUESSED: the DTSTART carried no usable
    # zone, so `calendar_assumed_timezone` supplied one. Counted into the digest so the
    # assumption is visible without reading the log stream, which under cron is discarded.
    # NOT `_utc` -- the assumed zone is configurable, and naming the flag after the shipped
    # DEFAULT would make it read as false the moment somebody sets the key.
    calendar_assumed_tz: bool = False
    materials_written: bool = False
    proposal: "str | None" = None
    note: str = ""
    # Something needs a HUMAN, independently of `action`. `action` conflates "what we wrote"
    # with "what needs attention", and a refused calendar write is legitimately both: the
    # interview is real so the status advance is right, and the missing calendar entry still
    # has to reach someone. Forcing it through `action="proposed"` would trade a silent loss
    # for a manual step on every truncated run; leaving it out is how it vanished entirely.
    # `engine.run` records a dead-letter row on this whatever `action` came out as.
    needs_review: str = ""


def _assumed_tz(outcome, ics):
    """Did that sync_event call book an instant we guessed? Shares `floating_start` with the
    warning calendar_sync emits, so the digest count and the log line can never disagree.

    Deliberately does NOT compare the configured zone against UTC. The flag means "this
    instant was assumed", which is equally true for `Europe/Berlin` as for the default -- a
    configured zone makes the guess better-informed, never certain, because the invite still
    stated no instant. Gating on `!= "UTC"` would silence the warning for exactly the users
    who took the trouble to configure it.

    `dry_run` is deliberately absent: `calendar_added` beside it also counts a dry run's
    would-be writes, so both counters report what the run WOULD do and the CLI changes the
    verb instead of the count."""
    return outcome in ("created", "updated") and floating_start(ics)


def _stamp_materials(vault, note, ev, dry_run=False):
    if not (ev.materials or ev.links):
        return False
    if dry_run:
        return True
    tag = f"track-materials-{note.status}-{ev.message_id or ev.type}"
    lines = [f"- {m}" for m in ev.materials] + [f"- {u}" for u in ev.links]
    section = f"## Interview materials <!--{tag}-->\n" + "\n".join(lines)
    return vault.append_body_section(note.ref, tag, section)


def _stamp_receipt(vault, note, ev):
    # Evidence for a receipt-driven advance. append_body_section is idempotent by tag,
    # so a re-processed receipt (same message_id) never double-writes; body untouched.
    # No dry_run parameter here: the sole call site already sits inside the reconcile()
    # `if not dry_run:` block below, so a dry-run guard on this function would be
    # unreachable dead code (a whole-branch review caught it: deleting the guard could
    # not redden any test).
    tag = f"track-receipt-{ev.message_id or ev.type}"
    section = (f"## Application receipt <!--{tag}-->\n"
               f"- Received: {date.today().isoformat()}\n"
               f"- From: {ev.sender}\n"
               f"- Subject: {ev.subject}\n"
               f"- Match: {ev.receipt_tier}")
    return vault.append_body_section(note.ref, tag, section)


def _advance(vault, note, target, ev, dry_run=False):
    fields = {"status": target, "last_signal": date.today().isoformat()}
    if ev.when:
        # #141: `ev.when` is `data.get("when")` straight from the model -- untrusted in
        # exactly the way `ev.links[0]` is, and guarded three lines below for exactly the
        # same reason. A `"` or backslash closes the quoted scalar early and corrupts the
        # note's frontmatter. #111 fixed the link and left its neighbour.
        #
        # Abstain on the FIELD, never the advance: losing the interview signal because the
        # model returned a bad date string would be the worse failure by far.
        safe_when = frontmatter_safe(ev.when)
        if safe_when:
            fields["interview_date"] = f'"{safe_when}"'
    elif ev.ics and ev.ics.start:
        fields["interview_date"] = f'"{ev.ics.start.date().isoformat()}"'
    if ev.links:
        # #111: ev.links[0] is parsed out of an inbound email -- untrusted, same class
        # as resolve.py's scraped company. A structural character must not corrupt the
        # note's frontmatter; abstain on this one field rather than the whole advance.
        safe_link = frontmatter_safe(ev.links[0])
        if safe_link:
            fields["interview_link"] = f'"{safe_link}"'
    if not dry_run:
        vault.update_fields(note.ref, fields)


def reconcile(event, note_by_slug, vault, cfg, client, dry_run=False, *, shortlist_by_slug=None) -> ReconcileResult:
    shortlist_by_slug = shortlist_by_slug or {}
    r = ReconcileResult(lead=event.lead_slug or ",".join(event.candidates) or "?")
    # Classification failed (#40): we have no trustworthy signal, so take no action beyond
    # surfacing it. Handled first, before any lead lookup or additive write, so a failed
    # classification can never advance status or stamp materials -- and gets an honest label
    # ("classification failed"), not the misleading "unmatched/ambiguous" of the generic path.
    if event.type == "unknown":
        r.action = "proposed"
        r.proposal = "classification failed -- review manually"
        r.note = event.summary
        return r
    # Application receipt (#10): advance shortlist->applied on a domain-PROOF match.
    # Placed BEFORE the generic no-match guard: a receipt's lead is resolved by the
    # deterministic matcher (engine) against the SHORTLIST set, not note_by_slug, and
    # carries its own tier. never-regress: can_apply is True only for shortlist, so a
    # receipt can never pull a lead out of the application ladder.
    if event.type == "receipt":
        note = shortlist_by_slug.get(event.lead_slug) if event.lead_slug else None
        if event.receipt_tier == "proof" and note is not None \
                and _status.can_apply(note.status) and event.confidence >= cfg.auto_apply_min:
            r.status_from = note.status
            if not dry_run:
                # EVIDENCE FIRST, status second. Either write can raise VaultConflict
                # (#16). Written the other way round, a conflict on the evidence append
                # left the lead already `applied` -- out of the shortlist set that
                # match_receipt searches -- so no future run could ever re-attach the
                # evidence, losing it unrecoverably. In this order a conflict on either
                # write leaves the lead in `shortlist`, engine.run's per-message except
                # skips seen.add, and the whole thing retries next run;
                # append_body_section is idempotent by tag, so the retry cannot
                # double-write. (A vault-level CAS across both writes was considered and
                # declined: it buys nothing this ordering does not, and `_advance` below
                # has the same shape.)
                _stamp_receipt(vault, note, event)
                # Receipt-specific field set: status + last_signal ONLY. Do NOT reuse
                # _advance, which stamps interview_date/interview_link from ev.when/links
                # -- wrong for an `applied` lead (a receipt is not an interview signal).
                vault.update_fields(note.ref, {"status": "applied",
                                               "last_signal": date.today().isoformat()})
            r.action = "applied"
            r.status_to = "applied"
            return r
        # A matched note that can_apply already rules out cannot be proposed either: the
        # proposal's only runnable form is `track confirm --to applied`, which routes
        # through the same can_apply predicate and would be refused forever, while the
        # dead-letter row re-surfaced every run (#49's un-runnable-hint shape). The
        # commonest producer is a SECOND receipt for a lead this same run already
        # advanced. engine.run's in-flight LLM-fallback path already reasons exactly this
        # way -- it withholds `--to applied` for in-flight candidates -- so the two agree.
        if note is not None and not _status.can_apply(note.status):
            r.status_from = note.status
            r.action = "skipped"
            r.note = event.summary
            return r
        # corroborated, ambiguous, or proof below the confidence floor -> propose.
        if note is not None or event.candidates:
            r.status_from = note.status if note is not None else None
            r.action = "proposed"
            r.proposal = "receipt (confirm applied)"
            r.note = event.summary
            return r
        r.action = "skipped"
        r.note = event.summary
        return r
    # No confident lead match -> propose (or skip pure noise).
    if event.lead_slug is None or event.lead_slug not in note_by_slug:
        if event.type in ("not_job", "update") and not event.candidates:
            r.action = "skipped"
            r.note = event.summary
            return r
        r.action = "proposed"
        r.proposal = f"{event.type} (unmatched/ambiguous)"
        r.note = event.summary
        return r
    note = note_by_slug[event.lead_slug]
    r.status_from = note.status

    # Cancellation: calendar cancel only, never advance.
    if event.ics is not None and event.ics.cancelled:
        r.calendar = sync_event(client, cfg, lead_slug=event.lead_slug, ics=event.ics, dry_run=dry_run)
        r.calendar_assumed_tz = _assumed_tz(r.calendar, event.ics)
        r.note = "cancellation"
        if r.calendar == "unresolved":
            # A cancel we could not resolve must reach a human. `action="calendar"` matches
            # none of engine.run's branches, so it wrote nothing and let seen.add consume the
            # message -- the work undone and the evidence gone. `proposed` is the existing
            # route for "we could not act", and already carries a dismiss lever.
            r.action = "proposed"
            r.proposal = "cancel-unresolved"
            return r
        r.action = "calendar"
        return r

    # Scheduling with a structured signal.
    if event.type in _SCHEDULE_TARGET and (event.ics is not None or event.when) \
            and event.confidence >= cfg.auto_status_min:
        if event.ics is not None and event.ics.start is not None:
            r.calendar = sync_event(client, cfg, lead_slug=event.lead_slug, ics=event.ics, dry_run=dry_run)
            r.calendar_assumed_tz = _assumed_tz(r.calendar, event.ics)
            if r.calendar == "unresolved":
                # The cancel branch above learned this and the scheduling branch did not, so a
                # refused insert (our event may be off-page in a truncated window, and
                # inserting would duplicate it) advanced the status to `interview`, booked
                # NOTHING, wrote no row, and let `seen.add` consume the message. `failures=0`,
                # `calendar_added=0` -- indistinguishable from a message carrying no invite.
                #
                # The advance itself is correct and stays: the interview genuinely was
                # scheduled. What was missing is that anyone finds out the calendar entry is
                # not there.
                r.needs_review = "calendar-unresolved"
        r.materials_written = _stamp_materials(vault, note, event, dry_run=dry_run)
        target = _SCHEDULE_TARGET[event.type]
        if _status.can_advance(note.status, target):
            _advance(vault, note, target, event, dry_run=dry_run)
            r.action = "applied"
            r.status_to = target
        else:
            r.action = "calendar" if r.calendar != "none" else "proposed"
        return r

    # Offer.
    if event.type == "offer" and event.confidence >= cfg.auto_status_min:
        r.materials_written = _stamp_materials(vault, note, event, dry_run=dry_run)
        if _status.can_advance(note.status, "offer"):
            _advance(vault, note, "offer", event, dry_run=dry_run)
            r.action = "applied"
            r.status_to = "offer"
        else:
            r.action = "proposed"
        return r

    # Rejection: strict bar (F4) - specific lead + high confidence.
    if event.type == "rejection" and event.confidence >= cfg.auto_reject_min:
        if _status.can_advance(note.status, "rejected"):
            _advance(vault, note, "rejected", event, dry_run=dry_run)
            r.action = "applied"
            r.status_to = "rejected"
        else:
            r.action = "proposed"
        return r

    # Everything else (soft rejection, low-confidence, update) -> propose.
    r.action = "proposed"
    r.proposal = f"{event.type} (conf {event.confidence:.2f})"
    r.note = event.summary
    return r
