# Heartland Cinemap

A free, non-commercial website listing showtimes at arthouse, independent,
repertory, and nonprofit cinemas across the US Midwest. Enter a ZIP code, pick
a radius (50 / 100 / 200 mi), and see what's screening nearby — then buy your
ticket at the cinema's own box office. We never sell or hold tickets.

**Live at <https://messhart.github.io/heartlandcinemap/>** — eleven cinemas
across seven states (Chicago ×2, Indianapolis, Fort Wayne, Milwaukee ×2,
Columbus, Cleveland, Dayton*, Des Moines, Minneapolis, St. Louis). Film
descriptions courtesy of the TMDB API (the site is not endorsed or
certified by TMDB). *The Neon is registered but not scraped — their
robots.txt disallows their showtimes API, so we're asking first.

## How it works (and why it costs ~nothing)

The dataset is tiny (dozens of venues × ~2 weeks of showtimes), so there is no
backend and no database at query time:

- A Python **scraper pipeline** (scheduled via GitHub Actions) produces one
  normalized `public/showtimes.json`.
- A **static frontend** loads `venues.json` + `showtimes.json` and does all
  filtering, ZIP→distance math, and sorting client-side.
- Hosting is a free static host. The only recurring cost is the domain.

See [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for the full design.

## Repo layout

```
venues/          source of truth for venue metadata — hand-maintained,
                 one YAML file per state (schema in venues/README.md)
scraper/         scraper pipeline: run.py orchestrator, adapters/ (one per
                 ticketing platform, not per venue), normalize.py
scripts/         build_venues.py (venues/*.yaml -> public/venues.json) and
                 other manually-run tools
public/          static site root; generated venues.json + showtimes.json
```

Rules of the road:
- `venues/*.yaml` is hand-maintained; **the scraper never writes there**.
- `public/showtimes.json` is disposable scraper output, regenerated every run.
- Adapters are written **per ticketing platform** (Elevent, Veezi, Agile, …),
  so one adapter serves every venue on that backend.

## Ethics

- Showtimes are facts; we aggregate the facts and **link out** for everything
  else. Film art and synopses come from TMDb, never copied from cinemas' sites.
- Scraping is polite: we respect `robots.txt`, rate-limit, cache, and identify
  ourselves with a contact address in the User-Agent.
- Every listing links the cinema's own page as the source of truth, and every
  record carries a freshness timestamp.
- **If you run a cinema and want to be removed, email
  dyfttym@protonmail.ch and we'll remove you — no questions asked.**

## Roadmap

- [x] Venue registry format + seed venues (IL, IN, MN)
- [x] Milestone 1: normalize + Kan-Kan (Elevent/WordPress) adapter, live run
- [x] Music Box adapter (Playwright headless past the Sucuri JS challenge)
- [x] Eventive platform adapter (Cinema Center, Hi-Pointe, Oriental, Downer)
- [x] Filmbot platform adapter (Gateway, Varsity, Hi-Pointe, Cleveland
      Cinematheque)
- [x] Gene Siskel (Drupal calendar), Trylon (WordPress) adapters
- [x] Neon GraphQL adapter written — held back until the venue OKs it
      (their robots.txt disallows /graphql)
- [x] Local scrape-health dashboard (dashboard.html, read-only)
- [x] Static frontend: ZIP + radius filter, sort by time/distance/title
- [x] GitHub Actions cron: scrape → commit JSON → deploy (GitHub Pages)
- [x] Printable calendar poster view (letter landscape, B/W or color) —
      pick showtimes with the ＋ toggle (5/day weekly, 3/day monthly) to
      curate your own calendar, or print the auto-filled one; 7-day horizon
      prints a week strip with TMDb thumbs, 30-day prints month grids;
      picks also export to Apple/Outlook/Google via a generated .ics
- [x] TMDb enrichment (synopsis + runtime; posters cached for later)
- [x] Map view — MapLibre GL + a self-hosted Protomaps extract
      (public/basemap/midwest.pmtiles, z0-9, ~60 MB, no tile server or API
      key; rebuild: `pmtiles extract https://build.protomaps.com/<date>.pmtiles
      public/basemap/midwest.pmtiles --bbox=-104.5,35.5,-79.0,49.5 --maxzoom=9`)
- [ ] Expand venue registry across the Midwest (next candidates: MSP Film
      / The Main, Facets Chicago, FilmScene Iowa City, Film Streams Omaha,
      The Ross Lincoln, Detroit Film Theatre, Michigan/State Ann Arbor)
- [ ] Venue-updater tool (approval-gated) + local scrape dashboard

## License

[MIT](LICENSE)
