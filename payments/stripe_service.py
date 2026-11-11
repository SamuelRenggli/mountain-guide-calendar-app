"""Stripe Connect (Express accounts, destination charges on behalf of the guide).

The guest pays the guide's connected account; the platform keeps PLATFORM_FEE_PERCENT. With destination
charges the platform pays Stripe's fees, and Stripe keeps them on refunds — so refunds cost the guest
and the guide nothing; Tourchef carries the fee (R-146, decision 33).
"""

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import mail_admins
from django.db import transaction
from django.utils.translation import gettext as _

from bookings import departures, services
from bookings.models import Booking, BookingEvent, cents, money

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
            capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True},
                          "twint_payments": {"requested": True}},
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


def _dates(booking) -> str:
    return f"{booking.start_date:%d.%m.%Y}" + (
        f" – {booking.end_date:%d.%m.%Y}" if booking.end_date != booking.start_date else ""
    )


def _expire_old_session(booking):
    """Any older open session is expired so the guest can't pay twice."""
    if not booking.stripe_checkout_session_id:
        return
    try:
        old = stripe.checkout.Session.retrieve(booking.stripe_checkout_session_id, **_key())
        if old.status == "open":
            stripe.checkout.Session.expire(old.id, **_key())
    except stripe.StripeError:
        logger.warning("Could not expire old checkout session for booking %s", booking.pk)


def expire_open_checkout(booking):
    """The bill changed: a checkout opened for the old amount must not stay payable."""
    if is_configured():
        _expire_old_session(booking)


def _checkout(booking: Booking, *, success_url: str, cancel_url: str, expires_minutes: int, quantity=1,
              unit_amount=None) -> str:
    guide = booking.guide
    if not (is_configured() and guide.can_take_card_payments):
        raise PaymentError(_("Online-Zahlung ist für diesen Bergführer nicht verfügbar."))
    _expire_old_session(booking)
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
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=booking.guest_email,
            client_reference_id=str(booking.pk),
            metadata={"booking_id": str(booking.pk)},
            payment_method_types=settings.STRIPE_PAYMENT_METHODS,
            locale=booking.language if booking.language in ("de", "fr", "it", "en") else "auto",
            line_items=[{
                "quantity": quantity,
                "price_data": {
                    "currency": "chf",
                    "unit_amount": unit_amount if unit_amount is not None else amount,
                    "product_data": {
                        "name": booking.title,
                        "description": f"{_dates(booking)} · {guide.display_name}",
                    },
                },
            }],
            payment_intent_data=payment_intent_data,
            success_url=success_url,
            cancel_url=cancel_url,
            expires_at=int(time.time()) + 60 * expires_minutes,
            **_key(),
        )
    except stripe.StripeError as exc:
        logger.exception("Checkout creation failed for booking %s", booking.pk)
        raise PaymentError(_("Die Zahlung konnte nicht gestartet werden. Bitte später erneut versuchen.")) from exc

    Booking.objects.filter(pk=booking.pk).update(stripe_checkout_session_id=session.id)
    return session.url


def create_checkout_url(booking: Booking) -> str:
    """Pay the guide's invoice after a custom tour."""
    if booking.status != Booking.Status.AWAITING_PAYMENT:
        raise PaymentError(_("Diese Buchung kann nicht bezahlt werden."))
    return _checkout(booking, success_url=f"{booking.guest_url}?bezahlt=1", cancel_url=booking.guest_url,
                     expires_minutes=60)


def create_registration_checkout(booking: Booking) -> str:
    """Pay the seats of a registration right away (R-121). The session lives as long as the seats are held."""
    if booking.status != Booking.Status.PAYMENT_PENDING:
        raise PaymentError(_("Diese Anmeldung kann nicht bezahlt werden."))
    per_seat = cents(booking.departure.price_per_person)
    return _checkout(
        booking, success_url=f"{booking.guest_url}?bezahlt=1", cancel_url=f"{booking.departure.public_url}?abgebrochen=1",
        expires_minutes=30, quantity=booking.participants, unit_amount=per_seat,
    )


# --- Refunds ---------------------------------------------------------------


