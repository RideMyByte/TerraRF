/**
 * Coverage on the map.
 *
 * Every station is computed once on the server (cached) and delivered as a small data image
 * (R = level dBm + 200, G = line-of-sight class | roof visibility << 2). The browser stacks the active
 * stations itself, so ticking stations, switching views and the margin slider need no server round trip.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml, fmt, hexToRgb } from "../core/util.js";
import { state, on, emit, activeStations, stationById, isMember, settings } from "../core/state.js";
import { apiPatient, projectRef } from "../core/api.js";
import { status, isPhone } from "../core/ui.js";
import { map, registerLayerSection, refreshLayersPanel } from "./map.js";

export const view = { layer: "margin", opacity: 0.6, marginDb: null, soloStationId: null };
export let lastRun = null;          // response of /api/coverage for all active stations
export const resultsByStation = {}; // station id -> entry of lastRun
const stationImages = {};           // "key@factor" -> {px, w, h}
export const busy = {};             // station id -> {state: "queued"|"run", progress, stage}
export const stale = new Set();     // changed since the last computation
const busyMarkers = {};
let coverageLayer = null;
let computing = false, pendingRun = null;

export const requiredMargin = () => view.marginDb ?? +settings().min_margin_db ?? 10;

// ---------------------------------------------------------------- canvas overlay
const CanvasOverlay = L.ImageOverlay.extend({
  initialize(canvas, bounds, options) { this._canvas = canvas; L.ImageOverlay.prototype.initialize.call(this, "", bounds, options); },
  _initImage() {
    const c = this._image = this._canvas;
    L.DomUtil.addClass(c, "leaflet-image-layer");
    if (this._zoomAnimated) L.DomUtil.addClass(c, "leaflet-zoom-animated");
    c.onselectstart = L.Util.falseFn; c.onmousemove = L.Util.falseFn;
    this._updateOpacity();
  },
});

export function clearCoverage() {
  if (coverageLayer) { map.removeLayer(coverageLayer); coverageLayer = null; }
}

export function resetCoverage() {
  clearCoverage();
  lastRun = null;
  for (const k of Object.keys(resultsByStation)) delete resultsByStation[k];
  stale.clear();
  view.soloStationId = null; renderSoloChip();
}

async function loadStationImage(entry) {
  const k = `${entry.key}@${lastRun.factor}`;
  if (stationImages[k]) return stationImages[k];
  const img = new Image();
  img.src = entry.png;
  await img.decode();
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  return (stationImages[k] = { px: g.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height });
}

function shownEntries() {
  if (!lastRun) return [];
  if (view.soloStationId && !activeStations().some((s) => s.id === view.soloStationId)) { view.soloStationId = null; renderSoloChip(); }
  return activeStations().filter((s) => resultsByStation[s.id] && (!view.soloStationId || s.id === view.soloStationId))
    .map((s) => ({ station: s, entry: resultsByStation[s.id], image: stationImages[`${resultsByStation[s.id].key}@${lastRun.factor}`] }))
    .filter((x) => x.image);
}

/** Margin bin edges relative to the required margin (same as the server's legend). */
function marginEdges(req) { return req > 0 ? [0, req, req + 6, req + 12, req + 20] : [0, 6, 12, 20, 30]; }

