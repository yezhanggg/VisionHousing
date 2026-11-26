/**
 * Small charts drawn as plain SVG (and a little HTML), with no chart library.
 *
 * Nothing here makes a number. Every value and every text form comes from the API: a chart
 * only places what it is given (the length of a bar, the sweep of a slice). Where the API
 * sends a share, the drawing uses it; where it sends a text form, the label shows it.
 *
 *   Donut      a ring of slices that sweeps in; pointing at a slice or a legend row lights
 *              both and shows that slice's label and share in the middle.
 *   HBars      horizontal bars that grow from zero, one row per item, optionally pressable.
 *   StackBar   one bar split into parts (a 100% stack) with its legend.
 *   RangeMini  a small likely-range bar: a band from P10 to P90 and a mark at P50.
 *   Sparkline  a small line over time that draws itself in.
 *   RankRows   ranks as words ("12th of 67") with a direction arrow.
 *   StepChart  "share of modeled draws with at least N" as falling steps (the paired readouts),
 *              with each share's Monte Carlo standard error (the engine's text) when given.
 *   YearBars   one column per year (P50) with its P10–P90 whisker.
 *   About      the "(i) About this chart" fold that holds a chart's source and method words.
 *   ProfileChart  draws one chart of a place profile section from the API's chart schema:
 *              {id, kind: "pie" | "bar" | "stack" | "rank", title, unit,
 *               items: [{label, value, share, moe, text?}], total, compare, source_ids,
 *               vintage, note}
 *
 * Every chart is a figure: the drawing is `role="img"` with a one-line summary as its label,
 * and a "Table" button shows the same values as a table (the chart's text twin). Its source
 * line and method words sit behind "(i) About this chart", so the chart stays short. Motion
 * (sweep, growth, draw-in) is CSS on `.chart-*`; reduced motion shows the final state.
 */
import { plain, percent, withUnit } from "../lib/format.js";
import { html, useId, useState } from "../lib/html.js";

/** Fill tokens for the parts of a chart, in order (the map's ramp, darkest first). */
export const PART_COLOURS = ["--seq-6", "--seq-5", "--seq-4", "--seq-3", "--seq-2", "--accent", "--line-strong", "--no-data"];
const colour = (i) => `var(${PART_COLOURS[i % PART_COLOURS.length]})`;

/** The text form of an item's value: the API's text if it sent one, else the value with its unit. */
export function valueText(item, unit = "") {
  if (item.text) return item.text;
  if (item.value === null || item.value === undefined) return "—";
  return typeof item.value === "number" ? withUnit(item.value, item.unit || unit) : String(item.value);
}

/** The text form of an item's share (0–1), or "". */
export function shareText(item) {
  if (item.share_text) return item.share_text;
  return Number.isFinite(item.share) ? percent(item.share) : "";
}

/** The part of the whole each item stands for, for drawing only (the API's share when sent). */
function fractions(items) {
  if (items.every((x) => Number.isFinite(x.share))) return items.map((x) => Math.max(0, x.share));
  const vals = items.map((x) => (Number.isFinite(x.value) ? Math.max(0, x.value) : 0));
  const sum = vals.reduce((a, b) => a + b, 0) || 1;
  return vals.map((v) => v / sum);
}

/** The words of a chart for a screen reader: "Title: A 40%, B 35%, C 25%." */
export function chartSummary(title, items, unit = "") {
  const parts = items.map((x) => `${x.label} ${shareText(x) || valueText(x, unit)}`);
  return `${title}: ${parts.join(", ")}.`;
}

/**
 * A chart with its title, the drawing and a table twin one press away.
 * `rows` is [[label, value text, share text?]]; `cols` the column heads.
 */
