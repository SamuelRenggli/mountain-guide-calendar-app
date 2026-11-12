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

from . import services
from .forms import GuestDetailsForm
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
    saved = booking.saved_details()
    profile = GuestProfile.from_booking(booking) or GuestProfile(
        guide=booking.guide, email=booking.guest_email.lower(), name=booking.guest_name,
        emergency_contact=booking.emergency_contact,  # from the signup form, if any
    )
    return GuestDetailsForm(data, instance=profile, prefix="angaben", remark=saved.get("remark", ""),
                            keep=saved.get("keep", False))


@never_cache
def detail(request, token, details_form=None, status=200):
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
        "details_just_saved": request.GET.get("angaben") == "1" and bool(booking.guest_details),
        "can_cancel": can_cancel,
        "fee_percent": fee_percent,
        "fee": fee,
        "refund": money(Decimal(fee_base) - fee),
        "vat": booking.vat_included(),
        "details_form": details_form or _details_form(booking),
    }, status=status)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


GUEST_ACTIONS = {
    "annehmen": (services.accept_offer,
                 lambda: _("Danke! Deine Tour ist bestätigt. Es fehlen nur noch deine Angaben für die Tourenplanung.")),
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
def details(request, token):
    """The guest enters or updates the details for planning the tour."""
    booking = _booking(request, token)
    form = _details_form(booking, request.POST)
    if form is None:
        return redirect("guest:detail", token=token)
    if not form.is_valid():
        return detail(request, token, details_form=form, status=400)
    entered = form.save(commit=False)
    keep = form.cleaned_data["keep"]
    details = {f: getattr(entered, f) for f in GuestProfile.PLANNING_FIELDS}
    with transaction.atomic():
        Booking.objects.filter(pk=booking.pk).update(
            guest_details=json.dumps({**details, "remark": form.cleaned_data["remark"], "keep": keep}),
            updated_at=timezone.now())
        if keep:  # the guest agreed that the guide keeps the details in the guest file for later tours
            profile = GuestProfile.for_booking(booking) or GuestProfile(guide=booking.guide,
                                                                        email=booking.guest_email.lower())
            profile.name = booking.guest_name
            for field, value in details.items():
                setattr(profile, field, value)
            profile.save()
    return redirect(reverse("guest:detail", args=[token]) + "?angaben=1#angaben")


@require_POST
def pay(request, token):
    booking = _booking(request, token)
    try:
        url = stripe_service.create_checkout_url(booking)
    except stripe_service.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("guest:detail", token=token)
    return redirect(url)
