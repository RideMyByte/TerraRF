/**
 * The inspector shows whatever was clicked, always with the same layout:
 *   station · repeater · link between repeaters (#26) · measurement ping (#27) · map location
 */
import { t } from "./core/i18n.js";
import { $, escapeHtml, fmt, signed, fmtAgo, fmtDateTime, distanceKm, snrColor, html } from "./core/util.js";
import { state, emit, on, isMember, canEditProject, canEditStation, isAdmin, stationById, stationOfRepeater, settings } from "./core/state.js";
import { api } from "./core/api.js";
import { showInspector, closeInspector, renderInspectorTab, inspectorKey, section, rememberSections, status, sourceBadge, sourceName, openWorkspace } from "./core/ui.js";
import { map } from "./map/map.js";
import { RADIO_PRESETS, LORA_REQUIRED_SNR, sensitivityDbm, noiseFloorDbm, estimatedVerticalBeamwidth, applyCatalogAntenna, drawStation, removeStationMarker, startPlacing, newStation, withoutLabel } from "./map/stations.js";
import { compute, resultsByStation, sampleStation, setSolo, view, requiredMargin } from "./map/coverage.js";
import { showProfile } from "./profile.js";
import { net, nodeByPk, linkTo, snrText, mastHeightGuess, showNeighbours, clearNetworkSelection, setFocus, loadMeta, saveOverride, nearbyNodes, repeaterName, enabled as networkEnabled, nodePosition } from "./network.js";
import { meas, seriesById, seriesName, receiverText, receiverFor, loadMeasurements, focusSeries } from "./measurements.js";
import { exportPdf } from "./workspaces/report.js";

// ================================================================ station
export function inspectStation(station, tab) {
  const editable = canEditStation(station);
  const titleInput = editable ? html(`<input value="${escapeHtml(station.name)}" aria-label="${escapeHtml(t("Name"))}">`) : null;
  if (titleInput) titleInput.onchange = () => { station.name = titleInput.value.trim() || station.name; changed(station); };
  const tabs = [{ id: "overview", label: t("Overview"), render: (b) => stationOverview(b, station) },
    { id: "settings", label: editable ? t("Edit") : t("Details"), render: (b) => stationSettings(b, station) }];
  if (networkEnabled()) tabs.push({ id: "network", label: t("Network"), render: (b) => stationNetwork(b, station) });
  showInspector({ kind: t("Station"), key: "station:" + station.id, title: titleInput || station.name, tabs, tab });
}

function changed(station) {
  emit("project-changed", { station });
  drawStation(station);
  if (inspectorKey() === "station:" + station.id) renderInspectorTab();
}

function antennaSummary(a) {
  const cat = state.catalog.antennas.find((x) => x.id === a.catalog_id);
  const base = a.type === "directional" ? `${t("directional")} ${fmt(a.gain_dbi, 0)} dBi · ${fmt(a.azimuth_deg ?? 0, 0)}°` : `${t("omni")} ${fmt(a.gain_dbi, 1)} dBi`;
  return (cat ? escapeHtml(cat.name) + " · " : "") + base + (a.sector_on ? ` · ${t("sector")} ${fmt(a.sector_az_deg ?? 270, 0)}°` : "");
}

function stationOverview(body, st) {
  const result = resultsByStation[st.id], a = st.antenna || {};
  const mw = 10 ** (st.tx_power_dbm / 10);
  const eirp = st.tx_power_dbm - (st.cable_loss_db || 0) + (+a.gain_dbi || 0);
  const repeater = st.corescope_pk ? repeaterName(st.corescope_pk) : null;
  body.innerHTML = `
    <div class="calc"><b style="color:${escapeHtml(st.color)}">●</b> ${fmt(st.freq_mhz, 3)} MHz · ${fmt(mw >= 1000 ? mw / 1000 : mw, mw >= 1000 ? 2 : 0)} ${mw >= 1000 ? "W" : "mW"} · EIRP ${fmt(eirp)} dBm<br>
      ${t("Height")} ${fmt(st.height_m, 1)} m · ${antennaSummary(a)}<br>${t("Sensitivity")} ${fmt(sensitivityDbm(st))} dBm · ${t("Radius")} ${fmt(st.radius_km, 1)} km
      ${repeater ? `<br>MeshCore: <b>${escapeHtml(repeater)}</b>` : ""}</div>
    ${result ? `<div>${t("Covered (≥ {n} dB)", { n: fmt(requiredMargin(), 0) })}: <b>${fmt(result.area_km2, 1)} km²</b> · ${t("line of sight")} ${fmt(result.los_area_km2, 1)} km²</div>
      ${(result.warnings || []).map((w) => `<div class="warn">⚠ ${escapeHtml(w)}</div>`).join("")}`
      : `<div class="muted">${t("Not computed yet.")}</div>`}
    <div class="actions">
      ${st.enabled !== false && isMember() ? `<button data-a="run" class="primary">▶ ${t("Compute")}</button>` : ""}
      <button data-a="solo">${view.soloStationId === st.id ? t("Show all stations") : t("Only this station")}</button>
      <button data-a="pdf">PDF</button>
      ${st.corescope_pk ? `<button data-a="compare">${t("Compare with measurements")}</button>` : ""}
    </div>
    ${state.project.stations.length > 1 ? `<h4>${t("Link to another station")}</h4>
      <div class="row"><select id="linkTarget">${state.project.stations.filter((x) => x.id !== st.id).map((x) => `<option value="${x.id}">${escapeHtml(x.name)} (${fmt(distanceKm(st.lat, st.lon, x.lat, x.lon), 1)} km)</option>`).join("")}</select>
        <button data-a="ptp" style="flex:0 0 auto">${t("Profile")}</button></div>
      ${canEditStation(st) ? `<button data-a="aim">${t("Point both directional antennas at each other")}</button>` : ""}` : ""}
    ${st.notes ? `<p class="muted">${escapeHtml(st.notes)}</p>` : ""}`;
  const on_ = (a, fn) => { const b = body.querySelector(`[data-a="${a}"]`); if (b) b.onclick = fn; };
  on_("run", () => compute({ only: [st.id], force: [st.id] }));
  on_("solo", () => { setSolo(view.soloStationId === st.id ? null : st.id); renderInspectorTab(); });
  on_("pdf", () => exportPdf(st.id));
  on_("compare", () => { emit("compare-station", { stationId: st.id }); openWorkspace("compare"); });
  on_("ptp", () => showProfile(st.id, null, { rxStationId: body.querySelector("#linkTarget").value }));
  on_("aim", () => aimAtEachOther(st, stationById(body.querySelector("#linkTarget").value)));
}

