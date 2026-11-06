/**
 * Terrain profile and link budget between a station and a point (or another station), shown as a drawer
 * at the bottom of the map. Buildings (from footprints, #25) are drawn grey, other surface green.
 */
import { t } from "./core/i18n.js";
import { $, escapeHtml, fmt, signed, niceStep } from "./core/util.js";
import { stationById, settings } from "./core/state.js";
import { api, projectRef } from "./core/api.js";
import { map } from "./map/map.js";
import { closeSheet } from "./core/ui.js";

let current = null;      // {stationId, latlng, options}
let lastProfile = null;
let pathLine = null;
let receiverOverride = { height: null, gain: null };

function shell() {
  const el = $("profile");
  el.innerHTML = `<div class="phead"><b id="profTitle"></b><span class="meta" id="profMeta"></span>
      <span id="profRx" title="${escapeHtml(t("Only for this profile – the map stays as computed"))}">${t("Receiver")}
        <input id="profRxH" type="number" step="0.5" min="0" max="300"> m <input id="profRxG" type="number" step="0.5" min="-10" max="40"> dBi
        <button id="profRxReset" class="small" title="${escapeHtml(t("Back to the global receiver values"))}">↺</button></span>
      <button id="profClose" class="iconbtn" title="${escapeHtml(t("Close"))}">✕</button></div>
    <div class="pbody"><canvas id="profCanvas"></canvas><table id="profBudget"></table></div>`;
  $("profClose").onclick = closeProfile;
  const again = () => { receiverOverride = { height: +$("profRxH").value, gain: +$("profRxG").value }; if (current) showProfile(current.stationId, current.latlng, current.options); };
  $("profRxH").onchange = again; $("profRxG").onchange = again;
  $("profRxReset").onclick = () => { receiverOverride = { height: null, gain: null }; if (current) showProfile(current.stationId, current.latlng, { ...current.options, rx: undefined }); };
}

export function closeProfile() {
  $("profile").classList.add("hidden");
  if (pathLine) { map.removeLayer(pathLine); pathLine = null; }
  current = null; lastProfile = null; receiverOverride = { height: null, gain: null };
}

/**
 * Profile from a station to a point, or to another station (options.rxStationId: its height, antenna
 * pattern and cable), or to a repeater with given receiver values (options.rx = {h, g, c, label}).
 */
export async function showProfile(stationId, latlng, options = {}) {
  const st = stationById(stationId);
  if (!st) return;
  const rxStation = options.rxStationId ? stationById(options.rxStationId) : null;
  if (rxStation) latlng = L.latLng(rxStation.lat, rxStation.lon);
  if (!$("profTitle")) shell();
  current = { stationId, latlng, options };
  closeSheet();
  $("profile").classList.remove("hidden");
  const rxHeight = options.rx?.h ?? receiverOverride.height ?? settings().rx_height_m;
  const rxGain = options.rx?.g ?? receiverOverride.gain ?? settings().rx_gain_dbi;
  $("profRxH").value = rxHeight; $("profRxG").value = rxGain;
  $("profRx").classList.toggle("hidden", !!rxStation);
  $("profTitle").textContent = rxStation ? `${st.name} → ${rxStation.name}` : options.rx?.label ? `${st.name} → ${options.rx.label}` : t("Profile {name} → point", { name: st.name });
  $("profMeta").textContent = t("computing …");
  if (pathLine) map.removeLayer(pathLine);
  pathLine = L.polyline([[st.lat, st.lon], [latlng.lat, latlng.lng]], { color: st.color, dashArray: "6 4", weight: 2 }).addTo(map);
  try {
    const p = await api("/api/profile", { method: "POST", body: { ...projectRef(), station_id: stationId, lat: latlng.lat, lon: latlng.lng,
      ...(rxStation ? { rx_station_id: rxStation.id } : { rx_height_m: rxHeight, rx_gain_dbi: rxGain, rx_cable_db: options.rx?.c }) } });
    const sight = [t("no line of sight"), t("line of sight, Fresnel zone obstructed"), t("Fresnel zone ≥ 60 % clear")][p.los_class];
    $("profMeta").textContent = `${fmt(p.distance_m / 1000, 2)} km · ${t("azimuth")} ${fmt(p.azimuth_deg, 0)}° · ${t("elevation")} ${fmt(p.elevation_deg, 2)}° · ${sight}` +
      (p.worst_at_m != null ? " · " + t("tightest at {d} km ({p} % of F1 clear)", { d: fmt(p.worst_at_m / 1000, 2), p: fmt(p.worst_clearance_ratio * 100, 0) }) : "");
    drawProfile(p);
    renderBudget(p);
  } catch (e) { $("profMeta").textContent = t("Error: {msg}", { msg: e.message }); }
}

