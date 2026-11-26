/**
 * One slide's face, drawn on the 960 x 540 logical frame (css/slides.css). The Slides view
 * and the live "Brief" preview in the side panels draw the same face, so they always agree.
 *
 * Every number on it is the engine's text from `explanation.slides`. The elements marked
 * `data-count` (the big range and the chart values) fade in (already the final text) when the
 * slide enters view (see `useReveal` in motion.js); the bars draw in and the sentences fade
 * in one after another. With reduced motion the slide shows its final state at once.
 */
import { shortHash } from "../lib/format.js";
import { html } from "../lib/html.js";
import { RangeBar, scaleOf } from "./bits.js";

export function stampText(stamp) {
  return `Code ${shortHash(stamp.code)} · Data ${shortHash(stamp.data)} · Assumptions ${shortHash(stamp.assumptions)}`;
}

export function rangeOf(sh) {
  if (!sh) return "";
  if (sh.p10 !== undefined) return `${sh.p10} to ${sh.p90}`;
  return sh.value || "";
}

function Chart({ chart }) {
  if (!chart?.items?.length) return null;
  if (chart.type === "tornado") {
    const vals = chart.items.flatMap((x) => [x.total_low, x.total_high]).concat([chart.center]);
    const lo = Math.min(0, ...vals);
    const hi = Math.max(0, ...vals);
    const at = (v) => `${(100 * (v - lo)) / (hi - lo || 1)}%`;
    return html`<ol class="slide-chart">
      ${chart.items.map((x, i) => {
        const a = Math.min(x.total_low, x.total_high);
        const b = Math.max(x.total_low, x.total_high);
        return html`<li class="slide-chart-row" style=${`--i: ${i + 3}`}>
          <span class="slide-chart-label">${x.label.text}</span>
          <span class="tornado-bar"><span class="tornado-band" style=${`left: ${at(a)}; width: calc(${at(b)} - ${at(a)})`}></span>
            <span class="tornado-mid" style=${`left: ${at(chart.center)}`}></span></span>
          <span class="slide-chart-value" data-count="">${`${x.shown.total_low} / ${x.shown.total_high}`}</span>
        </li>`;
      })}
    </ol>`;
  }
  const ranged = chart.items.filter((x) => x.shown?.p10 !== undefined);
  const scales = {};
  for (const u of new Set(ranged.map((x) => x.unit))) scales[u] = scaleOf(ranged.filter((x) => x.unit === u));
  return html`<ol class="slide-chart">
    ${chart.items.slice(0, 7).map(
      (x, i) => html`<li class="slide-chart-row" style=${`--i: ${i + 3}`}>
        <span class="slide-chart-label">${x.label.text}</span>
        ${x.shown?.p10 !== undefined
          ? html`<${RangeBar} label=${x.label.text} n=${x} lo=${scales[x.unit]?.[0]} hi=${scales[x.unit]?.[1]} />`
          : html`<span></span>`}
        <span class="slide-chart-value" data-count="">${rangeOf(x.shown)}</span>
      </li>`,
    )}
  </ol>`;
}

/** The `.slide` block of one slide: kicker, title, big range, sentences, chart, sources, footer. */
export function SlideFace({ slide, index, total, stamp }) {
  const srcs = slide.sources || [];
  // The disclaimer goes in the footer of every slide, so the body keeps the findings.
  const body = slide.body.filter((b) => b !== stamp.disclaimer);
  return html`<div class=${`slide slide--${slide.kind}`}>
    <p class="slide-kicker" style="--i: 0">${slide.kicker}</p>
    <h2 class="slide-title" style="--i: 1">${slide.title}</h2>
    <div class="slide-main slide-split">
      <div class="slide-left">
        ${slide.big &&
        html`<div class="slide-big" style="--i: 2">
          <span class="slide-big-value" data-count="">${slide.big.text}</span>
          <span class="slide-stat-label">${slide.big.label}</span>
          <span class="slide-stat-label">${slide.big.middle}</span>
        </div>`}
        ${slide.kind === "limits"
          ? html`<ul class="slide-bullets">${body.map((b, i) => html`<li style=${`--i: ${i + 2}`}>${b}</li>`)}</ul>`
          : body.slice(0, 2).map((b, i) => html`<p class="slide-text" style=${`--i: ${i + 3}`}>${b}</p>`)}
      </div>
      ${slide.chart && html`<div class="slide-right"><${Chart} chart=${slide.chart} /></div>`}
    </div>
    ${srcs.length > 0 &&
    html`<p class="slide-sources">Sources: ${srcs.slice(0, 3).map((s) => s.citation).join("; ")}${srcs.length > 3 ? `; and ${srcs.length - 3} more` : ""}.</p>`}
    <footer class="slide-foot">
      <span>VisionHousing · ${stamp.disclaimer ? `${stamp.disclaimer} · ` : ""}${stampText(stamp)}</span>
      <span>${index + 1} / ${total}</span>
    </footer>
  </div>`;
}
