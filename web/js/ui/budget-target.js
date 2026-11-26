/**
 * Budget and target: which tested ADU option reaches a target, within a budget, at an
 * accepted share of the modeled draws, and at what net public cost.
 *
 *   Inputs    target (added ADU authorizations, 2027-2031), budget ceiling ($), accepted share
 *             of modeled draws, allowed options (law change only / program only / both) and
 *             award size.
 *   Flags     "input missing", "outside the tested range", "between tested values" (shown as a
 *             bracket of the two tested cells, never interpolated), "budget exceeded" and "no
 *             tested option meets both", each with its own mark and words.
 *   Table     every allowed tested cell: five-year added homes (likely range), share of modeled
 *             draws meeting the target with its Monte Carlo standard error, net public cost,
 *             upfront appropriation, award, units taken from later years, owners funded, key
 *             lever, Pareto mark and status (meets / below the accepted share / over budget).
 *   Chart     cost against share meeting the target, the Pareto set joined; its table twin.
 *   Covered   the law reading on record behind the law change (U13's statewide shares).
 *   Demo      the walk-through: ADU benchmark range -> predictable approvals -> financed access
 *             on the same draws (the old uncapped variant, labelled so) -> a budget cap -> does
 *             the ranking hold.
 *
 * Data: GET /api/v1/states/ST/adu-budget-grid -> {grid, view} (lib/api.js `budgetGrid`; static:
 * ST/adu-budget-grid.json only when exported, else "needs the local app"). Which cells meet a
 * target is picked by `gridLookup` (lib/budget.js, the twin of the server's grid_lookup, checked
 * equal by tests). Every number shown is the view's text (src/visionhousing/api/budget_view.py);
 * the chart only places values. The demo also reads the ADU run (POST /run), the lever card
 * (GET /lever/ST?r=adu) and the law-changes reply. The only browser judgement is the order of
 * two server middle values in the demo's "does the ranking hold" sentence; it writes no number.
 *
 * Accessibility: native inputs with labels; the result line is a polite live region; the chart
 * is role="img" with a spoken summary and a Table twin; the fold is a native disclosure; motion
 * stops with reduced motion (css/budget-target.css).
 */
import "../../css/budget-target.css";
import { signal } from "@preact/signals";
import { budgetGrid, leverCard } from "../lib/api.js";
import { cellKey, gridLookup, targetKey } from "../lib/budget.js";
import { numberOf, useRun } from "../lib/data.js";
import { html, useEffect, useId, useState } from "../lib/html.js";
import { CardFrame, useCard } from "./cards-common.js";
import { About, ChartFrame } from "./charts.js";
import { lawChanges } from "./law-changes.js";

/** Set by the ADU Decision tab's link, so the Compare screen opens the panel at once. */
export const budgetOpen = signal(false);

const WAIT = "Working out the grid… the first time after a server restart this takes about a minute.";
const FLAG_ORDER = ["input_missing", "outside_tested_range", "between_tested_values", "budget_exceeded", "no_tested_option_meets_both"];

/** The allowed-options choices: which arms the reader lets in. */
const ARM_CHOICES = [
  ["any", "Any option", null],
  ["law_only", null, ["law_only"]],
  ["finance_only", null, ["finance_only"]],
  ["both", null, ["both"]],
];

/** A text box's value as a number, or null ("" or not a number). */
const asNumber = (v) => {
  const t = String(v ?? "").replace(/[$,\s]/g, "");
  return t === "" || !/^\d+(\.\d+)?$/.test(t) ? null : Number(t);
};

/** The lookup for the reader's inputs, over the allowed arms only. */
export function lookupFor(grid, inputs) {
  const arms = inputs.arms;
  const g = arms ? { ...grid, cells: grid.cells.filter((c) => arms.includes(c.arm)) } : grid;
  return gridLookup(g, {
    target: inputs.target,
    budget: inputs.budget,
    award: inputs.award,
    accept: inputs.accept,
  });
}

