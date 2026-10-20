"""Public booking widget, embedded via iframe on guides' websites."""

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.middleware import widget_csp
from core.ratelimit import allow, client_ip

from . import availability, monthgrid, services
from .forms import BookingRequestForm


def _guide(slug):
    return get_object_or_404(get_user_model(), slug=slug, is_active=True)


def _int(value):
    return int(value) if value and str(value).isdigit() else None


def guest_language(request) -> str:
    """Explicit ?lang=, else the first German/English entry of the browser's Accept-Language."""
    requested = request.GET.get("lang") or request.POST.get("language")
    if requested in ("de", "en"):
        return requested
    for part in request.headers.get("Accept-Language", "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in ("de", "en"):
            return code
    return "de"


def _framed(response, guide):
    """Allow framing only by the guide's websites (or anywhere if none configured)."""
    origins = guide.widget_allowed_origins.split()
    response.headers["Content-Security-Policy"] = widget_csp(" ".join(origins) if origins else "*")
    response.xframe_options_exempt = True
    return response


def _render(request, guide, *, tour_id=None, month_value=None, date_value=None, form=None, status=200):
    tours = list(guide.tours.filter(is_active=True))
    tour = next((t for t in tours if t.pk == tour_id), tours[0] if tours else None)
    earliest, latest = availability.booking_window(guide)
    selected = monthgrid.parse_date(date_value)

    month = monthgrid.parse_month(month_value, selected or earliest)
    first_month, last_month = timezone.localdate().replace(day=1), latest.replace(day=1)
    month = min(max(month, first_month), last_month)

    weeks = monthgrid.weeks(month)
    startable = availability.startable_days(tour, weeks[0][0], weeks[-1][-1]) if tour else set()
    notice = form.non_field_errors() if form is not None else None
    if selected not in startable:
        selected = None
        form = None
    lang = guest_language(request)
    if selected and form is None:
        form = BookingRequestForm(tour=tour, initial={"start_date": selected, "language": lang})

    context = {
        "guide": guide,
        "tours": tours,
        "tour": tour,
        "month": month,
        "month_label": monthgrid.month_label(month),
        "month_value": f"{month:%Y-%m}",
        "prev_month": f"{monthgrid.shift_month(month, -1):%Y-%m}" if month > first_month else None,
        "next_month": f"{monthgrid.shift_month(month, 1):%Y-%m}" if month < last_month else None,
        "weekday_names": monthgrid.WEEKDAY_NAMES,
        "weeks": [
            [{"date": d, "in_month": d.month == month.month, "free": d in startable and d.month == month.month,
              "selected": d == selected} for d in week]
            for week in weeks
        ],
        "selected": selected,
        "selected_end": tour.end_date_for(selected) if tour and selected else None,
        "form": form,
        "lang": lang,
        "notice": notice if form is None else None,  # form errors are shown in the form itself
    }
    return _framed(render(request, "widget/widget.html", context, status=status), guide)


def widget(request, slug):
    guide = _guide(slug)
    return _render(
        request, guide, tour_id=_int(request.GET.get("tour")),
        month_value=request.GET.get("monat"), date_value=request.GET.get("datum"),
    )


# Public, unauthenticated form inside a third-party iframe, where browsers often block the
# CSRF cookie. There's no session to abuse, so CSRF protection is replaced by a honeypot,
# a signed form timestamp and per-IP rate limiting.
@csrf_exempt
@require_POST
def widget_request(request, slug):
    guide = _guide(slug)
    tour = get_object_or_404(guide.tours, pk=_int(request.POST.get("tour")), is_active=True)
    form = BookingRequestForm(request.POST, tour=tour)
    rerender = {"tour_id": tour.pk, "date_value": request.POST.get("start_date")}

    if form.is_valid():
        if form.is_spam():
            return _framed(render(request, "widget/success.html", {"guide": guide, "booking": None}), guide)
        if not allow(f"widget:{client_ip(request)}", limit=5, window_seconds=10 * 60):
            form.add_error(None, "Zu viele Anfragen. Bitte versuche es später erneut.")
            return _render(request, guide, form=form, status=429, **rerender)
        data = form.cleaned_data
        try:
            booking = services.create_request(
                tour=tour, start_date=data["start_date"], participants=data["participants"],
                guest_name=data["guest_name"], guest_email=data["guest_email"],
                guest_phone=data["guest_phone"], guest_message=data["guest_message"], language=data["language"],
            )
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        else:
            return _framed(render(request, "widget/success.html", {"guide": guide, "booking": booking}), guide)
    return _render(request, guide, form=form, status=400, **rerender)
