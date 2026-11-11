"""Public pages for guests: the booking widget (iframe on guides' websites), scheduled dates and the guide's terms."""

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.middleware import widget_csp
from core.ratelimit import allow, client_ip
from payments import stripe_service

from . import availability, departures, monthgrid, partners, services
from .forms import BookingRequestForm, PartnerOfferForm, RegistrationForm, WaitlistForm
from .models import Departure, ExternalTour, Partner, Picture


def _guide(slug):
    return get_object_or_404(get_user_model(), slug=slug, is_active=True)


def _framed(response, guide):
    """Allow framing only by the guide's websites (or anywhere if none configured)."""
    origins = guide.widget_allowed_origins.split()
    response.headers["Content-Security-Policy"] = widget_csp(" ".join(origins) if origins else "*")
    response.xframe_options_exempt = True
    return response


def _bookable_days(guide, start, end) -> set:
    """Days in start..end on which the guide has time and that lie in the booking window."""
    earliest, latest = availability.booking_window(guide)
    start, end = max(start, earliest), min(end, latest)
    if start > end:
        return set()
    return {d for d, state in availability.day_states(guide, start, end).items() if state == availability.FREE}


def _selection(guide, start_value, end_value):
    """The guest's dates from the URL. A tour needs every day free (R-020); returns (start, end) or (start, None)."""
    start, end = monthgrid.parse_date(start_value), monthgrid.parse_date(end_value)
    if not start or start not in _bookable_days(guide, start, start):
        return None, None
    if not end or end < start or (end - start).days >= settings.MAX_REQUEST_DAYS:
        return start, None
    if not availability.range_bookable(guide, start, end):
        return start, None
    return start, end


def live_departures(guide):
    return [d for d in guide.departures.filter(status__in=[Departure.Status.OPEN, Departure.Status.CONFIRMED],
                                               start_date__gt=timezone.localdate()).select_related("picture")
            if d.signup_open]


@dataclass
class Offer:
    """Something a guest can book right away: a scheduled date of the guide or a tour at another provider."""

    title: str
    start: date
    end: date
    link: str
    picture: str  # image URL or ""
    external: bool = False
    provider: str = ""
    departure: Departure | None = None


def offers(guide) -> list[Offer]:
    found = [Offer(d.title, d.start_date, d.end_date, reverse("widget:departure", args=[d.public_id]),
                   d.picture.url if d.picture else "", departure=d) for d in live_departures(guide)]
    external = guide.external_tours.filter(status=ExternalTour.Status.CONFIRMED, show_to_guests=True,
                                           start_date__gt=timezone.localdate()).exclude(url="")
    found += [Offer(t.title, t.start_date, t.end_date, t.url, t.picture.url if t.picture else "", external=True,
                    provider=t.provider) for t in external.select_related("picture")]
    return sorted(found, key=lambda o: (o.start, o.end, o.title))


def _week_cells(week, month, offer_on, day_cell):
    """One calendar row. Consecutive days of the same offer become one tile spanning those days."""
    cells = []
    for d in week:
        offer = offer_on.get(d) if d.month == month.month else None
        if offer and cells and cells[-1].get("offer") is offer:
            cells[-1]["span"] += 1
            cells[-1]["last"] = d
            continue
        if offer:
            cells.append({"offer": offer, "span": 1, "first": d, "last": d, "continues": offer.start < d})
        else:
            cells.append(day_cell(d))
    for cell in cells:
        if cell.get("offer"):
            cell["goes_on"] = cell["offer"].end > cell["last"]
    return cells


