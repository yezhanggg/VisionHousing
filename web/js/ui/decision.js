/**
 * The decision view and the readouts that go with it.
 *
 *   DecisionView   the briefing in five steps, each one line and one small visual:
 *                    1 Question       the reform, what is counted, the window, the as-of date
 *                    2 Answer         homes added (likely range), the "in eight of ten futures"
 *                                     sentence, and the years 2027–2031 when the engine split them
 *                    3 Where & why    the county that gains most, the binding constraint, the top
 *                                     driver, and evidence coverage across the counties
 *                    4 What to change one variation on the same draws (an input at its high value)
 *                                     and the next question to ask
 *                    5 Use it         the one-page brief, the table and the share link
 *   HowSure        the paired readouts: "share of modeled draws in which it adds at least N" as a
 *                  step chart with each share's Monte Carlo standard error, the share
 *                  of the baseline as a range, and the cost row (fees: revenue given up, cost per
 *                  added home, the cut targeted by group; parking: the builders' saving; others:
 *                  "no direct public cost")
 *   Narrowing      "What would narrow the range": each input's share of the uncertainty, the
 *                  leftover, and the research agenda sentence
 *   BindingCard    the binding constraint: every funnel stage's loss, grouped by who acts there
 *                  (state, municipality, utility, builder, household), and the question to ask
 *
 * Data: the run reply (POST /run: explanation.readouts, explanation.narrowing,
 * explanation.headline) and the map reply's `decision` block (GET /states/ST/map for the same
 * scenario: question, years, top county, binding constraint, coverage), which the API builds in
 * src/visionhousing/api/decision.py. Each part shows only when its data is there (an older
 * export has none of it), so nothing breaks while the engine grows. Every number is the API's
 * own value or text; a chart only places what it is given.
 */
import { signal } from "@preact/signals";
import { reformById } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { IS_STATIC, LOCAL_ONLY, narrowing, readoutSentences } from "../lib/api.js";
import { downloadCsv, openBrief } from "../lib/exports.js";
import { html } from "../lib/html.js";
import { scenario, updateScenario } from "../state/store.js";
import { shareLink } from "./actions.js";
import { RangeBar, scaleOf } from "./bits.js";
import { About, HBars, StackBar, StepChart, YearBars } from "./charts.js";
import { Icon } from "./icons.js";
import { HiddenRange, hiddenView, PartialLabel, statusView } from "./tod-status.js";
import { Ticker } from "./motion.js";

/** The text the engine wrote for one cited field ("ref" ending, field), or "". */
function cite(text, refEnd, field) {
  const c = (text?.cites || []).find((x) => x.ref.endsWith(refEnd) && x.field === field);
  return c?.shown || "";
}

/** The map reply's decision block for this reform, if the API sent one. */
export function decisionOf(map, id) {
  const d = map?.data?.decision;
  return d && d.reform === id ? d : null;
}

/** The first input of the narrowing list that is a ledger entry (one a reader can move). */
function movable(n) {
  return (n?.items || []).find((x) => /^[a-z_]+\.[a-z0-9_]+$/.test(x.parameter) && !x.parameter.startsWith("engine."));
}

/**
 * The input "Try it at its high value" moved: `{state, id, parameter, label}`. It is kept
 * while the variation is on, because the varied run ranks its inputs again and its top input
 * may be another one; the button must keep naming the input that was moved, so "Back to the
 * benchmark" undoes exactly that change.
 */
export const triedLever = signal(null);

/**
 * The lever step 4 offers: the one that was moved while it is still at "h" (also after a
 * reload, when the address holds it), otherwise the top movable input of this run.
 */
export function leverOf(nar, sc, id) {
  const t = triedLever.value;
  if (t && t.state === sc.state && t.id === id && sc.overrides[t.parameter] === "h") return t;
  const held = (nar?.items || []).find((x) => sc.overrides[x.parameter] === "h");
  return held || movable(nar);
}

/**
 * "Assumption changed: <input> at its high value", shown on the headline while the decision
 * view's variation is on. The words hold no number (the input's label only).
 */
export function LeverFlag({ run, id }) {
  const sc = scenario.value;
  const lever = leverOf(narrowing(run.data, id), sc, id);
  if (!lever || sc.overrides[lever.parameter] !== "h") return null;
  return html`<p class="assume-flag" role="note" data-testid="assumption-flag">
    <${Icon} name="info" size=${14} /><span>Assumption changed: <strong>${lever.label}</strong> at its high value.
      Not the benchmark.</span>
  </p>`;
}

