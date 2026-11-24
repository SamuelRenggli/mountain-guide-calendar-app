import hmac
import re
from decimal import Decimal
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import DayRateTier, User


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label=_("E-Mail"), widget=forms.EmailInput(attrs={"autofocus": True,
                                                                                "autocomplete": "email"}))


class SignupForm(UserCreationForm):
    invite_code = forms.CharField(label=_("Einladungscode"), strip=True)
    accept_terms = forms.BooleanField(
        label=_("Ich akzeptiere die Nutzungsbedingungen von Tourchef inklusive Auftragsbearbeitung."),
        error_messages={"required": _("Bitte akzeptiere die Nutzungsbedingungen.")},
    )

    class Meta:
        model = User
        fields = ("display_name", "email", "language")

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"]
        expected = settings.GUIDE_SIGNUP_CODE
        if not expected or not hmac.compare_digest(code.encode(), expected.encode()):
            raise forms.ValidationError(_("Ungültiger Einladungscode."))
        return code


class ProfileForm(forms.ModelForm):
    current_password = forms.CharField(
        label=_("Aktuelles Passwort"), required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text=_("Nur nötig, wenn du E-Mail oder IBAN änderst."),
    )

    class Meta:
        model = User
        fields = ("display_name", "email", "phone", "language", "slug", "widget_allowed_origins",
                  "day_rate", "aspirant_day_rate", "iban", "vat_number", "vat_rate",
                  "cancel_free_days", "cancel_partial_days", "cancel_partial_percent", "guest_terms")
        help_texts = {
            "slug": _("Erscheint in der Widget-Adresse. Nur Kleinbuchstaben, Zahlen und Bindestriche."),
            "widget_allowed_origins": _("Websites, auf denen dein Widget eingebettet werden darf, "
                                        "z. B. https://www.mein-bergfuehrer.ch (eine pro Zeile). Leer = überall erlaubt."),
        }
        widgets = {"widget_allowed_origins": forms.Textarea(attrs={"rows": 3}),
                   "guest_terms": forms.Textarea(attrs={"rows": 8})}

    def clean_widget_allowed_origins(self):
        origins = []
        for raw in self.cleaned_data["widget_allowed_origins"].split():
            parts = urlsplit(raw)
            if parts.scheme not in ("https", "http") or not parts.netloc or parts.path not in ("", "/"):
                raise forms.ValidationError(_("Ungültige Adresse: %(url)s (Beispiel: https://www.example.ch)") % {"url": raw})
            origins.append(f"{parts.scheme}://{parts.netloc}".lower())
        return "\n".join(origins)

    def clean_iban(self):
        iban = re.sub(r"\s", "", self.cleaned_data["iban"]).upper()
        if iban and not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
            raise forms.ValidationError(_("Ungültige IBAN (Beispiel: CH93 0076 2011 6238 5295 7)."))
        return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))

    def clean(self):
        cleaned = super().clean()
        free, partial = cleaned.get("cancel_free_days"), cleaned.get("cancel_partial_days")
        if free is not None and partial is not None and partial > free:
            self.add_error("cancel_partial_days", _("Muss kleiner oder gleich der kostenlosen Frist sein."))
        # The login email receives password resets and the IBAN receives money: changing either needs the password.
        # (self.instance still holds the saved values here; the form copies the new ones in after clean().)
        sensitive = [name for name in ("email", "iban") if name in cleaned and cleaned[name] != getattr(self.instance, name)]
        if sensitive and not self.instance.check_password(cleaned.get("current_password") or ""):
            self.add_error("current_password", _("Zum Ändern von E-Mail oder IBAN brauchen wir dein aktuelles Passwort."))
        return cleaned


