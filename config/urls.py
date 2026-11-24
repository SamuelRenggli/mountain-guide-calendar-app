from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView

from bookings import views_album, views_guest
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
    path("mitreisende/<int:pk>-<str:code>/", views_guest.companion, name="companion"),
    path("abmelden/<str:token>/", views_guest.unsubscribe, name="unsubscribe"),
    path("album/<str:code>/", views_album.page, name="album"),
    path("album/<str:code>/hochladen/", views_album.start_upload, name="album_start"),
    path("album/<str:code>/fertig/", views_album.finish_upload, name="album_finish"),
    path("album/<str:code>/alle/", views_album.all_links, name="album_all"),
    path("album/<str:code>/vorschau/<str:key>.jpg", views_album.preview, name="album_preview"),
    path("album/<str:code>/original/<str:key>/", views_album.original, name="album_original"),
    path("album/<str:code>/aktion/<slug:name>/", views_album.guide_action, name="album_action"),
    path("album-lokal/<str:token>/", views_album.local_put, name="album_local_put"),
    path("album-lokal/<str:token>/datei/", views_album.local_get, name="album_local_get"),
    path("zahlungen/", include("payments.urls")),
    path("eingang/mail/", inbound_views.mail, name="inbound_mail"),
    path("rechtliches/<slug:page>/", core_views.legal, name="legal"),
]