/** Turn and tilt the directional antennas of two stations towards each other. */
async function aimAtEachOther(a, b) {
  if (!a || !b) return;
  try {
    const p = await api("/api/profile", { method: "POST", body: { project: state.project, station_id: a.id, lat: b.lat, lon: b.lon, rx_station_id: b.id } });
    for (const [st, az, el] of [[a, p.azimuth_deg, p.elevation_deg], [b, (p.azimuth_deg + 180) % 360, -p.elevation_deg]]) {
      if (st.antenna?.type === "directional" && canEditStation(st)) { st.antenna.azimuth_deg = Math.round(az); st.antenna.tilt_deg = Math.round(-el * 10) / 10; drawStation(st); }
    }
    emit("project-changed", {}); renderInspectorTab();
    status(t("Antennas aligned: {az}°.", { az: fmt(p.azimuth_deg, 0) }));
  } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
}

function stationSettings(body, st) {
  const a = st.antenna || (st.antenna = {});
  const editable = canEditStation(st);
  const option = (value, label, current) => `<option value="${value}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`;
  const antennas = state.catalog.antennas;
  body.innerHTML = `
    ${section("loc", t("Location"), `
      <div class="grid2">
        <label>${t("Latitude")}<input type="number" step="0.000001" data-k="lat" value="${st.lat}"></label>
        <label>${t("Longitude")}<input type="number" step="0.000001" data-k="lon" value="${st.lon}"></label>
        <label>${t("Antenna height (m)")}<input type="number" step="0.5" data-k="height_m" value="${st.height_m}"></label>
        <label>${t("Height above")}<select data-k="height_ref">${option("ground", t("terrain (ground)"), st.height_ref || "ground")}${option("surface", t("surface (roof)"), st.height_ref)}</select></label>
      </div>
      ${editable ? `<button id="btnMove">📍 ${t("Set on the map")}</button>` : ""}
      <div class="calc" id="elevationInfo">${t("Terrain height")}: <button id="btnElevation" class="small">${t("look up")}</button></div>`, `${fmt(st.height_m, 0)} m`)}
    ${section("radio", t("Radio"), `
      <label>${t("Preset")}<select data-k="radio_preset">${Object.entries(RADIO_PRESETS).map(([k, v]) => option(k, t(v.label), st.radio_preset || "custom")).join("")}</select></label>
      <div class="grid2">
        <label>${t("Frequency (MHz)")}<input type="number" step="0.001" data-k="freq_mhz" value="${st.freq_mhz}"></label>
        <label>${t("Bandwidth (kHz)")}<input type="number" step="0.1" data-k="bw_khz" value="${st.bw_khz}"></label>
        <label>${t("Transmit power (dBm)")}<input type="number" step="0.5" data-k="tx_power_dbm" value="${st.tx_power_dbm}"></label>
        <label>${t("Cable/connectors (dB)")}<input type="number" step="0.1" data-k="cable_loss_db" value="${st.cable_loss_db ?? 0}"></label>
        <label>${t("Modulation")}<select data-k="mode">${option("lora", "LoRa", st.mode)}${option("fm", "FM", st.mode)}${option("custom", t("other (SNR)"), st.mode)}</select></label>
        ${st.mode === "lora" ? `<label>${t("Spreading factor")}<select data-k="sf">${Object.keys(LORA_REQUIRED_SNR).map((x) => `<option ${+st.sf === +x ? "selected" : ""}>${x}</option>`).join("")}</select></label>`
          : `<label>${t("Required SNR (dB)")}<input type="number" step="0.5" data-k="req_snr_db" value="${st.req_snr_db ?? 10}"></label>`}
        <label>${t("Noise figure Rx (dB)")}<input type="number" step="0.5" data-k="nf_db" value="${st.nf_db ?? 6}"></label>
        <label>${t("Fixed sensitivity (dBm)")}<input type="number" step="0.5" data-k="sens_override_dbm" value="${st.sens_override_dbm ?? ""}" placeholder="auto"></label>
        <label title="${escapeHtml(t("Average noise the station's receiver sees, e.g. from the repeater's statistics. Replaces the thermal noise and the global noise rise for this station."))}">${t("Measured noise floor (dBm)")}<input type="number" step="1" data-k="noise_floor_dbm" value="${st.noise_floor_dbm ?? ""}" placeholder="${escapeHtml(t("thermal {v}", { v: fmt(noiseFloorDbm({ ...st, noise_floor_dbm: null }), 0) }))}"></label>
      </div>
      <p class="muted">${t("The noise floor counts where this station receives: its sensitivity on the map, and in the comparison the SNR it reports itself about other repeaters.")}</p>`, `${fmt(st.freq_mhz, 3)} MHz · ${fmt(st.tx_power_dbm, 0)} dBm`)}
    ${section("ant", t("Antenna"), `
      <label>${t("From the catalog")}<select id="antennaCatalog"><option value="">${t("– custom values –")}</option>${antennas.map((x) => option(x.id, `${x.name} (${fmt(x.gain_dbi, 1)} dBi)`, a.catalog_id)).join("")}</select></label>
      <div class="grid2">
        <label>${t("Type")}<select data-k="antenna.type">${option("omni", t("omni"), a.type)}${option("directional", t("directional"), a.type)}</select></label>
        <label>${t("Gain (dBi)")}<input type="number" step="0.1" data-k="antenna.gain_dbi" value="${a.gain_dbi ?? 0}"></label>
        ${a.type === "directional" ? `<label>${t("Azimuth (° from north)")}<input type="number" step="1" data-k="antenna.azimuth_deg" value="${a.azimuth_deg ?? 0}"></label>
        <label>${t("Horizontal beam width (°)")}<input type="number" step="1" data-k="antenna.hbw_deg" value="${a.hbw_deg ?? 60}"></label>` : ""}
        <label>${t("Vertical beam width (°)")}<input type="number" step="0.5" data-k="antenna.vbw_deg" value="${a.vbw_deg ?? ""}" placeholder="auto ${fmt(estimatedVerticalBeamwidth(a), 0)}"></label>
        <label>${t("Tilt, + = down (°)")}<input type="number" step="0.5" data-k="antenna.tilt_deg" value="${a.tilt_deg ?? 0}"></label>
        <label>${t("Front/back (dB)")}<input type="number" step="1" data-k="antenna.fb_db" value="${a.fb_db ?? 25}"></label>
      </div>
      ${a.type === "directional" && editable ? `<p class="muted">${t("Tip: drag the white handle at the end of the beam on the map to turn the antenna.")}</p>` : ""}
      <label class="chk"><input type="checkbox" data-k="antenna.sector_on" ${a.sector_on ? "checked" : ""}> ${t("Radiate only into a sector (wall or building behind)")}</label>
      ${a.sector_on ? `<div class="grid3">
        <label>${t("Direction (°)")}<input type="number" step="5" data-k="antenna.sector_az_deg" value="${a.sector_az_deg ?? 270}"></label>
        <label>${t("Width (°)")}<input type="number" step="10" min="10" max="359" data-k="antenna.sector_width_deg" value="${a.sector_width_deg ?? 180}"></label>
        <label>${t("Loss outside (dB)")}<input type="number" step="1" min="0" data-k="antenna.sector_loss_db" value="${a.sector_loss_db ?? 25}"></label></div>
        <p class="muted">${t("No directional pattern: inside the sector the antenna stays as set, outside it is attenuated with a soft 15° edge. Buildings in the surface model are computed anyway – the sector is for what the model cannot know (a wall, roof structures, metal).")}</p>` : ""}`,
      antennaSummary(a))}
    ${networkEnabled() ? section("mc", "MeshCore", st.corescope_pk
      ? `<div>${t("Linked with")} <b>${escapeHtml(repeaterName(st.corescope_pk))}</b>.</div>${isAdmin() ? `<button id="btnUnlink" class="danger">${t("Unlink")}</button>` : ""}`
      : isAdmin() ? `<label>${t("Link with a repeater (within 3 km)")}<select id="linkRepeater"><option value="">${t("– not linked –")}</option>${nearbyNodes(st.lat, st.lon).map(({ node, km }) => `<option value="${node.pk}">${escapeHtml(node.name)} (${fmt(km * 1000, 0)} m)</option>`).join("")}</select></label>`
      : `<p class="muted">${t("not linked")}</p>`, st.corescope_pk ? t("linked") : "") : ""}
    ${section("calc", t("Computation & notes"), `
      <div class="grid2"><label>${t("Radius (km)")}<input type="number" step="0.5" min="0.5" max="80" data-k="radius_km" value="${st.radius_km}"></label></div>
      <label>${t("Notes")}<textarea data-k="notes" rows="2">${escapeHtml(st.notes || "")}</textarea></label>`, `${fmt(st.radius_km, 1)} km`)}
    ${editable ? `<div class="actions"><button id="btnDeleteStation" class="danger">🗑 ${t("Delete station")}</button></div>` : ""}`;
  rememberSections(body);
  body.querySelectorAll("[data-k], #antennaCatalog, #linkRepeater").forEach((el) => { if (!editable && el.id !== "linkRepeater") el.disabled = true; });
  if (!editable) return;
  body.querySelectorAll("[data-k]").forEach((input) => input.addEventListener("change", () => setField(st, input)));
  body.querySelector("#antennaCatalog").onchange = (e) => {
    if (e.target.value) applyCatalogAntenna(st, e.target.value); else delete st.antenna.catalog_id;
    changed(st);
  };
  body.querySelector("#btnMove").onclick = () => { startPlacing("move", st.id); status(t("Click the new location of “{name}” (Esc cancels).", { name: st.name })); };
  body.querySelector("#btnElevation").onclick = async () => {
    const box = body.querySelector("#elevationInfo");
    box.textContent = t("Looking up elevation data …");
    try {
      const r = await api("/api/elevation", { method: "POST", body: { lat: st.lat, lon: st.lon } });
      const base = st.height_ref === "surface" ? r.surface_m : r.ground_m;
      box.innerHTML = t("Terrain {g} m · surface {s} m (Δ {d} m) → antenna at <b>{a} m</b> above sea level",
        { g: fmt(r.ground_m), s: fmt(r.surface_m), d: fmt(r.surface_m - r.ground_m), a: fmt(base + +st.height_m) });
    } catch (e) { box.textContent = t("Error: {msg}", { msg: e.message }); }
  };
  const link = body.querySelector("#linkRepeater");
  if (link) link.onchange = () => {
    if (!link.value) return;
    if (stationOfRepeater(link.value)) { status(t("This repeater is already linked with another station."), "err"); link.value = ""; return; }
    st.corescope_pk = link.value; emit("stations-changed"); changed(st);
  };
  const unlink = body.querySelector("#btnUnlink");
  if (unlink) unlink.onclick = () => { if (net.centrePk === st.corescope_pk) clearNetworkSelection(); delete st.corescope_pk; emit("stations-changed"); changed(st); };
  body.querySelector("#btnDeleteStation").onclick = () => {
    if (!confirm(t("Delete station “{name}”?", { name: st.name }))) return;
    state.project.stations = state.project.stations.filter((x) => x.id !== st.id);
    removeStationMarker(st.id); closeInspector();
    emit("stations-changed"); emit("project-changed", {});
  };
}

