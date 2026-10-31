/* TerraRF frontend (vanilla JS + Leaflet) */
(() => {
"use strict";

// ------------------------------------------------------------ presets
const RADIO_PRESETS = {
  meshcore_eu: { label: "MeshCore EU/UK Narrow (869,618 MHz, 62,5 kHz, SF8)", freq_mhz: 869.618, bw_khz: 62.5, mode: "lora", sf: 8, tx_power_dbm: 22, nf_db: 6 },
  meshtastic_lf: { label: "Meshtastic EU LongFast (869,525 MHz, 250 kHz, SF11)", freq_mhz: 869.525, bw_khz: 250, mode: "lora", sf: 11, tx_power_dbm: 22, nf_db: 6 },
  lora_custom: { label: "LoRa benutzerdefiniert", mode: "lora" },
  fm_2m: { label: "2 m FM (145 MHz, 12,5 kHz)", freq_mhz: 145.5, bw_khz: 12.5, mode: "fm", req_snr_db: 10, tx_power_dbm: 37, nf_db: 5 },
  fm_70cm: { label: "70 cm FM (438 MHz, 12,5 kHz)", freq_mhz: 438.5, bw_khz: 12.5, mode: "fm", req_snr_db: 10, tx_power_dbm: 37, nf_db: 5 },
  pmr446: { label: "PMR446 (446,1 MHz, 0,5 W)", freq_mhz: 446.1, bw_khz: 12.5, mode: "fm", req_snr_db: 10, tx_power_dbm: 27, nf_db: 6 },
  hamnet5: { label: "HAMNET 5,7 GHz (10 MHz, MCS0)", freq_mhz: 5765, bw_khz: 10000, mode: "custom", req_snr_db: 5, tx_power_dbm: 23, nf_db: 7 },
  wifi24: { label: "WLAN 2,4 GHz (20 MHz, MCS0)", freq_mhz: 2442, bw_khz: 20000, mode: "custom", req_snr_db: 5, tx_power_dbm: 20, nf_db: 7 },
  custom: { label: "Benutzerdefiniert" },
};
const ANT_PRESETS = {
  omni2: { label: "Rundstrahler 2,15 dBi (Dipol/Stab)", type: "omni", gain_dbi: 2.15, vbw_deg: null, fb_db: 25 },
  omni5: { label: "Rundstrahler 5,8 dBi (Kollinear)", type: "omni", gain_dbi: 5.8, vbw_deg: null, fb_db: 25 },
  omni8: { label: "Rundstrahler 8 dBi", type: "omni", gain_dbi: 8, vbw_deg: null, fb_db: 25 },
  sector90: { label: "Sektor 90° / 14 dBi", type: "directional", gain_dbi: 14, hbw_deg: 90, vbw_deg: 8, fb_db: 25 },
  sector120: { label: "Sektor 120° / 13 dBi", type: "directional", gain_dbi: 13, hbw_deg: 120, vbw_deg: 8, fb_db: 22 },
  yagi: { label: "Yagi 9 dBi", type: "directional", gain_dbi: 9, hbw_deg: 50, vbw_deg: 50, fb_db: 18 },
  panel: { label: "Panel/Patch 14 dBi", type: "directional", gain_dbi: 14, hbw_deg: 30, vbw_deg: 30, fb_db: 25 },
  dish: { label: "Parabol 24 dBi", type: "directional", gain_dbi: 24, hbw_deg: 8, vbw_deg: 8, fb_db: 30 },
  custom: { label: "Benutzerdefiniert" },
};
const LORA_SNR = { 5: -2.5, 6: -5, 7: -7.5, 8: -10, 9: -12.5, 10: -15, 11: -17.5, 12: -20 };
const COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46c0c8", "#f032e6", "#9a6324", "#808000", "#000075"];

// ------------------------------------------------------------ state
let cfg = null;
let project = null;
let selected = null;
let lastRun = null;
let placing = false;
let dirty = false;
const markers = {};
const azLines = {};
let isAdmin = false;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Math.random().toString(36).slice(2, 10);
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d).replace(".", ",");

// ------------------------------------------------------------ Handy-Layout
const narrowQ = window.matchMedia("(max-width: 800px)");
const narrow = () => narrowQ.matches;
function openSide() { document.body.classList.add("side-open"); }
function closeSide() { document.body.classList.remove("side-open"); }

let mstT = null;
function status(msg, cls = "") {
  const s = $("status"); s.textContent = msg; s.className = cls;
  // Handy: Seitenleiste ist meist zu, daher Meldung kurz auf der Karte zeigen
  const m = $("mstatus"); if (!m) return;
  m.textContent = msg; m.className = cls; m.classList.add("show");
  clearTimeout(mstT);
  if (cls !== "busy") mstT = setTimeout(() => m.classList.remove("show"), cls === "err" ? 8000 : 3500);
}
function setDirty(v = true) { dirty = v; $("dirty").className = v ? "on" : ""; }

async function api(path, opts = {}) {
  const o = { headers: { "Content-Type": "application/json" }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) { const er = new Error((data && data.detail) || `HTTP ${r.status}`); er.status = r.status; throw er; }
  return data;
}

// ------------------------------------------------------------ map
const map = L.map("map", { zoomControl: true, preferCanvas: false }).setView([51.575, 6.985], 12);
const overlays = {};
let base = {};
function setupBasemaps(proxy) {
  const osmA = "© OpenStreetMap-Mitwirkende";
  const osm = proxy ? "/tiles/osm/{z}/{x}/{y}.png" : "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const otm = proxy ? "/tiles/otm/{z}/{x}/{y}.png" : "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png";
  const dop = proxy ? "/tiles/wms/nrwdop" : "https://www.wms.nrw.de/geobasis/wms_nw_dop";
  base = {
    "OpenStreetMap": L.tileLayer(osm, { maxZoom: 19, attribution: osmA }),
    "OpenStreetMap (Graustufen)": L.tileLayer(osm, { maxZoom: 19, className: "osm-gray", attribution: osmA }),
    "OpenTopoMap": L.tileLayer(otm, { maxZoom: 17, attribution: osmA + ", SRTM | © OpenTopoMap (CC-BY-SA)" }),
    "Luftbild NRW (DOP)": L.tileLayer.wms(dop, { layers: "nw_dop_rgb", format: "image/jpeg", maxZoom: 20, attribution: "© Geobasis NRW (dl-de/zero-2-0)" }),
  };
  // Esri nur ohne Proxy: dessen Nutzungsbedingungen zum Zwischenspeichern sind nicht geprüft
  if (!proxy) base["Satellit (Esri)"] = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Tiles © Esri, Maxar, Earthstar Geographics" });
  base["OpenStreetMap"].addTo(map);
  L.control.layers(base, overlays, { position: "topright" }).addTo(map);
}
L.control.scale({ imperial: false }).addTo(map);
if (location.hash === "#debug") window.trfMap = map;  // für automatische Tests

function stationIcon(st) {
  const c = st.color || "#3388ff";
  const off = 1;
  return L.divIcon({
    className: "",
    html: `<svg width="26" height="34" viewBox="0 0 26 34" style="opacity:${off}"><path d="M13 33 L4 15 A10 10 0 1 1 22 15 Z" fill="${c}" stroke="#fff" stroke-width="2"/><circle cx="13" cy="11" r="4" fill="#fff"/></svg>`,
    iconSize: [26, 34], iconAnchor: [13, 33], popupAnchor: [0, -30],
  });
}

function bearing(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, p1 = lat1 * r, p2 = lat2 * r, dl = (lon2 - lon1) * r;
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) / r + 360) % 360;
}

function destPoint(lat, lon, brgDeg, distM) {
  const R = 6371000, b = brgDeg * Math.PI / 180, d = distM / R;
  const la1 = lat * Math.PI / 180, lo1 = lon * Math.PI / 180;
  const la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(b));
  const lo2 = lo1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la1), Math.cos(d) - Math.sin(la1) * Math.sin(la2));
  return [la2 * 180 / Math.PI, lo2 * 180 / Math.PI];
}

