---
name: verify
description: How to run and verify the Heartland Cinemap static frontend and scraper on this Windows machine
---

# Verifying Heartland Cinemap

## Frontend (public/)

Serve the static root, then drive it. **Playwright (in .venv) is the best
handle** — it can click (e.g. the `details.about` synopsis toggles) and read
`#status` / DOM counts directly:

```python
from playwright.sync_api import sync_playwright  # .venv has chromium installed
# page.goto('http://localhost:8137/?zip=46201&mi=200&sort=distance', wait_until='networkidle')
# page.inner_text('#status'), page.locator('ul.shows li').count()
# page.locator('details.about summary').first.click()
```

Fallback: headless Edge screenshots. App state is fully URL-addressable
(`?zip=&mi=&sort=`), so most states need no clicking.

```powershell
# serve (background) — RangeHTTPServer, NOT http.server: the map's .pmtiles
# needs HTTP Range requests, which plain http.server silently ignores
C:/Data/heartlandcinemap/.venv/Scripts/python.exe -m RangeHTTPServer 8137  # run from public/

# screenshot a state — MUST launch via PowerShell Start-Process; from Git Bash,
# msedge detaches and --dump-dom/--screenshot silently produce nothing
$edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
Start-Process -Wait -FilePath $edge -ArgumentList `
  "--headless","--disable-gpu","--no-first-run","--user-data-dir=$scratch/edgeprofile", `
  "--virtual-time-budget=8000","--window-size=1000,1600", `
  "--screenshot=$scratch/shot.png","http://localhost:8137/?zip=46201&mi=50&sort=distance"
```

`--virtual-time-budget=8000` is what lets the fetch()es of venues.json /
showtimes.json / zips.json finish before capture.

States worth checking: default (no params), `?zip=46201&mi=50` (distance
labels), `?zip=60613&mi=50` (empty state — venue in radius but no adapter),
`?sort=title` (film grouping, sold-out badges), `?zip=99999` (unknown-ZIP
warning). Verify tz labels: July shows EDT, November EST for Kan-Kan.

**Poster (print calendar):** `?poster=1` opens the preview overlay after the
first render. Verify the actual print surface with Playwright's
`page.pdf(prefer_css_page_size=True)` — it uses print media emulation, so it
exercises the `@media print` + named `@page cal` (letter landscape) rules for
real; then Read the PDF (count `/Type /Page` — pages must equal poster-page
count). Dense multi-venue month: `?zip=60614&d=30` (Music Box + Siskel).
Single venue (no codes/legend): `?venue=music-box-chicago`. 7-day horizon
renders the week strip (thumbs, 5/day); 30-day the month grid (3/day);
60/all hides the feature. Picks: click `.chip-add` toggles, state in
localStorage `hcm-plan`; clear it between test states. The .ics download is
testable via `pg.expect_download()`. Dark-mode map popup checks need
`new_page(color_scheme='dark')`.

Print-fit gotchas (hard-won): the fitter's overflow test must be strict
`scrollHeight > clientHeight` — scrollHeight is floored at clientHeight, so
any "- slack" makes it a tautology that floor-trims every cell. And any
body/ancestor padding left unzeroed in `@media print` tips each sheet onto
a trailing blank page.

## Scraper

```
.venv/Scripts/python scripts/build_venues.py
.venv/Scripts/python scraper/run.py --venue kan-kan-indianapolis   # ~90 s, live
```

Full run takes the same time (other venues skip — no adapters yet). Be polite:
avoid hammering Kan-Kan with repeated runs; it's a real nonprofit's WordPress
site. Check run_report.json for per-venue status; showtimes.json offsets must
be -04:00/-05:00 (Indianapolis).

## Gotchas

- No Node on this machine; Edge headless is the only local JS runtime.
- Windows needs the `tzdata` pip package for zoneinfo (in requirements.txt).
- Console prints of curly quotes show as `??` in the terminal — that's the
  console codepage, not a data bug; check the JSON bytes before "fixing".