function Flags({ q, view, money }) {
  const flags = FLAG_ORDER.filter((f) => q.flags.includes(f));
  if (!flags.length) return null;
  const b = q.brackets || {};
  const t = (list, v) => list.find((o) => o.value === v)?.text ?? "";
  return html`<ul class="bt-flags" data-testid="bt-flags">
    ${flags.map(
      (f) => html`<li class=${`bt-flag is-${f}`} data-flag=${f}>
        <span class="bt-flag-mark" aria-hidden="true"></span>
        <span>${view.flags[f] || f}
          ${f === "between_tested_values" && b.target &&
          html` <strong data-testid="bt-bracket-target">Bracket: ${t(view.options.targets, b.target[0])} and ${t(view.options.targets, b.target[1])} homes.</strong>`}
          ${f === "between_tested_values" && b.budget &&
          html` <strong data-testid="bt-bracket-budget">Your budget lies between ${t(view.options.budgets, b.budget[0])} and ${t(view.options.budgets, b.budget[1])}; only cells at or below it are read.</strong>`}
          ${f === "between_tested_values" && b.award &&
          html` <strong>Your award lies between ${t(view.options.awards, b.award[0])} and ${t(view.options.awards, b.award[1])}.</strong>`}
          ${f === "outside_tested_range" && money && html` Tested budgets: ${view.options.budgets.map((o) => o.text).join(", ")}.`}
        </span>
      </li>`,
    )}
  </ul>`;
}

/** The rows of one tested target, in the order the reader needs them. */
function rowsOf(q, view) {
  const key = (o) => cellKey(o);
  const meets = new Set(q.options.map(key));
  const over = new Set(q.over_budget.map(key));
  const order = [...q.options.map(key)];
  for (const c of view.cells) {
    const k = c.key;
    if (!order.includes(k) && !over.has(k) && q._within.has(k)) order.push(k);
  }
  for (const o of q.over_budget) order.push(key(o));
  const texts = Object.fromEntries(view.cells.map((c) => [c.key, c]));
  return order.map((k) => ({ c: texts[k], status: meets.has(k) ? "meets" : over.has(k) ? "over" : "below" }));
}

const STATUS = { meets: "Meets the target at the accepted share", below: "Below the accepted share", over: "Budget exceeded" };

function CellTable({ rows, tkey, caption }) {
  return html`<div class="table-wrap bt-table-wrap" tabindex="0" role="region" aria-label=${caption}>
    <table class="table bt-table" data-testid="bt-table">
      <caption class="visually-hidden">${caption}</caption>
      <thead><tr>
        <th scope="col">Option</th>
        <th scope="col" class="num">Upfront appropriation</th>
        <th scope="col" class="num">Award</th>
        <th scope="col" class="num">Added homes, 2027–2031 (likely range)</th>
        <th scope="col" class="num">Share of modeled draws meeting the target</th>
        <th scope="col" class="num">Net public cost (middle)</th>
        <th scope="col" class="num">Taken from later years (middle)</th>
        <th scope="col" class="num">Owners funded (middle)</th>
        <th scope="col">Key lever</th>
        <th scope="col">Pareto</th>
        <th scope="col">Status</th>
      </tr></thead>
      <tbody>
        ${rows.map(({ c, status }) => {
          const t = c.targets[tkey];
          return html`<tr data-key=${c.key} data-status=${status} class=${`bt-row is-${status} ${t.pareto ? "is-pareto" : ""}`}>
            <th scope="row"><span class=${`bt-arm is-${c.arm}`}>${c.arm_word}</span></th>
            <td class="num">${c.appropriation_text}</td>
            <td class="num">${c.award_text}</td>
            <td class="num" data-col="added" title=${c.added?.formula || ""}>${c.added?.likely}<span class="bt-sub">middle ${c.added?.shown.p50}</span></td>
            <td class="num" data-col="share" title=${t.draws_text}><strong>${t.share_text}</strong><span class="bt-sub">standard error ${t.se_text}</span></td>
            <td class="num" data-col="cost" title=${c.net_cost?.formula || ""}>${c.net_cost?.shown.p50}<span class="bt-sub">${c.net_cost?.likely}</span></td>
            <td class="num" data-col="moved" title=${c.moved_note}>${c.moved?.shown.p50}</td>
            <td class="num" data-col="served">${c.served?.shown.p50}</td>
            <td data-col="lever" title=${c.key_lever?.text}>${c.key_lever?.word}</td>
            <td data-col="pareto">${t.pareto ? html`<span class="bt-pareto-tag">On the Pareto set</span>` : "—"}</td>
            <td data-col="status"><span class=${`bt-status is-${status}`}>${STATUS[status]}</span></td>
          </tr>`;
        })}
      </tbody>
    </table>
  </div>`;
}

