/* Walking access map: MapLibre + PMTiles, all data static (docs/data/<slug>.pmtiles + _summary.json).
   Scores are recomputed in the browser from the per-service scores when weights change. */
"use strict";

const params = new URLSearchParams(location.search);
const CITY = (params.get("city") || "carson_city").replace(/[^a-z0-9_]/g, "");
const LABELS = { grocery: "Grocery", healthcare: "Healthcare", schools: "Schools (K-8)",
                 transit: "Transit stop", parks: "Parks", convenience: "Convenience store" };
const HEADLINE_LABELS = { grocery: "a full-service grocery store", healthcare: "primary care, urgent care or a pharmacy",
                         transit: "a transit stop", schools: "a K-8 school", parks: "a park" };
const LISA = { HH: "#d7191c", LL: "#2c7bb6" };

let S, map, popup, weights = {}, enabled = {}, hovered = null;

const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "–"
  : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });

/* ---------- weights and scores ---------- */
/* Weights are whole percentages that always sum to 100 over the enabled services. Moving a slider
   changes only the sliders below it (in proportion to their values); the ones above stay put, so
   the last enabled slider just takes what is left. */
function active() { return S.services.filter((s) => enabled[s]); }

function share(total, keys, basis) {
  // split an integer total over keys in proportion to basis (equal if all zero), largest remainder
  if (!keys.length) return {};
  const b = keys.map((k) => Math.max(basis[k] || 0, 0));
  const sum = b.reduce((a, x) => a + x, 0);
  const raw = keys.map((k, i) => total * (sum > 0 ? b[i] / sum : 1 / keys.length));
  const out = raw.map(Math.floor);
  let left = total - out.reduce((a, x) => a + x, 0);
  raw.map((r, i) => [r - out[i], i]).sort((x, y) => y[0] - x[0]).forEach(([, i]) => { if (left > 0) { out[i]++; left--; } });
  return Object.fromEntries(keys.map((k, i) => [k, out[i]]));
}

function setWeight(s, v) {
  const on = active(), i = on.indexOf(s);
  const above = on.slice(0, i), below = on.slice(i + 1);
  const free = 100 - above.reduce((a, k) => a + weights[k], 0);
  weights[s] = below.length ? Math.min(Math.max(v, 0), free) : free;
  Object.assign(weights, share(free - weights[s], below, weights));
}

function rescale(basis) {
  for (const s of S.services) if (!enabled[s]) weights[s] = 0;
  Object.assign(weights, share(100, active(), basis));
}

function syncSliders() {
  const on = active();
  for (const s of S.services) {
    const el = document.getElementById(`w-${s}`);
    el.value = weights[s];
    el.disabled = !enabled[s] || on.indexOf(s) === on.length - 1;
    el.title = el.disabled && enabled[s] ? "Takes what the sliders above leave" : "";
  }
}

function normWeights() {
  const on = S.services.filter((s) => enabled[s] && weights[s] > 0);
  const tot = on.reduce((a, s) => a + weights[s], 0);
  return Object.fromEntries(S.services.map((s) => [s, tot > 0 && on.includes(s) ? weights[s] / tot : 0]));
}
function scoreOf(row, w) {
  if (row.access_score === null) return null;
  return S.services.reduce((a, s) => a + w[s] * (row[`${s}_score`] ?? 0), 0);
}
function scoreExpr(w) {
  return ["+", ...S.services.map((s) => ["*", w[s], ["coalesce", ["get", `${s}_score`], 0]])];
}
function colorExpr(w) {
  const br = S.web.class_breaks, cols = S.web.class_colors;
  const step = ["step", scoreExpr(w), cols[0]];
  for (let i = 1; i < cols.length; i++) step.push(br[i], cols[i]);
  return ["case", ["!", ["to-boolean", ["get", "has_residents"]]], S.web.no_residents_color, step];
}
function classColor(v) {
  if (v === null) return S.web.no_residents_color;
  const br = S.web.class_breaks;
  let i = 0;
  while (i < S.web.class_colors.length - 1 && v >= br[i + 1]) i++;
  return S.web.class_colors[i];
}

