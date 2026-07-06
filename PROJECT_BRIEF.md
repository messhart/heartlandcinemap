# Midwest Arthouse Showtimes — Project Brief (Claude Code handoff)

## Purpose
A free, non-commercial website that lists showtimes at arthouse / independent /
repertory / nonprofit cinemas across the US Midwest. A user enters a ZIP code,
picks a radius (50 / 100 / 200 mi), and sees what's screening nearby — sortable
by showtime, distance, or film title — with a link out to each cinema's own
box office to buy tickets. Secondary feature: export a week or month of listings
to a letter-sized, printable calendar poster (B/W or color).

Design priorities, in order: **low running cost** (should survive for years on
near-$0), **easy to maintain**, **simple**, **accessible**, **clean**. The site
routes traffic *to* cinemas' box offices; it never sells or holds tickets.

## Architecture (why it's cheap)
The entire dataset is tiny (dozens of venues × ~2 weeks of showtimes = a few
hundred KB). So there is **no backend and no database at query time**:

- A Python **scraper pipeline** runs on a schedule (GitHub Actions cron, free
  for public repos), producing one normalized `showtimes.json`.
- A **static frontend** loads `venues.json` + `showtimes.json` and does *all*
  filtering, ZIP→distance math, and sorting **client-side**.
- Hosting is a static host (Cloudflare Pages / GitHub Pages, $0). Only real
  recurring cost is the domain.

### Recommended stack
- **Frontend:** Astro (static-first, ships ~no JS except interactive islands)
  or plain HTML+JS if you want maximum simplicity.
- **Map:** MapLibre GL + Protomaps `.pmtiles` — build ONE vector-tile file for
  the fixed Midwest bbox, host it as a static file, render fully client-side.
  No tile server, no API key, no per-request billing. Fallback: Leaflet + a
  free-tier tile provider.
- **ZIP → location:** ship a US ZIP-centroid table (public domain, Census/
  GeoNames) as a static lookup. Haversine in JS for the radius filter + the
  "sort by distance" option. (Straight-line distance to start; drive-time
  needs a paid routing API — defer.)
- **Poster export:** a dedicated print view, CSS Grid calendar, `@page { size:
  letter }`, a print stylesheet with a color/B&W toggle → browser "Save as PDF".
  No server. Add jsPDF later only if a direct download (no print dialog) matters.
- **Film metadata enrichment:** TMDb free API for posters/runtime/synopsis.
  IMPORTANT: pull creative content (art, synopses) from TMDb, NOT by copying
  cinemas' own curated blurbs — see Ethics below.

## Data model
Two files with opposite lifecycles — keep them separate:

1. **`venues/*.yaml`** — source of truth, HAND-MAINTAINED, split one file per
   state (`il.yaml`, `in.yaml`, …). Stable venue metadata. Schema in
   `venues/README.md`. `scripts/build_venues.py` globs `venues/*.yaml` and emits
   the merged `public/venues.json`. The **scraper must NEVER write here.** The
   only automated writer is the venue-updater, and only after human approval
   (see "Venue updater" below). `id` values must be unique across all files.
2. **`showtimes.json`** — DISPOSABLE scraper output, regenerated every run.

### Normalized showtime schema (scraper output)
Each screening record:
```json
{
  "venue_id": "kan-kan-indianapolis",   // FK into venues.yaml
  "film_title": "The Piano Teacher",
  "film_year": 2001,                     // if available, aids TMDb match
  "start": "2026-07-14T19:30:00-05:00",  // ISO 8601 WITH timezone offset
  "screen": null,                         // or auditorium name/number if known
  "format": null,                         // e.g. "35mm", "4K", "Q&A" if known
  "series": "David Lynch Directs",       // programming series/strand, if any
  "ticket_url": "https://kankanindy.com/events/thepianoteacher2001/",
  "sold_out": false,                      // if determinable
  "source_scraped_at": "2026-07-05T12:00:00Z"  // freshness timestamp
}
```
Enrichment (TMDb poster/runtime/synopsis) is joined on the frontend or in a
separate enrichment step keyed by (film_title, film_year) — do NOT bake it into
the scraper's per-venue adapters.

## Scrape spec — what each adapter must extract
Minimum viable per screening: **film title, start datetime (with tz), and a
ticket/detail URL**, tagged with the correct `venue_id`. Everything else
(year, screen, format, series, sold-out) is best-effort. Normalize times to ISO
8601 **with a timezone offset** — Midwest spans Central and Eastern, so a naive
local time is a bug waiting to happen. Dedupe on (venue_id, film_title, start).

## Adapter strategy — per platform, NOT per venue
Many indie theaters share a small set of backends (Elevent, Veezi, Agile,
Eventive, Filmbot, plus custom WordPress). Write ONE adapter per backend; it
serves every venue on that backend. Build a `fetch_method` abstraction so the
same adapter can pull via static HTML, a discovered JSON API, or headless
browser as needed. Prefer, in order: **structured feed (iCal / schema.org
JSON-LD) > discovered JSON API > static HTML parse > headless browser**.

### Feasibility findings from the live probe (already done — build on these)
Two pilot venues were fetched live and represent the two archetypes you'll hit:

- **Kan-Kan (Indianapolis) — EASY.** WordPress site that *server-renders* its
  listings. Plain `requests` + `BeautifulSoup` sees every event; per-event pages
  at `/events/{slug}/`. Ticketing hands off to **Elevent** (goelevent.com).
  → `fetch_method: static_html`, `adapter: wordpress_elevent`. Before hard-
  parsing HTML, check whether Elevent exposes a JSON search endpoint (it backs
  several Midwest venues, so an Elevent API adapter is high-leverage).
