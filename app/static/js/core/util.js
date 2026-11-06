/** Small helpers: DOM, escaping, number/date formatting, geometry. */
import { lang, locale, t } from "./i18n.js";

export const $ = (id) => document.getElementById(id);

export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const randomId = () => Math.random().toString(36).slice(2, 10);

/** Number with fixed decimals in the UI language ("–" for missing values). */
export function fmt(value, decimals = 1) {
  if (value === null || value === undefined || Number.isNaN(+value)) return "–";
  const s = Number(value).toFixed(decimals);
  return lang() === "de" ? s.replace(".", ",") : s;
}
export const signed = (value, decimals = 1) => (value > 0 ? "+" : "") + fmt(value, decimals);

export function fmtBytes(bytes) {
  if (bytes > 1e9) return fmt(bytes / 1e9, 2) + " GB";
  if (bytes > 1e6) return fmt(bytes / 1e6, 1) + " MB";
  return fmt(bytes / 1e3, 0) + " kB";
}

export function ageDays(timestamp) {
  const ms = typeof timestamp === "number" ? timestamp * 1000 : Date.parse(timestamp || "");
  return Number.isNaN(ms) ? 1e9 : (Date.now() - ms) / 864e5;
}

/** "5 min ago" / "vor 5 min"; accepts ISO strings or unix seconds. */
export function fmtAgo(timestamp) {
  const days = ageDays(timestamp);
  if (days > 1e8) return t("unknown");
  if (days < 1 / 24) return t("{n} min ago", { n: fmt(Math.max(0, days * 1440), 0) });
  if (days < 1) return t("{n} h ago", { n: fmt(days * 24, 0) });
  return t("{n} days ago", { n: fmt(days, 0) });
}

export const fmtDateTime = (unixSeconds) => new Date(unixSeconds * 1000).toLocaleString(locale(), { dateStyle: "short", timeStyle: "short" });
export const fmtTime = (unixSeconds) => new Date(unixSeconds * 1000).toLocaleTimeString(locale(), { timeStyle: "short" });

/** Great-circle distance in km. */
export function distanceKm(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const h = Math.sin((lat2 - lat1) * r / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

/** Initial bearing (degrees from north). */
export function bearing(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, p1 = lat1 * r, p2 = lat2 * r, dl = (lon2 - lon1) * r;
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) / r + 360) % 360;
}

/** Point at a distance (m) and bearing from a start point. */
export function destinationPoint(lat, lon, bearingDeg, distanceM) {
  const R = 6371000, b = bearingDeg * Math.PI / 180, d = distanceM / R;
  const la1 = lat * Math.PI / 180, lo1 = lon * Math.PI / 180;
  const la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(b));
  const lo2 = lo1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la1), Math.cos(d) - Math.sin(la1) * Math.sin(la2));
  return [la2 * 180 / Math.PI, lo2 * 180 / Math.PI];
}

/** 1, 2 or 5 times a power of ten close to x (axis steps). */
export function niceStep(x) {
  const p = 10 ** Math.floor(Math.log10(x));
  const n = x / p;
  return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * p;
}

export function hexToRgb(hex) {
  const h = (hex || "#3388ff").replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/** Colour for an SNR value (dB) – same scale everywhere. */
export const snrColor = (snr) => snr == null ? "#9ca3af" : snr >= 5 ? "#16a34a" : snr >= -5 ? "#65a30d" : snr >= -10 ? "#d97706" : "#dc2626";
/** Colour for a measured − predicted delta (dB): green = fits, orange/red = model too optimistic, blue/purple = too pessimistic. */
export const deltaColor = (d) => d == null ? "#9ca3af" : Math.abs(d) <= 3 ? "#16a34a" : Math.abs(d) <= 8 ? (d < 0 ? "#d97706" : "#2563eb") : (d < 0 ? "#dc2626" : "#7c3aed");

/** Download a Blob under a file name. */
export function download(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

/** Build an element from an HTML string. */
export function html(markup) {
  const tpl = document.createElement("template");
  tpl.innerHTML = markup.trim();
  return tpl.content.firstElementChild;
}
