/**
 * The shared Leaflet map, base maps, elevation-data overlays, building footprints, and the Layers panel.
 *
 * Every workspace adds its own section to the Layers panel with registerLayerSection(); the panel is the one
 * place where layers are switched on and off and where their legends live.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml } from "../core/util.js";
import { state, settings, on } from "../core/state.js";
import { api } from "../core/api.js";

export const map = L.map("map", { zoomControl: true, tapHold: true }).setView([51.575, 6.985], 12);
L.control.scale({ imperial: false }).addTo(map);
if (location.hash === "#debug") window.terrarfMap = map;   // for automated browser tests

// ---------------------------------------------------------------- layers panel
const sections = [];   // {id, order, render(container)}

/** A workspace contributes a section to the Layers panel. render(el) fills it (called on every refresh). */
export function registerLayerSection(id, order, render) {
  sections.push({ id, order, render });
  sections.sort((a, b) => a.order - b.order);
}

export function refreshLayersPanel() {
  const panel = $("layersPanel");
  if (panel.classList.contains("hidden")) return;
  panel.innerHTML = "";
  for (const s of sections) {
    const el = document.createElement("div");
    el.dataset.section = s.id;
    s.render(el);
    if (el.childNodes.length) panel.appendChild(el);
  }
}

export function toggleLayersPanel(force) {
  const panel = $("layersPanel");
  const show = force ?? panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !show);
  if (show) refreshLayersPanel();
}

// ---------------------------------------------------------------- base maps
const baseMaps = {};
let currentBase = null;

