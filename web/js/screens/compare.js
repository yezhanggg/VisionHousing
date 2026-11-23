/**
 * Screen 3, Compare: every reform against the baseline as likely-range bars on one scale,
 * the package waterfall (each reform alone added up, minus lots counted twice, plus savings
 * that feed another reform, equals all five together) and the table behind both, with a
 * CSV download.
 *
 * Data: one POST /run of the five benchmark reforms together (each reform is also run alone
 * inside it) and, for Pennsylvania, one run per state bill (a bill reaches the same lots as
 * the ADU reform, so it cannot join the package). The table is the text twin of the charts:
 * same rows, same order, same engine text.
 */
import { BENCHMARK_IDS, REFORMS, reformById, reformsFor } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { learn, numberOf, useRun } from "../lib/data.js";
import { downloadCsv } from "../lib/exports.js";
import { html, useEffect } from "../lib/html.js";
import { scenario } from "../state/store.js";
import { Card, RangeBar, scaleOf, Status } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";
import { Num, Term } from "../ui/popover.js";

const BILL_IDS = REFORMS.filter((r) => r.group === "bill").map((r) => r.id);

/** One row: a reform alone, read from the run that holds it. */
function rowOf(run, key) {
  const added = numberOf(run, `reform_results.${key}.added`);
  const pct = numberOf(run, `reform_results.${key}.pct_change`);
  const verdict = run?.explanation?.verdicts?.[key];
  return added ? { key, added, pct, verdict } : null;
}

function Bars({ rows, current }) {
  const [lo, hi] = scaleOf(rows.map((r) => r.added));
  return html`<ul class="bars">
    ${rows.map((r) => {
      const name = reformById(r.key)?.name || r.key;
      return html`<li class=${`bar-row ${current.includes(r.key) ? "is-on" : ""}`} data-key=${r.key}>
        <span class="bar-label">${name}</span>
        <${RangeBar} label=${name} n=${r.added} lo=${lo} hi=${hi} />
        <${Num} of=${`${name}: homes added`} n=${r.added} text=${r.added.shown.p50} />
      </li>`;
    })}
  </ul>`;
}

function Waterfall({ run }) {
  const w = run?.explanation?.waterfall;
  if (!w) return null;
  const [lo, hi] = scaleOf(w.steps);
  return html`<ol class="waterfall" aria-label="Package waterfall">
      ${w.steps.map(
        (st) => html`<li class=${`waterfall-step is-${st.kind}`} data-ref=${st.ref}>
          <span class="waterfall-label">${st.label.text}</span>
          <${RangeBar} label=${st.label.text} n=${st} lo=${lo} hi=${hi} />
          <${Num} of=${st.label.text} n=${{ ...numberOf(run, st.ref), shown: st.shown }}
            text=${`${st.shown.p10} to ${st.shown.p90}`} />
          <span class="headline-sub">middle ${st.shown.p50}</span>
        </li>`,
      )}
    </ol>
    <p class="card-sub">${w.text.text}</p>
    ${w.interactions?.some((x) => x.p50 !== 0) &&
    html`<details class="tract-details"><summary>Savings that feed another reform</summary>
      <ul class="verdict-list">
        ${w.interactions.map(
          (x) => html`<li class="verdict"><span>${x.label.text}</span>
            <${Num} of=${x.label.text} n=${{ ...numberOf(run, x.ref), shown: x.shown }} text=${`${x.shown.p10} to ${x.shown.p90}`} /></li>`,
        )}
      </ul></details>`}`;
}

export function CompareScreen() {
  const sc = scenario.value;
  const pkgScenario = { state: sc.state, reforms: [...BENCHMARK_IDS], overrides: sc.overrides };
  const run = useRun(pkgScenario);
  useEffect(() => learn(run.data), [run.data]);
  // Each state bill is its own run (it cannot join the package). The ids are fixed, so the
  // hooks are always called; a state without bills skips the requests.
  const offered = reformsFor(sc.state).some((r) => r.group === "bill");
  const billRuns = BILL_IDS.map((id) =>
    [id, useRun({ state: sc.state, reforms: [id], overrides: sc.overrides }, { skip: !offered || !run.data })],
  );
  useEffect(() => billRuns.forEach(([, r]) => learn(r.data)), billRuns.map(([, r]) => r.data));
  const bench = [
    ...BENCHMARK_IDS.map((k) => rowOf(run.data, k)),
    ...billRuns.map(([id, r]) => rowOf(r.data, id)),
  ].filter(Boolean);
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page">
      <header class="page-head">
        <p class="kicker">Compare · ${stateName(sc.state)}</p>
        <h1 class="page-title">Which reform does the most?</h1>
        <p class="page-lead">
          Homes added over five years against the <${Term} id="baseline">baseline<//>, each reform run alone and
          shown as a <${Term} id="range">likely range<//> (P10 to P90, the mark is the middle). Every bar uses the same scale.
        </p>
      </header>
      <${Status} state=${run} what="the comparison">Running all five reforms… A large state can take a few minutes.<//>
      ${run.data &&
      html`<div class="grid-main">
        <${Card} id="bars" title="Each reform against the baseline">
          <p class="sentence">${run.data.explanation?.headline?.sentence?.text}</p>
          <${Bars} rows=${bench} current=${sc.reforms} />
        <//>
        <${Card} id="package" title="All five together"
            sub=${html`Reforms that open the same lots <${Term} id="overlap">overlap<//>; one reform's savings can help another.`}>
          <${Waterfall} run=${run.data} />
        <//>
      </div>
      <${Card} id="table" title="Table"
          action=${html`<button type="button" class="btn btn-primary" onClick=${() => downloadCsv(pkgScenario)}>
            <${Icon} name="download" /><span>Download CSV</span></button>`}>
        <div class="table-wrap">
          <table class="table">
            <caption class="visually-hidden">Homes added by reform, each alone: likely range and change from the baseline</caption>
            <thead><tr>
              <th scope="col">Reform</th><th scope="col" class="num">Low (P10)</th><th scope="col" class="num">Middle (P50)</th>
              <th scope="col" class="num">High (P90)</th><th scope="col" class="num">Change, middle</th><th scope="col">Verdict</th>
            </tr></thead>
            <tbody>
              ${bench.map((r) => {
                const name = reformById(r.key)?.name || r.key;
                return html`<tr data-key=${r.key}>
                  <th scope="row">${name}</th>
                  <td class="num"><${Num} of=${`${name}: low`} n=${r.added} text=${r.added.shown.p10} /></td>
                  <td class="num"><${Num} of=${`${name}: middle`} n=${r.added} text=${r.added.shown.p50} /></td>
                  <td class="num"><${Num} of=${`${name}: high`} n=${r.added} text=${r.added.shown.p90} /></td>
                  <td class="num"><${Num} of=${`${name}: change`} n=${r.pct} text=${r.pct?.shown.p50} /></td>
                  <td>${r.verdict?.word || "—"}</td>
                </tr>`;
              })}
            </tbody>
          </table>
        </div>
        <p class="card-sub">The CSV holds every state and county record of the five-reform run, each stamped with the code, data and assumption fingerprints.</p>
      <//>`}
    </div>
  </main>`;
}
