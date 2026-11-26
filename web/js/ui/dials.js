/**
 * PolicyDial({state, reform}): "how much reform?" for one reform.
 *
 * A slider that snaps to the levels the engine ran (3-4 per dial, the benchmark marked),
 * the middle estimate and likely range at the chosen level, "each step adds about X homes"
 * and the diminishing-return note, a dose-response chart (the middle as a line, P10-P90 as a
 * band), the target seeker ("What would it take to add N homes?", looked up in the engine's
 * own table, "out of reach" where the curve tops out), and for ADUs the take-up threshold
 * sentence. A reform with two dials (ADU: lot reach and the owner-occupancy rule) gets a
 * switch between them.
 *
 * Keyboard: the slider is a native range input, so Left / Right (and Up / Down, Home, End)
 * move one level at a time; its spoken value is the level's label and middle estimate.
 *
 * Map hook: every time the chosen level changes, the dial sends a `vh:dial-level` event on
 * `window` (`DIAL_EVENT`), detail:
 *   {state, reform, dial, level, label, benchmark, record, counties, levels, moved}
 * where `counties` is the engine's county table for that level ({columns: [county, p10, p50,
 * p90], rows}), `levels` the dial's every level (for one scale) and `moved` true once the
 * reader has picked a level. When the dial goes away it sends {state, reform, off: true}.
 * The map (ui/columns.js) listens: a moved dial's level grows or sinks the county columns and
 * recolours the county fills without asking the server again (see docs/modules/web.md,
 * "Dial levels on the map").
 *
 * Data: the "dials" part of GET /api/v1/states/ST/comparisons (`comparisonPart`, shared with
 * comparisons.js); every number is the server's text. The chart only places values.
 */
import { adoptionThreshold, dialCurve, dialTargets } from "../lib/api.js";
import { html, useEffect, useId, useState } from "../lib/html.js";
import { CardFrame } from "./cards-common.js";
import { About, ChartFrame } from "./charts.js";
import { COMPARISONS_WAIT, useComparison } from "./comparisons.js";

/** The event a dial sends on `window` when its level changes (see the module note). */
export const DIAL_EVENT = "vh:dial-level";

const W = 100;
const H = 44;
const PAD = 4;

function DoseChart({ dial, at }) {
  const lv = dial.levels || [];
  const n = lv.length;
  const top = Math.max(0, ...lv.map((l) => l.record?.p90).filter(Number.isFinite));
  const x = (i) => (n > 1 ? PAD + (i * (W - 2 * PAD)) / (n - 1) : W / 2);
  const y = (v) => (top > 0 && Number.isFinite(v) ? H - PAD - (Math.max(0, v) / top) * (H - 2 * PAD) : H - PAD);
  const line = lv.map((l, i) => `${i ? "L" : "M"}${x(i).toFixed(2)} ${y(l.record?.p50).toFixed(2)}`).join(" ");
  const band =
    lv.map((l, i) => `${i ? "L" : "M"}${x(i).toFixed(2)} ${y(l.record?.p90).toFixed(2)}`).join(" ") +
    " " +
    lv
      .map((l, i) => [l, i])
      .reverse()
      .map(([l, i]) => `L${x(i).toFixed(2)} ${y(l.record?.p10).toFixed(2)}`)
      .join(" ") +
    " Z";
  const bi = lv.findIndex((l) => l.benchmark);
  const rows = lv.map((l) => [l.label, l.record?.shown?.p50, l.record?.likely]);
  const summary = `${dial.title}: ${lv.map((l) => `${l.label} about ${l.record?.shown?.p50} homes (${l.record?.likely})`).join("; ")}.`;
  return html`<${ChartFrame} title="Homes added at each level, 2027-2031" summary=${summary}
      cols=${["Level", "Middle (P50)", "Likely range (P10 to P90)"]} rows=${rows} className="cmp-chart dial-chart">
    <div class="dial-draw">
      <svg viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none" class="dial-svg" aria-hidden="true" focusable="false">
        <path class="dial-band" d=${band} />
        ${bi >= 0 && html`<line class="dial-bench" x1=${x(bi)} x2=${x(bi)} y1="0" y2=${H} />`}
        <path class="dial-line" d=${line} />
      </svg>
      ${lv.map(
        (l, i) => html`<span class=${`dial-dot ${i === at ? "is-on" : ""} ${l.benchmark ? "is-bench" : ""}`}
          style=${`--x: ${((100 * x(i)) / W).toFixed(2)}; --y: ${((100 * y(l.record?.p50)) / H).toFixed(2)}`}></span>`,
      )}
    </div>
  <//>`;
}

