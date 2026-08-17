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
    # #136 Task 5c: did THIS reconcile() call file the receipt's evidence onto the note
    # via `_skip_with_evidence`? Digest-visible trace of the quiet-skip write closing the
    # #40 safety gap -- a domain-matched receipt that cannot advance status (because the
    # lead is already past shortlist) used to record nothing anywhere. Deliberately does
    # NOT cover the auto-advance path's own (pre-existing, unrelated) evidence-first
    # stamp on a successful advance -- that case is already visible via `action`/
    # `status_to`, so folding it in here would double-count against `rep.auto` for no
    # added visibility.
    receipt_stamped: bool = False
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


def _stamp_receipt(vault, note, ev, dry_run=False):
    # Evidence for a receipt-driven advance, OR (#136 Task 5c/5d) for a receipt that
    # domain-matched a lead that cannot be advanced any further -- reconcile() now has
    # TWO call sites for this, not one: the auto-advance branch below (nested inside its
    # own `if not dry_run:`, so `dry_run` there is always False) and the quiet-skip tail
    # (`_skip_with_evidence`, further down), which is NOT nested inside any such guard --
    # a receipt that cannot advance status is exactly as real under a dry run as under a
    # real one, and reporting what a dry run WOULD do is the whole point of `dry_run`, so
    # that call site needs this function's own guard to keep from writing. `dry_run`
    # mirrors `_stamp_materials` immediately above for exactly that reason: this is no
    # longer a function with one call site whose caller's guard could be trusted
    # implicitly. append_body_section is idempotent by tag, so a re-processed receipt
    # (same message_id) never double-writes; body untouched otherwise.
    if dry_run:
        return True
    tag = f"track-receipt-{ev.message_id or ev.type}"
    section = (f"## Application receipt <!--{tag}-->\n"
               f"- Received: {date.today().isoformat()}\n"
               f"- From: {ev.sender}\n"
               f"- Subject: {ev.subject}\n"
               f"- Match: {ev.receipt_tier}")
    return vault.append_body_section(note.ref, tag, section)


def _skip_with_evidence(r, vault, note, event, dry_run, *, stamped=None) -> ReconcileResult:
    """Shared tail for a domain-matched receipt that will NOT advance status this call --
    either because the note was already past shortlist when it was matched (the
    steady-state case, #136 Task 5c) or because the fresh CAS re-read inside the
    auto-advance write below found the note had left shortlist between THIS call's own
    read and its write (#136 Task 5d's race). Both stamp the receipt's evidence onto the
    note via the same idempotent `_stamp_receipt` the auto-advance path uses on success,
    additive-only, status untouched -- see the two call sites for why going quiet with
    nothing recorded would drop the #40 safety cover.

    `stamped` (the Task 5d call site only) is the auto-advance branch's OWN
    `_stamp_receipt` return, passed through verbatim rather than left for this function to
    re-derive: that branch already stamped evidence BEFORE attempting its status write
    (evidence-first, so a raced or conflicted status write never loses it), so by the time
    the race is discovered the write already happened and its real result is already
    known. Re-deriving it here by calling `_stamp_receipt` a second time would be a real,
    if idempotent-on-disk, second I/O attempt -- and could silently report the wrong
    value: if THIS message was already stamped by an earlier run (the first attempt's
    status write raised `VaultConflict` after a successful stamp, so the message retried),
    the caller's own call already correctly returned False (tag already present), but a
    second call here would ALSO return False for the same reason -- so passing the
    caller's real result through is not just cheaper, it is the only way this function can
    ever report False for a genuinely-already-stamped message rather than defaulting to
    True unconditionally. `stamped=None` (the Task 5c call site's default, which has not
    stamped anything yet) means this function must perform the stamp itself. A
    VaultConflict from the stamp write itself is deliberately never caught here either
    way, whichever call site raised it: it propagates into engine.run's per-message
    `except`, which skips `seen.add` so the message retries next run -- and the retry is a
    no-op, since append_body_section is idempotent by tag."""
    r.status_from = note.status
    r.receipt_stamped = stamped if stamped is not None else _stamp_receipt(vault, note, event, dry_run=dry_run)
    r.action = "skipped"
    r.note = event.summary
    return r


