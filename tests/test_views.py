import time
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from accounts.models import AccessLog, DayRateTier
from bookings.forms import form_timestamp_token
from bookings.models import Booking, Departure, Tour

from .conftest import in_days

S = Booking.Status


def aged_token(seconds=10):
    with mock.patch("django.core.signing.time.time", return_value=time.time() - seconds):
        return form_timestamp_token()


def widget_post(client, guide, start, end=None, **extra):
    data = {
        "start_date": start.isoformat(), "end_date": (end or start).isoformat(), "participants": 2,
        "guest_name": "Gina Gast", "guest_email": "gina@example.com", "guest_phone": "", "guest_message": "Biancograt",
        "website": "", "ts": aged_token(), "language": "de", "accept_terms": "on",
    }
    data.update(extra)
    return client.post(reverse("widget:request", args=[guide.slug]), data)


def signup_post(client, departure, **extra):
    data = {"seats": 2, "guest_name": "Neu", "guest_email": "neu@example.com", "guest_phone": "079 1",
            "emergency_contact": "Mama 079 2", "experience": "Allalin", "language": "de", "accept_terms": "on",
            "website": "", "ts": aged_token()}
    data.update(extra)
    return client.post(reverse("widget:departure_signup", args=[departure.public_id]), data)


# --- Widget: custom requests -------------------------------------------------------------------


def test_widget_request_creates_booking_with_fixed_dates(client, guide):
    response = widget_post(client, guide, in_days(10), in_days(12))
    assert response.status_code == 200
    assert "Danke für deine Anfrage" in response.content.decode()
    booking = Booking.objects.get()
    assert (booking.status, booking.start_date, booking.end_date, booking.is_window) == (
        S.REQUESTED, in_days(10), in_days(12), False)
    assert booking.terms_accepted_at


def test_widget_request_needs_terms(client, guide):
    """R-002."""
    response = widget_post(client, guide, in_days(10), accept_terms="")
    assert response.status_code == 400
    assert not Booking.objects.exists()


@pytest.mark.parametrize("message", ["", "x" * 501])
def test_widget_request_needs_short_description(client, guide, message):
    """R-021: up to 500 characters."""
    assert widget_post(client, guide, in_days(10), guest_message=message).status_code == 400
    assert not Booking.objects.exists()


def test_widget_500_characters_are_fine(client, guide):
    assert widget_post(client, guide, in_days(10), guest_message="x" * 500).status_code == 200


def test_widget_rejects_taken_and_blocked_days(client, guide, make_booking, make_departure):
    make_booking(guide, in_days(10))
    make_departure(guide, in_days(20))
    assert widget_post(client, guide, in_days(9), in_days(10)).status_code == 400
    assert widget_post(client, guide, in_days(20)).status_code == 400
    assert Booking.objects.count() == 1


@pytest.mark.parametrize("extra", [{"website": "http://spam"}, {"ts": "forged"}])
def test_widget_spam_is_dropped(client, guide, extra):
    response = widget_post(client, guide, in_days(10), **extra)
    assert response.status_code == 200
    assert not Booking.objects.exists()


def test_widget_too_fast_submission_is_dropped(client, guide):
    widget_post(client, guide, in_days(10), ts=form_timestamp_token())
    assert not Booking.objects.exists()


def test_widget_rate_limit(client, guide):
    for i in range(5):
        assert widget_post(client, guide, in_days(10 + i)).status_code == 200
    assert widget_post(client, guide, in_days(20)).status_code == 429
    assert Booking.objects.count() == 5


def test_widget_rejects_header_injection(client, guide):
    response = widget_post(client, guide, in_days(10), guest_name="Eve\r\nBcc: victim@example.com")
    assert response.status_code == 400
    assert not Booking.objects.exists()


def test_widget_shows_selection_and_rate_tiers(client, guide):
    """R-201: the guest sees the day rate for their group size."""
    DayRateTier.objects.create(guide=guide, min_participants=5, day_rate=850)
    url = reverse("widget:show", args=[guide.slug])
    html = client.get(url, {"von": in_days(10).isoformat(), "bis": in_days(13).isoformat()}).content.decode()
    assert 'id="booking-form"' in html and 'maxlength="500"' in html
    assert html.count("day free selected") == 4
    assert "1–4 Personen" in html and "ab 5 Personen" in html and "CHF 850 pro Tag" in html
    assert 'data-rates="1:750,5:850"' in html


