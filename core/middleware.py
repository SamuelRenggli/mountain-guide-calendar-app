from urllib.parse import quote

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation

DEFAULT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self' https://checkout.stripe.com https://connect.stripe.com; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)

LANGUAGE_CODES = [code for code, _ in settings.LANGUAGES]


def widget_csp(frame_ancestors: str) -> str:
    return DEFAULT_CSP.replace("frame-ancestors 'none'", f"frame-ancestors {frame_ancestors}")


class ContentSecurityPolicyMiddleware:
    """Adds a strict CSP unless the view already set one (the embeddable widget does)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/" + settings.ADMIN_URL):
            return response  # Django admin uses inline scripts
        response.headers.setdefault("Content-Security-Policy", DEFAULT_CSP)
        return response


def supported(code) -> str | None:
    code = (code or "").lower()[:2]
    return code if code in LANGUAGE_CODES else None


def browser_language(request) -> str:
    for part in request.headers.get("Accept-Language", "").split(","):
        if code := supported(part.split(";")[0].strip()):
            return code
    return settings.LANGUAGE_CODE


class LanguageMiddleware:
    """Picks the interface language: ?lang= (remembered in a cookie), else a logged-in guide's profile
    language, else the cookie, else the browser language (R-011). Views may switch it further."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        chosen = supported(request.GET.get("lang"))
        user = getattr(request, "user", None)
        if chosen:
            lang = chosen
        elif user is not None and user.is_authenticated and not request.path.startswith(("/w/", "/b/", "/t/")):
            lang = supported(user.language) or settings.LANGUAGE_CODE
        else:
            lang = supported(request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)) or browser_language(request)
        request.language_from_url = bool(chosen)
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", translation.get_language())
        if chosen:
            response.set_cookie(
                settings.LANGUAGE_COOKIE_NAME, chosen, max_age=365 * 24 * 3600, secure=settings.LANGUAGE_COOKIE_SECURE,
                samesite="None" if settings.LANGUAGE_COOKIE_SECURE else "Lax", httponly=True,
            )
        return response


# Paths that never need a second factor: public guest pages, legal texts, webhooks, assets.
TWO_FACTOR_EXEMPT_PREFIXES = ("/static/", "/healthz", "/w/", "/b/", "/t/", "/rechtliches/", "/zahlungen/stripe/webhook/")


def needs_two_factor(user) -> bool:
    return user.is_staff or settings.REQUIRE_GUIDE_2FA


class TwoFactorMiddleware:
    """After the password, a logged-in user must enter a TOTP code (or set up 2FA first)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not request.path.startswith(TWO_FACTOR_EXEMPT_PREFIXES):
            allowed = {reverse("accounts:logout"), reverse("accounts:2fa_verify"), reverse("accounts:2fa_setup")}
            if request.path not in allowed:
                if user.totp_enabled and not request.session.get("2fa_ok"):
                    return redirect(f"{reverse('accounts:2fa_verify')}?next={quote(request.get_full_path())}")
                if not user.totp_enabled and needs_two_factor(user):
                    return redirect("accounts:2fa_setup")
        return self.get_response(request)
