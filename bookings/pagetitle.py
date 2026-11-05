"""Read the title of a tour offer page (e.g. a mountaineering school's website) so the guide only pastes the link.

Uses the same SSRF-guarded fetch as the calendar sync. Only the start of the page is read.
"""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .calsync import CalendarSyncError, safe_get

MAX_BYTES = 512 * 1024


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og_title = ""
        self.title = ""
        self.site_name = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            prop = (attrs.get("property") or attrs.get("name") or "").lower()
            content = (attrs.get("content") or "").strip()
            if prop == "og:title" and content and not self.og_title:
                self.og_title = content
            elif prop == "og:site_name" and content and not self.site_name:
                self.site_name = content
        elif tag == "title" and not self.title:
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:limit]


def parse(page: bytes) -> tuple[str, str]:
    """(title, site name) from HTML. Either may be empty."""
    parser = _TitleParser()
    try:
        parser.feed(page.decode("utf-8", errors="replace"))
    except Exception:  # broken markup: keep what was found so far
        pass
    return _clean(parser.og_title or parser.title, 300), _clean(parser.site_name, 200)


def provider_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def fetch(url: str) -> tuple[str, str]:
    """(title, provider) of the page. Raises CalendarSyncError when the page can't be loaded."""
    title, site = parse(safe_get(url, MAX_BYTES, too_big_ok=True))
    return title, site or provider_from_url(url)


__all__ = ["CalendarSyncError", "fetch", "parse", "provider_from_url"]
