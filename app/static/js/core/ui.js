/**
 * Shell of the user interface: status line and toast, workspaces (rail + panel), the inspector, and the
 * bottom sheets on phones.
 */
import { t } from "./i18n.js";
import { $, escapeHtml } from "./util.js";
import { emit } from "./state.js";

export const phoneQuery = window.matchMedia("(max-width: 800px)");
export const isPhone = () => phoneQuery.matches;

// ---------------------------------------------------------------- status
let toastTimer = null;
/** Status line in the panel footer; also a toast on the map (the panel is often closed on phones). */
export function status(message, kind = "") {
  const line = $("status");
  line.textContent = message; line.className = kind;
  const toast = $("toast");
  toast.textContent = message; toast.className = "show " + kind;
  clearTimeout(toastTimer);
  if (kind !== "busy") toastTimer = setTimeout(() => toast.classList.remove("show"), kind === "err" ? 8000 : 3500);
}

// ---------------------------------------------------------------- workspaces
let currentWorkspace = "plan";
export const workspace = () => currentWorkspace;

/** Open a workspace (plan, network, measure, compare, report). On phones this opens the sheet. */
export function openWorkspace(name, { openSheet = true } = {}) {
  currentWorkspace = name;
  document.querySelectorAll("#rail button").forEach((b) => b.classList.toggle("active", b.dataset.ws === name));
  document.querySelectorAll(".ws").forEach((s) => s.classList.toggle("active", s.id === "ws-" + name));
  document.body.className = document.body.className.replace(/\bws-\w+/g, "").trim() + " ws-" + name;
  if (isPhone() && openSheet) { closeInspector(); document.body.classList.add("sheet-open"); }
  emit("workspace", { name });
}

export function closeSheet() { document.body.classList.remove("sheet-open", "sheet-full"); }

/** Tap the handle: half → full → closed (phones). */
function cycleSheet() {
  const b = document.body;
  if (!b.classList.contains("sheet-full")) b.classList.add("sheet-full");
  else closeSheet();
}

// ---------------------------------------------------------------- inspector
let inspectorState = null;   // {kind, id, tabs, render, onClose}

/**
 * Show something in the inspector.
 *   kind:  label above the title ("Station", "Repeater", "Link", "Measurement", "Location")
 *   title: text or an element (e.g. a name input)
 *   tabs:  [{id, label, render(body)}] – the first is shown unless `tab` names another
 */
export function showInspector({ kind, key, title, tabs, tab, onClose }) {
  const sameObject = inspectorState && inspectorState.key === key;
  const activeTab = tab || (sameObject ? inspectorState.tab : null) || tabs[0].id;
  if (inspectorState && !sameObject && inspectorState.onClose) inspectorState.onClose();
  inspectorState = { kind, key, tabs, tab: activeTab, onClose };
  const head = $("inspectorHead");
  head.innerHTML = `<div style="flex:1;min-width:0"><div class="kind">${escapeHtml(kind)}</div><div class="title"></div></div>
    <button class="iconbtn" id="inspectorClose" title="${escapeHtml(t("Close"))}" aria-label="${escapeHtml(t("Close"))}">✕</button>`;
  const titleBox = head.querySelector(".title");
  if (title instanceof Element) titleBox.appendChild(title); else titleBox.textContent = title || "";
  $("inspectorClose").onclick = closeInspector;
  const nav = $("inspectorTabs");
  nav.innerHTML = tabs.length > 1 ? tabs.map((x) => `<button data-tab="${x.id}" class="${x.id === activeTab ? "active" : ""}">${escapeHtml(x.label)}</button>`).join("") : "";
  nav.querySelectorAll("button").forEach((b) => b.onclick = () => showInspector({ kind, key, title, tabs, tab: b.dataset.tab, onClose }));
  $("inspector").classList.remove("hidden");
  if (isPhone()) document.body.classList.remove("sheet-open");
  renderInspectorTab();
  setTimeout(() => window.dispatchEvent(new Event("resize")), 30);   // map width changed
}

/** Re-render the current tab (after data changed). */
export function renderInspectorTab() {
  if (!inspectorState) return;
  const body = $("inspectorBody");
  const tab = inspectorState.tabs.find((x) => x.id === inspectorState.tab) || inspectorState.tabs[0];
  body.innerHTML = "";
  tab.render(body);
}

export const inspectorKey = () => inspectorState?.key || null;

export function closeInspector() {
  if (!inspectorState) return;
  const done = inspectorState.onClose;
  inspectorState = null;
  $("inspector").classList.add("hidden");
  if (done) done();
  setTimeout(() => window.dispatchEvent(new Event("resize")), 30);
}

// ---------------------------------------------------------------- wiring
export function initShell() {
  document.querySelectorAll("#rail button").forEach((b) => b.onclick = () => {
    if (isPhone() && b.dataset.ws === currentWorkspace && document.body.classList.contains("sheet-open")) { closeSheet(); return; }
    openWorkspace(b.dataset.ws);
  });
  $("panelHandle").onclick = cycleSheet;
  document.querySelector("#inspector .sheet-handle").onclick = () => document.body.classList.toggle("sheet-full");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !document.querySelector("dialog[open]")) closeInspector(); });
}

/** A collapsible section; remembers which are open across re-renders. */
const openSections = new Set(["loc"]);
export function section(id, title, body, summary = "") {
  return `<details class="esec" data-sec="${id}" ${openSections.has(id) ? "open" : ""}><summary><span>${title}</span>${summary ? `<span class="muted">${summary}</span>` : ""}</summary><div>${body}</div></details>`;
}
export function rememberSections(root) {
  root.querySelectorAll("details.esec").forEach((d) => d.addEventListener("toggle", () => {
    if (d.open) openSections.add(d.dataset.sec); else openSections.delete(d.dataset.sec);
  }));
}

/** Source badge for measurements and comparisons. */
const SOURCES = { meshmapper: ["MM", "mm", "MeshMapper"], manual: ["✎", "man", "manual entry"], csv: ["CSV", "csv", "CSV import"], corescope: ["CS", "cs", "CoreScope"] };
export function sourceBadge(source) {
  const s = SOURCES[source] || [source || "?", "man", source || "?"];
  return `<span class="srcb ${s[1]}" title="${escapeHtml(t(s[2]))}">${escapeHtml(s[0])}</span>`;
}
export const sourceName = (source) => t((SOURCES[source] || [, , source])[2]);
