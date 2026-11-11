"""QA security probes (security & permissions). Each test documents either a confirmed weakness
(name starts with test_vuln_) or a check that passed (test_ok_)."""

import hashlib
import hmac
import io
import json
import time
from datetime import timedelta
from unittest import mock

import pyotp
import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import twofactor
from bookings import messaging
from bookings.models import (
    BlockedPeriod, Booking, Draft, ExternalTour, GuestProfile, Message, Notification, OpenPeriod, Partner,
    PartnerPeriod, Picture,
)

from .conftest import in_days
from .test_views import signup_post

S = Booking.Status
PW = "sehr-geheimes-pw-123"


def png_bytes():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (20, 20), (200, 10, 10)).save(out, "PNG")
    return out.getvalue()


# --- IDOR: every pk/key URL of the dashboard ------------------------------------------------------


@pytest.fixture
def victim_objects(guide, make_booking, make_departure, tour):
    booking = make_booking(guide, in_days(10), guest_name="Vertraulich")
    msg = Message.objects.create(booking=booking, direction=Message.Direction.FROM_GUEST, body="geheim",
                                 needs_review=True, sender_email="x@example.com")
    partner = Partner.objects.create(guide=guide, name="Bergschule A")
    return {
        "booking": booking,
        "message": msg,
        "departure": make_departure(guide),
        "tour": tour,
        "block": BlockedPeriod.objects.create(guide=guide, start_date=in_days(5), end_date=in_days(6)),
        "open": OpenPeriod.objects.create(guide=guide, start_date=in_days(5), end_date=in_days(6)),
        "partner": partner,
        "pperiod": PartnerPeriod.objects.create(partner=partner, start_date=in_days(20), end_date=in_days(22)),
        "ext": ExternalTour.objects.create(guide=guide, title="Ext", start_date=in_days(40), end_date=in_days(41),
                                           status=ExternalTour.Status.PENDING, partner=partner),
        "draft": Draft.objects.create(guide=guide, kind=Draft.Kind.DEPARTURE, data={"title": "GeheimEntwurf"}),
        "note": Notification.objects.create(guide=guide, kind=Notification.Kind.REQUEST, booking=booking),
        "picture": Picture.objects.create(guide=guide, data=b"x", in_library=True, title="Privatbild"),
    }


def test_ok_idor_all_dashboard_pk_urls(client, other_guide, victim_objects):
    o = victim_objects
    client.force_login(other_guide)
    b, d = o["booking"].pk, o["departure"].pk
    posts = [
        reverse("dashboard:booking_message_action", args=[b, o["message"].pk, "freigeben"]),
        reverse("dashboard:booking_message_action", args=[b, o["message"].pk, "loeschen"]),
        reverse("dashboard:booking_action", args=[b, "paid"]),
        reverse("dashboard:booking_action", args=[b, "ask_details"]),
        reverse("dashboard:invoice", args=[b]),
        reverse("dashboard:departure_edit", args=[d]),
        reverse("dashboard:departure_action", args=[d, "move"]),
        reverse("dashboard:tour_edit", args=[o["tour"].pk]),
        reverse("dashboard:block_delete", args=[o["block"].pk]),
        reverse("dashboard:open_delete", args=[o["open"].pk]),
        reverse("dashboard:partner_delete", args=[o["partner"].pk]),
        reverse("dashboard:partner_period_delete", args=[o["pperiod"].pk]),
        reverse("dashboard:external_tour_action", args=[o["ext"].pk, "annehmen"]),
        reverse("dashboard:external_tour_action", args=[o["ext"].pk, "loeschen"]),
        reverse("dashboard:draft_delete", args=[o["draft"].pk]),
        reverse("dashboard:picture_action", args=[o["picture"].key, "loeschen"]),
        reverse("dashboard:picture_action", args=[o["picture"].key, "speichern"]),
    ]
    for url in posts:
        assert client.post(url, {"title": "x"}).status_code == 404, url
    gets = [
        reverse("dashboard:draft", args=[o["draft"].pk]),
        reverse("dashboard:notification", args=[o["note"].pk]),
        reverse("dashboard:departure_new") + f"?vorlage={o['tour'].pk}",
    ]
    for url in gets:
        assert client.get(url).status_code == 404, url
    # everything still there and unchanged
    for key in ("block", "open", "partner", "pperiod", "draft", "picture", "message"):
        assert type(o[key]).objects.filter(pk=o[key].pk).exists(), key
    o["ext"].refresh_from_db()
    assert o["ext"].status == ExternalTour.Status.PENDING
    # foreign partner in the period form, foreign picture key in the template form
    client.post(reverse("dashboard:availability"), {"form": "partner_period", "pp-partner": o["partner"].pk,
                                                    "pp-start_date": in_days(50), "pp-end_date": in_days(51)})
    assert PartnerPeriod.objects.count() == 1
    client.post(reverse("dashboard:tour_new"), {"title": "Meins", "price_per_person": "100", "duration_days": 1,
                                                "min_participants": 1, "max_participants": 2,
                                                "library_picture": o["picture"].key})
    assert not Picture.objects.exclude(pk=o["picture"].pk).exists()
    for url in (reverse("dashboard:tours"), reverse("dashboard:availability"), reverse("dashboard:external_tours"),
                reverse("dashboard:pictures"), reverse("dashboard:guests")):
        html = client.get(url).content.decode()
        assert "GeheimEntwurf" not in html and "Bergschule A" not in html and o["picture"].key not in html, url


