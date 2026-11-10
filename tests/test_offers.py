"""Reading a tour page from its link, tour pictures, offers as tiles in the guest calendar and the grouped menu."""

import io
from datetime import date
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from bookings import pagetitle, pictures
from bookings.models import ExternalTour, Picture, Tour

from .conftest import in_days

TODAY = date(2026, 11, 5)


def jpeg(width=1600, height=900) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(out, "JPEG")
    return out.getvalue()


# --- Dates on a tour page ---


@pytest.mark.parametrize("text, expected", [
    ("Termin: 12.–14.03.2027", (date(2027, 3, 12), date(2027, 3, 14))),
    ("Sa 12.03. – So 13.03.2027", (date(2027, 3, 12), date(2027, 3, 13))),
    ("12. bis 14. März 2027", (date(2027, 3, 12), date(2027, 3, 14))),
    ("du 3 au 5 juillet 2027", (date(2027, 7, 3), date(2027, 7, 5))),
    ("Dal 7 al 9 agosto 2027", (date(2027, 8, 7), date(2027, 8, 9))),
    ("28.12. - 3.1.2027", (date(2026, 12, 28), date(2027, 1, 3))),
    ("Datum: 20. Juni 2027", (date(2027, 6, 20), date(2027, 6, 20))),
    ("am 2027-02-01", (date(2027, 2, 1), date(2027, 2, 1))),
])
def test_dates_in_text(text, expected):
    assert pagetitle.dates_in_text(text, TODAY) == [expected]


def test_vague_numbers_are_not_dates():
    assert pagetitle.dates_in_text("Version 2.1. – Tel. 12.3. – CHF 1.250.–", TODAY) == []


def test_structured_event_data_wins_over_text():
    page = (b'<html><head><title>Tour</title><meta property="og:image" content="/img/palue.jpg">'
            b'<script type="application/ld+json">{"@type": "Event", "name": "Piz Pal\u00fc", '
            b'"startDate": "2027-07-01T07:00", "endDate": "2027-07-02"}</script></head>'
            b'<body><p>Gedruckt am 03.08.2027</p></body></html>')
    info = pagetitle.parse(page, "https://bt.example.ch/touren/palue", TODAY)
    assert info.dates == [(date(2027, 7, 1), date(2027, 7, 2))]
    assert info.image == "https://bt.example.ch/img/palue.jpg"


def test_past_dates_and_scripts_are_ignored():
    page = b"<p>Letztes Jahr: 3.8.2025</p><script>var d='12.03.2027';</script><p>Neu: 1.9.2027</p>"
    assert pagetitle.parse(page, today=TODAY).dates == [(date(2027, 9, 1), date(2027, 9, 1))]


# --- Pasting only the link ---


def paste(client, url="https://bt.example.ch/palue", **extra):
    data = {"ext-url": url, "ext-title": "", "ext-provider": "", "ext-start_date": "", "ext-end_date": "",
            "ext-show_to_guests": "on", "ext-note": "", **extra}
    return client.post(reverse("dashboard:external_tours"), data)


def test_link_alone_fills_title_dates_and_logo(client, guide):
    """The provider's photo is theirs: without an own picture the tour shows the provider's logo on a white tile."""
    client.force_login(guide)
    info = pagetitle.PageInfo("Piz Palü", "Bergschule B+T", "https://bt.example.ch/p.jpg", [(in_days(10), in_days(11))],
                              logo="https://bt.example.ch/logo.png")
    with mock.patch.object(pagetitle, "fetch", return_value=info),             mock.patch.object(pictures, "safe_get", return_value=jpeg(200, 100)) as fetch_image:
        resp = paste(client)
    assert resp.status_code == 302
    fetch_image.assert_called_once_with("https://bt.example.ch/logo.png", pictures.MAX_UPLOAD_BYTES)
    tour = ExternalTour.objects.get()
    assert (tour.title, tour.provider, tour.start_date, tour.end_date) == ("Piz Palü", "Bergschule B+T", in_days(10),
                                                                           in_days(11))
    assert (tour.picture.width, tour.picture.height) == pictures.LOGO_TILE
    assert tour.is_public


