"""Booking state machine. All status changes go through here.

    requested ──confirm──────────────▶ awaiting_payment ──pay──▶ paid
        │  └──counter_offer──▶ offered ──guest accepts──┘
        │                        └──guest declines──▶ declined
        └── reject / cancel / expire (any open state) ──▶ rejected / cancelled / expired
"""

from datetime import date, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import availability, emails
from .models import Booking, BookingEvent, Tour

S = Booking.Status
Actor = BookingEvent.Actor


class BookingError(Exception):
    """A user-facing error; the message is safe to show."""


class DatesUnavailable(BookingError):
    def __init__(self, message="Diese Daten sind leider nicht mehr verfügbar."):
        super().__init__(message)


class InvalidTransition(BookingError):
    def __init__(self, message="Diese Aktion ist im aktuellen Status nicht möglich."):
        super().__init__(message)


def _deadline(days: int):
    return timezone.now() + timedelta(days=days)


def _payment_deadline(booking: Booking):
    # Manual payment (bank transfer) can take a while, so only online payment gets a deadline.
    return _deadline(settings.GUEST_RESPONSE_DAYS) if booking.guide.can_take_card_payments else None


def _save_holding_dates(booking: Booking, **save_kwargs):
    """Save a booking whose dates must not overlap another active booking (DB exclusion constraint)."""
    try:
        with transaction.atomic():
            booking.save(**save_kwargs)
    except IntegrityError as exc:
        if "booking_no_overlap" in str(exc):
            raise DatesUnavailable() from exc
        raise


def create_request(
    *, tour: Tour, start_date: date, participants: int, guest_name: str, guest_email: str,
    guest_phone: str = "", guest_message: str = "", language: str = "de",
) -> Booking:
    if not tour.is_active:
        raise BookingError("Diese Tour ist nicht mehr buchbar.")
    if not 1 <= participants <= tour.max_participants:
        raise BookingError(f"Maximal {tour.max_participants} Teilnehmende.")
    with transaction.atomic():
        if not availability.can_start_on(tour, start_date):
            raise DatesUnavailable()
        booking = Booking(
            guide=tour.guide,
            tour=tour,
            start_date=start_date,
            end_date=tour.end_date_for(start_date),
            participants=participants,
            price=tour.price_for(participants),
            guest_name=guest_name,
            guest_email=guest_email,
            guest_phone=guest_phone,
            guest_message=guest_message,
            language=language,
            deadline=_deadline(settings.REQUEST_RESPONSE_DAYS),
        )
        _save_holding_dates(booking)
        _log(booking, Actor.GUEST, "", S.REQUESTED)
        transaction.on_commit(lambda: emails.request_created(booking))
    return booking


def _transition(booking: Booking, allowed_from, to_status, actor, note="", **changes) -> Booking:
    """Lock the row, check the current status, apply changes, log, return the fresh booking."""
    with transaction.atomic():
        locked = Booking.objects.select_for_update().select_related("guide", "tour").get(pk=booking.pk)
        if locked.status not in allowed_from:
            raise InvalidTransition()
        from_status = locked.status
        for field, value in changes.items():
            setattr(locked, field, value)
        locked.status = to_status
        _save_holding_dates(locked)
        _log(locked, actor, from_status, to_status, note)
    return locked


def _log(booking, actor, from_status, to_status, note=""):
    BookingEvent.objects.create(booking=booking, actor=actor, from_status=from_status, to_status=to_status, note=note)


# --- Guide actions ---------------------------------------------------------


def confirm(booking: Booking, message: str = "") -> Booking:
    booking = _transition(
        booking, {S.REQUESTED}, S.AWAITING_PAYMENT, Actor.GUIDE, message,
        guide_message=message, deadline=_payment_deadline(booking),
    )
    transaction.on_commit(lambda: emails.confirmed(booking))
    return booking


