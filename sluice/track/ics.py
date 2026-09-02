"""Pure .ics (RFC5545) parse: enough of a VEVENT to schedule/cancel a calendar
event. Stdlib only. Handles line folding, TZID/UTC/date-only DTSTART."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sluice.core.log import get_logger

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_log = get_logger("track.ics")

# Windows/Exchange timezone names -> IANA. Outlook and Exchange write
# `DTSTART;TZID=GMT Standard Time:...` rather than an IANA key, and `ZoneInfo` cannot resolve
# those, so without this table such a DTSTART falls through to NAIVE. Naive is not merely
# unsendable (calendar_sync has to coerce it to satisfy RFC 3339) -- it is WRONG:
# `_event_body` stamps `timeZone: "UTC"` when `start.tzinfo is None`, which books a
# British-Summer-Time invite an hour late.
#
# A subset of CLDR's windowsZones table, so plenty of real Windows names are absent -- and an
# absent name is NOT harmless. It degrades to the naive fallback below rather than raising,
# and that naive value is exactly the hour-late booking above; it is only quieter than a
# crash, not safer. `IcsEvent.tzid_unresolved` carries the name out so calendar_sync can say
# so out loud at the point of the write. The same is true of a name that IS mapped but whose
# IANA target this host's tzdb lacks (`Europe/Kyiv` needs tzdata >= 2022b), which is why the
# fallback records the tzid rather than silently discarding it.
#
# INVARIANT: every key here must be lowercase -- the lookup is `.get(key.lower(), key)`, so a
# capitalised entry is dead code that never matches. Pinned by test_every_windows_tz_key_is_lowercase.
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
    # Was a SEQUENCE stated that we could NOT read? Same shape as `tzid_unresolved` below,
    # and for the same reason: "stated but unreadable" and "not stated" are different
    # facts, and collapsing them costs something as soon as anyone compares (#202). RFC
    # 5545 makes an absent SEQUENCE genuinely 0, so the tolerant coercion stays and this
    # flag is what lets a comparer abstain instead of treating a mangled line as revision
    # zero -- which loses to every recorded revision and discards a real reschedule.
    sequence_unreadable: bool = False
    status: str = ""
    start: "datetime | None" = None
    end: "datetime | None" = None
    # When the SENDER produced this revision (#202). RFC 5545's second arbiter between two
    # VEVENTs sharing a UID, after SEQUENCE. It IS written to the calendar body, as the
    # private `sluice-track-stamp` tag that makes the next run's comparison possible -- what
    # it never becomes is a BOOKING instant: `start` alone fills `start`/`end`, and this
    # never moves an appointment. An earlier draft of this comment said "never written to
    # the calendar body", which `_event_body` falsifies on the line that stores it.
    #
    # None when absent or unreadable, and the two need not be told apart: both mean the
    # tie cannot be broken, and the caller applies rather than refuses. A default stamp
    # would be worse than none, silently ordering revisions by a time nobody sent.
    dtstamp: "datetime | None" = None
    summary: str = ""
    location: str = ""
    url: str = ""
    # The raw TZID of a DTSTART that STATED a zone we could not read (unmapped Windows name,
    # or an IANA key absent from this host's tzdb). Empty when the zone resolved AND when
    # none was stated at all -- those two are different facts and must not collapse:
    # legal RFC 5545 floating time has no better answer than the caller's default, whereas an
    # unreadable TZID means the invite named an instant we failed to decode. calendar_sync
    # warns on the second so the assumption is visible instead of silently wrong.
    tzid_unresolved: str = ""

    @property
    def cancelled(self) -> bool:
        return self.method.upper() == "CANCEL" or self.status.upper() == "CANCELLED"


def _unfold(text: str) -> str:
    # RFC5545: a CRLF (or LF) followed by a space or tab continues the prior line.
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_dt(value: str, params: dict):
    """(datetime, unresolved_tzid). The second is non-empty ONLY when a TZID was stated and
    we failed to resolve it -- never for a date-only or TZID-less value, which are naive for
    reasons the caller cannot improve on."""
    v = value.strip()
    try:
        return _parse_dt_strict(v, params)
    except ValueError:
        # A pure parser must not raise on a malformed VALUE, for the same reason it must not
        # raise on an unresolvable TZID: `parse_ics` runs inside `engine.run`'s per-message
        # handler, so one mojibake DTSTART took down the WHOLE message -- classification,
        # status advance and calendar write -- and the invite became a dead-letter failure
        # row instead of the interview it described. `2026-08-17T15:30:00Z` (ISO where RFC
        # 5545 wants a basic-format stamp) is enough to do it. Returning None leaves the
        # cancel/no-DTSTART path already built for "we could not read a start" (#143).
        _log.warning("track: unparseable DTSTART/DTEND %r -- treating the event as having "
                     "no start", value)
        return None, ""


# RFC 5545 DATE-TIME is exactly `YYYYMMDDTHHMMSS` (optionally `Z`); DATE is exactly
# `YYYYMMDD`. Anchored and length-exact because `strptime` is NOT: `%H%M%S` against a
# truncated `1530` reads 15:03:00 rather than failing, so a clipped stamp booked an interview
# at the WRONG HOUR silently -- the failure class this module was last fixed for, arriving by
# a different door. Surfaced by a test written for #143's raise, not by the issue.
_DT_SHAPE = re.compile(r"^\d{8}T\d{6}Z?$")
_DATE_SHAPE = re.compile(r"^\d{8}$")


def _parse_dt_strict(v: str, params: dict):
    """The parse itself. Split out so the tolerant wrapper above has ONE place to catch, and
    so a future format can be added here without anyone having to notice the try/except."""
    if not (_DT_SHAPE.match(v) or _DATE_SHAPE.match(v)):
        raise ValueError(f"not an RFC 5545 DATE or DATE-TIME: {v!r}")
    if v.endswith("Z"):
        return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), ""
    if "T" in v:
        dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        if tzid and ZoneInfo:
            # Normalise for the lookup only: dequote (RFC 5545 permits a quoted param value)
            # then trim, in that order -- `" GMT Standard Time "` has its quotes on the
            # OUTSIDE, so stripping whitespace first leaves them attached. `.lower()` is what
            # matches the table, whose keys are lowercase by invariant; the spaces and dots in
            # `w. europe standard time` are carried verbatim BY those keys, not normalised
            # away. An unmapped name passes through dequoted-but-case-preserved, so a plain
            # IANA key still resolves and anything else lands in the except below.
            key = tzid.strip('"').strip()
            key = _WINDOWS_TZ.get(key.lower(), key)
            try:
                return dt.replace(tzinfo=ZoneInfo(key)), ""
            except Exception:
                return dt, tzid
        return dt, ""
    return datetime.strptime(v, "%Y%m%d"), ""


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
            # Same tolerance as DTSTART, and for the same reason: `parse_ics` runs inside
            # `engine.run`'s per-message handler and attachment bytes are decoded with
            # `errors="replace"`, so one mangled line took down classification, the status
            # advance and the calendar write. `SEQUENCE:abc` and `SEQUENCE:1.0` both raised.
            # The invariant is stated sixty lines above this and was applied to one field.
            try:
                ev.sequence = int(value.strip() or 0)
            except ValueError:
                _log.warning("track: unparseable SEQUENCE %r -- treating it as 0 and "
                             "marking it unreadable, so revision arbitration abstains "
                             "rather than reading it as the earliest revision", value)
                ev.sequence = 0
                ev.sequence_unreadable = True
        elif name == "STATUS":
            ev.status = value.strip()
        elif name == "DTSTART":
            # Only DTSTART's unresolved zone is recorded: it is the instant everything
            # downstream keys on (`_find_ours`, the proximity match, the calendar body),
            # and `_event_body` falls back to `ics.start` when `end` is missing anyway.
            ev.start, ev.tzid_unresolved = _parse_dt(value, params)
        elif name == "DTEND":
            ev.end, _ = _parse_dt(value, params)
        elif name == "DTSTAMP":
            # `_parse_dt` already answers None on anything it cannot read, which is the
            # tolerance the whole module needs: this runs inside `engine.run`'s
            # per-message handler, so raising over a tiebreak field would cost the
            # classification, the status advance and the calendar write -- the entire
            # invite. No `tzid_unresolved` is recorded: that flag drives a warning about
            # a GUESSED booking instant, and this field never books anything.
            ev.dtstamp, _ = _parse_dt(value, params)
        elif name == "SUMMARY":
            ev.summary = value.strip()
        elif name == "LOCATION":
            ev.location = value.strip()
        elif name == "URL":
            ev.url = value.strip()
    return ev if (ev.uid or ev.start) else None
