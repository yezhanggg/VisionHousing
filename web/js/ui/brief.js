/**
 * The "Brief" tab: the scenario's slide deck as a column of small slide cards, live.
 *
 * The cards are the same slides as the Slides view (the engine's `explanation.slides` of the
 * run the panel already shows), drawn small by the shared slide face. When the scenario
 * changes, the run changes and the cards follow. Each card plays its entry as it scrolls into
 * view: numbers fade in with the engine's final text, bars draw in, sentences fade in (all off with
 * reduced motion). Pressing a card opens the full Slides view at that slide.
 */
import { deckOf, stampOf } from "../content/slides.js";
import { html, useRef } from "../lib/html.js";
import { hrefFor, slideStart } from "../state/store.js";
import { Empty, Status } from "./bits.js";
import { useReveal } from "./motion.js";
import { SlideFace } from "./slide-face.js";

function BriefCard({ slide, index, total, stamp, play }) {
  const ref = useRef(null);
  useReveal(ref, { key: play });
  return html`<li class="brief-card" style=${`--n: ${Math.min(index, 8)}`}>
    <div class="brief-frame" ref=${ref} aria-hidden="true">
      <${SlideFace} slide=${slide} index=${index} total=${total} stamp=${stamp} />
    </div>
    <a class="brief-open" href=${hrefFor("slides")} onClick=${() => {
      slideStart.value = index;
    }}>
      <span class="brief-num">${index + 1}</span>
      <span class="brief-title">${slide.title}</span>
      <span class="brief-go">Open</span>
    </a>
  </li>`;
}

/** The live slide preview of a run (`run` is a useRun state). */
export function BriefPreview({ run }) {
  const deck = deckOf(run.data);
  const stamp = stampOf(run.data);
  const play = `${stamp.assumptions}${stamp.data}${deck.length}`;
  return html`<div class="brief">
    ${!run.data && html`<${Status} state=${run} what="the slides" />`}
    ${run.data && !deck.length && html`<${Empty}>The engine sent no slides for this scenario.<//>`}
    ${deck.length > 0 &&
    html`<p class="brief-lead">The ${deck.length} slides of this scenario, live. Press one to open it full size.</p>
      <ol class="brief-list">
        ${deck.map((s, i) => html`<${BriefCard} key=${`${play}-${i}`} slide=${s} index=${i} total=${deck.length}
          stamp=${stamp} play=${play} />`)}
      </ol>`}
  </div>`;
}