# --- Guest file leak through an unverified email address ---------------------------------------------


def test_vuln_guest_profile_leaks_to_anyone_booking_with_victims_email(client, stripe_guide, make_departure,
                                                                     make_registration):
    """GuestProfile is keyed by guide + email only. Whoever books with someone else's email address
    (never verified) gets the guest link straight back from Stripe's success_url and sees the victim's
    billing address, emergency contact and allergies, prefilled in the details form, and can overwrite them."""
    GuestProfile.objects.create(guide=stripe_guide, email="opfer@example.com", name="Opfer",
                                billing_address="Geheimweg 7, 3000 Bern", emergency_contact="Ehefrau 079 999 99 99",
                                allergies="HIV-Medikamente")
    departure = make_departure(stripe_guide)

    # 1) attacker signs up with the victim's address; success_url is the guest page
    session = mock.Mock(id="cs_att", url="https://checkout.stripe.test/cs_att")
    with mock.patch("stripe.checkout.Session.create", return_value=session) as create:
        response = signup_post(client, departure, guest_email="opfer@example.com", guest_name="Angreifer")
    assert response.status_code == 302
    booking = Booking.objects.get(guest_email="opfer@example.com")
    assert create.call_args.kwargs["success_url"] == f"{booking.guest_url}?bezahlt=1"

    # 2) after paying (refundable via free cancellation), the guest page shows the victim's file
    booking.status, booking.amount_paid, booking.paid_at = S.PAID, booking.price, timezone.now()
    booking.save()
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Geheimweg 7" in html and "079 999 99 99" in html and "HIV-Medikamente" in html

    # 3) and can overwrite it (the guide then sees the attacker's emergency contact)
    client.post(reverse("guest:details", args=[booking.token]), {
        "angaben-billing_address": "Fake", "angaben-emergency_contact": "Angreifer 079", "angaben-allergies": ""})
    assert GuestProfile.objects.get(email="opfer@example.com").emergency_contact == "Angreifer 079"


# --- Two-factor really enforced ----------------------------------------------------------------------


