import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_FROM     = os.getenv("MAIL_FROM", "")
MAIL_SERVER   = os.getenv("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT     = int(os.getenv("MAIL_PORT", "587"))


def _send(to: str, subject: str, html: str):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        logger.warning("[email] SMTP credentials not set — skipping email to %s", to)
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = MAIL_FROM
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(MAIL_USERNAME, MAIL_PASSWORD)
            s.sendmail(MAIL_FROM, to, msg.as_string())
        logger.info("[email] Sent '%s' to %s", subject, to)
    except Exception as e:
        logger.error("[email] Failed to send to %s: %s", to, e)


def notify_guide_new_booking(guide_email: str, guide_name: str,
                              guest_name: str, guest_email: str, date: str):
    subject = f"Neue Buchungsanfrage – {date}"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px">
      <h2 style="color:#1a1a2e">Neue Buchungsanfrage</h2>
      <p>Hallo {guide_name},</p>
      <p>Du hast eine neue Buchungsanfrage erhalten:</p>
      <table style="border-collapse:collapse;width:100%;margin:16px 0">
        <tr><td style="padding:8px;background:#f8f9fa;font-weight:600">Datum</td>
            <td style="padding:8px">{date}</td></tr>
        <tr><td style="padding:8px;background:#f8f9fa;font-weight:600">Gast</td>
            <td style="padding:8px">{guest_name}</td></tr>
        <tr><td style="padding:8px;background:#f8f9fa;font-weight:600">E-Mail</td>
            <td style="padding:8px"><a href="mailto:{guest_email}">{guest_email}</a></td></tr>
      </table>
      <p>Melde dich in deinem <a href="http://localhost:8000/dashboard/1">Dashboard</a> an,
         um die Anfrage zu bestätigen.</p>
      <p style="color:#888;font-size:0.85rem;margin-top:24px">Tourchef Plattform</p>
    </div>
    """
    _send(guide_email, subject, html)


def notify_guest_booking_received(guest_email: str, guest_name: str,
                                   guide_name: str, date: str):
    subject = f"Buchungsanfrage erhalten – {date}"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px">
      <h2 style="color:#1a1a2e">Deine Buchungsanfrage</h2>
      <p>Hallo {guest_name},</p>
      <p>Deine Anfrage wurde erfolgreich übermittelt:</p>
      <table style="border-collapse:collapse;width:100%;margin:16px 0">
        <tr><td style="padding:8px;background:#f8f9fa;font-weight:600">Bergführer</td>
            <td style="padding:8px">{guide_name}</td></tr>
        <tr><td style="padding:8px;background:#f8f9fa;font-weight:600">Datum</td>
            <td style="padding:8px">{date}</td></tr>
      </table>
      <p>Der Bergführer wird sich bei dir melden, sobald er die Anfrage bearbeitet hat.</p>
      <p style="color:#888;font-size:0.85rem;margin-top:24px">Tourchef Plattform</p>
    </div>
    """
    _send(guest_email, subject, html)
