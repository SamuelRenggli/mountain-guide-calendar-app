import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Func, Q

from accounts.models import LANGUAGE_CHOICES


class DateRange(Func):
    function = "DATERANGE"
    output_field = DateRangeField()


class Tour(models.Model):
    class PriceMode(models.TextChoices):
        PER_GROUP = "group", "Pauschal pro Gruppe"
        PER_PERSON = "person", "Pro Person"

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tours")
    title = models.CharField("Titel", max_length=200)
    description = models.TextField("Beschreibung", blank=True, validators=[MaxLengthValidator(5000)])
    duration_days = models.PositiveSmallIntegerField(
        "Dauer (Tage)", default=1, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    price = models.DecimalField("Preis (CHF)", max_digits=9, decimal_places=2, validators=[MinValueValidator(0)])
    price_mode = models.CharField("Preisart", max_length=10, choices=PriceMode.choices, default=PriceMode.PER_GROUP)
    max_participants = models.PositiveSmallIntegerField(
        "Max. Teilnehmende", default=4, validators=[MinValueValidator(1), MaxValueValidator(50)]
    )
    is_active = models.BooleanField("Im Widget anzeigen", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def price_for(self, participants: int) -> Decimal:
        total = self.price * participants if self.price_mode == self.PriceMode.PER_PERSON else self.price
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def end_date_for(self, start_date):
        return start_date + timedelta(days=self.duration_days - 1)


class BlockedPeriod(models.Model):
    """Days the guide blocked manually in the dashboard."""

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocked_periods")
    start_date = models.DateField("Von")
    end_date = models.DateField("Bis")
    note = models.CharField("Notiz", max_length=200, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="blocked_end_after_start")]


class ExternalBusy(models.Model):
    """Busy days imported from the guide's iCal feed. Fully replaced on every sync."""

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="external_busy")
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        indexes = [models.Index(fields=["guide", "start_date"])]


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Booking(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Anfrage offen"
        OFFERED = "offered", "Gegenangebot gesendet"
        AWAITING_PAYMENT = "awaiting_payment", "Bestätigt – Zahlung offen"
        PAID = "paid", "Bezahlt"
        DECLINED = "declined", "Vom Gast abgelehnt"
        REJECTED = "rejected", "Abgelehnt"
        CANCELLED = "cancelled", "Storniert"
        EXPIRED = "expired", "Abgelaufen"

    # Bookings in these states hold their dates; overlapping ones are rejected by the database.
    ACTIVE_STATUSES = (Status.REQUESTED, Status.OFFERED, Status.AWAITING_PAYMENT, Status.PAID)

    class PaymentMethod(models.TextChoices):
        STRIPE = "stripe", "Online (Stripe)"
        MANUAL = "manual", "Manuell erfasst"

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookings")
    tour = models.ForeignKey(Tour, on_delete=models.PROTECT, related_name="bookings")
    start_date = models.DateField()
    end_date = models.DateField()
    participants = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(max_digits=9, decimal_places=2)

    guest_name = models.CharField(max_length=100)
    guest_email = models.EmailField()
    guest_phone = models.CharField(max_length=30, blank=True)
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default="de")  # guest's email language
    guest_message = models.TextField(blank=True)
    guide_message = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    deadline = models.DateTimeField(null=True, blank=True)  # when the current status expires
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)

    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="booking_end_after_start"),
            ExclusionConstraint(
                name="booking_no_overlap",
                expressions=[
                    (DateRange("start_date", "end_date", RangeBoundary(inclusive_upper=True)), RangeOperators.OVERLAPS),
                    ("guide", RangeOperators.EQUAL),
                ],
                condition=Q(status__in=["requested", "offered", "awaiting_payment", "paid"]),
            ),
        ]

    def __str__(self):
        return f"{self.guest_name} – {self.tour} ({self.start_date:%d.%m.%Y})"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def guest_url(self) -> str:
        return f"{settings.SITE_URL}/b/{self.token}/"

    @property
    def amount_cents(self) -> int:
        return int((self.price * 100).to_integral_value(rounding=ROUND_HALF_UP))


class BookingEvent(models.Model):
    """Audit trail of everything that happened to a booking."""

    class Actor(models.TextChoices):
        GUEST = "guest", "Gast"
        GUIDE = "guide", "Bergführer"
        SYSTEM = "system", "System"
        STRIPE = "stripe", "Stripe"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
