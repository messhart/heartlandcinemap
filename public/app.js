/* Heartland Cinemap — all filtering/sorting happens right here in the browser.
   Data: venues.json (registry), showtimes.json (scraper output),
   zips.json (Census ZIP centroids, loaded lazily on first ZIP entry). */
"use strict";

(function () {
  const $zip = document.getElementById("zip");
  const $radius = document.getElementById("radius");
  const $days = document.getElementById("days");
  const $sort = document.getElementById("sort");
  const $status = document.getElementById("status");
  const $listings = document.getElementById("listings");

  let venues = {};      // id -> venue
  let shows = [];       // future showtimes, joined with venue
  let films = {};       // filmKey -> TMDb info (from films.json)
  let zipTable = null;  // zip -> [lat, lng]; null until fetched
  let zipFetch = null;

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

  // "2026-07-09T19:30:00-04:00" — the wall-clock part IS the venue-local
  // time, so we format from the string instead of letting Date convert it
  // to the viewer's timezone.
  function localParts(iso) {
    return { date: iso.slice(0, 10), h: +iso.slice(11, 13), m: iso.slice(14, 16) };
  }

  function fmtTime(iso) {
    const { h, m } = localParts(iso);
    const h12 = h % 12 || 12;
    return `${h12}:${m} ${h < 12 ? "AM" : "PM"}`;
  }

  function fmtDate(dateStr) {
    // parse as local noon to dodge UTC-midnight off-by-one-day issues
    const d = new Date(dateStr + "T12:00:00");
    return d.toLocaleDateString("en-US", {
      weekday: "long", month: "long", day: "numeric",
    });
  }

  const tzAbbrCache = {};
  function tzAbbr(tz, iso) {
    if (!tz) return "";
    const key = tz + iso.slice(0, 10);
    if (!(key in tzAbbrCache)) {
      try {
        tzAbbrCache[key] = new Intl.DateTimeFormat("en-US", {
          timeZone: tz, timeZoneName: "short",
        })
          .formatToParts(new Date(iso))
          .find((p) => p.type === "timeZoneName").value;
      } catch (e) {
        tzAbbrCache[key] = "";
      }
    }
    return tzAbbrCache[key];
  }

  // must mirror scraper/enrich.py film_key()
  function filmKey(s) {
    return s.film_title.replace(/\s+/g, " ").trim().toLowerCase() +
      "|" + (s.film_year || "");
  }

  function truncate(text, max) {
    if (text.length <= max) return text;
    const cut = text.slice(0, max);
    return cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 30)) + "…";
  }

  function fmtRuntime(mins) {
    return mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
  }

  // <details> block with the TMDb synopsis; null when we have nothing to say
  function aboutBlock(s) {
    const info = films[filmKey(s)];
    if (!info || info.miss || !info.overview) return null;
    const det = el("details", "about");
    det.appendChild(el("summary", null, "about this film"));
    det.appendChild(el("p", null, truncate(info.overview, 550)));
    const meta = el("p", "about-meta");
    const bits = [];
    if (info.year) bits.push(String(info.year));
    if (info.runtime) bits.push(fmtRuntime(info.runtime));
    meta.append(bits.join(" · ") + (bits.length ? " · " : ""));
    const a = el("a", null, "TMDB ↗");
    a.href = info.url;
    a.rel = "noopener";
    meta.appendChild(a);
    det.appendChild(meta);
    return det;
  }

  function timeAgo(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 90) return `${mins} min ago`;
    if (mins < 36 * 60) return `${Math.round(mins / 60)} h ago`;
    return `${Math.round(mins / 1440)} days ago`;
  }

  // ---------- URL state ----------

  function readParams() {
    const p = new URLSearchParams(location.search);
    if (/^\d{5}$/.test(p.get("zip") || "")) $zip.value = p.get("zip");
    if (["50", "100", "200"].includes(p.get("mi"))) $radius.value = p.get("mi");
    if (["7", "30", "60", "all"].includes(p.get("d"))) $days.value = p.get("d");
    if (["time", "distance", "title"].includes(p.get("sort"))) $sort.value = p.get("sort");
  }

  function writeParams() {
    const p = new URLSearchParams();
    if (/^\d{5}$/.test($zip.value)) p.set("zip", $zip.value);
    if ($radius.value !== "100") p.set("mi", $radius.value);
    if ($days.value !== "7") p.set("d", $days.value);
    if ($sort.value !== "time") p.set("sort", $sort.value);
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  // ---------- rendering ----------

  function showRow(s, withDate, withAbout) {
    const li = el("li");
    const when = withDate
      ? `${fmtDate(localParts(s.start).date).replace(/^(\w{3})\w*/, "$1")} · ${fmtTime(s.start)}`
      : fmtTime(s.start);
    li.appendChild(el("span", "when", `${when} ${tzAbbr(s.venue.tz, s.start)}`));

    const film = el("span", "film", s.film_title);
    if (s.film_year) film.appendChild(el("span", "year", ` (${s.film_year})`));
    li.appendChild(film);

    if (s.series) li.appendChild(el("span", "badge", s.series));
    if (s.format) li.appendChild(el("span", "badge", s.format));
    if (s.sold_out) li.appendChild(el("span", "badge soldout", "sold out"));

    let where = `${s.venue.name}, ${s.venue.city}`;
    if (s.miles !== undefined) where += ` — ${Math.round(s.miles)} mi`;
    li.appendChild(el("span", "where", where));

    const a = el("a", "tickets", "tickets / info ↗");
    a.href = s.ticket_url;
    a.rel = "noopener";
    li.appendChild(a);

    if (withAbout) {
      const about = aboutBlock(s);
      if (about) li.appendChild(about);
    }
    return li;
  }

  function renderGroups(groups, withDate, rowAbout) {
    $listings.textContent = "";
    for (const g of groups) {
      const sec = el("section", "group");
      const h2 = el("h2", null, g.title + " ");
      if (g.sub) h2.appendChild(el("span", "sub", g.sub));
      sec.appendChild(h2);
      if (g.about) sec.appendChild(g.about); // film-mode: one synopsis per group
      const ul = el("ul", "shows");
      for (const s of g.shows) ul.appendChild(showRow(s, withDate, rowAbout));
      sec.appendChild(ul);
      $listings.appendChild(sec);
    }
    if (!groups.length) {
      $listings.appendChild(
        el("p", "empty",
          "Nothing found. Try a wider radius — or no ZIP at all to see every venue we cover.")
      );
    }
  }

  function render() {
    writeParams();
    const zipOk = /^\d{5}$/.test($zip.value);
    const origin = zipOk && zipTable ? zipTable[$zip.value] : null;
    let visible = shows;
    let statusBits = [];

    if (zipOk && !zipTable) {
      loadZips();
      return; // re-rendered when the table arrives
    }
    if (zipOk && zipTable && !origin) {
      statusBits.push(`<span class="warn">ZIP ${$zip.value} isn't in our Midwest lookup — showing everything.</span>`);
    }

    // global date-range filter, applied before any arrangement
    if ($days.value !== "all") {
      const horizon = new Date(Date.now() + +$days.value * 86400000);
      visible = shows.filter((s) => new Date(s.start) <= horizon);
    }

    let skippedNoCoords = 0;
    if (origin) {
      const maxMi = +$radius.value;
      visible = visible.filter((s) => {
        if (s.venue.lat == null || s.venue.lng == null) { skippedNoCoords++; return false; }
        s.miles = haversineMiles(origin[0], origin[1], s.venue.lat, s.venue.lng);
        return s.miles <= maxMi;
      });
    } else {
      visible = visible.slice();
      visible.forEach((s) => delete s.miles);
    }

    const mode = $sort.value;
    let groups = [];
    const byStart = (a, b) => a.start.localeCompare(b.start);

    if (mode === "time") {
      const days = new Map();
      for (const s of [...visible].sort(byStart)) {
        const d = localParts(s.start).date;
        if (!days.has(d)) days.set(d, []);
        days.get(d).push(s);
      }
      groups = [...days].map(([d, list]) => ({ title: fmtDate(d), shows: list }));
    } else if (mode === "title") {
      // group by TMDb identity when we have it, so venues' variant titles
      // ("CatVideoFest" vs "CatVideoFest 2026") merge into one film
      const byFilm = new Map();
      for (const s of visible) {
        const info = films[filmKey(s)];
        const key = info && !info.miss && info.url
          ? info.url
          : s.film_title + " " + (s.film_year || "");
        if (!byFilm.has(key)) byFilm.set(key, []);
        byFilm.get(key).push(s);
      }
      groups = [...byFilm.values()]
        .map((list) => {
          const info = films[filmKey(list[0])];
          const matched = info && !info.miss;
          let title = matched ? info.title : list[0].film_title;
          const year = matched ? info.year : list[0].film_year;
          if (year && !title.endsWith(String(year))) title += ` (${year})`;
          return {
            title,
            sub: `${list.length} showing${list.length > 1 ? "s" : ""}`,
            about: aboutBlock(list[0]),
            shows: list.sort(byStart),
          };
        })
        .sort((a, b) => a.title.localeCompare(b.title));
    } else {
      // distance: group by venue, nearest first (alphabetical without a ZIP)
      const vs = new Map();
      for (const s of visible) {
        if (!vs.has(s.venue_id)) vs.set(s.venue_id, []);
        vs.get(s.venue_id).push(s);
      }
      groups = [...vs.values()]
        .map((list) => ({
          title: `${list[0].venue.name} — ${list[0].venue.city}, ${list[0].venue.state}`,
          sub: list[0].miles !== undefined ? `${Math.round(list[0].miles)} mi` : "",
          miles: list[0].miles ?? Infinity,
          shows: list.sort(byStart),
        }))
        .sort((a, b) => a.miles - b.miles || a.title.localeCompare(b.title));
    }

    renderGroups(groups, mode !== "time", mode !== "title");

    const nVenues = new Set(visible.map((s) => s.venue_id)).size;
    statusBits.unshift(
      `${visible.length} showtimes at ${nVenues} venue${nVenues === 1 ? "" : "s"}` +
      ($days.value !== "all" ? ` in the next ${$days.value} days` : "") +
      (origin ? ` within ${$radius.value} mi of ${$zip.value}` : "")
    );
    if (skippedNoCoords) {
      statusBits.push(`some venues lack map coordinates yet and are hidden from radius search`);
    }
    if (shows.length) {
      const newest = shows.reduce((m, s) => (s.source_scraped_at > m ? s.source_scraped_at : m), "");
      statusBits.push(`data updated ${timeAgo(newest)}`);
    }
    $status.innerHTML = statusBits.join(" · ");
  }

  // ---------- data loading ----------

  function loadZips() {
    if (zipFetch) return;
    $status.textContent = "Loading ZIP locations…";
    zipFetch = fetch("./zips.json")
      .then((r) => r.json())
      .then((t) => { zipTable = t; render(); })
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
      for (const v of vlist) venues[v.id] = v;
      const cutoff = new Date(Date.now() - 30 * 60000); // 30-min grace
      shows = slist.filter(
        (s) => venues[s.venue_id] && new Date(s.start) >= cutoff
      );
      shows.forEach((s) => (s.venue = venues[s.venue_id]));
      readParams();
      render();
    })
    .catch(() => {
      $status.innerHTML = '<span class="warn">Couldn\'t load showtime data. Please try again later.</span>';
    });

  $zip.addEventListener("input", render);
  $radius.addEventListener("change", render);
  $days.addEventListener("change", render);
  $sort.addEventListener("change", render);
  document.getElementById("controls").addEventListener("submit", (e) => e.preventDefault());
})();