export function PolicyDial({ state, reform, h = 2 }) {
  const st = useComparison(state, "dials");
  const all = dialCurve(st.data) || {};
  const mine = Object.values(all).filter((d) => d.reform === reform);
  const [which, setWhich] = useState(0);
  const dial = mine[Math.min(which, Math.max(0, mine.length - 1))];
  const levels = dial?.levels || [];
  const bench = Math.max(0, levels.findIndex((l) => l.benchmark));
  // The chosen level belongs to one dial: another dial (or a fresh mount) starts at the
  // benchmark. Kept with its dial's key, not reset by an effect, so an arrow key pressed right
  // after the dial draws is never undone by a late reset.
  const [pick, setPick] = useState({ key: null, i: null });
  const mineNow = pick.key === dial?.key && pick.i !== null && pick.i < levels.length;
  const at = mineNow ? pick.i : bench;
  const lv = levels[at];
  const targets = dial ? dialTargets(st.data, dial.key) : [];
  const [goal, setGoal] = useState(0);
  const row = targets[Math.min(goal, Math.max(0, targets.length - 1))];
  const adoption = reform === "adu" ? adoptionThreshold(st.data) : null;
  const aRow = adoption?.rows?.find((r) => r.homes === row?.homes);
  const sid = useId();

  // Tell the map the level (ui/columns.js shows it once the reader has moved the dial).
  useEffect(() => {
    if (!dial || !lv) return;
    window.dispatchEvent(
      new CustomEvent(DIAL_EVENT, {
        detail: { state, reform, dial: dial.key, level: lv.key, label: lv.label, benchmark: !!lv.benchmark, record: lv.record, counties: lv.counties, levels, moved: mineNow },
      }),
    );
  }, [state, reform, dial?.key, lv?.key]);
  // Gone (the Reform screen left, another reform): the map goes back to the scenario.
  useEffect(() => () => window.dispatchEvent(new CustomEvent(DIAL_EVENT, { detail: { state, reform, off: true } })), [state, reform]);

  const empty = st.data && !dial;
  return html`<${CardFrame} id=${`dial-${reform}-${state}`} testid="policy-dial" title="How much reform?" h=${h}
      state=${st} what="the policy dial" wait=${COMPARISONS_WAIT} className="cmp-card dial-card"
      line=${empty ? "No policy dial for this reform." : dial?.headline || ""}>
    ${dial &&
    html`${mine.length > 1 &&
      html`<div class="ec-seg" role="group" aria-label="Dial">
        ${mine.map((d, i) => html`<button type="button" class="ec-seg-btn" aria-pressed=${d === dial ? "true" : "false"} onClick=${() => setWhich(i)}>${d.title}</button>`)}
      </div>`}
      <div class="dial-control">
        <label class="dial-label" for=${sid}>${dial.title}</label>
        <input id=${sid} type="range" class="dial-range" min="0" max=${levels.length - 1} step="1" value=${at}
          style=${`--n: ${levels.length}`}
          aria-valuetext=${`${lv?.label}: about ${lv?.record?.shown?.p50} homes, likely ${lv?.record?.likely}`}
          onInput=${(e) => setPick({ key: dial.key, i: Number(e.currentTarget.value) })} />
        <ol class="dial-ticks" style=${`--n: ${levels.length}`} aria-hidden="true">
          ${levels.map(
            (l, i) => html`<li class=${`${i === at ? "is-on" : ""} ${l.benchmark ? "is-bench" : ""}`}>
              <span class="dial-tick-x">${l.x_text}</span>${l.benchmark && html`<span class="dial-bench-tag">benchmark</span>`}
            </li>`,
          )}
        </ol>
      </div>
      <p class="dial-readout" role="status" aria-live="polite">
        <span class="dial-level">${lv?.label}</span>
        <span class="dial-num"><strong>${lv?.record?.shown?.p50}</strong> homes</span>
        <span class="dial-range-text">likely range ${lv?.record?.likely}</span>
      </p>
      <${DoseChart} dial=${dial} at=${at} />
      <p class="dial-step">${dial.each_step?.sentence}</p>
      <p class="dial-dim ec-quiet">${dial.diminishing?.sentence}</p>
      ${targets.length > 0 &&
      html`<div class="dial-seek">
        <label class="dial-label" for=${`${sid}-goal`}>What would it take to add</label>
        <select id=${`${sid}-goal`} class="dial-goal" value=${String(Math.min(goal, targets.length - 1))} onChange=${(e) => setGoal(Number(e.currentTarget.value))}>
          ${targets.map((t, i) => html`<option value=${String(i)}>${t.homes_text} homes</option>`)}
        </select>
        <span class="dial-label">?</span>
        <dl class="dial-seek-out" aria-live="polite">
          <div class=${row?.p50?.status === "out_of_reach" ? "is-out" : ""}><dt>In the middle future</dt><dd>${row?.p50?.text}</dd></div>
          <div class=${row?.p10?.status === "out_of_reach" ? "is-out" : ""}><dt>In 9 of 10 futures</dt><dd>${row?.p10?.text}</dd></div>
        </dl>
      </div>`}
      ${aRow && html`<p class="dial-adopt"><span class="dial-adopt-tag">Or by take-up alone: </span>${aRow.p50?.sentence}</p>`}
      <${About} label="How the dial works">
        <p>Every other reform stays at its benchmark; every level uses the same seed (${st.data.draws} draws), so the levels differ only because of the dial.</p>
        <p>${dial.each_step?.formula}. ${dial.diminishing?.rule}.</p>
        <p>${dial.targets?.rule}.</p>
        ${adoption && html`<p>${adoption.rule}. Ledger range ${adoption.range_text} (middle ${adoption.central_text}).</p>
          <ul class="cmp-list">${adoption.rows.map((r) => html`<li>${r.p50?.sentence} ${r.p10?.sentence}</li>`)}</ul>`}
        <p class="ec-quiet">${lv?.record?.formula}</p>
      <//>`}
  <//>`;
}
