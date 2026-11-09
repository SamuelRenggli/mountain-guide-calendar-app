from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView

from bookings import views_inbound as inbound_views
from core import views as core_views


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


# The admin uses the normal login (rate limit + two-factor) instead of its own form.
admin.site.login = login_required(admin.site.login)

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard:home", permanent=False)),
    path("healthz", healthz),
    path(settings.ADMIN_URL, admin.site.urls),
    path("konto/", include("accounts.urls")),
    path("dashboard/", include("bookings.urls_dashboard")),
    path("", include("bookings.urls_widget")),
    path("b/", include("bookings.urls_guest")),
    path("zahlungen/", include("payments.urls")),
    path("eingang/mail/", inbound_views.mail, name="inbound_mail"),
    path("rechtliches/<slug:page>/", core_views.legal, name="legal"),
]
