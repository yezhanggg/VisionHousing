/**
 * Mode 2, Reform: one reform at a time, run alone against the baseline. The map shades the
 * counties by what this reform alone adds.
 *   left panel   the reform picker, "Add to scenario", the headline (homes added and the
 *                change, one sentence, the verdict) and the funnel
 *                and the binding constraint (the funnel stage that loses most, by who acts)
 *   right panel  tabs: Decision · Drivers · Limits · Assumptions · Evidence · Brief, with
 *                Slides, Export and Share link in its header; a picked county opens there
 *                instead. Decision leads: the five-step briefing (ui/decision.js) and the
 *                paired readouts ("how sure", the cost row); for a benchmark reform, after
 *                the lever and plausibility cards, a folded "How much reform?" section holds
 *                the policy dial (ui/dials.js), drawn and asked for only when opened; Drivers
 *                adds "What would narrow the range" under the tornado.
 * What each part holds:
 *   - the headline: homes added and the change from the baseline, with the verdict;
 *   - the funnel (eligible lots → fit → feasible → built in five years);
 *   - the top drivers: the sensitivity tornado (each input moved alone from low to high);
 *   - what this cannot tell you;
 *   - the assumptions with Range / Low / Central / High / Custom (custom values need the
 *     local app; the static site shows its note instead);
 *   - the evidence: places where a similar rule was tried.
 *
 * Data: POST /run for the reform alone (with the scenario's assumption changes), then the
 * same run with `drivers: true` for the tornado (it reruns the reform once per input, so it
 * comes second); GET /reforms/{id} for the ledger entries; GET /evidence for the cards;
 * GET /states/ST/comparisons?part=dials for the policy dial (static: only when exported).
 */
import { BENCHMARK_IDS, EXTRA_IDS, reformById, reformsFor } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { api, IS_STATIC, LOCAL_ONLY } from "../lib/api.js";
import { learn, numberOf, useJson, useRun } from "../lib/data.js";
import { withUnit } from "../lib/format.js";
import { html, useEffect, useState } from "../lib/html.js";
import { hrefFor, route, scenario, toggleReform, updateScenario } from "../state/store.js";
import { ScenarioActions } from "../ui/actions.js";
import { Card, Empty, RangeBar, scaleOf, Status } from "../ui/bits.js";
import { BriefPreview } from "../ui/brief.js";
import { About } from "../ui/charts.js";
import { BindingCard, DecisionView, HowSure, LeverFlag, Narrowing } from "../ui/decision.js";
import { ImplementationRows } from "../ui/implementation.js";
import { LeverCard } from "../ui/lever.js";
import { PlausibilityCard } from "../ui/plausibility.js";
import { CountyPanel } from "../ui/county-panel.js";
import { CountyTable } from "../ui/county-table.js";
import { Icon } from "../ui/icons.js";
import { Dock } from "../ui/dock.js";
import { Ticker } from "../ui/motion.js";
import { MapFunnelSteps, previewStage, stageOfRow, stagesOf, useFunnelPreview, useTractFunnel } from "../ui/map-funnel.js";
import { panelProps, Tabs } from "../ui/panel.js";
import { Explain, Num, Term } from "../ui/popover.js";
import { PolicyDial } from "../ui/dials.js";
import { ComparisonFold } from "../ui/comparisons.js";
import { OutsideCount, overlapReason, RecordsCard, useCatalog } from "../ui/extras.js";
import { LAW_REFORMS, LawCard, LawTable, LawToggle, lawView, ReadNext } from "../ui/law-changes.js";
import { budgetOpen } from "../ui/budget-target.js";
import { CycleChoice } from "../ui/cycle.js";
import { HiddenRange, hiddenView, PartialLabel, statusView, StatusTable } from "../ui/tod-status.js";

const CHOICES = [
  ["", "Range"],
  ["l", "Low"],
  ["c", "Central"],
  ["h", "High"],
];

