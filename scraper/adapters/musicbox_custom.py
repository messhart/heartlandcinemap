"""Adapter for Music Box Theatre (Chicago) — musicboxtheatre.com.

The site is Drupal behind a Sucuri Cloudproxy JS challenge, so plain HTTP
gets a cookie-wall (probed 2026-07-05; see venues/il.yaml notes). A real
browser passes the challenge like any visitor, so this adapter drives
headless Chromium via Playwright — with our honest bot User-Agent — and
parses the rendered month-grid calendar:

  #dayN .programming-content        one block per film per day
    h3 a[href=/films-and-events/..] film title + detail page (our ticket_url)
    .film-format                    "DCP", "35mm", ...
    .programming-showtimes .times a "7:15pm" per screening; class "disabled"
                                    when not purchasable (past OR sold out —
                                    we flag sold_out only for future shows)

We fetch the current month and the next (/calendar/{month}/{year}?view=grid),
then visit each distinct film's detail page once — those state "Production
Year YYYY" and "Part of: <series>", which the calendar grid doesn't. The
year is what lets enrichment disambiguate repertory remakes (their Ocean's
Eleven is 2001, not 1960). ~35 rate-limited page loads per run.
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
PRODUCTION_YEAR_RE = re.compile(r"Production Year\s+((?:19|20)\d{2})")
PART_OF_RE = re.compile(r"Part of:\s*([^\n]+)")
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def _month_urls(base: str, now: datetime) -> list[str]:
    urls = [urljoin(base, "/calendar")]
    ny, nm = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
    urls.append(urljoin(base, f"/calendar/{MONTHS[nm - 1]}/{ny}?view=grid"))
    return urls


def _parse_calendar(html: str, base: str, venue: dict, scraped_at: str,
                    now: datetime) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    # page title carries the grid's month: "July 2026 | Music Box Theatre"
    m = re.match(r"(\w+)\s+(\d{4})", soup.title.get_text(strip=True))
    if not m or m.group(1).lower() not in MONTHS:
        return []
    month, year = MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))

    records = []
    for cell in soup.select("div.calendar-cell[id^=day]"):
        try:
            day = int(cell["id"][3:])
        except ValueError:
            continue
        for prog in cell.select(".programming-content"):
            title_a = prog.select_one("h3 a")
            if title_a is None:
                continue
            film_title = title_a.get_text(" ", strip=True)
            detail_url = urljoin(base, title_a.get("href", ""))
            fmt_el = prog.select_one(".film-format")
            fmt = fmt_el.get_text(" ", strip=True) or None if fmt_el else None

            for t in prog.select(".programming-showtimes .times a"):
                tm = TIME_RE.search(t.get_text(" ", strip=True))
                if tm is None:
                    continue
                h, mi = int(tm.group(1)) % 12, int(tm.group(2))
                if tm.group(3).lower() == "pm":
                    h += 12
                start = localize(datetime(year, month, day, h, mi), venue["tz"])
                disabled = "disabled" in (t.get("class") or [])
                records.append({
                    "venue_id": venue["id"],
                    "film_title": film_title,
                    "film_year": None,  # not shown on the calendar
                    "start": start,
                    "screen": None,
                    "format": fmt,
                    "series": None,
                    "ticket_url": detail_url,
                    "sold_out": disabled and start > now,
                    "source_scraped_at": scraped_at,
                })
    return records


def scrape(venue: dict, session, scraped_at: str) -> list[dict]:
    # lazy import: environments without Playwright still run other adapters
    from playwright.sync_api import sync_playwright

    base = venue["listings_url"]
    now = datetime.now(ZoneInfo(venue["tz"]))
    records: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        for url in _month_urls(base, now):
            page.goto(url, wait_until="networkidle", timeout=60000)
            records.extend(_parse_calendar(page.content(), base, venue, scraped_at, now))
            time.sleep(1)  # politeness gap between month loads

        # per-film detail pages: year (disambiguates repertory remakes) + series
        details: dict[str, tuple] = {}
        for url in sorted({r["ticket_url"] for r in records}):
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                text = page.inner_text("body")
            except Exception:
                details[url] = (None, None)  # detail page is best-effort only
                continue
            y = PRODUCTION_YEAR_RE.search(text)
            s = PART_OF_RE.search(text)
            details[url] = (
                int(y.group(1)) if y else None,
                s.group(1).strip() if s else None,
            )
            time.sleep(1)
        browser.close()

    # detail fetches flake on CI (Sucuri is rough on datacenter IPs) — film
    # facts don't change, so backfill gaps from the previous run's records
    previous = previous_facts(venue["id"])
    for r in records:
        year, series = details.get(r["ticket_url"], (None, None))
        prev = previous.get(r["ticket_url"], {})
        r["film_year"] = r["film_year"] or year or prev.get("film_year")
        r["series"] = r["series"] or series or prev.get("series")
    return records
