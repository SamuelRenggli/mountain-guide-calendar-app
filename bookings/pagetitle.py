"""Read a tour offer page (e.g. a mountaineering school's website) so the guide only pastes the link.

Finds the title, the provider, a preview image and the tour dates. Dates come from structured data
(schema.org Event, as most booking systems publish it) or, failing that, from dates written in the text
("12.–14.03.2027", "12. März 2027", "Sa 12.03. – So 13.03.2027", "2027-03-12").

Uses the same SSRF-guarded fetch as the calendar sync. Only the start of the page is read.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from django.utils import timezone

from .calsync import CalendarSyncError, safe_get

MAX_BYTES = 768 * 1024
MAX_TOUR_DAYS = 30
MAX_DATES = 12


@dataclass
class PageInfo:
    title: str = ""
    provider: str = ""
    image: str = ""  # absolute https URL of the preview image, or ""
    dates: list[tuple[date, date]] = field(default_factory=list)  # upcoming tour dates, sorted
    logo: str = ""  # absolute https URL of the provider's logo or app icon, or ""


class _Parser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.icons = {}  # rel -> href (first one wins)
        self.title = ""
        self.json_ld = []
        self.text = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            content = (attrs.get("content") or "").strip()
            if key and content:
                self.meta.setdefault(key, content)
            return
        if tag == "link":
            rel = (attrs.get("rel") or "").lower()
            href = (attrs.get("href") or "").strip()
            if href and not href.lower().endswith(".svg"):
                for kind in ("apple-touch-icon", "icon"):
                    if kind in rel.split() or (kind == "apple-touch-icon" and kind in rel):
                        self.icons.setdefault(kind, href)
            return
        if tag in ("br", "p", "div", "li", "tr", "td", "h1", "h2", "h3", "h4"):
            self.text.append("\n")
        if tag in self.SKIP or tag == "title":
            kind = "ld" if tag == "script" and "ld+json" in (attrs.get("type") or "").lower() else tag
            self._stack.append(kind)
            if kind == "ld":
                self.json_ld.append("")

    def handle_endtag(self, tag):
        if self._stack and (tag == self._stack[-1] or (tag == "script" and self._stack[-1] == "ld")):
            self._stack.pop()

    def handle_data(self, data):
        if not self._stack:
            self.text.append(data)
        elif self._stack[-1] == "title" and len(self._stack) == 1:
            self.title += data
        elif self._stack[-1] == "ld":
            self.json_ld[-1] += data


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:limit]


# --- Dates -----------------------------------------------------------------

MONTH_NAMES = {
    1: "januar jan janvier janv gennaio genn gen january",
    2: "februar feb febr février fevrier févr fevr febbraio february",
    3: "märz maerz marz mär mrz mars marzo mar march",
    4: "april apr avril aprile",
    5: "mai mai maggio mag may",
    6: "juni jun juin giugno giu june",
    7: "juli jul juillet juil luglio lug july",
    8: "august aug août aout agosto ago",
    9: "september sep sept septembre settembre sett set",
    10: "oktober okt octobre oct ottobre ott october",
    11: "november nov novembre",
    12: "dezember dez décembre decembre déc dicembre dic december dec",
}
MONTHS = {name: number for number, names in MONTH_NAMES.items() for name in names.split()}
_MONTH = "|".join(sorted((re.escape(m) for m in MONTHS), key=len, reverse=True))
_WEEKDAY = r"(?:[A-Za-zÀ-ÿ]{2,10}\.?,?\s+)?"  # "Sa ", "Samstag, ", "sam. "

# One date: numeric "12.03.2027" / "12.03." / "12.3.27", named "12. März 2027" / "12 mars", or ISO.
def _date_pattern(p: str) -> str:
    return (rf"(?:(?P<{p}iso>\d{{4}}-\d{{2}}-\d{{2}})"
            rf"|(?P<{p}d>\d{{1,2}})\.\s?(?P<{p}m>\d{{1,2}})\.(?:\s?(?P<{p}y>\d{{4}}|\d{{2}}(?!\d)))?"
            rf"|(?P<{p}nd>\d{{1,2}})(?:\.|er|st|nd|rd|th)?\s*(?:de\s+|di\s+)?(?P<{p}nm>{_MONTH})\b\.?"
            rf"(?:\s+(?P<{p}ny>\d{{4}}))?)")


_ONE, _TWO = _date_pattern("a"), _date_pattern("b")
_SEP = r"\s*(?:-|–|—|bis|au|al|to|until|à)\s*"
_FIRST_DAY = r"(?P<only_day>\d{1,2})\.?"  # "12.–14.03.2027", "12. bis 14. März"
RANGE = re.compile(rf"(?<![\d.]){_WEEKDAY}(?:{_ONE}|{_FIRST_DAY}){_SEP}{_WEEKDAY}{_TWO}(?![\d])", re.IGNORECASE)
SINGLE = re.compile(rf"(?<![\d.]){_ONE}(?![\d])", re.IGNORECASE)


def _parts(match, p):
    """(day, month, year) of one side of a match; month/year may be None."""
    g = match.groupdict()
    if g.get(f"{p}iso"):
        y, m, d = (int(x) for x in g[f"{p}iso"].split("-"))
        return d, m, y
    if g.get(f"{p}d"):
        y = g.get(f"{p}y")
        return int(g[f"{p}d"]), int(g[f"{p}m"]), (int(y) + 2000 if y and len(y) == 2 else int(y) if y else None)
    if g.get(f"{p}nd"):
        y = g.get(f"{p}ny")
        return int(g[f"{p}nd"]), MONTHS[g[f"{p}nm"].lower()], int(y) if y else None
    return None


def _make(d, m, y, today):
    """A date; without a year the next such day from today."""
    try:
        if y is not None:
            return date(y, m, d)
        guess = date(today.year, m, d)
        return guess if guess >= today else date(today.year + 1, m, d)
    except ValueError:
        return None


def dates_in_text(text: str, today: date) -> list[tuple[date, date]]:
    found = []
    taken = []

    for match in RANGE.finditer(text):
        end_parts = _parts(match, "b")
        if not end_parts:
            continue
        ed, em, ey = end_parts
        end = _make(ed, em, ey, today)
        if match.group("only_day"):
            sd, sm, sy = int(match.group("only_day")), em, ey
        else:
            sd, sm, sy = _parts(match, "a")
        start = _make(sd, sm, sy if sy is not None else (end.year if end else None), today)
        if start and end and start > end and sy is None:  # "28.12. – 3.1.2027"
            start = _make(sd, sm, end.year - 1, today)
        if start and end and start <= end and (end - start).days < MAX_TOUR_DAYS:
            found.append((start, end))
            taken.append(match.span())

    for match in SINGLE.finditer(text):
        if any(s <= match.start() < e for s, e in taken):
            continue
        parts = _parts(match, "a")
        if not parts:
            continue
        d, m, y = parts
        if y is None:  # "12.03." or "12. März" alone is too vague (prices, versions, history); needs a year
            continue
        day = _make(d, m, y, today)
        if day:
            found.append((day, day))
    return found


def _iso_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _first_str(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("name") or ""
    return value if isinstance(value, str) else ""


def _structured(json_ld: list[str]) -> tuple[list[tuple[date, date]], str, str, str, str]:
    """Dates, name, image and organizer from schema.org Event data, and an Organization logo."""
    dates, name, image, organizer, logo = [], "", "", "", ""
    for block in json_ld:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in _walk(data):
            logo = logo or _first_str(node.get("logo"))
            start = _iso_date(node.get("startDate"))
            if not start:
                continue
            end = _iso_date(node.get("endDate")) or start
            if start <= end and (end - start).days < MAX_TOUR_DAYS:
                dates.append((start, end))
            name = name or _first_str(node.get("name"))
            image = image or _first_str(node.get("image"))
            organizer = organizer or _first_str(node.get("organizer"))
    return dates, name, image, organizer, logo


def _upcoming(dates, today):
    unique = sorted({d for d in dates if d[0] >= today and d[0] <= today + timedelta(days=3 * 365)})
    return unique[:MAX_DATES]


# --- Page ------------------------------------------------------------------


def parse(page: bytes, url: str = "", today: date | None = None) -> PageInfo:
    today = today or timezone.localdate()
    parser = _Parser()
    try:
        parser.feed(page.decode("utf-8", errors="replace"))
    except Exception:  # broken markup: keep what was found so far
        pass
    meta = parser.meta
    ld_dates, ld_name, ld_image, ld_org, ld_logo = _structured(parser.json_ld)

    title = meta.get("og:title") or ld_name or parser.title
    image = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image") or ld_image
    image = urljoin(url, html.unescape(image.strip())) if image else ""
    logo = ld_logo or parser.icons.get("apple-touch-icon") or parser.icons.get("icon") or ""
    logo = urljoin(url, html.unescape(logo.strip())) if logo else ""
    dates = _upcoming(ld_dates, today) or _upcoming(dates_in_text("".join(parser.text), today), today)
    return PageInfo(
        title=_clean(title, 300),
        provider=_clean(meta.get("og:site_name") or ld_org, 200),
        image=image if image.startswith("https://") else "",
        logo=logo if logo.startswith("https://") else "",
        dates=dates,
    )


def provider_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def fetch(url: str) -> PageInfo:
    """Everything the page says about the tour. Raises CalendarSyncError when the page can't be loaded."""
    info = parse(safe_get(url, MAX_BYTES, too_big_ok=True), url)
    info.provider = info.provider or provider_from_url(url)
    return info


__all__ = ["CalendarSyncError", "PageInfo", "fetch", "parse", "provider_from_url"]
