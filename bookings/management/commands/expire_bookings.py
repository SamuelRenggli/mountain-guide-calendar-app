from django.core.management.base import BaseCommand

from bookings import departures
from bookings.services import expire_overdue
from payments.stripe_service import retry_refunds


class Command(BaseCommand):
    help = ("Hourly: expire overdue requests and offers, release unpaid seats, remind guides before decision "
            "deadlines, cancel undecided dates and retry failed refunds.")

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_overdue()} booking(s)")
        self.stdout.write(f"Released {departures.release_unpaid()} unpaid registration(s)")
        reminded, cancelled = departures.remind_and_auto_cancel()
        self.stdout.write(f"Reminded {reminded}, auto-cancelled {cancelled} scheduled date(s)")
        self.stdout.write(f"Sent {retry_refunds()} pending refund(s)")