def test_link_with_own_upload_keeps_the_upload(client, guide):
    client.force_login(guide)
    info = pagetitle.PageInfo("Piz Palü", "B+T", "", [(in_days(10), in_days(11))], logo="https://bt.example.ch/l.png")
    upload = SimpleUploadedFile("mein.jpg", jpeg(1600, 1200), content_type="image/jpeg")
    with mock.patch.object(pagetitle, "fetch", return_value=info),             mock.patch.object(pictures, "safe_get") as fetch_image:
        resp = paste(client, **{"ext-photo": upload})
    assert resp.status_code == 302
    fetch_image.assert_not_called()
    pic = ExternalTour.objects.get().picture
    assert (pic.width, pic.height, pic.in_library) == (1200, 900, True)


def test_logo_link_is_read_from_the_page():
    page = (b'<html><head><link rel="icon" href="/favicon.ico"><link rel="apple-touch-icon" href="/touch.png">'
            b'<title>Kurs</title></head></html>')
    assert pagetitle.parse(page, "https://schule.example.ch/kurs").logo == "https://schule.example.ch/touch.png"


def test_several_dates_on_page_let_the_guide_pick(client, guide):
    client.force_login(guide)
    info = pagetitle.PageInfo("Biancograt", "B+T", "", [(in_days(10), in_days(11)), (in_days(20), in_days(21))])
    with mock.patch.object(pagetitle, "fetch", return_value=info):
        resp = paste(client)
        assert resp.status_code == 200 and not ExternalTour.objects.exists()
        html = resp.content.decode()
        value = f"{in_days(20):%Y-%m-%d}:{in_days(21):%Y-%m-%d}"
        assert f'name="ext-pick" value="{value}"' in html
        assert 'value="Biancograt"' in html and 'name="ext-picture_key" value="none"' in html
        resp = paste(client, **{"ext-title": "Biancograt", "ext-provider": "B+T", "ext-picture_key": "none",
                                "ext-pick": value})
    assert resp.status_code == 302
    tour = ExternalTour.objects.get()
    assert (tour.start_date, tour.end_date, tour.picture) == (in_days(20), in_days(21), None)


def test_no_date_on_page_asks_for_it(client, guide):
    client.force_login(guide)
    with mock.patch.object(pagetitle, "fetch", return_value=pagetitle.PageInfo("Piz Palü", "B+T")):
        resp = paste(client)
    assert resp.status_code == 200 and not ExternalTour.objects.exists()
    assert "kein Datum gefunden" in resp.content.decode()


# --- Pictures ---


def test_uploaded_picture_is_shrunk_and_served(client, guide):
    client.force_login(guide)
    upload = SimpleUploadedFile("berg.png", jpeg(3000, 2000), content_type="image/png")
    resp = client.post(reverse("dashboard:tour_new"), {
        "title": "Gran Paradiso", "duration_days": 2, "price_per_person": "480", "min_participants": 2,
        "max_participants": 4, "is_active": "on", "photo": upload,
    })
    assert resp.status_code == 302
    pic = Tour.objects.get().picture
    assert (pic.width, pic.height) == (1200, 800)
    client.logout()
    served = client.get(pic.url)
    assert served["Content-Type"] == "image/jpeg" and served.content[:2] == b"\xff\xd8"


def test_non_image_upload_is_refused(client, guide):
    client.force_login(guide)
    upload = SimpleUploadedFile("x.svg", b"<svg onload=alert(1)>", content_type="image/svg+xml")
    resp = client.post(reverse("dashboard:tour_new"), {
        "title": "x", "duration_days": 1, "price_per_person": "1", "min_participants": 1, "max_participants": 2,
        "photo": upload,
    })
    assert resp.status_code == 200 and not Tour.objects.exists() and not Picture.objects.exists()


