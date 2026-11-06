/**
 * Station markers on the map, antenna direction handle, radiation sector, and placing/moving stations.
 * Stations cannot be dragged (#24) – they are moved in the inspector ("Set on the map").
 */
import { t } from "../core/i18n.js";
import { $, bearing, destinationPoint, randomId } from "../core/util.js";
import { state, emit, canEditStation, isUser, stationById } from "../core/state.js";
import { map, metresPerPixel } from "./map.js";

export const RADIO_PRESETS = {
  meshcore_eu: { label: "MeshCore EU/UK Narrow (869.618 MHz, 62.5 kHz, SF8)", freq_mhz: 869.618, bw_khz: 62.5, mode: "lora", sf: 8, tx_power_dbm: 22, nf_db: 6 },
  meshtastic_lf: { label: "Meshtastic EU LongFast (869.525 MHz, 250 kHz, SF11)", freq_mhz: 869.525, bw_khz: 250, mode: "lora", sf: 11, tx_power_dbm: 22, nf_db: 6 },
  lora_custom: { label: "LoRa, custom", mode: "lora" },
  fm_2m: { label: "2 m FM (145 MHz, 12.5 kHz)", freq_mhz: 145.5, bw_khz: 12.5, mode: "fm", req_snr_db: 10, tx_power_dbm: 37, nf_db: 5 },
  fm_70cm: { label: "70 cm FM (438 MHz, 12.5 kHz)", freq_mhz: 438.5, bw_khz: 12.5, mode: "fm", req_snr_db: 10, tx_power_dbm: 37, nf_db: 5 },
  pmr446: { label: "PMR446 (446.1 MHz, 0.5 W)", freq_mhz: 446.1, bw_khz: 12.5, mode: "fm", req_snr_db: 10, tx_power_dbm: 27, nf_db: 6 },
  hamnet5: { label: "HAMNET 5.7 GHz (10 MHz, MCS0)", freq_mhz: 5765, bw_khz: 10000, mode: "custom", req_snr_db: 5, tx_power_dbm: 23, nf_db: 7 },
  wifi24: { label: "Wi-Fi 2.4 GHz (20 MHz, MCS0)", freq_mhz: 2442, bw_khz: 20000, mode: "custom", req_snr_db: 5, tx_power_dbm: 20, nf_db: 7 },
  custom: { label: "Custom" },
};
/** Required SNR (dB) for LoRa demodulation per spreading factor (Semtech data sheets). */
export const LORA_REQUIRED_SNR = { 5: -2.5, 6: -5, 7: -7.5, 8: -10, 9: -12.5, 10: -15, 11: -17.5, 12: -20 };
const STATION_COLOURS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46c0c8", "#f032e6", "#9a6324", "#808000", "#000075"];

/** Receiver sensitivity of a station's radio: thermal noise + bandwidth + noise figure + required SNR. */
export function sensitivityDbm(st) {
  if (st.sens_override_dbm !== null && st.sens_override_dbm !== "" && st.sens_override_dbm !== undefined) return +st.sens_override_dbm;
  const snr = st.mode === "lora" ? (LORA_REQUIRED_SNR[st.sf] ?? -17.5) : +(st.req_snr_db ?? 10);
  return -174 + 10 * Math.log10(st.bw_khz * 1000) + (+st.nf_db || 6) + snr;
}

/** Vertical beam width estimated from the gain when the data sheet gives none. */
export function estimatedVerticalBeamwidth(antenna) {
  const g = +antenna.gain_dbi || 0;
  if (antenna.type === "omni") return Math.max(3, Math.min(120, 78 / 10 ** ((g - 2.15) / 10)));
  return Math.max(2, Math.min(120, 32400 / ((+antenna.hbw_deg || 60) * 10 ** (g / 10))));
}

