import re
from datetime import datetime, time, timedelta

from django import forms
from django.core import signing
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import LANGUAGE_CHOICES

from .models import BlockedPeriod, Departure, InvoiceLine, OpenPeriod, Tour

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
FORM_SIGNER_SALT = "widget-form"
MIN_FILL_SECONDS = 3
MAX_FORM_AGE_SECONDS = 4 * 60 * 60
REQUEST_MESSAGE_MAX = 500  # R-021


def _date_input():
    # <input type="date"> only understands ISO dates; a localized default (31.12.2026) would leave it empty.
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _datetime_input():
    return forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


def form_timestamp_token() -> str:
    return signing.TimestampSigner(salt=FORM_SIGNER_SALT).sign("w")


def single_line(value: str) -> str:
    value = value.strip()
    if _CONTROL_CHARS.search(value):
        raise forms.ValidationError(_("Ungültige Zeichen."))
    return value


class PublicForm(forms.Form):
    """A form guests fill in without an account, often in a third-party iframe: honeypot + signed timestamp."""

    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))
    ts = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial["ts"] = form_timestamp_token()

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

    def clean_guest_name(self):
        return single_line(self.cleaned_data["guest_name"])

    def clean_guest_phone(self):
        return single_line(self.cleaned_data["guest_phone"])


def _terms_field():
    return forms.BooleanField(
        label=_("Ich akzeptiere die Bedingungen des Bergführers und habe die Datenschutzerklärung gelesen."),
        error_messages={"required": _("Bitte akzeptiere die Bedingungen.")},
    )


class BookingRequestForm(PublicForm):
    """A custom tour on fixed dates (R-020, R-021)."""

    start_date = forms.DateField(widget=forms.HiddenInput)
    end_date = forms.DateField(widget=forms.HiddenInput)
    guest_message = forms.CharField(
        label=_("Tourenwunsch"), max_length=REQUEST_MESSAGE_MAX,
        help_text=_("Was möchtet ihr unternehmen? Ziel, Erfahrung, Fragen."),
        widget=forms.Textarea(attrs={"rows": 5, "maxlength": REQUEST_MESSAGE_MAX}),
        error_messages={"required": _("Beschreibe kurz, was du unternehmen willst.")},
    )
    participants = forms.IntegerField(label=_("Teilnehmende"), min_value=1, max_value=50, initial=1,
                                      widget=forms.NumberInput(attrs={"data-group-size": ""}))
    guest_name = forms.CharField(label=_("Name"), max_length=100)
    guest_email = forms.EmailField(label=_("E-Mail"))
    guest_phone = forms.CharField(label=_("Telefon"), max_length=30, required=False)
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, widget=forms.HiddenInput)
    accept_terms = _terms_field()

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start_date"), cleaned.get("end_date")
        if s and e and e < s:
            raise forms.ValidationError(_("Das Ende muss nach dem Beginn liegen."))
        return cleaned


class RegistrationForm(PublicForm):
    """Sign up for a scheduled date and pay right away (R-120)."""

    seats = forms.IntegerField(label=_("Anzahl Plätze"), min_value=1, max_value=50, initial=1)
    guest_name = forms.CharField(label=_("Name"), max_length=100)
    guest_email = forms.EmailField(label=_("E-Mail"))
    guest_phone = forms.CharField(label=_("Telefon"), max_length=30)
    emergency_contact = forms.CharField(
        label=_("Notfallkontakt"), max_length=200, help_text=_("Name und Telefonnummer einer Person, die nicht mitkommt."),
    )
    experience = forms.CharField(
        label=_("Erfahrung / Niveau"), max_length=500,
        help_text=_("Kurz: ähnliche Touren, Kondition, Klettergrad …"), widget=forms.Textarea(attrs={"rows": 3}),
    )
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, widget=forms.HiddenInput)
    accept_terms = _terms_field()

    def __init__(self, *args, max_seats=None, **kwargs):
        super().__init__(*args, **kwargs)
        if max_seats:
            self.fields["seats"].max_value = max_seats
            self.fields["seats"].widget.attrs["max"] = max_seats
            self.fields["seats"].validators.append(_max_validator(max_seats))

    def clean_emergency_contact(self):
        return single_line(self.cleaned_data["emergency_contact"])


