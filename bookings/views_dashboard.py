"""Guide dashboard. Every query is scoped to request.user — guides only ever see their own data."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import CalendarSyncForm, ProfileForm
from core.ratelimit import allow

from . import availability, calsync, monthgrid, services
from .forms import (
    BlockedPeriodForm, ConfirmForm, ExpenseFormSet, InvoiceForm, MessageForm, OfferForm, OpenPeriodForm, TourForm,
)
from .models import BlockedPeriod, Booking, OpenPeriod, Tour

S = Booking.Status


@login_required
def home(request):
    bookings = request.user.bookings.select_related("tour")
    today = timezone.localdate()
    return render(request, "dashboard/home.html", {
        "needs_action": bookings.filter(status=S.REQUESTED).order_by("deadline"),
        "to_invoice": bookings.filter(status=S.CONFIRMED, start_date__lte=today),
        "upcoming": bookings.filter(status=S.CONFIRMED, start_date__gt=today),
        "waiting_for_guest": bookings.filter(status__in=[S.OFFERED, S.AWAITING_PAYMENT]),
        "history": bookings.exclude(status__in=[S.REQUESTED, S.OFFERED, S.CONFIRMED, S.AWAITING_PAYMENT])
        .order_by("-updated_at")[:20],
        "is_new": not bookings.exists(),
    })


def _own_booking(request, pk):
    return get_object_or_404(Booking.objects.select_related("tour", "guide"), pk=pk, guide=request.user)


def _detail_context(booking, **forms):
    context = {
        "booking": booking,
        "events": booking.events.all(),
        "confirm_form": ConfirmForm(prefix="confirm", initial={"price": booking.price or None}),
        "reject_form": MessageForm(prefix="reject"),
        "cancel_form": MessageForm(prefix="cancel"),
        "offer_form": OfferForm(prefix="offer", own_idea=booking.tour is None, initial={
            "start_date": booking.start_date, "end_date": booking.end_date, "price": booking.price or None,
        }),
        "invoice_lines": booking.invoice_lines.all(),
        "today": timezone.localdate(),
    }
    context.update(forms)
    return context


@login_required
def booking_detail(request, pk):
    booking = _own_booking(request, pk)
    return render(request, "dashboard/booking_detail.html", _detail_context(booking))


@login_required
@require_POST
def booking_action(request, pk, name):
    booking = _own_booking(request, pk)
    try:
        if name == "confirm":
            form = ConfirmForm(request.POST, prefix=name)
            if not form.is_valid():
                return render(request, "dashboard/booking_detail.html", _detail_context(booking, confirm_form=form))
            services.confirm(booking, form.cleaned_data["message"], price=form.cleaned_data["price"])
        elif name in ("reject", "cancel"):
            form = MessageForm(request.POST, prefix=name)
            if not form.is_valid():
                return render(request, "dashboard/booking_detail.html", _detail_context(booking, **{f"{name}_form": form}))
            action = {"reject": services.reject, "cancel": services.guide_cancel}[name]
            action(booking, form.cleaned_data["message"])
        elif name == "offer":
            form = OfferForm(request.POST, prefix="offer", own_idea=booking.tour is None)
            if not form.is_valid():
                return render(request, "dashboard/booking_detail.html", _detail_context(booking, offer_form=form))
            services.counter_offer(
                booking, start_date=form.cleaned_data["start_date"], end_date=form.cleaned_data.get("end_date"),
                price=form.cleaned_data["price"], message=form.cleaned_data["message"],
            )
        elif name == "paid":
            services.mark_paid_manually(booking)
        else:
            return redirect("dashboard:booking", pk=pk)
        messages.success(request, "Gespeichert. Der Gast wurde per E-Mail informiert.")
    except services.BookingError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:booking", pk=pk)


@login_required
def invoice(request, pk):
    booking = _own_booking(request, pk)
    if booking.status not in (S.CONFIRMED, S.AWAITING_PAYMENT):
        messages.error(request, "Diese Buchung kann nicht abgerechnet werden.")
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
            messages.success(request, "Rechnung gesendet. Der Gast hat einen Link zur Zahlung erhalten.")
            return redirect("dashboard:booking", pk=pk)
    return render(request, "dashboard/invoice.html", {"booking": booking, "form": form, "expense_forms": expense_forms})


# --- Tours -----------------------------------------------------------------


@login_required
def tours(request):
    return render(request, "dashboard/tours.html", {"tours": request.user.tours.all()})


@login_required
def tour_edit(request, pk=None):
    tour = get_object_or_404(Tour, pk=pk, guide=request.user) if pk else Tour(guide=request.user)
    form = TourForm(request.POST or None, instance=tour)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tour gespeichert.")
        return redirect("dashboard:tours")
    return render(request, "dashboard/tour_form.html", {"form": form, "tour": tour})


@login_required
@require_POST
def tour_delete(request, pk):
    tour = get_object_or_404(Tour, pk=pk, guide=request.user)
    try:
        tour.delete()
        messages.success(request, "Tour gelöscht.")
    except ProtectedError:
        tour.is_active = False
        tour.save(update_fields=["is_active"])
        messages.info(request, "Die Tour hat Buchungen und wurde deshalb nur deaktiviert.")
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
                messages.success(request, "Zeitraum gesperrt.")
                return redirect("dashboard:availability")
        elif which == "open":
            open_form = OpenPeriodForm(request.POST, prefix="open")
            if open_form.is_valid():
                period = open_form.save(commit=False)
                period.guide = guide
                period.save()
                messages.success(request, "Zeitraum freigegeben.")
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
    messages.success(request, "Sperre entfernt.")
    return redirect("dashboard:availability")


@login_required
@require_POST
def open_delete(request, pk):
    get_object_or_404(OpenPeriod, pk=pk, guide=request.user).delete()
    messages.success(request, "Freigabe entfernt.")
    return redirect("dashboard:availability")


def _sync(request):
    if not allow(f"icalsync:{request.user.pk}", limit=1, window_seconds=60):
        messages.warning(request, "Bitte warte eine Minute vor dem nächsten Abgleich.")
        return
    try:
        count = calsync.sync_guide(request.user)
    except calsync.CalendarSyncError as exc:
        messages.error(request, f"Kalender-Abgleich fehlgeschlagen: {exc}")
    else:
        if request.user.ical_url:
            messages.success(request, f"Kalender abgeglichen: {count} belegte Zeiträume übernommen.")
        else:
            messages.success(request, "Kalender-Verbindung entfernt.")


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
    states = availability.day_states(guide, weeks[0][0], weeks[-1][-1])
    bookings = guide.bookings.filter(
        status__in=Booking.ACTIVE_STATUSES, is_window=False, start_date__lte=weeks[-1][-1], end_date__gte=weeks[0][0]
    ).select_related("tour")
    by_day = {}
    for b in bookings:
        for d in availability.days_between(b.start_date, b.end_date):
            by_day.setdefault(d, b)
    opened = set()
    for p in guide.open_periods.filter(start_date__lte=weeks[-1][-1], end_date__gte=weeks[0][0]):
        opened.update(availability.days_between(p.start_date, p.end_date))
    return render(request, "dashboard/calendar.html", {
        "month_label": monthgrid.month_label(month),
        "prev_month": f"{monthgrid.shift_month(month, -1):%Y-%m}",
        "next_month": f"{monthgrid.shift_month(month, 1):%Y-%m}",
        "weekday_names": monthgrid.WEEKDAY_NAMES,
        "has_open_periods": guide.open_periods.filter(end_date__gte=timezone.localdate()).exists(),
        "weeks": [
            [{"date": d, "in_month": d.month == month.month, "state": states[d], "booking": by_day.get(d),
              "open": d in opened} for d in week]
            for week in weeks
        ],
    })


# --- Settings --------------------------------------------------------------


@login_required
def settings_view(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Einstellungen gespeichert.")
        return redirect("dashboard:settings")
    request.user.refresh_from_db()  # keep the embed code consistent with the saved slug
    return render(request, "dashboard/settings.html", {
        "form": form,
        "site_url": settings.SITE_URL,
        "stripe_available": bool(settings.STRIPE_SECRET_KEY),
    })
