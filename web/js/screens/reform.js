/**
 * Screen 2, Reform: one reform at a time, run alone against the baseline.
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
 * comes second); GET /reforms/{id} for the ledger entries; GET /evidence for the cards.
 */
import { reformById, reformsFor } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { api, IS_STATIC, LOCAL_ONLY } from "../lib/api.js";
import { learn, numberOf, useJson, useRun } from "../lib/data.js";
import { withUnit } from "../lib/format.js";
import { html, useEffect, useState } from "../lib/html.js";
import { hrefFor, route, scenario, toggleReform, updateScenario } from "../state/store.js";
import { Card, Empty, RangeBar, scaleOf, Status } from "../ui/bits.js";
import { Explain, Num, Term } from "../ui/popover.js";

const CHOICES = [
  ["", "Range"],
  ["l", "Low"],
  ["c", "Central"],
  ["h", "High"],
];

function ReformPicker({ current, state }) {
  return html`<nav class="pills" aria-label="Choose a reform">
    ${reformsFor(state).map(
      (r) => html`<a class="pill" href=${hrefFor("reform", r.id)}
        aria-current=${r.id === current ? "page" : undefined}>${r.short}</a>`,
    )}
  </nav>`;
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
  return html`<${Card} id="headline" title="What it adds, alone"
      sub=${html`Against the <${Term} id="baseline">baseline<//>, over ${e?.span || "the next five years"}.`}>
    <${Status} state=${run} what="the results">Running the engine… A large state can take a minute.<//>
    ${e && html`<p class="sentence">${e.headline.sentence.text}</p>`}
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

function Funnel({ run, id }) {
  const f = run.data?.explanation?.funnels?.[id];
  const steps = f?.steps || [];
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
    <ol class="funnel">
      ${steps.map((step, i) => {
        const n = numberOf(run.data, step.ref) || step;
        const text = step.shown?.p10 !== undefined ? `${step.shown.p10} to ${step.shown.p90}` : step.shown?.value;
        return html`<li class="funnel-step">
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
  const d = run.data?.explanation?.drivers?.[id];
  const items = d?.items || [];
  const vals = items.flatMap((x) => [x.total_low, x.total_high]).concat(d ? [d.central_total] : []);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const at = (v) => `${(100 * (v - lo)) / (hi - lo || 1)}%`;
  return html`<${Card} id="drivers" title="Top drivers" sub="Which inputs move the result most (the sensitivity tornado).">
    ${!run.data && html`<${Status} state=${run} what="the drivers">Ranking the drivers… This reruns the reform once per input; a large state can take a few minutes.<//>`}
    ${run.data && !items.length && html`<${Empty}>No drivers in the engine's reply for this reform.<//>`}
    ${d && html`<p class="card-sub">${d.lead.text}</p>`}
    ${items.length > 0 &&
    html`<ol class="tornado">
      ${items.map((x) => {
        const a = Math.min(x.total_low, x.total_high);
        const b = Math.max(x.total_low, x.total_high);
        return html`<li class="tornado-row">
          <p class="tornado-label">${x.rank}. ${x.label}</p>
          <div class="tornado-bar" role="img" aria-label=${x.text.text}>
            <span class="tornado-band" style=${`left: ${at(a)}; width: calc(${at(b)} - ${at(a)})`}></span>
            <span class="tornado-mid" style=${`left: ${at(d.central_total)}`}></span>
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
    ${IS_STATIC && html`<p class="static-inline" role="note">${LOCAL_ONLY} This site shows the default assumptions.</p>`}
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

export function ReformScreen() {
  const id = route.value.param;
  const sc = scenario.value;
  const reform = reformById(id);
  const body = { state: sc.state, reforms: reform ? [reform.id] : [], overrides: sc.overrides };
  const run = useRun(body, { skip: !reform });
  const withDrivers = useRun(body, { drivers: true, skip: !reform || !run.data });
  useEffect(() => learn(run.data), [run.data]);
  if (!reform) {
    return html`<main id="main" class="screen screen-page" tabindex="-1">
      <div class="page">
        <header class="page-head">
          <p class="kicker">Reform</p>
          <h1 class="page-title">We do not know that reform</h1>
          <p class="page-lead">Pick one of the reforms below.</p>
        </header>
        <${ReformPicker} current="" state=${sc.state} />
      </div>
    </main>`;
  }
  const on = sc.reforms.includes(reform.id);
  const best = withDrivers.data || run.data;
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page">
      <header class="page-head page-head-split">
        <div>
          <p class="kicker">Reform · ${stateName(sc.state)}</p>
          <h1 class="page-title">${reform.name}</h1>
          <p class="page-lead">${reform.line}</p>
        </div>
        <button type="button" class=${`btn ${on ? "btn-quiet" : "btn-primary"}`}
          aria-pressed=${on ? "true" : "false"} onClick=${() => toggleReform(reform.id)}>
          ${on ? "In the scenario" : "Add to scenario"}
        </button>
      </header>
      <${ReformPicker} current=${reform.id} state=${sc.state} />
      <div class="grid-main">
        <div class="stack">
          <${Headline} run=${{ ...run, data: best }} id=${reform.id} />
          <${Funnel} run=${{ ...run, data: best }} id=${reform.id} />
        </div>
        <div class="stack">
          <${Drivers} run=${withDrivers} id=${reform.id} />
          <${Limits} run=${{ ...run, data: best }} id=${reform.id} />
        </div>
      </div>
      <${Assumptions} id=${reform.id} run=${{ ...run, data: best }} />
      <${Evidence} id=${reform.id} />
    </div>
  </main>`;
}
