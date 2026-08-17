"""Classify one email into a structured Event against the in-flight leads. The LLM
decides type/confidence/lead-name; deterministic code resolves the lead name to an
actual in-flight lead (refuse-on-ambiguity -> propose) and never lets the model
invent a match."""
import json
import re
from dataclasses import dataclass, field

from sluice.core.leads import slug_matches
from sluice.core.log import get_logger

_log = get_logger("track.classify")

_TYPES = {"phone_screen", "interview", "rejection", "offer", "update", "receipt", "not_job"}


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
    receipt_tier: "str | None" = None   # set by engine.run for a receipt: proof|corroborated|none
    sender: str = ""                    # raw From header, for receipt evidence
    subject: str = ""                   # raw Subject header, for receipt evidence
    # The LLM's OWN name resolution for a receipt, kept in fields lead_slug/candidates can
    # never be confused with -- SURFACING-ONLY, never a write input. match_receipt (the
    # authoritative, domain-based matcher) now searches shortlist AND in-flight leads
    # together (#136, engine.run's receipt_by_slug) -- so a lead already advanced past
    # shortlist (applied/phone_screen/...) is no longer structurally invisible to it the way
    # it was before, and is often found directly by domain evidence. These fields still
    # matter when the deterministic matcher's tier lands on "none" (never even a
    # corroborated match): no populated lead-side host lines up with the sender (a
    # forwarded message, a personal mailbox), or a slug this run dropped as a twin. A
    # "corroborated" match is a different case entirely and never reaches this fallback --
    # it already proposes on its own (reconcile.py's receipt branch), regardless of
    # confidence. Silently skipping a genuine "none" would be the #40 loss class again (a
    # mislabelled rejection vanishes with the lead stuck at `applied` forever). engine.run
    # reads these ONLY to decide whether to surface a dead-letter row when the deterministic
    # matcher found nothing at all; it must never use them to advance status (#10
    # fix-round-1).
    llm_lead_slug: "str | None" = None
    llm_candidates: list = field(default_factory=list)


def _lead_key(note):
    return note.slug


def build_prompt(msg, leads, cfg):
    inflight = "\n".join(
        f"- {n.fm.get('company','')} | {n.fm.get('role','')} | status={n.status}" for n in leads)
    h = msg.get("headers") or {}      # `or {}`/`or ""`: a header can be present-but-None
    atts = msg.get("attachments", []) or []
    att_names = ", ".join(a.get("filename", "") for a in atts if a.get("filename")) or "none"
    ics_present = "yes" if any(
        (a.get("filename", "").lower().endswith(".ics") or "calendar" in a.get("mime", "").lower()) for a in atts
    ) else "no"
    return (
        "You track a job seeker's live applications. Classify this email against the "
        "in-flight applications below. Return ONLY a JSON object with keys: lead (the "
        "company name of the matching application, or null), type (one of "
        "phone_screen, interview, rejection, offer, update, receipt, not_job), confidence "
        "(0..1), when (ISO datetime of any interview, or null), links (array of URLs), "
        "materials (array of short descriptions of any attachments or prep links), "
        "summary (one short line). Only match an application actually listed. Do not "
        "invent a company. A receipt is an automated acknowledgement that an application "
        "was submitted or received (for example \"we received your application\" or "
        "\"thanks for applying\") -- classify it as receipt even when its company is not "
        "in the list below, setting lead to null in that case rather than forcing another "
        "type.\n\n"
        f"IN-FLIGHT APPLICATIONS:\n{inflight}\n\n"
        f"EMAIL:\nFrom: {h.get('from') or ''}\nSubject: {h.get('subject') or ''}\n\n"
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
        # `or ""` throughout, never a .get default: the default only covers a MISSING
        # key, so a present-but-None Subject would stamp a literal "None" into the
        # receipt evidence section reconcile writes (and a None headers dict would raise
        # AttributeError). Same treatment as receipt._headers -- see its docstring for
        # why a raise here becomes a permanently re-failing poison message.
        h = msg.get("headers") or {}
        ev.sender = h.get("from") or ""
        ev.subject = h.get("subject") or ""
        if ev.type != "receipt":
            ev.lead_slug, ev.candidates = _resolve_lead(data.get("lead"), leads)
        else:
            # lead_slug/candidates stay unset: engine.run resolves receipts by domain
            # (match_receipt), never by name. The LLM's own guess still goes somewhere --
            # llm_lead_slug/llm_candidates, resolved against this SAME in-flight `leads`
            # list -- so engine.run can surface (never advance) a receipt whose sender
            # carries no domain evidence the deterministic matcher can use. Since #136,
            # match_receipt searches shortlist AND in-flight leads together, so a lead
            # already advanced past shortlist is no longer structurally invisible to it the
            # way it once was -- this fallback now covers a message with no usable host
            # evidence at all, not merely "the lead had already advanced".
            ev.llm_lead_slug, ev.llm_candidates = _resolve_lead(data.get("lead"), leads)
    except Exception:
        # A classification we could NOT make is not evidence of "not a job" (#40). The default
        # Event.type is `not_job`, and reconcile silently SKIPS an unmatched not_job/update --
        # so returning the bare default here reports any backend error, malformed response, or
        # parse bug as a confident "this is not a job email", and a rejection email vanishes
        # while its lead sits at `applied` forever. `not_job` is a claim, and the exception
        # path has no evidence for it. Surface `unknown` instead so reconcile proposes it for
        # a human rather than swallowing it.
        _log.exception("classify failed for message %s", msg.get("message_id", ""))
        return Event(message_id=msg.get("message_id", ""), thread_id=msg.get("thread_id", ""),
                     ics=ics, type="unknown")
    return ev
