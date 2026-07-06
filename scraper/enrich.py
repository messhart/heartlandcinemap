"""TMDb enrichment — separate step, never part of the per-venue adapters.

Reads public/showtimes.json, looks up each distinct (film_title, film_year)
on TMDb, and maintains public/films.json: a committed cache keyed by the same
normalized key the frontend computes, so the browser just joins two JSON
files. Only unknown films hit the TMDb API, so a typical scheduled run makes
zero to a handful of calls.

Matching is deliberately conservative — a wrong synopsis is worse than none:
- normalized title must equal the TMDb title or original title, and
- if we know the film year, the release year must be within 1.
Non-film events (trivia nights etc.) simply never match.

Requires TMDB_API_KEY in the environment (GitHub Actions secret / local env).
Misses are cached too and retried after RETRY_DAYS.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOWTIMES = ROOT / "public" / "showtimes.json"
FILMS = ROOT / "public" / "films.json"

API = "https://api.themoviedb.org/3"
RETRY_DAYS = 30
CALL_DELAY_S = 0.3


def film_key(title: str, year) -> str:
    """Join key shared with the frontend (app.js filmKey) — keep in sync."""
    return re.sub(r"\s+", " ", title).strip().lower() + "|" + (str(year) if year else "")


FORMAT_PAREN_RE = re.compile(
    r"\((?:[^)]*(?:4K|RESTORATION|35MM|70MM|IMAX|REMASTER|ANNIVERSARY)[^)]*)\)", re.I
)


FORMAT_SUFFIX_RE = re.compile(
    r"(\s*[-–—]\s*(?:70\s?mm|35\s?mm|16\s?mm|4k[^,]*|imax)|\s+on\s+(?:70|35|16)\s?mm)\s*$", re.I
)
TITLE_YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)")


def norm(title: str) -> str:
    """Loose title form for match comparison only (article-insensitive)."""
    t = title.replace("’", "'").replace("‘", "'").replace("&", "and").casefold()
    t = re.sub(r"^the\s+", "", t.strip())
    return "".join(c for c in t if c.isalnum())


def clean_query(title: str, year) -> tuple[str, int | None]:
    """Strip presentation noise from a title before searching TMDb."""
    title = FORMAT_PAREN_RE.sub("", title).strip()   # "NETWORK (4K RESTORATION)"
    m = TITLE_YEAR_RE.search(title)
    if m:                                            # "Night Nurse (2026)"
        year = year or int(m.group(1))
        title = title.replace(m.group(0), "").strip()
    title = FORMAT_SUFFIX_RE.sub("", title).strip()  # "The Odyssey - 70mm"
    return title, year


def tmdb(path: str, **params) -> dict:
    params["api_key"] = os.environ["TMDB_API_KEY"]
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    time.sleep(CALL_DELAY_S)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def candidate_queries(title: str) -> list[str]:
    """The title itself, then colon/dash segments — venues embed series names
    and edition tags around the real film title ("Cult 101: Jaws", "Backrooms:
    Everything Must Go Edition w/ Bonus Footage", "From Book to Film 2026:
    Essential American Voices - Passing"). Segments are only tried when the
    full title finds nothing, so exact titles containing colons (Twin Peaks:
    Fire Walk with Me) are never split. Last segment is tried first."""
    queries = [title]
    parts = re.split(r":|\s[-–—]\s", title)
    if len(parts) > 1:
        for seg in reversed(parts):
            # drop "w/ guest"-style tails from segments
            seg = re.split(r"\s+w/\s|\s+with\s+special\s", seg, flags=re.I)[0].strip()
            if len(seg) > 2 and seg not in queries:
                queries.append(seg)
    return queries


def best_match(title: str, year, screen_year=None) -> dict | None:
    title, year = clean_query(title, year)
    queries = candidate_queries(title)
    for query in queries:
        match = _search_one(query, year, screen_year)
        if match is not None:
            return match
    # Series screenings often carry the EVENT year, not the film's (Gateway's
    # "From Book to Film 2026: ... - Passing" says 2026 for a 2021 film). For
    # segment queries only, retry year-less — the unambiguous-single-film rule
    # in _search_one still applies, so remade titles stay unmatched.
    if year:
        for query in queries[1:]:
            match = _search_one(query, None, screen_year)
            if match is not None:
                return match
    return None


def _search_one(title: str, year, screen_year=None) -> dict | None:
    results = []
    if year:
        results = tmdb("/search/movie", query=title, primary_release_year=year).get("results", [])
    if not results:
        results = tmdb("/search/movie", query=title).get("results", [])

    want = norm(title)
    exact = []
    for r in results:
        if norm(r.get("title", "")) != want and norm(r.get("original_title", "")) != want:
            continue
        r_year = int(r["release_date"][:4]) if r.get("release_date") else None
        if year and (r_year is None or abs(r_year - year) > 1):
            continue
        exact.append((r, r_year))
    if year and exact:
        return exact[0][0]
    if exact:
        # No year to disambiguate: only match when all same-titled candidates
        # are the same film (repertory houses screen remade titles — a wrong
        # synopsis is worse than none).
        years = {ry for _, ry in exact}
        if len(years) == 1:
            return exact[0][0]
        return None
    # Annual-edition rule: venues drop the year that IS part of the TMDb
    # title ("CatVideoFest" at Music Box vs TMDb's "CatVideoFest 2026").
    # Accept only a title that literally equals venue title + the year the
    # film is being screened — an exact construction, so ordinary titles
    # ("Alien") can never drift onto sequels or remakes.
    if year is None and screen_year:
        suffixed = [
            r for r in results
            if norm(r.get("title", "")) == want + str(screen_year)
        ]
        if len(suffixed) == 1:
            return suffixed[0]
    # Fallback for subtitled TMDb titles ("Summer of Soul (...Or, When the
    # Revolution Could Not Be Televised)"): prefix match, but only with an
    # exact year to keep sequels/remakes out.
    if year:
        for r in results:
            r_year = int(r["release_date"][:4]) if r.get("release_date") else None
            if r_year == year and norm(r.get("title", "")).startswith(want):
                return r
    return None


def main() -> None:
    if not os.environ.get("TMDB_API_KEY"):
        sys.exit("TMDB_API_KEY not set — skipping enrichment would leave films.json stale; failing loudly instead.")

    showtimes = json.loads(SHOWTIMES.read_text(encoding="utf-8"))
    films = json.loads(FILMS.read_text(encoding="utf-8")) if FILMS.exists() else {}

    wanted = {}  # key -> (title, year, earliest screening year)
    for s in showtimes:
        key = film_key(s["film_title"], s.get("film_year"))
        screen_year = int(s["start"][:4])
        if key in wanted:
            screen_year = min(screen_year, wanted[key][2])
        wanted[key] = (s["film_title"], s.get("film_year"), screen_year)

    retry_before = (date.today() - timedelta(days=RETRY_DAYS)).isoformat()
    new = misses = 0
    for key, (title, year, screen_year) in sorted(wanted.items()):
        cached = films.get(key)
        if cached and not (cached.get("miss") and cached.get("checked", "") < retry_before):
            continue
        match = best_match(title, year, screen_year)
        if match is None:
            films[key] = {"miss": True, "checked": date.today().isoformat()}
            misses += 1
            continue
        detail = tmdb(f"/movie/{match['id']}")
        films[key] = {
            "title": detail.get("title"),
            "year": int(detail["release_date"][:4]) if detail.get("release_date") else None,
            "overview": detail.get("overview") or None,
            "runtime": detail.get("runtime") or None,
            "poster_path": detail.get("poster_path"),  # for future poster use
            "url": f"https://www.themoviedb.org/movie/{detail['id']}",
        }
        new += 1

    FILMS.write_text(json.dumps(films, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    matched = sum(1 for v in films.values() if not v.get("miss"))
    print(f"films.json: {len(wanted)} films this run, {new} newly matched, "
          f"{misses} new misses, {matched}/{len(films)} matched total")


if __name__ == "__main__":
    main()
