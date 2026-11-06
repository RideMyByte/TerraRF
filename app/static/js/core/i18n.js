/**
 * Translations. Source strings in the code are English; /static/locales/<lang>.json maps them to the
 * target language (missing entries fall back to English). Placeholders: t("{n} stations", { n: 3 }).
 *
 * Static markup is translated with attributes: data-i18n (text), data-i18n-title, data-i18n-placeholder.
 */
const STORAGE_KEY = "terrarf.lang";
const SUPPORTED = ["de", "en"];
let dictionary = {};
let current = "en";

export function lang() { return current; }

/** Language: saved choice, else the browser's, else German (the project's home community). */
export function detectLanguage() {
  let saved = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch { /* storage may be blocked */ }
  if (SUPPORTED.includes(saved)) return saved;
  const browser = (navigator.language || "de").slice(0, 2).toLowerCase();
  return SUPPORTED.includes(browser) ? browser : "de";
}

export async function setLanguage(code, { remember = false } = {}) {
  current = SUPPORTED.includes(code) ? code : "en";
  dictionary = {};
  if (current !== "en") {
    try { dictionary = await (await fetch(`/static/locales/${current}.json`, { cache: "no-cache" })).json(); }
    catch { dictionary = {}; }
  }
  if (remember) { try { localStorage.setItem(STORAGE_KEY, current); } catch { /* ignore */ } }
  document.documentElement.lang = current;
  translateDom(document);
}

/** Translate a source string and fill in {placeholders}. */
export function t(text, vars) {
  let out = dictionary[text] ?? text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (vars[k] ?? m));
  return out;
}

export function translateDom(root) {
  root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
}

/** Locale for numbers and dates. */
export function locale() { return current === "de" ? "de-DE" : "en-GB"; }