/** Stack the station images into the chosen view. */
export function composite() {
  const list = shownEntries();
  if (!list.length) { clearCoverage(); return; }
  const req = requiredMargin();
  let gx0 = Infinity, gy0 = Infinity, gx1 = -Infinity, gy1 = -Infinity;
  for (const { entry } of list) { gx0 = Math.min(gx0, entry.gx0); gy0 = Math.min(gy0, entry.gy0); gx1 = Math.max(gx1, entry.gx0 + entry.w); gy1 = Math.max(gy1, entry.gy0 + entry.h); }
  const W = gx1 - gx0, H = gy1 - gy0, N = W * H;
  const bestMargin = new Float32Array(N).fill(-1e9);
  const bestStation = new Int16Array(N).fill(-1);
  const coveredCount = new Uint8Array(N);
  const sight = new Uint8Array(N);
  list.forEach(({ entry, image }, k) => {
    const sens = entry.sensitivity_dbm, ox = entry.gx0 - gx0, oy = entry.gy0 - gy0, px = image.px;
    for (let y = 0; y < image.h; y++) {
      let si = y * image.w * 4, di = (y + oy) * W + ox;
      for (let x = 0; x < image.w; x++, si += 4, di++) {
        const R = px[si];
        if (!R) continue;
        const margin = R - 200 - sens;
        if (margin > bestMargin[di]) { bestMargin[di] = margin; bestStation[di] = k; }
        if (margin >= req) coveredCount[di]++;
        const G = px[si + 1], cls = G & 3, roof = G >> 2;
        const v = cls === 2 ? 3 : cls === 1 ? 2 : roof ? 1 : 0;
        if (v > sight[di]) sight[di] = v;
      }
    }
  });
  const edges = marginEdges(req);
  const marginColours = lastRun.legend.margin.map((x) => hexToRgb(x.color));
  const overlapColours = lastRun.legend.overlap.map((x) => hexToRgb(x.color));
  const stationColours = list.map(({ station }) => hexToRgb(station.color));
  const sightColours = [null, [127, 179, 255], [255, 210, 0], [0, 170, 90]];
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const g = canvas.getContext("2d");
  const out = g.createImageData(W, H), o = out.data;
  for (let i = 0, j = 0; i < N; i++, j += 4) {
    let c = null;
    if (view.layer === "margin") {
      const m = bestMargin[i];
      if (m >= edges[0]) { let b = 0; while (b + 1 < edges.length && m >= edges[b + 1]) b++; c = marginColours[b]; }
    } else if (view.layer === "overlap") {
      if (coveredCount[i]) c = overlapColours[Math.min(coveredCount[i], overlapColours.length) - 1];
    } else if (view.layer === "server") {
      if (bestStation[i] >= 0 && bestMargin[i] >= req) c = stationColours[bestStation[i]];
    } else if (view.layer === "los") {
      if (sight[i]) c = sightColours[sight[i]];
    }
    if (c) { o[j] = c[0]; o[j + 1] = c[1]; o[j + 2] = c[2]; o[j + 3] = 255; }
  }
  g.putImageData(out, 0, 0);
  const cm = lastRun.cell_m, R = 6378137;
  const toLat = (y) => (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI;
  const toLon = (x) => x / R * 180 / Math.PI;
  const bounds = [[toLat(-gy1 * cm), toLon(gx0 * cm)], [toLat(-gy0 * cm), toLon(gx1 * cm)]];
  if (coverageLayer) map.removeLayer(coverageLayer);
  coverageLayer = new CanvasOverlay(canvas, bounds, { opacity: view.opacity, interactive: false }).addTo(map);
  refreshLayersPanel();
}

/** Level, margin and sight at a point for one station (from its data image), or null. */
export function sampleStation(stationId, lat, lon) {
  const entry = resultsByStation[stationId];
  const image = entry && lastRun && stationImages[`${entry.key}@${lastRun.factor}`];
  if (!image) return undefined;   // not computed
  const R = 6378137, cm = lastRun.cell_m;
  const x = lon * Math.PI / 180 * R, y = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) * R;
  const col = Math.floor(x / cm) - entry.gx0, row = Math.floor(-y / cm) - entry.gy0;
  if (col < 0 || row < 0 || col >= image.w || row >= image.h) return null;
  const i = (row * image.w + col) * 4;
  if (!image.px[i]) return null;
  const prx = image.px[i] - 200, G = image.px[i + 1];
  return { prx, margin: prx - entry.sensitivity_dbm, sight: G & 3, roof: !!(G >> 2) };
}