def test_widget_lists_open_departures_first(client, guide, make_departure, make_registration):
    """R-112."""
    open_one = make_departure(guide, title="Gran Paradiso", max_participants=4)
    make_registration(open_one, seats=3)
    make_departure(guide, in_days(40), title="Abgesagt", status=Departure.Status.CANCELLED)
    html = client.get(reverse("widget:show", args=[guide.slug])).content.decode()
    assert html.index("Gran Paradiso") < html.index("Eigene Tour anfragen")
    assert "1 Platz frei" in html and "CHF 480 pro Person" in html
    assert "Abgesagt" not in html


# --- Scheduled date pages ---------------------------------------------------------------------


def test_departure_page_has_own_address_and_signup(client, stripe_guide, make_departure):
    """R-110."""
    departure = make_departure(stripe_guide)
    response = client.get(departure.public_url.replace("https://tourchef.test", ""))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Gran Paradiso" in html and "Pont, Parkplatz" in html and "Hochtourenausrüstung" in html
    assert 'action="/t/' in html and 'target="_top"' in html, "Stripe Checkout can't run inside an iframe"


def test_signup_redirects_to_stripe(client, stripe_guide, make_departure):
    departure = make_departure(stripe_guide)
    with mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(id="cs", url="https://checkout.stripe.com/x")):
        response = signup_post(client, departure)
    assert response.status_code == 302 and response["Location"].startswith("https://checkout.stripe.com/")
    booking = Booking.objects.get()
    assert (booking.status, booking.participants, booking.emergency_contact) == (S.PAYMENT_PENDING, 2, "Mama 079 2")


@pytest.mark.parametrize("missing", ["guest_phone", "emergency_contact", "experience", "accept_terms"])
def test_signup_requires_safety_data_and_terms(client, stripe_guide, make_departure, missing):
    """R-120, R-002."""
    response = signup_post(client, make_departure(stripe_guide), **{missing: ""})
    assert response.status_code == 400
    assert not Booking.objects.exists()


def test_signup_failure_at_stripe_releases_seats(client, stripe_guide, make_departure):
    import stripe

    departure = make_departure(stripe_guide)
    with mock.patch("stripe.checkout.Session.create", side_effect=stripe.APIConnectionError("down")):
        assert signup_post(client, departure).status_code == 400
    assert Booking.objects.get().status == S.EXPIRED
    assert departure.seats_free == 4


def test_full_departure_offers_waitlist(client, stripe_guide, make_departure, make_registration):
    departure = make_departure(stripe_guide, min_participants=1, max_participants=1)
    make_registration(departure)
    html = client.get(reverse("widget:departure", args=[departure.public_id])).content.decode()
    assert "Warteliste" in html and "departure_signup" not in html
    data = {"guest_name": "W", "guest_email": "w@example.com", "seats": 1, "language": "it", "website": "",
            "ts": aged_token()}
    response = client.post(reverse("widget:departure_waitlist", args=[departure.public_id]), data)
    assert response.status_code == 200
    assert departure.waitlist.get().language == "it"


def test_guide_terms_page_shows_cancellation_tiers(client, guide):
    """R-003, R-004."""
    url = reverse("widget:terms", args=[guide.slug])
    html = client.get(url).content.decode()
    assert "bis 14 Tage vor Tourbeginn" in html and "50 %" in html and "Vertragspartner" in html
    guide.guest_terms = "Meine eigenen AGB."
    guide.save()
    html = client.get(url).content.decode()
    assert "Meine eigenen AGB." in html and "Vertragspartner" not in html


# --- Guest pages ------------------------------------------------------------------------------