/* ---------- panel ---------- */
function buildPanel() {
  document.title = `Walking access — ${S.city}`;
  document.getElementById("title").textContent = `Walking access to essential services — ${S.city}`;
  document.getElementById("subtitle").innerHTML =
    `${S.block_groups.length} neighborhoods · Overture ${S.overture_release} · ACS 5-year ${S.acs_year}` +
    `<br><span class="small">Neighborhoods are Census block groups, about 500–3,000 residents each.</span>`;
  const near = (s) => S.headline.find((h) => h.metric === `${s}_le_400m`);
  const rowsNear = S.services.filter(near).map((s) => {
    const h = near(s);
    return `<tr><td><span class="dot" style="background:${S.web.service_colors[s]}"></span>${HEADLINE_LABELS[s] || LABELS[s] || s}</td>
      <td class="num"><strong>${fmt(h.pct, 1)} %</strong></td>
      <td class="num muted">${fmt(Math.round(h.residents / 10) * 10)}</td></tr>`;
  });
  if (rowsNear.length) {
    document.getElementById("headline").innerHTML =
      `<p>Residents living within a 5-minute walk (400 m) of …</p>
       <table class="near"><tr><th></th><th class="num">share</th><th class="num">residents</th></tr>${rowsNear.join("")}</table>`;
  }

  const lg = document.getElementById("legend");
  const br = S.web.class_breaks;
  lg.innerHTML = S.web.class_colors.map((c, i) =>
    `<div class="swatch"><span style="background:${c}"></span>${br[i]}–${br[i + 1]}</div>`).join("") +
    `<div class="none-row"><span class="none-swatch"></span>no residents (not scored)</div>`;

  const wd = document.getElementById("weights");
  wd.innerHTML = S.services.map((s) => `
    <div class="weight">
      <input type="checkbox" id="on-${s}" checked aria-label="include ${s}">
      <label for="w-${s}">${LABELS[s] || s}</label>
      <input type="range" id="w-${s}" min="0" max="100" step="1" value="${weights[s]}">
      <span class="pct" id="p-${s}"></span>
    </div>`).join("");
  for (const s of S.services) {
    document.getElementById(`on-${s}`).addEventListener("change", (e) => {
      enabled[s] = e.target.checked;
      if (enabled[s]) weights[s] = Math.round(S.weights[s] * 100);  // back at its default share
      if (!active().length) { enabled[s] = true; e.target.checked = true; }  // keep one service
      rescale(weights);
      syncSliders(); update();
    });
    document.getElementById(`w-${s}`).addEventListener("input", (e) => {
      setWeight(s, +e.target.value);
      syncSliders(); update();
    });
  }
  document.getElementById("reset").addEventListener("click", () => {
    for (const s of S.services) {
      enabled[s] = true;
      document.getElementById(`on-${s}`).checked = true;
    }
    rescale(Object.fromEntries(S.services.map((s) => [s, S.weights[s]])));
    syncSliders(); update();
  });
  syncSliders();

  const svcTypes = Object.keys(S.web.service_colors);
  document.getElementById("layers").innerHTML =
    svcTypes.map((s) => `<label><input type="checkbox" data-svc="${s}" ${s === "convenience" ? "" : "checked"}>
      <span class="dot" style="background:${S.web.service_colors[s]}"></span>${LABELS[s] || s}</label>`).join("") +
    `<label><input type="checkbox" id="lyr-parks" checked> Park areas</label>` +
    `<label><input type="checkbox" id="lyr-lisa"> Clusters of high / low access
      <span class="muted small">(LISA, default weights)</span></label>`;
  document.querySelectorAll("#layers [data-svc]").forEach((el) => el.addEventListener("change", filterServices));
  document.getElementById("lyr-parks").addEventListener("change", (e) =>
    map.setLayoutProperty("parks", "visibility", e.target.checked ? "visible" : "none"));
  document.getElementById("lyr-lisa").addEventListener("change", (e) =>
    map.setLayoutProperty("lisa", "visibility", e.target.checked ? "visible" : "none"));

  document.getElementById("sources").innerHTML =
    "Sources: " + S.attribution.join(" · ") + " · " + S.basemap_attribution +
    `. Method and code: <a href="https://github.com/xoffvsg/urban-accessibility">github.com/xoffvsg/urban-accessibility</a>.`;
}