function renderBudget(p) {
  const b = p.budget, hata = p.hata;
  const rows = [
    [t("Transmit power"), `${fmt(b.tx_power_dbm)} dBm`], [t("Cable Tx"), `−${fmt(b.tx_cable_db)} dB`],
    [t("Antenna Tx (towards receiver)"), `${fmt(b.tx_gain_dbi)} dBi`], [t("Free-space loss"), `−${fmt(b.fspl_db)} dB`],
    [`${t("Diffraction")} (${settings().diffraction_model === "bullington" ? "Bullington" : "Deygout"})`, `−${fmt(b.diffraction_db)} dB`],
    [t("Ground reflection"), `−${fmt(b.reflection_db)} dB`],
    [`<span title="${escapeHtml(t("ITU-R P.833: part of the direct ray inside objects of the surface model"))}">${t("Through objects ({m} m)", { m: fmt(b.obstacle_len_m, 0) })}</span>`, `−${fmt(b.obstacle_db)} dB`],
    [t("Clutter Rx (R {m} m)", { m: fmt(b.clutter_height_m, 0) }), `−${fmt(b.clutter_db)} dB`],
    [b.rx_station ? t("Antenna Rx {name} (towards Tx, {h} m)", { name: escapeHtml(b.rx_station), h: fmt(b.rx_height_m, 1) }) : t("Antenna Rx"), `${fmt(b.rx_gain_dbi)} dBi`],
    [t("Cable + extra Rx"), `−${fmt(b.rx_cable_db + b.rx_extra_db)} dB`],
  ];
  const marginColour = b.margin_db >= b.required_margin_db ? "var(--go)" : b.margin_db >= 0 ? "var(--warn)" : "var(--bad)";
  $("profBudget").innerHTML = rows.map(([a, v]) => `<tr><td>${a}</td><td>${v}</td></tr>`).join("") +
    `<tr class="sum"><td>${t("Total path loss")}</td><td>${fmt(b.total_loss_db)} dB</td></tr>
     <tr><td title="${escapeHtml(t("Okumura-Hata, for comparison only"))}">${t("Hata urban/suburban")}${hata.valid ? "" : " ⚠"}</td><td>${fmt(hata.loss_db.urban)} / ${fmt(hata.loss_db.suburban)} dB</td></tr>
     <tr class="sum"><td>${t("Received level")}</td><td>${fmt(b.prx_dbm)} dBm</td></tr>
     <tr><td>${t("Sensitivity")}</td><td>${fmt(b.sensitivity_dbm)} dBm</td></tr>
     <tr class="sum"><td>${t("Margin (required {n})", { n: fmt(b.required_margin_db, 0) })}</td><td style="color:${marginColour}">${signed(b.margin_db)} dB</td></tr>`;
}

