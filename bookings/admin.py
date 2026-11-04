from django.contrib import admin

from .models import (
    BlockedPeriod, Booking, BookingEvent, Departure, ExternalBusy, InvoiceLine, OpenPeriod, Tour, WaitlistEntry,
)


@admin.register(Tour)
class TourAdmin(admin.ModelAdmin):
    list_display = ("title", "guide", "duration_days", "price_per_person", "min_participants", "max_participants",
                    "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "guide__display_name")


class WaitlistInline(admin.TabularInline):
    model = WaitlistEntry
    extra = 0
    readonly_fields = ("name", "email", "seats", "language", "created_at", "notified_at")


@admin.register(Departure)
class DepartureAdmin(admin.ModelAdmin):
    list_display = ("title", "guide", "start_date", "end_date", "status", "price_per_person", "max_participants")
    list_filter = ("status",)
    search_fields = ("title", "guide__display_name")
    readonly_fields = ("public_id", "created_at", "decided_at", "reminder_sent_at")
    inlines = [WaitlistInline]


class BookingEventInline(admin.TabularInline):
    model = BookingEvent
    extra = 0
    readonly_fields = ("created_at", "actor", "from_status", "to_status", "note")
    can_delete = False


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    readonly_fields = ("position", "kind", "description", "quantity", "unit_price")
    can_delete = False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "guide", "departure", "start_date", "end_date", "guest_name", "status", "price")
    list_filter = ("status",)
    search_fields = ("guest_name", "guest_email", "guide__display_name")
    readonly_fields = ("token", "created_at", "updated_at", "invoiced_at", "paid_at", "stripe_checkout_session_id",
                       "stripe_payment_intent_id", "amount_paid", "refunded_amount", "refund_due", "terms_accepted_at")
    inlines = [InvoiceLineInline, BookingEventInline]


admin.site.register(BlockedPeriod)
admin.site.register(OpenPeriod)
admin.site.register(ExternalBusy)