/* Service points: one feature per location. Where several visible services share a spot (Walmart:
   grocery + pharmacy) the dot is split into equal slices; with one visible service it is a full dot. */
const PIE_PX = 24;  // icon size in CSS px at icon-size 1 (drawn at 2x for sharp edges)

function pieImage(services) {
  const r = 2 * PIE_PX, c = document.createElement("canvas");
  c.width = c.height = r;
  const g = c.getContext("2d"), mid = r / 2, rad = mid - 2;
  services.forEach((s, i) => {
    const a0 = -Math.PI / 2 + (2 * Math.PI * i) / services.length;
    const a1 = -Math.PI / 2 + (2 * Math.PI * (i + 1)) / services.length;
    g.beginPath(); g.moveTo(mid, mid); g.arc(mid, mid, rad, a0, a1); g.closePath();
    g.fillStyle = S.web.service_colors[s] || "#888"; g.fill();
  });
  g.beginPath(); g.arc(mid, mid, rad, 0, 2 * Math.PI);
  g.lineWidth = 2.5; g.strokeStyle = "#000"; g.stroke();
  return g.getImageData(0, 0, r, r);
}

function visibleServices() {
  return [...document.querySelectorAll("#layers [data-svc]:checked")].map((el) => el.dataset.svc);
}

function servicePointsData() {
  const on = visibleServices(), order = Object.keys(S.web.service_colors);
  return {
    type: "FeatureCollection",
    features: (S.service_points || []).map((p) => {
      const items = p.items.filter((it) => on.includes(it.service));
      const kinds = order.filter((k) => items.some((it) => it.service === k));
      return { type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
               properties: { icon: kinds.length ? `pie:${kinds.join("+")}` : "", items: JSON.stringify(items) } };
    }),
  };
}

function filterServices() {
  map.getSource("svc")?.setData(servicePointsData());
}

/* names come from data (OSM/Overture/config): escape before putting them into HTML/SVG */
function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function short(t, n) {
  return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;
}

/* ---------- chart ---------- */
function drawChart() {
  const w = normWeights();
  const rows = S.block_groups.map((r) => ({ ...r, v: scoreOf(r, w) }))
    .sort((a, b) => (b.v ?? -1) - (a.v ?? -1));
  const H = 15, W = 340, L = 150, R = 30;
  const svg = [`<svg viewBox="0 0 ${W} ${rows.length * H + 4}" role="img" aria-label="Neighborhoods sorted by score">`];
  rows.forEach((r, i) => {
    const y = i * H + 2, len = r.v === null ? 0 : (r.v / 100) * (W - L - R);
    svg.push(`<g class="bar" data-geoid="${r.geoid}">
      <rect class="bg" x="0" y="${y}" width="${W}" height="${H}"></rect>
      <title>${esc(r.label)}</title>
      <text x="${L - 4}" y="${y + 11}" text-anchor="end">${esc(short(r.label, 26))}</text>
      <rect x="${L}" y="${y + 2}" width="${len.toFixed(1)}" height="${H - 4}" fill="${classColor(r.v)}"></rect>
      <text x="${L + len + 3}" y="${y + 11}">${r.v === null ? "no residents" : fmt(r.v, 1)}</text>
    </g>`);
  });
  svg.push("</svg>");
  const el = document.getElementById("chart");
  el.innerHTML = svg.join("");
  el.querySelectorAll(".bar").forEach((g) => {
    g.addEventListener("mouseenter", () => highlight(g.dataset.geoid));
    g.addEventListener("mouseleave", () => highlight(null));
    g.addEventListener("click", () => {
      const r = S.block_groups.find((x) => x.geoid === g.dataset.geoid);
      map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: 60, maxZoom: 15 });
    });
  });
}

