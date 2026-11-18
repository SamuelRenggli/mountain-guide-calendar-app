"""Where tour photo originals live: a private Google Cloud Storage bucket in Zürich (PHOTO_BUCKET), or a local
folder when no bucket is set (development, tests).

Browsers upload and download originals directly with short-lived signed links, so big files never pass through
the app's few workers. On Cloud Run there is no key file: links are signed through the IAM API as the run account
(it holds roles/iam.serviceAccountTokenCreator on itself, see deploy/gcp-setup.sh)."""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.urls import reverse

LINK_MINUTES = 30
LOCAL_SALT = "photostore-local"


def _bucket():
    from google.cloud import storage

    return storage.Client().bucket(settings.PHOTO_BUCKET)


def _signing_kwargs() -> dict:
    """On Cloud Run: sign through IAM with the run account's token (no private key available)."""
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default()
    credentials.refresh(Request())
    if hasattr(credentials, "signer"):  # a key file (local tests against a real bucket)
        return {}
    return {"service_account_email": credentials.service_account_email, "access_token": credentials.token}


def _local_path(key: str) -> Path:
    return Path(settings.PHOTO_LOCAL_DIR) / key


def upload_url(key: str, content_type: str) -> str:
    """A link the browser PUTs the file to, with exactly this Content-Type."""
    if settings.PHOTO_BUCKET:
        return _bucket().blob(key).generate_signed_url(
            version="v4", expiration=timedelta(minutes=LINK_MINUTES), method="PUT", content_type=content_type,
            **_signing_kwargs())
    token = signing.dumps({"key": key, "type": content_type}, salt=LOCAL_SALT)
    return reverse("album_local_put", args=[token])


def check_local_token(token: str) -> dict:
    return signing.loads(token, salt=LOCAL_SALT, max_age=LINK_MINUTES * 60)


def download_url(key: str, filename: str) -> str:
    """A link that downloads the original under its own name."""
    if settings.PHOTO_BUCKET:
        safe = filename.replace('"', "").replace("\\", "")
        return _bucket().blob(key).generate_signed_url(
            version="v4", expiration=timedelta(minutes=LINK_MINUTES), method="GET",
            response_disposition=f'attachment; filename="{safe}"', **_signing_kwargs())
    return reverse("album_local_get", args=[signing.dumps({"key": key, "name": filename}, salt=LOCAL_SALT)])


def save_local(key: str, data: bytes) -> None:
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read(key: str, max_bytes: int) -> bytes | None:
    """The uploaded file, or None when it's missing or bigger than max_bytes."""
    if settings.PHOTO_BUCKET:
        blob = _bucket().get_blob(key)
        if blob is None or (blob.size or 0) > max_bytes:
            return None
        return blob.download_as_bytes()
    path = _local_path(key)
    if not path.exists() or path.stat().st_size > max_bytes:
        return None
    return path.read_bytes()


def size(key: str) -> int | None:
    if settings.PHOTO_BUCKET:
        blob = _bucket().get_blob(key)
        return None if blob is None else blob.size
    path = _local_path(key)
    return path.stat().st_size if path.exists() else None


def delete(key: str) -> None:
    if settings.PHOTO_BUCKET:
        from google.api_core.exceptions import NotFound

        try:
            _bucket().blob(key).delete()
        except NotFound:
            pass
        return
    _local_path(key).unlink(missing_ok=True)
