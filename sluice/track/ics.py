"""Pure .ics (RFC5545) parse: enough of a VEVENT to schedule/cancel a calendar
event. Stdlib only. Handles line folding, TZID/UTC/date-only DTSTART."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# Windows/Exchange timezone names -> IANA. Outlook and Exchange write `TZID:GMT Standard
# Time` rather than an IANA key, and `ZoneInfo` cannot resolve those, so without this table
# such a DTSTART falls through to NAIVE. Naive is not merely unsendable (calendar_sync has to
# coerce it to satisfy RFC 3339) -- it is WRONG: `_event_body` stamps `timeZone: "UTC"` when
# `start.tzinfo is None`, which books a British-Summer-Time invite an hour late. A subset of
# CLDR's windowsZones table; a name that is absent still degrades to the naive fallback below
# rather than raising, so an unrecognised zone is no worse off than before.
_WINDOWS_TZ = {
    "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik",
    "utc": "UTC",
    "w. europe standard time": "Europe/Berlin",
    "central europe standard time": "Europe/Budapest",
    "central european standard time": "Europe/Warsaw",
    "romance standard time": "Europe/Paris",
    "e. europe standard time": "Europe/Chisinau",
    "gtb standard time": "Europe/Bucharest",
    "fle standard time": "Europe/Kyiv",
    "russian standard time": "Europe/Moscow",
    "turkey standard time": "Europe/Istanbul",
    "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "atlantic standard time": "America/Halifax",
    "sa pacific standard time": "America/Bogota",
    "e. south america standard time": "America/Sao_Paulo",
    "india standard time": "Asia/Kolkata",
    "china standard time": "Asia/Shanghai",
    "tokyo standard time": "Asia/Tokyo",
    "korea standard time": "Asia/Seoul",
    "singapore standard time": "Asia/Singapore",
    "arabian standard time": "Asia/Dubai",
    "arab standard time": "Asia/Riyadh",
    "israel standard time": "Asia/Jerusalem",
    "south africa standard time": "Africa/Johannesburg",
    "w. central africa standard time": "Africa/Lagos",
    "aus eastern standard time": "Australia/Sydney",
    "new zealand standard time": "Pacific/Auckland",
}


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
            # RFC 5545 permits a quoted param value, and Outlook's zone names contain spaces
            # and dots, so normalise before the lookup. An unmapped name is passed through
            # unchanged -- an IANA key resolves, anything else lands in the except below.
            key = tzid.strip().strip('"')
            key = _WINDOWS_TZ.get(key.lower(), key)
            try:
                return dt.replace(tzinfo=ZoneInfo(key))
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
