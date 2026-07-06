"""Adapter for WordPress sites using the Elevent ticketing plugin.

Serves Kan-Kan (Indianapolis) and should serve other Elevent-backed venues.

How these sites work (probed 2026-07-05 against Kan-Kan):
- The listings page server-renders links to per-event pages at /events/{slug}/.
- Each event page server-renders the full schedule via the Elevent WordPress
  plugin: <div class="date" data-date="YYYY-MM-DD"> blocks containing
  <elevent-ticket-button-widget class="available" showtime="...">
  <button>7:30 PM ...</button> elements.
- The Elevent widget API (widget.goelevent.com) only handles cart/checkout —
  there is NO public schedule endpoint, so static HTML is the right fetch
  method, not the API.

Notes:
- Event titles carry structure we split out best-effort (see parse_title):
  "AUTUMN SONATA (1978)"                      -> title + film_year
  "DAVID LYNCH DIRECTS: 'MULHOLLAND DRIVE'"   -> series + title
  "'BLADE' VHS SCREENING"                     -> title + format
  "A/V CLUB: 'EVIL DEAD II (1987)' W/GUEST"   -> series + title + year
  When in doubt we keep the raw title — the event page is always linked.
- ticket_url is the venue's own event page (checkout hands off to Elevent).
- sold_out is inferred from the widget's class: 'available' means on sale.
- Venues may list non-film events (e.g. bar trivia); we do not filter — the
  venue's upcoming-events calendar is taken as its programming.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from fetch import PoliteSession
from normalize import localize

YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)")
# parenthetical presentation tags that aren't part of the film title
FORMAT_PAREN_RE = re.compile(
    r"\((?:[^)]*(?:4K|RESTORATION|35MM|70MM|IMAX|REMASTER|ANNIVERSARY)[^)]*)\)", re.I
)
# curly-quoted span; greedy so apostrophes inside (I'LL, TIFFANY'S) stay in
QUOTED_RE = re.compile("[‘“](.+)[’”]")
BRACKET_TAG_RE = re.compile(r"\[[^\]]*\]")
FORMAT_KEYWORDS = (("35MM", "35mm"), ("70MM", "70mm"), ("VHS", "VHS"),
                   ("4K", "4K"), ("Q&A", "Q&A"))


def parse_title(raw: str) -> tuple[str, int | None, str | None, str | None]:
    """Split a raw event title into (film_title, film_year, series, format)."""
    series = None
    title_src = raw
    quoted = QUOTED_RE.search(raw)
    if quoted:
        prefix = raw[: quoted.start()].strip()
        if prefix.endswith(":"):
            series = prefix.rstrip(":").strip() or None
        title_src = quoted.group(1)

    year_match = YEAR_RE.search(title_src) or YEAR_RE.search(raw)
    year = int(year_match.group(1)) if year_match else None

    title = BRACKET_TAG_RE.sub("", FORMAT_PAREN_RE.sub("", YEAR_RE.sub("", title_src)))
    title = re.sub(r"\s+", " ", title).strip(" -–—:")
    if not title:
        title = raw

    fmt = next((label for kw, label in FORMAT_KEYWORDS if kw in raw.upper()), None)
    return title, year, series, fmt


def _event_urls(session: PoliteSession, listings_url: str) -> list[str]:
    soup = BeautifulSoup(session.get(listings_url).text, "html.parser")
    origin = "{0.scheme}://{0.netloc}".format(urlparse(listings_url))
    urls = set()
    for a in soup.select("a[href]"):
        href = urljoin(listings_url, a["href"])
        if href.startswith(origin) and re.search(r"/events/[^/]+/?$", href):
            urls.add(href.rstrip("/") + "/")
    return sorted(urls)


def _parse_event_page(html: str, url: str, venue: dict, scraped_at: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    if h1 is None:
        return []
    raw_title = h1.get_text(" ", strip=True)
    film_title, film_year, series, fmt = parse_title(raw_title)

    records = []
    for date_div in soup.select("div.date[data-date]"):
        date_str = date_div["data-date"]
        for widget in date_div.select("elevent-ticket-button-widget"):
            button = widget.find("button")
            if button is None:
                continue
            time_text = re.sub(r"\s+", " ", button.get_text(" ", strip=True))
            tm = re.search(r"\d{1,2}:\d{2}\s*[AP]M", time_text, re.I)
            if tm is None:
                continue
            naive = datetime.strptime(
                f"{date_str} {tm.group(0).upper().replace(' ', '')}", "%Y-%m-%d %I:%M%p"
            )
            records.append(
                {
                    "venue_id": venue["id"],
                    "film_title": film_title,
                    "film_year": film_year,
                    "start": localize(naive, venue["tz"]),
                    "screen": None,
                    "format": fmt,
                    "series": series,
                    "ticket_url": url,
                    "sold_out": "available" not in (widget.get("class") or []),
                    "source_scraped_at": scraped_at,
                }
            )
    return records


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    """Return raw showtime records for one venue (validated later by normalize)."""
    records: list[dict] = []
    for url in _event_urls(session, venue["listings_url"]):
        html = session.get(url).text
        records.extend(_parse_event_page(html, url, venue, scraped_at))
    return records
