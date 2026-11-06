/**
 * Workspace "Measure": drive-test series (MeshMapper, CSV, manual entries), the receiver of each series
 * (device and placement from the catalog, #29) and importing new data.
 */
import { t } from "../core/i18n.js";
import { $, escapeHtml, fmt, fmtDateTime, fmtTime } from "../core/util.js";
import { state, on, isCollaborator, canEditProject } from "../core/state.js";
import { api } from "../core/api.js";
import { status, sourceBadge } from "../core/ui.js";
import { map } from "../map/map.js";
import { meas, seriesById, seriesName, receiverText, loadMeasurements, focusSeries, drawMeasurements } from "../measurements.js";

const root = () => $("ws-measure");
let editingSeries = null;

export function renderMeasure() {
  const el = root();
  if (!meas.allowed) {
    el.innerHTML = `<h2>${t("Measure")}</h2><p class="muted">${!state.project?.id
      ? t("Save the project first – measurements belong to a project.")
      : t("The measurements of this project are not visible to you.")}</p>`;
    return;
  }
  const s = meas.stats || {};
  el.innerHTML = `
    <h2>${t("Measure")}</h2>
    <div class="calc">${t("{n} measurements of {r} repeaters", { n: s.count ?? 0, r: s.repeaters ?? 0 })}
      ${s.first ? `<br>${fmtDateTime(s.first)} – ${fmtDateTime(s.last)}` : ""}
      <br>${Object.entries(s.by_source || {}).map(([k, n]) => `${sourceBadge(k)} ${n}`).join(" ")}</div>
    <div class="grid2">
      <label class="chk"><input type="checkbox" id="mPoints" ${meas.show.points ? "checked" : ""}> ${t("show points")}</label>
      <label>${t("Period")}<select id="mDays">${[["", "all"], [1, "last 24 h"], [7, "last 7 days"], [30, "last 30 days"]].map(([v, l]) => `<option value="${v}" ${String(meas.days ?? "") === String(v) ? "selected" : ""}>${t(l)}</option>`).join("")}</select></label>
    </div>
    ${meas.focusPk ? `<p class="muted">${t("Only measurements of the selected repeater are shown.")}</p>` : ""}
    <h4>${t("Measurement series")}</h4>
    <div class="serieslist">${meas.series.slice(0, 50).map(seriesCard).join("") || `<p class="muted">${t("No measurements yet.")}</p>`}</div>
    ${meas.series.length > 50 ? `<p class="muted">${t("… {n} older", { n: meas.series.length - 50 })}</p>` : ""}
    ${canEditProject() ? `<h4>${t("Add measurements")}</h4>
      <p class="muted">${t("Single values: right-click (long press on phones) on the map → “Enter measurement”.")}</p>
      <label>${t("Import a CSV file")}<input type="file" id="mCsv" accept=".csv,text/csv"></label>
      <p class="muted">${t("Columns: lat, lon, rep (key or prefix), snr_down; optional snr_up, rssi, rx_h, ts, note.")}</p>
      <div id="mCsvOut" class="muted"></div>
      <h4>MeshMapper</h4>
      <p class="muted">${t("Measurements sent with this project's key land in this project only. In the MeshMapper app: Settings → API Endpoints → Import from Clipboard, with the link below.")}</p>
      <div id="mmKeyBox" class="muted wait">${t("loading")}</div>` : ""}
    ${canEditProject() ? `<h4>${t("Delete")}</h4><div class="row"><select id="mDelSrc">${Object.keys(s.by_source || {}).map((k) => `<option>${escapeHtml(k)}</option>`).join("")}</select>
      <button id="mDel" class="danger" style="flex:0 0 auto">${t("Delete all of this source")}</button></div>` : ""}`;
  if (canEditProject() && state.project?.id) showProjectKey(el.querySelector("#mmKeyBox"));
  el.querySelector("#mPoints").onchange = (e) => { meas.show.points = e.target.checked; drawMeasurements(); };
  el.querySelector("#mDays").onchange = (e) => { meas.days = e.target.value ? +e.target.value : null; meas.comparison = null; loadMeasurements(); };
  el.querySelectorAll(".series").forEach(bindSeries);
  const csv = el.querySelector("#mCsv");
  if (csv) csv.onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const out = el.querySelector("#mCsvOut");
    try {
      const r = await api("/api/measurements", { method: "POST", body: { project: state.project.id, csv: await file.text() } });
      out.textContent = t("{n} stored", { n: r.stored }) + (r.errors.length ? ` · ${t("{n} errors", { n: r.errors.length })}: ${r.errors.slice(0, 3).join("; ")}` : ".");
      meas.show.points = true; loadMeasurements();
    } catch (err) { out.textContent = t("Error: {msg}", { msg: err.message }); }
    e.target.value = "";
  };
  const del = el.querySelector("#mDel");
  if (del) del.onclick = async () => {
    const src = el.querySelector("#mDelSrc").value;
    if (!src || !confirm(t("Delete all measurements of the source “{src}”?", { src }))) return;
    try { const r = await api("/api/measurements", { method: "DELETE", body: { project: state.project.id, src } }); status(t("{n} measurements deleted.", { n: r.deleted })); loadMeasurements(); }
    catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
  };
}

