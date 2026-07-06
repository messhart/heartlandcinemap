"""Build public/zips.json — ZIP (ZCTA) centroid lookup for the distance filter.

Source: US Census Bureau Gazetteer ZCTA file (public domain).
https://www2.census.gov/geo/docs/maps-data/data/gazetteer/

We keep only centroids inside a generous Midwest bounding box (the site is
Midwest-only, but users just over the border — Louisville, Pittsburgh — may
search from outside it). Coordinates are rounded to 4 decimals (~11 m), which
is far finer than the 50-mile radius filter needs.

Run manually when the Census publishes a new gazetteer (yearly); commit the
output. Not part of the scheduled scrape.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
import zipfile
from pathlib import Path

YEAR = 2025
URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    f"{YEAR}_Gazetteer/{YEAR}_Gaz_zcta_national.zip"
)

# Generous Midwest bounding box: the 12 Census Midwest states plus a margin
# so near-border users (KY, PA, CO edges) can still search.
LAT_MIN, LAT_MAX = 35.5, 49.5
LNG_MIN, LNG_MAX = -104.5, -79.0

OUT_PATH = Path(__file__).resolve().parent.parent / "public" / "zips.json"


def main() -> None:
    print(f"downloading {URL} ...")
    req = urllib.request.Request(
        URL, headers={"User-Agent": "HeartlandCinemapBot/0.1 (dyfttym@protonmail.ch)"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        archive = zipfile.ZipFile(io.BytesIO(resp.read()))

    (member,) = archive.namelist()
    zips: dict[str, list[float]] = {}
    with archive.open(member) as fh:
        # 2025 gazetteer is pipe-delimited (earlier years were tab)
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"), delimiter="|")
        for row in reader:
            # Gazetteer columns have trailing whitespace in their names/values
            row = {k.strip(): v.strip() for k, v in row.items()}
            lat, lng = float(row["INTPTLAT"]), float(row["INTPTLONG"])
            if LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX:
                zips[row["GEOID"]] = [round(lat, 4), round(lng, 4)]

    OUT_PATH.write_text(
        json.dumps(zips, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"wrote {OUT_PATH.name}: {len(zips)} ZCTAs, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
