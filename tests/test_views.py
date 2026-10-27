import time
from unittest import mock

import pytest
from django.urls import reverse

from bookings.forms import form_timestamp_token
from bookings.models import Booking

from .conftest import in_days

S = Booking.Status


def aged_token(seconds=10):
    with mock.patch("django.core.signing.time.time", return_value=time.time() - seconds):
        return form_timestamp_token()


def widget_post(client, tour_, start, end=None, **extra):
    data = {
        "tour": tour_.pk, "start_date": start.isoformat(), "end_date": (end or tour_.end_date_for(start)).isoformat(),
        "participants": 2, "guest_name": "Gina Gast",
        "guest_email": "gina@example.com", "guest_phone": "", "guest_message": "", "website": "", "ts": aged_token(), "language": "de",
    }
    data.update(extra)
    return client.post(reverse("widget:request", args=[tour_.guide.slug]), data)


# --- Authentication & authorization ---------------------------------------


def test_dashboard_requires_login(client, db):
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_login(client, guide):
    response = client.post(reverse("accounts:login"), {"username": guide.email, "password": "sehr-geheimes-pw-123"})
    assert response.status_code == 302
    assert client.get(reverse("dashboard:home")).status_code == 200


def test_login_rate_limited(client, guide):
    for _ in range(10):
        client.post(reverse("accounts:login"), {"username": guide.email, "password": "falsch"})
    response = client.post(reverse("accounts:login"), {"username": guide.email, "password": "sehr-geheimes-pw-123"})
    assert response.status_code == 302 and response["Location"] == reverse("accounts:login")
    assert "_auth_user_id" not in client.session


def test_signup_requires_invite_code(client, db):
    data = {"display_name": "Neu", "email": "neu@example.ch", "password1": "Gletscher-Spalte-77", "password2": "Gletscher-Spalte-77"}
    assert client.post(reverse("accounts:signup"), {**data, "invite_code": "falsch"}).status_code == 200
    response = client.post(reverse("accounts:signup"), {**data, "invite_code": "bergfuehrer-2026"})
    assert response.status_code == 302


def test_signup_closed_without_code(client, db, settings):
    settings.GUIDE_SIGNUP_CODE = ""
    response = client.post(reverse("accounts:signup"), {"invite_code": ""})
    assert "Registrierung geschlossen" in response.content.decode()


def test_guide_cannot_access_other_guides_booking(client, tour, other_guide, make_booking):
    booking = make_booking(tour, in_days(10), guest_name="Vertraulich Muster")
    client.force_login(other_guide)
    assert client.get(reverse("dashboard:booking", args=[booking.pk])).status_code == 404
    assert client.post(reverse("dashboard:booking_action", args=[booking.pk, "confirm"])).status_code == 404
    assert client.post(reverse("dashboard:booking_action", args=[booking.pk, "reject"])).status_code == 404
    assert client.get(reverse("dashboard:tour_edit", args=[tour.pk])).status_code == 404
    booking.refresh_from_db()
    assert booking.status == S.REQUESTED
    assert "Vertraulich" not in client.get(reverse("dashboard:home")).content.decode()


def test_guide_confirms_own_booking(client, tour, make_booking, mailoutbox, django_capture_on_commit_callbacks):
    booking = make_booking(tour, in_days(10))
    client.force_login(tour.guide)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("dashboard:booking_action", args=[booking.pk, "confirm"]), {"confirm-message": "Bis bald"})
    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert mailoutbox[-1].to == ["gast@example.com"]


def test_actions_require_post(client, tour, make_booking):
    booking = make_booking(tour, in_days(10))
    client.force_login(tour.guide)
    assert client.get(reverse("dashboard:booking_action", args=[booking.pk, "confirm"])).status_code == 405


def test_guest_input_is_escaped_in_dashboard(client, tour, make_booking):
    booking = make_booking(tour, in_days(10), guest_name="<script>alert(1)</script>", guest_message="<img src=x onerror=alert(1)>")
    client.force_login(tour.guide)
    for url in (reverse("dashboard:home"), reverse("dashboard:booking", args=[booking.pk])):
        html = client.get(url).content.decode()
        assert "<script>alert(1)</script>" not in html
        assert "<img src=x" not in html
        assert "&lt;script&gt;" in html


