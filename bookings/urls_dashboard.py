from django.urls import path

from . import views_dashboard as v

app_name = "dashboard"

urlpatterns = [
    path("", v.home, name="home"),
    path("buchungen/<int:pk>/", v.booking_detail, name="booking"),
    path("buchungen/<int:pk>/abrechnen/", v.invoice, name="invoice"),
    path("buchungen/<int:pk>/<slug:name>/", v.booking_action, name="booking_action"),
    path("termine/", v.departure_list, name="departures"),
    path("termine/neu/", v.departure_new, name="departure_new"),
    path("termine/<int:pk>/", v.departure_detail, name="departure"),
    path("termine/<int:pk>/bearbeiten/", v.departure_edit, name="departure_edit"),
    path("termine/<int:pk>/<slug:name>/", v.departure_action, name="departure_action"),
    path("vorlagen/", v.tours, name="tours"),
    path("vorlagen/neu/", v.tour_edit, name="tour_new"),
    path("vorlagen/<int:pk>/", v.tour_edit, name="tour_edit"),
    path("vorlagen/<int:pk>/loeschen/", v.tour_delete, name="tour_delete"),
    path("verfuegbarkeit/", v.availability_view, name="availability"),
    path("verfuegbarkeit/sperre/<int:pk>/loeschen/", v.block_delete, name="block_delete"),
    path("verfuegbarkeit/freigabe/<int:pk>/loeschen/", v.open_delete, name="open_delete"),
    path("verfuegbarkeit/anbieter/<int:pk>/loeschen/", v.partner_delete, name="partner_delete"),
    path("verfuegbarkeit/anbieter-freigabe/<int:pk>/loeschen/", v.partner_period_delete, name="partner_period_delete"),
    path("extern/", v.external_tours, name="external_tours"),
    path("extern/<int:pk>/<slug:name>/", v.external_tour_action, name="external_tour_action"),
    path("verfuegbarkeit/abgleichen/", v.calendar_sync_now, name="calendar_sync"),
    path("kalender/", v.calendar_view, name="calendar"),
    path("einstellungen/", v.settings_view, name="settings"),
    path("einstellungen/gastdaten-loeschen/", v.erase_guest_data, name="erase_guest_data"),
    path("export/", v.export_view, name="export"),
    path("pilot/", v.pilot_stats, name="pilot_stats"),
]
