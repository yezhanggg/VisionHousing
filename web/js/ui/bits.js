/**
 * Small building blocks shared by the screens.
 * Nothing here ever invents a number: values come from the engine, and until it sends
 * them a slot shows "—".
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

/**
 * A likely-range bar: a band from P10 to P90 with a mark at P50, on a shared scale
 * (`lo` to `hi`, so bars in one chart compare). Without values it draws the track only.
 * The numbers come from the engine; this only places them.
 */
export function RangeBar({ label, n = null, lo = 0, hi = 0 }) {
  const ok = n && Number.isFinite(n.p10) && Number.isFinite(n.p90) && hi > lo;
  if (!ok) {
    return html`<div class="range" role="img" aria-label=${`${label}: range not available yet`}>
      <span class="range-track"></span>
    </div>`;
  }
  const at = (v) => `${(100 * (Math.min(hi, Math.max(lo, v)) - lo)) / (hi - lo)}%`;
  const left = at(Math.min(n.p10, n.p90));
  const right = at(Math.max(n.p10, n.p90));
  const words = n.shown ? `${n.shown.p10} to ${n.shown.p90}, middle ${n.shown.p50}` : "";
  return html`<div class="range" role="img" aria-label=${`${label}: ${words}`}>
    <span class="range-track"></span>
    <span class="range-band" style=${`left: ${left}; width: calc(${right} - ${left})`}></span>
    ${Number.isFinite(n.p50) && html`<span class="range-mid" style=${`left: ${at(n.p50)}`}></span>`}
    ${lo < 0 && hi > 0 && html`<span class="range-zero" style=${`left: ${at(0)}`}></span>`}
  </div>`;
}

/** The shared scale of several range records: from min(0, lowest P10) to the highest P90. */
export function scaleOf(records) {
  const vals = records.filter(Boolean).flatMap((r) => [r.p10, r.p90]).filter(Number.isFinite);
  if (!vals.length) return [0, 0];
  return [Math.min(0, ...vals), Math.max(0, ...vals)];
}

/** A plain status line while something loads, or why it did not. */
export function Status({ state, what, children }) {
  if (state.loading) {
    return html`<p class="status-line" role="status">${children || `Loading ${what}…`}</p>`;
  }
  if (state.error) {
    return html`<p class=${`status-line ${state.localOnly ? "is-local" : "is-error"}`} role="note">
      ${state.localOnly ? state.error : `${what[0].toUpperCase()}${what.slice(1)} did not load: ${state.error}`}
    </p>`;
  }
  return null;
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
