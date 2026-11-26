/**
 * Mode 1, State (home). The map shades each county by the homes the scenario adds there.
 *   left panel   "Scenario": state picker, reform switches, Back to baseline, All five reforms,
 *                and under them the folded "Extra scenarios" group (ui/extras.js). A switch that
 *                reaches the same lots as a reform that is on is greyed out with its reason
 *                (the back end refuses that package); "All five reforms" stays the benchmarks.
 *   right panel  "Results", with Slides, Export and Share link in its header and a loading bar
 *                while the engine runs. Tabs (Left / Right arrows move between them):
 *                  Results   one headline number (homes added, a likely range) and the
 *                            engine's sentence; with one reform on, the decision view
 *                            (ui/decision.js: question, answer by year, where and why, what
 *                            to change, use it); then the three key figures (baseline, homes
 *                            added, change), then each reform alone as bars, then the verdict
 *                            per reform (each opens to say why)
 *                  Rankings  counties ranked by homes added, per 1,000 homes or as a share of
 *                            the baseline (top or bottom ten, ui/rankings.js); press one to
 *                            centre it on the map
 *                  Drivers   a reform alone: its top drivers (the sensitivity tornado); several:
 *                            how the package adds up, with links to each reform's drivers
 *                  Limits    what the model cannot tell you, grouped and folding
 *                  Brief     the slide deck of the scenario as live small cards (ui/brief.js)
 *                A picked county replaces the tabs with the county panel (Esc closes it); the
 *                legend's "County table" shows every county as a table (the map's twin).
 * Numbers move from their old to their new text when the scenario changes (ui/motion.js).
 *
 * Data: POST /run (the scenario, with its explanation) and the map reply the shell loaded
 * (GET /states/{st}/map). Every number is the engine's text.
 */
import { BENCHMARK_IDS, reformById, reformsFor } from "../content/reforms.js";
import { STATES, TESTLAND, stateName } from "../content/states.js";
import { api } from "../lib/api.js";
import { learn, numberOf, useJson, useRun } from "../lib/data.js";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import {
  attention,
  backToBaseline,
  callAttention,
  defaultReform,
  hrefFor,
  prefs,
  scenario,
  setPref,
  setReforms,
  startTour,
  toggleReform,
  updateScenario,
} from "../state/store.js";
import { ScenarioActions } from "../ui/actions.js";
import { BaselineCard, HearingButton, ScopeNote } from "../ui/baseline-card.js";
import { Empty, RangeBar, scaleOf, Status, Switch } from "../ui/bits.js";
import { BriefPreview } from "../ui/brief.js";
import { HBars } from "../ui/charts.js";
import { CountyPanel } from "../ui/county-panel.js";
import { CountyTable } from "../ui/county-table.js";
import { DecisionView } from "../ui/decision.js";
import { HiddenRange, hiddenView, PackageNotes, PartialLabel, statusView } from "../ui/tod-status.js";
import { Dock } from "../ui/dock.js";
import { Icon } from "../ui/icons.js";
import { Ticker, useHeightMorph } from "../ui/motion.js";
import { panelProps, Tabs } from "../ui/panel.js";
import { Explain, Num, Term } from "../ui/popover.js";
import { Rankings } from "../ui/rankings.js";
import { ExtraSwitches, OutsideCount, overlapReason, useCatalog } from "../ui/extras.js";

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

