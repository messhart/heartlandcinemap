"""Adapter for Filmbot's newer hosted product (React SPA + GraphQL).

Serves The Neon (Dayton). Unlike the WordPress-plugin generation (see
filmbot.py), these sites run a consumer app whose /graphql endpoint answers
plain POSTs as long as the tenant-identifier headers the SPA itself sends
are present (probed 2026-07-06): circuit-id, site-id, client-type. Those
ids aren't secrets — every visitor's browser sends them — and live in the
venue registry as graphql_circuit_id / graphql_site_id.

Two lean queries (the SPA's own operations, minus the fields we don't need):
  datesWithShowing            -> every date with showings (~2 months out)
  showingsForDate(date: ...)  -> per-date showings: UTC time, seats left,
                                 movie name / releaseDate / urlSlug

~25 rate-limited calls per run. sold_out is seatsRemaining == 0.
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from fetch import PoliteSession

DATES_QUERY = (
    "query ($siteIds: [ID]) { datesWithShowing(siteIds: $siteIds) { value } }"
)
SHOWINGS_QUERY = (
    "query ($date: String, $siteIds: [ID]) {"
    " showingsForDate(date: $date, siteIds: $siteIds) {"
    " data { time seatsRemaining movie { name releaseDate urlSlug } } } }"
)


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    base = venue["listings_url"]
    endpoint = urljoin(base, "/graphql")
    site_id = int(venue["graphql_site_id"])
    headers = {
        "circuit-id": str(venue["graphql_circuit_id"]),
        "site-id": str(site_id),
        "client-type": "consumer",
    }
    tz = ZoneInfo(venue["tz"])

    def gql(query: str, variables: dict) -> dict:
        resp = session.post(endpoint, json={"query": query, "variables": variables},
                            headers=headers).json()
        if resp.get("errors"):
            raise RuntimeError(f"graphql error: {resp['errors'][0].get('message')}")
        return resp["data"]

    dates = json.loads(
        gql(DATES_QUERY, {"siteIds": [site_id]})["datesWithShowing"]["value"]
    )

    records: list[dict] = []
    for date_str in dates:
        shows = gql(SHOWINGS_QUERY, {"date": date_str, "siteIds": [site_id]})
        for s in shows["showingsForDate"]["data"]:
            movie = s.get("movie") or {}
            release = movie.get("releaseDate") or ""
            start = datetime.fromisoformat(
                s["time"].replace("Z", "+00:00")
            ).astimezone(tz)
            records.append({
                "venue_id": venue["id"],
                "film_title": movie.get("name") or "Untitled",
                "film_year": int(release[:4]) if release[:4].isdigit() else None,
                "start": start,
                "screen": None,
                "format": None,
                "series": None,
                "ticket_url": urljoin(base, f"/movies/{movie.get('urlSlug', '')}"),
                "sold_out": s.get("seatsRemaining") == 0,
                "source_scraped_at": scraped_at,
            })
    return records
