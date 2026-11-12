"""Messages between guide and guest (email replies land in the dashboard), notifications top right, the picture
library, drafts, phone numbers with country code and the meeting time field."""

import hashlib
import hmac
import time

import pytest
from django.urls import reverse

from bookings import messaging, pictures
from bookings.forms import MeetingTimeField, PhoneField
from bookings.models import Booking, Draft, Message, Notification, Picture, TourCategory

from .conftest import in_days
from .test_offers import jpeg
from .test_views import widget_post

S = Booking.Status


@pytest.fixture
def mail_settings(settings):
    settings.REPLY_EMAIL_DOMAIN = "tourchef.test"
    settings.REPLY_EMAIL_LOCAL = "antwort"
    settings.MAILGUN_WEBHOOK_SIGNING_KEY = "key-test"
    settings.DEFAULT_FROM_EMAIL = "Tourchef <noreply@tourchef.test>"
    return settings


def signed(data, key="key-test", timestamp=None, token="tok-1"):
    timestamp = str(int(timestamp or time.time()))
    signature = hmac.new(key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
    return {**data, "timestamp": timestamp, "token": token, "signature": signature}


# --- Guide writes, guest replies -----------------------------------------------------------------


def test_guide_message_goes_out_with_reply_address(client, guide, make_booking, mailoutbox, mail_settings,
                                                   django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED)
    client.force_login(guide)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("dashboard:booking_message", args=[booking.pk]), {"msg-body": "Treffpunkt 7 Uhr"})
    assert response.status_code == 302
    mail = mailoutbox[0]
    assert mail.to == ["gast@example.com"]
    assert mail.from_email == "Anna Berg via Tourchef <noreply@tourchef.test>"
    assert mail.reply_to == [f"antwort+{booking.pk}{booking.reply_code}@tourchef.test"]
    assert "Treffpunkt 7 Uhr" in mail.body and booking.guest_url in mail.body
    assert booking.messages.get().email_message_id == mail.extra_headers["Message-ID"]


def test_without_reply_domain_replies_go_to_the_guide(guide, make_booking, mailoutbox, settings,
                                                      django_capture_on_commit_callbacks):
    settings.REPLY_EMAIL_DOMAIN = ""
    booking = make_booking(guide, in_days(10))
    with django_capture_on_commit_callbacks(execute=True):
        messaging.send_to_guest(booking, "Hallo")
    assert mailoutbox[0].reply_to == [guide.email]


def test_email_reply_lands_in_history_with_notification(client, guide, make_booking, mailoutbox, mail_settings,
                                                        django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10), status=S.CONFIRMED)
    address = messaging.reply_address(booking)
    body = "Super, bis dann!\n\nAm 3. Nov. 2026 schrieb Anna Berg:\n> Treffpunkt 7 Uhr"
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post("/eingang/mail/", signed({
            "recipient": address, "sender": "Gast@Example.com", "body-plain": body, "Message-Id": "<r1@mail.example>",
            "X-Mailgun-Spf": "Pass",
        }))
    assert response.status_code == 200
    message = booking.messages.get()
    assert (message.body, message.from_guest, message.needs_review) == ("Super, bis dann!", True, False)
    assert Notification.objects.get().kind == Notification.Kind.MESSAGE
    assert mailoutbox[0].to == [guide.email] and "Super, bis dann!" in mailoutbox[0].body
    # The mail service retries: no duplicate.
    client.post("/eingang/mail/", signed({"recipient": address, "sender": "gast@example.com", "body-plain": body,
                                          "Message-Id": "<r1@mail.example>"}, token="tok-2"))
    assert booking.messages.count() == 1


def test_inbound_mail_must_be_signed(client, guide, make_booking, mail_settings):
    booking = make_booking(guide, in_days(10))
    data = {"recipient": messaging.reply_address(booking), "sender": "gast@example.com", "body-plain": "x"}
    assert client.post("/eingang/mail/", data).status_code == 403
    assert client.post("/eingang/mail/", signed(data, key="wrong")).status_code == 403
    assert client.post("/eingang/mail/", signed(data, timestamp=time.time() - 3600)).status_code == 403
    assert client.post("/eingang/mail/", signed(data)).status_code == 200
    assert client.post("/eingang/mail/", signed(data)).status_code == 403, "a token works only once"
    assert booking.messages.count() == 1


