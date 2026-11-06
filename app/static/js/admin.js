/**
 * Administration (admin only), a full-screen dialog with one page per topic:
 *   access & website · data sources (elevation, buildings, measurements) · model (global, #30) · catalogs · system
 *
 * Every page loads its own data when it is opened and saves with its own button.
 */
import { t } from "./core/i18n.js";
import { $, escapeHtml, fmt, fmtBytes } from "./core/util.js";
import { state, emit } from "./core/state.js";
import { api } from "./core/api.js";
import { status } from "./core/ui.js";
import { map } from "./map/map.js";

const PAGES = [
  ["access", "Access & website"], ["data", "Data sources"], ["model", "Model"], ["catalog", "Catalogs"], ["system", "System"],
];
let page = "access";
const dlg = () => $("adminDlg");

export function openAdmin(start) {
  if (start) page = start;
  dlg().innerHTML = `<div class="admin">
    <nav><h3>${t("Administration")}</h3>${PAGES.map(([id, label]) => `<button data-p="${id}" class="${id === page ? "active" : ""}">${t(label)}</button>`).join("")}</nav>
    <div class="admin-body"><h3><span>${t(PAGES.find((x) => x[0] === page)[1])}</span><button class="iconbtn" id="adminClose" aria-label="${escapeHtml(t("Close"))}">✕</button></h3>
      <div id="adminBody"></div></div></div>`;
  dlg().querySelector("#adminClose").onclick = () => dlg().close();
  dlg().querySelectorAll("nav button").forEach((b) => b.onclick = () => openAdmin(b.dataset.p));
  if (!dlg().open) dlg().showModal();
  const body = $("adminBody");
  ({ access: accessPage, data: dataPage, model: modelPage, catalog: catalogPage, system: systemPage })[page](body)
    .catch((e) => { body.innerHTML = `<div class="warn">${escapeHtml(e.message)}</div>`; });
}

async function reloadConfig() {
  state.config = await api("/api/config");
  emit("config-reloaded");
}

// ---------------------------------------------------------------- access & website
const ACCESS_KEYS = ["user_enabled", "user_name", "user_password", "parent_name", "parent_url", "show_github", "imprint_mode", "imprint_url",
  "imprint_name", "imprint_email", "imprint_address", "imprint_extra", "privacy_mode", "privacy_url", "privacy_extra", "measurements_public"];

async function accessPage(body) {
  const c = await api("/api/admin/appconfig");
  const input = (k, label, type = "text") => `<label>${t(label)}<input data-k="${k}" type="${type}" value="${escapeHtml(c[k] ?? "")}"></label>`;
  const check = (k, label) => `<label class="chk"><input data-k="${k}" type="checkbox" ${c[k] ? "checked" : ""}> ${t(label)}</label>`;
  const select = (k, label, options) => `<label>${t(label)}<select data-k="${k}">${options.map(([v, l]) => `<option value="${v}" ${c[k] === v ? "selected" : ""}>${t(l)}</option>`).join("")}</select></label>`;
  body.innerHTML = `
    <section><h3>${t("Second login (“user”)")}</h3>
      <p class="muted">${t("The user may see everything, add own stations and enter measurements. Catalogs, model and other people's stations stay with the admin.")}</p>
      ${check("user_enabled", "enabled")}<div class="grid2">${input("user_name", "User name")}${input("user_password", "Password", "password")}</div></section>
    <section><h3>${t("Visitors")}</h3>
      <p class="muted">${t("Visitors without login see projects marked “public” (read only).")}</p>
      ${check("measurements_public", "Visitors may see measurements (without time and notes)")}</section>
    <section><h3>${t("Website")}</h3>
      <div class="grid2">${input("parent_name", "Belongs to (name)")}${input("parent_url", "Belongs to (link)")}</div>
      ${check("show_github", "Show link to the source code")}
      <div class="grid2">${select("imprint_mode", "Imprint", [["generated", "generated from the fields below"], ["link", "external page"], ["off", "none"]])}${input("imprint_url", "Imprint link")}</div>
      <div class="grid2">${input("imprint_name", "Name")}${input("imprint_email", "E-mail")}</div>
      <label>${t("Address")}<textarea data-k="imprint_address" rows="2">${escapeHtml(c.imprint_address || "")}</textarea></label>
      <label>${t("Imprint, additional text")}<textarea data-k="imprint_extra" rows="2">${escapeHtml(c.imprint_extra || "")}</textarea></label>
      <div class="grid2">${select("privacy_mode", "Privacy policy", [["generated", "generated"], ["link", "external page"]])}${input("privacy_url", "Privacy policy link")}</div>
      <label>${t("Privacy policy, additional text")}<textarea data-k="privacy_extra" rows="2">${escapeHtml(c.privacy_extra || "")}</textarea></label></section>
    <div class="actions"><button class="primary" id="acSave">${t("Save")}</button><span class="muted" id="acOut"></span></div>`;
  body.querySelector("#acSave").onclick = async () => {
    const values = {};
    for (const k of ACCESS_KEYS) { const el = body.querySelector(`[data-k="${k}"]`); values[k] = el.type === "checkbox" ? el.checked : el.value; }
    try { await api("/api/admin/appconfig", { method: "PUT", body: values }); await reloadConfig(); body.querySelector("#acOut").textContent = t("Saved."); }
    catch (e) { body.querySelector("#acOut").textContent = t("Error: {msg}", { msg: e.message }); }
  };
}

