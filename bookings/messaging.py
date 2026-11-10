"""Messages between guide and guest, plus the notifications top right in the dashboard.

The guide writes in the dashboard. The guest gets a plain email from "<Guide> via Tourchef" whose reply address
belongs to the booking (antwort+<booking number><code>@<domain>), and simply replies. The mail service posts the reply
to /eingang/mail/ (views_inbound.py); it lands in the booking's history. Guests can also write on their booking page.

Replies are matched by the address, otherwise by the email headers (In-Reply-To / References). A reply from an address
that isn't the guest's is kept for the guide to review and never shown to the guest.
"""

import logging
import re
from email.utils import formataddr, make_msgid, parseaddr

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from .models import Booking, Message, Notification

logger = logging.getLogger(__name__)

MAX_BODY = 10000

SUBJECT_TO_GUEST = _("Nachricht von {guide}: {tour}")
BODY_TO_GUEST = _("{body}\n\n--\nAntworte einfach auf diese E-Mail. {guide} sieht deine Antwort in Tourchef.\n"
                  "Ganzer Verlauf und deine Buchung: {guest_link}")
BODY_TO_GUEST_NO_REPLY = _("{body}\n\n--\nAntworte einfach auf diese E-Mail, sie geht direkt an {guide}.\n"
                           "Deine Buchung: {guest_link}")
ASK_DETAILS = _("Hallo {guest}\n\nFür die Planung unserer Tour «{tour}» ({dates}) brauche ich noch ein paar Angaben: "
                "Rechnungsadresse, Notfallkontakt, Ernährung und Allergien, Alpenverein (wegen Gegenrecht in den "
                "Hütten), GA oder Halbtax und ob du mit dem Auto kommen könntest.\n\n"
                "Bitte trag sie hier ein (dauert zwei Minuten, beim nächsten Mal ist alles schon ausgefüllt):\n"
                "{details_link}\n\nDanke und bis bald\n{guide}")
SUBJECT_TO_GUIDE = _("Neue Nachricht von {guest}")
BODY_TO_GUIDE = _("Hallo {guide}\n\n{guest} schreibt zu «{tour}» ({dates}):\n\n{body}\n\n"
                  "Antworten und ganzer Verlauf:\n{guide_link}")


# --- Notifications ------------------------------------------------------------------


def notify(guide, kind, booking=None):
    return Notification.objects.create(guide=guide, kind=kind, booking=booking)


def mark_read(guide, booking):
    """The guide opened the booking: its notifications and the guest's messages count as seen."""
    now = timezone.now()
    guide.notifications.filter(booking=booking, read_at__isnull=True).update(read_at=now)
    booking.messages.filter(direction=Message.Direction.FROM_GUEST, read_at__isnull=True).update(read_at=now)


# --- Addresses ----------------------------------------------------------------------


def reply_address(booking) -> str:
    """antwort+4821k7qz@tourchef.ch, or "" when incoming mail isn't set up."""
    if not settings.REPLY_EMAIL_DOMAIN:
        return ""
    return f"{settings.REPLY_EMAIL_LOCAL}+{booking.pk}{booking.reply_code}@{settings.REPLY_EMAIL_DOMAIN}"


_ADDRESS = re.compile(r"^(?P<local>[^+@]+)\+(?P<pk>\d+)(?P<code>[a-z0-9]{4})@(?P<domain>.+)$", re.IGNORECASE)


def booking_for_address(address: str) -> Booking | None:
    match = _ADDRESS.match(parseaddr(address or "")[1].strip())
    if not match or not settings.REPLY_EMAIL_DOMAIN:
        return None
    if (match["local"].lower() != settings.REPLY_EMAIL_LOCAL.lower()
            or match["domain"].lower() != settings.REPLY_EMAIL_DOMAIN.lower()):
        return None
    return Booking.objects.select_related("guide").filter(pk=match["pk"], reply_code=match["code"].lower()).first()


def booking_for_headers(*header_values: str) -> Booking | None:
    """Fallback: the guest's mail program quotes the Message-ID of our email."""
    ids = [i for value in header_values for i in re.findall(r"<[^<>\s]+>", value or "")]
    if not ids:
        return None
    message = Message.objects.select_related("booking__guide").filter(email_message_id__in=ids).last()
    return message.booking if message else None


def _sender(guide) -> str:
    """"Anna Berg via Tourchef" <noreply@tourchef.ch>"""
    address = parseaddr(settings.DEFAULT_FROM_EMAIL)[1] or settings.DEFAULT_FROM_EMAIL
    return formataddr((f"{guide.display_name} via Tourchef", address))


def _message_id() -> str:
    domain = settings.REPLY_EMAIL_DOMAIN or parseaddr(settings.DEFAULT_FROM_EMAIL)[1].partition("@")[2] or "tourchef"
    return make_msgid(domain=domain)


# --- Sending ------------------------------------------------------------------------


def send_to_guest(booking, body: str) -> Message:
    """The guide writes to the guest. Stored right away; the email goes out after the transaction commits."""
    body = body.strip()[:MAX_BODY]
    message = Message.objects.create(booking=booking, direction=Message.Direction.TO_GUEST, body=body,
                                     email_message_id=_message_id(), read_at=timezone.now())
    transaction.on_commit(lambda: _email_guest(message))
    return message