/** Cost against share meeting the target; the Pareto set joined. Positions only. */
function ParetoChart({ rows, tkey, targetText }) {
  const pts = rows.filter((r) => r.status !== "over").map(({ c }) => ({ c, t: c.targets[tkey] }));
  const costs = pts.map((p) => p.c.net_cost?.p50 ?? 0);
  const top = Math.max(1, ...costs);
  const W = 300;
  const H = 90;
  const x = (v) => 8 + ((W - 16) * Math.max(0, v)) / top;
  const y = (s) => H - 8 - (H - 16) * Math.max(0, Math.min(1, s));
  const front = pts.filter((p) => p.t.pareto).sort((a, b) => (a.c.net_cost?.p50 ?? 0) - (b.c.net_cost?.p50 ?? 0));
  let d = "";
  front.forEach((p, i) => {
    const px = x(p.c.net_cost?.p50 ?? 0).toFixed(2);
    const py = y(p.t.share).toFixed(2);
    d += i ? ` H${px} V${py}` : `M${px} ${py}`;
  });
  const rowsTwin = front.map((p) => [`${p.c.arm_word} · ${p.c.appropriation_text} · ${p.c.award_text}`, p.c.net_cost?.shown.p50, p.t.share_text]);
  const summary = front.length
    ? `Pareto set for ${targetText} homes: ${front.map((p) => `${p.c.arm_word} ${p.c.appropriation_text}, ${p.t.share_text} of draws at ${p.c.net_cost?.shown.p50}`).join("; ")}.`
    : `No allowed option meets ${targetText} homes in any modeled draw.`;
  return html`<${ChartFrame} title=${`Cost against the share of draws meeting ${targetText} homes`} summary=${summary}
      cols=${["Pareto option", "Net public cost (middle)", "Share of modeled draws"]} rows=${rowsTwin} className="bt-chart">
    <svg viewBox=${`0 0 ${W} ${H}`} class="bt-svg" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">
      <line class="bt-axis" x1="8" y1=${H - 8} x2=${W - 2} y2=${H - 8} />
      <line class="bt-axis" x1="8" y1="4" x2="8" y2=${H - 8} />
      ${d && html`<path class="bt-front" d=${d} />`}
      ${pts.map((p, i) => html`<circle class=${`bt-dot is-${p.c.arm} ${p.t.pareto ? "is-pareto" : ""}`} style=${`--i: ${i}`}
        cx=${x(p.c.net_cost?.p50 ?? 0).toFixed(2)} cy=${y(p.t.share).toFixed(2)} r=${p.t.pareto ? 4 : 2.6} />`)}
    </svg>
    <p class="bt-axes"><span>Net public cost (middle) →</span><span>↑ Share of modeled draws meeting the target</span></p>
    <ul class="bt-legend" aria-hidden="true">
      <li><span class="bt-dot-key is-law_only"></span>Law change only</li>
      <li><span class="bt-dot-key is-finance_only"></span>Program only</li>
      <li><span class="bt-dot-key is-both"></span>Both</li>
      <li><span class="bt-dot-key is-pareto"></span>Pareto set (joined)</li>
    </ul>
  <//>`;
}

/** One tested target: its chart and table. `q` is the lookup at that target. */
function TargetBlock({ q, view, label }) {
  const tkey = targetKey(q.target);
  const ttext = view.options.targets.find((o) => o.value === q.target)?.text ?? "";
  const rows = rowsOf(q, view);
  return html`<div class="bt-block" data-target=${tkey} data-testid="bt-block">
    ${label && html`<p class="cmp-sub">${label}: ${ttext} homes (a tested target)</p>`}
    <${ParetoChart} rows=${rows} tkey=${tkey} targetText=${ttext} />
    <${CellTable} rows=${rows} tkey=${tkey} caption=${`Every allowed tested option for a target of ${ttext} homes`} />
  </div>`;
}

