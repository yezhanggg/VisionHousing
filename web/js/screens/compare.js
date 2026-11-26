/**
 * Mode 3, Compare. The map shades the counties by all five reforms together.
 *   left panel   every reform against the baseline as likely-range bars on one scale
 *   right panel  the package waterfall and the table behind both, with a CSV download
 *                (a picked county opens there instead)
 * In more words: every reform against the baseline as likely-range bars on one scale,
 * the package waterfall (each reform alone added up, minus lots counted twice, plus savings
 * that feed another reform, equals all five together) and the table behind both, with a
 * CSV download.
 *
 * Below the table, "More comparisons" (folded; drawn and asked for only when opened) holds the
 * interaction accounting (ui/comparisons.js InteractionWaterfall: the reforms alone, each named
 * overlap or saving, the package; last in vs alone) and the ranking check (RankingStability).
 * Below it, "Budget and target (ADUs)" (ui/budget-target.js, folded, asked for only when opened;
 * the ADU Decision tab's link opens it): which tested option reaches a target within a budget,
 * and the demo path.
 *
 * Data: one POST /run of the five benchmark reforms together (each reform is also run alone
 * inside it) and, for Pennsylvania, one run per state bill (a bill reaches the same lots as
 * the ADU reform, so it cannot join the package). The table is the text twin of the charts:
 * same rows, same order, same engine text. The folded cards ask
 * GET /states/ST/comparisons?part=interactions and ?part=ranking (static: only when exported).
 */
import { BENCHMARK_IDS, REFORMS, reformById, reformsFor } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { learn, numberOf, useRun } from "../lib/data.js";
import { downloadCsv } from "../lib/exports.js";
import { html, useEffect } from "../lib/html.js";
import { scenario } from "../state/store.js";
import { ScenarioActions } from "../ui/actions.js";
import { Card, RangeBar, scaleOf, Status } from "../ui/bits.js";
import { CountyPanel } from "../ui/county-panel.js";
import { Dock } from "../ui/dock.js";
import { Ticker } from "../ui/motion.js";
import { Icon } from "../ui/icons.js";
import { Num, Term } from "../ui/popover.js";
import { ComparisonFold, InteractionWaterfall, RankingStability } from "../ui/comparisons.js";
import { BudgetFold } from "../ui/budget-target.js";
import { PackageNotes, statusView } from "../ui/tod-status.js";

const BILL_IDS = REFORMS.filter((r) => r.group === "bill").map((r) => r.id);

/** One row: a reform alone, read from the run that holds it. */
function rowOf(run, key) {
  const added = numberOf(run, `reform_results.${key}.added`);
  const pct = numberOf(run, `reform_results.${key}.pct_change`);
  const verdict = run?.explanation?.verdicts?.[key];
  // The transit status (api/status_view.py): `hidden` rows show no range.
  const view = statusView(run, key);
  const hidden = view && view.shown === false ? view : null;
  return added ? { key, added, pct, verdict, view, hidden } : null;
}