def _render(request, guide, *, start_value=None, end_value=None, month_value=None, form=None, status=200):
    earliest, latest = availability.booking_window(guide)
    start, end = _selection(guide, start_value, end_value)
    lang = translation.get_language()

    first_month, last_month = timezone.localdate().replace(day=1), latest.replace(day=1)
    if month_value:
        month = monthgrid.parse_month(month_value, earliest)
    elif start:
        month = start.replace(day=1)
    else:  # open at the first month the guide has time, not at an empty one
        month = min(_bookable_days(guide, earliest, latest), default=earliest).replace(day=1)
    month = min(max(month, first_month), last_month)

    weeks = monthgrid.weeks(month)
    bookable = _bookable_days(guide, weeks[0][0], weeks[-1][-1])
    notice = form.non_field_errors() if form is not None else None

    def next_selection(d):
        """Clicking a day starts the tour; the next click on a later day sets its last day."""
        if start and not end and start < d and (d - start).days < settings.MAX_REQUEST_DAYS:
            return start, d
        return d, None

    def link(d):
        s, e = next_selection(d)
        query = f"?monat={month:%Y-%m}&von={s:%Y-%m-%d}"
        return query + (f"&bis={e:%Y-%m-%d}" if e else "")

    tour_end = end or start
    if start and form is None:
        form = BookingRequestForm(initial={"start_date": start, "end_date": tour_end, "language": lang})

    upcoming = offers(guide)
    offer_on = {}
    for offer in upcoming:
        for d in availability.days_between(offer.start, offer.end):
            if d not in bookable:
                offer_on.setdefault(d, offer)

    def day_cell(d):
        return {"date": d, "in_month": d.month == month.month, "free": d in bookable and d.month == month.month,
                "selected": bool(start) and start <= d <= tour_end, "link": link(d) if d in bookable else None}

    context = {
        "guide": guide,
        "offers": upcoming,
        "next_offer": upcoming[0] if upcoming else None,
        "has_external": any(o.external for o in upcoming),
        "rate_table": guide.rate_table(),
        "month_label": monthgrid.month_label(month),
        "prev_month": f"{monthgrid.shift_month(month, -1):%Y-%m}" if month > first_month else None,
        "next_month": f"{monthgrid.shift_month(month, 1):%Y-%m}" if month < last_month else None,
        "weekday_names": monthgrid.weekday_names(),
        "weeks": [_week_cells(week, month, offer_on, day_cell) for week in weeks],
        "start": start,
        "end": tour_end,
        "picking_end": bool(start) and not end,
        "max_request_days": settings.MAX_REQUEST_DAYS,
        "form": form if start else None,
        "notice": notice if not start else None,
    }
    return _framed(render(request, "widget/widget.html", context, status=status), guide)


def widget(request, slug):
    guide = _guide(slug)
    return _render(
        request, guide, start_value=request.GET.get("von"), end_value=request.GET.get("bis"),
        month_value=request.GET.get("monat"),
    )


def _rate_limited(request, scope) -> bool:
    return not allow(f"{scope}:{client_ip(request)}", limit=5, window_seconds=10 * 60)


# Public, unauthenticated forms inside a third-party iframe, where browsers often block the
# CSRF cookie. There's no session to abuse, so CSRF protection is replaced by a honeypot,
# a signed form timestamp and per-IP rate limiting.
@csrf_exempt
@require_POST
def widget_request(request, slug):
    guide = _guide(slug)
    form = BookingRequestForm(request.POST)
    rerender = {"start_value": request.POST.get("start_date"), "end_value": request.POST.get("end_date")}

    if form.is_valid():
        if form.is_spam():
            return _framed(render(request, "widget/success.html", {"guide": guide, "booking": None}), guide)
        if form.refuse_if_stale():
            return _render(request, guide, form=form, status=400, **rerender)
        if _rate_limited(request, "widget"):
            form.add_error(None, _("Zu viele Anfragen. Bitte versuche es später erneut."))
            return _render(request, guide, form=form, status=429, **rerender)
        data = form.cleaned_data
        try:
            booking = services.create_request(
                guide=guide, start_date=data["start_date"], end_date=data["end_date"],
                participants=data["participants"], guest_name=data["guest_name"], guest_email=data["guest_email"],
                guest_phone=data["guest_phone"], guest_message=data["guest_message"], language=data["language"],
            )
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        else:
            return _framed(render(request, "widget/success.html", {"guide": guide, "booking": booking}), guide)
    return _render(request, guide, form=form, status=400, **rerender)


# --- Scheduled dates ---------------------------------------------------------


def _departure(public_id):
    return get_object_or_404(Departure.objects.select_related("guide"), public_id=public_id, guide__is_active=True)


def _departure_page(request, departure, *, form=None, waitlist_form=None, status=200, joined=False):
    lang = translation.get_language()
    free = departure.seats_free
    if form is None and free:
        form = RegistrationForm(initial={"language": lang}, max_seats=free)
    if waitlist_form is None and not free:
        waitlist_form = WaitlistForm(initial={"language": lang})
    response = render(request, "widget/departure.html", {
        "guide": departure.guide, "departure": departure, "seats_free": free, "form": form,
        "waitlist_form": waitlist_form, "joined": joined, "cancelled_payment": request.GET.get("abgebrochen") == "1",
        "can_pay": departure.guide.can_take_card_payments,
    }, status=status)
    return _framed(response, departure.guide)


def departure_page(request, public_id):
    """Each scheduled date has its own address that leads straight to the signup (R-110)."""
    return _departure_page(request, _departure(public_id))


