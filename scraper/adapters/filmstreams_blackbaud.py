"""Adapter for Film Streams (Omaha) — filmstreams.org.

Film Streams runs two buildings (the Ruth Sokolof Theater and the Dundee
Theater) on one website, selling through Blackbaud-hosted ticketing. The site
server-renders a clean per-day schedule, and — best of all — every showtime
carries a machine-readable `data-time` attribute, so there's no year-rollover
guessing. Probed 2026-07-09:

  GET /films/date/YYYY-MM-DD   one page per day
    .film_widget               one film at one building
      .film_widget-title > a   title (may end "(YYYY)") + detail page
      .film_widget-categories  series/category tags
      .film_widget-meta        "{Building} – {Screen} {schedule}", e.g.
                              "Ruth Sokolof – Rachel Now playing"
      .showtime_link           one per screening: text is the time, href is
                              the Blackbaud ticket URL, data-time is the full
                              local datetime ("2026-07-09 15:30:00")

Two buildings share the site, like Milwaukee Film on Eventive: a registry
entry sets `venue_match` (a case-insensitive substring of the meta's building
name) to claim only its own screenings.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetch import PoliteSession

DAYS_AHEAD = 21  # how far forward to page the per-day schedule
DATA_TIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})")
TITLE_YEAR_RE = re.compile(r"\s*\(((?:19|20)\d{2})\)\s*$")
# the meta screen chunk is "{Screen name} {schedule blurb}"; cut the blurb,
# which always begins with a scheduling verb or a date
SCHED_RE = re.compile(
    r"\s+(Now playing|Starts|Opens|Preview|Select|Ends|Through|Final|Last|"
    r"Jul|Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun)\b.*$", re.I)


def _screen(chunk: str) -> str | None:
    """'Peggy Payne Starts Jul 10 Preview Jul 9' -> 'Peggy Payne'."""
    name = SCHED_RE.sub("", chunk).strip(" -–")
    return name or None


def _split_title_year(title: str) -> tuple[str, int | None]:
    m = TITLE_YEAR_RE.search(title)
    if m:
        return TITLE_YEAR_RE.sub("", title).strip(), int(m.group(1))
    return title, None


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"].rstrip("/")  # e.g. https://filmstreams.org/films
    origin = re.match(r"https?://[^/]+", base).group(0)
    tz = ZoneInfo(venue["tz"])
    today = datetime.now(tz).date()
    match = (venue.get("venue_match") or "").casefold()

    records: list[dict] = []
    seen: set[str] = set()  # dedupe on the ticket URL (stable per showing)
    for offset in range(DAYS_AHEAD):
        date_str = (today + timedelta(days=offset)).isoformat()
        soup = BeautifulSoup(session.get(f"{base}/date/{date_str}").text, "html.parser")

        for w in soup.select(".film_widget"):
            title_el = w.select_one(".film_widget-title")
            if title_el is None:
                continue
            link = title_el.select_one("a")
            title, year = _split_title_year((link or title_el).get_text(" ", strip=True))
            detail_url = urljoin(base + "/", link.get("href", "")) if link else None

            meta_el = w.select_one(".film_widget-meta")
            meta = re.sub(r"\s+", " ", meta_el.get_text(" ", strip=True)) if meta_el else ""
            building, _, screen = meta.partition("–")
            building = building.strip()
            if match and match not in building.casefold():
                continue  # a different Film Streams building

            cats = [c.get_text(" ", strip=True)
                    for c in w.select(".film_widget-categories a")]
            series = cats[0] if cats else None

            for st in w.select(".showtime_link, .js-showtime"):
                m = DATA_TIME_RE.search(st.get("data-time") or "")
                if m is None:
                    continue
                y, mo, d, hh, mm = map(int, m.groups())
                start = datetime(y, mo, d, hh, mm, tzinfo=tz)
                href = st.get("href") or detail_url or base
                # per-day pages overlap only if a run spans the window edges;
                # dedupe on (ticket URL, start) to be safe
                key = href + "|" + start.isoformat()
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "venue_id": venue["id"],
                    "film_title": title,
                    "film_year": year,
                    "start": start,
                    "screen": _screen(screen),
                    "format": None,
                    "series": series,
                    "ticket_url": urljoin(origin, href),
                    "detail_url": detail_url,
                    "sold_out": "sold out" in st.get_text(" ", strip=True).lower(),
                    "source_scraped_at": scraped_at,
                })
    return records
