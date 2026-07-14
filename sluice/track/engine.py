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
    proposals: list = field(default_factory=list)
    auth_error: bool = False


def _gmail_query(cfg, now_iso, since_iso=None):
    if since_iso:
        after = datetime.fromisoformat(since_iso).strftime("%Y/%m/%d")
    else:
        now = datetime.fromisoformat(now_iso)
        after = (now - timedelta(days=cfg.gmail_lookback_days)).strftime("%Y/%m/%d")
    q = f"after:{after} -category:promotions"
    return (q + " " + cfg.gmail_extra_query).strip()


def run(vault, cfg, client, backend, *, seen, now_iso, since_iso=None, dry_run=False) -> RunReport:
    rep = RunReport()
    leads = [n for n in vault.read_leads(set(_status.APPLICATION_OWNED))
             if n.status in _INFLIGHT]
    note_by_slug = {n.slug: n for n in leads}
    try:
        ids = client.search_messages(_gmail_query(cfg, now_iso, since_iso))
    except GoogleAuthError:
        rep.auth_error = True
        return rep
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
                rep.proposals.append(f"{res.lead}: {res.proposal or ev.type} :: {hint}")
            if res.calendar in ("created", "updated"):
                rep.calendar_added += 1
            if not dry_run:
                seen.add(mid)
        except GoogleAuthError:
            rep.auth_error = True
            break
        except Exception:
            rep.failures += 1
    return rep


def confirm(vault, cfg, slug, to, when=None, dry_run=False) -> dict:
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
    return {"ok": True, "from": note.status, "to": _status.normalize(to)}
