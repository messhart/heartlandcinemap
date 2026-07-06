"""Adapter for Drupal sites using the Calendar View module.

Serves the Gene Siskel Film Center (Chicago) — siskelfilmcenter.org, whose
ticketing is Agile but whose showtimes are server-rendered Drupal (probed
2026-07-06):

  li.calendar-view-day__row          one per screening
    .views-field-title a             film title + detail href
    time[datetime]                   UTC instant of the showtime

The calendar shows the current month only (grid overflow includes the first
days of the next month); the 6-hourly cron keeps coverage rolling. Film
detail pages carry an info line — "2001, Claire Denis, France/..., 101
mins" — which we fetch per distinct film for the year (disambiguates
repertory remakes) and scan for a print format (35mm etc).
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


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    soup = BeautifulSoup(session.get(base).text, "html.parser")
    tz = ZoneInfo(venue["tz"])

    records: list[dict] = []
    detail_urls: set[str] = set()
    for row in soup.select("li.calendar-view-day__row"):
        title_a = row.select_one(".views-field-title a")
        time_el = row.select_one("time[datetime]")
        if title_a is None or time_el is None:
            continue
        url = urljoin(base, title_a.get("href", ""))
        detail_urls.add(url)
        start = datetime.fromisoformat(
            time_el["datetime"].replace("Z", "+00:00")
        ).astimezone(tz)
        records.append({
            "venue_id": venue["id"],
            "film_title": title_a.get_text(" ", strip=True),
            "film_year": None,  # filled from the detail page below
            "start": start,
            "screen": None,
            "format": None,
            "series": None,
            "ticket_url": url,
            "sold_out": False,  # not shown on the calendar
            "source_scraped_at": scraped_at,
        })

    details: dict[str, tuple] = {}
    for url in sorted(detail_urls):
        try:
            text = re.sub(r"<[^>]+>", " ", session.get(url).text)
        except Exception:
            details[url] = (None, None)  # detail info is best-effort
            continue
        info = INFO_LINE_RE.search(text)
        fmt = FORMAT_RE.search(text)
        details[url] = (
            int(info.group(1)) if info else None,
            fmt.group(1).replace(" ", "").lower() if fmt else None,
        )

    for r in records:
        year, fmt = details.get(r["ticket_url"], (None, None))
        r["film_year"] = year
        r["format"] = fmt
    return records