export function setupBaseMaps(useProxy) {
  const osmAttribution = t("© OpenStreetMap contributors");
  const osm = useProxy ? "/tiles/osm/{z}/{x}/{y}.png" : "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const otm = useProxy ? "/tiles/otm/{z}/{x}/{y}.png" : "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png";
  const dop = useProxy ? "/tiles/wms/nrwdop" : "https://www.wms.nrw.de/geobasis/wms_nw_dop";
  baseMaps.gray = { label: "OpenStreetMap (grey)", layer: L.tileLayer(osm, { maxZoom: 19, className: "osm-gray", attribution: osmAttribution }) };
  baseMaps.osm = { label: "OpenStreetMap", layer: L.tileLayer(osm, { maxZoom: 19, attribution: osmAttribution }) };
  baseMaps.topo = { label: "OpenTopoMap", layer: L.tileLayer(otm, { maxZoom: 17, attribution: osmAttribution + ", SRTM | © OpenTopoMap (CC-BY-SA)" }) };
  baseMaps.dop = { label: "Aerial photo NRW (DOP)", layer: L.tileLayer.wms(dop, { layers: "nw_dop_rgb", format: "image/jpeg", maxZoom: 20, attribution: "© Geobasis NRW (dl-de/zero-2-0)" }) };
  // Esri only without the proxy: its terms for caching tiles were not checked
  if (!useProxy) baseMaps.esri = { label: "Satellite (Esri)", layer: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Tiles © Esri, Maxar, Earthstar Geographics" }) };
  setBaseMap("gray");
}

function setBaseMap(id) {
  if (currentBase) map.removeLayer(baseMaps[currentBase].layer);
  currentBase = id;
  baseMaps[id].layer.addTo(map);
  baseMaps[id].layer.bringToBack();
}

// ---------------------------------------------------------------- elevation data overlays
map.createPane("datapane");
map.getPane("datapane").style.zIndex = 450;
map.getPane("datapane").style.pointerEvents = "none";
const dataOverlays = {};   // relief | surface | ndom
for (const kind of ["relief", "surface", "ndom"]) {
  dataOverlays[kind] = L.tileLayer("", { pane: "datapane", minZoom: 11, maxZoom: 20, maxNativeZoom: 18,
    opacity: kind === "relief" ? 0.85 : 1, attribution: t("Elevation data used for computing") });
}
function updateOverlaySources() {
  const g = encodeURIComponent(settings().ground_source || "nrw_dgm");
  const s = encodeURIComponent(settings().surface_source || "nrw_dom");
  for (const [kind, layer] of Object.entries(dataOverlays)) layer.setUrl(`/api/overlay/${kind}/{z}/{x}/{y}.png?g=${g}&s=${s}`);
}

// ---------------------------------------------------------------- building footprints (#25)
const buildingLayer = L.geoJSON(null, { style: { color: "#475569", weight: 1, fillColor: "#64748b", fillOpacity: 0.35 }, interactive: false });
let buildingsOn = false;
async function loadBuildings() {
  if (!buildingsOn) return;
  if (map.getZoom() < 15) { buildingLayer.clearLayers(); return; }
  const b = map.getBounds();
  try {
    const data = await api(`/api/buildings?bbox=${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}`);
    buildingLayer.clearLayers(); buildingLayer.addData(data);
  } catch { /* layer stays as it is */ }
}
map.on("moveend", loadBuildings);

registerLayerSection("base", 90, (el) => {
  const b = state.config?.buildings || {};
  el.innerHTML = `<h4>${t("Base map & data")}</h4>
    <select id="baseSelect" style="width:100%">${Object.entries(baseMaps).map(([id, x]) => `<option value="${id}" ${id === currentBase ? "selected" : ""}>${escapeHtml(t(x.label))}</option>`).join("")}</select>
    ${["relief", "surface", "ndom"].map((k) => `<label class="chk"><input type="checkbox" data-ov="${k}" ${map.hasLayer(dataOverlays[k]) ? "checked" : ""}> ${t({ relief: "Terrain (height + hillshade)", surface: "Surface (buildings, trees)", ndom: "Obstacle height above ground" }[k])}</label>`).join("")}
    ${b.count ? `<label class="chk"><input type="checkbox" id="buildingsToggle" ${buildingsOn ? "checked" : ""}> ${t("Building footprints")} <span class="muted">(${b.count})</span></label>` : ""}
    <div class="legend">${map.hasLayer(dataOverlays.relief) ? `<div><span class="grad g-relief"></span> 0 · 150 · 600 m</div>` : ""}
      ${map.hasLayer(dataOverlays.ndom) ? `<div><span class="grad g-ndom"></span> 2 · 6 · 15 · 30 m</div>` : ""}</div>
    <div class="muted">${t("Elevation layers: only preloaded data, from zoom 11. Buildings from zoom 15.")}${b.attribution ? " " + escapeHtml(b.attribution) : ""}</div>`;
  el.querySelector("#baseSelect").onchange = (e) => setBaseMap(e.target.value);
  el.querySelectorAll("[data-ov]").forEach((c) => c.onchange = () => {
    const layer = dataOverlays[c.dataset.ov];
    if (c.checked) layer.addTo(map); else map.removeLayer(layer);
    refreshLayersPanel();
  });
  const bt = el.querySelector("#buildingsToggle");
  if (bt) bt.onchange = () => {
    buildingsOn = bt.checked;
    if (buildingsOn) { buildingLayer.addTo(map); loadBuildings(); } else map.removeLayer(buildingLayer);
  };
});

on("project-loaded", updateOverlaySources);
on("config-reloaded", updateOverlaySources);

export function initMap() {
  setupBaseMaps(!!state.config.tile_proxy);
  updateOverlaySources();
  $("btnLayers").onclick = (e) => { e.stopPropagation(); toggleLayersPanel(); };
  document.addEventListener("click", (e) => {
    if (!$("layersPanel").classList.contains("hidden") && !e.target.closest("#layersPanel, #btnLayers")) toggleLayersPanel(false);
  });
}

/** Metres per screen pixel at a latitude (for drawing things a fixed number of pixels long). */
export function metresPerPixel(lat) {
  return 40075016 * Math.cos(lat * Math.PI / 180) / (256 * 2 ** map.getZoom());
}
