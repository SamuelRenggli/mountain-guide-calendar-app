import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django import forms
from django.core import signing
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import LANGUAGE_CHOICES

from . import pictures
from .models import (
    BlockedPeriod, Departure, ExternalTour, GuestProfile, InvoiceLine, OpenPeriod, Partner, PartnerPeriod, Picture, Tour,
    TourCategory, money,
)

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


# Country codes offered in front of the phone number; Switzerland first. Numbers typed with + or 00 keep their own code.
COUNTRY_CODES = [
    ("41", "CH +41"), ("49", "DE +49"), ("43", "AT +43"), ("33", "FR +33"), ("39", "IT +39"), ("423", "LI +423"),
    ("44", "UK +44"), ("31", "NL +31"), ("32", "BE +32"), ("352", "LU +352"), ("34", "ES +34"), ("45", "DK +45"),
    ("46", "SE +46"), ("47", "NO +47"), ("420", "CZ +420"), ("48", "PL +48"), ("1", "US/CA +1"),
]


class PhoneWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        super().__init__([
            forms.Select(choices=COUNTRY_CODES, attrs={"class": "phone-code", "autocomplete": "tel-country-code"}),
            forms.TextInput(attrs={"class": "phone-number", "type": "tel", "autocomplete": "tel-national",
                                   "inputmode": "tel", "placeholder": "79 123 45 67"}),
        ], attrs)

    def value_from_datadict(self, data, files, name):
        if f"{name}_1" not in data and name in data:  # a single phone field (older embedded form)
            return ["", data.get(name, "")]
        return super().value_from_datadict(data, files, name)

    def value_omitted_from_data(self, data, files, name):
        return name not in data and super().value_omitted_from_data(data, files, name)

    def decompress(self, value):
        if value and value.startswith("+"):
            code, _sep, number = value[1:].partition(" ")
            if code in dict(COUNTRY_CODES) and number:
                return [code, number]
        return ["41", value or ""]


class PhoneField(forms.MultiValueField):
    """Country code plus number, stored as "+41 791234567" (WhatsApp and SMS links need the full number)."""

    widget = PhoneWidget

    def __init__(self, **kwargs):
        super().__init__(fields=[forms.ChoiceField(choices=COUNTRY_CODES, required=False),
                                 forms.CharField(max_length=30, required=False)],
                         require_all_fields=False, **kwargs)

    def compress(self, data_list):
        code, number = (list(data_list or []) + ["", ""])[:2]
        number = (number or "").strip()
        if not number:
            if self.required:
                raise forms.ValidationError(_("Bitte gib eine Telefonnummer an."))
            return ""
        if _CONTROL_CHARS.search(number) or re.search(r"[^\d\s+()./-]", number):
            raise forms.ValidationError(_("Bitte nur Ziffern eingeben."))
        digits = re.sub(r"\D", "", number)
        if number.startswith("+") or number.startswith("00"):
            full = digits[2:] if number.startswith("00") else digits
            if not 7 <= len(full) <= 15:
                raise forms.ValidationError(_("Diese Telefonnummer ist zu kurz oder zu lang."))
            known = next((c for c, _label in sorted(COUNTRY_CODES, key=lambda x: -len(x[0])) if full.startswith(c)), "")
            return f"+{known} {full[len(known):]}" if known else f"+{full}"
        code = code or "41"
        national = digits if code == "39" else digits.lstrip("0")  # Italy keeps the leading 0
        if not 7 <= len(code + national) <= 15:
            raise forms.ValidationError(_("Diese Telefonnummer ist zu kurz oder zu lang."))
        return f"+{code} {national}"


