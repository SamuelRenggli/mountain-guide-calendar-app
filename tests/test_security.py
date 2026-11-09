"""Login, two-factor, tenant isolation, encryption at rest, export, legal pages (12-datenschutz.md, level A + B)."""

import csv
import io

import pyotp
import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccessLog
from bookings.models import Booking

from .conftest import in_days

S = Booking.Status


def login(client, user, password="sehr-geheimes-pw-123"):
    return client.post(reverse("accounts:login"), {"username": user.email, "password": password})


# --- Login --------------------------------------------------------------------------------------


def test_dashboard_requires_login(client, db):
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]


def test_login_rate_limited(client, guide):
    for _ in range(10):
        login(client, guide, "falsch")
    response = login(client, guide)
    assert response.status_code == 302 and response["Location"] == reverse("accounts:login")
    assert "_auth_user_id" not in client.session


def test_signup_requires_invite_code_and_terms(client, db):
    data = {"display_name": "Neu", "email": "neu@example.ch", "language": "de", "password1": "Gletscher-Spalte-77",
            "password2": "Gletscher-Spalte-77", "invite_code": "bergfuehrer-2026"}
    assert client.post(reverse("accounts:signup"), {**data, "invite_code": "falsch", "accept_terms": "on"}).status_code == 200
    assert client.post(reverse("accounts:signup"), data).status_code == 200, "terms of use must be accepted"
    assert client.post(reverse("accounts:signup"), {**data, "accept_terms": "on"}).status_code == 302


def test_signup_closed_without_code(client, db, settings):
    settings.GUIDE_SIGNUP_CODE = ""
    assert "Registrierung geschlossen" in client.get(reverse("accounts:signup")).content.decode()


def test_admin_uses_the_normal_login(client, db, settings):
    response = client.get(f"/{settings.ADMIN_URL}")
    assert response.status_code == 302
    follow = client.get(response["Location"])
    assert follow.status_code == 302 and reverse("accounts:login") in follow["Location"]


# --- Two-factor ----------------------------------------------------------------------------------


def test_two_factor_is_required_and_set_up(client, guide, settings):
    settings.REQUIRE_GUIDE_2FA = True
    login(client, guide)
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302 and response["Location"] == reverse("accounts:2fa_setup")
    html = client.get(reverse("accounts:2fa_setup")).content.decode()
    assert "<svg" in html
    secret = client.session["2fa_setup_secret"]
    assert client.post(reverse("accounts:2fa_setup"), {"code": "000000"}).status_code == 200
    response = client.post(reverse("accounts:2fa_setup"), {"code": pyotp.TOTP(secret).now()})
    assert "Wiederherstellungscodes" in response.content.decode()
    guide.refresh_from_db()
    assert guide.totp_enabled and guide.totp_secret == secret
    assert client.get(reverse("dashboard:home")).status_code == 200


@pytest.fixture
def guide_2fa(guide):
    from accounts import twofactor

    guide.totp_secret, guide.totp_enabled = pyotp.random_base32(), True
    guide.recovery_codes_plain = twofactor.new_recovery_codes(guide)
    guide.save()
    return guide


def test_login_needs_code_after_password(client, guide_2fa):
    login(client, guide_2fa)
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302 and response["Location"].startswith(reverse("accounts:2fa_verify"))
    assert client.get(reverse("dashboard:settings")).status_code == 302
    assert client.post(reverse("accounts:2fa_verify"), {"code": "123456"}).status_code == 200
    code = pyotp.TOTP(guide_2fa.totp_secret).now()
    assert client.post(reverse("accounts:2fa_verify"), {"code": code, "next": "/dashboard/einstellungen/"})["Location"] == "/dashboard/einstellungen/"
    assert client.get(reverse("dashboard:home")).status_code == 200


def test_code_works_only_once(client, guide_2fa):
    code = pyotp.TOTP(guide_2fa.totp_secret).now()
    login(client, guide_2fa)
    assert client.post(reverse("accounts:2fa_verify"), {"code": code}).status_code == 302
    client.logout()
    login(client, guide_2fa)
    assert client.post(reverse("accounts:2fa_verify"), {"code": code}).status_code == 200, "replay rejected"


def test_recovery_code_is_used_up(client, guide_2fa):
    code = guide_2fa.recovery_codes_plain[0]
    login(client, guide_2fa)
    assert client.post(reverse("accounts:2fa_verify"), {"code": code}).status_code == 302
    guide_2fa.refresh_from_db()
    assert len(guide_2fa.recovery_codes.split()) == 7
    client.logout()
    login(client, guide_2fa)
    assert client.post(reverse("accounts:2fa_verify"), {"code": code}).status_code == 200


def test_verify_rejects_open_redirect(client, guide_2fa):
    login(client, guide_2fa)
    code = pyotp.TOTP(guide_2fa.totp_secret).now()
    response = client.post(reverse("accounts:2fa_verify"), {"code": code, "next": "https://evil.example/"})
    assert response["Location"] == reverse("dashboard:home")


def test_staff_always_need_two_factor(client, make_guide):
    admin = make_guide("Samuel Admin", is_staff=True)
    client.force_login(admin)
    assert client.get(reverse("dashboard:home"))["Location"] == reverse("accounts:2fa_setup")


