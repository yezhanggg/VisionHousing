/**
 * The comparison cards: how the reforms add up, two ADU inputs at once, the method with less
 * data, and whether the order of the reforms holds. (The policy dials are in `dials.js`.)
 *
 *   InteractionWaterfall({state})  sum of the reforms alone, each named mechanism, the package
 *                                  (a waterfall), and the last-in vs alone table behind (i).
 *   TakeupCostMatrix({state})      ADU take-up x construction cost: a 5 x 5 heat grid with the
 *                                  P50 in each cell, P10-P90 on hover or focus, the benchmark
 *                                  cell marked.
 *   AblationCard({state})          tiers 2 / 1 / 0 as likely-range bars per reform, the four
 *                                  measures, and "which missing dataset hurts most".
 *   RankingStability({state})      the reforms in order with their likely ranges, the cases where
 *                                  the order changed, and the paired win shares.
 *
 * Every card: one headline line, one visual, details behind "(i)". The visual is `role="img"`
 * with a spoken summary and a "Table" twin. Motion is CSS only and stops with reduced motion.
 *
 * Data: GET /api/v1/states/ST/comparisons?part=... (`api.comparisons`); the static site reads
 * ST/comparisons/<part>.json only when ST/index.json says `comparisons: true` (checked in
 * api.js), else the card says it needs the local app. The screens mount the cards inside a
 * `ComparisonFold` (or a tab) so each part is asked for only when its section opens; while a
 * part computes the card shows its skeleton and `COMPARISONS_WAIT`. Every number shown is the server's text (`shown`, `likely`,
 * `headline`, `sentence`, `*_text`, written by src/visionhousing/api/comparison_text.py). The
 * browser only places values in a drawing (bar ends, the scale's ends); it writes no number.
 */
import "../../css/comparisons.css";
import {
  ablationTiers,
  api,
  COMPARISONS_LOCAL_ONLY,
  costMatrix,
  interactionMechanisms,
  interactionWaterfall,
  lastInEffects,
  missingDataRanking,
  rankingStability,
} from "../lib/api.js";
import { html, useState } from "../lib/html.js";
import { RangeBar, scaleOf } from "./bits.js";
import { CardFrame, useCard } from "./cards-common.js";
import { About, ChartFrame } from "./charts.js";

export { COMPARISONS_LOCAL_ONLY };

/** The loading line of every comparisons card: the first ask after a restart is slow. */
export const COMPARISONS_WAIT = "Computing… the first time after a server restart this takes about a minute.";

const asked = new Map(); // one request per state and part per page load, shared by every card

/**
 * One comparisons part for a state: `api.comparisons(st, part)`, shared by every card that
 * asks. In static mode api.js first checks the state's index (`comparisons: true`) and raises
 * the local-app note when the part was not exported.
 */
export function comparisonPart(state, part) {
  const st = String(state || "").toUpperCase();
  const key = `${st}:${part}`;
  if (!asked.has(key)) {
    const p = api.comparisons(st, part);
    p.catch(() => asked.delete(key)); // a failure may be asked again later
    asked.set(key, p);
  }
  return asked.get(key);
}

/**
 * A folded section whose cards are drawn (and so ask for their part) only after it is first
 * opened, so a screen never fires every comparisons part at once. It stays drawn once opened.
 */
export function ComparisonFold({ id, title, sub, children }) {
  const [seen, setSeen] = useState(false);
  return html`<details class="cmp-fold" id=${id} data-testid=${id}
      onToggle=${(e) => e.currentTarget.open && setSeen(true)}>
    <summary class="cmp-fold-head"><span class="cmp-fold-title">${title}</span>
      ${sub && html`<span class="cmp-fold-sub">${sub}</span>`}</summary>
    ${seen && html`<div class="cmp-fold-body stack">${children}</div>`}
  </details>`;
}

/** `useCard` for one comparisons part. */
export function useComparison(state, part) {
  return useCard(() => comparisonPart(state, part), [state, part]);
}

/** Percent of a scale, clamped, for placing a value in a drawing. */
const place = (v, lo, hi) => (hi > lo ? (100 * (Math.min(hi, Math.max(lo, v)) - lo)) / (hi - lo) : 0);

