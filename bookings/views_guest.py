"""Guest pages, reached through the secret link in the booking emails (no login, decision 2)."""

import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db import transaction
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
from .models import Booking, GuestProfile, money

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


def _details_form(booking, data=None):
    """None when planning details aren't needed (request not taken yet, booking over)."""
    if not booking.planning_open:
        return None
    # Only what was entered on this booking (questionnaire Q8): the address is never verified, so details from the
    # guide's guest file must not reach whoever booked with it. The guide still sees the file on the dashboard.
    details = {"emergency_contact": booking.emergency_contact}  # from the signup form, if any
    details.update({f: v for f, v in _saved_details(booking).items() if f in DETAIL_FIELDS})
    profile = GuestProfile(guide=booking.guide, email=booking.guest_email.lower(), name=booking.guest_name, **details)
    return GuestDetailsForm(data, instance=profile, prefix="angaben")


DETAIL_FIELDS = ("billing_address", "emergency_contact", "diet", "allergies", "club", "club_number", "travel_card",
                 "car_seats")


def _saved_details(booking) -> dict:
    try:
        details = json.loads(booking.guest_details) if booking.guest_details else {}
    except ValueError:
        return {}
    return details if isinstance(details, dict) else {}


@never_cache
def detail(request, token, message_form=None, details_form=None, status=200):
    booking = _booking(request, token)
    can_cancel = booking.status in ((S.PAID, S.RESCHEDULE_PROPOSED) if booking.is_registration
                                    else (S.REQUESTED, S.OFFERED, S.CONFIRMED))
    # Same rules and rounding as the cancellation itself (services.guest_cancel / departures.guest_cancel).
    charged = booking.status == S.PAID or (booking.status == S.CONFIRMED and booking.has_price)
    fee_percent = services.cancellation_percent(booking) if can_cancel and charged else 0
    fee_base = booking.amount_paid if booking.is_registration else booking.price
    fee = money(Decimal(fee_base) * fee_percent / 100)
    response = render(request, "guest/detail.html", {
        "booking": booking,
        "invoice_lines": booking.invoice_lines.all(),
        "can_pay_online": booking.status == S.AWAITING_PAYMENT and booking.guide.can_take_card_payments,
        "returned_from_payment": request.GET.get("bezahlt") == "1",
        "can_cancel": can_cancel,
        "fee_percent": fee_percent,
        "fee": fee,
        "refund": money(Decimal(fee_base) - fee),
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
    entered = form.save(commit=False)
    details = {f: getattr(entered, f) for f in DETAIL_FIELDS}
    with transaction.atomic():
        Booking.objects.filter(pk=booking.pk).update(guest_details=json.dumps(details))
        # The guide's guest file keeps the latest details for this address.
        profile = GuestProfile.for_booking(booking) or GuestProfile(guide=booking.guide,
                                                                    email=booking.guest_email.lower())
        profile.name = booking.guest_name
        for field, value in details.items():
            setattr(profile, field, value)
        profile.save()
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