/** Copy an antenna from the catalog into a station (values are copied, the id is remembered). */
export function applyCatalogAntenna(st, catalogId) {
  const a = state.catalog.antennas.find((x) => x.id === catalogId);
  if (!a) return;
  const keep = { azimuth_deg: st.antenna?.azimuth_deg ?? 0, sector_on: st.antenna?.sector_on, sector_az_deg: st.antenna?.sector_az_deg,
    sector_width_deg: st.antenna?.sector_width_deg, sector_loss_db: st.antenna?.sector_loss_db };
  st.antenna = { ...keep, catalog_id: a.id, type: a.type, gain_dbi: a.gain_dbi, hbw_deg: a.hbw_deg ?? (a.type === "directional" ? 60 : undefined),
    vbw_deg: a.vbw_deg ?? null, tilt_deg: a.tilt_deg ?? 0, fb_db: a.fb_db ?? 25 };
}

export function newStation(lat, lon) {
  const stations = state.project.stations;
  const previous = stations[stations.length - 1];
  const template = previous ? JSON.parse(JSON.stringify(previous)) : {
    height_m: 10, height_ref: "ground", radio_preset: "meshcore_eu", ...withoutLabel(RADIO_PRESETS.meshcore_eu),
    cable_loss_db: 1, radius_km: 10, sens_override_dbm: null, req_snr_db: 10, antenna: {},
  };
  if (!previous) { const st = { antenna: {} }; applyCatalogAntenna(st, "collinear-5.8"); template.antenna = st.antenna.type ? st.antenna : { type: "omni", gain_dbi: 5.8 }; }
  delete template.corescope_pk;
  if (isUser()) template.owner = "user"; else delete template.owner;
  const n = stations.length;
  return { ...template, id: randomId(), name: t("Station {n}", { n: n + 1 }), color: STATION_COLOURS[n % STATION_COLOURS.length],
    enabled: true, lat: +lat.toFixed(6), lon: +lon.toFixed(6), notes: "" };
}
export function withoutLabel(o) { const c = { ...o }; delete c.label; return c; }

// ---------------------------------------------------------------- markers
const markers = {};       // station id -> marker
const antennaLayers = {}; // station id -> layer group (direction, sector)

function stationIcon(st) {
  const c = st.color || "#3388ff";
  return L.divIcon({ className: "", iconSize: [26, 34], iconAnchor: [13, 33],
    html: `<svg width="26" height="34" viewBox="0 0 26 34"><path d="M13 33 L4 15 A10 10 0 1 1 22 15 Z" fill="${c}" stroke="#fff" stroke-width="2"/><circle cx="13" cy="11" r="4" fill="#fff"/></svg>` });
}

let onStationClick = () => {};
/** Set what happens when a station marker is clicked (wired in main.js). */
export function setStationClickHandler(fn) { onStationClick = fn; }

export function removeStationMarker(id) {
  if (markers[id]) { map.removeLayer(markers[id]); delete markers[id]; }
  if (antennaLayers[id]) { map.removeLayer(antennaLayers[id]); delete antennaLayers[id]; }
}

export function drawStation(st) {
  removeStationMarker(st.id);
  if (st.enabled === false) return;   // inactive stations are not shown
  const marker = L.marker([st.lat, st.lon], { icon: stationIcon(st), title: st.name });
  marker.on("click", (e) => { L.DomEvent.stopPropagation(e); onStationClick(st); });
  marker.on("contextmenu", (e) => { L.DomEvent.stopPropagation(e); onStationClick(st, { menu: true }); });
  marker.addTo(map);
  markers[st.id] = marker;
  const a = st.antenna || {};
  const layers = [];
  if (a.type === "directional") layers.push(...directionLayers(st, a));
  if (a.sector_on) layers.push(sectorLayer(st, a));
  if (layers.length) antennaLayers[st.id] = L.layerGroup(layers).addTo(map);
}