function drawStation(st) {
  if (markers[st.id]) { map.removeLayer(markers[st.id]); delete markers[st.id]; }
  if (azLines[st.id]) { map.removeLayer(azLines[st.id]); delete azLines[st.id]; }
  if (st.enabled === false) return;  // deactivated stations are not shown on the map
  const m = L.marker([st.lat, st.lon], { draggable: isAdmin, icon: stationIcon(st), title: st.name });
  m.on("click", (e) => { L.DomEvent.stopPropagation(e); select(st.id); csStationClicked(st); });
  m.on("dragend", () => {
    const ll = m.getLatLng();
    st.lat = +ll.lat.toFixed(6); st.lon = +ll.lng.toFixed(6);
    changed(st); drawStation(st);
  });
  m.addTo(map);
  markers[st.id] = m;
  const a = st.antenna || {};
  if (a.type === "directional") {
    const len = Math.min(3000, (st.radius_km || 5) * 1000 * 0.35);
    const geo = (az) => {
      const hb = (a.hbw_deg || 60) / 2;
      return { line: [[st.lat, st.lon], destPoint(st.lat, st.lon, az, len)],
        wedge: [[st.lat, st.lon], destPoint(st.lat, st.lon, az - hb, len * 0.8), destPoint(st.lat, st.lon, az + hb, len * 0.8)] };
    };
    const g0 = geo(a.azimuth_deg || 0);
    const wedge = L.polygon(g0.wedge, { color: st.color, weight: 1, fillOpacity: 0.12, interactive: false });
    const line = L.polyline(g0.line, { color: st.color, weight: 3, interactive: false });
    const layers = [wedge, line];
    if (isAdmin) {
      // Griff am Ende der Hauptstrahlrichtung: mit der Maus/dem Finger drehen
      const h = L.marker(g0.line[1], { draggable: true, zIndexOffset: 500, title: "Antenne drehen",
        icon: L.divIcon({ className: "", iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="az-handle" style="border-color:${st.color}"></div>` }) });
      h.bindTooltip("", { direction: "top", offset: [0, -8] });
      h.on("drag", () => {
        const ll = h.getLatLng();
        const az = Math.round(bearing(st.lat, st.lon, ll.lat, ll.lng));
        a.azimuth_deg = az;
        const g = geo(az);
        wedge.setLatLngs(g.wedge); line.setLatLngs(g.line);
        h.setTooltipContent(`Azimut ${az}°`); h.openTooltip();
      });
      h.on("dragend", () => { h.setLatLng(geo(a.azimuth_deg).line[1]); h.closeTooltip(); changed(st); if (selected === st.id) renderEditor(); });
      layers.push(h);
    }
    azLines[st.id] = L.layerGroup(layers).addTo(map);
  }
}

// ------------------------------------------------------------ project
function defaultStation(lat, lon) {
  const prev = project.stations[project.stations.length - 1];
  const tmpl = prev ? JSON.parse(JSON.stringify(prev)) : {
    height_m: 10, height_ref: "ground", radio_preset: "meshcore_eu", ...stripLabel(RADIO_PRESETS.meshcore_eu),
    cable_loss_db: 1, radius_km: 10, sens_override_dbm: null, req_snr_db: 10,
    antenna: { preset: "omni5", ...stripLabel(ANT_PRESETS.omni5), azimuth_deg: 0, tilt_deg: 0, hbw_deg: 60 },
  };
  const n = project.stations.length;
  return { ...tmpl, id: uid(), name: `Station ${n + 1}`, color: COLORS[n % COLORS.length], enabled: true,
    lat: +lat.toFixed(6), lon: +lon.toFixed(6), notes: "" };
}
function stripLabel(o) { const c = { ...o }; delete c.label; return c; }

function newProject() {
  project = { id: null, name: "Neues Projekt", settings: { ...cfg.defaults }, stations: [], view: null };
  for (const k of Object.keys(markers)) { map.removeLayer(markers[k]); delete markers[k]; }
  for (const k of Object.keys(azLines)) { map.removeLayer(azLines[k]); delete azLines[k]; }
  clearOverlay(); selected = null; lastRun = null; runStations = {};
  $("projName").value = project.name;
  renderAll(); setDirty(false);
}

function loadProject(p) {
  newProject();
  project = p;
  project.settings = { ...cfg.defaults, ...(p.settings || {}) };
  project.stations = p.stations || [];
  $("projName").value = p.name || "Projekt";
  $("projPublic").checked = !!p.public;
  if (p.id) history.replaceState(null, "", `?p=${encodeURIComponent(p.id)}`);
  project.stations.forEach(drawStation);
  if (p.view) map.setView(p.view.center, p.view.zoom);
  else if (project.stations.length) map.fitBounds(L.latLngBounds(project.stations.map((s) => [s.lat, s.lon])).pad(0.5));
  renderAll(); setDirty(false);
  if (csNodes.length) csDrawNodes();
  status(`Projekt „${p.name}“ geladen.`);
  if ((!isAdmin || $("autoCalc").checked) && project.stations.some((x) => x.enabled !== false)) compute(true);
}

async function saveProject() {
  if (!project.id) project.id = (await api("/api/newid")).id;
  project.name = $("projName").value.trim() || "Projekt";
  project.public = $("projPublic").checked;
  project.view = { center: [map.getCenter().lat, map.getCenter().lng], zoom: map.getZoom() };
  const r = await api(`/api/projects/${project.id}`, { method: "PUT", body: project });
  project.updated = r.updated;
  setDirty(false); status(project.public ? "Gespeichert – öffentlich sichtbar." : "Gespeichert (privat)."); refreshProjectList();
  history.replaceState(null, "", `?p=${encodeURIComponent(project.id)}`);
}

async function refreshProjectList() {
  const list = await api("/api/projects");
  const sel = $("projList");
  sel.innerHTML = `<option value="">Laden … (${list.length})</option>` +
    list.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} · ${p.stations} St.${isAdmin ? (p.public ? " · öffentlich" : " · privat") : ""}</option>`).join("");
  return list;
}

// ------------------------------------------------------------ station list + editor
function renderAll() { renderList(); renderEditor(); renderSettings(); }

function renderList() {
  const el = $("stationList");
  if (!project.stations.length) {
    el.innerHTML = `<p class="note">Noch keine Station. „＋ Station platzieren“ und dann in die Karte klicken.</p>`;
    return;
  }
  el.innerHTML = project.stations.map((st) => `
    <div class="st ${st.id === selected ? "sel" : ""} ${st.enabled === false ? "off" : ""}" data-id="${st.id}">
      <input type="checkbox" class="en" ${st.enabled !== false ? "checked" : ""} title="In Berechnung einbeziehen">
      <span class="dot" style="background:${esc(st.color)}"></span>
      <span class="nm">${esc(st.name)}<br><span class="sub">${fmt(st.freq_mhz, 3)} MHz · ${fmt(st.tx_power_dbm, 0)} dBm · ${fmt(st.height_m, 0)} m · ${esc((ANT_PRESETS[st.antenna?.preset] || {}).label || st.antenna?.type)}</span></span>
    </div>`).join("");
  el.querySelectorAll(".st").forEach((d) => {
    d.addEventListener("click", (e) => { if (!e.target.classList.contains("en")) select(d.dataset.id, true); });
    d.querySelector(".en").addEventListener("change", (e) => {
      const st = byId(d.dataset.id); st.enabled = e.target.checked;
      drawStation(st); renderList();
      if (isAdmin) setDirty();
      composite();                                   // instant, from cached station images
      if (st.enabled && !runStations[st.id]) compute(true);  // only stations never computed
    });
  });
}

const byId = (id) => project.stations.find((s) => s.id === id);

function select(id, pan = false) {
  selected = id;
  const st = byId(id);
  if (pan && st) map.panTo([st.lat, st.lon]);
  activateTab("stations"); renderList(); renderEditor();
  if (narrow()) openSide();
}

function sensitivity(st) {
  if (st.sens_override_dbm !== null && st.sens_override_dbm !== "" && st.sens_override_dbm !== undefined) return +st.sens_override_dbm;
  const snr = st.mode === "lora" ? (LORA_SNR[st.sf] ?? -17.5) : +(st.req_snr_db ?? 10);
  return -174 + 10 * Math.log10(st.bw_khz * 1000) + (+st.nf_db || 6) + snr;
}
function autoVbw(a) {
  const g = +a.gain_dbi || 0;
  if (a.type === "omni") return Math.max(3, Math.min(120, 78 / 10 ** ((g - 2.15) / 10)));
  return Math.max(2, Math.min(120, 32400 / ((+a.hbw_deg || 60) * 10 ** (g / 10))));
}

function opt(obj, cur) { return Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v.label)}</option>`).join(""); }

function renderEditor() {
  const el = $("editor");
  const st = selected && byId(selected);
  if (!st) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  const a = st.antenna || (st.antenna = {});
  const sens = sensitivity(st);
  const eirp = st.tx_power_dbm - (st.cable_loss_db || 0) + (+a.gain_dbi || 0);
  const mw = 10 ** (st.tx_power_dbm / 10);
  el.innerHTML = `
    <div class="ehead">
      <input type="color" data-k="color" value="${esc(st.color)}">
      <input type="text" data-k="name" value="${esc(st.name)}">
      <button id="btnDel" class="danger" title="Station löschen">🗑</button>
    </div>
    <h4>Standort</h4>
    <div class="grid2">
      <label>Breite<input type="number" step="0.000001" data-k="lat" value="${st.lat}"></label>
      <label>Länge<input type="number" step="0.000001" data-k="lon" value="${st.lon}"></label>
      <label>Antennenhöhe (m)<input type="number" step="0.5" data-k="height_m" value="${st.height_m}"></label>
      <label>Höhe bezogen auf<select data-k="height_ref">
        <option value="ground" ${st.height_ref !== "surface" ? "selected" : ""}>Gelände (Boden)</option>
        <option value="surface" ${st.height_ref === "surface" ? "selected" : ""}>Oberfläche (Dach)</option></select></label>
    </div>
    <div class="calc" id="elevInfo">Geländehöhe: <button id="btnElev">abfragen</button></div>
    <h4>Funk</h4>
    <label>Vorlage<select data-k="radio_preset">${opt(RADIO_PRESETS, st.radio_preset || "custom")}</select></label>
    <div class="grid2">
      <label>Frequenz (MHz)<input type="number" step="0.001" data-k="freq_mhz" value="${st.freq_mhz}"></label>
      <label>Bandbreite (kHz)<input type="number" step="0.1" data-k="bw_khz" value="${st.bw_khz}"></label>
      <label>Sendeleistung (dBm)<input type="number" step="0.5" data-k="tx_power_dbm" value="${st.tx_power_dbm}"></label>
      <label>Kabel/Stecker (dB)<input type="number" step="0.1" data-k="cable_loss_db" value="${st.cable_loss_db ?? 0}"></label>
      <label>Modulation<select data-k="mode">
        <option value="lora" ${st.mode === "lora" ? "selected" : ""}>LoRa</option>
        <option value="fm" ${st.mode === "fm" ? "selected" : ""}>FM</option>
        <option value="custom" ${st.mode === "custom" ? "selected" : ""}>Sonstige (SNR)</option></select></label>
      ${st.mode === "lora"
        ? `<label>Spreading Factor<select data-k="sf">${[5, 6, 7, 8, 9, 10, 11, 12].map((s) => `<option ${+st.sf === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>`
        : `<label>nötiges SNR (dB)<input type="number" step="0.5" data-k="req_snr_db" value="${st.req_snr_db ?? 10}"></label>`}
      <label>Rauschzahl Rx (dB)<input type="number" step="0.5" data-k="nf_db" value="${st.nf_db ?? 6}"></label>
      <label>Empfindlichkeit fix (dBm)<input type="number" step="0.5" data-k="sens_override_dbm" value="${st.sens_override_dbm ?? ""}" placeholder="auto"></label>
    </div>
    <div class="calc">${fmt(mw >= 1000 ? mw / 1000 : mw, mw >= 1000 ? 2 : 0)} ${mw >= 1000 ? "W" : "mW"} · EIRP ${fmt(eirp)} dBm (${fmt(10 ** (eirp / 10) / 1000 / 1.64, 2)} W ERP) · Empfindlichkeit ${fmt(sens)} dBm</div>
    <h4>Antenne</h4>
    <label>Vorlage<select data-k="antenna.preset">${opt(ANT_PRESETS, a.preset || "custom")}</select></label>
    <div class="grid2">
      <label>Typ<select data-k="antenna.type">
        <option value="omni" ${a.type === "omni" ? "selected" : ""}>Rundstrahler</option>
        <option value="directional" ${a.type === "directional" ? "selected" : ""}>Richtantenne</option></select></label>
      <label>Gewinn (dBi)<input type="number" step="0.1" data-k="antenna.gain_dbi" value="${a.gain_dbi ?? 0}"></label>
      ${a.type === "directional" ? `
      <label>Azimut (° von Nord)<input type="number" step="1" data-k="antenna.azimuth_deg" value="${a.azimuth_deg ?? 0}"></label>
      <label>Öffnung horiz. (°)<input type="number" step="1" data-k="antenna.hbw_deg" value="${a.hbw_deg ?? 60}"></label>` : ""}
      <label>Öffnung vert. (°)<input type="number" step="0.5" data-k="antenna.vbw_deg" value="${a.vbw_deg ?? ""}" placeholder="auto ${fmt(autoVbw(a), 0)}"></label>
      <label>Neigung, + = abwärts (°)<input type="number" step="0.5" data-k="antenna.tilt_deg" value="${a.tilt_deg ?? 0}"></label>
      <label>Vor/Rück (dB)<input type="number" step="1" data-k="antenna.fb_db" value="${a.fb_db ?? 25}"></label>
    </div>
    ${st.corescope_pk ? `<h4>MeshCore</h4><div class="calc">Verknüpft mit <b>${esc(csNode(st.corescope_pk)?.name || st.corescope_pk.slice(0, 8) + "…")}</b>
      <div class="row tight"><button id="btnCsLinks">Nachbarn zeigen</button><button id="btnCal">Mit Messwerten vergleichen</button></div>
      ${isAdmin ? `<button id="btnCsUnlink" class="danger" style="border:0;padding:0">Verknüpfung lösen</button>` : ""}</div>`
    : cfg.corescope?.enabled && isAdmin ? `<h4>MeshCore</h4><label>Mit Repeater verknüpfen (Umkreis 3 km)<select id="csLinkSel">
        <option value="">– nicht verknüpft –</option>${csNearby(st).map(({ n, d }) => `<option value="${n.pk}">${esc(n.name)} (${fmt(d * 1000, 0)} m)</option>`).join("")}</select></label>` : ""}
    ${project.stations.length > 1 ? `<h4>Strecke zu anderer Station</h4>
    <div class="row tight"><select id="ptpSel">${project.stations.filter((x) => x.id !== st.id).map((x) => `<option value="${x.id}">${esc(x.name)} (${fmt(distKm(st.lat, st.lon, x.lat, x.lon), 1)} km)</option>`).join("")}</select>
      <button id="btnPtp" title="Profil und Linkbudget mit beiden Antennen (Höhe, Gewinn in Richtung, Kabel)">Strecke zeigen</button></div>
    ${isAdmin ? `<button id="btnAim" title="Richtantennen beider Stationen auf die jeweils andere drehen und neigen">Richtantennen aufeinander ausrichten</button>` : ""}` : ""}
    <h4>Berechnung</h4>
    <div class="grid2">
      <label>Radius (km)<input type="number" step="0.5" min="0.5" max="80" data-k="radius_km" value="${st.radius_km}"></label>
    </div>
    <label>Notizen<textarea data-k="notes" rows="2" style="width:100%">${esc(st.notes || "")}</textarea></label>`;

  if ($("btnPtp")) $("btnPtp").onclick = () => showProfile(st.id, null, { rxStationId: $("ptpSel").value });
  if ($("btnAim")) $("btnAim").onclick = () => aimPair(st, byId($("ptpSel").value));
  if ($("csLinkSel")) $("csLinkSel").onchange = (e) => {
    if (!e.target.value) return;
    if (csStationOf(e.target.value)) { status("Dieser Repeater ist schon mit einer anderen Station verknüpft.", "err"); e.target.value = ""; return; }
    st.corescope_pk = e.target.value; changed(); csDrawNodes(); renderEditor();
  };
  if ($("btnCsUnlink")) $("btnCsUnlink").onclick = () => {
    if (csSelPk === st.corescope_pk) csDeselect();
    delete st.corescope_pk; changed(); csDrawNodes(); renderEditor();
  };
  if (st.corescope_pk && $("btnCal")) {
    $("btnCal").onclick = () => csCalibrate(st);
    $("btnCsLinks").onclick = () => { if (csSelPk !== st.corescope_pk) csSelect(st.corescope_pk, { fromStation: true }); };
    if (!cfg.corescope?.enabled) { $("btnCal").disabled = true; $("btnCal").title = "RFP_CORESCOPE_URL nicht gesetzt"; }
  }
  el.querySelectorAll("[data-k]").forEach((inp) => {
    if (!isAdmin) { inp.disabled = true; return; }
    inp.addEventListener("change", () => onField(st, inp));
  });
  if (!isAdmin) { $("btnDel").remove(); $("elevInfo").remove(); return; }
  $("btnDel").onclick = () => {
    if (!confirm(`Station „${st.name}“ löschen?`)) return;
    project.stations = project.stations.filter((s) => s.id !== st.id);
    if (markers[st.id]) { map.removeLayer(markers[st.id]); delete markers[st.id]; }
    if (azLines[st.id]) { map.removeLayer(azLines[st.id]); delete azLines[st.id]; }
    selected = null; renderAll(); changed();
  };
  $("btnElev").onclick = async () => {
    $("elevInfo").textContent = "Frage Höhendaten ab …";
    try {
      const r = await api("/api/elevation", { method: "POST", body: { project, lat: st.lat, lon: st.lon } });
      const base = st.height_ref === "surface" ? r.surface_m : r.ground_m;
      $("elevInfo").innerHTML = `Gelände ${fmt(r.ground_m)} m · Oberfläche ${fmt(r.surface_m)} m (Δ ${fmt(r.surface_m - r.ground_m)} m) → Antenne auf <b>${fmt(base + +st.height_m)} m</b> ü. NHN`;
    } catch (e) { $("elevInfo").textContent = "Fehler: " + e.message; }
  };
}

/** Richtantennen zweier Stationen aufeinander ausrichten (Azimut, Neigung aus dem Profil). */
async function aimPair(a, b) {
  if (!a || !b) return;
  const da = a.antenna || {}, db = b.antenna || {};
  if (da.type !== "directional" && db.type !== "directional") { status("Keine der beiden Stationen hat eine Richtantenne.", "err"); return; }
  let elev = 0;
  try {
    const p = await api("/api/profile", { method: "POST", body: { project, station_id: a.id, rx_station_id: b.id } });
    elev = p.elevation_deg;
  } catch { /* ohne Höhendaten: nur Azimut */ }
  if (da.type === "directional") { da.azimuth_deg = Math.round(bearing(a.lat, a.lon, b.lat, b.lon)); da.tilt_deg = +(-elev).toFixed(1); da.preset = da.preset || "custom"; }
  if (db.type === "directional") { db.azimuth_deg = Math.round(bearing(b.lat, b.lon, a.lat, a.lon)); db.tilt_deg = +(elev).toFixed(1); db.preset = db.preset || "custom"; }
  drawStation(a); drawStation(b); renderEditor(); changed();
  status(`Ausgerichtet: ${a.name} ${fmt(da.azimuth_deg ?? 0, 0)}° · ${b.name} ${fmt(db.azimuth_deg ?? 0, 0)}° (Neigung ±${fmt(Math.abs(elev), 1)}°).`);
  showProfile(a.id, null, { rxStationId: b.id });
}

function onField(st, inp) {
  const path = inp.dataset.k.split(".");
  let v = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value;
  if (path[0] === "sf") v = +v;
  if (path.length === 2) {
    st[path[0]] = st[path[0]] || {};
    st[path[0]][path[1]] = v;
    if (path[1] === "preset" && ANT_PRESETS[v] && v !== "custom") Object.assign(st.antenna, stripLabel(ANT_PRESETS[v]));
    else if (path[1] !== "preset" && path[1] !== "azimuth_deg" && path[1] !== "tilt_deg") st.antenna.preset = "custom";
  } else {
    st[path[0]] = v;
    if (path[0] === "radio_preset" && RADIO_PRESETS[v] && !["custom", "lora_custom"].includes(v)) Object.assign(st, stripLabel(RADIO_PRESETS[v]));
    if (path[0] === "radio_preset" && v === "lora_custom") st.mode = "lora";
    if (["freq_mhz", "bw_khz", "mode", "sf", "tx_power_dbm", "nf_db", "req_snr_db"].includes(path[0])) st.radio_preset = "custom";
  }
  changed(st); drawStation(st); renderList(); renderEditor();
}

let autoTimer = null, computing = false, pending = false, retries = 0;
function changed() { if (!isAdmin) return; setDirty(); scheduleAuto(); }
function scheduleAuto() {
  if (!$("autoCalc").checked) { if (lastRun) status("Geändert – „▶ Berechnen“ aktualisiert die Karte."); return; }
  clearTimeout(autoTimer);
  autoTimer = setTimeout(() => compute(true), 900);
}

// ------------------------------------------------------------ settings
function renderSettings() {
  const s = project.settings;
  const srcOpts = (kind, cur, allowNone) => (allowNone ? `<option value="none" ${cur === "none" ? "selected" : ""}>keine (nur Gelände)</option>` : "") +
    cfg.sources.filter((x) => kind === "ground" ? x.kind !== "surface" : x.kind !== "ground")
      .map((x) => `<option value="${esc(x.id)}" ${x.id === cur ? "selected" : ""}>${esc(x.label)}</option>`).join("");
  $("s_ground_source").innerHTML = srcOpts("ground", s.ground_source, false);
  $("s_surface_source").innerHTML = srcOpts("surface", s.surface_source, true);
  $("s_diffraction_model").value = s.diffraction_model || "bullington";
  $("s_clutter_mode").value = s.clutter_mode || "auto";
  $("s_clutter_floor").value = s.clutter_floor || "none";
  $("s_two_ray").checked = s.two_ray !== false;
  $("s_veg_mode").value = s.veg_mode || "auto";
  $("s_veg_gamma_db_m").value = s.veg_gamma_db_m ?? "";
  $("s_veg_max_db").value = s.veg_max_db ?? "";
  $("reqSlider").value = s.min_margin_db ?? 10; $("reqVal").textContent = s.min_margin_db ?? 10;
  for (const k of ["res_m", "k_factor", "max_profile_points", "max_rays", "rx_height_m", "rx_gain_dbi", "rx_cable_db", "min_margin_db", "rx_extra_loss_db", "noise_rise_db", "clutter_exclude_m"]) $("s_" + k).value = s[k] ?? cfg.defaults[k];
}
function bindSettings() {
  for (const k of ["ground_source", "surface_source", "diffraction_model", "clutter_mode", "clutter_floor", "two_ray", "veg_mode", "veg_gamma_db_m", "veg_max_db", ...["res_m", "k_factor", "max_profile_points", "max_rays", "rx_height_m", "rx_gain_dbi", "rx_cable_db", "min_margin_db", "rx_extra_loss_db", "noise_rise_db", "clutter_exclude_m"]]) {
    $("s_" + k).addEventListener("change", (e) => {
      const el = e.target;
      project.settings[k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : (el.value === "" ? null : +el.value);
      if (k === "min_margin_db") { $("reqSlider").value = el.value; $("reqVal").textContent = el.value; showLayer(); setDirty(); return; }
      changed();
    });
  }
  bindRegions();
  const test = async (which) => {
    const sid = project.settings[which];
    const c = map.getCenter();
    $("testOut").textContent = `Teste ${sid} bei ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)} …`;
    const r = await api(`/api/terrain/test?source=${encodeURIComponent(sid)}&lat=${c.lat}&lon=${c.lng}&res=${project.settings.res_m}`);
    $("testOut").textContent = JSON.stringify(r, null, 1);
  };
  $("btnTestGround").onclick = () => test("ground_source").catch((e) => ($("testOut").textContent = e.message));
  $("btnTestSurface").onclick = () => test("surface_source").catch((e) => ($("testOut").textContent = e.message));
}

// ------------------------------------------------------------ compute + overlay (client-side compositing)
// Each station is computed once on the server (cached) and delivered as a small data image
// (R = level dBm+200, G = LOS class | roof visibility << 2). The browser stacks the enabled
// stations itself, so ticking stations, switching views and the reserve slider need no server.
const stationData = {};   // "key@factor" -> {px: Uint8ClampedArray, w, h}
let runStations = {};     // station id -> entry from /api/coverage
let covLayer = null;

const CanvasOverlay = L.ImageOverlay.extend({
  initialize(canvas, bounds, options) { this._canvas = canvas; L.ImageOverlay.prototype.initialize.call(this, "", bounds, options); },
  _initImage() {
    const c = this._image = this._canvas;
    L.DomUtil.addClass(c, "leaflet-image-layer");
    if (this._zoomAnimated) L.DomUtil.addClass(c, "leaflet-zoom-animated");
    if (this.options.className) L.DomUtil.addClass(c, this.options.className);
    c.onselectstart = L.Util.falseFn; c.onmousemove = L.Util.falseFn;
    this._updateOpacity();
  },
});

function clearOverlay() { if (covLayer) { map.removeLayer(covLayer); covLayer = null; } $("layerbox").classList.add("hidden"); }

function computeBody(ids) {
  const b = isAdmin ? { project, station_ids: ids } : { project_id: project.id, station_ids: ids };
  if (narrow()) b.max_px = 1600;  // Handy: gröberes Bild, weniger Speicher beim Zusammensetzen
  return b;
}

async function loadStationData(e) {
  const k = `${e.key}@${lastRun.factor}`;
  if (stationData[k]) return stationData[k];
  const img = new Image();
  img.src = e.png;
  await img.decode();
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const d = { px: g.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height };
  stationData[k] = d;
  return d;
}

async function compute(auto = false) {
  const act = project.stations.filter((s) => s.enabled !== false);
  if (!act.length) { clearOverlay(); status("Keine aktive Station."); return; }
  if (!isAdmin && !project.id) return;
  if (computing) { pending = true; return; }
  computing = true;
  $("btnCompute").disabled = true;
  const known = act.filter((s) => runStations[s.id]).length;
  status(known === act.length ? `Aktualisiere ${act.length} Station(en) …`
    : `Berechne ${act.length - known} neue/geänderte Station(en), übrige aus dem Cache … (beim ersten Mal werden Höhendaten geladen)`, "busy");
  const t0 = performance.now();
  try {
    const r = await api("/api/coverage", { method: "POST", body: computeBody(act.map((s) => s.id)) });
    if (lastRun && lastRun.factor !== r.factor) runStations = {};
    lastRun = r;
    for (const e of r.stations) runStations[e.id] = e;
    await Promise.all(r.stations.map(loadStationData));
    composite();
    renderResults(r);
    const nNew = r.stations.filter((e) => !e.cached).length;
    status(`Fertig in ${((performance.now() - t0) / 1000).toFixed(1)} s – ${nNew} berechnet, ${r.stations.length - nNew} aus dem Cache.` + (r.factor > 1 ? ` (Anzeige ${fmt(r.cell_m * Math.cos(map.getCenter().lat * Math.PI / 180), 0)} m Raster)` : ""));
    retries = 0;
  } catch (e) {
    if ([502, 503, 504].includes(e.status) && retries < 30) {
      // Zeitlimit eines Reverse-Proxys: der Server rechnet weiter und legt das Ergebnis in den Cache
      retries++;
      status(`Die Berechnung läuft auf dem Server noch … neuer Versuch in 10 s (${retries})`, "busy");
      setTimeout(() => compute(true), 10000);
    } else {
      retries = 0;
      status("Fehler: " + e.message, "err");
    }
  } finally {
    $("btnCompute").disabled = false; computing = false;
    if (pending) { pending = false; compute(true); }
  }
}

function activeEntries() {
  if (!lastRun) return [];
  return project.stations.filter((s) => s.enabled !== false && runStations[s.id])
    .map((s) => ({ st: s, e: runStations[s.id], d: stationData[`${runStations[s.id].key}@${lastRun.factor}`] }))
    .filter((x) => x.d);
}

function hexRgb(h) { h = (h || "#3388ff").replace("#", ""); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; }

function composite() {
  const list = activeEntries();
  if (!list.length) { clearOverlay(); return; }
  const layer = $("layerSel").value || "margin";
  const req = +project.settings.min_margin_db || 0;
  let gx0 = Infinity, gy0 = Infinity, gx1 = -Infinity, gy1 = -Infinity;
  for (const { e } of list) { gx0 = Math.min(gx0, e.gx0); gy0 = Math.min(gy0, e.gy0); gx1 = Math.max(gx1, e.gx0 + e.w); gy1 = Math.max(gy1, e.gy0 + e.h); }
  const W = gx1 - gx0, H = gy1 - gy0, N = W * H;
  const best = new Float32Array(N).fill(-1e9);
  const idx = new Int16Array(N).fill(-1);
  const cnt = new Uint8Array(N);
  const los = new Uint8Array(N);
  list.forEach(({ e, d }, k) => {
    const sens = e.sensitivity_dbm, ox = e.gx0 - gx0, oy = e.gy0 - gy0, px = d.px;
    for (let y = 0; y < d.h; y++) {
      let si = y * d.w * 4, di = (y + oy) * W + ox;
      for (let x = 0; x < d.w; x++, si += 4, di++) {
        const R = px[si];
        if (!R) continue;
        const m = R - 200 - sens;
        if (m > best[di]) { best[di] = m; idx[di] = k; }
        if (m >= req) cnt[di]++;
        const G = px[si + 1], cls = G & 3, vis = G >> 2;
        const v = cls === 2 ? 3 : cls === 1 ? 2 : vis ? 1 : 0;
        if (v > los[di]) los[di] = v;
      }
    }
  });
  const lows = req > 0 ? [0, req, req + 6, req + 12, req + 20] : [0, 6, 12, 20, 30];
  const mcol = lastRun.legend.margin.map((x) => hexRgb(x.color));
  const ocol = lastRun.legend.overlap.map((x) => hexRgb(x.color));
  const scol = list.map(({ st }) => hexRgb(st.color));
  const lcol = [null, [127, 179, 255], [255, 210, 0], [0, 170, 90]];
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const g = canvas.getContext("2d");
  const out = g.createImageData(W, H), o = out.data;
  for (let i = 0, j = 0; i < N; i++, j += 4) {
    let c = null;
    if (layer === "margin") {
      const m = best[i];
      if (m >= lows[0]) { let b = 0; while (b + 1 < lows.length && m >= lows[b + 1]) b++; c = mcol[b]; }
    } else if (layer === "overlap") {
      if (cnt[i]) c = ocol[Math.min(cnt[i], ocol.length) - 1];
    } else if (layer === "server") {
      if (idx[i] >= 0 && best[i] >= req) c = scol[idx[i]];
    } else if (layer === "los") {
      if (los[i]) c = lcol[los[i]];
    }
    if (c) { o[j] = c[0]; o[j + 1] = c[1]; o[j + 2] = c[2]; o[j + 3] = 255; }
  }
  g.putImageData(out, 0, 0);
  // grid -> lat/lon bounds (web mercator)
  const cm = lastRun.cell_m, R = 6378137;
  const toLat = (y) => (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI;
  const toLon = (x) => x / R * 180 / Math.PI;
  const bounds = [[toLat(-gy1 * cm), toLon(gx0 * cm)], [toLat(-gy0 * cm), toLon(gx1 * cm)]];
  if (covLayer) map.removeLayer(covLayer);
  covLayer = new CanvasOverlay(canvas, bounds, { opacity: +$("opacity").value, interactive: false, className: "cov" }).addTo(map);
  $("layerbox").classList.remove("hidden");
  renderLegend(layer);
}

function showLayer() { composite(); }

function renderLegend(layer) {
  const lg = lastRun.legend;
  let h = "";
  if (layer === "margin") {
    const req = +project.settings.min_margin_db || 0;
    const lows = req > 0 ? [0, req, req + 6, req + 12, req + 20] : [0, 6, 12, 20, 30];
    const b = lg.margin.map((x, i) => ({ color: x.color, from: lows[i] }));
    h = b.map((x, i) => `<div class="li"><span class="sw" style="background:${x.color}"></span>${i + 1 < b.length ? `${x.from} … ${b[i + 1].from}` : `≥ ${x.from}`} dB${req > 0 && i === 0 ? " – unter Reserve, unsicher" : ""}</div>`).join("") +
      `<div class="note">Pegel über Empfindlichkeit. Versorgt ab ${req} dB (Fading-/Standortreserve).</div>`;
  } else if (layer === "overlap") {
    h = lg.overlap.map((x, i) => `<div class="li"><span class="sw" style="background:${x.color}"></span>${x.n}${i === lg.overlap.length - 1 ? "+" : ""} Station(en)</div>`).join("") +
      `<div class="note">zählt Stationen mit ≥ ${+project.settings.min_margin_db || 0} dB Reserve</div>`;
  } else if (layer === "server") {
    h = project.stations.filter((s) => s.enabled !== false).map((s) => `<div class="li"><span class="sw" style="background:${esc(s.color)}"></span>${esc(s.name)}</div>`).join("");
  } else if (layer === "los") {
    h = lg.los.map((x) => `<div class="li"><span class="sw" style="background:${x.color}"></span>${esc(x.label)}</div>`).join("") +
      `<div class="note">Empfänger in ${fmt(project.settings.rx_height_m)} m Höhe</div>`;
  }
  $("legend").innerHTML = h;
}

function renderResults(r) {
  $("resultInfo").innerHTML = `<p>Gesamt ${fmt(r.t_total_s)} s. Klick in die Karte zeigt Pegel und Geländeprofil.</p>` +
    r.stations.map((s) => {
      const st = byId(s.id) || {};
      return `<div class="res-st" style="border-color:${esc(st.color)}">
        <b>${esc(s.name)}</b><br>
        Versorgt: <b>${fmt(s.area_km2, 1)} km²</b> · Sichtlinie: ${fmt(s.los_area_km2, 1)} km²<br>
        Empfindlichkeit ${fmt(s.sensitivity_dbm)} dBm · Antenne ${fmt(s.antenna_alt_m)} m ü. NHN (Gelände ${fmt(s.ground_m)} / Oberfl. ${fmt(s.surface_m)})<br>
        <span class="note">${s.rays} Strahlen × ${s.bins} Punkte, Schritt ${fmt(s.step_m)} m, Raster ${fmt(s.res_m)} m · ${s.cached ? "aus Cache" : `${fmt(s.t_total_s)} s (Höhendaten ${fmt(s.t_terrain_s)} s, Rechnung ${fmt(s.t_kernel_s)} s)`}</span>
        ${(s.warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}
      </div>`;
    }).join("");
}

// ------------------------------------------------------------ inspect + profile
function sampleStation(e, d, lat, lon) {
  const R = 6378137, cm = lastRun.cell_m;
  const x = lon * Math.PI / 180 * R, y = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) * R;
  const col = Math.floor(x / cm) - e.gx0, row = Math.floor(-y / cm) - e.gy0;
  if (col < 0 || row < 0 || col >= d.w || row >= d.h) return null;
  const i = (row * d.w + col) * 4;
  if (!d.px[i]) return null;
  const prx = d.px[i] - 200, G = d.px[i + 1];
  return { prx, margin: prx - e.sensitivity_dbm, los: G & 3, roof: !!(G >> 2) };
}
function distKm(a, b, c, d) {
  const r = Math.PI / 180, x = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}

function inspectAt(latlng) {
  const req = project.settings.min_margin_db || 0;
  const rows = project.stations.filter((s) => s.enabled !== false).map((s) => {
    const e = runStations[s.id], d = e && lastRun && stationData[`${e.key}@${lastRun.factor}`];
    const v = d ? sampleStation(e, d, latlng.lat, latlng.lng) : null;
    return `<tr><td><span style="color:${esc(s.color)}">●</span> ${esc(s.name)}</td>
      <td>${fmt(distKm(s.lat, s.lon, latlng.lat, latlng.lng), 2)} km</td>
      <td>${v ? `${fmt(v.prx, 0)} dBm` : (d ? "außerhalb" : "–")}</td>
      <td class="${v && v.margin >= req ? "ok" : "no"}">${v ? (v.margin >= 0 ? "+" : "") + fmt(v.margin, 0) + " dB" : ""}</td>
      <td title="Sicht Empfänger / Dach">${v ? ["–", "LOS", "LOS+F"][v.los] + (v.roof ? " · Dach✓" : "") : ""}</td>
      <td><button data-prof="${esc(s.id)}">Profil</button></td></tr>`;
  }).join("");
  const div = document.createElement("div");
  div.className = "popup";
  div.innerHTML = `<div class="note">${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}</div><table>${rows || "<tr><td>Keine aktive Station</td></tr>"}</table>`;
  div.querySelectorAll("[data-prof]").forEach((b) => b.addEventListener("click", () => showProfile(b.dataset.prof, latlng)));
  L.popup({ maxWidth: 380 }).setLatLng(latlng).setContent(div).openOn(map);
}

let profLine = null, profAt = null;
/** Profil von Station sid zu einem Punkt oder (opts.rxStationId) zu einer anderen Station.
 *  opts.rx: feste Empfängerwerte {h, g, c, label} (z. B. Repeater aus dem Abgleich). */
async function showProfile(sid, latlng, opts = {}) {
  const st = byId(sid);
  const rxSt = opts.rxStationId ? byId(opts.rxStationId) : null;
  if (rxSt) latlng = L.latLng(rxSt.lat, rxSt.lon);
  if (opts.rx) { $("profRxH").value = opts.rx.h; $("profRxG").value = opts.rx.g; }
  profAt = { sid, latlng, opts };
  closeSide(); map.closePopup();
  document.body.classList.add("profile-open");
  if ($("profRxH").value === "") $("profRxH").value = project.settings.rx_height_m;
  if ($("profRxG").value === "") $("profRxG").value = project.settings.rx_gain_dbi;
  $("profile").classList.remove("hidden");
  $("profRxBox").classList.toggle("hidden", !!rxSt);
  $("profTitle").textContent = rxSt ? `Strecke ${st.name} → ${rxSt.name}` : opts.rx?.label ? `Strecke ${st.name} → ${opts.rx.label}` : `Profil ${st.name} → Punkt`;
  $("profMeta").textContent = "rechne …";
  if (profLine) map.removeLayer(profLine);
  profLine = L.polyline([[st.lat, st.lon], [latlng.lat, latlng.lng]], { color: st.color, dashArray: "6 4", weight: 2 }).addTo(map);
  try {
    const p = await api("/api/profile", { method: "POST", body: { ...(isAdmin ? { project } : { project_id: project.id }), station_id: sid, lat: latlng.lat, lon: latlng.lng,
      ...(rxSt ? { rx_station_id: rxSt.id } : { rx_height_m: +$("profRxH").value, rx_gain_dbi: +$("profRxG").value, rx_cable_db: opts.rx?.c }) } });
    $("profMeta").textContent = `${fmt(p.distance_m / 1000, 2)} km · Azimut ${fmt(p.azimuth_deg, 0)}° · Elevation ${fmt(p.elevation_deg, 2)}° · ` +
      ["keine Sichtlinie", "Sichtlinie, Fresnelzone verletzt", "Fresnelzone ≥ 60 % frei"][p.los_class] +
      (p.worst_at_m != null ? ` · engste Stelle bei ${fmt(p.worst_at_m / 1000, 2)} km (Freiraum ${fmt(p.worst_clearance_ratio * 100, 0)} % F1)` : "");
    drawProfile(p);
    const b = p.budget;
    const hv = p.hata;
    $("profBudget").innerHTML = [
      ["Sendeleistung", `${fmt(b.tx_power_dbm)} dBm`], ["Kabel Tx", `−${fmt(b.tx_cable_db)} dB`],
      ["Antenne Tx (Richtung)", `${fmt(b.tx_gain_dbi)} dBi`], ["Freiraumdämpfung", `−${fmt(b.fspl_db)} dB`],
      [`Beugung (${project.settings.diffraction_model === "deygout" ? "Deygout" : "Bullington"})`, `−${fmt(b.diffraction_db)} dB`],
      ["Bodenreflexion", `−${fmt(b.reflection_db)} dB`],
      [`Hindernisse durchquert (${fmt(b.obstacle_len_m, 0)} m)`, `−${fmt(b.obstacle_db)} dB`],
      [`Clutter Rx (R ${fmt(b.clutter_height_m, 0)} m)`, `−${fmt(b.clutter_db)} dB`],
      [b.rx_station ? `Antenne Rx ${esc(b.rx_station)} (Richtung, ${fmt(b.rx_height_m, 1)} m)` : "Antenne Rx", `${fmt(b.rx_gain_dbi)} dBi`],
      ["Kabel + Zusatz Rx", `−${fmt(b.rx_cable_db + b.rx_extra_db)} dB`],
    ].map(([a, v]) => `<tr><td>${a}</td><td>${v}</td></tr>`).join("") +
      `<tr class="sum"><td>Gesamtdämpfung Pfad</td><td>${fmt(b.total_loss_db)} dB</td></tr>
       <tr><td title="Okumura-Hata, nur zum Vergleich${hv.valid ? "" : " – außerhalb des Gültigkeitsbereichs"}">Vergleich Hata Stadt/Vorst.${hv.valid ? "" : " ⚠"}</td><td>${fmt(hv.loss_db.urban)} / ${fmt(hv.loss_db.suburban)} dB</td></tr>` +
      `<tr class="sum"><td>Empfangspegel</td><td>${fmt(b.prx_dbm)} dBm</td></tr>
       <tr><td>Empfindlichkeit</td><td>${fmt(b.sensitivity_dbm)} dBm</td></tr>
       <tr class="sum"><td>Reserve (nötig ${fmt(b.required_margin_db, 0)})</td><td style="color:${b.margin_db >= b.required_margin_db ? "var(--go)" : b.margin_db >= 0 ? "#b45309" : "var(--bad)"}">${b.margin_db >= 0 ? "+" : ""}${fmt(b.margin_db)} dB</td></tr>`;
  } catch (e) { $("profMeta").textContent = "Fehler: " + e.message; }
}

let lastProfile = null;
function drawProfile(p) {
  lastProfile = p;
  const cv = $("profCanvas");
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, W, H);
  const padL = 44, padR = 10, padT = 10, padB = 22;
  const D = p.d[p.d.length - 1];
  const lowF = p.los.map((v, i) => v - p.fresnel[i]);
  const highF = p.los.map((v, i) => v + p.fresnel[i]);
  const ymin = Math.min(...p.ground) - 5;
  const ymax = Math.max(...p.surface, ...highF, p.tx_alt, p.rx_alt) + 5;
  const X = (d) => padL + (d / D) * (W - padL - padR);
  const Y = (z) => padT + (1 - (z - ymin) / (ymax - ymin)) * (H - padT - padB);
  // grid
  g.strokeStyle = "#e5e7eb"; g.fillStyle = "#6b7280"; g.font = "11px system-ui"; g.lineWidth = 1;
  const stepZ = niceStep((ymax - ymin) / 5);
  for (let z = Math.ceil(ymin / stepZ) * stepZ; z <= ymax; z += stepZ) {
    g.beginPath(); g.moveTo(padL, Y(z)); g.lineTo(W - padR, Y(z)); g.stroke(); g.fillText(`${z.toFixed(0)} m`, 2, Y(z) + 4);
  }
  const stepD = niceStep(D / 8);
  const dLabel = (d) => stepD < 100 ? `${fmt(d, 0)} m` : `${fmt(d / 1000, stepD < 1000 ? 1 : 0)} km`;
  for (let d = 0; d <= D; d += stepD) { g.fillText(dLabel(d), X(d) - 12, H - 6); }
  const poly = (arr, close) => { g.beginPath(); arr.forEach((z, i) => (i ? g.lineTo(X(p.d[i]), Y(z)) : g.moveTo(X(p.d[i]), Y(z)))); if (close) { g.lineTo(X(D), H - padB); g.lineTo(X(0), H - padB); g.closePath(); } };
  // Fresnel zone
  g.beginPath(); highF.forEach((z, i) => (i ? g.lineTo(X(p.d[i]), Y(z)) : g.moveTo(X(p.d[i]), Y(z))));
  for (let i = lowF.length - 1; i >= 0; i--) g.lineTo(X(p.d[i]), Y(lowF[i]));
  g.closePath(); g.fillStyle = "rgba(37,99,235,0.10)"; g.fill();
  // surface + ground
  poly(p.surface, true); g.fillStyle = "#86a873"; g.fill();
  poly(p.ground, true); g.fillStyle = "#b08a5e"; g.fill();
  // LOS
  g.strokeStyle = "#2563eb"; g.lineWidth = 1.5; poly(p.los, false); g.stroke();
  g.setLineDash([4, 3]); g.lineWidth = 1; poly(lowF, false); g.stroke(); g.setLineDash([]);
  // endpoints
  g.fillStyle = "#111"; g.beginPath(); g.arc(X(0), Y(p.tx_alt), 3.5, 0, 7); g.fill();
  g.beginPath(); g.arc(X(D), Y(p.rx_alt), 3.5, 0, 7); g.fill();
  if (p.worst_at_m != null) { g.strokeStyle = "#dc2626"; g.beginPath(); g.moveTo(X(p.worst_at_m), padT); g.lineTo(X(p.worst_at_m), H - padB); g.stroke(); }
}
function niceStep(x) { const p = 10 ** Math.floor(Math.log10(x)); const n = x / p; return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * p; }

// ------------------------------------------------------------ regions + cache
function regionBody() {
  const area = $("r_area").value;
  const src = $("r_source").value;
  const res = +$("r_res").value;
  let bbox = null, a = area;
  if (area === "view") {
    const b = map.getBounds(); bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  } else if (area === "stations") {
    if (!project.stations.length) throw new Error("Keine Stationen");
    let w = 180, s = 90, e = -180, n = -90;
    for (const st of project.stations) {
      const r = (st.radius_km || 10) * 1000 + 1000;
      const [n1] = destPoint(st.lat, st.lon, 0, r), [s1] = destPoint(st.lat, st.lon, 180, r);
      const [, e1] = destPoint(st.lat, st.lon, 90, r), [, w1] = destPoint(st.lat, st.lon, 270, r);
      w = Math.min(w, w1); e = Math.max(e, e1); s = Math.min(s, s1); n = Math.max(n, n1);
    }
    bbox = [w, s, e, n]; a = "view";
  }
  const name = $("r_name").value.trim() || `${src}_${res}m`;
  return { source: src, area: a, res_m: res, bbox, name };
}
function fmtBytes(b) { return b > 1e9 ? fmt(b / 1e9, 2) + " GB" : b > 1e6 ? fmt(b / 1e6, 1) + " MB" : fmt(b / 1e3, 0) + " kB"; }
let jobTimer = null;
async function pollJobs() {
  const jobs = await api("/api/regions/jobs");
  const label = { running: "läuft", done: "fertig", done_with_errors: "fertig mit Fehlern", cancelled: "abgebrochen", error: "Fehler" };
  $("jobs").innerHTML = jobs.map((j) => {
    const prog = j.done + j.skipped + j.empty + j.failed;
    const pct = j.total ? Math.round(100 * prog / j.total) : 0;
    const kind = j.source === "nrw_dom" ? "surface" : "ground";
    return `<div class="job"><b>${esc(j.name)}</b> · ${esc(j.source)} ${j.res_m} m · ${esc(label[j.state] || j.state)}
      <div class="bar"><div style="width:${pct}%"></div></div>
      ${prog}/${j.total} Kacheln (${j.skipped} vorhanden, ${j.empty} leer, ${j.failed} Fehler) · ${fmtBytes(j.bytes)} · ${fmt(j.elapsed_s / 60, 1)} min
      ${j.state === "running" ? `<button data-cancel="${j.id}">Abbrechen</button>` : ""}
      ${j.state.startsWith("done") && j.source !== "copernicus" ? `<button data-use="${esc(j.name)}" data-kind="${kind}">als ${kind === "surface" ? "Oberfläche" : "Gelände"} verwenden</button>` : ""}
      ${(j.errors || []).map((e) => `<div class="warn">${esc(e)}</div>`).join("")}${j.message ? `<div class="note">${esc(j.message)}</div>` : ""}</div>`;
  }).join("");
  $("jobs").querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = () => api(`/api/regions/cancel/${b.dataset.cancel}`, { method: "POST" }).then(pollJobs)));
  $("jobs").querySelectorAll("[data-use]").forEach((b) => (b.onclick = async () => {
    cfg = await api("/api/config");
  setupBasemaps(!!cfg.tile_proxy);
    project.settings[b.dataset.kind === "surface" ? "surface_source" : "ground_source"] = "local:" + b.dataset.use;
    renderSettings(); setDirty(); status(`Quelle auf „Lokal: ${b.dataset.use}“ gestellt.`);
  }));
  const pre = jobs.filter((j) => j.preload && j.state === "running");
  $("preloadInfo").textContent = pre.length ? "NRW-Höhendaten werden geladen: " + pre.map((j) => `${j.source} ${j.done + j.skipped + j.empty}/${j.total}`).join(", ") : "";
  clearTimeout(jobTimer);
  if (jobs.some((j) => j.state === "running")) jobTimer = setTimeout(() => pollJobs().catch(() => {}), 4000);
  else if (jobs.length) { cfg = await api("/api/config"); renderSettings(); }
}
function bindRegions() {
  $("btnPlan").onclick = async () => {
    try {
      const b = regionBody();
      const p = await api("/api/regions/plan", { method: "POST", body: b });
      $("planOut").textContent = `${p.n_tiles} Kacheln${p.tile_km ? ` à ${p.tile_km} km` : ""}, ca. ${fmt(p.est_mb, 0)} MB → Ordner data/dem/${b.name}. ${p.note || ""}`;
    } catch (e) { $("planOut").textContent = e.message; }
  };
  $("btnDownload").onclick = async () => {
    try {
      const b = regionBody();
      if (b.area === "nrw" && !confirm("Ganz NRW laden? Das kann je nach Auflösung Stunden dauern und mehrere GB belegen. Der Download lässt sich abbrechen und später fortsetzen.")) return;
      await api("/api/regions/start", { method: "POST", body: b });
      pollJobs();
    } catch (e) { $("planOut").textContent = e.message; }
  };
  $("btnCache").onclick = async () => {
    const c = await api("/api/cache");
    $("cacheOut").textContent = "Cache: " + Object.entries(c.cache).map(([k, v]) => `${k} ${fmtBytes(v)}`).join(", ") +
      "\nOffline-Ordner: " + (Object.entries(c.dem).map(([k, v]) => `${k} (${v.files} Dateien, ${fmtBytes(v.bytes)})`).join(", ") || "keine");
  };
  $("btnCacheClear").onclick = async () => {
    if (!confirm("Berechnungs- und Gitter-Cache leeren? Downloads und Offline-Ordner bleiben.")) return;
    await api("/api/cache/clear", { method: "POST", body: { parts: ["terrain", "coverage"] } });
    $("btnCache").click();
  };
  pollJobs().catch(() => {});
}

// ------------------------------------------------------------ MeshCore (CoreScope)
const csRenderer = L.canvas({ padding: 0.3 });
const csLayer = L.layerGroup();
let csNodes = [], csSelPk = null, csLinks = null, csTimer = null, csSelMarker = null, csOvr = {}, csLoading = null;

function ageDays(ts) { const t = Date.parse(ts || ""); return Number.isNaN(t) ? 1e9 : (Date.now() - t) / 864e5; }
function csAgeColor(d) { return d <= 1 ? "#16a34a" : d <= 7 ? "#d97706" : "#9ca3af"; }
function snrColor(s) { return s >= 5 ? "#16a34a" : s >= -5 ? "#65a30d" : s >= -10 ? "#d97706" : "#dc2626"; }
function fmtAgo(ts) {
  const d = ageDays(ts);
  if (d > 1e8) return "unbekannt";
  if (d < 1 / 24) return `vor ${fmt(d * 1440, 0)} min`;
  if (d < 1) return `vor ${fmt(d * 24, 0)} h`;
  return `vor ${fmt(d, 0)} Tagen`;
}
const csNode = (pk) => csNodes.find((x) => x.pk === pk);
const csStationOf = (pk) => (project?.stations || []).find((s) => s.corescope_pk === pk);

async function csRefreshStatus() {
  const s = await api("/api/corescope/status");
  $("csStatus").innerHTML = `Quelle: <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a><br>` +
    (s.fetched_at ? `${s.count} Knoten mit Standort · Stand ${esc(new Date(s.fetched_at).toLocaleString("de-DE"))}` : "Noch nicht synchronisiert.") +
    (s.syncing ? ` · <b>synchronisiere … ${esc(s.progress || "")}</b>` : "") +
    (s.error ? `<div class="warn">Letzter Sync fehlgeschlagen: ${esc(s.error)}</div>` : "");
  return s;
}

async function csLoadNodes() {
  const [r, o] = await Promise.all([api("/api/corescope/nodes"), api("/api/corescope/overrides").catch(() => ({}))]);
  csNodes = r.nodes || []; csOvr = o || {};
  csDrawNodes();
}

/** Nachbarn des gewählten Repeaters nach den Filtern im Tab. Dieselbe Auswahl gilt für Karte und Abgleich. */
function csVisibleLinks() {
  if (!csLinks) return [];
  const min = +$("csMin").value, bidir = $("csBidir").checked, keepMeas = $("csMeas").checked;
  return csLinks.links.filter((l) => {
    if (keepMeas && l.snr != null) return true;
    const obs = bidir ? Math.min(l.we_hear, l.they_hear) : Math.max(l.we_hear, l.they_hear, l.count || 0, l.snr_n || 0);
    return (!bidir || l.bidir) && obs >= min;
  });
}

function csSnrText(l) {
  if (l.snr != null) return `SNR ${fmt(l.snr, 1)} dB gemessen (${l.snr_n} Pakete)`;
  if (l.snr_any != null) return `Ø-SNR ${fmt(l.snr_any, 1)} dB (indirekt, über andere Observer)`;
  return "kein SNR";
}

function csSelIcon(loading, ring) {
  return L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 15],
    html: `<div class="cs-sel${ring ? " ring" : ""}${loading ? " loading" : ""}"></div>` });
}

