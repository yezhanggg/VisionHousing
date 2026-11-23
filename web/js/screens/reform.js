/**
 * Screen 2, Reform: one reform at a time.
 *   the funnel (eligible sites → physically fit → financially feasible → built in five years),
 *   the top five drivers, the assumptions with Low / Central / High / Custom, the evidence,
 *   and what this cannot tell you.
 * Every value is "—" until the engine is connected.
 */
import { REFORMS, reformById } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { html } from "../lib/html.js";
import { hrefFor, route, scenario, toggleReform } from "../state/store.js";
import { Card, Empty, RangeBar, Value } from "../ui/bits.js";
import { Explain, Term } from "../ui/popover.js";

const FUNNEL = [
  { id: "eligible", label: "Eligible sites", text: "Lots where the reform allows new homes." },
  {
    id: "fit",
    label: "Physically fit",
    text: "Eligible lots with room for the new homes.",
  },
  {
    id: "feasible",
    label: "Financially feasible",
    text: "Fit lots where building would likely pay for itself.",
    term: "feasible",
  },
  {
    id: "built",
    label: "Built in five years",
    text: "Feasible lots where owners likely build, given take-up.",
    term: "takeup",
  },
];

const LEDGER_COLUMNS = ["Assumption", "Low", "Central", "High", "Your choice", "Tag", "Source"];

function ReformPicker({ current }) {
  return html`<nav class="pills" aria-label="Choose a reform">
    ${REFORMS.map(
      (r) => html`<a class="pill" href=${hrefFor("reform", r.id)}
        aria-current=${r.id === current ? "page" : undefined}>${r.short}</a>`,
    )}
  </nav>`;
}

function Funnel() {
  return html`<${Card} id="funnel" title="From eligible sites to homes built"
      sub=${html`Each step keeps part of the one before. See <${Term} id="funnel">funnel<//>.`}>
    <p class="sentence is-empty">Results appear here once the engine is connected.</p>
    <ol class="funnel">
      ${FUNNEL.map(
        (step, i) => html`<li class="funnel-step">
          <span class="funnel-num" aria-hidden="true">${i + 1}</span>
          <div class="funnel-text">
            <p class="funnel-label">
              ${step.term ? html`<${Term} id=${step.term}>${step.label}<//>` : step.label}
            </p>
            <p class="funnel-help">${step.text}</p>
          </div>
          <div class="funnel-value">
            <${Value} size="lg" />
            <${RangeBar} label=${step.label} />
          </div>
          <${Explain} of=${step.label} />
        </li>`,
      )}
    </ol>
  <//>`;
}

export function ReformScreen() {
  const id = route.value.param;
  const reform = reformById(id);
  if (!reform) {
    return html`<main id="main" class="screen screen-page" tabindex="-1">
      <div class="page">
        <header class="page-head">
          <p class="kicker">Reform</p>
          <h1 class="page-title">We do not know that reform</h1>
          <p class="page-lead">Pick one of the benchmark reforms below.</p>
        </header>
        <${ReformPicker} current="" />
      </div>
    </main>`;
  }
  const on = scenario.value.reforms.includes(reform.id);
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page">
      <header class="page-head page-head-split">
        <div>
          <p class="kicker">Reform · ${stateName(scenario.value.state)}</p>
          <h1 class="page-title">${reform.name}</h1>
          <p class="page-lead">${reform.line}</p>
        </div>
        <button type="button" class=${`btn ${on ? "btn-quiet" : "btn-primary"}`}
          aria-pressed=${on ? "true" : "false"} onClick=${() => toggleReform(reform.id)}>
          ${on ? "Included in the scenario" : "Add to the scenario"}
        </button>
      </header>
      <${ReformPicker} current=${reform.id} />
      <div class="grid-main">
        <${Funnel} />
        <div class="stack">
          <${Card} id="drivers" title="Top five drivers"
              sub="Which assumptions move the result most.">
            <${Empty} title="No drivers yet">
              The sensitivity ranking appears here once the engine is connected.
            <//>
          <//>
          <${Card} id="limits" title="What this cannot tell you">
            <${Empty} title="Written with the results">
              The limits for this reform are listed next to its results.
            <//>
          <//>
        </div>
      </div>
      <${Card} id="assumptions" title="Assumptions"
          sub=${html`Each comes from the <${Term} id="ledger">Assumption Ledger<//>, with a sourced range.`}>
        <div class="table-wrap">
          <table class="table">
            <thead><tr>${LEDGER_COLUMNS.map((c) => html`<th scope="col">${c}</th>`)}</tr></thead>
            <tbody>
              <tr>
                <td colspan=${LEDGER_COLUMNS.length} class="table-empty">
                  The assumptions load here once the engine is connected.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      <//>
      <${Card} id="evidence" title="Evidence"
          sub="Where this reform has been tried: predicted against observed.">
        <${Empty} title="No evidence loaded yet">
          The evidence library appears here once the engine is connected.
        <//>
      <//>
    </div>
  </main>`;
}
