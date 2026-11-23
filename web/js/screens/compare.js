/**
 * Screen 3, Compare: every reform against the baseline as likely-range bars, the package
 * waterfall (sum of parts → overlap removed → combined) and the table behind both.
 * The table is the text twin of the charts: same rows, same order.
 */
import { REFORMS } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { html } from "../lib/html.js";
import { scenario } from "../state/store.js";
import { runExport, EXPORTS } from "../ui/topbar.js";
import { Card, RangeBar, Value } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";
import { Term } from "../ui/popover.js";

const WATERFALL = ["Sum of parts", "Overlap removed", "Combined package"];
const COLUMNS = ["Reform", "Low", "Middle", "High", "Verdict"];
const NUMERIC = ["Low", "Middle", "High"];

export function CompareScreen() {
  const sc = scenario.value;
  const csv = EXPORTS.find((e) => e.id === "csv");
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page">
      <header class="page-head">
        <p class="kicker">Compare · ${stateName(sc.state)}</p>
        <h1 class="page-title">Which reform does the most?</h1>
        <p class="page-lead">
          Added units over five years against the <${Term} id="baseline">baseline<//>, each shown as a
          <${Term} id="range">likely range<//>. Every bar uses the same scale.
        </p>
      </header>
      <div class="grid-main">
        <${Card} id="bars" title="Each reform against the baseline">
          <p class="sentence is-empty">Results appear here once the engine is connected.</p>
          <ul class="bars">
            ${REFORMS.map(
              (r) => html`<li class=${`bar-row ${sc.reforms.includes(r.id) ? "is-on" : ""}`}>
                <span class="bar-label">${r.name}</span>
                <${RangeBar} label=${r.name} />
                <${Value} />
              </li>`,
            )}
          </ul>
        <//>
        <${Card} id="package" title="The package"
            sub=${html`Reforms that open the same sites <${Term} id="overlap">overlap<//>.`}>
          <ol class="waterfall" aria-label="Package waterfall, values not available yet">
            ${WATERFALL.map(
              (step) => html`<li class="waterfall-step">
                <span class="waterfall-bar" aria-hidden="true"></span>
                <span class="waterfall-label">${step}</span>
                <${Value} />
              </li>`,
            )}
          </ol>
        <//>
      </div>
      <${Card} id="table" title="Table"
          action=${html`<button type="button" class="btn btn-primary" aria-disabled="true"
            onClick=${() => runExport(csv)}><${Icon} name="download" /><span>Download table (CSV)</span></button>`}>
        <div class="table-wrap">
          <table class="table">
            <caption class="visually-hidden">Added units by reform, likely range</caption>
            <thead><tr>${COLUMNS.map((c) => html`<th scope="col" class=${NUMERIC.includes(c) ? "num" : ""}>${c}</th>`)}</tr></thead>
            <tbody>
              ${REFORMS.map(
                (r) => html`<tr>
                  <th scope="row">${r.name}</th>
                  <td class="num"><${Value} /></td><td class="num"><${Value} /></td>
                  <td class="num"><${Value} /></td><td><${Value} /></td>
                </tr>`,
              )}
            </tbody>
          </table>
        </div>
      <//>
    </div>
  </main>`;
}