/** The left panel's content: the state, the reform switches and the two quick buttons. */
function ScenarioBody() {
  const sc = scenario.value;
  const offered = reformsFor(sc.state);
  const catalog = useCatalog(sc.state);
  const groups = [
    ["Benchmark reforms", offered.filter((r) => r.group === "benchmark")],
    ["Pennsylvania bills", offered.filter((r) => r.group === "bill")],
  ].filter(([, list]) => list.length);
  const allFive = BENCHMARK_IDS.every((id) => sc.reforms.includes(id)) && sc.reforms.length === BENCHMARK_IDS.length;
  const card = useRef(null);
  // "Choose in Scenario" (the results' prompt): open the panel, flash it, focus its first switch.
  const ask = attention.value;
  useEffect(() => {
    if (ask.id !== "scenario") return undefined;
    if (prefs.value.leftMin) setPref({ leftMin: false });
    const t = setTimeout(() => {
      const el = card.current;
      if (!el) return;
      el.classList.remove("is-flash");
      void el.offsetWidth;
      el.classList.add("is-flash");
      el.querySelector(".switch")?.focus();
    }, 80);
    return () => clearTimeout(t);
  }, [ask.n]);
  return html`<div class="scenario-body" ref=${card}>
    <${StatePicker} sc=${sc} />
    ${groups.map(
      ([title, list], gi) => html`<div class="group" role="group" aria-labelledby=${`reforms-label-${gi}`}>
        <p class="field-label" id=${`reforms-label-${gi}`}>${title}</p>
        <div class="switches">
          ${list.map(
            (r, i) => html`<div class="stagger" style=${`--i: ${i}`}><${Switch} on=${sc.reforms.includes(r.id)} label=${r.name} caption=${r.line}
              id=${r.id} onToggle=${() => toggleReform(r.id)}
              reason=${sc.reforms.includes(r.id) ? "" : overlapReason(catalog.blockers(r.id, sc.reforms))} /></div>`,
          )}
        </div>
      </div>`,
    )}
    <${ExtraSwitches} sc=${sc} catalog=${catalog} />
    <div class="dock-foot">
      <button type="button" class="btn btn-quiet btn-xs" onClick=${() => setReforms([...BENCHMARK_IDS])} disabled=${allFive}>
        <span>All five reforms</span></button>
      <button type="button" class="btn btn-quiet btn-xs" onClick=${backToBaseline}
        disabled=${sc.reforms.length === 0 && Object.keys(sc.overrides).length === 0}>
        <${Icon} name="reset" size=${15} /><span>Back to baseline</span>
      </button>
    </div>
  </div>`;
}

/** The reform the map's prompt suggests: the first benchmark. */
function suggestion() {
  const r = reformById(BENCHMARK_IDS[0]);
  return r ? { id: r.id, name: r.name, short: r.short } : null;
}

/** No reform on: say so, and offer the next step. */
function StartPrompt({ text }) {
  const s = suggestion();
  return html`<div class="start-prompt">
    <p class="start-text">${text}</p>
    <div class="start-actions">
      ${s && html`<button type="button" class="btn btn-primary btn-xs" onClick=${() => toggleReform(s.id)}>Switch on ${s.name}</button>`}
      <button type="button" class="btn btn-quiet btn-xs" onClick=${() => setReforms([...BENCHMARK_IDS])}>Switch on all five</button>
      <button type="button" class="btn btn-ghost btn-xs" onClick=${() => callAttention("scenario")}>Choose in Scenario</button>
    </div>
  </div>`;
}

/** One reform's verdict: the word in view, the reason one press away. */
function VerdictRow({ v }) {
  const [open, setOpen] = useState(false);
  const id = `verdict-${v.key}`;
  return html`<li class=${`verdict verdict-col ${open ? "is-open" : ""}`}>
    <button type="button" class="verdict-top" aria-expanded=${open ? "true" : "false"} aria-controls=${id}
        onClick=${() => setOpen(!open)}>
      <span>${reformById(v.key)?.name || v.title}</span>
      <span class="verdict-end"><strong class="verdict-word">${v.word}</strong>
        <${Icon} name="chevronDown" size=${14} /></span>
    </button>
    <div class="verdict-more" id=${id} hidden=${!open}>
      <span class="verdict-why">${v.why.text}</span>
      <a class="verdict-link" href=${hrefFor("reform", v.key)}>Funnel and drivers<${Icon} name="chevronRight" size=${14} /></a>
    </div>
  </li>`;
}