function csDrawNodes() {
  csLayer.clearLayers();
  if (csSelMarker) { map.removeLayer(csSelMarker); csSelMarker = null; }
  if (csSelPk) {
    // Auswahlmodus: gewählter Repeater groß, nur seine Nachbarn, eingefärbt nach SNR, keine Linien
    const st = csStationOf(csSelPk);
    const n = csNode(csSelPk) || (csLinks && { lat: csLinks.lat, lon: csLinks.lon, name: csLinks.name });
    const pos = st ? [st.lat, st.lon] : n && n.lat != null ? [n.lat, n.lon] : null;
    if (pos) {
      csSelMarker = L.marker(pos, { icon: csSelIcon(csLoading === csSelPk, !!st), zIndexOffset: st ? -100 : 1000, interactive: !st,
        title: (st && st.name) || (n && n.name) || "" });
      if (!st) csSelMarker.on("click", (e) => { L.DomEvent.stopPropagation(e); csSelect(csSelPk); });
      csSelMarker.addTo(map);
    }
    for (const l of csVisibleLinks()) {
      if (csStationOf(l.pk)) continue;  // als Station eingetragen: deren Marker steht schon da
      const s = l.snr ?? l.snr_any, meas = l.snr != null;
      const m = L.circleMarker([l.lat, l.lon], { renderer: csRenderer, radius: meas ? 7 : 6, weight: meas ? 2.5 : 1.5,
        color: meas ? "#111827" : "#fff", dashArray: meas ? null : "3 2", fillColor: s != null ? snrColor(s) : "#9ca3af", fillOpacity: 0.95 });
      m.bindTooltip(`${esc(l.name || l.pk.slice(0, 8))} · ${fmt(l.distance_km, 1)} km<br>${csSnrText(l)}<br>hört uns ${l.they_hear}× · wir hören ${l.we_hear}×`, { direction: "top" });
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); csSelect(l.pk); });
      csLayer.addLayer(m);
    }
    csLayer.addTo(map);
    return;
  }
  if (!$("csShow").checked) { map.removeLayer(csLayer); return; }
  const maxAge = +$("csAge").value, role = $("csRole").value;
  for (const n of csNodes) {
    const d = ageDays(n.last_seen);
    if (maxAge && d > maxAge) continue;
    if (role && n.role !== role) continue;
    if (csStationOf(n.pk)) continue;
    const m = L.circleMarker([n.lat, n.lon], { renderer: csRenderer, radius: 5, weight: 1, color: "#fff", fillColor: csAgeColor(d), fillOpacity: 0.95 });
    m.bindTooltip(esc(n.name), { direction: "top" });
    m.on("click", (e) => { L.DomEvent.stopPropagation(e); csSelect(n.pk); });
    csLayer.addLayer(m);
  }
  csLayer.addTo(map);
}

