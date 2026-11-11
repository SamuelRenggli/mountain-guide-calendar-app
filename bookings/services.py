"""Booking state machine for custom tour requests. All status changes go through here.
Scheduled dates (Termine) and their registrations live in departures.py and reuse the helpers below.

    requested ──accept (price, maybe other days)──▶ offered ──guest accepts──▶ confirmed
        │                                              │  └──guest declines──▶ declined          │
        │                                              └──guide adjusts──▶ offered                │
        │      confirmed ──invoice (after the tour)──▶ awaiting_payment ──pay──▶ paid  ◀─────────┘
        │                                                └─invoice again (correction)
        │                                    confirmed ──propose_new_date (weather)──▶ reschedule
        │                                                   ├─ guest accepts ──▶ confirmed (new dates)
        │                                                   └─ declines / no reply ──▶ cancelled (or billed costs)
        └── reject / cancel / expire (before the tour) ──▶ rejected / cancelled / expired
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import availability, emails, messaging
from .models import Booking, BookingEvent, InvoiceLine, Notification, money

S = Booking.Status
Actor = BookingEvent.Actor

MAX_CUSTOM_PARTICIPANTS = 50


class BookingError(Exception):
    """A user-facing error; the message is safe to show."""


class DatesUnavailable(BookingError):
    def __init__(self, message=None):
        super().__init__(message or _("Diese Daten sind leider nicht mehr verfügbar."))


class InvalidTransition(BookingError):
    def __init__(self, message=None):
        super().__init__(message or _("Diese Aktion ist im aktuellen Status nicht möglich."))


def _deadline(days: int):
    return timezone.now() + timedelta(days=days)


def _save_holding_dates(booking: Booking, **save_kwargs):
    """Save a booking whose dates must not overlap another active booking (DB exclusion constraint)."""
    try:
        with transaction.atomic():
            booking.save(**save_kwargs)
    except IntegrityError as exc:
        if "booking_no_overlap" in str(exc):
            raise DatesUnavailable() from exc
        raise


def freeze_terms(booking: Booking, guide) -> None:
    """The guest accepted the guide's terms (R-002): keep the cancellation tiers valid at that moment."""
    booking.terms_accepted_at = timezone.now()
    booking.cancel_free_days = guide.cancel_free_days
    booking.cancel_partial_days = guide.cancel_partial_days
    booking.cancel_partial_percent = guide.cancel_partial_percent


def cancellation_percent(booking: Booking, today: date | None = None) -> int:
    """Share of the price the guest owes when cancelling now (R-003): template 14 days 0 %, 7 days 50 %, then 100 %."""
    today = today or timezone.localdate()
    guide = booking.guide
    free = booking.cancel_free_days if booking.cancel_free_days is not None else guide.cancel_free_days
    partial = booking.cancel_partial_days if booking.cancel_partial_days is not None else guide.cancel_partial_days
    percent = booking.cancel_partial_percent if booking.cancel_partial_percent is not None else guide.cancel_partial_percent
    days_before = (booking.start_date - today).days
    if days_before >= free:
        return 0
    if days_before >= partial:
        return percent
    return 100


def cancellation_fee(booking: Booking, today: date | None = None) -> Decimal:
    return money(Decimal(booking.price) * cancellation_percent(booking, today) / 100)


def create_request(
    *, guide, start_date: date, end_date: date, participants: int, guest_name: str, guest_email: str,
    guest_phone: str = "", guest_message: str = "", language: str = "de",
) -> Booking:
    """A request for the guest's own tour on fixed dates (R-020). The days are held right away."""
    if end_date < start_date:
        raise BookingError(_("Das Ende muss nach dem Beginn liegen."))
    if (end_date - start_date).days + 1 > settings.MAX_REQUEST_DAYS:
        raise BookingError(_("Eine Anfrage kann höchstens %(days)s Tage lang sein.") % {"days": settings.MAX_REQUEST_DAYS})
    if not 1 <= participants <= MAX_CUSTOM_PARTICIPANTS:
        raise BookingError(_("Maximal %(n)s Teilnehmende.") % {"n": MAX_CUSTOM_PARTICIPANTS})

    with transaction.atomic():
        if not availability.range_bookable(guide, start_date, end_date):
            raise DatesUnavailable()
        booking = Booking(
            guide=guide, start_date=start_date, end_date=end_date, participants=participants, price=0,
            guest_name=guest_name, guest_email=guest_email, guest_phone=guest_phone, guest_message=guest_message,
            language=language, deadline=_deadline(settings.REQUEST_RESPONSE_DAYS),
        )
        freeze_terms(booking, guide)
        _save_holding_dates(booking)
        log(booking, Actor.GUEST, "", S.REQUESTED)
        messaging.notify(guide, Notification.Kind.REQUEST, booking)
        transaction.on_commit(lambda: emails.request_created(booking))
    return booking


