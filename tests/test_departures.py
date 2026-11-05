"""Scheduled dates (Termine): signup with immediate payment, seats, waitlist, cancellations, refunds, deadlines."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone

from bookings import departures, services
from bookings.models import Booking, Departure, Tour, WaitlistEntry

from .conftest import at, in_days

S = Booking.Status
D = Departure.Status


@pytest.fixture(autouse=True)
def no_real_refunds():
    """Refunds go to Stripe after commit; here we only record what would be sent."""
    with mock.patch("payments.stripe_service.process_refund") as refund:
        yield refund


def signup(departure, seats=1, email="neu@example.com", **extra):
    values = dict(seats=seats, guest_name="Neu Gast", guest_email=email, guest_phone="079 222 22 22",
                  emergency_contact="Partner 079 333 33 33", experience="Breithorn", language="de")
    values.update(extra)
    return departures.start_registration(departure, **values)


def fields(start, **extra):
    values = dict(title="Weissmies", start_date=start, end_date=start, price_per_person=Decimal("390"),
                  min_participants=2, max_participants=5, signup_deadline=at(start - timedelta(days=3)),
                  decision_deadline=at(start - timedelta(days=2)), meeting_point="Saas-Grund", meeting_time=None)
    values.update(extra)
    return values


# --- Creating ---------------------------------------------------------------------------------


def test_publishing_needs_stripe(guide):
    with pytest.raises(services.BookingError, match="Stripe"):
        departures.create_departure(guide, **fields(in_days(20)))


def test_quick_entry_can_save_a_template(stripe_guide):
    """R-100, R-103."""
    departure = departures.create_departure(stripe_guide, save_as_template=True, **fields(in_days(20)))
    template = Tour.objects.get()
    assert departure.tour == template
    assert (template.title, template.price_per_person, template.max_participants) == ("Weissmies", 390, 5)


def test_not_included_goes_from_template_to_page_and_email(stripe_guide, client, mailoutbox,
                                                          django_capture_on_commit_callbacks):
    departure = departures.create_departure(stripe_guide, save_as_template=True,
                                            **fields(in_days(20), not_included="Anreise, Getränke"))
    assert Tour.objects.get().not_included == "Anreise, Getränke"
    page = client.get(departure.public_url).content.decode()
    assert "Nicht inbegriffen" in page and "Anreise, Getränke" in page
    booking = signup(departure)
    with django_capture_on_commit_callbacks(execute=True):
        departures.registration_paid(booking, session_id="cs_1", payment_intent="pi_1", amount_cents=39000)
    guest = next(m for m in mailoutbox if m.to == ["neu@example.com"])
    assert "Nicht inbegriffen:\nAnreise, Getränke" in guest.body


@pytest.mark.parametrize("bad", [
    {"max_participants": 1},
    {"start_date": None},
    {"decision_deadline": "after-start"},
])
def test_departure_validation(stripe_guide, bad):
    start = in_days(20)
    values = fields(start)
    if bad.get("start_date", 1) is None:
        values.update(start_date=in_days(0), end_date=in_days(0))
    elif bad.get("decision_deadline") == "after-start":
        values["decision_deadline"] = at(start + timedelta(days=1))
    else:
        values.update(bad)
    with pytest.raises(services.BookingError):
        departures.create_departure(stripe_guide, **values)


def test_overlapping_departures_are_allowed(stripe_guide):
    """R-106: e.g. a second group with an aspirant."""
    departures.create_departure(stripe_guide, **fields(in_days(20)))
    departures.create_departure(stripe_guide, **fields(in_days(20), title="Zweite Gruppe"))
    assert Departure.objects.count() == 2


# --- Signup and payment ---------------------------------------------------------------------


def test_signup_holds_seats_until_paid(stripe_guide, make_departure):
    departure = make_departure(stripe_guide)
    booking = signup(departure, seats=3)
    assert (booking.status, booking.price, booking.participants) == (S.PAYMENT_PENDING, Decimal("1440.00"), 3)
    assert booking.emergency_contact == "Partner 079 333 33 33"
    assert departure.seats_free == 1
    with pytest.raises(services.BookingError, match="nur noch 1"):
        signup(departure, seats=2, email="b@example.com")


def test_unpaid_hold_runs_out(stripe_guide, make_departure, mailoutbox, django_capture_on_commit_callbacks):
    """R-122: no payment, no seats."""
    departure = make_departure(stripe_guide)
    booking = signup(departure, seats=4)
    assert departure.seats_free == 0
    Booking.objects.filter(pk=booking.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
    assert departure.seats_free == 4, "an expired hold no longer counts"
    with django_capture_on_commit_callbacks(execute=True):
        assert departures.release_unpaid() == 1
    booking.refresh_from_db()
    assert booking.status == S.EXPIRED
    assert "nicht abgeschlossen" in mailoutbox[-1].subject


def test_payment_confirms_without_guide(stripe_guide, make_departure, mailoutbox, django_capture_on_commit_callbacks):
    """R-121, R-125, R-126, R-204."""
    departure = make_departure(stripe_guide)
    booking = signup(departure, seats=2)
    WaitlistEntry.objects.create(departure=departure, name="Neu", email="neu@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        booking = departures.registration_paid(booking, session_id="cs_1", payment_intent="pi_1", amount_cents=96000)
    assert (booking.status, booking.amount_paid, booking.stripe_payment_intent_id) == (S.PAID, Decimal("960.00"), "pi_1")
    assert not WaitlistEntry.objects.exists(), "signed up people leave the waitlist"
    guest = next(m for m in mailoutbox if m.to == ["neu@example.com"])
    for expected in ("Pont, Parkplatz, 08:00", "Hochtourenausrüstung", "Kondition für 1200 Hm", "+41 79 123 45 67",
                     "Zahlungsbeleg", "CHF 960.00", booking.guest_url):
        assert expected in guest.body, expected
    guide_mail = next(m for m in mailoutbox if m.to == [stripe_guide.email])
    assert "Angemeldet: 2 von 4" in guide_mail.body
    assert "079 333" not in guide_mail.body, "emergency contacts stay on the dashboard"


def test_late_payment_after_seats_are_gone_is_refunded(stripe_guide, make_departure, make_registration, no_real_refunds,
                                                       django_capture_on_commit_callbacks):
    departure = make_departure(stripe_guide)
    late = signup(departure, seats=2)
    Booking.objects.filter(pk=late.pk).update(status=S.EXPIRED, hold_until=None)
    make_registration(departure, seats=4, email="voll@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        late = departures.registration_paid(late, session_id="cs_2", payment_intent="pi_2", amount_cents=96000)
    assert late.status == S.EXPIRED and late.refund_due == Decimal("960.00")
    no_real_refunds.assert_called_with(late.pk)


def test_concurrent_signups_never_overbook(stripe_guide, make_departure):
    """R-123: the departure row is locked; the second signup sees the first one's held seats."""
    departure = make_departure(stripe_guide, max_participants=2)
    signup(departure, seats=2)
    with pytest.raises(services.BookingError):
        signup(departure, seats=1, email="zu-spaet@example.com")


