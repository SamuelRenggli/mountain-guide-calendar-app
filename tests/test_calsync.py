import socket
from datetime import date
from unittest import mock

import pytest

from bookings import calsync
from bookings.models import ExternalBusy

WINDOW = (date(2026, 1, 1), date(2026, 12, 31))


def ical(*events):
    body = "".join(f"BEGIN:VEVENT\r\nUID:{i}@test\r\nDTSTAMP:20260101T000000Z\r\n{e.strip()}\r\nEND:VEVENT\r\n" for i, e in enumerate(events))
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n{body}END:VCALENDAR\r\n".encode()


def ranges(*events):
    return calsync.busy_ranges(ical(*events), *WINDOW)


def test_all_day_event():
    assert ranges("DTSTART;VALUE=DATE:20260310\r\nDTEND;VALUE=DATE:20260311") == [(date(2026, 3, 10), date(2026, 3, 10))]


def test_multi_day_all_day_event():
    assert ranges("DTSTART;VALUE=DATE:20260310\r\nDTEND;VALUE=DATE:20260313") == [(date(2026, 3, 10), date(2026, 3, 12))]


def test_all_day_without_end():
    assert ranges("DTSTART;VALUE=DATE:20260310") == [(date(2026, 3, 10), date(2026, 3, 10))]


def test_timed_event_blocks_its_day():
    """The old app skipped these entirely."""
    assert ranges("DTSTART:20260310T080000Z\r\nDTEND:20260310T160000Z") == [(date(2026, 3, 10), date(2026, 3, 10))]


def test_timed_event_uses_local_timezone():
    # 23:30 UTC on 9 March is 00:30 on 10 March in Zurich
    assert ranges("DTSTART:20260309T233000Z\r\nDTEND:20260310T010000Z") == [(date(2026, 3, 10), date(2026, 3, 10))]


def test_event_ending_at_midnight_does_not_block_next_day():
    assert ranges("DTSTART;TZID=Europe/Zurich:20260310T180000\r\nDTEND;TZID=Europe/Zurich:20260311T000000") == [
        (date(2026, 3, 10), date(2026, 3, 10))
    ]


def test_overnight_event_blocks_both_days():
    assert ranges("DTSTART;TZID=Europe/Zurich:20260310T180000\r\nDTEND;TZID=Europe/Zurich:20260311T090000") == [
        (date(2026, 3, 10), date(2026, 3, 11))
    ]


def test_transparent_and_cancelled_events_are_ignored():
    assert ranges(
        "DTSTART;VALUE=DATE:20260310\r\nDTEND;VALUE=DATE:20260311\r\nTRANSP:TRANSPARENT",
        "DTSTART;VALUE=DATE:20260312\r\nDTEND;VALUE=DATE:20260313\r\nSTATUS:CANCELLED",
    ) == []


def test_recurring_events_are_expanded():
    result = ranges("DTSTART;VALUE=DATE:20260302\r\nDTEND;VALUE=DATE:20260303\r\nRRULE:FREQ=WEEKLY;COUNT=3")
    assert result == [(date(2026, 3, 2),) * 2, (date(2026, 3, 9),) * 2, (date(2026, 3, 16),) * 2]


def test_ranges_are_merged_and_clipped():
    result = calsync.busy_ranges(
        ical(
            "DTSTART;VALUE=DATE:20260310\r\nDTEND;VALUE=DATE:20260312",
            "DTSTART;VALUE=DATE:20260312\r\nDTEND;VALUE=DATE:20260314",
            "DTSTART;VALUE=DATE:20251230\r\nDTEND;VALUE=DATE:20260103",
        ),
        *WINDOW,
    )
    assert result == [(date(2026, 1, 1), date(2026, 1, 2)), (date(2026, 3, 10), date(2026, 3, 13))]


def test_invalid_calendar():
    with pytest.raises(calsync.CalendarSyncError):
        calsync.busy_ranges(b"<html>not a calendar</html>", *WINDOW)


# --- SSRF protection ---------------------------------------------------------------


def fake_dns(ip):
    return mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "172.17.0.1", "::1"])
def test_private_addresses_rejected(ip):
    with fake_dns(ip), pytest.raises(calsync.CalendarSyncError):
        calsync.fetch_ical("https://calendar.example.com/x.ics")


def test_http_rejected():
    with pytest.raises(calsync.CalendarSyncError):
        calsync.fetch_ical("http://calendar.google.com/x.ics")


def test_redirect_to_private_address_rejected():
    redirect = mock.MagicMock(is_redirect=True, headers={"Location": "https://internal.example.com/"})
    redirect.__enter__.return_value = redirect
    resolve = {"calendar.example.com": "142.250.1.1", "internal.example.com": "10.0.0.1"}

    def getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolve[host], 443))]

    with mock.patch("socket.getaddrinfo", side_effect=getaddrinfo), mock.patch("requests.get", return_value=redirect) as get:
        with pytest.raises(calsync.CalendarSyncError):
            calsync.fetch_ical("https://calendar.example.com/x.ics")
    assert get.call_count == 1


# --- Sync ----------------------------------------------------------------------------


def test_sync_replaces_busy_days(guide):
    guide.ical_url = "https://calendar.example.com/x.ics"
    guide.save()
    ExternalBusy.objects.create(guide=guide, start_date=date(2026, 5, 1), end_date=date(2026, 5, 1))
    with mock.patch.object(calsync, "fetch_ical", return_value=ical("DTSTART;VALUE=DATE:20270310")), \
            mock.patch.object(calsync.timezone, "localdate", return_value=date(2026, 1, 1)):
        assert calsync.sync_guide(guide) == 1
    assert list(ExternalBusy.objects.values_list("start_date", flat=True)) == [date(2027, 3, 10)]
    guide.refresh_from_db()
    assert guide.ical_last_synced_at and guide.ical_last_error == ""


def test_sync_failure_keeps_old_data_and_stores_error(guide):
    guide.ical_url = "https://calendar.example.com/x.ics"
    guide.save()
    ExternalBusy.objects.create(guide=guide, start_date=date(2026, 5, 1), end_date=date(2026, 5, 1))
    with mock.patch.object(calsync, "fetch_ical", side_effect=calsync.CalendarSyncError("kaputt")):
        with pytest.raises(calsync.CalendarSyncError):
            calsync.sync_guide(guide)
    assert ExternalBusy.objects.count() == 1
    guide.refresh_from_db()
    assert guide.ical_last_error == "kaputt"