def test_guest_page_needs_valid_token(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    assert client.get(reverse("guest:detail", args=["falsch"])).status_code == 404
    response = client.get(reverse("guest:detail", args=[booking.token]))
    assert response.status_code == 200 and response["X-Robots-Tag"] == "noindex"


def test_guest_accepts_offer(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.OFFERED, price=900)
    client.post(reverse("guest:action", args=[booking.token, "annehmen"]))
    booking.refresh_from_db()
    assert booking.status == S.CONFIRMED


def test_guest_answers_replacement_date(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.RESCHEDULE_PROPOSED, price=900)
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Neues Datum annehmen" in html
    client.post(reverse("guest:action", args=[booking.token, "ersatzdatum-ablehnen"]))
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED


def test_guest_sees_cancellation_cost_before_cancelling(client, stripe_guide, make_departure, make_registration):
    from datetime import timedelta

    from django.utils import timezone

    departure = make_departure(stripe_guide, in_days(10), signup_deadline=timezone.now() + timedelta(hours=1),
                               decision_deadline=timezone.now() + timedelta(hours=2))
    booking = make_registration(departure, seats=2)
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "50 % (CHF 480" in html and "CHF 480" in html


def test_guest_cannot_pay_unbilled_booking(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    response = client.post(reverse("guest:pay", args=[booking.token]))
    assert response.status_code == 302
    assert response["Location"] == reverse("guest:detail", args=[booking.token])


def test_guest_page_in_booking_language(client, guide, make_booking):
    """R-012: the page follows the language the guest wrote in, unless they switch."""
    booking = make_booking(guide, in_days(10), language="fr")
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert '<html lang="fr">' in html
    html = client.get(reverse("guest:detail", args=[booking.token]), {"lang": "it"}).content.decode()
    assert '<html lang="it">' in html


# --- Dashboard --------------------------------------------------------------------------------


def test_guide_confirms_with_breakdown(client, guide, make_booking, mailoutbox, django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), end_date=in_days(11))
    client.force_login(guide)
    data = {"confirm-price": "2000", "confirm-guide_rate": "750", "confirm-extra_guides": "0",
            "confirm-aspirants": "1", "confirm-aspirant_rate": "500", "confirm-estimated_expenses": "150",
            "confirm-show_breakdown": "on", "confirm-message": "Bis bald"}
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("dashboard:booking_action", args=[booking.pk, "confirm"]), data)
    assert response.status_code == 302
    booking.refresh_from_db()
    assert (booking.status, booking.price, booking.estimated_expenses) == (S.CONFIRMED, 2000, 150)
    assert [r["amount"] for r in booking.price_breakdown] == ["1500.00", "1000.00"]
    guest_html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Aspirant" in guest_html and "Spesen geschätzt ca. CHF 150" in guest_html


