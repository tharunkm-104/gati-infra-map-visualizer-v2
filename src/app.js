const format = new Intl.NumberFormat("en-IN");
const HOVER_DELAY_MS = 550;

// ---- GATI palette tokens ----
// Health scale: NABH recedes into the basemap grey, colleges pop.
// Language/skilling scale: warm orange + gold steps, entirely separate from health hues.
const GATI = {
  inkMuted: "#9AA3AB",   // --ink-300/400 grey-teal (NABH, high-volume, recessive)
  teal: "#006B76",       // nursing colleges
  gold: "#48878F",       // medical colleges
  goldBright: "#E5A812",
  orange: "#F39821",     // formal German / Goethe
  goldDeep: "#9E7619",   // HEIs offering German
  goldDeepest: "#7C5C18" // general skilling
};

// ---- view mode definitions (raw counts only, no derived scores) ----
const VIEW_MODES = {
  domain: {
    label: "Language vs Health",
    series: [
      { key: "language_total", label: "Language Infrastructure", color: GATI.orange },
      { key: "health_total", label: "Health Infrastructure", color: GATI.teal },
    ],
  },
  pairs: {
    label: "By category pair",
    series: [
      { key: "formal_german_raw", label: "Formal German Infrastructure (Goethe/PASCH/Zentrum + HEIs + Exam Centres)", color: GATI.orange },
      { key: "general_skilling_raw", label: "General Skilling Infrastructure (PDOT/SIIC/IISC)", color: GATI.goldDeepest },
      { key: "nursing_colleges", label: "INC Nursing Colleges", color: GATI.teal },
      { key: "medical_colleges", label: "NMC Medical Colleges", color: GATI.gold },
      { key: "health_facilities", label: "NABH Accredited Health Facilities", color: GATI.inkMuted },
    ],
  },
  full: {
    label: "Fully disaggregated",
    series: [
      { key: "goethe_schools", label: "Goethe/PASCH/Zentrum Schools", color: GATI.orange },
      { key: "heis_german", label: "HEIs Offering German", color: GATI.goldDeep },
      { key: "exam_centres", label: "Goethe/TELC Exam Centres", color: GATI.goldBright },
      { key: "pdot_siics", label: "PDOT/SIIC Centres", color: GATI.goldDeepest },
      { key: "iiscs", label: "IISC Centres", color: "#B0821C" },
      { key: "nursing_colleges", label: "INC Nursing Colleges", color: GATI.teal },
      { key: "medical_colleges", label: "NMC Medical Colleges", color: GATI.gold },
      { key: "health_facilities", label: "NABH Accredited Health Facilities", color: GATI.inkMuted },
    ],
  },
};

// Individual infrastructure points carry these subtypes. Exam Centres and
// Private German Training Organisations have no individually geocoded points in
// the source data -- they only exist as city/state totals, so their legend chips
// toggle a table column but no dots.
const POINT_SUBTYPE_META = {
  "Goethe/PASCH/Zentrum School": { domain: "language", pairsKey: "formal_german_raw", fullKey: "goethe_schools" },
  "HEI Offering German": { domain: "language", pairsKey: "formal_german_raw", fullKey: "heis_german" },
  "PDOT Centre": { domain: "language", pairsKey: "general_skilling_raw", fullKey: "pdot_siics" },
  "SIIC Centre": { domain: "language", pairsKey: "general_skilling_raw", fullKey: "pdot_siics" },
  "IISC Centre (PMKK)": { domain: "language", pairsKey: "general_skilling_raw", fullKey: "iiscs" },
  // Retained only for rows that could not be matched back to a source section.
  "General Skilling Infrastructure (PDOT/SIIC/IISC)": { domain: "language", pairsKey: "general_skilling_raw", fullKey: "pdot_siics" },
  "NABH Accredited Health Facility": { domain: "health", pairsKey: "health_facilities", fullKey: "health_facilities" },
  "NMC Medical College": { domain: "health", pairsKey: "medical_colleges", fullKey: "medical_colleges" },
  "INC Nursing College": { domain: "health", pairsKey: "nursing_colleges", fullKey: "nursing_colleges" },
};

const DOMAIN_COLOR = { language: GATI.orange, health: GATI.teal };
const FALLBACK_COLOR = GATI.inkMuted;

const CITY_PALETTE = [...d3.schemeTableau10, ...d3.schemeSet3];
let cityColorScale = null;
let stateColorScale = null;

let cities = [];
let states = [];
let infrastructure = [];
let renderableInfrastructure = [];
let trueCoordsOnly = false; // when true, hides pin_centroid ("hollow dot") points
let viewMode = "domain";
let forcedLevel = "auto";
let activeMarkers = [];
let activeIndex = null;
let hoverTimer = null;

