---
name: verify
description: How to run and verify the Heartland Cinemap static frontend and scraper on this Windows machine
---

# Verifying Heartland Cinemap

## Frontend (public/)

Serve the static root, then drive it with headless Edge. App state is fully
URL-addressable, so no clicking is needed — pass `?zip=&mi=&sort=` params.

```powershell
# serve (background)
C:/Data/heartlandcinemap/.venv/Scripts/python.exe -m http.server 8137  # run from public/

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