def test_two_factor_secret_is_encrypted(guide_2fa):
    raw = _raw("accounts_user", "totp_secret", guide_2fa.pk)
    assert raw.startswith("enc1:") and guide_2fa.totp_secret not in raw


# --- Tenant isolation -----------------------------------------------------------------------------


def test_guide_cannot_reach_other_guides_data(client, stripe_guide, other_guide, make_booking, make_departure,
                                              make_registration, tour):
    booking = make_booking(stripe_guide, in_days(10), guest_name="Vertraulich Muster")
    departure = make_departure(stripe_guide)
    registration = make_registration(departure, guest_name="Geheim Gast")
    client.force_login(other_guide)
    for url in (reverse("dashboard:booking", args=[booking.pk]), reverse("dashboard:booking", args=[registration.pk]),
                reverse("dashboard:invoice", args=[booking.pk]), reverse("dashboard:departure", args=[departure.pk]),
                reverse("dashboard:departure_edit", args=[departure.pk]), reverse("dashboard:tour_edit", args=[tour.pk])):
        assert client.get(url).status_code == 404, url
    for url in (reverse("dashboard:booking_action", args=[booking.pk, "reject"]),
                reverse("dashboard:booking_action", args=[registration.pk, "refund"]),
                reverse("dashboard:departure_action", args=[departure.pk, "cancel"]),
                reverse("dashboard:departure_action", args=[departure.pk, "confirm"]),
                reverse("dashboard:departure_action", args=[departure.pk, "nachricht-an-alle"]),
                reverse("dashboard:booking_message", args=[booking.pk]),
                reverse("dashboard:tour_delete", args=[tour.pk])):
        assert client.post(url).status_code == 404, url
    booking.refresh_from_db()
    departure.refresh_from_db()
    assert booking.status == S.REQUESTED and departure.status == "open"
    for url in (reverse("dashboard:home"), reverse("dashboard:departures"), reverse("dashboard:calendar")):
        html = client.get(url).content.decode()
        assert "Vertraulich" not in html and "Geheim" not in html and "Gran Paradiso" not in html


def test_guest_input_is_escaped_in_dashboard(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), guest_name="<script>alert(1)</script>",
                           guest_message="<img src=x onerror=alert(1)>")
    client.force_login(guide)
    for url in (reverse("dashboard:home"), reverse("dashboard:booking", args=[booking.pk])):
        html = client.get(url).content.decode()
        assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
        assert "&lt;script&gt;" in html


# --- Encryption at rest ------------------------------------------------------------------------------


def _raw(table, column, pk):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {table} WHERE id = %s", [pk])  # noqa: S608 - fixed names
        return cursor.fetchone()[0]


def test_sensitive_guest_fields_are_encrypted(stripe_guide, make_departure, make_registration):
    booking = make_registration(make_departure(stripe_guide))
    for column, plain in (("guest_phone", "+41 79 000 00 00"), ("emergency_contact", "Mama, 079 111 11 11"),
                          ("experience", "Mont Blanc 2024")):
        raw = _raw("bookings_booking", column, booking.pk)
        assert raw.startswith("enc1:") and plain not in raw, column
    booking.refresh_from_db()
    assert booking.emergency_contact == "Mama, 079 111 11 11"


def test_key_rotation_keeps_old_values_readable(stripe_guide, make_departure, make_registration, settings):
    from cryptography.fernet import Fernet

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEYS = [old]
    booking = make_registration(make_departure(stripe_guide))
    settings.FIELD_ENCRYPTION_KEYS = [new, old]
    booking.refresh_from_db()
    assert booking.experience == "Mont Blanc 2024"


