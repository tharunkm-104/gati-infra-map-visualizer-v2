const format = new Intl.NumberFormat("en-IN");
const HOVER_DELAY_MS = 550;

// ---- view mode definitions (raw counts only, no derived scores) ----
const VIEW_MODES = {
  domain: {
    label: "Language vs Health",
    series: [
      { key: "language_total", label: "Language Infrastructure", color: "#2f6fed" },
      { key: "health_total", label: "Health Infrastructure", color: "#1f9d63" },
    ],
  },
  pairs: {
    label: "By category pair",
    series: [
      { key: "formal_german_raw", label: "Formal German Infrastructure (Goethe/PASCH/Zentrum + HEIs + Exam Centres)", color: "#2f6fed" },
      { key: "general_skilling_raw", label: "General Skilling Infrastructure (PDOT/SIIC/IISC + Private Training Orgs)", color: "#93b6ff" },
      { key: "nursing_colleges", label: "INC Nursing Colleges", color: "#0e6b45" },
      { key: "medical_colleges", label: "NMC Medical Colleges", color: "#1f9d63" },
      { key: "health_facilities", label: "NABH Accredited Health Facilities", color: "#8fd6ac" },
    ],
  },
  full: {
    label: "Fully disaggregated",
    series: [
      { key: "goethe_schools", label: "Goethe/PASCH/Zentrum Schools", color: "#1c4fc4" },
      { key: "heis_german", label: "HEIs Offering German", color: "#3f74e6" },
      { key: "exam_centres", label: "Goethe/TELC Exam Centres", color: "#6f97ee" },
      { key: "pdot_siics", label: "PDOT/SIIC Centres", color: "#93b6ff" },
      { key: "iiscs", label: "IISC Centres", color: "#b7ceff" },
      { key: "private_training", label: "Private German Training Organisations", color: "#d6e2ff" },
      { key: "nursing_colleges", label: "INC Nursing Colleges", color: "#0e6b45" },
      { key: "medical_colleges", label: "NMC Medical Colleges", color: "#1f9d63" },
      { key: "health_facilities", label: "NABH Accredited Health Facilities", color: "#8fd6ac" },
    ],
  },
};

// Individual infrastructure points only carry these 6 distinguishable subtypes.
// Exam Centres and Private German Training Organisations have no individually
// geocoded points in the source data -- they only exist as city/state totals.
const POINT_SUBTYPE_META = {
  "Goethe/PASCH/Zentrum School": { domain: "language", pairsKey: "formal_german_raw", fullKey: "goethe_schools" },
  "HEI Offering German": { domain: "language", pairsKey: "formal_german_raw", fullKey: "heis_german" },
  "General Skilling Infrastructure (PDOT/SIIC/IISC)": { domain: "language", pairsKey: "general_skilling_raw", fullKey: "general_skilling_raw" },
  "NABH Accredited Health Facility": { domain: "health", pairsKey: "health_facilities", fullKey: "health_facilities" },
  "NMC Medical College": { domain: "health", pairsKey: "medical_colleges", fullKey: "medical_colleges" },
  "INC Nursing College": { domain: "health", pairsKey: "nursing_colleges", fullKey: "nursing_colleges" },
};

const DOMAIN_COLOR = { language: "#2f6fed", health: "#1f9d63" };

const CITY_PALETTE = [...d3.schemeTableau10, ...d3.schemeSet3];
let cityColorScale = null;
let stateColorScale = null;

let cities = [];
let states = [];
let infrastructure = [];
let renderableInfrastructure = [];
let viewMode = "domain";
let forcedLevel = "auto";
let activeMarkers = [];
let activeIndex = null;
let hoverTimer = null;

const map = L.map("map", {
  zoomControl: false,
  scrollWheelZoom: true,
}).setView([20.7, 78.9], 5);

L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  maxZoom: 19,
}).addTo(map);

function currentSeries() {
  return VIEW_MODES[viewMode].series;
}

function safeLevel() {
  if (forcedLevel !== "auto") return forcedLevel;
  const zoom = map.getZoom();
  if (zoom <= 6) return "state";
  if (zoom <= 9) return "city";
  return "infrastructure";
}

function locationTotal(row) {
  return currentSeries().reduce((sum, s) => sum + (row[s.key] || 0), 0);
}

function pointsForLevel(level) {
  if (level === "state") return states.map((s) => ({ ...s, levelName: s.state }));
  if (level === "city") return cities.map((c) => ({ ...c, levelName: c.city }));
  return renderableInfrastructure;
}

function featureForPoint(point, level) {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [point.longitude, point.latitude] },
    properties: { ...point, level },
  };
}