function HeadlineRow({ item, n, hidden = null }) {
  const label = item.label.text;
  const sh = item.shown;
  // A reform whose statewide range is hidden (transit status) shows its measured contribution
  // and "Not shown" instead (api/status_view.py).
  const off = hidden && item.key !== "baseline";
  if (off) {
    const text = item.key === "added" ? hidden.measured.text : hidden.not_shown;
    return html`<div class="headline-row" data-key=${item.key} data-hidden="true">
      <dt class="headline-label"><${Term} id=${TERMS[item.key] || "range"}>${label}<//></dt>
      <dd class="headline-value">
        ${item.key === "added" && n ? html`<${Num} of=${label} n=${{ ...n, shown: text }} text=${text} size="lg" />` : html`<strong>${text}</strong>`}
        <span class="headline-sub">${hidden.label}</span>
      </dd>
    </div>`;
  }
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

const RESULT_TABS = [
  { id: "results", label: "Results" },
  { id: "rankings", label: "Rankings" },
  { id: "drivers", label: "Drivers" },
  { id: "limits", label: "Limits" },
  { id: "brief", label: "Brief" },
];

/** Each reform switched on, run alone, as bars (the engine's middle estimate and range). */
function ByReform({ run, sc }) {
  const items = sc.reforms
    .map((k) => {
      const n = numberOf(run.data, `reform_results.${k}.added`);
      const off = hiddenView(run.data, k);
      if (off && n) return { key: k, label: reformById(k)?.name || k, value: n.p50, text: off.measured.text };
      return n?.shown ? { key: k, label: reformById(k)?.name || k, value: n.p50, text: `${n.shown.p10} to ${n.shown.p90}` } : null;
    })
    .filter(Boolean);
  if (items.length < 2) return null;
  return html`<${HBars} title="Each reform alone: homes added" items=${items}
    note="Bars show the middle estimate; the words give the likely range. Reforms that reach the same lots overlap, so these do not add up to the package." />`;
}

/** The headline: one number, one sentence, then the key figures, a chart, and the details. */
function ResultsTab({ run, sc, map }) {
  const e = run.data?.explanation;
  const items = e?.headline?.numbers || [];
  const verdicts = e?.verdicts || {};
  const lead = items.find((x) => x.key === "added") || items.find((x) => x.key === "baseline");
  const hidden = sc.reforms.length === 1 ? hiddenView(run.data, sc.reforms[0]) : null;
  return html`${!run.data || run.error
      ? html`<${Status} state=${run} what="the results" lines=${4}>Running the engine… A large state can take a minute.<//>`
      : null}
    ${run.data && !e && html`<p class="status-line is-error">The engine sent numbers but no explanation (${run.data.explanation_status}).</p>`}
    ${hidden && html`<${HiddenRange} view=${hidden} />`}
    ${lead && !(hidden && lead.key === "added") &&
    html`<div class="lead">
      <p class="lead-label">${lead.key === "added" ? "Homes added, likely range" : "Baseline homes, middle estimate"}</p>
      <p class="lead-number"><${Ticker} text=${lead.key === "added" ? `${lead.shown.p10} to ${lead.shown.p90}` : lead.shown.p50} live=${true} /></p>
    </div>`}
    ${run.data && sc.reforms.length > 1 && html`<${PackageNotes} run=${run.data} />`}
    ${run.data && sc.reforms.map((id) => html`<${PartialLabel} key=${`partial-${id}`} view=${statusView(run.data, id)} named=${sc.reforms.length > 1} />`)}
    ${e && html`<p class="sentence" data-testid="headline-sentence">${hidden ? hidden.text : e.headline.sentence.text}</p>`}
    ${e && sc.reforms.length === 0 && html`<${StartPrompt} text="Switch a reform on to see how many homes it adds." />`}
    ${e && sc.reforms.length === 1 && html`<${DecisionView} run=${run} map=${map} id=${sc.reforms[0]} compact=${true} />`}
    ${e && sc.reforms.map((id) => html`<${OutsideCount} key=${id} run=${run.data} id=${id} />`)}
    ${e && sc.reforms.map((id) => html`<${ScopeNote} key=${`scope-${id}`} run=${run.data} id=${id} />`)}
    ${e && sc.reforms.length === 1 && items.length > 0 && html`<h2 class="section-head">Key figures</h2>`}
    ${items.length > 0 &&
    html`<dl class="headline">
      ${items.map((item) => html`<${HeadlineRow} item=${item} n=${e.numbers?.[item.ref]} hidden=${hidden} />`)}
    </dl>`}
    ${e && html`<${BaselineCard} state=${sc.state} compact=${true} />`}
    ${e && html`<${ByReform} run=${run} sc=${sc} />`}
    ${Object.keys(verdicts).length > 0 &&
    html`<div class="verdicts">
      <p class="field-label">Verdict by reform, each alone · press one for why</p>
      <ul class="verdict-list">
        ${Object.values(verdicts).map((v) => {
          const off = hiddenView(run.data, v.key);
          return html`<${VerdictRow} key=${v.key} v=${off ? { ...v, word: off.hidden_word, why: { text: off.text } } : v} />`;
        })}
      </ul>
    </div>`}`;
}

/** Top drivers of a reform alone (the sensitivity tornado), or how a package adds up. */
function DriversTab({ run, sc }) {
  const e = run.data?.explanation;
  const one = sc.reforms.length === 1 ? sc.reforms[0] : "";
  const [hot, setHot] = useState(-1);
  if (!sc.reforms.length) return html`<${StartPrompt} text="Switch a reform on to see what drives its result." />`;
  if (!run.data) {
    return html`<${Status} state=${run} what="the drivers">Ranking the drivers… This reruns the reform once per input; a large state can take a few minutes.<//>`;
  }
  const off = one ? hiddenView(run.data, one) : null;
  if (off) return html`<${HiddenRange} view=${off} testid="tod-hidden-drivers" />`;
  const d = one ? e?.drivers?.[one] : null;
  if (one && !d?.items?.length) return html`<${Empty}>No drivers in the engine's reply for this reform.<//>`;
  if (d) {
    const vals = d.items.flatMap((x) => [x.total_low, x.total_high]).concat([d.central_total]);
    const lo = Math.min(0, ...vals);
    const hi = Math.max(0, ...vals);
    const at = (v) => `${(100 * (v - lo)) / (hi - lo || 1)}%`;
    return html`<p class="card-sub">${d.lead.text}</p>
      <ol class="driver-list" onMouseLeave=${() => setHot(-1)}>
        ${d.items.map((x, i) => {
          const a = Math.min(x.total_low, x.total_high);
          const b = Math.max(x.total_low, x.total_high);
          return html`<li class=${`driver-row ${hot === i ? "is-hot" : ""}`} tabindex="0"
              onMouseEnter=${() => setHot(i)} onFocus=${() => setHot(i)} onBlur=${() => setHot(-1)}>
            <p class="driver-label">${x.rank}. ${x.label}</p>
            <div class="tornado-bar" role="img" aria-label=${x.text.text}>
              <span class="tornado-band" style=${`left: ${at(a)}; width: calc(${at(b)} - ${at(a)})`}></span>
              <span class="tornado-mid" style=${`left: ${at(d.central_total)}`}></span>
            </div>
            <p class="driver-text">${x.text.text}</p>
          </li>`;
        })}
      </ol>
      <a class="btn btn-quiet btn-xs" href=${hrefFor("reform", one)}>All drivers and assumptions<${Icon} name="chevronRight" size=${14} /></a>`;
  }
  const w = e?.waterfall;
  return html`<p class="card-sub">Drivers are ranked for one reform at a time. Together, the reforms add up like this:</p>
    ${w?.steps?.length > 0 &&
    html`<ol class="driver-list">
      ${w.steps.map(
        (st) => html`<li class=${`driver-row is-${st.kind}`}>
          <p class="driver-label">${st.label.text}</p>
          <p class="driver-text"><${Num} of=${st.label.text} n=${numberOf(run.data, st.ref) || st}
            text=${st.shown?.p10 !== undefined ? `${st.shown.p10} to ${st.shown.p90}` : st.shown?.value} /></p>
        </li>`,
      )}
    </ol>`}
    <p class="field-label">Drivers of each reform</p>
    <ul class="link-list">
      ${sc.reforms.map((id) => html`<li><a href=${hrefFor("reform", id)}>${reformById(id)?.name || id}</a></li>`)}
    </ul>`;
}