function ReformPicker({ current, state, catalog }) {
  const pill = (r) => html`<a class="pill" href=${hrefFor("reform", r.id)} data-reform=${r.id}
    aria-current=${r.id === current ? "page" : undefined}>${r.short}</a>`;
  const extras = catalog.extras;
  return html`<nav class="pills" aria-label="Choose a reform">
    ${reformsFor(state).map(pill)}
  </nav>
  ${extras.length > 0 &&
  html`<details class="extra-group extra-pills" data-testid="extra-scenarios" open=${extras.some((x) => x.id === current)}>
    <summary><span>Extra scenarios</span><span class="limit-count">${extras.length}</span></summary>
    <nav class="pills" aria-label="Choose an extra scenario">${extras.map(pill)}</nav>
  </details>`}`;
}

function Headline({ run, id }) {
  const e = run.data?.explanation;
  // The reform runs alone, so the headline's "added" and "change" are this reform's own
  // numbers; their text matches the sentence above them.
  const head = Object.fromEntries((e?.headline?.numbers || []).map((x) => [x.key, x.shown]));
  const withText = (n, sh) => (n && sh ? { ...n, shown: sh } : n);
  const added = withText(numberOf(run.data, `reform_results.${id}.added`), head.added);
  const pct = withText(numberOf(run.data, `reform_results.${id}.pct_change`), head.pct_change);
  const verdict = e?.verdicts?.[id];
  // The transit status: a hidden statewide range shows the status instead (api/status_view.py).
  const hidden = hiddenView(run.data, id);
  if (hidden) {
    return html`<${Card} id="headline" title="What it adds, alone"
        sub=${html`Against the <${Term} id="baseline">baseline<//>, over ${e?.span || "the next five years"}.`}>
      <${HiddenRange} view=${hidden} />
      <p class="sentence" data-testid="tod-status-text">${hidden.text}</p>
      <dl class="reform-numbers">
        <div><dt>Homes added</dt><dd><${Num} of="Homes added, measured contribution" n=${added && { ...added, shown: hidden.measured.text }}
          size="lg" text=${hidden.measured.text} /></dd></div>
        <div><dt>Change from baseline</dt><dd><strong>${hidden.not_shown}</strong></dd></div>
      </dl>
    <//>`;
  }
  return html`<${Card} id="headline" title="What it adds, alone"
      sub=${html`Against the <${Term} id="baseline">baseline<//>, over ${e?.span || "the next five years"}.`}>
    <${Status} state=${run} what="the results">Running the engine… A large state can take a minute.<//>
    ${added &&
    html`<div class="lead"><p class="lead-label">Homes added, likely range</p>
      <p class="lead-number"><${Ticker} text=${`${added.shown.p10} to ${added.shown.p90}`} live=${true} /></p></div>`}
    <${PartialLabel} view=${statusView(run.data, id)} />
    ${run.data && html`<${LeverFlag} run=${run} id=${id} />`}
    ${e && html`<p class="sentence">${e.headline.sentence.text}</p>`}
    <${OutsideCount} run=${run.data} id=${id} />
    ${e &&
    html`<dl class="reform-numbers">
      <div><dt>Homes added</dt><dd><${Num} of="Homes added" n=${added} size="lg"
        text=${added && `${added.shown.p10} to ${added.shown.p90}`} /><span class="headline-sub">middle ${added?.shown.p50}</span></dd></div>
      <div><dt>Change from baseline</dt><dd><${Num} of="Change from baseline" n=${pct} size="lg"
        text=${pct && `${pct.shown.p10} to ${pct.shown.p90}`} /><span class="headline-sub">middle ${pct?.shown.p50}</span></dd></div>
      ${verdict && html`<div><dt>Verdict</dt><dd><strong class="verdict-word">${verdict.word}</strong>
        <span class="headline-sub">${verdict.why.text}</span></dd></div>`}
    </dl>`}
  <//>`;
}

