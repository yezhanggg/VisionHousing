/**
 * What the four explanation cards share (confidence.js, plausibility.js, lever.js,
 * implementation.js): loading a reply, the card frame, and a small grade chip.
 *
 * Every card follows one shape: a heading, one headline line, one visual, and the details
 * behind "(i)". Nothing here makes a number; the cards show the API's values and text.
 * The stylesheet for all four is imported here, so a screen that mounts a card gets it.
 */
import "../../css/cards.css";
import { LocalOnlyError } from "../lib/api.js";
import { html, useEffect, useId, useState } from "../lib/html.js";
import { Status } from "./bits.js";

/**
 * Load a reply for a card: `ask()` is called when any of `deps` change. Returns
 * `{loading, error, localOnly, data}`; an older reply never overwrites a newer one.
 */
export function useCard(ask, deps) {
  const [state, setState] = useState({ loading: true, error: "", localOnly: false, data: null });
  useEffect(() => {
    let live = true;
    setState({ loading: true, error: "", localOnly: false, data: null });
    ask()
      .then((data) => live && setState({ loading: false, error: "", localOnly: false, data }))
      .catch((err) => {
        if (!live) return;
        const localOnly = err instanceof LocalOnlyError;
        setState({ loading: false, error: err?.message || String(err), localOnly, data: null });
      });
    return () => {
      live = false;
    };
  }, deps);
  return state;
}

/**
 * The card frame: a heading (level `h`, default 2), one headline line, the body.
 * While loading or after a failure it shows the shared status line instead of the body;
 * `wait`, when given, replaces the plain "Loading …" line (a slow card says how slow).
 */
export function CardFrame({ id, title, line, state, what, wait, h = 2, className = "", testid, children }) {
  const Tag = `h${Math.min(6, Math.max(2, h))}`;
  // The heading id must be unique even when the same card is mounted twice on one screen.
  const hid = `${id}-title-${useId()}`;
  return html`<section class=${`card ec-card ${className}`} id=${id} aria-labelledby=${hid} data-testid=${testid || id}>
    <header class="card-head"><div>
      <${Tag} class="card-title" id=${hid}>${title}<//>
      ${line && html`<p class="card-sub ec-line">${line}</p>`}
    </div></header>
    <div class="card-body">
      ${state && (state.loading || state.error) ? html`<${Status} state=${state} what=${what} lines=${2}>${wait || ""}<//>` : children}
    </div>
  </section>`;
}

/**
 * A grade as a chip: the API's grade word or letter, with a tone class from `tone`. When the
 * API gives the grade's sentence (`label`), it is shown as text beside the chip and tied to it
 * with `aria-describedby`, so a screen reader and a touch screen get it too (a `title` alone
 * reaches neither). The id comes from `useId`, so every card instance has its own.
 */
export function GradeChip({ text, tone = "", label = "" }) {
  const id = useId();
  if (!text) return null;
  const chip = html`<span class=${`ec-chip ${tone ? `is-${tone}` : ""}`} aria-describedby=${label ? `${id}-why` : undefined}>${text}</span>`;
  if (!label) return chip;
  return html`<span class="ec-chip-wrap">${chip}<span class="ec-chip-why" id=${`${id}-why`}>${label}</span></span>`;
}

/** A link to a source: the address shortened to its site for reading, the whole address on hover. */
export function SourceLink({ href }) {
  let site = href;
  try {
    site = new URL(href).hostname.replace(/^www\./, "");
  } catch {
    // not an address (a file path): shown as is
  }
  return /^https?:\/\//.test(href)
    ? html`<a class="ec-src" href=${href} target="_blank" rel="noopener noreferrer" title=${href}>${site}</a>`
    : html`<code class="ec-src">${href}</code>`;
}