def test_guessed_address_or_wrong_code_matches_nothing(guide, make_booking, mail_settings):
    booking = make_booking(guide, in_days(10))
    wrong = f"antwort+{booking.pk}zzzz@tourchef.test" if booking.reply_code != "zzzz" else f"antwort+{booking.pk}aaaa@x"
    assert messaging.receive_email(recipient=wrong, sender="gast@example.com", body="Hi") is None
    assert not Message.objects.exists()


def test_reply_from_other_address_needs_review(client, guide, make_booking, mail_settings):
    booking = make_booking(guide, in_days(10))
    message = messaging.receive_email(recipient=messaging.reply_address(booking), sender="fremd@example.org",
                                      body="Hallo")
    assert message.needs_review
    client.force_login(guide)
    html = client.get(reverse("dashboard:booking", args=[booking.pk])).content.decode()
    assert "fremd@example.org" in html
    client.post(reverse("dashboard:booking_message_action", args=[booking.pk, message.pk, "freigeben"]))
    message.refresh_from_db()
    assert not message.needs_review


def test_reply_matched_by_headers_when_address_is_lost(guide, make_booking, mail_settings,
                                                       django_capture_on_commit_callbacks):
    booking = make_booking(guide, in_days(10))
    with django_capture_on_commit_callbacks(execute=True):
        sent = messaging.send_to_guest(booking, "Hallo")
    message = messaging.receive_email(recipient="antwort@tourchef.test", sender="gast@example.com", body="Antwort",
                                      in_reply_to=sent.email_message_id)
    assert message.booking == booking


def test_message_to_everyone_signed_up(client, stripe_guide, make_departure, make_registration, mailoutbox,
                                       django_capture_on_commit_callbacks):
    departure = make_departure(stripe_guide)
    a = make_registration(departure, email="a@example.com")
    b = make_registration(departure, email="b@example.com")
    client.force_login(stripe_guide)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("dashboard:departure_action", args=[departure.pk, "nachricht-an-alle"]),
                    {"all-body": "Wetter sieht gut aus"})
    assert sorted(m.to[0] for m in mailoutbox) == ["a@example.com", "b@example.com"]
    assert a.messages.get().body == b.messages.get().body == "Wetter sieht gut aus"
    html = client.get(reverse("dashboard:departure", args=[departure.pk])).content.decode()
    assert "Nachricht an alle Angemeldeten" in html and "Nachricht an die Gäste" in html


# --- Notifications -------------------------------------------------------------------------------


def test_new_request_shows_top_right_until_opened(client, guide):
    widget_post(client, guide, in_days(10))
    booking = Booking.objects.get()
    client.force_login(guide)
    html = client.get(reverse("dashboard:home")).content.decode()
    assert "Du hast eine neue Anfrage: Gina Gast" in html
    client.get(reverse("dashboard:booking", args=[booking.pk]))
    assert "Du hast eine neue Anfrage" not in client.get(reverse("dashboard:home")).content.decode()


def test_paid_registration_notifies_new_booking(stripe_guide, make_departure, make_registration):
    from bookings import departures

    departure = make_departure(stripe_guide)
    booking = make_registration(departure, status=S.PAYMENT_PENDING)
    departures.registration_paid(booking, session_id="cs", payment_intent="pi", amount_cents=48000)
    note = Notification.objects.get()
    assert note.kind == Notification.Kind.BOOKING and note.text == "Du hast eine neue Buchung: Gast"


def test_notifications_are_private(client, guide, other_guide, make_booking):
    note = messaging.notify(guide, Notification.Kind.REQUEST, make_booking(guide, in_days(10)))
    client.force_login(other_guide)
    assert client.get(reverse("dashboard:notification", args=[note.pk])).status_code == 404


