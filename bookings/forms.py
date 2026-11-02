import re

from django import forms
from django.core import signing

from accounts.models import LANGUAGE_CHOICES

from .models import BlockedPeriod, InvoiceLine, OpenPeriod, Tour

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
FORM_SIGNER_SALT = "widget-form"
MIN_FILL_SECONDS = 3
MAX_FORM_AGE_SECONDS = 4 * 60 * 60


def _date_input():
    # <input type="date"> only understands ISO dates; the de-ch default (31.12.2026) would leave it empty.
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def form_timestamp_token() -> str:
    return signing.TimestampSigner(salt=FORM_SIGNER_SALT).sign("w")


class BookingRequestForm(forms.Form):
    start_date = forms.DateField(widget=forms.HiddenInput)
    end_date = forms.DateField(widget=forms.HiddenInput)
    guest_message = forms.CharField(
        label="Idee / Bemerkung", max_length=250,
        help_text="Beschreibe in 2–3 Sätzen, was du unternehmen willst.",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Tourenidee, Erfahrung, wie flexibel ihr seid, Fragen …"}),
        error_messages={"required": "Beschreibe kurz, was du unternehmen willst."},
    )
    participants = forms.IntegerField(label="Teilnehmende", min_value=1, initial=1)
    guest_name = forms.CharField(label="Name", max_length=100)
    guest_email = forms.EmailField(label="E-Mail")
    guest_phone = forms.CharField(label="Telefon", max_length=30, required=False)
    language = forms.ChoiceField(label="Sprache der E-Mails / Email language", choices=LANGUAGE_CHOICES, initial="de")
    # Spam protection: bots fill the hidden field or submit instantly.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))
    ts = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial["ts"] = form_timestamp_token()

    def _single_line(self, name):
        value = self.cleaned_data[name].strip()
        if _CONTROL_CHARS.search(value):
            raise forms.ValidationError("Ungültige Zeichen.")
        return value

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start_date"), cleaned.get("end_date")
        if s and e and e < s:
            raise forms.ValidationError("Das Ende muss nach dem Beginn liegen.")
        return cleaned

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
            "start_date": _date_input(),
            "end_date": _date_input(),
        }

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start_date"), cleaned.get("end_date")
        if s and e and e < s:
            self.add_error("end_date", "Das Ende muss nach dem Beginn liegen.")
        return cleaned


class OpenPeriodForm(BlockedPeriodForm):
    class Meta(BlockedPeriodForm.Meta):
        model = OpenPeriod


class MessageForm(forms.Form):
    message = forms.CharField(
        label="Nachricht an den Gast (optional)", max_length=2000, required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


def _money(label, **kwargs):
    return forms.DecimalField(label=label, min_value=0, max_digits=9, decimal_places=2,
                              widget=forms.NumberInput(attrs={"step": "0.05"}), **kwargs)


def _price(label="Preis ohne Spesen (CHF)"):
    field = _money(label, help_text="Den Vorschlag des Preisrechners kannst du übernehmen und anpassen.")
    field.widget.attrs["data-price"] = ""  # filled by the price calculator (static/js/pricing.js)
    return field


class ConfirmForm(MessageForm):
    price = _price()
    field_order = ["price", "message"]


class OfferForm(forms.Form):
    start_date = forms.DateField(label="Startdatum", widget=_date_input())
    end_date = forms.DateField(label="Enddatum", widget=_date_input())
    price = _price()
    message = forms.CharField(
        label="Nachricht an den Gast", max_length=2000, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, own_idea=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not own_idea:
            del self.fields["end_date"]  # follows from the tour's duration


class InvoiceForm(forms.Form):
    """The guide's bill after the tour: the tour price (from the offer, editable) plus expenses (formset)."""

    price = _price("Tourenpreis (CHF)")
    message = forms.CharField(label="Nachricht an den Gast (optional)", max_length=2000, required=False,
                              widget=forms.Textarea(attrs={"rows": 2}))

    def tour_line(self) -> InvoiceLine:
        return InvoiceLine(kind=InvoiceLine.Kind.TOUR, description="Tourenpreis (Führung)", quantity=1,
                           unit_price=self.cleaned_data["price"])


class ExpenseForm(forms.Form):
    description = forms.CharField(label="Spesen", max_length=200, required=False,
                                  widget=forms.TextInput(attrs={"placeholder": "z. B. Hüttenübernachtung, Bahn, Fahrt"}))
    amount = _money("Betrag (CHF)", required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("amount") and not cleaned.get("description", "").strip():
            self.add_error("description", "Wofür sind diese Spesen?")
        if cleaned.get("description", "").strip() and cleaned.get("amount") is None:
            self.add_error("amount", "Betrag fehlt.")
        return cleaned

    def line(self) -> InvoiceLine | None:
        d = self.cleaned_data
        if not d.get("description", "").strip():
            return None
        return InvoiceLine(kind=InvoiceLine.Kind.EXPENSE, description=d["description"].strip(), quantity=1,
                           unit_price=d["amount"])


ExpenseFormSet = forms.formset_factory(ExpenseForm, extra=4, max_num=20)
