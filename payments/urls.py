from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("stripe/verbinden/", views.connect, name="connect"),
    path("stripe/zurueck/", views.connect_return, name="connect_return"),
    path("stripe/webhook/", views.webhook, name="webhook"),
]
