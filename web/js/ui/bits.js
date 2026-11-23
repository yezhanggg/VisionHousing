/**
 * Small building blocks shared by the screens.
 * Nothing here ever invents a number: until the engine sends a result, values show "—".
 */
import { html, useEffect, useState } from "../lib/html.js";

/** True while a CSS media query matches; follows changes (for example a rotated phone). */
export function useMedia(query) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** A value slot. Shows the engine's text when given, else an em dash with a spoken label. */
export function Value({ text, size = "md" }) {
  if (text) return html`<span class=${`value value-${size}`}>${text}</span>`;
  return html`<span class=${`value value-${size} is-empty`} aria-label="Not available yet">—</span>`;
}

/** An empty likely-range bar: the track only, until the engine sends low, middle and high. */
export function RangeBar({ label }) {
  return html`<div class="range" role="img" aria-label=${`${label}: range not available yet`}>
    <span class="range-track"></span>
  </div>`;
}

/** A plain, honest empty state. */
export function Empty({ title, children }) {
  return html`<div class="empty">
    ${title && html`<p class="empty-title">${title}</p>`}
    ${children && html`<p class="empty-text">${children}</p>`}
  </div>`;
}

/** A card with a title row. `action` is the card's one primary action, if any. */
export function Card({ title, id, sub, action, className = "", children }) {
  const hid = id ? `${id}-title` : undefined;
  return html`<section class=${`card ${className}`} id=${id} aria-labelledby=${hid}>
    <header class="card-head">
      <div>
        <h2 class="card-title" id=${hid}>${title}</h2>
        ${sub && html`<p class="card-sub">${sub}</p>`}
      </div>
    </header>
    <div class="card-body">${children}</div>
    ${action && html`<footer class="card-foot">${action}</footer>`}
  </section>`;
}

/** A whole-row switch: one tab stop, `aria-pressed`, label and a one-line caption. */
export function Switch({ on, label, caption, onToggle }) {
  return html`<button type="button" class=${`switch ${on ? "is-on" : ""}`} aria-pressed=${on ? "true" : "false"}
      onClick=${onToggle}>
    <span class="switch-text">
      <span class="switch-label">${label}</span>
      ${caption && html`<span class="switch-caption">${caption}</span>`}
    </span>
    <span class="switch-track" aria-hidden="true"><span class="switch-knob"></span></span>
  </button>`;
}
