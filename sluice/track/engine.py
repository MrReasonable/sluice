"""Orchestration: run (fetch -> classify -> reconcile, per-message resilient) and
confirm (apply an approved proposal). The Gmail query is scoped by time window; the
`seen` set (a message-id store) provides dedup, never read-state (F8/F10)."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sluice.core import status as _status
from sluice.core.leads import slug_matches
from sluice.track.classify import classify
from sluice.track.reconcile import reconcile
from sluice.track.ics import parse_ics
from sluice.track.google_client import GoogleAuthError
from sluice.track.deadletter import Entry

_INFLIGHT = ("applied", "phone_screen", "interview", "offer")  # non-terminal application states

# Map a classified event type -> the target status a proposal would advance to (F7).
_PROPOSE_TARGET = {"phone_screen": "phone_screen", "interview": "interview",
                   "rejection": "rejected", "offer": "offer"}


@dataclass
class RunReport:
    msgs: int = 0
    classified: int = 0
    auto: int = 0
    proposed: int = 0
    calendar_added: int = 0
    failures: int = 0
    results: list = field(default_factory=list)
    open_proposals: list = field(default_factory=list)  # every currently-open dead-letter Entry
    auth_error: bool = False
    deadletter_error: bool = False  # a dead-letter WRITE raised this run; app.py must hold
                                     # the lastrun watermark so the un-persisted message
                                     # re-queries next run instead of aging out of Gmail's
                                     # advancing `after:` window (#49's write-path silent loss)


def _dl_write(rep, op):
    # A dead-letter WRITE failure must both skip seen.add (re-raise into the per-message
    # except) and hold the lastrun watermark (rep.deadletter_error -> app.py skips
    # _save_lastrun), so the un-persisted message re-queries next run instead of falling
    # out of the advancing Gmail `after:` window. #49's silent-loss on the write path.
    try:
        op()
    except Exception:
        rep.deadletter_error = True
        raise


def _gmail_query(cfg, now_iso, since_iso=None):
    if since_iso:
        after = datetime.fromisoformat(since_iso).strftime("%Y/%m/%d")
    else:
        now = datetime.fromisoformat(now_iso)
        after = (now - timedelta(days=cfg.gmail_lookback_days)).strftime("%Y/%m/%d")
    q = f"after:{after} -category:promotions"
    return (q + " " + cfg.gmail_extra_query).strip()


def run(vault, cfg, client, backend, *, seen, deadletter, now_iso, since_iso=None, dry_run=False) -> RunReport:
    rep = RunReport()
    today = datetime.fromisoformat(now_iso).date().isoformat()
    leads = [n for n in vault.read_leads(set(_status.APPLICATION_OWNED))
             if n.status in _INFLIGHT]
    note_by_slug = {n.slug: n for n in leads}
    try:
        ids = client.search_messages(_gmail_query(cfg, now_iso, since_iso))
    except GoogleAuthError:
        rep.auth_error = True
        return rep
    # Bump carried entries before any new record, so a row first recorded THIS run
    # stays at times_surfaced=1. Outside the per-message try on purpose: a raise
    # here (corrupt/unwritable store) aborts the run before seen/lastrun save --
    # fail-safe, since nothing has been processed or seen.add'd yet.
    if not dry_run:
        deadletter.bump_surfaced()
    new_entries = []
    for mid in ids:
        if mid in seen:
            continue
        rep.msgs += 1
        try:
            msg = client.get_message(mid)
            msg["message_id"] = mid
            ics = None
            for att in msg.get("attachments", []):
                if att.get("filename", "").lower().endswith(".ics") or "calendar" in att.get("mime", "").lower():
                    ics = parse_ics(att.get("data", b"").decode("utf-8", "replace"))
                    break
            ev = classify(msg, leads, backend, cfg, ics=ics)
            rep.classified += 1
            res = reconcile(ev, note_by_slug, vault, cfg, client, dry_run=dry_run)
            rep.results.append(res)
            # Never-regress across messages in one run: reflect the just-written
            # status back into the snapshot so the next message for this lead
            # reconciles against current, not stale, state. Only meaningful when
            # something was actually written (never in a dry-run preview).
            if not dry_run and res.status_to and ev.lead_slug in note_by_slug:
                note_by_slug[ev.lead_slug].status = res.status_to
            if res.action == "applied":
                rep.auto += 1
                # Symmetric with confirm's clear-on-advance: an auto-resolved lead's
                # pending proposals are resolved too, so its dead-letter entries stop
                # re-surfacing with a now-un-runnable confirm hint (the lead is already
                # terminal). Clear on ev.lead_slug -- the same key run() records under.
                if not dry_run and ev.lead_slug:
                    _dl_write(rep, lambda: deadletter.clear_lead(ev.lead_slug))
            elif res.action == "proposed":
                rep.proposed += 1
                target = _PROPOSE_TARGET.get(ev.type, "")
                if ev.lead_slug and target:
                    hint = f'sluice track confirm --lead "{ev.lead_slug}" --to {target}'
                elif ev.candidates:
                    opts = "; ".join(f'--lead "{c}" --to {target or "<status>"}' for c in ev.candidates)
                    hint = f"(ambiguous lead; pick one: sluice track confirm {opts})"
                else:
                    hint = f'(no runnable action for type "{ev.type}" / lead "{res.lead}"; review manually)'
                entry = Entry(message_id=mid, lead=ev.lead_slug or "",
                              candidates=",".join(ev.candidates), ev_type=ev.type,
                              proposal=res.proposal or ev.type, hint=hint,
                              first_seen=today, times_surfaced=1)
                new_entries.append(entry)
                # record BEFORE seen.add: a write failure raises, the `except`
                # below skips seen.add, and the message re-processes next run.
                if not dry_run:
                    _dl_write(rep, lambda: deadletter.record(entry))
            if res.calendar in ("created", "updated"):
                rep.calendar_added += 1
            if not dry_run:
                seen.add(mid)
        except GoogleAuthError:
            rep.auth_error = True
            break
        except Exception:
            rep.failures += 1
    # Emit the full open set. Non-dry: the store already holds this run's new rows,
    # so it is the single source of truth. Dry: union the persisted set with this
    # run's computed-new (keyed by message_id, persisted wins), recording nothing.
    if dry_run:
        by_id = {e.message_id: e for e in new_entries}
        for e in deadletter.open_entries():
            by_id[e.message_id] = e
        rep.open_proposals = sorted(by_id.values(), key=lambda e: (e.first_seen, e.message_id))
    else:
        rep.open_proposals = deadletter.open_entries()
    return rep


def confirm(vault, cfg, slug, to, *, deadletter, when=None, dry_run=False) -> dict:
    matches = [n for n in vault.read_leads() if slug_matches(n, slug)]
    if not matches:
        return {"ok": False, "reason": "no_match"}
    if len(matches) > 1:
        return {"ok": False, "reason": "ambiguous"}
    note = matches[0]
    if not _status.can_advance(note.status, to):
        return {"ok": False, "reason": note.status}
    if not dry_run:
        fields = {"status": _status.normalize(to), "last_signal": date.today().isoformat()}
        if when:
            fields["interview_date"] = f'"{when}"'
        vault.update_fields(note.ref, fields)
        # Clear only after can_advance passed AND the write happened: a refused
        # confirm returned above and never reaches here, so it never deletes a row
        # (deleting on a refused confirm would be #49's silent loss on the clear path).
        deadletter.clear_lead(note.slug)
    return {"ok": True, "from": note.status, "to": _status.normalize(to)}
