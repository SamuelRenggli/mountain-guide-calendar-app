from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from core.ratelimit import allow, client_ip

from .forms import SignupForm


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if not allow(f"login:{client_ip(request)}", limit=10, window_seconds=15 * 60):
            messages.error(request, "Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen.")
            return redirect("accounts:login")
        return super().post(request, *args, **kwargs)


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    if not settings.GUIDE_SIGNUP_CODE:
        return render(request, "accounts/signup_closed.html")
    if request.method == "POST":
        if not allow(f"signup:{client_ip(request)}", limit=5, window_seconds=60 * 60):
            messages.error(request, "Zu viele Versuche. Bitte später erneut versuchen.")
            return redirect("accounts:signup")
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Willkommen! Lege als Erstes deine Touren an.")
            return redirect("dashboard:tours")
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    extra_email_context = {"site_url": settings.SITE_URL}

    def post(self, request, *args, **kwargs):
        if not allow(f"pwreset:{client_ip(request)}", limit=5, window_seconds=60 * 60):
            return redirect("accounts:password_reset_done")
        return super().post(request, *args, **kwargs)
