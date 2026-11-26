/**
 * Notes about the link and the build, pinned to the top of the screen area.
 *
 * Link status (live and static): a shared link is never quietly turned into another view.
 * - Parts of the link this version cannot use (an unknown reform, a bad assumption, a link
 *   from a newer version, the retired `p` view preset) are listed in plain words: the codec
 *   reports them and the router keeps the list as `linkDropped` (js/state/store.js).
 * - Static site only: a custom scenario (changed assumptions, or a mix of reforms the export
 *   did not save) gets its own status, "not on this site", with what to do next: open it in the
 *   local app, or switch to the default assumptions with one press. The screens show the same
 *   reason instead of a number; nothing falls back to the default run by itself.
 * - Static site only: data files newer than this page (index `schema_version`) say so; the
 *   controls that need them are off with that reason (lib/api.js `staticSupport`).
 * - Static site only: a site built before any data was exported says so plainly.
 */
import "../../css/baseline-card.css";
import { canShow, DATA_BASE, IS_STATIC, LOCAL_ONLY, staticSupport } from "../lib/api.js";
import { html, useEffect, useState } from "../lib/html.js";
import { linkDropped, scenario, updateScenario } from "../state/store.js";

let exported = null; // one check per page load: Promise<boolean>

function hasData() {
  exported ||= fetch(`${DATA_BASE}/states.json`, { headers: { accept: "application/json" } })
    .then((r) => r.ok && (r.headers.get("content-type") || "").includes("json"))
    .catch(() => false);
  return exported;
}

/** Static site: can this exact scenario be shown, and is the export's layout one this page reads? */
function useStaticStatus(sc) {
  const key = `${sc.state}|${sc.reforms.join(",")}|${JSON.stringify(sc.overrides)}|${sc.preset || "central"}`;
  const [st, setSt] = useState({ key: "", ok: true, reason: "" });
  useEffect(() => {
    if (!IS_STATIC) return undefined;
    let live = true;
    Promise.all([canShow(sc), staticSupport(sc.state)]).then(([ok, support]) => {
      if (live) setSt({ key, ok, reason: support.reason });
    });
    return () => {
      live = false;
    };
  }, [key]);
  return st.key === key ? st : { ok: true, reason: "" };
}

/** The parts of the link that were left out, in plain words. */
function DroppedNote({ items }) {
  if (!items.length) return null;
  return html`<p class="static-note link-note" role="status" data-testid="link-dropped">
    This link had parts this version cannot use, so they were left out: ${items.join("; ")}.
  </p>`;
}

/** Static site: a custom scenario that is not saved here. */
function CustomNote({ sc }) {
  const n = Object.keys(sc.overrides).length;
  const cycle = sc.preset && sc.preset !== "central";
  return html`<div class="static-note link-note is-custom" role="status" data-testid="link-custom">
    <p><strong>Custom scenario, not on this site.</strong> ${`${n
      ? `This link changes ${n} assumption${n === 1 ? "" : "s"} (${Object.keys(sc.overrides).join(", ")}). ${LOCAL_ONLY}`
      : cycle
        ? `This link sets the housing cycle to ${sc.preset}. ${LOCAL_ONLY}`
        : "This mix of reforms was not saved on this site; the local app (make app) runs it."} No result is shown for it here.`}</p>
    ${(n > 0 || cycle) &&
    html`<button type="button" class="btn btn-quiet btn-xs" onClick=${() => updateScenario({ overrides: {}, preset: "central" })}>
      Use the default assumptions instead</button>`}
  </div>`;
}

export function StaticNote() {
  const [ready, setReady] = useState(true);
  const sc = scenario.value;
  const status = useStaticStatus(sc);
  useEffect(() => {
    if (IS_STATIC) hasData().then(setReady);
  }, []);
  if (IS_STATIC && !ready) {
    return html`<p class="static-note" role="note">The data for this site has not been exported
      yet. Run <code>visionhousing export-static --state PA</code>, then build the site again.</p>`;
  }
  const items = linkDropped.value;
  const schema = IS_STATIC && status.reason;
  const custom =
    IS_STATIC && !status.ok && (sc.reforms.length > 0 || Object.keys(sc.overrides).length > 0 || (sc.preset || "central") !== "central");
  if (!items.length && !schema && !custom) return null;
  return html`<div class="link-notes">
    <${DroppedNote} items=${items} />
    ${schema && html`<p class="static-note" role="note" data-testid="schema-note">${status.reason}</p>`}
    ${custom && html`<${CustomNote} sc=${sc} />`}
  </div>`;
}
