from decimal import Decimal
from unittest import mock

import pytest
import stripe
from django.urls import reverse

from bookings import departures
from bookings.models import Booking, BookingEvent
from payments import stripe_service
from payments.models import StripeEvent

from .conftest import in_days

S = Booking.Status


def test_checkout_only_for_billed_bookings(stripe_guide, make_booking):
    booking = make_booking(stripe_guide, in_days(10), status=S.REQUESTED)
    with pytest.raises(stripe_service.PaymentError):
        stripe_service.create_checkout_url(booking)


def test_checkout_requires_connected_guide(guide, make_booking, settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    booking = make_booking(guide, in_days(10), status=S.AWAITING_PAYMENT, price=600)
    with pytest.raises(stripe_service.PaymentError):
        stripe_service.create_checkout_url(booking)


def test_checkout_pays_guide_card_and_twint_only(stripe_guide, make_booking):
    booking = make_booking(stripe_guide, in_days(10), status=S.AWAITING_PAYMENT, price=Decimal("600"), language="fr")
    session = mock.Mock(id="cs_1", url="https://checkout.stripe.com/c/cs_1")
    with mock.patch("stripe.checkout.Session.create", return_value=session) as create:
        assert stripe_service.create_checkout_url(booking) == session.url
    kwargs = create.call_args.kwargs
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 60000
    assert kwargs["payment_method_types"] == ["card", "twint"], "decision 37: no bank transfer"
    assert kwargs["locale"] == "fr"
    assert kwargs["payment_intent_data"]["transfer_data"] == {"destination": "acct_guide"}
    assert kwargs["payment_intent_data"]["on_behalf_of"] == "acct_guide"
    assert kwargs["payment_intent_data"]["application_fee_amount"] == 6000
    booking.refresh_from_db()
    assert booking.stripe_checkout_session_id == "cs_1"


def test_registration_checkout_charges_seats(stripe_guide, make_departure):
    booking = departures.start_registration(
        make_departure(stripe_guide), seats=3, guest_name="G", guest_email="g@example.com", guest_phone="1",
        emergency_contact="x", experience="y", language="de")
    with mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(id="cs_r", url="u")) as create:
        stripe_service.create_registration_checkout(booking)
    item = create.call_args.kwargs["line_items"][0]
    assert (item["quantity"], item["price_data"]["unit_amount"]) == (3, 48000)
    assert create.call_args.kwargs["cancel_url"].endswith("?abgebrochen=1")


def test_new_checkout_expires_previous_session(stripe_guide, make_booking):
    booking = make_booking(stripe_guide, in_days(10), status=S.AWAITING_PAYMENT, price=600,
                           stripe_checkout_session_id="cs_old")
    with mock.patch("stripe.checkout.Session.retrieve", return_value=mock.Mock(id="cs_old", status="open")), \
            mock.patch("stripe.checkout.Session.expire") as expire, \
            mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(id="cs_new", url="u")):
        stripe_service.create_checkout_url(booking)
    expire.assert_called_once()


# --- Refunds ----------------------------------------------------------------------------------


def test_refund_reverses_transfer_and_is_recorded(stripe_guide, make_departure, make_registration):
    booking = make_registration(make_departure(stripe_guide), seats=2, refund_due=Decimal("480"))
    with mock.patch("stripe.Refund.create") as create:
        assert stripe_service.process_refund(booking.pk)
        assert stripe_service.process_refund(booking.pk), "nothing left to do"
    create.assert_called_once()
    kwargs = create.call_args.kwargs
    assert (kwargs["payment_intent"], kwargs["amount"]) == ("pi_test", 48000)
    assert kwargs["reverse_transfer"] and kwargs["refund_application_fee"]
    booking.refresh_from_db()
    assert (booking.refunded_amount, booking.refund_due) == (Decimal("480.00"), 0)


def test_failed_refund_is_retried(stripe_guide, make_departure, make_registration, mailoutbox):
    booking = make_registration(make_departure(stripe_guide), refund_due=Decimal("480"))
    with mock.patch("stripe.Refund.create", side_effect=stripe.APIConnectionError("down")):
        assert not stripe_service.process_refund(booking.pk)
    booking.refresh_from_db()
    assert booking.refund_due == Decimal("480.00")
    assert BookingEvent.objects.filter(booking=booking, note__contains="fehlgeschlagen").exists()
    with mock.patch("stripe.Refund.create"):
        assert stripe_service.retry_refunds() == 1
    booking.refresh_from_db()
    assert booking.refunded_amount == Decimal("480.00")


# --- Webhook ---------------------------------------------------------------------------


def completed_event(booking, event_id="evt_1", amount=None, payment_status="paid"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_1", "payment_status": payment_status, "currency": "chf", "payment_intent": "pi_1",
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


def test_webhook_marks_booking_paid_once(client, guide, make_booking, mailoutbox, django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), status=S.AWAITING_PAYMENT, price=600)
    with django_capture_on_commit_callbacks(execute=True):
        assert post_webhook(client, completed_event(booking)).status_code == 200
        assert post_webhook(client, completed_event(booking)).status_code == 200  # redelivery
    booking.refresh_from_db()
    assert (booking.status, booking.payment_method, booking.stripe_payment_intent_id) == (S.PAID, "stripe", "pi_1")
    assert len(mailoutbox) == 2  # guest receipt + guide, not duplicated
    assert StripeEvent.objects.count() == 1


def test_webhook_confirms_registration(client, stripe_guide, make_departure, mailoutbox, django_capture_on_commit_callbacks):
    booking = departures.start_registration(
        make_departure(stripe_guide), seats=1, guest_name="G", guest_email="g@example.com", guest_phone="1",
        emergency_contact="x", experience="y", language="de")
    with django_capture_on_commit_callbacks(execute=True):
        post_webhook(client, completed_event(booking))
    booking.refresh_from_db()
    assert booking.status == S.PAID and booking.amount_paid == Decimal("480.00")


def test_webhook_expired_session_releases_seats(client, stripe_guide, make_departure):
    booking = departures.start_registration(
        make_departure(stripe_guide), seats=1, guest_name="G", guest_email="g@example.com", guest_phone="1",
        emergency_contact="x", experience="y", language="de")
    Booking.objects.filter(pk=booking.pk).update(stripe_checkout_session_id="cs_x")
    post_webhook(client, {"id": "evt_x", "type": "checkout.session.expired",
                          "data": {"object": {"id": "cs_x", "metadata": {"booking_id": str(booking.pk)}}}})
    booking.refresh_from_db()
    assert booking.status == S.EXPIRED


def test_webhook_ignores_unpaid_session(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.AWAITING_PAYMENT, price=600)
    post_webhook(client, completed_event(booking, payment_status="unpaid"))
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


def test_webhook_amount_mismatch_alerts(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.AWAITING_PAYMENT, price=600)
    post_webhook(client, completed_event(booking, amount=100))
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert BookingEvent.objects.filter(booking=booking, note__startswith="ACHTUNG").exists()


def test_webhook_payment_for_cancelled_booking_alerts(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.CANCELLED, price=600)
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
