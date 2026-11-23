/**
 * The Slides view (decision D-22): a 16:9 deck of the current scenario.
 *   - Each slide is drawn on a 960 x 540 logical frame that scales to the window.
 *   - Left / Right arrows (and Page Up / Page Down, Home / End) move; Esc goes back.
 *   - "Read as a page" shows the same slides as plain text in one column.
 *   - Printing gives one slide per page (css/slides.css), which is how the slide PDF is made.
 */
import { placeholderDeck } from "../content/slides.js";
import { html, useLayoutEffect, useState } from "../lib/html.js";
import { hrefFor, lastScreen, scenario } from "../state/store.js";
import { Icon } from "../ui/icons.js";

const STAMP = "Code — · Data — · Assumptions —";

function SlideBody({ slide }) {
  if (slide.layout === "headline") {
    return html`<div class="slide-stats">
      ${slide.stats.map(
        (s) => html`<div class="slide-stat">
          <span class="slide-stat-value">${s.value ?? "—"}</span>
          <span class="slide-stat-label">${s.label}</span>
        </div>`,
      )}
    </div>
    ${slide.body && html`<p class="slide-text">${slide.body}</p>`}`;
  }
  if (slide.layout === "bullets") {
    return html`<ul class="slide-bullets">${slide.bullets.map((b) => html`<li>${b}</li>`)}</ul>`;
  }
  return html`${slide.subtitle && html`<p class="slide-subtitle">${slide.subtitle}</p>`}
    ${slide.body && html`<p class="slide-text">${slide.body}</p>`}`;
}

function Slide({ slide, index, total, current }) {
  return html`<section class=${`slide-frame ${index === current ? "" : "is-off"}`}
      aria-roledescription="slide" aria-label=${`Slide ${index + 1} of ${total}`}
      aria-hidden=${index === current ? undefined : "true"}>
    <div class=${`slide slide--${slide.layout}`}>
      <p class="slide-kicker">${slide.kicker}</p>
      <h2 class="slide-title">${slide.title}</h2>
      <div class="slide-main"><${SlideBody} slide=${slide} /></div>
      <footer class="slide-foot">
        <span>VisionHousing · ${STAMP}</span>
        <span>${index + 1} / ${total}</span>
      </footer>
    </div>
  </section>`;
}

function TextVersion({ deck }) {
  return html`<article class="slides-text">
    ${deck.map(
      (s, i) => html`<section class="slides-text-item">
        <p class="kicker">Slide ${i + 1} · ${s.kicker}</p>
        <h2>${s.title}</h2>
        ${s.subtitle && html`<p>${s.subtitle}</p>`}
        ${s.stats && html`<dl class="slides-text-stats">${s.stats.map(
          (x) => html`<div><dt>${x.label}</dt><dd>${x.value ?? "—"}</dd></div>`,
        )}</dl>`}
        ${s.bullets && html`<ul>${s.bullets.map((b) => html`<li>${b}</li>`)}</ul>`}
        ${s.body && html`<p>${s.body}</p>`}
      </section>`,
    )}
    <p class="slides-text-stamp">${STAMP}</p>
  </article>`;
}

export function SlidesScreen() {
  const deck = placeholderDeck(scenario.value);
  const total = deck.length;
  const [current, setCurrent] = useState(0);
  const [asPage, setAsPage] = useState(false);
  const at = Math.min(current, total - 1);

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
    <h1 class="visually-hidden">Slides</h1>
    <div class="slides-bar" role="toolbar" aria-label="Slides">
      <div class="slides-nav">
        <button type="button" class="btn btn-quiet" onClick=${() => setCurrent(Math.max(0, at - 1))}
          disabled=${asPage || at === 0}><${Icon} name="chevronLeft" /><span>Previous</span></button>
        <span class="slides-count" aria-live="polite">${asPage ? `${total} slides` : `Slide ${at + 1} of ${total}`}</span>
        <button type="button" class="btn btn-quiet" onClick=${() => setCurrent(Math.min(total - 1, at + 1))}
          disabled=${asPage || at === total - 1}><span>Next</span><${Icon} name="chevronRight" /></button>
      </div>
      <div class="slides-tools">
        <button type="button" class="btn btn-quiet" aria-pressed=${asPage ? "true" : "false"}
          onClick=${() => setAsPage(!asPage)}><${Icon} name="page" /><span>Read as a page</span></button>
        <button type="button" class="btn btn-quiet" onClick=${() => window.print()}>
          <${Icon} name="print" /><span>Print or save as PDF</span></button>
        <button type="button" class="btn btn-quiet" onClick=${back}>
          <${Icon} name="close" /><span>Close</span><kbd>Esc</kbd></button>
      </div>
    </div>
    ${asPage
      ? html`<${TextVersion} deck=${deck} />`
      : html`<div class="deck" aria-label="Slide deck">
          ${deck.map((s, i) => html`<${Slide} slide=${s} index=${i} total=${total} current=${at} />`)}
        </div>
        <p class="slides-hint">Use the Left and Right arrow keys to move, Esc to close.</p>`}
  </main>`;
}
