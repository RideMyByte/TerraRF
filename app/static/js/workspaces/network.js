/**
 * Workspace "Network": the real MeshCore network from CoreScope – find a repeater, show its neighbours,
 * filter the links and keep the node list up to date.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml, fmt, fmtAgo, snrColor } from "../core/util.js";
import { on, isAdmin } from "../core/state.js";
import { api } from "../core/api.js";
import { status } from "../core/ui.js";
import { map } from "../map/map.js";
import { net, enabled, visibleLinks, drawNetwork, loadNodes, refreshStatus, repeaterName, nodeByPk, clearNetworkSelection } from "../network.js";
import { inspectRepeater, inspectLink } from "../inspector.js";

const root = () => $("ws-network");
let lastStatus = null, searchText = "";

export async function renderNetwork() {
  const el = root();
  if (!enabled()) {
    el.innerHTML = `<h2>${t("Network")}</h2><p class="muted">${t("No CoreScope server is configured. Set RFP_CORESCOPE_URL to see the real MeshCore network (repeaters, neighbours, SNR).")}</p>`;
    return;
  }
  const f = net.filter;
  const centre = net.centrePk;
  const links = visibleLinks().sort((a, b) => (b.snr ?? b.snr_any ?? -99) - (a.snr ?? a.snr_any ?? -99));
  el.innerHTML = `
    <h2>${t("Network")}</h2>
    <div class="calc" id="csStatus">${statusText(lastStatus)}</div>
    ${isAdmin() ? `<button id="btnSync">⟳ ${t("Update nodes from CoreScope")}</button>` : ""}
    <h4>${t("Find a repeater")}</h4>
    <input id="nodeSearch" type="search" value="${escapeHtml(searchText)}" placeholder="${escapeHtml(t("name or key"))}">
    <div id="nodeHits" class="hits"></div>
    <h4>${centre ? t("Neighbours of {name}", { name: escapeHtml(net.links?.name || repeaterName(centre)) }) : t("Neighbours")}</h4>
    ${!centre ? `<p class="muted">${t("Tap a repeater on the map and choose “Show neighbours”.")}</p>`
      : net.loadingPk ? `<div class="muted wait">${t("loading")}</div>`
      : net.linksError ? `<div class="warn">${escapeHtml(net.linksError)}</div>`
      : `<div class="grid2">
          <label class="chk"><input type="checkbox" id="fBoth" ${f.bothWays ? "checked" : ""}> ${t("only both ways")}</label>
          <label class="chk"><input type="checkbox" id="fDirect" ${f.keepDirect ? "checked" : ""}> ${t("always keep direct SNR")}</label>
          <label>${t("min. heard by neighbour")} <b id="vTx">${f.minTx}</b><input type="range" id="fTx" min="0" max="50" value="${f.minTx}"></label>
          <label>${t("min. heard from neighbour")} <b id="vRx">${f.minRx}</b><input type="range" id="fRx" min="0" max="50" value="${f.minRx}"></label>
          <label>${t("min. SNR")} <b id="vSnr">${f.minSnr <= -21 ? t("off") : f.minSnr + " dB"}</b><input type="range" id="fSnr" min="-21" max="15" value="${f.minSnr}"></label>
        </div>
        <table class="mini clickable"><tr><th>${t("Repeater")}</th><th>km</th><th>SNR</th><th title="${escapeHtml(t("heard by the neighbour / from the neighbour"))}">→/←</th><th>${t("last")}</th></tr>
        ${links.map((l) => `<tr data-pk="${escapeHtml(l.pk)}"><td class="ellipsis" title="${escapeHtml(l.name || l.pk)}">${escapeHtml(l.name || l.pk.slice(0, 8))}</td><td>${fmt(l.distance_km, 1)}</td>
          <td style="color:${snrColor(l.snr ?? l.snr_any)}">${l.snr != null ? fmt(l.snr, 1) : l.snr_any != null ? `<i>${fmt(l.snr_any, 1)}</i>` : "–"}</td>
          <td style="white-space:nowrap">${l.they_hear}/${l.we_hear}</td><td>${l.last_seen ? fmtAgo(l.last_seen) : "–"}</td></tr>`).join("") || `<tr><td colspan="5" class="muted wrap">${t("No neighbour matches the filters.")}</td></tr>`}</table>
        <p class="muted">${t("Upright: SNR directly reported (zero hop). Italic: from packet paths, uncertain. Tap a row for the link details and profile.")}</p>
        <button id="btnClearNb">${t("Hide neighbours")}</button>`}`;
  const sync = el.querySelector("#btnSync");
  if (sync) sync.onclick = syncNodes;
  const search = el.querySelector("#nodeSearch");
  search.oninput = () => { searchText = search.value; renderHits(); };
  renderHits();
  el.querySelectorAll("tr[data-pk]").forEach((tr) => tr.onclick = () => inspectLink(centre, tr.dataset.pk));
  const clear = el.querySelector("#btnClearNb");
  if (clear) clear.onclick = clearNetworkSelection;
  const bind = (id, key, label, fn = (v) => v) => {
    const input = el.querySelector("#" + id); if (!input) return;
    input.oninput = () => { f[key] = +input.value; el.querySelector("#" + label).textContent = fn(+input.value); drawNetwork(); };
    input.onchange = renderNetwork;
  };
  bind("fTx", "minTx", "vTx"); bind("fRx", "minRx", "vRx"); bind("fSnr", "minSnr", "vSnr", (v) => v <= -21 ? t("off") : v + " dB");
  for (const [id, key] of [["fBoth", "bothWays"], ["fDirect", "keepDirect"]]) {
    const box = el.querySelector("#" + id);
    if (box) box.onchange = () => { f[key] = box.checked; drawNetwork(); renderNetwork(); };
  }
}

function renderHits() {
  const box = root().querySelector("#nodeHits"); if (!box) return;
  const q = searchText.trim().toLowerCase();
  if (q.length < 2) { box.innerHTML = ""; return; }
  const hits = net.nodes.filter((n) => (n.name || "").toLowerCase().includes(q) || n.pk.startsWith(q)).slice(0, 12);
  box.innerHTML = hits.map((n) => `<button data-pk="${escapeHtml(n.pk)}" class="hit">${escapeHtml(n.name || n.pk.slice(0, 8))} <span class="muted">${escapeHtml(n.role || "")} · ${fmtAgo(n.last_seen)}</span></button>`).join("")
    || `<p class="muted">${t("nothing found")}</p>`;
  box.querySelectorAll("button").forEach((b) => b.onclick = () => {
    const n = nodeByPk(b.dataset.pk);
    if (n) map.setView([n.lat, n.lon], Math.max(map.getZoom(), 13));
    inspectRepeater(b.dataset.pk);
  });
}

function statusText(s) {
  if (!s) return t("CoreScope: status unknown");
  if (s.error) return `<span class="warn">CoreScope: ${escapeHtml(s.error)}</span>`;
  return t("CoreScope: {n} nodes · updated {ago}", { n: s.count ?? net.nodes.length, ago: fmtAgo(s.fetched_at) }) + (s.syncing ? ` · ${t("updating …")}` : "");
}

async function syncNodes() {
  try { await api("/api/corescope/sync", { method: "POST" }); } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); return; }
  status(t("CoreScope: updating nodes …"), "busy");
  const timer = setInterval(async () => {
    const s = await refreshStatus().catch(() => null);
    lastStatus = s; renderNetwork();
    if (s && !s.syncing) { clearInterval(timer); await loadNodes(); status(t("CoreScope: {n} nodes loaded.", { n: s.count })); }
  }, 2000);
}

export async function initNetworkWorkspace() {
  if (!enabled()) { renderNetwork(); return; }
  lastStatus = await refreshStatus().catch((e) => ({ error: e.message }));
  if (lastStatus && lastStatus.count) await loadNodes().catch(() => {});
  renderNetwork();
}

for (const ev of ["network-loaded", "neighbours-loaded"]) on(ev, renderNetwork);
on("network-focus", () => { if (net.loadingPk) renderNetwork(); });
