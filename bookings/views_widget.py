"""Public booking widget, embedded via iframe on guides' websites."""

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.middleware import widget_csp
from core.ratelimit import allow, client_ip

from . import availability, monthgrid, services
from .forms import OWN_IDEA, BookingRequestForm


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


def _bookable_days(guide, start, end) -> set:
    """Days in start..end on which the guide has time and that lie in the booking window."""
    earliest, latest = availability.booking_window(guide)
    start, end = max(start, earliest), min(end, latest)
    if start > end:
        return set()
    return {d for d, state in availability.day_states(guide, start, end).items() if state == availability.FREE}


def _selection(guide, start_value, end_value):
    """The guest's time window from the URL, or (None, None) / (start, None) if it isn't valid (yet)."""
    start, end = monthgrid.parse_date(start_value), monthgrid.parse_date(end_value)
    if not start or start not in _bookable_days(guide, start, start):
        return None, None
    if not end or end < start or (end - start).days >= services.MAX_WINDOW_DAYS or end not in _bookable_days(guide, end, end):
        return start, None
    return start, end


def _render(request, guide, *, start_value=None, end_value=None, month_value=None, tour_id=None, form=None, status=200):
    tours = list(guide.tours.filter(is_active=True))
    earliest, latest = availability.booking_window(guide)
    start, end = _selection(guide, start_value, end_value)

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
    lang = guest_language(request)
    notice = form.non_field_errors() if form is not None else None

    def next_selection(d):
        """Clicking a day starts a new window, or ends the open one if it is within reach."""
        if start and not end and start < d and (d - start).days < services.MAX_WINDOW_DAYS:
            return start, d
        return d, None

    def link(d):
        s, e = next_selection(d)
        query = f"?lang={lang}&monat={month:%Y-%m}&von={s:%Y-%m-%d}"
        query += f"&bis={e:%Y-%m-%d}" if e else ""
        return query + (f"&tour={tour_id}" if tour_id else "")

    window_end = end or start
    if start and form is None:
        form = BookingRequestForm(tours=tours, initial={
            "start_date": start, "end_date": window_end, "language": lang,
            "tour": str(tour_id) if tour_id else None,
        })

    context = {
        "guide": guide,
        "tours": tours,
        "month_label": monthgrid.month_label(month),
        "prev_month": f"{monthgrid.shift_month(month, -1):%Y-%m}" if month > first_month else None,
        "next_month": f"{monthgrid.shift_month(month, 1):%Y-%m}" if month < last_month else None,
        "weekday_names": monthgrid.WEEKDAY_NAMES,
        "weeks": [
            [{"date": d, "in_month": d.month == month.month, "free": d in bookable and d.month == month.month,
              "selected": bool(start) and start <= d <= window_end, "link": link(d) if d in bookable else None}
             for d in week]
            for week in weeks
        ],
        "start": start,
        "end": window_end,
        "window_open": bool(start) and not end,
        "max_window_days": services.MAX_WINDOW_DAYS,
        "lang": lang,
        "lang_query": f"?lang={lang}",
        "form": form if start else None,
        "notice": notice if not start else None,
    }
    return _framed(render(request, "widget/widget.html", context, status=status), guide)


def widget(request, slug):
    guide = _guide(slug)
    return _render(
        request, guide, start_value=request.GET.get("von"), end_value=request.GET.get("bis"),
        month_value=request.GET.get("monat"), tour_id=_int(request.GET.get("tour")),
    )


# Public, unauthenticated form inside a third-party iframe, where browsers often block the
# CSRF cookie. There's no session to abuse, so CSRF protection is replaced by a honeypot,
# a signed form timestamp and per-IP rate limiting.
@csrf_exempt
@require_POST
def widget_request(request, slug):
    guide = _guide(slug)
    tours = list(guide.tours.filter(is_active=True))
    form = BookingRequestForm(request.POST, tours=tours)
    rerender = {"start_value": request.POST.get("start_date"), "end_value": request.POST.get("end_date")}

    if form.is_valid():
        if form.is_spam():
            return _framed(render(request, "widget/success.html", {"guide": guide, "booking": None}), guide)
        if not allow(f"widget:{client_ip(request)}", limit=5, window_seconds=10 * 60):
            form.add_error(None, "Zu viele Anfragen. Bitte versuche es später erneut.")
            return _render(request, guide, form=form, status=429, **rerender)
        data = form.cleaned_data
        tour = None if data["tour"] == OWN_IDEA else next(t for t in tours if str(t.pk) == data["tour"])
        try:
            booking = services.create_request(
                guide=guide, tour=tour, start_date=data["start_date"], end_date=data["end_date"],
                participants=data["participants"], guest_name=data["guest_name"], guest_email=data["guest_email"],
                guest_phone=data["guest_phone"], guest_message=data["guest_message"], language=data["language"],
            )
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        else:
            return _framed(render(request, "widget/success.html", {"guide": guide, "booking": booking}), guide)
    return _render(request, guide, form=form, status=400, **rerender)