function Funnel({ run, id, sc }) {
  const f = run.data?.explanation?.funnels?.[id];
  const steps = f?.steps || [];
  // Pointing at a row previews its stage on the map (the other rows dim).
  const stages = stagesOf(useTractFunnel(sc.state, id, sc.overrides).data, id).length;
  const preview = useFunnelPreview(id);
  // Bars compare steps of the same unit only (lots with lots, homes with homes).
  const scales = {};
  for (const u of new Set(steps.map((x) => x.unit))) {
    scales[u] = scaleOf(steps.filter((x) => x.unit === u && x.kind === "range"));
  }
  return html`<${Card} id="funnel" title="From eligible sites to homes built"
      sub=${html`Each step keeps part of the one before. See <${Term} id="funnel">funnel<//>.`}>
    ${!run.data && html`<${Status} state=${run} what="the funnel" />`}
    ${run.data && !f && html`<${Empty}>This reform has no funnel in the engine's reply.<//>`}
    ${f?.central_only && html`<p class="card-sub">Steps show the central assumption values only.</p>`}
    ${statusView(run.data, id)?.partial && html`<p class="tod-partial" data-testid="tod-funnel-label">
      <span class="tod-badge">${statusView(run.data, id).badge}</span> ${statusView(run.data, id).label}</p>`}
    <ol class=${`funnel ${preview >= 0 ? "has-preview" : ""}`} onMouseLeave=${() => previewStage(id, -1)}>
      ${steps.map((step, i) => {
        const n = numberOf(run.data, step.ref) || step;
        const text = step.shown?.p10 !== undefined ? `${step.shown.p10} to ${step.shown.p90}` : step.shown?.value;
        const stage = stageOfRow(i, stages);
        const lit = preview >= 0 && stage === preview;
        return html`<li class=${`funnel-step ${lit ? "is-preview" : ""}`} style=${`--i: ${i}`}
            onMouseEnter=${() => stages && previewStage(id, stage)} onFocusIn=${() => stages && previewStage(id, stage)}
            onFocusOut=${() => previewStage(id, -1)}>
          <span class="funnel-num" aria-hidden="true">${i + 1}</span>
          <div class="funnel-text">
            <p class="funnel-label">${step.label.text}</p>
            <p class="funnel-help">${step.meaning.text}</p>
          </div>
          <div class="funnel-value">
            <${Num} of=${step.label.text} n=${{ ...n, shown: step.shown }} text=${text} />
            ${step.kind === "range" &&
            html`<${RangeBar} label=${step.label.text} n=${step} lo=${scales[step.unit][0]} hi=${scales[step.unit][1]} />`}
          </div>
          <${Explain} of=${step.label.text} n=${{ ...n, shown: step.shown }} />
        </li>`;
      })}
    </ol>
  <//>`;
}

/** The shown text of a cited field in a drivers sentence ("about 29 homes" -> "29"). */
function cited(item, field) {
  return item.text?.cites?.find((c) => c.field === field)?.shown;
}

function Drivers({ run, id }) {
  const off = hiddenView(run.data, id);
  const d = off ? null : run.data?.explanation?.drivers?.[id];
  const items = d?.items || [];
  const vals = items.flatMap((x) => [x.total_low, x.total_high]).concat(d ? [d.central_total] : []);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  // Percent of the track; bands are placed with transform (see RangeBar in ui/bits.js).
  const pc = (v) => ((100 * (v - lo)) / (hi - lo || 1)).toFixed(3);
  const [hot, setHot] = useState(-1);
  return html`<${Card} id="drivers" title="Top drivers" sub="Which inputs move the result most, each moved alone from low to high.">
    ${!run.data && html`<${Status} state=${run} what="the drivers">Ranking the drivers… This reruns the reform once per input; a large state can take a few minutes.<//>`}
    ${off && html`<${HiddenRange} view=${off} testid="tod-hidden-drivers" />`}
    ${run.data && !off && !items.length && html`<${Empty}>No drivers in the engine's reply for this reform.<//>`}
    ${d && html`<${About} label="About this chart"><p>${d.lead.text}</p><p>Point at or focus a row to compare it.</p><//>`}
    ${items.length > 0 &&
    html`<ol class=${`tornado ${hot >= 0 ? "has-hot" : ""}`} onMouseLeave=${() => setHot(-1)}>
      ${items.map((x, i) => {
        const a = Math.min(x.total_low, x.total_high);
        const b = Math.max(x.total_low, x.total_high);
        return html`<li class=${`tornado-row ${hot === i ? "is-hot" : ""}`} tabindex="0" style=${`--i: ${i}`}
            onMouseEnter=${() => setHot(i)} onFocus=${() => setHot(i)} onBlur=${() => setHot(-1)}>
          <p class="tornado-label">${x.rank}. ${x.label}</p>
          <div class="tornado-bar" role="img" aria-label=${x.text.text}>
            <span class="tornado-band is-tf" style=${`--l: ${pc(a)}; --w: ${Math.max(1.5, pc(b) - pc(a))}`}></span>
            <span class="tornado-mid is-tf" style=${`--x: ${pc(d.central_total)}`}></span>
          </div>
          <p class="tornado-ends"><span>At its low value: ${cited(x, "total_low")} homes</span><span>At its high value: ${cited(x, "total_high")} homes</span></p>
          <p class="funnel-help">${x.text.text}</p>
        </li>`;
      })}
    </ol>`}
  <//>`;
}