// Legend filter state: which series keys are currently switched on.
// Reset to "all on" whenever the view mode changes.
let activeCategories = new Set(VIEW_MODES[viewMode].series.map((s) => s.key));

const map = L.map("map", {
  zoomControl: false,
  scrollWheelZoom: true,
}).setView([20.7, 78.9], 5);

L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  maxZoom: 19,
}).addTo(map);

// Shared canvas renderer -- ~4,400 circleMarkers on one canvas instead of
// ~4,400 divIcon DOM nodes. This is what keeps the full-India dot view usable.
const infraCanvas = L.canvas({ padding: 0.3 });

function currentSeries() {
  return VIEW_MODES[viewMode].series;
}

// Every series in every view mode maps to a count column in cities.json.
function countableSeries() {
  return currentSeries();
}

function activeSeries() {
  return countableSeries().filter((s) => activeCategories.has(s.key));
}

function resetActiveCategories() {
  activeCategories = new Set(currentSeries().map((s) => s.key));
}

function safeLevel() {
  if (forcedLevel !== "auto") return forcedLevel;
  const zoom = map.getZoom();
  if (zoom <= 6) return "state";
  if (zoom <= 9) return "city";
  return "infrastructure";
}

// Only sums series that are currently switched on, so bubble sizes and the
// table stay consistent with whatever is deselected in the legend.
function locationTotal(row) {
  return activeSeries().reduce((sum, s) => sum + (row[s.key] || 0), 0);
}

// The series key an individual infra point maps to under the current view mode.
// Returns null when the point's subtype has no matching series (e.g. the
// PDOT/SIIC/IISC bundle in "Fully disaggregated", which is split across three
// series that individual points cannot be attributed to).
function seriesKeyForPoint(point) {
  const meta = POINT_SUBTYPE_META[point.subtype];
  if (!meta) return null;
  if (viewMode === "domain") return `${meta.domain}_total`;
  const key = viewMode === "pairs" ? meta.pairsKey : meta.fullKey;
  return currentSeries().some((s) => s.key === key) ? key : null;
}

function isPointActive(point) {
  const key = seriesKeyForPoint(point);
  if (key === null) return true; // unmapped subtypes are never filtered out
  return activeCategories.has(key);
}

// Points currently eligible to render, after the "true coordinates only" toggle.
// Toggled off pin_centroid ("hollow dot") points stay in renderableInfrastructure
// (data untouched) but are excluded here, so counts and dots update together.
function coordinateFilteredInfrastructure() {
  if (!trueCoordsOnly) return renderableInfrastructure;
  return renderableInfrastructure.filter((p) => p.coordinateStatus !== "pin_centroid");
}

function pointsForLevel(level) {
  if (level === "state") return states.map((s) => ({ ...s, levelName: s.state }));
  if (level === "city") return cities.map((c) => ({ ...c, levelName: c.city }));
  return coordinateFilteredInfrastructure().filter(isPointActive);
}

function featureForPoint(point, level) {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [point.longitude, point.latitude] },
    properties: { ...point, level },
  };
}

// State/city levels still cluster. Infrastructure level does NOT -- every dot is
// drawn individually at any zoom, so wide zoom-outs no longer collapse into
// grey count-bubbles.
function buildIndex(level) {
  if (level === "infrastructure") {
    activeIndex = null;
    return;
  }
  const points = pointsForLevel(level).filter((p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude));
  const features = points.map((p) => featureForPoint(p, level));
  activeIndex = new Supercluster({
    radius: 44,
    maxZoom: 11,
    map: (props) => ({ count: 1, total: locationTotal(props) }),
    reduce: (accumulated, props) => {
      accumulated.count += props.count;
      accumulated.total += props.total;
    },
  }).load(features);
}

function clearMarkers() {
  activeMarkers.forEach((m) => m.remove());
  activeMarkers = [];
}

function drawMarkers() {
  const level = safeLevel();
  clearMarkers();
  buildIndex(level);
  const bounds = map.getBounds().pad(0.2);

  if (level === "infrastructure") {
    // Raw dot mode: no clustering, canvas-rendered circle markers.
    pointsForLevel("infrastructure").forEach((point) => {
      if (!Number.isFinite(point.latitude) || !Number.isFinite(point.longitude)) return;
      if (!bounds.contains([point.latitude, point.longitude])) return;
      const marker = infrastructureMarker(point, point.latitude, point.longitude);
      marker.addTo(map);
      activeMarkers.push(marker);
    });
    renderTable(level);
    return;
  }

  const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
  const clusters = activeIndex.getClusters(bbox, Math.round(map.getZoom()));
  clusters.forEach((feature) => {
    const [longitude, latitude] = feature.geometry.coordinates;
    const props = feature.properties;
    const marker = props.cluster
      ? clusterMarker(feature, level, latitude, longitude)
      : pointMarker(feature, level, latitude, longitude);
    marker.addTo(map);
    activeMarkers.push(marker);
  });
  renderTable(level);
}

