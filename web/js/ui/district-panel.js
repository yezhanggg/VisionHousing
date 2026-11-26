/**
 * The district panel: one state legislative district under the current scenario, opened from
 * the district layer (a click, or "Find my district"). It takes the right panel while it is
 * open; Close or Esc gives the panel back.
 *
 * It shows, all from GET /states/ST/districts (one reply per chamber and scenario):
 *   - the headline: homes the switched-on reforms add in the district (the package with two
 *     or more reforms), as the likely range the server wrote, and the same per 1,000 of its
 *     2020 homes;
 *   - the baseline range (no change), each reform's range, and the package;
 *   - each reform's top driver: the first driver sentence of the statewide sensitivity
 *     tornado (`explanation.drivers.<key>.items[0].text`, its sentence in `.text`, of POST /run with drivers, which is
 *     what the reply's `brief` pointers name). With two or more reforms they load on request,
 *     because each one reruns the reform once per uncertain input;
 *   - what the numbers cannot say: the district limits and each reform's limits
 *     (assumptions/limits.yaml, sent with the reply);
 *   - the split-share caveat, the counties the district touches and the plans' vintage.
 *
 * "Print / save PDF" prints the panel alone as a one-page brief (css/district-brief.css). No
 * number is made here: every value is the server's text; the bars only place the server's
 * P10 / P50 / P90.
 */
import "../../css/district-brief.css";
import printPageCss from "../../css/district-print-page.css?url";
import { reformById } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { useRun } from "../lib/data.js";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { Empty, RangeBar, scaleOf, Status } from "./bits.js";
import { CHAMBERS, DISTRICT_COLOURS, setDistricts, useDistrictReply } from "./districts-layer.js";
import { Icon } from "./icons.js";
import { districtView } from "./map-state.js";

const range = (n) => (n?.shown ? `${n.shown.p10} to ${n.shown.p90}` : "—");

/**
 * Print the panel alone as a one-page brief: mark the page (css/district-brief.css), add
 * the US Letter portrait page (css/district-print-page.css) for this print only, open every
 * limits list, print, and undo it all when the print dialog closes.
 */
export function preparePrint() {
  const root = document.documentElement;
  root.dataset.print = "district";
  const page = document.createElement("link");
  page.rel = "stylesheet";
  page.href = printPageCss;
  page.dataset.print = "district";
  const loaded = new Promise((ok) => {
    page.onload = ok;
    page.onerror = ok;
    setTimeout(ok, 800);
  });
  document.head.appendChild(page);
  const closed = [...document.querySelectorAll(".district-brief details:not([open])")];
  closed.forEach((d) => (d.open = true));
  const done = () => {
    delete root.dataset.print;
    page.remove();
    closed.forEach((d) => (d.open = false));
    window.removeEventListener("afterprint", done);
  };
  window.addEventListener("afterprint", done);
  return loaded;
}

export function printBrief() {
  preparePrint().then(() => window.print());
}

/** One reform's top driver sentence (statewide), from its run alone with drivers. */
function DriverLine({ sc, reformKey, load }) {
  const run = useRun({ state: sc.state, reforms: [reformKey], overrides: sc.overrides, preset: sc.preset }, { drivers: true, skip: !load });
  if (!load) return null;
  const top = run.data?.explanation?.drivers?.[reformKey]?.items?.[0];
  const text = typeof top?.text === "string" ? top.text : top?.text?.text;
  if (text) return html`<p class="district-driver">${text}</p>`;
  if (run.loading || run.error) return html`<${Status} state=${run} what="the top driver" lines=${1}>Ranking the drivers… this reruns the reform once per input.<//>`;
  return html`<p class="district-driver is-none">The engine sent no driver for this reform.</p>`;
}

function Limits({ group, open = false }) {
  if (!group?.items?.length) return null;
  return html`<details class="limit-group district-limits" open=${open}>
    <summary>${group.title}<span class="limit-count">${group.items.length}</span></summary>
    <ul class="limits-list">${group.items.map((t) => html`<li>${t}</li>`)}</ul>
  </details>`;
}

