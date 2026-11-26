/**
 * Small motion helpers: numbers that fade or roll in, things that appear as they scroll into view.
 *
 * Nothing here makes a number. The engine's text (for example "3,500–16,000") is the only
 * input, and it is on screen from the first frame: numbers fade in, or roll digit by digit
 * from the previous engine text to the new one. No frame ever shows a value in between that
 * the engine did not send.
 *
 * - `reducedMotion()`: true when the reader asked the system for less motion. Every helper
 *   then shows the final state at once.
 * - `countUp(el)`: the element's final text fades and slides in once (the name is kept for
 *   its callers; it no longer counts through in-between values).
 * - `Ticker`: the engine's text in a span; when the text changes (a new scenario), the digits
 *   that changed roll: the old digit leaves (upward when the number grew, downward when it
 *   fell) and the new one comes in, and the span glows once (`is-flash`) so the eye finds what
 *   moved. The span's own text is the engine's final text from the first frame (the rolling
 *   digits are a drawing laid over it, hidden from screen readers), so assistive technology
 *   only ever reads the final value.
 * - `useHeightMorph(ref, key)`: when `key` changes, the element's height glides from its old
 *   value to its new one (the fold spring), instead of jumping.
 * - `usePresence(open)`: keeps a closing panel on screen for its exit (`is-closing`, 200 ms),
 *   then lets it go. While it closes it is hidden from assistive technology and inert.
 * - `SPRING`: the spring curves of tokens.css, for animations started from script.
 * - `useReveal(ref, active)`: adds `is-in` to an element the first time it is in view (or,
 *   with `active` given, whenever `active` turns true) and fades in every `[data-count]` inside
 *   it in (`countUp`). CSS (`.reveal`) fades its text in and draws its bars.
 */
import { html, useEffect, useLayoutEffect, useRef, useState } from "../lib/html.js";

/** The spring curves (tokens.css), with a cubic fallback where `linear()` is not supported. */
const LINEAR_OK = (() => {
  try {
    return CSS.supports("transition-timing-function", "linear(0, 1)");
  } catch {
    return false;
  }
})();
export const SPRING = {
  panel: LINEAR_OK
    ? "linear(0,.107,.315,.525,.696,.821,.903,.954,.982,.997,1.003,1.005,1.005,1.004,1.003,1.002,1)"
    : "cubic-bezier(.22,1,.36,1)",
  fold: LINEAR_OK
    ? "linear(0,.102,.289,.473,.625,.739,.821,.879,.919,.946,.964,.976,.985,.99,.993,.996,1)"
    : "cubic-bezier(.22,1,.36,1)",
  out: "cubic-bezier(.22,1,.36,1)",
};

const DIGITS = /\d[\d,]*(?:\.\d+)?/g;
const REVEAL_MS = 420;

/** True when the reader asked for reduced motion (or the browser cannot say). */
export function reducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

/**
 * Show one element's number: its text is the engine's final text from the first frame and
 * fades and slides in once (420 ms). Nothing counts through in-between values, which the
 * engine never produced. `from` is accepted for older callers and ignored. Returns a stop
 * function that ends the fade at once.
 */
export function countUp(el, { from = null } = {}) {
  void from;
  if (!el || reducedMotion() || document.hidden || typeof el.animate !== "function") return () => {};
  const anim = el.animate(
    [
      { opacity: 0, transform: "translateY(4px)" },
      { opacity: 1, transform: "none" },
    ],
    { duration: REVEAL_MS, easing: "cubic-bezier(.22,1,.36,1)" },
  );
  return () => anim.finish();
}

const ROLL_MS = 220;
const ROLL_STAGGER = 28;

/**
 * Pair each character of `after` with the character it replaces in `before`. Numbers are
 * matched one to one and aligned on their last digit (so "9,800" to "10,200" lines up the
 * hundreds); the words around them must be the same, else null (no roll, just the new text).
 * `dir` is +1 for each character of a number that grew, -1 for one that fell.
 */
function columns(before, after) {
  const A = [...after.matchAll(DIGITS)];
  const B = [...before.matchAll(DIGITS)];
  if (A.length !== B.length || after.replace(DIGITS, "#") !== before.replace(DIGITS, "#")) return null;
  const cols = [...after].map((ch) => ({ ch, old: ch, dir: 0 }));
  A.forEach((a, k) => {
    const b = B[k];
    const dir = Number(a[0].replace(/,/g, "")) >= Number(b[0].replace(/,/g, "")) ? 1 : -1;
    for (let j = 1; j <= a[0].length; j += 1) {
      const col = cols[a.index + a[0].length - j];
      col.old = b[0][b[0].length - j] ?? "";
      col.dir = dir;
    }
  });
  return cols;
}

/**
 * Lay rolling digits over `el` (whose own text is already the final text, made transparent
 * while they roll). Returns a stop function that removes them at once.
 */