class RateTableForm(forms.Form):
    """Day rate per group size: one row per size from 2 to 8 persons (1 person is the profile's day rate), plus own
    sizes for bigger groups. An empty row means "same as the size before". Stored as DayRateTier ("from n persons")."""

    TABLE_MAX = 8
    EXTRA_ROWS = 3

    def __init__(self, *args, guide, **kwargs):
        super().__init__(*args, **kwargs)
        self.guide = guide
        stored = {t.min_participants: t.day_rate for t in guide.rate_tiers.all()}
        for n in range(2, self.TABLE_MAX + 1):
            self.fields[f"rate_{n}"] = forms.DecimalField(
                required=False, min_value=0, max_digits=7, decimal_places=2, initial=stored.get(n),
                widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))
        extra = sorted((n, rate) for n, rate in stored.items() if n > self.TABLE_MAX)
        for i in range(self.EXTRA_ROWS):
            n, rate = extra[i] if i < len(extra) else (None, None)
            self.fields[f"size_{i}"] = forms.IntegerField(
                required=False, min_value=self.TABLE_MAX + 1, max_value=50, initial=n,
                widget=forms.NumberInput(attrs={"placeholder": str(self.TABLE_MAX + 1 + i)}))
            self.fields[f"extra_rate_{i}"] = forms.DecimalField(
                required=False, min_value=0, max_digits=7, decimal_places=2, initial=rate,
                widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))

    def clean(self):
        cleaned = super().clean()
        sizes = set()
        for i in range(self.EXTRA_ROWS):
            n, rate = cleaned.get(f"size_{i}"), cleaned.get(f"extra_rate_{i}")
            if (n is None) != (rate is None):
                self.add_error(f"size_{i}" if n is None else f"extra_rate_{i}",
                               _("Bitte Gruppengrösse und Tagesansatz angeben."))
            elif n is not None:
                if n in sizes:
                    self.add_error(f"size_{i}", _("Diese Gruppengrösse ist schon erfasst."))
                sizes.add(n)
        return cleaned

    def rows(self):
        """For the template: (persons, field, rate that applies when the field is left empty)."""
        effective, rows = self.guide.day_rate, []
        for n in range(2, self.TABLE_MAX + 1):
            field = self[f"rate_{n}"]
            value = field.value()
            rows.append((n, field, effective))
            if value not in (None, ""):
                try:
                    effective = Decimal(str(value))
                except ArithmeticError:
                    pass
        return rows

    def extra_rows(self):
        return [(self[f"size_{i}"], self[f"extra_rate_{i}"]) for i in range(self.EXTRA_ROWS)]

    def save(self):
        d = self.cleaned_data
        tiers = {n: d[f"rate_{n}"] for n in range(2, self.TABLE_MAX + 1) if d.get(f"rate_{n}") is not None}
        for i in range(self.EXTRA_ROWS):
            if d.get(f"size_{i}") is not None and d.get(f"extra_rate_{i}") is not None:
                tiers[d[f"size_{i}"]] = d[f"extra_rate_{i}"]
        self.guide.rate_tiers.all().delete()
        DayRateTier.objects.bulk_create(DayRateTier(guide=self.guide, min_participants=n, day_rate=rate)
                                        for n, rate in sorted(tiers.items()))


class CalendarSyncForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("ical_url", "ical_block_all")
        help_texts = {
            "ical_url": _("Google Kalender: Einstellungen → dein Kalender → «Privatadresse im iCal-Format». "
                          "Belegte Tage werden automatisch für Anfragen gesperrt.")
        }

    def clean_ical_url(self):
        url = self.cleaned_data["ical_url"].strip()
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if url and not url.startswith("https://"):
            raise forms.ValidationError(_("Die Adresse muss mit https:// beginnen."))
        return url


class CodeForm(forms.Form):
    code = forms.CharField(
        label=_("Code aus der App"), max_length=20,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}),
        help_text=_("6 Ziffern – oder einer deiner Wiederherstellungscodes."),
    )


class ReauthForm(forms.Form):
    password = forms.CharField(label=_("Passwort"), widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    code = forms.CharField(label=_("Code aus der App"), max_length=20, required=False,
                           widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}))
