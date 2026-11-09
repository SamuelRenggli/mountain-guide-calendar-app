"""Load Tourchef's stock pictures, one or more per kind of tour, that every guide can pick in the picture library.

Name each file after its kind of tour, e.g. skitour_tag.jpg, freeride-2.jpg, bergtour_eis.png. Kinds:
freeride, skitour_tag, skitour_woche, fruehling, gletschertrekking, sportklettern, bergtour_fels, bergtour_eis.

    python manage.py load_stock_pictures path/to/folder [--replace]
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bookings import pictures
from bookings.models import Picture, TourCategory


class Command(BaseCommand):
    help = "Load Tourchef stock pictures (one file per kind of tour) into the picture library of all guides."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--replace", action="store_true",
                            help="Remove the existing stock pictures of each kind that gets a new one.")

    def handle(self, folder, replace=False, **options):
        root = Path(folder)
        if not root.is_dir():
            raise CommandError(f"{folder} is not a folder")
        kinds = {c for c, _label in TourCategory.choices}
        files = sorted(p for p in root.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
        replaced = set()
        for path in files:
            kind = path.stem.split("-")[0].lower()
            if kind not in kinds:
                self.stderr.write(f"Skipped {path.name}: unknown kind of tour «{kind}»")
                continue
            if replace and kind not in replaced:
                Picture.objects.filter(guide__isnull=True, category=kind).delete()
                replaced.add(kind)
            try:
                pictures.from_bytes(None, path.read_bytes(), category=kind, title="Tourchef")
            except pictures.PictureError as exc:
                self.stderr.write(f"Skipped {path.name}: {exc}")
                continue
            self.stdout.write(f"Loaded {path.name} as {kind}")
