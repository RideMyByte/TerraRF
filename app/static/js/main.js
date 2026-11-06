/**
 * Start-up and the parts of the shell that tie everything together: login, projects (top bar), map clicks,
 * server status and the footer links.
 *
 * Module map:
 *   core/       i18n, helpers, server calls, shared state + events, shell (workspaces, inspector, status)
 *   map/        Leaflet map with layers panel, coverage compositing, station markers
 *   network.js  CoreScope nodes and neighbour links      measurements.js  drive-test points and pings
 *   profile.js  terrain profile of one path             inspector.js     details of whatever was clicked
 *   workspaces/ plan · network · measure · compare · report               admin.js  administration dialog
 */
import { t, setLanguage, detectLanguage, lang } from "./core/i18n.js";
import { $, escapeHtml, fmt, fmtBytes, download } from "./core/util.js";
import { state, on, emit, isAdmin, isMember, isCollaborator, canCreateProjects, canEditProject } from "./core/state.js";
import { api } from "./core/api.js";
import { initShell, status, openWorkspace, closeInspector } from "./core/ui.js";
import { map, initMap, refreshLayersPanel } from "./map/map.js";
import { stale, resetCoverage, compute, updateProgress, isComputing } from "./map/coverage.js";
import { setStationClickHandler, redrawAllStations, isPlacing, finishPlacing } from "./map/stations.js";
import { setNetworkHandlers, drawNetwork } from "./network.js";
import { setPingHandler, loadMeasurements } from "./measurements.js";
import { closeProfile } from "./profile.js";
import { inspectStation, inspectRepeater, inspectLink, inspectPing, inspectLocation } from "./inspector.js";
import { renderPlan } from "./workspaces/plan.js";
import { initNetworkWorkspace } from "./workspaces/network.js";
import { renderMeasure } from "./workspaces/measure.js";
import { renderCompare } from "./workspaces/compare.js";
import { renderReport } from "./workspaces/report.js";
import { openAdmin } from "./admin.js";
import { openMembers } from "./members.js";
import { openWelcome, maybeWelcome, renderNotice } from "./welcome.js";
import { clearSelection } from "./selection.js";

// ---------------------------------------------------------------- projects
function emptyProject() { return { id: null, name: t("New project"), public: false, owner: state.role || "admin", stations: [] }; }

function setDirty(value) {
  state.dirty = value;
  $("dirtyDot").classList.toggle("on", value && canEditProject());
}

function loadProject(project, { keepUrl = false } = {}) {
  closeInspector(); closeProfile();
  state.project = { stations: [], ...project };
  state.selectedStationId = null;
  $("projectName").value = state.project.name || "";
  $("projectName").disabled = !canEditProject();
  document.body.classList.toggle("project-readonly", !canEditProject());
  if (project.id && !keepUrl) history.replaceState(null, "", `?p=${encodeURIComponent(project.id)}${location.hash}`);
  resetCoverage(); redrawAllStations();
  if (project.view) map.setView(project.view.center, project.view.zoom);
  else if (state.project.stations.length) map.fitBounds(L.latLngBounds(state.project.stations.map((s) => [s.lat, s.lon])).pad(0.5));
  setDirty(false);
  emit("project-loaded");
  drawNetwork();
  if (state.project.stations.some((s) => s.enabled !== false)) compute();   // mostly from the cache
}

async function saveProject() {
  const p = state.project;
  const firstSave = !p.id;
  if (firstSave) p.id = (await api("/api/newid")).id;
  p.name = $("projectName").value.trim() || t("Project");
  p.view = { center: [map.getCenter().lat, map.getCenter().lng], zoom: map.getZoom() };
  const r = await api(`/api/projects/${p.id}`, { method: "PUT", body: p });
  p.updated = r.updated;
  p.owner = r.project?.owner || p.owner;
  setDirty(false);
  history.replaceState(null, "", `?p=${encodeURIComponent(p.id)}${location.hash}`);
  if (firstSave) loadMeasurements();    // measurements belong to the project from now on (#53)
  status(p.public ? t("Saved – publicly visible.") : t("Saved (private)."));
}

const discardOk = () => !state.dirty || confirm(t("Discard unsaved changes?"));

