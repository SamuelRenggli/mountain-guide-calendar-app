import re

from django import forms
from django.core import signing

from accounts.models import LANGUAGE_CHOICES

from .models import BlockedPeriod, Tour

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
FORM_SIGNER_SALT = "widget-form"
MIN_FILL_SECONDS = 3
MAX_FORM_AGE_SECONDS = 4 * 60 * 60


def form_timestamp_token() -> str:
    return signing.TimestampSigner(salt=FORM_SIGNER_SALT).sign("w")


class BookingRequestForm(forms.Form):
    start_date = forms.DateField(widget=forms.HiddenInput)
    participants = forms.IntegerField(label="Teilnehmende", min_value=1, initial=1)
    guest_name = forms.CharField(label="Name", max_length=100)
    guest_email = forms.EmailField(label="E-Mail")
    guest_phone = forms.CharField(label="Telefon", max_length=30, required=False)
    guest_message = forms.CharField(
        label="Nachricht an den Bergführer", max_length=2000, required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Erfahrung, Wünsche, Fragen …"}),
    )
    language = forms.ChoiceField(label="Sprache der E-Mails / Email language", choices=LANGUAGE_CHOICES, initial="de")
    # Spam protection: bots fill the hidden field or submit instantly.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))
    ts = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, tour=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tour:
            self.fields["participants"] = forms.IntegerField(
                label="Teilnehmende", min_value=1, max_value=tour.max_participants, initial=1
            )
        if not self.is_bound:
            self.initial["ts"] = form_timestamp_token()

    def _single_line(self, name):
        value = self.cleaned_data[name].strip()
        if _CONTROL_CHARS.search(value):
            raise forms.ValidationError("Ungültige Zeichen.")
        return value

    def clean_guest_name(self):
        return self._single_line("guest_name")

    def clean_guest_phone(self):
        return self._single_line("guest_phone")

    def is_spam(self) -> bool:
        if self.data.get("website"):
            return True
        signer = signing.TimestampSigner(salt=FORM_SIGNER_SALT)
        token = self.data.get("ts", "")
        try:
            signer.unsign(token, max_age=MAX_FORM_AGE_SECONDS)
        except signing.BadSignature:  # forged or too old
            return True
        try:
            signer.unsign(token, max_age=MIN_FILL_SECONDS)
        except signing.SignatureExpired:
            return False  # took longer than a bot would
        return True


class TourForm(forms.ModelForm):
    class Meta:
        model = Tour
        fields = ("title", "description", "duration_days", "price", "price_mode", "max_participants", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class BlockedPeriodForm(forms.ModelForm):
    class Meta:
        model = BlockedPeriod
        fields = ("start_date", "end_date", "note")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start_date"), cleaned.get("end_date")
        if s and e and e < s:
            self.add_error("end_date", "Das Ende muss nach dem Beginn liegen.")
        return cleaned


class MessageForm(forms.Form):
    message = forms.CharField(
        label="Nachricht an den Gast (optional)", max_length=2000, required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class OfferForm(forms.Form):
    start_date = forms.DateField(label="Neues Startdatum", widget=forms.DateInput(attrs={"type": "date"}))
    price = forms.DecimalField(label="Preis total (CHF)", min_value=0, max_digits=9, decimal_places=2)
    message = forms.CharField(
        label="Nachricht an den Gast", max_length=2000, widget=forms.Textarea(attrs={"rows": 3})
    )
