import secrets
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeBoundary, RangeOperators
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Func, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import LANGUAGE_CHOICES
from core.fields import EncryptedTextField


class DateRange(Func):
    function = "DATERANGE"
    output_field = DateRangeField()


def money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(value) -> int:
    return int((Decimal(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


class TourDetails(models.Model):
    """What a guest needs to know about a tour. Shared by templates (Tour) and scheduled dates (Departure)."""

    title = models.CharField(_("Titel"), max_length=200)
    description = models.TextField(_("Beschreibung"), blank=True, validators=[MaxLengthValidator(5000)])
    requirements = models.TextField(_("Anforderungen"), blank=True, validators=[MaxLengthValidator(2000)],
                                    help_text=_("Niveau, Kondition, Erfahrung"))
    equipment = models.TextField(_("Ausrüstung"), blank=True, validators=[MaxLengthValidator(2000)])
    included = models.TextField(_("Inbegriffen"), blank=True, validators=[MaxLengthValidator(2000)],
                                help_text=_("z. B. Führung, Hütte mit Halbpension, Bahn, Material"))
    location = models.CharField(_("Ort / Gebiet"), max_length=200, blank=True)
    meeting_point = models.CharField(_("Treffpunkt"), max_length=200, blank=True)
    meeting_time = models.TimeField(_("Zeit"), null=True, blank=True)
    price_per_person = models.DecimalField(_("Preis pro Person (CHF)"), max_digits=9, decimal_places=2,
                                           validators=[MinValueValidator(0)])
    min_participants = models.PositiveSmallIntegerField(
        _("Mindestzahl"), default=2, validators=[MinValueValidator(1), MaxValueValidator(50)]
    )
    max_participants = models.PositiveSmallIntegerField(
        _("Höchstzahl"), default=6, validators=[MinValueValidator(1), MaxValueValidator(50)]
    )

    DETAIL_FIELDS = ("title", "description", "requirements", "equipment", "included", "location", "meeting_point",
                     "meeting_time", "price_per_person", "min_participants", "max_participants")

    class Meta:
        abstract = True

    def __str__(self):
        return self.title


class Tour(TourDetails):
    """A template the guide reuses for scheduled dates (R-100, R-103)."""

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tours")
    duration_days = models.PositiveSmallIntegerField(
        _("Dauer (Tage)"), default=1, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    is_active = models.BooleanField(_("Aktiv"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]
        constraints = [models.CheckConstraint(condition=Q(max_participants__gte=F("min_participants")),
                                              name="tour_max_gte_min")]

    def end_date_for(self, start_date):
        return start_date + timedelta(days=self.duration_days - 1)


def new_public_id() -> str:
    return secrets.token_urlsafe(6)


class Departure(TourDetails):
    """A scheduled tour date guests sign up for and pay right away (Termin, phase 1)."""

    class Status(models.TextChoices):
        OPEN = "open", _("Ausgeschrieben")
        CONFIRMED = "confirmed", _("Findet statt")
        CANCELLED = "cancelled", _("Abgesagt")

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="departures")
    tour = models.ForeignKey(Tour, on_delete=models.SET_NULL, null=True, blank=True, related_name="departures")
    public_id = models.CharField(max_length=16, unique=True, default=new_public_id, editable=False)
    start_date = models.DateField(_("Startdatum"))
    end_date = models.DateField(_("Enddatum"))
    signup_deadline = models.DateTimeField(_("Anmeldeschluss"))
    decision_deadline = models.DateTimeField(
        _("Entscheid-Frist"),
        help_text=_("Bis dann entscheidest du, ob der Termin stattfindet. Ohne Entscheid wird er automatisch abgesagt "
                    "und alle erhalten ihr Geld zurück."),
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="departure_end_after_start"),
            models.CheckConstraint(condition=Q(max_participants__gte=F("min_participants")),
                                   name="departure_max_gte_min"),
        ]

    @property
    def is_live(self) -> bool:
        return self.status in (self.Status.OPEN, self.Status.CONFIRMED)

    @property
    def is_decided(self) -> bool:
        return self.status != self.Status.OPEN

    def seats_taken(self) -> int:
        """Paid seats plus seats held while a guest is paying (R-123)."""
        held = Q(status__in=Booking.SEAT_STATUSES) | Q(status=Booking.Status.PAYMENT_PENDING, hold_until__gt=timezone.now())
        return self.registrations.filter(held).aggregate(n=Sum("participants"))["n"] or 0

    def seats_confirmed(self) -> int:
        return self.registrations.filter(status__in=Booking.SEAT_STATUSES).aggregate(n=Sum("participants"))["n"] or 0

    @property
    def seats_free(self) -> int:
        return max(self.max_participants - self.seats_taken(), 0)

    @property
    def minimum_reached(self) -> bool:
        return self.seats_confirmed() >= self.min_participants

    @property
    def signup_open(self) -> bool:
        return self.is_live and timezone.now() < self.signup_deadline and self.start_date > timezone.localdate()

    @property
    def public_url(self) -> str:
        return f"{settings.SITE_URL}/t/{self.public_id}/"

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class BlockedPeriod(models.Model):
    """Days the guide blocked manually in the dashboard."""

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocked_periods")
    start_date = models.DateField(_("Von"))
    end_date = models.DateField(_("Bis"))
    note = models.CharField(_("Notiz"), max_length=200, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="blocked_end_after_start")]


class OpenPeriod(models.Model):
    """Days the guide opened for bookings. Only used when the guide is bookable in open periods only."""

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="open_periods")
    start_date = models.DateField(_("Von"))
    end_date = models.DateField(_("Bis"))
    note = models.CharField(_("Notiz"), max_length=200, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="open_end_after_start")]


