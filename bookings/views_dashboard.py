"""Guide dashboard. Every query is scoped to request.user — guides only ever see their own data."""

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.forms import CalendarSyncForm, ProfileForm, RateTierFormSet
from accounts.models import AccessLog
from accounts.views import recent_auth_required
from core.ratelimit import allow
from payments import stripe_service

from . import availability, calsync, departures, export, monthgrid, services
from .forms import (
    BlockedPeriodForm, ChangeBookingForm, ConfirmForm, DepartureForm, EraseGuestDataForm, ExpenseFormSet, ExportForm,
    InvoiceForm, MessageForm, MoveDepartureForm, OfferForm, OpenPeriodForm, ProposeDateForm, RefundExtraForm, TourForm,
)
from .models import BlockedPeriod, Booking, Departure, OpenPeriod, Tour

S = Booking.Status
D = Departure.Status
SAVED = lambda: _("Gespeichert. Der Gast wurde per E-Mail informiert.")  # noqa: E731


@login_required
def home(request):
    requests = request.user.bookings.filter(departure__isnull=True).select_related("tour")
    today = timezone.localdate()
    upcoming_departures = request.user.departures.filter(status__in=[D.OPEN, D.CONFIRMED], end_date__gte=today)
    return render(request, "dashboard/home.html", {
        "needs_action": requests.filter(status=S.REQUESTED).order_by("deadline"),
        "departures": upcoming_departures,
        "to_invoice": requests.filter(status=S.CONFIRMED, start_date__lte=today),
        "upcoming": requests.filter(status=S.CONFIRMED, start_date__gt=today),
        "waiting_for_guest": requests.filter(status__in=[S.OFFERED, S.RESCHEDULE_PROPOSED, S.AWAITING_PAYMENT]),
        "history": requests.exclude(status__in=[S.REQUESTED, S.OFFERED, S.CONFIRMED, S.RESCHEDULE_PROPOSED,
                                                S.AWAITING_PAYMENT]).order_by("-updated_at")[:20],
        "is_new": not request.user.bookings.exists() and not upcoming_departures.exists(),
    })


# --- Bookings ----------------------------------------------------------------


def _own_booking(request, pk):
    return get_object_or_404(Booking.objects.select_related("tour", "guide", "departure"), pk=pk, guide=request.user)


def _detail_context(booking, **forms):
    context = {
        "booking": booking,
        "events": booking.events.all(),
        "confirm_form": ConfirmForm(prefix="confirm", initial=ConfirmForm.initial_for(booking)),
        "reject_form": MessageForm(prefix="reject"),
        "cancel_form": MessageForm(prefix="cancel"),
        "offer_form": OfferForm(prefix="offer", initial={
            "start_date": booking.start_date, "end_date": booking.end_date, **OfferForm.initial_for(booking),
        }),
        "change_form": ChangeBookingForm(prefix="change", initial={
            "start_date": booking.start_date, "end_date": booking.end_date, "participants": booking.participants,
            "price": booking.price,
        }),
        "propose_form": ProposeDateForm(prefix="propose"),
        "refund_form": RefundExtraForm(prefix="refund"),
        "invoice_lines": booking.invoice_lines.all(),
        "today": timezone.localdate(),
        "guide_rate": booking.guide.rate_for(booking.participants),
        "vat": booking.vat_included(),
    }
    context.update(forms)
    return context


@login_required
def booking_detail(request, pk):
    booking = _own_booking(request, pk)
    AccessLog.record(request, AccessLog.Action.VIEW_BOOKING, f"booking:{booking.pk}")
    return render(request, "dashboard/booking_detail.html", _detail_context(booking))


def _rerender(request, booking, **forms):
    return render(request, "dashboard/booking_detail.html", _detail_context(booking, **forms), status=400)


