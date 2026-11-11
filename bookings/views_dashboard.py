"""Guide dashboard. Every query is scoped to request.user — guides only ever see their own data."""

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.forms import CalendarSyncForm, ProfileForm, RateTierFormSet
from accounts.models import AccessLog
from accounts.views import recent_auth_required
from core.ratelimit import allow
from payments import stripe_service

from . import (
    availability, calsync, departures, export, messaging, monthgrid, pagetitle, partners, pictures, services,
)
from .forms import (
    AcceptForm, BlockedPeriodForm, ChangeBookingForm, DepartureForm, EraseGuestDataForm, ExpenseFormSet, ExportForm,
    ExternalTourForm, GuestsMessageForm, GuideCancelForm, GuideMessageForm, InvoiceForm, MessageForm, MoveDepartureForm,
    OpenPeriodForm, PartnerForm, PartnerPeriodForm, PictureCategoryForm, ProposeDateForm, RefundExtraForm, TourForm,
    library_groups,
)
from .models import (
    BlockedPeriod, Booking, Departure, Draft, ExternalTour, GuestProfile, Message, Notification, OpenPeriod, Partner, PartnerPeriod,
    Picture, Tour, TourCategory,
)

S = Booking.Status
D = Departure.Status
SAVED = lambda: _("Gespeichert. Der Gast wurde per E-Mail informiert.")  # noqa: E731


def upcoming_tours(guide, today):
    """Everything coming up, in date order: confirmed requests, own scheduled dates and tours for other providers."""
    entries = []
    for b in guide.bookings.filter(departure__isnull=True, status=S.CONFIRMED, end_date__gt=today).select_related("tour"):
        entries.append({"kind": _("Auf Anfrage"), "start": b.start_date, "end": b.end_date, "title": b.title,
                        "who": f"{b.guest_name} · {b.participants} P.", "url": booking_url(b),
                        "status": b.get_status_display(), "badge": f"badge-{b.status}"})
    for d in guide.departures.filter(status__in=[D.OPEN, D.CONFIRMED], end_date__gte=today):
        entries.append({"kind": _("Ausgeschrieben"), "start": d.start_date, "end": d.end_date, "title": d.title,
                        "who": _("%(n)s von %(max)s angemeldet (min. %(min)s)") % {
                            "n": d.seats_confirmed(), "max": d.max_participants, "min": d.min_participants},
                        "url": reverse("dashboard:departure", args=[d.pk]),
                        "status": d.get_status_display(), "badge": f"badge-d-{d.status}"})
    for t in guide.external_tours.filter(status=ExternalTour.Status.CONFIRMED, end_date__gte=today):
        entries.append({"kind": _("Externer Anbieter"), "start": t.start_date, "end": t.end_date, "title": t.title,
                        "who": t.provider, "url": t.url or reverse("dashboard:external_tours"), "external": bool(t.url),
                        "status": t.get_status_display(), "badge": "badge-confirmed"})
    return sorted(entries, key=lambda e: (e["start"], e["end"]))


@login_required
def home(request):
    requests = request.user.bookings.filter(departure__isnull=True).select_related("tour")
    today = timezone.localdate()
    upcoming_departures = request.user.departures.filter(status__in=[D.OPEN, D.CONFIRMED], end_date__gte=today)
    return render(request, "dashboard/home.html", {
        "needs_action": requests.filter(status=S.REQUESTED).order_by("deadline"),
        "upcoming": upcoming_tours(request.user, today),
        "to_invoice": requests.filter(status=S.CONFIRMED, end_date__lte=today),
        "waiting_for_guest": requests.filter(status__in=[S.OFFERED, S.RESCHEDULE_PROPOSED, S.AWAITING_PAYMENT]),
        "history": requests.exclude(status__in=[S.REQUESTED, S.OFFERED, S.CONFIRMED, S.RESCHEDULE_PROPOSED,
                                                S.AWAITING_PAYMENT]).order_by("-updated_at")[:50],
        "is_new": not request.user.bookings.exists() and not upcoming_departures.exists(),
        "partner_offers": request.user.external_tours.filter(status=ExternalTour.Status.PENDING),
        "past_departures": request.user.departures.filter(Q(end_date__lt=today) | Q(status=D.CANCELLED))
                                                  .order_by("-start_date")[:50],
    })