def test_signup_closes_at_deadline(stripe_guide, make_departure):
    """R-124."""
    departure = make_departure(stripe_guide, signup_deadline=timezone.now() - timedelta(minutes=1))
    with pytest.raises(services.BookingError, match="keine Anmeldungen"):
        signup(departure)


# --- Waitlist -------------------------------------------------------------------------------


def test_waitlist_only_when_full_and_everyone_notified(stripe_guide, make_departure, make_registration, mailoutbox,
                                                       django_capture_on_commit_callbacks):
    """R-130, R-131: everyone at once, first come first served."""
    departure = make_departure(stripe_guide, max_participants=2)
    with pytest.raises(services.BookingError):
        departures.join_waitlist(departure, name="A", email="a@example.com", seats=1, language="de")
    paid = make_registration(departure, seats=2)
    departures.join_waitlist(departure, name="A", email="a@example.com", seats=1, language="fr")
    departures.join_waitlist(departure, name="B", email="b@example.com", seats=1, language="de")
    mailoutbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        departures.guest_cancel(paid)
    waiting = {m.to[0]: m for m in mailoutbox if m.to[0] in ("a@example.com", "b@example.com")}
    assert set(waiting) == {"a@example.com", "b@example.com"}
    assert waiting["a@example.com"].subject.startswith("Place libre"), "in the language of the entry"
    assert departure.public_url in waiting["b@example.com"].body
    assert all(e.notified_at for e in departure.waitlist.all())