@login_required
@require_POST
def booking_action(request, pk, name):
    booking = _own_booking(request, pk)
    post = request.POST
    try:
        if name in ("confirm", "offer"):
            form = (ConfirmForm if name == "confirm" else OfferForm)(post, prefix=name)
            if not form.is_valid():
                return _rerender(request, booking, **{f"{name}_form": form})
            d = form.cleaned_data
            if name == "confirm":
                services.confirm(booking, d["message"], price=d["price"], estimated_expenses=d["estimated_expenses"],
                                 price_breakdown=form.breakdown(booking, booking.days))
            else:
                days = (d["end_date"] - d["start_date"]).days + 1
                services.counter_offer(booking, start_date=d["start_date"], end_date=d["end_date"], price=d["price"],
                                       message=d["message"], estimated_expenses=d["estimated_expenses"],
                                       price_breakdown=form.breakdown(booking, days))
        elif name in ("reject", "cancel"):
            form = MessageForm(post, prefix=name)
            if not form.is_valid():
                return _rerender(request, booking, **{f"{name}_form": form})
            {"reject": services.reject, "cancel": services.guide_cancel}[name](booking, form.cleaned_data["message"])
        elif name == "change":
            form = ChangeBookingForm(post, prefix=name)
            if not form.is_valid():
                return _rerender(request, booking, change_form=form)
            services.change_booking(booking, **form.cleaned_data)
        elif name == "propose":
            form = ProposeDateForm(post, prefix=name)
            if not form.is_valid():
                return _rerender(request, booking, propose_form=form)
            d = form.cleaned_data
            services.propose_new_date(booking, start_date=d["start_date"], end_date=d["end_date"], message=d["message"],
                                      incurred_costs=d["incurred_costs"] or 0, incurred_costs_note=d["incurred_costs_note"])
        elif name == "refund":
            form = RefundExtraForm(post, prefix=name)
            if not form.is_valid():
                return _rerender(request, booking, refund_form=form)
            departures.refund_extra(booking, form.cleaned_data["amount"])
        elif name == "paid":
            services.mark_paid_manually(booking)
        else:
            return redirect("dashboard:booking", pk=pk)
        messages.success(request, SAVED())
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:booking", pk=pk)


@login_required
def invoice(request, pk):
    booking = _own_booking(request, pk)
    if booking.is_registration or booking.status not in (S.CONFIRMED, S.AWAITING_PAYMENT):
        messages.error(request, _("Diese Buchung kann nicht abgerechnet werden."))
        return redirect("dashboard:booking", pk=pk)
    # Start from the offered price, or from the existing bill when correcting it.
    lines = list(booking.invoice_lines.all())
    initial = {"price": booking.price or None}
    if lines:
        tour_total = sum(line.amount for line in lines if line.kind == line.Kind.TOUR)
        initial = {"price": tour_total, "message": booking.guide_message}
    expenses = [{"description": line.description, "amount": line.unit_price}
                for line in lines if line.kind == line.Kind.EXPENSE]
    form = InvoiceForm(request.POST or None, initial=initial)
    expense_forms = ExpenseFormSet(request.POST or None, initial=expenses, prefix="spesen")
    if request.method == "POST" and form.is_valid() and expense_forms.is_valid():
        lines = [form.tour_line()] + [line for f in expense_forms if (line := f.line())]
        try:
            services.invoice(booking, lines, form.cleaned_data["message"])
        except services.BookingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Rechnung gesendet. Der Gast hat einen Link zur Zahlung erhalten."))
            return redirect("dashboard:booking", pk=pk)
    return render(request, "dashboard/invoice.html", {
        "booking": booking, "form": form, "expense_forms": expense_forms,
        "guide_rate": booking.guide.rate_for(booking.participants),
    })


# --- Scheduled dates (Termine) ------------------------------------------------


def _own_departure(request, pk):
    return get_object_or_404(Departure.objects.select_related("guide", "tour"), pk=pk, guide=request.user)


@login_required
def departure_list(request):
    today = timezone.localdate()
    mine = request.user.departures.all()
    return render(request, "dashboard/departures.html", {
        "upcoming": mine.filter(end_date__gte=today).exclude(status=D.CANCELLED),
        "past": mine.filter(Q(end_date__lt=today) | Q(status=D.CANCELLED)).order_by("-start_date")[:20],
        "templates": request.user.tours.filter(is_active=True),
        "can_pay": request.user.can_take_card_payments,
    })