function radiusForTotal(total, maxTotal) {
  const ratio = maxTotal > 0 ? total / maxTotal : 0;
  return Math.round(20 + ratio * 46);
}

function maxTotalForLevel(level) {
  const rows = level === "state" ? states : cities;
  return Math.max(1, ...rows.map((r) => locationTotal(r)));
}

function clusterMarker(feature, level, latitude, longitude) {
  const props = feature.properties;
  const maxTotal = maxTotalForLevel(level);
  const size = radiusForTotal(props.total, maxTotal);
  const marker = L.marker([latitude, longitude], {
    icon: L.divIcon({
      html: `<div class="cluster-bubble" style="width:${size}px;height:${size}px;background:#5b6673">${format.format(props.point_count)}</div>`,
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    }),
  });
  marker.on("click", () => {
    const expansionZoom = Math.min(activeIndex.getClusterExpansionZoom(props.cluster_id), 16);
    map.setView([latitude, longitude], expansionZoom);
  });
  bindHover(marker, () => clusterHoverHtml(level, props));
  return marker;
}

function clusterHoverHtml(level, props) {
  return `<strong>${format.format(props.point_count)} ${level === "state" ? "states" : "cities"} clustered</strong><div>Total ${VIEW_MODES[viewMode].label.toLowerCase()}: ${format.format(props.total)}</div>`;
}

function pointMarker(feature, level, latitude, longitude) {
  const point = feature.properties;
  const maxTotal = maxTotalForLevel(level);
  const total = locationTotal(point);
  const size = radiusForTotal(total, maxTotal);
  const colorScale = level === "state" ? stateColorScale : cityColorScale;
  const color = colorScale(point.levelName);
  const marker = L.marker([latitude, longitude], {
    icon: L.divIcon({
      html: `<div class="marker-bubble" style="width:${size}px;height:${size}px;background:${color}">${format.format(total)}</div>`,
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    }),
  });
  bindHover(marker, () => locationHoverHtml(point));
  return marker;
}

function locationHoverHtml(point) {
  const rows = countableSeries()
    .map((s) => {
      const off = activeCategories.has(s.key) ? "" : " hover-row--off";
      return `<div class="hover-row${off}"><span>${s.label}</span><b>${format.format(point[s.key] || 0)}</b></div>`;
    })
    .join("");
  return `<strong>${point.levelName}</strong>${rows}`;
}

// Canvas circleMarker -- cheap enough to draw every point at any zoom.
function infrastructureMarker(point, latitude, longitude) {
  const color = pointColor(point);
  // Approximate coordinates (PIN-code centroids, shared points) render hollow:
  // coloured ring, no fill, so precision is visible at a glance.
  const approximate = point.coordinateStatus === "pin_centroid";
  const marker = L.circleMarker([latitude, longitude], {
    renderer: infraCanvas,
    radius: approximate ? 5 : 4.5,
    weight: approximate ? 1.6 : 1,
    color: approximate ? color : "#ffffff",
    opacity: approximate ? 1 : 0.9,
    fillColor: color,
    fillOpacity: approximate ? 0 : 0.95,
  });
  bindHover(marker, () => infrastructureHoverHtml(point));
  return marker;
}

function pointColor(point) {
  const meta = POINT_SUBTYPE_META[point.subtype];
  if (!meta) return FALLBACK_COLOR;
  if (viewMode === "domain") return DOMAIN_COLOR[meta.domain];
  const key = viewMode === "pairs" ? meta.pairsKey : meta.fullKey;
  const series = currentSeries().find((s) => s.key === key);
  return series ? series.color : FALLBACK_COLOR;
}

function infrastructureDetailRows(point) {
  const fields = [
    ["Facility type", point.facilityType],
    ["Ownership", point.ownership],
    ["NABH status", point.nabhStatus],
    ["Corridor eligibility", point.corridorEligibility],
  ];
  return fields
    .filter(([, value]) => value)
    .map(([label, value]) => `<div class="hover-row"><span>${label}</span><b>${value}</b></div>`)
    .join("");
}