/* ------------------------------------------------------------------ waterfall */

/**
 * `hidden` maps a reform key to its transit status view when its statewide range is hidden
 * (ui/tod-status.js): its row shows "Not shown" and the package note, never a range.
 */
export function InteractionWaterfall({ state, h = 2, hidden = {} }) {
  const st = useComparison(state, "interactions");
  const i = st.data?.interactions;
  const wf = interactionWaterfall(st.data);
  const steps = wf?.steps || [];
  const last = lastInEffects(st.data) || {};
  const mech = interactionMechanisms(st.data);
  const ends = steps.flatMap((s) => (s.bar ? [s.bar.from, s.bar.to] : []));
  const lo = Math.min(0, ...ends);
  const hi = Math.max(0, ...ends);
  const summary = steps.map((s) => `${s.label} ${s.signed ?? s.record?.shown?.p50 ?? ""}`).join("; ");
  const rows = steps.map((s) => [s.label, s.signed ?? "", s.record?.likely ?? ""]);
  return html`<${CardFrame} id=${`cmp-interactions-${state}`} testid="cmp-interactions" title="How the reforms add up"
      h=${h} state=${st} what="the interaction accounting" className="cmp-card" wait=${COMPARISONS_WAIT} line=${i?.headline || ""}>
    ${steps.length > 0 &&
    html`<${ChartFrame} title="From the reforms alone to the package (middle of each step)"
        summary=${`Waterfall: ${summary}.`} cols=${["Step", "Homes (P50)", "Likely range (P10 to P90)"]} rows=${rows}
        className="cmp-chart">
      <ol class="cmp-wf" aria-hidden="true">
        ${steps.map((s, k) => {
          const a = place(Math.min(s.bar?.from ?? 0, s.bar?.to ?? 0), lo, hi);
          const b = place(Math.max(s.bar?.from ?? 0, s.bar?.to ?? 0), lo, hi);
          const neg = (s.bar?.to ?? 0) < (s.bar?.from ?? 0);
          return html`<li class=${`cmp-wf-row is-${s.kind} ${neg ? "is-neg" : ""}`} style=${`--i: ${k}`}>
            <span class="cmp-wf-label">${s.label}</span>
            <span class="cmp-wf-track">
              <span class="cmp-wf-bar" style=${`--l: ${a.toFixed(3)}; --w: ${Math.max(0.4, b - a).toFixed(3)}`}></span>
            </span>
            <span class="cmp-wf-num">${s.signed ?? ""}</span>
          </li>`;
        })}
      </ol>
    <//>`}
    ${i &&
    html`<${About} label="How the reforms add up: details">
      <p>${wf?.note}</p>
      <table class="table cmp-table">
        <caption>Each reform alone, last in (all five minus the other four), and the difference</caption>
        <thead><tr><th scope="col">Reform</th><th scope="col" class="num">Alone</th><th scope="col" class="num">Last in</th><th scope="col" class="num">Interaction share</th></tr></thead>
        <tbody>${Object.entries(last).map(([k, r]) =>
          hidden[k]
            ? html`<tr data-reform=${k} data-hidden="true"><th scope="row">${r.title}<span class="cmp-range"> ${hidden[k].label}</span></th>
              <td class="num">${hidden[k].not_shown}</td><td class="num">${hidden[k].not_shown}</td><td class="num">${hidden[k].not_shown}</td></tr>`
            : html`<tr data-reform=${k}><th scope="row">${r.title}</th>
            <td class="num">${r.solo?.shown?.p50}<span class="cmp-range"> ${r.solo?.likely}</span></td>
            <td class="num">${r.last_in?.shown?.p50}<span class="cmp-range"> ${r.last_in?.likely}</span></td>
            <td class="num">${r.interaction_share?.shown?.p50}<span class="cmp-range"> ${r.interaction_share?.likely}</span></td></tr>`,
        )}</tbody>
      </table>
      ${Object.values(hidden).map((v) => v.package_note && html`<p class="tod-package-note" role="note">${v.package_note}</p>`)}
      <ul class="cmp-list">${mech.map((m) => html`<li>${m.sentence}</li>`)}</ul>
      <p class="ec-quiet">Order of the ordered allocation: ${(i.ordering || []).map((k) => last[k]?.title || k).join(", ")} (${i.ordering_id}).
        ${i.check?.formula}.</p>
    <//>`}
  <//>`;
}

