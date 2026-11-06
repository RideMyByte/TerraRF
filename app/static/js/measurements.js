/**
 * Drive-test measurements on the map: points (soft colour blobs, #20), the measurement plan grid, focus on
 * one repeater or series, and grouping into pings (#27).
 *
 * One stored row = one repeater heard at one place. MeshMapper reports one ping heard by several repeaters as
 * several rows; on the map they become one point ("ping") that lists every repeater, strongest first.
 */
import { t } from "./core/i18n.js";
import { escapeHtml, fmt, signed, snrColor, deltaColor, fmtDateTime } from "./core/util.js";
import { state, on, emit, isAdmin } from "./core/state.js";
import { api } from "./core/api.js";
import { sourceBadge, sourceName } from "./core/ui.js";
import { map, registerLayerSection, refreshLayersPanel } from "./map/map.js";
import { repeaterName } from "./network.js";

export const meas = {
  rows: [], plan: [], series: [], stats: null,
  allowed: false,                // may this visitor see measurements?
  focusPk: null,                 // only this repeater (set when a repeater is selected, #22)
  seriesId: null,                // only this series
  days: null,                    // time filter
  comparison: null,              // {id: row} after a drive-test comparison
  show: { points: false, plan: false },
};
const pointLayer = L.layerGroup();
const planLayer = L.layerGroup();
const renderer = L.svg({ padding: 0.3 });
let loadSeq = 0;
let onPingClick = () => {};
export function setPingHandler(fn) { onPingClick = fn; }

export const seriesById = (id) => meas.series.find((s) => s.id === id);
export function seriesName(s) {
  if (!s) return "–";
  if (s.name) return s.name;
  return `${sourceName(s.src)}${s.t0 ? " " + fmtDateTime(s.t0) : ""}`;
}

/**
 * Receiver of one measurement row – same precedence as the server's comparison:
 * height in the row > values of its series > device/placement from the catalog > global receiver.
 */
export function receiverFor(row, s) {
  const d = state.config?.defaults || {};
  const dev = s && state.catalog.devices.find((x) => x.id === s.device_id);
  const plc = s && state.catalog.placements.find((x) => x.id === s.placement_id);
  let deviceGain = dev?.antenna_gain_dbi ?? (dev?.antenna_id ? state.catalog.antennas.find((a) => a.id === dev.antenna_id)?.gain_dbi : null);
  if (deviceGain != null && plc?.gain_bonus_db) deviceGain += plc.gain_bonus_db;
  const profileLoss = dev?.cable_db != null || plc?.extra_loss_db != null ? (dev?.cable_db || 0) + (plc?.extra_loss_db || 0) : null;
  return {
    h: row?.rx_h ?? s?.rx_h ?? plc?.height_m ?? d.rx_height_m,
    g: s?.rx_gain_dbi ?? deviceGain ?? d.rx_gain_dbi,
    c: s?.rx_cable_db ?? profileLoss ?? d.rx_cable_db,
  };
}

/** Receiver of a series in words: height, gain, loss, device and placement. */
export function receiverText(s) {
  if (!s) return t("global receiver");
  const dev = state.catalog.devices.find((x) => x.id === s.device_id);
  const plc = state.catalog.placements.find((x) => x.id === s.placement_id);
  const r = receiverFor(null, s);
  const parts = [`${fmt(r.h, 1)} m`, `${fmt(r.g, 1)} dBi`];
  if (r.c) parts.push(`−${fmt(r.c, 1)} dB`);
  if (dev) parts.push(dev.name);
  if (plc) parts.push(plc.name);
  if (s.ext_ant) parts.push(t("external antenna"));
  return parts.join(" · ");
}

// ---------------------------------------------------------------- SVG radial gradients (#20)
function gradient(colour) {
  const id = "mg" + colour.replace("#", "");
  const svg = renderer._container;
  if (svg && !svg.querySelector("#" + id)) {
    let defs = svg.querySelector("defs");
    if (!defs) { defs = document.createElementNS("http://www.w3.org/2000/svg", "defs"); svg.insertBefore(defs, svg.firstChild); }
    const g = document.createElementNS("http://www.w3.org/2000/svg", "radialGradient");
    g.setAttribute("id", id);
    g.innerHTML = `<stop offset="0" stop-color="${colour}" stop-opacity="0.95"/><stop offset="0.45" stop-color="${colour}" stop-opacity="0.7"/><stop offset="1" stop-color="${colour}" stop-opacity="0"/>`;
    defs.appendChild(g);
  }
  return `url(#${id})`;
}

// ---------------------------------------------------------------- pings (#27)
/** Rows of the same ping: same series, within 10 s and ~30 m. */
export function groupPings(rows) {
  const sorted = [...rows].sort((a, b) => (a.ts || 0) - (b.ts || 0));
  const pings = [];
  for (const r of sorted) {
    const p = pings.find((x) => x.series_id === r.series_id && Math.abs((x.ts || 0) - (r.ts || 0)) <= 10
      && Math.abs(x.lat - r.lat) < 0.0003 && Math.abs(x.lon - r.lon) < 0.00045);
    if (p) p.rows.push(r);
    else pings.push({ key: `p${r.id}`, ts: r.ts, lat: r.lat, lon: r.lon, series_id: r.series_id, src: r.src, rows: [r] });
  }
  for (const p of pings) p.rows.sort((a, b) => (b.snr_down ?? -99) - (a.snr_down ?? -99));
  return pings;
}

