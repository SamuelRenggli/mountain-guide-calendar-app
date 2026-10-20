from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("anmelden/", views.LoginView.as_view(), name="login"),
    path("abmelden/", auth_views.LogoutView.as_view(), name="logout"),
    path("registrieren/", views.signup, name="signup"),
    path(
        "passwort/aendern/",
        auth_views.PasswordChangeView.as_view(
            template_name="accounts/password_change.html", success_url=reverse_lazy("dashboard:settings")
        ),
        name="password_change",
    ),
    path("passwort/vergessen/", views.PasswordResetView.as_view(), name="password_reset"),
    path(
        "passwort/vergessen/gesendet/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "passwort/neu/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "passwort/neu/fertig/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
]
