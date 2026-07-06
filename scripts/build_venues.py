"""Merge venues/*.yaml into public/venues.json.

The YAML files are the hand-maintained source of truth (see venues/README.md);
this script is the only thing that should ever write public/venues.json.
Fails loudly on duplicate ids — they are the join key for showtimes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
VENUES_DIR = ROOT / "venues"
OUT_PATH = ROOT / "public" / "venues.json"


def load_venues() -> list[dict]:
    venues: list[dict] = []
    seen_ids: dict[str, str] = {}
    for path in sorted(VENUES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for venue in data.get("venues") or []:
            vid = venue.get("id")
            if not vid:
                sys.exit(f"ERROR: venue without id in {path.name}: {venue!r}")
            if vid in seen_ids:
                sys.exit(f"ERROR: duplicate id {vid!r} in {path.name} (also in {seen_ids[vid]})")
            seen_ids[vid] = path.name
            venues.append(venue)
    return venues


def main() -> None:
    venues = load_venues()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(venues, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT_PATH.relative_to(ROOT)} ({len(venues)} venues)")


if __name__ == "__main__":
    main()