// ---------------------------------------------------------------- computing
function drawBusy(id) {
  const st = stationById(id), b = busy[id];
  if (busyMarkers[id]) { map.removeLayer(busyMarkers[id]); delete busyMarkers[id]; }
  if (!st || !b) return;
  const p = Math.max(0, Math.min(1, b.progress || 0)), C = 2 * Math.PI * 17;
  const html = b.state === "queued"
    ? `<div class="busyq" title="${escapeHtml(t("waiting"))}">⏳</div>`
    : `<svg class="busyring${p > 0 ? "" : " spin"}" width="44" height="44" viewBox="0 0 44 44"><circle cx="22" cy="22" r="17" class="bg"/>
       <circle cx="22" cy="22" r="17" class="fg" stroke-dasharray="${p > 0 ? `${(C * p).toFixed(1)} ${C.toFixed(1)}` : `${(C * 0.25).toFixed(1)} ${C.toFixed(1)}`}"/></svg>
       ${p > 0 ? `<span class="busypct">${Math.round(p * 100)}</span>` : ""}`;
  busyMarkers[id] = L.marker([st.lat, st.lon], { interactive: false, zIndexOffset: 2000, keyboard: false,
    icon: L.divIcon({ className: "busyicon", iconSize: [44, 44], iconAnchor: [22, 36], html }) }).addTo(map);
}
function setBusy(id, value) { if (value) busy[id] = value; else delete busy[id]; drawBusy(id); emit("busy-changed"); }

/** Progress from /api/status while a station computes. */
export function updateProgress(items) {
  for (const it of items) {
    if (it.station && busy[it.station]?.state === "run" && it.progress != null) {
      busy[it.station].progress = it.progress; busy[it.station].stage = it.stage; drawBusy(it.station);
    }
  }
  emit("busy-changed");
}
export const isComputing = () => computing;

function requestBody(ids) {
  const body = { ...projectRef(), station_ids: ids };
  if (isPhone()) body.max_px = 1600;   // phones: coarser image, less memory while compositing
  return body;
}

/**
 * Compute stations one after another (each with progress), then show all active ones together.
 *   only:  ids to compute (default: all active)
 *   force: ids to recompute even if cached
 */
export async function compute({ only = null, force = [] } = {}) {
  const active = activeStations();
  if (!active.length) { clearCoverage(); status(t("No active station.")); return; }
  if (!isMember() && !state.project.id) return;
  if (computing) { pendingRun = pendingRun || { only, force }; return; }
  computing = true; emit("computing", true);
  const queue = only ? active.filter((s) => only.includes(s.id)) : active;
  queue.forEach((st) => setBusy(st.id, { state: "queued" }));
  const t0 = performance.now();
  let computedNew = 0, done = 0;
  const onWait = (n) => status(t("The server is still computing … next try in 10 s ({n})", { n }), "busy");
  try {
    for (const st of queue) {
      setBusy(st.id, { state: "run", progress: 0 });
      status(t("Computing “{name}” ({i}/{n}) …", { name: st.name, i: done + 1, n: queue.length }), "busy");
      const r = await apiPatient("/api/coverage", { method: "POST", body: { ...requestBody([st.id]), force: force.includes(st.id) ? [st.id] : [] } }, onWait);
      if (r.stations[0] && !r.stations[0].cached) computedNew++;
      stale.delete(st.id);
      setBusy(st.id, null);
      done++;
    }
    const r = await apiPatient("/api/coverage", { method: "POST", body: requestBody(active.map((s) => s.id)) }, onWait);
    if (lastRun && lastRun.factor !== r.factor) for (const k of Object.keys(resultsByStation)) delete resultsByStation[k];
    lastRun = r;
    for (const e of r.stations) resultsByStation[e.id] = e;
    await Promise.all(r.stations.map(loadStationImage));
    composite();
    emit("coverage-updated", r);
    status(t("Done in {s} s – {n} computed, {c} from cache.", { s: fmt((performance.now() - t0) / 1000, 1), n: computedNew, c: active.length - computedNew }));
  } catch (e) {
    status(t("Error: {msg}", { msg: e.message }), "err");
  } finally {
    queue.forEach((st) => { if (busy[st.id]) setBusy(st.id, null); });
    // #36: never leave a ring behind (station deleted or moved while it was computing)
    for (const id of Object.keys(busyMarkers)) if (!busy[id]) { map.removeLayer(busyMarkers[id]); delete busyMarkers[id]; }
    computing = false; emit("computing", false);
    if (pendingRun) { const p = pendingRun; pendingRun = null; compute(p); }
  }
}

