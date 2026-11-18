"""Custom request flow after the guide test with Dominik (2026-11-18): linear yes with planning details, fellow
participants enter their own details, guide's booking page and to-do list."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, Companion, Notification

from .conftest import DETAILS, confirm_offer, in_days

S = Booking.Status


def test_offer_page_asks_for_details_before_the_definite_yes(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.OFFERED, price=900)
    page = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Bitte bestätigen" in page and "?zusagen=1" in page and 'name="angaben-billing_address"' not in page
    page = client.get(reverse("guest:detail", args=[booking.token]), {"zusagen": "1"}).content.decode()
    assert 'name="angaben-billing_address"' in page and "Definitiv zusagen" in page and "Bitte bestätigen" not in page
    # the old one-click yes is gone
    client.post(reverse("guest:action", args=[booking.token, "annehmen"]))
    booking.refresh_from_db()
    assert booking.status == S.OFFERED


def test_definite_yes_confirms_saves_details_and_rings_the_bell(client, guide, make_booking, mailoutbox,
                                                                django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), status=S.OFFERED, price=900, participants=4)
    response = client.post(reverse("guest:details", args=[booking.token]), {"angaben-billing_address": ""})
    booking.refresh_from_db()
    assert response.status_code == 400 and booking.status == S.OFFERED and not booking.guest_details
    assert "Definitiv zusagen" in response.content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        confirm_offer(client, booking)
    booking.refresh_from_db()
    assert booking.status == S.CONFIRMED and "Bahnhofstr" in booking.guest_details
    assert Notification.objects.get(guide=guide, booking=booking).kind == Notification.Kind.CONFIRMED
    guest_mail = next(m for m in mailoutbox if m.to == [booking.guest_email])
    assert booking.companion_url in guest_mail.body  # group: link for the others
    page = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert booking.companion_url in page and "Deine Mitreisenden" in page


def test_too_late_yes_keeps_nothing(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.OFFERED, price=900,
                           deadline=timezone.now() - timedelta(minutes=5))
    confirm_offer(client, booking)
    booking.refresh_from_db()
    assert booking.status == S.OFFERED and not booking.guest_details


def test_companion_enters_own_details(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED, price=900, participants=3)
    url = reverse("companion", args=[booking.pk, booking.companion_code])
    page = client.get(url).content.decode()
    assert booking.guest_name in page and 'name="angaben-name"' in page
    data = {**DETAILS, "angaben-name": "Mia Mit", "angaben-email": "mia@example.org", "angaben-phone_0": "41",
            "angaben-phone_1": "079 222 33 44", "angaben-billing_address": "", "angaben-diet": "vegan"}
    response = client.post(url, data)
    assert response.status_code == 302 and "gespeichert=1" in response["Location"]
    companion = Companion.objects.get(booking=booking)
    assert (companion.name, companion.phone, companion.saved_details()["diet"]) == ("Mia Mit", "+41 792223344", "vegan")
    assert client.get(reverse("companion", args=[booking.pk, "falsch" * 4])).status_code == 404


def test_companion_link_closed_for_single_or_unconfirmed_bookings(client, guide, make_booking):
    single = make_booking(guide, in_days(10), status=S.CONFIRMED, price=900, participants=1)
    offered = make_booking(guide, in_days(20), status=S.OFFERED, price=900, participants=3,
                           guest_email="o@example.org")
    for b in (single, offered):
        assert client.get(reverse("companion", args=[b.pk, b.companion_code])).status_code == 404


def test_guide_sees_participants_adds_and_removes(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED, price=900, participants=3)
    Companion.objects.create(booking=booking, name="Mia Mit", phone="+41 792223344", details='{"diet": "vegan"}')
    client.force_login(guide)
    page = client.get(reverse("dashboard:booking", args=[booking.pk])).content.decode()
    assert "Mia Mit" in page and "Nummern kopieren" in page and "Abrechnung auslösen" in page
    assert "Ersatzdatum" not in page and 'name="msg-body"' not in page
    client.post(reverse("dashboard:booking_action", args=[booking.pk, "add_companion"]),
                {"companion-name": "Ueli Neu", "companion-phone_0": "41", "companion-phone_1": "079 111 22 33"})
    assert booking.companions.count() == 2
    mia = booking.companions.get(name="Mia Mit")
    client.post(reverse("dashboard:booking_action", args=[booking.pk, f"remove_companion-{mia.pk}"]))
    assert list(booking.companions.values_list("name", flat=True)) == ["Ueli Neu"]
    printed = client.get(reverse("dashboard:booking_print", args=[booking.pk])).content.decode()
    assert "Ueli Neu" in printed and booking.guest_name in printed


def test_other_guide_cannot_touch_participants(client, guide, other_guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED, price=900, participants=2)
    mia = Companion.objects.create(booking=booking, name="Mia Mit")
    client.force_login(other_guide)
    assert client.get(reverse("dashboard:booking_print", args=[booking.pk])).status_code == 404
    client.post(reverse("dashboard:booking_action", args=[booking.pk, f"remove_companion-{mia.pk}"]))
    assert Companion.objects.filter(pk=mia.pk).exists()


def test_guide_notes(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED, price=900)
    client.force_login(guide)
    client.post(reverse("dashboard:booking_action", args=[booking.pk, "notes"]),
                {"notes-guide_notes": "Am Telefon: 8 Uhr Bahnhof"})
    booking.refresh_from_db()
    assert booking.guide_notes == "Am Telefon: 8 Uhr Bahnhof"
    assert "8 Uhr Bahnhof" not in client.get(reverse("guest:detail", args=[booking.token])).content.decode()


def test_request_page_shows_only_the_request_and_two_buttons(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    page = client.get(reverse("dashboard:booking", args=[booking.pk])).content.decode()
    assert "Annehmen" in page and "Ablehnen" in page and "Anderer Tagessatz" in page
    assert "Preis und Bedingungen" not in page and "Verlauf" not in page and "Teilnehmende" not in page


def test_group_rate_on_guest_page(client, guide, make_booking):
    from accounts.models import DayRateTier

    DayRateTier.objects.create(guide=guide, min_participants=5, day_rate=950)
    booking = make_booking(guide, in_days(10), participants=8)
    page = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "CHF 950 pro Tag" in page and "CHF 750 pro Tag" not in page


def test_home_todo_lists_decisions_and_billing(client, guide, make_booking, make_departure):
    make_booking(guide, in_days(5), status=S.CONFIRMED, price=900, decision_date=in_days(3), guest_name="Eva Entscheid")
    make_booking(guide, in_days(-3), status=S.CONFIRMED, price=Decimal("900"), guest_name="Rolf Rechnung",
                 guest_email="r@example.org")
    client.force_login(guide)
    page = client.get(reverse("dashboard:home")).content.decode()
    assert "To Do" in page and "Durchführung entscheiden" in page and "Eva Entscheid" in page
    assert "Abrechnen" in page and "Rolf Rechnung" in page