def test_ok_password_only_session_reaches_nothing_sensitive(client, guide, settings, make_booking):
    settings.REQUIRE_GUIDE_2FA = True
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    booking = make_booking(guide, in_days(10))
    client.post(reverse("accounts:login"), {"username": guide.email, "password": PW})
    assert "_auth_user_id" in client.session
    for method, url in (("get", reverse("dashboard:home")), ("get", reverse("dashboard:booking", args=[booking.pk])),
                        ("get", reverse("dashboard:export")), ("post", reverse("dashboard:settings")),
                        ("post", reverse("accounts:password_change")), ("post", reverse("accounts:reauth")),
                        ("get", reverse("payments:connect")), ("get", reverse("dashboard:guests")),
                        ("post", reverse("dashboard:erase_guest_data")), ("get", f"/{settings.ADMIN_URL}")):
        response = getattr(client, method)(url)
        assert response.status_code == 302 and "/konto/" in response["Location"], url


def test_ok_password_only_session_with_totp_must_verify(client, guide, settings):
    guide.totp_secret, guide.totp_enabled = pyotp.random_base32(), True
    guide.save()
    client.post(reverse("accounts:login"), {"username": guide.email, "password": PW})
    # setup page must not let a password-only session replace the existing TOTP secret
    response = client.get(reverse("accounts:2fa_setup"))
    assert response.status_code == 302 and response["Location"] == reverse("accounts:2fa_verify")


def test_fixed_totp_replacement_needs_password(client, guide):
    """Replacing the second factor needs password and current code again (a stolen session can't rebind it)."""
    guide.totp_secret, guide.totp_enabled = pyotp.random_base32(), True
    guide.save()
    client.force_login(guide)
    session = client.session
    session["2fa_ok"] = True
    session["reauth_at"] = 0
    session.save()
    response = client.get(reverse("accounts:2fa_setup"))
    assert response.status_code == 302 and response["Location"].startswith(reverse("accounts:reauth"))


def test_fixed_email_and_iban_change_need_password(client, guide):
    """Login email (password reset target) and IBAN (where invoices send money) change only with the password."""
    client.force_login(guide)
    data = {"display_name": guide.display_name, "email": "angreifer@evil.example", "phone": "", "language": "de",
            "slug": guide.slug, "widget_allowed_origins": "", "day_rate": "750", "aspirant_day_rate": "500",
            "iban": "CH93 0076 2011 6238 5295 7", "vat_number": "", "vat_rate": "8.1", "cancel_free_days": 14,
            "cancel_partial_days": 7, "cancel_partial_percent": 50, "guest_terms": "",
            "tiers-TOTAL_FORMS": 0, "tiers-INITIAL_FORMS": 0, "tiers-MIN_NUM_FORMS": 0, "tiers-MAX_NUM_FORMS": 6}
    response = client.post(reverse("dashboard:settings"), data)
    assert response.status_code == 200 and "current_password" in response.context["form"].errors
    guide.refresh_from_db()
    assert guide.email != "angreifer@evil.example" and not guide.iban.startswith("CH93")
    response = client.post(reverse("dashboard:settings"), {**data, "current_password": PW})
    assert response.status_code == 302
    guide.refresh_from_db()
    assert guide.email == "angreifer@evil.example" and guide.iban.startswith("CH93")


def test_ok_logout_needs_post(client, guide):
    client.force_login(guide)
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert "_auth_user_id" in client.session


def test_ok_pilot_stats_staff_only(client, guide, make_booking):
    make_booking(guide, in_days(5))
    client.force_login(guide)
    response = client.get(reverse("dashboard:pilot_stats"))
    assert response.status_code == 302 and "pilot" not in response["Location"].split("?")[0]
    final = client.get(response["Location"], follow=True)
    assert "n_requests" not in final.content.decode() and "Gast" not in final.content.decode()


def test_ok_login_rate_limit_not_bypassed_with_forwarded_for(client, guide, settings):
    settings.NUM_PROXIES = 0
    for i in range(10):
        client.post(reverse("accounts:login"), {"username": guide.email, "password": "falsch"},
                    HTTP_X_FORWARDED_FOR=f"10.0.0.{i}")
    client.post(reverse("accounts:login"), {"username": guide.email, "password": PW}, HTTP_X_FORWARDED_FOR="10.9.9.9")
    assert "_auth_user_id" not in client.session


