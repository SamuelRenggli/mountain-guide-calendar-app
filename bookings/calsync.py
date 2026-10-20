"""Import busy days from a guide's iCal feed (Google, iCloud, Outlook ...).

The URL is user input, so fetching it is guarded against SSRF: https only, every
hop (including redirects) must resolve to public IP addresses, and the
response size is capped.
"""

import ipaddress
import logging
import socket
from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from icalendar import Calendar

from .models import ExternalBusy

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
SYNC_DAYS_AHEAD = 730


class CalendarSyncError(Exception):
    """User-facing error message."""


def _check_url(url: str):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise CalendarSyncError("Die Adresse muss mit https:// beginnen.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise CalendarSyncError("Server des Kalenders nicht gefunden.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise CalendarSyncError("Diese Adresse ist nicht erlaubt.")


def fetch_ical(url: str) -> bytes:
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url)
        try:
            resp = requests.get(
                url, timeout=(5, 20), stream=True, allow_redirects=False,
                headers={"User-Agent": "Tourchef-Calendar-Sync/1.0"},
            )
        except requests.RequestException as exc:
            raise CalendarSyncError("Kalender konnte nicht geladen werden.") from exc
        with resp:
            if resp.is_redirect:
                url = urljoin(url, resp.headers.get("Location", ""))
                continue
            if resp.status_code != 200:
                raise CalendarSyncError(f"Kalender-Server antwortet mit Fehler {resp.status_code}.")
            data = b""
            for chunk in resp.iter_content(64 * 1024):
                data += chunk
                if len(data) > MAX_BYTES:
                    raise CalendarSyncError("Kalender ist zu gross.")
            return data
    raise CalendarSyncError("Zu viele Weiterleitungen.")


def _to_local(value, tz) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    return datetime.combine(value, time.min, tzinfo=tz)


def busy_ranges(ical_data: bytes, window_start: date, window_end: date, tz=None) -> list[tuple[date, date]]:
    """Inclusive (start, end) day ranges covered by opaque events, merged and clipped to the window."""
    tz = tz or ZoneInfo(settings.TIME_ZONE)
    try:
        cal = Calendar.from_ical(ical_data)
    except ValueError as exc:
        raise CalendarSyncError("Die Adresse liefert keinen gültigen iCal-Kalender.") from exc

    ranges = []
    for event in recurring_ical_events.of(cal).between(window_start, window_end + timedelta(days=1)):
        if str(event.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            continue  # marked as "free" in the calendar
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = event.get("DTSTART")
        if dtstart is None:
            continue
        start = dtstart.dt
        if event.get("DTEND") is not None:
            end = event["DTEND"].dt
        elif event.get("DURATION") is not None:
            end = start + event["DURATION"].dt
        else:
            end = start

        if isinstance(start, datetime):
            # Timed event: busy on every day it touches; ending exactly at midnight doesn't touch the next day.
            local_start, local_end = _to_local(start, tz), _to_local(end, tz)
            first = local_start.date()
            last = local_end.date()
            if local_end.time() == time.min and last > first:
                last -= timedelta(days=1)
        else:
            # All-day event: DTEND is exclusive.
            first = start
            last = end - timedelta(days=1) if end > start else start

        first, last = max(first, window_start), min(last, window_end)
        if first <= last:
            ranges.append((first, last))

    merged: list[tuple[date, date]] = []
    for first, last in sorted(ranges):
        if merged and first <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def sync_guide(guide) -> int:
    """Replace the guide's imported busy days. Returns the number of busy ranges. Raises CalendarSyncError."""
    if not guide.ical_url:
        ExternalBusy.objects.filter(guide=guide).delete()
        return 0
    today = timezone.localdate()
    try:
        ranges = busy_ranges(fetch_ical(guide.ical_url), today, today + timedelta(days=SYNC_DAYS_AHEAD))
    except CalendarSyncError as exc:
        guide.ical_last_error = str(exc)[:300]
        guide.save(update_fields=["ical_last_error"])
        raise
    except Exception as exc:  # malformed feeds can break the parser in many ways
        logger.exception("Calendar sync failed for guide %s", guide.pk)
        guide.ical_last_error = "Kalender konnte nicht gelesen werden."
        guide.save(update_fields=["ical_last_error"])
        raise CalendarSyncError(guide.ical_last_error) from exc

    with transaction.atomic():
        ExternalBusy.objects.filter(guide=guide).delete()
        ExternalBusy.objects.bulk_create(ExternalBusy(guide=guide, start_date=s, end_date=e) for s, e in ranges)
        guide.ical_last_synced_at = timezone.now()
        guide.ical_last_error = ""
        guide.save(update_fields=["ical_last_synced_at", "ical_last_error"])
    return len(ranges)