// ---------------------------------------------------------------- solo view (#19)
/** Show only one station's coverage (null = all). */
export function setSolo(stationId) {
  view.soloStationId = stationId && stationById(stationId) ? stationId : null;
  renderSoloChip();
  if (lastRun) composite();
}
function renderSoloChip() {
  const chip = $("soloChip");
  const st = view.soloStationId && stationById(view.soloStationId);
  chip.classList.toggle("hidden", !st);
  if (!st) return;
  chip.innerHTML = `<span>${t("Only")} <b style="color:${escapeHtml(st.color)}">●</b> ${escapeHtml(st.name)}</span><button type="button">${t("show all stations")}</button>`;
  chip.querySelector("button").onclick = () => setSolo(null);
}

// ---------------------------------------------------------------- layers panel section
registerLayerSection("coverage", 10, (el) => {
  const req = requiredMargin();
  const legend = lastRun?.legend;
  let legendHtml = "";
  if (legend && view.layer === "margin") {
    const edges = marginEdges(req);
    legendHtml = legend.margin.map((x, i) => `<div class="li"><span class="sw" style="background:${x.color}"></span>${i + 1 < edges.length ? `${edges[i]} … ${edges[i + 1]}` : `≥ ${edges[i]}`} dB${req > 0 && i === 0 ? " – " + t("below margin, unreliable") : ""}</div>`).join("")
      + `<div class="muted">${t("Level above sensitivity. Covered from {n} dB (fade/location margin).", { n: req })}</div>`;
  } else if (legend && view.layer === "overlap") {
    legendHtml = legend.overlap.map((x, i) => `<div class="li"><span class="sw" style="background:${x.color}"></span>${x.n}${i === legend.overlap.length - 1 ? "+" : ""} ${t("station(s)")}</div>`).join("");
  } else if (legend && view.layer === "server") {
    legendHtml = activeStations().map((s) => `<div class="li"><span class="sw" style="background:${escapeHtml(s.color)}"></span>${escapeHtml(s.name)}</div>`).join("");
  } else if (legend && view.layer === "los") {
    const labels = [t("Roof visible from the antenna"), t("Line of sight, Fresnel zone obstructed"), t("Fresnel zone ≥ 60 % clear")];
    legendHtml = legend.los.map((x, i) => `<div class="li"><span class="sw" style="background:${x.color}"></span>${escapeHtml(labels[i] || x.label)}</div>`).join("")
      + `<div class="muted">${t("Receiver at {h} m", { h: fmt(settings().rx_height_m) })}</div>`;
  }
  el.innerHTML = `<h4>${t("Coverage")}</h4>
    <select id="viewLayer" style="width:100%">
      <option value="margin">${t("Best level (margin above sensitivity)")}</option>
      <option value="overlap">${t("Overlap (number of stations)")}</option>
      <option value="server">${t("Best server (station colour)")}</option>
      <option value="los">${t("Line of sight / Fresnel zone / roofs")}</option></select>
    <label>${t("Opacity")}<input id="viewOpacity" type="range" min="0" max="1" step="0.05" value="${view.opacity}"></label>
    <label>${t("Required margin")}: <b id="viewMarginValue">${req}</b> dB<input id="viewMargin" type="range" min="-10" max="40" step="1" value="${req}"></label>
    <div class="legend">${legendHtml || `<div class="muted">${t("Not computed yet.")}</div>`}</div>`;
  el.querySelector("#viewLayer").value = view.layer;
  el.querySelector("#viewLayer").onchange = (e) => { view.layer = e.target.value; composite(); refreshLayersPanel(); };
  el.querySelector("#viewOpacity").oninput = (e) => { view.opacity = +e.target.value; if (coverageLayer) coverageLayer.setOpacity(view.opacity); };
  el.querySelector("#viewMargin").oninput = (e) => { el.querySelector("#viewMarginValue").textContent = e.target.value; };
  el.querySelector("#viewMargin").onchange = (e) => { view.marginDb = +e.target.value; composite(); emit("margin-changed"); };
});

on("project-loaded", () => resetCoverage());
