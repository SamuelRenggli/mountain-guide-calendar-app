"""Transactional emails (plain text) in German and English. Called after the DB transaction commits.

Guests get emails in the language they chose in the widget (Booking.language), guides in their
profile language (User.language). Subjects never contain guest input (header injection); bodies
are plain text, so there are no HTML escaping issues. Guest input is only ever passed as a format
*value*, never as a template, so braces in it are harmless.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

FOOTER = {
    "de": "\n\n--\nTourchef – Buchungen für Bergführer\n",
    "en": "\n\n--\nTourchef – bookings for mountain guides\n",
}

# key -> language -> (subject, body)
MESSAGES = {
    "request_to_guide": {
        "de": ("Neue Buchungsanfrage für {dates}",
               "Hallo {guide}\n\nDu hast eine neue Anfrage erhalten:\n\n{summary}\n{guest_contact}\n\n"
               "Nachricht:\n{guest_message}\n\nBitte antworte bis {deadline}, sonst verfällt die Anfrage:\n{guide_link}"),
        "en": ("New booking request for {dates}",
               "Hello {guide}\n\nYou have received a new request:\n\n{summary}\n{guest_contact}\n\n"
               "Message:\n{guest_message}\n\nPlease reply by {deadline}, otherwise the request expires:\n{guide_link}"),
    },
    "request_to_guest": {
        "de": ("Deine Anfrage bei {guide}",
               "Hallo {guest}\n\nDeine Anfrage ist bei {guide} eingegangen:\n\n{summary}\n\n"
               "Du erhältst eine E-Mail, sobald sie beantwortet ist. Status jederzeit hier:\n{guest_link}"),
        "en": ("Your request to {guide}",
               "Hello {guest}\n\n{guide} has received your request:\n\n{summary}\n\n"
               "You will get an email as soon as it has been answered. Check the status any time:\n{guest_link}"),
    },
    "confirmed": {
        "de": ("Deine Tour am {start} ist bestätigt",
               "Hallo {guest}\n\n{guide} hat deine Anfrage bestätigt:\n\n{summary}\n{guide_note}\n{billing_hint}"),
        "en": ("Your tour on {start} is confirmed",
               "Hello {guest}\n\n{guide} has confirmed your request:\n\n{summary}\n{guide_note}\n{billing_hint}"),
    },
    "offer_sent": {
        "de": ("Neuer Vorschlag von {guide}",
               "Hallo {guest}\n\n{guide} schlägt dir Folgendes vor:\n\n{summary}\n{guide_note}\n"
               "Annehmen oder ablehnen bis {deadline}:\n{guest_link}"),
        "en": ("New proposal from {guide}",
               "Hello {guest}\n\n{guide} proposes the following:\n\n{summary}\n{guide_note}\n"
               "Accept or decline by {deadline}:\n{guest_link}"),
    },
    "offer_accepted_guide": {
        "de": ("Gegenangebot angenommen",
               "Hallo {guide}\n\n{guest} hat dein Angebot angenommen:\n\n{summary}\n\n{guide_link}"),
        "en": ("Proposal accepted",
               "Hello {guide}\n\n{guest} has accepted your proposal:\n\n{summary}\n\n{guide_link}"),
    },
    "offer_accepted_guest": {
        "de": ("Deine Tour am {start} ist bestätigt",
               "Hallo {guest}\n\nDanke! Deine Buchung ist bestätigt:\n\n{summary}\n\n{billing_hint}"),
        "en": ("Your tour on {start} is confirmed",
               "Hello {guest}\n\nThank you! Your booking is confirmed:\n\n{summary}\n\n{billing_hint}"),
    },
    "offer_declined": {
        "de": ("Gegenangebot abgelehnt",
               "Hallo {guide}\n\n{guest} hat dein Angebot abgelehnt. Die Tage sind wieder frei.\n\n{summary}\n\n{guide_link}"),
        "en": ("Proposal declined",
               "Hello {guide}\n\n{guest} has declined your proposal. The days are free again.\n\n{summary}\n\n{guide_link}"),
    },
    "rejected": {
        "de": ("Deine Anfrage bei {guide}",
               "Hallo {guest}\n\nLeider kann {guide} deine Anfrage nicht annehmen.\n\n{summary}\n{guide_note}"),
        "en": ("Your request to {guide}",
               "Hello {guest}\n\nUnfortunately {guide} cannot accept your request.\n\n{summary}\n{guide_note}"),
    },
    "cancelled_by_guest": {
        "de": ("Buchung vom Gast storniert",
               "Hallo {guide}\n\n{guest} hat storniert. Die Tage sind wieder frei.\n\n{summary}\n\n{guide_link}"),
        "en": ("Booking cancelled by guest",
               "Hello {guide}\n\n{guest} has cancelled. The days are free again.\n\n{summary}\n\n{guide_link}"),
    },
    "cancelled_by_guide": {
        "de": ("Deine Tour am {start} wurde abgesagt",
               "Hallo {guest}\n\n{guide} musste die Tour leider absagen.\n\n{summary}\n{guide_note}\n"
               "Bei Fragen: {guide_email}"),
        "en": ("Your tour on {start} has been cancelled",
               "Hello {guest}\n\nUnfortunately {guide} had to cancel the tour.\n\n{summary}\n{guide_note}\n"
               "Questions: {guide_email}"),
    },
    "invoice": {
        "de": ("Rechnung für deine Tour vom {start}",
               "Hallo {guest}\n\nVielen Dank für die Tour! Hier ist die Abrechnung von {guide}:\n\n{invoice}\n{guide_note}\n"
               "{payment_instructions}"),
        "en": ("Invoice for your tour on {start}",
               "Hello {guest}\n\nThank you for the tour! Here is the invoice from {guide}:\n\n{invoice}\n{guide_note}\n"
               "{payment_instructions}"),
    },
    "paid_guest": {
        "de": ("Zahlung erhalten – vielen Dank",
               "Hallo {guest}\n\nVielen Dank, deine Zahlung ist eingegangen.\n\n{summary}\n\n"
               "Kontakt Bergführer: {guide}, {guide_email} {guide_phone}\n\n{guest_link}"),
        "en": ("Payment received – thank you",
               "Hello {guest}\n\nThank you, we have received your payment.\n\n{summary}\n\n"
               "Your mountain guide: {guide}, {guide_email} {guide_phone}\n\n{guest_link}"),
    },
    "paid_guide": {
        "de": ("Zahlung erhalten für {dates}",
               "Hallo {guide}\n\n{guest} hat bezahlt.\n\n{summary}\n\n{guide_link}"),
        "en": ("Payment received for {dates}",
               "Hello {guide}\n\n{guest} has paid.\n\n{summary}\n\n{guide_link}"),
    },
    "expired_guest": {
        "de": ("Deine Anfrage bei {guide} ist abgelaufen",
               "Hallo {guest}\n\nDie Frist ist abgelaufen, die Buchung wurde nicht abgeschlossen.\n\n{summary}"),
        "en": ("Your request to {guide} has expired",
               "Hello {guest}\n\nThe deadline has passed and the booking was not completed.\n\n{summary}"),
    },
    "expired_guide": {
        "de": ("Buchung abgelaufen",
               "Hallo {guide}\n\nDiese Buchung ist abgelaufen, die Tage sind wieder frei.\n\n{summary}\n"
               "Gast: {guest}\n\n{guide_link}"),
        "en": ("Booking expired",
               "Hello {guide}\n\nThis booking has expired, the days are free again.\n\n{summary}\n"
               "Guest: {guest}\n\n{guide_link}"),
    },
}

SUMMARY = {
    "de": "Tour:          {tour}\nDatum:         {dates}\nTeilnehmende:  {participants}\nPreis:         {price}",
    "en": "Tour:          {tour}\nDate:          {dates}\nParticipants:  {participants}\nPrice:         {price}",
}
OWN_IDEA = {"de": "Eigene Tourenidee", "en": "Own tour idea"}
WINDOW_NOTE = {"de": " (Zeitfenster, genaue Daten folgen)", "en": " (time window, exact dates to follow)"}
PRICE_BASE = {"de": "CHF {rate} pro Tag + Spesen", "en": "CHF {rate} per day + expenses"}
BILLING_HINT = {
    "de": "Nach der Tour erhältst du die Rechnung per E-Mail.\nDetails zu deiner Buchung: {guest_link}",
    "en": "You will receive the invoice by email after the tour.\nYour booking: {guest_link}",
}
PRICE_QUOTED = {"de": "CHF {price} + Spesen", "en": "CHF {price} + expenses"}
TOTAL = {"de": "Total", "en": "Total"}
PAY_ONLINE = {"de": "Online bezahlen: {guest_link}", "en": "Pay online: {guest_link}"}
PAY_TRANSFER = {
    "de": "Überweisung an:\n{guide}\nIBAN {iban}\nMitteilung: Tour {start}, {guest}",
    "en": "Bank transfer to:\n{guide}\nIBAN {iban}\nReference: Tour {start}, {guest}",
}
GUEST_CONTACT = {
    "de": "Gast:          {guest}\nE-Mail:        {guest_email}\nTelefon:       {guest_phone}",
    "en": "Guest:         {guest}\nEmail:         {guest_email}\nPhone:         {guest_phone}",
}
GUIDE_NOTE = {"de": "\nNachricht von {guide}:\n{message}\n", "en": "\nMessage from {guide}:\n{message}\n"}
PAYMENT_MANUAL = {
    "de": "{guide} sendet dir die Zahlungsangaben direkt.\nRechnung online: {guest_link}",
    "en": "{guide} will send you the payment details directly.\nInvoice online: {guest_link}",
}


def _lang(value) -> str:
    return value if value in ("de", "en") else "de"


def _dates(b) -> str:
    if b.start_date == b.end_date:
        return f"{b.start_date:%d.%m.%Y}"
    return f"{b.start_date:%d.%m.%Y} – {b.end_date:%d.%m.%Y}"


def _context(b, lang) -> dict:
    guide = b.guide
    ctx = {
        "guide": guide.display_name,
        "guide_email": guide.email,
        "guide_phone": guide.phone,
        "guest": b.guest_name,
        "guest_email": b.guest_email,
        "guest_phone": b.guest_phone or "–",
        "guest_message": b.guest_message or "–",
        "tour": b.tour.title if b.tour else OWN_IDEA[lang],
        "dates": _dates(b) + (WINDOW_NOTE[lang] if b.is_window else ""),
        "start": f"{b.start_date:%d.%m.%Y}",
        "participants": b.participants,
        "price": _price(b, lang),
        "deadline": f"{b.deadline:%d.%m.%Y}" if b.deadline else "–",
        "guest_link": b.guest_url,
        "guide_link": f"{settings.SITE_URL}/dashboard/buchungen/{b.pk}/",
    }
    ctx["summary"] = SUMMARY[lang].format(**ctx)
    ctx["guest_contact"] = GUEST_CONTACT[lang].format(**ctx)
    ctx["guide_note"] = GUIDE_NOTE[lang].format(guide=ctx["guide"], message=b.guide_message) if b.guide_message else ""
    ctx["billing_hint"] = BILLING_HINT[lang].format(**ctx)
    ctx["invoice"] = _invoice(b, lang)
    ctx["payment_instructions"] = _payment_instructions(b, lang, ctx)
    return ctx


def _amount(value) -> str:
    """750 rather than 750.00, but 82.50 stays."""
    value = Decimal(value)
    return f"{value:.0f}" if value == value.to_integral_value() else f"{value:.2f}"


def _price(b, lang) -> str:
    if b.is_invoiced:
        return f"CHF {Decimal(b.price):.2f}"
    if b.has_price:
        return PRICE_QUOTED[lang].format(price=f"{Decimal(b.price):.2f}")
    return PRICE_BASE[lang].format(rate=_amount(b.guide.day_rate))


def _invoice(b, lang) -> str:
    rows = [f"{line.description:<48} {line.amount:>10}" for line in b.invoice_lines.all()]
    rows.append(f"{TOTAL[lang] + ' CHF':<48} {b.price:>10}")
    return "\n".join(rows)


def _payment_instructions(b, lang, ctx) -> str:
    guide = b.guide
    parts = []
    if guide.can_take_card_payments:
        parts.append(PAY_ONLINE[lang].format(**ctx))
    if guide.iban:
        parts.append(PAY_TRANSFER[lang].format(**ctx, iban=guide.iban))
    return "\n\n".join(parts) or PAYMENT_MANUAL[lang].format(**ctx)


def _send(key, b, *, to_guest: bool):
    lang = _lang(b.language if to_guest else b.guide.language)
    to = b.guest_email if to_guest else b.guide.email
    subject, body = MESSAGES[key][lang]
    ctx = _context(b, lang)
    try:
        send_mail(subject.format(**ctx), body.format(**ctx).strip() + FOOTER[lang], settings.DEFAULT_FROM_EMAIL, [to])
    except Exception:
        logger.exception("Email %s for booking %s failed", key, b.pk)


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


def cancelled_by_guide(b):
    _send("cancelled_by_guide", b, to_guest=True)


def invoice_sent(b):
    _send("invoice", b, to_guest=True)


def paid(b):
    _send("paid_guest", b, to_guest=True)
    _send("paid_guide", b, to_guest=False)


def expired(b):
    _send("expired_guest", b, to_guest=True)
    _send("expired_guide", b, to_guest=False)
