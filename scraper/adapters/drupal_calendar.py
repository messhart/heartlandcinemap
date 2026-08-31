"""Adapter for Drupal sites using the Calendar View module.

Serves the Gene Siskel Film Center (Chicago) — siskelfilmcenter.org, whose
ticketing is Agile but whose showtimes are server-rendered Drupal (probed
2026-07-06, re-probed 2026-08-30):

  li.calendar-view-day__row          one per screening
    .views-field-title a             film title + detail href
    time[datetime]                   UTC instant of the showtime

The calendar is a SHORT forward window — about a week, not the full month it
looked like on first probe — so it alone misses the back half of a film's
run (Mysterious Object at Noon showed Sep 5 on the calendar but not its Sep 7
screening). Each film's detail page lists that film's COMPLETE run in
.views-field-field-showtime, and we already fetch every detail page for the
year, so we harvest showtimes there too and union them with the calendar.
Past screenings listed on a detail page are dropped (the calendar is
forward-only; we keep it that way).

Films are still discovered through the calendar, so a film whose whole run
starts after the calendar window stays invisible until the window reaches it.

Detail pages carry an info line — "2001, Claire Denis, France/..., 101
mins" — scanned for the year (disambiguates repertory remakes) and for a
print format (35mm etc).
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetch import PoliteSession

# "2001, Claire Denis, France/Germany, 101 mins"
INFO_LINE_RE = re.compile(
    r"\b((?:19|20)\d{2}),\s*[^,<>]{2,60},\s*[^,<>]{2,60},\s*\d{1,3}\s*min", re.I
)
FORMAT_RE = re.compile(r"\b(35\s?mm|70\s?mm|16\s?mm)\b", re.I)
# the full run, on a film's own detail page
DETAIL_SHOWTIME_SEL = ".views-field-field-showtime time[datetime]"


def _local(stamp: str, tz: ZoneInfo) -> datetime:
    """'2026-09-07T23:00:00Z' -> venue-local aware datetime."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(tz)


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    soup = BeautifulSoup(session.get(base).text, "html.parser")
    tz = ZoneInfo(venue["tz"])
    today = _local(scraped_at, tz).replace(hour=0, minute=0, second=0, microsecond=0)

    titles: dict[str, str] = {}          # detail url -> film title
    starts: dict[str, set[datetime]] = {}  # detail url -> showtimes
    for row in soup.select("li.calendar-view-day__row"):
        title_a = row.select_one(".views-field-title a")
        time_el = row.select_one("time[datetime]")
        if title_a is None or time_el is None:
            continue
        url = urljoin(base, title_a.get("href", ""))
        titles.setdefault(url, title_a.get_text(" ", strip=True))
        starts.setdefault(url, set()).add(_local(time_el["datetime"], tz))

    records: list[dict] = []
    for url in sorted(titles):
        year = fmt = None
        try:
            html = session.get(url).text
        except Exception:
            pass  # detail pages are best-effort; the calendar rows still stand
        else:
            detail = BeautifulSoup(html, "html.parser")
            for time_el in detail.select(DETAIL_SHOWTIME_SEL):
                start = _local(time_el["datetime"], tz)
                if start >= today:
                    starts[url].add(start)
            text = re.sub(r"<[^>]+>", " ", html)
            info = INFO_LINE_RE.search(text)
            fmt_match = FORMAT_RE.search(text)
            year = int(info.group(1)) if info else None
            fmt = fmt_match.group(1).replace(" ", "").lower() if fmt_match else None

        for start in sorted(starts[url]):
            records.append({
                "venue_id": venue["id"],
                "film_title": titles[url],
                "film_year": year,
                "start": start,
                "screen": None,
                "format": fmt,
                "series": None,
                "ticket_url": url,
                "sold_out": False,  # not shown on the calendar
                "source_scraped_at": scraped_at,
            })
    return records
