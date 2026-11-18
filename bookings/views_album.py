"""The album page behind the secret link: guests and the guide upload and download originals. The guide (logged in)
also keeps photos for the library, deletes photos and sends the link to the guests."""

import json

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from core.middleware import DEFAULT_CSP
from core.ratelimit import allow, client_ip

from . import album as albums
from . import emails, photostore

# Uploads go straight from the browser to the photo bucket.
ALBUM_CSP = DEFAULT_CSP + "; connect-src 'self' https://storage.googleapis.com"


def _album(request, code) -> albums.Album:
    if not allow(f"album:{client_ip(request)}", limit=600, window_seconds=10 * 60):
        raise Http404
    album = albums.Album.for_code(code)
    if album is None or not album.took_place:
        raise Http404
    return album


def _is_guide(request, album) -> bool:
    return request.user.is_authenticated and request.user.pk == album.guide.pk


@never_cache
def page(request, code):
    album = _album(request, code)
    is_guide = _is_guide(request, album)
    if not is_guide and not request.language_from_url and not request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME):
        lang = getattr(album.owner, "language", None) or album.guide.language
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
    photos = list(album.ready_photos())
    response = render(request, "album/page.html", {
        "album": album, "photos": photos, "is_guide": is_guide, "max_mb": settings.PHOTO_MAX_MB,
        "kept_count": sum(1 for p in photos if p.kept),
    })
    response.headers["Content-Security-Policy"] = ALBUM_CSP
    response.headers["X-Robots-Tag"] = "noindex"
    return response


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@require_POST
def start_upload(request, code):
    album = _album(request, code)
    data = _json_body(request)
    is_guide = _is_guide(request, album)
    try:
        photo, url = album.start_upload(
            filename=str(data.get("filename", ""))[:200], content_type=str(data.get("type", "")),
            size=int(data.get("size") or 0), uploaded_by=str(data.get("name", ""))[:100], by_guide=is_guide,
            may_publish=bool(data.get("consent")))
    except (albums.AlbumError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"key": photo.key, "url": url})


@require_POST
def finish_upload(request, code):
    album = _album(request, code)
    try:
        album.finish_upload(str(_json_body(request).get("key", "")))
    except albums.AlbumError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


def preview(request, code, key):
    album = _album(request, code)
    photo = album.ready_photos().defer(None).filter(key=key).first()
    if photo is None:
        raise Http404
    response = HttpResponse(bytes(photo.preview), content_type="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


def original(request, code, key):
    album = _album(request, code)
    photo = album.ready_photos().filter(key=key).first()
    if photo is None or not album.is_open:
        raise Http404
    return redirect(photostore.download_url(photo.object_key, photo.filename))


def all_links(request, code):
    """Signed links for "download all": the browser fetches the originals one after the other."""
    album = _album(request, code)
    if not album.is_open:
        raise Http404
    return JsonResponse({"links": [photostore.download_url(p.object_key, p.filename) for p in album.ready_photos()]})


@require_POST
def guide_action(request, code, name):
    album = _album(request, code)
    if not _is_guide(request, album):
        raise Http404
    keys = request.POST.getlist("photo")
    chosen = list(album.ready_photos().filter(key__in=keys)) if keys else []
    if name == "behalten":
        kept, refused = 0, 0
        for photo in chosen:
            try:
                albums.keep(photo)
                kept += 1
            except albums.AlbumError:
                refused += 1
        if kept:
            messages.success(request, _("%(n)s Fotos in deine Bildbibliothek übernommen.") % {"n": kept})
        if refused:
            messages.error(request, _("%(n)s Fotos von Gästen ohne ihr Einverständnis nicht übernommen.") % {"n": refused})
    elif name == "loeschen":
        for photo in chosen:
            albums.discard(photo)
        if chosen:
            messages.success(request, _("%(n)s Fotos gelöscht.") % {"n": len(chosen)})
    elif name == "teilen":
        sent = emails.album_shared(album)
        messages.success(request, _("Link an %(n)s Gäste gesendet.") % {"n": sent})
    return redirect(album.path)


# --- Local storage (development and tests only, when no bucket is set) ------------------------------


@csrf_exempt
@require_http_methods(["PUT"])
def local_put(request, token):
    if settings.PHOTO_BUCKET:
        raise Http404
    try:
        info = photostore.check_local_token(token)
    except signing.BadSignature:
        raise Http404
    if request.headers.get("Content-Type") != info["type"]:
        return HttpResponseBadRequest()
    photostore.save_local(info["key"], request.body)
    return HttpResponse(status=200)


def local_get(request, token):
    if settings.PHOTO_BUCKET:
        raise Http404
    try:
        info = signing.loads(token, salt=photostore.LOCAL_SALT, max_age=photostore.LINK_MINUTES * 60)
    except signing.BadSignature:
        raise Http404
    data = photostore.read(info["key"], settings.PHOTO_MAX_MB * 1024 * 1024)
    if data is None:
        raise Http404
    from io import BytesIO

    return FileResponse(BytesIO(data), as_attachment=True, filename=info["name"])