async function openProjectMenu() {
  const menu = $("projectMenu");
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  let list = [];
  try { list = await api("/api/projects"); } catch { /* not allowed */ }
  const p = state.project;
  menu.innerHTML = `
    ${list.length ? `<div class="menu-h">${t("Open")}</div>${list.map((x) => `<button data-open="${escapeHtml(x.id)}" class="${x.id === p.id ? "sel" : ""}">${escapeHtml(x.name)} <span class="muted">${t("{n} st.", { n: x.stations })}${isMember() ? " · " + (x.public ? t("public") : t("private")) : ""}${isAdmin() && x.owner === "user" ? " · " + t("by the user") : ""}${isMember() && !x.editable ? " · 🔒" : ""}</span></button>`).join("")}<hr>` : ""}
    ${canCreateProjects() ? `<button data-a="new">＋ ${t("New project")}</button>` : ""}
    ${canEditProject() && canCreateProjects() ? `<label class="chk menu-chk"><input type="checkbox" data-a="public" ${p.public ? "checked" : ""}> ${t("public (visitors can see it)")}</label>` : ""}
    ${canCreateProjects() && !canEditProject() ? `<button data-a="copy">⧉ ${t("Save as my own copy")}</button>` : ""}
    ${p.id ? `<button data-a="share">🔗 ${t("Copy link")}</button>` : ""}
    <button data-a="export">⇩ ${t("Export (JSON)")}</button>
    ${isAdmin() && p.id ? `<button data-a="members">👥 ${t("Share & collaborators")}</button>` : ""}
    ${canCreateProjects() ? `<button data-a="import">⇧ ${t("Import (JSON)")}</button>` : ""}
    ${canEditProject() && canCreateProjects() && p.id ? `<button data-a="delete" class="danger">🗑 ${t("Delete project")}</button>` : ""}`;
  menu.classList.remove("hidden");
  menu.querySelectorAll("[data-open]").forEach((b) => b.onclick = async () => {
    menu.classList.add("hidden");
    if (!discardOk()) return;
    try { loadProject(await api(`/api/projects/${encodeURIComponent(b.dataset.open)}`)); } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
  });
  const act = (a, fn) => { const el = menu.querySelector(`[data-a="${a}"]`); if (el) el[el.type === "checkbox" ? "onchange" : "onclick"] = (e) => { if (el.type !== "checkbox") menu.classList.add("hidden"); fn(e); }; };
  act("new", () => { if (discardOk()) loadProject(emptyProject()); });
  act("members", () => openMembers(p));
  act("public", (e) => { p.public = e.target.checked; setDirty(true); });
  act("copy", () => {
    const copy = JSON.parse(JSON.stringify({ ...p, id: null, public: false, owner: state.role, name: t("Copy of {name}", { name: p.name }) }));
    loadProject(copy); setDirty(true);
    saveProject().catch((e) => status(t("Error: {msg}", { msg: e.message }), "err"));
  });
  act("share", async () => {
    const url = `${location.origin}${location.pathname}?p=${encodeURIComponent(p.id)}`;
    try { await navigator.clipboard.writeText(url); status(p.public ? t("Link copied.") : t("Link copied – note: the project is private, visitors cannot see it.")); }
    catch { prompt(t("Link to the project:"), url); }
  });
  act("export", () => download(new Blob([JSON.stringify({ ...p, name: $("projectName").value }, null, 1)], { type: "application/json" }),
    (p.name || "project").replace(/[^\w\-äöüÄÖÜß ]/g, "_") + ".json"));
  act("import", () => {
    const input = document.createElement("input"); input.type = "file"; input.accept = ".json,application/json";
    input.onchange = async () => {
      const file = input.files[0]; if (!file || !discardOk()) return;
      try { const imported = JSON.parse(await file.text()); imported.id = null; imported.owner = state.role; loadProject(imported); setDirty(true); }
      catch (e) { status(t("Import failed: {msg}", { msg: e.message }), "err"); }
    };
    input.click();
  });
  act("delete", async () => {
    if (!confirm(t("Delete project “{name}” on the server?", { name: p.name }))) return;
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
    history.replaceState(null, "", location.pathname);
    loadProject(emptyProject());
  });
}

