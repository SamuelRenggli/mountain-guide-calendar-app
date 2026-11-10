"""Load Tourchef's stock pictures, one or more per kind of tour, that every guide can pick in the picture library.

Name each file after its kind of tour, e.g. skitour_tag.jpg, freeride-2.jpg (kinds: see TourCategory).
Files already loaded (same file name) are skipped, so the command runs on every deploy (deploy/migrate.sh)
and only adds new files. Defaults to the stock_pictures folder in the repository.

    python manage.py load_stock_pictures [folder] [--replace]
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bookings import pictures
from bookings.models import Picture, TourCategory

SOURCE_PREFIX = "stock:"


class Command(BaseCommand):
    help = "Load Tourchef stock pictures (files named after the kind of tour) into the picture library of all guides."

    def add_arguments(self, parser):
        parser.add_argument("folder", nargs="?", default=str(Path(settings.BASE_DIR) / "stock_pictures"))
        parser.add_argument("--replace", action="store_true",
                            help="Remove all stock pictures first and load the folder again.")

    def handle(self, folder, replace=False, **options):
        root = Path(folder)
        if not root.is_dir():
            raise CommandError(f"{folder} is not a folder")
        if replace:
            Picture.objects.filter(guide__isnull=True).delete()
        kinds = {c for c, _label in TourCategory.choices}
        loaded = set(Picture.objects.filter(guide__isnull=True, source_url__startswith=SOURCE_PREFIX)
                     .values_list("source_url", flat=True))
        files = sorted(p for p in root.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
        added = 0
        for path in files:
            kind = path.stem.split("-")[0].lower()
            if kind not in kinds:
                self.stderr.write(f"Skipped {path.name}: unknown kind of tour «{kind}»")
                continue
            if SOURCE_PREFIX + path.name in loaded:
                continue
            try:
                pictures.from_bytes(None, path.read_bytes(), source_url=SOURCE_PREFIX + path.name, category=kind,
                                    title="Tourchef")
            except pictures.PictureError as exc:
                self.stderr.write(f"Skipped {path.name}: {exc}")
                continue
            added += 1
        self.stdout.write(f"{added} stock picture(s) loaded.")
