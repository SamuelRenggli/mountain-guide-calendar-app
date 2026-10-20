import hmac
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm

from .models import User

WEEKDAY_CHOICES = [(str(i), name) for i, name in enumerate(["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"])]


class SignupForm(UserCreationForm):
    invite_code = forms.CharField(label="Einladungscode", strip=True)

    class Meta:
        model = User
        fields = ("display_name", "email")

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"]
        expected = settings.GUIDE_SIGNUP_CODE
        if not expected or not hmac.compare_digest(code.encode(), expected.encode()):
            raise forms.ValidationError("Ungültiger Einladungscode.")
        return code


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("display_name", "email", "phone", "language", "slug", "widget_allowed_origins")
        help_texts = {
            "slug": "Erscheint in der Widget-Adresse. Nur Kleinbuchstaben, Zahlen und Bindestriche.",
            "widget_allowed_origins": "Websites, auf denen dein Widget eingebettet werden darf, "
            "z. B. https://www.mein-bergfuehrer.ch (eine pro Zeile). Leer = überall erlaubt.",
        }
        widgets = {"widget_allowed_origins": forms.Textarea(attrs={"rows": 3})}

    def clean_widget_allowed_origins(self):
        origins = []
        for raw in self.cleaned_data["widget_allowed_origins"].split():
            parts = urlsplit(raw)
            if parts.scheme not in ("https", "http") or not parts.netloc or parts.path not in ("", "/"):
                raise forms.ValidationError(f"Ungültige Adresse: {raw} (Beispiel: https://www.example.ch)")
            origins.append(f"{parts.scheme}://{parts.netloc}".lower())
        return "\n".join(origins)


class AvailabilityForm(forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        label="Buchbare Wochentage", choices=WEEKDAY_CHOICES, widget=forms.CheckboxSelectMultiple, required=False
    )

    class Meta:
        model = User
        fields = ("min_notice_days", "max_advance_days")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weekdays"].initial = list(self.instance.available_weekdays)

    def save(self, commit=True):
        self.instance.available_weekdays = "".join(sorted(self.cleaned_data["weekdays"]))
        return super().save(commit)


class CalendarSyncForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("ical_url",)
        help_texts = {
            "ical_url": "Google Kalender: Einstellungen → dein Kalender → «Privatadresse im iCal-Format». "
            "Belegte Tage werden automatisch für Buchungen gesperrt."
        }

    def clean_ical_url(self):
        url = self.cleaned_data["ical_url"].strip()
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if url and not url.startswith("https://"):
            raise forms.ValidationError("Die Adresse muss mit https:// beginnen.")
        return url