def _max_validator(limit):
    from django.core.validators import MaxValueValidator

    return MaxValueValidator(limit, message=_("Leider sind nur noch %(limit_value)s Plätze frei."))


class WaitlistForm(PublicForm):
    guest_name = forms.CharField(label=_("Name"), max_length=100)
    guest_email = forms.EmailField(label=_("E-Mail"))
    seats = forms.IntegerField(label=_("Anzahl Plätze"), min_value=1, max_value=50, initial=1)
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, widget=forms.HiddenInput)


TEXTAREA = forms.Textarea(attrs={"rows": 3})


class TourForm(forms.ModelForm):
    class Meta:
        model = Tour
        fields = ("title", "duration_days", "price_per_person", "min_participants", "max_participants", "description",
                  "requirements", "equipment", "included", "location", "meeting_point", "meeting_time", "is_active")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}), "requirements": TEXTAREA, "equipment": TEXTAREA,
            "included": TEXTAREA, "meeting_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }
        help_texts = {"is_active": _("Inaktive Vorlagen erscheinen nicht mehr beim Ausschreiben.")}

    def clean(self):
        cleaned = super().clean()
        lo, hi = cleaned.get("min_participants"), cleaned.get("max_participants")
        if lo and hi and hi < lo:
            self.add_error("max_participants", _("Die Höchstzahl muss mindestens so gross sein wie die Mindestzahl."))
        return cleaned


class DepartureForm(forms.ModelForm):
    """Publish a date from a template or as a quick entry (R-100–R-104). Works on a phone."""

    save_as_template = forms.BooleanField(label=_("Als Vorlage speichern"), required=False,
                                          help_text=_("Damit du diese Tour später mit einem Klick wieder ausschreiben kannst."))

    class Meta:
        model = Departure
        fields = ("title", "start_date", "end_date", "price_per_person", "min_participants", "max_participants",
                  "signup_deadline", "decision_deadline", "meeting_point", "meeting_time", "description",
                  "requirements", "equipment", "included", "location")
        widgets = {
            "start_date": _date_input(), "end_date": _date_input(),
            "signup_deadline": _datetime_input(), "decision_deadline": _datetime_input(),
            "meeting_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "description": forms.Textarea(attrs={"rows": 4}), "requirements": TEXTAREA, "equipment": TEXTAREA,
            "included": TEXTAREA,
        }

    REQUIRED = ("meeting_point", "meeting_time")  # R-101

    def __init__(self, *args, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.REQUIRED:
            self.fields[name].required = True
        if editing:
            del self.fields["save_as_template"]

    @staticmethod
    def initial_from(tour: Tour | None, start=None) -> dict:
        """Prefill: the template's details, deadlines two days before the start at 18:00."""
        initial = {}
        if tour:
            initial = {f: getattr(tour, f) for f in Tour.DETAIL_FIELDS}
        if start:
            initial["start_date"] = start
            initial["end_date"] = tour.end_date_for(start) if tour else start
            two_days_before = timezone.make_aware(datetime.combine(start - timedelta(days=2), time(18, 0)))
            initial["signup_deadline"] = initial["decision_deadline"] = two_days_before
        return initial


class MoveDepartureForm(forms.Form):
    start_date = forms.DateField(label=_("Neues Startdatum"), widget=_date_input())
    end_date = forms.DateField(label=_("Neues Enddatum"), widget=_date_input())
    message = forms.CharField(label=_("Nachricht an die Gäste"), max_length=2000, required=False, widget=TEXTAREA)


class MessageForm(forms.Form):
    message = forms.CharField(
        label=_("Nachricht an den Gast (optional)"), max_length=2000, required=False, widget=TEXTAREA,
    )


class RefundExtraForm(forms.Form):
    amount = forms.DecimalField(label=_("Zusätzlich zurückzahlen (CHF)"), min_value=0.05, max_digits=9, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "0.05"}))