function drawProfile(p) {
  lastProfile = p;
  const cv = $("profCanvas");
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, W, H);
  const padL = 44, padR = 10, padT = 22, padB = 22;
  const D = p.d[p.d.length - 1];
  const fresnelLow = p.los.map((v, i) => v - p.fresnel[i]);
  const fresnelHigh = p.los.map((v, i) => v + p.fresnel[i]);
  const ymin = Math.min(...p.ground) - 5;
  const ymax = Math.max(...p.surface, ...fresnelHigh, p.tx_alt, p.rx_alt) + 5;
  const X = (d) => padL + (d / D) * (W - padL - padR);
  const Y = (z) => padT + (1 - (z - ymin) / (ymax - ymin)) * (H - padT - padB);
  g.strokeStyle = "#e5e7eb"; g.fillStyle = "#6b7280"; g.font = "11px system-ui"; g.lineWidth = 1;
  const stepZ = niceStep((ymax - ymin) / 5);
  for (let z = Math.ceil(ymin / stepZ) * stepZ; z <= ymax; z += stepZ) {
    g.beginPath(); g.moveTo(padL, Y(z)); g.lineTo(W - padR, Y(z)); g.stroke(); g.fillText(`${z.toFixed(0)} m`, 2, Y(z) + 4);
  }
  const stepD = niceStep(D / Math.max(2, Math.floor((W - padL - padR) / 70)));   // ~70 px per label
  const distLabel = (d) => stepD < 100 ? `${fmt(d, 0)} m` : `${fmt(d / 1000, stepD < 1000 ? 1 : 0)} km`;
  for (let d = 0; d <= D; d += stepD) g.fillText(distLabel(d), X(d) - 12, H - 6);
  const area = (arr) => { g.beginPath(); arr.forEach((z, i) => (i ? g.lineTo(X(p.d[i]), Y(z)) : g.moveTo(X(p.d[i]), Y(z)))); g.lineTo(X(D), H - padB); g.lineTo(X(0), H - padB); g.closePath(); };
  const line = (arr) => { g.beginPath(); arr.forEach((z, i) => (i ? g.lineTo(X(p.d[i]), Y(z)) : g.moveTo(X(p.d[i]), Y(z)))); };
  // Fresnel zone
  g.beginPath(); fresnelHigh.forEach((z, i) => (i ? g.lineTo(X(p.d[i]), Y(z)) : g.moveTo(X(p.d[i]), Y(z))));
  for (let i = fresnelLow.length - 1; i >= 0; i--) g.lineTo(X(p.d[i]), Y(fresnelLow[i]));
  g.closePath(); g.fillStyle = "rgba(37,99,235,0.10)"; g.fill();
  // surface (vegetation green), buildings grey, ground brown
  area(p.surface); g.fillStyle = "#86a873"; g.fill();
  const bld = p.building || [];
  g.fillStyle = "#94a3b8";
  for (let i = 0; i + 1 < p.d.length; i++) {
    if (!bld[i]) continue;
    g.beginPath();
    g.moveTo(X(p.d[i]), Y(p.surface[i])); g.lineTo(X(p.d[i + 1]), Y(p.surface[i + 1]));
    g.lineTo(X(p.d[i + 1]), Y(p.ground[i + 1])); g.lineTo(X(p.d[i]), Y(p.ground[i])); g.closePath(); g.fill();
  }
  area(p.ground); g.fillStyle = "#b08a5e"; g.fill();
  // legend
  const items = [["#b08a5e", t("Terrain")], ["#86a873", bld.some(Boolean) ? t("Vegetation / other surface") : t("Surface (buildings, trees)")]];
  if (bld.some(Boolean)) items.push(["#94a3b8", t("Buildings (footprints)")]);
  let lx = padL + 6;
  for (const [c, text] of items) { g.fillStyle = c; g.fillRect(lx, 5, 10, 10); g.fillStyle = "#374151"; g.fillText(text, lx + 14, 14); lx += 22 + g.measureText(text).width; }
  // line of sight, lower Fresnel edge, end points, tightest spot
  g.strokeStyle = "#2563eb"; g.lineWidth = 1.5; line(p.los); g.stroke();
  g.setLineDash([4, 3]); g.lineWidth = 1; line(fresnelLow); g.stroke(); g.setLineDash([]);
  g.fillStyle = "#111"; g.beginPath(); g.arc(X(0), Y(p.tx_alt), 3.5, 0, 7); g.fill();
  g.beginPath(); g.arc(X(D), Y(p.rx_alt), 3.5, 0, 7); g.fill();
  if (p.worst_at_m != null) { g.strokeStyle = "#dc2626"; g.beginPath(); g.moveTo(X(p.worst_at_m), padT); g.lineTo(X(p.worst_at_m), H - padB); g.stroke(); }
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (lastProfile && !$("profile").classList.contains("hidden")) drawProfile(lastProfile); }, 150);
});
