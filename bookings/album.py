"""Photo albums after a tour (questionnaire on pictures, 2026-11-18).

Every confirmed custom booking and every scheduled date has an album. Its secret link lets the guide and the guests
upload originals and download them in full quality; the guide marks the photos to keep, which go into the picture
library (that's what the guide gets out of it). Photos nobody kept are deleted PHOTO_KEEP_DAYS after the tour."""

import io
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext as _
from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

from . import photostore, pictures
from .models import Booking, Departure, Picture, TourPhoto

PREVIEW_SIDE = 800
MAX_PHOTOS = 500  # per album
MAX_PIXELS = 80_000_000  # big phone sensors; still refuses decompression bombs
CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
BOOKED = (Booking.Status.CONFIRMED, Booking.Status.AWAITING_PAYMENT, Booking.Status.PAID)


class AlbumError(Exception):
    pass


class Album:
    """The album of a custom booking (kind "b") or of a scheduled date (kind "d")."""

    def __init__(self, owner):
        self.owner = owner
        self.kind = "d" if isinstance(owner, Departure) else "b"
        self.guide = owner.guide

    @classmethod
    def for_code(cls, code: str):
        """The album behind a link code ("b12-abc…"), or None."""
        try:
            prefix, secret = code.split("-", 1)
            kind, pk = prefix[0], int(prefix[1:])
        except (ValueError, IndexError):
            return None
        model = {"b": Booking, "d": Departure}.get(kind)
        owner = model and model.objects.select_related("guide").filter(pk=pk).first()
        if owner is None:
            return None
        album = cls(owner)
        return album if constant_time_compare(secret, album.secret) else None

    @property
    def secret(self) -> str:
        return salted_hmac("album", f"{self.kind}{self.owner.pk}").hexdigest()[:24]

    @property
    def code(self) -> str:
        return f"{self.kind}{self.owner.pk}-{self.secret}"

    @property
    def path(self) -> str:
        return reverse("album", args=[self.code])

    @property
    def url(self) -> str:
        return settings.SITE_URL + self.path

    @property
    def title(self) -> str:
        return self.owner.title

    @property
    def start_date(self):
        return self.owner.start_date

    @property
    def end_date(self):
        return self.owner.end_date

    @property
    def took_place(self) -> bool:
        if self.kind == "d":
            happening = self.owner.status == Departure.Status.CONFIRMED
        else:
            happening = self.owner.status in BOOKED
        return happening and self.start_date <= timezone.localdate()

    @property
    def delete_on(self):
        return self.end_date + timedelta(days=settings.PHOTO_KEEP_DAYS)

    @property
    def is_open(self) -> bool:
        """Uploads and downloads: from the first tour day until the photos are deleted."""
        return self.took_place and timezone.localdate() <= self.delete_on

    def photos(self):
        field = "departure" if self.kind == "d" else "booking"
        return TourPhoto.objects.filter(**{field: self.owner}).defer("preview")

    def ready_photos(self):
        return self.photos().filter(status=TourPhoto.Status.READY)

    def guests(self) -> list[tuple[str, str, str]]:
        """(name, email, language) of everyone who should get the album link."""
        if self.kind == "d":
            regs = self.owner.registrations.filter(status__in=Booking.SEAT_STATUSES)
            return [(b.guest_name, b.guest_email, b.language) for b in regs]
        b = self.owner
        people = [(b.guest_name, b.guest_email, b.language)]
        people += [(c.name, c.email, b.language) for c in b.companions.exclude(email="")]
        return people

    # --- Upload -------------------------------------------------------------------------------------

    def start_upload(self, *, filename: str, content_type: str, size: int, uploaded_by: str, by_guide: bool,
                     may_publish: bool) -> tuple[TourPhoto, str]:
        """A photo row waiting for its file, and the signed link the browser uploads to."""
        if not self.is_open:
            raise AlbumError(_("Das Album ist geschlossen."))
        if content_type not in CONTENT_TYPES:
            raise AlbumError(_("Bitte ein JPG-, PNG-, WebP- oder HEIC-Bild verwenden."))
        if not 0 < size <= settings.PHOTO_MAX_MB * 1024 * 1024:
            raise AlbumError(_("Das Bild ist zu gross (höchstens %(mb)s MB).") % {"mb": settings.PHOTO_MAX_MB})
        if self.photos().count() >= MAX_PHOTOS:
            raise AlbumError(_("Das Album ist voll."))
        photo = TourPhoto.objects.create(
            guide=self.guide, **{"departure" if self.kind == "d" else "booking": self.owner},
            filename=(filename or "foto.jpg")[-200:], content_type=content_type, size=size,
            uploaded_by=self.guide.display_name if by_guide else ((uploaded_by or "").strip()[:100] or "?"),
            by_guide=by_guide,
            may_publish=by_guide or may_publish)
        return photo, photostore.upload_url(photo.object_key, content_type)

    def finish_upload(self, key: str) -> TourPhoto:
        """The browser says the file is up: check it's an image, make the preview, read date and place."""
        photo = self.photos().filter(key=key, status=TourPhoto.Status.UPLOADING).first()
        if photo is None:
            raise AlbumError(_("Unbekanntes Foto."))
        raw = photostore.read(photo.object_key, settings.PHOTO_MAX_MB * 1024 * 1024)
        try:
            if raw is None:
                raise AlbumError(_("Das Bild konnte nicht gelesen werden."))
            preview, width, height, taken_at, lat, lon = _preview(raw)
        except AlbumError:
            discard(photo)
            raise
        photo.preview, photo.width, photo.height = preview, width, height
        photo.taken_at, photo.latitude, photo.longitude = taken_at, lat, lon
        photo.size, photo.status = len(raw), TourPhoto.Status.READY
        photo.save()
        return photo