/** Repeater wählen (lädt Nachbarn) bzw. abwählen, wenn er schon gewählt ist. */
async function csSelect(pk, { fromStation = false } = {}) {
  if (!cfg.corescope?.enabled) return;
  if (csSelPk === pk) { csDeselect(); return; }
  csSelPk = pk; csLinks = null; csLoading = pk;
  csDrawNodes(); csRenderSel();
  if (!fromStation) csPopup();
  status("MeshCore: lade Nachbarn …", "busy");
  try {
    const r = await api(`/api/corescope/links/${pk}`);
    if (csSelPk !== pk) return;
    csLinks = r;
    status(`MeshCore: ${csVisibleLinks().length} Nachbarn (nach Filter).`);
  } catch (e) {
    if (csSelPk === pk) status("MeshCore: " + e.message, "err");
  } finally {
    if (csLoading === pk) csLoading = null;
  }
  if (csSelPk !== pk) return;
  csDrawNodes(); csRenderSel(); if (!fromStation) csPopup();
}

function csDeselect() {
  csSelPk = null; csLinks = null; csLoading = null;
  map.closePopup(); csDrawNodes(); csRenderSel();
}

function csActionsHtml(pk) {
  const st = csStationOf(pk), o = csOvr[pk] || {};
  if (!isAdmin) return st ? `<button data-cs="open">Station „${esc(st.name)}“ öffnen</button>` : "";
  return `${st ? `<button data-cs="open">Station „${esc(st.name)}“ öffnen</button>` : `<button data-cs="adopt" class="primary">Als Station hinzufügen</button>`}
    ${st ? "" : `<div class="cs-ovr"><span>Für den Abgleich:</span>
      <label>Höhe m<input data-ovr="height_m" type="number" step="1" value="${o.height_m ?? ""}" placeholder="?"></label>
      <label>Gewinn dBi<input data-ovr="gain_dbi" type="number" step="0.5" value="${o.gain_dbi ?? ""}" placeholder="?"></label>
      <button data-cs="ovr">Speichern</button></div>`}`;
}

