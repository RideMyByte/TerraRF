/**
 * Workspace "Plan": the project's stations, adding new ones and computing coverage.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml, fmt } from "../core/util.js";
import { state, on, emit, canEditAny } from "../core/state.js";
import { status } from "../core/ui.js";
import { map } from "../map/map.js";
import { compute, resultsByStation, busy, stale, isComputing, composite } from "../map/coverage.js";
import { startPlacing, drawStation } from "../map/stations.js";
import { inspectStation } from "../inspector.js";

const root = () => $("ws-plan");

export function renderPlan() {
  const el = root();
  if (!state.project) { el.innerHTML = ""; return; }
  const stations = state.project.stations;
  el.innerHTML = `
    <h2>${t("Plan")}</h2>
    <p class="muted">${t("Stations of this project. Tap one to edit it; the coverage map shows all ticked stations together.")}</p>
    <div class="actions">
      ${canEditAny() ? `<button id="btnAddStation">＋ ${t("Add station")}</button>` : ""}
      ${stations.length ? `<button id="btnCompute" class="primary" ${isComputing() ? "disabled" : ""}>▶ ${t("Compute coverage")}</button>` : ""}
    </div>
    <div class="stlist">${stations.map((st) => {
      const r = resultsByStation[st.id], b = busy[st.id];
      const state_ = b ? (b.state === "queued" ? t("waiting") : `${t("computing")} ${b.progress ? Math.round(b.progress * 100) + " %" : "…"}`)
        : r && !stale.has(st.id) ? `${fmt(r.area_km2, 1)} km²` : r ? t("changed – compute again") : t("not computed");
      return `<div data-id="${st.id}" class="st ${state.selectedStationId === st.id ? "sel" : ""} ${st.enabled === false ? "off" : ""}">
        <input type="checkbox" ${st.enabled !== false ? "checked" : ""} ${canEditAny() ? "" : "disabled"} title="${escapeHtml(t("include in the map"))}">
        <span class="dot" style="background:${escapeHtml(st.color)}"></span>
        <span class="nm"><b>${escapeHtml(st.name)}</b><span class="sub">${fmt(st.freq_mhz, 3)} MHz · ${fmt(st.tx_power_dbm, 0)} dBm · ${fmt(st.height_m, 0)} m</span></span>
        <span class="${stale.has(st.id) ? "stale" : "muted"}">${escapeHtml(state_)}</span></div>`;
    }).join("") || `<p class="muted">${t("No stations yet.")} ${canEditAny() ? t("Add one with “Add station” and a click on the map.") : ""}</p>`}</div>
    ${!canEditAny() ? `<p class="muted">${t("Log in to add stations or change them.")}</p>` : ""}`;
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
      map.panTo([st.lat, st.lon]);
      inspectStation(st);
      renderPlan();
    };
  });
}

for (const ev of ["project-loaded", "stations-changed", "coverage-updated", "busy-changed", "computing", "project-changed"]) on(ev, renderPlan);