/** #53: the project's own MeshMapper key and the link for the app. */
async function showProjectKey(box) {
  if (!box) return;
  const path = `/api/projects/${encodeURIComponent(state.project.id)}/ingest-key`;
  const render = (key) => {
    const ingest = `${location.host}/api/ingest/meshmapper`;
    const link = key ? `meshmapper://custom-api?url=${ingest}&key=${key}` : "";
    box.className = "";
    box.innerHTML = key ? `<label>${t("Link for phones")}<div class="row"><input readonly value="${escapeHtml(link)}"><button data-a="copy" style="flex:0 0 auto">${t("copy")}</button></div></label>
        <div class="muted">${t("Endpoint")}: <code>https://${escapeHtml(ingest)}</code> · X-API-Key: <code>${escapeHtml(key)}</code></div>` : `<p class="muted">${t("This project has no key yet.")}</p>`;
    if (!isCollaborator()) box.insertAdjacentHTML("beforeend", `<button data-a="new">${key ? t("New key (the old one stops working)") : t("Create key")}</button>`);
    const copy = box.querySelector('[data-a="copy"]');
    if (copy) copy.onclick = () => navigator.clipboard?.writeText(link).then(() => status(t("Link copied.")));
    const fresh = box.querySelector('[data-a="new"]');
    if (fresh) fresh.onclick = async () => {
      if (key && !confirm(t("Create a new key? Phones with the old key stop sending to this project."))) return;
      try { render((await api(path, { method: "POST" })).key); } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
    };
  };
  try { render((await api(path)).key); } catch (e) { box.textContent = t("Error: {msg}", { msg: e.message }); }
}

function seriesCard(s) {
  const selected = meas.seriesId === s.id;
  return `<div class="series ${selected ? "on" : ""}" data-sid="${s.id}">
    <div class="series-head"><span>${sourceBadge(s.src)} <b>${escapeHtml(seriesName(s))}</b></span><span class="muted">${t("{n} meas.", { n: s.n })} · ${t("{n} rep.", { n: s.repeaters })}</span></div>
    <div class="muted">${s.t0 ? `${fmtDateTime(s.t0)} – ${fmtTime(s.t1)} · ` : ""}${t("Receiver")}: ${escapeHtml(receiverText(s))}${s.power_seen ? ` · ${escapeHtml(s.power_seen)}` : ""}</div>
    <div class="actions"><button data-a="show">${selected ? t("show all") : t("show")}</button>${canEditProject() ? `<button data-a="edit">✎ ${t("Receiver")}</button>` : ""}${canEditProject() ? `<button data-a="del" class="danger" title="${escapeHtml(t("Delete series"))}">🗑</button>` : ""}</div>
    ${editingSeries === s.id ? seriesForm(s) : ""}</div>`;
}

