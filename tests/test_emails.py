import re
from decimal import Decimal

import pytest
from django.utils import timezone

from bookings import emails
from bookings.models import Booking, WaitlistEntry

from .conftest import in_days

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
LANGS = ["de", "fr", "it", "en"]


@pytest.mark.parametrize("lang", LANGS)
def test_every_email_renders_in_every_language(lang, stripe_guide, make_booking, make_departure, make_registration,
                                               mailoutbox):
    stripe_guide.language = lang
    stripe_guide.save()
    request = make_booking(stripe_guide, in_days(10), language=lang, guide_message="Bis bald {not a placeholder}",
                           guest_message="Hallo {guide}", price=Decimal("1500"), estimated_expenses=Decimal("200"),
                           price_breakdown=[{"role": "guide", "persons": 1, "days": 2.0, "rate": "750.00",
                                             "amount": "1500.00"}],
                           incurred_costs=Decimal("50"), incurred_costs_note="Hütte")
    invoiced = make_booking(stripe_guide, in_days(-5), status=Booking.Status.PAID, language=lang, price=750,
                            invoiced_at=timezone.now(), paid_at=timezone.now(), amount_paid=750, payment_method="manual")
    departure = make_departure(stripe_guide)
    registration = make_registration(departure, language=lang)
    entry = WaitlistEntry.objects.create(departure=departure, name="W", email="w@example.com", language=lang)

    calls = [
        lambda: emails.request_created(request), lambda: emails.confirmed(request), lambda: emails.offer_sent(request),
        lambda: emails.offer_accepted(request), lambda: emails.offer_declined(request), lambda: emails.rejected(request),
        lambda: emails.cancelled_by_guest(request), lambda: emails.cancelled_by_guide(request),
        lambda: emails.booking_changed(request, [("price", "CHF 1", "CHF 2"), ("dates", "a", "b")]),
        lambda: emails.new_date_proposed(request), lambda: emails.new_date_answered(request, accepted=True),
        lambda: emails.new_date_answered(request, accepted=False), lambda: emails.invoice_sent(invoiced),
        lambda: emails.paid(invoiced), lambda: emails.expired(request),
        lambda: emails.registration_confirmed(registration), lambda: emails.registration_too_late(registration),
        lambda: emails.payment_failed(registration),
        lambda: emails.registration_cancelled(registration, fee=Decimal("240"), percent=50),
        lambda: emails.departure_confirmed(registration),
        lambda: emails.departure_cancelled(registration, automatic=True),
        lambda: emails.departure_auto_cancelled_guide(departure, 3), lambda: emails.decision_reminder(departure),
        lambda: emails.waitlist_seat_free(entry), lambda: emails.goodwill_refund(registration, Decimal("100")),
        lambda: emails.new_date_proposed(registration),
    ]
    for call in calls:
        mailoutbox.clear()
        call()
        assert mailoutbox, call
        for mail in mailoutbox:
            assert "\n" not in mail.subject
            assert not PLACEHOLDER.search(mail.subject), mail.subject
            assert not PLACEHOLDER.search(mail.body.replace("{guide}", "").replace("{not a placeholder}", "")), mail.body
            assert "/rechtliches/datenschutz/" in mail.body


def test_emails_are_translated(stripe_guide, make_registration, make_departure, mailoutbox):
    subjects = {}
    departure = make_departure(stripe_guide)
    for lang in LANGS:
        registration = make_registration(departure, language=lang, email=f"{lang}@example.com")
        emails.registration_confirmed(registration)
        subjects[lang] = next(m.subject for m in mailoutbox if m.to == [f"{lang}@example.com"])
    assert subjects["de"].startswith("Anmeldung bestätigt")
    assert len(set(subjects.values())) == 4, subjects


def test_guest_and_guide_languages_are_independent(guide, make_booking, mailoutbox):
    guide.language = "en"
    guide.save()
    booking = make_booking(guide, in_days(10), language="it")
    emails.request_created(booking)
    by_recipient = {m.to[0]: m for m in mailoutbox}
    assert by_recipient[guide.email].subject.startswith("New request")
    assert not by_recipient["gast@example.com"].subject.startswith(("Deine", "Your"))


def test_invoice_payment_options(guide, make_booking, mailoutbox, settings):
    booking = make_booking(guide, in_days(-1), status=Booking.Status.AWAITING_PAYMENT, language="en",
                           invoiced_at=timezone.now(), price=Decimal("750.00"))
    booking.invoice_lines.create(position=0, kind="tour", description="Tourenpreis", quantity=1, unit_price=750)
    emails.invoice_sent(booking)
    assert "payment details directly" in mailoutbox[-1].body
    assert "750.00" in mailoutbox[-1].body
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    guide.stripe_account_id, guide.stripe_charges_enabled, guide.iban = "acct_1", True, "CH93 0076 2011 6238 5295 7"
    guide.save()
    emails.invoice_sent(booking)
    assert "Pay online (card/TWINT): " in mailoutbox[-1].body
    assert "IBAN CH93 0076 2011 6238 5295 7" in mailoutbox[-1].body