function highlight(geoid) {
  if (hovered) map.setFeatureState({ source: "ua", sourceLayer: "block_groups", id: hovered }, { hover: false });
  hovered = geoid;
  if (geoid) map.setFeatureState({ source: "ua", sourceLayer: "block_groups", id: geoid }, { hover: true });
  document.querySelectorAll("#chart .bar").forEach((g) => g.classList.toggle("hl", g.dataset.geoid === geoid));
}

/* ---------- popup ---------- */
function popupHtml(p) {
  const w = normWeights();
  const warn = (flag) => flag ? ` <span class="warn" title="ACS margin of error is large (CV > ${S.web.cv_flag_pct} %)">⚠</span>` : "";
  if (!p.has_residents) {
    return `<div class="popup"><h3>${esc(p.label)}</h3><div class="muted small">${esc(p.census_label)}</div>
      <p class="muted">No residents (ACS population 0): not scored.</p></div>`;
  }
  const row = Object.fromEntries(S.services.map((s) => [`${s}_score`, p[`${s}_score`]]));
  const v = scoreOf({ ...row, access_score: p.access_score }, w);
  const custom = Math.abs(v - p.access_score) > 0.05;
  return `<div class="popup">
    <h3>${esc(p.label)}</h3>
    <div class="muted small">${esc(p.census_label)}</div>
    <div><span class="score">${fmt(v, 1)}</span> / 100
      ${custom ? `<span class="muted">(default weights: ${fmt(p.access_score, 1)})</span>` : ""}</div>
    <div class="muted">rank ${fmt(p.access_rank)} of ${S.block_groups.filter((r) => r.access_score !== null).length} neighborhoods (default weights)</div>
    <table>
      <tr><th>Service</th><th>score</th><th title="Share of the neighborhood's residents within a 400 m walk">residents ≤ 5 min</th><th>weight</th></tr>
      ${S.services.map((s) => `<tr><td><span class="dot" style="background:${S.web.service_colors[s]}"></span>${LABELS[s] || s}</td>
        <td>${fmt(p[`${s}_score`], 0)}</td><td>${fmt(p[`${s}_pop_5min_pct`], 0)} %</td><td>${fmt(100 * w[s], 0)} %</td></tr>`).join("")}
    </table>
    <div>Residents: ${fmt(p.pop_total)} ± ${fmt(p.pop_total_moe)}</div>
    <div>Median household income: $${fmt(p.median_hh_income)} ± ${fmt(p.median_hh_income_moe)}${warn(p.median_hh_income_uncertain)}</div>
    <div>Below 200 % of poverty line: ${fmt(p.below_pov_pct, 0)} %${warn(p.below_pov_pct_uncertain)}</div>
    <div>Households without a car: ${fmt(p.zero_car_hh_pct, 0)} %${warn(p.zero_car_hh_pct_uncertain)}</div>
    <div>Residents 65+: ${fmt(p.pop_65plus_pct, 0)} %${warn(p.pop_65plus_pct_uncertain)}</div>
    ${p.lisa_cluster && p.lisa_cluster !== "ns" ? `<div class="muted">Cluster: ${p.lisa_cluster === "LL" ? "low" : p.lisa_cluster === "HH" ? "high" : p.lisa_cluster} access (LISA)</div>` : ""}
  </div>`;
}

/* ---------- map ---------- */
function update() {
  const w = normWeights();
  for (const s of S.services) document.getElementById(`p-${s}`).textContent = `${fmt(100 * w[s], 0)} %`;
  map.setPaintProperty("bg-fill", "fill-color", colorExpr(w));
  drawChart();
  if (popup && popup.isOpen() && popup._ua) popup.setHTML(popupHtml(popup._ua));
}