/* ------------------------------------------------------------------ matrix */

export function TakeupCostMatrix({ state, h = 2 }) {
  const st = useComparison(state, "matrix");
  const m = costMatrix(st.data);
  // The focused cell and the pointed-at cell are kept apart: a pointer that happens to sit
  // over the grid (or a layout shift under it) must not move the keyboard's cell or blank the
  // live line while a cell has focus. The live line names the focused cell first.
  const [focused, setFocused] = useState(null);
  const [pointed, setPointed] = useState(null);
  const hot = focused || pointed;
  const cells = m?.cells || [];
  const rt = m?.rows?.multipliers_text || [];
  const ct = m?.columns?.multipliers_text || [];
  let benchAt = null;
  cells.forEach((row, r) => row.forEach((c, k) => c.benchmark && (benchAt = [r, k])));
  const bench = benchAt ? cells[benchAt[0]][benchAt[1]] : null;
  const onAt = hot || benchAt;
  const on = onAt ? cells[onAt[0]]?.[onAt[1]] : null;
  const where = (c, r, k) => `take-up ${rt[r]}, cost ${ct[k]}${c.benchmark ? " (benchmark)" : ""}`;
  const tableRows = cells.map((row, r) => [`Take-up ${rt[r]}`, ...row.map((c) => `${c.shown?.p50} (${c.likely})`)]);
  return html`<${CardFrame} id=${`cmp-matrix-${state}`} testid="cmp-matrix" title="ADU take-up and construction cost together"
      h=${h} state=${st} what="the take-up and cost grid" className="cmp-card" wait=${COMPARISONS_WAIT} line=${m?.headline || ""}>
    ${m &&
    html`<${ChartFrame} title="Added ADUs, 2027-2031 (middle estimate)" className="cmp-chart"
        summary=${`Heat grid of added ADUs: rows ${m.rows?.label} ${rt.join(", ")}; columns ${m.columns?.label} ${ct.join(", ")}. ${m.headline}`}
        cols=${["", ...ct.map((c) => `Cost ${c}`)]} rows=${tableRows}>
      <div class="cmp-grid-wrap">
        <p class="cmp-axis-x" aria-hidden="true">Columns: ${m.columns?.label}, multiples of ${m.columns?.central_text}</p>
        <p class="cmp-axis-y" aria-hidden="true">Rows: ${m.rows?.label}, multiples of ${m.rows?.central_text}</p>
        <div class="cmp-grid" style=${`--cols: ${ct.length}`} onMouseLeave=${() => setPointed(null)}>
          <span></span>${ct.map((c) => html`<span class="cmp-grid-head">${c}</span>`)}
          ${cells.map(
            (row, r) => html`<span class="cmp-grid-head is-row">${rt[r]}</span>
              ${row.map(
                (c, k) => html`<button type="button" class=${`cmp-cell tone-${c.tone || 1} ${c.benchmark ? "is-bench" : ""} ${hot && hot[0] === r && hot[1] === k ? "is-hot" : ""}`}
                    aria-label=${`${where(c, r, k)}: ${c.shown?.p50} homes, likely ${c.likely}`}
                    onMouseEnter=${() => setPointed([r, k])} onFocus=${() => setFocused([r, k])}
                    onBlur=${() => setFocused((f) => (f && f[0] === r && f[1] === k ? null : f))}>
                  ${c.shown?.p50}${c.benchmark && html`<span class="cmp-bench-tag">benchmark</span>`}
                </button>`,
              )}`,
          )}
        </div>
      </div>
    <//>
    <p class="cmp-live" role="status" aria-live="polite">${on &&
      html`<strong>${where(on, onAt[0], onAt[1])}</strong>:
        about ${on.shown?.p50} homes, likely range ${on.likely}.`}</p>
    <${About} label="How this grid is made">
      <p>${m.rows?.label}: ${m.rows?.parameter}, ledger middle ${m.rows?.central_text}. ${m.columns?.label}: ${m.columns?.parameter}, ledger middle ${m.columns?.central_text}.</p>
      <p>A multiplier scales the input's whole range, so each simulated future keeps its place in it and every cell is paired with the benchmark (same seed, ${st.data.draws} draws). ${bench?.record?.formula}</p>
    <//>`}
  <//>`;
}