# --- Bookings ----------------------------------------------------------------


def _own_booking(request, pk):
    return get_object_or_404(Booking.objects.select_related("tour", "guide", "departure"), pk=pk, guide=request.user)


def _detail_context(booking, **forms):
    context = {
        "booking": booking,
        "events": booking.events.all(),
        "accept_form": AcceptForm(prefix="accept", initial=AcceptForm.initial_for(booking)),
        "reject_form": MessageForm(prefix="reject"),
        "cancel_form": GuideCancelForm(prefix="cancel"),
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
        "thread": booking.messages.all(),
        "message_form": GuideMessageForm(prefix="msg"),
        "whatsapp": whatsapp_number(booking.guest_phone),
        "profile": GuestProfile.for_booking(booking),
    }
    context.update(forms)
    return context


@login_required
def booking_detail(request, pk):
    booking = _own_booking(request, pk)
    AccessLog.record(request, AccessLog.Action.VIEW_BOOKING, f"booking:{booking.pk}")
    messaging.mark_read(request.user, booking)
    return render(request, "dashboard/booking_detail.html", _detail_context(booking))


def whatsapp_number(phone: str) -> str:
    """+41 791234567 -> 41791234567 for a wa.me link; "" when there is no full international number."""
    digits = "".join(c for c in phone or "" if c.isdigit())
    return digits if (phone or "").strip().startswith("+") and len(digits) >= 8 else ""


@login_required
@require_POST
def booking_message(request, pk):
    """The guide writes to the guest; the guest gets an email and replies to it."""
    booking = _own_booking(request, pk)
    form = GuideMessageForm(request.POST, prefix="msg")
    if not form.is_valid():
        return _rerender(request, booking, message_form=form)
    messaging.send_to_guest(booking, form.cleaned_data["body"])
    messages.success(request, _("Nachricht gesendet. Die Antwort des Gastes erscheint hier."))
    return redirect(f"{booking_url(booking)}#nachrichten")


def booking_url(booking):
    return reverse("dashboard:booking", args=[booking.pk])


@login_required
@require_POST
def booking_message_action(request, pk, message_pk, name):
    """A reply from an unknown address: keep it (it was the guest after all) or delete it."""
    booking = _own_booking(request, pk)
    message = get_object_or_404(Message, pk=message_pk, booking=booking, needs_review=True)
    if name == "freigeben":
        messaging.approve(message)
        messages.success(request, _("Nachricht freigegeben."))
    elif name == "loeschen":
        message.delete()
        messages.success(request, _("Nachricht gelöscht."))
    return redirect(f"{booking_url(booking)}#nachrichten")


# --- Notifications -------------------------------------------------------------


@login_required
def notification_open(request, pk):
    note = get_object_or_404(Notification, pk=pk, guide=request.user)
    if note.read_at is None:
        note.read_at = timezone.now()
        note.save(update_fields=["read_at"])
    if note.booking_id:
        anchor = "#nachrichten" if note.kind == Notification.Kind.MESSAGE else ""
        return redirect(booking_url(note.booking) + anchor)
    return redirect("dashboard:home")


