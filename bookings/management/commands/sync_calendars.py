from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from bookings.calsync import CalendarSyncError, sync_guide


class Command(BaseCommand):
    help = "Import busy days from every guide's iCal feed."

    def handle(self, *args, **options):
        guides = get_user_model().objects.filter(is_active=True).exclude(ical_url="")
        ok = failed = 0
        for guide in guides:
            try:
                sync_guide(guide)
                ok += 1
            except CalendarSyncError as exc:
                failed += 1
                self.stderr.write(f"Guide {guide.pk}: {exc}")
        self.stdout.write(f"Calendar sync: {ok} ok, {failed} failed")
