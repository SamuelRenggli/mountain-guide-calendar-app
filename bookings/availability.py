"""Which days can a guest book? Computed from rules, never stored per day.

A day is free when it is a bookable weekday and not covered by a manual block,
an external calendar event or an active booking. The booking window
(min notice / max advance) applies to the start date of a tour.
"""

from datetime import date, timedelta

from django.utils import timezone

from .models import BlockedPeriod, Booking, ExternalBusy

FREE = "free"
UNAVAILABLE = "unavailable"  # weekday not offered
BUSY = "busy"  # external calendar
BLOCKED = "blocked"  # manual block
REQUESTED = "requested"  # open request / offer
BOOKED = "booked"  # confirmed or paid
PAST = "past"

# When several things apply to a day, the most important one wins.
_PRIORITY = {FREE: 0, UNAVAILABLE: 1, BUSY: 2, BLOCKED: 3, REQUESTED: 4, BOOKED: 5, PAST: 6}


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
    for s, e in ExternalBusy.objects.filter(**overlapping).values_list("start_date", "end_date"):
        mark(s, e, BUSY)
    for s, e in BlockedPeriod.objects.filter(**overlapping).values_list("start_date", "end_date"):
        mark(s, e, BLOCKED)
    for s, e, status in Booking.objects.filter(status__in=Booking.ACTIVE_STATUSES, **overlapping).values_list(
        "start_date", "end_date", "status"
    ):
        held = BOOKED if status in (Booking.Status.AWAITING_PAYMENT, Booking.Status.PAID) else REQUESTED
        mark(s, e, held)
    return states


def booking_window(guide) -> tuple[date, date]:
    today = timezone.localdate()
    return today + timedelta(days=guide.min_notice_days), today + timedelta(days=guide.max_advance_days)


def startable_days(tour, start: date, end: date) -> set[date]:
    """Days between start and end on which `tour` can begin (all its days must be free)."""
    guide = tour.guide
    span = tour.duration_days - 1
    states = day_states(guide, start, end + timedelta(days=span))
    earliest, latest = booking_window(guide)
    return {
        d
        for d in days_between(max(start, earliest), min(end, latest))
        if all(states[d + timedelta(days=i)] == FREE for i in range(span + 1))
    }


def can_start_on(tour, start_date: date) -> bool:
    return start_date in startable_days(tour, start_date, start_date)
