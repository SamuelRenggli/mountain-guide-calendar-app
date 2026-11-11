import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

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
    data = {"seats": 2, "guest_name": "Neu", "guest_email": "neu@example.com", "guest_phone_0": "41", "guest_phone_1": "079 123 45 67",
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
    assert html.index("Gran Paradiso") < html.index('id="request-title"')
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


@pytest.mark.parametrize("missing", ["guest_phone_1", "emergency_contact", "experience", "accept_terms"])
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


def accept_data(start, end, **extra):
    data = {"accept-start_date": start.isoformat(), "accept-end_date": end.isoformat(), "accept-guide_rate": "750",
            "accept-decision_date": (start - timedelta(days=2)).isoformat(),
            "accept-answer_deadline": (start - timedelta(days=3)).isoformat()}
    data.update({f"accept-{k}": v for k, v in extra.items()})
    return data


def test_guide_accepts_with_day_rate_then_guest_confirms(client, guide, make_booking, mailoutbox,
                                                         django_capture_on_commit_callbacks):
    """Accepting: day rate x days (price left empty), decision date, expense estimate. Pending until the guest agrees."""
    booking = make_booking(guide, in_days(10), end_date=in_days(11))
    client.force_login(guide)
    page = client.get(reverse("dashboard:booking", args=[booking.pk])).content.decode()
    assert "Annehmen" in page and "Ablehnen" in page and "Wer" in page and "Wann" in page
    data = accept_data(in_days(10), in_days(11), estimated_expenses="150", message="Bis bald")
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("dashboard:booking_action", args=[booking.pk, "accept"]), data)
    assert response.status_code == 302
    booking.refresh_from_db()
    assert (booking.status, booking.price, booking.estimated_expenses) == (S.OFFERED, 1500, 150)
    assert booking.price_breakdown == [] and booking.decision_date == in_days(8)
    assert f"{in_days(8):%d.%m.%Y}" in mailoutbox[-1].body
    guest_html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Spesen geschätzt ca. CHF 150" in guest_html and "Bezahlt wird erst nach der Tour" in guest_html
    client.post(reverse("guest:action", args=[booking.token, "annehmen"]))
    booking.refresh_from_db()
    assert booking.status == S.CONFIRMED