@login_required
@require_POST
def notifications_read(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect(request.POST.get("next") if (request.POST.get("next") or "").startswith("/dashboard/")
                    else "dashboard:home")


def _rerender(request, booking, **forms):
    return render(request, "dashboard/booking_detail.html", _detail_context(booking, **forms), status=400)


@login_required
@require_POST
def booking_action(request, pk, name):
    booking = _own_booking(request, pk)
    post = request.POST
    try:
        if name == "accept":
            form = AcceptForm(post, prefix=name)
            if not form.is_valid():
                return _rerender(request, booking, accept_form=form)
            d = form.cleaned_data
            try:
                services.accept_request(booking, start_date=d["start_date"], end_date=d["end_date"],
                                        price=form.final_price(), message=d["message"],
                                        estimated_expenses=d["estimated_expenses"], decision_date=d["decision_date"],
                                        answer_deadline=d["answer_deadline"])
            except services.DatesUnavailable as exc:  # keep what the guide typed
                form.add_error("end_date", str(exc))
                return _rerender(request, booking, accept_form=form)
            messages.success(request, _("Gesendet. Sobald der Gast zusagt, ist die Tour bestätigt."))
            return redirect("dashboard:booking", pk=pk)
        elif name in ("reject", "cancel"):
            form = (GuideCancelForm if name == "cancel" else MessageForm)(post, prefix=name)
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
        elif name == "ask_details":
            if not booking.planning_open:
                raise services.InvalidTransition()
            messaging.ask_for_details(booking)
            messages.success(request, _("Der Gast wurde per E-Mail gebeten, seine Angaben einzutragen."))
            return redirect(f"{booking_url(booking)}#angaben")
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
            billed = services.invoice(booking, lines, form.cleaned_data["message"])
        except services.BookingError as exc:
            messages.error(request, str(exc))
        else:
            if billed.status == S.PAID:
                messages.success(request, _("Abrechnung über CHF 0 gespeichert. Die Tour ist erledigt."))
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
        "can_pay": request.user.can_take_card_payments,
    })


def _departure_fields(form, guide) -> dict:
    fields = {k: v for k, v in form.detail_data().items() if k != "save_as_template"}
    fields["picture"] = form.picture(guide)
    return fields