def _departure_fields(form) -> dict:
    return {k: v for k, v in form.cleaned_data.items() if k != "save_as_template"}


@login_required
def departure_new(request):
    tour = None
    if template_id := request.GET.get("vorlage") or request.POST.get("vorlage"):
        tour = get_object_or_404(Tour, pk=template_id, guide=request.user)
    start = monthgrid.parse_date(request.GET.get("datum"))
    form = DepartureForm(request.POST or None, initial=DepartureForm.initial_from(tour, start))
    if request.method == "POST" and form.is_valid():
        try:
            departure = departures.create_departure(request.user, tour=tour, **_departure_fields(form),
                                                    save_as_template=form.cleaned_data["save_as_template"])
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, _("Termin veröffentlicht. Teile jetzt den Link."))
            return redirect("dashboard:departure", pk=departure.pk)
    return render(request, "dashboard/departure_form.html", {
        "form": form, "tour": tour, "departure": None, "can_pay": request.user.can_take_card_payments,
    })


@login_required
def departure_edit(request, pk):
    departure = _own_departure(request, pk)
    form = DepartureForm(request.POST or None, instance=departure, editing=True)
    if request.method == "POST":
        if form.is_valid():
            try:
                departures.update_departure(departure, **form.cleaned_data)
            except services.BookingError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, _("Termin gespeichert."))
                return redirect("dashboard:departure", pk=pk)
        departure.refresh_from_db()  # the form wrote invalid values into the instance
    return render(request, "dashboard/departure_form.html", {"form": form, "departure": departure, "can_pay": True})


@login_required
def departure_detail(request, pk):
    """Participants with contacts, emergency contacts and experience, seats, waitlist, payments (R-150)."""
    departure = _own_departure(request, pk)
    AccessLog.record(request, AccessLog.Action.VIEW_PARTICIPANTS, f"departure:{departure.pk}")
    registrations = departure.registrations.exclude(status=S.EXPIRED).order_by("created_at")
    return render(request, "dashboard/departure_detail.html", {
        "departure": departure,
        "registrations": registrations,
        "waitlist": departure.waitlist.all(),
        "seats_taken": departure.seats_confirmed(),
        "seats_free": departure.seats_free,
        "move_form": MoveDepartureForm(prefix="move", initial={"start_date": departure.start_date,
                                                               "end_date": departure.end_date}),
        "cancel_form": MessageForm(prefix="cancel"),
    })


@login_required
@require_POST
def departure_action(request, pk, name):
    departure = _own_departure(request, pk)
    try:
        if name == "confirm":
            departures.confirm_departure(departure)
            messages.success(request, _("Der Termin findet statt. Alle Angemeldeten wurden informiert."))
        elif name == "cancel":
            form = MessageForm(request.POST, prefix="cancel")
            form.is_valid()
            departures.cancel_departure(departure, form.cleaned_data.get("message", ""))
            messages.success(request, _("Termin abgesagt. Alle erhalten den vollen Betrag zurück."))
        elif name == "move":
            form = MoveDepartureForm(request.POST, prefix="move")
            if not form.is_valid():
                messages.error(request, _("Bitte gib ein gültiges Ersatzdatum an."))
            else:
                departures.move_departure(departure, **form.cleaned_data)
                messages.success(request, _("Ersatzdatum gesendet. Die Gäste sagen zu oder stornieren kostenlos."))
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:departure", pk=pk)


# --- Tour templates ----------------------------------------------------------


@login_required
def tours(request):
    return render(request, "dashboard/tours.html", {"tours": request.user.tours.all()})


@login_required
def tour_edit(request, pk=None):
    tour = get_object_or_404(Tour, pk=pk, guide=request.user) if pk else Tour(guide=request.user)
    form = TourForm(request.POST or None, instance=tour)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Vorlage gespeichert."))
        return redirect("dashboard:tours")
    return render(request, "dashboard/tour_form.html", {"form": form, "tour": tour})


