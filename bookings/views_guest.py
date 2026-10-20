"""Guest pages, reached through the secret link in the booking emails."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from payments import stripe_service

from . import services
from .models import Booking


def _booking(token):
    return get_object_or_404(Booking.objects.select_related("guide", "tour"), token=token)


@never_cache
def detail(request, token):
    booking = _booking(token)
    response = render(request, "guest/detail.html", {
        "booking": booking,
        "can_pay_online": booking.status == Booking.Status.AWAITING_PAYMENT and booking.guide.can_take_card_payments,
        "returned_from_payment": request.GET.get("bezahlt") == "1",
    })
    response.headers["X-Robots-Tag"] = "noindex"
    return response


GUEST_ACTIONS = {
    "annehmen": (services.accept_offer, "Danke! Deine Buchung ist bestätigt."),
    "ablehnen": (services.decline_offer, "Du hast das Angebot abgelehnt."),
    "stornieren": (services.guest_cancel, "Deine Buchung wurde storniert."),
}


@require_POST
def action(request, token, name):
    booking = _booking(token)
    if name not in GUEST_ACTIONS:
        return redirect("guest:detail", token=token)
    func, success = GUEST_ACTIONS[name]
    try:
        func(booking)
        messages.success(request, success)
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect("guest:detail", token=token)


@require_POST
def pay(request, token):
    booking = _booking(token)
    try:
        url = stripe_service.create_checkout_url(booking)
    except stripe_service.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("guest:detail", token=token)
    return redirect(url)
