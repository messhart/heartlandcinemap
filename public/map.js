/* Heartland Cinemap — map panel (MapLibre GL + Protomaps pmtiles).
   Loaded lazily by app.js when the user opens the map. The basemap is a
   self-hosted z0-9 Midwest extract (basemap/midwest.pmtiles) rendered with
   a deliberately quiet hand-rolled style so the venue pins carry the map.
   Venue pin click <-> the card list's venue filter (app.js owns state). */
"use strict";

(function () {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const C = dark ? {
    earth: "#211f1d", water: "#141d24", green: "#232620",
    road: "#38342e", highway: "#4a443a", boundary: "#4a443a",
    label: "#a39c93", halo: "#191817",
    pin: "#e0888b", pinRing: "#191817", pinOff: "#6b6560",
  } : {
    earth: "#f2efe7", water: "#c3d5e2", green: "#e6eadb",
    road: "#ded7ca", highway: "#c9bfae", boundary: "#b5aa99",
    label: "#6b6560", halo: "#faf9f6",
    pin: "#8a3033", pinRing: "#faf9f6", pinOff: "#8f8a82",
  };

  const style = {
    version: 8,
    // string concat, not new URL(): the {tokens} must survive un-encoded
    glyphs: new URL(".", location.href).href + "vendor/fonts/{fontstack}/{range}.pbf",
    sources: {
      basemap: {
        type: "vector",
        url: "pmtiles://" + new URL("./basemap/midwest.pmtiles", location.href).href,
        attribution: "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a>, <a href='https://protomaps.com'>Protomaps</a>",
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": C.earth } },
      { id: "earth", type: "fill", source: "basemap", "source-layer": "earth",
        paint: { "fill-color": C.earth } },
      { id: "landcover", type: "fill", source: "basemap", "source-layer": "landcover",
        filter: ["in", ["get", "kind"], ["literal", ["forest", "wood", "grassland", "scrub"]]],
        paint: { "fill-color": C.green, "fill-opacity": 0.6 } },
      { id: "water", type: "fill", source: "basemap", "source-layer": "water",
        paint: { "fill-color": C.water } },
      { id: "roads-minor", type: "line", source: "basemap", "source-layer": "roads",
        minzoom: 8, filter: ["in", ["get", "kind"], ["literal", ["major_road", "medium_road"]]],
        paint: { "line-color": C.road, "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.4, 12, 2] } },
      { id: "roads-highway", type: "line", source: "basemap", "source-layer": "roads",
        minzoom: 5, filter: ["==", ["get", "kind"], "highway"],
        paint: { "line-color": C.highway, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 9, 1.6, 12, 3.5] } },
      { id: "boundaries", type: "line", source: "basemap", "source-layer": "boundaries",
        filter: ["<=", ["get", "kind_detail"], 4],
        paint: { "line-color": C.boundary, "line-width": 0.8, "line-dasharray": [3, 2] } },
      { id: "place-labels", type: "symbol", source: "basemap", "source-layer": "places",
        minzoom: 4.5, filter: ["==", ["get", "kind"], "locality"],
        layout: {
          "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 10, 14],
          "symbol-sort-key": ["get", "min_zoom"],
        },
        paint: { "text-color": C.label, "text-halo-color": C.halo, "text-halo-width": 1.2 } },
    ],
  };

  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  const map = new maplibregl.Map({
    container: "map",
    style,
    center: [-88.5, 41.8],
    zoom: 5,
    minZoom: 3.5,
    maxZoom: 14.5,
    maxBounds: [[-108, 33], [-75, 52]],
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));

  let popup = null;
  let lastData = null;

  function circleGeoJSON(lat, lng, miles) {
    const pts = [];
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * 2 * Math.PI;
      pts.push([
        lng + (miles / (69.0 * Math.cos((lat * Math.PI) / 180))) * Math.cos(a),
        lat + (miles / 69.0) * Math.sin(a),
      ]);
    }
    return { type: "Feature", geometry: { type: "Polygon", coordinates: [pts] } };
  }

  function apply(data) {
    lastData = data;
    const feats = data.venues
      .filter((v) => v.lat != null && v.lng != null)
      .map((v) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [v.lng, v.lat] },
        properties: {
          id: v.id,
          name: v.name,
          city: `${v.city}, ${v.state}`,
          count: data.counts[v.id] || 0,
          active: (data.counts[v.id] || 0) > 0,
          selected: v.id === data.venueFilter,
        },
      }));
    map.getSource("venues").setData({ type: "FeatureCollection", features: feats });
    map.getSource("origin").setData(
      data.origin
        ? { type: "FeatureCollection", features: [
            circleGeoJSON(data.origin[0], data.origin[1], data.radius),
            { type: "Feature", geometry: { type: "Point", coordinates: [data.origin[1], data.origin[0]] }, properties: { origin: true } },
          ] }
        : { type: "FeatureCollection", features: [] }
    );
  }

  map.on("load", () => {
    map.addSource("venues", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addSource("origin", { type: "geojson", data: { type: "FeatureCollection", features: [] } });

    map.addLayer({
      id: "origin-circle", type: "fill", source: "origin",
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": C.pin, "fill-opacity": 0.06 },
    });
    map.addLayer({
      id: "origin-line", type: "line", source: "origin",
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "line-color": C.pin, "line-width": 1, "line-dasharray": [2, 2], "line-opacity": 0.5 },
    });
    map.addLayer({
      id: "origin-pt", type: "circle", source: "origin",
      filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 4, "circle-color": C.pin, "circle-opacity": 0.9 },
    });
    map.addLayer({
      id: "venue-pins", type: "circle", source: "venues",
      paint: {
        "circle-radius": ["case", ["get", "selected"], 9, 7],
        "circle-color": ["case", ["get", "active"], C.pin, C.pinOff],
        "circle-stroke-width": ["case", ["get", "selected"], 3, 1.5],
        "circle-stroke-color": C.pinRing,
      },
    });
    map.addLayer({
      id: "venue-labels", type: "symbol", source: "venues",
      minzoom: 6.5,
      layout: {
        "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"],
        "text-size": 11, "text-offset": [0, 1.3], "text-anchor": "top",
        "text-optional": true,
      },
      paint: { "text-color": C.label, "text-halo-color": C.halo, "text-halo-width": 1.2 },
    });

    map.on("click", "venue-pins", (e) => {
      const f = e.features[0];
      const p = f.properties;
      window.__setVenueFilter(p.selected === true || p.selected === "true" ? null : p.id);
      if (popup) popup.remove();
      popup = new maplibregl.Popup({ offset: 12, closeButton: false })
        .setLngLat(f.geometry.coordinates)
        .setText(`${p.name} — ${p.city} · ${p.count} showtime${p.count === "1" ? "" : "s"}`)
        .addTo(map);
    });
    map.on("mouseenter", "venue-pins", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "venue-pins", () => { map.getCanvas().style.cursor = ""; });

    if (window.__mapData) apply(window.__mapData);
  });

  window.__updateMap = (data) => {
    if (map.loaded() && map.getSource("venues")) apply(data);
    else window.__mapData = data;
  };
  if (window.__mapData) window.__updateMap(window.__mapData);
  window.__map = map; // for debugging / test drivers
})();