function setField(st, input) {
  const path = input.dataset.k.split(".");
  let value = input.type === "checkbox" ? input.checked : input.type === "number" ? (input.value === "" ? null : +input.value) : input.value;
  if (path[0] === "sf") value = +value;
  if (path.length === 2) {
    st[path[0]] = st[path[0]] || {};
    st[path[0]][path[1]] = value;
    if (!["azimuth_deg", "tilt_deg"].includes(path[1]) && !path[1].startsWith("sector_")) delete st.antenna.catalog_id;  // own values now
    if (path[1] === "sector_on" && value) { st.antenna.sector_az_deg ??= 270; st.antenna.sector_width_deg ??= 180; st.antenna.sector_loss_db ??= 25; }
  } else {
    st[path[0]] = value;
    if (path[0] === "radio_preset" && RADIO_PRESETS[value] && !["custom", "lora_custom"].includes(value)) Object.assign(st, withoutLabel(RADIO_PRESETS[value]));
    if (path[0] === "radio_preset" && value === "lora_custom") st.mode = "lora";
    if (["freq_mhz", "bw_khz", "mode", "sf", "tx_power_dbm", "nf_db", "req_snr_db"].includes(path[0])) st.radio_preset = "custom";
  }
  changed(st);
}

function stationNetwork(body, st) {
  if (!st.corescope_pk) { body.innerHTML = `<p class="muted">${t("Link the station with its repeater under “Edit → MeshCore” to see its neighbours and measurements.")}</p>`; return; }
  const shown = net.centrePk === st.corescope_pk;
  body.innerHTML = `<p>${t("Repeater")}: <b>${escapeHtml(repeaterName(st.corescope_pk))}</b></p>
    <div class="actions"><button data-a="nb" class="primary">${shown ? t("Hide neighbours") : t("Show neighbours")}</button><button data-a="rep">${t("Repeater details")}</button></div>
    ${shown && net.links ? `<p class="muted">${t("{n} neighbours after filters – see workspace “Network”.", { n: net.links.links.length })}</p>` : ""}`;
  body.querySelector('[data-a="nb"]').onclick = () => { if (shown) clearNetworkSelection(); else showNeighbours(st.corescope_pk); renderInspectorTab(); };
  body.querySelector('[data-a="rep"]').onclick = () => inspectRepeater(st.corescope_pk);
}

