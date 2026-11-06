/**
 * Workspace "Compare": model against reality for one station that is linked with its repeater.
 *
 *   CoreScope  – repeater ↔ repeater, SNR directly reported by the neighbours (zero hop)
 *   Drive tests – repeater → handheld (MeshMapper, CSV, manual), receiver per series
 *
 * Delta = measured − model in dB; negative means the model is too optimistic. From the drive tests the
 * server derives a suggestion for the global receiver extra loss (#30); only the admin can apply it.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml, fmt, signed, deltaColor } from "../core/util.js";
import { state, on, isAdmin, stationById } from "../core/state.js";
import { api, projectRef } from "../core/api.js";
import { status, sourceBadge } from "../core/ui.js";
import { map } from "../map/map.js";
import { showProfile } from "../profile.js";
import { enabled as networkEnabled, visibleLinks, showNeighbours, muteLines, net } from "../network.js";
import { meas, seriesById, seriesName, receiverText, focusRepeater, drawMeasurements, groupPings } from "../measurements.js";
import { inspectPing } from "../inspector.js";

const root = () => $("ws-compare");
const form = { stationId: null, useCs: true, useMm: true, rxHeight: 10, rxGain: 3, rxCable: 1 };
let result = { cs: null, mm: null, csError: null, mmError: null, running: false };

const linkedStations = () => (state.project?.stations || []).filter((s) => s.corescope_pk);

export function renderCompare() {
  const el = root();
  const stations = linkedStations();
  if (!stations.length) {
    el.innerHTML = `<h2>${t("Compare")}</h2><p class="muted">${t("Comparing needs a station that is linked with its repeater (station → Edit → MeshCore). Measurements are assigned through the repeater.")}</p>`;
    return;
  }
  if (!stationById(form.stationId) || !stationById(form.stationId).corescope_pk) form.stationId = stations[0].id;
  el.innerHTML = `
    <h2>${t("Compare")}</h2>
    <label>${t("Station")}<select id="cStation">${stations.map((s) => `<option value="${s.id}" ${s.id === form.stationId ? "selected" : ""}>${escapeHtml(s.name)}</option>`).join("")}</select></label>
    ${networkEnabled() ? `<label class="chk"><input type="checkbox" id="cCs" ${form.useCs ? "checked" : ""}> ${sourceBadge("corescope")} ${t("CoreScope: repeater ↔ repeater (zero hop)")}</label>
      ${form.useCs ? `<div class="grid3">
        <label title="${escapeHtml(t("for neighbours without own values"))}">${t("Partner height (m)")}<input id="cH" type="number" step="1" value="${form.rxHeight}"></label>
        <label>${t("Partner gain (dBi)")}<input id="cG" type="number" step="0.5" value="${form.rxGain}"></label>
        <label>${t("Partner cable (dB)")}<input id="cC" type="number" step="0.5" value="${form.rxCable}"></label></div>` : ""}` : ""}
    ${meas.allowed ? `<label class="chk"><input type="checkbox" id="cMm" ${form.useMm ? "checked" : ""}> ${sourceBadge("meshmapper")} ${t("Drive tests: repeater → handheld")}</label>` : ""}
    <div class="actions"><button id="cRun" class="primary" ${result.running ? "disabled" : ""}>▶ ${t("Compare")}</button></div>
    <div id="cSummary">${summaryHtml()}</div>
    <div id="cSuggestion">${suggestionHtml()}</div>
    <div id="cCsOut">${csHtml()}</div>
    <div id="cMmOut">${mmHtml()}</div>`;
  el.querySelector("#cStation").onchange = (e) => { form.stationId = e.target.value; result = { cs: null, mm: null }; renderCompare(); };
  const bindBox = (id, key) => { const b = el.querySelector(id); if (b) b.onchange = () => { form[key] = b.checked; renderCompare(); }; };
  bindBox("#cCs", "useCs"); bindBox("#cMm", "useMm");
  for (const [id, key] of [["#cH", "rxHeight"], ["#cG", "rxGain"], ["#cC", "rxCable"]]) { const i = el.querySelector(id); if (i) i.onchange = () => { form[key] = +i.value; }; }
  el.querySelector("#cRun").onclick = run;
  el.querySelectorAll("#cCsOut tr[data-i]").forEach((tr) => tr.onclick = () => {
    const x = result.cs.rows[+tr.dataset.i], st = stationById(form.stationId);
    if (x.rx_station_id) showProfile(st.id, null, { rxStationId: x.rx_station_id });
    else showProfile(st.id, L.latLng(x.lat, x.lon), { rx: { h: x.rx_height_m, g: x.rx_gain_dbi, c: x.rx_cable_db, label: x.name } });
  });
  el.querySelectorAll("#cMmOut tr[data-id]").forEach((tr) => tr.onclick = () => {
    const row = meas.rows.find((r) => r.id === +tr.dataset.id);
    if (!row) return;
    map.setView([row.lat, row.lon], Math.max(map.getZoom(), 15));
    const ping = groupPings(meas.rows).find((p) => p.rows.some((r) => r.id === row.id));
    if (ping) inspectPing(ping);
  });
  const apply = el.querySelector("#cApply");
  if (apply) apply.onclick = applySuggestion;
}

async function run() {
  const st = stationById(form.stationId); if (!st) return;
  result = { cs: null, mm: null, csError: null, mmError: null, running: true };
  renderCompare();
  const jobs = [];
  if (form.useCs && networkEnabled()) jobs.push(runCoreScope(st));
  if (form.useMm && meas.allowed) jobs.push(runDriveTests(st));
  if (!jobs.length) { result.running = false; status(t("Choose at least one source."), "err"); renderCompare(); return; }
  status(t("Comparing “{name}” …", { name: st.name }), "busy");
  await Promise.allSettled(jobs);
  if (result.mm) muteLines(true);   // the measurement points matter now, not the ~300 lines (#40)
  result.running = false;
  status(t("Comparison done."));
  renderCompare();
}

async function runCoreScope(st) {
  try {
    if (net.centrePk !== st.corescope_pk || !net.links) await showNeighbours(st.corescope_pk);
    const partners = visibleLinks().filter((l) => l.snr != null).map((l) => l.pk);
    result.cs = await api("/api/corescope/calibrate", { method: "POST", body: { ...projectRef(), station_id: st.id, partners,
      rx_height_m: form.rxHeight, rx_gain_dbi: form.rxGain, rx_cable_db: form.rxCable } });
  } catch (e) { result.csError = e.message; }
}

async function runDriveTests(st) {
  try {
    const body = { ...projectRef(), station_id: st.id };
    if (meas.days) body.days = meas.days;
    result.mm = await api("/api/measurements/compare", { method: "POST", body });
    meas.comparison = Object.fromEntries(result.mm.rows.map((x) => [x.id, x]));
    if (meas.focusPk !== st.corescope_pk) focusRepeater(st.corescope_pk); else drawMeasurements();
  } catch (e) { result.mmError = e.message; }
}

function statsRow(badge, what, s) {
  if (!s) return "";
  return `<tr><td>${badge} ${what}</td><td>${s.n}</td><td style="color:${deltaColor(s.mean_db)}">${signed(s.mean_db)}</td><td>${signed(s.median_db)}</td><td>${fmt(s.std_db)}</td></tr>`;
}

function summaryHtml() {
  if (result.running) return `<div class="muted wait">${t("computing")}</div>`;
  const rows = [];
  if (result.cs) rows.push(statsRow(sourceBadge("corescope"), "SNR", result.cs.stats));
  if (result.mm) { rows.push(statsRow(sourceBadge("meshmapper"), "SNR", result.mm.stats)); rows.push(statsRow(sourceBadge("meshmapper"), "RSSI", result.mm.rssi_stats)); }
  if (!rows.join("")) return "";
  return `<h4>${t("Summary")}</h4><table class="mini"><tr><th>${t("Source")}</th><th>n</th><th>${t("Δ mean")}</th><th>${t("median")}</th><th>${t("scatter")}</th></tr>${rows.join("")}</table>
    <p class="muted">${t("Δ = measured − model in dB. Negative: the model is too optimistic. Measured averages only contain packets that arrived, so near the range limit they tend to be too high.")}</p>`;
}

function suggestionHtml() {
  const s = result.mm?.suggestion;
  if (!s) return "";
  const text = {
    too_few: t("Only {n} usable drive-test values – at least 10 are needed for a suggestion.", { n: s.n }),
    fits: t("Model and drive tests agree (median {m} dB). No change needed.", { m: signed(s.median_db) }),
    scatter: t("The values scatter a lot (± {s} dB) around a median of {m} dB. The cause is local (terrain, buildings, placement) – no global value fixes it.", { s: fmt(s.std_db), m: signed(s.median_db) }),
    offset: t("Systematic offset: median {m} dB with scatter {s} dB. Suggestion: receiver extra loss {v} dB instead of {c} dB.", { m: signed(s.median_db), s: fmt(s.std_db), v: fmt(s.suggested_extra_loss_db), c: fmt(s.current_extra_loss_db) }),
    pessimistic: t("The model is too pessimistic (median {m} dB, scatter {s} dB). A receiver value cannot fix that – the extra loss comes from diffraction, obstacles or clutter. Compare the link budget in the profile, and try the model settings (e.g. receiver clutter, building mode) before measuring again.", { m: signed(s.median_db), s: fmt(s.std_db) })
      + (s.suggested_extra_loss_db != null ? " " + t("Setting the receiver extra loss from {c} dB to 0 dB helps part of the way.", { c: fmt(s.current_extra_loss_db) }) : ""),
  }[s.advice];
  return `<h4>${t("Suggestion for the model")}</h4><div class="calc">${text}</div>
    ${s.suggested_extra_loss_db != null && isAdmin() ? `<button id="cApply">${t("Apply globally (all projects)")}</button>` : ""}
    ${s.suggested_extra_loss_db != null && !isAdmin() ? `<p class="muted">${t("Only the admin can change the global model.")}</p>` : ""}`;
}

async function applySuggestion() {
  const s = result.mm?.suggestion; if (!s || s.suggested_extra_loss_db == null) return;
  if (!confirm(t("Set the receiver extra loss to {v} dB for all projects?", { v: fmt(s.suggested_extra_loss_db) }))) return;
  try {
    const current = await api("/api/admin/settings");
    await api("/api/admin/settings", { method: "PUT", body: { values: { ...current.values, rx_extra_loss_db: s.suggested_extra_loss_db } } });
    state.config.defaults = (await api("/api/config")).defaults;
    status(t("Model updated – compute the coverage again."));
  } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
}

function csHtml() {
  const head = `<h4>${sourceBadge("corescope")} ${t("CoreScope: repeater ↔ repeater")}</h4>`;
  if (result.csError) return head + `<div class="warn">${escapeHtml(result.csError)}</div>`;
  const r = result.cs; if (!r) return "";
  const origin = { station: ["📍", t("values of the station in the project")], entered: ["✎", t("own values for the repeater")], standard: ["", t("default values above")] };
  return head + `<p class="muted">${t("Noise floor {n} dBm · {k} partners with directly reported SNR · window {d} days. Tap a row for the profile.", { n: fmt(r.noise_dbm), k: r.rows.length, d: r.window_days ?? "?" })}</p>
    <table class="mini clickable"><tr><th>${t("Partner")}</th><th>${t("Height")}</th><th>km</th><th>LOS</th><th>${t("meas.")}</th><th>${t("model")}</th><th>Δ</th></tr>
    ${r.rows.map((x, i) => x.error ? `<tr><td>${escapeHtml(x.name || "")}</td><td colspan="6" class="warn wrap">${escapeHtml(x.error)}</td></tr>`
      : `<tr data-i="${i}" class="${x.saturated ? "dim" : ""}"><td class="ellipsis" title="${escapeHtml(x.name || "")}">${escapeHtml(x.name || "")} <span title="${escapeHtml((origin[x.rx_src] || origin.standard)[1])}">${(origin[x.rx_src] || origin.standard)[0]}</span></td>
        <td>${fmt(x.rx_height_m, 0)} m</td><td>${fmt(x.distance_km, 1)}</td><td>${["–", "~", "✓"][x.los_class] ?? "?"}</td>
        <td>${fmt(x.measured_snr, 1)}</td><td>${fmt(x.predicted_snr, 1)}</td><td style="color:${deltaColor(x.delta_db)}">${signed(x.delta_db)}</td></tr>`).join("")}</table>
    <p class="muted">${t("Grey: SNR ≥ {s} dB – LoRa reports are capped at the top, not counted.", { s: r.sat_db })}</p>`;
}

function mmHtml() {
  const head = `<h4>${sourceBadge("meshmapper")} ${t("Drive tests: repeater → handheld")}</h4>`;
  if (result.mmError) return head + `<div class="warn">${escapeHtml(result.mmError)}</div>`;
  const r = result.mm; if (!r) return "";
  const rows = r.rows.filter((x) => x.predicted_snr != null)
    .sort((a, b) => (a.saturated - b.saturated) || Math.abs(b.delta_db) - Math.abs(a.delta_db)).slice(0, 40);
  return head + `<p class="muted">${t("{n} measurements · receiver heights {h} (per series)", { n: r.rows.length, h: r.rx_heights.map((h) => fmt(h, 1) + " m").join(", ") || "–" })}
      ${r.saturated ? " · " + t("{n} at the upper SNR limit (RSSI only)", { n: r.saturated }) : ""}${r.outside ? " · " + t("{n} outside the computed radius", { n: r.outside }) : ""}</p>
    ${rows.length ? `<table class="mini clickable"><tr><th></th><th>${t("Series")}</th><th>km</th><th>${t("meas.")}</th><th>${t("model")}</th><th>Δ SNR</th><th>Δ RSSI</th></tr>
      ${rows.map((x) => `<tr data-id="${x.id}" class="${x.saturated ? "dim" : ""}"><td>${sourceBadge(x.src)}</td>
        <td class="ellipsis" title="${escapeHtml(seriesName(seriesById(x.series_id)) + " · " + receiverText(seriesById(x.series_id)))}">${escapeHtml(seriesName(seriesById(x.series_id)))}</td>
        <td>${fmt(x.distance_km, 2)}</td><td>${fmt(x.measured_snr, 1)}</td><td>${fmt(x.predicted_snr, 1)}</td>
        <td style="color:${x.saturated ? "inherit" : deltaColor(x.delta_db)}">${x.saturated ? "–" : signed(x.delta_db)}</td>
        <td>${x.delta_rssi_db != null ? signed(x.delta_rssi_db) : "–"}</td></tr>`).join("")}</table>
      <p class="muted">${t("Largest deviations first (at most 40). The points on the map are now coloured by Δ SNR: green ±3 dB, orange/red model too good, blue/purple model too pessimistic.")}</p>`
      : `<div class="warn">${t("No measurements of this repeater within the computed radius.")}</div>`}`;
}

on("compare-station", ({ stationId }) => { form.stationId = stationId; result = { cs: null, mm: null }; renderCompare(); });
for (const ev of ["project-loaded", "stations-changed", "measurements-loaded", "network-loaded"]) on(ev, () => { if (!result.running) renderCompare(); });