def test_ok_signup_closed_post_creates_nobody(client, db, settings):
    from accounts.models import User

    settings.GUIDE_SIGNUP_CODE = ""
    client.post(reverse("accounts:signup"), {"display_name": "X", "email": "x@example.ch", "language": "de",
                                             "password1": "Gletscher-Spalte-77", "password2": "Gletscher-Spalte-77",
                                             "invite_code": "", "accept_terms": "on"})
    assert not User.objects.exists()


def test_fixed_erase_after_reauth_returns_to_settings(client, guide):
    """The password check after a guest-data erasure leads back to the settings page (not a GET on a POST-only
    view), with a note to send the form again."""
    client.force_login(guide)
    response = client.post(reverse("dashboard:erase_guest_data"), {"email": "gast@example.com"})
    assert "einstellungen" in response["Location"]
    response = client.post(response["Location"], {"password": PW, "next": reverse("dashboard:settings")})
    assert response["Location"] == reverse("dashboard:settings")
    assert client.get(response["Location"]).status_code == 200


# --- CSRF ---------------------------------------------------------------------------------------------


def test_ok_csrf_enforced_on_dashboard_and_guest_posts(guide, victim_objects):
    c = Client(enforce_csrf_checks=True)
    c.force_login(guide)
    o = victim_objects
    for url in (reverse("dashboard:booking_action", args=[o["booking"].pk, "reject"]),
                reverse("dashboard:booking_message", args=[o["booking"].pk]),
                reverse("dashboard:settings"), reverse("dashboard:availability"),
                reverse("dashboard:block_delete", args=[o["block"].pk]),
                reverse("dashboard:picture_action", args=[o["picture"].key, "loeschen"]),
                reverse("dashboard:draft_save", args=["tour"]), reverse("dashboard:notifications_read"),
                reverse("dashboard:pictures"), reverse("accounts:password_change"), reverse("accounts:logout"),
                reverse("guest:action", args=[o["booking"].token, "stornieren"]),
                reverse("guest:message", args=[o["booking"].token]),
                reverse("guest:details", args=[o["booking"].token]),
                reverse("guest:pay", args=[o["booking"].token]),
                reverse("widget:partner", args=[o["partner"].token])):
        assert c.post(url, {"x": "1"}).status_code == 403, url


def test_ok_state_changing_views_reject_get(client, guide, victim_objects):
    client.force_login(guide)
    o = victim_objects
    for url in (reverse("dashboard:booking_action", args=[o["booking"].pk, "paid"]),
                reverse("dashboard:block_delete", args=[o["block"].pk]),
                reverse("dashboard:partner_delete", args=[o["partner"].pk]),
                reverse("dashboard:draft_delete", args=[o["draft"].pk]),
                reverse("dashboard:picture_action", args=[o["picture"].key, "loeschen"]),
                reverse("dashboard:calendar_sync"), reverse("dashboard:erase_guest_data"),
                reverse("guest:action", args=[o["booking"].token, "stornieren"]),
                reverse("payments:webhook"), reverse("inbound_mail")):
        assert client.get(url).status_code == 405, url


def test_fixed_stripe_connect_starts_only_by_post(client, guide, settings):
    """Creating the Stripe account takes a POST from the settings page; a cross-site GET does nothing."""
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    client.force_login(guide)
    with mock.patch("stripe.Account.create", return_value=mock.Mock(id="acct_new")) as create, \
            mock.patch("stripe.AccountLink.create", return_value=mock.Mock(url="https://connect.stripe.test/x")):
        response = client.get(reverse("payments:connect"), HTTP_REFERER="https://evil.example/")
        assert response.status_code == 302 and not create.called
        response = client.post(reverse("payments:connect"))
        assert response.status_code == 302 and create.called
    guide.refresh_from_db()
    assert guide.stripe_account_id == "acct_new"