/** Lookup plus the set of within-budget keys (for the row order). */
function fullLookup(grid, inputs) {
  const q = lookupFor(grid, inputs);
  if (q.options) {
    const arms = inputs.arms;
    const cells = grid.cells.filter((c) => (!arms || arms.includes(c.arm)));
    const over = new Set(q.over_budget.map(cellKey));
    const awardOk = (c) => inputs.award === null || c.arm === "law_only" || !grid.settings.awards.includes(inputs.award) || c.award === inputs.award;
    q._within = new Set(cells.filter((c) => awardOk(c) && !over.has(cellKey(c))).map(cellKey));
  }
  return q;
}

function Inputs({ view, form, set, ids }) {
  const opts = view.options;
  return html`<form class="bt-inputs" onSubmit=${(e) => e.preventDefault()} aria-label="Budget and target inputs">
    <div class="bt-field">
      <label for=${`${ids}-t`}>Target: added ADU authorizations, 2027–2031</label>
      <input id=${`${ids}-t`} class="input input-xs" inputmode="numeric" list=${`${ids}-tl`} value=${form.target}
        data-testid="bt-target" onInput=${(e) => set({ target: e.currentTarget.value.slice(0, 12) })} />
      <datalist id=${`${ids}-tl`}>${opts.targets.map((o) => html`<option value=${String(o.value)}>${o.text}</option>`)}</datalist>
      <span class="bt-hint">Tested: ${opts.targets.map((o) => o.text).join(", ")}</span>
    </div>
    <div class="bt-field">
      <label for=${`${ids}-b`}>Budget ceiling ($, blank for none)</label>
      <input id=${`${ids}-b`} class="input input-xs" inputmode="numeric" list=${`${ids}-bl`} value=${form.budget}
        data-testid="bt-budget" onInput=${(e) => set({ budget: e.currentTarget.value.slice(0, 14) })} />
      <datalist id=${`${ids}-bl`}>${opts.budgets.map((o) => html`<option value=${String(o.value)}>${o.text}</option>`)}</datalist>
      <span class="bt-hint">Tested: ${opts.budgets.map((o) => o.text).join(", ")}</span>
    </div>
    <div class="bt-field">
      <label for=${`${ids}-a`}>Accepted chance (share of modeled draws)</label>
      <select id=${`${ids}-a`} class="input input-xs" value=${form.accept} data-testid="bt-accept"
        onChange=${(e) => set({ accept: e.currentTarget.value })}>
        <option value="0">Any share, including none</option>
        ${view.accept_levels.map((o) => html`<option value=${String(o.value)}>At least ${o.text}</option>`)}
      </select>
    </div>
    <div class="bt-field">
      <label for=${`${ids}-w`}>Award size</label>
      <select id=${`${ids}-w`} class="input input-xs" value=${form.award} data-testid="bt-award"
        onChange=${(e) => set({ award: e.currentTarget.value })}>
        <option value="">Any tested award</option>
        ${opts.awards.map((o) => html`<option value=${String(o.value)}>${o.text}</option>`)}
      </select>
    </div>
    <fieldset class="bt-field bt-arms">
      <legend>Allowed options</legend>
      ${ARM_CHOICES.map(([id, word]) => {
        const text = word || opts.arms.find((a) => a.value === id)?.text || id;
        return html`<label class="bt-radio"><input type="radio" name=${`${ids}-arms`} value=${id} checked=${form.arms === id}
          onChange=${() => set({ arms: id })} /> ${text}</label>`;
      })}
    </fieldset>
  </form>`;
}

/** The inputs as the lookup takes them. */
function inputsOf(form) {
  const arms = ARM_CHOICES.find(([id]) => id === form.arms)?.[2] ?? null;
  return {
    target: asNumber(form.target),
    budget: asNumber(form.budget),
    award: form.award === "" ? null : Number(form.award),
    accept: form.accept === "" ? null : Number(form.accept),
    arms,
  };
}

