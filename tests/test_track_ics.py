from datetime import timedelta, timezone
from sluice.track.ics import parse_ics


_REQUEST = (
    "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
    "UID:abc-123@flowline\r\nSEQUENCE:0\r\nSTATUS:CONFIRMED\r\n"
    "DTSTART;TZID=Europe/London:20260715T110000\r\n"
    "DTEND;TZID=Europe/London:20260715T113000\r\n"
    "SUMMARY:flowline first-stage screen\r\nLOCATION:Google Meet\r\n"
    "URL:https://meet.google.com/abc\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
_CANCEL = _REQUEST.replace("METHOD:REQUEST", "METHOD:CANCEL").replace("SEQUENCE:0", "SEQUENCE:1")


def test_parse_request():
    e = parse_ics(_REQUEST)
    assert e.method == "REQUEST" and e.uid == "abc-123@flowline" and e.sequence == 0
    assert e.summary == "flowline first-stage screen" and e.url.endswith("/abc")
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


def test_windows_timezone_name_resolves_to_the_real_offset():
    # Outlook/Exchange write WINDOWS zone names, not IANA ones, so ZoneInfo() raises and the
    # value used to fall through to naive. Naive is not merely unsendable (the API wants
    # RFC 3339) -- it is WRONG: _event_body stamps timeZone "UTC" for a naive start, booking a
    # British-Summer-Time invite an hour late.
    e = parse_ics("BEGIN:VEVENT\r\nUID:u\r\n"
                  "DTSTART;TZID=GMT Standard Time:20260817T153000\r\nEND:VEVENT")
    assert e.start.tzinfo is not None
    assert e.start.utcoffset() == timedelta(hours=1)   # 17 Aug is BST, i.e. UTC+1


def test_quoted_windows_timezone_name_resolves_too():
    # RFC 5545 permits a QUOTED param value, and `parse_ics` stores param values raw, so the
    # quotes reach `_parse_dt` and would be part of the lookup key. Kept as a second case
    # rather than replacing the unquoted one: unquoted is the form the invites that motivated
    # this actually carry, so swapping would trade real coverage for hypothetical coverage.
    e = parse_ics("BEGIN:VEVENT\r\nUID:u\r\n"
                  'DTSTART;TZID="GMT Standard Time":20260817T153000\r\nEND:VEVENT')
    assert e.start.tzinfo is not None
    assert e.start.utcoffset() == timedelta(hours=1)


def test_unknown_timezone_name_still_parses_as_naive():
    # Fallback stays intact: an unrecognisable TZID must not raise out of a pure parser.
    e = parse_ics("BEGIN:VEVENT\r\nUID:u\r\n"
                  "DTSTART;TZID=Nowhere/Notreal:20260817T153000\r\nEND:VEVENT")
    assert e.start.hour == 15 and e.start.tzinfo is None


def test_line_folding_and_no_vevent():
    folded = "BEGIN:VEVENT\r\nUID:u\r\nSUMMARY:Very long\r\n  wrapped title\r\nEND:VEVENT"
    assert parse_ics(folded).summary == "Very long wrapped title"
    assert parse_ics("no event here") is None