# --- Phone, time ---------------------------------------------------------------------------------


@pytest.mark.parametrize("code, number, expected", [
    ("41", "079 123 45 67", "+41 791234567"),
    ("49", "0171 1234567", "+49 1711234567"),
    ("39", "06 1234 5678", "+39 0612345678"),
    ("41", "+33 6 12 34 56 78", "+33 612345678"),
    ("41", "0044 7700 900123", "+44 7700900123"),
])
def test_phone_with_country_code(code, number, expected):
    assert PhoneField().clean([code, number]) == expected


def test_phone_too_short_is_refused():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        PhoneField().clean(["41", "079 1"])


@pytest.mark.parametrize("value", ["10:30", "10.30", "1030", "10h30", "10:30 Uhr"])
def test_meeting_time_accepts_common_spellings(value):
    assert MeetingTimeField().clean(value).strftime("%H:%M") == "10:30"


# --- Picture library -----------------------------------------------------------------------------


def test_library_upload_and_pick_for_template(client, guide):
    client.force_login(guide)
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.post(reverse("dashboard:pictures"), {
        "photos": [SimpleUploadedFile("a.jpg", jpeg(), "image/jpeg"), SimpleUploadedFile("b.jpg", jpeg(), "image/jpeg")],
    })
    assert Picture.objects.filter(guide=guide, in_library=True, category="").count() == 2
    assert "Zwischenablage" in client.get(reverse("dashboard:pictures")).content.decode()
    pic, extra = Picture.objects.all()
    client.post(reverse("dashboard:picture_action", args=[pic.key, "speichern"]),
                {"category": TourCategory.SKITOUR_DAY, "title": "Piz Lunghin", "region": "Engadin",
                 "place": "Maloja", "rating": "4", "note": "Morgenlicht"})
    pic.refresh_from_db()
    assert (pic.category, pic.title, pic.region, pic.place, pic.rating, pic.note) == (
        TourCategory.SKITOUR_DAY, "Piz Lunghin", "Engadin", "Maloja", 4, "Morgenlicht")
    # the archive is searchable by name, place, region and rating; the clipboard always shows
    found = client.get(reverse("dashboard:pictures"), {"q": "maloja"}).content.decode()
    assert f"bild-{pic.key}" in found and f"bild-{extra.key}" in found
    assert f"bild-{pic.key}" not in client.get(reverse("dashboard:pictures"), {"q": "eiger"}).content.decode()
    assert f"bild-{pic.key}" not in client.get(reverse("dashboard:pictures"), {"region": "Wallis"}).content.decode()
    resp = client.post(reverse("dashboard:pictures"), {"photos": [SimpleUploadedFile("c.jpg", jpeg(), "image/jpeg")]},
                       HTTP_ACCEPT="application/json")
    assert resp.json() == {"added": 1, "errors": []}
    stock = pictures.from_bytes(None, jpeg(), category=TourCategory.FREERIDE)
    html = client.get(reverse("dashboard:tour_new")).content.decode()
    assert pic.url in html and stock.url in html
    client.post(reverse("dashboard:tour_new"), {
        "title": "Lunghin", "duration_days": 1, "price_per_person": 200, "min_participants": 1, "max_participants": 4,
        "is_active": "on", "library_picture": stock.key,
    })
    assert guide.tours.get().picture == stock


def test_other_guides_pictures_cannot_be_picked(client, guide, other_guide):
    foreign = pictures.from_bytes(other_guide, jpeg(), in_library=True)
    client.force_login(guide)
    client.post(reverse("dashboard:tour_new"), {
        "title": "X", "duration_days": 1, "price_per_person": 200, "min_participants": 1, "max_participants": 4,
        "library_picture": foreign.key,
    })
    assert not guide.tours.filter(picture=foreign).exists()


