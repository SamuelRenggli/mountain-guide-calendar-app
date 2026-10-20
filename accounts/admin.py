from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "display_name", "slug", "stripe_charges_enabled", "is_active", "is_staff")
    search_fields = ("email", "display_name", "slug")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profil", {"fields": ("display_name", "phone", "slug")}),
        ("Verfügbarkeit", {"fields": ("available_weekdays", "min_notice_days", "max_advance_days")}),
        ("Kalender", {"fields": ("ical_url", "ical_last_synced_at", "ical_last_error")}),
        ("Widget & Zahlungen", {"fields": ("widget_allowed_origins", "stripe_account_id", "stripe_charges_enabled")}),
        ("Rechte", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Daten", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "display_name", "password1", "password2")}),)
