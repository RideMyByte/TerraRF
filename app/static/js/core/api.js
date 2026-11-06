/** Talking to the server. */
import { state, canEditAny } from "./state.js";

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

/** JSON request; throws ApiError with the server's detail message. */
export async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const response = await fetch(path, opts);
  let data = null;
  try { data = await response.json(); } catch { /* empty body */ }
  if (!response.ok) throw new ApiError((data && data.detail) || `HTTP ${response.status}`, response.status);
  return data;
}

/**
 * Like api(), but rides out reverse-proxy timeouts (502/503/504): the server keeps computing and caches the
 * result, so a later attempt gets it at once. onWait(attempt) is called before each retry.
 */
export async function apiPatient(path, options, onWait) {
  for (let attempt = 1; ; attempt++) {
    try { return await api(path, options); }
    catch (e) {
      if (![502, 503, 504].includes(e.status) || attempt > 30) throw e;
      if (onWait) onWait(attempt);
      await new Promise((resolve) => setTimeout(resolve, 10000));
      if (options.body && options.body.force) options = { ...options, body: { ...options.body, force: [] } };
    }
  }
}

/** Members send the project as it is in the editor; visitors reference the saved public project. */
export function projectRef() {
  return canEditAny() ? { project: state.project } : { project_id: state.project.id };
}

/** POST that returns a file (PDF). */
export async function apiBlob(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    let detail = null;
    try { detail = (await response.json()).detail; } catch { /* not JSON */ }
    throw new ApiError(detail || `HTTP ${response.status}`, response.status);
  }
  const filename = (response.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || "terrarf.pdf";
  return { blob: await response.blob(), filename };
}