def _money(label, **kwargs):
    return forms.DecimalField(label=label, min_value=0, max_digits=9, decimal_places=2,
                              widget=forms.NumberInput(attrs={"step": "0.05"}), **kwargs)


def _price(label=None):
    field = _money(label or _("Preis ohne Spesen (CHF)"),
                   help_text=_("Vorschlag aus dem Preisrechner; du kannst ihn anpassen."))
    field.widget.attrs["data-price"] = ""  # filled by the price calculator (static/js/pricing.js)
    return field


class PricingForm(forms.Form):
    """Price plus how it is made up (R-022) and an estimate of expenses (R-023)."""

    guide_rate = _money(_("Tagesansatz Bergführer (CHF)"), help_text=_("Aus deinen Stufen nach Gruppengrösse."))
    extra_guides = forms.IntegerField(label=_("Zusätzliche Bergführer"), min_value=0, max_value=9, initial=0,
                                      help_text=_("Zum gleichen Tagesansatz."))
    aspirants = forms.IntegerField(label=_("Aspiranten"), min_value=0, max_value=9, initial=0)
    aspirant_rate = _money(_("Tagesansatz Aspirant (CHF)"))
    price = _price()
    estimated_expenses = _money(_("Geschätzte Spesen (CHF, unverbindlich)"), required=False,
                                help_text=_("v. a. Reise, Hütte, Bahn. Der Gast sieht sie als Schätzung."))
    show_breakdown = forms.BooleanField(label=_("Aufteilung nach Person und Tagesansatz dem Gast zeigen"), required=False,
                                        initial=True)
    message = forms.CharField(label=_("Nachricht an den Gast (optional)"), max_length=2000, required=False,
                              widget=TEXTAREA)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("guide_rate", "extra_guides", "aspirants", "aspirant_rate"):  # read by static/js/pricing.js
            self.fields[name].widget.attrs[f"data-calc-{name.replace('_', '-')}"] = ""

    @classmethod
    def initial_for(cls, booking) -> dict:
        return {"price": booking.price or None, "estimated_expenses": booking.estimated_expenses,
                "guide_rate": booking.guide.rate_for(booking.participants),
                "aspirant_rate": booking.guide.aspirant_day_rate, "show_breakdown": True}

    def breakdown(self, booking, days) -> list:
        from .services import breakdown

        d = self.cleaned_data
        if not d.get("show_breakdown"):
            return []
        return breakdown(booking.guide, days=days, participants=booking.participants,
                         extra_guides=d.get("extra_guides") or 0, aspirants=d.get("aspirants") or 0,
                         guide_rate=d["guide_rate"], aspirant_rate=d["aspirant_rate"])


class ConfirmForm(PricingForm):
    pass


class OfferForm(PricingForm):
    start_date = forms.DateField(label=_("Startdatum"), widget=_date_input())
    end_date = forms.DateField(label=_("Enddatum"), widget=_date_input())
    field_order = ["start_date", "end_date", "guide_rate", "extra_guides", "aspirants", "aspirant_rate", "price",
                   "estimated_expenses", "show_breakdown", "message"]

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start_date"), cleaned.get("end_date")
        if s and e and e < s:
            self.add_error("end_date", _("Das Ende muss nach dem Beginn liegen."))
        if not cleaned.get("message", "").strip():
            self.add_error("message", _("Bitte erkläre dem Gast kurz deinen Vorschlag."))
        return cleaned


class ChangeBookingForm(forms.Form):
    """R-024: the guide adjusts a confirmed tour."""

    start_date = forms.DateField(label=_("Startdatum"), widget=_date_input())
    end_date = forms.DateField(label=_("Enddatum"), widget=_date_input())
    participants = forms.IntegerField(label=_("Teilnehmende"), min_value=1, max_value=50)
    price = _money(_("Preis ohne Spesen (CHF)"))
    message = forms.CharField(label=_("Nachricht an den Gast (optional)"), max_length=2000, required=False,
                              widget=TEXTAREA)