// ---------------------------------------------------------------- data sources
async function dataPage(body) {
  const [c, b, jobs] = await Promise.all([api("/api/admin/appconfig"), api("/api/admin/buildings"), api("/api/regions/jobs")]);
  const ingest = `${location.host}/api/ingest/meshmapper`;
  body.innerHTML = `
    <section><h3>${t("Elevation data")}</h3>
      <p class="muted">${t("Which sources are used is set under “Model”. Here you test them and load areas for offline use.")}</p>
      <div class="grid3"><label>${t("Source")}<select id="rSource">${state.config.sources.filter((s) => !s.id.startsWith("local:")).map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.label)}</option>`).join("")}</select></label>
        <label>${t("Resolution (m)")}<input id="rRes" type="number" value="10" step="1" min="1"></label>
        <label>${t("Area")}<select id="rArea"><option value="view">${t("current map view")}</option><option value="nrw">${t("all of NRW")}</option></select></label></div>
      <label>${t("Folder name (optional)")}<input id="rName" placeholder="nrw_dgm_10m"></label>
      <div class="actions"><button id="rTest">${t("Test at map centre")}</button><button id="rPlan">${t("Estimate size")}</button><button id="rStart" class="primary">${t("Download")}</button></div>
      <pre id="rOut" class="muted"></pre><div id="jobs">${jobsHtml(jobs)}</div></section>
    <section><h3>${t("Building footprints")} <span class="muted">(#25)</span></h3>
      <p>${t("{n} footprints stored.", { n: b.count })} ${b.attribution ? `<span class="muted">${escapeHtml(b.attribution)}</span>` : ""}</p>
      ${(b.sources || []).map((s) => `<div class="muted">· ${escapeHtml(s.name || s)}${s.count != null ? ` (${s.count})` : ""}</div>`).join("")}
      <p class="muted">${t("Footprints make the model treat surface heights inside them as buildings (setting “building mode” under Model). Export from overpass-turbo.eu as GeoJSON, or load the current map view directly (at most 30 km²).")}</p>
      <label>${t("Import GeoJSON")}<input type="file" id="bFile" accept=".geojson,.json,application/geo+json"></label>
      <div class="actions"><button id="bOsm">${t("Load OSM buildings in the map view")}</button><button id="bClear" class="danger">${t("Delete all")}</button></div>
      <div id="bOut" class="muted"></div></section>
    <section><h3>${t("Measurements (MeshMapper)")}</h3>
      <p class="muted">${t("MeshMapper sends drive-test results to this server when the endpoint and key are entered in the app.")}</p>
      <label>${t("Key")}<div class="row"><input id="mmKey" value="${escapeHtml(c.mm_api_key || "")}"><button id="mmGen" style="flex:0 0 auto">${t("generate")}</button></div></label>
      <label>${t("Endpoint")}<input readonly value="https://${escapeHtml(ingest)}"></label>
      <label>${t("Link for phones")}<div class="row"><input id="mmLink" readonly><button id="mmCopy" style="flex:0 0 auto">${t("copy")}</button></div></label>
      <div class="actions"><button id="mmSave" class="primary">${t("Save")}</button><span class="muted" id="mmOut"></span></div></section>`;
  const $$ = (id) => body.querySelector("#" + id);
  const region = () => {
    const bounds = map.getBounds();
    return { source: $$("rSource").value, area: $$("rArea").value, res_m: +$$("rRes").value, name: $$("rName").value.trim() || undefined,
      bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()] };
  };
  $$("rTest").onclick = async () => {
    const c0 = map.getCenter();
    $$("rOut").textContent = t("testing …");
    try { $$("rOut").textContent = JSON.stringify(await api(`/api/terrain/test?source=${encodeURIComponent($$("rSource").value)}&lat=${c0.lat}&lon=${c0.lng}&res=${$$("rRes").value}`), null, 1); }
    catch (e) { $$("rOut").textContent = e.message; }
  };
  $$("rPlan").onclick = async () => {
    try { const p = await api("/api/regions/plan", { method: "POST", body: region() }); $$("rOut").textContent = t("{n} tiles, about {mb} MB.", { n: p.n_tiles, mb: fmt(p.est_mb, 0) }) + (p.note ? " " + p.note : ""); }
    catch (e) { $$("rOut").textContent = e.message; }
  };
  $$("rStart").onclick = async () => {
    const r = region();
    if (r.area === "nrw" && !confirm(t("Load all of NRW? Depending on the resolution this takes hours and several GB. It can be cancelled and resumed."))) return;
    try { await api("/api/regions/start", { method: "POST", body: r }); pollJobs(body); } catch (e) { $$("rOut").textContent = e.message; }
  };
  bindJobs(body); if (jobs.some((j) => j.state === "running")) pollJobs(body);
  $$("bFile").onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    $$("bOut").textContent = t("importing …");
    try { const r = await api("/api/admin/buildings/geojson", { method: "POST", body: { geojson: await file.text(), name: file.name } }); $$("bOut").textContent = t("{n} new, {total} footprints stored.", { n: r.new, total: r.total }); await reloadConfig(); }
    catch (err) { $$("bOut").textContent = t("Error: {msg}", { msg: err.message }); }
    e.target.value = "";
  };
  $$("bOsm").onclick = async () => {
    const bounds = map.getBounds();
    $$("bOut").textContent = t("loading from OpenStreetMap …");
    try { const r = await api("/api/admin/buildings/osm", { method: "POST", body: { bbox: [bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast()] } }); $$("bOut").textContent = t("{n} new, {total} footprints stored.", { n: r.new, total: r.total }); await reloadConfig(); }
    catch (e) { $$("bOut").textContent = t("Error: {msg}", { msg: e.message }); }
  };
  $$("bClear").onclick = async () => {
    if (!confirm(t("Delete all building footprints?"))) return;
    await api("/api/admin/buildings", { method: "DELETE" }); await reloadConfig(); openAdmin("data");
  };
  const link = () => { const k = $$("mmKey").value.trim(); $$("mmLink").value = k ? `meshmapper://custom-api?url=${ingest}&key=${k}` : t("– set a key first –"); };
  link(); $$("mmKey").oninput = link;
  $$("mmGen").onclick = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(24));
    $$("mmKey").value = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); link();
  };
  $$("mmCopy").onclick = () => navigator.clipboard?.writeText($$("mmLink").value).then(() => { $$("mmOut").textContent = t("Link copied."); });
  $$("mmSave").onclick = async () => {
    try { await api("/api/admin/appconfig", { method: "PUT", body: { mm_api_key: $$("mmKey").value.trim() } }); $$("mmOut").textContent = t("Saved."); }
    catch (e) { $$("mmOut").textContent = t("Error: {msg}", { msg: e.message }); }
  };
}

