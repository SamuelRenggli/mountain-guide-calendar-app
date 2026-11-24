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
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.ratelimit import allow, client_ip
from payments import stripe_service

from . import album as albums
from . import messaging, services
from .forms import CompanionDetailsForm, GuestDetailsForm, RegistrationDetailsForm
from .models import Booking, BookingEvent, Companion, GuestProfile, Notification, Subscriber, money

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
    form_class = RegistrationDetailsForm if booking.is_registration else GuestDetailsForm  # paid online: no address
    return form_class(data, instance=profile, prefix="angaben", remark=saved.get("remark", ""),
                            keep=saved.get("keep", False), guide_name=booking.guide.display_name,
                            forward_link=booking.status == S.OFFERED and booking.participants > 1)


@never_cache
def detail(request, token, details_form=None, status=200):
    booking = _booking(request, token)
    # An offer: the details form only opens once the guest clicked "Zusagen"; saving it is the definite yes.
    confirming = booking.status == S.OFFERED and (request.GET.get("zusagen") == "1" or details_form is not None)
    if booking.status == S.OFFERED and not confirming:
        details_form = None
    else:
        details_form = details_form or _details_form(booking)
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
        "guide_rate": booking.guide_rate,
        "details_form": details_form,
        "confirming": confirming,
        "companions_open": companions_open(booking),
        "companion_names": [c.name for c in booking.companions.all()],
        "album": albums.Album(booking.departure if booking.is_registration else booking),
    }, status=status)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


def companions_open(booking) -> bool:
    """Fellow participants can enter their details: a group booking that is agreed and not over yet."""
    return booking.participants > 1 and booking.planning_open and booking.status != S.OFFERED


# The yes to an offer is no action of its own: it comes with the planning details (details() below).
GUEST_ACTIONS = {
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
    try:
        with transaction.atomic():
            Booking.objects.filter(pk=booking.pk).update(guest_details=entered_details(form),
                                                         updated_at=timezone.now())
            keep_in_guest_file(booking.guide, booking.guest_email, booking.guest_name, form)
            subscribe(booking.guide, booking.guest_email, booking.guest_name, booking.language, form)
            if booking.status == S.OFFERED:
                services.accept_offer(booking)  # the definite yes; rolls the details back if too late
            else:
                messaging.notify(booking.guide, Notification.Kind.DETAILS, booking)
    except services.BookingError as exc:
        messages.error(request, str(exc))
        return redirect("guest:detail", token=token)
    if booking.status == S.OFFERED:
        messages.success(request, _("Danke! Deine Zusage ist definitiv, die Tour ist bestätigt."))
        return redirect("guest:detail", token=token)
    return redirect(reverse("guest:detail", args=[token]) + "?angaben=1#angaben")


def entered_details(form) -> str:
    entered = form.save(commit=False)
    details = {f: getattr(entered, f) for f in GuestProfile.PLANNING_FIELDS}
    return json.dumps({**details, "remark": form.cleaned_data["remark"], "keep": form.cleaned_data["keep"]})


def subscribe(guide, email, name, language, form):
    """The guest ticked "tell me about new tours"."""
    if form.cleaned_data.get("news"):
        Subscriber.subscribe(guide, email, name, language)


def keep_in_guest_file(guide, email, name, form):
    """The guest agreed that the guide keeps the details in the guest file for later tours."""
    if not form.cleaned_data["keep"] or not email:
        return
    entered = form.instance
    profile = GuestProfile.objects.filter(guide=guide, email__iexact=email).first() or GuestProfile(
        guide=guide, email=email.lower())
    profile.name = name
    for field in GuestProfile.PLANNING_FIELDS:
        setattr(profile, field, getattr(entered, field))
    profile.save()


@never_cache
def companion(request, pk, code):
    """A fellow participant enters their own details through the link the booking guest passed on (no login).
    Each submission adds one person; they never see anyone else's details."""
    if not allow(f"companion:{client_ip(request)}", limit=60, window_seconds=10 * 60):
        raise Http404
    booking = get_object_or_404(Booking.objects.select_related("guide", "tour", "departure"), pk=pk)
    if not constant_time_compare(code, booking.companion_code) or not companions_open(booking):
        raise Http404
    if not request.language_from_url and not request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME):
        translation.activate(booking.language)
        request.LANGUAGE_CODE = booking.language
    form = CompanionDetailsForm(request.POST or None, instance=GuestProfile(guide=booking.guide), prefix="angaben",
                                guide_name=booking.guide.display_name)
    saved = request.GET.get("gespeichert") == "1"
    if request.method == "POST":
        if booking.companions.count() >= services.MAX_CUSTOM_PARTICIPANTS:
            raise Http404
        if form.is_valid():
            d = form.cleaned_data
            with transaction.atomic():
                Companion.objects.create(booking=booking, name=d["name"], email=d["email"], phone=d["phone"],
                                         details=entered_details(form))
                keep_in_guest_file(booking.guide, d["email"], d["name"], form)
                subscribe(booking.guide, d["email"], d["name"], booking.language, form)
                messaging.notify(booking.guide, Notification.Kind.DETAILS, booking)
                services.log(booking, BookingEvent.Actor.GUEST, booking.status, booking.status,
                             f"Angaben von Mitreisendem: {d['name']}")
            return redirect(reverse("companion", args=[pk, code]) + "?gespeichert=1")
    response = render(request, "guest/companion.html", {"booking": booking, "form": None if saved else form,
                                                        "saved": saved}, status=400 if form.errors else 200)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@require_POST
def pay(request, token):
    booking = _booking(request, token)
    try:
        url = stripe_service.create_checkout_url(booking)
    except stripe_service.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("guest:detail", token=token)
    return redirect(url)


@never_cache
def unsubscribe(request, token):
    """The link in every tour announcement and newsletter. GET asks, POST unsubscribes (mail scanners open links)."""
    if not allow(f"unsubscribe:{client_ip(request)}", limit=60, window_seconds=10 * 60):
        raise Http404
    sub = Subscriber.objects.select_related("guide").filter(token=token).first()
    done = False
    if request.method == "POST" and sub:
        sub.delete()
        done = True
    response = render(request, "guest/unsubscribe.html", {"sub": sub, "done": done})
    response.headers["X-Robots-Tag"] = "noindex"
    return response