def ask_for_details(booking) -> Message:
    """Ask the guest for the details needed to plan the tour (guest page, section #angaben), in their language."""
    lang = booking.language if booking.language in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
    with translation.override(lang):
        body = str(ASK_DETAILS).format(guest=booking.guest_name, tour=booking.title, guide=booking.guide.display_name,
                                       dates=f"{booking.start_date:%d.%m.%Y}", details_link=f"{booking.guest_url}#angaben")
    return send_to_guest(booking, body)


def record_sent(booking, body: str) -> Message:
    """Keep a message in the history that already went out inside another email (cancellation, new date)."""
    return Message.objects.create(booking=booking, direction=Message.Direction.TO_GUEST, body=body.strip()[:MAX_BODY],
                                  read_at=timezone.now())


def send_to_all(departure, body: str) -> int:
    """One message to everyone signed up for a scheduled date; each guest gets it in their own history."""
    guests = departure.registrations.filter(status__in=Booking.SEAT_STATUSES).select_related("guide", "departure")
    count = 0
    for booking in guests:
        send_to_guest(booking, body)
        count += 1
    return count


def _email_guest(message):
    booking = message.booking
    guide = booking.guide
    reply_to = reply_address(booking)
    lang = booking.language if booking.language in dict(settings.LANGUAGES) else settings.LANGUAGE_CODE
    previous = booking.messages.filter(direction=Message.Direction.FROM_GUEST).exclude(email_message_id="").last()
    headers = {"Message-ID": message.email_message_id}
    if previous:
        headers["In-Reply-To"] = headers["References"] = previous.email_message_id
    with translation.override(lang):
        ctx = {"guide": guide.display_name, "tour": booking.title, "body": message.body, "guest_link": booking.guest_url}
        subject = SUBJECT_TO_GUEST.format(**ctx)
        text = (BODY_TO_GUEST if reply_to else BODY_TO_GUEST_NO_REPLY).format(**ctx)
    try:
        EmailMessage(subject, text, _sender(guide), [booking.guest_email], reply_to=[reply_to or guide.email],
                     headers=headers).send()
    except Exception:
        logger.exception("Message email to guest failed (booking %s)", booking.pk)


# --- Receiving ----------------------------------------------------------------------


_QUOTE_START = re.compile(
    r"^(>|-{2,}\s*(Original|Ursprüngliche|Message d'origine|Messaggio originale)"
    r"|(Am|On|Le|Il) .{4,300}(schrieb|wrote|a écrit|ha scritto).{0,200}:\s*$"
    r"|(Von|From|De|Da)\s*:)",
    re.IGNORECASE,
)


def strip_quoted(text: str) -> str:
    """Only the new part of an email reply: drop everything from the quoted original on."""
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line.strip()):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _from_guest(booking, body, *, sender_email="", email_message_id="", needs_review=False) -> Message:
    message = Message.objects.create(booking=booking, direction=Message.Direction.FROM_GUEST, body=body[:MAX_BODY],
                                     sender_email=sender_email[:254], email_message_id=email_message_id[:255],
                                     needs_review=needs_review)
    notify(booking.guide, Notification.Kind.MESSAGE, booking)
    transaction.on_commit(lambda: _email_guide(message))
    return message


def receive_email(*, recipient: str, sender: str, body: str, message_id: str = "", in_reply_to: str = "",
                  references: str = "") -> Message | None:
    """An email reply from the mail service. Returns None when it belongs to no booking or was seen already."""
    booking = booking_for_address(recipient) or booking_for_headers(in_reply_to, references)
    if booking is None:
        return None
    if message_id and Message.objects.filter(email_message_id=message_id[:255]).exists():
        return None  # the mail service retried
    body = strip_quoted(body)
    if not body:
        return None
    sender_email = parseaddr(sender or "")[1].lower()
    with transaction.atomic():
        return _from_guest(booking, body, sender_email=sender_email, email_message_id=message_id,
                           needs_review=sender_email != booking.guest_email.lower())


def receive_from_guest_page(booking, body: str) -> Message:
    """The guest wrote on their booking page (the secret link is proof enough)."""
    with transaction.atomic():
        return _from_guest(booking, body.strip(), sender_email=booking.guest_email)


def _email_guide(message):
    booking = message.booking
    guide = booking.guide
    from .emails import _dates

    with translation.override(guide.language):
        ctx = {"guide": guide.display_name, "guest": booking.guest_name, "tour": booking.title,
               "dates": _dates(booking), "body": message.body,
               "guide_link": f"{settings.SITE_URL}/dashboard/buchungen/{booking.pk}/"}
        subject, text = SUBJECT_TO_GUIDE.format(**ctx), BODY_TO_GUIDE.format(**ctx)
    try:
        EmailMessage(subject, text, settings.DEFAULT_FROM_EMAIL, [guide.email]).send()
    except Exception:
        logger.exception("Message email to guide failed (booking %s)", booking.pk)


def approve(message):
    message.needs_review = False
    message.save(update_fields=["needs_review"])