class MeetingTimeField(forms.TimeField):
    """A plain text field: the browser's time picker shows AM/PM or refuses input depending on browser and language.
    Accepts 10:30, 10.30, 1030, 10h30 and 7."""

    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Zeit"))
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "numeric", "placeholder": "07:30",
                                                           "autocomplete": "off", "class": "time-input"}))
        super().__init__(input_formats=["%H:%M", "%H.%M", "%H%M", "%Hh%M", "%H:%M:%S", "%H"], **kwargs)
        self.widget.format = "%H:%M"

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().lower().replace(" ", "").removesuffix("uhr").removesuffix("h")
        return super().to_python(value)


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
    guest_phone = PhoneField(label=_("Telefon"), required=False,
                             help_text=_("Mit Vorwahl, damit dich dein Bergführer auch per WhatsApp erreicht."))
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
    guest_phone = PhoneField(label=_("Telefon"),
                             help_text=_("Mit Vorwahl, damit dich dein Bergführer auch per WhatsApp erreicht."))
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


class PhotoMixin:
    """An optional tour photo: upload a new one, keep the current one or remove it.
    Call picture(guide, current) after is_valid() to get the Picture to store."""

    def add_photo_fields(self, current=None, guide=None):
        self.current_picture = current
        self.library_groups = []
        if guide is not None:
            library = list(Picture.library_for(guide))
            self.library_groups = library_groups(library)
            if library:
                self.fields["library_picture"] = forms.ChoiceField(
                    label=_("Bild aus deiner Bibliothek"), required=False,
                    choices=[("", "")] + [(p.key, p.key) for p in library],
                )
        self.fields["photo"] = forms.FileField(
            label=_("Bild"), required=False,
            widget=forms.ClearableFileInput(attrs={"accept": pictures.ACCEPT,
                                                   "data-too-big": _("Das Bild ist zu gross (höchstens 8 MB).")}),
            help_text=_("Neues Bild hochladen. Erscheint im Buchungskalender und kommt in deine Bibliothek. "
                        "JPG, PNG, WebP oder HEIC, höchstens 8 MB."),
        )

    def clean_photo(self):
        upload = self.cleaned_data.get("photo")
        if upload:
            if upload.size > pictures.MAX_UPLOAD_BYTES:
                raise forms.ValidationError(_("Das Bild ist zu gross (höchstens 8 MB)."))
            try:
                upload.seek(0)  # the same upload is read again when the form is checked a second time
                self._shrunk = pictures.shrink(upload.read())
            except pictures.PictureError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return upload

    def library_choice(self, guide):
        key = self.cleaned_data.get("library_picture")
        return Picture.library_for(guide).filter(key=key).first() if key else None

    def picture(self, guide, current=None):
        """Upload first, then a picture picked from the library, then removing, else the current one."""
        current = self.current_picture if current is None else current
        if getattr(self, "_shrunk", None):
            return pictures.store(guide, self._shrunk, in_library=True, category=self.cleaned_data.get("category") or "")
        if picked := self.library_choice(guide):
            return picked
        if self.cleaned_data.get("remove_photo"):
            return None
        return current

    def detail_data(self) -> dict:
        return {k: v for k, v in self.cleaned_data.items() if k not in ("photo", "remove_photo", "library_picture")}


def library_groups(library) -> list:
    """[(label, [pictures])] by kind of tour; own pictures before Tourchef's."""
    labels = dict(TourCategory.choices)
    groups = {}
    for pic in sorted(library, key=lambda p: p.is_stock):
        groups.setdefault(pic.category, []).append(pic)
    order = [c for c, _label in TourCategory.choices] + [""]
    return [(labels.get(c) or _("Ohne Tourenart"), groups[c]) for c in order if c in groups]


class PictureCategoryForm(forms.Form):
    """Kind of tour and description of one picture in the library."""

    category = forms.ChoiceField(choices=[("", "–")] + list(TourCategory.choices), required=False)
    title = forms.CharField(max_length=120, required=False)

    def clean_title(self):
        return single_line(self.cleaned_data["title"])


class GuideMessageForm(forms.Form):
    body = forms.CharField(
        label=_("Nachricht"), max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 5000}),
        error_messages={"required": _("Bitte schreibe eine Nachricht.")},
    )


