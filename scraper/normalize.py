"""Shared normalization for scraper output.

Every adapter returns raw screening dicts; this module turns them into the
canonical showtime schema (see PROJECT_BRIEF.md), enforcing:

- `start` as ISO-8601 WITH a timezone offset (Midwest spans Central/Eastern,
  so naive local times are forbidden),
- dedupe on (venue_id, film_title, start),
- presence and basic sanity of required fields.

Records that fail validation are dropped and reported, never emitted.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

REQUIRED_FIELDS = ("venue_id", "film_title", "start", "ticket_url", "source_scraped_at")

OPTIONAL_DEFAULTS = {
    "film_year": None,
    "screen": None,
    "format": None,
    "series": None,
    "sold_out": False,
}


class ValidationError(ValueError):
    pass


def localize(naive: datetime, tz_name: str) -> datetime:
    """Attach an IANA timezone to a naive local datetime."""
    if naive.tzinfo is not None:
        raise ValidationError(f"expected naive datetime, got tz-aware: {naive!r}")
    return naive.replace(tzinfo=ZoneInfo(tz_name))


def validate_record(rec: dict) -> dict:
    """Return a schema-shaped copy of `rec`, or raise ValidationError."""
    for field in REQUIRED_FIELDS:
        if not rec.get(field):
            raise ValidationError(f"missing required field {field!r}: {rec!r}")

    start = rec["start"]
    if isinstance(start, datetime):
        if start.tzinfo is None:
            raise ValidationError(f"start has no timezone: {rec['film_title']!r} {start}")
        start = start.isoformat()
    else:
        parsed = datetime.fromisoformat(start)
        if parsed.tzinfo is None:
            raise ValidationError(f"start string has no offset: {start!r}")

    if not str(rec["ticket_url"]).startswith(("http://", "https://")):
        raise ValidationError(f"ticket_url is not a URL: {rec['ticket_url']!r}")

    out = {**OPTIONAL_DEFAULTS, **rec, "start": start}
    return {k: out[k] for k in (*REQUIRED_FIELDS, *OPTIONAL_DEFAULTS)}


def normalize(records: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate + dedupe. Returns (clean_records, problem_messages)."""
    problems: list[str] = []
    seen: set[tuple] = set()
    clean: list[dict] = []

    for rec in records:
        try:
            valid = validate_record(rec)
        except ValidationError as exc:
            problems.append(str(exc))
            continue
        key = (valid["venue_id"], valid["film_title"], valid["start"])
        if key in seen:
            continue
        seen.add(key)
        clean.append(valid)

    clean.sort(key=lambda r: (r["start"], r["venue_id"], r["film_title"]))
    return clean, problems