// ================================================================ repeater
export function inspectRepeater(pk, tab) {
  setFocus(pk);
  const tabs = [{ id: "overview", label: t("Overview"), render: (b) => repeaterOverview(b, pk) }];
  if (meas.allowed) tabs.push({ id: "measurements", label: t("Measurements"), render: (b) => repeaterMeasurements(b, pk) });
  showInspector({ kind: t("Repeater"), key: "repeater:" + pk, title: repeaterName(pk), tabs, tab,
    onClose: () => { if (net.focusPk === pk) setFocus(null); } });
  loadMeta(pk).then(() => { if (inspectorKey() === "repeater:" + pk) renderInspectorTab(); });
}

function repeaterOverview(body, pk) {
  const n = nodeByPk(pk) || linkTo(pk) || {}, meta = net.meta[pk], own = net.overrides[pk] || {}, st = stationOfRepeater(pk), link = linkTo(pk);
  const rows = [[t("Role"), n.role || meta?.node?.role], [t("Last heard"), fmtAgo(n.last_seen || meta?.node?.last_seen)]];
  if (meta && !meta.error) {
    const d = meta.node || {};
    if (meta.rf) rows.push([t("Radio"), Object.entries(meta.rf).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k} ${v}`).join(" · ")]);
    if (d.battery_mv) rows.push([t("Battery"), `${fmt(d.battery_mv / 1000, 2)} V`]);
    if (d.temperature_c != null) rows.push([t("Temperature"), `${fmt(d.temperature_c, 1)} °C`]);
    if (d.relay_count_24h != null) rows.push([t("Relayed (24 h)"), d.relay_count_24h]);
    if (d.advert_count != null) rows.push([t("Adverts"), d.advert_count]);
  }
  if (own.height_m != null || own.gain_dbi != null) rows.push([t("Entered values"), [own.height_m != null ? `${fmt(own.height_m, 0)} m` : "", own.gain_dbi != null ? `${fmt(own.gain_dbi, 1)} dBi` : ""].filter(Boolean).join(" · ")]);
  const isCentre = net.centrePk === pk;
  body.innerHTML = `
    ${link && net.centrePk ? `<div class="calc">${t("Neighbour of {name}", { name: escapeHtml(net.links?.name || repeaterName(net.centrePk)) })}: ${fmt(link.distance_km, 1)} km · ${snrText(link)} · → ${link.they_hear}× · ← ${link.we_hear}×
      <br><a href="#" id="openLink">${t("Link details")}</a></div>` : ""}
    <table class="kv">${rows.filter((r) => r[1] !== undefined && r[1] !== "").map(([k, v]) => `<tr><td>${k}</td><td>${escapeHtml(v)}</td></tr>`).join("")}</table>
    ${!meta ? `<div class="muted wait">${t("loading details")}</div>` : meta.error ? `<div class="warn">${t("Details not available: {msg}", { msg: escapeHtml(meta.error) })}</div>`
      : `<p class="muted">${t("MeshCore does not announce height, antenna and power – enter them below if known.")}</p>`}
    <div class="actions">
      <button data-a="nb" class="primary">${isCentre ? t("Hide neighbours") : t("Show neighbours")}</button>
      ${st ? `<button data-a="station">${t("Open station “{name}”", { name: escapeHtml(st.name) })}</button>` : canEditProject() ? `<button data-a="adopt">${t("Add as station")}</button>` : ""}
    </div>
    ${!st && isAdmin() ? `<h4>${t("Values for the comparison")}</h4><div class="grid2">
      <label>${t("Height (m)")}<input data-o="height_m" type="number" step="1" value="${own.height_m ?? ""}"></label>
      <label>${t("Gain (dBi)")}<input data-o="gain_dbi" type="number" step="0.5" value="${own.gain_dbi ?? ""}"></label></div>
      <button data-a="save">${t("Save")}</button>` : ""}`;
  const on_ = (a, fn) => { const b = body.querySelector(`[data-a="${a}"]`); if (b) b.onclick = fn; };
  on_("nb", async () => { if (isCentre) clearNetworkSelection(); else await showNeighbours(pk); renderInspectorTab(); });
  on_("station", () => inspectStation(st));
  on_("adopt", () => adoptRepeater(pk));
  on_("save", async () => {
    const values = {};
    body.querySelectorAll("[data-o]").forEach((i) => { values[i.dataset.o] = i.value === "" ? null : +i.value; });
    try { await saveOverride(pk, values); status(t("Values saved.")); renderInspectorTab(); } catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
  });
  const ol = body.querySelector("#openLink");
  if (ol) ol.onclick = (e) => { e.preventDefault(); inspectLink(net.centrePk, pk); };
}

function repeaterMeasurements(body, pk) {
  const rows = meas.focusPk === pk ? meas.rows : [];
  if (meas.focusPk !== pk) { body.innerHTML = `<div class="muted wait">${t("loading")}</div>`; return; }
  const bySource = {};
  for (const r of rows) bySource[r.src] = (bySource[r.src] || 0) + 1;
  const snrs = rows.map((r) => r.snr_down).filter((x) => x != null).sort((a, b) => a - b);
  body.innerHTML = rows.length ? `<p>${t("{n} measurements", { n: rows.length })} ${Object.entries(bySource).map(([k, n]) => `${sourceBadge(k)} ${n}`).join(" ")}</p>
      <p class="muted">${t("SNR at the handheld: median {m} dB, best {b} dB. Shown on the map as colour blobs.", { m: fmt(snrs[Math.floor(snrs.length / 2)], 1), b: fmt(snrs[snrs.length - 1], 1) })}</p>
      ${stationOfRepeater(pk) ? `<button data-a="cmp">${t("Compare with the model")}</button>` : ""}`
    : `<p class="muted">${t("No measurements of this repeater yet.")}</p>`;
  const b = body.querySelector('[data-a="cmp"]');
  if (b) b.onclick = () => { emit("compare-station", { stationId: stationOfRepeater(pk).id }); openWorkspace("compare"); };
}

/** Take a CoreScope repeater over as a station of the project (#45: also several at once from "Plan"). */
export async function adoptRepeater(pk, { open = true } = {}) {
  const existing = stationOfRepeater(pk);
  if (existing) { if (open) inspectStation(existing); return existing; }
  const n = nodeByPk(pk) || linkTo(pk);
  if (!n) return null;
  const own = net.overrides[pk] || {};
  const roof = own.height_m == null ? await mastHeightGuess(n.lat, n.lon) : null;
  const st = newStation(n.lat, n.lon);
  Object.assign(st, { name: n.name || st.name, corescope_pk: pk, height_m: own.height_m ?? roof ?? 10, height_ref: "ground",
    notes: (roof != null ? t("Height: roof in the surface model + 1 m.") + " " : "")
      + t("Taken over from CoreScope. Height, antenna and power are assumptions – please check.") });
  if (own.gain_dbi != null) st.antenna = { ...st.antenna, gain_dbi: own.gain_dbi, catalog_id: undefined };
  state.project.stations.push(st); drawStation(st);
  if (net.focusPk === pk) setFocus(null);
  emit("stations-changed"); emit("project-changed", { station: st });
  if (open) {
    inspectStation(st, "settings");
    status(t("“{name}” added – check height, antenna and power, then save.", { name: st.name }));
  }
  return st;
}

// ================================================================ link between two repeaters (#26)
export function inspectLink(centrePk, partnerPk) {
  const link = net.links?.pk === centrePk ? linkTo(partnerPk) : null;
  if (!link) return;
  const centreName = net.links?.name || repeaterName(centrePk);
  const centreStation = stationOfRepeater(centrePk), partnerStation = stationOfRepeater(partnerPk);
  showInspector({ kind: t("Link"), key: `link:${centrePk}:${partnerPk}`, title: `${centreName} ↔ ${link.name || partnerPk.slice(0, 8)}`,
    tabs: [{ id: "overview", label: t("Overview"), render: (body) => {
      const time = (unix) => unix ? `${fmtDateTime(unix)} (${fmtAgo(unix)})` : "–";
      body.innerHTML = `<table class="kv">
        <tr><td>${t("Distance")}</td><td>${fmt(link.distance_km ?? distanceKm(...nodePosition(centrePk), link.lat, link.lon), 2)} km</td></tr>
        <tr><td>${t("{a} heard by {b}", { a: escapeHtml(centreName), b: escapeHtml(link.name || "") })}</td><td>${link.they_hear || 0}×</td></tr>
        <tr><td>${t("{a} heard by {b}", { a: escapeHtml(link.name || ""), b: escapeHtml(centreName) })}</td><td>${link.we_hear || 0}×</td></tr>
        <tr><td>${t("Both ways")}</td><td>${link.bidir ? t("yes") : t("no")}</td></tr>
        <tr><td>${t("SNR at {b}, directly reported", { b: escapeHtml(link.name || "") })}</td><td style="color:${snrColor(link.snr)}">${link.snr != null ? fmt(link.snr, 1) + " dB" : "–"}</td></tr>
        <tr><td>${t("… last heard")}</td><td>${time(link.heard_at)}</td></tr>
        <tr><td>${t("SNR at {a}, directly reported", { a: escapeHtml(centreName) })}</td><td style="color:${snrColor(link.snr_rev)}">${link.snr_rev != null ? fmt(link.snr_rev, 1) + " dB" : "–"}</td></tr>
        <tr><td>${t("… last heard")}</td><td>${time(link.heard_rev_at)}</td></tr>
        <tr><td>${t("SNR from packet paths (average)")}</td><td>${link.snr_any != null ? fmt(link.snr_any, 1) + " dB" : "–"} <span class="muted">${t("uncertain")}</span></td></tr>
        <tr><td>${t("Packets counted")}</td><td>${link.count || "–"}</td></tr>
        <tr><td>${t("Data window")}</td><td>${net.links?.window_days ?? "?"} ${t("days")}</td></tr></table>
        <p class="muted">${t("Directly reported SNR comes from the neighbour table the receiving repeater sends to CoreScope (zero hop). Path values are averages over observers and only hint at the link.")}</p>
        <div class="actions">${centreStation ? `<button data-a="prof" class="primary">${t("Profile")}</button>` : ""}<button data-a="partner">${t("Partner details")}</button></div>
        ${!centreStation ? `<p class="muted">${t("A profile needs the repeater as a station of the project.")}</p>` : ""}`;
      const pr = body.querySelector('[data-a="prof"]');
      if (pr) pr.onclick = () => openLinkProfile(centreStation, partnerStation, link);
      body.querySelector('[data-a="partner"]').onclick = () => inspectRepeater(partnerPk);
    } }] });
  if (centreStation) openLinkProfile(centreStation, partnerStation, link);   // #26: profile right away
}

async function openLinkProfile(centreStation, partnerStation, link) {
  if (partnerStation) { showProfile(centreStation.id, null, { rxStationId: partnerStation.id }); return; }
  const own = net.overrides[link.pk] || {};
  const roof = own.height_m == null && isMember() ? await mastHeightGuess(link.lat, link.lon) : null;
  showProfile(centreStation.id, L.latLng(link.lat, link.lon), { rx: { h: own.height_m ?? roof ?? 10, g: own.gain_dbi ?? 3, c: 1, label: link.name || link.pk.slice(0, 8) }, repeaterPk: link.pk });
}

// ================================================================ measurement ping (#27)
export function inspectPing(ping) {
  const best = ping.rows[0], series = seriesById(best.series_id);
  openPingProfile(ping, best);   // like a link: the profile opens right away
  showInspector({ kind: t("Measurement"), key: "ping:" + ping.key, title: `${sourceName(best.src)} · ${best.ts ? fmtDateTime(best.ts) : ""}`,
    tabs: [{ id: "overview", label: t("Overview"), render: (body) => {
      const kinds = { DISC: t("discovery (DISC, direct answer)"), TRACE: "Trace", TX: t("echo of own ping (TX)"), RX: t("received packet (RX)"), MANUAL: t("manual entry"), CSV: "CSV" };
      const cmp = (r) => meas.comparison?.[r.id];
      body.innerHTML = `
        <h4>${t("Heard by {n} repeater(s)", { n: ping.rows.length })}</h4>
        <div class="ping-reps">${ping.rows.map((r, i) => `<div class="rep"><span><a data-pk="${escapeHtml(r.rep_pk || "")}">${escapeHtml(repeaterName(r.rep_pk, r.rep_prefix))}</a>
          ${r.rep_pk ? `<button class="small" data-prof="${i}" title="${escapeHtml(t("Profile"))}">⛰</button>` : ""}</span>
          <span><b style="color:${snrColor(r.snr_down)}">${r.snr_down != null ? fmt(r.snr_down, 1) + " dB" : "–"}</b>${r.rssi_down != null ? ` · ${fmt(r.rssi_down, 0)} dBm` : ""}${cmp(r)?.delta_db != null && !cmp(r).saturated ? ` · Δ ${signed(cmp(r).delta_db)}` : ""}</span></div>`).join("")}</div>
        <table class="kv" style="margin-top:8px">
          <tr><td>${t("Source")}</td><td>${sourceBadge(best.src)} ${escapeHtml(sourceName(best.src))}</td></tr>
          <tr><td>${t("Kind")}</td><td>${escapeHtml(kinds[best.kind] || best.kind || "–")}</td></tr>
          ${best.snr_up != null ? `<tr><td>${t("SNR at the repeater")}</td><td>${fmt(best.snr_up, 2)} dB</td></tr>` : ""}
          ${best.noise != null ? `<tr><td>${t("Noise floor")}</td><td>${fmt(best.noise, 0)} dBm</td></tr>` : ""}
          ${best.power ? `<tr><td>${t("Transmit power")}</td><td>${escapeHtml(best.power)}</td></tr>` : ""}
          ${best.radio ? `<tr><td>${t("Radio")}</td><td>${escapeHtml(best.radio)}</td></tr>` : ""}
          ${best.alt != null ? `<tr><td>${t("Altitude")}</td><td>${fmt(best.alt, 0)} m</td></tr>` : ""}
          <tr><td>${t("Place")}</td><td>${ping.lat.toFixed(5)}, ${ping.lon.toFixed(5)}</td></tr>
          ${series ? `<tr><td>${t("Series")}</td><td>${escapeHtml(seriesName(series))}<br><span class="muted">${escapeHtml(receiverText(series))}</span></td></tr>` : ""}
          ${best.note ? `<tr><td>${t("Note")}</td><td>${escapeHtml(best.note)}</td></tr>` : ""}
          ${cmp(best)?.predicted_snr != null ? `<tr><td>${t("Model")}</td><td>SNR ${fmt(cmp(best).predicted_snr, 1)} dB · RSSI ${fmt(cmp(best).predicted_rssi, 0)} dBm</td></tr>` : ""}
          <tr><td>${t("Receiver")}</td><td>${escapeHtml(receiverSummary(receiverFor(best, series)))}</td></tr>
          ${isMember() ? `<tr><td>${t("Terrain / surface")}</td><td data-elev><span class="muted">${t("loading")}</span></td></tr>` : ""}
        </table>
        <p class="muted">${t("Tap a repeater name for its details; the profile shows the path from the strongest repeater (tap ⛰ for another one).")}</p>
        <div class="actions">${series ? `<button data-a="series">${t("Show series")}</button>` : ""}${isAdmin() ? `<button data-a="delete" class="danger">${t("Delete measurement")}</button>` : ""}</div>`;
      body.querySelectorAll("[data-pk]").forEach((a) => a.onclick = () => { if (a.dataset.pk) inspectRepeater(a.dataset.pk); });
      body.querySelectorAll("[data-prof]").forEach((b) => b.onclick = () => openPingProfile(ping, ping.rows[+b.dataset.prof]));
      const elev = body.querySelector("[data-elev]");
      if (elev) api("/api/elevation", { method: "POST", body: { project: state.project, lat: ping.lat, lon: ping.lon } })
        .then((r) => { elev.textContent = t("{g} m above sea level · objects {o} m high", { g: fmt(r.ground_m, 1), o: fmt(Math.max(0, r.surface_m - r.ground_m), 1) }); })
        .catch(() => { elev.textContent = "–"; });
      const s = body.querySelector('[data-a="series"]');
      if (s) s.onclick = () => { focusSeries(series.id); openWorkspace("measure"); };
      const del = body.querySelector('[data-a="delete"]');
      if (del) del.onclick = async () => {
        if (!confirm(t("Delete this ping ({n} rows)?", { n: ping.rows.length }))) return;
        try { await api("/api/measurements", { method: "DELETE", body: { ids: ping.rows.map((r) => r.id) } }); closeInspector(); loadMeasurements(); }
        catch (e) { status(t("Error: {msg}", { msg: e.message }), "err"); }
      };
    } }] });
}

const receiverSummary = (r) => `${fmt(r.h, 1)} m · ${fmt(r.g, 1)} dBi${r.c ? ` · −${fmt(r.c, 1)} dB` : ""}`;

/**
 * Profile from the repeater that heard a ping to the place of the ping, with the receiver of its series.
 * A repeater that is not a station of the project is computed as a temporary station (members only):
 * radio values like the last station, height and gain from the values entered for the repeater, else the roof
 * height nearby + 1 m (#43) or 10 m.
 */
async function openPingProfile(ping, row) {
  if (!row?.rep_pk) return;
  const rx = { ...receiverFor(row, seriesById(row.series_id)), label: t("measurement point") };
  const station = stationOfRepeater(row.rep_pk);
  if (station) { showProfile(station.id, L.latLng(ping.lat, ping.lon), { rx }); return; }
  const node = nodeByPk(row.rep_pk) || linkTo(row.rep_pk);
  if (!node || !isMember()) { status(t("A profile needs the repeater as a station of the project.")); return; }
  const own = net.overrides[row.rep_pk] || {};
  const temporary = newStation(node.lat, node.lon);
  const roof = own.height_m == null ? await mastHeightGuess(node.lat, node.lon) : null;
  Object.assign(temporary, { id: "tmp-" + row.rep_pk.slice(0, 12), name: node.name || row.rep_pk.slice(0, 8), corescope_pk: row.rep_pk,
    height_m: own.height_m ?? roof ?? 10, height_ref: "ground" });
  if (own.gain_dbi != null) temporary.antenna = { ...temporary.antenna, gain_dbi: own.gain_dbi, catalog_id: undefined };
  showProfile(temporary.id, L.latLng(ping.lat, ping.lon), { rx, txStation: temporary });
}

// ================================================================ map location (right click / long press)
let locationMarker = null;
export function inspectLocation(latlng) {
  if (locationMarker) map.removeLayer(locationMarker);
  locationMarker = L.circleMarker(latlng, { radius: 6, color: "#111827", weight: 2, fillColor: "#fff", fillOpacity: 1, interactive: false }).addTo(map);
  const tabs = [{ id: "levels", label: t("Levels"), render: (b) => locationLevels(b, latlng) }];
  if (isMember()) tabs.push({ id: "measure", label: t("Enter measurement"), render: (b) => locationMeasurement(b, latlng) });
  showInspector({ kind: t("Location"), key: `loc:${latlng.lat.toFixed(5)},${latlng.lng.toFixed(5)}`, title: `${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`, tabs,
    onClose: () => { if (locationMarker) { map.removeLayer(locationMarker); locationMarker = null; } } });
}

function locationLevels(body, latlng) {
  const req = requiredMargin();
  const stations = state.project.stations.filter((s) => s.enabled !== false);
  body.innerHTML = `<table class="mini"><tr><th>${t("Station")}</th><th>km</th><th>dBm</th><th>${t("Margin")}</th><th></th></tr>
    ${stations.map((s) => {
      const v = sampleStation(s.id, latlng.lat, latlng.lng);
      return `<tr><td class="ellipsis" title="${escapeHtml(s.name)}"><span style="color:${escapeHtml(s.color)}">●</span> ${escapeHtml(s.name)}</td><td>${fmt(distanceKm(s.lat, s.lon, latlng.lat, latlng.lng), 2)}</td>
        <td>${v ? fmt(v.prx, 0) : v === null ? t("outside") : "–"}</td><td class="${v && v.margin >= req ? "ok" : "no"}">${v ? signed(v.margin, 0) : ""}</td>
        <td><button class="small" data-prof="${s.id}">${t("Profile")}</button></td></tr>`;
    }).join("") || `<tr><td colspan="5">${t("No active station.")}</td></tr>`}</table>
    <p class="muted">${t("Line of sight per station in the profile.")}</p>
    ${canEditProject() ? `<div class="actions"><button data-a="place">＋ ${t("Place station here")}</button></div>` : ""}`;
  body.querySelectorAll("[data-prof]").forEach((b) => b.onclick = () => showProfile(b.dataset.prof, latlng));
  const place = body.querySelector('[data-a="place"]');
  if (place) place.onclick = () => {
    const st = newStation(latlng.lat, latlng.lng);
    state.project.stations.push(st); drawStation(st);
    emit("stations-changed"); emit("project-changed", { station: st });
    inspectStation(st, "settings");
  };
}

function locationMeasurement(body, latlng) {
  const near = nearbyNodes(latlng.lat, latlng.lng, 30, 40);
  const d = settings();
  const options = (list) => `<option value="">${t("– not set –")}</option>` + list.map((x) => `<option value="${escapeHtml(x.id)}">${escapeHtml(x.name)}</option>`).join("");
  body.innerHTML = `<p class="muted">${t("Enter a measurement taken here, e.g. from a handheld's neighbour list.")}</p>
    <label>${t("Repeater")}<select data-k="rep">${near.map(({ node, km }) => `<option value="${escapeHtml(node.pk)}">${escapeHtml(node.name)} (${fmt(km, 1)} km)</option>`).join("")}<option value="">${t("other (enter prefix) …")}</option></select></label>
    <label class="${near.length ? "hidden" : ""}" data-other>${t("Hex prefix or key")}<input data-k="rep2" placeholder="a3f1"></label>
    <div class="grid2"><label>${t("SNR at the handheld (dB)")}<input data-k="snr_down" type="number" step="0.25"></label>
      <label>${t("SNR at the repeater (dB, optional)")}<input data-k="snr_up" type="number" step="0.25"></label>
      <label>${t("RSSI (dBm, optional)")}<input data-k="rssi" type="number" step="1"></label></div>
    <h4>${t("Receiver")}</h4>
    <div class="grid2">
      <label>${t("Device")}<select data-k="device_id">${options(state.catalog.devices)}</select></label>
      <label>${t("Placement")}<select data-k="placement_id">${options(state.catalog.placements)}</select></label>
      <label>${t("Antenna height above ground (m)")} *<input data-k="rx_h" type="number" step="0.1" min="0" max="300" required></label>
      <label>${t("Antenna gain (dBi)")}<input data-k="rx_gain_dbi" type="number" step="0.1" placeholder="${fmt(d.rx_gain_dbi, 1)}"></label>
      <label>${t("Cable / body loss (dB)")}<input data-k="rx_cable_db" type="number" step="0.5" min="0" placeholder="${fmt(d.rx_cable_db, 1)}"></label>
      <label>${t("Transmit power (dBm)")}<input data-k="power" type="number" step="1" min="-10" max="40"></label>
    </div>
    <label class="chk"><input data-k="ext_ant" type="checkbox"> ${t("external antenna (e.g. on the car roof)")}</label>
    <label>${t("Note")}<input data-k="note" maxlength="300" placeholder="${escapeHtml(t("e.g. indoors, behind a window, line of sight"))}"></label>
    <p class="muted">${t("Device and placement fill in height, gain and loss; change them if you know better. Entries with the same receiver within 30 minutes form one series.")}</p>
    <div class="actions"><button class="primary" data-a="save">${t("Save")}</button></div><div class="muted" data-out></div>`;
  const field = (k) => body.querySelector(`[data-k="${k}"]`);
  field("rep").onchange = () => body.querySelector("[data-other]").classList.toggle("hidden", !!field("rep").value);
  const fillFromCatalog = () => {
    const r = receiverFor(null, { device_id: field("device_id").value, placement_id: field("placement_id").value });
    const plc = state.catalog.placements.find((x) => x.id === field("placement_id").value);
    const dev = state.catalog.devices.find((x) => x.id === field("device_id").value);
    if (plc?.height_m != null) field("rx_h").value = plc.height_m;
    if (dev || plc?.gain_bonus_db) field("rx_gain_dbi").value = r.g;
    if (dev?.cable_db != null || plc?.extra_loss_db != null) field("rx_cable_db").value = r.c;
  };
  field("device_id").onchange = fillFromCatalog; field("placement_id").onchange = fillFromCatalog;
  body.querySelector('[data-a="save"]').onclick = async () => {
    const out = body.querySelector("[data-out]");
    if (field("rx_h").value === "") { out.textContent = t("Please enter the antenna height above ground."); field("rx_h").focus(); return; }
    const payload = { lat: latlng.lat, lon: latlng.lng, rep: field("rep").value || field("rep2").value.trim(), ext_ant: field("ext_ant").checked };
    for (const k of ["snr_down", "snr_up", "rssi", "rx_h", "rx_gain_dbi", "rx_cable_db", "power"]) if (field(k).value !== "") payload[k] = +field(k).value;
    for (const k of ["device_id", "placement_id", "note"]) if (field(k).value) payload[k] = field(k).value;
    try {
      await api("/api/measurements", { method: "POST", body: payload });
      out.textContent = t("Saved.");
      meas.show.points = true; loadMeasurements();
    } catch (e) { out.textContent = t("Error: {msg}", { msg: e.message }); }
  };
}

// keep an open station/repeater view current
on("coverage-updated", () => { const k = inspectorKey(); if (k && k.startsWith("station:")) renderInspectorTab(); });
on("measurements-loaded", () => { const k = inspectorKey(); if (k && k.startsWith("repeater:")) renderInspectorTab(); });
on("catalog-loaded", () => { const k = inspectorKey(); if (k && k.startsWith("station:")) renderInspectorTab(); });

