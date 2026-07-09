"""Adapter for Marquee Arts' Ann Arbor houses — the Michigan Theater and the
State Theatre (marquee-arts.org).

Both venues share one Agile Ticketing backend and one website. The public
schedule at /calendar/ is a FullCalendar SPA whose events are injected by
JavaScript from the Agile widget (which itself sits behind Incapsula), so we
render the page in headless Chromium — with our honest bot User-Agent — and
read the populated DOM, the same approach the Music Box adapter uses for its
Sucuri challenge. Probed 2026-07-09:

  .fc-daygrid-event                 one per showtime
    closest [data-date]             the screening date (YYYY-MM-DD)
    .venue                          building name — "Michigan Theater[...]" or
                                    "State Theatre"; this splits the two venues
    .title                          film / event title
    .btn-wrapper a.btn              the time ("7:00pm", sometimes suffixed
                                    "| Free Event!"); href is the ticket URL
    .see-details a                  /event-page/?showingId=&eventId=

The calendar mixes films with concerts and live events (the Michigan hosts
symphony, UMS, Sonic Lunch, etc.). To keep only films we cross-reference the
server-rendered /all-films archive, which lists films only and carries each
one's production year and building — so it doubles as both a film filter and
the year source (the calendar shows neither). Registry entries set
`venue_match` (a substring of the .venue text) to claim their building's
screenings, exactly like the Eventive/Blackbaud multi-building adapters.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from fetch import USER_AGENT
from normalize import localize, previous_facts

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.I)
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
EVENT_ID_RE = re.compile(r"eventId=(\d+)")
DESC_YEAR_RE = re.compile(r"\|\s*((?:19|20)\d{2})\b")


def _film_index(session, base: str) -> dict[str, dict]:
    """eventId -> {title, year, theater} from the film-only /all-films archive.

    Server-rendered, so a plain (polite) fetch is enough. Doubles as the
    film filter for the mixed calendar and the year source.
    """
    html = session.get(urljoin(base, "/all-films")).text
    soup = BeautifulSoup(html, "html.parser")
    films: dict[str, dict] = {}
    for h3 in soup.select(".event-archive-wrapper h3"):
        a = h3.find_parent("a") or h3.find_next("a")
        m = EVENT_ID_RE.search(a.get("href", "") if a else "")
        if not m:
            continue
        desc = h3.find_next(class_="event-archive-desc")
        dt = desc.get_text(" ", strip=True) if desc else ""
        yr = DESC_YEAR_RE.search(dt)
        films[m.group(1)] = {
            "title": h3.get_text(" ", strip=True),
            "year": int(yr.group(1)) if yr else None,
        }
    return films


def _render_calendar(base: str) -> list[dict]:
    """Headless-render /calendar/ and pull every showtime out of the DOM."""
    from playwright.sync_api import sync_playwright  # lazy: optional dep

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(urljoin(base, "/calendar/"), wait_until="networkidle", timeout=60000)
        page.wait_for_selector(".fc-daygrid-event", timeout=30000)
        time.sleep(2)  # let FullCalendar finish painting all events
        events = page.evaluate(
            """() => [...document.querySelectorAll('.fc-daygrid-event')].map((e) => {
                const cell = e.closest('[data-date]');
                const btn = e.querySelector('.btn-wrapper a.btn');
                const det = e.querySelector('.see-details a') || btn;
                return {
                    date: cell ? cell.getAttribute('data-date') : null,
                    venue: (e.querySelector('.venue') || {}).textContent || '',
                    title: (e.querySelector('.title') || {}).textContent || '',
                    time: btn ? btn.textContent : '',
                    href: det ? det.getAttribute('href') : '',
                };
            })"""
        )
        browser.close()
    return events


def scrape(venue: dict, session, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    tz = venue["tz"]
    match = (venue.get("venue_match") or "").casefold()

    films = _film_index(session, base)   # eventId -> {title, year}
    events = _render_calendar(base)

    records: list[dict] = []
    for e in events:
        if match and match not in (e["venue"] or "").casefold():
            continue  # a different Marquee Arts building
        eid_m = EVENT_ID_RE.search(e["href"] or "")
        eid = eid_m.group(1) if eid_m else None
        if eid not in films:
            continue  # not in the film archive -> a concert / live event
        dm = DATE_RE.search(e["date"] or "")
        tm = TIME_RE.search(e["time"] or "")
        if not dm or not tm:
            continue
        y, mo, d = map(int, dm.groups())
        hh = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
        start = localize(datetime(y, mo, d, hh, int(tm.group(2))), tz)
        detail_url = urljoin(base, e["href"])
        records.append({
            "venue_id": venue["id"],
            "film_title": films[eid]["title"] or e["title"].strip(),
            "film_year": films[eid]["year"],
            "start": start,
            "screen": None,
            "format": None,
            "series": None,
            "ticket_url": detail_url,
            "detail_url": detail_url,
            "sold_out": False,  # not distinguished in the calendar DOM
            "source_scraped_at": scraped_at,
        })

    # years come from /all-films; backfill any gap from the previous run
    previous = previous_facts(venue["id"], ("film_year",))
    for r in records:
        if not r["film_year"]:
            r["film_year"] = previous.get(r["ticket_url"], {}).get("film_year")
    return records