class GuestMessageForm(forms.Form):
    body = forms.CharField(
        label=_("Deine Nachricht"), max_length=3000,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 3000}),
        error_messages={"required": _("Bitte schreibe eine Nachricht.")},
    )


CAR_CHOICES = [("", _("Kein Auto")), ("0", _("Auto, aber keine freien Plätze"))] + [
    (str(n), _("Auto mit %(n)s freien Plätzen") % {"n": n} if n > 1 else _("Auto mit 1 freien Platz")) for n in range(1, 7)
]


class GuestDetailsForm(forms.ModelForm):
    """What the guide needs for planning, entered by the guest on the booking page (no login). Saved per guide and
    email address, so it's filled in already for the next tour with the same guide."""

    car = forms.ChoiceField(label=_("Auto"), choices=CAR_CHOICES, required=False,
                            help_text=_("Falls wir gemeinsam anreisen: Könntest du fahren?"))

    class Meta:
        model = GuestProfile
        fields = ("billing_address", "emergency_contact", "diet", "allergies", "club", "club_number", "travel_card")
        widgets = {"billing_address": forms.Textarea(attrs={"rows": 3}), "allergies": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "emergency_contact": _("Name und Telefon einer Person, die wir im Notfall erreichen."),
            "allergies": _("Nur was für die Tour wichtig ist, z. B. Nussallergie, Asthma, Medikamente."),
            "club": _("Mitglieder von Alpenvereinen mit Gegenrecht zahlen in SAC-Hütten den Mitgliederpreis."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("billing_address", "emergency_contact"):
            self.fields[name].required = True
        self.fields["diet"].choices = [("", "–")] + list(GuestProfile.Diet.choices)
        self.fields["club"].choices = [("", "–")] + list(GuestProfile.Club.choices)
        self.fields["travel_card"].choices = [("", "–")] + list(GuestProfile.TravelCard.choices)
        seats = self.instance.car_seats
        self.fields["car"].initial = "" if seats is None else str(seats)

    def clean_emergency_contact(self):
        return single_line(self.cleaned_data["emergency_contact"])

    def save(self, commit=True):
        car = self.cleaned_data.get("car")
        self.instance.car_seats = int(car) if car not in (None, "") else None
        return super().save(commit)


class TourForm(PhotoMixin, forms.ModelForm):
    meeting_time = MeetingTimeField(required=False)

    class Meta:
        model = Tour
        fields = ("title", "category", "duration_days", "price_per_person", "min_participants", "max_participants", "description",
                  "requirements", "equipment", "included", "not_included", "location", "meeting_point", "meeting_time")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}), "requirements": TEXTAREA, "equipment": TEXTAREA,
            "included": TEXTAREA, "not_included": TEXTAREA,
        }

    def __init__(self, *args, guide=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_photo_fields(self.instance.picture if self.instance.pk else None, guide=guide)

    def clean(self):
        cleaned = super().clean()
        lo, hi = cleaned.get("min_participants"), cleaned.get("max_participants")
        if lo and hi and hi < lo:
            self.add_error("max_participants", _("Die Höchstzahl muss mindestens so gross sein wie die Mindestzahl."))
        return cleaned


class DepartureForm(PhotoMixin, forms.ModelForm):
    """Publish a date from a template or as a quick entry (R-100–R-104). Works on a phone."""

    meeting_time = MeetingTimeField()
    save_as_template = forms.BooleanField(label=_("Als Vorlage speichern"), required=False,
                                          help_text=_("Damit du diese Tour später mit einem Klick wieder ausschreiben kannst."))

    class Meta:
        model = Departure
        fields = ("title", "category", "start_date", "end_date", "price_per_person", "min_participants", "max_participants",
                  "signup_deadline", "decision_deadline", "meeting_point", "meeting_time", "description",
                  "requirements", "equipment", "included", "not_included", "location")
        widgets = {
            "start_date": _date_input(), "end_date": _date_input(),
            "signup_deadline": _datetime_input(), "decision_deadline": _datetime_input(),
            "description": forms.Textarea(attrs={"rows": 4}), "requirements": TEXTAREA, "equipment": TEXTAREA,
            "included": TEXTAREA, "not_included": TEXTAREA,
        }

    REQUIRED = ("meeting_point", "meeting_time")  # R-101

    def __init__(self, *args, editing=False, from_template=False, picture=None, guide=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.REQUIRED:
            self.fields[name].required = True
        if editing or from_template:
            del self.fields["save_as_template"]
        self.add_photo_fields(self.instance.picture if self.instance.pk else picture, guide=guide)

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
    message = forms.CharField(label=_("Nachricht an die Gäste (optional)"), max_length=2000, required=False,
                              widget=TEXTAREA)


class MessageForm(forms.Form):
    message = forms.CharField(
        label=_("Nachricht an den Gast (optional)"), max_length=2000, required=False, widget=TEXTAREA,
    )


def _sure_field(label):
    return forms.BooleanField(label=label, error_messages={"required": _("Bitte bestätige die Absage.")})


class GuestsMessageForm(forms.Form):
    """Cancel a scheduled date: one message to everyone signed up. Needs a tick, then a confirm dialog."""

    message = forms.CharField(
        label=_("Nachricht an die Gäste (optional)"), max_length=2000, required=False, widget=TEXTAREA,
    )
    sure = _sure_field(_("Ja, ich sage den Termin für alle Angemeldeten ab."))


class GuideCancelForm(MessageForm):
    """Cancel one booked tour. Needs a tick, then a confirm dialog."""

    sure = _sure_field(_("Ja, ich sage die Tour ab."))


class RefundExtraForm(forms.Form):
    amount = forms.DecimalField(label=_("Zusätzlich zurückzahlen (CHF)"), min_value=0.05, max_digits=9, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "0.05"}))


