# Heartland Cinemap

A free, non-commercial website listing showtimes at arthouse, independent,
repertory, and nonprofit cinemas across the US Midwest. Enter a ZIP code, pick
a radius (50 / 100 / 200 mi), and see what's screening nearby — then buy your
ticket at the cinema's own box office. We never sell or hold tickets.

**Live at <https://messhart.github.io/heartlandcinemap/>** — early days:
nine cinemas across six states so far (Indianapolis, Chicago, Fort Wayne,
Milwaukee ×2, Columbus, Des Moines, St. Louis, and more coming). Film
descriptions courtesy of the TMDB API (the site is not endorsed or
certified by TMDB).

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
- [x] Filmbot platform adapter (Gateway, Varsity; Neon + Cleveland
      Cinematheque need follow-up — see venues/oh.yaml notes)
- [x] Local scrape-health dashboard (dashboard.html, read-only)
- [x] Static frontend: ZIP + radius filter, sort by time/distance/title
- [x] GitHub Actions cron: scrape → commit JSON → deploy (GitHub Pages)
- [ ] Printable calendar poster view (letter, B/W or color)
- [x] TMDb enrichment (synopsis + runtime; posters cached for later)
- [ ] Map view (MapLibre GL + Protomaps static tiles)
- [ ] Expand venue registry across the Midwest (next: Neon GraphQL +
      Cinematheque, then Siskel/Agile, Trylon/MSP WordPress, FilmScene)
- [ ] Venue-updater tool (approval-gated) + local scrape dashboard

## License

[MIT](LICENSE)