function csWireActions(root, pk) {
  root.querySelectorAll("[data-cs]").forEach((b) => b.onclick = async (e) => {
    L.DomEvent.stopPropagation(e);
    const n = csNode(pk) || {};
    if (b.dataset.cs === "open") { const st = csStationOf(pk); activateTab("stations"); select(st.id, true); }
    else if (b.dataset.cs === "adopt") csAdopt({ ...n, pk });
    else if (b.dataset.cs === "ovr") {
      const body = {};
      root.querySelectorAll("[data-ovr]").forEach((i) => { body[i.dataset.ovr] = i.value === "" ? null : +i.value; });
      try {
        const r = await api(`/api/corescope/overrides/${pk}`, { method: "PUT", body });
        if (Object.keys(r.values).length) csOvr[pk] = r.values; else delete csOvr[pk];
        status(`Angaben für ${n.name || pk.slice(0, 8)} gespeichert.`);
      } catch (er) { status("Fehler: " + er.message, "err"); }
    }
  });
}

function csPopup() {
  const pk = csSelPk; if (!pk) return;
  const n = csNode(pk) || (csLinks && { name: csLinks.name, lat: csLinks.lat, lon: csLinks.lon }) || {};
  if (n.lat == null) return;
  const cnt = csLinks ? `${csVisibleLinks().length} Nachbarn (nach Filter)` : `<span class="cs-wait">Nachbarn werden geladen …</span>`;
  const el = document.createElement("div");
  el.className = "popup";
  el.innerHTML = `<b>${esc(n.name || pk.slice(0, 8))}</b><br><span class="note">${esc(n.role || "")} · zuletzt gehört ${esc(fmtAgo(n.last_seen))}</span><br>${cnt}
    <div class="cs-act">${csActionsHtml(pk)}<button data-cs="close">Abwählen</button></div>`;
  csWireActions(el, pk);
  el.querySelector('[data-cs="close"]').onclick = (e) => { L.DomEvent.stopPropagation(e); csDeselect(); };
  L.popup({ autoPan: true, closeButton: true, offset: [0, -8] }).setLatLng([n.lat, n.lon]).setContent(el).openOn(map);
}

