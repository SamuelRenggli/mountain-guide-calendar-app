"""Scheduled dates (Termine) and registrations for them (phase 1).

    guest signs up ──▶ payment_pending (seats held ~30 min) ──Stripe paid──▶ paid ──guest cancels──▶ cancelled
                          └─ payment fails / times out ──▶ expired (seats free again)    (fee per terms, rest refunded)

    departure open ──guide confirms──▶ confirmed
         ├─ guide cancels / decision deadline passes ──▶ cancelled, everyone refunded in full (R-142, R-144)
         └─ guide moves it (weather) ──▶ registrations: reschedule ─┬─ accept ──▶ paid
                                                                     └─ decline / no reply ──▶ cancelled, full refund

Money only moves through refunds here: Booking.refund_due is set inside the transaction and sent to
Stripe after commit (payments.stripe_service.process_refund), retried hourly if Stripe fails.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import emails, messaging
from .models import Booking, BookingEvent, Departure, Notification, Tour, WaitlistEntry, money
from .services import BookingError, InvalidTransition, cancellation_percent, freeze_terms, log, transition

S = Booking.Status
D = Departure.Status
Actor = BookingEvent.Actor


def _refund_after_commit(booking: Booking):
    from payments import stripe_service

    transaction.on_commit(lambda: stripe_service.process_refund(booking.pk))


def _schedule_refund(booking: Booking, amount: Decimal) -> Booking:
    """Add `amount` to what is still to be refunded (call inside a transaction, on a locked row)."""
    amount = min(money(amount), booking.refundable - booking.refund_due)
    if amount > 0:
        booking.refund_due = money(booking.refund_due + amount)
        booking.save(update_fields=["refund_due", "updated_at"])
        _refund_after_commit(booking)
    return booking


def _lock(departure: Departure) -> Departure:
    return Departure.objects.select_for_update().select_related("guide").get(pk=departure.pk)


# --- Guide: create and manage ----------------------------------------------


def create_departure(guide, *, tour: Tour | None = None, save_as_template: bool = False, **fields) -> Departure:
    """Publish a scheduled date (R-100–R-103). Its days are blocked for requests right away (R-105);
    other scheduled dates may overlap (R-106)."""
    if not guide.can_take_card_payments:
        raise BookingError(_("Für ausgeschriebene Termine brauchst du Online-Zahlungen: Verbinde zuerst Stripe "
                             "in den Einstellungen."))
    _check(fields)
    with transaction.atomic():
        if save_as_template and tour is None:
            tour = Tour.objects.create(guide=guide, duration_days=(fields["end_date"] - fields["start_date"]).days + 1,
                                       **{f: fields[f] for f in Tour.DETAIL_FIELDS if f in fields})
        departure = Departure.objects.create(guide=guide, tour=tour, **fields)
    return departure


def _start_of(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def _check(fields, changed=None):
    """Validate a departure's dates and numbers; `changed` limits the future-only checks to edited fields."""
    changed = set(fields) if changed is None else changed
    if fields["end_date"] < fields["start_date"]:
        raise BookingError(_("Das Ende muss nach dem Beginn liegen."))
    if "start_date" in changed and fields["start_date"] <= timezone.localdate():
        raise BookingError(_("Der Termin muss in der Zukunft liegen."))
    if fields["max_participants"] < fields["min_participants"]:
        raise BookingError(_("Die Höchstzahl muss mindestens so gross sein wie die Mindestzahl."))
    for name in ("signup_deadline", "decision_deadline"):
        if name in changed and fields[name] <= timezone.now():
            raise BookingError(_("Anmeldeschluss und Entscheid-Frist müssen in der Zukunft liegen."))
        if fields[name] > _start_of(fields["start_date"]):
            raise BookingError(_("Anmeldeschluss und Entscheid-Frist müssen vor dem Tourbeginn liegen."))


def update_departure(departure: Departure, **fields) -> Departure:
    """Edit details. Dates and price stay fixed once someone has paid (use «move» for new dates)."""
    with transaction.atomic():
        departure = _lock(departure)
        if not departure.is_live:
            raise InvalidTransition()
        has_guests = departure.registrations.filter(status__in=Booking.SEAT_STATUSES).exists()
        if has_guests:
            for name in ("start_date", "end_date", "price_per_person"):
                if fields.get(name) is not None and fields[name] != getattr(departure, name):
                    raise BookingError(_("Datum und Preis lassen sich nicht mehr ändern, weil schon Gäste angemeldet "
                                         "sind. Für ein neues Datum: Termin verschieben."))
            if fields["max_participants"] < departure.seats_taken():
                raise BookingError(_("Die Höchstzahl ist kleiner als die bereits vergebenen Plätze."))
        checked = ("start_date", "end_date", "min_participants", "max_participants", "signup_deadline",
                   "decision_deadline")
        current = {f: getattr(departure, f) for f in checked}
        changed = {f for f in checked if f in fields and fields[f] != current[f]}
        _check({**current, **{f: fields[f] for f in checked if f in fields}}, changed)
        for name, value in fields.items():
            setattr(departure, name, value)
        departure.save()
    transaction.on_commit(lambda: notify_waitlist(departure))
    return departure


