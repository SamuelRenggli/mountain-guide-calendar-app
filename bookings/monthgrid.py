import calendar
from datetime import date

from django.utils.dates import MONTHS
from django.utils.translation import pgettext_lazy

# Two-letter weekday names, Monday first
WEEKDAY_NAMES = [
    pgettext_lazy("weekday", "Mo"), pgettext_lazy("weekday", "Di"), pgettext_lazy("weekday", "Mi"),
    pgettext_lazy("weekday", "Do"), pgettext_lazy("weekday", "Fr"), pgettext_lazy("weekday", "Sa"),
    pgettext_lazy("weekday", "So"),
]


def weekday_names() -> list[str]:
    return [str(name) for name in WEEKDAY_NAMES]


def parse_month(value: str | None, default: date) -> date:
    """'2026-11' -> date(2026, 11, 1); falls back to the default's month. Years far out are refused too, so that
    the neighbouring months and full weeks around the month still exist (year 1 and 9999 have none)."""
    try:
        year, month = (int(part) for part in (value or "").split("-"))
        if not 1900 <= year <= 2200:
            raise ValueError(year)
        return date(year, month, 1)
    except (TypeError, ValueError):
        return default.replace(day=1)


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def shift_month(month: date, delta: int) -> date:
    index = month.year * 12 + month.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def weeks(month: date) -> list[list[date]]:
    """Full weeks (Monday first) covering the month."""
    return calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)


def month_label(month: date) -> str:
    return f"{MONTHS[month.month]} {month.year}"  # Django's translated month names
