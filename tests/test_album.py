"""Tour photo albums: guests and guide upload originals, download them, the guide keeps the best for the library."""

import io
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from PIL import Image

from bookings import album as albums
from bookings.models import Booking, Companion, Picture, TourPhoto

from .conftest import in_days

S = Booking.Status


@pytest.fixture(autouse=True)
def local_photos(settings, tmp_path):
    settings.PHOTO_BUCKET = ""
    settings.PHOTO_LOCAL_DIR = str(tmp_path)


def jpeg(gps=True) -> bytes:
    img = Image.new("RGB", (3000, 2000), (40, 120, 200))
    exif = Image.Exif()
    exif[0x0132] = "2026:11:10 09:30:00"  # DateTime
    if gps:
        exif[0x8825] = {1: "N", 2: (46.0, 38.0, 0.0), 3: "E", 4: (8.0, 35.0, 24.0)}  # Andermatt
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif, quality=90)
    return out.getvalue()


def upload(client, album, data: bytes, **extra):
    body = {"filename": "IMG_1.jpg", "type": "image/jpeg", "size": len(data), "name": "Gina", **extra}
    slot = client.post(reverse("album_start", args=[album.code]), json.dumps(body), content_type="application/json")
    assert slot.status_code == 200, slot.content
    slot = slot.json()
    put = client.generic("PUT", slot["url"], data, content_type="image/jpeg")
    assert put.status_code == 200
    return client.post(reverse("album_finish", args=[album.code]), json.dumps({"key": slot["key"]}),
                       content_type="application/json")


@pytest.fixture
def done_booking(guide, make_booking):
    return make_booking(guide, in_days(-2), end_date=in_days(-1), status=S.CONFIRMED, price=900, participants=3)


def test_guest_uploads_and_downloads_original(client, done_booking):
    album = albums.Album(done_booking)
    assert upload(client, album, jpeg()).status_code == 200
    photo = TourPhoto.objects.get()
    assert photo.status == TourPhoto.Status.READY and photo.uploaded_by == "Gina" and not photo.by_guide
    assert photo.width == 800 and round(photo.latitude, 2) == 46.63 and round(photo.longitude, 2) == 8.59
    assert photo.taken_at is not None and not photo.may_publish
    page = client.get(album.path)
    assert page.status_code == 200 and "storage.googleapis.com" in page["Content-Security-Policy"]
    assert reverse("album_preview", args=[album.code, photo.key]) in page.content.decode()
    original = client.get(client.get(reverse("album_original", args=[album.code, photo.key]))["Location"])
    assert b"".join(original.streaming_content) == jpeg()  # untouched original
    links = client.get(reverse("album_all", args=[album.code])).json()["links"]
    assert len(links) == 1


def test_not_an_image_is_refused_and_removed(client, done_booking):
    album = albums.Album(done_booking)
    response = upload(client, album, b"not a picture at all")
    assert response.status_code == 400 and not TourPhoto.objects.exists()


def test_wrong_type_and_too_big_refused(client, done_booking, settings):
    album = albums.Album(done_booking)
    url = reverse("album_start", args=[album.code])
    for body in ({"filename": "x.pdf", "type": "application/pdf", "size": 10},
                 {"filename": "x.jpg", "type": "image/jpeg", "size": settings.PHOTO_MAX_MB * 1024 * 1024 + 1}):
        assert client.post(url, json.dumps(body), content_type="application/json").status_code == 400


def test_album_closed_before_tour_and_for_wrong_code(client, guide, make_booking, done_booking):
    future = make_booking(guide, in_days(10), status=S.CONFIRMED, price=900, guest_email="f@example.org")
    assert client.get(albums.Album(future).path).status_code == 404
    code = albums.Album(done_booking).code
    assert client.get(reverse("album", args=[code[:-1] + "x"])).status_code == 404
    assert client.get(reverse("album", args=["d" + code[1:]])).status_code == 404


def test_guide_keeps_own_photos_but_guest_photos_only_with_consent(client, guide, done_booking):
    album = albums.Album(done_booking)
    upload(client, album, jpeg())  # guest, no consent
    upload(client, album, jpeg(), consent=True)  # guest, consent
    client.force_login(guide)
    upload(client, album, jpeg())  # guide
    without, with_consent, own = TourPhoto.objects.order_by("id")
    assert own.by_guide and own.uploaded_by == guide.display_name
    client.post(reverse("album_action", args=[album.code, "behalten"]),
                {"photo": [without.key, with_consent.key, own.key]})
    kept = set(TourPhoto.objects.filter(picture__isnull=False).values_list("pk", flat=True))
    assert kept == {with_consent.pk, own.pk}
    picture = Picture.objects.get(pk=own.__class__.objects.get(pk=own.pk).picture_id)
    assert picture.in_library and picture.guide == guide and picture.title == done_booking.title


def test_only_the_guide_keeps_deletes_and_shares(client, other_guide, done_booking):
    album = albums.Album(done_booking)
    upload(client, album, jpeg())
    photo = TourPhoto.objects.get()
    client.post(reverse("album_action", args=[album.code, "loeschen"]), {"photo": [photo.key]})
    assert TourPhoto.objects.exists()
    client.force_login(other_guide)
    assert client.post(reverse("album_action", args=[album.code, "loeschen"]),
                       {"photo": [photo.key]}).status_code == 404
    assert TourPhoto.objects.exists()


def test_guide_shares_link_by_email(client, guide, done_booking, mailoutbox):
    Companion.objects.create(booking=done_booking, name="Mia", email="mia@example.org")
    client.force_login(guide)
    client.post(reverse("album_action", args=[albums.Album(done_booking).code, "teilen"]))
    assert sorted(m.to[0] for m in mailoutbox) == sorted([done_booking.guest_email, "mia@example.org"])
    assert albums.Album(done_booking).url in mailoutbox[0].body


def test_cleanup_deletes_unkept_after_keep_days(client, guide, done_booking, settings, tmp_path):
    album = albums.Album(done_booking)
    client.force_login(guide)
    upload(client, album, jpeg())
    upload(client, album, jpeg())
    keep, drop = TourPhoto.objects.order_by("id")
    albums.keep(keep)
    assert albums.cleanup() == 0
    later = done_booking.end_date + timedelta(days=settings.PHOTO_KEEP_DAYS + 1)
    assert albums.cleanup(today=later) == 1
    assert list(TourPhoto.objects.values_list("pk", flat=True)) == [keep.pk]
    assert not (tmp_path / drop.object_key).exists() and (tmp_path / keep.object_key).exists()


def test_todo_reminds_to_upload_photos(client, guide, done_booking):
    client.force_login(guide)
    page = client.get(reverse("dashboard:home")).content.decode()
    assert "Fotos hochladen" in page and albums.Album(done_booking).path in page
    upload(client, albums.Album(done_booking), jpeg())
    assert albums.Album(done_booking).path not in client.get(reverse("dashboard:home")).content.decode()


def test_booking_and_guest_pages_link_the_album(client, guide, done_booking):
    path = albums.Album(done_booking).path
    assert path in client.get(reverse("guest:detail", args=[done_booking.token])).content.decode()
    client.force_login(guide)
    assert path in client.get(reverse("dashboard:booking", args=[done_booking.pk])).content.decode()