def confirm_departure(departure: Departure) -> Departure:
    """The guide decides the tour goes ahead (R-141). Signups stay open until the signup deadline."""
    with transaction.atomic():
        departure = _lock(departure)
        if departure.status != D.OPEN:
            raise InvalidTransition()
        departure.status, departure.decided_at = D.CONFIRMED, timezone.now()
        departure.save(update_fields=["status", "decided_at"])
        guests = list(departure.registrations.filter(status=S.PAID).select_related("guide", "departure"))
    transaction.on_commit(lambda: [emails.departure_confirmed(b) for b in guests])
    return departure


def cancel_departure(departure: Departure, message: str = "", actor=Actor.GUIDE) -> Departure:
    """Call off a scheduled date: everyone gets the full amount back automatically (R-142, R-144, R-146)."""
    with transaction.atomic():
        departure = _lock(departure)
        if not departure.is_live:
            raise InvalidTransition()
        departure.status, departure.decided_at = D.CANCELLED, timezone.now()
        departure.save(update_fields=["status", "decided_at"])
        cancelled = []
        note = message or ("Entscheid-Frist abgelaufen" if actor == Actor.SYSTEM else "Termin abgesagt")
        for booking in departure.registrations.filter(status__in=[*Booking.SEAT_STATUSES, S.PAYMENT_PENDING]):
            was_paying = booking.status == S.PAYMENT_PENDING
            booking = transition(booking, {booking.status}, S.CANCELLED if not was_paying else S.EXPIRED, actor, note,
                                 deadline=None, hold_until=None, guide_message=message)
            _schedule_refund(booking, booking.refundable)
            if not was_paying:
                cancelled.append(booking)
                if message:
                    messaging.record_sent(booking, message)  # the cancel email carries it; keep it in the history
        departure.waitlist.all().delete()
    automatic = actor == Actor.SYSTEM
    transaction.on_commit(lambda: [emails.departure_cancelled(b, automatic=automatic) for b in cancelled])
    if automatic:
        transaction.on_commit(lambda: emails.departure_auto_cancelled_guide(departure, len(cancelled)))
    else:
        transaction.on_commit(lambda: emails.departure_cancelled_guide(departure, len(cancelled)))
    return departure


def move_departure(departure: Departure, *, start_date, end_date, message: str = "") -> Departure:
    """Weather: the guide offers a replacement date (R-143). Each guest accepts it or cancels for free;
    no answer within GUEST_RESPONSE_DAYS counts as cancelling."""
    if start_date <= timezone.localdate() or end_date < start_date:
        raise BookingError(_("Bitte gib ein gültiges Ersatzdatum in der Zukunft an."))
    deadline = timezone.now() + timedelta(days=settings.GUEST_RESPONSE_DAYS)
    with transaction.atomic():
        departure = _lock(departure)
        if not departure.is_live:
            raise InvalidTransition()
        note = f"{departure.start_date:%d.%m.%Y} → {start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}. {message}".strip()
        departure.start_date, departure.end_date = start_date, end_date
        start = _start_of(start_date)
        departure.signup_deadline = min(max(departure.signup_deadline, timezone.now()), start)
        departure.decision_deadline = max(departure.decision_deadline, min(deadline, start))
        departure.reminder_sent_at = None
        departure.save()
        moved = []
        for booking in departure.registrations.filter(status__in=Booking.SEAT_STATUSES):
            moved.append(transition(booking, {booking.status}, S.RESCHEDULE_PROPOSED, Actor.GUIDE, note,
                                    start_date=start_date, end_date=end_date, guide_message=message, deadline=deadline))
            if message:
                messaging.record_sent(booking, message)
    transaction.on_commit(lambda: [emails.new_date_proposed(b) for b in moved])
    return departure


