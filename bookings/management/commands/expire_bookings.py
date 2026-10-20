from django.core.management.base import BaseCommand

from bookings.services import expire_overdue


class Command(BaseCommand):
    help = "Expire requests, offers and payments whose deadline passed."

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_overdue()} booking(s)")