/** The result line for the live region: words only, from the view and the flags. */
function resultLine(q, view) {
  if (q.flags.includes("input_missing")) return view.flags.input_missing;
  if (q.brackets?.target) return "Your target lies between two tested targets: both tested cells are shown below, nothing in between.";
  if (!q.options) return view.flags.outside_tested_range;
  const meets = q.options;
  if (!meets.length) return view.flags.no_tested_option_meets_both;
  const first = Object.fromEntries(view.cells.map((c) => [c.key, c]))[cellKey(meets[0])];
  const t = first?.targets[targetKey(q.target)];
  return `Cheapest tested option that meets it: ${first?.arm_word}${first?.arm === "law_only" ? "" : `, ${first?.appropriation_text} of ${first?.award_text} awards`}, in ${t?.share_text} of the modeled draws (standard error ${t?.se_text}), net public cost ${first?.net_cost?.shown.p50}.`;
}

export function BudgetTarget({ state, h = 3 }) {
  const st = useCard(() => budgetGrid(state), [state]);
  const ids = useId();
  const [form, setForm] = useState({ target: "", budget: "", accept: "", award: "", arms: "any", touched: false });
  const data = st.data;
  const view = data?.view;
  const grid = data?.grid;
  // Until the reader types, the panel opens on a tested target and the first accepted share.
  const tested = view?.options.targets || [];
  const defaults = view && !form.touched
    ? {
        ...form,
        target: String(tested[Math.min(tested.length - 1, Math.floor(tested.length / 2) + 1)]?.value ?? ""),
        accept: String(view.accept_levels[0]?.value ?? "0"),
      }
    : form;
  const set = (patch) => setForm({ ...defaults, ...patch, touched: true });
  const inputs = inputsOf(defaults);
  const q = grid ? fullLookup(grid, inputs) : null;
  const brackets = q?.brackets?.target;
  const qs = brackets && grid ? brackets.map((t) => fullLookup(grid, { ...inputs, target: t })) : [];
  return html`<${CardFrame} id=${`budget-target-${state}`} testid="budget-target" title="Budget and target: ADU options" h=${h}
      state=${st} what="the budget-and-target grid" wait=${WAIT} className="cmp-card bt-card"
      line=${view ? view.scope : ""}>
    ${view &&
    html`<p class="ec-quiet bt-illustrative">Illustrative scenario settings: the appropriations, awards and targets are choices to test, not estimates.</p>
      <${Inputs} view=${view} form=${defaults} set=${set} ids=${ids} />
      <p class="bt-result" role="status" aria-live="polite" data-testid="bt-result">${resultLine(q, view)}</p>
      <${Flags} q=${q} view=${view} money=${inputs.budget !== null} />
      ${q.options && html`<${TargetBlock} q=${q} view=${view} />`}
      ${qs.map((qq, i) => html`<${TargetBlock} q=${qq} view=${view} label=${i ? "Tested target above yours" : "Tested target below yours"} />`)}
      <${Covered} state=${state} />
      <${About} label="How the grid is built">
        ${view.notes.map((n) => html`<p>${n}</p>`)}
        <p>${view.uncapped_note}</p>
        <p>Benchmark (linear take-up): ${view.reference.benchmark_linear?.likely}, middle ${view.reference.benchmark_linear?.shown.p50}.
          Law only with the hazard method: ${view.reference.law_only_hazard?.likely}, middle ${view.reference.law_only_hazard?.shown.p50}.
          Method difference (disclosed, same draws): ${view.reference.method_difference?.likely}, middle ${view.reference.method_difference?.shown.p50}.</p>
        <p>"Taken from later years": owners the money only speeds up. It counts only projects that would have been filed after 2031 without the option and now land earlier, so it is never below zero. The law change itself moves none: its extra projects after 2031 are new projects, reported separately. The share of draws is a count of draws meeting the target; its Monte Carlo standard error is draw-to-draw noise only.</p>
        <p class="ec-quiet">${view.grid_id} · ${view.id} · ${view.draws_text} draws, seed ${view.seed}</p>
      <//>`}
  <//>`;
}

