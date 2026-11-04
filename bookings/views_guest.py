"""Guest pages, reached through the secret link in the booking emails (no login, decision 2)."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.ratelimit import allow, client_ip
from payments import stripe_service

from . import services
from .models import Booking

S = Booking.Status
FINISHED = (S.PAID, S.DECLINED, S.REJECTED, S.CANCELLED, S.EXPIRED)


def _booking(request, token):
    # Guessing a 256-bit token is hopeless, but cap probing anyway.
    if not allow(f"guestlink:{client_ip(request)}", limit=120, window_seconds=10 * 60):
        raise Http404
    booking = get_object_or_404(Booking.objects.select_related("guide", "tour", "departure"), token=token)
    # The link closes a while after a finished tour (12-datenschutz.md); open bills stay reachable.
    if booking.status in FINISHED and booking.end_date + timedelta(days=settings.GUEST_LINK_VALID_DAYS) < timezone.localdate():
        raise Http404
    if not request.language_from_url and not request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME):
        translation.activate(booking.language)  # the language the guest wrote in
        request.LANGUAGE_CODE = booking.language
    return booking


@never_cache
def detail(request, token):
    booking = _booking(request, token)
    can_cancel = booking.status in ((S.PAID, S.RESCHEDULE_PROPOSED) if booking.is_registration
                                    else (S.REQUESTED, S.OFFERED, S.CONFIRMED))
    fee_percent = services.cancellation_percent(booking) if can_cancel and booking.status in (S.PAID, S.CONFIRMED) else 0
    fee_base = booking.amount_paid if booking.is_registration else booking.price
    response = render(request, "guest/detail.html", {
        "booking": booking,
        "invoice_lines": booking.invoice_lines.all(),
        "can_pay_online": booking.status == S.AWAITING_PAYMENT and booking.guide.can_take_card_payments,
        "returned_from_payment": request.GET.get("bezahlt") == "1",
        "can_cancel": can_cancel,
        "fee_percent": fee_percent,
        "fee": round(fee_base * fee_percent / 100, 2),
        "refund": round(fee_base - fee_base * fee_percent / 100, 2),
        "vat": booking.vat_included(),
    })
    response.headers["X-Robots-Tag"] = "noindex"
    return response


GUEST_ACTIONS = {
    "annehmen": (services.accept_offer, lambda: _("Danke! Deine Buchung ist bestätigt.")),
    "ablehnen": (services.decline_offer, lambda: _("Du hast das Angebot abgelehnt.")),
    "ersatzdatum-annehmen": (services.accept_new_date, lambda: _("Danke! Das neue Datum ist bestätigt.")),
    "ersatzdatum-ablehnen": (services.decline_new_date, lambda: _("Die Buchung ist kostenlos storniert.")),
    "stornieren": (services.guest_cancel, lambda: _("Deine Buchung wurde storniert.")),
}


@require_POST
def action(request, token, name):
    booking = _booking(request, token)
    if name not in GUEST_ACTIONS:
        return redirect("guest:detail", token=token)
    func, success = GUEST_ACTIONS[name]
    try:
        func(booking)
        messages.success(request, success())
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect("guest:detail", token=token)


@require_POST
def pay(request, token):
    booking = _booking(request, token)
    try:
        url = stripe_service.create_checkout_url(booking)
    except stripe_service.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("guest:detail", token=token)
    return redirect(url)