export function DistrictPanel({ sc }) {
  const view = districtView.value;
  const reply = useDistrictReply(sc, view.chamber, true);
  const data = reply.data;
  const row = data?.districts?.find((d) => d.id === view.picked);
  const word = CHAMBERS.find(([id]) => id === view.chamber)?.[1] || "";
  const keys = (data?.reforms || []).map((r) => r.key);
  const [drivers, setDrivers] = useState(false);
  useEffect(() => setDrivers(keys.length === 1), [keys.join(",")]);
  const body = useRef(null);
  // Opened from the map or the search: focus moves into the panel.
  useEffect(() => body.current?.focus({ preventScroll: true }), [view.picked]);
  const close = () => setDistricts({ picked: "" });
  const head = row?.headline;
  const bars = row ? scaleOf([...keys.map((k) => row.reforms[k]), row.package]) : [0, 0];
  const titleOf = (k) => data.reforms.find((r) => r.key === k)?.title || reformById(k)?.name || k;
  const actions = html`<button type="button" class="btn btn-quiet btn-xs" onClick=${printBrief} disabled=${!row}
      title="Print this district's one-page brief, or save it as a PDF">
      <${Icon} name="print" size=${15} /><span>Print / PDF</span></button>
    <button type="button" class="btn btn-quiet btn-xs" onClick=${close} title="Close (Esc)">
      <${Icon} name="close" size=${15} /><span>Close</span></button>`;
  // The right panel's frame (ui/dock.js) with its own ids: the mode's panel stays mounted, hidden.
  return html`<section class=${`dock dock-right district-dock ${reply.loading ? "is-busy" : ""}`} data-zone="right"
      aria-labelledby="district-dock-title">
    <header class="dock-head">
      <span class="dock-icon" aria-hidden="true"><${Icon} name="pin" size=${16} /></span>
      <div class="dock-titles">
        <h2 class="dock-title" id="district-dock-title">${row?.name || `${word} district ${view.picked}`}</h2>
        <p class="dock-sub">State ${word.toLowerCase()} district · ${stateName(sc.state)}</p>
      </div>
      <div class="dock-actions">${actions}</div>
      <span class=${`load-bar ${reply.loading ? "is-on" : ""}`} aria-hidden="true"><span></span></span>
    </header>
    <div class="dock-body">
    <article class="district-brief" aria-label="District brief" tabindex="-1" ref=${body}>
      <header class="district-print-head">
        <p class="district-print-kicker">District brief · ${stateName(sc.state)} · state ${word.toLowerCase()}</p>
        <h1 class="district-print-title">${row?.name || view.picked}</h1>
        <p class="district-print-sub">Scenario: ${keys.length ? keys.map(titleOf).join(", ") : "no reform (baseline only)"}</p>
      </header>
      <${Status} state=${reply} what="the district" />
      ${data && !row && html`<${Empty}>No ${word.toLowerCase()} district ${view.picked} in this state.<//>`}
      ${row &&
      html`<section class="district-head" aria-label="Headline">
          ${head
            ? html`<p class="district-label">Homes added over five years · ${head.title}</p>
              <p class="district-big">${range(head.homes)}</p>
              <p class="district-mid">Middle estimate ${head.homes.shown.p50}${head.per_1000?.likely ? html` · <span title=${head.per_1000.formula || ""}>${head.per_1000.likely}</span>` : ""}</p>`
            : html`<p class="district-label">No reform switched on</p>
              <p class="district-mid">Switch a reform on to see the homes it would add in this district.</p>`}
          <p class="district-meta"><span class="key-swatch" style=${`background: var(${DISTRICT_COLOURS[row.class] || "--no-data"})`}></span>
            ${row.units_2020_shown} in 2020 · ${row.tracts} census tracts · ${row.counties} ${row.counties === 1 ? "county" : "counties"}</p>
        </section>
        ${row.caveat &&
        html`<p class="district-caveat" role="note"><span class="key-swatch is-hatch" aria-hidden="true"></span>
          <span><strong>Split-share caveat.</strong> ${row.caveat_text}</span></p>`}
        <section class="district-section" aria-labelledby="district-ranges">
          <h3 class="district-h" id="district-ranges">Likely ranges in this district</h3>
          <table class="district-table">
            <thead><tr><th scope="col">Estimate</th><th scope="col">Likely range (P10 to P90)</th><th scope="col">Middle</th></tr></thead>
            <tbody>
              <tr class="is-base"><th scope="row">Baseline: homes permitted with no change</th><td>${range(row.baseline)}</td><td>${row.baseline.shown.p50}</td></tr>
              ${keys.map((k) => html`<tr><th scope="row">${titleOf(k)}: homes added</th><td>${range(row.reforms[k])}
                  <span class="district-bar"><${RangeBar} label=${titleOf(k)} n=${row.reforms[k]} lo=${bars[0]} hi=${bars[1]} /></span></td>
                <td>${row.reforms[k].shown.p50}</td></tr>`)}
              ${row.package && html`<tr class="is-pkg"><th scope="row">All reforms together: homes added</th><td>${range(row.package)}
                  <span class="district-bar"><${RangeBar} label="All reforms together" n=${row.package} lo=${bars[0]} hi=${bars[1]} /></span></td>
                <td>${row.package.shown.p50}</td></tr>`}
            </tbody>
          </table>
          <p class="district-note">Tract-level reforms are shared out by tract weights, county-level ones (the fee cut, parking, the baseline) by county weights: each district gets its tracts' and counties' results in proportion to its 2020 homes, draw by draw.</p>
        </section>
        ${keys.length > 0 &&
        html`<section class="district-section" aria-labelledby="district-drivers">
          <h3 class="district-h" id="district-drivers">Top driver of each reform (statewide)</h3>
          ${!drivers &&
          html`<button type="button" class="btn btn-quiet btn-xs district-load" onClick=${() => setDrivers(true)}>
            Show each reform's top driver<span class="visually-hidden"> (reruns each reform's sensitivity; slow on a large state)</span></button>`}
          <ul class="district-drivers">
            ${keys.map((k) => html`<li><strong>${titleOf(k)}.</strong> <${DriverLine} sc=${sc} reformKey=${k} load=${drivers} /></li>`)}
          </ul>
        </section>`}
        <section class="district-section is-limits" aria-labelledby="district-limits">
          <h3 class="district-h" id="district-limits">What these numbers cannot tell you</h3>
          <${Limits} group=${data.limits?.districts} open=${true} />
          ${keys.map((k) => html`<${Limits} group=${data.limits?.reforms?.[k]} />`)}
        </section>
        <section class="district-section" aria-labelledby="district-where">
          <h3 class="district-h" id="district-where">Where the district's homes are</h3>
          <p class="district-note">Split share: ${row.split_share_shown} of its 2020 homes are in census tracts it shares with another district${row.caveat ? "" : ` (the caveat starts above ${data.threshold_shown})`}.</p>
          <ul class="district-counties">
            ${row.counties_touched.map((c) => html`<li><span>${c.name}</span><span class="district-share">${c.shown}</span></li>`)}
          </ul>
        </section>
        <footer class="district-foot">
          <p>Plans: ${data.vintage?.plans || "—"}${data.vintage?.outlines ? ` · Outlines: ${data.vintage.outlines}` : ""}${data.vintage?.housing_units ? ` · Homes: ${data.vintage.housing_units}` : ""}.</p>
          ${data.vintage?.note && html`<p>${data.vintage.note}</p>`}
          <p>Run: seed ${data.scenario?.seed}, ${data.scenario?.draws} draws. Every number on this page is the engine's (GET /api/v1/states/${data.state}/districts).</p>
        </footer>`}
    </article>
    </div>
  </section>`;
}