def transition(booking: Booking, allowed_from, to_status, actor, note="", **changes) -> Booking:
    """Lock the row, check the current status, apply changes, log, return the fresh booking."""
    with transaction.atomic():
        locked = (Booking.objects.select_for_update(of=("self",))
                  .select_related("guide", "tour", "departure").get(pk=booking.pk))
        if locked.status not in allowed_from:
            raise InvalidTransition()
        from_status = locked.status
        for field, value in changes.items():
            setattr(locked, field, value)
        locked.status = to_status
        _save_holding_dates(locked)
        log(locked, actor, from_status, to_status, note)
    return locked


def log(booking, actor, from_status, to_status, note=""):
    BookingEvent.objects.create(booking=booking, actor=actor, from_status=from_status, to_status=to_status, note=note)


def breakdown(guide, *, days, participants, extra_guides=0, aspirants=0, guide_rate=None, aspirant_rate=None) -> list:
    """Price split by person and day rate (R-022). The rate for the group size comes from the guide's tiers (R-200)."""
    guide_rate = Decimal(guide_rate if guide_rate is not None else guide.rate_for(participants))
    aspirant_rate = Decimal(aspirant_rate if aspirant_rate is not None else guide.aspirant_day_rate)
    rows = [("guide", 1 + extra_guides, guide_rate)]
    if aspirants:
        rows.append(("aspirant", aspirants, aspirant_rate))
    return [{"role": role, "persons": persons, "days": float(days), "rate": str(money(rate)),
             "amount": str(money(Decimal(persons) * Decimal(str(days)) * rate))} for role, persons, rate in rows]


def _price_note(price, rows, estimated_expenses) -> str:
    parts = [f"CHF {money(price)}"]
    for row in rows:
        parts.append(f"{row['persons']}× {row['role']} × {row['days']:g} d × CHF {row['rate']}")
    if estimated_expenses:
        parts.append(f"Spesen ca. CHF {money(estimated_expenses)}")
    return ", ".join(parts)


# --- Guide actions ---------------------------------------------------------


def check_days_free(booking: Booking, start_date: date, end_date: date) -> None:
    """The guide picks days for a request: they must not collide with anything else in their calendar
    (the request's own days don't count). Weekdays the guide doesn't offer to guests are fine here."""
    blocking = {availability.BUSY: _("Termin aus deinem Kalender"), availability.BLOCKED: _("gesperrt"),
                availability.REQUESTED: _("andere Anfrage"), availability.DEPARTURE: _("ausgeschriebene Tour"),
                availability.EXTERNAL: _("Tour für einen Anbieter"), availability.BOOKED: _("gebuchte Tour")}
    states = availability.day_states(booking.guide, start_date, end_date, exclude_booking=booking)
    taken = [(day, blocking[state]) for day, state in states.items() if state in blocking]
    if taken:
        raise DatesUnavailable(_("An diesen Tagen bist du schon belegt: %(days)s") % {
            "days": ", ".join(f"{day:%d.%m.%Y} ({why})" for day, why in taken[:5])})