export async function loadMeasurements() {
  const seq = ++loadSeq;
  const q = new URLSearchParams();
  // #53: measurements belong to a project – those of the open project (the admin without a saved project: own ones)
  const projectId = state.project?.id;
  if (!projectId && !isAdmin()) {
    Object.assign(meas, { allowed: false, rows: [], plan: [], series: [], stats: null });
    drawMeasurements(); emit("measurements-loaded"); refreshLayersPanel();
    return;
  }
  if (projectId) q.set("project", projectId);
  if (meas.days) q.set("days", meas.days);
  if (meas.focusPk) q.set("pk", meas.focusPk);
  if (meas.seriesId) q.set("series", meas.seriesId);
  try {
    const r = await api("/api/measurements?" + q);
    if (seq !== loadSeq) return;
    meas.allowed = true;
    Object.assign(meas, { rows: r.rows, plan: r.plan, series: r.series || [], stats: r.stats });
  } catch (e) {
    if (seq !== loadSeq) return;
    if ([400, 401, 403, 404].includes(e.status)) { meas.allowed = false; meas.rows = []; meas.plan = []; meas.series = []; }
    else console.warn("measurements", e);
  }
  drawMeasurements(); emit("measurements-loaded"); refreshLayersPanel();
}

const pointsVisible = () => meas.show.points || !!meas.focusPk || !!meas.seriesId;

export function drawMeasurements() {
  pointLayer.clearLayers(); planLayer.clearLayers();
  if (meas.show.plan) {
    for (const c of meas.plan) {
      L.rectangle([[c.s, c.w], [c.n, c.e]], { weight: 1, color: c.done ? "#16a34a" : "#d97706", fillOpacity: 0.18 })
        .bindTooltip(`${c.done ? t("enough") : t("measure again")} · ${t("{n} measurements, {r} repeaters", { n: c.count, r: c.repeaters })}`).addTo(planLayer);
    }
    planLayer.addTo(map);
  } else map.removeLayer(planLayer);
  if (!pointsVisible()) { map.removeLayer(pointLayer); return; }
  if (!map.hasLayer(renderer)) renderer.addTo(map);
  for (const ping of groupPings(meas.rows)) {
    const best = ping.rows[0];
    const cmp = meas.comparison && meas.comparison[best.id];
    const colour = cmp && !cmp.saturated && cmp.delta_db != null ? deltaColor(cmp.delta_db) : snrColor(best.snr_down);
    const marker = L.circleMarker([ping.lat, ping.lon], { renderer, radius: ping.rows.length > 1 ? 13 : 11, stroke: false, fillColor: gradient(colour), fillOpacity: 1 });
    const extra = ping.rows.length > 1 ? ` · ${t("{n} repeaters", { n: ping.rows.length })}` : "";
    marker.bindTooltip(`${sourceBadge(best.src)} ${escapeHtml(repeaterName(best.rep_pk, best.rep_prefix))} · SNR ${best.snr_down != null ? fmt(best.snr_down, 1) + " dB" : "–"}${extra}` +
      (cmp && cmp.delta_db != null && !cmp.saturated ? ` · Δ ${signed(cmp.delta_db)} dB` : ""), { direction: "top" });
    marker.on("click", (e) => { L.DomEvent.stopPropagation(e); onPingClick(ping); });
    pointLayer.addLayer(marker);
  }
  pointLayer.addTo(map);
}

/** A repeater was selected elsewhere: show its measurements (#22). null = back to all. */
export function focusRepeater(pk) {
  if ((pk || null) === meas.focusPk) return;
  meas.focusPk = pk || null; meas.seriesId = null; meas.comparison = null;
  loadMeasurements();
}
export function focusSeries(id) { meas.seriesId = id || null; meas.comparison = null; loadMeasurements(); }

registerLayerSection("measurements", 30, (el) => {
  if (!meas.allowed) return;
  el.innerHTML = `<h4>${t("Drive tests")}</h4>
    <label class="chk"><input type="checkbox" id="lyPoints" ${meas.show.points ? "checked" : ""}> ${t("Measurement points")} <span class="muted">(${meas.stats?.count ?? 0})</span></label>
    <label class="chk"><input type="checkbox" id="lyPlan" ${meas.show.plan ? "checked" : ""}> ${t("Measurement plan (green = enough, orange = measure again)")}</label>
    <div class="legend-net">${[[5, "≥ 5"], [-5, "−5 … 5"], [-10, "−10 … −5"], [-20, "< −10"]].map(([v, l]) => `<span><i class="c" style="background:${snrColor(v)}"></i>${l} dB</span>`).join("")}</div>`;
  el.querySelector("#lyPoints").onchange = (e) => { meas.show.points = e.target.checked; drawMeasurements(); };
  el.querySelector("#lyPlan").onchange = (e) => { meas.show.plan = e.target.checked; drawMeasurements(); };
});

on("project-loaded", () => { meas.comparison = null; });
