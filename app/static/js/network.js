/**
 * The real network from CoreScope: repeater nodes, neighbours of a selected repeater, link lines.
 *
 * Two kinds of selection:
 *   focusPk   – a repeater that was tapped: big marker, details in the inspector
 *   centrePk  – the repeater whose neighbours are shown (all other nodes are hidden meanwhile)
 *
 * SNR of a link: "direct" = the partner's own neighbour table reports it (zero hop, used for comparison);
 * "path" = derived from packet paths (last hop only, ambiguous prefixes – shown, never compared).
 */
import { t } from "./core/i18n.js";
import { $, escapeHtml, fmt, fmtAgo, ageDays, snrColor, distanceKm } from "./core/util.js";
import { state, on, emit, stationOfRepeater } from "./core/state.js";
import { api } from "./core/api.js";
import { status } from "./core/ui.js";
import { map, registerLayerSection, refreshLayersPanel } from "./map/map.js";

export const net = {
  nodes: [], overrides: {}, meta: {},
  centrePk: null, focusPk: null, links: null, linksError: null, loadingPk: null, muted: false,
  filter: { bothWays: false, keepDirect: true, minTx: 0, minRx: 0, minSnr: -21, maxAgeDays: 7, role: "" },
  show: { nodes: true, lines: true, lineOpacity: 0.55 },
};
const renderer = L.canvas({ padding: 0.3 });
const nodeLayer = L.layerGroup().addTo(map);
let bigMarkers = null;
let handlers = { node: () => {}, link: () => {} };

export const enabled = () => !!state.config?.corescope?.enabled;
export const nodeByPk = (pk) => net.nodes.find((x) => x.pk === pk);
export const linkTo = (pk) => net.links?.links.find((l) => l.pk === pk);
export function repeaterName(pk, prefix) { const n = pk && nodeByPk(pk); return n ? n.name : (pk || prefix || "?").slice(0, 8); }

/** What happens on a tap on a node or a link line (wired by main.js to the inspector). */
export function setNetworkHandlers(h) { handlers = { ...handlers, ...h }; }

export function snrText(link) {
  if (link.snr != null) return t("SNR {v} dB, directly reported (zero hop)", { v: fmt(link.snr, 1) });
  if (link.snr_any != null) return t("SNR {v} dB from packet paths (uncertain, not compared)", { v: fmt(link.snr_any, 1) });
  return t("no SNR");
}

export function nodePosition(pk) {
  const st = stationOfRepeater(pk); if (st) return [st.lat, st.lon];
  const n = nodeByPk(pk); if (n) return [n.lat, n.lon];
  const l = linkTo(pk); if (l) return [l.lat, l.lon];
  if (net.links && net.links.pk === pk && net.links.lat != null) return [net.links.lat, net.links.lon];
  return null;
}

/** Neighbours of the centre repeater after the filters (same set for map, list and comparison). */
export function visibleLinks() {
  if (!net.links) return [];
  const f = net.filter;
  return net.links.links.filter((l) => {
    if (f.keepDirect && l.snr != null) return true;
    if (f.bothWays && !l.bidir) return false;
    if (l.we_hear < f.minRx || l.they_hear < f.minTx) return false;
    const s = l.snr ?? l.snr_any;
    if (f.minSnr > -21 && (s == null || s < f.minSnr)) return false;
    return true;
  });
}

export function nearbyNodes(lat, lon, maxKm = 3, max = 15) {
  return net.nodes.map((n) => ({ node: n, km: distanceKm(lat, lon, n.lat, n.lon) })).filter((x) => x.km <= maxKm)
    .sort((a, b) => a.km - b.km).slice(0, max);
}

const nodeColour = (days) => days <= 1 ? "#16a34a" : days <= 7 ? "#d97706" : "#9ca3af";