def _advance(vault, note, target, ev, dry_run=False):
    fields = {"status": target, "last_signal": date.today().isoformat()}
    # #141. `ev.when` is the MODEL's `when` falling back to the parsed DTSTART --
    # `classify.py` builds it as `data.get("when") or ics.start.isoformat()` -- so it is
    # untrusted in exactly the way `ev.links[0]` is, and guarded in the `ev.links` branch
    # below for exactly the same reason (that branch is ~15 lines down, not the "three" an
    # earlier version of this comment claimed -- name the branch, never the distance). A `"` closes the quoted scalar early and a backslash
    # opens a YAML escape sequence; either corrupts the note's frontmatter. #111 fixed the
    # link and left its neighbour.
    #
    # Abstain on the FIELD, never the advance: losing the interview signal because the model
    # returned a bad date string would be the worse failure by far.
    #
    # The abstention FALLS THROUGH to the ics date. Written as `if ev.when: ... elif ev.ics`
    # it did not: the `elif` binds to `ev.when` being falsy, not to the guard rejecting it, so
    # a model returning `2026-07-15 10:00 "BST"` on an invite carrying a perfectly good
    # DTSTART wrote NO date at all -- discarding the junk and the authoritative value
    # together, which is the outcome the paragraph above says is the worse one.
    safe_when = frontmatter_safe(ev.when) if ev.when else None
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


