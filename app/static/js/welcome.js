/**
 * #44 welcome page: a short "what is this and how do I use it" for first-time visitors, again any time via "?".
 * #46 notice banner: a strip the admin switches on (beta, under construction, maintenance, updates, own text).
 */
import { t } from "./core/i18n.js";
import { $, escapeHtml } from "./core/util.js";
import { state, isMember } from "./core/state.js";
import { openWorkspace } from "./core/ui.js";

const SEEN_KEY = "terrarf.welcome";   // remembered in the browser so the page is shown once (privacy policy)

export function openWelcome() {
  const network = !!state.config?.corescope?.enabled;
  const steps = [
    ["◎ " + t("Plan"), t("Stations on the map: coverage, overlap, best server and line of sight over real terrain (NRW laser scan, Copernicus elsewhere).")],
    ["⋈ " + t("Network"), t("The real MeshCore network from CoreScope: repeaters, their neighbours and the SNR they report.")],
    ["⌖ " + t("Measure"), t("Drive tests from the MeshMapper app, CSV files or entered by hand.")],
    ["⇄ " + t("Compare"), t("How well the model matches the measurements – per station and source.")],
    ["▤ " + t("Report"), t("Key figures and a PDF report per station.")],
  ];
  $("welcomeDlg").innerHTML = `<h2>${t("Welcome to TerraRF")}</h2>
    <p>${t("TerraRF is a hub for everything about radio reception in the mesh: plan where a station would reach, look at the real network, measure in the field and compare the model with reality.")}</p>
    <div class="ws-list">${steps.map(([a, b]) => `<b>${a}</b><span>${b}</span>`).join("")}</div>
    <h4>${t("How to start")}</h4>
    <ul>
      <li>${t("Open a project in the menu ▾ next to the project name; public projects can be viewed without login.")}</li>
      <li>${t("Tap a station, a repeater or a measurement point for its details. Right click (long press on phones) anywhere shows the levels and the terrain profile there.")}</li>
      <li>${t("Switch views and base maps with “Layers” at the top right of the map.")}</li>
      ${network ? `<li>${t("Already running a MeshCore repeater? Logged in, use “Take over from MeshCore” in “Plan”: the repeater becomes a station with its position from CoreScope – then only height, antenna and power need checking.")}</li>` : ""}
      <li>${isMember() ? t("You are logged in: changes are saved with “Save”.") : t("To plan your own stations or share measurements, ask the operator of this site for a login.")}</li>
    </ul>
    <p class="muted">${t("The model is checked against measurements, but it is a model: treat coverage maps as an estimate, not a promise.")}</p>
    <div class="actions"><button class="primary" data-a="go">${t("Let's go")}</button><button data-a="network">${network ? t("Show the network") : t("Close")}</button></div>`;
  const close = () => { try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* private mode */ } $("welcomeDlg").close(); };
  $("welcomeDlg").querySelector('[data-a="go"]').onclick = close;
  $("welcomeDlg").querySelector('[data-a="network"]').onclick = () => { close(); if (network) openWorkspace("network"); };
  $("welcomeDlg").onclose = () => { try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* ignore */ } };
  if (!$("welcomeDlg").open) $("welcomeDlg").showModal();
}

/** Show the welcome page once for visitors who have not seen it (and are not logged in). */
export function maybeWelcome() {
  if (!state.config?.site?.welcome || isMember() || $("loginDlg").open) return;
  let seen = false;
  try { seen = !!localStorage.getItem(SEEN_KEY); } catch { /* ignore */ }
  if (!seen) openWelcome();
}

const NOTICES = {
  beta: "Beta: TerraRF is still evolving – things may change or break.",
  construction: "Under construction: I am working on the site right now – some things may not work for a moment.",
  maintenance: "Maintenance: the site may be unavailable for a while.",
  updates: "Updates are coming – new features soon.",
  custom: "",
};

export function renderNotice() {
  const bar = $("noticeBar"), notice = state.config?.site?.notice;
  const text = notice ? [t(NOTICES[notice.kind] || ""), notice.text].filter(Boolean).join(" ") : "";
  bar.className = notice && text ? notice.kind : "hidden";
  document.body.classList.toggle("has-notice", !!(notice && text));
  if (!notice || !text) return;
  bar.innerHTML = `<span>${escapeHtml(text)}</span><button aria-label="${escapeHtml(t("Close"))}">✕</button>`;
  bar.querySelector("button").onclick = () => { bar.className = "hidden"; document.body.classList.remove("has-notice"); };
}
