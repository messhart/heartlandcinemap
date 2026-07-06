"""Adapter registry: adapter name (venues.yaml `adapter` field) -> module.

Each adapter module exposes scrape(venue, session, scraped_at) -> list[dict].
Adapters are written per ticketing platform, not per venue.
"""

from adapters import eventive, filmbot, musicbox_custom, wordpress_elevent

ADAPTERS = {
    "wordpress_elevent": wordpress_elevent,
    "musicbox_custom": musicbox_custom,
    "eventive": eventive,
    "filmbot": filmbot,
}