/** What the model cannot tell you: general limits, the baseline's, each reform's. */
function LimitsTab({ run, sc }) {
  const l = run.data?.explanation?.limits;
  if (!l) return html`<${Status} state=${run} what="the limits" />`;
  const groups = [l.general, l.baseline, sc.reforms.length > 1 ? l.package : null, ...sc.reforms.map((id) => l.reforms?.[id])]
    .filter((g) => g?.items?.length);
  return html`${groups.map(
    (g, i) => html`<details class="limit-group" open=${i === 0}>
      <summary>${g.title}<span class="limit-count">${g.items.length}</span></summary>
      <ul class="limits-list">${g.items.map((t) => html`<li>${t.text}</li>`)}</ul>
    </details>`,
  )}`;
}

/** The right panel's tabs. */
function ResultsTabs({ run, map, onOpen }) {
  const sc = scenario.value;
  const e = run.data?.explanation;
  const [tab, setTab] = useState("results");
  // The first reform switched on (or a new tab) changes the body: its height glides.
  const body = useRef(null);
  useHeightMorph(body, `${tab}|${sc.reforms.length > 0}|${Boolean(e)}`);
  // Drivers and the brief ask for the drivers run of a reform alone (as the Slides view does);
  // it is only asked for once one of those tabs is opened.
  const wantsDrivers = tab === "drivers" || tab === "brief";
  const deep = useRun(sc, { drivers: sc.reforms.length === 1, skip: !wantsDrivers });
  const deepRun = deep.data ? deep : run;
  const panel = (id, content) =>
    html`<div ...${panelProps("results", id, tab)}>${tab === id ? content : null}</div>`;
  return html`<div class=${`results-tabs ${run.loading && run.data ? "is-updating" : ""}`} ref=${body}
      aria-busy=${run.loading || (wantsDrivers && deep.loading) ? "true" : "false"}>
    <${Tabs} group="results" label="Results views" tabs=${RESULT_TABS} current=${tab} onChange=${setTab} />
    ${panel("results", html`<${ResultsTab} run=${run} sc=${sc} map=${map} />`)}
    ${panel("rankings", html`<${Rankings} map=${map} onPick=${onOpen} />`)}
    ${panel("drivers", html`<${DriversTab} run=${deep.loading && !deep.data ? deep : deepRun} sc=${sc} />`)}
    ${panel("limits", html`<${LimitsTab} run=${run} sc=${sc} />`)}
    ${panel("brief", html`<${BriefPreview} run=${deep.loading && !deep.data ? deep : deepRun} />`)}
    <div class="dock-foot">
      <${HearingButton} sc=${sc} />
      <a class="btn btn-primary btn-xs" href=${hrefFor("reform", defaultReform())}>
        <span>See the funnel</span><${Icon} name="chevronRight" size=${16} />
      </a>
    </div>
  </div>`;
}