// ---------------------------------------------------------------- login
function applyRole() {
  const role = state.role;
  document.body.classList.toggle("role-admin", role === "admin");
  document.body.classList.toggle("role-user", role === "user" || role === "project");   // member-only elements
  document.body.classList.toggle("readonly", !isMember());
  $("btnLogin").textContent = isMember() ? t("Log out ({user})", { user: state.config.auth.user }) : t("Log in");
}

function wireLogin() {
  $("btnLogin").onclick = async () => {
    if (!isMember()) { $("loginError").textContent = ""; $("loginDlg").showModal(); return; }
    if (!discardOk()) return;
    state.dirty = false;
    await api("/api/logout", { method: "POST" });
    location.reload();
  };
  $("loginForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/login", { method: "POST", body: { user: $("loginUser").value, password: $("loginPassword").value, remember: $("loginRemember").checked } });
      location.reload();
    } catch (er) { $("loginError").textContent = er.message; }
  };
  $("loginCancel").onclick = () => $("loginDlg").close();
}

// ---------------------------------------------------------------- footer, install, server status
function renderSiteLinks() {
  const s = state.config.site || {}, parts = [];
  if (s.parent) parts.push(t("Part of {name}", { name: s.parent.url ? `<a href="${escapeHtml(s.parent.url)}" target="_blank" rel="noopener">${escapeHtml(s.parent.name || s.parent.url)}</a>` : escapeHtml(s.parent.name) }));
  if (s.imprint) parts.push(`<a href="${escapeHtml(s.imprint)}" target="_blank" rel="noopener">${t("Imprint")}</a>`);
  parts.push(`<a href="${escapeHtml(s.privacy || "/datenschutz")}" target="_blank" rel="noopener">${t("Privacy")}</a>`);
  if (s.github) parts.push(`<a href="${escapeHtml(s.github)}" target="_blank" rel="noopener">GitHub</a>`);
  parts.push(`<a href="#" id="lnkInstall">${t("Install as app")}</a>`);
  $("siteLinks").innerHTML = parts.join(" · ");
  $("lnkInstall").onclick = (e) => { e.preventDefault(); showInstall(); };
}

function showInstall() {
  const secure = location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname);
  $("installDlg").innerHTML = `<h3>${t("TerraRF as an app")}</h3>
    <p class="muted">${t("TerraRF can be put on the home screen and then opens without the browser bar.")}</p>
    <p><b>Android (Chrome, Edge, Samsung):</b> ${t("menu ⋮ → “Install app” or “Add to home screen”.")}</p>
    <p><b>iPhone/iPad (Safari):</b> ${t("share icon □↑ → “Add to Home Screen”.")}</p>
    <p><b>Desktop (Chrome, Edge):</b> ${t("install icon on the right of the address bar.")}</p>
    ${secure ? "" : `<p class="warn">${t("This page runs without HTTPS. It can only be installed as a stand-alone app over HTTPS (e.g. behind a reverse proxy with a certificate) – otherwise you get a bookmark that opens in the browser.")}</p>`}
    <div class="actions"><button class="primary" id="installClose">OK</button></div>`;
  $("installClose").onclick = () => $("installDlg").close();
  $("installDlg").showModal();
}

let statusTimer = null;
async function pollStatus() {
  clearTimeout(statusTimer);
  if (document.hidden) { statusTimer = setTimeout(pollStatus, 10000); return; }
  let busyNow = false;
  try {
    const r = await api("/api/status");
    const items = r.busy || [];
    updateProgress(items);
    if (isComputing() && !items.length) items.push({ what: t("waiting for the server …") });
    busyNow = items.length > 0;
    $("sysbox").className = busyNow ? "busy" : "";
    $("sysText").textContent = busyNow ? t("computing") + ": " + items.map((x) => x.what + (x.seconds != null ? ` ${fmt(x.seconds, 0)} s` : "")).join(" · ") : t("ready");
    $("sysRes").textContent = r.res ? ` · CPU ${fmt(r.res.cpu_pct ?? 0, 0)} % · RAM ${fmtBytes(r.res.mem_used)}${r.res.mem_limit ? " / " + fmtBytes(r.res.mem_limit) : ""}` : "";
  } catch { $("sysbox").className = "err"; $("sysText").textContent = t("server not reachable"); }
  statusTimer = setTimeout(pollStatus, busyNow || isComputing() ? 1500 : 8000);
}