/* ------------------------------------------------------------------ ablation */

const TIER_ORDER = ["2", "1", "0"];

export function AblationCard({ state, h = 2 }) {
  const st = useComparison(state, "ablation");
  const a = st.data?.ablation;
  const t = ablationTiers(st.data);
  const hurts = missingDataRanking(st.data);
  const titles = a?.titles || {};
  const tiers = TIER_ORDER.filter((k) => t?.tiers?.[k]);
  const reforms = Object.keys(titles);
  const [pick, setPick] = useState("adu");
  const reform = reforms.includes(pick) ? pick : reforms[0];
  const ranges = tiers.map((k) => t.tiers[k].reforms?.[reform]?.ablated).filter(Boolean);
  const [lo, hi] = scaleOf(ranges);
  const rows = tiers.map((k) => {
    const r = t.tiers[k].reforms?.[reform] || {};
    const mt = r.measures_text || {};
    return [`Tier ${k}: ${t.tiers[k].title}`, r.ablated?.shown?.p50, r.ablated?.likely, mt.middle_shift, mt.range_widening, mt.rank_agreement, mt.coverage];
  });
  return html`<${CardFrame} id=${`cmp-ablation-${state}`} testid="cmp-ablation" title="With less data"
      h=${h} state=${st} what="the data ablation test" className="cmp-card" wait=${COMPARISONS_WAIT} line=${a?.headline || ""}>
    ${a &&
    html`<div class="ec-seg" role="group" aria-label="Reform">
        ${reforms.map((k) => html`<button type="button" class="ec-seg-btn" aria-pressed=${k === reform ? "true" : "false"} onClick=${() => setPick(k)}>${titles[k]}</button>`)}
      </div>
      <${ChartFrame} title=${`${titles[reform]}: likely range by data tier`} className="cmp-chart"
          summary=${tiers.map((k) => `Tier ${k} ${t.tiers[k].reforms?.[reform]?.ablated?.likely || ""}`).join("; ")}
          cols=${["Tier", "Middle", "Likely range", "Middle shift", "Range widening", "County rank agreement", "Coverage"]} rows=${rows}>
        <ul class="cmp-tiers">
          ${tiers.map((k) => {
            const r = t.tiers[k].reforms?.[reform] || {};
            return html`<li class="cmp-tier" data-tier=${k}>
              <span class="cmp-tier-name"><strong>Tier ${k}</strong> ${t.tiers[k].title}</span>
              <${RangeBar} label=${`Tier ${k}`} n=${r.ablated} lo=${lo} hi=${hi} focusable=${true} />
              <span class="cmp-tier-num">${r.ablated?.shown?.p50}<span class="cmp-range"> ${r.ablated?.likely}</span></span>
            </li>`;
          })}
        </ul>
      <//>
      ${hurts?.rows?.length > 0 &&
      html`<div class="cmp-hurts">
        <p class="cmp-sub">Which missing dataset hurts most</p>
        <ol class="cmp-hurts-list">${hurts.rows.map(
          (row) => html`<li><span>${row.title} <span class="ec-quiet">(tier ${row.tier})</span></span>
            <span class="num">${row.score_text} shift, ${row.widening_text} width</span></li>`,
        )}</ol>
        <p class="ec-quiet">${hurts.sentence}</p>
      </div>`}
      <${About} label="How the sparse-data test works">
        <table class="table cmp-table">
          <caption>${titles[reform]}: each tier against full data</caption>
          <thead><tr><th scope="col">Tier</th><th scope="col" class="num">Middle shift</th><th scope="col" class="num">Range widening</th><th scope="col" class="num">County rank agreement</th><th scope="col" class="num">Coverage</th></tr></thead>
          <tbody>${tiers.map((k) => {
            const mt = t.tiers[k].reforms?.[reform]?.measures_text || {};
            return html`<tr><th scope="row">Tier ${k}</th><td class="num">${mt.middle_shift}</td><td class="num">${mt.range_widening}</td><td class="num">${mt.rank_agreement}</td><td class="num">${mt.coverage}</td></tr>`;
          })}</tbody>
        </table>
        ${tiers.map((k) => t.tiers[k].reforms?.[reform]?.sentence && html`<p>${t.tiers[k].reforms[reform].sentence}</p>`)}
        <dl class="cmp-dl">${Object.entries(t.formulas || {}).map(([k, f]) => html`<dt>${k.replaceAll("_", " ")}</dt><dd>${f}</dd>`)}</dl>
        <p>${t.property?.sentence} Rule: ${t.property?.rule}.</p>
        <p class="ec-quiet">${hurts?.rule}. Measures ${a.measures_id}; ${st.data.draws} draws, seed ${st.data.seed}.</p>
      <//>`}
  <//>`;
}