def reconcile(event, note_by_slug, vault, cfg, client, dry_run=False, *, receipt_by_slug=None) -> ReconcileResult:
    receipt_by_slug = receipt_by_slug or {}
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
    # deterministic matcher (engine) against the COMBINED shortlist + in-flight index
    # (#136, engine.run's receipt_by_slug -- a lead reaches `applied` at apply time and
    # its receipt normally arrives AFTER, so a shortlist-only index left the matcher
    # structurally blind in steady state), and carries its own tier. never-regress:
    # can_apply is True only for shortlist, so a receipt can never pull a lead out of
    # the application ladder -- see the two branches below for what happens to a receipt
    # that domain-matches a note can_apply already refuses.
    if event.type == "receipt":
        note = receipt_by_slug.get(event.lead_slug) if event.lead_slug else None
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
                receipt_stamped = _stamp_receipt(vault, note, event, dry_run=dry_run)
                # Receipt-specific field set: status + last_signal ONLY. Do NOT reuse
                # _advance, which stamps interview_date/interview_link from ev.when/links
                # -- wrong for an `applied` lead (a receipt is not an interview signal).
                #
                # #136 Task 5d: require_status re-reads the status from the FRESH note
                # inside the atomic compare-and-set transform and refuses to write if it
                # is no longer `shortlist` -- closing the gap between when `note.status`
                # was read (can_apply above, a snapshot possibly seconds stale) and this
                # write actually landing. Mirrors apply/record.py's identical guard for
                # the identical snapshot-staleness hazard. Reaching this call at all means
                # the snapshot said shortlist, so `wrote is False` unambiguously means the
                # note left shortlist in that window -- a genuine race, not a
                # hypothetical.
                wrote = vault.update_fields(
                    note.ref, {"status": "applied", "last_signal": date.today().isoformat()},
                    require_status=frozenset({"shortlist"}))
                if not wrote:
                    # Reporting action="applied" here would be a lie -- the write never
                    # landed -- and would also trip engine.py's clear_lead dead-letter-
                    # clearing logic on a status that never actually moved. The evidence
                    # stamp above already landed (it ran BEFORE this write,
                    # unconditionally, evidence-first), so this falls through to the same
                    # quiet-skip-and-stamp shape as the domain-matched-but-already-applied
                    # branch below -- this IS that case now, just discovered a few
                    # microseconds later than a snapshot read could have told us.
                    # `receipt_stamped` (not a hardcoded True) is threaded through: on a
                    # message that already raced once -- an EARLIER run's status write
                    # raised VaultConflict after its own stamp landed, so this run is a
                    # retry -- the call above is itself a no-op (tag already present) and
                    # correctly returns False, which must survive here rather than being
                    # overwritten to a True the message did not earn THIS run.
                    return _skip_with_evidence(r, vault, note, event, dry_run,
                                               stamped=receipt_stamped)
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
        #
        # #136: this is also the STEADY-STATE case -- a lead reaches `applied` before its
        # receipt arrives, so THIS branch fires on every ordinary confirmation, not an
        # edge case. Going quiet on the STATUS is right (no `track confirm` could ever
        # act on it), but going quiet with NOTHING recorded anywhere would drop the #40
        # safety cover engine.run's LLM-name fallback used to provide for a mislabelled
        # rejection: if the model called a real rejection a "receipt" and it happens to
        # domain-match, the lead would sit at `applied` forever with zero trace. So the
        # evidence goes on the note instead, via `_skip_with_evidence` -- the SAME
        # idempotent, CAS-routed `_stamp_receipt` helper the auto-advance path above
        # already uses, additive-only, status untouched.
        if note is not None and not _status.can_apply(note.status):
            return _skip_with_evidence(r, vault, note, event, dry_run)
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
        if r.calendar in ("unresolved", "foreign"):
            # A cancel we could not FINISH must reach a human -- which since #146 includes one
            # we partly acted on: `sync_event` may delete every entry it could identify and
            # still answer `unresolved`, because a truncated tag query cannot rule out another
            # copy off-page. "Could not act on" was the older, narrower reading and is why the
            # hint this routes to once claimed nothing had been deleted.
            #
            # Both outcomes, not just
            # `unresolved`: `foreign` means something we did not create sits at that slot --
            # routinely the recruiter's own invite, auto-added by Google from the mail. We
            # must never delete it, but the operator's calendar still shows a cancelled
            # interview and `present` told them nothing.
            #
            # `needs_review`, the SAME channel the scheduling branch uses. This used to force
            # `action="proposed"` with a magic `proposal` string, which `engine.run` then had
            # to detect twice -- once to withhold `_PROPOSE_TARGET` (or the operator is handed
            # a runnable `confirm --to interview` for an interview that was just CANCELLED)
            # and again to pick the row's `ev_type`. Two routes for one fact is how one of
            # them ends up handled and the other not, which is exactly what happened when the
            # scheduling producer was added.
            r.needs_review = f"cancel-{r.calendar}"
        r.action = "calendar"
        return r

    # Scheduling with a structured signal.
    if event.type in _SCHEDULE_TARGET and (event.ics is not None or event.when) \
            and event.confidence >= cfg.auto_status_min:
        if event.ics is not None and event.ics.start is not None:
            r.calendar = sync_event(client, cfg, lead_slug=event.lead_slug, ics=event.ics, dry_run=dry_run)
            r.calendar_assumed_tz = _assumed_tz(r.calendar, event.ics)
            if r.calendar in ("unresolved", "foreign"):
                # The cancel branch above learned this and the scheduling branch did not, so a
                # refused insert advanced the status to `interview`, booked NOTHING, wrote no
                # row, and let `seen.add` consume the message. `failures=0 calendar_added=0`
                # -- indistinguishable from a message carrying no invite.
                #
                # `unresolved`: our event may be off-page in a truncated window, so inserting
                # could duplicate it. `foreign`: an event we did not create already covers the
                # slot. `calendar_match_minutes` defaults to 30, so ANY untagged event within
                # half an hour -- a standup, a dentist appointment -- suppressed the booking.
                #
                # The advance itself is correct and stays: the interview genuinely was
                # scheduled. What was missing is that anyone finds out the entry is not there.
                r.needs_review = f"calendar-{r.calendar}"
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
