from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AccessLog, DayRateTier, User


class DayRateTierInline(admin.TabularInline):
    model = DayRateTier
    extra = 0


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "display_name", "slug", "stripe_charges_enabled", "totp_enabled", "is_active", "is_staff")
    search_fields = ("email", "display_name", "slug")
    readonly_fields = ("totp_enabled", "last_login", "date_joined")
    inlines = [DayRateTierInline]
    fieldsets = (
        (None, {"fields": ("email", "password", "totp_enabled")}),
        ("Profil", {"fields": ("display_name", "phone", "slug", "language")}),
        ("Verfügbarkeit", {"fields": ("available_weekdays", "min_notice_days", "max_advance_days")}),
        ("Preise & Bedingungen", {"fields": ("day_rate", "aspirant_day_rate", "iban", "vat_number", "vat_rate",
                                             "cancel_free_days", "cancel_partial_days", "cancel_partial_percent",
                                             "guest_terms")}),
        ("Kalender", {"fields": ("ical_url", "ical_last_synced_at", "ical_last_error")}),
        ("Widget & Zahlungen", {"fields": ("widget_allowed_origins", "stripe_account_id", "stripe_charges_enabled")}),
        ("Rechte", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Daten", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "display_name", "password1", "password2")}),)
    actions = ["reset_two_factor"]

    @admin.action(description="Zwei-Faktor zurücksetzen (Gerät verloren)")
    def reset_two_factor(self, request, queryset):
        queryset.update(totp_enabled=False, totp_secret="", recovery_codes="")


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "object_ref", "ip")
    list_filter = ("action",)
    search_fields = ("user__email", "object_ref")
    readonly_fields = ("created_at", "user", "action", "object_ref", "ip")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