/** Wedge + line along the main beam; a handle at its end turns the antenna (members only). */
function directionLayers(st, a) {
  const len = 80 * metresPerPixel(st.lat);   // pixels, so the handle sits beside the station at any zoom
  const shape = (az) => {
    const half = (a.hbw_deg || 60) / 2;
    return { line: [[st.lat, st.lon], destinationPoint(st.lat, st.lon, az, len)],
      wedge: [[st.lat, st.lon], destinationPoint(st.lat, st.lon, az - half, len * 0.8), destinationPoint(st.lat, st.lon, az + half, len * 0.8)] };
  };
  const g0 = shape(a.azimuth_deg || 0);
  const wedge = L.polygon(g0.wedge, { color: st.color, weight: 1, fillOpacity: 0.12, interactive: false });
  const line = L.polyline(g0.line, { color: st.color, weight: 3, interactive: false });
  const out = [wedge, line];
  if (canEditStation(st)) {
    const handle = L.marker(g0.line[1], { draggable: true, zIndexOffset: 500, title: t("Turn antenna"),
      icon: L.divIcon({ className: "", iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="az-handle" style="border-color:${st.color}"></div>` }) });
    handle.bindTooltip("", { direction: "top", offset: [0, -8] });
    handle.on("drag", () => {
      const ll = handle.getLatLng();
      const az = Math.round(bearing(st.lat, st.lon, ll.lat, ll.lng));
      a.azimuth_deg = az;
      const g = shape(az);
      wedge.setLatLngs(g.wedge); line.setLatLngs(g.line);
      handle.setTooltipContent(t("Azimuth {n}°", { n: az })); handle.openTooltip();
    });
    handle.on("dragend", () => { handle.setLatLng(shape(a.azimuth_deg).line[1]); handle.closeTooltip(); emit("project-changed", { station: st }); });
    out.push(handle);
  }
  return out;
}

/** #23: open radiation sector as a dashed fan. */
function sectorLayer(st, a) {
  const len = 55 * metresPerPixel(st.lat), width = Math.min(359, Math.max(10, a.sector_width_deg ?? 180)), centre = a.sector_az_deg ?? 270;
  const pts = [[st.lat, st.lon]];
  for (let d = -width / 2; d <= width / 2 + 0.01; d += Math.max(2, width / 36)) pts.push(destinationPoint(st.lat, st.lon, centre + d, len));
  return L.polygon(pts, { color: st.color || "#2563eb", weight: 1.2, dashArray: "4 3", fillOpacity: 0.1, interactive: false });
}

export function redrawAllStations() {
  for (const id of Object.keys(markers)) removeStationMarker(id);
  for (const st of state.project?.stations || []) drawStation(st);
}

map.on("zoomend", () => {
  for (const st of state.project?.stations || []) if (st.antenna?.type === "directional" || st.antenna?.sector_on) drawStation(st);
});

// ---------------------------------------------------------------- placing and moving
let placing = null;   // {mode: "new"} | {mode: "move", id}
export const isPlacing = () => !!placing;

export function startPlacing(mode = "new", stationId = null) {
  placing = { mode, id: stationId };
  const banner = $("placingBanner");
  banner.textContent = mode === "move" ? t("Click the new location of the station on the map (Esc cancels)") : t("Click on the map to place the station (Esc cancels)");
  banner.classList.remove("hidden");
  $("map").classList.add("placing");
  document.body.classList.remove("sheet-open", "sheet-full");
}
export function stopPlacing() {
  placing = null;
  $("placingBanner").classList.add("hidden");
  $("map").classList.remove("placing");
}

/** Called by the map click handler; returns the station placed or moved, or null. */
export function finishPlacing(latlng) {
  if (!placing) return null;
  const p = placing;
  stopPlacing();
  if (p.mode === "move") {
    const st = stationById(p.id);
    if (!st) return null;
    st.lat = +latlng.lat.toFixed(6); st.lon = +latlng.lng.toFixed(6);
    drawStation(st); emit("project-changed", { station: st });
    return st;
  }
  const st = newStation(latlng.lat, latlng.lng);
  state.project.stations.push(st);
  drawStation(st); emit("stations-changed"); emit("project-changed", { station: st });
  return st;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && placing) stopPlacing(); });
