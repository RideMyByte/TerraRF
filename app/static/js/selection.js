/**
 * #51: what is selected decides what the map shows.
 *
 * Selecting a station or a repeater filters everything to it: only its coverage, the neighbours of its repeater,
 * the measurements of its repeater. Switching from one selection to another replaces all three – nothing of the
 * previous one stays behind. Closing the inspector or clicking into the empty map ends the selection and brings
 * back the layer state from before the first selection.
 */
import { stationById, stationOfRepeater } from "./core/state.js";
import { view, setSolo } from "./map/coverage.js";
import { net, enabled as networkEnabled, showNeighbours, clearNetworkSelection } from "./network.js";
import { meas, focusRepeater } from "./measurements.js";

let before = null;     // layer state before the first selection
let current = null;    // { stationId, pk }

export const selection = () => current;

export function select({ stationId = null, pk = null } = {}) {
  const station = stationId ? stationById(stationId) : pk ? stationOfRepeater(pk) : null;
  const repeater = (pk || station?.corescope_pk || "").toLowerCase() || null;
  if (current && current.stationId === (station?.id || null) && current.pk === repeater) return;
  if (!current) before = { solo: view.soloStationId, centrePk: net.centrePk, focusPk: meas.focusPk };
  current = { stationId: station?.id || null, pk: repeater };
  setSolo(station ? station.id : null);
  if (networkEnabled()) {
    if (repeater) { if (net.centrePk !== repeater) showNeighbours(repeater); }
    else if (net.centrePk) clearNetworkSelection();
  }
  focusRepeater(repeater);
}

export function clearSelection() {
  if (!current) return;
  const back = before || {};
  current = null; before = null;
  setSolo(back.solo || null);
  if (networkEnabled()) {
    if (back.centrePk) { if (net.centrePk !== back.centrePk) showNeighbours(back.centrePk); }
    else if (net.centrePk) clearNetworkSelection();
  }
  focusRepeater(back.focusPk || null);
}