function Limits({ run, id }) {
  const e = run.data?.explanation;
  const mine = e?.limits?.reforms?.[id];
  const general = e?.limits?.general;
  const notes = e?.reform_notes?.[id] || [];
  return html`<${Card} id="limits" title="What the model cannot tell you">
    ${!e && html`<${Status} state=${run} what="the limits" />`}
    ${mine && html`<ul class="limits-list">${mine.items.map((t) => html`<li>${t.text}</li>`)}</ul>`}
    ${general && html`<p class="field-label">${general.title}</p>
      <ul class="limits-list">${general.items.map((t) => html`<li>${t.text}</li>`)}</ul>`}
    ${notes.length > 0 && html`<p class="field-label">Notes on this reform</p>
      <ul class="limits-list">${notes.map((t) => html`<li>${t.text}</li>`)}</ul>`}
  <//>`;
}

function setOverride(pid, value) {
  const next = { ...scenario.value.overrides };
  if (value === "" || value === null) delete next[pid];
  else next[pid] = value;
  updateScenario({ overrides: next });
}

function ChoiceCell({ p }) {
  const mine = scenario.value.overrides[p.id];
  const [custom, setCustom] = useState(mine && !"lch".includes(mine) ? mine : "");
  const fixed = p.low === p.high;
  if (fixed) return html`<span class="tag">Fixed</span>`;
  if (IS_STATIC) return html`<span class="tag">Range</span>`;
  const isCustom = mine !== undefined && !["l", "c", "h"].includes(mine);
  return html`<div class="choice" role="group" aria-label=${`Value for ${p.label}`}>
    ${CHOICES.map(
      ([v, label]) => html`<button type="button" class="seg" aria-pressed=${(mine || "") === v && !isCustom ? "true" : "false"}
        onClick=${() => setOverride(p.id, v)}>${label}</button>`,
    )}
    <form class="custom" onSubmit=${(e) => {
      e.preventDefault();
      if (/^-?\d{1,9}(\.\d{1,6})?$/.test(custom.trim())) setOverride(p.id, String(Number(custom.trim())));
    }}>
      <label class="visually-hidden" for=${`custom-${p.id}`}>Custom value for ${p.label}, in ${p.unit}</label>
      <input id=${`custom-${p.id}`} class="input input-xs" inputmode="decimal" placeholder="Custom"
        value=${custom} onInput=${(e) => setCustom(e.currentTarget.value.slice(0, 16))} />
      <button type="submit" class=${`seg ${isCustom ? "is-on" : ""}`} aria-pressed=${isCustom ? "true" : "false"}>Set custom</button>
    </form>
  </div>`;
}