const STATUS_LABEL = {
  source: "As given in source data",
  pin_centroid: "Approximate \u2014 PIN-code centroid or point shared with another institution",
  researched_override: "Corrected \u2014 source coordinate was wrong, replaced after research",
};

function infrastructureHoverHtml(point) {
  const status = STATUS_LABEL[point.coordinateStatus] || point.coordinateStatus;
  return `
    <strong>${point.name || "Unnamed entry"}</strong>
    <div class="hover-row"><span>Category</span><b>${point.subtype}</b></div>
    <div class="hover-row"><span>City</span><b>${point.city}</b></div>
    ${infrastructureDetailRows(point)}
    <div class="hover-note">Coordinate: ${status}.${
      point.ownershipBasis ? ` Ownership: ${point.ownershipBasis.toLowerCase()}.` : ""
    }</div>
  `;
}

// ---- hover card ----
const hoverCard = document.getElementById("hover-card");

function bindHover(marker, htmlFn) {
  marker.on("mouseover", (e) => {
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => showHoverCard(htmlFn(), e.originalEvent), HOVER_DELAY_MS);
  });
  marker.on("mousemove", (e) => {
    if (!hoverCard.hidden) positionHoverCard(e.originalEvent);
  });
  marker.on("mouseout", () => {
    clearTimeout(hoverTimer);
    hoverCard.hidden = true;
  });
}

function showHoverCard(html, originalEvent) {
  hoverCard.innerHTML = html;
  hoverCard.hidden = false;
  positionHoverCard(originalEvent);
}

function positionHoverCard(originalEvent) {
  if (!originalEvent) return;
  const mapRect = document.getElementById("map").getBoundingClientRect();
  hoverCard.style.left = `${originalEvent.clientX - mapRect.left + 16}px`;
  hoverCard.style.top = `${originalEvent.clientY - mapRect.top + 16}px`;
}

// ---- aggregate summary strip ----
// Fixed totals across all 15 cities, summed from cities.json. Deliberately NOT
// derived from visible dots: that would be expensive and would change on pan.
// Refreshed on view-mode change and initial load only.
function mappedPointCounts() {
  const counts = new Map();
  coordinateFilteredInfrastructure().forEach((point) => {
    const key = seriesKeyForPoint(point);
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  });
  return counts;
}

function renderAggregateSummary() {
  const host = document.getElementById("aggregate-summary");
  if (!host) return;
  if (!cities.length) {
    host.innerHTML = "";
    return;
  }

  const mapped = mappedPointCounts();
  let grandCounted = 0;
  let grandMapped = 0;

  const items = countableSeries()
    .map((s) => {
      const counted = cities.reduce((sum, c) => sum + (c[s.key] || 0), 0);
      const points = mapped.get(s.key) || 0;
      grandCounted += counted;
      grandMapped += points;

      // Flag the two ways the official count and the map can disagree, so a
      // gap in the source data never looks like a rendering bug.
      let detail = "";
      if (points === 0 && counted > 0) {
        detail = `<span class="summary-gap">counts only \u2014 no mapped points</span>`;
      } else if (points !== counted) {
        detail = `<span class="summary-gap">${format.format(points)} mapped</span>`;
      }

      const off = activeCategories.has(s.key) ? "" : " summary-item--off";
      return `<div class="summary-item${off}">
        <span class="swatch" style="background:${s.color}"></span>
        <b>${format.format(counted)}</b>
        <span class="summary-label">${s.label}</span>
        ${detail}
      </div>`;
    })
    .join("");

  const centroidCount = renderableInfrastructure.filter((p) => p.coordinateStatus === "pin_centroid").length;
  const centroidNote = trueCoordsOnly
    ? `<span class="summary-gap">${format.format(centroidCount)} approximate (hollow-dot) points hidden</span>`
    : `<span class="summary-gap">${format.format(centroidCount)} approximate (hollow-dot) points shown</span>`;

  host.innerHTML =
    `<div class="summary-title">All 15 cities &middot; ${VIEW_MODES[viewMode].label} &middot; ` +
    `${format.format(grandCounted)} counted &middot; ${format.format(grandMapped)} mapped &middot; ${centroidNote}</div>` +
    `<div class="summary-items">${items}</div>`;
}