const JOB_STATES = { running: "running", queued: "queued", done: "done", done_with_errors: "done with errors", cancelled: "cancelled", error: "error" };
function jobsHtml(jobs) {
  return jobs.map((j) => {
    const progress = j.done + j.skipped + j.empty + j.failed, pct = j.total ? Math.round(100 * progress / j.total) : 0;
    return `<div class="job"><b>${escapeHtml(j.name)}</b> · ${escapeHtml(j.source)} ${j.res_m} m · ${t(JOB_STATES[j.state] || j.state)}
      <div class="bar"><div style="width:${pct}%"></div></div>
      ${t("{p}/{n} tiles ({s} present, {e} empty, {f} failed)", { p: progress, n: j.total, s: j.skipped, e: j.empty, f: j.failed })} · ${fmtBytes(j.bytes)} · ${fmt(j.elapsed_s / 60, 1)} min
      ${j.state === "running" ? `<button data-cancel="${j.id}">${t("Cancel")}</button>` : ""}
      ${(j.errors || []).map((e) => `<div class="warn">${escapeHtml(e)}</div>`).join("")}${j.message ? `<div class="muted">${escapeHtml(j.message)}</div>` : ""}</div>`;
  }).join("");
}
function bindJobs(body) {
  body.querySelectorAll("[data-cancel]").forEach((b) => b.onclick = () => api(`/api/regions/cancel/${b.dataset.cancel}`, { method: "POST" }).then(() => pollJobs(body)));
}
let jobTimer = null;
async function pollJobs(body) {
  clearTimeout(jobTimer);
  if (!dlg().open || page !== "data") return;
  const jobs = await api("/api/regions/jobs").catch(() => []);
  const box = body.querySelector("#jobs"); if (!box) return;
  box.innerHTML = jobsHtml(jobs); bindJobs(body);
  if (jobs.some((j) => j.state === "running")) jobTimer = setTimeout(() => pollJobs(body), 4000);
  else reloadConfig();
}

