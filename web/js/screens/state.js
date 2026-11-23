/**
 * Screen 1, State (home). A full-width county map with floating cards:
 *   left    "Scenario": state picker, reform switches, Back to baseline, All five reforms
 *   centre  the map key, and either the county table (the map's twin for keyboard and
 *           screen-reader users) or the county panel of the county picked
 *   right   "Results": one sentence and three numbers (baseline, homes added, change),
 *           each a likely range, plus a verdict per switched-on reform
 * Under 768 px the cards become one bottom sheet over the map.
 *
 * Data: POST /run (the scenario, with its explanation), GET /states/{st}/map (each county's
 * class and values) and GET /states/{st}/outlines. Every number is the engine's text.
 */
import { BENCHMARK_IDS, reformById, reformsFor } from "../content/reforms.js";
import { STATES, TESTLAND, stateName } from "../content/states.js";
import { api } from "../lib/api.js";
import { learn, useJson, useMap, useRun } from "../lib/data.js";
import { html, useEffect, useState } from "../lib/html.js";
import {
  backToBaseline,
  defaultReform,
  hrefFor,
  scenario,
  setReforms,
  toggleReform,
  updateScenario,
} from "../state/store.js";
import { Empty, RangeBar, scaleOf, Status, Switch, useMedia } from "../ui/bits.js";
import { CountyPanel } from "../ui/county-panel.js";
import { Icon } from "../ui/icons.js";
import { CLASS_COLOURS, MapKey, MapView } from "../ui/map.js";
import { Explain, Num, Term } from "../ui/popover.js";

const TERMS = { baseline: "baseline", added: "unit", pct_change: "range" };

function StatePicker({ sc }) {
  const list = useJson("states", () => api.states());
  const built = (list.data?.states || []).map((s) => [s.code, s.code === "TL" ? TESTLAND[1] : stateName(s.code)]);
  const options = built.length ? built : STATES;
  const all = options.some(([c]) => c === sc.state) ? options : [...options, [sc.state, stateName(sc.state)]];
  return html`<label class="field">
    <span class="field-label">State</span>
    <select class="select" value=${sc.state} onChange=${(e) => updateScenario({ state: e.currentTarget.value })}>
      ${all.map(([code, name]) => html`<option value=${code}>${name}</option>`)}
    </select>
  </label>`;
}

function ScenarioCard() {
  const sc = scenario.value;
  const offered = reformsFor(sc.state);
  const groups = [
    ["Benchmark reforms", offered.filter((r) => r.group === "benchmark")],
    ["Pennsylvania bills", offered.filter((r) => r.group === "bill")],
  ].filter(([, list]) => list.length);
  const allFive = BENCHMARK_IDS.every((id) => sc.reforms.includes(id)) && sc.reforms.length === BENCHMARK_IDS.length;
  return html`<section class="card float-card card-scenario" aria-labelledby="scenario-title">
    <header class="card-head">
      <div>
        <h2 class="card-title" id="scenario-title">Scenario</h2>
        <p class="card-sub">Pick a state and switch reforms on.</p>
      </div>
    </header>
    <div class="card-body">
      <${StatePicker} sc=${sc} />
      ${groups.map(
        ([title, list], gi) => html`<div class="group" role="group" aria-labelledby=${`reforms-label-${gi}`}>
          <p class="field-label" id=${`reforms-label-${gi}`}>${title}</p>
          <div class="switches">
            ${list.map(
              (r) => html`<${Switch} on=${sc.reforms.includes(r.id)} label=${r.name} caption=${r.line}
                onToggle=${() => toggleReform(r.id)} />`,
            )}
          </div>
        </div>`,
      )}
    </div>
    <footer class="card-foot">
      <button type="button" class="btn btn-quiet" onClick=${() => setReforms([...BENCHMARK_IDS])} disabled=${allFive}>
        <span>All five reforms</span></button>
      <button type="button" class="btn btn-quiet" onClick=${backToBaseline}
        disabled=${sc.reforms.length === 0 && Object.keys(sc.overrides).length === 0}>
        <${Icon} name="reset" /><span>Back to baseline</span>
      </button>
    </footer>
  </section>`;
}