/* ------------------------------------------------------------------ ranking */

export function RankingStability({ state, h = 2 }) {
  const st = useComparison(state, "ranking");
  const r = rankingStability(st.data);
  const titles = r?.titles || {};
  const order = r?.benchmark?.order || [];
  const ranges = r?.benchmark?.ranges || {};
  const [lo, hi] = scaleOf(order.map((k) => ranges[k]));
  const changed = (r?.cases || []).filter((c) => c.changed);
  const label = (p) => (r?.inputs || []).find((x) => x.parameter === p)?.label || p;
  return html`<${CardFrame} id=${`cmp-ranking-${state}`} testid="cmp-ranking" title="Does the order of the reforms hold?"
      h=${h} state=${st} what="the ranking check" className="cmp-card" wait=${COMPARISONS_WAIT} line=${r?.headline || ""}>
    ${r &&
    html`<${ChartFrame} title="Reforms by middle estimate, with likely ranges" className="cmp-chart"
        summary=${order.map((k, n) => `${n + 1}. ${titles[k]} ${ranges[k]?.shown?.p50} (${ranges[k]?.likely})`).join("; ")}
        cols=${["Reform", "Middle", "Likely range"]} rows=${order.map((k) => [titles[k], ranges[k]?.shown?.p50, ranges[k]?.likely])}>
      <ol class="cmp-rank">
        ${order.map(
          (k) => html`<li class="cmp-rank-row" data-reform=${k}>
            <span class="cmp-rank-name">${titles[k]}</span>
            <${RangeBar} label=${titles[k]} n=${ranges[k]} lo=${lo} hi=${hi} focusable=${true} />
            <span class="cmp-rank-num">${ranges[k]?.shown?.p50}</span>
          </li>`,
        )}
      </ol>
    <//>
    ${changed.length > 0 &&
    html`<div class="cmp-swaps">
      <p class="cmp-sub">Where the order changed</p>
      <ul class="cmp-swap-list">${changed.map(
        (c) => html`<li><span class="cmp-swap-what">${label(c.parameter)} at its ${c.end} end (${c.value_text})</span>
          <span class="cmp-swap-order">${c.order.map((k) => html`<span class=${`cmp-chip ${c.moved.includes(k) ? "is-moved" : ""}`}>${titles[k]}</span>`)}</span></li>`,
      )}</ul>
    </div>`}
    <${About} label="How the order was tested">
      <p>${r.sentence}</p>
      <table class="table cmp-table">
        <caption>Pairs of reforms: do their likely ranges overlap, and how often does the first add more?</caption>
        <thead><tr><th scope="col">Higher</th><th scope="col">Lower</th><th scope="col">Ranges overlap</th><th scope="col" class="num">Futures where the higher adds more</th></tr></thead>
        <tbody>${(r.pairs || []).map(
          (p) => html`<tr><th scope="row">${titles[p.higher]}</th><td>${titles[p.lower]}</td><td>${p.ranges_overlap ? "yes" : "no"}</td><td class="num">${p.share_text}</td></tr>`,
        )}</tbody>
      </table>
      <p class="ec-quiet">${r.rule}. ${st.data.draws} draws, seed ${st.data.seed}.</p>
    <//>`}
  <//>`;
}