/** The law reading behind the law change: U13's statewide shares (the server's text). */
function Covered({ state }) {
  const law = useCard(() => lawChanges(state, "adu"), [state]);
  const d = law.data;
  const sw = d?.statewide;
  return html`<div class="bt-covered" data-testid="bt-covered">
    <p class="cmp-sub">Covered share: the law reading behind the law change</p>
    ${law.loading && html`<p class="ec-quiet">Loading the law reading…</p>`}
    ${!law.loading && !sw && html`<p class="ec-quiet">Input missing: ${law.error || "the law reading is not in this build"}.</p>`}
    ${sw &&
    html`<ul class="bt-covered-list">
      ${(d.classes || []).map((c) => html`<li><span>${c.word}</span> <strong>${sw.classes[c.id]?.share?.shown ?? "—"}</strong></li>`)}
    </ul>
    <p class="ec-quiet">Of ${sw.denominator?.shown} ${d.denominator?.label}.${sw.unresolved_result_share ? ` Share of the added homes in unresolved places: ${sw.unresolved_result_share.shown}.` : ""} The same reading applies to every cell.</p>`}
  </div>`;
}

/* ------------------------------------------------------------------ the demo path */

/** The lever card's effect of one named variant, or null. */
const effectOf = (card, name) => (card?.effects || []).find((e) => e.variant === name) || null;

/**
 * The demo path, step by step: the run, the lever card's named variants (old and capped) and the
 * grid's cell at the capped variant's illustrative appropriation and award.
 */
export function BudgetDemo({ state, h = 3 }) {
  const run = useRun({ state, reforms: ["adu"], overrides: {} });
  const lever = useCard(() => leverCard(state, "adu"), [state]);
  const grid = useCard(() => budgetGrid(state), [state]);
  const view = grid.data?.view;
  const added = numberOf(run.data, "reform_results.adu.added");
  const c = run.data?.contract;
  const audit = c?.audit?.adu;
  const timing = effectOf(lever.data, "adu_timing_reliable");
  const access = effectOf(lever.data, "adu_financing_access");
  const conserved = effectOf(lever.data, "adu_timing_conserved");
  const budget = effectOf(lever.data, "adu_finance_budget");
  const capped = (view?.cells || []).find((x) => x.key === "both|20000000|25000") || null;
  // Which of two server middle values is larger: the only browser judgement (no number written).
  const order = (fin, tim) => (fin && tim ? (fin.effect.p50 > tim.effect.p50 ? "financing" : "timing") : "");
  const holdsOld = order(access, timing);
  const holdsNew = order(budget, conserved);
  const word = { financing: "financed access ranks above predictable approvals", timing: "predictable approvals rank above financed access" };
  const H = `h${Math.min(6, h + 1)}`;
  return html`<section class="card ec-card bt-demo" data-testid="bt-demo" aria-labelledby=${`bt-demo-${state}`}>
    <header class="card-head"><div>
      <${`h${h}`} class="card-title" id=${`bt-demo-${state}`}>How to demo it<//>
      <p class="card-sub">Four steps on the same draws; each number is the server's text.</p>
    </div></header>
    <ol class="card-body bt-steps">
      <li class="bt-step" data-step="benchmark">
        <${H} class="bt-step-title">1. The ADU benchmark<//>
        ${added
          ? html`<p><strong>${added.shown.p10} to ${added.shown.p90}</strong>, middle ${added.shown.p50}: ${audit?.measure || "authorizations"}, ${c?.window || "2027-2031"}, as of ${c?.as_of}.</p>
            ${audit?.coverage_note && html`<p class="ec-quiet">Outcome scope: ${audit.coverage_note}</p>`}`
          : html`<p class="ec-quiet">${run.error || "Loading the ADU run…"}</p>`}
      </li>
      <li class="bt-step" data-step="timing">
        <${H} class="bt-step-title">2. Predictable approvals<//>
        ${timing
          ? html`<p>${timing.title}: <strong>${timing.effect.shown.p50}</strong> against the benchmark (likely ${timing.effect.shown.p10} to ${timing.effect.shown.p90}). Original variant, linear take-up.</p>`
          : html`<p class="ec-quiet">${lever.loading ? "Running the variants… the first time can take a few minutes." : lever.error || "Not in this build."}</p>`}
        ${conserved && html`<p>${conserved.title}: <strong>${conserved.effect.shown.p50}</strong> against the benchmark (likely ${conserved.effect.shown.p10} to ${conserved.effect.shown.p90}); hazard method, so this includes the method difference.</p>`}
      </li>
      <li class="bt-step" data-step="access">
        <${H} class="bt-step-title">3. Financed access on the same draws<//>
        ${access
          ? html`<p>${access.title}: <strong>${access.effect.shown.p50}</strong> against the benchmark (likely ${access.effect.shown.p10} to ${access.effect.shown.p90}).</p>
            <p class="bt-uncapped" data-testid="bt-uncapped"><strong>Old uncapped variant.</strong> ${view?.uncapped_note || "It has no appropriation limit."}</p>`
          : html`<p class="ec-quiet">${lever.loading ? "Waiting for the lever card…" : lever.error || "Not in this build."}</p>`}
      </li>
      <li class="bt-step" data-step="cap">
        <${H} class="bt-step-title">4. A budget cap, and whether the ranking holds<//>
        ${budget
          ? html`<p>${budget.title}: <strong>${budget.effect.shown.p50}</strong> against the benchmark (likely ${budget.effect.shown.p10} to ${budget.effect.shown.p90}); hazard method, so this includes the method difference${view?.reference?.method_difference ? ` (${view.reference.method_difference.shown.p50} in the middle, the same for every hazard variant)` : ""}.</p>`
          : html`<p class="ec-quiet">${lever.loading ? "Waiting for the lever card…" : lever.error || "The capped variant is not in this build's lever card."}</p>`}
        ${capped &&
        html`<p>From the grid, ${capped.label}: the program adds <strong>${capped.program_added?.shown.p50}</strong> on top of the law (likely ${capped.program_added?.likely}), net public cost ${capped.net_cost?.shown.p50}, ${capped.served?.shown.p50} owners funded (${capped.windfall?.shown.p50} of them would have built anyway).</p>`}
        ${holdsOld && holdsNew &&
        html`<p data-testid="bt-ranking">Original variants: ${word[holdsOld]}. With the cap: ${word[holdsNew]}.
          <strong>${holdsOld === holdsNew ? "The ranking holds." : "The ranking does not hold."}</strong>
          The capped comparison sets the conserved timing variant against the capped program, both on the hazard method and against the same benchmark.</p>`}
      </li>
    </ol>
  </section>`;
}