# --- Open redirects ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["//evil.example/", "https://evil.example/", "/\\evil.example",
                                    "\\\\evil.example", "https:evil.example", " //evil.example"])
def test_ok_no_open_redirect(client, guide, target):
    client.force_login(guide)
    loc = client.post(reverse("dashboard:notifications_read"), {"next": target})["Location"]
    assert "evil" not in loc or loc.startswith("/dashboard/"), loc
    loc = client.post(reverse("accounts:reauth"), {"password": PW, "next": target})["Location"]
    assert "evil" not in loc, loc
    client.logout()
    client.post(reverse("accounts:login") + "?next=" + target, {"username": guide.email, "password": PW})
    resp = client.post(reverse("accounts:login"), {"username": guide.email, "password": PW, "next": target})
    assert "evil" not in resp.get("Location", ""), resp.get("Location")


def test_ok_notifications_next_dashboard_prefix_stays_local(client, guide):
    client.force_login(guide)
    loc = client.post(reverse("dashboard:notifications_read"), {"next": "/dashboard/..//evil.example"})["Location"]
    assert loc.startswith("/dashboard/")


# --- XSS ------------------------------------------------------------------------------------------------------

XSS = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'


def test_ok_xss_escaped_everywhere(client, guide, make_booking, make_departure):
    booking = make_booking(guide, in_days(10), guest_name=XSS, guest_message=XSS, status=S.OFFERED)
    Message.objects.create(booking=booking, direction=Message.Direction.FROM_GUEST, body=XSS)
    Message.objects.create(booking=booking, direction=Message.Direction.FROM_GUEST, body=XSS, needs_review=True,
                           sender_email="a@b.ch")
    Notification.objects.create(guide=guide, kind=Notification.Kind.MESSAGE, booking=booking)
    departure = make_departure(guide, title=XSS, description=XSS, meeting_point=XSS, equipment=XSS)
    partner = Partner.objects.create(guide=guide, name=XSS)
    ExternalTour.objects.create(guide=guide, title=XSS, provider=XSS, note=XSS, start_date=in_days(60),
                                end_date=in_days(60), url="https://ok.example/", partner=partner)
    Picture.objects.create(guide=guide, data=b"x", in_library=True, title=XSS)
    guide.guest_terms = XSS
    guide.display_name = XSS
    guide.save()
    client.force_login(guide)
    urls = [reverse("dashboard:home"), reverse("dashboard:booking", args=[booking.pk]),
            reverse("dashboard:departure", args=[departure.pk]), reverse("dashboard:departures"),
            reverse("dashboard:availability"), reverse("dashboard:external_tours"), reverse("dashboard:pictures"),
            reverse("dashboard:calendar"), reverse("dashboard:settings"),
            reverse("guest:detail", args=[booking.token]), reverse("widget:show", args=[guide.slug]),
            reverse("widget:departure", args=[departure.public_id]), reverse("widget:terms", args=[guide.slug]),
            reverse("widget:partner", args=[partner.token])]
    for url in urls:
        html = client.get(url).content.decode()
        assert "<script>alert(1)" not in html and "<img src=x" not in html, url


def test_ok_upload_filename_escaped_and_svg_rejected(client, guide):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(guide)
    svg = SimpleUploadedFile('<img src=x onerror=alert(1)>.svg',
                             b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                             content_type="image/svg+xml")
    html = client.post(reverse("dashboard:pictures"), {"photos": [svg]}, follow=True).content.decode()
    assert "<img src=x" not in html and "&lt;img src=x" in html
    assert not Picture.objects.filter(guide=guide).exists()
    # polyglot: valid PNG with HTML appended is re-encoded, served as JPEG with nosniff
    ok = SimpleUploadedFile("a.png", png_bytes() + b"<script>alert(1)</script>", content_type="image/png")
    client.post(reverse("dashboard:pictures"), {"photos": [ok]})
    pic = Picture.objects.get(guide=guide)
    response = client.get(reverse("widget:picture", args=[pic.key]))
    assert response["Content-Type"] == "image/jpeg" and response["X-Content-Type-Options"] == "nosniff"
    assert b"<script>" not in response.content and response.content[:2] == b"\xff\xd8"
    assert client.get("/bild/..%2F..%2Fetc%2Fpasswd.jpg").status_code == 404


