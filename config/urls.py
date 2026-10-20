from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard:home", permanent=False)),
    path("healthz", healthz),
    path(settings.ADMIN_URL, admin.site.urls),
    path("konto/", include("accounts.urls")),
    path("dashboard/", include("bookings.urls_dashboard")),
    path("w/", include("bookings.urls_widget")),
    path("b/", include("bookings.urls_guest")),
    path("zahlungen/", include("payments.urls")),
]