function csRenderSel() {
  const box = $("csSel"); if (!box) return;
  if (!csSelPk) { box.innerHTML = `<p class="note">Repeater auf der Karte anklicken, um seine Nachbarn zu sehen. Erneut anklicken hebt die Auswahl auf.</p>`; return; }
  const n = csNode(csSelPk) || {};
  if (!csLinks) { box.innerHTML = `<h3>${esc(n.name || csSelPk.slice(0, 8))}</h3><div class="note cs-wait">lade Nachbarn …</div>`; return; }
  const vis = csVisibleLinks().sort((a, b) => ((b.snr ?? b.snr_any ?? -99) - (a.snr ?? a.snr_any ?? -99)));
  box.innerHTML = `<h3>${esc(csLinks.name || n.name || "")}</h3>
    <div class="note">${esc(n.role || "")} · zuletzt gehört ${esc(fmtAgo(n.last_seen))} · ${csLinks.links.length} Verbindungen bekannt, ${vis.length} nach Filter (Fenster ${csLinks.window_days ?? "?"} Tage)</div>
    <div class="cs-act">${csActionsHtml(csSelPk)}<button data-cs="close">Abwählen</button></div>
    ${vis.length ? `<table class="mini cs-nb"><tr><th>Nachbar</th><th>km</th><th title="gemessen bzw. Mittel über Observer">SNR</th></tr>${vis.map((l) =>
      `<tr data-pk="${l.pk}"><td>${esc(l.name || l.pk.slice(0, 8))}${csStationOf(l.pk) ? " 📍" : ""}</td><td>${fmt(l.distance_km, 1)}</td>
       <td style="color:${snrColor(l.snr ?? l.snr_any ?? -99)}">${l.snr != null ? `<b>${fmt(l.snr, 1)}</b>` : l.snr_any != null ? `<i>${fmt(l.snr_any, 1)}</i>` : "–"}</td></tr>`).join("")}</table>
      <p class="note">Fett: gemessen (Partner hat direkt gehört). Kursiv: Mittel über andere Observer, sagt über die Strecke wenig.</p>` : ""}`;
  csWireActions(box, csSelPk);
  box.querySelector('[data-cs="close"]').onclick = () => csDeselect();
  box.querySelectorAll("tr[data-pk]").forEach((tr) => tr.onclick = () => csSelect(tr.dataset.pk));
}