class ProposeDateForm(forms.Form):
    """R-025: weather — call off and propose a replacement date."""

    start_date = forms.DateField(label=_("Ersatzdatum: Beginn"), widget=_date_input())
    end_date = forms.DateField(label=_("Ersatzdatum: Ende"), widget=_date_input())
    incurred_costs = _money(_("Bereits angefallene Kosten (CHF)"), required=False,
                            help_text=_("z. B. verfallene Hüttenanzahlung. Trägt der Gast, falls er das Ersatzdatum ablehnt."))
    incurred_costs_note = forms.CharField(label=_("Wofür?"), max_length=200, required=False)
    message = forms.CharField(label=_("Nachricht an den Gast"), max_length=2000, required=False, widget=TEXTAREA)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("incurred_costs") and not cleaned.get("incurred_costs_note", "").strip():
            self.add_error("incurred_costs_note", _("Wofür sind diese Kosten?"))
        return cleaned


class InvoiceForm(forms.Form):
    """The guide's bill after the tour: the tour price (from the offer, editable) plus expenses (formset)."""

    price = _price(_("Tourenpreis (CHF)"))
    message = forms.CharField(label=_("Nachricht an den Gast (optional)"), max_length=2000, required=False,
                              widget=forms.Textarea(attrs={"rows": 2}))

    def tour_line(self) -> InvoiceLine:
        return InvoiceLine(kind=InvoiceLine.Kind.TOUR, description=str(_("Tourenpreis (Führung)")), quantity=1,
                           unit_price=self.cleaned_data["price"])


class ExpenseForm(forms.Form):
    description = forms.CharField(label=_("Spesen"), max_length=200, required=False,
                                  widget=forms.TextInput(attrs={"placeholder": _("z. B. Hüttenübernachtung, Bahn, Fahrt")}))
    amount = _money(_("Betrag (CHF)"), required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("amount") and not cleaned.get("description", "").strip():
            self.add_error("description", _("Wofür sind diese Spesen?"))
        if cleaned.get("description", "").strip() and cleaned.get("amount") is None:
            self.add_error("amount", _("Betrag fehlt."))
        return cleaned

    def line(self) -> InvoiceLine | None:
        d = self.cleaned_data
        if not d.get("description", "").strip():
            return None
        return InvoiceLine(kind=InvoiceLine.Kind.EXPENSE, description=d["description"].strip(), quantity=1,
                           unit_price=d["amount"])


ExpenseFormSet = forms.formset_factory(ExpenseForm, extra=4, max_num=20)


class BlockedPeriodForm(forms.ModelForm):
    class Meta:
        model = BlockedPeriod
        fields = ("start_date", "end_date", "note")
        widgets = {"start_date": _date_input(), "end_date": _date_input()}

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start_date"), cleaned.get("end_date")
        if s and e and e < s:
            self.add_error("end_date", _("Das Ende muss nach dem Beginn liegen."))
        return cleaned


class OpenPeriodForm(BlockedPeriodForm):
    class Meta(BlockedPeriodForm.Meta):
        model = OpenPeriod


class ExportForm(forms.Form):
    """R-203: income and expenses of a period."""

    start = forms.DateField(label=_("Von"), widget=_date_input())
    end = forms.DateField(label=_("Bis"), widget=_date_input())
    format = forms.ChoiceField(label=_("Format"), choices=[("xlsx", "Excel (.xlsx)"), ("csv", "CSV")], initial="xlsx")

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get("start"), cleaned.get("end")
        if s and e and e < s:
            self.add_error("end", _("Das Ende muss nach dem Beginn liegen."))
        return cleaned


class EraseGuestDataForm(forms.Form):
    """R-152: delete phone, emergency contact and experience of one guest."""

    email = forms.EmailField(label=_("E-Mail des Gastes"))
