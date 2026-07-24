"""Adapter for cinemas on Veezi (veezi.com) hosted ticketing.

Veezi runs the ticketing for many independent US cinemas (Redford Theatre in
Detroit, the Riverview and the Heights in Minneapolis, and others). The
venue's own site embeds a Veezi "sessions" page, which — unlike many modern
ticketers — is **server-rendered HTML** at a stable public URL:

  https://ticketing.us{region}.veezi.com/sessions/?siteToken={siteToken}

(probed 2026-07-24: region is `east` for the Midwest venues; the siteToken is
the public embed token on the venue's schedule/tickets page, not a secret.)

The page carries two tab panels with the same data; we parse the
`#sessionsByFilmConent` ("Sort by film") one, which lists each film once:

  .film
    h3.title                         the film title (poster img alt matches)
    .censor                          MPAA-ish rating (ignored)
    .date-container                  one per date the film plays
      h4.date                        "Saturday 8, August"  (NO year)
      ul.session-times li a[href]    each showtime:
        <time>10:30 AM</time>        the local start time
        href .../purchase/{id}?...   the per-session ticket link

Dates carry no year, so we roll forward from today: the schedule ascends from
the current date, and when the month number drops we bump the year (same trick
as the Trylon adapter). Veezi exposes no film year or reliable format string
here, so `film_year`/`format` are left None and TMDb enrichment matches on the
title (as it does for the other title-only venues). No sold-out flag is shown
on the sessions page, so `sold_out` is always False.

The registry entry needs the full sessions URL as `listings_url` and
`adapter: veezi`. Multi-token or multi-building Veezi orgs aren't a thing we've
hit yet; if one appears, add a `venue_match` like the Eventive adapter.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetch import PoliteSession

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}
# "Saturday 8, August"  ->  day-of-month 8, month August
DATE_RE = re.compile(r"\b(\d{1,2})\b\s*,\s*([A-Za-z]+)")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    tz = ZoneInfo(venue["tz"])
    now = datetime.now(tz)

    soup = BeautifulSoup(session.get(base).text, "html.parser")
    # prefer the "Sort by film" panel (one .film per film); fall back to the
    # whole page if Veezi ever renames the panel id
    panel = soup.select_one("#sessionsByFilmConent") or soup
    films = panel.select(".film")
    if not films:
        raise ValueError("no .film blocks on the Veezi sessions page")

    records: list[dict] = []
    for film in films:
        title_el = film.select_one("h3.title, .title")
        if title_el is None:
            poster = film.select_one("img.poster")
            title = poster.get("alt", "").strip() if poster else ""
        else:
            title = title_el.get_text(" ", strip=True)
        if not title:
            continue

        for dc in film.select(".date-container"):
            date_el = dc.select_one("h4.date, .date")
            if date_el is None:
                continue
            dm = DATE_RE.search(date_el.get_text(" ", strip=True))
            if dm is None:
                continue
            dom = int(dm.group(1))
            month = MONTHS.get(dm.group(2).strip().lower())
            if not month:
                continue
            # no year on the page: roll forward from today (schedule ascends)
            year = now.year + 1 if month < now.month else now.year

            for a in dc.select("ul.session-times li a[href], .session-times a[href]"):
                tm = TIME_RE.search(a.get_text(" ", strip=True))
                if tm is None:
                    continue
                hour = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "PM" else 0)
                start = datetime(year, month, dom, hour, int(tm.group(2)), tzinfo=tz)
                records.append({
                    "venue_id": venue["id"],
                    "film_title": title,
                    "film_year": None,     # Veezi doesn't expose it; TMDb by title
                    "start": start,
                    "screen": None,
                    "format": None,        # no reliable format string on this page
                    "series": None,
                    "ticket_url": urljoin(base, a.get("href", "")) or base,
                    "sold_out": False,     # sessions page shows no sold-out state
                    "source_scraped_at": scraped_at,
                })
    return records
