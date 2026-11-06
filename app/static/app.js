/*
 * Compatibility stub. Up to 0.4.x the whole frontend lived in this file; since 0.5 it is made of ES modules
 * in /static/js/ (entry: js/main.js). A browser or installed app that still has the old index.html cached
 * loads this file – reload once so it fetches the current page. (CI also runs `node --check` on this path.)
 */
(function () {
  "use strict";
  if (document.querySelector('script[type="module"][src*="/static/js/main.js"]')) return;
  const url = new URL(location.href);
  if (url.searchParams.has("fresh")) return;           // already tried once – do not loop
  url.searchParams.set("fresh", Date.now().toString(36));
  location.replace(url.toString());
})();
