"""QA probe tests (guest view). Tests marked BUG fail on purpose and document a confirmed defect."""

import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from bookings import availability, services
from bookings.forms import form_timestamp_token
from bookings.models import Booking, Departure

from .conftest import in_days

S = Booking.Status


def aged_token(seconds=10):
    with mock.patch("django.core.signing.time.time", return_value=time.time() - seconds):
        return form_timestamp_token()


def widget_post(client, guide, start, end=None, **extra):
    data = {
        "start_date": start.isoformat(), "end_date": (end or start).isoformat(), "participants": 2,
        "guest_name": "Gina Gast", "guest_email": "gina@example.com", "guest_phone": "", "guest_message": "Biancograt",
        "website": "", "ts": aged_token(), "language": "de", "accept_terms": "on",
    }
    data.update(extra)
    return client.post(reverse("widget:request", args=[guide.slug]), data)


def signup_post(client, departure, **extra):
    data = {"seats": 2, "guest_name": "Neu", "guest_email": "neu@example.com", "guest_phone_0": "41",
            "guest_phone_1": "079 123 45 67", "emergency_contact": "Mama 079 2", "experience": "Allalin",
            "language": "de", "accept_terms": "on", "website": "", "ts": aged_token()}
    data.update(extra)
    return client.post(reverse("widget:departure_signup", args=[departure.public_id]), data)


def act(client, booking, name):
    return client.post(reverse("guest:action", args=[booking.token, name]))


# =====================================================================================
# BUG probes (expected to FAIL until fixed)
# =====================================================================================


def test_late_guest_cancel_frees_days(client, guide, make_booking, django_capture_on_commit_callbacks):
    """Questionnaire Q1 (answer A): guest cancels a confirmed tour late -> Storno invoice (awaiting_payment). The tour
    won't happen, so the days are free again right away."""
    day = in_days(3)
    booking = make_booking(guide, day, status=S.CONFIRMED, price=Decimal("1000"),
                           cancel_free_days=14, cancel_partial_days=7, cancel_partial_percent=50)
    with django_capture_on_commit_callbacks(execute=True):
        act(client, booking, "stornieren")
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT  # storno fee billed (correct)
    assert booking.price == Decimal("1000.00")
    assert availability.day_states(guide, day, day)[day] == availability.FREE
    # and the guide can take a new request on that day
    other = make_booking(guide, day, status=S.REQUESTED, guest_email="neu@example.com")
    assert other.pk


def test_declined_new_date_with_costs_frees_days(client, guide, make_booking,
                                                                  django_capture_on_commit_callbacks):
    day = in_days(20)
    booking = make_booking(guide, day, status=S.RESCHEDULE_PROPOSED, price=Decimal("900"),
                           incurred_costs=Decimal("120"), incurred_costs_note="Hütte")
    with django_capture_on_commit_callbacks(execute=True):
        act(client, booking, "ersatzdatum-ablehnen")
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT and booking.price == Decimal("120.00")
    assert availability.day_states(guide, day, day)[day] == availability.FREE


def test_guest_can_still_cancel_after_the_tour(client, guide, make_booking, django_capture_on_commit_callbacks):
    """Questionnaire Q2 (answer C): cancelling stays possible after the tour; the fee is 100 % anyway."""
    booking = make_booking(guide, in_days(-3), end_date=in_days(-2), status=S.CONFIRMED, price=Decimal("1500"),
                           estimated_expenses=Decimal("300"))
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Verbindlich stornieren" in html
    with django_capture_on_commit_callbacks(execute=True):
        act(client, booking, "stornieren")
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT and booking.price == Decimal("1500.00")


def test_bug_guest_accepts_offer_after_deadline(client, guide, make_booking):
    """Offer deadline passed (the hourly expire job hasn't run yet): the guest can still accept."""
    booking = make_booking(guide, in_days(10), status=S.OFFERED, price=900,
                           deadline=timezone.now() - timedelta(minutes=30))
    act(client, booking, "annehmen")
    booking.refresh_from_db()
    assert booking.status != S.CONFIRMED


def test_bug_guest_accepts_offer_for_past_date(client, guide, make_booking):
    """Offer for tomorrow with the standard 5-day answer window: guest accepts after the tour date has passed."""
    booking = make_booking(guide, in_days(-1), status=S.OFFERED, price=900,
                           deadline=timezone.now() + timedelta(days=3))
    act(client, booking, "annehmen")
    booking.refresh_from_db()
    assert booking.status != S.CONFIRMED


def test_bug_guest_accepts_new_date_after_deadline(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.RESCHEDULE_PROPOSED, price=900,
                           deadline=timezone.now() - timedelta(minutes=30))
    act(client, booking, "ersatzdatum-annehmen")
    booking.refresh_from_db()
    assert booking.status != S.CONFIRMED


