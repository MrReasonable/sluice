"""Pure .ics (RFC5545) parse: enough of a VEVENT to schedule/cancel a calendar
event. Stdlib only. Handles line folding, TZID/UTC/date-only DTSTART."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


@dataclass
class IcsEvent:
    method: str = ""
    uid: str = ""
    sequence: int = 0
    status: str = ""
    start: "datetime | None" = None
    end: "datetime | None" = None
    summary: str = ""
    location: str = ""
    url: str = ""

    @property
    def cancelled(self) -> bool:
        return self.method.upper() == "CANCEL" or self.status.upper() == "CANCELLED"


def _unfold(text: str) -> str:
    # RFC5545: a CRLF (or LF) followed by a space or tab continues the prior line.
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_dt(value: str, params: dict):
    v = value.strip()
    if v.endswith("Z"):
        return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if "T" in v:
        dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        if tzid and ZoneInfo:
            try:
                return dt.replace(tzinfo=ZoneInfo(tzid))
            except Exception:
                return dt
        return dt
    return datetime.strptime(v, "%Y%m%d")


def parse_ics(text: str):
    if not text or "BEGIN:VEVENT" not in text:
        return None
    ev = IcsEvent()
    for line in _unfold(text).splitlines():
        if ":" not in line:
            continue
        name_part, _, value = line.partition(":")
        bits = name_part.split(";")
        name = bits[0].upper()
        params = {}
        for p in bits[1:]:
            if "=" in p:
                k, _, pv = p.partition("=")
                params[k.upper()] = pv
        if name == "METHOD":
            ev.method = value.strip()
        elif name == "UID":
            ev.uid = value.strip()
        elif name == "SEQUENCE":
            ev.sequence = int(value.strip() or 0)
        elif name == "STATUS":
            ev.status = value.strip()
        elif name == "DTSTART":
            ev.start = _parse_dt(value, params)
        elif name == "DTEND":
            ev.end = _parse_dt(value, params)
        elif name == "SUMMARY":
            ev.summary = value.strip()
        elif name == "LOCATION":
            ev.location = value.strip()
        elif name == "URL":
            ev.url = value.strip()
    return ev if (ev.uid or ev.start) else None
