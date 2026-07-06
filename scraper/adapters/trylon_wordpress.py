"""Adapter for the Trylon Cinema (Minneapolis) — trylon.org.

Custom WordPress with an on-site cart; the homepage server-renders an
"upcoming by date" widget (probed 2026-07-06):

  .upcoming-by-date-list-nav button   one per day, "Sat<br>Aug 1" (no year)
  .upcoming-by-date-list > div        day blocks, 1:1 with the buttons
    .film-data                        per film: a.film-link (detail page),
                                      h3 title (may carry a "*Sold Out*"
                                      span and an "in 4K/35mm" suffix),
                                      .showtime spans ("7:00 pm"; class
                                      "inactive" when not purchasable)

Dates lack a year, so we roll forward from today: the widget starts at the
current date and ascends; when the month number drops, the year bumps.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetch import PoliteSession

DATE_RE = re.compile(r"([A-Za-z]{3})\s+(\d{1,2})$")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
TITLE_FORMAT_RE = re.compile(r"\s+in\s+(4K|35\s?mm|70\s?mm|16\s?mm)\s*$", re.I)
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.I)


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    soup = BeautifulSoup(session.get(base).text, "html.parser")
    tz = ZoneInfo(venue["tz"])
    now = datetime.now(tz)

    buttons = soup.select(".upcoming-by-date-list-nav button")
    day_lists = soup.select(".upcoming-by-date-list > div")
    if not buttons or len(buttons) != len(day_lists):
        raise ValueError(
            f"date buttons ({len(buttons)}) and day lists ({len(day_lists)}) mismatch"
        )

    records: list[dict] = []
    year, prev_month = now.year, now.month
    for button, day in zip(buttons, day_lists):
        m = DATE_RE.search(button.get_text(" ", strip=True))
        if m is None or m.group(1)[:3].title() not in MONTHS:
            continue
        month, dom = MONTHS[m.group(1)[:3].title()], int(m.group(2))
        if month < prev_month:
            year += 1  # widget rolled into the next calendar year
        prev_month = month

        for film in day.select(".film-data"):
            link = film.select_one("a.film-link")
            h3 = film.select_one("h3")
            if h3 is None:
                continue
            sold_out_marker = h3.select_one(".sold-out")
            if sold_out_marker:
                sold_out_marker.extract()
            title = h3.get_text(" ", strip=True)
            fmt_match = TITLE_FORMAT_RE.search(title)
            fmt = fmt_match.group(1).replace(" ", "").lower().replace("k", "K") if fmt_match else None
            title = TITLE_FORMAT_RE.sub("", title).strip()

            for span in film.select(".showtime"):
                tm = TIME_RE.search(span.get_text(" ", strip=True))
                if tm is None:
                    continue
                h = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
                start = datetime(year, month, dom, h, int(tm.group(2)), tzinfo=tz)
                records.append({
                    "venue_id": venue["id"],
                    "film_title": title,
                    "film_year": None,
                    "start": start,
                    "screen": None,
                    "format": fmt,
                    "series": None,
                    "ticket_url": urljoin(base, link.get("href", "")) if link else base,
                    "sold_out": bool(sold_out_marker)
                    or "inactive" in (span.get("class") or []),
                    "source_scraped_at": scraped_at,
                })
    return records