def test_bug_widget_request_from_old_tab_is_silently_dropped(client, guide):
    """Form open > 4 h (MAX_FORM_AGE_SECONDS): the guest gets the success page, but no request exists."""
    response = widget_post(client, guide, in_days(10), ts=aged_token(5 * 60 * 60))
    created = Booking.objects.exists()
    assert created or response.status_code != 200, "success page shown, request silently discarded"


def test_registration_cancellable_after_tour(client, stripe_guide, make_departure, make_registration,
                                                 django_capture_on_commit_callbacks):
    """Questionnaire Q2 (answer C): a paid registration can still be cancelled after the tour, at 100 % fee."""
    departure = make_departure(stripe_guide, in_days(10))
    Departure.objects.filter(pk=departure.pk).update(start_date=in_days(-5), end_date=in_days(-4),
                                                     status=Departure.Status.CONFIRMED)
    departure.refresh_from_db()
    booking = make_registration(departure, seats=1, start_date=in_days(-5), end_date=in_days(-4))
    with django_capture_on_commit_callbacks(execute=True):
        act(client, booking, "stornieren")
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED and booking.refund_due == 0


def test_bug_waitlist_accepts_more_seats_than_tour_has(client, stripe_guide, make_departure, make_registration):
    departure = make_departure(stripe_guide, min_participants=1, max_participants=1)
    make_registration(departure)
    data = {"guest_name": "W", "guest_email": "w@example.com", "seats": 50, "language": "de", "website": "",
            "ts": aged_token()}
    client.post(reverse("widget:departure_waitlist", args=[departure.public_id]), data)
    assert not departure.waitlist.exists(), "waitlist for 50 seats on a 1-seat tour"


# =====================================================================================
# Checks without finding (should pass)
# =====================================================================================

ALL_STATUSES = list(S)
ACTIONS = ["annehmen", "ablehnen", "ersatzdatum-annehmen", "ersatzdatum-ablehnen", "stornieren", "gibtsnicht"]


@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("name", ACTIONS)
def test_every_action_in_every_status_custom(client, guide, make_booking, status, name,
                                            django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), status=status, price=Decimal("900"),
                           invoiced_at=timezone.now() if status in (S.AWAITING_PAYMENT, S.PAID) else None)
    with django_capture_on_commit_callbacks(execute=True):
        response = act(client, booking, name)
    assert response.status_code == 302
    assert client.get(reverse("guest:detail", args=[booking.token])).status_code == 200


@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("name", ACTIONS)
def test_every_action_in_every_status_registration(client, stripe_guide, make_departure, make_registration, status,
                                                  name, django_capture_on_commit_callbacks):
    departure = make_departure(stripe_guide)
    booking = make_registration(departure, status=status)
    with django_capture_on_commit_callbacks(execute=True):
        response = act(client, booking, name)
    assert response.status_code == 302
    assert client.get(reverse("guest:detail", args=[booking.token])).status_code == 200
    booking.refresh_from_db()
    assert booking.refund_due + booking.refunded_amount <= booking.amount_paid


def test_double_accept_and_accept_after_cancel(client, guide, make_booking):
    booking = make_booking(guide, in_days(30), status=S.OFFERED, price=900)  # outside the fee window
    act(client, booking, "annehmen")
    act(client, booking, "annehmen")
    booking.refresh_from_db()
    assert booking.status == S.CONFIRMED and booking.events.count() == 1
    act(client, booking, "stornieren")
    act(client, booking, "annehmen")
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED


def test_pay_without_stripe_config(client, guide, make_booking):
    booking = make_booking(guide, in_days(-5), status=S.AWAITING_PAYMENT, price=900, invoiced_at=timezone.now())
    response = client.post(reverse("guest:pay", args=[booking.token]))
    assert response.status_code == 302 and response["Location"] == reverse("guest:detail", args=[booking.token])
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


def test_message_and_details_edge_cases(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.REQUESTED)
    # details not available before the offer: just redirect
    assert client.post(reverse("guest:details", args=[booking.token]), {}).status_code == 302
    booking.status = S.CONFIRMED
    booking.save()
    r = client.post(reverse("guest:details", args=[booking.token]), {"angaben-billing_address": ""})
    assert r.status_code == 400
    r = client.post(reverse("guest:details", args=[booking.token]),
                    {"angaben-billing_address": "Weg 1", "angaben-emergency_name": "A\nB",
                     "angaben-emergency_phone_0": "41", "angaben-emergency_phone_1": "79 123 45 67"})
    assert r.status_code == 400


