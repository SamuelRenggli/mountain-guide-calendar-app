"""Tour photos: uploaded by the guide or taken from a tour page's preview image.

Every image is decoded, shrunk to at most 1200 px and re-encoded as JPEG, so only clean, small files are stored
and served (no metadata, no scripts in SVGs, no huge originals). Stored in the database: a few hundred KB per tour.
"""

import io

from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # iPhone photos (HEIC) straight from a computer
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

from .calsync import CalendarSyncError, safe_get
from .models import Picture

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1200
FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "MPO", "HEIF")
LOGO_TILE = (800, 600)
ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse decompression bombs


class PictureError(Exception):
    pass


def _on_white(img):
    if img.mode == "RGB":
        return img
    background = Image.new("RGB", img.size, (255, 255, 255))
    rgba = img.convert("RGBA")
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def shrink(raw: bytes, *, logo=False) -> tuple[bytes, int, int]:
    """A clean JPEG of at most MAX_SIDE. logo=True: a provider's small logo, centred on a white 4:3 tile so it
    looks tidy where a tour photo would be."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in FORMATS + (("ICO",) if logo else ()):
                raise PictureError(_("Bitte ein JPG-, PNG-, WebP- oder HEIC-Bild verwenden."))
            img = _on_white(ImageOps.exif_transpose(img))
            if logo:
                tile = Image.new("RGB", LOGO_TILE, (255, 255, 255))
                img.thumbnail((LOGO_TILE[0] // 2, LOGO_TILE[1] // 2))
                tile.paste(img, ((LOGO_TILE[0] - img.width) // 2, (LOGO_TILE[1] - img.height) // 2))
                img = tile
            img.thumbnail((MAX_SIDE, MAX_SIDE))
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


def from_url(guide, url: str, *, logo=False) -> Picture | None:
    """An image from another website (a provider's logo), or None when it can't be loaded."""
    try:
        raw = safe_get(url, MAX_UPLOAD_BYTES)
        return store(guide, shrink(raw, logo=logo), source_url=url, title="Logo" if logo else "")
    except (CalendarSyncError, PictureError):
        return None
