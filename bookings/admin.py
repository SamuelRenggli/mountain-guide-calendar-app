from django.contrib import admin

from .models import BlockedPeriod, Booking, BookingEvent, ExternalBusy, Tour


@admin.register(Tour)
class TourAdmin(admin.ModelAdmin):
    list_display = ("title", "guide", "duration_days", "price", "price_mode", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "guide__display_name")


class BookingEventInline(admin.TabularInline):
    model = BookingEvent
    extra = 0
    readonly_fields = ("created_at", "actor", "from_status", "to_status", "note")
    can_delete = False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "guide", "tour", "start_date", "end_date", "guest_name", "status", "price")
    list_filter = ("status",)
    search_fields = ("guest_name", "guest_email", "guide__display_name")
    readonly_fields = ("token", "created_at", "updated_at", "paid_at", "stripe_checkout_session_id")
    inlines = [BookingEventInline]


admin.site.register(BlockedPeriod)
admin.site.register(ExternalBusy)