function HeadlineRow({ item, n }) {
  const label = item.label.text;
  const sh = item.shown;
  // The baseline leads with its middle estimate; homes added and the change lead with the range.
  const main = item.key === "baseline" ? sh.p50 : `${sh.p10} to ${sh.p90}`;
  const sub = item.key === "baseline" ? `likely ${sh.p10} to ${sh.p90}` : `middle ${sh.p50}`;
  const [lo, hi] = scaleOf([item]);
  const payload = n ? { ...n, shown: sh } : null;
  return html`<div class="headline-row" data-key=${item.key}>
    <dt class="headline-label"><${Term} id=${TERMS[item.key] || "range"}>${label}<//></dt>
    <dd class="headline-value">
      <${Num} of=${label} n=${payload} text=${main} size="lg" />
      <span class="headline-sub">${sub}</span>
      <${RangeBar} label=${label} n=${item} lo=${lo} hi=${hi} />
    </dd>
    <dd class="headline-explain"><${Explain} of=${label} n=${payload} /></dd>
  </div>`;
}

function ResultsCard({ run }) {
  const sc = scenario.value;
  const e = run.data?.explanation;
  const items = e?.headline?.numbers || [];
  const verdicts = e?.verdicts || {};
  return html`<section class="card float-card card-results" aria-labelledby="results-title" aria-busy=${run.loading ? "true" : "false"}>
    <header class="card-head">
      <div>
        <h2 class="card-title" id="results-title">Results</h2>
        <p class="card-sub">${stateName(sc.state)} · ${e?.span || "the next five years"}</p>
      </div>
    </header>
    <div class="card-body">
      <${Status} state=${run} what="the results">Running the engine… A large state can take a minute.<//>
      ${run.data && !e && html`<p class="status-line is-error">The engine sent numbers but no explanation (${run.data.explanation_status}).</p>`}
      ${e && html`<p class="sentence" data-testid="headline-sentence">${e.headline.sentence.text}</p>`}
      ${items.length > 0 &&
      html`<dl class="headline">
        ${items.map((item) => html`<${HeadlineRow} item=${item} n=${e.numbers?.[item.ref]} />`)}
      </dl>`}
      ${e && sc.reforms.length === 0 && html`<${Empty}>Switch a reform on to see how many homes it adds.<//>`}
      ${Object.keys(verdicts).length > 0 &&
      html`<div class="verdicts">
        <p class="field-label">Verdict by reform, each alone</p>
        <ul class="verdict-list">
          ${Object.values(verdicts).map(
            (v) => html`<li class="verdict verdict-col">
              <span class="verdict-top"><span>${reformById(v.key)?.name || v.title}</span>
                <strong class="verdict-word">${v.word}</strong></span>
              <span class="verdict-why">${v.why.text}</span>
            </li>`,
          )}
        </ul>
      </div>`}
    </div>
    <footer class="card-foot">
      <a class="btn btn-primary" href=${hrefFor("reform", defaultReform())}>
        <span>See the funnel</span><${Icon} name="chevronRight" size=${16} />
      </a>
    </footer>
  </section>`;
}

const SORTS = {
  name: (r) => r.name,
  baseline: (r) => r.baseline?.p50 ?? -Infinity,
  added: (r) => r.added?.p50 ?? -Infinity,
  change: (r) => r.change?.p50 ?? -Infinity,
};

function CountyTable({ map, onOpen, selected }) {
  const [sort, setSort] = useState({ by: "name", up: true });
  const any = map.reforms.length > 0;
  const words = Object.fromEntries(map.classes.map((c) => [c.id, c.word]));
  words.none = "No reform on";
  words.nodata = "No data";
  const rows = [...map.counties].sort((a, b) => {
    const [x, y] = [SORTS[sort.by](a), SORTS[sort.by](b)];
    const c = typeof x === "string" ? x.localeCompare(y) : x - y;
    return sort.up ? c : -c;
  });
  const head = (by, label, num = true) => {
    const on = sort.by === by;
    return html`<th scope="col" class=${num ? "num" : ""} aria-sort=${on ? (sort.up ? "ascending" : "descending") : "none"}>
      <button type="button" class="sort-btn" onClick=${() => setSort({ by, up: on ? !sort.up : by === "name" })}>
        ${label}<span aria-hidden="true">${on ? (sort.up ? " ▲" : " ▼") : ""}</span></button>
    </th>`;
  };
  const ids = map.ids || {};
  return html`<section class="card county-table" aria-labelledby="county-table-title">
    <header class="card-head">
      <div>
        <h2 class="card-title" id="county-table-title">Counties</h2>
        <p class="card-sub">The map's values as a table. Press a county name to open it.</p>
      </div>
    </header>
    <div class="card-body">
      <div class="table-wrap">
        <table class="table">
          <caption class="visually-hidden">Counties, sortable. ${map.measure}.</caption>
          <thead><tr>
            ${head("name", "County", false)}
            ${head("baseline", "Baseline homes")}
            ${any && head("added", "Homes added")}
            ${any && head("change", "Change, middle")}
            <th scope="col">Class</th>
          </tr></thead>
          <tbody>
            ${rows.map(
              (r) => html`<tr class=${r.fips === selected ? "is-picked" : ""} data-fips=${r.fips}>
                <th scope="row"><button type="button" class="link-btn" onClick=${() => onOpen(r.fips)}>${r.name}</button></th>
                <td class="num"><${Num} of=${`${r.name}: baseline homes`} n=${r.baseline && { ...r.baseline, ...ids.baseline }}
                  text=${r.baseline && `${r.baseline.shown.p10} to ${r.baseline.shown.p90}`} /></td>
                ${any &&
                html`<td class="num"><${Num} of=${`${r.name}: homes added`} n=${r.added && { ...r.added, ...ids.added }}
                    text=${r.added && `${r.added.shown.p10} to ${r.added.shown.p90}`} /></td>
                  <td class="num"><${Num} of=${`${r.name}: change from baseline`} n=${r.change && { ...r.change, ...ids.change }}
                    text=${r.change?.shown.p50} /></td>`}
                <td><span class="class-chip"><span class="key-swatch" style=${`background: var(${CLASS_COLOURS[r.class]})`}></span>${words[r.class]}</span></td>
              </tr>`,
            )}
          </tbody>
        </table>
      </div>
    </div>
  </section>`;
}

export function StateScreen() {
  const sc = scenario.value;
  const [expanded, setExpanded] = useState(false);
  const [table, setTable] = useState(false);
  const [picked, setPicked] = useState("");
  const phone = useMedia("(max-width: 767px)");
  const run = useRun(sc);
  const map = useMap(sc);
  const outlines = useJson(`outlines:${sc.state}`, () => api.outlines(sc.state));
  useEffect(() => learn(run.data), [run.data]);
  useEffect(() => setPicked(""), [sc.state]);
  const classes = Object.fromEntries((map.data?.counties || []).map((c) => [c.fips, c.class]));
  const row = map.data?.counties.find((c) => c.fips === picked);
  const open = (fips) => {
    setPicked(fips);
    setExpanded(true);
  };
  const centre = html`<div class="centre-col">
    <${MapKey} map=${map.data} tableOpen=${table} onTable=${() => setTable(!table)} />
    <${Status} state=${map} what="the county map" />
    ${picked &&
    html`<${CountyPanel} key=${picked} sc=${sc} fips=${picked} row=${row} ids=${map.data?.ids}
      run=${run.data} onClose=${() => setPicked("")} />`}
    ${!picked && table && map.data && html`<${CountyTable} map=${map.data} onOpen=${open} selected=${picked} />`}
  </div>`;
  return html`<main id="main" class="screen screen-map" tabindex="-1">
    <h1 class="visually-hidden">State: ${stateName(sc.state)}</h1>
    <${MapView} stateName=${stateName(sc.state)} outlines=${outlines.data} classes=${classes}
      selected=${picked} onSelect=${open} />
    <div class=${`sheet ${expanded ? "is-expanded" : ""}`}>
      <button type="button" class="sheet-handle" aria-expanded=${expanded ? "true" : "false"}
        onClick=${() => setExpanded(!expanded)}>
        <span class="sheet-grip" aria-hidden="true"></span>
        <span>${expanded ? "Show the map" : "Show all controls"}</span>
      </button>
      <div class="sheet-body">
        ${phone
          ? html`<${ResultsCard} run=${run} /><${ScenarioCard} />${centre}`
          : html`<${ScenarioCard} />${centre}<${ResultsCard} run=${run} />`}
      </div>
    </div>
  </main>`;
}
