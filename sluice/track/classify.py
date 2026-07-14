"""Classify one email into a structured Event against the in-flight leads. The LLM
decides type/confidence/lead-name; deterministic code resolves the lead name to an
actual in-flight lead (refuse-on-ambiguity -> propose) and never lets the model
invent a match."""
import json
import re
from dataclasses import dataclass, field

from sluice.core.leads import slug_matches

_TYPES = {"phone_screen", "interview", "rejection", "offer", "update", "not_job"}


@dataclass
class Event:
    message_id: str = ""
    thread_id: str = ""
    lead_slug: "str | None" = None
    candidates: list = field(default_factory=list)
    type: str = "not_job"
    confidence: float = 0.0
    when: "str | None" = None
    links: list = field(default_factory=list)
    ics: object = None
    materials: list = field(default_factory=list)
    summary: str = ""


def _lead_key(note):
    return note.slug


def build_prompt(msg, leads, cfg):
    inflight = "\n".join(
        f"- {n.fm.get('company','')} | {n.fm.get('role','')} | status={n.status}" for n in leads)
    h = msg.get("headers", {})
    atts = msg.get("attachments", []) or []
    att_names = ", ".join(a.get("filename", "") for a in atts if a.get("filename")) or "none"
    ics_present = "yes" if any(
        (a.get("filename", "").lower().endswith(".ics") or "calendar" in a.get("mime", "").lower()) for a in atts
    ) else "no"
    return (
        "You track a job seeker's live applications. Classify this email against the "
        "in-flight applications below. Return ONLY a JSON object with keys: lead (the "
        "company name of the matching application, or null), type (one of "
        "phone_screen, interview, rejection, offer, update, not_job), confidence "
        "(0..1), when (ISO datetime of any interview, or null), links (array of URLs), "
        "materials (array of short descriptions of any attachments or prep links), "
        "summary (one short line). Only match an application actually listed. Do not "
        "invent a company.\n\n"
        f"IN-FLIGHT APPLICATIONS:\n{inflight}\n\n"
        f"EMAIL:\nFrom: {h.get('from','')}\nSubject: {h.get('subject','')}\n\n"
        f"Attachments: {att_names}\nCalendar invite attached: {ics_present}\n\n"
        f"{(msg.get('body_text') or '')[:4000]}\n")


def _resolve_lead(name, leads):
    if not name:
        return None, []
    matches = [n for n in leads if slug_matches(n, str(name))]
    if len(matches) == 1:
        return _lead_key(matches[0]), []
    if len(matches) > 1:
        return None, [_lead_key(n) for n in matches]
    return None, []


def classify(msg, leads, backend, cfg, ics=None) -> Event:
    ev = Event(message_id=msg.get("message_id", ""), thread_id=msg.get("thread_id", ""), ics=ics)
    try:
        raw = backend.complete(build_prompt(msg, leads, cfg))
        data = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))
        t = str(data.get("type", "not_job"))
        ev.type = t if t in _TYPES else "not_job"
        ev.confidence = float(data.get("confidence") or 0.0)
        ev.when = data.get("when") or (ics.start.isoformat() if ics and ics.start else None)
        ev.links = list(data.get("links") or [])
        ev.materials = list(data.get("materials") or [])
        for a in msg.get("attachments", []) or []:
            fn = a.get("filename", "")
            if fn and not fn.lower().endswith(".ics") and fn not in ev.materials:
                ev.materials.append(fn)
        ev.summary = str(data.get("summary") or "")
        ev.lead_slug, ev.candidates = _resolve_lead(data.get("lead"), leads)
    except Exception:
        return Event(message_id=msg.get("message_id", ""), thread_id=msg.get("thread_id", ""), ics=ics)
    return ev