def accept_request(booking: Booking, *, start_date: date, end_date: date, price, message: str = "",
                   estimated_expenses=None, decision_date=None, answer_deadline: date | None = None) -> Booking:
    """The guide takes the request: price, estimated expenses and possibly other or more days. The days are held
    provisionally; the booking is confirmed once the guest agrees by the answer deadline the guide sets on each offer
    (questionnaire Q3; default GUEST_RESPONSE_DAYS). Can be repeated to adjust the proposal while the guest hasn't
    answered."""
    today = timezone.localdate()
    if start_date < today:
        raise BookingError(_("Das Datum liegt in der Vergangenheit."))
    if end_date < start_date:
        raise BookingError(_("Das Ende muss nach dem Beginn liegen."))
    if decision_date and decision_date > start_date:
        raise BookingError(_("Die Entscheid-Frist muss vor dem Tourenbeginn liegen."))
    if answer_deadline and not today <= answer_deadline <= start_date:
        raise BookingError(_("Die Antwortfrist muss zwischen heute und dem ersten Tourentag liegen."))
    check_days_free(booking, start_date, end_date)
    note = f"{start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}, {_price_note(price, [], estimated_expenses)}"
    if answer_deadline:
        # The whole chosen day counts.
        deadline = timezone.make_aware(datetime.combine(answer_deadline, time(23, 59)))
    else:
        deadline = _deadline(settings.GUEST_RESPONSE_DAYS)
    booking = transition(
        booking, {S.REQUESTED, S.OFFERED}, S.OFFERED, Actor.GUIDE, f"{note}. {message}".strip(),
        start_date=start_date, end_date=end_date, is_window=False, price=price, price_breakdown=[],
        estimated_expenses=estimated_expenses, guide_message=message, deadline=deadline, decision_date=decision_date,
    )
    transaction.on_commit(lambda: emails.offer_sent(booking))
    return booking


def reject(booking: Booking, message: str = "") -> Booking:
    booking = transition(
        booking, {S.REQUESTED, S.OFFERED}, S.REJECTED, Actor.GUIDE, message, guide_message=message, deadline=None,
    )
    transaction.on_commit(lambda: emails.rejected(booking))
    return booking


def guide_cancel(booking: Booking, message: str = "") -> Booking:
    """Call off a confirmed tour without a replacement date (free for the guest)."""
    if booking.is_registration:
        raise InvalidTransition()
    booking = transition(booking, {S.CONFIRMED}, S.CANCELLED, Actor.GUIDE, message, guide_message=message, deadline=None)
    transaction.on_commit(lambda: emails.cancelled_by_guide(booking))
    return booking


def change_booking(booking: Booking, *, start_date: date, end_date: date, participants: int, price,
                   message: str = "") -> Booking:
    """The guide changes a confirmed tour (R-024): the change is logged and the guest gets an email listing it."""
    if booking.is_registration:
        raise InvalidTransition()
    if end_date < start_date:
        raise BookingError(_("Das Ende muss nach dem Beginn liegen."))
    old = booking
    changes = []
    if (start_date, end_date) != (old.start_date, old.end_date):
        if start_date < timezone.localdate():
            raise BookingError(_("Das Datum liegt in der Vergangenheit."))
        check_days_free(booking, start_date, end_date)
        changes.append(("dates", f"{old.start_date:%d.%m.%Y}–{old.end_date:%d.%m.%Y}", f"{start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}"))
    if participants != old.participants:
        changes.append(("participants", str(old.participants), str(participants)))
    if money(price) != money(old.price):
        changes.append(("price", f"CHF {money(old.price)}", f"CHF {money(price)}"))
    if not changes:
        raise BookingError(_("Es wurde nichts geändert."))
    note = "; ".join(f"{field}: {before} → {after}" for field, before, after in changes)
    booking = transition(
        booking, {S.CONFIRMED}, S.CONFIRMED, Actor.GUIDE, f"{note}. {message}".strip(),
        start_date=start_date, end_date=end_date, participants=participants, price=price, guide_message=message,
    )
    transaction.on_commit(lambda: emails.booking_changed(booking, changes))
    return booking


def propose_new_date(booking: Booking, *, start_date: date, end_date: date, message: str = "",
                     incurred_costs=Decimal(0), incurred_costs_note: str = "") -> Booking:
    """Weather or conditions: the guide calls off the date and proposes another one (R-025).
    The new days are held; if the guest says no or doesn't answer, the booking is cancelled for free,
    apart from costs the guide already had and lists here (R-026)."""
    if booking.is_registration:
        raise InvalidTransition()
    if start_date < timezone.localdate() or end_date < start_date:
        raise BookingError(_("Bitte gib ein gültiges Ersatzdatum in der Zukunft an."))
    check_days_free(booking, start_date, end_date)
    note = f"{booking.start_date:%d.%m.%Y} →{start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}"
    if incurred_costs:
        note += f", angefallene Kosten CHF {money(incurred_costs)} ({incurred_costs_note})"
    booking = transition(
        booking, {S.CONFIRMED}, S.RESCHEDULE_PROPOSED, Actor.GUIDE, f"{note}. {message}".strip(),
        start_date=start_date, end_date=end_date, guide_message=message, incurred_costs=money(incurred_costs),
        incurred_costs_note=incurred_costs_note, deadline=_deadline(settings.GUEST_RESPONSE_DAYS),
    )
    transaction.on_commit(lambda: emails.new_date_proposed(booking))
    return booking


