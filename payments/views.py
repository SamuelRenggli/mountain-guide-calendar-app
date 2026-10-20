import logging

import stripe
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import stripe_service

logger = logging.getLogger(__name__)


@login_required
def connect(request):
    """Start (or resume) Stripe onboarding. GET is allowed because Stripe sends guides back here on refresh."""
    if not stripe_service.is_configured():
        messages.error(request, "Online-Zahlungen sind auf dieser Plattform noch nicht eingerichtet.")
        return redirect("dashboard:settings")
    try:
        return redirect(stripe_service.onboarding_url(request.user))
    except stripe.StripeError:
        logger.exception("Stripe onboarding failed for guide %s", request.user.pk)
        messages.error(request, "Verbindung zu Stripe fehlgeschlagen. Bitte später erneut versuchen.")
        return redirect("dashboard:settings")


@login_required
def connect_return(request):
    try:
        stripe_service.refresh_account(request.user)
    except stripe.StripeError:
        logger.exception("Stripe account refresh failed for guide %s", request.user.pk)
    if request.user.stripe_charges_enabled:
        messages.success(request, "Stripe ist verbunden. Gäste können jetzt online bezahlen.")
    else:
        messages.warning(request, "Die Stripe-Einrichtung ist noch nicht abgeschlossen.")
    return redirect("dashboard:settings")


@csrf_exempt
@require_POST
def webhook(request):
    try:
        event = stripe_service.construct_event(request.body, request.headers.get("Stripe-Signature", ""))
    except ValueError:
        return HttpResponseBadRequest("invalid signature")
    stripe_service.handle_event(event)
    return HttpResponse("ok")