// ---- legend ----
function renderLegend() {
  const legend = document.getElementById("legend");
  const level = safeLevel();
  if (level === "infrastructure") {
    legend.innerHTML = currentSeries()
      .map((s) => {
        const on = activeCategories.has(s.key);
        const style = on
          ? `background:${s.color};border-color:${s.color};color:#fff;`
          : `background:transparent;border-color:${s.color};color:${s.color};`;
        return `<button type="button" class="legend-chip${on ? " active" : ""}" data-key="${s.key}" aria-pressed="${on}" style="${style}"><span class="chip-dot" style="background:${on ? "#fff" : s.color}"></span>${s.label}</button>`;
      })
      .join("");
    legend.querySelectorAll(".legend-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const key = chip.dataset.key;
        if (activeCategories.has(key)) activeCategories.delete(key);
        else activeCategories.add(key);
        renderAggregateSummary();
        drawMarkers();
      });
    });
    return;
  }
  legend.innerHTML = `<div class="legend-note">Bubble color reflects each ${level === "state" ? "state" : "city"}; bubble size reflects total ${VIEW_MODES[viewMode].label.toLowerCase()} across selected categories.</div>`;
}

// ---- location table ----
function renderTable(level) {
  renderLegend();
  const container = document.getElementById("location-table");
  const rows = level === "state" ? states : cities;
  const sorted = rows.slice().sort((a, b) => (a.levelName || a.city || a.state).localeCompare(b.levelName || b.city || b.state));
  const series = countableSeries();
  // Drives the fixed-width grid columns in styles.css so the table scrolls
  // horizontally instead of compressing.
  container.style.setProperty("--col-count", series.length);
  const head = `<div class="table-row table-head"><span>${level === "state" ? "State" : "City"}</span>${series
    .map((s) => `<span${activeCategories.has(s.key) ? "" : ' class="col-off"'}>${s.label}</span>`)
    .join("")}</div>`;
  const body = sorted
    .map((row) => {
      const cells = series
        .map((s) => `<span${activeCategories.has(s.key) ? "" : ' class="col-off"'}>${format.format(row[s.key] || 0)}</span>`)
        .join("");
      const name = level === "state" ? row.state : row.city;
      return `<div class="table-row"><span>${name}</span>${cells}<span class="row-total">${format.format(locationTotal(row))}</span></div>`;
    })
    .join("");
  container.innerHTML =
    head.replace("</div>", "<span>Selected total</span></div>") + body;
}

// ---- controls ----
document.querySelectorAll(".level-button").forEach((button) => {
  button.addEventListener("click", () => {
    forcedLevel = button.dataset.level;
    document.querySelectorAll(".level-button").forEach((b) => b.classList.toggle("active", b === button));
    drawMarkers();
  });
});

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => {
    viewMode = button.dataset.mode;
    resetActiveCategories();
    document.querySelectorAll(".mode-button").forEach((b) => b.classList.toggle("active", b === button));
    renderAggregateSummary();
    drawMarkers();
  });
});

map.on("zoomend moveend", () => {
  drawMarkers();
});

const trueCoordsToggle = document.getElementById("true-coords-toggle");
if (trueCoordsToggle) {
  trueCoordsToggle.addEventListener("change", () => {
    trueCoordsOnly = trueCoordsToggle.checked;
    renderAggregateSummary();
    drawMarkers();
  });
}

const RENDERABLE_STATUSES = new Set(["source", "pin_centroid", "researched_override"]);

function isRenderableInfra(point) {
  // "duplicate_collapsed" and "undefined_flagged" stay in the data but off the map.
  return (
    RENDERABLE_STATUSES.has(point.coordinateStatus) &&
    Number.isFinite(point.latitude) &&
    Number.isFinite(point.longitude) &&
    !(point.latitude === 0 && point.longitude === 0)
  );
}

Promise.all([
  fetch("../data/cities.json").then((r) => r.json()),
  fetch("../data/states.json").then((r) => r.json()),
  fetch("../data/infrastructure-cleaned.json").then((r) => r.json()),
])
  .then(([cityRows, stateRows, infraRows]) => {
    cities = cityRows;
    states = stateRows;
    infrastructure = infraRows;
    renderableInfrastructure = infrastructure.filter(isRenderableInfra);
    cityColorScale = d3.scaleOrdinal().domain(cities.map((c) => c.city)).range(CITY_PALETTE);
    stateColorScale = d3.scaleOrdinal().domain(states.map((s) => s.state)).range(CITY_PALETTE);
    console.info(
      `[infrastructure-layer] renderable=${renderableInfrastructure.length} dropped=${infrastructure.length - renderableInfrastructure.length} total=${infrastructure.length}`
    );
    resetActiveCategories();
    renderAggregateSummary();
    drawMarkers();
  })
  .catch((error) => {
    document.getElementById("location-table").innerHTML = `<p class="detail-copy">Data load failed: ${error.message}</p>`;
  });
