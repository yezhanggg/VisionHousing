/**
 * The two side panels that float over the map ("docks"), the same frame on every screen.
 *
 *   - Each dock has a header (icon, title, one line under it, its buttons) and a body that
 *     scrolls on its own. What it holds depends on the screen (see ui/shell.js).
 *   - "Minimise" folds a dock into a small box: its icon, its title and one key number (the
 *     engine's text). The dock's outline glides from the panel's size to the box's size (and
 *     back when the box is pressed); the content fades. The choice is kept in this browser.
 *   - The right dock can be made wider or narrower by dragging its inner edge, or by focusing
 *     that edge and pressing Left / Right (Home and End go to the narrowest and widest; a
 *     double click goes back to the default). The width is kept in this browser. Its content
 *     reflows; the charts are SVG with a view box, so they scale.
 *   - On a phone the docks are parts of one bottom sheet (ui/shell.js), so they never fold.
 *
 * With reduced motion every change is instant.
 */
import { html, useEffect, useLayoutEffect, useRef } from "../lib/html.js";
import { prefs, RIGHT_W, setPref } from "../state/store.js";
import { useMedia } from "./bits.js";
import { Icon } from "./icons.js";
import { reducedMotion, SPRING, Ticker } from "./motion.js";

/** The widest the right dock may be now: it leaves the middle of the map at least this wide. */
const CENTRE_MIN = 460;

export function rightMax() {
  const hud = document.querySelector(".hud");
  const left = hud?.querySelector('[data-zone="left"]')?.getBoundingClientRect().width || 360;
  return Math.max(RIGHT_W.min, Math.min(RIGHT_W.max, window.innerWidth - left - CENTRE_MIN - 56));
}

function clampW(w) {
  return Math.round(Math.max(RIGHT_W.min, Math.min(rightMax(), w)));
}

/** Write the right dock's width to the layout (a CSS variable on the HUD) without a render. */
function paintWidth(w) {
  document.querySelector(".hud")?.style.setProperty("--rw-px", `${w}px`);
}

/**
 * Glide the element's box from the size it had before `key` changed to its size now
 * (width and height, with the panel spring), so the panel morphs into its box and back.
 */
function useMorph(ref, key) {
  const last = useRef(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const to = el.getBoundingClientRect();
    const from = last.current;
    last.current = to;
    if (!from || reducedMotion() || !el.animate) return;
    if (Math.abs(from.width - to.width) < 2 && Math.abs(from.height - to.height) < 2) return;
    el.animate(
      [
        { width: `${from.width}px`, height: `${from.height}px`, overflow: "hidden" },
        { width: `${to.width}px`, height: `${to.height}px`, overflow: "hidden" },
      ],
      { duration: 420, easing: SPRING.panel },
    );
  }, [key]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => {
      if (!el.getAnimations?.().length) last.current = el.getBoundingClientRect();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
}

/** The drag handle on the right dock's inner edge (a vertical separator). */
function ResizeHandle() {
  const w = prefs.value.rightW;
  const drag = useRef(null);
  useEffect(() => {
    const fit = () => paintWidth(clampW(prefs.peek().rightW));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  const commit = (next) => {
    const v = clampW(next);
    paintWidth(v);
    setPref({ rightW: v });
  };
  const down = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, w: prefs.peek().rightW, now: prefs.peek().rightW };
    document.body.classList.add("is-resizing");
  };
  const move = (e) => {
    const d = drag.current;
    if (!d) return;
    d.now = clampW(d.w + (d.x - e.clientX));
    paintWidth(d.now);
  };
  const up = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    document.body.classList.remove("is-resizing");
    commit(d.now);
  };
  const key = (e) => {
    const step = e.shiftKey ? 80 : 24;
    const to = { ArrowLeft: w + step, ArrowRight: w - step, Home: RIGHT_W.min, End: rightMax() }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    commit(to);
  };
  return html`<div class="dock-resize" role="separator" aria-orientation="vertical" tabindex="0"
      aria-label="Resize the right panel" aria-valuemin=${RIGHT_W.min} aria-valuemax=${RIGHT_W.max} aria-valuenow=${w}
      aria-valuetext=${`${w} pixels wide`} title="Drag to resize (double-click to reset)"
      onPointerDown=${down} onPointerMove=${move} onPointerUp=${up} onPointerCancel=${up}
      onDblClick=${() => commit(420)} onKeyDown=${key}>
    <span class="dock-grip" aria-hidden="true"></span>
  </div>`;
}

/**
 * One side panel. `side` is "left" or "right"; `title`, `sub` and `icon` name it; `chip` is
 * the key number shown in its folded box (the engine's text); `actions` sit in its header.
 * `mode` is the screen it belongs to, so a new screen's content slides in.
 */
export function Dock({ side, title, sub = "", icon = "panel", chip = "", actions = null, busy = false, mode = "", children, label = "" }) {
  const phone = useMedia("(max-width: 767px)");
  const key = side === "left" ? "leftMin" : "rightMin";
  const min = !phone && prefs.value[key];
  const ref = useRef(null);
  useMorph(ref, min);
  const id = `dock-${side}`;
  const restore = useRef(null);
  const fold = useRef(null);
  // Keep the keyboard where the reader is: the box after folding, the panel after opening.
  const toggle = (to) => {
    setPref({ [key]: to });
    requestAnimationFrame(() => (to ? restore.current : fold.current)?.focus({ preventScroll: true }));
  };
  if (min) {
    return html`<section class=${`dock dock-${side} is-min`} data-zone=${side} ref=${ref} aria-label=${label || title}>
      <button type="button" class="dock-chip" ref=${restore} aria-expanded="false"
          onClick=${() => toggle(false)} title=${`Show ${title}`}>
        <span class="dock-chip-icon" aria-hidden="true"><${Icon} name=${icon} size=${16} /></span>
        <span class="dock-chip-text">
          <span class="dock-chip-title">${title}</span>
          ${chip && html`<${Ticker} text=${chip} className="dock-chip-key" />`}
        </span>
        <span class="visually-hidden">: show the panel</span>
        <${Icon} name=${side === "left" ? "chevronRight" : "chevronLeft"} size=${14} />
      </button>
    </section>`;
  }
  return html`<section class=${`dock dock-${side} ${busy ? "is-busy" : ""}`} data-zone=${side} ref=${ref}
      aria-labelledby=${`${id}-title`}>
    ${side === "right" && !phone && html`<${ResizeHandle} />`}
    <header class="dock-head">
      <span class="dock-icon" aria-hidden="true"><${Icon} name=${icon} size=${16} /></span>
      <div class="dock-titles">
        <h2 class="dock-title" id=${`${id}-title`}>${title}</h2>
        ${sub && html`<p class="dock-sub">${sub}</p>`}
      </div>
      ${actions && html`<div class="dock-actions">${actions}</div>`}
      ${!phone &&
      html`<button type="button" class="btn btn-ghost btn-xs dock-fold" ref=${fold} aria-expanded="true"
          aria-controls=${`${id}-body`} onClick=${() => toggle(true)} title="Fold into a small box">
        <${Icon} name="minimise" size=${16} /><span class="visually-hidden">Minimise ${title}</span>
      </button>`}
      <span class=${`load-bar ${busy ? "is-on" : ""}`} aria-hidden="true"><span></span></span>
    </header>
    <div class="dock-body" id=${`${id}-body`} key=${mode}>${children}</div>
  </section>`;
}