@pytest.mark.parametrize("extra", [
    {"participants": 0}, {"participants": -1}, {"participants": 51}, {"participants": 10 ** 12},
    {"participants": "abc"}, {"guest_name": ""}, {"guest_email": "kein"}, {"guest_message": ""},
    {"start_date": "2026-02-30"}, {"language": "xx"},
])
def test_widget_invalid_input(client, guide, extra):
    assert widget_post(client, guide, in_days(10), **extra).status_code == 400
    assert not Booking.objects.exists()


def test_widget_date_edge_cases(client, guide, make_booking):
    assert widget_post(client, guide, in_days(-3)).status_code == 400
    assert widget_post(client, guide, in_days(12), in_days(10)).status_code == 400
    assert widget_post(client, guide, in_days(10), in_days(400)).status_code == 400
    assert widget_post(client, guide, in_days(5000)).status_code == 400
    assert not Booking.objects.exists()
    assert widget_post(client, guide, in_days(10)).status_code == 200
    assert widget_post(client, guide, in_days(10)).status_code == 400  # double submit
    assert Booking.objects.count() == 1


def test_widget_get_with_garbage_params(client, guide):
    url = reverse("widget:show", args=[guide.slug])
    for q in [{"von": "x", "bis": "y", "monat": "z"}, {"monat": "0-0"}, {"monat": "9999-12"}, {"von": "0001-01-01"},
              {"von": "9999-12-31", "bis": "0001-01-01"}, {"monat": "2026-1-1"}]:
        assert client.get(url, q).status_code == 200


def test_widget_unknown_and_inactive(client, guide):
    assert client.get(reverse("widget:show", args=["gibtsnicht"])).status_code == 404
    guide.is_active = False
    guide.save()
    assert client.get(reverse("widget:show", args=[guide.slug])).status_code == 404
    assert widget_post(client, guide, in_days(10)).status_code == 404


def test_departure_signup_edge_cases(client, stripe_guide, make_departure, make_registration):
    departure = make_departure(stripe_guide)
    assert signup_post(client, departure, seats=5).status_code == 400
    assert signup_post(client, departure, seats=0).status_code == 400
    make_registration(departure, seats=4)
    assert signup_post(client, departure, seats=1).status_code == 400
    closed = make_departure(stripe_guide, signup_deadline=timezone.now() + timedelta(hours=1))
    Departure.objects.filter(pk=closed.pk).update(signup_deadline=timezone.now() - timedelta(minutes=1))
    assert signup_post(client, closed, seats=1).status_code == 400
    cancelled = make_departure(stripe_guide, status=Departure.Status.CANCELLED)
    assert client.get(reverse("widget:departure", args=[cancelled.public_id])).status_code == 200
    assert signup_post(client, cancelled, seats=1).status_code == 400
    assert Booking.objects.filter(status=S.PAYMENT_PENDING).count() == 0


def test_waitlist_while_seats_free(client, stripe_guide, make_departure):
    departure = make_departure(stripe_guide)
    data = {"guest_name": "W", "guest_email": "w@example.com", "seats": 1, "language": "de", "website": "",
            "ts": aged_token()}
    assert client.post(reverse("widget:departure_waitlist", args=[departure.public_id]), data).status_code == 400


def test_signup_without_stripe_releases_seats(client, guide, make_departure):
    guide.stripe_account_id, guide.stripe_charges_enabled = "acct_x", True
    guide.save()
    departure = make_departure(guide)
    assert signup_post(client, departure).status_code == 400
    assert Booking.objects.get().status == S.EXPIRED and departure.seats_free == 4


def test_cancel_fee_shown_matches_billed(client, guide, make_booking, django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED, price=Decimal("1000"),
                           cancel_free_days=14, cancel_partial_days=7, cancel_partial_percent=50)
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "50 % (CHF 500" in html
    with django_capture_on_commit_callbacks(execute=True):
        act(client, booking, "stornieren")
    booking.refresh_from_db()
    assert booking.price == Decimal("500.00") and booking.status == S.AWAITING_PAYMENT


def test_registration_reschedule_cancel_full_refund(client, stripe_guide, make_departure, make_registration,
                                                    django_capture_on_commit_callbacks):
    departure = make_departure(stripe_guide, in_days(3))
    booking = make_registration(departure, seats=2, status=S.RESCHEDULE_PROPOSED, amount_paid=Decimal("960"))
    with mock.patch("payments.stripe_service.process_refund"), django_capture_on_commit_callbacks(execute=True):
        act(client, booking, "stornieren")
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED and booking.refund_due == Decimal("960.00")


def test_expire_job_then_guest_action(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.OFFERED, price=900,
                           deadline=timezone.now() - timedelta(minutes=1))
    services.expire_overdue()
    act(client, booking, "annehmen")
    booking.refresh_from_db()
    assert booking.status == S.EXPIRED