export function drawNetwork() {
  nodeLayer.clearLayers();
  if (bigMarkers) { map.removeLayer(bigMarkers); bigMarkers = null; }
  if (!enabled()) return;
  const big = [net.centrePk, net.focusPk !== net.centrePk ? net.focusPk : null].filter(Boolean);
  bigMarkers = L.layerGroup().addTo(map);
  for (const pk of big) {
    const pos = nodePosition(pk); if (!pos) continue;
    const st = stationOfRepeater(pk);
    const m = L.marker(pos, { zIndexOffset: st ? -100 : 1000, interactive: !st,
      icon: L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 15],
        html: `<div class="node-sel${st ? " ring" : ""}${pk === net.centrePk ? " center" : ""}${net.loadingPk === pk ? " loading" : ""}" title="${escapeHtml(pk === net.centrePk ? t("Neighbours of this repeater are shown") : t("Selected repeater"))}"></div>` }) });
    if (!st) m.on("click", (e) => { L.DomEvent.stopPropagation(e); handlers.node(pk); });
    bigMarkers.addLayer(m);
  }
  const drawn = new Set(big);
  if (net.centrePk && net.links) {
    const centre = nodePosition(net.centrePk), links = visibleLinks();
    if (centre && net.show.lines) {
      for (const l of links) {
        const s = l.snr ?? l.snr_any;
        // a wide invisible line on top makes the thin line easy to tap (#26)
        const visible = L.polyline([centre, [l.lat, l.lon]], { renderer, interactive: false, weight: l.snr != null ? 2 : 1.2,
          color: s != null ? snrColor(s) : "#9ca3af", opacity: net.muted ? Math.min(0.12, net.show.lineOpacity) : net.show.lineOpacity });
        const hit = L.polyline([centre, [l.lat, l.lon]], { renderer, weight: 12, opacity: 0, interactive: true });
        hit.bindTooltip(`${escapeHtml(l.name || l.pk.slice(0, 8))} · ${fmt(l.distance_km, 1)} km`, { sticky: true });
        hit.on("click", (e) => { L.DomEvent.stopPropagation(e); handlers.link(net.centrePk, l.pk); });
        nodeLayer.addLayer(visible); nodeLayer.addLayer(hit);
      }
    }
    for (const l of links) {
      if (stationOfRepeater(l.pk) || drawn.has(l.pk)) continue;
      const s = l.snr ?? l.snr_any, direct = l.snr != null;
      const m = L.circleMarker([l.lat, l.lon], { renderer, radius: direct ? 9 : 8, weight: direct ? 2.5 : 1.5,
        color: direct ? "#111827" : "#fff", dashArray: direct ? null : "3 2", fillColor: s != null ? snrColor(s) : "#9ca3af", fillOpacity: 0.95 });
      m.bindTooltip(`${escapeHtml(l.name || l.pk.slice(0, 8))} · ${fmt(l.distance_km, 1)} km<br>${snrText(l)}<br>→ ${l.they_hear}× · ← ${l.we_hear}×`, { direction: "top" });
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); handlers.node(l.pk); });
      nodeLayer.addLayer(m);
    }
    return;
  }
  if (!net.show.nodes) return;
  for (const n of net.nodes) {
    const days = ageDays(n.last_seen);
    if ((net.filter.maxAgeDays && days > net.filter.maxAgeDays) || (net.filter.role && n.role !== net.filter.role) || stationOfRepeater(n.pk) || drawn.has(n.pk)) continue;
    const m = L.circleMarker([n.lat, n.lon], { renderer, radius: 7, weight: 1.5, color: "#fff", fillColor: nodeColour(days), fillOpacity: 0.95 });
    m.bindTooltip(escapeHtml(n.name), { direction: "top" });
    m.on("click", (e) => { L.DomEvent.stopPropagation(e); handlers.node(n.pk); });
    nodeLayer.addLayer(m);
  }
}

export async function loadNodes() {
  if (!enabled()) return;
  const [r, o] = await Promise.all([api("/api/corescope/nodes"), api("/api/corescope/overrides").catch(() => ({}))]);
  net.nodes = r.nodes || []; net.overrides = o || {};
  drawNetwork(); emit("network-loaded");
}

export async function loadMeta(pk) {
  if (net.meta[pk]) return net.meta[pk];
  try { net.meta[pk] = await api(`/api/corescope/node/${pk}`); } catch (e) { net.meta[pk] = { error: e.message }; }
  emit("network-focus", { pk: net.focusPk });
  return net.meta[pk];
}

export function setFocus(pk) {
  net.focusPk = pk;
  drawNetwork(); emit("network-focus", { pk });
  if (pk) loadMeta(pk);
}