function addLayers() {
  const w = normWeights();
  map.addSource("ua", { type: "vector", url: `pmtiles://${new URL(`data/${CITY}.pmtiles`, location.href)}`,
                        promoteId: { block_groups: "geoid" } });
  const firstLabel = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  map.addLayer({ id: "bg-fill", type: "fill", source: "ua", "source-layer": "block_groups",
                 paint: { "fill-color": colorExpr(w), "fill-opacity": 0.72 } }, firstLabel);
  map.addLayer({ id: "parks", type: "fill", source: "ua", "source-layer": "parks",
                 paint: { "fill-color": "#1b7837", "fill-opacity": 0.25, "fill-outline-color": "#1b7837" } }, firstLabel);
  map.addLayer({ id: "bg-line", type: "line", source: "ua", "source-layer": "block_groups",
                 paint: { "line-color": ["case", ["boolean", ["feature-state", "hover"], false], "#111", "#ffffff"],
                          "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 3, 0.6] } });
  map.addLayer({ id: "lisa", type: "line", source: "ua", "source-layer": "block_groups",
                 filter: ["in", ["get", "lisa_cluster"], ["literal", ["HH", "LL"]]],
                 layout: { visibility: "none" },
                 paint: { "line-color": ["match", ["get", "lisa_cluster"], "HH", LISA.HH, LISA.LL], "line-width": 3,
                          "line-dasharray": [2, 1] } });
  map.on("styleimagemissing", (e) => {
    if (!e.id.startsWith("pie:") || map.hasImage(e.id)) return;
    map.addImage(e.id, pieImage(e.id.slice(4).split("+")), { pixelRatio: 2 });
  });
  map.addSource("svc", { type: "geojson", data: servicePointsData() });
  // icon diameter: 5 px at zoom 10, 12 px at zoom 15, 18 px at zoom 17 (split dots stay readable)
  map.addLayer({ id: "services", type: "symbol", source: "svc", filter: ["!=", ["get", "icon"], ""],
                 layout: { "icon-image": ["get", "icon"], "icon-allow-overlap": true, "icon-ignore-placement": true,
                           "icon-size": ["interpolate", ["linear"], ["zoom"], 10, 5 / PIE_PX, 15, 12 / PIE_PX,
                                         17, 18 / PIE_PX] } });

  map.on("mousemove", "bg-fill", (e) => {
    map.getCanvas().style.cursor = "pointer";
    const id = e.features[0]?.properties.geoid;
    if (id !== hovered) highlight(id);
  });
  map.on("mouseleave", "bg-fill", () => { map.getCanvas().style.cursor = ""; highlight(null); });
  map.on("click", "bg-fill", (e) => {
    if (map.queryRenderedFeatures(e.point, { layers: ["services"] }).length) return;  // the dot's popup wins
    const p = e.features[0].properties;
    popup = new maplibregl.Popup({ maxWidth: "340px" }).setLngLat(e.lngLat).setHTML(popupHtml(p)).addTo(map);
    popup._ua = p;
  });
  map.on("click", "services", (e) => {
    const items = JSON.parse(e.features[0].properties.items);
    new maplibregl.Popup({ maxWidth: "260px" }).setLngLat(e.features[0].geometry.coordinates)
      .setHTML(`<div class="popup">${items.map((it) =>
        `<div><span class="dot" style="background:${S.web.service_colors[it.service]}"></span>` +
        `<strong>${esc(it.name || "(unnamed)")}</strong> <span class="muted">${LABELS[it.service] || it.service}</span></div>`).join("")}</div>`)
      .addTo(map);
  });
  map.on("mouseenter", "services", () => { map.getCanvas().style.cursor = "pointer"; });
}

async function main() {
  const res = await fetch(`data/${CITY}_summary.json`);
  if (!res.ok) throw new Error(`summary for "${CITY}" not found`);
  S = await res.json();
  for (const s of S.services) enabled[s] = true;
  rescale(Object.fromEntries(S.services.map((s) => [s, S.weights[s]])));

  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  map = new maplibregl.Map({
    container: "map", style: S.web.basemap_style,
    bounds: [[S.bounds[0], S.bounds[1]], [S.bounds[2], S.bounds[3]]], fitBoundsOptions: { padding: 30 },
    attributionControl: { compact: true, customAttribution: "Data: Overture Maps, OpenStreetMap, US Census Bureau, JAC, Carson City GIS, Douglas County GIS" },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  buildPanel();
  map.on("load", () => { addLayers(); update(); });
}

main().catch((err) => {
  document.getElementById("headline").textContent = `Could not load the map data: ${err.message}`;
  console.error(err);
});
