# Venue registry — `venues/`

The **source of truth** for stable venue metadata, split **one file per state**
(`il.yaml`, `in.yaml`, `mn.yaml`, …). These files are **hand-maintained** and
human/agent/code-readable. They are NOT where showtimes live — showtimes are
volatile and machine-generated into `showtimes.json` by the scraper.

**Write rules**
- Humans hand-edit these files freely.
- The **scraper never touches them.**
- The **venue-updater** (`scripts/update_venues.py`) is the *only* automated
  writer, and only *after you approve* its proposal. See PROJECT_BRIEF.md.
- Build step: `scripts/build_venues.py` globs `venues/*.yaml` and emits the
  merged `public/venues.json` the frontend consumes. Never hand-edit that JSON.

Each file is a top-level `venues:` list. `id` values must be globally unique
across *all* state files (they're the join key for showtimes).

## Field reference
| field          | req | notes |
|----------------|-----|-------|
| `id`           | ✓   | stable slug, lowercase-hyphenated. Never change once assigned — it's the FK for showtimes.json. |
| `name`         | ✓   | display name. |
| `city`,`state` | ✓   | `state` = 2-letter USPS code (matches the filename). |
| `address`      |     | street only (no city/state). |
| `zip`          |     | 5-digit, quoted string. |
| `lat`,`lng`    | ✓*  | decimal degrees. *Required for the distance filter — the point of the site. Mark `coords: approx` if guessed. |
| `tz`           | ✓   | IANA timezone (e.g. `America/Chicago`, `America/Indiana/Indianapolis`). The scraper localizes naive showtimes with this — Midwest spans Central/Eastern, and Indiana is its own adventure. |
| `website`      |     | official homepage. |
| `listings_url` |     | page the scraper actually hits for showtimes (often calendar/now-showing, not homepage). |
| `platform`     |     | ticketing/CMS backend → drives adapter choice. One of: `elevent` `veezi` `agile` `eventive` `filmbot` `audienceview` `etix` `wordpress_custom` `custom_js` `unknown`. |
| `fetch_method` |     | `static_html` \| `headless` \| `api` \| `ical` \| `jsonld` \| `unknown`. |
| `adapter`      |     | scraper module that handles it; usually == platform. Group by adapter, not venue. |
| `venue_match`  |     | eventive only: case-insensitive substring of the event's venue name, for orgs running several cinemas on one Eventive bucket (e.g. Milwaukee Film). Omit to take every event. |
| `nonprofit`    |     | true/false if known. |
| `screens`      |     | integer if known. |
| `programming`  |     | tags: arthouse, repertory, foreign, documentary, first-run-indie, cult, classic, university, microcinema, mixed. |
| `status`       | ✓   | `verified` \| `needs_verification` \| `draft`. |
| `last_verified`|     | ISO date of last human/updater confirmation. Used by the updater to prioritize re-checks. |
| `source_urls`  |     | list of URLs the info was confirmed from (provenance for the updater's diffs). |
| `notes`        |     | adapter quirks, borderline-qualifying reason, known API endpoints, robots.txt caveats. |
