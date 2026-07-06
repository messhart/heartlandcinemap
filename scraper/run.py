"""Scraper orchestrator.

Reads public/venues.json (build it first: python scripts/build_venues.py),
dispatches each venue to its adapter, and writes:

- public/showtimes.json — the normalized, deduped screening records
- run_report.json      — per-venue status for the ops dashboard

A venue failing NEVER crashes the run: it's logged in the report and skipped.

Usage:
    python scraper/run.py [--venue VENUE_ID]

--venue rescrapes ONE venue and merges the result into the existing
public/showtimes.json (other venues' records are kept). The scheduled run
always covers all venues and rewrites the file from scratch.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from adapters import ADAPTERS
from fetch import PoliteSession
from normalize import normalize

ROOT = Path(__file__).resolve().parent.parent
VENUES_JSON = ROOT / "public" / "venues.json"
SHOWTIMES_JSON = ROOT / "public" / "showtimes.json"
RUN_REPORT = ROOT / "run_report.json"


def scrape_venue(venue: dict, session: PoliteSession, scraped_at: str) -> dict:
    """Scrape one venue; returns a report entry with records attached."""
    entry = {
        "venue_id": venue["id"],
        "status": "skipped",
        "showtimes": 0,
        "duration_s": 0.0,
        "error": None,
        "scraped_at": scraped_at,
    }
    adapter = ADAPTERS.get(venue.get("adapter") or "")
    if adapter is None:
        entry["error"] = f"no adapter for {venue.get('adapter')!r}"
        return entry
    if not venue.get("listings_url"):
        entry["error"] = "no listings_url"
        return entry
    if not venue.get("tz"):
        entry["error"] = "no tz (required to localize showtimes)"
        return entry

    started = time.monotonic()
    try:
        records = adapter.scrape(venue, session, scraped_at)
    except Exception as exc:  # any venue failure is isolated, never fatal
        entry["status"] = "error"
        entry["error"] = f"{type(exc).__name__}: {exc}"
    else:
        entry["status"] = "ok"
        entry["showtimes"] = len(records)
        entry["records"] = records
    entry["duration_s"] = round(time.monotonic() - started, 1)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", help="scrape only this venue_id")
    args = parser.parse_args()

    if not VENUES_JSON.exists():
        raise SystemExit("public/venues.json missing — run: python scripts/build_venues.py")
    venues = json.loads(VENUES_JSON.read_text(encoding="utf-8"))
    if args.venue:
        venues = [v for v in venues if v["id"] == args.venue]
        if not venues:
            raise SystemExit(f"unknown venue_id {args.venue!r}")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session = PoliteSession()
    all_records: list[dict] = []
    report = []

    if args.venue and SHOWTIMES_JSON.exists():
        # single-venue refresh: keep everyone else's existing records
        all_records = [
            r for r in json.loads(SHOWTIMES_JSON.read_text(encoding="utf-8"))
            if r["venue_id"] != args.venue
        ]

    for venue in venues:
        entry = scrape_venue(venue, session, scraped_at)
        all_records.extend(entry.pop("records", []))
        report.append(entry)
        print(f"{entry['venue_id']}: {entry['status']}"
              f" ({entry['showtimes']} showtimes, {entry['duration_s']}s)"
              + (f" — {entry['error']}" if entry["error"] else ""))

    clean, problems = normalize(all_records)
    for msg in problems:
        print(f"DROPPED: {msg}")

    SHOWTIMES_JSON.parent.mkdir(parents=True, exist_ok=True)
    SHOWTIMES_JSON.write_text(
        json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    RUN_REPORT.write_text(
        json.dumps({"run_at": scraped_at, "dropped_records": len(problems),
                    "venues": report}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {len(clean)} showtimes -> {SHOWTIMES_JSON.relative_to(ROOT)}")
    print(f"wrote run report        -> {RUN_REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