def invoice(booking: Booking, lines: list[InvoiceLine], message: str = "") -> Booking:
    """Bill the guest after the tour (lines are unsaved). Can be repeated to correct the bill until it is paid."""
    if booking.is_registration:
        raise InvalidTransition()
    if booking.end_date > timezone.localdate():
        raise BookingError(_("Abgerechnet wird nach der Tour, ab dem letzten Tourentag."))
    if not lines:
        raise BookingError(_("Die Rechnung hat keine Posten."))
    total = sum((line.amount for line in lines), Decimal("0.00"))
    if total < 0:
        raise BookingError(_("Die Rechnung darf nicht unter CHF 0 liegen."))
    if booking.status == S.AWAITING_PAYMENT:
        # A correction: the checkout opened for the old amount must not stay payable.
        from payments import stripe_service

        stripe_service.expire_open_checkout(booking)
    if total == 0:
        # Nothing to pay (a free tour, later e.g. a voucher): the tour is settled at once (questionnaire Q6).
        with transaction.atomic():
            booking = transition(
                booking, {S.CONFIRMED, S.AWAITING_PAYMENT}, S.PAID, Actor.GUIDE,
                f"Rechnung über CHF 0 – erledigt. {message}".strip(),
                price=total, invoiced_at=timezone.now(), guide_message=message, deadline=None,
                payment_method=Booking.PaymentMethod.MANUAL, paid_at=timezone.now(), amount_paid=total,
            )
            _replace_lines(booking, lines)
        return booking
    with transaction.atomic():
        booking = transition(
            booking, {S.CONFIRMED, S.AWAITING_PAYMENT}, S.AWAITING_PAYMENT, Actor.GUIDE,
            f"Rechnung über CHF {total}. {message}".strip(),
            price=total, invoiced_at=timezone.now(), guide_message=message, deadline=None,
        )
        _replace_lines(booking, lines)
    transaction.on_commit(lambda: emails.invoice_sent(booking))
    return booking


def _replace_lines(booking, lines):
    booking.invoice_lines.all().delete()
    for i, line in enumerate(lines):
        line.booking, line.position = booking, i
    InvoiceLine.objects.bulk_create(lines)


def _bill_costs(booking: Booking, actor, lines: list[InvoiceLine], note: str) -> Booking:
    """Cancelled, but the guest owes something (cancellation fee or costs already incurred). The tour won't take
    place, so its days are free again right away (guide answer, questionnaire Q1)."""
    total = sum((line.amount for line in lines), Decimal("0.00"))
    with transaction.atomic():
        booking = transition(
            booking, {S.CONFIRMED, S.RESCHEDULE_PROPOSED}, S.AWAITING_PAYMENT, actor, f"{note}: CHF {total}",
            price=total, invoiced_at=timezone.now(), deadline=None, days_released=True,
        )
        _replace_lines(booking, lines)
    transaction.on_commit(lambda: emails.invoice_sent(booking))
    return booking


def mark_paid_manually(booking: Booking) -> Booking:
    booking = transition(
        booking, {S.AWAITING_PAYMENT}, S.PAID, Actor.GUIDE, "Zahlung manuell erfasst",
        payment_method=Booking.PaymentMethod.MANUAL, paid_at=timezone.now(), amount_paid=booking.price, deadline=None,
    )
    transaction.on_commit(lambda: emails.paid(booking))
    return booking


# --- Guest actions ---------------------------------------------------------


def check_answer_in_time(booking: Booking) -> None:
    """The guest answers an offer or a replacement date: only before the deadline and before the tour starts."""
    if booking.deadline and booking.deadline < timezone.now():
        raise BookingError(_("Die Frist für deine Antwort ist abgelaufen."))
    if booking.start_date < timezone.localdate():
        raise BookingError(_("Das Datum liegt in der Vergangenheit."))