# --- Security headers -------------------------------------------------------


def test_dashboard_cannot_be_framed(client, guide):
    client.force_login(guide)
    response = client.get(reverse("dashboard:home"))
    assert response["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]


def test_widget_can_be_framed(client, tour):
    response = client.get(reverse("widget:show", args=[tour.guide.slug]))
    assert response.status_code == 200
    assert "X-Frame-Options" not in response
    assert "frame-ancestors *" in response["Content-Security-Policy"]


def test_widget_framing_restricted_to_guide_sites(client, tour):
    tour.guide.widget_allowed_origins = "https://www.anna-berg.ch"
    tour.guide.save()
    response = client.get(reverse("widget:show", args=[tour.guide.slug]))
    assert "frame-ancestors https://www.anna-berg.ch;" in response["Content-Security-Policy"]


# --- Widget -----------------------------------------------------------------


def test_widget_shows_free_days_and_form(client, tour):
    day = in_days(10)
    response = client.get(reverse("widget:show", args=[tour.guide.slug]), {"tour": tour.pk, "von": day.isoformat()})
    html = response.content.decode()
    assert "Piz Palü" in html
    assert 'id="booking-form"' in html


def test_widget_ignores_unavailable_date(client, tour, make_booking):
    make_booking(tour, in_days(10))
    response = client.get(reverse("widget:show", args=[tour.guide.slug]), {"von": in_days(10).isoformat()})
    assert 'id="booking-form"' not in response.content.decode()


def test_widget_request_creates_booking(client, tour):
    response = widget_post(client, tour, in_days(10))
    assert response.status_code == 200
    assert "Danke für deine Anfrage" in response.content.decode()
    booking = Booking.objects.get()
    assert (booking.guide, booking.status, booking.participants) == (tour.guide, S.REQUESTED, 2)


def test_widget_request_cannot_use_other_guides_tour(client, tour, other_guide):
    url = reverse("widget:request", args=[other_guide.slug])
    response = client.post(url, {"tour": tour.pk, "start_date": in_days(10).isoformat(), "ts": aged_token()})
    assert response.status_code == 400
    assert not Booking.objects.exists()


@pytest.mark.parametrize("extra", [{"website": "http://spam"}, {"ts": "forged"}])
def test_widget_spam_is_dropped(client, tour, extra):
    response = widget_post(client, tour, in_days(10), **extra)
    assert response.status_code == 200
    assert not Booking.objects.exists()


def test_widget_too_fast_submission_is_dropped(client, tour):
    widget_post(client, tour, in_days(10), ts=form_timestamp_token())
    assert not Booking.objects.exists()


def test_widget_rate_limit(client, tour):
    for i in range(5):
        assert widget_post(client, tour, in_days(10 + i)).status_code == 200
    assert widget_post(client, tour, in_days(20)).status_code == 429
    assert Booking.objects.count() == 5


def test_widget_rejects_taken_dates(client, tour, make_booking):
    make_booking(tour, in_days(10))
    response = widget_post(client, tour, in_days(10))
    assert response.status_code == 400
    assert Booking.objects.count() == 1


def test_widget_rejects_header_injection(client, tour):
    response = widget_post(client, tour, in_days(10), guest_name="Eve\r\nBcc: victim@example.com")
    assert response.status_code == 400
    assert not Booking.objects.exists()


# --- Guest pages -------------------------------------------------------------


def test_guest_page_needs_valid_token(client, tour, make_booking):
    booking = make_booking(tour, in_days(10))
    assert client.get(reverse("guest:detail", args=["falsch"])).status_code == 404
    response = client.get(reverse("guest:detail", args=[booking.token]))
    assert response.status_code == 200
    assert response["X-Robots-Tag"] == "noindex"


def test_guest_accepts_offer(client, tour, make_booking):
    booking = make_booking(tour, in_days(10), status=S.OFFERED)
    client.post(reverse("guest:action", args=[booking.token, "annehmen"]))
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


def test_guest_cannot_pay_unconfirmed_booking(client, tour, make_booking):
    booking = make_booking(tour, in_days(10))
    response = client.post(reverse("guest:pay", args=[booking.token]))
    assert response.status_code == 302
    assert response["Location"] == reverse("guest:detail", args=[booking.token])


# --- Tours --------------------------------------------------------------------


def test_tour_with_bookings_is_deactivated_not_deleted(client, tour, make_booking):
    make_booking(tour, in_days(10))
    client.force_login(tour.guide)
    client.post(reverse("dashboard:tour_delete", args=[tour.pk]))
    tour.refresh_from_db()
    assert tour.is_active is False


def test_pages_render(client, tour, make_booking):
    booking = make_booking(tour, in_days(10))
    client.force_login(tour.guide)
    for name, args in [("dashboard:home", []), ("dashboard:tours", []), ("dashboard:tour_new", []),
                       ("dashboard:availability", []), ("dashboard:calendar", []), ("dashboard:settings", []),
                       ("dashboard:booking", [booking.pk])]:
        assert client.get(reverse(name, args=args)).status_code == 200, name


def test_widget_language_from_browser_and_param(client, tour):
    url = reverse("widget:show", args=[tour.guide.slug])
    day = {"von": in_days(10).isoformat()}
    html = client.get(url, day, HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9,de;q=0.8").content.decode()
    assert '<option value="en" selected>' in html
    html = client.get(url, {**day, "lang": "de"}, HTTP_ACCEPT_LANGUAGE="en-GB").content.decode()
    assert '<option value="de" selected>' in html


def test_widget_request_in_english(client, tour, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        widget_post(client, tour, in_days(10), language="en")
    assert Booking.objects.get().language == "en"
    guest_mail = next(m for m in mailoutbox if m.to == ["gina@example.com"])
    guide_mail = next(m for m in mailoutbox if m.to == [tour.guide.email])
    assert guest_mail.subject.startswith("Your request")
    assert guide_mail.subject.startswith("Neue Buchungsanfrage")  # guide's profile language is German


def test_widget_window_request_for_tour_holds_no_days(client, tour):
    response = widget_post(client, tour, in_days(10), in_days(14), guest_message="Flexibel")
    assert response.status_code == 200
    booking = Booking.objects.get()
    assert booking.is_window and (booking.start_date, booking.end_date) == (in_days(10), in_days(14))
    assert widget_post(client, tour, in_days(12)).status_code == 200, "the window holds no days"


def test_widget_own_idea_request(client, tour):
    response = widget_post(client, tour, in_days(10), in_days(12), **{"tour": "idee", "guest_message": "Biancograt"})
    assert response.status_code == 200
    booking = Booking.objects.get()
    assert booking.tour is None and booking.is_window and booking.price == 0


def test_widget_own_idea_needs_description(client, tour):
    assert widget_post(client, tour, in_days(10), **{"tour": "idee"}).status_code == 400
    assert not Booking.objects.exists()


def test_widget_window_limited_to_14_days(client, tour):
    assert widget_post(client, tour, in_days(10), in_days(24)).status_code == 400
    assert not Booking.objects.exists()


def test_widget_shows_window_selection(client, tour):
    url = reverse("widget:show", args=[tour.guide.slug])
    html = client.get(url, {"von": in_days(10).isoformat(), "bis": in_days(13).isoformat()}).content.decode()
    assert 'id="booking-form"' in html
    assert "Eigene Idee" in html and "Piz Palü" in html
    assert html.count("day free selected") == 4


def test_guide_offers_dates_for_window(client, tour, make_booking):
    booking = make_booking(tour, in_days(10), end_date=in_days(14), is_window=True)
    client.force_login(tour.guide)
    confirm = reverse("dashboard:booking_action", args=[booking.pk, "confirm"])
    client.post(confirm, {"confirm-message": ""})
    booking.refresh_from_db()
    assert booking.status == S.REQUESTED, "a window can't be confirmed without exact dates"
    offer = reverse("dashboard:booking_action", args=[booking.pk, "offer"])
    client.post(offer, {"offer-start_date": in_days(11).isoformat(), "offer-price": "600", "offer-message": "Passt"})
    booking.refresh_from_db()
    assert (booking.status, booking.is_window, booking.start_date, booking.end_date) == (S.OFFERED, False, in_days(11), in_days(11))