# --- Guest / partner tokens ---------------------------------------------------------------------------------


def test_ok_tokens_random_and_independent(guide, make_booking):
    tokens = {make_booking(guide, in_days(10 + i)).token for i in range(5)}
    assert len(tokens) == 5 and all(len(t) >= 43 for t in tokens)
    partner = Partner.objects.create(guide=guide, name="P")
    assert len(partner.token) >= 43


def test_ok_partner_link_dies_with_partner_and_shows_nothing_else(client, guide, make_booking):
    make_booking(guide, in_days(21), guest_name="Vertraulich Gast", status=S.CONFIRMED)
    partner = Partner.objects.create(guide=guide, name="P")
    PartnerPeriod.objects.create(partner=partner, start_date=in_days(20), end_date=in_days(22))
    html = client.get(reverse("widget:partner", args=[partner.token])).content.decode()
    assert "Vertraulich" not in html
    partner.delete()
    assert client.get(reverse("widget:partner", args=[partner.token])).status_code == 404


def test_fixed_erasure_removes_guest_texts(client, guide, make_booking):
    """R-152 erasure also removes the request text and the guest's messages."""
    booking = make_booking(guide, in_days(10), guest_message="Ich habe Epilepsie", status=S.CONFIRMED,
                           guest_phone="+41 79 111 22 33")
    Message.objects.create(booking=booking, direction=Message.Direction.FROM_GUEST, body="Meine Adresse: Gasse 1")
    client.force_login(guide)
    client.post(reverse("accounts:reauth"), {"password": PW})
    client.post(reverse("dashboard:erase_guest_data"), {"email": "gast@example.com"})
    booking.refresh_from_db()
    assert booking.guest_phone == "" and booking.guest_message == ""
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Gasse 1" not in html and "Epilepsie" not in html


# --- Webhooks -------------------------------------------------------------------------------------------------


def stripe_sig(payload: bytes, secret="whsec_test", ts=None):
    ts = int(ts or time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_ok_stripe_webhook_signature_and_replay(client, guide, make_booking):
    booking = make_booking(guide, in_days(10), status=S.AWAITING_PAYMENT, price=600)
    event = {"id": "evt_q1", "object": "event", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "object": "checkout.session", "payment_status": "paid", "currency": "chf",
        "amount_total": booking.amount_cents, "payment_intent": "pi_1", "metadata": {"booking_id": str(booking.pk)}}}}
    payload = json.dumps(event).encode()
    url = reverse("payments:webhook")
    assert client.post(url, payload, content_type="application/json",
                       HTTP_STRIPE_SIGNATURE=stripe_sig(payload, "whsec_wrong")).status_code == 400
    assert client.post(url, payload, content_type="application/json",
                       HTTP_STRIPE_SIGNATURE=stripe_sig(payload, ts=time.time() - 3600)).status_code == 400
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert client.post(url, payload, content_type="application/json",
                       HTTP_STRIPE_SIGNATURE=stripe_sig(payload)).status_code == 200
    booking.refresh_from_db()
    assert booking.status == S.PAID


