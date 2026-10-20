from django.urls import path

from . import views_widget

app_name = "widget"

urlpatterns = [
    path("<slug:slug>/", views_widget.widget, name="show"),
    path("<slug:slug>/anfrage/", views_widget.widget_request, name="request"),
]