function Bars({ rows, current }) {
  const [lo, hi] = scaleOf(rows.filter((r) => !r.hidden).map((r) => r.added));
  return html`<ul class="bars">
    ${rows.map((r, i) => {
      const name = reformById(r.key)?.name || r.key;
      if (r.hidden) {
        return html`<li class=${`bar-row stagger ${current.includes(r.key) ? "is-on" : ""}`} data-key=${r.key} data-hidden="true" style=${`--i: ${i}`}>
          <span class="bar-label">${name}</span>
          <span class="tod-badge" data-testid="tod-compare-hidden">${r.hidden.hidden_word}</span>
          <${Num} of=${`${name}: homes added, measured contribution`} n=${{ ...r.added, shown: r.hidden.measured.text }} text=${r.hidden.measured.text} />
        </li>`;
      }
      return html`<li class=${`bar-row stagger ${current.includes(r.key) ? "is-on" : ""}`} data-key=${r.key} style=${`--i: ${i}`}>
        <span class="bar-label">${name}${r.view?.partial && html` <span class="tod-badge" title=${r.view.label} data-testid="tod-compare-partial">${r.view.badge}</span>`}</span>
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

export function CompareMode({ map, mapScenario, picked, close }) {
  const sc = scenario.value;
  const pkgScenario = { state: sc.state, reforms: [...BENCHMARK_IDS], overrides: sc.overrides, preset: sc.preset };
  const run = useRun(pkgScenario);
  useEffect(() => learn(run.data), [run.data]);
  // Each state bill is its own run (it cannot join the package). The ids are fixed, so the
  // hooks are always called; a state without bills skips the requests.
  const offered = reformsFor(sc.state).some((r) => r.group === "bill");
  const billRuns = BILL_IDS.map((id) =>
    [id, useRun({ state: sc.state, reforms: [id], overrides: sc.overrides, preset: sc.preset }, { skip: !offered || !run.data })],
  );
  useEffect(() => billRuns.forEach(([, r]) => learn(r.data)), billRuns.map(([, r]) => r.data));
  const bench = [
    ...BENCHMARK_IDS.map((k) => rowOf(run.data, k)),
    ...billRuns.map(([id, r]) => rowOf(r.data, id)),
  ].filter(Boolean);
  const total = run.data?.explanation?.waterfall?.steps?.find((st) => st.kind === "total");
  const best = [...bench].sort((a, b) => (b.added.p50 ?? 0) - (a.added.p50 ?? 0))[0];
  const row = map.data?.counties.find((c) => c.fips === picked);
  return html`<${Dock} side="left" mode="compare" title="Which reform does the most?" icon="compare"
      sub=${`${stateName(sc.state)} · each reform run alone`}
      chip=${best ? `Most: ${reformById(best.key)?.short || best.key}` : ""} busy=${run.loading}>
      <${Status} state=${run} what="the comparison">Running all five reforms… A large state can take a few minutes.<//>
      ${run.data &&
      html`<p class="sentence">${run.data.explanation?.headline?.sentence?.text}</p>
        <p class="card-sub">Homes added over five years against the <${Term} id="baseline">baseline<//>, each as a
          <${Term} id="range">likely range<//> (P10 to P90, the mark is the middle). Every bar uses the same scale.</p>
        <${Bars} rows=${bench} current=${sc.reforms} />`}
    <//>
    <${Dock} side="right" mode=${picked ? "county" : "compare"} title=${picked ? "County" : "All five together"} icon="chart"
      sub=${picked ? "All five reforms together" : "How the package adds up, and the table"}
      chip=${total ? `${total.shown.p10} to ${total.shown.p90}` : ""} busy=${run.loading}
      actions=${html`<${ScenarioActions} compact=${true} label="Slides, export and share" />`}>
      ${picked
        ? html`<${CountyPanel} key=${picked} sc=${mapScenario} fips=${picked} row=${row} ids=${map.data?.ids}
            run=${run.data} onClose=${close} />`
        : run.data &&
          html`${total &&
            html`<div class="lead"><p class="lead-label">All five together, likely range</p>
              <p class="lead-number"><${Ticker} text=${`${total.shown.p10} to ${total.shown.p90}`} /></p></div>`}
          <${PackageNotes} run=${run.data} />
          <${Card} id="package" title="How the package adds up"
              sub=${html`Reforms that open the same lots <${Term} id="overlap">overlap<//>; one reform's savings can help another.`}>
            <${Waterfall} run=${run.data} />
          <//>
          <${Card} id="table" title="Table"
              action=${html`<button type="button" class="btn btn-primary btn-xs" onClick=${() => downloadCsv(pkgScenario)}>
                <${Icon} name="download" size=${15} /><span>Download CSV</span></button>`}>
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
                    if (r.hidden) {
                      return html`<tr data-key=${r.key} data-hidden="true">
                        <th scope="row">${name}<span class="headline-sub">${r.hidden.label}</span></th>
                        <td class="num">${r.hidden.not_shown}</td>
                        <td class="num"><${Num} of=${`${name}: measured contribution`} n=${{ ...r.added, shown: r.hidden.measured.text }} text=${r.hidden.measured.text} /></td>
                        <td class="num">${r.hidden.not_shown}</td>
                        <td class="num">${r.hidden.not_shown}</td>
                        <td>${r.hidden.hidden_word}</td>
                      </tr>`;
                    }
                    return html`<tr data-key=${r.key}>
                      <th scope="row">${name}${r.view?.partial && html`<span class="headline-sub" data-testid="tod-compare-partial-label">${r.view.label}</span>`}</th>
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
          <//>
          <${ComparisonFold} id="cmp-more" title="More comparisons"
              sub="How the reforms add up (last in vs alone), and whether their order holds. Computed when opened; the first time can take about a minute.">
            <${InteractionWaterfall} state=${sc.state} h=${3}
              hidden=${Object.fromEntries(bench.filter((r) => r.hidden).map((r) => [r.key, r.hidden]))} />
            <${RankingStability} state=${sc.state} h=${3} />
          <//>
          <${BudgetFold} state=${sc.state} />`}
    <//>`;
}