def test_fixed_checkout_from_connected_account_is_ignored(client, stripe_guide, make_booking):
    """A checkout event from a connected account (not made by Tourchef) never marks a booking paid."""
    booking = make_booking(stripe_guide, in_days(10), status=S.AWAITING_PAYMENT, price=600,
                           stripe_checkout_session_id="cs_real")
    event = {"id": "evt_q2", "object": "event", "type": "checkout.session.completed", "account": "acct_attacker",
             "data": {"object": {"id": "cs_other", "object": "checkout.session", "payment_status": "paid",
                                 "currency": "chf", "amount_total": booking.amount_cents, "payment_intent": "pi_x",
                                 "metadata": {"booking_id": str(booking.pk)}}}}
    payload = json.dumps(event).encode()
    assert client.post(reverse("payments:webhook"), payload, content_type="application/json",
                       HTTP_STRIPE_SIGNATURE=stripe_sig(payload)).status_code == 200
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


@pytest.fixture
def mail_settings(settings):
    settings.REPLY_EMAIL_DOMAIN = "tourchef.test"
    settings.REPLY_EMAIL_LOCAL = "antwort"
    settings.MAILGUN_WEBHOOK_SIGNING_KEY = "key-test"
    return settings


def signed(data, token, key="key-test", timestamp=None):
    timestamp = str(int(timestamp or time.time()))
    signature = hmac.new(key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
    return {**data, "timestamp": timestamp, "token": token, "signature": signature}


def test_ok_inbound_mail_signature_replay_stale(client, guide, make_booking, mail_settings):
    booking = make_booking(guide, in_days(10))
    data = {"recipient": messaging.reply_address(booking), "sender": "gast@example.com", "stripped-text": "hallo"}
    url = reverse("inbound_mail")
    assert client.post(url, data).status_code == 403
    assert client.post(url, signed(data, "t1", key="falsch")).status_code == 403
    assert client.post(url, signed(data, "t2", timestamp=time.time() - 3600)).status_code == 403
    assert client.post(url, signed(data, "t3")).status_code == 200
    assert client.post(url, signed(data, "t3")).status_code == 403, "token replay"
    mail_settings.MAILGUN_WEBHOOK_SIGNING_KEY = ""
    assert client.post(url, signed(data, "t4", key="")).status_code == 403


def test_fixed_inbound_sender_must_pass_spf_or_dkim(client, guide, make_booking, mail_settings):
    """A reply whose sender Mailgun could not verify (SPF/DKIM) goes to the guide for review, never to the guest
    page; a verified one lands in the history."""
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED)
    data = {"recipient": messaging.reply_address(booking), "sender": "Gast <gast@example.com>",
            "from": "Gast <gast@example.com>", "stripped-text": "Bitte zahlen Sie auf IBAN CH00 EVIL"}
    assert client.post(reverse("inbound_mail"), signed({**data, "X-Mailgun-Spf": "Fail"}, "s1")).status_code == 200
    assert Message.objects.get(booking=booking).needs_review is True
    assert "CH00 EVIL" not in client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    data["stripped-text"] = "Bis morgen"
    client.post(reverse("inbound_mail"), signed({**data, "X-Mailgun-Dkim-Check-Result": "Pass"}, "s2"))
    assert Message.objects.get(booking=booking, body="Bis morgen").needs_review is False


def test_ok_inbound_wrong_code_or_other_booking_matches_nothing(client, guide, other_guide, make_booking,
                                                                mail_settings):
    mine = make_booking(guide, in_days(10))
    other = make_booking(other_guide, in_days(11))
    wrong = messaging.reply_address(other)[:-len("@tourchef.test")]
    wrong = wrong[:-1] + ("a" if wrong[-1] != "a" else "b") + "@tourchef.test"
    data = {"recipient": wrong, "sender": "gast@example.com", "stripped-text": "x", "In-Reply-To": "<nope@x>"}
    assert client.post(reverse("inbound_mail"), signed(data, "w1")).status_code == 200
    assert not Message.objects.exists()
    # headers of a foreign booking's guest message can't be guessed; own guest-chosen ids only map to own booking
    data = {"recipient": messaging.reply_address(mine), "sender": "gast@example.com", "stripped-text": "a",
            "Message-Id": "<chosen@att>"}
    client.post(reverse("inbound_mail"), signed(data, "w2"))
    assert Message.objects.get().booking == mine
