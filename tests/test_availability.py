from bookings import availability
from bookings.models import BlockedPeriod, Booking, Departure, ExternalBusy, OpenPeriod

from .conftest import in_days


def free(guide, start, end=None):
    return availability.range_bookable(guide, start, end or start)


def test_free_day_is_bookable(guide):
    assert free(guide, in_days(10))


def test_booking_window(guide):
    guide.min_notice_days = 3
    guide.max_advance_days = 30
    guide.save()
    assert not free(guide, in_days(-1))
    assert not free(guide, in_days(2))
    assert free(guide, in_days(3))
    assert free(guide, in_days(30))
    assert not free(guide, in_days(31))


def test_weekday_rules(guide):
    day = in_days(10)
    guide.available_weekdays = "".join(str(i) for i in range(7) if i != day.weekday())
    guide.save()
    assert not free(guide, day)
    assert free(guide, in_days(11))


def test_blocks_busy_days_and_bookings(guide, make_booking):
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(10), end_date=in_days(11))
    ExternalBusy.objects.create(guide=guide, start_date=in_days(13), end_date=in_days(13))
    make_booking(guide, in_days(15))
    make_booking(guide, in_days(17), status=Booking.Status.REJECTED)

    assert not free(guide, in_days(10))
    assert not free(guide, in_days(11))
    assert free(guide, in_days(12))
    assert not free(guide, in_days(13))
    assert not free(guide, in_days(15))
    assert free(guide, in_days(17)), "rejected bookings free their dates"


def test_every_day_of_a_tour_must_be_free(guide):
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(12), end_date=in_days(12))
    assert free(guide, in_days(9), in_days(11))
    assert not free(guide, in_days(10), in_days(12))


def test_other_guides_data_does_not_matter(guide, other_guide):
    BlockedPeriod.objects.create(guide=other_guide, start_date=in_days(10), end_date=in_days(10))
    assert free(guide, in_days(10))


def test_published_dates_block_requests(guide, make_departure):
    """R-105: the days of a scheduled date can't be requested; a cancelled one frees them."""
    departure = make_departure(guide, in_days(20))
    assert not free(guide, in_days(20))
    assert not free(guide, in_days(21))
    states = availability.day_states(guide, in_days(20), in_days(21))
    assert set(states.values()) == {availability.DEPARTURE}
    Departure.objects.filter(pk=departure.pk).update(status=Departure.Status.CANCELLED)
    assert free(guide, in_days(20))


def test_day_states_priority(guide, make_booking):
    ExternalBusy.objects.create(guide=guide, start_date=in_days(10), end_date=in_days(12))
    make_booking(guide, in_days(20), status=Booking.Status.PAID)
    make_booking(guide, in_days(21))
    states = availability.day_states(guide, in_days(-1), in_days(22))
    assert states[in_days(-1)] == availability.PAST
    assert states[in_days(10)] == availability.BUSY
    assert states[in_days(20)] == availability.BOOKED
    assert states[in_days(21)] == availability.REQUESTED
    assert states[in_days(22)] == availability.FREE


def test_open_periods_apply_once_there_is_one(guide):
    assert free(guide, in_days(20)), "without open periods every day is bookable"
    OpenPeriod.objects.create(guide=guide, start_date=in_days(-10), end_date=in_days(-5))
    assert free(guide, in_days(20)), "periods that are over don't count"

    OpenPeriod.objects.create(guide=guide, start_date=in_days(10), end_date=in_days(12))
    assert not free(guide, in_days(20))
    assert free(guide, in_days(10), in_days(12))
    assert not free(guide, in_days(9))
    assert not free(guide, in_days(12), in_days(13)), "may not end after the open period"
