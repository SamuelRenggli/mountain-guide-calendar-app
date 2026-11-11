"""Incoming email replies, posted by the mail service (Mailgun route with action forward("…/eingang/mail/")).

Every request is signed by Mailgun (HMAC-SHA256 over timestamp + token with the webhook signing key). Unsigned,
stale or replayed requests are refused. Mail that belongs to no booking is accepted and dropped, so the mail
service doesn't retry it.
"""

import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.ratelimit import allow, client_ip

from . import messaging

logger = logging.getLogger(__name__)

MAX_AGE_SECONDS = 15 * 60


def signature_valid(timestamp: str, token: str, signature: str) -> bool:
    key = settings.MAILGUN_WEBHOOK_SIGNING_KEY
    if not (key and timestamp and token and signature):
        return False
    try:
        if abs(time.time() - int(timestamp)) > MAX_AGE_SECONDS:
            return False
    except ValueError:
        return False
    expected = hmac.new(key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    return cache.add(f"mailgun-token:{token}", 1, MAX_AGE_SECONDS)  # each token only once


def sender_verified(post) -> bool:
    """Mailgun's checks of the sender's domain: SPF or DKIM must pass, otherwise the From address may be forged."""
    spf = post.get("X-Mailgun-Spf", "").strip().lower()
    dkim = post.get("X-Mailgun-Dkim-Check-Result", "").strip().lower()
    return spf == "pass" or dkim == "pass"


@csrf_exempt
@require_POST
def mail(request):
    if not allow(f"inbound:{client_ip(request)}", limit=300, window_seconds=10 * 60):
        return HttpResponse(status=429)
    post = request.POST
    if not signature_valid(post.get("timestamp", ""), post.get("token", ""), post.get("signature", "")):
        return HttpResponse(status=403)
    message = messaging.receive_email(
        recipient=post.get("recipient", ""),
        sender=post.get("sender") or post.get("from", ""),
        body=post.get("stripped-text") or post.get("body-plain", ""),
        message_id=post.get("Message-Id", ""),
        in_reply_to=post.get("In-Reply-To", ""),
        references=post.get("References", ""),
        sender_verified=sender_verified(post),
    )
    if message is None:
        logger.info("Inbound mail for %s matched no booking", post.get("recipient", "")[:100])
    return HttpResponse("ok", content_type="text/plain")