/** Load and show the neighbours of a repeater (other nodes are hidden meanwhile). */
export async function showNeighbours(pk) {
  net.muted = false;
  if (!enabled()) return;
  net.centrePk = pk; net.links = null; net.loadingPk = pk; net.linksError = null;
  if (net.focusPk === pk) net.focusPk = null;
  drawNetwork(); emit("network-focus", { pk: net.focusPk });
  status(t("MeshCore: loading neighbours …"), "busy");
  try {
    const r = await api(`/api/corescope/links/${pk}`);
    if (net.centrePk !== pk) return;
    net.links = r;
    status(t("MeshCore: {n} neighbours (after filters).", { n: visibleLinks().length }));
  } catch (e) {
    if (net.centrePk === pk) { net.linksError = e.message; status("MeshCore: " + e.message, "err"); }
  } finally {
    if (net.loadingPk === pk) net.loadingPk = null;
  }
  if (net.centrePk === pk) { drawNetwork(); emit("network-focus", { pk: net.focusPk }); emit("neighbours-loaded", { pk }); }
}

export function clearNetworkSelection() {
  net.centrePk = null; net.focusPk = null; net.links = null; net.loadingPk = null;
  drawNetwork(); emit("network-focus", { pk: null }); emit("neighbours-loaded", { pk: null });
}

/** Fade the neighbour lines (e.g. while drive-test points are compared, #40); showing neighbours again ends it. */
export function muteLines(on) { net.muted = !!on; drawNetwork(); }

export async function saveOverride(pk, values) {
  const r = await api(`/api/corescope/overrides/${pk}`, { method: "PUT", body: values });
  if (Object.keys(r.values).length) net.overrides[pk] = r.values; else delete net.overrides[pk];
  return r.values;
}

export async function refreshStatus() {
  if (!enabled()) return null;
  return api("/api/corescope/status");
}

// ---------------------------------------------------------------- layers panel section
registerLayerSection("network", 20, (el) => {
  if (!enabled()) return;
  const f = net.filter, s = net.show;
  el.innerHTML = `<h4>${t("MeshCore network")}</h4>
    <label class="chk"><input type="checkbox" id="lyNodes" ${s.nodes ? "checked" : ""}> ${t("Repeaters")} <span class="muted">(${net.nodes.length})</span></label>
    <div class="grid2"><label>${t("Heard within")}<select id="lyAge">${[[1, "1 day"], [7, "7 days"], [30, "30 days"], [0, "any time"]].map(([v, l]) => `<option value="${v}" ${f.maxAgeDays === v ? "selected" : ""}>${t(l)}</option>`).join("")}</select></label>
      <label>${t("Role")}<select id="lyRole"><option value="">${t("all")}</option>${["repeater", "room", "companion", "sensor"].map((r) => `<option ${f.role === r ? "selected" : ""}>${r}</option>`).join("")}</select></label></div>
    <label class="chk"><input type="checkbox" id="lyLines" ${s.lines ? "checked" : ""}> ${t("Lines to neighbours")}</label>
    <label>${t("Line opacity")}<input id="lyLineOp" type="range" min="0.1" max="1" step="0.05" value="${s.lineOpacity}"></label>
    <div class="legend-net"><span><i class="c" style="background:#16a34a"></i>${t("heard today")}</span><span><i class="c" style="background:#d97706"></i>${t("this week")}</span><span><i class="c" style="background:#9ca3af"></i>${t("older")}</span></div>`;
  el.querySelector("#lyNodes").onchange = (e) => { s.nodes = e.target.checked; drawNetwork(); };
  el.querySelector("#lyAge").onchange = (e) => { f.maxAgeDays = +e.target.value; drawNetwork(); };
  el.querySelector("#lyRole").onchange = (e) => { f.role = e.target.value; drawNetwork(); };
  el.querySelector("#lyLines").onchange = (e) => { s.lines = e.target.checked; drawNetwork(); };
  el.querySelector("#lyLineOp").oninput = (e) => { s.lineOpacity = +e.target.value; drawNetwork(); };
});

on("stations-changed", drawNetwork);
on("network-loaded", refreshLayersPanel);

/** Adopt a repeater as a station of the project (admin). */
export function nodeSummary(pk) {
  const n = nodeByPk(pk) || linkTo(pk) || {};
  return { pk, name: n.name || pk.slice(0, 8), lat: n.lat, lon: n.lon, role: n.role, last_seen: n.last_seen };
}
export const ageText = (pk) => fmtAgo(nodeByPk(pk)?.last_seen);