def accept_offer(booking: Booking) -> Booking:
    check_answer_in_time(booking)
    booking = transition(booking, {S.OFFERED}, S.CONFIRMED, Actor.GUEST, deadline=None)
    transaction.on_commit(lambda: emails.offer_accepted(booking))
    return booking


def decline_offer(booking: Booking) -> Booking:
    booking = transition(booking, {S.OFFERED}, S.DECLINED, Actor.GUEST, deadline=None)
    transaction.on_commit(lambda: emails.offer_declined(booking))
    return booking


def accept_new_date(booking: Booking) -> Booking:
    if booking.is_registration:
        from . import departures

        return departures.accept_new_date(booking)
    check_answer_in_time(booking)
    booking = transition(booking, {S.RESCHEDULE_PROPOSED}, S.CONFIRMED, Actor.GUEST, deadline=None,
                         incurred_costs=0, incurred_costs_note="")
    transaction.on_commit(lambda: emails.new_date_answered(booking, accepted=True))
    return booking


def decline_new_date(booking: Booking, actor=Actor.GUEST) -> Booking:
    """No to the replacement date (or no answer in time): free, apart from costs the guide listed (R-026)."""
    if booking.is_registration:
        from . import departures

        return departures.decline_new_date(booking, actor)
    if booking.incurred_costs > 0:
        line = InvoiceLine(kind=InvoiceLine.Kind.EXPENSE, quantity=1, unit_price=booking.incurred_costs,
                           description=(booking.incurred_costs_note or "Angefallene Kosten")[:200])
        booking = _bill_costs(booking, actor, [line], "Ersatzdatum abgelehnt, angefallene Kosten")
    else:
        booking = transition(booking, {S.RESCHEDULE_PROPOSED}, S.CANCELLED, actor, "Ersatzdatum abgelehnt", deadline=None)
    transaction.on_commit(lambda: emails.new_date_answered(booking, accepted=False))
    return booking


def guest_cancel(booking: Booking) -> Booking:
    """Withdraw a request, or cancel a confirmed tour. Late cancellations are billed per the guide's terms (R-003)."""
    if booking.is_registration:
        from . import departures

        return departures.guest_cancel(booking)
    percent = cancellation_percent(booking) if booking.status == S.CONFIRMED and booking.has_price else 0
    if percent:
        fee = cancellation_fee(booking)
        line = InvoiceLine(kind=InvoiceLine.Kind.TOUR, quantity=1, unit_price=fee,
                           description=_("Storno-Kosten %(percent)s %% gemäss Bedingungen") % {"percent": percent})
        booking = _bill_costs(booking, Actor.GUEST, [line], f"Vom Gast storniert, {percent} %")
    else:
        booking = transition(booking, {S.REQUESTED, S.OFFERED, S.CONFIRMED}, S.CANCELLED, Actor.GUEST, deadline=None)
    transaction.on_commit(lambda: emails.cancelled_by_guest(booking))
    return booking


# --- System ----------------------------------------------------------------


def mark_paid_by_stripe(booking: Booking, session_id: str, payment_intent: str = "") -> Booking:
    booking = transition(
        booking, {S.AWAITING_PAYMENT}, S.PAID, Actor.STRIPE, f"Stripe Checkout {session_id}",
        payment_method=Booking.PaymentMethod.STRIPE, paid_at=timezone.now(), deadline=None, amount_paid=booking.price,
        stripe_checkout_session_id=session_id, stripe_payment_intent_id=payment_intent,
    )
    transaction.on_commit(lambda: emails.paid(booking))
    return booking


def expire_overdue() -> int:
    """Requests and offers nobody answered expire; unanswered replacement dates count as a no (R-026)."""
    count = 0
    now = timezone.now()
    for booking in Booking.objects.filter(status__in=[S.REQUESTED, S.OFFERED], deadline__lt=now):
        try:
            expired = transition(booking, {S.REQUESTED, S.OFFERED}, S.EXPIRED, Actor.SYSTEM, "Frist abgelaufen",
                                 deadline=None)
        except InvalidTransition:
            continue  # changed concurrently
        transaction.on_commit(lambda b=expired: emails.expired(b))
        count += 1
    for booking in Booking.objects.filter(status=S.RESCHEDULE_PROPOSED, deadline__lt=now).select_related("guide"):
        try:
            decline_new_date(booking, actor=Actor.SYSTEM)
        except InvalidTransition:
            continue
        count += 1
    return count
