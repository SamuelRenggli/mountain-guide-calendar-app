from datetime import timedelta

from bookings import availability
from bookings.models import BlockedPeriod, Booking, ExternalBusy, OpenPeriod, Tour

from .conftest import in_days


def test_free_day_is_bookable(tour):
    assert availability.can_start_on(tour, in_days(10))


def test_booking_window(tour):
    tour.guide.min_notice_days = 3
    tour.guide.max_advance_days = 30
    tour.guide.save()
    assert not availability.can_start_on(tour, in_days(-1))
    assert not availability.can_start_on(tour, in_days(2))
    assert availability.can_start_on(tour, in_days(3))
    assert availability.can_start_on(tour, in_days(30))
    assert not availability.can_start_on(tour, in_days(31))


def test_weekday_rules(tour):
    day = in_days(10)
    tour.guide.available_weekdays = "".join(str(i) for i in range(7) if i != day.weekday())
    tour.guide.save()
    assert not availability.can_start_on(tour, day)
    assert availability.can_start_on(tour, day + timedelta(days=1))


def test_blocks_busy_days_and_bookings(tour, make_booking):
    BlockedPeriod.objects.create(guide=tour.guide, start_date=in_days(10), end_date=in_days(11))
    ExternalBusy.objects.create(guide=tour.guide, start_date=in_days(13), end_date=in_days(13))
    make_booking(tour, in_days(15))
    make_booking(tour, in_days(17), status=Booking.Status.REJECTED)

    assert not availability.can_start_on(tour, in_days(10))
    assert not availability.can_start_on(tour, in_days(11))
    assert availability.can_start_on(tour, in_days(12))
    assert not availability.can_start_on(tour, in_days(13))
    assert not availability.can_start_on(tour, in_days(15))
    assert availability.can_start_on(tour, in_days(17)), "rejected bookings free their dates"


def test_other_guides_data_does_not_matter(tour, other_guide):
    BlockedPeriod.objects.create(guide=other_guide, start_date=in_days(10), end_date=in_days(10))
    assert availability.can_start_on(tour, in_days(10))


def test_multi_day_tour_needs_all_days_free(guide):
    tour = Tour.objects.create(guide=guide, title="Haute Route", price=1500, duration_days=3)
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(12), end_date=in_days(12))
    startable = availability.startable_days(tour, in_days(5), in_days(15))
    assert in_days(9) in startable  # 9, 10, 11
    assert in_days(10) not in startable  # 10, 11, 12 -> 12 blocked
    assert in_days(12) not in startable
    assert in_days(13) in startable


def test_day_states_priority(tour, make_booking):
    ExternalBusy.objects.create(guide=tour.guide, start_date=in_days(10), end_date=in_days(12))
    make_booking(tour, in_days(20), status=Booking.Status.PAID)
    make_booking(tour, in_days(21))
    states = availability.day_states(tour.guide, in_days(-1), in_days(22))
    assert states[in_days(-1)] == availability.PAST
    assert states[in_days(10)] == availability.BUSY
    assert states[in_days(20)] == availability.BOOKED
    assert states[in_days(21)] == availability.REQUESTED
    assert states[in_days(22)] == availability.FREE


def test_open_periods_only_apply_when_enabled(tour):
    OpenPeriod.objects.create(guide=tour.guide, start_date=in_days(10), end_date=in_days(12))
    assert availability.can_start_on(tour, in_days(20)), "periods are ignored while the setting is off"

    tour.guide.only_open_periods = True
    tour.guide.save()
    assert availability.can_start_on(tour, in_days(10))
    assert availability.can_start_on(tour, in_days(12))
    assert not availability.can_start_on(tour, in_days(9))
    assert not availability.can_start_on(tour, in_days(13))


def test_open_periods_still_respect_blocks_and_weekdays(guide):
    guide.only_open_periods = True
    guide.save()
    tour = Tour.objects.create(guide=guide, title="Haute Route", price=1500, duration_days=3)
    OpenPeriod.objects.create(guide=guide, start_date=in_days(10), end_date=in_days(20))
    BlockedPeriod.objects.create(guide=guide, start_date=in_days(15), end_date=in_days(15))
    startable = availability.startable_days(tour, in_days(5), in_days(25))
    assert in_days(10) in startable
    assert in_days(13) not in startable  # 13, 14, 15 -> 15 blocked
    assert in_days(16) in startable
    assert in_days(18) in startable  # 18, 19, 20
    assert in_days(19) not in startable  # would end after the open period


def test_no_open_periods_means_nothing_bookable(guide):
    guide.only_open_periods = True
    guide.save()
    states = availability.day_states(guide, in_days(1), in_days(30))
    assert availability.FREE not in states.values()
