"""Adapter for WordPress venues on Filmbot ticketing (filmbot.com).

Serves Gateway Film Center (Columbus) and the Varsity (Des Moines). The
Filmbot WordPress plugin exposes a clean public REST route (probed
2026-07-06):

  GET {origin}/wp-json/nj/v1/showtime/listings
  -> { theater_id, theater_name,
       movies:    [{movie_id, movie_name, runtime, release_year, ...}],
       showtimes: [{movie_id, datetime: "YYYYMMDDHHMMSS" (venue-local),
                    qualifier, purchase_url}] }

One request per venue covers every upcoming showtime (~a month out).

purchase_url goes straight to checkout, so we also resolve each movie's
intro page on the venue site (detail_url): slugified title matched against
the WP movie sitemap; when the sitemap is stale (Gateway's second page
404s, hiding recent films) we probe /movies/{slug}/ directly — once per
new film, since resolved URLs are reused from the previous run.

NOTE: newer Filmbot-hosted sites (The Neon, Dayton) are a React app talking
GraphQL on the venue domain instead — that generation needs its own adapter;
see the venue notes.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fetch import PoliteSession
from normalize import localize, previous_facts

YEAR_PAREN_RE = re.compile(r"\s*\(((?:19|20)\d{2})\)")
TRAILING_FORMAT_RE = re.compile(
    r"\s*(?:in\s+|on\s+)?(?:4K(?:\s+Restoration)?|Restoration|35\s?mm|70\s?mm|16\s?mm|IMAX)\s*$",
    re.I,
)
FORMAT_KEYWORDS = (("35MM", "35mm"), ("70MM", "70mm"), ("16MM", "16mm"),
                   ("IMAX", "IMAX"), ("4K", "4K"))


def _clean_movie(movie: dict) -> tuple[str, int | None, str | None]:
    """'Cult 101: Jaws (1975) 4K Restoration' -> (title, 1975, '4K')."""
    raw = html.unescape(movie.get("movie_name") or "")  # WP feeds "&amp;" etc.
    year = None
    m = YEAR_PAREN_RE.search(raw)
    if m:
        year = int(m.group(1))
    ry = str(movie.get("release_year") or "")
    if ry.isdigit():
        year = int(ry)
    title = YEAR_PAREN_RE.sub("", raw)
    title = TRAILING_FORMAT_RE.sub("", title).strip(" -–—:") or raw
    fmt = next((label for kw, label in FORMAT_KEYWORDS if kw in raw.upper()), None)
    return title, year, fmt


def _slugify(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def _movie_pages(session: PoliteSession, origin: str) -> dict[str, str]:
    """slug -> movie page URL, from the WP movie sitemap (best-effort)."""
    pages: dict[str, str] = {}
    try:
        index = session.get(origin + "/wp-sitemap.xml").text
        for sm in re.findall(r"<loc>([^<]*wp-sitemap-posts-movie-\d+\.xml)</loc>", index):
            try:
                body = session.get(sm).text
            except Exception:
                continue
            for loc in re.findall(r"<loc>([^<]+/movies/([^/<]+)/?)</loc>", body):
                pages[loc[1]] = loc[0]
    except Exception:
        pass
    return pages


def _resolve_details(session, origin: str, venue: dict, movies: dict) -> dict:
    """movie_id -> intro page URL: sitemap, previous run, then a direct probe."""
    pages = _movie_pages(session, origin)
    prev = {}  # (title lowercased, year) -> detail_url from the last run
    try:
        import normalize
        old = json.loads(normalize.PREVIOUS_SHOWTIMES.read_text(encoding="utf-8"))
        for r in old:
            if r.get("venue_id") == venue["id"] and r.get("detail_url"):
                prev[(r["film_title"].lower(), r.get("film_year"))] = r["detail_url"]
    except (OSError, ValueError):
        pass

    details: dict = {}
    for mid, movie in movies.items():
        title, year, _ = _clean_movie(movie)
        slug = _slugify(title)
        url = pages.get(slug) or (pages.get(f"{slug}-{year}") if year else None) \
            or prev.get((title.lower(), year))
        if url is None:
            for candidate in ([f"{origin}/movies/{slug}-{year}/"] if year else []) + \
                             [f"{origin}/movies/{slug}/"]:
                try:
                    session.get(candidate)
                    url = candidate
                    break
                except Exception:
                    continue
        details[mid] = url
    return details


def scrape(venue: dict, session: PoliteSession, scraped_at: str) -> list[dict]:
    origin = "{0.scheme}://{0.netloc}".format(urlparse(venue["listings_url"]))
    data = json.loads(session.get(origin + "/wp-json/nj/v1/showtime/listings").text)
    movies = {m["movie_id"]: m for m in data.get("movies", [])}
    details = _resolve_details(session, origin, venue, movies)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

    records: list[dict] = []
    for show in data.get("showtimes", []):
        movie = movies.get(show.get("movie_id"))
        if movie is None or not show.get("datetime"):
            continue
        title, year, fmt = _clean_movie(movie)
        start = localize(
            datetime.strptime(show["datetime"], "%Y%m%d%H%M%S"), venue["tz"]
        )
        if start < cutoff:
            continue
        records.append({
            "venue_id": venue["id"],
            "film_title": title,
            "film_year": year,
            "start": start,
            "screen": None,
            "format": fmt or (show.get("qualifier") or None),
            "series": None,
            "ticket_url": show.get("purchase_url") or venue["listings_url"],
            "detail_url": details.get(show.get("movie_id")),
            "sold_out": False,  # not exposed by the listings route
            "source_scraped_at": scraped_at,
        })
    return records
