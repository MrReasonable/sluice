from datetime import timedelta, timezone

import pytest

from sluice.track.ics import _WINDOWS_TZ, parse_ics


_REQUEST = (
    "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
    "UID:abc-123@example.invalid\r\nSEQUENCE:0\r\nSTATUS:CONFIRMED\r\n"
    "DTSTART;TZID=Europe/London:20260715T110000\r\n"
    "DTEND;TZID=Europe/London:20260715T113000\r\n"
    "SUMMARY:Example Meridian first-stage screen\r\nLOCATION:Example Meet\r\n"
    "URL:https://meet.example.invalid/abc\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
_CANCEL = _REQUEST.replace("METHOD:REQUEST", "METHOD:CANCEL").replace("SEQUENCE:0", "SEQUENCE:1")


def test_parse_request():
    e = parse_ics(_REQUEST)
    assert e.method == "REQUEST" and e.uid == "abc-123@example.invalid" and e.sequence == 0
    assert e.summary == "Example Meridian first-stage screen" and e.url.endswith("/abc")
    assert e.start.hour == 11 and e.start.tzinfo is not None
    assert e.cancelled is False


def test_parse_utc_and_date_only():
    utc = parse_ics("BEGIN:VEVENT\r\nUID:u\r\nDTSTART:20260715T100000Z\r\nEND:VEVENT")
    assert utc.start.tzinfo == timezone.utc and utc.start.hour == 10
    d = parse_ics("BEGIN:VEVENT\r\nUID:u\r\nDTSTART;VALUE=DATE:20260715\r\nEND:VEVENT")
    assert d.start.year == 2026 and d.start.month == 7 and d.start.day == 15


def test_cancel_flagged():
    assert parse_ics(_CANCEL).cancelled is True
    assert parse_ics("BEGIN:VEVENT\r\nUID:u\r\nSTATUS:CANCELLED\r\nEND:VEVENT").cancelled is True


def _dtstart(tzid_param, value="20260715T110000"):
    return f"BEGIN:VEVENT\r\nUID:u\r\nDTSTART{tzid_param}:{value}\r\nEND:VEVENT"


def test_windows_timezone_name_resolves_to_the_real_offset():
    # Outlook/Exchange write WINDOWS zone names, not IANA ones, so ZoneInfo() raises and the
    # value used to fall through to naive. Naive is not merely unsendable (the API wants
    # RFC 3339) -- it is WRONG: _event_body stamps timeZone "UTC" for a naive start, booking a
    # British-Summer-Time invite an hour late. A July date is load-bearing: in January
    # Europe/London is UTC+0 and a mutant mapping this name to UTC would pass.
    e = parse_ics(_dtstart(";TZID=GMT Standard Time"))
    assert e.start.tzinfo is not None
    assert e.start.utcoffset() == timedelta(hours=1)   # 15 Jul is BST, i.e. UTC+1
    assert e.tzid_unresolved == ""                      # resolved, so nothing to warn about


def test_quoted_windows_timezone_name_resolves_too():
    # RFC 5545 permits a QUOTED param value, and `parse_ics` stores param values raw, so the
    # quotes reach `_parse_dt` and would be part of the lookup key. Kept as a second case
    # rather than replacing the unquoted one: both forms are legal and the unquoted one is
    # the shape this table was written for, so swapping would drop coverage rather than move it.
    e = parse_ics(_dtstart(';TZID="GMT Standard Time"'))
    assert e.start.utcoffset() == timedelta(hours=1)


def test_padded_and_quoted_timezone_name_resolves():
    # Dequote-then-trim, in that order. The quotes are on the OUTSIDE, so trimming first
    # leaves them attached and the lookup misses -- which is what the original ordering did.
    e = parse_ics(_dtstart(';TZID=" GMT Standard Time "'))
    assert e.start.utcoffset() == timedelta(hours=1)


def test_unknown_timezone_name_parses_naive_AND_records_the_tzid():
    # Fallback stays intact: an unrecognisable TZID must not raise out of a pure parser. But
    # naive-and-silent is the hour-late booking, so the parser records WHICH zone it failed to
    # read; calendar_sync turns that into a warning at the point of the write.
    e = parse_ics(_dtstart(";TZID=Nowhere/Notreal"))
    assert e.start.hour == 11 and e.start.tzinfo is None
    assert e.tzid_unresolved == "Nowhere/Notreal"


def test_floating_and_date_only_starts_record_no_tzid():
    # Three inputs parse naive; only ONE of them is a failure to read a stated zone. Floating
    # time and a date-only DTSTART named no zone at all, so there is nothing we failed at and
    # nothing to warn about -- collapsing them would make the warning cry wolf on every
    # all-day event.
    assert parse_ics(_dtstart("")).tzid_unresolved == ""
    assert parse_ics(_dtstart(";VALUE=DATE", "20260715")).tzid_unresolved == ""
    # ...and the date-only case really is naive, i.e. a third naive source beyond the two the
    # calendar_sync docstring used to enumerate.
    assert parse_ics(_dtstart(";VALUE=DATE", "20260715")).start.tzinfo is None


@pytest.mark.parametrize("win_name", sorted(_WINDOWS_TZ))
def test_every_mapped_windows_zone_resolves_through_the_parser(win_name):
    # Only "gmt standard time" was ever driven, leaving 30 entries where a typo, or an IANA
    # key this host's tzdb lacks (Europe/Kyiv needs tzdata >= 2022b), fails OPEN: `except
    # Exception: return dt` degrades to naive, silently, for that whole zone.
    e = parse_ics(_dtstart(f";TZID={win_name}"))
    assert e.start.tzinfo is not None, (
        f"{win_name!r} maps to {_WINDOWS_TZ[win_name]!r}, which this tzdata cannot load")
    assert e.tzid_unresolved == ""


def test_every_windows_tz_key_is_lowercase():
    # The lookup is `_WINDOWS_TZ.get(key.lower(), key)`, so a capitalised entry added later
    # would be dead code that never matches -- and its zone would silently book an hour wrong.
    # The table test above cannot catch it: it feeds the key back in already-lowercased.
    assert [k for k in _WINDOWS_TZ if k != k.lower()] == []


def test_line_folding_and_no_vevent():
    folded = "BEGIN:VEVENT\r\nUID:u\r\nSUMMARY:Very long\r\n  wrapped title\r\nEND:VEVENT"
    assert parse_ics(folded).summary == "Very long wrapped title"
    assert parse_ics("no event here") is None
