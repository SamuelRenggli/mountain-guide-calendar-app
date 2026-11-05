from django.urls import path

from . import views_widget

app_name = "widget"

urlpatterns = [
    path("w/<slug:slug>/", views_widget.widget, name="show"),
    path("w/<slug:slug>/anfrage/", views_widget.widget_request, name="request"),
    path("w/<slug:slug>/bedingungen/", views_widget.terms, name="terms"),
    path("p/<str:token>/", views_widget.partner_page, name="partner"),
    path("t/<str:public_id>/", views_widget.departure_page, name="departure"),
    path("t/<str:public_id>/anmelden/", views_widget.departure_signup, name="departure_signup"),
    path("t/<str:public_id>/warteliste/", views_widget.departure_waitlist, name="departure_waitlist"),
]