@login_required
@require_POST
def tour_delete(request, pk):
    get_object_or_404(Tour, pk=pk, guide=request.user).delete()  # scheduled dates keep their own copy
    messages.success(request, _("Vorlage gelöscht."))
    return redirect("dashboard:tours")


# --- Availability ----------------------------------------------------------


@login_required
def availability_view(request):
    guide = request.user
    block_form = BlockedPeriodForm(prefix="block")
    open_form = OpenPeriodForm(prefix="open")
    ical_form = CalendarSyncForm(instance=guide, prefix="ical")

    if request.method == "POST":
        which = request.POST.get("form")
        if which == "block":
            block_form = BlockedPeriodForm(request.POST, prefix="block")
            if block_form.is_valid():
                block = block_form.save(commit=False)
                block.guide = guide
                block.save()
                messages.success(request, _("Zeitraum gesperrt."))
                return redirect("dashboard:availability")
        elif which == "open":
            open_form = OpenPeriodForm(request.POST, prefix="open")
            if open_form.is_valid():
                period = open_form.save(commit=False)
                period.guide = guide
                period.save()
                messages.success(request, _("Zeitraum freigegeben."))
                return redirect("dashboard:availability")
        elif which == "ical":
            ical_form = CalendarSyncForm(request.POST, instance=guide, prefix="ical")
            if ical_form.is_valid():
                ical_form.save()
                _sync(request)
                return redirect("dashboard:availability")

    return render(request, "dashboard/availability.html", {
        "block_form": block_form,
        "open_form": open_form,
        "ical_form": ical_form,
        "blocks": guide.blocked_periods.filter(end_date__gte=timezone.localdate()),
        "open_periods": guide.open_periods.filter(end_date__gte=timezone.localdate()),
    })


@login_required
@require_POST
def block_delete(request, pk):
    get_object_or_404(BlockedPeriod, pk=pk, guide=request.user).delete()
    messages.success(request, _("Sperre entfernt."))
    return redirect("dashboard:availability")


@login_required
@require_POST
def open_delete(request, pk):
    get_object_or_404(OpenPeriod, pk=pk, guide=request.user).delete()
    messages.success(request, _("Freigabe entfernt."))
    return redirect("dashboard:availability")


def _sync(request):
    if not allow(f"icalsync:{request.user.pk}", limit=1, window_seconds=60):
        messages.warning(request, _("Bitte warte eine Minute vor dem nächsten Abgleich."))
        return
    try:
        count = calsync.sync_guide(request.user)
    except calsync.CalendarSyncError as exc:
        messages.error(request, _("Kalender-Abgleich fehlgeschlagen: %(error)s") % {"error": exc})
    else:
        if request.user.ical_url:
            messages.success(request, _("Kalender abgeglichen: %(count)s belegte Zeiträume übernommen.") % {"count": count})
        else:
            messages.success(request, _("Kalender-Verbindung entfernt."))


@login_required
@require_POST
def calendar_sync_now(request):
    _sync(request)
    return redirect("dashboard:availability")