function seriesForm(s) {
  const d = state.config?.defaults || {};
  const opt = (list, current) => `<option value="">${t("– not set –")}</option>` + list.map((x) => `<option value="${escapeHtml(x.id)}" ${x.id === current ? "selected" : ""}>${escapeHtml(x.name)}</option>`).join("");
  return `<div class="form">
    <label>${t("Name")}<input data-k="name" value="${escapeHtml(s.name || "")}" placeholder="${escapeHtml(seriesName({ ...s, name: "" }))}"></label>
    <div class="grid2">
      <label>${t("Device")}<select data-k="device_id">${opt(state.catalog.devices, s.device_id)}</select></label>
      <label>${t("Placement")}<select data-k="placement_id">${opt(state.catalog.placements, s.placement_id)}</select></label>
    </div>
    <div class="grid3">
      <label>${t("Height (m)")}<input data-k="rx_h" type="number" step="0.1" value="${s.rx_h ?? ""}" placeholder="${fmt(d.rx_height_m, 1)}"></label>
      <label>${t("Gain (dBi)")}<input data-k="rx_gain_dbi" type="number" step="0.1" value="${s.rx_gain_dbi ?? ""}" placeholder="${fmt(d.rx_gain_dbi, 1)}"></label>
      <label>${t("Cable (dB)")}<input data-k="rx_cable_db" type="number" step="0.1" value="${s.rx_cable_db ?? ""}" placeholder="${fmt(d.rx_cable_db, 1)}"></label>
    </div>
    <label class="chk"><input data-k="ext_ant" type="checkbox" ${s.ext_ant ? "checked" : ""}> ${t("external antenna (e.g. on the car roof)")}</label>
    <label>${t("Note")}<input data-k="note" value="${escapeHtml(s.note || "")}"></label>
    <p class="muted">${t("Order of precedence: a height in the single measurement, then the values here, then device and placement, then the global receiver.")}</p>
    <div class="actions"><button class="primary" data-a="save">${t("Save")}</button></div><div class="muted" data-out></div></div>`;
}

function bindSeries(el) {
  const s = seriesById(+el.dataset.sid);
  const on_ = (a, fn) => { const b = el.querySelector(`[data-a="${a}"]`); if (b) b.onclick = fn; };
  on_("show", () => {
    const show = meas.seriesId !== s.id;
    focusSeries(show ? s.id : null);
    if (show && s.bounds && s.bounds[0] != null) map.fitBounds([[s.bounds[0], s.bounds[1]], [s.bounds[2], s.bounds[3]]], { padding: [30, 30], maxZoom: 16 });
  });
  on_("edit", () => { editingSeries = editingSeries === s.id ? null : s.id; renderMeasure(); });
  on_("del", async () => {
    if (!confirm(t("Delete series “{name}” with {n} measurements?", { name: seriesName(s), n: s.n }))) return;
    try { await api("/api/measurements", { method: "DELETE", body: { project: state.project.id, series: s.id } }); if (meas.seriesId === s.id) meas.seriesId = null; loadMeasurements(); }
    catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
  });
  on_("save", async () => {
    const body = {};
    el.querySelectorAll("[data-k]").forEach((i) => { body[i.dataset.k] = i.type === "checkbox" ? i.checked : i.value === "" ? null : (i.type === "number" ? +i.value : i.value); });
    try {
      await api(`/api/measurements/series/${s.id}`, { method: "PUT", body: { ...body, project: state.project.id } });
      editingSeries = null; meas.comparison = null; loadMeasurements();
      status(t("Series saved – compare again for new values."));
    } catch (e) { el.querySelector("[data-out]").textContent = t("Error: {msg}", { msg: e.message }); }
  });
}

for (const ev of ["measurements-loaded", "catalog-loaded"]) on(ev, renderMeasure);
