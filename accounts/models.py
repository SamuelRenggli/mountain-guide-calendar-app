from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify

ALL_WEEKDAYS = "0123456"  # Monday = 0
LANGUAGE_CHOICES = [("de", "Deutsch"), ("en", "English")]


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
    email = models.EmailField("E-Mail", unique=True)
    display_name = models.CharField("Name", max_length=100)
    phone = models.CharField("Telefon", max_length=30, blank=True)
    language = models.CharField("Sprache der E-Mails", max_length=2, choices=LANGUAGE_CHOICES, default="de")
    slug = models.SlugField("Widget-Kennung", max_length=60, unique=True)

    # Availability rules
    available_weekdays = models.CharField(max_length=7, default=ALL_WEEKDAYS)
    min_notice_days = models.PositiveSmallIntegerField(
        "Mindestvorlauf (Tage)", default=2, validators=[MaxValueValidator(90)]
    )
    max_advance_days = models.PositiveSmallIntegerField(
        "Buchbar im Voraus (Tage)", default=365, validators=[MinValueValidator(7), MaxValueValidator(730)]
    )

    # External calendar (iCal/ICS) — the URL is a secret, never shown publicly
    ical_url = models.URLField("iCal-Adresse", max_length=1000, blank=True)
    ical_last_synced_at = models.DateTimeField(null=True, blank=True)
    ical_last_error = models.CharField(max_length=300, blank=True)

    # Widget embedding: space-separated origins allowed to frame the widget, empty = any
    widget_allowed_origins = models.TextField("Erlaubte Websites", blank=True)

    # Stripe Connect
    stripe_account_id = models.CharField(max_length=100, blank=True)
    stripe_charges_enabled = models.BooleanField(default=False)

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
    def can_take_card_payments(self) -> bool:
        from django.conf import settings

        return bool(settings.STRIPE_SECRET_KEY and self.stripe_account_id and self.stripe_charges_enabled)
