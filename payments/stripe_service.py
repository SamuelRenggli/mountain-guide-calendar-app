"""Stripe Connect (Express accounts, destination charges on behalf of the guide).

The guest pays the guide's connected account; the platform keeps PLATFORM_FEE_PERCENT.
"""

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import mail_admins
from django.db import transaction

from bookings import services
from bookings.models import Booking, BookingEvent

from .models import StripeEvent

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """User-facing error message."""


def is_configured() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


def _key():
    return {"api_key": settings.STRIPE_SECRET_KEY}


# --- Connect onboarding ----------------------------------------------------


def onboarding_url(guide) -> str:
    if not guide.stripe_account_id:
        account = stripe.Account.create(
            type="express",
            country="CH",
            email=guide.email,
            capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
            business_profile={"product_description": "Bergführer-Touren"},
            metadata={"guide_id": str(guide.pk)},
            **_key(),
        )
        guide.stripe_account_id = account.id
        guide.save(update_fields=["stripe_account_id"])
    link = stripe.AccountLink.create(
        account=guide.stripe_account_id,
        refresh_url=f"{settings.SITE_URL}/zahlungen/stripe/verbinden/",
        return_url=f"{settings.SITE_URL}/zahlungen/stripe/zurueck/",
        type="account_onboarding",
        **_key(),
    )
    return link.url


def refresh_account(guide):
    if not guide.stripe_account_id:
        return
    account = stripe.Account.retrieve(guide.stripe_account_id, **_key())
    guide.stripe_charges_enabled = bool(account.charges_enabled)
    guide.save(update_fields=["stripe_charges_enabled"])


# --- Checkout --------------------------------------------------------------


def platform_fee_cents(amount_cents: int) -> int:
    fee = Decimal(amount_cents) * Decimal(settings.PLATFORM_FEE_PERCENT) / 100
    return int(fee.to_integral_value(rounding=ROUND_HALF_UP))


def create_checkout_url(booking: Booking) -> str:
    """Checkout for a confirmed booking. Any older open session is expired so the guest can't pay twice."""
    if booking.status != Booking.Status.AWAITING_PAYMENT:
        raise PaymentError("Diese Buchung kann nicht bezahlt werden.")
    guide = booking.guide
    if not (is_configured() and guide.can_take_card_payments):
        raise PaymentError("Online-Zahlung ist für diesen Bergführer nicht verfügbar.")

    if booking.stripe_checkout_session_id:
        try:
            old = stripe.checkout.Session.retrieve(booking.stripe_checkout_session_id, **_key())
            if old.status == "open":
                stripe.checkout.Session.expire(old.id, **_key())
        except stripe.StripeError:
            logger.warning("Could not expire old checkout session for booking %s", booking.pk)

    amount = booking.amount_cents
    fee = platform_fee_cents(amount)
    payment_intent_data = {
        "on_behalf_of": guide.stripe_account_id,
        "transfer_data": {"destination": guide.stripe_account_id},
        "metadata": {"booking_id": str(booking.pk)},
        "description": f"{booking.title} – {booking.start_date:%d.%m.%Y}",
    }
    if fee:
        payment_intent_data["application_fee_amount"] = fee
    dates = f"{booking.start_date:%d.%m.%Y}" + (
        f" – {booking.end_date:%d.%m.%Y}" if booking.end_date != booking.start_date else ""
    )
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=booking.guest_email,
            client_reference_id=str(booking.pk),
            metadata={"booking_id": str(booking.pk)},
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "chf",
                    "unit_amount": amount,
                    "product_data": {
                        "name": booking.title,
                        "description": f"{dates} · {booking.participants} Pers. · mit {guide.display_name}",
                    },
                },
            }],
            payment_intent_data=payment_intent_data,
            success_url=f"{booking.guest_url}?bezahlt=1",
            cancel_url=booking.guest_url,
            expires_at=int(time.time()) + 60 * 60,
            **_key(),
        )
    except stripe.StripeError as exc:
        logger.exception("Checkout creation failed for booking %s", booking.pk)
        raise PaymentError("Die Zahlung konnte nicht gestartet werden. Bitte später erneut versuchen.") from exc

    Booking.objects.filter(pk=booking.pk).update(stripe_checkout_session_id=session.id)
    return session.url


# --- Webhooks --------------------------------------------------------------


def construct_event(payload: bytes, signature: str):
    """Verify against the platform secret, then the Connect secret. Raises ValueError if neither matches."""
    for secret in (settings.STRIPE_WEBHOOK_SECRET, settings.STRIPE_CONNECT_WEBHOOK_SECRET):
        if not secret:
            continue
        try:
            return stripe.Webhook.construct_event(payload, signature, secret)
        except stripe.SignatureVerificationError:
            continue
    raise ValueError("invalid signature")


def handle_event(event) -> None:
    """Process an event once. Runs in one transaction: if handling fails, Stripe retries the delivery."""
    with transaction.atomic():
        _, created = StripeEvent.objects.get_or_create(event_id=event["id"], defaults={"type": event["type"]})
        if not created:
            return
        obj = event["data"]["object"]
        if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if obj.get("payment_status") == "paid":
                _session_paid(obj)
        elif event["type"] == "account.updated":
            get_user_model().objects.filter(stripe_account_id=obj["id"]).update(
                stripe_charges_enabled=bool(obj.get("charges_enabled"))
            )


def _session_paid(session) -> None:
    booking_id = (session.get("metadata") or {}).get("booking_id")
    booking = Booking.objects.select_related("guide", "tour").filter(pk=booking_id).first()
    if booking is None:
        logger.error("Paid checkout %s for unknown booking %r", session["id"], booking_id)
        return
    if session.get("currency") != "chf" or session.get("amount_total") != booking.amount_cents:
        _alert(booking, f"Betrag stimmt nicht: {session.get('amount_total')} {session.get('currency')}", session)
        return
    try:
        services.mark_paid_by_stripe(booking, session["id"])
    except services.InvalidTransition:
        # Paid although no longer awaiting payment (e.g. cancelled meanwhile) — needs a manual refund.
        _alert(booking, f"Zahlung eingegangen, aber Status ist «{booking.get_status_display()}»", session)


def _alert(booking, problem, session):
    BookingEvent.objects.create(
        booking=booking, actor=BookingEvent.Actor.STRIPE, note=f"ACHTUNG: {problem} (Checkout {session['id']})"
    )
    mail_admins(f"Zahlungsproblem Buchung {booking.pk}", f"{problem}\nCheckout: {session['id']}\nBuchung: {booking.pk}")
    logger.error("Payment problem for booking %s: %s", booking.pk, problem)
