from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking, Tour


@pytest.fixture(autouse=True)
def test_settings(settings):
    settings.STRIPE_SECRET_KEY = ""
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    settings.STRIPE_CONNECT_WEBHOOK_SECRET = ""
    settings.PLATFORM_FEE_PERCENT = "10"
    settings.SITE_URL = "https://tourchef.test"
    settings.GUIDE_SIGNUP_CODE = "bergfuehrer-2026"
    settings.REQUEST_RESPONSE_DAYS = 5
    settings.GUEST_RESPONSE_DAYS = 5
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    cache.clear()
    yield
    cache.clear()


def in_days(n):
    return timezone.localdate() + timedelta(days=n)


@pytest.fixture
def make_guide(db):
    def make(name="Anna Berg", email=None, **extra):
        email = email or f"{name.split()[0].lower()}@example.ch"
        return User.objects.create_user(email=email, password="sehr-geheimes-pw-123", display_name=name, **extra)

    return make


@pytest.fixture
def guide(make_guide):
    return make_guide()


@pytest.fixture
def other_guide(make_guide):
    return make_guide("Bruno Fels")


@pytest.fixture
def tour(guide):
    return Tour.objects.create(guide=guide, title="Piz Palü", price=Decimal("600.00"), max_participants=4)


@pytest.fixture
def make_booking(db):
    def make(tour, start, status=Booking.Status.REQUESTED, **extra):
        values = dict(
            guide=tour.guide, tour=tour, start_date=start, end_date=tour.end_date_for(start), participants=2,
            price=tour.price, guest_name="Gast", guest_email="gast@example.com", status=status,
        )
        values.update(extra)
        return Booking.objects.create(**values)

    return make
