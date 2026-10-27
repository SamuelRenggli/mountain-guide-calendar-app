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


MAX_WINDOW_DAYS = 14
MAX_CUSTOM_PARTICIPANTS = 50


def create_request(
    *, tour: Tour | None, start_date: date, participants: int, guest_name: str, guest_email: str,
    guest_phone: str = "", guest_message: str = "", language: str = "de", end_date: date | None = None, guide=None,
) -> Booking:
    """A request for a tour (or the guest's own idea, tour=None) in the days start_date..end_date.

    If the days fit the tour exactly, they are held right away. Otherwise they are a time window:
    nothing is held until the guide offers exact dates.
    """
    guide = tour.guide if tour else guide
    end_date = end_date or (tour.end_date_for(start_date) if tour else start_date)
    window_days = (end_date - start_date).days + 1
    if not 1 <= window_days <= MAX_WINDOW_DAYS:
        raise BookingError(f"Das Zeitfenster darf höchstens {MAX_WINDOW_DAYS} Tage lang sein.")
    if tour:
        if not tour.is_active:
            raise BookingError("Diese Tour ist nicht mehr buchbar.")
        if not 1 <= participants <= tour.max_participants:
            raise BookingError(f"Maximal {tour.max_participants} Teilnehmende.")
        if window_days < tour.duration_days:
            raise BookingError(f"Das Zeitfenster ist kürzer als die Tour ({tour.duration_days} Tage).")
    elif not 1 <= participants <= MAX_CUSTOM_PARTICIPANTS:
        raise BookingError(f"Maximal {MAX_CUSTOM_PARTICIPANTS} Teilnehmende.")
    is_window = not tour or window_days != tour.duration_days

    with transaction.atomic():
        if is_window:
            earliest, latest = availability.booking_window(guide)
            states = availability.day_states(guide, start_date, end_date)
            if start_date < earliest or end_date > latest or availability.FREE not in (states[start_date], states[end_date]):
                raise DatesUnavailable()
        elif not availability.can_start_on(tour, start_date):
            raise DatesUnavailable()
        booking = Booking(
            guide=guide,
            tour=tour,
            start_date=start_date,
            end_date=end_date,
            is_window=is_window,
            participants=participants,
            price=tour.price_for(participants) if tour else 0,
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
        locked = Booking.objects.select_for_update(of=("self",)).select_related("guide", "tour").get(pk=booking.pk)
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
    if booking.is_window:
        raise BookingError("Die Anfrage hat ein Zeitfenster: Bitte schlage genaue Daten und einen Preis vor.")
    booking = _transition(
        booking, {S.REQUESTED}, S.AWAITING_PAYMENT, Actor.GUIDE, message,
        guide_message=message, deadline=_payment_deadline(booking),
    )
    transaction.on_commit(lambda: emails.confirmed(booking))
    return booking


def counter_offer(booking: Booking, *, start_date: date, price, message: str, end_date: date | None = None) -> Booking:
    """Offer exact dates and a price. The end follows from the tour; for the guest's own idea the guide sets it."""
    if start_date < timezone.localdate():
        raise BookingError("Das Datum liegt in der Vergangenheit.")
    end_date = booking.tour.end_date_for(start_date) if booking.tour else (end_date or start_date)
    if end_date < start_date:
        raise BookingError("Das Ende muss nach dem Beginn liegen.")
    booking = _transition(
        booking, {S.REQUESTED, S.OFFERED}, S.OFFERED, Actor.GUIDE,
        f"Angebot: {start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}, CHF {price}. {message}".strip(),
        start_date=start_date, end_date=end_date, is_window=False, price=price,
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
