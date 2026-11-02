import hmac
import re
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm

from .models import User

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
        fields = ("display_name", "email", "phone", "language", "slug", "widget_allowed_origins",
                  "day_rate", "aspirant_day_rate", "iban")
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

    def clean_iban(self):
        iban = re.sub(r"\s", "", self.cleaned_data["iban"]).upper()
        if iban and not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
            raise forms.ValidationError("Ungültige IBAN (Beispiel: CH93 0076 2011 6238 5295 7).")
        return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


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
