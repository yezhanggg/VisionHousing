/**
 * The Slides view: a 16:9 deck of the current scenario, written by the
 * engine's explanation layer from one run (`explanation.slides`).
 *   - Each slide is drawn on a 960 x 540 logical frame that scales to the window.
 *   - Left / Right arrows (and Page Up / Page Down, Home / End) move; Esc goes back.
 *   - "Read as a page" shows the same slides as plain text in one column.
 *   - Printing gives one slide per page (css/slides.css), which is how the slide PDF is made.
 *   - Every footer carries the run's three fingerprints: code, data and assumptions.
 * A reform run alone also gets its top-drivers slide (the run asks for the drivers).
 *   - The slide on show plays a short entry (numbers fade in with the engine's final text, bars
 *     draw in, sentences fade in); with reduced motion it appears at once.
 * The slide face itself is drawn by ui/slide-face.js, shared with the live Brief preview.
 */
import { deckOf, stampOf } from "../content/slides.js";
import { stateName } from "../content/states.js";
import { learn, useRun } from "../lib/data.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { hrefFor, lastScreen, scenario, slideStart } from "../state/store.js";
import { Status } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";
import { ScenarioActions } from "../ui/actions.js";
import { useReveal } from "../ui/motion.js";
import { rangeOf, SlideFace, stampText } from "../ui/slide-face.js";

function Slide({ slide, index, total, current, stamp, play }) {
  const ref = useRef(null);
  // The slide on show plays its entry: numbers fade in, bars draw in, sentences fade in.
  useReveal(ref, { active: index === current, key: play });
  return html`<section ref=${ref} class=${`slide-frame ${index === current ? "" : "is-off"}`}
      aria-roledescription="slide" aria-label=${`Slide ${index + 1} of ${total}`}
      aria-hidden=${index === current ? undefined : "true"}>
    <${SlideFace} slide=${slide} index=${index} total=${total} stamp=${stamp} />
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
        html`<ul>${s.chart.items.map((x) => html`<li>${x.label.text}: ${x.shown?.total_low !== undefined ? `${x.shown.total_low} / ${x.shown.total_high}` : rangeOf(x.shown)}</li>`)}</ul>`}
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
  // A card in the Brief preview can open the deck at its slide.
  const [current, setCurrent] = useState(() => {
    const start = slideStart.value;
    slideStart.value = 0;
    return start;
  });
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
        <${ScenarioActions} slides=${false} label="Export and share" />
        <button type="button" class="btn btn-quiet" onClick=${back}>
          <${Icon} name="close" /><span>Close</span><kbd>Esc</kbd></button>
      </div>
    </div>
    ${!run.data && html`<div class="page"><${Status} state=${run} what="the slides">Running the engine for the slides…<//></div>`}
    ${total > 0 &&
    (asPage
      ? html`<${TextVersion} deck=${deck} stamp=${stamp} />`
      : html`<div class="deck" aria-label="Slide deck">
          ${deck.map((s, i) => html`<${Slide} slide=${s} index=${i} total=${total} current=${at} stamp=${stamp}
            play=${stamp.assumptions + stamp.data} />`)}
        </div>
        <p class="slides-hint">Use the Left and Right arrow keys to move, Esc to close.</p>`)}
  </main>`;
}