def refund_extra(booking: Booking, amount) -> Booking:
    """Goodwill: after a cancellation the guide pays back more, up to the full amount (R-134)."""
    amount = money(amount)
    with transaction.atomic():
        locked = Booking.objects.select_for_update(of=("self",)).select_related("guide", "departure").get(pk=booking.pk)
        if not locked.is_registration or locked.status not in (S.CANCELLED, S.EXPIRED):
            raise InvalidTransition()
        if amount <= 0 or amount > locked.refundable - locked.refund_due:
            raise BookingError(_("Höchstens CHF %(max)s können noch zurückbezahlt werden.")
                               % {"max": locked.refundable - locked.refund_due})
        log(locked, Actor.GUIDE, locked.status, locked.status, f"Kulanz: zusätzlich CHF {amount} zurück")
        _schedule_refund(locked, amount)
    transaction.on_commit(lambda: emails.goodwill_refund(locked, amount))
    return locked


# --- Guest: sign up, pay, cancel -------------------------------------------


def start_registration(departure: Departure, *, seats: int, guest_name: str, guest_email: str, guest_phone: str,
                       emergency_contact: str, experience: str, language: str) -> Booking:
    """Hold the seats while the guest pays (R-121). Concurrent signups never get more seats than are free (R-123)."""
    with transaction.atomic():
        departure = _lock(departure)
        if not departure.signup_open:
            raise BookingError(_("Für diesen Termin sind keine Anmeldungen mehr möglich."))
        if seats < 1:
            raise BookingError(_("Bitte mindestens einen Platz wählen."))
        if seats > departure.seats_free:
            raise BookingError(_("Leider sind nur noch %(n)s Plätze frei.") % {"n": departure.seats_free})
        booking = Booking(
            guide=departure.guide, departure=departure, tour=departure.tour, start_date=departure.start_date,
            end_date=departure.end_date, participants=seats, price=money(departure.price_per_person * seats),
            guest_name=guest_name, guest_email=guest_email, guest_phone=guest_phone,
            emergency_contact=emergency_contact, experience=experience, language=language,
            status=S.PAYMENT_PENDING, hold_until=timezone.now() + timedelta(minutes=settings.CHECKOUT_HOLD_MINUTES),
        )
        freeze_terms(booking, departure.guide)
        booking.save()
        log(booking, Actor.GUEST, "", S.PAYMENT_PENDING, f"{seats} Platz/Plätze")
    return booking


def registration_paid(booking: Booking, *, session_id: str, payment_intent: str, amount_cents: int) -> Booking:
    """Stripe confirmed the payment: the registration is binding without the guide (R-121).
    If the hold had run out and the seats are gone meanwhile, the money goes straight back."""
    with transaction.atomic():
        departure = _lock(booking.departure)
        locked = Booking.objects.select_for_update(of=("self",)).select_related("guide", "departure").get(pk=booking.pk)
        amount = money(Decimal(amount_cents) / 100)
        paid = {"payment_method": Booking.PaymentMethod.STRIPE, "paid_at": timezone.now(), "amount_paid": amount,
                "stripe_checkout_session_id": session_id, "stripe_payment_intent_id": payment_intent,
                "hold_until": None, "deadline": None}
        if locked.status == S.PAYMENT_PENDING or (locked.status == S.EXPIRED and departure.signup_open
                                                   and departure.seats_free >= locked.participants):
            locked = transition(locked, {locked.status}, S.PAID, Actor.STRIPE, f"Stripe Checkout {session_id}", **paid)
            departure.waitlist.filter(email__iexact=locked.guest_email).delete()
            messaging.notify(locked.guide, Notification.Kind.BOOKING, locked)
            transaction.on_commit(lambda: emails.registration_confirmed(locked))
            return locked
        # Too late: nothing to sign up for any more.
        for name, value in paid.items():
            setattr(locked, name, value)
        locked.save()
        log(locked, Actor.STRIPE, locked.status, locked.status, "Zahlung nach Ablauf der Reservation – zurückbezahlt")
        _schedule_refund(locked, amount)
        transaction.on_commit(lambda: emails.registration_too_late(locked))
        return locked


def payment_failed(booking: Booking) -> Booking | None:
    """Checkout expired or failed (R-122): release the seats and tell the guest."""
    try:
        booking = transition(booking, {S.PAYMENT_PENDING}, S.EXPIRED, Actor.STRIPE, "Zahlung nicht abgeschlossen",
                             hold_until=None)
    except InvalidTransition:
        return None
    transaction.on_commit(lambda: emails.payment_failed(booking))
    transaction.on_commit(lambda: notify_waitlist(booking.departure))
    return booking