def test_guide_offers_other_dates(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    data = {"offer-start_date": in_days(11).isoformat(), "offer-end_date": in_days(12).isoformat(),
            "offer-price": "1600", "offer-guide_rate": "800", "offer-extra_guides": "0", "offer-aspirants": "0",
            "offer-aspirant_rate": "500", "offer-message": "Passt besser"}
    client.post(reverse("dashboard:booking_action", args=[booking.pk, "offer"]), data)
    booking.refresh_from_db()
    assert (booking.status, booking.start_date, booking.end_date) == (S.OFFERED, in_days(11), in_days(12))
    assert booking.price_breakdown == [], "breakdown only when the guide ticks it"


def test_actions_require_post(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    assert client.get(reverse("dashboard:booking_action", args=[booking.pk, "confirm"])).status_code == 405


def test_guide_bills_after_tour(client, guide, make_booking, mailoutbox, django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(-3), end_date=in_days(-2), status=S.CONFIRMED, price=Decimal("2000"))
    client.force_login(guide)
    url = reverse("dashboard:invoice", args=[booking.pk])
    html = client.get(url).content.decode()
    assert 'name="price" value="2000.00"' in html and "Preisrechner" in html
    data = {
        "price": "2100", "message": "",
        "spesen-TOTAL_FORMS": "2", "spesen-INITIAL_FORMS": "0", "spesen-MIN_NUM_FORMS": "0", "spesen-MAX_NUM_FORMS": "20",
        "spesen-0-description": "Hütte", "spesen-0-amount": "120", "spesen-1-description": "", "spesen-1-amount": "",
    }
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(url, data).status_code == 302
    booking.refresh_from_db()
    assert (booking.status, str(booking.price)) == (S.AWAITING_PAYMENT, "2220.00")
    guest_html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "2220" in guest_html and "Hütte" in guest_html


def test_quick_entry_publishes_departure(client, stripe_guide):
    client.force_login(stripe_guide)
    start = in_days(25)
    data = {"title": "Alphubel", "start_date": start.isoformat(), "end_date": start.isoformat(),
            "price_per_person": "350", "min_participants": "2", "max_participants": "4",
            "signup_deadline": f"{in_days(22).isoformat()}T18:00", "decision_deadline": f"{in_days(23).isoformat()}T18:00",
            "meeting_point": "Täschalp", "meeting_time": "06:00", "save_as_template": "on"}
    response = client.post(reverse("dashboard:departure_new"), data)
    departure = Departure.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("dashboard:departure", args=[departure.pk])
    assert Tour.objects.get().title == "Alphubel"
    html = client.get(response["Location"]).content.decode()
    assert departure.public_url in html and "data-share" in html


def test_departure_from_template_is_prefilled(client, stripe_guide, tour):
    client.force_login(stripe_guide)
    html = client.get(reverse("dashboard:departure_new"), {"vorlage": tour.pk, "datum": in_days(30).isoformat()}).content.decode()
    assert 'value="Piz Palü"' in html and 'value="Diavolezza Talstation"' in html
    assert f'name="end_date" value="{in_days(31).isoformat()}"' in html, "end follows the template's duration"


def test_departure_detail_lists_participants_and_logs_access(client, stripe_guide, make_departure, make_registration):
    """R-150: contacts, emergency contacts and experience — and who looked at them."""
    departure = make_departure(stripe_guide)
    make_registration(departure, seats=2)
    client.force_login(stripe_guide)
    html = client.get(reverse("dashboard:departure", args=[departure.pk])).content.decode()
    assert "Mama, 079 111 11 11" in html and "Mont Blanc 2024" in html and "+41 79 000 00 00" in html
    log = AccessLog.objects.get()
    assert (log.user, log.action, log.object_ref) == (stripe_guide, "view_participants", f"departure:{departure.pk}")


def test_guide_cancels_departure_from_dashboard(client, stripe_guide, make_departure, make_registration):
    departure = make_departure(stripe_guide)
    booking = make_registration(departure)
    client.force_login(stripe_guide)
    with mock.patch("payments.stripe_service.process_refund"):
        client.post(reverse("dashboard:departure_action", args=[departure.pk, "cancel"]), {"cancel-message": "Wetter"})
    booking.refresh_from_db()
    assert booking.status == S.CANCELLED and booking.refund_due == Decimal("480.00")


def test_pages_render(client, stripe_guide, make_booking, make_departure, make_registration, tour):
    booking = make_booking(stripe_guide, in_days(10))
    departure = make_departure(stripe_guide)
    registration = make_registration(departure)
    client.force_login(stripe_guide)
    for name, args in [("dashboard:home", []), ("dashboard:tours", []), ("dashboard:tour_new", []),
                       ("dashboard:tour_edit", [tour.pk]), ("dashboard:departures", []),
                       ("dashboard:departure_new", []), ("dashboard:departure", [departure.pk]),
                       ("dashboard:departure_edit", [departure.pk]), ("dashboard:availability", []),
                       ("dashboard:calendar", []), ("dashboard:settings", []), ("dashboard:booking", [booking.pk]),
                       ("dashboard:booking", [registration.pk])]:
        assert client.get(reverse(name, args=args)).status_code == 200, name


def test_settings_saves_terms_vat_and_tiers(client, guide):
    client.force_login(guide)
    data = {"display_name": guide.display_name, "email": guide.email, "phone": "", "language": "fr", "slug": guide.slug,
            "widget_allowed_origins": "", "day_rate": "780", "aspirant_day_rate": "520", "iban": "ch9300762011623852957",
            "vat_number": "CHE-123.456.789 MWST", "vat_rate": "8.1", "cancel_free_days": "21", "cancel_partial_days": "10",
            "cancel_partial_percent": "40", "guest_terms": "",
            "tiers-TOTAL_FORMS": "2", "tiers-INITIAL_FORMS": "0", "tiers-MIN_NUM_FORMS": "0", "tiers-MAX_NUM_FORMS": "6",
            "tiers-0-min_participants": "5", "tiers-0-day_rate": "850", "tiers-1-min_participants": "",
            "tiers-1-day_rate": ""}
    assert client.post(reverse("dashboard:settings"), data).status_code == 302
    guide.refresh_from_db()
    assert (guide.day_rate, guide.iban, guide.cancel_free_days, guide.language) == (780, "CH93 0076 2011 6238 5295 7", 21, "fr")
    assert guide.rate_for(5) == 850 and guide.charges_vat


def test_dashboard_in_guide_language(client, guide):
    guide.language = "it"
    guide.save()
    client.force_login(guide)
    html = client.get(reverse("dashboard:home")).content.decode()
    assert '<html lang="it">' in html


def test_pilot_stats_for_staff_only(client, guide, make_booking, make_guide):
    """R-030."""
    make_booking(guide, in_days(10))
    make_booking(guide, in_days(12), status=S.CONFIRMED, price=900)
    client.force_login(guide)
    assert client.get(reverse("dashboard:pilot_stats")).status_code == 302
    admin = make_guide("Samuel Admin", is_staff=True)
    client.force_login(admin)
    from accounts.models import User

    User.objects.filter(pk=admin.pk).update(totp_enabled=True)
    session = client.session
    session["2fa_ok"] = True
    session.save()
    html = client.get(reverse("dashboard:pilot_stats")).content.decode()
    assert "Anna Berg" in html
    row = html[html.index("Anna Berg"):].split("</tr>")[0]
    assert "<td>2</td><td>1</td><td>0</td><td>0</td><td>0</td>" in row.replace("\n", "")