function buildIndex(level) {
  const points = pointsForLevel(level).filter((p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude));
  const features = points.map((p) => featureForPoint(p, level));
  activeIndex = new Supercluster({
    radius: level === "infrastructure" ? 52 : 44,
    maxZoom: level === "infrastructure" ? 15 : 11,
    map: (props) => {
      const acc = { count: 1, total: level === "infrastructure" ? 1 : locationTotal(props) };
      return acc;
    },
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
  const bounds = map.getBounds();
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
  const maxTotal = maxTotalForLevel(level === "infrastructure" ? "city" : level);
  const total = level === "infrastructure" ? props.point_count : props.total;
  const size = level === "infrastructure" ? Math.max(30, Math.min(70, 24 + Math.sqrt(props.point_count) * 6)) : radiusForTotal(total, maxTotal);
  const color = level === "infrastructure" ? "#9aa5b1" : "#5b6673";
  const marker = L.marker([latitude, longitude], {
    icon: L.divIcon({
      html: `<div class="cluster-bubble" style="width:${size}px;height:${size}px;background:${color}">${format.format(props.point_count)}</div>`,
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
  if (level === "infrastructure") {
    return `<strong>${format.format(props.point_count)} infrastructure points</strong><div>Zoom in to see individual entries</div>`;
  }
  return `<strong>${format.format(props.point_count)} ${level === "state" ? "states" : "cities"} clustered</strong><div>Total ${VIEW_MODES[viewMode].label.toLowerCase()}: ${format.format(props.total)}</div>`;
}

function pointMarker(feature, level, latitude, longitude) {
  const point = feature.properties;
  if (level === "infrastructure") return infrastructureMarker(point, latitude, longitude);

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
  const rows = currentSeries()
    .map((s) => `<div class="hover-row"><span>${s.label}</span><b>${format.format(point[s.key] || 0)}</b></div>`)
    .join("");
  return `<strong>${point.levelName}</strong>${rows}`;
}

function infrastructureMarker(point, latitude, longitude) {
  const meta = POINT_SUBTYPE_META[point.subtype];
  const color = pointColor(point, meta);
  const marker = L.marker([latitude, longitude], {
    icon: L.divIcon({
      html: `<span class="infra-dot" style="background:${color}"></span>`,
      className: "",
      iconSize: [13, 13],
      iconAnchor: [6.5, 6.5],
    }),
  });
  bindHover(marker, () => infrastructureHoverHtml(point));
  return marker;
}

function pointColor(point, meta) {
  if (!meta) return "#9aa5b1";
  if (viewMode === "domain") return DOMAIN_COLOR[meta.domain];
  if (viewMode === "pairs") {
    const series = VIEW_MODES.pairs.series.find((s) => s.key === meta.pairsKey);
    return series ? series.color : "#9aa5b1";
  }
  const series = VIEW_MODES.full.series.find((s) => s.key === meta.fullKey);
  return series ? series.color : "#9aa5b1";
}

function infrastructureHoverHtml(point) {
  return `
    <strong>${point.name || "Unnamed entry"}</strong>
    <div class="hover-row"><span>Category</span><b>${point.subtype}</b></div>
    <div class="hover-row"><span>City</span><b>${point.city}</b></div>
    <div class="hover-note">Ownership (Govt./Private) and facility-specific parameters are not present in the source data for this entry.</div>
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

// ---- legend ----
function renderLegend() {
  const legend = document.getElementById("legend");
  const level = safeLevel();
  if (level === "infrastructure") {
    legend.innerHTML = currentSeries()
      .map((s) => `<div><span class="swatch" style="background:${s.color}"></span>${s.label}</div>`)
      .join("");
    return;
  }
  legend.innerHTML = `<div class="legend-note">Bubble color reflects each ${level === "state" ? "state" : "city"}; bubble size reflects total ${VIEW_MODES[viewMode].label.toLowerCase()}.</div>`;
}

// ---- location table ----
function renderTable(level) {
  renderLegend();
  const container = document.getElementById("location-table");
  const rows = level === "state" ? states : cities;
  const sorted = rows.slice().sort((a, b) => (a.levelName || a.city || a.state).localeCompare(b.levelName || b.city || b.state));
  const series = currentSeries();
  const head = `<div class="table-row table-head"><span>${level === "state" ? "State" : "City"}</span>${series
    .map((s) => `<span>${s.label}</span>`)
    .join("")}</div>`;
  const body = sorted
    .map((row) => {
      const cells = series.map((s) => `<span>${format.format(row[s.key] || 0)}</span>`).join("");
      const name = level === "state" ? row.state : row.city;
      return `<div class="table-row"><span>${name}</span>${cells}</div>`;
    })
    .join("");
  container.innerHTML = head + body;
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
    document.querySelectorAll(".mode-button").forEach((b) => b.classList.toggle("active", b === button));
    drawMarkers();
  });
});

map.on("zoomend moveend", () => {
  drawMarkers();
});

function isRenderableInfra(point) {
  return (
    point.coordinateStatus !== "undefined_flagged" &&
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
    drawMarkers();
  })
  .catch((error) => {
    document.getElementById("location-table").innerHTML = `<p class="detail-copy">Data load failed: ${error.message}</p>`;
  });
