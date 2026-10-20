from django.urls import path

from . import views_guest

app_name = "guest"

urlpatterns = [
    path("<str:token>/", views_guest.detail, name="detail"),
    path("<str:token>/bezahlen/", views_guest.pay, name="pay"),
    path("<str:token>/<slug:name>/", views_guest.action, name="action"),
]