@login_required
def departure_new(request):
    tour = None
    if template_id := request.GET.get("vorlage") or request.POST.get("vorlage"):
        if not template_id.isdigit():
            raise Http404
        tour = get_object_or_404(Tour, pk=template_id, guide=request.user)
    start = monthgrid.parse_date(request.GET.get("datum"))
    draft = _own_draft(request, Draft.Kind.DEPARTURE)
    initial = DepartureForm.initial_from(tour, start)
    if draft and request.method == "GET":
        initial.update(_draft_initial(draft))
    form = DepartureForm(request.POST or None, request.FILES or None, initial=initial, from_template=tour is not None,
                         picture=tour.picture if tour else None, guide=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            departure = departures.create_departure(request.user, tour=tour, **_departure_fields(form, request.user),
                                                    save_as_template=form.cleaned_data.get("save_as_template", False))
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        else:
            if draft:
                draft.delete()
            messages.success(request, _("Termin veröffentlicht. Teile jetzt den Link."))
            return redirect("dashboard:departure", pk=departure.pk)
    return render(request, "dashboard/departure_form.html", {
        "form": form, "tour": tour, "departure": None, "can_pay": request.user.can_take_card_payments,
        "draft": draft, "draft_kind": Draft.Kind.DEPARTURE,
    })


@login_required
def departure_edit(request, pk):
    departure = _own_departure(request, pk)
    form = DepartureForm(request.POST or None, request.FILES or None, instance=departure, editing=True,
                         guide=request.user)
    if request.method == "POST":
        if form.is_valid():
            try:
                departures.update_departure(departure, **_departure_fields(form, request.user))
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
    registrations = departure.registrations.exclude(status=S.EXPIRED).order_by("created_at").annotate(
        unread=Count("messages", filter=Q(messages__direction=Message.Direction.FROM_GUEST,
                                          messages__read_at__isnull=True)),
        message_count=Count("messages"),
    )
    return render(request, "dashboard/departure_detail.html", {
        "message_all_form": GuideMessageForm(prefix="all"),
        "seated": registrations.filter(status__in=Booking.SEAT_STATUSES).count(),
        "departure": departure,
        "registrations": registrations,
        "waitlist": departure.waitlist.all(),
        "seats_taken": departure.seats_confirmed(),
        "seats_free": departure.seats_free,
        "move_form": MoveDepartureForm(prefix="move", initial={"start_date": departure.start_date,
                                                               "end_date": departure.end_date}),
        "cancel_form": GuestsMessageForm(prefix="cancel"),
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
            form = GuestsMessageForm(request.POST, prefix="cancel")
            if not form.is_valid():
                messages.error(request, _("Nicht abgesagt: Bitte bestätige die Absage mit dem Häkchen."))
            else:
                departures.cancel_departure(departure, form.cleaned_data.get("message", ""))
                messages.success(request, _("Termin abgesagt. Alle erhalten den vollen Betrag zurück."))
        elif name == "nachricht-an-alle":
            form = GuideMessageForm(request.POST, prefix="all")
            if not form.is_valid():
                messages.error(request, _("Bitte schreibe eine Nachricht."))
            else:
                count = messaging.send_to_all(departure, form.cleaned_data["body"])
                messages.success(request, _("Nachricht an %(count)s Angemeldete gesendet. Jede Antwort erscheint bei "
                                            "der jeweiligen Person.") % {"count": count})
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
    return render(request, "dashboard/tours.html", {
        "tours": request.user.tours.select_related("picture"),
        "drafts": request.user.drafts.all(),
    })


@login_required
def tour_edit(request, pk=None):
    tour = get_object_or_404(Tour, pk=pk, guide=request.user) if pk else Tour(guide=request.user)
    draft = None if pk else _own_draft(request, Draft.Kind.TOUR)
    form = TourForm(request.POST or None, request.FILES or None, instance=tour, guide=request.user,
                    initial=_draft_initial(draft) if draft and request.method == "GET" else None)
    if request.method == "POST" and form.is_valid():
        tour = form.save(commit=False)
        tour.picture = form.picture(request.user)
        tour.save()
        if draft:
            draft.delete()
        messages.success(request, _("Vorlage gespeichert."))
        return redirect("dashboard:tours")
    return render(request, "dashboard/tour_form.html", {
        "form": form, "tour": tour, "draft": draft, "draft_kind": None if tour.pk else Draft.Kind.TOUR,
    })


# --- Drafts --------------------------------------------------------------------

DRAFT_MAX_FIELDS = 60
DRAFT_MAX_VALUE = 6000
DRAFT_SKIP = {"csrfmiddlewaretoken", "entwurf", "photo", "remove_photo"}


def _own_draft(request, kind):
    pk = request.GET.get("entwurf") or request.POST.get("entwurf")
    if not pk or not str(pk).isdigit():
        return None
    return request.user.drafts.filter(pk=pk, kind=kind).first()


def _draft_initial(draft) -> dict:
    return {k: v for k, v in draft.data.items() if k != "vorlage"}


@login_required
@require_POST
def draft_save(request, kind):
    """Called by static/js/autosave.js while the guide types. Returns the draft's id for the next save."""
    if kind not in Draft.Kind.values:
        return JsonResponse({"error": "kind"}, status=400)
    if not allow(f"draft:{request.user.pk}", limit=240, window_seconds=10 * 60):
        return JsonResponse({"error": "rate"}, status=429)
    data = {k: v[:DRAFT_MAX_VALUE] for k, v in list(request.POST.items())[:DRAFT_MAX_FIELDS]
            if k not in DRAFT_SKIP and isinstance(v, str)}
    draft = _own_draft(request, kind) or Draft(guide=request.user, kind=kind)
    draft.data = data
    draft.save()
    return JsonResponse({"id": draft.pk, "saved": timezone.localtime(draft.updated_at).strftime("%H:%M")})


@login_required
def draft_open(request, pk):
    draft = get_object_or_404(Draft, pk=pk, guide=request.user)
    from django.urls import reverse

    if draft.kind == Draft.Kind.TOUR:
        return redirect(f"{reverse('dashboard:tour_new')}?entwurf={draft.pk}")
    template = draft.data.get("vorlage", "")
    extra = f"&vorlage={template}" if template.isdigit() else ""
    return redirect(f"{reverse('dashboard:departure_new')}?entwurf={draft.pk}{extra}")


@login_required
@require_POST
def draft_delete(request, pk):
    draft = get_object_or_404(Draft, pk=pk, guide=request.user)
    draft.delete()
    messages.success(request, _("Entwurf gelöscht."))
    return redirect("dashboard:tours")


# --- Guest file ------------------------------------------------------------------


@login_required
def guests(request):
    """Everyone who has booked with the guide (confirmed tours and paid registrations; mere requests don't count),
    with their planning details. One row per email address."""
    guide = request.user
    booked = (guide.bookings.filter(status__in=[S.CONFIRMED, S.AWAITING_PAYMENT, S.PAID])
              .select_related("tour", "departure").order_by("-start_date"))
    query = (request.GET.get("q") or "").strip().lower()
    profiles = {p.email.lower(): p for p in guide.guest_profiles.all()}
    rows = {}
    for b in booked:
        key = b.guest_email.lower()
        row = rows.setdefault(key, {"name": b.guest_name, "email": b.guest_email, "phone": b.guest_phone,
                                    "bookings": [], "profile": profiles.get(key)})
        row["bookings"].append(b)
    found = [r for r in rows.values() if not query or query in r["name"].lower() or query in r["email"].lower()]
    AccessLog.record(request, AccessLog.Action.VIEW_BOOKING, "guests")
    return render(request, "dashboard/guests.html", {
        "rows": sorted(found, key=lambda r: r["name"].lower()), "query": query, "today": timezone.localdate(),
    })


# --- Picture library -----------------------------------------------------------


@login_required
def picture_library(request):
    """The guide's own photos and Tourchef's stock pictures, sorted by kind of tour. Uploading only stores the files;
    kind of tour and description are set afterwards on each picture. The page's script sends one file per request
    (keeps every request small); without script the form sends them all at once."""
    guide = request.user
    if request.method == "POST":
        files = request.FILES.getlist("photos")
        wants_json = request.headers.get("Accept", "").startswith("application/json")
        errors = []
        if not files:
            errors.append(_("Bitte wähle mindestens ein Bild."))
        elif len(files) > 20:
            errors.append(_("Höchstens 20 Bilder auf einmal."))
        added = 0
        for upload in files if not errors else []:
            try:
                if upload.size > pictures.MAX_UPLOAD_BYTES:
                    raise pictures.PictureError(_("Das Bild ist zu gross (höchstens 8 MB)."))
                pictures.store(guide, pictures.shrink(upload.read()), in_library=True)
                added += 1
            except pictures.PictureError as exc:
                errors.append(f"{upload.name[:60]}: {exc}")
        if wants_json:
            return JsonResponse({"added": added, "errors": errors}, status=200 if added else 400)
        if added:
            messages.success(request, _("%(count)s Bild(er) hochgeladen. Ordne sie unten einer Tourenart zu.")
                             % {"count": added})
        for error in errors:
            messages.error(request, error)
        return redirect("dashboard:pictures")
    library = list(Picture.library_for(guide))
    kinds = dict(TourCategory.choices)
    return render(request, "dashboard/pictures.html", {
        "unsorted": [p for p in library if not p.category and not p.is_stock],
        "groups": library_groups([p for p in library if p.category or p.is_stock]),
        "missing_kinds": [kinds[c] for c in kinds if not any(p.category == c and not p.is_stock for p in library)],
        "category_choices": [("", "–")] + list(TourCategory.choices),
        "accept": pictures.ACCEPT,
    })


@login_required
@require_POST
def picture_action(request, key, name):
    pic = get_object_or_404(Picture, key=key, guide=request.user)
    if name == "loeschen":
        pic.delete()  # tours using it keep their text, just without a picture
        messages.success(request, _("Bild gelöscht."))
    elif name in ("tourenart", "speichern"):
        form = PictureCategoryForm(request.POST)
        if form.is_valid():
            pic.category = form.cleaned_data["category"]
            pic.title = form.cleaned_data["title"]
            pic.in_library = True
            pic.save(update_fields=["category", "title", "in_library"])
            messages.success(request, _("Gespeichert."))
    return redirect(reverse("dashboard:pictures") + f"#bild-{pic.key}" if pic.pk else "dashboard:pictures")


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
    partner_form = PartnerForm(prefix="partner")
    partner_period_form = PartnerPeriodForm(prefix="pp", guide=guide)

    if request.method == "POST":
        which = request.POST.get("form")
        if which == "partner":
            partner_form = PartnerForm(request.POST, prefix="partner")
            if partner_form.is_valid():
                partner = partner_form.save(commit=False)
                partner.guide = guide
                partner.save()
                messages.success(request, _("Anbieter angelegt. Gib jetzt einen Zeitraum frei und schick den Link."))
                return redirect("dashboard:availability")
        elif which == "partner_period":
            partner_period_form = PartnerPeriodForm(request.POST, prefix="pp", guide=guide)
            if partner_period_form.is_valid():
                partner_period_form.save()
                messages.success(request, _("Zeitraum für %(name)s freigegeben.")
                                 % {"name": partner_period_form.cleaned_data["partner"].name})
                return redirect("dashboard:availability")
        elif which == "block":
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
        "partner_form": partner_form,
        "partner_period_form": partner_period_form,
        "partners": [
            (p, [pp for pp in p.periods.all() if pp.end_date >= timezone.localdate()])
            for p in guide.partners.prefetch_related("periods")
        ],
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


@login_required
@require_POST
def partner_delete(request, pk):
    get_object_or_404(Partner, pk=pk, guide=request.user).delete()  # the link stops working; tours stay
    messages.success(request, _("Anbieter entfernt. Der Link funktioniert nicht mehr."))
    return redirect("dashboard:availability")


@login_required
@require_POST
def partner_period_delete(request, pk):
    get_object_or_404(PartnerPeriod, pk=pk, partner__guide=request.user).delete()
    messages.success(request, _("Freigabe entfernt."))
    return redirect("dashboard:availability")


# --- Tours for other providers ---------------------------------------------


def _read_tour_page(form, data):
    """Fill in what the guide left empty from the linked page: title, provider and dates. Returns the tour dates
    found on the page (the guide picks one when there are several). The provider's photos stay theirs: the
    picture is the guide's own (uploaded, picked, or one of this kind of tour), else the provider's logo."""
    url = form.cleaned_data.get("url")
    try:
        info = pagetitle.fetch(url)
    except calsync.CalendarSyncError:
        info = pagetitle.PageInfo(provider=pagetitle.provider_from_url(url))
    for name, value in (("title", info.title), ("provider", info.provider)):
        if not form.cleaned_data.get(name) and value:
            data[f"ext-{name}"] = value
    if not form.cleaned_data.get("start_date") and len(info.dates) == 1:
        start, end = info.dates[0]
        data["ext-start_date"], data["ext-end_date"] = start.isoformat(), end.isoformat()
    if not data.get("ext-picture_key"):
        if form.cleaned_data.get("photo"):
            picture = None  # the upload is stored when saving
        else:
            picture = form.own_picture() or (pictures.from_url(form.guide, info.logo, logo=True) if info.logo else None)
        data["ext-picture_key"] = picture.key if picture else ExternalTourForm.NO_PICTURE
    return info.dates


@login_required
def external_tours(request):
    """The guide pastes the link of a tour they lead for another provider; title, dates and picture are read from
    the page. When something can't be found, the form comes back filled in as far as possible."""
    guide = request.user
    form = ExternalTourForm(request.POST or None, request.FILES or None, prefix="ext", guide=guide)
    found_dates = []
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if d["url"] and not (d["title"] and d["start_date"] and form.picture_key_given):
            data = request.POST.copy()
            found_dates = _read_tour_page(form, data)
            form = ExternalTourForm(data, request.FILES or None, prefix="ext", guide=guide)
            form.is_valid()
        if form.is_valid() and form.complete(found_dates):
            tour = form.save(commit=False)
            tour.guide = guide
            tour.status = ExternalTour.Status.CONFIRMED
            tour.picture = form.picture(guide)
            tour.save()
            states = availability.day_states(guide, tour.start_date, tour.end_date)
            if any(states[d] not in (availability.FREE, availability.UNAVAILABLE, availability.EXTERNAL)
                   for d in states):
                messages.warning(request, _("Eingetragen. Achtung: An mindestens einem dieser Tage hast du schon "
                                            "etwas anderes."))
            else:
                messages.success(request, _("Tour eingetragen. Die Tage sind für Gäste gesperrt."))
            return redirect("dashboard:external_tours")
    today = timezone.localdate()
    active = guide.external_tours.filter(status__in=ExternalTour.ACTIVE_STATUSES).select_related("partner", "picture")
    return render(request, "dashboard/external_tours.html", {
        "form": form,
        "found_dates": found_dates if len(found_dates) > 1 else [],
        "pending": active.filter(status=ExternalTour.Status.PENDING),
        "upcoming": active.filter(status=ExternalTour.Status.CONFIRMED, end_date__gte=today),
        "past": active.filter(status=ExternalTour.Status.CONFIRMED, end_date__lt=today).order_by("-start_date")[:20],
    })


@login_required
@require_POST
def external_tour_action(request, pk, name):
    tour = get_object_or_404(ExternalTour, pk=pk, guide=request.user)
    if name in ("annehmen", "ablehnen"):
        try:
            partners.answer_offer(tour, accept=name == "annehmen")
        except partners.PartnerOfferError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Bestätigt. Die Tage sind im Kalender eingetragen.") if name == "annehmen"
                             else _("Abgelehnt. Die Tage sind wieder frei."))
    elif name in ("zeigen", "verbergen"):
        tour.show_to_guests = name == "zeigen"
        tour.save(update_fields=["show_to_guests"])
        messages.success(request, _("Die Tour erscheint jetzt im Buchungskalender.") if tour.show_to_guests
                         else _("Die Tour ist für Gäste nicht mehr sichtbar."))
    elif name == "loeschen":
        if tour.is_pending:
            partners.answer_offer(tour, accept=False)
        else:
            tour.status = ExternalTour.Status.CANCELLED
            tour.save(update_fields=["status"])
        messages.success(request, _("Tour entfernt. Die Tage sind wieder frei."))
    return redirect("dashboard:external_tours")


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
        status__in=Booking.ACTIVE_STATUSES, is_window=False, departure__isnull=True, days_released=False,
        start_date__lte=last, end_date__gte=first,
    ).select_related("tour")
    by_day, departures_by_day, external_by_day = {}, {}, {}
    for b in bookings:
        for d in availability.days_between(b.start_date, b.end_date):
            by_day.setdefault(d, b)
    for dep in guide.departures.filter(status__in=[D.OPEN, D.CONFIRMED], start_date__lte=last, end_date__gte=first):
        for d in availability.days_between(dep.start_date, dep.end_date):
            departures_by_day.setdefault(d, []).append(dep)
    externals = guide.external_tours.filter(status__in=ExternalTour.ACTIVE_STATUSES, start_date__lte=last,
                                            end_date__gte=first)
    for t in externals:
        for d in availability.days_between(t.start_date, t.end_date):
            external_by_day.setdefault(d, []).append(t)
    opened, for_partner = set(), set()
    for p in guide.open_periods.filter(start_date__lte=last, end_date__gte=first):
        opened.update(availability.days_between(p.start_date, p.end_date))
    for p in PartnerPeriod.objects.filter(partner__guide=guide, start_date__lte=last, end_date__gte=first):
        for_partner.update(availability.days_between(p.start_date, p.end_date))
    return render(request, "dashboard/calendar.html", {
        "month_label": monthgrid.month_label(month),
        "prev_month": f"{monthgrid.shift_month(month, -1):%Y-%m}",
        "next_month": f"{monthgrid.shift_month(month, 1):%Y-%m}",
        "weekday_names": monthgrid.weekday_names(),
        "has_open_periods": guide.open_periods.filter(end_date__gte=timezone.localdate()).exists(),
        "today": timezone.localdate(),
        "weeks": [
            [{"date": d, "in_month": d.month == month.month, "state": states[d], "booking": by_day.get(d),
              "departures": departures_by_day.get(d, []), "externals": external_by_day.get(d, []),
              "open": d in opened, "for_partner": d in for_partner} for d in week]
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