def counter_offer(booking: Booking, *, start_date: date, price, message: str) -> Booking:
    if start_date < timezone.localdate():
        raise BookingError("Das Datum liegt in der Vergangenheit.")
    booking = _transition(
        booking, {S.REQUESTED, S.OFFERED}, S.OFFERED, Actor.GUIDE,
        f"Gegenangebot: {start_date:%d.%m.%Y}, CHF {price}. {message}".strip(),
        start_date=start_date, end_date=booking.tour.end_date_for(start_date), price=price,
        guide_message=message, deadline=_deadline(settings.GUEST_RESPONSE_DAYS),
    )
    transaction.on_commit(lambda: emails.offer_sent(booking))
    return booking


def reject(booking: Booking, message: str = "") -> Booking:
    booking = _transition(
        booking, {S.REQUESTED, S.OFFERED, S.AWAITING_PAYMENT}, S.REJECTED, Actor.GUIDE, message,
        guide_message=message, deadline=None,
    )
    transaction.on_commit(lambda: emails.rejected(booking))
    return booking


def guide_cancel(booking: Booking, message: str = "") -> Booking:
    """Cancel a paid booking. Refunds are made in the Stripe dashboard."""
    booking = _transition(booking, {S.PAID}, S.CANCELLED, Actor.GUIDE, message, guide_message=message, deadline=None)
    transaction.on_commit(lambda: emails.cancelled_by_guide(booking))
    return booking


def mark_paid_manually(booking: Booking) -> Booking:
    booking = _transition(
        booking, {S.AWAITING_PAYMENT}, S.PAID, Actor.GUIDE, "Zahlung manuell erfasst",
        payment_method=Booking.PaymentMethod.MANUAL, paid_at=timezone.now(), deadline=None,
    )
    transaction.on_commit(lambda: emails.paid(booking))
    return booking


# --- Guest actions ---------------------------------------------------------


def accept_offer(booking: Booking) -> Booking:
    booking = _transition(
        booking, {S.OFFERED}, S.AWAITING_PAYMENT, Actor.GUEST, deadline=_payment_deadline(booking)
    )
    transaction.on_commit(lambda: emails.offer_accepted(booking))
    return booking


def decline_offer(booking: Booking) -> Booking:
    booking = _transition(booking, {S.OFFERED}, S.DECLINED, Actor.GUEST, deadline=None)
    transaction.on_commit(lambda: emails.offer_declined(booking))
    return booking


def guest_cancel(booking: Booking) -> Booking:
    booking = _transition(booking, {S.REQUESTED, S.OFFERED, S.AWAITING_PAYMENT}, S.CANCELLED, Actor.GUEST, deadline=None)
    transaction.on_commit(lambda: emails.cancelled_by_guest(booking))
    return booking


# --- System ----------------------------------------------------------------


def mark_paid_by_stripe(booking: Booking, session_id: str) -> Booking:
    booking = _transition(
        booking, {S.AWAITING_PAYMENT}, S.PAID, Actor.STRIPE, f"Stripe Checkout {session_id}",
        payment_method=Booking.PaymentMethod.STRIPE, paid_at=timezone.now(), deadline=None,
        stripe_checkout_session_id=session_id,
    )
    transaction.on_commit(lambda: emails.paid(booking))
    return booking


def expire_overdue() -> int:
    overdue = Booking.objects.filter(
        status__in=[S.REQUESTED, S.OFFERED, S.AWAITING_PAYMENT], deadline__lt=timezone.now()
    )
    count = 0
    for booking in overdue:
        try:
            expired = _transition(
                booking, {S.REQUESTED, S.OFFERED, S.AWAITING_PAYMENT}, S.EXPIRED, Actor.SYSTEM, "Frist abgelaufen",
                deadline=None,
            )
        except InvalidTransition:
            continue  # changed concurrently
        transaction.on_commit(lambda b=expired: emails.expired(b))
        count += 1
    return count