// ---------------------------------------------------------------- wiring
function wire() {
  initShell();
  initMap();
  wireLogin();
  setStationClickHandler((st) => { state.selectedStationId = st.id; inspectStation(st); emit("selection-changed"); });
  setNetworkHandlers({ node: inspectRepeater, link: inspectLink });
  setPingHandler(inspectPing);

  map.on("click", (e) => {
    if (!isPlacing()) { closeInspector(); clearSelection(); return; }   // #51: a click into the empty map ends the selection
    const st = finishPlacing(e.latlng);
    if (st) { state.selectedStationId = st.id; inspectStation(st, "settings"); }
  });
  map.on("contextmenu", (e) => { if (!isPlacing()) inspectLocation(e.latlng); });   // right click / long press

  $("projectName").onchange = () => { state.project.name = $("projectName").value; setDirty(true); };
  $("btnSave").onclick = () => saveProject().catch((e) => status(t("Error: {msg}", { msg: e.message }), "err"));
  $("btnProjectMenu").onclick = (e) => { e.stopPropagation(); openProjectMenu(); };
  document.addEventListener("click", (e) => { if (!e.target.closest("#projectMenu, #btnProjectMenu")) $("projectMenu").classList.add("hidden"); });
  $("btnAdmin").onclick = () => openAdmin();
  $("btnHelp").onclick = () => openWelcome();

  const langSelect = $("langSelect");
  langSelect.value = lang();
  langSelect.onchange = async () => {
    await setLanguage(langSelect.value, { remember: true });
    applyRole(); renderSiteLinks(); renderNotice(); refreshLayersPanel(); redrawAllStations();
    for (const render of [renderPlan, renderMeasure, renderCompare, renderReport]) render();
    initNetworkWorkspace();
    closeInspector();
  };

  window.addEventListener("beforeunload", (e) => { if (state.dirty && canEditProject()) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });
}

// a change in the project: mark it dirty, the changed station stale
on("project-changed", (payload = {}) => {
  if (!canEditProject()) return;
  setDirty(true);
  if (payload.station && !payload.keepCoverage) stale.add(payload.station.id);
});
on("config-reloaded", () => { applyRole(); renderSiteLinks(); renderNotice(); });
on("settings-changed", () => { resetCoverage(); emit("project-loaded"); });

async function start() {
  await setLanguage(detectLanguage());
  state.config = await api("/api/config");
  state.role = state.config.auth?.role || null;
  const v = state.config.version;
  $("version").textContent = !v ? "" : /^\d/.test(v) ? "v" + v : v;
  applyRole();
  wire();
  renderSiteLinks();
  renderNotice();
  openWorkspace("plan", { openSheet: false });
  state.project = emptyProject();
  try { state.catalog = await api("/api/catalog"); emit("catalog-loaded"); } catch (e) { console.warn("catalog", e); }
  pollStatus();
  initNetworkWorkspace().catch((e) => console.warn("CoreScope", e));

  if (!isMember() && !state.config.auth?.public_read) { loadProject(emptyProject()); status(t("Please log in.")); $("loginDlg").showModal(); return; }
  const list = await api("/api/projects").catch(() => []);
  const wanted = new URLSearchParams(location.search).get("p");
  const fallback = isAdmin() ? state.config.last_project : isCollaborator() ? state.config.auth.project : list[0]?.id;
  for (const pick of [...new Set([wanted, fallback].filter(Boolean))]) {
    try {
      // a link to a project this visitor may not see: keep it in the address bar, so logging in opens it
      loadProject(await api(`/api/projects/${encodeURIComponent(pick)}`), { keepUrl: !!wanted && pick !== wanted && !isMember() });
      status(pick === wanted || !wanted ? t("Project “{name}” loaded. Right click (long press on phones) shows levels and the profile at a point.", { name: state.project.name })
        : t("Project not found or not public."), pick === wanted || !wanted ? "" : "err");
      return;
    } catch { /* try the next one */ }
  }
  if (wanted) status(t("Project not found or not public."), "err");
  loadProject(emptyProject());
  if (wanted) return;
  if (isMember()) status(t("Ready. “Add station”, then click on the map. Right click (long press on phones) shows levels and the profile at a point."));
  else status(t("No public projects yet."));
}

async function init() {
  await start();
  maybeWelcome();
}

init().catch((e) => { console.error(e); status(t("Start failed: {msg}", { msg: e.message }), "err"); });