# --- Guest cancels --------------------------------------------------------------------------


@pytest.mark.parametrize("days_before, refund", [(20, "960.00"), (10, "480.00"), (3, "0.00")])
def test_guest_cancel_refunds_per_terms(stripe_guide, make_departure, make_registration, no_real_refunds,
                                        django_capture_on_commit_callbacks, days_before, refund):
    """R-132, R-133."""
    departure = make_departure(stripe_guide, in_days(days_before),
                               signup_deadline=timezone.now() + timedelta(hours=1),
                               decision_deadline=timezone.now() + timedelta(hours=2))
    booking = make_registration(departure, seats=2)
    with django_capture_on_commit_callbacks(execute=True):
        booking = departures.guest_cancel(booking)
    assert booking.status == S.CANCELLED
    assert booking.refund_due == Decimal(refund)
    assert departure.seats_free == 4


def test_goodwill_refund_up_to_full_amount(stripe_guide, make_departure, make_registration):
    """R-134."""
    departure = make_departure(stripe_guide, in_days(10), signup_deadline=timezone.now() + timedelta(hours=1),
                               decision_deadline=timezone.now() + timedelta(hours=2))
    booking = departures.guest_cancel(make_registration(departure, seats=2))  # 50 %: 480 back
    with pytest.raises(services.BookingError):
        departures.refund_extra(booking, Decimal("481"))
    booking = departures.refund_extra(booking, Decimal("480"))
    assert booking.refund_due == Decimal("960.00")
    with pytest.raises(services.InvalidTransition):
        departures.refund_extra(make_registration(departure, email="aktiv@example.com"), Decimal("10"))


# --- Guide decides --------------------------------------------------------------------------