def test_accept_rejects_decision_date_after_start(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    data = accept_data(in_days(10), in_days(10))
    data["accept-decision_date"] = in_days(11).isoformat()
    response = client.post(reverse("dashboard:booking_action", args=[booking.pk, "accept"]), data)
    booking.refresh_from_db()
    assert response.status_code == 400 and booking.status == S.REQUESTED
    assert "vor dem Tourenbeginn" in response.content.decode()


def test_guide_sets_answer_deadline_per_offer(client, guide, make_booking):
    """Questionnaire Q3 (answer B): the guide picks the guest's answer deadline on each offer; the whole day counts."""
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    page = client.get(reverse("dashboard:booking", args=[booking.pk])).content.decode()
    assert "Antwortfrist" in page and in_days(5).isoformat() in page  # suggestion: usual 5 days
    client.post(reverse("dashboard:booking_action", args=[booking.pk, "accept"]),
                accept_data(in_days(10), in_days(10), answer_deadline=in_days(2).isoformat()))
    booking.refresh_from_db()
    local = timezone.localtime(booking.deadline)
    assert booking.status == S.OFFERED and (local.date(), local.hour, local.minute) == (in_days(2), 23, 59)
    response = client.post(reverse("dashboard:booking_action", args=[booking.pk, "accept"]),
                           accept_data(in_days(10), in_days(10), answer_deadline=in_days(11).isoformat()))
    assert response.status_code == 400 and "Antwortfrist" in response.content.decode()


def test_guide_accepts_with_other_dates_and_own_price(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    client.post(reverse("dashboard:booking_action", args=[booking.pk, "accept"]),
                accept_data(in_days(11), in_days(12), price="1600", message="Passt besser"))
    booking.refresh_from_db()
    assert (booking.status, booking.start_date, booking.end_date, booking.price) == (
        S.OFFERED, in_days(11), in_days(12), 1600)


def test_accepting_onto_a_busy_day_keeps_the_form(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    make_booking(guide, in_days(11), status=S.CONFIRMED, guest_email="x@example.com")
    client.force_login(guide)
    response = client.post(reverse("dashboard:booking_action", args=[booking.pk, "accept"]),
                           accept_data(in_days(10), in_days(11), message="Zwei Tage?"))
    assert response.status_code == 400 and "schon belegt" in response.content.decode()
    booking.refresh_from_db()
    assert booking.status == S.REQUESTED and booking.end_date == in_days(10)


def test_guest_enters_planning_details_once(client, guide, make_booking):
    from bookings.models import GuestProfile

    booking = make_booking(guide, in_days(10), status=S.CONFIRMED, price=Decimal("750"))
    url = reverse("guest:details", args=[booking.token])
    data = {"angaben-billing_address": "Gina Gast, Bahnhofstr. 1, 3000 Bern",
            "angaben-emergency_contact": "Paul, 079 123 45 67", "angaben-diet": "vegan", "angaben-allergies": "Nüsse",
            "angaben-club": "DAV", "angaben-club_number": "", "angaben-travel_card": "halbtax", "angaben-car": "3"}
    assert client.post(url, data).status_code == 302
    profile = GuestProfile.objects.get()
    assert (profile.diet, profile.club, profile.car_seats, profile.has_reciprocal_rights) == ("vegan", "DAV", 3, True)
    client.force_login(guide)
    page = client.get(reverse("dashboard:booking", args=[booking.pk])).content.decode()
    assert "Bahnhofstr. 1" in page and "Gegenrecht" in page
    # Questionnaire Q8 (answer C): a later booking with the same address doesn't show the details to the guest,
    # only to the guide.
    later = make_booking(guide, in_days(30), status=S.CONFIRMED, price=Decimal("750"))
    assert "Bahnhofstr. 1" not in client.get(reverse("guest:detail", args=[later.token])).content.decode()
    assert "Bahnhofstr. 1" in client.get(reverse("dashboard:booking", args=[later.pk])).content.decode()
    # the first booking still shows the guest what they entered there
    assert "Bahnhofstr. 1" in client.get(reverse("guest:detail", args=[booking.token])).content.decode()


def test_actions_require_post(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    assert client.get(reverse("dashboard:booking_action", args=[booking.pk, "accept"])).status_code == 405


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
    assert 'name="save_as_template"' not in html, "already a template"


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
    url = reverse("dashboard:departure_action", args=[departure.pk, "cancel"])
    with mock.patch("payments.stripe_service.process_refund"):
        client.post(url, {"cancel-message": "Wetter"})
        booking.refresh_from_db()
        assert booking.status == S.PAID, "without the confirmation tick nothing happens"
        client.post(url, {"cancel-message": "Wetter", "cancel-sure": "on"})
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
            "cancel_partial_percent": "40", "guest_terms": "", "current_password": "sehr-geheimes-pw-123",  # new IBAN
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


def test_dashboard_pages_show_upcoming_guests_and_pictures(client, guide, make_booking, make_departure):
    from bookings.models import ExternalTour

    confirmed = make_booking(guide, in_days(20), status=S.CONFIRMED, price=Decimal("750"), guest_name="Cora")
    make_departure(guide, in_days(10))
    ExternalTour.objects.create(guide=guide, title="Biancograt mit Bergschule", start_date=in_days(5),
                                end_date=in_days(6), provider="B+T")
    for i in range(5):
        make_booking(guide, in_days(40 + i), status=S.CANCELLED, guest_email=f"g{i}@example.com")
    client.force_login(guide)
    home = client.get(reverse("dashboard:home")).content.decode()
    assert home.index("Biancograt mit Bergschule") < home.index(confirmed.title)
    assert "Kommende Touren" in home and "weitere anzeigen" in home
    guests = client.get(reverse("dashboard:guests")).content.decode()
    assert "Cora" in guests and "g1@example.com" not in guests
    assert client.get(reverse("dashboard:pictures")).status_code == 200
    ext = client.get(reverse("dashboard:external_tours")).content.decode()
    assert "Manuelle Eingabe" in ext
