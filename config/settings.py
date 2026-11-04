"""Django settings. Everything environment-specific comes from env vars (see .env.example)."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    return int(env(name, str(default)))


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# Load .env for local development (docker compose passes vars directly). On Cloud Run, ENV_FILE
# points to the config mounted from Secret Manager.
_env_file = Path(os.environ.get("ENV_FILE", BASE_DIR / ".env"))
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

DEBUG = env_bool("DEBUG", False)

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key"

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", SITE_URL)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "core",
    "accounts",
    "bookings",
    "payments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.LanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.TwoFactorMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.site",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", "postgres://tourchef:tourchef@localhost:5432/tourchef"),
        conn_max_age=60,
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FORMS_URLFIELD_ASSUME_HTTPS = True

# Shared across gunicorn workers so rate limits hold (table created by `createcachetable`).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_table",
    }
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de"
LANGUAGES = [("de", "Deutsch"), ("fr", "Français"), ("it", "Italiano"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = "tourchef_lang"
LANGUAGE_COOKIE_SECURE = not DEBUG
LANGUAGE_COOKIE_SAMESITE = "Lax"
TIME_ZONE = "Europe/Zurich"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --- Email ---
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Tourchef <noreply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)
ADMINS = [("Admin", email) for email in env_list("ADMIN_EMAILS")]

# --- Security ---
NUM_PROXIES = env_int("NUM_PROXIES", 0)  # reverse proxies in front of the app (for client IP)
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"  # guest links carry a secret token
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
# Deliberate: the app runs on a shared *.run.app host, so HSTS can't cover subdomains or be preloaded.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# Phone, emergency contact and experience of guests are encrypted in the database (12-datenschutz.md, level B).
# Comma-separated Fernet keys, newest first; older keys only decrypt (rotation). Lives in Secret Manager,
# apart from the database. Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEYS = env_list("FIELD_ENCRYPTION_KEYS")
if not FIELD_ENCRYPTION_KEYS and not DEBUG:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS must be set when DEBUG is off.")

# Two-factor login (TOTP app). Staff always need it; guides too unless this is switched off.
REQUIRE_GUIDE_2FA = env_bool("REQUIRE_GUIDE_2FA", True)
REAUTH_SECONDS = 10 * 60  # how long a re-entered password / code unlocks exports

ADMIN_URL = env("ADMIN_URL", "admin/")
GUIDE_SIGNUP_CODE = env("GUIDE_SIGNUP_CODE", "")  # empty = signup closed

# --- Booking rules ---
REQUEST_RESPONSE_DAYS = env_int("REQUEST_RESPONSE_DAYS", 5)  # guide must answer within
GUEST_RESPONSE_DAYS = env_int("GUEST_RESPONSE_DAYS", 5)  # guest must accept offer / new date within
MAX_REQUEST_DAYS = env_int("MAX_REQUEST_DAYS", 14)  # longest tour a guest can request in the widget
CHECKOUT_HOLD_MINUTES = 35  # seats are held while the guest pays (Stripe Checkout lives 30 min)
DECISION_REMINDER_HOURS = env_int("DECISION_REMINDER_HOURS", 3)  # remind the guide before the decision deadline
GUEST_LINK_VALID_DAYS = env_int("GUEST_LINK_VALID_DAYS", 365)  # guest page closes this long after a finished tour

# Operator of the platform (Impressum, privacy policy)
OPERATOR_NAME = env("OPERATOR_NAME", "Samuel Renggli")
OPERATOR_ADDRESS = env("OPERATOR_ADDRESS", "[Adresse eintragen]")
PRIVACY_EMAIL = env("PRIVACY_EMAIL", "[datenschutz@…]")

# --- Stripe (Connect, destination charges) ---
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", "")
STRIPE_CONNECT_WEBHOOK_SECRET = env("STRIPE_CONNECT_WEBHOOK_SECRET", "")
PLATFORM_FEE_PERCENT = env("PLATFORM_FEE_PERCENT", "0")
# Card and TWINT only, no bank transfer (decision 37). Drop "twint" if the Stripe account can't offer it.
STRIPE_PAYMENT_METHODS = env_list("STRIPE_PAYMENT_METHODS", "card,twint")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