def test_external_tour_uses_own_picture_of_its_kind(client, guide):
    from unittest import mock

    from bookings import pagetitle

    own = pictures.from_bytes(guide, jpeg(), in_library=True, category=TourCategory.SKITOUR_WEEK)
    info = pagetitle.PageInfo(title="Skitourenwoche Silvretta", provider="Bergschule", image="https://x.test/a.jpg",
                              dates=[(in_days(20), in_days(25))])
    client.force_login(guide)
    with mock.patch.object(pagetitle, "fetch", return_value=info), \
            mock.patch.object(pictures, "safe_get") as fetch_image:
        client.post(reverse("dashboard:external_tours"), {"ext-url": "https://bergschule.test/silvretta",
                                                          "ext-category": TourCategory.SKITOUR_WEEK,
                                                          "ext-show_to_guests": "on"})
    fetch_image.assert_not_called()
    assert guide.external_tours.get().picture == own


# --- Drafts --------------------------------------------------------------------------------------


def test_draft_is_saved_while_typing_and_resumed(client, stripe_guide):
    client.force_login(stripe_guide)
    url = reverse("dashboard:draft_save", args=["departure"])
    first = client.post(url, {"title": "Gross Fiescherhorn", "meeting_point": "Grindelwald"}).json()
    client.post(url, {"entwurf": first["id"], "title": "Gross Fiescherhorn", "meeting_point": "Grund"})
    draft = Draft.objects.get()
    assert draft.data["meeting_point"] == "Grund"
    assert "Gross Fiescherhorn" in client.get(reverse("dashboard:tours")).content.decode()
    html = client.get(f"{reverse('dashboard:departure_new')}?entwurf={draft.pk}").content.decode()
    assert 'value="Grund"' in html


def test_drafts_are_private(client, guide, other_guide):
    draft = Draft.objects.create(guide=other_guide, kind=Draft.Kind.DEPARTURE, data={"title": "Geheim"})
    client.force_login(guide)
    assert client.get(reverse("dashboard:draft", args=[draft.pk])).status_code == 404
    client.post(reverse("dashboard:draft_save", args=["departure"]), {"entwurf": draft.pk, "title": "Meins"})
    draft.refresh_from_db()
    assert draft.data["title"] == "Geheim"


# --- Guest page after paying -------------------------------------------------------------------


def test_no_equipment_list_after_payment(client, stripe_guide, make_departure, make_registration):
    booking = make_registration(make_departure(stripe_guide, equipment="Pickel und Steigeisen"))
    html = client.get(reverse("guest:detail", args=[booking.token])).content.decode()
    assert "Pickel und Steigeisen" not in html


def test_tour_gallery_up_to_ten_pictures(client, stripe_guide, other_guide, make_departure):
    from bookings.models import TourDetails

    guide = stripe_guide
    own = [pictures.from_bytes(guide, jpeg(), in_library=True, category=TourCategory.HIGH_TOUR)
           for _ in range(TourDetails.GALLERY_MAX + 1)]
    foreign = pictures.from_bytes(other_guide, jpeg(), in_library=True)
    client.force_login(guide)
    base = {"title": "Lunghin", "duration_days": 1, "price_per_person": 200, "min_participants": 1,
            "max_participants": 4, "library_picture": own[0].key}
    too_many = client.post(reverse("dashboard:tour_new"), {**base, "gallery": [p.key for p in own[1:]]})
    assert too_many.status_code == 200 and not guide.tours.exists()
    assert client.post(reverse("dashboard:tour_new"), {**base, "gallery": [foreign.key]}).status_code == 200
    client.post(reverse("dashboard:tour_new"), {**base, "gallery": [own[0].key, own[2].key, own[1].key]})
    tour = guide.tours.get()
    assert tour.gallery == [own[2].key, own[1].key]  # main picture not repeated, order kept
    # a scheduled date shows main picture plus gallery to guests, never another guide's picture
    departure = make_departure(guide, picture=own[0], gallery=[own[2].key, foreign.key, own[1].key])
    assert departure.gallery_pictures() == [own[0], own[2], own[1]]
    html = client.get(departure.public_url).content.decode()
    assert own[2].url in html and foreign.url not in html
