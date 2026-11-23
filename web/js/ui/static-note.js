/**
 * Notes that show only on the static site (built with `npm run build:static`, no back end).
 *
 * - If the site was built before any data was exported (web/public/data/ was empty), it
 *   says so plainly: the screens still draw, but nothing can load.
 * - Only the default-assumption results were exported, so a link that carries custom
 *   assumptions shows "Changing assumptions needs the local app (make app)" instead of
 *   breaking. (The link's `p` value is a slide-deck preset, not an assumption.)
 *
 * On the local app neither note ever shows.
 */
import { DATA_BASE, IS_STATIC, LOCAL_ONLY } from "../lib/api.js";
import { html, useEffect, useState } from "../lib/html.js";
import { scenario } from "../state/store.js";

let exported = null; // one check per page load: Promise<boolean>

function hasData() {
  exported ||= fetch(`${DATA_BASE}/states.json`, { headers: { accept: "application/json" } })
    .then((r) => r.ok && (r.headers.get("content-type") || "").includes("json"))
    .catch(() => false);
  return exported;
}

export function StaticNote() {
  const [ready, setReady] = useState(true);
  useEffect(() => {
    if (IS_STATIC) hasData().then(setReady);
  }, []);
  if (!IS_STATIC) return null;
  if (!ready) {
    return html`<p class="static-note" role="note">The data for this site has not been exported
      yet. Run <code>visionhousing export-static --state PA</code>, then build the site again.</p>`;
  }
  if (Object.keys(scenario.value.overrides).length === 0) return null;
  return html`<p class="static-note" role="note">${LOCAL_ONLY} This site shows the default assumptions.</p>`;
}