// ---------------------------------------------------------------- model (global settings, #30)
/** Labels of the global settings (English source strings, translated through the locale files). */
const SETTING_LABELS = {
  ground_source: "Terrain model (bare ground)", surface_source: "Surface model (buildings, trees)", fallback_source: "Fallback outside the region",
  res_m: "Grid resolution", k_factor: "Earth radius factor k", max_profile_points: "Samples per ray", max_rays: "Maximum rays per station",
  near_m: "Near field radius", near_res_m: "Near field resolution", diffraction_model: "Diffraction model", veg_mode: "Attenuation through objects",
  veg_gamma_db_m: "Specific attenuation (custom)", veg_max_db: "Maximum attenuation (custom)", building_mode: "Building mode",
  clutter_mode: "Receiver clutter", clutter_floor: "Minimum clutter class", clutter_exclude_m: "Clutter zone around the receiver",
  two_ray: "Ground reflection (two-ray)", rx_height_m: "Receiver height", rx_gain_dbi: "Receiver antenna gain", rx_cable_db: "Receiver cable loss",
  rx_extra_loss_db: "Receiver extra loss (body, building)", noise_rise_db: "Noise rise", min_margin_db: "Required margin",
};
const CHOICE_LABELS = {
  deygout: "Deygout (3 edges)", bullington: "Bullington", auto: "automatic", custom: "custom values", off: "off", none: "none",
  vegetation: "like vegetation (P.833)", diffraction: "diffraction over the roof only (P.526)",
  suburban: "suburban", urban: "urban", dense: "dense urban", trees: "trees",
};

const GROUPS = [["data", "Elevation data"], ["compute", "Computation"], ["model", "Propagation model"], ["receiver", "Default receiver (handheld)"]];