function csAdopt(n) {
  const ex = csStationOf(n.pk);
  if (ex) { activateTab("stations"); select(ex.id, true); return; }
  const o = csOvr[n.pk] || {};
  const st = defaultStation(n.lat, n.lon);
  Object.assign(st, { name: n.name || st.name, corescope_pk: n.pk, height_m: o.height_m ?? 10, height_ref: "ground",
    notes: `Aus CoreScope übernommen (${cfg.corescope.url}). Höhe, Antenne und Leistung sind Annahmen – bitte anpassen.` });
  if (o.gain_dbi != null) st.antenna = { ...st.antenna, gain_dbi: o.gain_dbi, preset: "custom" };
  project.stations.push(st); drawStation(st); map.closePopup(); csDrawNodes();
  activateTab("stations"); select(st.id); changed();
  status(`„${st.name}“ übernommen – Höhe/Antenne/Leistung prüfen, dann speichern.`);
}

/** Klick auf eine Station: verknüpfte zeigen ihre Repeater-Nachbarn; erneuter Klick oder andere Station blendet aus. */
function csStationClicked(st) {
  if (!cfg.corescope?.enabled) return;
  if (st.corescope_pk) csSelect(st.corescope_pk, { fromStation: true });
  else if (csSelPk) csDeselect();
}

/** Liste der nächstgelegenen Knoten für die manuelle Verknüpfung im Stations-Editor. */
function csNearby(st, maxKm = 3, max = 15) {
  return csNodes.map((n) => ({ n, d: distKm(st.lat, st.lon, n.lat, n.lon) }))
    .filter((x) => x.d <= maxKm).sort((a, b) => a.d - b.d).slice(0, max);
}