@login_required
def calendar_view(request):
    guide = request.user
    month = monthgrid.parse_month(request.GET.get("monat"), timezone.localdate())
    weeks = monthgrid.weeks(month)
    first, last = weeks[0][0], weeks[-1][-1]
    states = availability.day_states(guide, first, last)
    bookings = guide.bookings.filter(
        status__in=Booking.ACTIVE_STATUSES, is_window=False, departure__isnull=True, start_date__lte=last,
        end_date__gte=first,
    ).select_related("tour")
    by_day, departures_by_day = {}, {}
    for b in bookings:
        for d in availability.days_between(b.start_date, b.end_date):
            by_day.setdefault(d, b)
    for dep in guide.departures.filter(status__in=[D.OPEN, D.CONFIRMED], start_date__lte=last, end_date__gte=first):
        for d in availability.days_between(dep.start_date, dep.end_date):
            departures_by_day.setdefault(d, []).append(dep)
    opened = set()
    for p in guide.open_periods.filter(start_date__lte=last, end_date__gte=first):
        opened.update(availability.days_between(p.start_date, p.end_date))
    return render(request, "dashboard/calendar.html", {
        "month_label": monthgrid.month_label(month),
        "prev_month": f"{monthgrid.shift_month(month, -1):%Y-%m}",
        "next_month": f"{monthgrid.shift_month(month, 1):%Y-%m}",
        "weekday_names": monthgrid.weekday_names(),
        "has_open_periods": guide.open_periods.filter(end_date__gte=timezone.localdate()).exists(),
        "today": timezone.localdate(),
        "weeks": [
            [{"date": d, "in_month": d.month == month.month, "state": states[d], "booking": by_day.get(d),
              "departures": departures_by_day.get(d, []), "open": d in opened} for d in week]
            for week in weeks
        ],
    })


# --- Settings --------------------------------------------------------------


@login_required
def settings_view(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    tiers = RateTierFormSet(request.POST or None, instance=request.user, prefix="tiers")
    if request.method == "POST" and form.is_valid() and tiers.is_valid():
        form.save()
        tiers.save()
        messages.success(request, _("Einstellungen gespeichert."))
        return redirect("dashboard:settings")
    request.user.refresh_from_db()  # keep the embed code consistent with the saved slug
    return render(request, "dashboard/settings.html", {
        "form": form,
        "tiers": tiers,
        "site_url": settings.SITE_URL,
        "stripe_available": stripe_service.is_configured(),
        "erase_form": EraseGuestDataForm(),
    })


@login_required
@require_POST
@recent_auth_required
def erase_guest_data(request):
    """R-152: the guest asked for their data to be deleted."""
    form = EraseGuestDataForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data["email"]
        count = departures.erase_guest_data(request.user, email)
        AccessLog.record(request, AccessLog.Action.ERASE, f"guest:{email[:60]}")
        messages.success(request, _("Personendaten in %(count)s Buchung(en) gelöscht. Buchungen und Rechnungen bleiben "
                                    "erhalten.") % {"count": count})
    else:
        messages.error(request, _("Bitte gib eine gültige E-Mail-Adresse an."))
    return redirect("dashboard:settings")


@login_required
@recent_auth_required
def export_view(request):
    """R-203: income and expenses as CSV or Excel. Needs a fresh password/code (12-datenschutz.md, level B)."""
    today = timezone.localdate()
    form = ExportForm(request.GET or None, initial={"start": today.replace(month=1, day=1), "end": today,
                                                   "format": "xlsx"})
    if request.GET and form.is_valid():
        d = form.cleaned_data
        AccessLog.record(request, AccessLog.Action.EXPORT, f"{d['start']}..{d['end']}")
        return export.response(request.user, d["start"], d["end"], d["format"])
    return render(request, "dashboard/export.html", {"form": form})


# --- Pilot evaluation (admin) ----------------------------------------------


@staff_member_required
def pilot_stats(request):
    """R-030: per guide the number of requests, bookings, scheduled dates, registrations and cancellations."""
    AccessLog.record(request, AccessLog.Action.ADMIN_STATS)
    req = Q(bookings__departure__isnull=True)
    reg = Q(bookings__departure__isnull=False)
    guides = get_user_model().objects.filter(is_staff=False).annotate(
        n_requests=Count("bookings", filter=req, distinct=True),
        n_bookings=Count("bookings", filter=req & Q(bookings__status__in=Booking.BOOKED_STATUSES), distinct=True),
        n_departures=Count("departures", distinct=True),
        n_registrations=Count("bookings", filter=reg & Q(bookings__paid_at__isnull=False), distinct=True),
        n_cancellations=Count("bookings", filter=Q(bookings__status=S.CANCELLED), distinct=True),
    ).order_by("display_name")
    return render(request, "dashboard/pilot_stats.html", {"guides": guides})
