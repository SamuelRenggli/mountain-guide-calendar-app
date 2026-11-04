"""Which days can a guest book? Computed from rules, never stored per day.

A day is free when it is a bookable weekday (and, as soon as the guide has
opened any current periods, inside one of them) and not covered by a manual block,
an external calendar event, an active booking or a published scheduled date (R-105). The booking window
(min notice / max advance) applies to the start date of a tour.
"""

from datetime import date, timedelta

from django.utils import timezone

from .models import BlockedPeriod, Booking, Departure, ExternalBusy, OpenPeriod

FREE = "free"
UNAVAILABLE = "unavailable"  # weekday not offered
BUSY = "busy"  # external calendar
BLOCKED = "blocked"  # manual block
REQUESTED = "requested"  # open request / offer
DEPARTURE = "departure"  # published scheduled date
BOOKED = "booked"  # confirmed or paid
PAST = "past"

# When several things apply to a day, the most important one wins.
_PRIORITY = {FREE: 0, UNAVAILABLE: 1, BUSY: 2, BLOCKED: 3, REQUESTED: 4, DEPARTURE: 5, BOOKED: 6, PAST: 7}


def days_between(start: date, end: date):
    """All days from start to end, both inclusive."""
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def day_states(guide, start: date, end: date) -> dict[date, str]:
    today = timezone.localdate()
    weekdays = guide.weekday_set
    states = {d: PAST if d < today else (FREE if d.weekday() in weekdays else UNAVAILABLE) for d in days_between(start, end)}

    def mark(range_start, range_end, state):
        for d in days_between(max(range_start, start), min(range_end, end)):
            if _PRIORITY[state] > _PRIORITY[states[d]]:
                states[d] = state

    overlapping = {"guide": guide, "start_date__lte": end, "end_date__gte": start}
    if guide.uses_open_periods:
        opened = set()
        for s, e in OpenPeriod.objects.filter(**overlapping).values_list("start_date", "end_date"):
            opened.update(days_between(max(s, start), min(e, end)))
        for d, state in states.items():
            if state == FREE and d not in opened:
                states[d] = UNAVAILABLE

    for s, e in ExternalBusy.objects.filter(**overlapping).values_list("start_date", "end_date"):
        mark(s, e, BUSY)
    for s, e in BlockedPeriod.objects.filter(**overlapping).values_list("start_date", "end_date"):
        mark(s, e, BLOCKED)
    live = (Departure.Status.OPEN, Departure.Status.CONFIRMED)
    for s, e in Departure.objects.filter(status__in=live, **overlapping).values_list("start_date", "end_date"):
        mark(s, e, DEPARTURE)
    requests = Booking.objects.filter(status__in=Booking.ACTIVE_STATUSES, is_window=False, departure__isnull=True,
                                      **overlapping)
    for s, e, status in requests.values_list("start_date", "end_date", "status"):
        held = BOOKED if status in Booking.BOOKED_STATUSES else REQUESTED
        mark(s, e, held)
    return states


def booking_window(guide) -> tuple[date, date]:
    today = timezone.localdate()
    return today + timedelta(days=guide.min_notice_days), today + timedelta(days=guide.max_advance_days)


def range_bookable(guide, start: date, end: date) -> bool:
    """Every day of start..end is free and the start lies in the booking window (fixed-date request, R-020)."""
    earliest, latest = booking_window(guide)
    if start < earliest or end > latest:
        return False
    return all(state == FREE for state in day_states(guide, start, end).values())