async function csCalibrate(st) {
  activateTab("net");
  $("calOut").innerHTML = `<div class="note cs-wait">rechne Strecken für „${esc(st.name)}“ …</div>`;
  if (csSelPk !== st.corescope_pk || !csLinks) await csSelect(st.corescope_pk, { fromStation: true });
  if (!csLinks) { $("calOut").innerHTML = `<div class="warn">Nachbarn konnten nicht geladen werden.</div>`; return; }
  const partners = csVisibleLinks().filter((l) => l.snr != null).map((l) => l.pk);
  status(`Abgleich: rechne ${partners.length} Strecken …`, "busy");
  try {
    const r = await api("/api/corescope/calibrate", { method: "POST", body: {
      ...(isAdmin ? { project } : { project_id: project.id }), station_id: st.id, partners,
      rx_height_m: +$("calH").value, rx_gain_dbi: +$("calG").value, rx_cable_db: +$("calC").value } });
    const s = r.stats;
    let hint = "";
    if (s) {
      const m = s.mean_db;
      hint = Math.abs(m) < 2 ? "Modell und Messung passen im Mittel gut zusammen."
        : m < 0 ? `Das Modell ist im Mittel um ${fmt(-m)} dB zu optimistisch. Mögliche Ursachen: Antennenhöhe/-gewinn zu hoch angesetzt (bei der Station oder den Partnern), mehr Rauschen vor Ort (Einstellung „Rauschanhebung“) oder Hindernisse, die das Oberflächenmodell nicht kennt.`
        : `Das Modell ist im Mittel um ${fmt(m)} dB zu pessimistisch. Prüfe, ob Station oder Partner höher sitzen oder mehr Gewinn haben als eingetragen.`;
      if (s.n < 5) hint += " Nur wenige Messpunkte – mit Vorsicht deuten.";
    }
    const src = { station: ["📍", "Werte der Station im Projekt"], angabe: ["✎", "eigene Angabe zum Repeater"], standard: ["", "Standardwerte oben"] };
    $("calOut").innerHTML = `<div class="note">Rauschboden ${fmt(r.noise_dbm)} dBm · ${r.rows.length} gemessene Partner nach Filter · Fenster ${r.window_days ?? "?"} Tage. Zeile anklicken zeigt die Strecke.</div>
      <table class="mini cs-nb"><tr><th>Partner</th><th title="Antennenhöhe der Gegenstelle">Höhe</th><th>km</th><th title="Sichtlinie">LOS</th><th>gem.</th><th>Modell</th><th>Δ</th></tr>
      ${r.rows.map((x, i) => x.error ? `<tr><td>${esc(x.name)}</td><td colspan="6" class="warn">${esc(x.error)}</td></tr>`
        : `<tr data-i="${i}"${x.saturated ? ' class="dim" title="SNR ≥ ' + r.sat_db + ' dB: LoRa-Meldungen sind nach oben begrenzt, nicht in der Statistik"' : ""}>
          <td>${esc(x.name || "")} <span title="${src[x.rx_src][1]}">${src[x.rx_src][0]}</span></td><td>${fmt(x.rx_height_m, 0)} m</td><td>${fmt(x.distance_km, 1)}</td><td>${["–", "~", "✓"][x.los_class]}</td>
          <td>${fmt(x.measured_snr, 1)}</td><td>${fmt(x.predicted_snr, 1)}</td><td style="color:${Math.abs(x.delta_db) < 3 ? "var(--go)" : Math.abs(x.delta_db) < 8 ? "#b45309" : "var(--bad)"}">${x.delta_db > 0 ? "+" : ""}${fmt(x.delta_db, 1)}</td></tr>`).join("")}</table>
      <p class="note">📍 Werte der Station im Projekt (Höhe, Antenne in Richtung, Kabel) · ✎ eigene Angabe zum Repeater · sonst die Standardwerte oben.</p>
      ${s ? `<div class="calc">Δ = gemessen − Modell: Mittel <b>${s.mean_db > 0 ? "+" : ""}${fmt(s.mean_db)} dB</b> · Median ${fmt(s.median_db)} · Streuung ${fmt(s.std_db)} dB · n = ${s.n}</div><p class="note">${hint}</p>
        <p class="note">Gemessene Mittelwerte enthalten nur Pakete, die angekommen sind. An der Reichweitengrenze liegen sie deshalb eher zu hoch.</p>` : `<p class="note">Keine auswertbaren Messpunkte.</p>`}`;
    $("calOut").querySelectorAll("tr[data-i]").forEach((tr) => tr.onclick = () => {
      const x = r.rows[+tr.dataset.i];
      if (x.rx_station_id) showProfile(st.id, null, { rxStationId: x.rx_station_id });
      else showProfile(st.id, L.latLng(x.lat, x.lon), { rx: { h: x.rx_height_m, g: x.rx_gain_dbi, c: x.rx_cable_db, label: x.name } });
    });
    status(`Abgleich fertig: ${r.rows.length} Strecken.`);
  } catch (e) { $("calOut").innerHTML = `<div class="warn">Fehler: ${esc(e.message)}</div>`; status("Abgleich: " + e.message, "err"); }
}

async function csInit() {
  if (!cfg.corescope?.enabled) return;
  $("csOff").classList.add("hidden"); $("csOn").classList.remove("hidden");
  const s = await csRefreshStatus().catch(() => null);
  if (s && s.count) await csLoadNodes().catch(() => {});
  $("csShow").onchange = csDrawNodes;
  $("csAge").onchange = csDrawNodes; $("csRole").onchange = csDrawNodes;
  const refilter = () => { csDrawNodes(); csRenderSel(); };
  $("csBidir").onchange = refilter; $("csMeas").onchange = refilter;
  $("csMin").oninput = () => { $("csMinVal").textContent = $("csMin").value; refilter(); };
  csRenderSel();
  $("btnCsSync").onclick = async () => {
    try { await api("/api/corescope/sync", { method: "POST" }); } catch (e) { status("Fehler: " + e.message, "err"); return; }
    clearInterval(csTimer);
    csTimer = setInterval(async () => {
      const st = await csRefreshStatus().catch(() => null);
      if (st && !st.syncing) { clearInterval(csTimer); await csLoadNodes(); status(`CoreScope: ${st.count} Knoten geladen.`); }
    }, 2000);
    csRefreshStatus();
  };
}

// ------------------------------------------------------------ ui wiring
function activateTab(t) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + t));
}

function setPlacing(v) {
  placing = v;
  if (v) closeSide();
  $("placing").classList.toggle("hidden", !v);
  $("map").classList.toggle("placing", v);
}

function wire() {
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => activateTab(b.dataset.tab)));
  $("btnAdd").onclick = () => setPlacing(!placing);
  $("btnCompute").onclick = compute;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setPlacing(false); });
  map.on("click", (e) => {
    if (placing && isAdmin) {
      const st = defaultStation(e.latlng.lat, e.latlng.lng);
      project.stations.push(st); drawStation(st); setPlacing(false); select(st.id); changed();
      return;
    }
    inspectAt(e.latlng);
  });
  $("layerSel").onchange = showLayer;
  $("opacity").oninput = () => covLayer && covLayer.setOpacity(+$("opacity").value);
  $("reqSlider").oninput = () => { $("reqVal").textContent = $("reqSlider").value; };
  $("reqSlider").onchange = () => {
    project.settings.min_margin_db = +$("reqSlider").value;
    $("s_min_margin_db").value = $("reqSlider").value;
    if (isAdmin) setDirty();
    composite();
  };
  $("autoCalc").onchange = () => { if ($("autoCalc").checked) scheduleAuto(); };
  $("profClose").onclick = () => {
    $("profile").classList.add("hidden"); if (profLine) { map.removeLayer(profLine); profLine = null; }
    document.body.classList.remove("profile-open"); lastProfile = null;
    $("profRxH").value = ""; $("profRxG").value = ""; profAt = null;  // nächstes Profil startet mit den Projektwerten
  };
  const reProfile = () => { if (profAt && byId(profAt.sid)) showProfile(profAt.sid, profAt.latlng, { ...profAt.opts, rx: profAt.opts?.rx ? { ...profAt.opts.rx, h: +$("profRxH").value, g: +$("profRxG").value } : undefined }); };
  $("profRxH").onchange = reProfile; $("profRxG").onchange = reProfile;
  $("profReset").onclick = () => { $("profRxH").value = project.settings.rx_height_m; $("profRxG").value = project.settings.rx_gain_dbi; reProfile(); };
  $("projName").onchange = () => { project.name = $("projName").value; setDirty(); };
  $("projPublic").onchange = () => { project.public = $("projPublic").checked; setDirty(); };
  $("btnShare").onclick = async () => {
    if (!project.id) { status("Projekt zuerst speichern.", "err"); return; }
    const url = `${location.origin}${location.pathname}?p=${encodeURIComponent(project.id)}`;
    try { await navigator.clipboard.writeText(url); status("Link kopiert" + (project.public ? "." : " – Achtung: Projekt ist privat, Besucher sehen es nicht.")); }
    catch { prompt("Link zum Projekt:", url); }
  };
  $("btnLogin").onclick = () => (isAdmin ? logout() : $("loginDlg").showModal());
  $("loginForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/login", { method: "POST", body: { user: $("loginUser").value, password: $("loginPw").value, remember: $("loginRemember").checked } });
      location.reload();
    } catch (er) { $("loginErr").textContent = er.message; }
  };
  $("loginCancel").onclick = () => $("loginDlg").close();
  $("btnSave").onclick = () => saveProject().catch((e) => status("Fehler: " + e.message, "err"));
  $("btnNew").onclick = () => { if (!dirty || confirm("Ungespeicherte Änderungen verwerfen?")) newProject(); };
  $("projList").onchange = async (e) => {
    const id = e.target.value; e.target.value = "";
    if (!id || (dirty && !confirm("Ungespeicherte Änderungen verwerfen?"))) return;
    try { loadProject(await api(`/api/projects/${id}`)); } catch (er) { status("Fehler: " + er.message, "err"); }
  };
  $("btnMore").onclick = () => $("moreMenu").classList.toggle("hidden");
  $("btnExport").onclick = () => {
    $("moreMenu").classList.add("hidden");
    project.name = $("projName").value;
    const blob = new Blob([JSON.stringify(project, null, 1)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = (project.name || "projekt").replace(/[^\w\-äöüÄÖÜß ]/g, "_") + ".json"; a.click();
  };
  $("btnImport").onclick = () => { $("moreMenu").classList.add("hidden"); $("fileImport").click(); };
  $("fileImport").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const p = JSON.parse(await f.text()); p.id = null; loadProject(p); setDirty(); } catch (er) { status("Import fehlgeschlagen: " + er.message, "err"); }
    e.target.value = "";
  };
  $("btnDelete").onclick = async () => {
    $("moreMenu").classList.add("hidden");
    if (!project.id || !confirm(`Projekt „${project.name}“ auf dem Server löschen?`)) return;
    await api(`/api/projects/${project.id}`, { method: "DELETE" }); newProject(); refreshProjectList();
  };
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  let rzT = null;
  window.addEventListener("resize", () => {
    clearTimeout(rzT);
    rzT = setTimeout(() => { if (lastProfile && !$("profile").classList.contains("hidden")) drawProfile(lastProfile); }, 150);
  });
  // Handy: Menü, Ansicht-Box, Installationshinweis
  $("btnSide").onclick = openSide;
  $("btnSideClose").onclick = closeSide;
  $("sideBackdrop").onclick = closeSide;
  $("layerToggle").onclick = () => {
    const c = $("layerbox").classList.toggle("collapsed");
    $("layerToggle").setAttribute("aria-expanded", String(!c));
  };
  if (narrow()) $("layerbox").classList.add("collapsed");
  $("lnkInstall").onclick = (e) => {
    e.preventDefault();
    const secure = location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname);
    $("installHttp").textContent = secure ? "" : "Achtung: Diese Seite läuft ohne HTTPS. Als eigenständige App lässt sie sich nur über HTTPS installieren (z. B. über einen Reverse-Proxy mit Zertifikat) – sonst entsteht nur ein Lesezeichen, das im Browser öffnet.";
    $("installDlg").showModal();
  };
  $("installClose").onclick = () => $("installDlg").close();
}

async function logout() {
  if (dirty && !confirm("Ungespeicherte Änderungen verwerfen?")) return;
  dirty = false;
  await api("/api/logout", { method: "POST" });
  location.reload();
}

function applyRole() {
  document.body.classList.toggle("readonly", !isAdmin);
  $("btnLogin").textContent = isAdmin ? `Abmelden (${cfg.auth.user})` : "Anmelden";
  if (!isAdmin && document.querySelector(".tabs button.active")?.dataset.tab === "settings") activateTab("stations");
}

async function init() {
  cfg = await api("/api/config");
  setupBasemaps(!!cfg.tile_proxy);
  isAdmin = !!cfg.auth?.admin;
  $("ver").textContent = !cfg.version ? "" : /^\d/.test(cfg.version) ? "v" + cfg.version : cfg.version;
  wire(); applyRole();
  csInit().catch((e) => console.warn("CoreScope", e));
  if (isAdmin) bindSettings();
  newProject();
  if (!isAdmin && !cfg.auth.public_read) { status("Bitte anmelden."); $("loginDlg").showModal(); return; }
  const list = await refreshProjectList().catch(() => []);
  const want = new URLSearchParams(location.search).get("p");
  const pick = want || (isAdmin ? cfg.last_project : (list && list.length ? list[0].id : null));
  if (pick) {
    try { loadProject(await api(`/api/projects/${encodeURIComponent(pick)}`)); }
    catch (e) { status("Projekt nicht gefunden oder nicht öffentlich.", "err"); }
  } else {
    status(isAdmin ? "Bereit. „＋ Station platzieren“, dann „▶ Berechnen“." : "Noch keine öffentlichen Projekte.");
  }
}
init().catch((e) => status("Start fehlgeschlagen: " + e.message, "err"));
})();
