import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bookings import photostore


class Command(BaseCommand):
    help = "Round trip through the photo bucket: signed upload, signed download, delete. Run once after setup."

    def handle(self, *args, **options):
        if not settings.PHOTO_BUCKET:
            raise CommandError("PHOTO_BUCKET is not set")
        key, data = "selftest/check.txt", b"tourchef photo bucket check"
        put = requests.put(photostore.upload_url(key, "text/plain"), data=data,
                           headers={"Content-Type": "text/plain"}, timeout=30)
        if put.status_code != 200:
            raise CommandError(f"signed upload failed: {put.status_code} {put.text[:300]}")
        got = requests.get(photostore.download_url(key, "check.txt"), timeout=30)
        if got.content != data:
            raise CommandError(f"signed download failed: {got.status_code}")
        if photostore.read(key, 1024) != data:
            raise CommandError("read failed")
        photostore.delete(key)
        self.stdout.write(f"Photo bucket {settings.PHOTO_BUCKET} OK")
