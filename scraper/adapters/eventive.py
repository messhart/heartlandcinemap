"""Adapter for venues on Eventive (https://eventive.org) hosted ticketing.

Serves Cinema Center (Fort Wayne), Hi-Pointe (St. Louis), Milwaukee Film's
Oriental & Downer, and any other {tenant}.eventive.org venue.

Eventive schedule pages are React SPAs, but the data is a clean public JSON
API. The tenant page embeds everything needed (probed 2026-07-06):

  1. GET {listings_url}                -> <script data-type="tenant"
                                          src="/{tenant}.{hash}.js">
  2. GET that bundle                   -> event_bucket id + the PUBLIC
                                          api_key the site ships to every
                                          browser (not a secret)
  3. GET api.eventive.org/event_buckets/{bucket}/upcoming?date=YYYY-MM-DD
                                       -> one week: films map (name/year/
                                          runtime) + shows_by_day keyed
                                          day -> film_id -> screen -> shows

/upcoming is the exact call the venue's own schedule page makes. (The older
/events listing can serve stale seasons — Hi-Pointe's did — so we mirror the
page instead.) We fetch two week-windows (today and today+7) = 4 requests
per venue per run; multi-venue orgs sharing a tenant reuse nothing extra.

Multi-cinema orgs (Milwaukee Film) run one bucket for several buildings; the
per-show screen name embeds the building ("Downer Theatre - North Cinema").
Registry entries may set `venue_match` (case-insensitive substring of that
name) to claim only their screenings; without it, all shows are taken.

No sold-out flag is exposed by /upcoming, so sold_out is always False here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fetch import PoliteSession

TENANT_SRC_RE = re.compile(r'data-type="tenant"\s+src="(/[^"]+\.js)"')
BUCKET_RE = re.compile(r'event_bucket"\s*:\s*"([a-f0-9]{24})"')
API_KEY_RE = re.compile(r'api_key"\s*:\s*"([a-f0-9]{16,64})"')

API = "https://api.eventive.org"


def _tenant_config(session: PoliteSession, listings_url: str) -> tuple[str, str, str]:
    origin = "{0.scheme}://{0.netloc}".format(urlparse(listings_url))
    page = session.get(listings_url).text
    m = TENANT_SRC_RE.search(page)
    if m is None:
        raise ValueError(f"no tenant bundle found on {listings_url}")
    bundle = session.get(origin + m.group(1)).text
    bucket = BUCKET_RE.search(bundle)
    key = API_KEY_RE.search(bundle)
    if bucket is None or key is None:
        raise ValueError(f"bucket/api_key not found in tenant bundle for {listings_url}")
    return origin, bucket.group(1), key.group(1)


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    origin, bucket, api_key = _tenant_config(session, venue["listings_url"])
    tz = ZoneInfo(venue["tz"])
    today = datetime.now(tz).date()
    match = (venue.get("venue_match") or "").casefold()

    records: list[dict] = []
    seen: set[str] = set()  # the two week-windows can overlap
    for offset in (0, 7):
        date_str = (today + timedelta(days=offset)).isoformat()
        data = json.loads(session.get(
            f"{API}/event_buckets/{bucket}/upcoming?date={date_str}&api_key={api_key}"
        ).text)
        films = {f["id"]: f for f in data.get("films", [])}

        for by_film in (data.get("shows_by_day") or {}).values():
            for film_id, by_screen in by_film.items():
                film = films.get(film_id) or {}
                details = film.get("details") or {}
                year = str(details.get("year") or "")
                for screen, shows in by_screen.items():
                    if match and match not in screen.casefold():
                        continue
                    for show in shows:
                        if show["id"] in seen:
                            continue
                        seen.add(show["id"])
                        start = datetime.fromisoformat(
                            show["start_time"].replace("Z", "+00:00")
                        ).astimezone(tz)
                        records.append({
                            "venue_id": venue["id"],
                            "film_title": film.get("name") or "Untitled",
                            "film_year": int(year) if year.isdigit() else None,
                            "start": start,
                            "screen": screen,
                            "format": None,
                            "series": None,
                            "ticket_url": f"{origin}/schedule/{show['id']}",
                            "sold_out": False,  # not exposed by /upcoming
                            "source_scraped_at": scraped_at,
                        })
    return records