def guest_cancel(booking: Booking) -> Booking:
    """The guest cancels (R-132, R-133): seats are free at once, the fee follows the guide's terms, the rest is refunded."""
    with transaction.atomic():
        locked = Booking.objects.select_for_update(of=("self",)).select_related("guide", "departure").get(pk=booking.pk)
        if locked.status == S.RESCHEDULE_PROPOSED:
            return decline_new_date(locked)
        if locked.status != S.PAID:
            raise InvalidTransition()
        percent = cancellation_percent(locked)
        fee = money(locked.amount_paid * percent / 100)
        locked = transition(locked, {S.PAID}, S.CANCELLED, Actor.GUEST,
                            f"Vom Gast storniert: {percent} % Storno-Kosten (CHF {fee}), Rückzahlung CHF {locked.amount_paid - fee}")
        _schedule_refund(locked, locked.amount_paid - fee)
    transaction.on_commit(lambda: emails.registration_cancelled(locked, fee=fee, percent=percent))
    transaction.on_commit(lambda: notify_waitlist(locked.departure))
    return locked


def accept_new_date(booking: Booking) -> Booking:
    booking = transition(booking, {S.RESCHEDULE_PROPOSED}, S.PAID, Actor.GUEST, "Ersatzdatum angenommen", deadline=None)
    transaction.on_commit(lambda: emails.new_date_answered(booking, accepted=True))
    return booking


def decline_new_date(booking: Booking, actor=Actor.GUEST) -> Booking:
    with transaction.atomic():
        booking = transition(booking, {S.RESCHEDULE_PROPOSED}, S.CANCELLED, actor, "Ersatzdatum abgelehnt – volle Rückzahlung",
                             deadline=None)
        _schedule_refund(booking, booking.refundable)
    transaction.on_commit(lambda: emails.new_date_answered(booking, accepted=False))
    transaction.on_commit(lambda: notify_waitlist(booking.departure))
    return booking


# --- Waitlist ----------------------------------------------------------------


def join_waitlist(departure: Departure, *, name: str, email: str, seats: int, language: str) -> WaitlistEntry:
    """Only while the date is full (R-130)."""
    if not departure.signup_open:
        raise BookingError(_("Für diesen Termin sind keine Anmeldungen mehr möglich."))
    if departure.seats_free >= seats:
        raise BookingError(_("Es sind Plätze frei – du kannst dich direkt anmelden."))
    entry, _created = WaitlistEntry.objects.update_or_create(
        departure=departure, email=email.lower(), defaults={"name": name, "seats": seats, "language": language,
                                                            "notified_at": None},
    )
    return entry


def notify_waitlist(departure: Departure) -> int:
    """A seat is free: tell everyone waiting at the same time, first come first served (R-131, decision 38)."""
    departure.refresh_from_db()
    if not departure.signup_open or departure.seats_free < 1:
        return 0
    entries = list(departure.waitlist.filter(notified_at__isnull=True))
    for entry in entries:
        emails.waitlist_seat_free(entry)
    departure.waitlist.filter(pk__in=[e.pk for e in entries]).update(notified_at=timezone.now())
    return len(entries)


# --- Hourly jobs -------------------------------------------------------------


def release_unpaid() -> int:
    """Seats held for a payment that never came."""
    count = 0
    for booking in Booking.objects.filter(status=S.PAYMENT_PENDING, hold_until__lt=timezone.now()).select_related("departure"):
        if payment_failed(booking):
            count += 1
    return count


def remind_and_auto_cancel() -> tuple[int, int]:
    """Remind guides before the decision deadline (R-140, R-145); cancel dates nobody decided on (R-144)."""
    now = timezone.now()
    reminded = 0
    soon = now + timedelta(hours=settings.DECISION_REMINDER_HOURS)
    for departure in Departure.objects.filter(status=D.OPEN, reminder_sent_at__isnull=True,
                                              decision_deadline__lte=soon, decision_deadline__gt=now):
        emails.decision_reminder(departure)
        Departure.objects.filter(pk=departure.pk).update(reminder_sent_at=now)
        reminded += 1
    cancelled = 0
    for departure in Departure.objects.filter(status=D.OPEN, decision_deadline__lte=now):
        try:
            cancel_departure(departure, actor=Actor.SYSTEM)
            cancelled += 1
        except InvalidTransition:
            continue
    return reminded, cancelled


# --- Data protection ---------------------------------------------------------


def erase_guest_data(guide, email: str) -> int:
    """The guest asked for deletion (R-152): phone, emergency contact and experience go; bookings and invoices stay."""
    bookings = Booking.objects.filter(guide=guide, guest_email__iexact=email)
    count = 0
    for booking in bookings:
        if booking.has_sensitive_data:
            booking.guest_phone = booking.emergency_contact = booking.experience = ""
            booking.save(update_fields=["guest_phone", "emergency_contact", "experience", "updated_at"])
            log(booking, Actor.GUIDE, booking.status, booking.status, "Telefon, Notfallkontakt und Erfahrung gelöscht")
            count += 1
    WaitlistEntry.objects.filter(departure__guide=guide, email__iexact=email).delete()
    return count