def test_confirm_departure_informs_everyone(stripe_guide, make_departure, make_registration, mailoutbox,
                                            django_capture_on_commit_callbacks):
    """R-141."""
    departure = make_departure(stripe_guide)
    make_registration(departure, email="a@example.com")
    make_registration(departure, email="b@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        departure = departures.confirm_departure(departure)
    assert departure.status == D.CONFIRMED
    assert {m.to[0] for m in mailoutbox} == {"a@example.com", "b@example.com"}
    assert all("findet statt" in m.subject for m in mailoutbox)


def test_cancel_departure_refunds_everyone(stripe_guide, make_departure, make_registration, mailoutbox, no_real_refunds,
                                           django_capture_on_commit_callbacks):
    """R-142, R-146: full amount back, no matter how close to the date."""
    departure = make_departure(stripe_guide, in_days(3), signup_deadline=timezone.now() + timedelta(hours=1),
                               decision_deadline=timezone.now() + timedelta(hours=2))
    a = make_registration(departure, seats=2, email="a@example.com")
    pending = signup(departure, seats=1)
    with django_capture_on_commit_callbacks(execute=True):
        departures.cancel_departure(departure, "Zu viel Neuschnee")
    a.refresh_from_db()
    pending.refresh_from_db()
    assert (a.status, a.refund_due) == (S.CANCELLED, Decimal("960.00"))
    assert pending.status == S.EXPIRED
    assert [m.to[0] for m in mailoutbox] == ["a@example.com"]
    assert "CHF 960.00" in mailoutbox[0].body and "Zu viel Neuschnee" in mailoutbox[0].body
    assert services.availability.range_bookable(stripe_guide, in_days(3), in_days(3)), "days free for requests again"


def test_move_departure_guests_accept_or_cancel_free(stripe_guide, make_departure, make_registration, mailoutbox,
                                                     django_capture_on_commit_callbacks):
    """R-143."""
    departure = make_departure(stripe_guide, in_days(10), signup_deadline=timezone.now() + timedelta(hours=1),
                               decision_deadline=timezone.now() + timedelta(hours=2))
    a = make_registration(departure, seats=1, email="a@example.com")
    b = make_registration(departure, seats=1, email="b@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        departure = departures.move_departure(departure, start_date=in_days(17), end_date=in_days(18), message="Sturm")
    assert (departure.start_date, departure.end_date) == (in_days(17), in_days(18))
    a.refresh_from_db()
    assert (a.status, a.start_date) == (S.RESCHEDULE_PROPOSED, in_days(17))
    assert {m.to[0] for m in mailoutbox} == {"a@example.com", "b@example.com"}
    assert services.accept_new_date(a).status == S.PAID
    b.refresh_from_db()
    b = services.decline_new_date(b)
    assert (b.status, b.refund_due) == (S.CANCELLED, Decimal("480.00")), "full refund even 10 days before"


def test_decision_reminder_and_auto_cancel(stripe_guide, make_departure, make_registration, mailoutbox, no_real_refunds,
                                           django_capture_on_commit_callbacks):
    """R-140, R-144, R-145."""
    soon = make_departure(stripe_guide, in_days(5), signup_deadline=timezone.now() + timedelta(hours=1),
                          decision_deadline=timezone.now() + timedelta(hours=2))
    later = make_departure(stripe_guide, in_days(9), decision_deadline=timezone.now() + timedelta(days=3),
                           signup_deadline=timezone.now() + timedelta(days=2))
    with django_capture_on_commit_callbacks(execute=True):
        assert departures.remind_and_auto_cancel() == (1, 0)
        assert departures.remind_and_auto_cancel() == (0, 0), "remind only once"
    reminder = mailoutbox[-1]
    assert reminder.to == [stripe_guide.email] and "Mindestzahl noch nicht erreicht" in reminder.body
    assert "automatisch abgesagt" in reminder.body

    guest = make_registration(soon)
    Departure.objects.filter(pk=soon.pk).update(decision_deadline=timezone.now() - timedelta(minutes=1))
    mailoutbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert departures.remind_and_auto_cancel() == (0, 1)
    soon.refresh_from_db()
    guest.refresh_from_db()
    assert soon.status == D.CANCELLED and guest.refund_due == Decimal("480.00")
    assert {m.to[0] for m in mailoutbox} == {"gast@example.com", stripe_guide.email}
    later.refresh_from_db()
    assert later.status == D.OPEN


def test_confirmed_departure_is_not_auto_cancelled(stripe_guide, make_departure):
    departure = departures.confirm_departure(make_departure(stripe_guide))
    Departure.objects.filter(pk=departure.pk).update(decision_deadline=timezone.now() - timedelta(minutes=1))
    assert departures.remind_and_auto_cancel() == (0, 0)


def test_edit_keeps_dates_and_price_once_guests_paid(stripe_guide, make_departure, make_registration):
    departure = make_departure(stripe_guide)
    departures.update_departure(departure, description="Neu: mit Gletscherkurs", max_participants=6)
    make_registration(departure, seats=2)
    with pytest.raises(services.BookingError):
        departures.update_departure(departure, price_per_person=Decimal("500"))
    with pytest.raises(services.BookingError):
        departures.update_departure(departure, max_participants=1)


# --- Data protection ------------------------------------------------------------------------


def test_erase_guest_data_keeps_bookings(stripe_guide, other_guide, make_departure, make_registration):
    """R-151, R-152."""
    mine = make_registration(make_departure(stripe_guide))
    theirs = make_registration(make_departure(other_guide))
    assert departures.erase_guest_data(stripe_guide, "GAST@example.com") == 1
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert (mine.guest_phone, mine.emergency_contact, mine.experience) == ("", "", "")
    assert (mine.guest_name, mine.amount_paid) == ("Gast", Decimal("480.00"))
    assert theirs.emergency_contact, "another guide's data stays untouched"
