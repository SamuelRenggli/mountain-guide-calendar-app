from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from bookings import services
from bookings.models import Booking, Tour

from .conftest import in_days

S = Booking.Status


def request(tour, start, **extra):
    values = dict(tour=tour, start_date=start, participants=2, guest_name="Gina Gast", guest_email="gina@example.com")
    values.update(extra)
    return services.create_request(**values)


def test_create_request_notifies_guide_and_guest(tour, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        booking = request(tour, in_days(10))
    assert booking.status == S.REQUESTED
    assert booking.price == Decimal("600.00")
    assert booking.deadline > timezone.now()
    assert {m.to[0] for m in mailoutbox} == {tour.guide.email, "gina@example.com"}
    assert booking.events.count() == 1


def test_price_per_person(guide):
    tour = Tour.objects.create(guide=guide, title="Klettern", price=Decimal("150"), price_mode="person", max_participants=4)
    assert request(tour, in_days(10), participants=3).price == Decimal("450.00")


def test_too_many_participants(tour):
    with pytest.raises(services.BookingError):
        request(tour, in_days(10), participants=5)


def test_unavailable_date(tour):
    with pytest.raises(services.DatesUnavailable):
        request(tour, in_days(0))


def test_double_booking_rejected(tour):
    request(tour, in_days(10))
    with pytest.raises(services.DatesUnavailable):
        request(tour, in_days(10))


def test_database_prevents_overlap_even_without_service(tour, make_booking):
    make_booking(tour, in_days(10))
    with pytest.raises(IntegrityError):
        make_booking(tour, in_days(10))


def test_overlap_allowed_for_inactive_and_other_guides(tour, other_guide, make_booking):
    make_booking(tour, in_days(10), status=S.CANCELLED)
    make_booking(tour, in_days(10))
    other_tour = Tour.objects.create(guide=other_guide, title="Eiger", price=900)
    make_booking(other_tour, in_days(10))


def test_confirm_without_stripe_has_no_payment_deadline(tour, mailoutbox, django_capture_on_commit_callbacks):
    booking = request(tour, in_days(10))
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.confirm(booking, "Freue mich!")
    assert booking.status == S.AWAITING_PAYMENT
    assert booking.deadline is None
    assert "Zahlungsangaben" in mailoutbox[-1].body


def test_confirm_with_stripe_sets_deadline(tour, settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    tour.guide.stripe_account_id = "acct_1"
    tour.guide.stripe_charges_enabled = True
    tour.guide.save()
    booking = services.confirm(request(tour, in_days(10)))
    assert booking.deadline is not None


def test_invalid_transition(tour):
    booking = services.confirm(request(tour, in_days(10)))
    with pytest.raises(services.InvalidTransition):
        services.confirm(booking)
    with pytest.raises(services.InvalidTransition):
        services.accept_offer(booking)


def test_counter_offer_moves_dates(tour, make_booking):
    booking = request(tour, in_days(10))
    offered = services.counter_offer(booking, start_date=in_days(20), price=Decimal("550"), message="Lieber später")
    assert (offered.status, offered.start_date, offered.price) == (S.OFFERED, in_days(20), Decimal("550"))
    assert services.accept_offer(offered).status == S.AWAITING_PAYMENT


def test_counter_offer_conflict(tour, make_booking):
    make_booking(tour, in_days(20), status=S.PAID)
    booking = request(tour, in_days(10))
    with pytest.raises(services.DatesUnavailable):
        services.counter_offer(booking, start_date=in_days(20), price=Decimal("550"), message="x")
    booking.refresh_from_db()
    assert booking.start_date == in_days(10)


def test_declined_offer_frees_dates(tour):
    booking = request(tour, in_days(10))
    booking = services.counter_offer(booking, start_date=in_days(12), price=Decimal("1"), message="x")
    services.decline_offer(booking)
    request(tour, in_days(12))  # no conflict anymore


def test_guest_cancel_and_guide_cancel(tour):
    assert services.guest_cancel(request(tour, in_days(10))).status == S.CANCELLED
    paid = services.mark_paid_manually(services.confirm(request(tour, in_days(12))))
    assert paid.payment_method == Booking.PaymentMethod.MANUAL
    with pytest.raises(services.InvalidTransition):
        services.guest_cancel(paid)
    assert services.guide_cancel(paid, "Wetter").status == S.CANCELLED


def test_expire_overdue(tour, mailoutbox, django_capture_on_commit_callbacks):
    old = request(tour, in_days(10))
    Booking.objects.filter(pk=old.pk).update(deadline=timezone.now() - timedelta(minutes=1))
    fresh = request(tour, in_days(12))
    with django_capture_on_commit_callbacks(execute=True):
        assert services.expire_overdue() == 1
    old.refresh_from_db()
    fresh.refresh_from_db()
    assert old.status == S.EXPIRED
    assert fresh.status == S.REQUESTED
