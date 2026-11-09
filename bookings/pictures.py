"""Tour photos: uploaded by the guide or taken from a tour page's preview image.

Every image is decoded, shrunk to at most 1200 px and re-encoded as JPEG, so only clean, small files are stored
and served (no metadata, no scripts in SVGs, no huge originals). Stored in the database: a few hundred KB per tour.
"""

import io

from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

from .calsync import CalendarSyncError, safe_get
from .models import Picture

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1200
Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse decompression bombs


class PictureError(Exception):
    pass


def shrink(raw: bytes) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP", "GIF", "MPO"):
                raise PictureError(_("Bitte ein JPG-, PNG- oder WebP-Bild verwenden."))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            if img.mode != "RGB":
                background = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
            return out.getvalue(), img.width, img.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PictureError(_("Das Bild konnte nicht gelesen werden.")) from exc


def from_bytes(guide, raw: bytes, source_url: str = "", **extra) -> Picture:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PictureError(_("Das Bild ist zu gross (höchstens 8 MB)."))
    return store(guide, shrink(raw), source_url, **extra)


def store(guide, shrunk: tuple[bytes, int, int], source_url: str = "", *, in_library=False, category="",
          title="") -> Picture:
    """guide=None stores a Tourchef stock picture."""
    data, width, height = shrunk
    return Picture.objects.create(guide=guide, data=data, width=width, height=height, source_url=source_url[:1000],
                                  in_library=in_library or guide is None, category=category or "", title=title[:120])


def from_url(guide, url: str) -> Picture | None:
    """The preview image of a tour page, or None when it can't be loaded."""
    try:
        raw = safe_get(url, MAX_UPLOAD_BYTES)
        return from_bytes(guide, raw, source_url=url)
    except (CalendarSyncError, PictureError):
        return None