function Assumptions({ id, run }) {
  const family = reformById(id)?.family || id;
  const detail = useJson(`reform:${id}`, () => api.reform(id));
  const shown = run.data?.explanation?.assumptions || {};
  const overrides = scenario.value.overrides;
  const params = detail.data?.parameters || [];
  const ranged = params.filter((p) => p.low !== p.high);
  const yours = Object.keys(overrides).length;
  return html`<${Card} id="assumptions" title="Assumptions"
      sub=${html`Each comes from the <${Term} id="ledger">Assumption Ledger<//> with a sourced low, central and high value. By default the model draws each across its range.`}
      action=${yours > 0 &&
      html`<button type="button" class="btn btn-quiet" onClick=${() => updateScenario({ overrides: {} })}>Reset all (${yours})</button>`}>
    ${IS_STATIC &&
    (yours > 0 || (scenario.value.preset || "central") !== "central"
      ? html`<p class="static-inline" role="note" data-testid="assumptions-static-custom">${LOCAL_ONLY} This link changes the assumptions or the housing cycle, so this site shows no result for it; the table below lists the default ranges only.</p>`
      : html`<p class="static-inline" role="note">${LOCAL_ONLY} This site shows the default assumptions.</p>`)}
    <${CycleChoice} />
    <${Status} state=${detail} what="the assumptions" />
    ${detail.data?.definition &&
    html`<div class="definition">
      <p>${detail.data.definition.handbook_text || detail.data.definition.description}</p>
      ${detail.data.definition.handbook_text && html`<p class="card-sub">${detail.data.definition.description}</p>`}
    </div>`}
    ${params.length > 0 &&
    html`<div class="table-wrap">
      <table class="table table-wrapcells">
        <caption class="visually-hidden">Assumptions of ${family}, with low, central and high values</caption>
        <thead><tr>
          <th scope="col">Assumption</th><th scope="col" class="num">Low</th><th scope="col" class="num">Central</th>
          <th scope="col" class="num">High</th><th scope="col">Your choice</th><th scope="col">Tag</th>
        </tr></thead>
        <tbody>
          ${[...ranged, ...params.filter((p) => p.low === p.high)].map((p) => {
            const sh = shown[p.id]?.shown;
            const mine = overrides[p.id] !== undefined;
            return html`<tr class=${mine ? "is-yours" : ""}>
              <th scope="row"><span class="param-label">${p.label}</span>
                <code class="param-id">${p.id}</code>
                ${mine && html`<span class="tag tag-yours">Yours</span>`}</th>
              <td class="num">${sh?.low || withUnit(p.low, p.unit)}</td>
              <td class="num">${sh?.central || withUnit(p.central, p.unit)}</td>
              <td class="num">${sh?.high || withUnit(p.high, p.unit)}</td>
              <td><${ChoiceCell} key=${p.id} p=${p} /></td>
              <td><span class="tag">${p.status}</span> <span class="tag">${p.kind}</span></td>
            </tr>`;
          })}
        </tbody>
      </table>
    </div>`}
  <//>`;
}

function Evidence({ id }) {
  const family = reformById(id)?.family || id;
  const ev = useJson(`evidence:${family}`, () => api.evidence(family));
  const cards = (ev.data?.entries || []).filter((e) => e.used_for.includes("evidence_card") || e.entry_type === "outcome");
  const figures = (list, title) =>
    list.length > 0 &&
    html`<div><p class="field-label">${title}</p><ul class="ev-figures">
      ${list.map((o) => html`<li>${o.measure}: <strong>${withUnit(o.value, o.unit)}</strong> (${o.period})</li>`)}
    </ul></div>`;
  return html`<${Card} id="evidence" title="Evidence" sub="Places where a similar rule was tried, and what was permitted before and after.">
    <${Status} state=${ev} what="the evidence" />
    ${ev.data && !cards.length && html`<${Empty}>No evidence entries for this reform yet.<//>`}
    <div class="ev-grid">
      ${cards.map(
        (c) => html`<article class="ev-card">
          <p class="kicker">${c.date_of_change} · ${c.entry_type.replace("_", " ")}</p>
          <h3 class="ev-title">${c.place}</h3>
          <p class="ev-text">${c.what_changed}</p>
          ${figures(c.before, "Before")}
          ${figures(c.after, "After")}
          ${figures(c.findings, "Findings")}
          ${c.rule_summary && html`<p class="ev-text">${c.rule_summary}</p>`}
          <p class="card-sub"><strong>How far it carries over:</strong> ${c.comparability}</p>
          <p class="card-sub">Checked: ${c.verification.replace("_", " ")}. Sources:
            ${c.sources.map((s, i) => html`${i ? "; " : " "}<a href=${s.url} rel="noopener noreferrer" target="_blank">${s.citation}</a>`)}</p>
        </article>`,
      )}
    </div>
  <//>`;
}

const SIDE_TABS = [
  { id: "decision", label: "Decision" },
  { id: "drivers", label: "Drivers" },
  { id: "limits", label: "Limits" },
  { id: "assumptions", label: "Assumptions" },
  { id: "evidence", label: "Evidence" },
  { id: "brief", label: "Brief" },
];

