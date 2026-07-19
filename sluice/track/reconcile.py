"""Tiered reconciliation of one classified Event against the vault + calendar.
Auto-applies only high-confidence/structured signals under the never-regress guard;
everything else is proposed. Additive actions (calendar, materials, stamp) run on a
confident lead match even when the status change is only proposed."""
from dataclasses import dataclass
from datetime import date

from sluice.core import status as _status
from sluice.track.calendar_sync import sync_event

_SCHEDULE_TARGET = {"phone_screen": "phone_screen", "interview": "interview"}


@dataclass
class ReconcileResult:
    lead: str = ""
    action: str = "skipped"       # applied | proposed | calendar | skipped
    status_from: "str | None" = None
    status_to: "str | None" = None
    calendar: str = "none"
    materials_written: bool = False
    proposal: "str | None" = None
    note: str = ""


def _stamp_materials(vault, note, ev, dry_run=False):
    if not (ev.materials or ev.links):
        return False
    if dry_run:
        return True
    tag = f"track-materials-{note.status}-{ev.message_id or ev.type}"
    lines = [f"- {m}" for m in ev.materials] + [f"- {u}" for u in ev.links]
    section = f"## Interview materials <!--{tag}-->\n" + "\n".join(lines)
    return vault.append_body_section(note.ref, tag, section)


def _advance(vault, note, target, ev, dry_run=False):
    fields = {"status": target, "last_signal": date.today().isoformat()}
    if ev.when:
        fields["interview_date"] = f'"{ev.when}"'
    elif ev.ics and ev.ics.start:
        fields["interview_date"] = f'"{ev.ics.start.date().isoformat()}"'
    if ev.links:
        fields["interview_link"] = f'"{ev.links[0]}"'
    if not dry_run:
        vault.update_fields(note.ref, fields)


def reconcile(event, note_by_slug, vault, cfg, client, dry_run=False) -> ReconcileResult:
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
        r.action = "calendar"
        r.note = "cancellation"
        return r

    # Scheduling with a structured signal.
    if event.type in _SCHEDULE_TARGET and (event.ics is not None or event.when) \
            and event.confidence >= cfg.auto_status_min:
        if event.ics is not None and event.ics.start is not None:
            r.calendar = sync_event(client, cfg, lead_slug=event.lead_slug, ics=event.ics, dry_run=dry_run)
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
