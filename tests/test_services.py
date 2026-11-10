"""Custom tour requests: fixed dates, offers, changes, weather, cancellation, billing."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import DayRateTier
from bookings import services
from bookings.models import Booking, InvoiceLine

from .conftest import in_days

S = Booking.Status


def request(guide, start, end=None, **extra):
    values = dict(guide=guide, start_date=start, end_date=end or start, participants=2, guest_name="Gina Gast",
                  guest_email="gina@example.com", guest_message="Biancograt")
    values.update(extra)
    return services.create_request(**values)


def confirm(booking, price, message="", **extra):
    """The guide accepts the request as asked and the guest agrees."""
    offered = services.accept_request(booking, start_date=booking.start_date, end_date=booking.end_date, price=price,
                                      message=message, **extra)
    return services.accept_offer(offered)


def test_create_request_holds_fixed_dates_and_notifies(guide, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        booking = request(guide, in_days(10), in_days(11))
    assert booking.status == S.REQUESTED and not booking.is_window
    assert booking.price == 0 and booking.deadline > timezone.now()
    assert booking.terms_accepted_at is not None
    assert (booking.cancel_free_days, booking.cancel_partial_days, booking.cancel_partial_percent) == (14, 7, 50)
    assert {m.to[0] for m in mailoutbox} == {guide.email, "gina@example.com"}
    with pytest.raises(services.DatesUnavailable):
        request(guide, in_days(11))


def test_request_needs_every_day_free(guide, make_booking):
    make_booking(guide, in_days(12), status=S.CONFIRMED)
    with pytest.raises(services.DatesUnavailable):
        request(guide, in_days(10), in_days(13))


def test_request_length_and_size_limits(guide, settings):
    with pytest.raises(services.BookingError):
        request(guide, in_days(10), in_days(10 + settings.MAX_REQUEST_DAYS))
    with pytest.raises(services.BookingError):
        request(guide, in_days(10), participants=51)
    with pytest.raises(services.DatesUnavailable):
        request(guide, in_days(0))


def test_database_prevents_overlap_even_without_service(guide, make_booking):
    make_booking(guide, in_days(10))
    with pytest.raises(IntegrityError):
        make_booking(guide, in_days(10))


def test_overlap_allowed_for_inactive_other_guides_and_registrations(guide, other_guide, make_booking, make_departure,
                                                                     make_registration):
    make_booking(guide, in_days(10), status=S.CANCELLED)
    make_booking(guide, in_days(10))
    make_booking(other_guide, in_days(10))
    departure = make_departure(guide, in_days(30))
    make_registration(departure)
    make_registration(departure, email="zwei@example.com")  # registrations share the date


def test_accept_needs_guest_agreement_and_explains_billing(guide, mailoutbox, django_capture_on_commit_callbacks):
    """The guide accepts with price and expense estimate: pending until the guest agrees; paid after the tour."""
    booking = request(guide, in_days(10), in_days(11))
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.accept_request(booking, start_date=in_days(10), end_date=in_days(11), price=Decimal("1500"),
                                          message="Freue mich!", estimated_expenses=Decimal("300"),
                                          decision_date=in_days(8))
    assert booking.status == S.OFFERED and booking.deadline is not None and booking.decision_date == in_days(8)
    mail = mailoutbox[-1]
    assert "hat deine Anfrage angenommen" in mail.subject
    assert "CHF 1500.00 + Spesen (geschätzt ca. CHF 300, unverbindlich)" in mail.body
    assert "Bezahlt wird erst nach der Tour" in mail.body and "Freue mich!" in mail.body
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.accept_offer(booking)
    assert booking.status == S.CONFIRMED and booking.deadline is None
    guest_mail = [m for m in mailoutbox if m.to == ["gina@example.com"]][-1]
    assert "#angaben" in guest_mail.body


def test_accept_can_add_a_day_but_not_a_busy_one(guide, make_booking):
    booking = request(guide, in_days(10))
    make_booking(guide, in_days(12), status=S.CONFIRMED)
    with pytest.raises(services.DatesUnavailable) as err:
        services.accept_request(booking, start_date=in_days(10), end_date=in_days(12), price=2250)
    assert f"{in_days(12):%d.%m.%Y}" in str(err.value)
    booking = services.accept_request(booking, start_date=in_days(10), end_date=in_days(11), price=1500)
    assert (booking.start_date, booking.end_date) == (in_days(10), in_days(11))
    with pytest.raises(services.DatesUnavailable):
        request(guide, in_days(11))  # the added day is held provisionally


def test_day_rate_tiers_by_group_size(guide):
    """R-200."""
    DayRateTier.objects.create(guide=guide, min_participants=5, day_rate=850)
    DayRateTier.objects.create(guide=guide, min_participants=7, day_rate=950)
    assert [guide.rate_for(n) for n in (1, 4, 5, 6, 7, 12)] == [750, 750, 850, 850, 950, 950]
    assert guide.rate_table() == [(1, 4, 750), (5, 6, 850), (7, None, 950)]


def tour_price(amount):
    return InvoiceLine(kind=InvoiceLine.Kind.TOUR, description="Tourenpreis", quantity=1, unit_price=Decimal(amount))


def expense(description, amount):
    return InvoiceLine(kind=InvoiceLine.Kind.EXPENSE, description=description, quantity=1, unit_price=Decimal(amount))


def test_invoice_after_tour(guide, mailoutbox, django_capture_on_commit_callbacks):
    booking = confirm(request(guide, in_days(10)), price=750)
    with pytest.raises(services.BookingError):
        services.invoice(booking, [tour_price("750")])
    Booking.objects.filter(pk=booking.pk).update(start_date=in_days(-2), end_date=in_days(1))
    booking.refresh_from_db()
    with pytest.raises(services.BookingError):
        services.invoice(booking, [tour_price("750")])  # only after the tour
    Booking.objects.filter(pk=booking.pk).update(start_date=in_days(-2), end_date=in_days(-2))
    booking.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.invoice(booking, [tour_price("1500"), expense("Hütte", "82.50")], "Danke!")
    assert (booking.status, booking.price) == (S.AWAITING_PAYMENT, Decimal("1582.50"))
    assert [line.description for line in booking.invoice_lines.all()] == ["Tourenpreis", "Hütte"]
    mail = mailoutbox[-1]
    assert mail.subject == f"Rechnung für deine Tour vom {in_days(-2):%d.%m.%Y}"
    assert "1582.50" in mail.body and "Hütte" in mail.body and "MWST" not in mail.body


def test_invoice_shows_vat_when_guide_is_liable(guide, mailoutbox, django_capture_on_commit_callbacks):
    """R-202: prices include VAT; the bill shows the share."""
    guide.vat_number, guide.vat_rate = "CHE-123.456.789 MWST", Decimal("8.1")
    guide.save()
    booking = confirm(request(guide, in_days(10)), price=1081)
    Booking.objects.filter(pk=booking.pk).update(start_date=in_days(-1), end_date=in_days(-1))
    booking.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.invoice(booking, [tour_price("1081")])
    assert booking.vat_included() == Decimal("81.00")
    assert "inkl. 8.1 % MWST CHF 81.00 · MWST-Nr. CHE-123.456.789 MWST" in mailoutbox[-1].body


def test_invoice_can_be_corrected_until_paid(guide, mailoutbox, django_capture_on_commit_callbacks):
    booking = confirm(request(guide, in_days(10)), price=750)
    Booking.objects.filter(pk=booking.pk).update(start_date=in_days(-1), end_date=in_days(-1))
    booking.refresh_from_db()
    services.invoice(booking, [tour_price("750"), expense("Bahn", "40")])
    booking = services.invoice(booking, [tour_price("750")])
    assert booking.price == Decimal("750.00") and booking.invoice_lines.count() == 1
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.mark_paid_manually(booking)
    assert (booking.status, booking.amount_paid) == (S.PAID, Decimal("750.00"))
    assert "Zahlungsbeleg" in mailoutbox[-2].body, "R-204: the guest gets a receipt"
    with pytest.raises(services.InvalidTransition):
        services.invoice(booking, [tour_price("750")])


def test_invalid_transition(guide):
    booking = confirm(request(guide, in_days(10)), price=750)
    with pytest.raises(services.InvalidTransition):
        services.accept_request(booking, start_date=in_days(10), end_date=in_days(10), price=750)
    with pytest.raises(services.InvalidTransition):
        services.accept_offer(booking)


def test_counter_offer_moves_dates(guide):
    booking = request(guide, in_days(10))
    offered = services.accept_request(booking, start_date=in_days(20), end_date=in_days(21), price=Decimal("1500"),
                                      message="Lieber später")
    assert (offered.status, offered.start_date, offered.end_date) == (S.OFFERED, in_days(20), in_days(21))
    assert services.accept_offer(offered).status == S.CONFIRMED


def test_counter_offer_conflict(guide, make_booking):
    make_booking(guide, in_days(20), status=S.PAID)
    booking = request(guide, in_days(10))
    with pytest.raises(services.DatesUnavailable):
        services.accept_request(booking, start_date=in_days(20), end_date=in_days(20), price=1500, message="x")
    booking.refresh_from_db()
    assert booking.start_date == in_days(10)


def test_declined_offer_frees_dates(guide):
    booking = services.accept_request(request(guide, in_days(10)), start_date=in_days(12), end_date=in_days(12),
                                      price=750, message="x")
    services.decline_offer(booking)
    request(guide, in_days(12))


def test_guide_changes_confirmed_booking(guide, mailoutbox, django_capture_on_commit_callbacks):
    """R-024: the change is logged and the guest gets an email listing it."""
    booking = confirm(request(guide, in_days(10)), price=750)
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.change_booking(booking, start_date=in_days(11), end_date=in_days(12), participants=3,
                                          price=Decimal("1650"))
    assert (booking.start_date, booking.participants, booking.price, booking.status) == (in_days(11), 3, 1650, S.CONFIRMED)
    event = booking.events.last()
    assert "participants: 2 → 3" in event.note and "CHF 750.00 → CHF 1650.00" in event.note
    mail = mailoutbox[-1]
    assert mail.to == ["gina@example.com"] and "Teilnehmende: 2 → 3" in mail.body
    with pytest.raises(services.BookingError):
        services.change_booking(booking, start_date=in_days(11), end_date=in_days(12), participants=3, price=1650)


def test_weather_replacement_date_accepted(guide, mailoutbox, django_capture_on_commit_callbacks):
    """R-025: the guide proposes another date; the new days are held."""
    booking = confirm(request(guide, in_days(10)), price=750)
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.propose_new_date(booking, start_date=in_days(17), end_date=in_days(17), message="Föhnsturm")
    assert (booking.status, booking.start_date) == (S.RESCHEDULE_PROPOSED, in_days(17))
    assert "Ersatzdatum" in mailoutbox[-1].subject
    request(guide, in_days(10))  # the old day is free again
    with pytest.raises(services.DatesUnavailable):
        request(guide, in_days(17))
    assert services.accept_new_date(booking).status == S.CONFIRMED


def test_weather_replacement_declined_is_free(guide):
    booking = confirm(request(guide, in_days(10)), price=750)
    booking = services.propose_new_date(booking, start_date=in_days(17), end_date=in_days(17))
    assert services.decline_new_date(booking).status == S.CANCELLED


def test_weather_replacement_declined_bills_incurred_costs(guide, mailoutbox, django_capture_on_commit_callbacks):
    """R-026: free, except costs the guide already had and listed."""
    booking = confirm(request(guide, in_days(10)), price=750)
    booking = services.propose_new_date(booking, start_date=in_days(17), end_date=in_days(17), incurred_costs=Decimal("60"),
                                        incurred_costs_note="Hüttenanzahlung")
    with django_capture_on_commit_callbacks(execute=True):
        booking = services.decline_new_date(booking)
    assert (booking.status, booking.price) == (S.AWAITING_PAYMENT, Decimal("60.00"))
    assert booking.invoice_lines.get().description == "Hüttenanzahlung"
    assert any("Rechnung" in m.subject for m in mailoutbox)


def test_unanswered_replacement_date_counts_as_no(guide):
    booking = confirm(request(guide, in_days(10)), price=750)
    booking = services.propose_new_date(booking, start_date=in_days(17), end_date=in_days(17))
    Booking.objects.filter(pk=booking.pk).update(deadline=timezone.now() - timedelta(minutes=1))
    assert services.expire_overdue() == 1
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED


@pytest.mark.parametrize("days_before, percent", [(20, 0), (14, 0), (13, 50), (7, 50), (6, 100), (1, 100)])
def test_cancellation_template(guide, days_before, percent):
    """R-003: until 14 days 0 %, until 7 days 50 %, then 100 %."""
    booking = Booking(guide=guide, start_date=in_days(days_before), end_date=in_days(days_before), price=1000,
                      cancel_free_days=14, cancel_partial_days=7, cancel_partial_percent=50)
    assert services.cancellation_percent(booking) == percent


def test_cancellation_uses_terms_accepted_by_guest(guide):
    booking = request(guide, in_days(10))
    guide.cancel_free_days, guide.cancel_partial_days, guide.cancel_partial_percent = 30, 20, 80
    guide.save()
    assert services.cancellation_percent(booking) == 50, "changed terms don't apply to earlier bookings"


def test_late_guest_cancellation_of_request_is_billed(guide):
    booking = confirm(request(guide, in_days(10)), price=Decimal("1000"))
    booking = services.guest_cancel(booking)
    assert (booking.status, booking.price) == (S.AWAITING_PAYMENT, Decimal("500.00"))
    early = services.guest_cancel(confirm(request(guide, in_days(30)), price=1000))
    assert early.status == S.CANCELLED


def test_guest_cancel_and_guide_cancel(guide):
    assert services.guest_cancel(request(guide, in_days(10))).status == S.CANCELLED
    assert services.guide_cancel(confirm(request(guide, in_days(14)), price=750), "Krank").status == S.CANCELLED


def test_invoiced_booking_cannot_be_cancelled(guide, make_booking):
    invoiced = make_booking(guide, in_days(-3), status=S.AWAITING_PAYMENT, invoiced_at=timezone.now(), price=750)
    for action in (services.guest_cancel, services.guide_cancel, services.reject):
        with pytest.raises(services.InvalidTransition):
            action(invoiced)
    assert services.mark_paid_manually(invoiced).payment_method == Booking.PaymentMethod.MANUAL


def test_expire_overdue(guide, make_booking, django_capture_on_commit_callbacks):
    old = request(guide, in_days(10))
    Booking.objects.filter(pk=old.pk).update(deadline=timezone.now() - timedelta(minutes=1))
    fresh = request(guide, in_days(12))
    make_booking(guide, in_days(-5), status=S.AWAITING_PAYMENT, deadline=timezone.now() - timedelta(days=1), price=750)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.expire_overdue() == 1
    old.refresh_from_db()
    fresh.refresh_from_db()
    assert (old.status, fresh.status) == (S.EXPIRED, S.REQUESTED)