function roll(el, before, after) {
  const cols = columns(before, after);
  if (!cols || !cols.some((c) => c.old !== c.ch) || reducedMotion() || document.hidden) return () => {};
  const layer = document.createElement("span");
  layer.className = "tick-roll";
  layer.setAttribute("aria-hidden", "true");
  layer.style.color = getComputedStyle(el).color;
  let n = 0;
  for (const c of cols) {
    if (c.old === c.ch) {
      layer.appendChild(document.createTextNode(c.ch));
      continue;
    }
    const col = document.createElement("span");
    col.className = `tick-col ${c.dir < 0 ? "is-down" : "is-up"}`;
    col.style.setProperty("--d", `${Math.min(n, 8) * ROLL_STAGGER}ms`);
    n += 1;
    const out = document.createElement("span");
    out.className = "tick-old";
    out.textContent = c.old;
    const inn = document.createElement("span");
    inn.className = "tick-new";
    inn.textContent = c.ch;
    col.append(out, inn);
    layer.appendChild(col);
  }
  el.classList.add("is-rolling");
  el.appendChild(layer);
  const done = () => {
    clearTimeout(timer);
    layer.remove();
    el.classList.remove("is-rolling");
  };
  const timer = setTimeout(done, ROLL_MS + Math.min(n, 8) * ROLL_STAGGER + 60);
  return done;
}

/**
 * The engine's text in a span; when it changes, the changed digits roll to the new text.
 * `live` makes the span a polite live region, so a screen reader says the new final value
 * (used for the few headline numbers only, so a scenario change is not read out many times).
 */
export function Ticker({ text, className = "tick", live = false }) {
  const ref = useRef(null);
  const last = useRef(text);
  const stop = useRef(() => {});
  useLayoutEffect(() => {
    const before = last.current;
    last.current = text;
    stop.current();
    stop.current = () => {};
    const el = ref.current;
    if (!el || before === text || before === null || before === undefined || text === null || text === undefined) return;
    stop.current = roll(el, String(before), String(text));
    if (!reducedMotion()) {
      el.classList.remove("is-flash");
      void el.offsetWidth; // restart the glow if the text changes again mid-way
      el.classList.add("is-flash");
    }
  }, [text]);
  useEffect(() => () => stop.current(), []);
  return html`<span class=${className} ref=${ref} aria-live=${live ? "polite" : undefined}
    aria-atomic=${live ? "true" : undefined}>${text}</span>`;
}

const EXIT_MS = 200;

/**
 * For a panel that opens and closes: returns [mounted, closing]. When `open` turns false the
 * panel stays mounted for its exit animation (CSS on `.is-closing`), then unmounts. With
 * reduced motion it unmounts at once.
 */
export function usePresence(open) {
  const [kept, setKept] = useState(open);
  useEffect(() => {
    if (open) {
      setKept(true);
      return undefined;
    }
    if (!kept) return undefined;
    if (reducedMotion()) {
      setKept(false);
      return undefined;
    }
    const t = setTimeout(() => setKept(false), EXIT_MS);
    return () => clearTimeout(t);
  }, [open]);
  return [open || kept, !open && kept];
}

/** Props for a panel that is closing: out of the accessibility tree, not focusable. */
export function closingProps(closing) {
  return closing ? { "aria-hidden": "true", inert: true } : {};
}

/**
 * When `key` changes, glide the element's height from what it was to what it is now (the
 * fold spring), so new content does not make the card jump. Height is only animated for
 * that moment; at rest the element sizes itself as before.
 */
export function useHeightMorph(ref, key) {
  const last = useRef(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const from = last.current;
    const to = el.getBoundingClientRect().height;
    last.current = to;
    if (!from || Math.abs(from - to) < 2 || reducedMotion() || !el.animate) return;
    el.animate([{ height: `${from}px`, overflow: "hidden" }, { height: `${to}px`, overflow: "hidden" }], {
      duration: 420,
      easing: SPRING.fold,
    });
  }, [key]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => {
      if (!el.getAnimations?.().length) last.current = el.getBoundingClientRect().height;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
}

/**
 * Reveal `ref`'s element: add `reveal` now and `is-in` when it comes into view (or when
 * `active` turns true, if `active` is given), then fade in its `[data-count]` elements (their final text).
 * `key` restarts the reveal (for example a new scenario).
 */
export function useReveal(ref, { active = undefined, key = "" } = {}) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let stops = [];
    const run = () => {
      stops.forEach((s) => s());
      el.classList.remove("is-in");
      // Read layout once so the browser sees the "before" state, then go.
      void el.offsetWidth;
      el.classList.add("is-in");
      stops = [...el.querySelectorAll("[data-count]")].map((n) => countUp(n));
    };
    const finish = () => {
      stops.forEach((s) => s());
      stops = [];
    };
    if (reducedMotion()) {
      el.classList.add("is-in");
      return undefined;
    }
    el.classList.add("reveal");
    if (active !== undefined) {
      if (active) run();
      else el.classList.remove("is-in");
      return finish;
    }
    if (typeof IntersectionObserver === "undefined") {
      el.classList.add("is-in");
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((x) => x.isIntersecting)) {
          io.disconnect();
          run();
        }
      },
      { threshold: 0.25 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      finish();
    };
  }, [active, key]);
}
