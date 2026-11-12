"""Transactional emails (plain text) in German, French, Italian and English. Called after the DB transaction commits.

Guests get emails in the language they used in the widget (Booking.language, R-012), guides in their
profile language. Texts are gettext strings with {placeholders}; translations live in locale/.
Subjects never contain guest input (header injection). Guest input is only ever passed as a format
*value*, never as a template, so braces in it are harmless. Emails carry only what the recipient needs:
guests never see other guests, guides get emergency contacts only on the dashboard (12-datenschutz.md).
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

FOOTER = _("Tourchef – Buchungen für Bergführer\nDatenschutz: {privacy_url}")

# key -> (subject, body)
MESSAGES = {
    "request_to_guide": (
        _("Neue Anfrage für {dates}"),
        _("Hallo {guide}\n\nDu hast eine neue Anfrage erhalten:\n\n{summary}\n{guest_contact}\n\n"
          "Tourenwunsch:\n{guest_message}\n\nBitte antworte bis {deadline}, sonst verfällt die Anfrage:\n{guide_link}"),
    ),
    "request_to_guest": (
        _("Deine Anfrage bei {guide}"),
        _("Hallo {guest}\n\nDeine Anfrage ist bei {guide} eingegangen:\n\n{summary}\n\n"
          "{guide} antwortet bis {deadline}. Du erhältst eine E-Mail. Status jederzeit hier:\n{guest_link}"),
    ),
    "confirmed": (
        _("Deine Tour am {start} ist bestätigt"),
        _("Hallo {guest}\n\n{guide} hat deine Anfrage bestätigt:\n\n{summary}\n{breakdown}{guide_note}\n{billing_hint}"),
    ),
    "offer_sent": (
        _("{guide} hat deine Anfrage angenommen – bitte bestätigen"),
        _("Hallo {guest}\n\n{guide} hat deine Anfrage angenommen und schlägt dir Folgendes vor:\n\n{summary}\n"
          "{breakdown}{guide_note}\n{payment_terms}\n\nBitte sag zu oder ab bis {deadline}:\n{guest_link}"),
    ),
    "offer_accepted_guide": (
        _("Tour bestätigt: {guest}"),
        _("Hallo {guide}\n\n{guest} hat deinem Vorschlag zugesagt. Die Tour ist bestätigt:\n\n{summary}\n\n{guide_link}"),
    ),
    "offer_accepted_guest": (
        _("Deine Tour am {start} ist bestätigt"),
        _("Hallo {guest}\n\nDanke! Deine Tour ist bestätigt:\n\n{summary}\n\n{payment_terms}\n\n"
          "Für die Planung braucht {guide} noch ein paar Angaben (Rechnungsadresse, Notfallkontakt, Ernährung, "
          "Alpenverein, Abo, Auto). Bitte trag sie hier ein:\n{details_link}"),
    ),
    "offer_declined": (
        _("Gegenangebot abgelehnt"),
        _("Hallo {guide}\n\n{guest} hat dein Angebot abgelehnt. Die Tage sind wieder frei.\n\n{summary}\n\n{guide_link}"),
    ),
    "rejected": (
        _("Deine Anfrage bei {guide}"),
        _("Hallo {guest}\n\nLeider kann {guide} deine Anfrage nicht annehmen.\n\n{summary}\n{guide_note}"),
    ),
    "cancelled_by_guest": (
        _("Buchung vom Gast storniert"),
        _("Hallo {guide}\n\n{guest} hat storniert. Die Tage sind wieder frei.\n\n{summary}\n\n{guide_link}"),
    ),
    "cancelled_by_guide": (
        _("Deine Tour am {start} wurde abgesagt"),
        _("Hallo {guest}\n\n{guide} musste die Tour leider absagen. Für dich entstehen keine Kosten.\n\n{summary}\n"
          "{guide_note}\nBei Fragen: {guide_email}"),
    ),
    "changed": (
        _("Deine Buchung bei {guide} wurde geändert"),
        _("Hallo {guest}\n\n{guide} hat deine Buchung angepasst:\n\n{changes}\n\nNeu:\n{summary}\n{guide_note}\n"
          "Passt etwas nicht? Melde dich bei {guide_email}.\n{guest_link}"),
    ),
    "new_date_proposed": (
        _("Ersatzdatum für {tour}"),
        _("Hallo {guest}\n\n{guide} muss die Tour wegen Wetter oder Verhältnissen verschieben und schlägt ein "
          "Ersatzdatum vor:\n\n{summary}\n{guide_note}{costs_note}\n"
          "Bitte sag bis {deadline} zu oder ab. Wenn das Datum nicht passt oder du nicht antwortest, wird die Buchung "
          "kostenlos storniert{refund_note}.\n{guest_link}"),
    ),
    "new_date_accepted": (
        _("Ersatzdatum angenommen"),
        _("Hallo {guide}\n\n{guest} hat das Ersatzdatum angenommen:\n\n{summary}\n\n{guide_link}"),
    ),
    "new_date_declined": (
        _("Ersatzdatum abgelehnt – Buchung storniert"),
        _("Hallo {guide}\n\n{guest} hat das Ersatzdatum abgelehnt oder nicht rechtzeitig geantwortet. Die Buchung ist "
          "storniert.\n\n{summary}\n\n{guide_link}"),
    ),
    "new_date_declined_guest": (
        _("Deine Buchung bei {guide} ist storniert"),
        _("Hallo {guest}\n\nDas Ersatzdatum hat nicht gepasst, deine Buchung ist storniert.{refund_note}\n\n"
          "{summary}\n\n{guest_link}"),
    ),
    "invoice": (
        _("Rechnung für deine Tour vom {start}"),
        _("Hallo {guest}\n\nHier ist die Abrechnung von {guide}:\n\n{invoice}\n{vat_line}{guide_note}\n"
          "{payment_instructions}"),
    ),
    "paid_guest": (
        _("Zahlungsbeleg – vielen Dank"),
        _("Hallo {guest}\n\nVielen Dank, deine Zahlung ist eingegangen.\n\n{receipt}\n\n"
          "Kontakt Bergführer: {guide}, {guide_email} {guide_phone}\n\n{guest_link}"),
    ),
    "paid_guide": (
        _("Zahlung erhalten für {dates}"),
        _("Hallo {guide}\n\n{guest} hat bezahlt.\n\n{summary}\n\n{guide_link}"),
    ),
    "expired_guest": (
        _("Deine Anfrage bei {guide} ist abgelaufen"),
        _("Hallo {guest}\n\nDie Frist ist abgelaufen, die Buchung wurde nicht abgeschlossen.\n\n{summary}"),
    ),
    "expired_guide": (
        _("Buchung abgelaufen"),
        _("Hallo {guide}\n\nDiese Buchung ist abgelaufen, die Tage sind wieder frei.\n\n{summary}\n"
          "Gast: {guest}\n\n{guide_link}"),
    ),
    # --- Scheduled dates (Termine) ---
    "registration_confirmed": (
        _("Anmeldung bestätigt: {tour} am {start}"),
        _("Hallo {guest}\n\nDu bist angemeldet – vielen Dank!\n\n{summary}\n\n"
          "Treffpunkt:     {meeting}\n"
          "Entscheid:      {guide} entscheidet bis {decision_deadline}, ob die Tour stattfindet. Fällt sie aus, "
          "erhältst du den vollen Betrag automatisch zurück.\n\n"
          "Anforderungen:\n{requirements}\n\nAusrüstung:\n{equipment}\n\nInbegriffen:\n{included}\n\nNicht inbegriffen:\n{not_included}\n\n"
          "Bergführer: {guide}, Telefon {guide_phone}, {guide_email}\n\n{receipt}\n\n"
          "Deine Anmeldung, Stornieren und Bedingungen:\n{guest_link}"),
    ),
    "details_reminder": (
        _("{guide} braucht noch deine Infos für die anstehende Tour"),
        _("Hallo {guest}\n\nFür «{tour}» am {start} fehlen noch deine Angaben für die Tourenplanung (Rechnungsadresse, "
          "Notfallkontakt, Ernährung, Alpenverein, Abo, Auto). Es dauert nur zwei Minuten:\n{details_link}\n\n"
          "Danke! {guide}"),
    ),
    "registration_to_guide": (
        _("Neue Anmeldung: {tour} am {start}"),
        _("Hallo {guide}\n\n{guest} hat {participants} Platz/Plätze gebucht und bezahlt.\n\n"
          "Angemeldet: {seats_taken} von {max_participants} (Mindestzahl {min_participants}).\n\n{departure_link}"),
    ),
    "registration_too_late": (
        _("Anmeldung nicht möglich – Betrag zurückbezahlt"),
        _("Hallo {guest}\n\nDeine Zahlung kam an, als die Plätze schon vergeben waren. Wir haben dir den vollen Betrag "
          "zurückbezahlt; er erscheint in einigen Tagen auf deiner Karte.\n\n{summary}"),
    ),
    "registration_stopped_by_move": (
        _("Termin verschoben – Anmeldung abgebrochen"),
        _("Hallo {guest}\n\n{guide} hat «{tour}» verschoben, während du bezahlt hast. Deine Anmeldung für das alte "
          "Datum ist deshalb abgebrochen. Wurde schon Geld abgebucht, erhältst du den vollen Betrag automatisch "
          "zurück; er erscheint in einigen Tagen auf deiner Karte.\n\nNeues Datum: {new_dates}\n\n"
          "Passt dir das neue Datum, kannst du dich hier neu anmelden:\n{departure_url}"),
    ),
    "payment_failed": (
        _("Anmeldung nicht abgeschlossen"),
        _("Hallo {guest}\n\nDie Zahlung für deine Anmeldung wurde nicht abgeschlossen, deshalb sind die Plätze nicht "
          "reserviert.\n\n{summary}\n\nSind noch Plätze frei, kannst du dich hier erneut anmelden:\n{departure_url}"),
    ),
    "registration_cancelled_guest": (
        _("Stornierung bestätigt: {tour} am {start}"),
        _("Hallo {guest}\n\nDeine Anmeldung ist storniert.\n\n{summary}\n\n"
          "Storno-Kosten gemäss Bedingungen: {percent} % = CHF {fee}\nRückzahlung: CHF {refund} "
          "(erscheint in einigen Tagen auf deiner Karte)"),
    ),
    "registration_cancelled_guide": (
        _("Abmeldung: {tour} am {start}"),
        _("Hallo {guide}\n\n{guest} hat {participants} Platz/Plätze storniert. Storno-Kosten {percent} % = CHF {fee}, "
          "Rückzahlung CHF {refund}.\nDie Plätze sind wieder frei; die Warteliste wurde informiert.\n\n"
          "Kulanz (mehr zurückzahlen): {guide_link}"),
    ),
    "departure_confirmed": (
        _("{tour} am {start} findet statt"),
        _("Hallo {guest}\n\n{guide} hat entschieden: Die Tour findet statt.\n\n{summary}\n\nTreffpunkt: {meeting}\n\n"
          "Ausrüstung:\n{equipment}\n\nBergführer: {guide}, Telefon {guide_phone}\n\n{guest_link}"),
    ),
    "departure_cancelled": (
        _("{tour} am {start} ist abgesagt"),
        _("Hallo {guest}\n\n{reason}\n\n{summary}\n{guide_note}\n"
          "Du erhältst den vollen Betrag von CHF {refund} automatisch zurück; er erscheint in einigen Tagen auf deiner "
          "Karte."),
    ),
    "departure_auto_cancelled_guide": (
        _("Termin automatisch abgesagt: {tour} am {start}"),
        _("Hallo {guide}\n\nDie Entscheid-Frist ist ohne Entscheid abgelaufen. Der Termin wurde deshalb abgesagt, "
          "{count} angemeldete Gäste erhalten ihr Geld zurück.{fees_kept}\n\n{departure_link}"),
    ),
    "departure_cancelled_guide": (
        _("Termin abgesagt: {tour} am {start}"),
        _("Hallo {guide}\n\nDu hast den Termin abgesagt. {count} angemeldete Gäste wurden per E-Mail informiert und "
          "erhalten den vollen Betrag automatisch zurück.{fees_kept}\n\n{departure_link}"),
    ),
    "decision_reminder": (
        _("Entscheid fällig: {tour} am {start}"),
        _("Hallo {guide}\n\nBis {decision_deadline} musst du entscheiden, ob «{tour}» stattfindet.\n\n"
          "Angemeldet: {seats_taken} von {max_participants}, Mindestzahl {min_participants} – {minimum_state}.\n\n"
          "Ohne Entscheid wird der Termin automatisch abgesagt und alle Gäste erhalten ihr Geld zurück.\n\n"
          "{departure_link}"),
    ),
    "waitlist_seat_free": (
        _("Platz frei: {tour} am {start}"),
        _("Hallo {guest}\n\nFür «{tour}» am {start} ist ein Platz frei geworden. Alle auf der Warteliste erhalten diese "
          "E-Mail gleichzeitig – wer zuerst bucht und bezahlt, erhält den Platz.\n\n{departure_url}"),
    ),
    "goodwill_refund": (
        _("Zusätzliche Rückzahlung von {guide}"),
        _("Hallo {guest}\n\n{guide} zahlt dir aus Kulanz zusätzlich CHF {amount} zurück. Der Betrag erscheint in einigen "
          "Tagen auf deiner Karte.\n\n{summary}"),
    ),
}

SUMMARY = _("Tour:          {tour}\nDatum:         {dates}\nTeilnehmende:  {participants}\nPreis:         {price}")
GUEST_CONTACT = _("Gast:          {guest}\nE-Mail:        {guest_email}\nTelefon:       {guest_phone}")
GUIDE_NOTE = _("\nNachricht von {guide}:\n{message}\n")
DECISION_NOTE = _("\nDefinitiver Entscheid, ob die Tour stattfindet (Wetter, Verhältnisse): bis {day}.\n")
PRICE_BASE = _("CHF {rate} pro Tag + Spesen")
PRICE_QUOTED = _("CHF {price} + Spesen")
PRICE_EXPENSES = _("CHF {price} + Spesen (geschätzt ca. CHF {expenses}, unverbindlich)")
PRICE_SEATS = _("CHF {price} ({seats} × CHF {per_person})")
BREAKDOWN_TITLE = _("Zusammensetzung:")
BREAKDOWN_GUIDE = _("Bergführer")
BREAKDOWN_ASPIRANT = _("Aspirant")
MESSAGES.update({
    "partner_offer_to_guide": (
        _("Neues Tourenangebot von {partner}"),
        _("Hallo {guide}\n\n{partner} möchte dich für diese Tour einplanen:\n\n{tour}\n{dates}\n{url}\n\n"
          "Kontakt: {contact} <{contact_email}>\nNachricht:\n{note}\n\n"
          "Die Tage sind vorläufig für dich reserviert. Bitte bestätige oder lehne ab:\n{guide_link}"),
    ),
    "partner_offer_confirmed": (
        _("{guide} hat die Tour bestätigt"),
        _("Hallo {contact}\n\n{guide} hat die Tour bestätigt und die Tage im Kalender eingetragen:\n\n"
          "{tour}\n{dates}\n\nWeitere Zeiträume: {partner_link}"),
    ),
    "partner_offer_declined": (
        _("{guide} kann die Tour nicht übernehmen"),
        _("Hallo {contact}\n\nLeider kann {guide} diese Tour nicht übernehmen:\n\n{tour}\n{dates}\n\n"
          "Weitere Zeiträume: {partner_link}"),
    ),
})

BREAKDOWN_ROW = _("{persons} × {role} × {days} Tage × CHF {rate} = CHF {amount}")
PAYMENT_TERMS = _("Bezahlt wird erst nach der Tour, nach effektivem Aufwand: Seid ihr einen Tag mehr oder weniger "
                  "unterwegs oder müsst abbrechen, ändert sich der Betrag. Dazu kommen die effektiven Spesen. "
                  "Bis dahin wird nichts belastet.")
BILLING_HINT = _("Nach der Tour erhältst du die Rechnung per E-Mail.\nDetails zu deiner Buchung: {guest_link}")
TOTAL = _("Total")
VAT_LINE = _("inkl. {rate} % MWST CHF {vat} · MWST-Nr. {vat_number}\n")
PAY_ONLINE = _("Online bezahlen (Karte/TWINT): {guest_link}")
PAY_TRANSFER = _("Überweisung an:\n{guide}\nIBAN {iban}\nMitteilung: Tour {start}, {guest}")
PAYMENT_MANUAL = _("{guide} sendet dir die Zahlungsangaben direkt.\nRechnung online: {guest_link}")
RECEIPT = _("Zahlungsbeleg\nEmpfänger:     {guide}{vat_number_line}\nFür:           {tour}, {dates}\n"
            "Betrag:        CHF {amount}{vat_amount_line}\nBezahlt am:    {paid_at} ({method})")
RECEIPT_VAT_NUMBER = _("\nMWST-Nr.:      {vat_number}")
RECEIPT_VAT = _(" (inkl. {rate} % MWST CHF {vat})")
COSTS_NOTE = _("\nBereits angefallene Kosten, die du bei einer Absage trägst: CHF {costs} ({note})\n")
REFUND_NOTE = _(" und du erhältst den vollen Betrag zurück")
REASON_GUIDE = _("{guide} musste die Tour leider absagen.")
REASON_AUTO = _("Die Tour findet nicht statt: {guide} hat nicht rechtzeitig über die Durchführung entschieden.")
FEES_KEPT = _("Diese Gäste haben sich vorher abgemeldet und Storno-Kosten bezahlt. Ob du ihnen die Gebühr "
              "zurückzahlst, entscheidest du (Kulanz auf der Buchung):")
FEE_KEPT_ROW = _("- {guest}: CHF {fee} – {link}")
MIN_REACHED = _("Mindestzahl erreicht")
MIN_MISSING = _("Mindestzahl noch nicht erreicht")
CHANGE_LABELS = {"dates": _("Datum"), "participants": _("Teilnehmende"), "price": _("Preis")}
NONE = "–"


def _dates(b) -> str:
    if b.start_date == b.end_date:
        return f"{b.start_date:%d.%m.%Y}"
    return f"{b.start_date:%d.%m.%Y} – {b.end_date:%d.%m.%Y}"


def _amount(value) -> str:
    """750 rather than 750.00, but 82.50 stays."""
    value = Decimal(value)
    return f"{value:.0f}" if value == value.to_integral_value() else f"{value:.2f}"


def _percent(value) -> str:
    """8.10 -> 8.1, 7.70 -> 7.7, 8.00 -> 8"""
    return format(Decimal(value).normalize(), "f")


def _price(b) -> str:
    if b.is_registration:
        return PRICE_SEATS.format(price=f"{Decimal(b.price):.2f}", seats=b.participants,
                                  per_person=_amount(b.departure.price_per_person))
    if b.is_invoiced:
        return f"CHF {Decimal(b.price):.2f}"
    if b.has_price and b.estimated_expenses:
        return PRICE_EXPENSES.format(price=f"{Decimal(b.price):.2f}", expenses=_amount(b.estimated_expenses))
    if b.has_price:
        return PRICE_QUOTED.format(price=f"{Decimal(b.price):.2f}")
    return PRICE_BASE.format(rate=_amount(b.guide.rate_for(b.participants)))


def _breakdown(b) -> str:
    if not b.price_breakdown or b.is_invoiced:
        return ""
    roles = {"guide": BREAKDOWN_GUIDE, "aspirant": BREAKDOWN_ASPIRANT}
    rows = [BREAKDOWN_ROW.format(persons=r["persons"], role=roles.get(r["role"], r["role"]),
                                 days=f"{r['days']:g}", rate=_amount(r["rate"]), amount=_amount(r["amount"]))
            for r in b.price_breakdown]
    return f"\n{BREAKDOWN_TITLE}\n" + "\n".join(rows) + "\n"


def _vat_line(b, amount=None) -> str:
    vat = b.vat_included(amount)
    if vat is None:
        return ""
    return VAT_LINE.format(rate=_percent(b.guide.vat_rate), vat=f"{vat:.2f}", vat_number=b.guide.vat_number)


def _receipt(b) -> str:
    guide = b.guide
    vat = b.vat_included(b.amount_paid)
    return RECEIPT.format(
        guide=guide.display_name, tour=b.title, dates=_dates(b), amount=f"{Decimal(b.amount_paid):.2f}",
        vat_number_line=RECEIPT_VAT_NUMBER.format(vat_number=guide.vat_number) if guide.charges_vat else "",
        vat_amount_line=RECEIPT_VAT.format(rate=_percent(guide.vat_rate), vat=f"{vat:.2f}") if vat is not None else "",
        paid_at=f"{timezone.localtime(b.paid_at):%d.%m.%Y}" if b.paid_at else NONE, method=b.get_payment_method_display() or NONE,
    )


def _invoice(b) -> str:
    rows = [f"{line.description:<48} {line.amount:>10}" for line in b.invoice_lines.all()]
    rows.append(f"{str(TOTAL) + ' CHF':<48} {b.price:>10}")
    return "\n".join(rows)


def _payment_instructions(b, ctx) -> str:
    guide = b.guide
    parts = []
    if guide.can_take_card_payments:
        parts.append(PAY_ONLINE.format(**ctx))
    if guide.iban:
        parts.append(PAY_TRANSFER.format(**ctx, iban=guide.iban))
    return "\n\n".join(parts) or PAYMENT_MANUAL.format(**ctx)


def _deadline(value) -> str:
    from django.utils import timezone

    return f"{timezone.localtime(value):%d.%m.%Y %H:%M}" if value else NONE


def _departure_context(d) -> dict:
    meeting = ", ".join(part for part in (d.meeting_point, f"{d.meeting_time:%H:%M}" if d.meeting_time else "") if part)
    return {
        "meeting": meeting or NONE,
        "decision_deadline": _deadline(d.decision_deadline),
        "requirements": d.requirements or NONE,
        "equipment": d.equipment or NONE,
        "included": d.included or NONE,
        "not_included": d.not_included or NONE,
        "seats_taken": d.seats_confirmed(),
        "min_participants": d.min_participants,
        "max_participants": d.max_participants,
        "departure_url": d.public_url,
        "departure_link": f"{settings.SITE_URL}/dashboard/termine/{d.pk}/",
        "privacy_url": f"{settings.SITE_URL}/rechtliches/datenschutz/",
    }


def _context(b) -> dict:
    guide = b.guide
    ctx = {
        "guide": guide.display_name,
        "guide_email": guide.email,
        "guide_phone": guide.phone or NONE,
        "guest": b.guest_name,
        "guest_email": b.guest_email,
        "guest_phone": b.guest_phone or NONE,
        "guest_message": b.guest_message or NONE,
        "tour": b.title,
        "dates": _dates(b),
        "start": f"{b.start_date:%d.%m.%Y}",
        "participants": b.participants,
        "price": _price(b),
        "deadline": _deadline(b.deadline),
        "guest_link": b.guest_url,
        "guide_link": f"{settings.SITE_URL}/dashboard/buchungen/{b.pk}/",
        "privacy_url": f"{settings.SITE_URL}/rechtliches/datenschutz/",
    }
    if b.departure_id:
        ctx.update({k: v for k, v in _departure_context(b.departure).items() if k not in ctx})
    ctx["summary"] = SUMMARY.format(**ctx)
    ctx["guest_contact"] = GUEST_CONTACT.format(**ctx)
    ctx["guide_note"] = GUIDE_NOTE.format(guide=ctx["guide"], message=b.guide_message) if b.guide_message else ""
    ctx["breakdown"] = _breakdown(b)
    if b.decision_date and not b.is_invoiced:
        ctx["breakdown"] += DECISION_NOTE.format(day=f"{b.decision_date:%d.%m.%Y}")
    ctx["billing_hint"] = BILLING_HINT.format(**ctx)
    ctx["payment_terms"] = str(PAYMENT_TERMS)
    ctx["details_link"] = f"{b.guest_url}#angaben"
    ctx["vat_line"] = _vat_line(b)
    ctx["invoice"] = _invoice(b) if b.is_invoiced else ""
    ctx["payment_instructions"] = _payment_instructions(b, ctx)
    ctx["receipt"] = _receipt(b) if b.paid_at else ""
    return ctx


def _deliver(key, lang, to, ctx):
    with translation.override(lang if lang in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE):
        subject, body = MESSAGES[key]
        try:
            text = body.format(**ctx).strip() + "\n\n--\n" + FOOTER.format(**ctx)
            send_mail(subject.format(**ctx), text, settings.DEFAULT_FROM_EMAIL, [to])
        except Exception:
            logger.exception("Email %s failed", key)


def _send(key, b, *, to_guest: bool, **extra):
    lang = b.language if to_guest else b.guide.language
    with translation.override(lang):
        ctx = {**_context(b), **extra}
    _deliver(key, lang, b.guest_email if to_guest else b.guide.email, ctx)


# --- Requests ---


def request_created(b):
    _send("request_to_guide", b, to_guest=False)
    _send("request_to_guest", b, to_guest=True)


def confirmed(b):
    _send("confirmed", b, to_guest=True)


def offer_sent(b):
    _send("offer_sent", b, to_guest=True)


def offer_accepted(b):
    _send("offer_accepted_guide", b, to_guest=False)
    _send("offer_accepted_guest", b, to_guest=True)


def offer_declined(b):
    _send("offer_declined", b, to_guest=False)


def rejected(b):
    _send("rejected", b, to_guest=True)


def cancelled_by_guest(b):
    _send("cancelled_by_guest", b, to_guest=False)
    if b.is_invoiced:
        invoice_sent(b)  # late cancellation: the guest gets the bill for the fee


def cancelled_by_guide(b):
    _send("cancelled_by_guide", b, to_guest=True)


def booking_changed(b, changes):
    with translation.override(b.language):
        text = "\n".join(f"{CHANGE_LABELS[field]}: {before} → {after}" for field, before, after in changes)
    _send("changed", b, to_guest=True, changes=text)


def new_date_proposed(b):
    with translation.override(b.language):
        extra = {
            "costs_note": COSTS_NOTE.format(costs=_amount(b.incurred_costs), note=b.incurred_costs_note or NONE)
            if b.incurred_costs else "",
            "refund_note": str(REFUND_NOTE) if b.is_registration else "",
        }
    _send("new_date_proposed", b, to_guest=True, **extra)


def new_date_answered(b, *, accepted: bool):
    if accepted:
        _send("new_date_accepted", b, to_guest=False)
        return
    _send("new_date_declined", b, to_guest=False)
    with translation.override(b.language):
        refund_note = (" " + str(REFUND_NOTE).strip().capitalize() + ".") if b.is_registration else ""
    _send("new_date_declined_guest", b, to_guest=True, refund_note=refund_note)
    if b.is_invoiced:
        invoice_sent(b)


def invoice_sent(b):
    _send("invoice", b, to_guest=True)


def paid(b):
    _send("paid_guest", b, to_guest=True)
    _send("paid_guide", b, to_guest=False)


def expired(b):
    _send("expired_guest", b, to_guest=True)
    _send("expired_guide", b, to_guest=False)


# --- Scheduled dates ---


def registration_confirmed(b):
    _send("registration_confirmed", b, to_guest=True)  # includes the receipt (R-125, R-204)
    _send("registration_to_guide", b, to_guest=False)  # R-126


def details_reminder(b):
    _send("details_reminder", b, to_guest=True)


def registration_too_late(b):
    _send("registration_too_late", b, to_guest=True)


def payment_failed(b):
    _send("payment_failed", b, to_guest=True)


def registration_stopped_by_move(b, d):
    new_dates = f"{d.start_date:%d.%m.%Y}" + (f" – {d.end_date:%d.%m.%Y}" if d.end_date != d.start_date else "")
    _send("registration_stopped_by_move", b, to_guest=True, new_dates=new_dates)


def registration_cancelled(b, *, fee, percent):
    extra = {"fee": f"{Decimal(fee):.2f}", "percent": percent, "refund": f"{Decimal(b.amount_paid) - Decimal(fee):.2f}"}
    _send("registration_cancelled_guest", b, to_guest=True, **extra)
    _send("registration_cancelled_guide", b, to_guest=False, **extra)


def departure_confirmed(b):
    _send("departure_confirmed", b, to_guest=True)


def departure_cancelled(b, *, automatic: bool):
    with translation.override(b.language):
        reason = (REASON_AUTO if automatic else REASON_GUIDE).format(guide=b.guide.display_name)
    _send("departure_cancelled", b, to_guest=True, reason=reason, refund=f"{Decimal(b.amount_paid):.2f}")


def _send_guide(key, d, **extra):
    guide = d.guide
    with translation.override(guide.language):
        ctx = {"guide": guide.display_name, "tour": d.title, "start": f"{d.start_date:%d.%m.%Y}",
               **_departure_context(d), **extra}
        if "minimum_state" in ctx:
            ctx["minimum_state"] = str(MIN_REACHED if d.minimum_reached else MIN_MISSING)
    _deliver(key, guide.language, guide.email, ctx)


def _fees_kept(d, bookings) -> str:
    """Guests who cancelled earlier and paid a fee: the guide decides whether to pay it back (questionnaire Q5)."""
    if not bookings:
        return ""
    with translation.override(d.guide.language):
        rows = [FEE_KEPT_ROW.format(guest=b.guest_name, fee=f"{b.refundable - b.refund_due:.2f}",
                                    link=f"{settings.SITE_URL}/dashboard/buchungen/{b.pk}/") for b in bookings]
        return "\n\n" + str(FEES_KEPT) + "\n" + "\n".join(rows)


def departure_auto_cancelled_guide(d, count, fees_kept=()):
    _send_guide("departure_auto_cancelled_guide", d, count=count, fees_kept=_fees_kept(d, fees_kept))


def departure_cancelled_guide(d, count, fees_kept=()):
    _send_guide("departure_cancelled_guide", d, count=count, fees_kept=_fees_kept(d, fees_kept))


def decision_reminder(d):
    _send_guide("decision_reminder", d, minimum_state="")


def waitlist_seat_free(entry):
    d = entry.departure
    ctx = {"guest": entry.name, "tour": d.title, "start": f"{d.start_date:%d.%m.%Y}", **_departure_context(d)}
    _deliver("waitlist_seat_free", entry.language, entry.email, ctx)


def goodwill_refund(b, amount):
    _send("goodwill_refund", b, to_guest=True, amount=f"{Decimal(amount):.2f}")


# --- Tours for partners ---


def _external_context(t) -> dict:
    dates = f"{t.start_date:%d.%m.%Y}" + (f" – {t.end_date:%d.%m.%Y}" if t.end_date != t.start_date else "")
    partner = t.partner
    return {
        "guide": t.guide.display_name, "partner": partner.name if partner else t.provider, "tour": t.title,
        "dates": dates, "url": t.url or NONE, "contact": t.contact_name or NONE,
        "contact_email": t.contact_email or NONE, "note": t.note or NONE,
        "guide_link": f"{settings.SITE_URL}/dashboard/extern/",
        "partner_link": partner.public_url if partner else NONE,
        "privacy_url": f"{settings.SITE_URL}/rechtliches/datenschutz/",
    }


def partner_offer_to_guide(t):
    lang = t.guide.language
    with translation.override(lang):
        ctx = _external_context(t)
    _deliver("partner_offer_to_guide", lang, t.guide.email, ctx)


def partner_offer_answered(t, *, accepted: bool):
    if not t.contact_email:
        return
    with translation.override(t.language):
        ctx = _external_context(t)
    _deliver("partner_offer_confirmed" if accepted else "partner_offer_declined", t.language, t.contact_email, ctx)
