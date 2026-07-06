"""Adapter registry: adapter name (venues.yaml `adapter` field) -> module.

Each adapter module exposes scrape(venue, session, scraped_at) -> list[dict].
Adapters are written per ticketing platform, not per venue.
"""

from adapters import (drupal_calendar, eventive, filmbot, filmbot_graphql,
                      musicbox_custom, trylon_wordpress, wordpress_elevent)

ADAPTERS = {
    "wordpress_elevent": wordpress_elevent,
    "musicbox_custom": musicbox_custom,
    "eventive": eventive,
    "filmbot": filmbot,
    "filmbot_graphql": filmbot_graphql,
    "trylon_wordpress": trylon_wordpress,
    "drupal_calendar": drupal_calendar,
}
