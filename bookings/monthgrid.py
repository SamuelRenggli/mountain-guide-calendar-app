import calendar
from datetime import date

MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember"]
WEEKDAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def parse_month(value: str | None, default: date) -> date:
    """'2026-11' -> date(2026, 11, 1); falls back to the default's month."""
    try:
        year, month = (int(part) for part in (value or "").split("-"))
        return date(year, month, 1)
    except (TypeError, ValueError):
        return default.replace(day=1)


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def shift_month(month: date, delta: int) -> date:
    index = month.year * 12 + month.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def weeks(month: date) -> list[list[date]]:
    """Full weeks (Monday first) covering the month."""
    return calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)


def month_label(month: date) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"
