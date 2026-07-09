"""Adapter for FilmScene (Iowa City) — icfilmscene.org.

FilmScene sells through Agile Ticketing, but its own WordPress site pre-renders
the full schedule into clean, day-grouped HTML (a `film-api-cron` plugin polls
Agile and caches the results), so we read that rather than the Agile backend
(which sits behind Incapsula bot protection). Probed 2026-07-09:

  .today-all-shows            one block per day; <header><date>Thursday, Jul 9
  ol > li                     one film per list item
    h3.film-title > a         title + detail page (/film/{slug}/)
    .film__series__label      series name, when the film belongs to one
    .film-loc-times           per FilmScene location (Ped Mall / Chauncey /
                              FilmScene in the Park):
      venue                   the location name
      a.bttn-showtime         one per screening; text is the time ("7:00pm"),
                              data-buylink is the real Agile ticket URL

Dates carry no year, so we roll forward from today (month number dropping =
next calendar year). Film years aren't in the listing; they come from each
film's detail page (a "(YYYY)" in the copy), backfilled from the previous run
when a fetch flakes — the same pattern the Trylon adapter uses.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetch import PoliteSession
from normalize import previous_facts

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
DATE_RE = re.compile(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.I)
# a production year the venue appends to a title, e.g. "Vertigo (1958)"
TITLE_YEAR_RE = re.compile(r"\s*\(((?:19|20)\d{2})\)\s*$")


def _split_title_year(title: str) -> tuple[str, int | None]:
    """Pull a trailing "(YYYY)" off a title; it's the most reliable year."""
    m = TITLE_YEAR_RE.search(title)
    if m:
        return TITLE_YEAR_RE.sub("", title).strip(), int(m.group(1))
    return title, None


def _film_year(session: PoliteSession, url: str) -> int | None:
    """Production year from a /film/{slug}/ detail page, only when explicit.

    We deliberately DON'T grab the first stray 4-digit number on the page
    (copyright lines, run dates, etc. produce wrong years, which is worse for
    TMDb matching than no year). We take a year only from a labelled spec
    ("Year: 1971") or a country/year/runtime run ("USA, 1971, 99 min").
    """
    try:
        text = BeautifulSoup(session.get(url).text, "html.parser").get_text(" ", strip=True)
    except Exception:
        return None
    m = re.search(r"(?:Year|Released|Release Date)[:\s]+((?:19|20)\d{2})", text)
    if m:
        return int(m.group(1))
    # "..., 1971, 99 min" / "1971 · 99 min" style spec lines
    m = re.search(r"\b((?:19|20)\d{2})\b\s*[,·|]\s*\d+\s*min", text)
    return int(m.group(1)) if m else None


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    soup = BeautifulSoup(session.get(base).text, "html.parser")
    tz = ZoneInfo(venue["tz"])
    now = datetime.now(tz)

    records: list[dict] = []
    year, prev_month = now.year, now.month
    for day in soup.select(".today-all-shows"):
        header = day.select_one("header date") or day.select_one("date")
        if header is None:
            continue
        m = DATE_RE.search(header.get_text(" ", strip=True))
        if m is None or m.group(1)[:3].title() not in MONTHS:
            continue
        month, dom = MONTHS[m.group(1)[:3].title()], int(m.group(2))
        if month < prev_month:
            year += 1  # the schedule rolled into the next calendar year
        prev_month = month

        for li in day.select("ol > li"):
            h3 = li.select_one("h3.film-title, h3.all_show-film-title, h3")
            if h3 is None:
                continue
            link = h3.select_one("a")
            title, title_year = _split_title_year((link or h3).get_text(" ", strip=True))
            detail_url = urljoin(base, link.get("href", "")) if link else None
            series_el = li.select_one(".film__series__label")
            series = series_el.get_text(" ", strip=True) if series_el else None

            for loc in li.select(".film-loc-times"):
                venue_el = loc.select_one("venue")
                screen = venue_el.get_text(" ", strip=True) if venue_el else None
                for btn in loc.select("a.bttn-showtime, a.show-time"):
                    tm = TIME_RE.search(btn.get_text(" ", strip=True))
                    if tm is None:
                        continue
                    h = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
                    start = datetime(year, month, dom, h, int(tm.group(2)), tzinfo=tz)
                    buy = btn.get("data-buylink") or detail_url or base
                    records.append({
                        "venue_id": venue["id"],
                        "film_title": title,
                        "film_year": title_year,  # detail page fills the rest
                        "start": start,
                        "screen": screen,
                        "format": None,
                        "series": series or None,
                        "ticket_url": urljoin(base, buy.strip()),
                        "detail_url": detail_url,
                        "sold_out": False,  # no sold-out marker in the listing
                        "source_scraped_at": scraped_at,
                    })

    # years: prefer a "(YYYY)" already parsed from the title; otherwise the
    # film detail page; otherwise the previous run (fetches flake on CI IPs).
    # Only fetch detail pages for films that still need a year.
    need = {r["detail_url"] for r in records if not r["film_year"] and r["detail_url"]}
    years = {url: _film_year(session, url) for url in sorted(need)}
    previous = previous_facts(venue["id"], ("film_year",))
    for r in records:
        if not r["film_year"]:
            r["film_year"] = years.get(r["detail_url"]) or \
                previous.get(r["ticket_url"], {}).get("film_year")
    return records
