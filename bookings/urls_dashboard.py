from django.urls import path

from . import views_dashboard as v

app_name = "dashboard"

urlpatterns = [
    path("", v.home, name="home"),
    path("buchungen/<int:pk>/", v.booking_detail, name="booking"),
    path("buchungen/<int:pk>/<slug:name>/", v.booking_action, name="booking_action"),
    path("touren/", v.tours, name="tours"),
    path("touren/neu/", v.tour_edit, name="tour_new"),
    path("touren/<int:pk>/", v.tour_edit, name="tour_edit"),
    path("touren/<int:pk>/loeschen/", v.tour_delete, name="tour_delete"),
    path("verfuegbarkeit/", v.availability_view, name="availability"),
    path("verfuegbarkeit/sperre/<int:pk>/loeschen/", v.block_delete, name="block_delete"),
    path("verfuegbarkeit/abgleichen/", v.calendar_sync_now, name="calendar_sync"),
    path("kalender/", v.calendar_view, name="calendar"),
    path("einstellungen/", v.settings_view, name="settings"),
]
