from django.core.management.base import BaseCommand

from bookings import departures
from bookings.services import expire_overdue, remind_missing_details
from payments.stripe_service import retry_refunds


class Command(BaseCommand):
    help = ("Hourly: expire overdue requests and offers, release unpaid seats, remind guides before decision "
            "deadlines, cancel undecided dates, retry failed refunds and remind guests of missing planning details.")

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_overdue()} booking(s)")
        self.stdout.write(f"Released {departures.release_unpaid()} unpaid registration(s)")
        reminded, cancelled = departures.remind_and_auto_cancel()
        self.stdout.write(f"Reminded {reminded}, auto-cancelled {cancelled} scheduled date(s)")
        self.stdout.write(f"Sent {retry_refunds()} pending refund(s)")
        self.stdout.write(f"Reminded {remind_missing_details()} guest(s) of missing planning details")