export function ChartFrame({ title, summary, note, cols, rows, children, className = "" }) {
  const [table, setTable] = useState(false);
  const id = useId();
  return html`<figure class=${`chart ${className}`}>
    ${title && html`<figcaption class="chart-title">${title}</figcaption>`}
    <div class="chart-draw" role="img" aria-label=${summary || title}>${children}</div>
    ${rows?.length > 0 &&
    html`<button type="button" class="chart-table-btn" aria-expanded=${table ? "true" : "false"} aria-controls=${id}
        onClick=${() => setTable(!table)}>${table ? "Hide table" : "Table"}</button>
      <div class="chart-table" id=${id} hidden=${!table}>
        <table class="table">
          <caption class="visually-hidden">${title}</caption>
          <thead><tr>${cols.map((c, i) => html`<th scope="col" class=${i ? "num" : ""}>${c}</th>`)}</tr></thead>
          <tbody>${rows.map((r) => html`<tr><th scope="row">${r[0]}</th>${r.slice(1).map((v) => html`<td class="num">${v}</td>`)}</tr>`)}</tbody>
        </table>
      </div>`}
    ${note && html`<${About}>${note}<//>`}
  </figure>`;
}

/**
 * "(i) About this chart": a chart's source line and longer method words, folded away so the
 * chart itself stays short. A native disclosure (keyboard and screen readers work as is).
 */
export function About({ children, label = "About this chart" }) {
  return html`<details class="about">
    <summary class="about-sum"><span class="about-i" aria-hidden="true">i</span>${label}</summary>
    <div class="about-body">${children}</div>
  </details>`;
}

/**
 * A step chart of draw shares: "share of modeled draws with at least N" for rising N, a step
 * down at each N. `items`: [{label (the N as the API wrote it), value (0–1, the API's share),
 * text, se?}] where `se` is the standard error's text from the server. The height of each step is
 * the API's share; nothing is worked out here.
 */
export function StepChart({ title, items = [], note, xLabel = "", valueLabel = "Share of modeled draws" }) {
  const n = items.length;
  const W = 100;
  const H = 40;
  const step = n ? W / n : W;
  const y = (v) => H - Math.max(0, Math.min(1, v)) * (H - 4);
  let d = "";
  items.forEach((x, i) => {
    const yy = y(Number.isFinite(x.value) ? x.value : 0).toFixed(2);
    d += `${i ? "L" : "M"}${(i * step).toFixed(2)} ${yy} L${((i + 1) * step).toFixed(2)} ${yy} `;
  });
  const se = items.some((x) => x.se);
  const rows = items.map((x) => [x.label, x.text, ...(se ? [x.se || "—"] : [])]);
  return html`<${ChartFrame} title=${title} summary=${chartSummary(title, items)} note=${note}
      cols=${[xLabel || "At least", valueLabel, ...(se ? ["Monte Carlo standard error"] : [])]} rows=${rows} className="chart-step">
    <div class="step-chart">
      <svg viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none" class="step-svg" aria-hidden="true" focusable="false">
        ${items.map((x, i) => html`<rect class="step-col" x=${(i * step + 0.6).toFixed(2)} y=${y(x.value || 0).toFixed(2)}
          width=${(step - 1.2).toFixed(2)} height=${(H - y(x.value || 0)).toFixed(2)} style=${`--i: ${i}`} />`)}
        <path class="step-line" d=${d.trim()} pathLength="1" />
      </svg>
      <ol class="step-labels" style=${`--n: ${n}`}>
        ${items.map((x) => html`<li><strong>${x.text}</strong><span>${x.label}</span>${x.se && html`<span class="step-se" title=${`Monte Carlo standard error ${x.se}`}>${x.seShort || x.se}</span>`}</li>`)}
      </ol>
    </div>
  <//>`;
}

/**
 * Columns by year with a likely-range whisker: `items` [{label (year), p10, p50, p90, shown}].
 * Bars stand for the API's P50; the whisker spans P10–P90; the labels are the API's text.
 */
export function YearBars({ title, items = [], note }) {
  const top = Math.max(0, ...items.flatMap((x) => [x.p90, x.p50]).filter(Number.isFinite));
  const h = (v) => (top > 0 && Number.isFinite(v) ? Math.max(0, Math.min(1, v / top)) : 0);
  const rows = items.map((x) => [x.label, x.shown?.p50 || "—", x.shown ? `${x.shown.p10} to ${x.shown.p90}` : "—"]);
  const summary = `${title}: ${items.map((x) => `${x.label} ${x.shown?.p50 || "—"}`).join(", ")}.`;
  return html`<${ChartFrame} title=${title} summary=${summary} note=${note}
      cols=${["Year", "Middle", "Likely range"]} rows=${rows} className="chart-years">
    <ol class="year-cols" style=${`--n: ${items.length}`}>
      ${items.map(
        (x, i) => html`<li class="year-bar" style=${`--i: ${i}`}>
          <span class="year-col">
            <span class="year-whisker" style=${`--lo: ${h(x.p10).toFixed(4)}; --hi: ${h(x.p90).toFixed(4)}`}></span>
            <span class="year-fill" style=${`--h: ${h(x.p50).toFixed(4)}`}></span>
          </span>
          <span class="year-value">${x.shown?.p50 || "—"}</span>
          <span class="year-label">${x.label}</span>
        </li>`,
      )}
    </ol>
  <//>`;
}