def test_departure_from_template_keeps_its_picture(client, stripe_guide):
    pic = pictures.from_bytes(stripe_guide, jpeg())
    tour = Tour.objects.create(guide=stripe_guide, title="Gran Paradiso", price_per_person=480, picture=pic)
    client.force_login(stripe_guide)
    start = in_days(30)
    resp = client.post(reverse("dashboard:departure_new") + f"?vorlage={tour.pk}", {
        "vorlage": tour.pk, "title": "Gran Paradiso", "start_date": start, "end_date": start,
        "price_per_person": "480", "min_participants": 2, "max_participants": 4,
        "signup_deadline": f"{in_days(27):%Y-%m-%d}T18:00", "decision_deadline": f"{in_days(28):%Y-%m-%d}T18:00",
        "meeting_point": "Pont", "meeting_time": "08:00",
    })
    assert resp.status_code == 302
    assert stripe_guide.departures.get().picture == pic


# --- Guest calendar ---


def test_offers_are_tiles_with_title_and_picture(client, guide, make_departure):
    pic = pictures.from_bytes(guide, jpeg())
    make_departure(guide, in_days(30), title="Gran Paradiso", picture=pic)
    ExternalTour.objects.create(guide=guide, title="Biancograt", provider="B+T", url="https://bt.example.ch/b",
                                start_date=in_days(31), end_date=in_days(31))
    resp = client.get(reverse("widget:show", args=[guide.slug]) + f"?monat={in_days(30):%Y-%m}")
    html = resp.content.decode()
    assert 'class="day tile' in html and f'src="{pic.url}"' in html
    assert 'href="https://bt.example.ch/b"' in html and "Biancograt" in html
    assert html.index("Als Nächstes") < html.index('id="request-title"')
    assert html.count('class="card offer-card') == 1 + 2, "teaser plus the full list below the calendar"


def test_hidden_or_linkless_external_tours_stay_blocked(client, guide):
    ExternalTour.objects.create(guide=guide, title="Privatkunde", url="https://bt.example.ch/p",
                                start_date=in_days(31), end_date=in_days(31), show_to_guests=False)
    ExternalTour.objects.create(guide=guide, title="Ohne Link", start_date=in_days(32), end_date=in_days(32))
    html = client.get(reverse("widget:show", args=[guide.slug]) + f"?monat={in_days(31):%Y-%m}").content.decode()
    assert "Privatkunde" not in html and "Ohne Link" not in html and "Als Nächstes" not in html


def test_guide_can_hide_an_external_tour(client, guide):
    tour = ExternalTour.objects.create(guide=guide, title="x", url="https://bt.example.ch/x", start_date=in_days(5),
                                       end_date=in_days(5))
    client.force_login(guide)
    client.post(reverse("dashboard:external_tour_action", args=[tour.pk, "verbergen"]))
    tour.refresh_from_db()
    assert not tour.is_public


def test_partner_offers_start_hidden(client, guide):
    from bookings.models import Partner, PartnerPeriod
    from bookings import partners
    p = Partner.objects.create(guide=guide, name="B+T")
    PartnerPeriod.objects.create(partner=p, start_date=in_days(20), end_date=in_days(30))
    with mock.patch("bookings.emails.partner_offer_to_guide"):
        tour = partners.create_offer(p, title="x", url="https://bt.example.ch/x", start_date=in_days(21),
                                     end_date=in_days(21), contact_name="", contact_email="", note="", language="de")
    assert not tour.show_to_guests


# --- Menu ---


def test_menu_groups_pages_in_four_sections(client, guide):
    client.force_login(guide)
    html = client.get(reverse("dashboard:availability")).content.decode()
    for label in ("Meine Touren", "Kalender", "Buchhaltung", "Einstellungen"):
        assert label in html
    assert 'class="subnav"' in html and "Externe Anbieter" in html
    assert f'href="{reverse("dashboard:availability")}" class="active"' in html
    assert ">Export<" not in html and ">Vorlagen<" not in html
