/**
 * Screen 1, State (home). A full-width map with floating cards:
 *   left  "Scenario": state picker, reform switches, Back to baseline
 *   right "Results":  one sentence and three numbers, each a likely range, plus a verdict per
 *                     active reform
 * Under 768 px the two cards become one bottom sheet over the map.
 */
import { REFORMS, reformById } from "../content/reforms.js";
import { STATES, stateName } from "../content/states.js";
import { html, useState } from "../lib/html.js";
import {
  backToBaseline,
  defaultReform,
  hrefFor,
  scenario,
  toggleReform,
  updateScenario,
} from "../state/store.js";
import { Empty, RangeBar, Switch, useMedia, Value } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";
import { MapKey, MapView } from "../ui/map.js";
import { Explain, Term } from "../ui/popover.js";

const HEADLINE = [
  { id: "baseline", label: "Baseline, five-year units", term: "baseline" },
  { id: "added", label: "Added units", term: "unit" },
  { id: "change", label: "Change from baseline", term: "range" },
];

function ScenarioCard() {
  const sc = scenario.value;
  return html`<section class="card float-card card-scenario" aria-labelledby="scenario-title">
    <header class="card-head">
      <div>
        <h2 class="card-title" id="scenario-title">Scenario</h2>
        <p class="card-sub">Pick a state and switch reforms on.</p>
      </div>
    </header>
    <div class="card-body">
      <label class="field">
        <span class="field-label">State</span>
        <select class="select" value=${sc.state}
          onChange=${(e) => updateScenario({ state: e.currentTarget.value })}>
          ${STATES.map(([code, name]) => html`<option value=${code}>${name}</option>`)}
        </select>
      </label>
      <div class="group" role="group" aria-labelledby="reforms-label">
        <p class="field-label" id="reforms-label">Reforms</p>
        <div class="switches">
          ${REFORMS.map(
            (r) => html`<${Switch} on=${sc.reforms.includes(r.id)} label=${r.name} caption=${r.line}
              onToggle=${() => toggleReform(r.id)} />`,
          )}
        </div>
      </div>
    </div>
    <footer class="card-foot">
      <button type="button" class="btn btn-quiet" onClick=${backToBaseline}
        disabled=${sc.reforms.length === 0 && Object.keys(sc.overrides).length === 0}>
        <${Icon} name="reset" /><span>Back to baseline</span>
      </button>
    </footer>
  </section>`;
}

function ResultsCard() {
  const sc = scenario.value;
  const active = sc.reforms.map(reformById).filter(Boolean);
  return html`<section class="card float-card card-results" aria-labelledby="results-title">
    <header class="card-head">
      <div>
        <h2 class="card-title" id="results-title">Results</h2>
        <p class="card-sub">${stateName(sc.state)} · the next five years</p>
      </div>
    </header>
    <div class="card-body">
      <p class="sentence is-empty">Results appear here once the engine is connected.</p>
      <dl class="headline">
        ${HEADLINE.map(
          (h) => html`<div class="headline-row">
            <dt class="headline-label"><${Term} id=${h.term}>${h.label}<//></dt>
            <dd class="headline-value">
              <${Value} size="lg" />
              <${RangeBar} label=${h.label} />
            </dd>
            <dd class="headline-explain"><${Explain} of=${h.label} /></dd>
          </div>`,
        )}
      </dl>
      <div class="verdicts">
        <p class="field-label">Verdict by reform</p>
        ${active.length === 0
          ? html`<${Empty}>Switch a reform on to see its verdict.<//>`
          : html`<ul class="verdict-list">
              ${active.map(
                (r) => html`<li class="verdict"><span>${r.name}</span><${Value} /></li>`,
              )}
            </ul>`}
      </div>
    </div>
    <footer class="card-foot">
      <a class="btn btn-primary" href=${hrefFor("reform", defaultReform())}>
        <span>Why: see the funnel</span><${Icon} name="chevronRight" size=${16} />
      </a>
    </footer>
  </section>`;
}

export function StateScreen() {
  const [expanded, setExpanded] = useState(false);
  // On a phone the sheet leads with the results; on a wide screen the scenario card is first
  // (left). The DOM order follows, so the keyboard order matches what is seen.
  const phone = useMedia("(max-width: 767px)");
  return html`<main id="main" class="screen screen-map" tabindex="-1">
    <h1 class="visually-hidden">State: ${stateName(scenario.value.state)}</h1>
    <${MapView} stateName=${stateName(scenario.value.state)} />
    <${MapKey} />
    <div class=${`sheet ${expanded ? "is-expanded" : ""}`}>
      <button type="button" class="sheet-handle" aria-expanded=${expanded ? "true" : "false"}
        onClick=${() => setExpanded(!expanded)}>
        <span class="sheet-grip" aria-hidden="true"></span>
        <span>${expanded ? "Show the map" : "Show all controls"}</span>
      </button>
      <div class="sheet-body">
        ${phone ? html`<${ResultsCard} /><${ScenarioCard} />` : html`<${ScenarioCard} /><${ResultsCard} />`}
      </div>
    </div>
  </main>`;
}
