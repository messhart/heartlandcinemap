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

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PREVIOUS_SHOWTIMES = Path(__file__).resolve().parent.parent / "public" / "showtimes.json"

REQUIRED_FIELDS = ("venue_id", "film_title", "start", "ticket_url", "source_scraped_at")

# words kept lowercase in titles unless first/last or after a colon
SMALL_WORDS = {"a", "an", "the", "and", "but", "or", "nor", "of", "in", "on",
               "at", "to", "for", "with", "from", "by", "as", "vs"}
ROMAN_RE = re.compile(r"^[IVXLCDM]+$")


def _cap_word(word: str) -> str:
    """MADDIE’S -> Maddie’s; II stays II; A/V and SPIDER-MAN cap each part."""
    core, punct = re.match(r"^(.*?)([:;,!?]*)$", word).groups()
    for sep in ("-", "/", "."):
        if sep in core and core != sep:
            return sep.join(_cap_word(p) for p in core.split(sep)) + punct
    if ROMAN_RE.match(core):
        return word  # roman numerals (and single letters) stay uppercase
    return core[:1].upper() + core[1:].lower() + punct


def smart_title(text):
    """Title-case SHOUTING text; anything already mixed-case passes through.

    Some venues (Kan-Kan) publish everything in ALL CAPS; others use real
    casing. Only strings with no lowercase letters are touched, so venues
    that case their titles deliberately are never mangled. Best-effort:
    acronym titles (RRR) and Mc/Mac names lose their casing — acceptable.
    """
    if not text or any(c.islower() for c in text):
        return text
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        lower = word.lower()
        prev = words[i - 1] if i else ""
        if 0 < i < len(words) - 1 and lower in SMALL_WORDS and not prev.endswith(":"):
            out.append(lower)
        else:
            out.append(_cap_word(word))
    return " ".join(out)

OPTIONAL_DEFAULTS = {
    "film_year": None,
    "screen": None,
    "format": None,
    "series": None,
    "sold_out": False,
    "detail_url": None,  # film intro page on the venue's own site, when it
                         # differs from ticket_url (which may be a checkout)
}


class ValidationError(ValueError):
    pass


def previous_facts(venue_id: str, fields: tuple = ("film_year", "series")) -> dict:
    """ticket_url -> {field: value} from the previous run's showtimes.json.

    Detail-page fetches can flake (CDNs are rough on CI datacenter IPs), but
    film facts don't change — adapters use this to backfill gaps so every
    run is at least as informed as the last one.
    """
    try:
        old = json.loads(PREVIOUS_SHOWTIMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    facts: dict = {}
    for r in old:
        if r.get("venue_id") == venue_id:
            vals = {f: r.get(f) for f in fields if r.get(f)}
            if vals:
                facts[r["ticket_url"]] = vals
    return facts


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
    out["film_title"] = smart_title(out["film_title"])
    out["series"] = smart_title(out["series"])
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
