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


# ---- #143: a pure parser must not raise on a malformed VALUE -------------------------------

@pytest.mark.parametrize("dtstart", [
    "2026-08-17T15:30:00Z",      # ISO where RFC 5545 wants basic format -- the reported case
    "not-a-date",
    "20260817T1530",             # truncated
    "",
    "20261301T000000Z",          # month 13
])
def test_a_malformed_DTSTART_does_not_raise_out_of_the_parser(dtstart):
    """`parse_ics` runs inside `engine.run`'s per-message handler, so a raise here took down
    the WHOLE message -- classification, status advance and calendar write -- and the invite
    became a dead-letter failure row instead of the interview it described.

    Same principle the module already applies to an unresolvable TZID: a pure parser reports
    what it could not read, it does not abort the caller.
    """
    ics = parse_ics(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
        f"DTSTART:{dtstart}\r\nSUMMARY:Screen\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics is not None, "the VEVENT must still parse"
    assert ics.uid == "u1", "the fields we COULD read must survive"
    assert ics.start is None, "an unreadable start is None, not a guess"


def test_a_malformed_DTSTART_is_reported_not_swallowed(caplog):
    with caplog.at_level("WARNING", logger="sluice.track.ics"):
        parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                  "DTSTART:not-a-date\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.track.ics"]
    assert said, "a value we could not read must say so"
    assert "not-a-date" in " ".join(said), said


def test_a_WELL_FORMED_dtstart_is_unaffected():
    # The tolerance must be narrow: the ordinary path still parses to an aware datetime.
    ics = parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    "DTSTART:20260715T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.start is not None and ics.start.tzinfo is not None


@pytest.mark.parametrize("dtstart,why", [
    ("20260817T1530", "a truncated stamp read as 15:03 -- strptime is not length-strict"),
    ("20260817T153000+0100", "an offset suffix RFC 5545 does not use in this field"),
])
def test_a_TRUNCATED_or_odd_stamp_is_refused_rather_than_silently_misread(dtstart, why):
    """The sharper half of #143, found by the test rather than the issue.

    Raising was never the worst outcome here. `%H%M%S` against `1530` yields 15:03:00 --
    `strptime` consumes greedily and does not require the field widths it names -- so a
    clipped DTSTART booked the interview an hour and twenty-seven minutes early, silently,
    with `failures=0`. That is the wrong-hour class this module was last fixed for, arriving
    through a different door.
    """
    ics = parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    f"DTSTART:{dtstart}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.start is None, f"{why}: got {ics.start}"


def test_a_date_only_DTSTART_still_parses():
    # The shape guard must not reject the legal DATE form the module already supports.
    ics = parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    "DTSTART;VALUE=DATE:20260715\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.start is not None and ics.start.date().isoformat() == "2026-07-15"


@pytest.mark.parametrize("seq", ["abc", "1.0", "", "  ", "9" * 5000])
def test_a_malformed_SEQUENCE_does_not_raise_either(seq):
    """The same tolerance as DTSTART, applied to the field it was not applied to.

    `SEQUENCE:abc` and `SEQUENCE:1.0` both raised `ValueError` straight out of `parse_ics`,
    sixty lines below the comment stating that a pure parser must not raise on a malformed
    VALUE. Attachment bytes are decoded with `errors="replace"`, so a mangled line is exactly
    what this receives. (The 5000-digit case trips CPython's int-conversion limit.)
    """
    ics = parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\n"
                    f"SEQUENCE:{seq}\r\nDTSTART:20260715T100000Z\r\n"
                    "END:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics is not None and ics.uid == "u1"
    assert ics.start is not None, "an unreadable SEQUENCE must not cost the DTSTART"
    assert isinstance(ics.sequence, int)


def test_a_WELL_FORMED_sequence_is_still_read():
    ics = parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nSEQUENCE:3\r\n"
                    "DTSTART:20260715T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    assert ics.sequence == 3