- **Music Box (Chicago) — HARD.** JS-rendered SPA; a raw GET returns a
  "JavaScript required" wall with NO data. → First inspect the Network tab on
  `/now-showing` and `/calendar` for the JSON/XHR endpoint the page calls and
  target that (`fetch_method: api`). Only if none exists, fall back to Playwright
  (`fetch_method: headless`). Try `/calendar` (month view) — often easier to
  parse than `/now-showing`.

## Ethics / guardrails (non-negotiable)
- Showtimes are facts (not copyrightable); aggregate the facts, **link out** for
  everything creative. Pull posters/synopses from TMDb, not from cinemas' sites.
- Respect `robots.txt` and each site's ToS. Rate-limit politely, cache, and
  identify a contact via User-Agent. If a ToS forbids scraping, skip or email.
- **Freshness:** stamp every record (`source_scraped_at`), refresh often, and
  always link the cinema's own page as source of truth. Stale listings that send
  someone to a cancelled show is the main way to actually harm a venue.
- **Opt-out backbone:** an About page (who runs it, that it's free/non-profit and
  drives traffic to box offices, a contact email, and a standing "ask to be
  removed and I'll remove you, no questions" offer). Honor removal/C&D requests
  immediately and cheerfully.

## Venue updater (manual, approval-gated)
A separate tool from the showtimes scraper, run **manually and infrequently**
(e.g. monthly) to keep the hand-maintained registry fresh. It must **never**
write to `venues/*.yaml` directly — it proposes, the human approves, then it
applies. Flow:

1. `scripts/update_venues.py` re-checks existing venues (prioritizing the
   oldest `last_verified`) for changed address / URL / platform, and can surface
   candidate new venues. It gathers evidence but changes nothing.
2. It writes a **proposal file**, `venue_updates.proposed.yaml` — a plain-text,
   reviewable diff of intended changes, each with old→new values, a confidence,
   and the source URL(s). Example:
   ```yaml
   proposals:
     - id: gene-siskel-film-center-chicago
       change: update
       fields:
         address:   {old: "",   new: "164 N. State St."}
         platform:  {old: unknown, new: agile}
       confidence: 0.9
       sources: ["https://www.siskelfilmcenter.org/visit"]
       approve: false          # <- you flip this to true to accept
     - id: some-new-venue
       change: add
       proposed: { name: "...", city: "...", state: "IL", ... }
       confidence: 0.7
       sources: ["..."]
       approve: false
   ```
3. You review — either edit the file and set `approve: true` per item, or use
   the dashboard's review view (below). Then `scripts/update_venues.py --apply`
   merges only the approved items into the correct `venues/<state>.yaml`,
   preserving comments/formatting (use `ruamel.yaml` round-trip, not `pyyaml`),
   and stamps `last_verified` + `source_urls`. Unapproved items are left intact
   for the next pass. Nothing is ever overwritten without an explicit `approve`.

## Local scrape dashboard
A tiny **local-only** ops tool (never deployed) so you can watch runs and review
venue updates in one place. Two ways to build it, pick per taste:

- **Simplest (read-only):** the scraper writes `run_report.json` (per-venue:
  status, #showtimes found, duration, error text, `scraped_at`); a single static
  `dashboard.html` fetches and renders it. Serve with `python -m http.server`.
  Shows scrape health, failures, and staleness at a glance.
- **Nicer (interactive, recommended given the approval need):** a small local
  FastAPI/Flask app (`scripts/dashboard.py`) with two views — **Scrape Health**
  (reads `run_report.json`) and **Venue Updates** (reads
  `venue_updates.proposed.yaml`, with Approve/Reject buttons that POST back and
  invoke the `--apply` step). This unifies both requests — progress monitoring
  and update approval — into one `localhost` page. Keep it dev-only; it's not
  part of the public static site.

## Suggested repo layout
```
/venues/                     # source of truth (hand-maintained, per state)
  README.md                  # schema / field reference
  il.yaml  in.yaml  mn.yaml  # ... one file per state
/scripts/
  build_venues.py            # venues/*.yaml -> public/venues.json
  update_venues.py           # manual, approval-gated registry updater
  dashboard.py               # local-only ops + update-review UI
/venue_updates.proposed.yaml # updater's proposal (review + approve here)
/scraper/
  run.py                     # orchestrates all adapters -> showtimes.json + run_report.json
  adapters/
    wordpress_elevent.py     # serves Kan-Kan (+ other Elevent venues)
    musicbox_custom.py       # serves Music Box
    __init__.py              # registry mapping adapter name -> module
  normalize.py               # shared: tz handling, dedupe, schema validation
/public/                     # static site root (Astro output)
  venues.json                # generated
  showtimes.json             # generated by scraper
/run_report.json             # per-venue scrape status (feeds dashboard)
/.github/workflows/scrape.yml  # cron: run scraper, commit outputs, deploy
```
Note: `update_venues.py` and `dashboard.py` run **locally/manually only** —
they are never part of the scheduled GitHub Action or the deployed site.

## First test-run task (do this first)
1. Read `venues.yaml`. Write `scraper/normalize.py` (ISO-8601+tz, dedupe,
   validate against the showtime schema above).
2. Write the **`wordpress_elevent`** adapter and run it against **Kan-Kan only**
   (the easy archetype). Output valid `showtimes.json` for that one venue and
   validate it. Confirm timezone is America/Indiana/Indianapolis (Eastern).
3. Then attempt **Music Box**: inspect for a JSON endpoint first; report what you
   find before writing the headless fallback.
4. Keep each adapter defensive — log and skip a venue on failure rather than
   crashing the run, and emit a per-venue success/failure summary.

Start narrow (these two), prove the full loop (scrape → normalize → JSON →
frontend filter → poster), then expand the venue registry from the research
report — cheapest expansion is everything else on Elevent, since it reuses the
Kan-Kan adapter.
