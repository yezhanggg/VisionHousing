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
 * The numbers come from the engine; this only places them. Hovering the bar (or focusing
 * it, when `focusable` is set) shows the engine's P10 / P50 / P90 text above it. The band
 * draws in from the left when it first appears (rows one after another, by `--i`) and slides
 * when the values change. Band and mark are placed with transform only (`--l`, `--w`, `--x`
 * in percent of the track), so a change never lays the page out again.
 */
export function RangeBar({ label, n = null, lo = 0, hi = 0, focusable = false }) {
  const ok = n && Number.isFinite(n.p10) && Number.isFinite(n.p90) && hi > lo;
  if (!ok) {
    return html`<div class="range" role="img" aria-label=${`${label}: range not available yet`}>
      <span class="range-track"></span>
    </div>`;
  }
  const pct = (v) => (100 * (Math.min(hi, Math.max(lo, v)) - lo)) / (hi - lo);
  const at = (v) => `${pct(v)}%`;
  const left = pct(Math.min(n.p10, n.p90));
  const width = Math.max(1.5, pct(Math.max(n.p10, n.p90)) - left);
  const sh = n.shown || {};
  const words = n.shown ? `${sh.p10} to ${sh.p90}, middle ${sh.p50}` : "";
  // Hover or focus shows the engine's P10 / P50 / P90 text over the bar.
  const tip = sh.p10 !== undefined && sh.p90 !== undefined;
  const mid = Math.min(80, Math.max(20, pct(Number.isFinite(n.p50) ? n.p50 : (n.p10 + n.p90) / 2)));
  return html`<div class=${`range ${tip ? "has-tip" : ""}`} role="img" aria-label=${`${label}: ${words}`}
      tabindex=${tip && focusable ? "0" : undefined}>
    <span class="range-track"></span>
    <span class="range-band is-tf" style=${`--l: ${left.toFixed(3)}; --w: ${width.toFixed(3)}`}></span>
    ${Number.isFinite(n.p50) && html`<span class="range-mid is-tf" style=${`--x: ${pct(n.p50).toFixed(3)}`}></span>`}
    ${lo < 0 && hi > 0 && html`<span class="range-zero" style=${`left: ${at(0)}`}></span>`}
    ${tip &&
    html`<span class="range-tip" aria-hidden="true" style=${`left: ${mid}%`}>
      <span><b>P10</b> ${sh.p10}</span>${sh.p50 !== undefined && html`<span><b>P50</b> ${sh.p50}</span>`}<span><b>P90</b> ${sh.p90}</span>
    </span>`}
  </div>`;
}

/** The shared scale of several range records: from min(0, lowest P10) to the highest P90. */
export function scaleOf(records) {
  const vals = records.filter(Boolean).flatMap((r) => [r.p10, r.p90]).filter(Number.isFinite);
  if (!vals.length) return [0, 0];
  return [Math.min(0, ...vals), Math.max(0, ...vals)];
}

/**
 * While something loads: a skeleton of grey bars with a light passing over them, under one
 * line that says what is loading (that line is the live status for screen readers). After a
 * failure: why it did not load. The skeleton waits 150 ms before it shows (CSS), so a quick
 * reply never flashes it.
 */
export function Status({ state, what, children, lines = 3 }) {
  if (state.loading) {
    return html`<div class="skeleton" aria-busy="true">
      <p class="status-line is-loading" role="status">${children || `Loading ${what}…`}</p>
      ${Array.from({ length: lines }, (_, i) => html`<span class=${`skel skel-${i % 3}`} aria-hidden="true"></span>`)}
    </div>`;
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

/**
 * A whole-row switch: one tab stop, `aria-pressed`, label and a one-line caption. The knob
 * springs across and widens a little while pressed (CSS). With `reason`, the switch is greyed
 * out (`aria-disabled`, still focusable so the reason is read out) and the reason shows under
 * the caption; pressing it does nothing.
 */
export function Switch({ on, label, caption, onToggle, id, reason = "" }) {
  const rid = reason && id ? `switch-why-${id}` : undefined;
  return html`<button type="button" class=${`switch ${on ? "is-on" : ""} ${reason ? "is-blocked" : ""}`}
      aria-pressed=${on ? "true" : "false"} aria-disabled=${reason ? "true" : undefined} aria-describedby=${rid}
      onClick=${reason ? undefined : onToggle} data-reform=${id}>
    <span class="switch-text">
      <span class="switch-label">${label}</span>
      ${caption && html`<span class="switch-caption">${caption}</span>`}
      ${reason && html`<span class="switch-why" id=${rid}>${reason}</span>`}
    </span>
    <span class="switch-track" aria-hidden="true"><span class="switch-knob"></span></span>
  </button>`;
}