const R = 42;
const C = 2 * Math.PI * R;

/** A ring of slices. `items`: [{label, value, share, text}]. `centre`: text in the middle at rest. */
export function Donut({ title, items = [], unit = "", centre = "", centreLabel = "", note }) {
  const [hot, setHot] = useState(-1);
  const fr = fractions(items);
  let start = 0;
  const arcs = fr.map((f, i) => {
    const a = { i, f, start };
    start += f;
    return a;
  });
  const on = hot >= 0 ? items[hot] : null;
  const rows = items.map((x) => [x.label, valueText(x, unit), shareText(x)]);
  return html`<${ChartFrame} title=${title} summary=${chartSummary(title, items, unit)} note=${note}
      cols=${["Part", "Value", "Share"]} rows=${rows} className="chart-donut">
    <div class="donut" onMouseLeave=${() => setHot(-1)}>
      <svg viewBox="0 0 100 100" class="donut-svg" aria-hidden="true" focusable="false">
        <circle class="donut-track" cx="50" cy="50" r=${R} />
        ${arcs.map(
          (a) => html`<circle class=${`donut-arc ${hot === a.i ? "is-hot" : ""} ${hot >= 0 && hot !== a.i ? "is-dim" : ""}`}
            cx="50" cy="50" r=${R} style=${`--len: ${(a.f * C).toFixed(2)}; --gap: ${C.toFixed(2)}; --rot: ${(a.start * 360 - 90).toFixed(2)}deg; --i: ${a.i}; stroke: ${colour(a.i)}`}
            onMouseEnter=${() => setHot(a.i)} />`,
        )}
      </svg>
      <div class="donut-centre" aria-hidden="true">
        <strong class="donut-big">${on ? shareText(on) || valueText(on, unit) : centre}</strong>
        <span class="donut-small">${on ? on.label : centreLabel}</span>
      </div>
    </div>
    <ul class="chart-legend" aria-hidden="true">
      ${items.map(
        (x, i) => html`<li class=${`legend-row ${hot === i ? "is-hot" : ""}`} onMouseEnter=${() => setHot(i)}>
          <span class="legend-swatch" style=${`background: ${colour(i)}`}></span>
          <span class="legend-label">${x.label}</span>
          <span class="legend-value">${shareText(x) || valueText(x, unit)}${x.cmp && html`<small class="cmp">${x.cmp}</small>`}</span>
        </li>`,
      )}
    </ul>
  <//>`;
}

/**
 * Horizontal bars that grow in. `items`: [{key?, label, value, text, sub?}]. Bars share one
 * scale from 0 to the largest value (or `max`). `onPick(item)` makes each row a button.
 */
export function HBars({ title, items = [], unit = "", max = null, onPick = null, onHover = null, note, hotKey = "" }) {
  const vals = items.map((x) => (Number.isFinite(x.value) ? x.value : 0));
  const cmps = items.map((x) => x.cmpValue).filter(Number.isFinite);
  const top = max ?? Math.max(0, ...vals, ...cmps);
  const counts = items.some((x) => Number.isFinite(x.count));
  const rows = items.map((x) => [x.label, valueText(x, unit), ...(counts ? [withUnit(x.count, x.unit || unit)] : []), ...(x.cmp ? [x.cmp] : [])]);
  const cols = ["Item", "Value", ...(counts ? ["Count"] : []), ...(items.some((x) => x.cmp) ? ["Compared with"] : [])];
  const Row = (x, i) => {
    const w = top > 0 ? Math.max(0, Math.min(1, vals[i] / top)) : 0;
    const inner = html`<span class="hbar-label">${x.rank_text ? html`<span class="hbar-rank">${x.rank}</span>` : null}${x.label}</span>
      <span class="hbar-track"><span class="hbar-fill" style=${`--w: ${w.toFixed(4)}; --i: ${i}`}></span>
        ${Number.isFinite(x.cmpValue) && top > 0 &&
        html`<span class="hbar-cmp" style=${`--x: ${(100 * Math.min(1, x.cmpValue / top)).toFixed(2)}%; --i: ${i}`}></span>`}</span>
      <span class="hbar-value">${valueText(x, unit)}${x.cmp && html`<small class="cmp">${x.cmp}</small>`}</span>`;
    const hover = onHover ? { onMouseEnter: () => onHover(x), onMouseLeave: () => onHover(null), onFocus: () => onHover(x), onBlur: () => onHover(null) } : {};
    return html`<li class=${`hbar ${hotKey && x.key === hotKey ? "is-hot" : ""}`} data-fips=${x.key} key=${x.key || x.label}>
      ${onPick
        ? html`<button type="button" class="hbar-btn" onClick=${() => onPick(x)} ...${hover}
            aria-label=${`${x.label}: ${valueText(x, unit)}${x.rank_text ? `, ${x.rank_text}` : ""}`}>${inner}</button>`
        : html`<div class="hbar-btn" ...${hover}>${inner}</div>`}
    </li>`;
  };
  return html`<${ChartFrame} title=${title} summary=${chartSummary(title, items, unit)} note=${note}
      cols=${cols} rows=${rows} className="chart-hbars">
    <ol class="hbars">${items.map(Row)}</ol>
  <//>`;
}

