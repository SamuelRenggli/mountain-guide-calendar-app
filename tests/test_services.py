from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from bookings import services
from bookings.models import Booking, InvoiceLine, Tour

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


def test_confirm_reserves_days_and_bills_later(tour, mailoutbox, django_capture_on_commit_callbacks):
    booking = request(tour, in_days(10))
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.confirm(booking, "Freue mich!", price=Decimal("1200"))
    assert booking.status == S.CONFIRMED
    assert booking.deadline is None
    assert booking.price == Decimal("1200")
    assert "CHF 1200.00 + Spesen" in mailoutbox[-1].body
    assert "Nach der Tour erhältst du die Rechnung" in mailoutbox[-1].body
    with pytest.raises(services.DatesUnavailable):
        request(tour, in_days(10))


def tour_price(amount):
    return InvoiceLine(kind=InvoiceLine.Kind.TOUR, description="Tourenpreis", quantity=1, unit_price=Decimal(amount))


def expense(description, amount):
    return InvoiceLine(kind=InvoiceLine.Kind.EXPENSE, description=description, quantity=1, unit_price=Decimal(amount))


def test_invoice_after_tour(tour, mailoutbox, django_capture_on_commit_callbacks):
    booking = services.confirm(request(tour, in_days(10)), price=750)
    with pytest.raises(services.BookingError, match="ersten Tourentag"):
        services.invoice(booking, [tour_price("750")])
    Booking.objects.filter(pk=booking.pk).update(start_date=in_days(-2), end_date=in_days(-2))
    booking.refresh_from_db()
    lines = [tour_price("1500"), expense("Hütte", "82.50")]
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.invoice(booking, lines, "Danke!")
    assert (booking.status, booking.price) == (S.AWAITING_PAYMENT, Decimal("1582.50"))
    assert booking.is_invoiced and booking.deadline is None
    assert [line.description for line in booking.invoice_lines.all()] == ["Tourenpreis", "Hütte"]
    mail = mailoutbox[-1]
    assert mail.subject == f"Rechnung für deine Tour vom {in_days(-2):%d.%m.%Y}"
    assert "1582.50" in mail.body and "Hütte" in mail.body


def test_invoice_can_be_corrected_until_paid(tour):
    booking = services.confirm(request(tour, in_days(10)), price=750)
    Booking.objects.filter(pk=booking.pk).update(start_date=in_days(-1), end_date=in_days(-1))
    booking.refresh_from_db()
    services.invoice(booking, [tour_price("750"), expense("Bahn", "40")])
    booking = services.invoice(booking, [tour_price("750")])
    assert booking.price == Decimal("750.00")
    assert booking.invoice_lines.count() == 1
    booking = services.mark_paid_manually(booking)
    assert booking.status == S.PAID
    with pytest.raises(services.InvalidTransition):
        services.invoice(booking, [tour_price("750")])


def test_invalid_transition(tour):
    booking = services.confirm(request(tour, in_days(10)), price=750)
    with pytest.raises(services.InvalidTransition):
        services.confirm(booking, price=750)
    with pytest.raises(services.InvalidTransition):
        services.accept_offer(booking)


def test_counter_offer_moves_dates(tour, make_booking):
    booking = request(tour, in_days(10))
    offered = services.counter_offer(booking, start_date=in_days(20), price=Decimal("1500"), message="Lieber später")
    assert (offered.status, offered.start_date, offered.price) == (S.OFFERED, in_days(20), Decimal("1500"))
    assert services.accept_offer(offered).status == S.CONFIRMED


def test_counter_offer_conflict(tour, make_booking):
    make_booking(tour, in_days(20), status=S.PAID)
    booking = request(tour, in_days(10))
    with pytest.raises(services.DatesUnavailable):
        services.counter_offer(booking, start_date=in_days(20), price=Decimal("1500"), message="x")
    booking.refresh_from_db()
    assert booking.start_date == in_days(10)


def test_declined_offer_frees_dates(tour):
    booking = request(tour, in_days(10))
    booking = services.counter_offer(booking, start_date=in_days(12), price=Decimal("750"), message="x")
    services.decline_offer(booking)
    request(tour, in_days(12))  # no conflict anymore


def test_guest_cancel_and_guide_cancel(tour):
    assert services.guest_cancel(request(tour, in_days(10))).status == S.CANCELLED
    assert services.guest_cancel(services.confirm(request(tour, in_days(12)), price=750)).status == S.CANCELLED
    assert services.guide_cancel(services.confirm(request(tour, in_days(14)), price=750), "Wetter").status == S.CANCELLED


def test_invoiced_booking_cannot_be_cancelled(tour, make_booking):
    invoiced = make_booking(tour, in_days(-3), status=S.AWAITING_PAYMENT, invoiced_at=timezone.now())
    for action in (services.guest_cancel, services.guide_cancel, services.reject):
        with pytest.raises(services.InvalidTransition):
            action(invoiced)
    assert services.mark_paid_manually(invoiced).payment_method == Booking.PaymentMethod.MANUAL


def test_expire_overdue(tour, mailoutbox, django_capture_on_commit_callbacks):
    old = request(tour, in_days(10))
    Booking.objects.filter(pk=old.pk).update(deadline=timezone.now() - timedelta(minutes=1))
    fresh = request(tour, in_days(12))
    Booking.objects.create(  # an unpaid bill never expires
        guide=tour.guide, start_date=in_days(-5), end_date=in_days(-5), participants=1, price=750,
        guest_name="G", guest_email="g@example.com", status=S.AWAITING_PAYMENT, deadline=timezone.now() - timedelta(days=1),
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert services.expire_overdue() == 1
    old.refresh_from_db()
    fresh.refresh_from_db()
    assert old.status == S.EXPIRED
    assert fresh.status == S.REQUESTED
