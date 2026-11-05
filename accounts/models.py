from decimal import Decimal

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.fields import EncryptedTextField

ALL_WEEKDAYS = "0123456"  # Monday = 0
LANGUAGE_CHOICES = [("de", "Deutsch"), ("fr", "Français"), ("it", "Italiano"), ("en", "English")]

# Cancellation template (R-003, SBV terms): free until 14 days before, 50 % until 7 days, then 100 %.
DEFAULT_CANCEL_FREE_DAYS = 14
DEFAULT_CANCEL_PARTIAL_DAYS = 7
DEFAULT_CANCEL_PARTIAL_PERCENT = 50


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("E-Mail ist erforderlich.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """A mountain guide (or platform staff). Logs in with email."""

    username = None
    first_name = None
    last_name = None
    email = models.EmailField(_("E-Mail"), unique=True)
    display_name = models.CharField(_("Name"), max_length=100)
    phone = models.CharField(_("Telefon"), max_length=30, blank=True,
                             help_text=_("Steht in der Bestätigung an angemeldete Gäste."))
    language = models.CharField(_("Sprache"), max_length=2, choices=LANGUAGE_CHOICES, default="de",
                                help_text=_("Sprache des Dashboards und deiner E-Mails."))
    slug = models.SlugField(_("Widget-Kennung"), max_length=60, unique=True)

    # Availability rules
    available_weekdays = models.CharField(max_length=7, default=ALL_WEEKDAYS)
    min_notice_days = models.PositiveSmallIntegerField(
        _("Mindestvorlauf (Tage)"), default=2, validators=[MaxValueValidator(90)]
    )
    max_advance_days = models.PositiveSmallIntegerField(
        _("Buchbar im Voraus (Tage)"), default=365, validators=[MinValueValidator(7), MaxValueValidator(730)]
    )

    # Billing: the guest sees the day rate up front, the guide bills after the tour
    day_rate = models.DecimalField(
        _("Tagesansatz Bergführer (CHF)"), max_digits=7, decimal_places=2, default=750, validators=[MinValueValidator(0)],
        help_text=_("Tagesansatz für kleine Gruppen; für grössere Gruppen legst du unten Stufen fest. "
                    "Vorgabe im Preisrechner, auch für einen zweiten Bergführer."),
    )
    aspirant_day_rate = models.DecimalField(
        _("Tagesansatz Aspirant (CHF)"), max_digits=7, decimal_places=2, default=500, validators=[MinValueValidator(0)],
        help_text=_("Vorgabe im Preisrechner."),
    )
    iban = models.CharField(
        _("IBAN für Überweisungen"), max_length=42, blank=True,  # stored in groups of four
        help_text=_("Steht auf der Rechnung, damit Gäste überweisen können."),
    )
    vat_number = models.CharField(
        _("MWST-Nummer"), max_length=30, blank=True,
        help_text=_("Nur wenn du mehrwertsteuerpflichtig bist, z. B. CHE-123.456.789 MWST. "
                    "Rechnungen und Belege weisen dann die MWST aus."),
    )
    vat_rate = models.DecimalField(
        _("MWST-Satz (%)"), max_digits=4, decimal_places=2, default=Decimal("8.1"),
        validators=[MinValueValidator(0), MaxValueValidator(30)],
        help_text=_("Deine Preise verstehen sich inklusive MWST."),
    )

    # Terms for guests (R-002–R-004). Without own terms the cancellation template applies.
    cancel_free_days = models.PositiveSmallIntegerField(
        _("Kostenlos stornierbar bis (Tage vor Tourbeginn)"), default=DEFAULT_CANCEL_FREE_DAYS,
        validators=[MaxValueValidator(365)],
    )
    cancel_partial_days = models.PositiveSmallIntegerField(
        _("Teilweise Kosten bis (Tage vor Tourbeginn)"), default=DEFAULT_CANCEL_PARTIAL_DAYS,
        validators=[MaxValueValidator(365)],
    )
    cancel_partial_percent = models.PositiveSmallIntegerField(
        _("Storno-Kosten in dieser Zeit (%)"), default=DEFAULT_CANCEL_PARTIAL_PERCENT,
        validators=[MaxValueValidator(100)],
        help_text=_("Danach 100 %. Bei einer Absage wegen Wetter oder durch dich: 0 %."),
    )
    guest_terms = models.TextField(
        _("Eigene Bedingungen für Gäste"), blank=True, max_length=20000,
        help_text=_("Optional. Ersetzt den Text der Tourchef-Vorlage; Gäste akzeptieren ihn bei jeder Anfrage und "
                    "Anmeldung. Die automatische Rückzahlung richtet sich weiterhin nach den Storno-Stufen oben."),
    )

    # External calendar (iCal/ICS) — the URL is a secret, never shown publicly
    ical_url = models.URLField(_("iCal-Adresse"), max_length=1000, blank=True)
    ical_last_synced_at = models.DateTimeField(null=True, blank=True)
    ical_last_error = models.CharField(max_length=300, blank=True)
    ical_block_all = models.BooleanField(
        _("Auch «verfügbare» Termine sperren"), default=False,
        help_text=_("Normalerweise sperren nur Termine, die im Kalender als «beschäftigt» markiert sind. "
                    "Aktivieren, wenn jeder Termin die Tage sperren soll."),
    )

    # Widget embedding: space-separated origins allowed to frame the widget, empty = any
    widget_allowed_origins = models.TextField(_("Erlaubte Websites"), blank=True)

    # Stripe Connect
    stripe_account_id = models.CharField(max_length=100, blank=True)
    stripe_charges_enabled = models.BooleanField(default=False)

    # Two-factor login (TOTP). The secret is encrypted; recovery codes are stored as hashes.
    totp_secret = EncryptedTextField()
    totp_enabled = models.BooleanField(default=False)
    recovery_codes = models.TextField(blank=True, editable=False)  # space-separated SHA-256 hashes

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    objects = UserManager()

    def __str__(self):
        return self.display_name or self.email

    def get_full_name(self):
        return self.display_name

    def get_short_name(self):
        return self.display_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        base = slugify(self.display_name or self.email.split("@")[0])[:50] or "guide"
        slug, n = base, 2
        while User.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug, n = f"{base}-{n}", n + 1
        return slug

    @property
    def weekday_set(self) -> set[int]:
        return {int(c) for c in self.available_weekdays}

    @property
    def uses_open_periods(self) -> bool:
        """Once the guide has opened periods that aren't over yet, guests can only book inside them."""
        return self.open_periods.filter(end_date__gte=timezone.localdate()).exists()

    def rate_for(self, participants: int) -> Decimal:
        """Day rate for a group of this size (R-200): the highest tier the group reaches."""
        rate = self.day_rate
        for tier in self.rate_tiers.all():  # ordered by min_participants
            if participants >= tier.min_participants:
                rate = tier.day_rate
        return rate

    def rate_table(self) -> list[tuple[int, int | None, Decimal]]:
        """[(from, to or None, rate)] for showing the tiers, starting at 1 person."""
        bounds = [(1, self.day_rate)] + [(t.min_participants, t.day_rate) for t in self.rate_tiers.all()]
        return [(lo, bounds[i + 1][0] - 1 if i + 1 < len(bounds) else None, rate) for i, (lo, rate) in enumerate(bounds)]

    @property
    def has_own_terms(self) -> bool:
        return bool(self.guest_terms.strip())

    @property
    def charges_vat(self) -> bool:
        return bool(self.vat_number.strip())

    @property
    def can_take_card_payments(self) -> bool:
        from django.conf import settings

        return bool(settings.STRIPE_SECRET_KEY and self.stripe_account_id and self.stripe_charges_enabled)


class DayRateTier(models.Model):
    """From this group size on, the guide's day rate is `day_rate` (R-200), e.g. from 5 → 850, from 7 → 950."""

    guide = models.ForeignKey(User, on_delete=models.CASCADE, related_name="rate_tiers")
    min_participants = models.PositiveSmallIntegerField(
        _("ab Personen"), validators=[MinValueValidator(2), MaxValueValidator(50)]
    )
    day_rate = models.DecimalField(_("Tagesansatz (CHF)"), max_digits=7, decimal_places=2,
                                   validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["min_participants"]
        constraints = [models.UniqueConstraint(fields=["guide", "min_participants"], name="one_tier_per_size")]


class AccessLog(models.Model):
    """Who looked at, exported or deleted sensitive guest data (12-datenschutz.md, level B)."""

    class Action(models.TextChoices):
        VIEW_BOOKING = "view_booking", _("Buchung mit Kontaktdaten angesehen")
        VIEW_PARTICIPANTS = "view_participants", _("Teilnehmerliste mit Notfallkontakten angesehen")
        EXPORT = "export", _("Export heruntergeladen")
        ERASE = "erase", _("Personendaten gelöscht")
        ADMIN_STATS = "admin_stats", _("Pilot-Auswertung angesehen")

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="access_logs")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    object_ref = models.CharField(max_length=100, blank=True)  # e.g. "booking:12", "departure:3"
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def record(cls, request, action, object_ref=""):
        from core.ratelimit import client_ip

        cls.objects.create(user=request.user, action=action, object_ref=object_ref, ip=client_ip(request) or None)