class ExternalBusy(models.Model):
    """Busy days imported from the guide's iCal feed. Fully replaced on every sync."""

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="external_busy")
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        indexes = [models.Index(fields=["guide", "start_date"])]


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


class Booking(models.Model):
    """Either a request for a custom tour (departure is empty) or a registration for a scheduled date."""

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Anfrage offen")
        OFFERED = "offered", _("Gegenangebot gesendet")
        CONFIRMED = "confirmed", _("Bestätigt")
        RESCHEDULE_PROPOSED = "reschedule", _("Ersatzdatum vorgeschlagen")
        PAYMENT_PENDING = "payment_pending", _("Zahlung läuft")
        AWAITING_PAYMENT = "awaiting_payment", _("Abgerechnet – Zahlung offen")
        PAID = "paid", _("Bezahlt")
        DECLINED = "declined", _("Vom Gast abgelehnt")
        REJECTED = "rejected", _("Abgelehnt")
        CANCELLED = "cancelled", _("Storniert")
        EXPIRED = "expired", _("Abgelaufen")

    # Requests in these states hold their dates; overlapping ones are rejected by the database.
    ACTIVE_STATUSES = (Status.REQUESTED, Status.OFFERED, Status.CONFIRMED, Status.RESCHEDULE_PROPOSED,
                       Status.AWAITING_PAYMENT, Status.PAID)
    # Agreed with the guest: the days are taken for sure.
    BOOKED_STATUSES = (Status.CONFIRMED, Status.AWAITING_PAYMENT, Status.PAID)
    # A registration for a scheduled date that counts against its seats (paid, or deciding on a new date).
    SEAT_STATUSES = (Status.PAID, Status.RESCHEDULE_PROPOSED)

    class PaymentMethod(models.TextChoices):
        STRIPE = "stripe", _("Online (Karte/TWINT)")
        MANUAL = "manual", _("Manuell erfasst")

    guide = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookings")
    tour = models.ForeignKey(Tour, on_delete=models.SET_NULL, related_name="bookings", null=True, blank=True)
    departure = models.ForeignKey(Departure, on_delete=models.PROTECT, related_name="registrations",
                                  null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    # Old requests could be a time window; new requests always have fixed dates (R-020).
    is_window = models.BooleanField(default=False)
    participants = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])  # seats for registrations
    price = models.DecimalField(max_digits=9, decimal_places=2)  # quoted price; invoice total once invoiced
    # How the guide arrived at the price (R-022): [{"label", "persons", "days", "rate", "amount"}]
    price_breakdown = models.JSONField(default=list, blank=True)
    estimated_expenses = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)  # R-023
    invoiced_at = models.DateTimeField(null=True, blank=True)

    guest_name = models.CharField(max_length=100)
    guest_email = models.EmailField()
    guest_phone = EncryptedTextField(max_length=30)
    emergency_contact = EncryptedTextField(max_length=200)
    experience = EncryptedTextField(max_length=500)
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default="de")  # guest's email language
    guest_message = models.TextField(blank=True)
    guide_message = models.TextField(blank=True)

    # The guest accepted the guide's terms (R-002); the cancellation tiers are frozen at that moment.
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    cancel_free_days = models.PositiveSmallIntegerField(null=True, blank=True)
    cancel_partial_days = models.PositiveSmallIntegerField(null=True, blank=True)
    cancel_partial_percent = models.PositiveSmallIntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    deadline = models.DateTimeField(null=True, blank=True)  # when the current status expires
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)

    # Weather cancellation with a replacement date (R-025/R-026): costs the guest pays if the new date doesn't work
    incurred_costs = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    incurred_costs_note = models.CharField(max_length=200, blank=True)

    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    hold_until = models.DateTimeField(null=True, blank=True)  # seats are held while paying
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    amount_paid = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    refunded_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    refund_due = models.DecimalField(max_digits=9, decimal_places=2, default=0)  # decided, not yet sent to Stripe

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
                condition=Q(
                    status__in=["requested", "offered", "confirmed", "reschedule", "awaiting_payment", "paid"],
                    is_window=False, departure__isnull=True,
                ),
            ),
        ]
        indexes = [models.Index(fields=["guide", "guest_email"])]

    def __str__(self):
        return f"{self.guest_name} – {self.title} ({self.start_date:%d.%m.%Y})"

    @property
    def title(self) -> str:
        if self.departure_id:
            return self.departure.title
        return self.tour.title if self.tour else str(_("Eigene Tourenidee"))

    @property
    def is_registration(self) -> bool:
        return self.departure_id is not None

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_invoiced(self) -> bool:
        return self.invoiced_at is not None

    @property
    def has_price(self) -> bool:
        """The guide has quoted a price (when confirming or offering); before that only the day rate applies."""
        return self.price > 0

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def guest_url(self) -> str:
        return f"{settings.SITE_URL}/b/{self.token}/"

    @property
    def amount_cents(self) -> int:
        return cents(self.price)

    @property
    def refundable(self) -> Decimal:
        return money(self.amount_paid - self.refunded_amount)

    @property
    def breakdown_total(self) -> Decimal:
        return money(sum((Decimal(str(row["amount"])) for row in self.price_breakdown), Decimal(0)))

    @property
    def has_sensitive_data(self) -> bool:
        return bool(self.guest_phone or self.emergency_contact or self.experience)

    def vat_included(self, amount=None) -> Decimal | None:
        """VAT contained in a gross amount, if the guide charges VAT (R-202)."""
        guide = self.guide
        if not guide.charges_vat:
            return None
        gross = Decimal(self.price if amount is None else amount)
        return money(gross * guide.vat_rate / (100 + guide.vat_rate))


class WaitlistEntry(models.Model):
    """A guest waiting for a seat on a full scheduled date (R-130). Everyone is told when a seat frees up (R-131)."""

    departure = models.ForeignKey(Departure, on_delete=models.CASCADE, related_name="waitlist")
    name = models.CharField(max_length=100)
    email = models.EmailField()
    seats = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default="de")
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["departure", "email"], name="one_waitlist_entry_per_email")]


class InvoiceLine(models.Model):
    """One line of the bill the guide sends after the tour: the tour price or an expense."""

    class Kind(models.TextChoices):
        TOUR = "tour", _("Tourenpreis")
        EXPENSE = "expense", _("Spesen")

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="invoice_lines")
    position = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=12, choices=Kind.choices)
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=6, decimal_places=1)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2)

    class Meta:
        ordering = ["position"]

    @property
    def amount(self) -> Decimal:
        return money(self.quantity * self.unit_price)


class BookingEvent(models.Model):
    """Audit trail of everything that happened to a booking."""

    class Actor(models.TextChoices):
        GUEST = "guest", _("Gast")
        GUIDE = "guide", _("Bergführer")
        SYSTEM = "system", _("System")
        STRIPE = "stripe", _("Stripe")

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "id"]