@csrf_exempt
@require_POST
def departure_signup(request, public_id):
    departure = _departure(public_id)
    form = RegistrationForm(request.POST, max_seats=max(departure.seats_free, 1))
    if not form.is_valid():
        return _departure_page(request, departure, form=form, status=400)
    if form.is_spam():
        return redirect("widget:departure", public_id=public_id)
    if form.refuse_if_stale():
        return _departure_page(request, departure, form=form, status=400)
    if _rate_limited(request, "signup"):
        form.add_error(None, _("Zu viele Anmeldungen. Bitte versuche es später erneut."))
        return _departure_page(request, departure, form=form, status=429)
    d = form.cleaned_data
    try:
        booking = departures.start_registration(
            departure, seats=d["seats"], guest_name=d["guest_name"], guest_email=d["guest_email"],
            guest_phone=d["guest_phone"], emergency_contact=d["emergency_contact"], experience=d["experience"],
            language=d["language"],
        )
        url = stripe_service.create_registration_checkout(booking)
    except services.BookingError as exc:
        form.add_error(None, str(exc))
        return _departure_page(request, departure, form=form, status=400)
    except stripe_service.PaymentError as exc:
        departures.payment_failed(booking)
        form.add_error(None, str(exc))
        return _departure_page(request, departure, form=form, status=400)
    return redirect(url)


@csrf_exempt
@require_POST
def departure_waitlist(request, public_id):
    departure = _departure(public_id)
    form = WaitlistForm(request.POST)
    if not form.is_valid():
        return _departure_page(request, departure, waitlist_form=form, status=400)
    if form.is_spam():
        return redirect("widget:departure", public_id=public_id)
    if form.refuse_if_stale():
        return _departure_page(request, departure, waitlist_form=form, status=400)
    if _rate_limited(request, "waitlist"):
        form.add_error(None, _("Zu viele Versuche. Bitte versuche es später erneut."))
        return _departure_page(request, departure, waitlist_form=form, status=429)
    d = form.cleaned_data
    try:
        departures.join_waitlist(departure, name=d["guest_name"], email=d["guest_email"], seats=d["seats"],
                                 language=d["language"])
    except services.BookingError as exc:
        form.add_error(None, str(exc))
        return _departure_page(request, departure, waitlist_form=form, status=400)
    return _departure_page(request, departure, joined=True)


def terms(request, slug):
    """The guide's terms for guests, or the Tourchef template with the guide's cancellation tiers (R-003, R-004)."""
    guide = _guide(slug)
    return _framed(render(request, "widget/terms.html", {"guide": guide}), guide)


# --- Tour pictures -------------------------------------------------------------


def picture(request, key):
    """Tour photos, served from the database. The random key is the address; the bytes never change."""
    pic = get_object_or_404(Picture.objects.only("data"), key=key)
    response = HttpResponse(bytes(pic.data), content_type="image/jpeg")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# --- Partner link --------------------------------------------------------------


def partner_page(request, token):
    """A provider the guide works for offers a tour in a period the guide released for them. The token is the key;
    the page shows only those periods and whether each day is still free. Not embedded, so normal CSRF applies."""
    partner = get_object_or_404(Partner.objects.select_related("guide"), token=token, guide__is_active=True)
    guide = partner.guide
    today = timezone.localdate()
    form = PartnerOfferForm(request.POST or None, initial={"start_date": request.GET.get("von"),
                                                           "end_date": request.GET.get("von")})
    sent = None
    status = 200
    if request.method == "POST":
        if form.is_valid() and form.is_spam():
            return redirect("widget:partner", token=token)
        if form.is_valid() and _rate_limited(request, "partner"):
            form.add_error(None, _("Zu viele Versuche. Bitte versuche es später erneut."))
        elif form.is_valid():
            d = form.cleaned_data
            try:
                sent = partners.create_offer(
                    partner, title=d["title"], url=d["url"], start_date=d["start_date"], end_date=d["end_date"],
                    contact_name=d["contact_name"], contact_email=d["contact_email"], note=d["note"],
                    language=translation.get_language()[:2],
                )
            except partners.PartnerOfferError as exc:
                form.add_error(None, str(exc))
            else:
                form = PartnerOfferForm()
        if not sent:
            status = 400

    periods = list(partner.periods.filter(end_date__gt=today))
    months = []
    if periods:
        first = max(min(p.start_date for p in periods), today).replace(day=1)
        last = max(p.end_date for p in periods).replace(day=1)
        month = first
        while month <= last and len(months) < 18:
            weeks = monthgrid.weeks(month)
            released = partners.released_days(partner, weeks[0][0], weeks[-1][-1])
            free = partners.free_days(partner, weeks[0][0], weeks[-1][-1]) if released else set()
            months.append({
                "label": monthgrid.month_label(month),
                "weeks": [[{"date": d, "in_month": d.month == month.month,
                            "state": ("free" if d in free and d > today else "taken") if d in released else ""}
                           for d in week] for week in weeks],
            })
            month = monthgrid.shift_month(month, 1)
    return render(request, "widget/partner.html", {
        "guide": guide, "partner": partner, "periods": periods, "months": months, "form": form, "sent": sent,
        "weekday_names": monthgrid.weekday_names(),
        "offers": partner.external_tours.filter(end_date__gte=today).exclude(status="cancelled"),
    }, status=status)