def test_viewing_a_booking_is_logged(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    client.get(reverse("dashboard:booking", args=[booking.pk]))
    assert AccessLog.objects.filter(user=guide, action="view_booking", object_ref=f"booking:{booking.pk}").exists()


# --- Export and deletion need a fresh password -----------------------------------------------------------


def test_export_needs_reauth_and_contains_no_sensitive_data(client, stripe_guide, make_departure, make_registration):
    """R-203."""
    make_registration(make_departure(stripe_guide), seats=2)
    client.force_login(stripe_guide)
    url = reverse("dashboard:export")
    query = {"start": in_days(-1).isoformat(), "end": in_days(1).isoformat(), "format": "csv"}
    response = client.get(url, query)
    assert response.status_code == 302 and response["Location"].startswith(reverse("accounts:reauth"))
    assert client.post(reverse("accounts:reauth"), {"password": "falsch"}).status_code == 200
    client.post(reverse("accounts:reauth"), {"password": "sehr-geheimes-pw-123"})
    response = client.get(url, query)
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "Datum" and len(rows) == 2
    assert rows[1][3] == "Gran Paradiso" and rows[1][6] == "960.00"
    assert "079" not in response.content.decode() and "Mama" not in response.content.decode()
    assert AccessLog.objects.filter(action="export").exists()


def test_excel_export(client, stripe_guide, make_booking):
    make_booking(stripe_guide, in_days(-3), status=S.PAID, price=750, amount_paid=750, paid_at=timezone.now(),
                 invoiced_at=timezone.now(), payment_method="manual")
    client.force_login(stripe_guide)
    client.post(reverse("accounts:reauth"), {"password": "sehr-geheimes-pw-123"})
    response = client.get(reverse("dashboard:export"), {"start": in_days(-1).isoformat(), "end": in_days(1).isoformat(),
                                                         "format": "xlsx"})
    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet["D2"].value == "Eigene Tourenidee" and sheet["G2"].value == 750
    assert sheet["A3"].value == "Total"


def test_erase_guest_data_needs_reauth(client, stripe_guide, make_departure, make_registration):
    booking = make_registration(make_departure(stripe_guide))
    client.force_login(stripe_guide)
    url = reverse("dashboard:erase_guest_data")
    assert client.post(url, {"email": "gast@example.com"}).status_code == 302
    booking.refresh_from_db()
    assert booking.emergency_contact, "nothing deleted without a fresh password"
    client.post(reverse("accounts:reauth"), {"password": "sehr-geheimes-pw-123"})
    client.post(url, {"email": "gast@example.com"})
    booking.refresh_from_db()
    assert booking.emergency_contact == "" and AccessLog.objects.filter(action="erase").exists()


# --- Guest links ---------------------------------------------------------------------------------------


def test_guest_link_has_256_bits_and_expires_after_finished_tour(client, guide, make_booking, settings):
    booking = make_booking(guide, in_days(10))
    assert len(booking.token) >= 43
    old = make_booking(guide, in_days(-400), status=S.PAID, price=750)
    unpaid = make_booking(guide, in_days(-399), status=S.AWAITING_PAYMENT, price=750)
    assert client.get(reverse("guest:detail", args=[old.token])).status_code == 404
    assert client.get(reverse("guest:detail", args=[unpaid.token])).status_code == 200, "open bills stay reachable"


def test_guest_link_probing_is_rate_limited(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    for _ in range(120):
        client.get(reverse("guest:detail", args=["x"]))
    assert client.get(reverse("guest:detail", args=[booking.token])).status_code == 404


# --- Headers and legal pages ----------------------------------------------------------------------------


def test_dashboard_cannot_be_framed(client, guide):
    client.force_login(guide)
    response = client.get(reverse("dashboard:home"))
    assert response["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]


def test_widget_framing_restricted_to_guide_sites(client, guide):
    response = client.get(reverse("widget:show", args=[guide.slug]))
    assert "X-Frame-Options" not in response and "frame-ancestors *" in response["Content-Security-Policy"]
    guide.widget_allowed_origins = "https://www.anna-berg.ch"
    guide.save()
    response = client.get(reverse("widget:show", args=[guide.slug]))
    assert "frame-ancestors https://www.anna-berg.ch;" in response["Content-Security-Policy"]


@pytest.mark.parametrize("lang, title", [("de", "Datenschutzerklärung"), ("fr", "Déclaration de protection"),
                                         ("it", "Informativa sulla protezione"), ("en", "Privacy policy")])
@pytest.mark.parametrize("page", ["datenschutz", "impressum", "nutzungsbedingungen"])
def test_legal_pages_in_four_languages(client, db, lang, title, page):
    """R-001."""
    response = client.get(reverse("legal", args=[page]), {"lang": lang})
    assert response.status_code == 200
    if page == "datenschutz":
        assert title in response.content.decode()


def test_legal_links_everywhere(client, guide, make_booking):
    booking = make_booking(guide, in_days(10))
    client.force_login(guide)
    for url in (reverse("widget:show", args=[guide.slug]), reverse("guest:detail", args=[booking.token]),
                reverse("dashboard:home")):
        html = client.get(url).content.decode()
        for page in ("datenschutz", "impressum", "nutzungsbedingungen"):
            assert f"/rechtliches/{page}/" in html, (url, page)


def test_widget_language_from_browser_and_param(client, guide):
    """R-011."""
    url = reverse("widget:show", args=[guide.slug])
    assert '<html lang="fr">' in client.get(url, HTTP_ACCEPT_LANGUAGE="fr-CH,fr;q=0.9").content.decode()
    response = client.get(url, {"lang": "it"}, HTTP_ACCEPT_LANGUAGE="fr-CH")
    assert '<html lang="it">' in response.content.decode()
    assert response.cookies["tourchef_lang"].value == "it"
    client.cookies.clear()
    assert '<html lang="de">' in client.get(url, HTTP_ACCEPT_LANGUAGE="es").content.decode()


def test_widget_request_language_follows_widget(client, guide, mailoutbox, django_capture_on_commit_callbacks):
    from .test_views import widget_post

    with django_capture_on_commit_callbacks(execute=True):
        widget_post(client, guide, in_days(10), language="en")
    assert Booking.objects.get().language == "en"
    guest = next(m for m in mailoutbox if m.to == ["gina@example.com"])
    guide_mail = next(m for m in mailoutbox if m.to == [guide.email])
    assert guest.subject.startswith("Your request")
    assert guide_mail.subject.startswith("Neue Anfrage")
