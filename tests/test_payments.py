from unittest import mock

import pytest
from django.urls import reverse

from bookings.models import Booking, BookingEvent
from payments import stripe_service
from payments.models import StripeEvent

from .conftest import in_days

S = Booking.Status


@pytest.fixture
def stripe_guide(guide, settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    guide.stripe_account_id = "acct_guide"
    guide.stripe_charges_enabled = True
    guide.save()
    return guide


def test_checkout_only_for_confirmed_bookings(tour, stripe_guide, make_booking):
    booking = make_booking(tour, in_days(10), status=S.REQUESTED)
    with pytest.raises(stripe_service.PaymentError):
        stripe_service.create_checkout_url(booking)


def test_checkout_requires_connected_guide(tour, make_booking, settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    booking = make_booking(tour, in_days(10), status=S.AWAITING_PAYMENT)
    with pytest.raises(stripe_service.PaymentError):
        stripe_service.create_checkout_url(booking)


def test_checkout_pays_guide_with_platform_fee(tour, stripe_guide, make_booking):
    booking = make_booking(tour, in_days(10), status=S.AWAITING_PAYMENT)
    session = mock.Mock(id="cs_1", url="https://checkout.stripe.com/c/cs_1")
    with mock.patch("stripe.checkout.Session.create", return_value=session) as create:
        assert stripe_service.create_checkout_url(booking) == session.url
    kwargs = create.call_args.kwargs
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 60000
    assert kwargs["line_items"][0]["price_data"]["currency"] == "chf"
    assert kwargs["payment_intent_data"]["transfer_data"] == {"destination": "acct_guide"}
    assert kwargs["payment_intent_data"]["on_behalf_of"] == "acct_guide"
    assert kwargs["payment_intent_data"]["application_fee_amount"] == 6000
    assert kwargs["metadata"] == {"booking_id": str(booking.pk)}
    booking.refresh_from_db()
    assert booking.stripe_checkout_session_id == "cs_1"


def test_new_checkout_expires_previous_session(tour, stripe_guide, make_booking):
    booking = make_booking(tour, in_days(10), status=S.AWAITING_PAYMENT, stripe_checkout_session_id="cs_old")
    with mock.patch("stripe.checkout.Session.retrieve", return_value=mock.Mock(id="cs_old", status="open")), \
            mock.patch("stripe.checkout.Session.expire") as expire, \
            mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(id="cs_new", url="u")):
        stripe_service.create_checkout_url(booking)
    expire.assert_called_once()


# --- Webhook ---------------------------------------------------------------------------


def completed_event(booking, event_id="evt_1", amount=None, payment_status="paid"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_1", "payment_status": payment_status, "currency": "chf",
            "amount_total": booking.amount_cents if amount is None else amount,
            "metadata": {"booking_id": str(booking.pk)},
        }},
    }


def post_webhook(client, event):
    with mock.patch("stripe.Webhook.construct_event", return_value=event):
        return client.post(reverse("payments:webhook"), b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=x")


def test_webhook_rejects_bad_signature(client, db):
    response = client.post(reverse("payments:webhook"), b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="bad")
    assert response.status_code == 400


def test_webhook_marks_booking_paid_once(client, tour, make_booking, mailoutbox, django_capture_on_commit_callbacks):
    booking = make_booking(tour, in_days(10), status=S.AWAITING_PAYMENT)
    with django_capture_on_commit_callbacks(execute=True):
        assert post_webhook(client, completed_event(booking)).status_code == 200
        assert post_webhook(client, completed_event(booking)).status_code == 200  # redelivery
    booking.refresh_from_db()
    assert booking.status == S.PAID
    assert booking.payment_method == Booking.PaymentMethod.STRIPE
    assert len(mailoutbox) == 2  # guest + guide, not duplicated
    assert StripeEvent.objects.count() == 1


def test_webhook_ignores_unpaid_session(client, tour, make_booking):
    booking = make_booking(tour, in_days(10), status=S.AWAITING_PAYMENT)
    post_webhook(client, completed_event(booking, payment_status="unpaid"))
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


def test_webhook_amount_mismatch_alerts(client, tour, make_booking, mailoutbox):
    booking = make_booking(tour, in_days(10), status=S.AWAITING_PAYMENT)
    post_webhook(client, completed_event(booking, amount=100))
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert BookingEvent.objects.filter(booking=booking, note__startswith="ACHTUNG").exists()


def test_webhook_payment_for_cancelled_booking_alerts(client, tour, make_booking):
    booking = make_booking(tour, in_days(10), status=S.CANCELLED)
    post_webhook(client, completed_event(booking))
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED
    assert BookingEvent.objects.filter(booking=booking, note__startswith="ACHTUNG").exists()


def test_webhook_account_updated(client, guide):
    guide.stripe_account_id = "acct_guide"
    guide.save()
    post_webhook(client, {"id": "evt_a", "type": "account.updated", "data": {"object": {"id": "acct_guide", "charges_enabled": True}}})
    guide.refresh_from_db()
    assert guide.stripe_charges_enabled
