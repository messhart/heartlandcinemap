/* Heartland Cinemap — all filtering/sorting happens right here in the browser.
   Data: venues.json (registry), showtimes.json (scraper output), films.json
   (TMDb metadata), zips.json (Census ZIP centroids, loaded lazily).

   Layout: chronological day sections; within a day, ONE card per film
   (poster, metadata, synopsis) with per-venue showtime chips — the chips are
   the ticket links. "By film" flips the grouping for is-X-playing lookups. */
"use strict";

(function () {
  const $zip = document.getElementById("zip");
  const $radius = document.getElementById("radius");
  const $days = document.getElementById("days");
  const $view = document.getElementById("view");
  const $tz = document.getElementById("tz");
  const $status = document.getElementById("status");
  const $listings = document.getElementById("listings");

  const FIXED_TZ = { ct: "America/Chicago", et: "America/New_York" };
  const POSTER_BASE = "https://image.tmdb.org/t/p/w154";
  const BACKDROP_BASE = "https://image.tmdb.org/t/p/w300"; // calendar strips

  let venues = {};
  let venueList = [];
  let shows = [];
  let films = {};
  let zipTable = null;
  let zipFetch = null;
  let venueFilter = null; // venue id, set by pin clicks / ?venue=
  let mapLoaded = false;
  let pendingPoster = false; // ?poster=1 seen before first render finished
  // last completed render's filtered state — the poster prints exactly this
  let lastVisible = [];
  let lastTzFn = null;
  let lastFixed = null;

  // ----- picked showtimes ("my plan") -----
  // Keys are venue_id|start; picks persist in localStorage and transcend the
  // ZIP/venue filters (they're resolved against the full shows array).
  const PLAN_LS = "hcm-plan";
  const DAY_CAPS = { 7: 5, 30: 3 }; // max distinct films/day the poster holds
  const plan = new Set();
  const showByKey = new Map(); // pickKey -> show, built at data load

  function pickKey(s) {
    return s.venue_id + "|" + s.start;
  }

  function posterCap() {
    // 0 = poster (and picking) disabled at this horizon — 60d/all listings
    // would be printed long before they're stale-proof
    return DAY_CAPS[$days.value] || 0;
  }

  function savePlan() {
    try { localStorage.setItem(PLAN_LS, JSON.stringify([...plan])); } catch (e) {}
  }

  function planShows() {
    return [...plan].map((k) => showByKey.get(k)).filter(Boolean)
      .sort((a, b) => new Date(a.start) - new Date(b.start));
  }

  // ---------- helpers ----------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function haversineMiles(lat1, lng1, lat2, lng2) {
    const R = 3958.8;
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad;
    const dLng = (lng2 - lng1) * rad;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  // Intl-based formatting so any showtime can be rendered in any zone
  const dtfCache = {};
  function dtf(tz, opts, key) {
    const k = tz + key;
    if (!dtfCache[k]) {
      dtfCache[k] = new Intl.DateTimeFormat("en-US", { timeZone: tz, ...opts });
    }
    return dtfCache[k];
  }

  function fmtTime(iso, tz) {
    return dtf(tz, { hour: "numeric", minute: "2-digit" }, "t").format(new Date(iso));
  }

  // split a showtime into the marquee's big-hour / raised-minutes parts:
  //   { h: "7", m: "00PM" }  (minutes + meridiem glued, uppercased, no space)
  function fmtTimeParts(iso, tz) {
    const parts = dtf(tz, { hour: "numeric", minute: "2-digit" }, "t")
      .formatToParts(new Date(iso));
    let h = "", m = "", ap = "";
    for (const p of parts) {
      if (p.type === "hour") h = p.value;
      else if (p.type === "minute") m = p.value;
      else if (p.type === "dayPeriod") ap = p.value;
    }
    return { h, m: (m + ap).replace(/\s+/g, "").toUpperCase() };
  }

  function dayKeyIn(iso, tz) {
    // en-CA locale renders YYYY-MM-DD, which sorts correctly
    const k = tz + "d";
    if (!dtfCache[k]) {
      dtfCache[k] = new Intl.DateTimeFormat("en-CA", {
        timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
      });
    }
    return dtfCache[k].format(new Date(iso));
  }

  function fmtDayHeading(dayKey) {
    const d = new Date(dayKey + "T12:00:00");
    return d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  }

  function fmtDayShort(dayKey) {
    const d = new Date(dayKey + "T12:00:00");
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

  const tzAbbrCache = {};
  function tzAbbr(tz, iso) {
    const key = tz + iso.slice(0, 10);
    if (!(key in tzAbbrCache)) {
      try {
        tzAbbrCache[key] = dtf(tz, { timeZoneName: "short" }, "z")
          .formatToParts(new Date(iso))
          .find((p) => p.type === "timeZoneName").value;
      } catch (e) {
        tzAbbrCache[key] = "";
      }
    }
    return tzAbbrCache[key];
  }

  function timeAgo(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 90) return `${mins} min ago`;
    if (mins < 36 * 60) return `${Math.round(mins / 60)} h ago`;
    return `${Math.round(mins / 1440)} days ago`;
  }

  // must mirror scraper/enrich.py film_key()
  function filmKey(s) {
    return s.film_title.replace(/\s+/g, " ").trim().toLowerCase() +
      "|" + (s.film_year || "");
  }

  function filmInfo(s) {
    const info = films[filmKey(s)];
    return info && !info.miss ? info : null;
  }

  function groupKey(s) {
    const info = filmInfo(s);
    return info && info.url ? info.url : filmKey(s);
  }

  function truncate(text, max) {
    if (text.length <= max) return text;
    const cut = text.slice(0, max);
    return cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 30)) + "…";
  }

  // ---------- URL state ----------

  function readParams() {
    const p = new URLSearchParams(location.search);
    if (/^\d{5}$/.test(p.get("zip") || "")) $zip.value = p.get("zip");
    if (["50", "100", "200"].includes(p.get("mi"))) $radius.value = p.get("mi");
    if (["7", "30", "60", "all"].includes(p.get("d"))) $days.value = p.get("d");
    if (["day", "film"].includes(p.get("view"))) $view.value = p.get("view");
    else if (p.get("sort") === "title") $view.value = "film"; // old links
    if (["auto", "venue", "ct", "et"].includes(p.get("tz"))) $tz.value = p.get("tz");
    if (p.get("venue")) venueFilter = p.get("venue");
    if (p.get("map") === "1") openMap();
    if (p.get("poster") === "1") pendingPoster = true; // opens after render
  }

  function writeParams() {
    const p = new URLSearchParams();
    if (/^\d{5}$/.test($zip.value)) p.set("zip", $zip.value);
    if ($radius.value !== "100") p.set("mi", $radius.value);
    if ($days.value !== "7") p.set("d", $days.value);
    if ($view.value !== "day") p.set("view", $view.value);
    if ($tz.value !== "auto") p.set("tz", $tz.value);
    if (venueFilter) p.set("venue", venueFilter);
    if (mapLoaded && !document.getElementById("map").hidden) p.set("map", "1");
    if (!document.getElementById("posterwrap").hidden) p.set("poster", "1");
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  // ---------- card building ----------

  function posterEl(info, title) {
    if (info && info.poster_path) {
      const img = el("img", "poster");
      img.src = POSTER_BASE + info.poster_path;
      img.alt = "";
      img.loading = "lazy";
      return img;
    }
    return el("div", "poster poster-blank", (title[0] || "?").toUpperCase());
  }

  // transient note appended to the status line (cap rejections etc.)
  function flash(msg) {
    const n = el("span", "warn", " · " + msg);
    $status.appendChild(n);
    setTimeout(() => n.remove(), 3500);
  }

  function updatePlanCount() {
    const box = document.getElementById("plancount");
    if (!plan.size || !posterCap()) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML = `★ ${plan.size} picked · <a href="#" id="clearplan">clear</a>`;
    box.querySelector("#clearplan").addEventListener("click", (e) => {
      e.preventDefault();
      plan.clear();
      savePlan();
      render();
    });
  }

  function togglePick(s, btn) {
    const k = pickKey(s);
    if (plan.has(k)) {
      plan.delete(k);
    } else {
      // cap: distinct films per day (two times of one film count once)
      const cap = posterCap();
      const tzFn = lastTzFn || ((x) => x.venue.tz);
      const day = dayKeyIn(s.start, tzFn(s));
      const filmsOnDay = new Set();
      for (const k2 of plan) {
        const s2 = showByKey.get(k2);
        if (s2 && dayKeyIn(s2.start, tzFn(s2)) === day) filmsOnDay.add(groupKey(s2));
      }
      if (!filmsOnDay.has(groupKey(s)) && filmsOnDay.size >= cap) {
        flash(`${fmtDayShort(day)} is full — ${cap} films max on the ` +
          (cap === 5 ? "weekly" : "monthly") + " calendar");
        return;
      }
      plan.add(k);
    }
    savePlan();
    const picked = plan.has(k);
    btn.textContent = picked ? "★" : "+";
    btn.title = picked ? "Remove from calendar picks" : "Pick for my calendar";
    btn.classList.toggle("picked", picked);
    updatePlanCount();
    updateCalReady();   // first pick enables the Print-calendar button
    syncMapPicks();
  }

  // the calendar needs picks or a selected venue to have a bounded scope;
  // reflect that on the Print-calendar button (called from render + on pick)
  function updateCalReady() {
    const $pt = document.getElementById("postertoggle");
    const ready = plan.size > 0 || !!venueFilter;
    $pt.disabled = !ready;
    $pt.title = ready ? "" :
      "Pick showtimes with + or select a venue in the map to build calendar";
    if (!ready && !document.getElementById("posterwrap").hidden) closePoster();
  }

  // repaint the map's pick rings without a full re-render (keeps scroll pos).
  // Always refresh __mapData.picks (so it's correct when the map opens later);
  // only push to the live map if it's actually loaded.
  function syncMapPicks() {
    if (!window.__mapData) return;
    const picks = {};
    for (const k of plan) {
      const s = showByKey.get(k);
      if (s) picks[s.venue_id] = (picks[s.venue_id] || 0) + 1;
    }
    window.__mapData.picks = picks;
    if (window.__updateMap) window.__updateMap(window.__mapData);
  }

  // the lightbox header's rolling rows: up to 3 SHORT currently-listed titles
  // (a real cinema sign only fits short ones), soonest first, deduped by film,
  // rendered "JUL 12 · VERTIGO · MUSIC BOX IL".
  // The lightbox header shows 3 SHORT titles picked at RANDOM from everything
  // screening across the whole site — independent of the current filter/venue,
  // like a real marquee that just advertises what's on. Built once at boot.
  function fillMarquee() {
    const box = document.getElementById("marquee-titles");
    if (!box) return;
    box.textContent = "";
    // one candidate per distinct film (soonest upcoming showing), short titles
    const now = Date.now();
    const byFilm = new Map();
    for (const s of shows) {
      if (new Date(s.start).getTime() < now) continue;   // upcoming only
      const info = filmInfo(s);
      const title = (info ? info.title : s.film_title) || "";
      if (title.length > 18) continue;                   // signs fit short titles
      // the sign can't wrap, so skip long venue names too (pick another one)
      if (s.venue.name.length > 20) continue;
      const gk = groupKey(s);
      const prev = byFilm.get(gk);
      if (!prev || s.start < prev.start) {
        byFilm.set(gk, { title, start: s.start, venue: s.venue });
      }
    }
    const pool = [...byFilm.values()];
    // shuffle (Fisher–Yates) and take 3
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    for (const p of pool.slice(0, 3)) {
      const tz = p.venue.tz;
      const date = fmtDayShort(dayKeyIn(p.start, tz)).replace(",", "")
        .split(" ").slice(1).join(" ");
      const row = el("div", "mq-title");
      row.appendChild(el("span", "mq-date", date.toUpperCase()));
      row.appendChild(el("span", "mq-film", p.title));
      row.appendChild(el("span", "mq-venue", `${p.venue.name} ${p.venue.state}`.toUpperCase()));
      box.appendChild(row);
    }
  }

  // one showtime — the marquee time IS the ticket link (big hour + raised
  // minutes + format, underlined). The sibling ☆/★ toggle picks it for the
  // printable calendar / .ics export.
  function chip(s, tz) {
    const a = el("a", "chip" + (s.sold_out ? " chip-soldout" : ""));
    a.href = s.ticket_url;
    a.rel = "noopener";
    const { h, m } = fmtTimeParts(s.start, tz);
    a.appendChild(el("span", "h", h));
    a.appendChild(el("span", "m", m));
    if (s.format) a.appendChild(el("span", "fmt", s.format));
    a.title = s.sold_out ? "Sold out" : fmtTime(s.start, tz);
    if (!posterCap()) return a; // picking disabled at 60d/all horizons
    const picked = plan.has(pickKey(s));
    const wrap = el("span", "chipwrap");
    wrap.appendChild(a);
    const b = el("button", "chip-add" + (picked ? " picked" : ""), picked ? "★" : "+");
    b.type = "button";
    b.title = picked ? "Remove from calendar picks" : "Pick for my calendar";
    b.addEventListener("click", (e) => {
      e.preventDefault();
      togglePick(s, b);
    });
    wrap.appendChild(b);
    return wrap;
  }

  function venueLabel(s) {
    let label = `${s.venue.name}, ${s.venue.city}`;
    if (s.miles !== undefined) label += ` — ${Math.round(s.miles)} mi`;
    return label;
  }

  // group a card's shows into lines; keyFn/labelFn decide the line grouping
  function chipLines(list, keyFn, labelFn, tzFn) {
    const lines = new Map();
    for (const s of [...list].sort((a, b) => a.start.localeCompare(b.start))) {
      const k = keyFn(s);
      if (!lines.has(k)) lines.set(k, { label: labelFn(s), shows: [] });
      lines.get(k).shows.push(s);
    }
    const box = el("div", "chiplines");
    for (const line of lines.values()) {
      const row = el("div", "chipline");
      row.appendChild(el("span", "chipline-label", line.label));
      const chips = el("span", "chips");
      for (const s of line.shows) chips.appendChild(chip(s, tzFn(s)));
      row.appendChild(chips);
      box.appendChild(row);
    }
    return box;
  }

  function card(list, mode, tzFn, fixed) {
    // without a fixed display zone, label each venue line with its zone
    const zone = (s) => (fixed ? "" : ` · ${tzAbbr(s.venue.tz, s.start)}`);
    const s0 = list[0];
    const info = filmInfo(s0);
    const div = el("article", "filmcard");
    div.appendChild(posterEl(info, info ? info.title : s0.film_title));

    const body = el("div", "filmcard-body");
    // title + runtime share the first line; runtime pins to the right edge
    const head = el("div", "film-head");
    const h3 = el("h3", null, info ? info.title : s0.film_title);
    const year = info ? info.year : s0.film_year;
    if (year) h3.appendChild(el("span", "year", ` (${year})`));
    div.classList.toggle("matched", !!info);
    head.appendChild(h3);
    if (info && info.runtime) {
      head.appendChild(el("div", "film-runtime", `${info.runtime} MIN`));
    }
    body.appendChild(head);

    // series tags, deduped; drop overly long ones (a full "post-film
    // discussion" title is a paragraph, not a tag)
    const series = [...new Set(list.map((s) => s.series).filter(Boolean))]
      .filter((sr) => sr.length <= 25);

    // film intro page per venue: detail_url when the chips go to checkout,
    // otherwise the venue's own film/event page (same target as the chips)
    const venuePages = [...new Map(
      list.map((s) => [s.venue_id, [s.detail_url || s.ticket_url, s.venue.name]])
    ).values()];
    const det = el("details", "about");
    det.appendChild(el("summary", null, "about this film"));
    if (info && info.overview) det.appendChild(el("p", null, truncate(info.overview, 550)));
    const m = el("p", "about-meta");
    const links = [];
    if (info) links.push([info.url, "TMDB ↗"]);
    for (const [url, name] of venuePages) links.push([url, `${name} ↗`]);
    links.forEach(([url, label], i) => {
      if (i) m.append(" · ");
      const a = el("a", null, label);
      a.href = url;
      a.rel = "noopener";
      m.appendChild(a);
    });
    det.appendChild(m);
    // ABOUT toggle + series tags share one line (the playground about-line)
    const aboutLine = el("div", "about-line");
    aboutLine.appendChild(det);
    if (series.length) {
      const tags = el("div", "tags");
      for (const sr of series) tags.appendChild(el("span", "badge", sr));
      aboutLine.appendChild(tags);
    }
    body.appendChild(aboutLine);

    if (mode === "day") {
      body.appendChild(chipLines(
        list, (s) => s.venue_id, (s) => venueLabel(s) + zone(s), tzFn));
    } else {
      body.appendChild(chipLines(
        list,
        (s) => dayKeyIn(s.start, tzFn(s)) + "|" + s.venue_id,
        (s) => `${fmtDayShort(dayKeyIn(s.start, tzFn(s)))} · ${venueLabel(s)}${zone(s)}`,
        tzFn
      ));
    }
    div.appendChild(body);
    return div;
  }

  // ---------- render ----------

  function render() {
    writeParams();
    const zipOk = /^\d{5}$/.test($zip.value);
    const origin = zipOk && zipTable ? zipTable[$zip.value] : null;
    let visible = shows;
    let statusBits = [];

    if (zipOk && !zipTable) {
      loadZips();
      return;
    }
    if (zipOk && zipTable && !origin) {
      statusBits.push(`<span class="warn">ZIP ${$zip.value} isn't in our Midwest lookup — showing everything.</span>`);
    }

    if ($days.value !== "all") {
      const horizon = new Date(Date.now() + +$days.value * 86400000);
      visible = shows.filter((s) => new Date(s.start) <= horizon);
    }

    let skippedNoCoords = 0;
    let nearestTz = null;
    if (origin) {
      const maxMi = +$radius.value;
      let best = Infinity;
      visible = visible.filter((s) => {
        if (s.venue.lat == null || s.venue.lng == null) { skippedNoCoords++; return false; }
        s.miles = haversineMiles(origin[0], origin[1], s.venue.lat, s.venue.lng);
        if (s.miles < best) { best = s.miles; nearestTz = s.venue.tz; }
        return s.miles <= maxMi;
      });
    } else {
      visible = visible.slice();
      visible.forEach((s) => delete s.miles);
    }

    // counts BEFORE the venue filter, so map pins reflect the area, not the pick
    const counts = {};
    const nextByVenue = {}; // venue_id -> soonest shows, for the pin popup
    for (const s of visible) {
      counts[s.venue_id] = (counts[s.venue_id] || 0) + 1;
      (nextByVenue[s.venue_id] = nextByVenue[s.venue_id] || []).push(s);
    }

    if (venueFilter) {
      visible = visible.filter((s) => s.venue_id === venueFilter);
    }

    // display timezone: auto = nearest venue's zone (with a ZIP) else venue-local
    const mode = $tz.value;
    const fixed = mode === "auto" ? nearestTz : FIXED_TZ[mode] || null;
    const tzFn = fixed ? () => fixed : (s) => s.venue.tz;

    lastVisible = visible; // the poster prints exactly what the list shows
    lastTzFn = tzFn;
    lastFixed = fixed;

    $listings.textContent = "";
    if ($view.value === "day") {
      const byDay = new Map();
      for (const s of visible) {
        const k = dayKeyIn(s.start, tzFn(s));
        if (!byDay.has(k)) byDay.set(k, []);
        byDay.get(k).push(s);
      }
      for (const [dayKey, dayShows] of [...byDay].sort()) {
        const sec = el("section", "day");
        sec.appendChild(el("h2", "dayhead", fmtDayHeading(dayKey)));
        const byFilm = new Map();
        for (const s of dayShows) {
          const k = groupKey(s);
          if (!byFilm.has(k)) byFilm.set(k, []);
          byFilm.get(k).push(s);
        }
        const cards = [...byFilm.values()].sort(
          (a, b) => a[0].start.localeCompare(b[0].start)
        );
        for (const list of cards) sec.appendChild(card(list, "day", tzFn, fixed));
        $listings.appendChild(sec);
      }
    } else {
      const byFilm = new Map();
      for (const s of visible) {
        const k = groupKey(s);
        if (!byFilm.has(k)) byFilm.set(k, []);
        byFilm.get(k).push(s);
      }
      const cards = [...byFilm.values()].map((list) => {
        const info = filmInfo(list[0]);
        return { title: (info ? info.title : list[0].film_title).toLowerCase(), list };
      }).sort((a, b) => a.title.localeCompare(b.title));
      const sec = el("section", "day");
      for (const c of cards) sec.appendChild(card(c.list, "film", tzFn, fixed));
      $listings.appendChild(sec);
    }

    if (!visible.length) {
      $listings.appendChild(el("p", "empty",
        "Nothing found. Try a wider radius — or no ZIP at all to see every venue we cover."));
    }

    const nVenues = new Set(visible.map((s) => s.venue_id)).size;
    statusBits.unshift(
      `${visible.length} showtimes at ${nVenues} venue${nVenues === 1 ? "" : "s"}` +
      ($days.value !== "all" ? ` in the next ${$days.value} days` : "") +
      (origin ? ` within ${$radius.value} mi of ${$zip.value}` : "")
    );
    if (venueFilter) {
      const vn = venues[venueFilter] ? venues[venueFilter].name : venueFilter;
      statusBits.splice(1, 0,
        `showing ${vn} only <a href="#" id="clearvenue">show all ✕</a>`);
    }
    if (fixed) {
      statusBits.push(`times shown in ${tzAbbr(fixed, new Date().toISOString())}`);
    }
    if (skippedNoCoords) {
      statusBits.push("some venues lack map coordinates yet and are hidden from radius search");
    }
    if (shows.length) {
      const newest = shows.reduce((m, s) => (s.source_scraped_at > m ? s.source_scraped_at : m), "");
      statusBits.push(`data updated ${timeAgo(newest)}`);
    }
    $status.innerHTML = statusBits.join(" · ");
    const clear = document.getElementById("clearvenue");
    if (clear) clear.addEventListener("click", (e) => {
      e.preventDefault();
      window.__setVenueFilter(null);
    });

    // next few screenings per venue (soonest first) for the pin popup, and
    // a per-venue count of calendar picks so pins can flag "you have plans here"
    const next = {};
    for (const vid in nextByVenue) {
      next[vid] = nextByVenue[vid]
        .sort((a, b) => new Date(a.start) - new Date(b.start))
        .slice(0, 3)
        .map((s) => {
          const info = filmInfo(s);
          return {
            // "Wed Jul 8" — full short date; a bare weekday is ambiguous
            // across a 7-day (or wider) window
            d: fmtDayShort(dayKeyIn(s.start, tzFn(s))).replace(",", ""),
            t: compactTime(s.start, tzFn(s)),
            title: info ? info.title : s.film_title,
          };
        });
    }
    const picks = {};
    for (const k of plan) {
      const s = showByKey.get(k);
      if (s) picks[s.venue_id] = (picks[s.venue_id] || 0) + 1;
    }

    // feed the map (initializes from window.__mapData when opened later)
    window.__mapData = {
      venues: venueList,
      counts,
      next,
      picks,
      venueFilter,
      origin: origin ? [origin[0], origin[1]] : null,
      radius: +$radius.value,
    };
    if (window.__updateMap) window.__updateMap(window.__mapData);

    // poster + picking only exist at 7/30-day horizons
    document.getElementById("postertoggle").hidden = !posterCap();
    if (!posterCap() && !document.getElementById("posterwrap").hidden) closePoster();
    updateCalReady(); // and it needs picks or a selected venue for its scope
    updatePlanCount();

    if (pendingPoster) { // ?poster=1 — open once the first render has data
      pendingPoster = false;
      openPoster();
    }
  }

  // ---------- map panel (lazy) ----------

  window.__setVenueFilter = (id) => {
    venueFilter = id;
    render();
  };

  let pendingOrigin = null; // click landed before zips.json finished loading

  // nearest ZIP centroid to an arbitrary point (linear scan; ~14k rows is
  // instant, and snapping to the centroid keeps the search model consistent
  // with the radius circle, which is drawn from the ZIP centroid)
  function nearestZip(lat, lng) {
    let best = null;
    let bestMi = Infinity;
    for (const zip in zipTable) {
      const c = zipTable[zip];
      const mi = haversineMiles(lat, lng, c[0], c[1]);
      if (mi < bestMi) { bestMi = mi; best = zip; }
    }
    return best;
  }

  // map click on empty ground -> search from the nearest ZIP centroid
  window.__setOrigin = (lat, lng) => {
    if (!zipTable) { // zips still loading: resolve once they arrive
      pendingOrigin = [lat, lng];
      loadZips();
      return;
    }
    const zip = nearestZip(lat, lng);
    if (zip) {
      $zip.value = zip;
      render();
    }
  };

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function openMap() {
    const $map = document.getElementById("map");
    const $toggle = document.getElementById("maptoggle");
    $map.hidden = false;
    $toggle.textContent = "Hide map ▴";
    document.getElementById("maphint").hidden = false;
    loadZips(); // prefetch centroids so click-to-search is instant
    if (mapLoaded) return;
    mapLoaded = true;
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "./vendor/maplibre-gl.css";
    // insert BEFORE our own stylesheet so our popup theming wins the cascade
    // (equal specificity — otherwise MapLibre's white popup rules, appended
    // later, would override ours)
    const site = document.getElementById("sitecss");
    if (site) site.parentNode.insertBefore(css, site);
    else document.head.appendChild(css);
    loadScript("./vendor/maplibre-gl.js")
      .then(() => loadScript("./vendor/pmtiles.js"))
      .then(() => loadScript("./map.js"))
      .catch(() => {
        $map.textContent = "Couldn't load the map.";
      });
  }

  document.getElementById("maptoggle").addEventListener("click", () => {
    const $map = document.getElementById("map");
    if ($map.hidden) openMap();
    else {
      $map.hidden = true;
      document.getElementById("maphint").hidden = true;
      document.getElementById("maptoggle").textContent = "Show map ▾";
    }
    writeParams();
  });

  // ---------- printable calendar poster ----------
  // One letter-landscape page per month, in the classic repertory-house
  // calendar-grid style. Prints exactly what the current filters show.

  const P_STOP = new Set(["the", "of", "and", "at", "cinema", "cinemas",
    "theatre", "theater", "film", "films", "center", "centre"]);

  // deterministic short venue codes (MB, GS, KK…) for day-cell time lines
  function venueCodes(ids) {
    const codes = {};
    const used = new Set();
    const order = ids.map((id) => venues[id])
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const v of order) {
      const words = v.name.split(/[\s\-–—]+/).filter((w) => /[a-z0-9]/i.test(w));
      let sig = words.filter((w) => !P_STOP.has(w.toLowerCase()));
      if (!sig.length) sig = words; // "Cinema Center" is all stopwords
      let code = (sig.length >= 2
        ? sig.slice(0, 3).map((w) => w[0]).join("")
        : sig[0].slice(0, 2)
      ).toUpperCase();
      if (used.has(code)) code = sig[0].slice(0, 3).toUpperCase();
      const base = code;
      for (let n = 2; used.has(code); n++) code = base + n;
      used.add(code);
      codes[v.id] = code;
    }
    return { codes, order };
  }

  function compactTime(iso, tz) {
    // "7:00 PM" -> "7:00p" (newer ICU uses U+202F before AM/PM; \s covers it)
    return fmtTime(iso, tz).replace(/\s*([AP])M$/i,
      (m, p) => (p.toUpperCase() === "A" ? "a" : "p"));
  }

  // one film's line in a day cell: Title (Year) FMT  [CODE] 7:00p 9:15p
  // (with `thumb`, the week strip adds a wide B&W backdrop still above the
  // text — films without a backdrop just show text; hidden by the B&W toggle)
  function posterEntry(list, tzFn, codes, thumb) {
    const s0 = list[0];
    const info = filmInfo(s0);
    const div = el("div", "pentry" + (thumb ? " pentry-t" : ""));
    let body = div;
    if (thumb) {
      const title0 = info ? info.title : s0.film_title;
      if (info && info.backdrop_path) {
        const img = el("img", "pthumb");
        img.src = BACKDROP_BASE + info.backdrop_path;
        img.alt = "";
        div.appendChild(img);
      } else {
        // no backdrop art — a letter tile in the same strip, like the poster
        div.appendChild(el("span", "pthumb pthumb-blank",
          (title0[0] || "?").toUpperCase()));
      }
      body = el("div", "pentry-txt");
      div.appendChild(body);
    }
    body.appendChild(el("span", "ptitle", info ? info.title : s0.film_title));
    const year = info ? info.year : s0.film_year;
    if (year) body.appendChild(el("span", "pyear", ` (${year})`));
    const oneFmt = s0.format && list.every((s) => s.format === s0.format);
    if (oneFmt) body.appendChild(el("span", "pfmt", ` ${s0.format}`));
    const times = el("span", "ptimes", " ");
    const byVenue = new Map();
    for (const s of list) {
      if (!byVenue.has(s.venue_id)) byVenue.set(s.venue_id, []);
      byVenue.get(s.venue_id).push(s);
    }
    let vi = 0;
    for (const [vid, ss] of byVenue) {
      if (vi++) times.append(" · ");
      if (codes) {
        times.appendChild(el("b", "pvcode", codes[vid]));
        times.append(" ");
      }
      ss.forEach((s, i) => {
        if (i) times.append(" ");
        const t = el(s.sold_out ? "s" : "span", null, compactTime(s.start, tzFn(s)));
        if (!oneFmt && s.format) t.textContent += ` (${s.format})`;
        times.appendChild(t);
      });
    }
    body.appendChild(times);
    return div;
  }

  // "+N more" note shown when a cell is capped to fit a page
  function moreLine(n) {
    return el("div", "pentry pmore", `+${n} more`);
  }

  // render a cell's entries capped to `limit` groups (Infinity = all); the
  // fitter re-renders overpacked cells with a smaller limit until the month
  // fits one page. Groups/tzFn/codes ride on the cell so we can re-render.
  function fillCell(cell, limit) {
    const { groups, tzFn, codes, thumb } = cell._pdata;
    while (cell.childNodes.length > 1) cell.removeChild(cell.lastChild); // keep daynum/header
    const shown = Math.min(limit, groups.length);
    for (let i = 0; i < shown; i++) {
      cell.appendChild(posterEntry(groups[i], tzFn, codes, thumb));
    }
    // the "+N more" note sits in its own trailing row (week strip) / after the
    // films (month grid); it doesn't consume a film slot
    if (shown < groups.length) cell.appendChild(moreLine(groups.length - shown));
    cell._limit = limit;
  }

  // sort a day's shows into per-film groups, earliest first
  function dayGroups(dayShows) {
    const byFilm = new Map();
    for (const s of dayShows) {
      const k = groupKey(s);
      if (!byFilm.has(k)) byFilm.set(k, []);
      byFilm.get(k).push(s);
    }
    const groups = [...byFilm.values()];
    for (const g of groups) g.sort((a, b) => new Date(a.start) - new Date(b.start));
    groups.sort((a, b) => new Date(a[0].start) - new Date(b[0].start));
    return groups;
  }

  function posterCell(mk, d, byDay, tzFn, codes, startKey, endKey, cap) {
    const cell = el("div", "pp-cell");
    if (d === null) return cell.classList.add("pp-out"), cell;
    const key = `${mk}-${String(d).padStart(2, "0")}`;
    // outside the covered window: tint, so blank != "nothing playing"
    if (key < startKey || key > endKey) cell.classList.add("pp-out");
    cell.appendChild(el("div", "pp-daynum", String(d)));
    const dayShows = byDay.get(key);
    if (!dayShows) {
      cell.classList.add("pp-empty");
      return cell;
    }
    cell._pdata = { groups: dayGroups(dayShows), tzFn, codes };
    fillCell(cell, cap || Infinity);
    return cell;
  }

  // Shrink the densest cells until the month's week-grid fits one landscape
  // letter page. We measure the .pp-weeks grid (real font metrics in the live
  // DOM) against the height left after the header/day-row/footer, rather than
  // the whole .poster-page — the screen page carries a min-height and padding
  // that differ from print, but the grid content height is what actually
  // overflows. Trims the tallest cell first, so ink is dropped where it's
  // densest, not uniformly. Must run while the page is attached & visible.
  function fitMonth(art) {
    // The on-screen .poster-page is sized to the exact print content box and
    // clips overflow, and .pp-weeks is its own clipped flex slot — so
    // scrollHeight > clientHeight on .pp-weeks means "this would spill a print
    // page." Trim the tallest cell one film at a time until it no longer does.
    const weeks = art.querySelector(".pp-weeks");
    if (!weeks) return; // week-strip pages are capped by construction
    // NOTE: strict > is the correct test. scrollHeight is floored at
    // clientHeight, so "scrollHeight > clientHeight - slack" is a tautology
    // that would floor-trim every cell. When content fits (flex-stretched),
    // scrollHeight === clientHeight exactly.
    const overflowing = () => weeks.scrollHeight > weeks.clientHeight;
    const cells = [...art.querySelectorAll(".pp-cell")].filter((c) => c._pdata);
    let guard = 0;
    while (overflowing() && guard++ < 600) {
      let victim = null;
      let tallest = -1;
      for (const c of cells) {
        const lim = c._limit === Infinity ? c._pdata.groups.length : c._limit;
        if (lim <= 1) continue; // never cap below one film + the "+N more" line
        const h = c.getBoundingClientRect().height;
        if (h > tallest) { tallest = h; victim = c; }
      }
      if (!victim) break; // everything already at the floor
      const cur = victim._limit === Infinity
        ? victim._pdata.groups.length : victim._limit;
      fillCell(victim, cur - 1);
    }
  }

  function posterHead(title, scope) {
    const head = el("header", "pp-head");
    head.appendChild(el("div", "pp-month", title));
    head.appendChild(el("div", "pp-brand", "Heartland Cinemap"));
    head.appendChild(el("div", "pp-scope", scope));
    return head;
  }

  function posterFoot(codes, order) {
    const foot = el("footer", "pp-foot");
    if (codes) {
      // names only — cities would clip the single-line legend past ~3 venues
      const lg = el("span", "pp-legend");
      order.forEach((v, i) => {
        if (i) lg.append("  ·  ");
        lg.appendChild(el("b", null, codes[v.id]));
        lg.append(` ${v.name}`);
      });
      foot.appendChild(lg);
    }
    foot.appendChild(el("span", null,
      "Showtimes change — confirm with the cinema · " +
      "heartlandcinemap.org · printed " +
      new Date().toLocaleDateString("en-US",
        { month: "short", day: "numeric", year: "numeric" })));
    return foot;
  }

  function posterMonth(mk, byDay, tzFn, codes, order, scope, startKey, endKey, cap) {
    const [Y, M] = mk.split("-").map(Number);
    const first = new Date(Y, M - 1, 1, 12);
    const art = el("article", "poster-page");
    art.appendChild(posterHead(
      first.toLocaleDateString("en-US", { month: "long", year: "numeric" }), scope));

    const dow = el("div", "pp-dow");
    for (const d of ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
      "Friday", "Saturday"]) dow.appendChild(el("span", null, d));
    art.appendChild(dow);

    const weeks = el("div", "pp-weeks");
    const cells = [];
    for (let i = 0; i < first.getDay(); i++) cells.push(null);
    const nDays = new Date(Y, M, 0).getDate();
    for (let d = 1; d <= nDays; d++) cells.push(d);
    while (cells.length % 7) cells.push(null);
    for (let w = 0; w < cells.length; w += 7) {
      const week = el("div", "pp-week");
      for (const d of cells.slice(w, w + 7)) {
        week.appendChild(posterCell(mk, d, byDay, tzFn, codes, startKey, endKey, cap));
      }
      weeks.appendChild(week);
    }
    art.appendChild(weeks);
    art.appendChild(posterFoot(codes, order));
    return art;
  }

  // 7-day layout: one page, one column per date — a "this week" strip.
  // Thumbs always rendered; B&W mode hides them with CSS.
  function buildWeekStrip(title, byDay, tzFn, codes, order, scope, startKey, endKey) {
    const art = el("article", "poster-page pp-week-strip");
    art.appendChild(posterHead(title, scope));

    const days = [];
    let d = new Date(startKey + "T12:00:00");
    const end = new Date(endKey + "T12:00:00");
    while (d <= end && days.length < 9) {
      days.push(d.toLocaleDateString("en-CA"));
      d = new Date(d.getTime() + 86400000);
    }
    // the rolling 168h window can touch an 8th date; only keep it if it has
    // something to show
    while (days.length > 7 && !byDay.has(days[days.length - 1])) days.pop();

    const row = el("div", "pp-daycols");
    row.style.gridTemplateColumns = `repeat(${days.length}, 1fr)`;
    for (const key of days) {
      const col = el("div", "pp-daycol");
      const dt = new Date(key + "T12:00:00");
      const hd = el("div", "pp-dayhead");
      hd.appendChild(el("span", "pp-dayhead-dow",
        dt.toLocaleDateString("en-US", { weekday: "short" }).toUpperCase()));
      hd.append(" " + dt.toLocaleDateString("en-US", { month: "short", day: "numeric" }));
      col.appendChild(hd);
      const dayShows = byDay.get(key);
      if (dayShows) {
        col._pdata = { groups: dayGroups(dayShows), tzFn, codes, thumb: true, slots: 5 };
        fillCell(col, 5);
      }
      row.appendChild(col);
    }
    art.appendChild(row);
    art.appendChild(posterFoot(codes, order));
    return art;
  }

  function buildPoster() {
    const pages = document.getElementById("posterpages");
    const infoEl = document.getElementById("posterinfo");
    pages.textContent = "";
    document.getElementById("postercal").disabled = !plan.size;
    const cap = posterCap();
    if (!cap) return; // 60d/all: poster disabled (button hidden anyway)
    const tzFn = lastTzFn || ((s) => s.venue.tz);

    // picks-only when anything is picked; otherwise auto-fill to the cap
    const horizon = new Date(Date.now() + +$days.value * 86400000);
    const picked = planShows().filter((s) => new Date(s.start) <= horizon);
    const usePicks = picked.length > 0;
    const source = usePicks ? picked : lastVisible;

    if (!source.length) {
      pages.appendChild(el("p", "poster-empty",
        "Nothing to print — the current filters match no showtimes."));
      infoEl.textContent = "Calendar poster · 0 pages";
      return;
    }

    const byDay = new Map();
    for (const s of source) {
      const k = dayKeyIn(s.start, tzFn(s));
      if (!byDay.has(k)) byDay.set(k, []);
      byDay.get(k).push(s);
    }

    const venueIds = [...new Set(source.map((s) => s.venue_id))];
    const multi = venueIds.length > 1;
    const { codes, order } = venueCodes(venueIds);

    // covered window: today through the "When" horizon
    const localKey = (d) => d.toLocaleDateString("en-CA");
    const startKey = localKey(new Date());
    const endKey = localKey(horizon);

    const fmtMD = (k) => new Date(k + "T12:00:00")
      .toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const range = `${fmtMD(startKey)} – ${fmtMD(endKey)}`;
    const scopeBits = [];
    if (!multi) {
      const v = venues[venueIds[0]];
      scopeBits.push(`${v.name}, ${v.city}`);
    }
    if (usePicks) {
      scopeBits.push("my picks");
    } else if (/^\d{5}$/.test($zip.value) && zipTable && zipTable[$zip.value]) {
      scopeBits.push(`within ${$radius.value} mi of ${$zip.value}`);
    }
    scopeBits.push(lastFixed
      ? `times in ${tzAbbr(lastFixed, new Date().toISOString())}`
      : "venue local times");

    let nPages;
    if ($days.value === "7") {
      // week strip: the title carries the date range, scope carries the rest
      pages.appendChild(buildWeekStrip(range, byDay, tzFn,
        multi ? codes : null, order, scopeBits.join(" · "), startKey, endKey));
      nPages = 1;
    } else {
      const scope = [range, ...scopeBits].join(" · ");
      const dayKeys = [...byDay.keys()].sort();
      const months = [...new Set(dayKeys.map((k) => k.slice(0, 7)))].sort();
      for (const mk of months) {
        const art = posterMonth(mk, byDay, tzFn,
          multi ? codes : null, order, scope, startKey, endKey, cap);
        pages.appendChild(art); // attach first — fitMonth measures live DOM
        fitMonth(art);          // safety net if a capped month still overflows
      }
      nPages = months.length;
    }
    const scopeLabel = usePicks
      ? `your ${picked.length} pick${picked.length === 1 ? "" : "s"}`
      : (!multi && venues[venueIds[0]])
        ? `everything at ${venues[venueIds[0]].name}`
        : "pick showtimes with + to make your own";
    infoEl.textContent = "Calendar poster · " + scopeLabel +
      ` · ${nPages} page${nPages === 1 ? "" : "s"}`;
  }

  function openPoster() {
    if (!posterCap()) return; // 60d/all horizons: no poster
    // needs picks or a selected venue (button is disabled otherwise); this also
    // makes ?poster=1 a no-op until the scope is defined
    if (document.getElementById("postertoggle").disabled) return;
    // unhide BEFORE building: fitMonth measures rendered heights, and a
    // hidden container reports zero, so trimming must happen while visible
    document.getElementById("posterwrap").hidden = false;
    document.body.classList.add("postering");
    buildPoster();
    writeParams();
  }

  function closePoster() {
    document.getElementById("posterwrap").hidden = true;
    document.body.classList.remove("postering");
    writeParams();
  }

  // ----- .ics export: hand people their picks in Apple Calendar / Outlook /
  // Google Calendar. All times in UTC (avoids shipping VTIMEZONE blocks);
  // the calendar app renders them in the user's zone.
  function icsEscape(t) {
    return String(t).replace(/\\/g, "\\\\").replace(/;/g, "\\;")
      .replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
  }

  function icsDate(iso) {
    return new Date(iso).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  }

  function icsFold(line) {
    // RFC 5545: content lines fold at 75 octets; continuation starts with a space
    const out = [];
    while (line.length > 73) {
      out.push(line.slice(0, 73));
      line = " " + line.slice(73);
    }
    out.push(line);
    return out.join("\r\n");
  }

  function buildICS(list) {
    const L = ["BEGIN:VCALENDAR", "VERSION:2.0",
      "PRODID:-//Heartland Cinemap//heartlandcinemap//EN",
      "CALSCALE:GREGORIAN", "METHOD:PUBLISH"];
    const stamp = icsDate(new Date().toISOString());
    for (const s of list) {
      const info = filmInfo(s);
      const v = s.venue;
      const run = (info && info.runtime) || 120; // unknown runtime: block 2h
      const end = new Date(new Date(s.start).getTime() + run * 60000).toISOString();
      const title = (info ? info.title : s.film_title) +
        (s.format ? ` (${s.format})` : "");
      L.push("BEGIN:VEVENT",
        `UID:${s.venue_id}-${Date.parse(s.start)}@heartlandcinemap`,
        `DTSTAMP:${stamp}`,
        `DTSTART:${icsDate(s.start)}`,
        `DTEND:${icsDate(end)}`,
        icsFold(`SUMMARY:${icsEscape(title + " — " + v.name)}`),
        icsFold("LOCATION:" + icsEscape(
          [v.name, v.address, v.city, v.state + (v.zip ? " " + v.zip : "")]
            .filter(Boolean).join(", "))),
        icsFold(`DESCRIPTION:${icsEscape("Tickets: " + s.ticket_url + "\nvia Heartland Cinemap")}`),
        icsFold(`URL:${icsEscape(s.ticket_url)}`),
        "END:VEVENT");
    }
    L.push("END:VCALENDAR");
    return L.join("\r\n") + "\r\n";
  }

  document.getElementById("postercal").addEventListener("click", () => {
    const list = planShows();
    if (!list.length) return;
    const blob = new Blob([buildICS(list)], { type: "text/calendar" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "heartland-cinemap.ics";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  });

  document.getElementById("postertoggle").addEventListener("click", openPoster);
  document.getElementById("posterclose").addEventListener("click", closePoster);
  document.getElementById("posterprint").addEventListener("click", () => window.print());
  document.getElementById("posterbw").addEventListener("click", () => {
    const bw = document.getElementById("poster").classList.toggle("bw");
    document.getElementById("posterbw").textContent =
      bw ? "Switch to color" : "Switch to B&W";
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("posterwrap").hidden) {
      closePoster();
    }
  });

  // ---------- data loading ----------

  function loadZips() {
    if (zipFetch) return;
    $status.textContent = "Loading ZIP locations…";
    zipFetch = fetch("./zips.json")
      .then((r) => r.json())
      .then((t) => {
        zipTable = t;
        if (pendingOrigin) { // a map click is waiting on the centroids
          const [lat, lng] = pendingOrigin;
          pendingOrigin = null;
          const zip = nearestZip(lat, lng);
          if (zip) $zip.value = zip;
        }
        render();
      })
      .catch(() => {
        zipFetch = null;
        $status.innerHTML = '<span class="warn">Couldn\'t load ZIP data — distance filtering unavailable.</span>';
      });
  }

  Promise.all([
    fetch("./venues.json").then((r) => r.json()),
    fetch("./showtimes.json").then((r) => r.json()),
    fetch("./films.json").then((r) => r.json()).catch(() => ({})), // optional
  ])
    .then(([vlist, slist, flist]) => {
      films = flist;
      venueList = vlist;
      for (const v of vlist) venues[v.id] = v;
      const cutoff = new Date(Date.now() - 30 * 60000); // 30-min grace
      shows = slist.filter(
        (s) => venues[s.venue_id] && new Date(s.start) >= cutoff
      );
      shows.forEach((s) => (s.venue = venues[s.venue_id]));
      // resolve saved picks; prune ones whose showtime left the data
      for (const s of shows) showByKey.set(pickKey(s), s);
      try {
        for (const k of JSON.parse(localStorage.getItem(PLAN_LS) || "[]")) {
          if (showByKey.has(k)) plan.add(k);
        }
      } catch (e) { /* corrupt storage: start fresh */ }
      savePlan();
      fillMarquee();   // once, from the whole dataset — not filter-dependent
      readParams();
      render();
    })
    .catch(() => {
      $status.innerHTML = '<span class="warn">Couldn\'t load showtime data. Please try again later.</span>';
    });

  $zip.addEventListener("input", render);
  $radius.addEventListener("change", render);
  $days.addEventListener("change", render);
  $view.addEventListener("change", render);
  $tz.addEventListener("change", render);
  document.getElementById("controls").addEventListener("submit", (e) => e.preventDefault());
})();