def process_refund(booking_id: int) -> bool:
    """Send what is due back to the guest. Idempotent; the hourly job retries failures."""
    with transaction.atomic():
        booking = Booking.objects.select_for_update().filter(pk=booking_id).first()
        if booking is None or booking.refund_due <= 0:
            return True
        amount = booking.refund_due
        if not (is_configured() and booking.stripe_payment_intent_id):
            logger.warning("Refund for booking %s waits: Stripe not configured or no payment", booking.pk)
            return False
        try:
            stripe.Refund.create(
                payment_intent=booking.stripe_payment_intent_id,
                amount=cents(amount),
                reverse_transfer=True,  # take it back from the guide's account
                refund_application_fee=True,
                metadata={"booking_id": str(booking.pk)},
                idempotency_key=f"refund-{booking.pk}-{cents(booking.refunded_amount)}-{cents(amount)}",
                **_key(),
            )
        except stripe.StripeError as exc:
            logger.exception("Refund failed for booking %s", booking.pk)
            BookingEvent.objects.create(booking=booking, actor=BookingEvent.Actor.STRIPE,
                                        note=f"Rückzahlung CHF {amount} fehlgeschlagen, wird wiederholt: {exc.user_message or ''}")
            mail_admins(f"Rückzahlung fehlgeschlagen Buchung {booking.pk}", f"CHF {amount}\n{exc}")
            return False
        booking.refunded_amount = money(booking.refunded_amount + amount)
        booking.refund_due = Decimal(0)
        booking.save(update_fields=["refunded_amount", "refund_due", "updated_at"])
        BookingEvent.objects.create(booking=booking, actor=BookingEvent.Actor.STRIPE, note=f"CHF {amount} zurückbezahlt")
    return True


def refund_extra_payment(booking_id: int, payment_intent: str, amount: Decimal) -> bool:
    """A second payment arrived for a registration that already holds a payment: send the second one straight back
    through its own payment, so the first stays refundable as before."""
    booking = Booking.objects.filter(pk=booking_id).first()
    if booking is None:
        return False
    if not (is_configured() and payment_intent):
        mail_admins(f"Doppelte Zahlung Buchung {booking.pk}", f"CHF {amount} über {payment_intent!r} manuell zurückzahlen")
        return False
    try:
        stripe.Refund.create(
            payment_intent=payment_intent,
            amount=cents(amount),
            reverse_transfer=True,
            refund_application_fee=True,
            metadata={"booking_id": str(booking.pk)},
            idempotency_key=f"refund-extra-{payment_intent}",
            **_key(),
        )
    except stripe.StripeError as exc:
        logger.exception("Refund of a second payment failed for booking %s", booking.pk)
        mail_admins(f"Rückzahlung doppelte Zahlung fehlgeschlagen Buchung {booking.pk}",
                    f"CHF {amount} über {payment_intent}\n{exc}")
        return False
    BookingEvent.objects.create(booking=booking, actor=BookingEvent.Actor.STRIPE,
                                note=f"Doppelte Zahlung CHF {amount} zurückbezahlt ({payment_intent})")
    return True


def retry_refunds() -> int:
    return sum(1 for pk in Booking.objects.filter(refund_due__gt=0).values_list("pk", flat=True) if process_refund(pk))


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
        if event["type"].startswith("checkout.session.") and event.get("account"):
            # Our checkouts live on the platform account (destination charges). A checkout on a connected account
            # was not made by Tourchef and must never mark a booking paid.
            logger.warning("Ignoring %s from connected account %s", event["type"], event.get("account"))
            return
        if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if obj.get("payment_status") == "paid":
                _session_paid(obj)
        elif event["type"] in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            _session_failed(obj)
        elif event["type"] == "account.updated":
            get_user_model().objects.filter(stripe_account_id=obj["id"]).update(
                stripe_charges_enabled=bool(obj.get("charges_enabled"))
            )


def _booking_for(session):
    booking_id = (session.get("metadata") or {}).get("booking_id")
    return Booking.objects.select_related("guide", "tour", "departure").filter(pk=booking_id).first(), booking_id


def _session_paid(session) -> None:
    booking, booking_id = _booking_for(session)
    if booking is None:
        logger.error("Paid checkout %s for unknown booking %r", session["id"], booking_id)
        return
    if session.get("currency") != "chf" or session.get("amount_total") != booking.amount_cents:
        _alert(booking, f"Betrag stimmt nicht: {session.get('amount_total')} {session.get('currency')}", session)
        return
    payment_intent = session.get("payment_intent") or ""
    if booking.is_registration:
        departures.registration_paid(booking, session_id=session["id"], payment_intent=payment_intent,
                                     amount_cents=session["amount_total"])
        return
    try:
        services.mark_paid_by_stripe(booking, session["id"], payment_intent)
    except services.InvalidTransition:
        # Paid although no longer awaiting payment (e.g. cancelled meanwhile) — needs a manual refund.
        _alert(booking, f"Zahlung eingegangen, aber Status ist «{booking.get_status_display()}»", session)


def _session_failed(session) -> None:
    booking, _booking_id = _booking_for(session)
    if booking is not None and booking.is_registration and booking.stripe_checkout_session_id == session["id"]:
        departures.payment_failed(booking)


def _alert(booking, problem, session):
    BookingEvent.objects.create(
        booking=booking, actor=BookingEvent.Actor.STRIPE, note=f"ACHTUNG: {problem} (Checkout {session['id']})"
    )
    mail_admins(f"Zahlungsproblem Buchung {booking.pk}", f"{problem}\nCheckout: {session['id']}\nBuchung: {booking.pk}")
    logger.error("Payment problem for booking %s: %s", booking.pk, problem)