def _money(label, **kwargs):
    return forms.DecimalField(label=label, min_value=0, max_digits=9, decimal_places=2,
                              widget=forms.NumberInput(attrs={"step": "any"}), **kwargs)


def _price(label=None):
    field = _money(label or _("Preis ohne Spesen (CHF)"),
                   help_text=_("Vorschlag aus dem Preisrechner; du kannst ihn anpassen."))
    field.widget.attrs["data-price"] = ""  # filled by the price calculator (static/js/pricing.js)
    return field


class AcceptForm(forms.Form):
    """The guide takes a request: days (as asked, more, or other ones), price (day rate x days or an own price),
    estimated expenses and a note. The guest then confirms."""

    start_date = forms.DateField(label=_("Erster Tag"), widget=_date_input())
    end_date = forms.DateField(label=_("Letzter Tag"), widget=_date_input(),
                               help_text=_("Du kannst Tage dazunehmen oder ein anderes Datum wählen. Die Tage werden "
                                           "provisorisch reserviert, bis der Gast zusagt."))
    guide_rate = _money(_("Tagesansatz (CHF)"), help_text=_("Aus deinen Stufen nach Gruppengrösse."))
    price = _money(_("Preis ohne Spesen (CHF)"), required=False,
                   help_text=_("Leer lassen: Tagesansatz × Tage. Oder einen eigenen Preis eintragen."))
    estimated_expenses = _money(_("Geschätzte Spesen (CHF)"), required=False,
                                help_text=_("Grobe Schätzung für den Gast, z. B. Reise, Hütte, Bahn. Abgerechnet werden "
                                            "die effektiven Spesen nach der Tour."))
    decision_date = forms.DateField(
        label=_("Entscheid-Frist"), widget=_date_input(),
        help_text=_("Bis dann entscheidest du definitiv, ob die Tour stattfindet (Wetter, Verhältnisse)."),
    )
    message = forms.CharField(label=_("Nachricht an den Gast (optional)"), max_length=2000, required=False,
                              widget=TEXTAREA,
                              help_text=_("z. B. warum du einen Tag dazunimmst oder ein anderes Datum vorschlägst."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["guide_rate"].widget.attrs["data-calc-guide-rate"] = ""  # read by static/js/pricing.js
        self.fields["price"].widget.attrs["data-price"] = ""

    @classmethod
    def initial_for(cls, booking) -> dict:
        today = timezone.localdate()
        return {"start_date": booking.start_date, "end_date": booking.end_date,
                "guide_rate": booking.guide.rate_for(booking.participants),
                "price": booking.price if booking.has_price else None,
                "estimated_expenses": booking.estimated_expenses,
                "decision_date": booking.decision_date or max(booking.start_date - timedelta(days=2), today),
                "message": booking.guide_message if booking.status == "offered" else ""}

    def clean(self):
        cleaned = super().clean()
        s, e, decide = cleaned.get("start_date"), cleaned.get("end_date"), cleaned.get("decision_date")
        today = timezone.localdate()
        if s and s < today:
            self.add_error("start_date", _("Das Datum liegt in der Vergangenheit."))
        if s and e and e < s:
            self.add_error("end_date", _("Das Ende muss nach dem Beginn liegen."))
        if decide and decide < today:
            self.add_error("decision_date", _("Das Datum liegt in der Vergangenheit."))
        elif decide and s and decide > s:
            self.add_error("decision_date", _("Die Entscheid-Frist muss vor dem Tourenbeginn liegen."))
        return cleaned

    @property
    def days(self) -> int:
        d = self.cleaned_data
        return (d["end_date"] - d["start_date"]).days + 1

    def final_price(self):
        d = self.cleaned_data
        if d.get("price") is not None:
            return money(d["price"])
        return money(d["guide_rate"] * Decimal(self.days))


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


def _check_range(form, cleaned, start="start_date", end="end_date"):
    s, e = cleaned.get(start), cleaned.get(end)
    if s and e and e < s:
        form.add_error(end, _("Das Ende muss nach dem Beginn liegen."))


class ExternalTourForm(PhotoMixin, forms.ModelForm):
    """A tour the guide leads for another provider. Pasting the link is enough: title, provider, dates and picture
    are read from the page; whatever can't be found the guide fills in."""

    NO_PICTURE = "none"

    picture_key = forms.CharField(required=False, widget=forms.HiddenInput)  # picture already read from the page

    class Meta:
        model = ExternalTour
        fields = ("url", "title", "provider", "category", "start_date", "end_date", "show_to_guests", "note")
        widgets = {"start_date": _date_input(), "end_date": _date_input(), "note": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "url": _("Link zum Tourenangebot, z. B. auf der Website der Bergschule. Titel, Anbieter und Datum "
                     "werden von der Seite übernommen."),
            "title": _("Leer lassen: wird von der verlinkten Seite übernommen."),
            "start_date": _("Leer lassen: wird von der verlinkten Seite übernommen."),
            "category": _("Ohne eigenes Bild wird ein Bild dieser Tourenart aus deiner Bibliothek verwendet."),
        }

    def __init__(self, *args, guide=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.guide = guide
        for name in ("title", "start_date", "end_date"):
            self.fields[name].required = False
        self.add_photo_fields(guide=guide)
        self.fields["photo"].help_text = _("Leer lassen: ein Bild dieser Tourenart aus deiner Bibliothek, sonst das "
                                           "Logo des Anbieters.")

    @property
    def picture_key_given(self) -> bool:
        return bool(self.cleaned_data.get("picture_key"))

    def clean(self):
        cleaned = super().clean()
        if pick := self.data.get(self.add_prefix("pick")):  # a date button: "2027-03-12:2027-03-14"
            try:
                start, end = (datetime.strptime(part, "%Y-%m-%d").date() for part in pick.split(":"))
            except ValueError:
                pass
            else:
                cleaned["start_date"], cleaned["end_date"] = start, end
        if cleaned.get("start_date") and not cleaned.get("end_date"):
            cleaned["end_date"] = cleaned["start_date"]
        _check_range(self, cleaned)
        if not cleaned.get("title") and not cleaned.get("url") and "url" not in self.errors:
            self.add_error("title", _("Bitte einen Titel oder einen Link angeben."))
        return cleaned

    def complete(self, found_dates) -> bool:
        """After reading the page: is everything there? Otherwise say what's missing."""
        if not self.cleaned_data.get("title"):
            self.add_error("title", _("Der Titel konnte nicht von der Seite gelesen werden. Bitte selbst eingeben."))
        if not self.cleaned_data.get("start_date"):
            if not self.cleaned_data.get("url"):
                self.add_error("start_date", _("Bitte ein Datum angeben."))
            elif len(found_dates) > 1:
                self.add_error("start_date", _("Auf der Seite stehen mehrere Daten. Wähle oben das richtige oder gib "
                                               "es ein."))
            else:
                self.add_error("start_date", _("Auf der Seite wurde kein Datum gefunden. Bitte selbst eingeben."))
        return not self.errors

    def own_picture(self):
        """The guide's own picture instead of the provider's: picked from the library, or the newest of its kind."""
        if picked := self.library_choice(self.guide):
            return picked
        category = self.cleaned_data.get("category")
        if not category:
            return None
        library = Picture.library_for(self.guide).filter(category=category)
        return library.filter(guide=self.guide).first() or library.first()

    def picture(self, guide, current=None):
        key = self.cleaned_data.get("picture_key")
        if key and key != self.NO_PICTURE:
            current = (Picture.library_for(guide).filter(key=key).first()
                       or Picture.objects.filter(guide=guide, key=key).first())
        if getattr(self, "_shrunk", None) or self.library_choice(guide):
            return super().picture(guide, current)
        return current

    def clean_url(self):
        url = self.cleaned_data["url"].strip()
        if url and not url.startswith("https://"):
            raise forms.ValidationError(_("Die Adresse muss mit https:// beginnen."))
        return url


class PartnerForm(forms.ModelForm):
    class Meta:
        model = Partner
        fields = ("name", "email")


class PartnerPeriodForm(forms.ModelForm):
    class Meta:
        model = PartnerPeriod
        fields = ("partner", "start_date", "end_date", "note")
        widgets = {"start_date": _date_input(), "end_date": _date_input()}
        labels = {"partner": _("Anbieter")}

    def __init__(self, *args, guide, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["partner"].queryset = guide.partners.all()
        self.fields["partner"].empty_label = None

    def clean(self):
        cleaned = super().clean()
        _check_range(self, cleaned)
        return cleaned


class PartnerOfferForm(PublicForm):
    """A partner places a tour in a period the guide released for them."""

    title = forms.CharField(label=_("Tour"), max_length=300)
    url = forms.URLField(label=_("Link zum Angebot"), max_length=1000, required=False)
    start_date = forms.DateField(label=_("Von"), widget=_date_input())
    end_date = forms.DateField(label=_("Bis"), widget=_date_input())
    contact_name = forms.CharField(label=_("Kontaktperson"), max_length=200)
    contact_email = forms.EmailField(label=_("E-Mail"))
    note = forms.CharField(label=_("Nachricht"), required=False, max_length=2000,
                           widget=forms.Textarea(attrs={"rows": 3}))

    def clean_title(self):
        return single_line(self.cleaned_data["title"])

    def clean_contact_name(self):
        return single_line(self.cleaned_data["contact_name"])

    def clean_url(self):
        url = self.cleaned_data["url"].strip()
        if url and not url.startswith("https://"):
            raise forms.ValidationError(_("Die Adresse muss mit https:// beginnen."))
        return url

    def clean(self):
        cleaned = super().clean()
        _check_range(self, cleaned)
        return cleaned


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
