import pytest

from bookings import emails
from bookings.models import Booking

from .conftest import in_days


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize("key", sorted(emails.MESSAGES))
def test_every_email_renders(key, lang, tour, make_booking, mailoutbox):
    tour.guide.language = lang
    tour.guide.save()
    booking = make_booking(tour, in_days(10), language=lang, guide_message="Bis bald {not a placeholder}",
                           guest_message="Hallo {guide}")
    emails._send(key, booking, to_guest=True)
    emails._send(key, booking, to_guest=False)
    assert len(mailoutbox) == 2
    for mail in mailoutbox:
        assert "{" not in mail.subject
        assert "\n" not in mail.subject
        assert mail.body.endswith(emails.FOOTER[lang])


def test_guest_and_guide_languages_are_independent(tour, make_booking, mailoutbox):
    tour.guide.language = "en"
    tour.guide.save()
    booking = make_booking(tour, in_days(10), language="de")
    booking.deadline = None
    emails.request_created(booking)
    by_recipient = {m.to[0]: m for m in mailoutbox}
    assert by_recipient[tour.guide.email].subject.startswith("New booking request")
    assert by_recipient["gast@example.com"].subject.startswith("Deine Anfrage")


def test_payment_hint_depends_on_stripe(tour, make_booking, mailoutbox, settings):
    booking = make_booking(tour, in_days(10), status=Booking.Status.AWAITING_PAYMENT, language="en")
    emails.confirmed(booking)
    assert "payment details directly" in mailoutbox[-1].body
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    tour.guide.stripe_account_id, tour.guide.stripe_charges_enabled = "acct_1", True
    tour.guide.save()
    emails.confirmed(booking)
    assert "complete your booking by paying" in mailoutbox[-1].body