def _preview(raw: bytes):
    """Preview JPEG plus what the camera recorded: (bytes, width, height, taken_at, latitude, longitude)."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in pictures.FORMATS:
                raise AlbumError(_("Bitte ein JPG-, PNG-, WebP- oder HEIC-Bild verwenden."))
            exif = img.getexif()
            if img.format in ("JPEG", "MPO"):
                img.draft("RGB", (PREVIEW_SIDE * 2, PREVIEW_SIDE * 2))  # decode small: saves memory and time
            img = pictures._on_white(ImageOps.exif_transpose(img))
            img.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=78, optimize=True, progressive=True)
            taken_at = _taken_at(exif)
            lat, lon = _position(exif)
            return out.getvalue(), img.width, img.height, taken_at, lat, lon
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise AlbumError(_("Das Bild konnte nicht gelesen werden.")) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = pictures.MAX_PIXELS_DEFAULT


def _taken_at(exif):
    try:
        value = exif.get_ifd(ExifTags.IFD.Exif).get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
        return timezone.make_aware(datetime.strptime(str(value).strip("\x00 "), "%Y:%m:%d %H:%M:%S")) if value else None
    except (ValueError, TypeError, KeyError):
        return None


def _position(exif):
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if not gps:
            return None, None

        def degrees(values, ref):
            d, m, s = (float(v) for v in values)
            value = d + m / 60 + s / 3600
            return -value if ref in ("S", "W") else value

        lat = degrees(gps[ExifTags.GPS.GPSLatitude], gps.get(ExifTags.GPS.GPSLatitudeRef))
        lon = degrees(gps[ExifTags.GPS.GPSLongitude], gps.get(ExifTags.GPS.GPSLongitudeRef))
        if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0):
            return lat, lon
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None, None


# --- Guide actions ---------------------------------------------------------------------------------


def keep(photo: TourPhoto) -> Picture:
    """Into the guide's picture library, pre-filled with the tour's kind, place and date. The original stays."""
    if photo.kept:
        return photo.picture
    if not photo.can_keep:
        raise AlbumError(_("Fotos von Gästen nur mit ihrem Einverständnis."))
    raw = photostore.read(photo.object_key, settings.PHOTO_MAX_MB * 1024 * 1024)
    if raw is None:
        raise AlbumError(_("Das Bild konnte nicht gelesen werden."))
    owner = photo.departure or photo.booking
    tour = owner if photo.departure_id else owner.tour
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        shrunk = pictures.shrink(raw)
    except pictures.PictureError as exc:
        raise AlbumError(str(exc)) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = pictures.MAX_PIXELS_DEFAULT
    with transaction.atomic():
        picture = pictures.store(photo.guide, shrunk, in_library=True, category=getattr(tour, "category", "") or "",
                                 title=owner.title)
        picture.place = (getattr(tour, "location", "") or "")[:120]
        picture.note = _("Aus dem Album vom %(day)s, Foto: %(who)s") % {
            "day": f"{owner.start_date:%d.%m.%Y}", "who": photo.uploaded_by}
        picture.save(update_fields=["place", "note"])
        photo.picture = picture
        photo.save(update_fields=["picture"])
    return picture


def discard(photo: TourPhoto) -> None:
    """Delete the original and the album entry (a kept library picture stays)."""
    photostore.delete(photo.object_key)
    photo.delete()


def cleanup(today=None) -> int:
    """Hourly: photos nobody kept, PHOTO_KEEP_DAYS after the tour; uploads that never finished after a day."""
    today = today or timezone.localdate()
    cutoff = today - timedelta(days=settings.PHOTO_KEEP_DAYS)
    stale = TourPhoto.objects.filter(status=TourPhoto.Status.UPLOADING,
                                     created_at__lt=timezone.now() - timedelta(days=1))
    expired = TourPhoto.objects.filter(Q(booking__end_date__lt=cutoff) | Q(departure__end_date__lt=cutoff),
                                       picture__isnull=True)
    count = 0
    for photo in list(stale.defer("preview")) + list(expired.defer("preview")):
        discard(photo)
        count += 1
    return count


def photo_reminders(guide, today):
    """To-do: tours of the last two weeks without any photo from the guide yet."""
    since = today - timedelta(days=14)
    albums = [Album(b) for b in guide.bookings.filter(departure__isnull=True, status__in=BOOKED,
                                                      end_date__gte=since, end_date__lt=today)
              .select_related("tour", "guide")]
    albums += [Album(d) for d in guide.departures.filter(status=Departure.Status.CONFIRMED, end_date__gte=since,
                                                         end_date__lt=today).select_related("guide")]
    return [a for a in albums if not a.photos().filter(by_guide=True).exists()]