/** The folded section on the Compare screen: drawn (and asked for) only once opened. */
export function BudgetFold({ state }) {
  // Opened by the ADU Decision tab's link: start open once, then the reader's toggle rules.
  const [open, setOpen] = useState(budgetOpen.peek());
  const [seen, setSeen] = useState(budgetOpen.peek());
  useEffect(() => {
    budgetOpen.value = false;
    if (open) document.getElementById("budget-target")?.scrollIntoView({ block: "start" });
  }, []);
  return html`<details class="cmp-fold bt-fold" id="budget-target" data-testid="budget-fold" open=${open}
      onToggle=${(e) => {
        const now = e.currentTarget.open;
        setOpen(now);
        if (now) setSeen(true);
      }}>
    <summary class="cmp-fold-head"><span class="cmp-fold-title">Budget and target (ADUs)</span>
      <span class="cmp-fold-sub">Which tested option reaches a target within a budget, in what share of the modeled draws, at what net cost. Computed when opened.</span></summary>
    ${seen && html`<div class="cmp-fold-body stack"><${BudgetTarget} state=${state} h=${3} /><${DemoFold} state=${state} /></div>`}
  </details>`;
}

/** The demo path in its own fold: it runs the lever card's variants, so only when asked. */
function DemoFold({ state }) {
  const [seen, setSeen] = useState(false);
  return html`<details class="cmp-fold bt-demo-fold" data-testid="bt-demo-fold" onToggle=${(e) => e.currentTarget.open && setSeen(true)}>
    <summary class="cmp-fold-head"><span class="cmp-fold-title">How to demo it</span>
      <span class="cmp-fold-sub">Benchmark range, predictable approvals, financed access on the same draws, a budget cap, and whether the ranking holds. Runs the ADU variants; the first time can take a few minutes.</span></summary>
    ${seen && html`<div class="cmp-fold-body stack"><${BudgetDemo} state=${state} h=${4} /></div>`}
  </details>`;
}
