import time
from functools import wraps
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from core.middleware import needs_two_factor
from core.ratelimit import allow, client_ip

from . import twofactor
from .forms import CodeForm, LoginForm, ReauthForm, SignupForm


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if not allow(f"login:{client_ip(request)}", limit=10, window_seconds=15 * 60):
            messages.error(request, _("Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen."))
            return redirect("accounts:login")
        return super().post(request, *args, **kwargs)


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    if not settings.GUIDE_SIGNUP_CODE:
        return render(request, "accounts/signup_closed.html")
    if request.method == "POST":
        if not allow(f"signup:{client_ip(request)}", limit=5, window_seconds=60 * 60):
            messages.error(request, _("Zu viele Versuche. Bitte später erneut versuchen."))
            return redirect("accounts:signup")
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, _("Willkommen! Richte zuerst die Zwei-Faktor-Anmeldung ein."))
            return redirect("dashboard:settings")
    else:
        form = SignupForm(initial={"language": request.LANGUAGE_CODE})
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


# --- Two-factor -------------------------------------------------------------


def _safe_next(request, default="dashboard:home"):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse(default)


@login_required
def two_factor_verify(request):
    user = request.user
    if not user.totp_enabled:
        return redirect("accounts:2fa_setup")
    form = CodeForm(request.POST or None)
    if request.method == "POST":
        if not allow(f"2fa:{user.pk}", limit=5, window_seconds=5 * 60):
            messages.error(request, _("Zu viele Versuche. Bitte in 5 Minuten erneut versuchen."))
        elif form.is_valid() and twofactor.verify(user, form.cleaned_data["code"]):
            request.session["2fa_ok"] = True
            request.session["reauth_at"] = time.time()
            return redirect(_safe_next(request))
        else:
            form.add_error("code", _("Der Code stimmt nicht."))
    return render(request, "accounts/2fa_verify.html", {"form": form, "next": _safe_next(request)})


@login_required
def two_factor_setup(request):
    """Scan the QR code, confirm with a code, note the recovery codes."""
    user = request.user
    if user.totp_enabled and not request.session.get("2fa_ok"):
        return redirect("accounts:2fa_verify")
    if user.totp_enabled and time.time() - request.session.get("reauth_at", 0) > settings.REAUTH_SECONDS:
        # Replacing the second factor: password and current code again, so a stolen session can't take the account.
        return redirect(f"{reverse('accounts:reauth')}?next={quote(request.get_full_path())}")
    secret = request.session.get("2fa_setup_secret") or twofactor.new_secret()
    request.session["2fa_setup_secret"] = secret
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if twofactor.check_setup_code(secret, form.cleaned_data["code"], user.pk):
            user.totp_secret, user.totp_enabled = secret, True
            codes = twofactor.new_recovery_codes(user)
            user.save(update_fields=["totp_secret", "totp_enabled", "recovery_codes"])
            del request.session["2fa_setup_secret"]
            request.session["2fa_ok"] = True
            return render(request, "accounts/2fa_codes.html", {"codes": codes})
        form.add_error("code", _("Der Code stimmt nicht. Prüfe die Uhrzeit deines Handys."))
    uri = twofactor.provisioning_uri(user, secret)
    return render(request, "accounts/2fa_setup.html", {
        "form": form, "qr": mark_safe(twofactor.qr_svg(uri)), "secret": secret,  # svg generated by segno, no user input
        "required": needs_two_factor(user), "replacing": user.totp_enabled,
    })


def recent_auth_required(view):
    """Exports and deleting guest data need the password (and code) again if the last check is old."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if time.time() - request.session.get("reauth_at", 0) > settings.REAUTH_SECONDS:
            if request.method != "GET":
                # A form post can't be repeated by a redirect: confirm, then send the form again from where it was.
                messages.info(request, _("Bitte bestätige zuerst dein Passwort und sende das Formular dann noch einmal."))
                return redirect(f"{reverse('accounts:reauth')}?next={quote(reverse('dashboard:settings'))}")
            return redirect(f"{reverse('accounts:reauth')}?next={quote(request.get_full_path())}")
        return view(request, *args, **kwargs)

    return wrapper


@login_required
def reauth(request):
    user = request.user
    form = ReauthForm(request.POST or None)
    if request.method == "POST":
        if not allow(f"reauth:{user.pk}", limit=5, window_seconds=5 * 60):
            messages.error(request, _("Zu viele Versuche. Bitte in 5 Minuten erneut versuchen."))
        elif form.is_valid() and user.check_password(form.cleaned_data["password"]) and (
            not user.totp_enabled or twofactor.verify(user, form.cleaned_data["code"])
        ):
            request.session["reauth_at"] = time.time()
            return redirect(_safe_next(request))
        else:
            form.add_error(None, _("Passwort oder Code stimmt nicht."))
    return render(request, "accounts/reauth.html", {"form": form, "next": _safe_next(request),
                                                    "needs_code": user.totp_enabled})
