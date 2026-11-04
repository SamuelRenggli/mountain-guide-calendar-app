from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking, Departure, Tour


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
    settings.REQUIRE_GUIDE_2FA = False  # tested separately in test_security.py
    settings.FIELD_ENCRYPTION_KEYS = []
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


def at(day, hour=18):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


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
def stripe_guide(guide, settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    guide.stripe_account_id = "acct_guide"
    guide.stripe_charges_enabled = True
    guide.phone = "+41 79 123 45 67"
    guide.save()
    return guide


@pytest.fixture
def tour(guide):
    return Tour.objects.create(guide=guide, title="Piz Palü", price_per_person=Decimal("450.00"), duration_days=2,
                               min_participants=2, max_participants=4, meeting_point="Diavolezza Talstation",
                               meeting_time=time(7, 30), equipment="Steigeisen, Pickel")


@pytest.fixture
def make_booking(db):
    """A request for a custom tour (no scheduled date)."""

    def make(guide, start, status=Booking.Status.REQUESTED, **extra):
        values = dict(
            guide=guide, start_date=start, end_date=start, participants=2, price=Decimal("0"),
            guest_name="Gast", guest_email="gast@example.com", status=status,
        )
        values.update(extra)
        return Booking.objects.create(**values)

    return make


@pytest.fixture
def make_departure(db):
    def make(guide, start=None, **extra):
        start = start or in_days(30)
        values = dict(
            guide=guide, title="Gran Paradiso", start_date=start, end_date=start + timedelta(days=1),
            price_per_person=Decimal("480.00"), min_participants=2, max_participants=4,
            signup_deadline=at(start - timedelta(days=3)), decision_deadline=at(start - timedelta(days=2)),
            meeting_point="Pont, Parkplatz", meeting_time=time(8, 0), equipment="Hochtourenausrüstung",
            requirements="Kondition für 1200 Hm",
        )
        values.update(extra)
        return Departure.objects.create(**values)

    return make


@pytest.fixture
def make_registration(db):
    """A paid registration for a scheduled date."""

    def make(departure, seats=1, status=Booking.Status.PAID, email="gast@example.com", **extra):
        price = departure.price_per_person * seats
        values = dict(
            guide=departure.guide, departure=departure, start_date=departure.start_date, end_date=departure.end_date,
            participants=seats, price=price, guest_name="Gast", guest_email=email, status=status,
            guest_phone="+41 79 000 00 00", emergency_contact="Mama, 079 111 11 11", experience="Mont Blanc 2024",
            cancel_free_days=14, cancel_partial_days=7, cancel_partial_percent=50,
        )
        if status == Booking.Status.PAID:
            values.update(amount_paid=price, paid_at=timezone.now(), payment_method="stripe",
                          stripe_payment_intent_id="pi_test")
        values.update(extra)
        return Booking.objects.create(**values)

    return make
