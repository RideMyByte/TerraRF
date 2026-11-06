/**
 * Workspace "Report": key figures per computed station and the PDF report.
 */
import { t, lang } from "../core/i18n.js";
import { $, escapeHtml, fmt, signed, download } from "../core/util.js";
import { state, on, activeStations } from "../core/state.js";
import { api, apiBlob, projectRef } from "../core/api.js";
import { status } from "../core/ui.js";
import { resultsByStation } from "../map/coverage.js";

const root = () => $("ws-report");
const summaries = {};   // station id -> /api/report/summary (keyed by the coverage key)

export function renderReport() {
  const el = root();
  if (!state.project) { el.innerHTML = ""; return; }
  const stations = activeStations().filter((s) => resultsByStation[s.id]);
  el.innerHTML = `<h2>${t("Report")}</h2>
    <p class="muted">${t("Key figures of every computed station. The PDF contains the map, the figures, the model settings and – if available – the comparison with measurements.")}</p>
    ${stations.length ? stations.map((s) => `<div class="card" id="rep-${s.id}"><div class="head"><b><span style="color:${escapeHtml(s.color)}">●</span> ${escapeHtml(s.name)}</b>
      <button data-pdf="${s.id}">PDF</button></div><div class="body">${cardBody(s)}</div></div>`).join("")
      : `<p class="muted">${t("Compute the coverage first (workspace “Plan”).")}</p>`}`;
  el.querySelectorAll("[data-pdf]").forEach((b) => b.onclick = () => exportPdf(b.dataset.pdf));
  for (const s of stations) if (!fresh(s)) loadSummary(s);
}

const fresh = (s) => summaries[s.id] && summaries[s.id].key === resultsByStation[s.id]?.key;

function cardBody(s) {
  const r = summaries[s.id];
  if (!fresh(s)) return `<div class="muted wait">${t("loading")}</div>`;
  if (r.error) return `<div class="warn">${escapeHtml(r.error)}</div>`;
  return `<table class="kv">
      <tr><td>EIRP</td><td>${fmt(r.eirp_dbm)} dBm</td></tr>
      <tr><td>${t("Sensitivity")}</td><td>${fmt(r.sensitivity_dbm)} dBm</td></tr>
      <tr><td>${t("Covered (≥ {n} dB)", { n: fmt(r.min_margin_db, 0) })}</td><td><b>${fmt(r.area_covered_km2, 1)} km²</b> (${fmt(r.covered_pct, 0)} %)</td></tr>
      <tr><td>${t("line of sight")}</td><td>${fmt(r.area_los_km2, 1)} km²</td></tr>
      <tr><td>${t("Margin median / 90 %")}</td><td>${signed(r.margin_p50_db)} / ${signed(r.margin_p90_db)} dB</td></tr></table>
    ${r.rings.length ? `<table class="mini"><tr><th>km</th><th>${t("covered")}</th><th>${t("median margin")}</th></tr>
      ${r.rings.map((x) => `<tr><td>${fmt(x.from_km, 0)}–${fmt(x.to_km, 0)}</td><td>${fmt(x.covered_pct, 0)} %</td><td>${signed(x.median_margin_db)} dB</td></tr>`).join("")}</table>` : ""}`;
}

async function loadSummary(s) {
  try { summaries[s.id] = await api("/api/report/summary", { method: "POST", body: { ...projectRef(), station_id: s.id } }); }
  catch (e) { summaries[s.id] = { key: resultsByStation[s.id]?.key, error: e.message }; }
  const box = root().querySelector(`#rep-${s.id} .body`);
  if (box) box.innerHTML = cardBody(s);
}

/** Download the PDF report of a station in the current interface language. */
export async function exportPdf(stationId) {
  status(t("Creating the PDF report …"), "busy");
  try {
    const { blob, filename } = await apiBlob("/api/report/pdf", { ...projectRef(), station_id: stationId, lang: lang() });
    download(blob, filename);
    status(t("PDF created."));
  } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
}

for (const ev of ["coverage-updated", "project-loaded", "stations-changed"]) on(ev, renderReport);