/** One bar split into parts (each part's share of the whole), with its legend. */
export function StackBar({ title, items = [], unit = "", note }) {
  const fr = fractions(items);
  const rows = items.map((x) => [x.label, valueText(x, unit), shareText(x)]);
  return html`<${ChartFrame} title=${title} summary=${chartSummary(title, items, unit)} note=${note}
      cols=${["Part", "Value", "Share"]} rows=${rows} className="chart-stack">
    <div class="stack-bar" aria-hidden="true">
      ${items.map((x, i) => html`<span class="stack-part" style=${`--w: ${(fr[i] * 100).toFixed(3)}%; --i: ${i}; background: ${colour(i)}`}
        title=${`${x.label}: ${shareText(x) || valueText(x, unit)}`}></span>`)}
    </div>
    <ul class="chart-legend is-inline" aria-hidden="true">
      ${items.map(
        (x, i) => html`<li class="legend-row"><span class="legend-swatch" style=${`background: ${colour(i)}`}></span>
          <span class="legend-label">${x.label}</span><span class="legend-value">${shareText(x) || valueText(x, unit)}${x.cmp && html`<small class="cmp">${x.cmp}</small>`}</span></li>`,
      )}
    </ul>
  <//>`;
}

/**
 * A small likely-range bar in SVG: band P10–P90, a mark at P50, on the scale lo–hi.
 * `n` is an engine record ({p10, p50, p90, shown}).
 */
export function RangeMini({ n, lo = 0, hi = 0, label = "" }) {
  const ok = n && Number.isFinite(n.p10) && Number.isFinite(n.p90) && hi > lo;
  const x = (v) => (100 * (Math.min(hi, Math.max(lo, v)) - lo)) / (hi - lo);
  const sh = n?.shown || {};
  return html`<svg class="range-mini" viewBox="0 0 100 10" preserveAspectRatio="none" role="img"
      aria-label=${ok ? `${label}: ${sh.p10} to ${sh.p90}, middle ${sh.p50}` : `${label}: not available yet`}>
    <rect class="rm-track" x="0" y="4" width="100" height="2" rx="1" />
    ${ok && html`<rect class="rm-band" x=${x(Math.min(n.p10, n.p90))} y="2" width=${Math.max(1.2, x(Math.max(n.p10, n.p90)) - x(Math.min(n.p10, n.p90)))} height="6" rx="3" />`}
    ${ok && Number.isFinite(n.p50) && html`<rect class="rm-mid" x=${x(n.p50) - 0.6} y="0" width="1.2" height="10" />`}
  </svg>`;
}

/** A small line that draws itself in. `points`: [{label, value, text?}] in time order. */
export function Sparkline({ title, points = [], unit = "", note }) {
  const vals = points.map((p) => p.value).filter(Number.isFinite);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const n = points.length;
  const xy = points.map((p, i) => [n > 1 ? (i * 100) / (n - 1) : 50, hi > lo ? 28 - (24 * (p.value - lo)) / (hi - lo) : 16]);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const last = points[n - 1];
  const rows = points.map((p) => [p.label, valueText(p, unit)]);
  const summary = n ? `${title}: from ${valueText(points[0], unit)} (${points[0].label}) to ${valueText(last, unit)} (${last.label}).` : title;
  return html`<${ChartFrame} title=${title} summary=${summary} note=${note} cols=${["When", "Value"]} rows=${rows} className="chart-spark">
    ${n > 0 &&
    html`<div class="spark">
      <svg viewBox="-2 0 104 32" class="spark-svg" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <path class="spark-line" d=${d} pathLength="1" />
        <circle class="spark-dot" cx=${xy[n - 1][0]} cy=${xy[n - 1][1]} r="2" />
      </svg>
      <span class="spark-end">${valueText(last, unit)}<small>${last.label}</small></span>
    </div>`}
  <//>`;
}