async function modelPage(body) {
  const s = await api("/api/admin/settings");
  const sources = state.config.sources;
  const field = (f) => {
    const v = s.values[f.key], label = t(SETTING_LABELS[f.key] || f.key);
    const changed = f.key in s.changed;
    let control;
    if (f.type === "bool") control = `<input type="checkbox" data-k="${f.key}" ${v ? "checked" : ""}>`;
    else if (f.type === "choice") control = `<select data-k="${f.key}">${f.choices.map((c) => `<option value="${c}" ${c === v ? "selected" : ""}>${escapeHtml(t(CHOICE_LABELS[c] || c))}</option>`).join("")}</select>`;
    else if (f.type === "source") control = `<select data-k="${f.key}">${f.key === "fallback_source" ? `<option value="none" ${v === "none" ? "selected" : ""}>${t("none")}</option>` : ""}${sources.map((x) => `<option value="${escapeHtml(x.id)}" ${x.id === v ? "selected" : ""}>${escapeHtml(x.label)}</option>`).join("")}</select>`;
    else control = `<input type="number" data-k="${f.key}" data-int="${f.type === "int"}" step="${f.type === "int" ? 1 : "any"}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} value="${v ?? ""}" placeholder="${f.default ?? ""}">`;
    return `<div class="setting ${changed ? "changed" : ""}"><div class="name">${escapeHtml(label)}${f.unit ? ` <span class="muted">(${f.unit})</span>` : ""}</div>${control}
      <div class="help">${escapeHtml(f.reference || "")} · ${t("default")}: ${escapeHtml(String(f.default ?? "–"))}</div></div>`;
  };
  body.innerHTML = `<p class="muted">${t("These settings apply to every project. Cached results are keyed by them, so a change never serves stale maps. Highlighted: differs from the default. Model version {v}.", { v: s.model_version })}</p>
    ${GROUPS.map(([g, title]) => `<section><h3>${t(title)}</h3><div class="settings">${s.spec.filter((f) => f.group === g).map(field).join("")}</div></section>`).join("")}
    <div class="actions"><button class="primary" id="msSave">${t("Save")}</button><button id="msReset">${t("Reset to defaults")}</button><span class="muted" id="msOut"></span></div>`;
  const collect = () => {
    const values = {};
    body.querySelectorAll("[data-k]").forEach((el) => {
      values[el.dataset.k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : el.value === "" ? null : (el.dataset.int === "true" ? parseInt(el.value, 10) : +el.value);
    });
    return values;
  };
  const save = async (values) => {
    try {
      await api("/api/admin/settings", { method: "PUT", body: { values } });
      await reloadConfig(); emit("settings-changed");
      status(t("Model settings saved – compute the coverage again."));
      openAdmin("model");
    } catch (e) { body.querySelector("#msOut").textContent = t("Error: {msg}", { msg: e.message }); }
  };
  body.querySelector("#msSave").onclick = () => save(collect());
  body.querySelector("#msReset").onclick = () => { if (confirm(t("Reset all model settings to the defaults?"))) save({}); };
}

// ---------------------------------------------------------------- catalogs (#28, #29)
const KINDS = [["antennas", "Antennas"], ["devices", "Devices"], ["placements", "Placements"]];
const COLUMNS = {
  antennas: [["name", "Name"], ["maker", "Maker"], ["type", "Type"], ["gain_dbi", "Gain (dBi)"], ["hbw_deg", "H beam (°)"], ["vbw_deg", "V beam (°)"], ["fb_db", "F/B (dB)"], ["source", "Source"]],
  devices: [["name", "Name"], ["antenna_gain_dbi", "Antenna gain (dBi)"], ["cable_db", "Cable (dB)"], ["note", "Note"], ["source", "Source"]],
  placements: [["name", "Name"], ["height_m", "Height (m)"], ["extra_loss_db", "Extra loss (dB)"], ["note", "Note"], ["source", "Source"]],
};
let catalogKind = "antennas";

async function catalogPage(body) {
  const catalog = await api("/api/catalog");
  state.catalog = catalog; emit("catalog-loaded");
  const cols = COLUMNS[catalogKind];
  body.innerHTML = `<p class="muted">${t("Catalog entries are offered in the station editor (antennas) and for measurement series (devices, placements). Values without a datasheet are marked as assumptions in “Source”.")}</p>
    <nav class="subtabs">${KINDS.map(([k, l]) => `<button data-kind="${k}" class="${k === catalogKind ? "active" : ""}">${t(l)} (${catalog[k].length})</button>`).join("")}</nav>
    <div class="tablewrap"><table class="mini"><tr><th>id</th>${cols.map(([, l]) => `<th>${t(l)}</th>`).join("")}<th></th></tr>
      ${catalog[catalogKind].map((x) => `<tr><td><code>${escapeHtml(x.id)}</code></td>${cols.map(([k]) => `<td>${escapeHtml(x[k] ?? "")}</td>`).join("")}
        <td><button class="small" data-edit="${escapeHtml(x.id)}">✎</button><button class="small danger" data-del="${escapeHtml(x.id)}">🗑</button></td></tr>`).join("")}</table></div>
    <div class="actions"><button id="catNew">＋ ${t("New entry")}</button></div><div id="catForm"></div>`;
  body.querySelectorAll("[data-kind]").forEach((b) => b.onclick = () => { catalogKind = b.dataset.kind; catalogPage(body); });
  body.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => catalogForm(body, catalog[catalogKind].find((x) => x.id === b.dataset.edit)));
  body.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm(t("Delete “{id}”?", { id: b.dataset.del }))) return;
    try { await api(`/api/catalog/${catalogKind}/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); catalogPage(body); }
    catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
  });
  body.querySelector("#catNew").onclick = () => catalogForm(body, null);
}

const EXTRA_FIELDS = { antennas: [["tilt_deg", "Tilt (°)"], ["freq_min_mhz", "Frequency from (MHz)"], ["freq_max_mhz", "Frequency to (MHz)"], ["connector", "Connector"], ["url", "Link"], ["note", "Note"]],
  devices: [["antenna_id", "Antenna (catalog id)"]], placements: [["gain_bonus_db", "Gain bonus (dB)"]] };

function catalogForm(body, item) {
  const fields = [...COLUMNS[catalogKind], ...EXTRA_FIELDS[catalogKind]];
  const box = body.querySelector("#catForm");
  box.innerHTML = `<section><h3>${item ? t("Edit “{id}”", { id: escapeHtml(item.id) }) : t("New entry")}</h3>
    <label>id<input data-f="id" value="${escapeHtml(item?.id || "")}" ${item ? "readonly" : ""} placeholder="my-antenna-868"></label>
    <div class="grid2">${fields.map(([k, l]) => k === "type"
      ? `<label>${t(l)}<select data-f="type"><option value="omni" ${item?.type !== "directional" ? "selected" : ""}>${t("omni")}</option><option value="directional" ${item?.type === "directional" ? "selected" : ""}>${t("directional")}</option></select></label>`
      : `<label>${t(l)}<input data-f="${k}" value="${escapeHtml(item?.[k] ?? "")}"></label>`).join("")}</div>
    <div class="actions"><button class="primary" id="catSave">${t("Save")}</button><span class="muted" id="catOut"></span></div></section>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  box.querySelector("#catSave").onclick = async () => {
    const values = {};
    box.querySelectorAll("[data-f]").forEach((el) => { if (el.value !== "") values[el.dataset.f] = el.value; });
    const id = values.id; delete values.id;
    try { await api(`/api/catalog/${catalogKind}/${encodeURIComponent(id || "")}`, { method: "PUT", body: values }); catalogPage(body); }
    catch (e) { box.querySelector("#catOut").textContent = t("Error: {msg}", { msg: e.message }); }
  };
}

// ---------------------------------------------------------------- system
async function systemPage(body) {
  const c = await api("/api/cache");
  body.innerHTML = `<section><h3>${t("Storage")}</h3>
      <table class="kv">${Object.entries(c.cache).map(([k, v]) => `<tr><td>${t("Cache")} ${escapeHtml(k)}</td><td>${fmtBytes(v)}</td></tr>`).join("")}
      ${Object.entries(c.dem).map(([k, v]) => `<tr><td>dem/${escapeHtml(k)}</td><td>${t("{n} files", { n: v.files })}, ${fmtBytes(v.bytes)}</td></tr>`).join("")}</table>
      <div class="actions"><button id="cacheClear" class="danger">${t("Clear computation cache")}</button></div>
      <p class="muted">${t("Removes cached coverage results and terrain grids. Downloaded elevation tiles stay.")}</p></section>
    <section><h3>${t("Version")}</h3><p>TerraRF ${escapeHtml(state.config.version)}</p></section>`;
  body.querySelector("#cacheClear").onclick = async () => {
    if (!confirm(t("Clear the computation cache?"))) return;
    await api("/api/cache/clear", { method: "POST", body: { parts: ["terrain", "coverage"] } });
    systemPage(body);
  };
}


