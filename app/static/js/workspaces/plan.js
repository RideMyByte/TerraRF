/**
 * Workspace "Plan": the project's stations, adding new ones and computing coverage.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml, fmt, fmtAgo } from "../core/util.js";
import { state, on, emit, isMember, isCollaborator, canEditProject } from "../core/state.js";
import { status } from "../core/ui.js";
import { map } from "../map/map.js";
import { compute, resultsByStation, busy, stale, isComputing, composite } from "../map/coverage.js";
import { startPlacing, drawStation } from "../map/stations.js";
import { inspectStation, adoptRepeater } from "../inspector.js";
import { net, enabled as networkEnabled, nearbyNodes } from "../network.js";

// #45: pick repeaters from CoreScope near the map centre and take them over as stations
const picker = { open: false, search: "", chosen: new Set() };

function pickerCandidates() {
  const c = map.getCenter(), q = picker.search.trim().toLowerCase();
  const taken = new Set(state.project.stations.map((s) => s.corescope_pk).filter(Boolean));
  const list = q.length >= 2
    ? net.nodes.filter((n) => (n.name || "").toLowerCase().includes(q) || n.pk.startsWith(q)).map((n) => ({ node: n, km: null })).slice(0, 40)
    : nearbyNodes(c.lat, c.lng, 30, 40);
  return list.filter(({ node }) => !taken.has(node.pk));
}

function pickerHtml() {
  if (!net.nodes.length) return `<div class="calc">${t("No repeaters loaded from CoreScope yet – see workspace “Network”.")}</div>`;
  const list = pickerCandidates();
  return `<div class="calc picker">
    <p class="muted">${t("Repeaters near the map centre (or search by name). Their position comes from CoreScope; height is taken from the roof nearby + 1 m where there is a building, otherwise 10 m – antenna and power are assumptions to check.")}</p>
    <input type="search" id="pickSearch" value="${escapeHtml(picker.search)}" placeholder="${escapeHtml(t("name or key"))}">
    <div class="picklist">${list.map(({ node, km }) => `<label class="chk"><input type="checkbox" data-pk="${escapeHtml(node.pk)}" ${picker.chosen.has(node.pk) ? "checked" : ""}>
      ${escapeHtml(node.name || node.pk.slice(0, 8))} <span class="muted">${km != null ? fmt(km, 1) + " km · " : ""}${fmtAgo(node.last_seen)}</span></label>`).join("")
      || `<p class="muted">${t("nothing found")}</p>`}</div>
    <div class="actions"><button class="primary" id="pickGo" ${picker.chosen.size ? "" : "disabled"}>${t("Take over ({n})", { n: picker.chosen.size })}</button></div></div>`;
}

function bindPicker(el) {
  const search = el.querySelector("#pickSearch");
  if (!search) return;
  search.oninput = () => { picker.search = search.value; const pos = search.selectionStart; renderPlan(); const s2 = root().querySelector("#pickSearch"); s2.focus(); s2.setSelectionRange(pos, pos); };
  el.querySelectorAll(".picklist input").forEach((box) => box.onchange = () => {
    if (box.checked) picker.chosen.add(box.dataset.pk); else picker.chosen.delete(box.dataset.pk);
    el.querySelector("#pickGo").disabled = !picker.chosen.size;
    el.querySelector("#pickGo").textContent = t("Take over ({n})", { n: picker.chosen.size });
  });
  el.querySelector("#pickGo").onclick = async () => {
    const pks = [...picker.chosen];
    let last = null;
    for (const pk of pks) last = (await adoptRepeater(pk, { open: false })) || last;
    picker.open = false; picker.chosen.clear();
    renderPlan();
    if (last) inspectStation(last, "settings");
    status(t("{n} repeater(s) taken over – check height, antenna and power, then save.", { n: pks.length }));
  };
}

const root = () => $("ws-plan");

export function renderPlan() {
  const el = root();
  if (!state.project) { el.innerHTML = ""; return; }
  const stations = state.project.stations;
  el.innerHTML = `
    <h2>${t("Plan")}</h2>
    <p class="muted">${t("Stations of this project. Tap one to edit it; the coverage map shows all ticked stations together.")}</p>
    <div class="actions">
      ${canEditProject() ? `<button id="btnAddStation">＋ ${t("Add station")}</button>` : ""}
      ${canEditProject() && networkEnabled() ? `<button id="btnFromMeshCore" class="${picker.open ? "on" : ""}">⋈ ${t("Take over from MeshCore")}</button>` : ""}
      ${stations.length ? `<button id="btnCompute" class="primary" ${isComputing() ? "disabled" : ""}>▶ ${t("Compute coverage")}</button>` : ""}
    </div>
    ${picker.open ? pickerHtml() : ""}
    <div class="stlist">${stations.map((st) => {
      const r = resultsByStation[st.id], b = busy[st.id];
      const state_ = b ? (b.state === "queued" ? t("waiting") : `${t("computing")} ${b.progress ? Math.round(b.progress * 100) + " %" : "…"}`)
        : r && !stale.has(st.id) ? `${fmt(r.area_km2, 1)} km²` : r ? t("changed – compute again") : t("not computed");
      return `<div data-id="${st.id}" class="st ${state.selectedStationId === st.id ? "sel" : ""} ${st.enabled === false ? "off" : ""}">
        <input type="checkbox" ${st.enabled !== false ? "checked" : ""} ${canEditProject() ? "" : "disabled"} title="${escapeHtml(t("include in the map"))}">
        <span class="dot" style="background:${escapeHtml(st.color)}"></span>
        <span class="nm"><b>${escapeHtml(st.name)}</b><span class="sub">${fmt(st.freq_mhz, 3)} MHz · ${fmt(st.tx_power_dbm, 0)} dBm · ${fmt(st.height_m, 0)} m</span></span>
        <span class="${stale.has(st.id) ? "stale" : "muted"}">${escapeHtml(state_)}</span></div>`;
    }).join("") || `<p class="muted">${t("No stations yet.")} ${canEditProject() ? t("Add one with “Add station” and a click on the map.") : ""}</p>`}</div>
    ${!isMember() ? `<p class="muted">${t("Log in to add stations or change them.")}</p>` : !canEditProject() ? `<p class="muted">${isCollaborator() ? t("Your login works on its shared project only; this one you can only look at.") : t("Only the creator of this project and the admin can change it. Create your own project in the project menu.")}</p>` : ""}`;
  const fromMc = el.querySelector("#btnFromMeshCore");
  if (fromMc) fromMc.onclick = () => { picker.open = !picker.open; picker.chosen.clear(); renderPlan(); };
  if (picker.open) bindPicker(el);
  const add = el.querySelector("#btnAddStation");
  if (add) add.onclick = () => startPlacing("new");
  const run = el.querySelector("#btnCompute");
  if (run) run.onclick = () => compute();
  el.querySelectorAll(".st[data-id]").forEach((li) => {
    const st = state.project.stations.find((s) => s.id === li.dataset.id);
    li.querySelector("input").onchange = (e) => {
      st.enabled = e.target.checked; drawStation(st);
      emit("project-changed", { station: st, keepCoverage: true });
      composite();
      if (st.enabled && !resultsByStation[st.id]) status(t("“{name}” is not computed yet – press “Compute coverage”.", { name: st.name }));
    };
    li.onclick = (e) => {
      if (e.target.tagName === "INPUT") return;
      state.selectedStationId = st.id;
      if (!map.getBounds().pad(-0.1).contains([st.lat, st.lon])) map.panTo([st.lat, st.lon]);   // only if out of view
      inspectStation(st);
      renderPlan();
    };
  });
}

on("network-loaded", () => { if (picker.open) renderPlan(); });
map.on("moveend", () => { if (picker.open && !picker.search) renderPlan(); });
for (const ev of ["project-loaded", "stations-changed", "coverage-updated", "busy-changed", "computing", "project-changed"]) on(ev, renderPlan);
