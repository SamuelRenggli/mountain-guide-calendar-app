import hmac
import re
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
        return cleaned


class RateTierForm(forms.ModelForm):
    class Meta:
        model = DayRateTier
        fields = ("min_participants", "day_rate")


RateTierFormSet = forms.inlineformset_factory(User, DayRateTier, form=RateTierForm, extra=2, max_num=6,
                                              can_delete=True)


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