function Step({ n, title, line, children, testid }) {
  return html`<li class="decision-step stagger" style=${`--i: ${n - 1}`} data-step=${testid}>
    <span class="decision-num" aria-hidden="true">${n}</span>
    <div class="decision-main">
      <h3 class="decision-title">${title}</h3>
      ${line && html`<div class="decision-line">${line}</div>`}
      ${children && html`<div class="decision-visual">${children}</div>`}
    </div>
  </li>`;
}

/**
 * The five-step briefing. `run` is the run of this reform alone (with drivers when loaded),
 * `map` the map reply of the same scenario, `onTab(id)` opens a tab of the panel ("drivers",
 * "limits") when given. `compact` leaves out the answer's big number (the State screen shows
 * it just above).
 */
export function DecisionView({ run, map, id, onTab = null, compact = false }) {
  const data = run.data;
  const e = data?.explanation;
  const reform = reformById(id);
  const d = decisionOf(map, id);
  const ro = readoutSentences(data, id);
  const nar = narrowing(data, id);
  const head = Object.fromEntries((e?.headline?.numbers || []).map((x) => [x.key, x]));
  const added = head.added;
  const sc = scenario.value;
  const q = d?.question;
  const b = d?.binding?.binding;
  const top = e?.drivers?.[id]?.items?.[0];
  const lever = leverOf(nar, sc, id);
  const moved = lever && sc.overrides[lever.parameter] === "h";
  const toggleLever = () => {
    const next = { ...sc.overrides };
    if (moved) {
      delete next[lever.parameter];
      triedLever.value = null;
    } else {
      next[lever.parameter] = "h";
      triedLever.value = { state: sc.state, id, parameter: lever.parameter, label: lever.label };
    }
    updateScenario({ overrides: next });
  };
  if (!data) return null;
  const hidden = hiddenView(data, id);
  const years = d?.annual?.years || [];
  return html`<section class="decision" aria-labelledby="decision-title" data-testid="decision-view">
    <h2 class="section-head" id="decision-title">Decision view</h2>
    <ol class="decision-steps">
      <${Step} n=${1} title="Question" testid="question"
        line=${html`<strong>${reform?.name || id}</strong> in ${stateName(sc.state)}: ${`${q?.measure || "additional homes authorized by building permits"}, ${q?.window || e?.span || "2027–2031"}.`}`}>
        <ul class="chip-row">
          ${q?.settings && html`<li class="chip">${q.settings}</li>`}
          ${q?.as_of && html`<li class="chip">As of ${q.as_of}</li>`}
        </ul>
        ${q?.definition?.text && html`<${About} label="The reform as modelled">
          <p>${q.definition.text}</p>${q.definition.handbook_text && html`<p>Benchmark: ${q.definition.handbook_text}</p>`}<//>`}
      <//>
      ${hidden
        ? html`<${Step} n=${2} title="Answer" testid="answer" line=${hidden.measured?.text}>
          <${HiddenRange} view=${hidden} testid="tod-hidden-decision" />
        <//>`
        : html`<${Step} n=${2} title="Answer" testid="answer"
        line=${added && !compact && html`<span class="decision-big"><${Ticker} text=${`${added.shown.p10} to ${added.shown.p90}`} /></span>
          <span class="decision-sub">more homes, middle ${added.shown.p50}</span>`}>
        <${LeverFlag} run=${run} id=${id} />
        ${(ro?.frequency || e?.headline?.frequency) && html`<p class="decision-sentence" data-testid="frequency">${(ro?.frequency || e.headline.frequency).text}</p>`}
        ${years.length > 0
          ? html`<${YearBars} title="By year (middle, with the likely range)"
              items=${years.map((y) => ({ ...y, label: String(y.year) }))}
              note=${[d.annual.label, d.annual.note].filter(Boolean).join(". ")} />`
          : added && html`<${RangeBar} label="Homes added" n=${added} lo=${scaleOf([added])[0]} hi=${scaleOf([added])[1]} />`}
        <${PartialLabel} view=${statusView(data, id)} />
      <//>`}
      <${Step} n=${3} title="Where and why" testid="where"
        line=${html`${d?.top_county && html`<span class="decision-fact">Most in <strong>${d.top_county.name}</strong>: ${d.top_county.added.shown.p50}.</span> `}
          ${b && html`<span class="decision-fact">Binding constraint: <strong>${b.name}</strong> (${b.actor_word.toLowerCase()})${b.lost_text ? `, ${b.lost_text} lost` : ""}.</span> `}
          ${(top || nar?.items?.[0]) && html`<span class="decision-fact">Top driver: ${top ? top.label : nar.items[0].label}.</span>`}`}>
        ${d?.coverage &&
        html`<${StackBar} title="Evidence coverage of the counties"
          items=${d.coverage.counts.map((c) => ({ label: c.word, value: c.count, text: c.text }))}
          note=${`${d.coverage.sentence} ${d.coverage.rule}`} />`}
        ${onTab &&
        html`<div class="decision-links">
          <button type="button" class="btn btn-ghost btn-xs" onClick=${() => onTab("drivers")}>Drivers<${Icon} name="chevronRight" size=${14} /></button>
          <button type="button" class="btn btn-ghost btn-xs" onClick=${() => onTab("limits")}>Limits<${Icon} name="chevronRight" size=${14} /></button>
        </div>`}
      <//>
      <${Step} n=${4} title="What to change" testid="change"
        line=${b?.question ? html`Ask next: ${b.question}` : "Move one input and compare, on the same simulated futures."}>
        ${!lever && onTab &&
        html`<div class="decision-links">
          <button type="button" class="btn btn-ghost btn-xs" onClick=${() => onTab("assumptions")}>Assumptions<${Icon} name="chevronRight" size=${14} /></button>
        </div>`}
        ${lever &&
        (IS_STATIC
          ? html`<p class="decision-hint" role="note">Try “${lever.label}” at its high value: ${LOCAL_ONLY}</p>`
          : html`<p class="decision-hint">${moved ? "The input moved:" : "The input that matters most:"} “${lever.label}” (<code>${lever.parameter}</code>)${moved ? ", now at its high value." : "."}</p>
            <button type="button" class=${`btn btn-xs decision-try ${moved ? "btn-quiet" : "btn-primary"}`} aria-pressed=${moved ? "true" : "false"}
              onClick=${toggleLever}>${moved ? html`<${Icon} name="chevronLeft" size=${14} /><span>Back to the benchmark</span>` : "Try it at its high value (same futures)"}</button>`)}
      <//>
      <${Step} n=${5} title="Use it" testid="use">
        <div class="decision-links">
          <button type="button" class="btn btn-quiet btn-xs" onClick=${() => openBrief(sc)}><${Icon} name="page" size=${14} /><span>One-page brief</span></button>
          <button type="button" class="btn btn-quiet btn-xs" onClick=${() => downloadCsv(sc)}><${Icon} name="download" size=${14} /><span>Table (CSV)</span></button>
          <button type="button" class="btn btn-quiet btn-xs" onClick=${shareLink}><${Icon} name="link" size=${14} /><span>Share link</span></button>
        </div>
      <//>
    </ol>
  </section>`;
}

/** Cost row titles by readout name (the engine's ref ending). */
const COST_LABELS = {
  revenue_given_up: "Fee revenue given up, five years",
  cost_per_added_home: "Public cost per added home",
  private_saving_per_home: "Builders' saving per home reached",
  private_saving_total: "Builders' saving, five years",
};

/** One cost sentence as a tile: the middle text big, the range under it, the sentence in the fold. */
function CostTile({ t }) {
  const p50 = (t.cites || []).find((c) => c.field === "p50");
  if (!p50) return html`<p class="decision-hint">${t.text}</p>`;
  const name = p50.ref.split(".").pop();
  const lo = cite(t, name, "p10");
  const hi = cite(t, name, "p90");
  if (!COST_LABELS[name]) return html`<p class="decision-hint">${t.text}</p>`;
  return html`<div class="cost-tile" data-readout=${name}>
    <span class="cost-label">${COST_LABELS[name]}</span>
    <strong class="cost-value">${p50.shown}</strong>
    ${lo && hi && html`<span class="cost-range">likely ${lo} to ${hi}</span>`}
  </div>`;
}

/** The paired readouts and the cost row of one reform. */
export function HowSure({ run, id }) {
  const ro = readoutSentences(run.data, id);
  if (!ro) return null;
  const chances = (ro.chances || []).map((c) => {
    const name = c.ref.split(".").pop();
    const se = cite(c.text, `${name}_se`, "value");
    return {
      label: `${cite(c.text, c.parameter, "central")} homes`,
      value: c.chance,
      text: `${cite(c.text, name, "value")}%`,
      // The Monte Carlo standard error, the engine's own text (percentage points).
      se: se ? `${se} percentage points` : "",
      seShort: se ? `SE ${se} pts` : "",
    };
  });
  const sb = ro.share_of_baseline;
  const cost = ro.cost;
  const tiles = (cost?.texts || []).filter((t) => COST_LABELS[(t.cites || []).find((c) => c.field === "p50")?.ref.split(".").pop()]);
  const rest = (cost?.texts || []).filter((t) => !tiles.includes(t));
  const targeted = cost?.targeted || [];
  return html`<section class="how-sure" aria-labelledby="how-sure-title" data-testid="readouts">
    <h2 class="section-head" id="how-sure-title">How sure</h2>
    ${chances.length > 0 &&
    html`<${StepChart} title="Share of modeled draws in which it adds at least…" items=${chances} xLabel="At least"
      valueLabel="Share of modeled draws"
      note=${[ro.paired?.text, ...(ro.chances || []).map((c) => c.text.text)].filter(Boolean).join(" ")} />`}
    ${sb?.shown &&
    html`<div class="readout-row" data-readout="share_of_baseline">
      <p class="readout-label">${sb.label.text}</p>
      <p class="readout-value"><strong>${sb.shown.p10} to ${sb.shown.p90}</strong> <span class="decision-sub">middle ${sb.shown.p50}</span></p>
      <${RangeBar} label=${sb.label.text} n=${sb} lo=${scaleOf([sb])[0]} hi=${scaleOf([sb])[1]} />
    </div>`}
    ${cost &&
    html`<div class="cost-row" data-testid="cost-row">
      <p class="readout-label">What it costs</p>
      ${tiles.length > 0
        ? html`<div class="cost-tiles">${tiles.map((t) => html`<${CostTile} t=${t} />`)}</div>`
        : html`<p class="decision-hint" data-testid="cost-framing">${cost.framing?.text}</p>`}
      ${rest.map((t) => html`<p class="decision-hint">${t.text}</p>`)}
      ${targeted.length > 0 &&
      html`<${HBars} title="Cost per added home if the cut is limited to one group"
        items=${targeted.map((g) => ({ key: g.ref, label: g.group, value: g.p50, text: cite(g.text, g.ref.split(".").pop(), "p50") }))}
        note=${[cost.framing?.text, cost.state_fees?.text, ...targeted.map((g) => g.text.text)].filter(Boolean).join(" ")} />`}
      ${tiles.length > 0 && cost.framing?.text && html`<${About} label="How the cost is counted"><p>${cost.framing.text}</p>${cost.state_fees && html`<p>${cost.state_fees.text}</p>`}<//>`}
    </div>`}
  </section>`;
}

/** "What would narrow the range": variance shares, the leftover, the research agenda. */
export function Narrowing({ run, id }) {
  const n = narrowing(run.data, id);
  if (!n?.items?.length) return null;
  const items = n.items.map((x) => ({
    key: x.parameter,
    label: x.label,
    value: x.share,
    text: `${cite(x.text, x.ref, "share") || "—"}%`,
  }));
  return html`<section class="narrowing" aria-labelledby="narrowing-title" data-testid="narrowing">
    <h2 class="section-head" id="narrowing-title">What would narrow the range</h2>
    ${n.agenda?.[0] && html`<p class="decision-sentence" data-testid="agenda">${n.agenda[0].text}</p>`}
    <${HBars} title="Share of the uncertainty from each input" items=${items}
      note=${[n.lead?.text, n.leftover?.text?.text, ...(n.agenda || []).slice(1).map((a) => a.text)].filter(Boolean).join(" ")} />
  </section>`;
}

/** The binding constraint: the funnel's losses grouped by who acts, and what to ask next. */
export function BindingCard({ map, id }) {
  const bc = decisionOf(map, id)?.binding;
  if (!bc) return null;
  const b = bc.binding;
  const loss = bc.kind === "loss";
  const items = loss
    ? bc.stages
        .filter((s) => s.kind === "loss")
        .map((s) => ({ key: s.id, label: `${s.name} · ${s.actor_word}`, value: s.lost, text: `${s.lost_text} lost` }))
    : [];
  return html`<section class="card binding" id="binding" aria-labelledby="binding-title" data-testid="binding">
    <header class="card-head"><div>
      <h2 class="card-title" id="binding-title">Binding constraint</h2>
      <p class="card-sub">${b.sentence}</p>
    </div></header>
    <div class="card-body">
      ${loss && html`<${HBars} title="Share lost at each stage" items=${items} max=${1}
        note=${[bc.rule, ...bc.stages.map((s) => `${s.name}: ${s.formula}`)].join(" ")} />`}
      ${!loss && b.value?.shown && html`<p class="readout-value"><strong>${b.value.shown.value || b.value.shown.p50}</strong> <span class="decision-sub">${b.step}</span></p>`}
      <ul class="actor-row" aria-label="By who acts">
        ${bc.by_actor.map((g) => html`<li class=${`actor-chip actor-${g.actor}`}><strong>${g.word}</strong>${g.lost_text ? ` · up to ${g.lost_text} lost` : ""}</li>`)}
      </ul>
      <p class="decision-ask"><strong>Ask next:</strong> ${b.question}</p>
      <${About} label="Why this stage"><p>${b.why}</p><p>${bc.rule}</p><//>
    </div>
  </section>`;
}
