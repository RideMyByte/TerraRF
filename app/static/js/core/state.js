/**
 * Shared application state and a tiny event bus.
 *
 * Events (emit/on):
 *   project-loaded            a project was opened or created
 *   project-changed  {station} something in the project changed (marks it dirty)
 *   stations-changed          list of stations changed (added, removed, linked)
 *   coverage-updated          new coverage results are available
 *   selection-changed         selected station changed
 *   network-loaded            CoreScope nodes loaded
 *   network-focus  {pk}       repeater focus changed (pk may be null)
 *   catalog-loaded            antenna/device/placement catalog loaded
 *   workspace  {name}         a workspace was opened
 */
export const state = {
  config: null,          // /api/config
  role: null,            // "admin" | "user" | null
  project: null,         // {id, name, public, stations: [...], view}
  dirty: false,
  selectedStationId: null,
  catalog: { antennas: [], devices: [], placements: [] },
};

const listeners = {};
export function on(event, fn) { (listeners[event] ||= []).push(fn); }
export function emit(event, payload) { for (const fn of listeners[event] || []) { try { fn(payload); } catch (e) { console.error(event, e); } } }

export const isAdmin = () => state.role === "admin";
export const isUser = () => state.role === "user";
/** Admin and user may add stations and enter measurements. */
export const canEditAny = () => isAdmin() || isUser();
/** The user role may only change stations it created itself. */
export const canEditStation = (station) => isAdmin() || (isUser() && station && station.owner === "user");

export const stationById = (id) => (state.project?.stations || []).find((s) => s.id === id);
export const stationOfRepeater = (pk) => (state.project?.stations || []).find((s) => s.corescope_pk === pk);
export const activeStations = () => (state.project?.stations || []).filter((s) => s.enabled !== false);

/** Global model settings (read-only for everybody but the admin, who edits them in Administration). */
export const settings = () => state.config?.defaults || {};
