/**
 * The Slides view (decision D-22): a 16:9 deck of the current scenario, written by the
 * engine's explanation layer from one run (`explanation.slides`).
 *   - Each slide is drawn on a 960 x 540 logical frame that scales to the window.
 *   - Left / Right arrows (and Page Up / Page Down, Home / End) move; Esc goes back.
 *   - "Read as a page" shows the same slides as plain text in one column.
 *   - Printing gives one slide per page (css/slides.css), which is how the slide PDF is made.
 *   - Every footer carries the run's three fingerprints: code, data and assumptions.
 * A reform run alone also gets its top-drivers slide (the run asks for the drivers).
 */
import { deckOf, stampOf } from "../content/slides.js";
import { stateName } from "../content/states.js";
import { learn, useRun } from "../lib/data.js";
import { shortHash } from "../lib/format.js";
import { html, useEffect, useLayoutEffect, useState } from "../lib/html.js";
import { hrefFor, lastScreen, scenario } from "../state/store.js";
import { RangeBar, scaleOf, Status } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";

function stampText(stamp) {
  return `Code ${shortHash(stamp.code)} · Data ${shortHash(stamp.data)} · Assumptions ${shortHash(stamp.assumptions)}`;
}

function range(sh) {
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
      ${chart.items.map((x) => {
        const a = Math.min(x.total_low, x.total_high);
        const b = Math.max(x.total_low, x.total_high);
        return html`<li class="slide-chart-row">
          <span class="slide-chart-label">${x.label.text}</span>
          <span class="tornado-bar"><span class="tornado-band" style=${`left: ${at(a)}; width: calc(${at(b)} - ${at(a)})`}></span>
            <span class="tornado-mid" style=${`left: ${at(chart.center)}`}></span></span>
          <span class="slide-chart-value">${x.shown.total_low} / ${x.shown.total_high}</span>
        </li>`;
      })}
    </ol>`;
  }
  const ranged = chart.items.filter((x) => x.shown?.p10 !== undefined);
  const scales = {};
  for (const u of new Set(ranged.map((x) => x.unit))) scales[u] = scaleOf(ranged.filter((x) => x.unit === u));
  return html`<ol class="slide-chart">
    ${chart.items.slice(0, 7).map(
      (x) => html`<li class="slide-chart-row">
        <span class="slide-chart-label">${x.label.text}</span>
        ${x.shown?.p10 !== undefined
          ? html`<${RangeBar} label=${x.label.text} n=${x} lo=${scales[x.unit]?.[0]} hi=${scales[x.unit]?.[1]} />`
          : html`<span></span>`}
        <span class="slide-chart-value">${range(x.shown)}</span>
      </li>`,
    )}
  </ol>`;
}

function Slide({ slide, index, total, current, stamp }) {
  const srcs = slide.sources || [];
  // The disclaimer goes in the footer of every slide, so the body keeps the findings.
  const body = slide.body.filter((b) => b !== stamp.disclaimer);
  return html`<section class=${`slide-frame ${index === current ? "" : "is-off"}`}
      aria-roledescription="slide" aria-label=${`Slide ${index + 1} of ${total}`}
      aria-hidden=${index === current ? undefined : "true"}>
    <div class=${`slide slide--${slide.kind}`}>
      <p class="slide-kicker">${slide.kicker}</p>
      <h2 class="slide-title">${slide.title}</h2>
      <div class="slide-main slide-split">
        <div class="slide-left">
          ${slide.big &&
          html`<div class="slide-big">
            <span class="slide-big-value">${slide.big.text}</span>
            <span class="slide-stat-label">${slide.big.label}</span>
            <span class="slide-stat-label">${slide.big.middle}</span>
          </div>`}
          ${slide.kind === "limits"
            ? html`<ul class="slide-bullets">${body.map((b) => html`<li>${b}</li>`)}</ul>`
            : body.slice(0, 2).map((b) => html`<p class="slide-text">${b}</p>`)}
        </div>
        ${slide.chart && html`<div class="slide-right"><${Chart} chart=${slide.chart} /></div>`}
      </div>
      ${srcs.length > 0 &&
      html`<p class="slide-sources">Sources: ${srcs.slice(0, 3).map((s) => s.citation).join("; ")}${srcs.length > 3 ? `; and ${srcs.length - 3} more` : ""}.</p>`}
      <footer class="slide-foot">
        <span>VisionHousing · ${stamp.disclaimer ? `${stamp.disclaimer} · ` : ""}${stampText(stamp)}</span>
        <span>${index + 1} / ${total}</span>
      </footer>
    </div>
  </section>`;
}

function TextVersion({ deck, stamp }) {
  return html`<article class="slides-text">
    ${deck.map(
      (s, i) => html`<section class="slides-text-item">
        <p class="kicker">Slide ${i + 1} · ${s.kicker}</p>
        <h2>${s.title}</h2>
        ${s.big && html`<dl class="slides-text-stats"><div><dt>${s.big.label}</dt><dd>${s.big.text} (${s.big.middle})</dd></div></dl>`}
        ${s.chart?.items?.length > 0 &&
        html`<ul>${s.chart.items.map((x) => html`<li>${x.label.text}: ${x.shown?.total_low !== undefined ? `${x.shown.total_low} / ${x.shown.total_high}` : range(x.shown)}</li>`)}</ul>`}
        ${s.body.map((b) => html`<p>${b}</p>`)}
      </section>`,
    )}
    <p class="slides-text-stamp">${stampText(stamp)}</p>
  </article>`;
}

export function SlidesScreen() {
  const sc = scenario.value;
  const run = useRun(sc, { drivers: sc.reforms.length === 1 });
  useEffect(() => learn(run.data), [run.data]);
  const deck = deckOf(run.data);
  const stamp = stampOf(run.data);
  const total = deck.length;
  const [current, setCurrent] = useState(0);
  const [asPage, setAsPage] = useState(false);
  const at = Math.max(0, Math.min(current, total - 1));

  const back = () => {
    const s = lastScreen.value;
    location.hash = hrefFor(s.name, s.param);
  };

  // Layout effect: the keys work from the first frame, not after the next paint.
  useLayoutEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const tag = (e.target?.tagName || "").toLowerCase();
      if (["input", "select", "textarea"].includes(tag)) return;
      if (e.target?.closest?.("[role=menu], .pop-panel, .mobile-menu")) return;
      const moves = { ArrowRight: 1, PageDown: 1, ArrowLeft: -1, PageUp: -1 };
      if (e.key in moves) {
        if (asPage) return;
        setCurrent((c) => Math.max(0, Math.min(total - 1, c + moves[e.key])));
      } else if (e.key === "Home" && !asPage) setCurrent(0);
      else if (e.key === "End" && !asPage) setCurrent(total - 1);
      else if (e.key === "Escape") back();
      else return;
      e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [asPage, total]);

  return html`<main id="main" class="screen screen-slides" tabindex="-1">
    <h1 class="visually-hidden">Slides: ${stateName(sc.state)}</h1>
    <div class="slides-bar" role="toolbar" aria-label="Slides">
      <div class="slides-nav">
        <button type="button" class="btn btn-quiet" onClick=${() => setCurrent(Math.max(0, at - 1))}
          disabled=${asPage || at === 0}><${Icon} name="chevronLeft" /><span>Previous</span></button>
        <span class="slides-count" aria-live="polite">${total ? (asPage ? `${total} slides` : `Slide ${at + 1} of ${total}`) : "No slides yet"}</span>
        <button type="button" class="btn btn-quiet" onClick=${() => setCurrent(Math.min(total - 1, at + 1))}
          disabled=${asPage || at >= total - 1}><span>Next</span><${Icon} name="chevronRight" /></button>
      </div>
      <div class="slides-tools">
        <button type="button" class="btn btn-quiet" aria-pressed=${asPage ? "true" : "false"}
          onClick=${() => setAsPage(!asPage)}><${Icon} name="page" /><span>Read as page</span></button>
        <button type="button" class="btn btn-quiet" onClick=${() => window.print()}>
          <${Icon} name="print" /><span>Print or PDF</span></button>
        <button type="button" class="btn btn-quiet" onClick=${back}>
          <${Icon} name="close" /><span>Close</span><kbd>Esc</kbd></button>
      </div>
    </div>
    ${!run.data && html`<div class="page"><${Status} state=${run} what="the slides">Running the engine for the slides…<//></div>`}
    ${total > 0 &&
    (asPage
      ? html`<${TextVersion} deck=${deck} stamp=${stamp} />`
      : html`<div class="deck" aria-label="Slide deck">
          ${deck.map((s, i) => html`<${Slide} slide=${s} index=${i} total=${total} current=${at} stamp=${stamp} />`)}
        </div>
        <p class="slides-hint">Use the Left and Right arrow keys to move, Esc to close.</p>`)}
  </main>`;
}