/** The State mode: the scenario on the left, the results (or the open county) on the right. */
export function StateMode({ map, picked, pick, close, table, setTable }) {
  const sc = scenario.value;
  const run = useRun(sc);
  useEffect(() => learn(run.data), [run.data]);
  const e = run.data?.explanation;
  const offered = reformsFor(sc.state).length + useCatalog(sc.state).extras.length;
  const added = e?.headline?.numbers?.find((x) => x.key === "added");
  const row = map.data?.counties.find((c) => c.fips === picked);
  const chip = added ? `${added.shown.p10} to ${added.shown.p90}` : e ? "No reform on" : "";
  let right;
  if (picked) {
    right = html`<${CountyPanel} key=${picked} sc=${sc} fips=${picked} row=${row} ids=${map.data?.ids}
      run=${run.data} onClose=${close} />`;
  } else if (table && map.data) {
    right = html`<${CountyTable} map=${map.data} onOpen=${pick} selected=${picked} />
      <div class="dock-foot"><button type="button" class="btn btn-quiet btn-xs" onClick=${() => setTable(false)}>
        <${Icon} name="chevronLeft" size=${15} /><span>Back to the results</span></button></div>`;
  } else {
    right = html`<${ResultsTabs} run=${run} map=${map} onOpen=${pick} />`;
  }
  return html`<${Dock} side="left" mode="state" title="Scenario" icon="sliders"
      sub=${sc.reforms.length ? `${sc.reforms.length} of ${offered} reforms on` : "Pick a state and switch reforms on."}
      chip=${sc.reforms.length ? `${sc.reforms.length} reforms on` : "Baseline"}
      actions=${html`<button type="button" class="btn btn-quiet btn-xs tour-btn" onClick=${startTour}>Take the tour</button>`}>
      <${ScenarioBody} />
    <//>
    <${Dock} side="right" mode=${picked ? "county" : table ? "table" : "state"} title="Results" icon="chart"
      sub=${`${stateName(sc.state)} · ${e?.span || "the next five years"}`} chip=${chip}
      busy=${run.loading || map.loading} actions=${html`<${ScenarioActions} compact=${true} label="Slides, export and share" />`}>
      ${right}
    <//>`;
}