/**
 * The right panel's tabs. Drivers, Limits and Assumptions stay in the page when hidden
 * (printing shows them all); the evidence and the brief are drawn when their tab is open.
 */
function SideTabs({ run, withDrivers, best, id, map }) {
  const [tab, setTab] = useState("decision");
  const bestRun = { ...run, data: best };
  const hidden = hiddenView(best, id);
  return html`<div class="side-tabs">
    <${Tabs} group="reform-side" label="Reform details" tabs=${SIDE_TABS} current=${tab} onChange=${setTab} />
    <div ...${panelProps("reform-side", "decision", tab)}>
      ${!best && html`<${Status} state=${run} what="the decision view">Running the engine… A large state can take a minute.<//>`}
      <${DecisionView} run=${bestRun} map=${map} id=${id} onTab=${setTab} />
      ${!hidden && html`<${HowSure} run=${bestRun} id=${id} />`}
      ${id === "adu" &&
      html`<p class="bt-link" data-testid="budget-link"><a class="btn btn-quiet btn-xs" href=${hrefFor("compare")}
        onClick=${() => (budgetOpen.value = true)}>Budget and target: which tested option reaches a target within a budget →</a></p>`}
      ${EXTRA_IDS.includes(id) && html`<${RecordsCard} run=${best} id=${id} />`}
      ${hidden && html`<p class="tod-package-note" role="note" data-testid="tod-hidden-cards-note">${hidden.hidden_word}. ${hidden.rule}</p>`}
      ${BENCHMARK_IDS.includes(id) && !hidden && html`<${LeverCard} state=${scenario.value.state} reform=${id} h=${3} />
        <${PlausibilityCard} state=${scenario.value.state} reform=${id} h=${3} />
        <${ComparisonFold} key=${`dial-${id}`} id="how-much" title="How much reform?"
            sub="Move the policy dial: homes added at each level the engine ran. Computed when opened; the first time can take about a minute.">
          <${PolicyDial} state=${scenario.value.state} reform=${id} h=${3} />
        <//>`}
      ${(BENCHMARK_IDS.includes(id) || ["pa_hb2186", "pa_sb1346"].includes(id)) &&
      html`<${ImplementationRows} state=${scenario.value.state} reform=${BENCHMARK_IDS.includes(id) ? id : "adu"} h=${3} />`}
    </div>
    <div ...${panelProps("reform-side", "drivers", tab)}>
      <${Drivers} run=${withDrivers} id=${id} />
      <${Narrowing} run=${bestRun} id=${id} />
    </div>
    <div ...${panelProps("reform-side", "limits", tab)}><${Limits} run=${{ ...run, data: best }} id=${id} /></div>
    <div ...${panelProps("reform-side", "assumptions", tab)}><${Assumptions} id=${id} run=${{ ...run, data: best }} /></div>
    <div ...${panelProps("reform-side", "evidence", tab)}>${tab === "evidence" && html`<${Evidence} id=${id} />`}</div>
    <div ...${panelProps("reform-side", "brief", tab)}>
      ${tab === "brief" &&
      html`<${Card} id="brief" title="Brief">
        <${BriefPreview} run=${withDrivers.data ? withDrivers : { ...run, data: best }} />
      <//>`}
    </div>
  </div>`;
}

export function ReformMode({ map, mapScenario, picked, pick, close, table, setTable }) {
  const id = route.value.param;
  const sc = scenario.value;
  const reform = reformById(id);
  const catalog = useCatalog(sc.state);
  const body = { state: sc.state, reforms: reform ? [reform.id] : [], overrides: sc.overrides, preset: sc.preset };
  const run = useRun(body, { skip: !reform });
  const withDrivers = useRun(body, { drivers: true, skip: !reform || !run.data });
  useEffect(() => learn(run.data), [run.data]);
  // "What this law changes" stays on across reforms and switches off when the screen closes.
  useEffect(() => () => (lawView.value = { ...lawView.peek(), on: false }), []);
  const lawOn = lawView.value.on && LAW_REFORMS.includes(id);
  const actions = html`<${ScenarioActions} compact=${true} label="Slides, export and share" />`;
  if (!reform) {
    return html`<${Dock} side="left" mode="reform" title="We do not know that reform" icon="funnel" sub="Pick one of the reforms below.">
        <${ReformPicker} current="" state=${sc.state} catalog=${catalog} />
      <//>
      <${Dock} side="right" mode="reform" title="Details" icon="chart" actions=${actions}>
        <${Empty}>Pick a reform on the left.<//>
      <//>`;
  }
  const on = sc.reforms.includes(reform.id);
  // A reform that reaches the same lots as one in the scenario cannot join it (the back end
  // refuses that package); the button says why instead.
  const why = on ? "" : overlapReason(catalog.blockers(reform.id, sc.reforms));
  const best = withDrivers.data || run.data;
  const added = numberOf(best, `reform_results.${reform.id}.added`);
  const row = map.data?.counties.find((c) => c.fips === picked);
  return html`<${Dock} side="left" mode=${`reform-${reform.id}`} title=${reform.name} icon="funnel"
      sub=${`${stateName(sc.state)} · run alone against the baseline`}
      chip=${hiddenView(best, reform.id)?.hidden_word || (added?.shown ? `${added.shown.p10} to ${added.shown.p90}` : reform.short)} busy=${run.loading}>
      <${ReformPicker} current=${reform.id} state=${sc.state} catalog=${catalog} />
      <p class="page-lead">${(reform.group === "extra" && catalog.description(reform.id)) || reform.line}</p>
      <button type="button" class=${`btn btn-xs ${on || why ? "btn-quiet" : "btn-primary"}`}
        aria-pressed=${on ? "true" : "false"} aria-disabled=${why ? "true" : undefined}
        aria-describedby=${why ? "add-why" : undefined} data-testid="add-to-scenario"
        onClick=${why ? undefined : () => toggleReform(reform.id)}>
        ${on ? "In the scenario" : "Add to scenario"}
      </button>
      ${why && html`<p class="switch-why" id="add-why">${why}</p>`}
      ${LAW_REFORMS.includes(reform.id) && html`<${LawToggle} />`}
      ${lawOn && html`<${ReadNext} state=${sc.state} reform=${reform.id} />`}
      <${Headline} run=${{ ...run, data: best }} id=${reform.id} />
      <${StatusTable} view=${statusView(best, reform.id)}
        names=${Object.fromEntries((map.data?.counties || []).map((c) => [c.fips, c.name]))} />
      <${Funnel} run=${{ ...run, data: best }} id=${reform.id} sc=${sc} />
      <${MapFunnelSteps} state=${sc.state} reform=${reform.id} overrides=${sc.overrides} fips=${picked}
        countyName=${row?.name || ""} />
      <${BindingCard} map=${map} id=${reform.id} />
    <//>
    <${Dock} side="right" mode=${picked ? "county" : table ? "table" : "reform"} title=${picked ? "County" : "Decision, and how sure"} icon="chart"
      sub=${picked ? `${reform.name}, alone` : "The answer, why, what to change; drivers and limits"}
      chip=${withDrivers.data ? "Drivers ready" : "Drivers"} busy=${withDrivers.loading} actions=${actions}>
      ${picked && lawOn
        ? html`<${LawCard} key=${`law-${picked}`} state=${sc.state} reform=${reform.id} fips=${picked} onClose=${close} />`
        : table && lawOn
          ? html`<${LawTable} state=${sc.state} reform=${reform.id} onOpen=${pick} selected=${picked} />
            <div class="dock-foot"><button type="button" class="btn btn-quiet btn-xs" onClick=${() => setTable(false)}>
              <${Icon} name="chevronLeft" size=${15} /><span>Back to the decision view</span></button></div>`
        : picked
        ? html`<${CountyPanel} key=${picked} sc=${mapScenario} fips=${picked} row=${row} ids=${map.data?.ids}
            run=${run.data} onClose=${close} funnel=${false} />`
        : table && map.data
          ? html`<${CountyTable} map=${map.data} onOpen=${pick} selected=${picked} />
            <div class="dock-foot"><button type="button" class="btn btn-quiet btn-xs" onClick=${() => setTable(false)}>
              <${Icon} name="chevronLeft" size=${15} /><span>Back to the decision view</span></button></div>`
          : html`<${SideTabs} run=${run} withDrivers=${withDrivers} best=${best} id=${reform.id} map=${map} />`}
    <//>`;
}