const ARROW = { up: "▲", high: "▲", higher: "▲", highest: "▲", down: "▼", low: "▼", lower: "▼", lowest: "▼" };

/** Ranks as words. `items`: [{label, text | rank_text, direction?, note?}]. */
export function RankRows({ title, items = [], note }) {
  const words = (x) => x.rank_text || x.text_rank || (Number.isFinite(x.rank) && x.of ? `${x.rank} of ${x.of}` : x.text || valueText(x));
  const rows = items.map((x) => [x.label, words(x)]);
  return html`<${ChartFrame} title=${title} summary=${chartSummary(title, items.map((x) => ({ ...x, text: words(x) })))} note=${note}
      cols=${["Measure", "Rank"]} rows=${rows} className="chart-ranks">
    <ul class="rank-words">
      ${items.map(
        (x, i) => html`<li class="rank-word" style=${`--i: ${i}`}>
          <span class="rank-word-label">${x.label}</span>
          <span class=${`rank-word-value dir-${x.direction || "none"}`}>
            ${ARROW[x.direction] && html`<span class="rank-arrow" aria-hidden="true">${ARROW[x.direction]}</span>`}${words(x)}</span>
          ${(x.value_text || x.words) && html`<span class="rank-word-note">${[x.value_text, x.words].filter(Boolean).join(": ")}</span>`}
          ${x.note && html`<span class="rank-word-note">${x.note}</span>`}
        </li>`,
      )}
    </ul>
  <//>`;
}

/** The "source, vintage" line under a profile chart. */
function sourceLine(chart, sources = {}) {
  const names = (chart.source_ids || []).map((id) => sources[id]?.name || id);
  const bits = [names.join("; "), chart.vintage].filter(Boolean);
  return [bits.join(", "), chart.note].filter(Boolean).map((t) => String(t).replace(/\.$/, "")).join(". ");
}

/**
 * One chart of a place profile section, from the API's chart schema. Unknown kinds fall back
 * to bars, so a new kind never breaks the page.
 */
export function ProfileChart({ chart, sources = {} }) {
  // The comparison place's share of each item (matched by key), shown beside the item.
  const cmp = chart.compare && typeof chart.compare === "object" ? chart.compare : null;
  const cmpShare = Object.fromEntries((cmp?.items || []).map((c) => [c.key, c.share]));
  const cmpWord = cmp?.label ? cmp.label.split(" (")[0] : "";
  const items = (chart.items || []).map((x) => ({
    ...x,
    unit: x.unit || chart.unit,
    // Bars of a whole read best as shares; the count is in the table twin.
    text: x.text || (chart.kind === "bar" && Number.isFinite(x.share) ? percent(x.share) : undefined),
    cmp: Number.isFinite(cmpShare[x.key]) ? `${cmpWord || "compare"} ${percent(cmpShare[x.key])}` : "",
    // With a comparison, bars are drawn as shares so the comparison's mark sits on the same scale.
    ...(chart.kind === "bar" && cmp && Number.isFinite(x.share) ? { value: x.share, cmpValue: cmpShare[x.key], count: x.value } : {}),
  }));
  const note = sourceLine(chart, sources);
  const total = chart.total;
  const totalText = total && typeof total === "object" ? total.text || plain(total.value) : Number.isFinite(total) ? withUnit(total, chart.unit) : total || "";
  const compare = chart.compare;
  const compareWords = typeof compare === "string" ? compare : compare?.text || (cmp?.label ? `Beside each part (and the grey mark on bars): ${cmp.label}` : "");
  const withCompare = compareWords ? `${note}${note ? ". " : ""}${compareWords}` : note;
  switch (chart.kind) {
    case "pie":
      return html`<${Donut} title=${chart.title} items=${items} unit=${chart.unit} centre=${totalText}
        centreLabel=${totalText ? "in all" : ""} note=${withCompare} />`;
    case "stack":
      return html`<${StackBar} title=${chart.title} items=${items} unit=${chart.unit} note=${withCompare} />`;
    case "rank":
      return html`<${RankRows} title=${chart.title} items=${items} note=${withCompare} />`;
    case "line":
      return html`<${Sparkline} title=${chart.title} points=${items} unit=${chart.unit} note=${withCompare} />`;
    default:
      return html`<${HBars} title=${chart.title} items=${items} unit=${chart.unit} note=${withCompare} />`;
  }
}
