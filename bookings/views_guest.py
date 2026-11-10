"""Guest pages, reached through the secret link in the booking emails (no login, decision 2)."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.ratelimit import allow, client_ip
from payments import stripe_service

from . import messaging, services
from .forms import GuestDetailsForm, GuestMessageForm
from .models import Booking, GuestProfile

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


PLANNING_STATUSES = (S.OFFERED, S.CONFIRMED, S.RESCHEDULE_PROPOSED, S.PAID)


def _details_form(booking, data=None):
    """None when planning details aren't needed (request not taken yet, booking over)."""
    if booking.status not in PLANNING_STATUSES or booking.end_date < timezone.localdate():
        return None
    profile = GuestProfile.for_booking(booking) or GuestProfile(
        guide=booking.guide, email=booking.guest_email.lower(), name=booking.guest_name,
        emergency_contact=booking.emergency_contact)
    return GuestDetailsForm(data, instance=profile, prefix="angaben")


@never_cache
def detail(request, token, message_form=None, details_form=None, status=200):
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
        "thread": booking.messages.filter(needs_review=False),
        "message_form": message_form or GuestMessageForm(),
        "details_form": details_form or _details_form(booking),
    }, status=status)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


GUEST_ACTIONS = {
    "annehmen": (services.accept_offer,
                 lambda: _("Danke! Deine Tour ist bestätigt. Trag unten noch deine Angaben für die Planung ein.")),
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
def message(request, token):
    """The guest writes to the guide on the booking page."""
    booking = _booking(request, token)
    form = GuestMessageForm(request.POST)
    if not form.is_valid():
        return detail(request, token, message_form=form, status=400)
    if not allow(f"guestmsg:{booking.pk}", limit=20, window_seconds=60 * 60):
        messages.error(request, _("Zu viele Nachrichten. Bitte versuche es später erneut."))
        return redirect("guest:detail", token=token)
    messaging.receive_from_guest_page(booking, form.cleaned_data["body"])
    messages.success(request, _("Nachricht gesendet."))
    return redirect(reverse("guest:detail", args=[token]) + "#nachrichten")


@require_POST
def details(request, token):
    """The guest enters or updates the details for planning the tour."""
    booking = _booking(request, token)
    form = _details_form(booking, request.POST)
    if form is None:
        return redirect("guest:detail", token=token)
    if not form.is_valid():
        return detail(request, token, details_form=form, status=400)
    form.instance.name = booking.guest_name
    form.save()
    messages.success(request, _("Danke! Deine Angaben sind gespeichert."))
    return redirect(reverse("guest:detail", args=[token]) + "#angaben")


@require_POST
def pay(request, token):
    booking = _booking(request, token)
    try:
        url = stripe_service.create_checkout_url(booking)
    except stripe_service.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("guest:detail", token=token)
    return redirect(url)
