/**
 * The guided tour: a few coach marks that walk a first-time reader through the tool.
 *
 * `Tour({steps, onClose})` darkens the screen except for one element (a "spotlight" cut out
 * around it), and puts a small card with one or two plain sentences next to it. The card has
 * Back, Next (Done on the last step) and Skip.
 *
 * - Each step names its element by CSS selector (`selector`, a string or a list of fallbacks
 *   tried in order) and, when several elements match, the visible text it must contain
 *   (`text`). A step whose element is not on the screen shows its card in the middle, with
 *   no spotlight, so the count of steps never changes under the reader.
 * - The card never covers the element it explains: it goes below, above, right or left of it,
 *   whichever side has room first, and is kept inside the window.
 * - Keyboard: Enter or Right goes to the next step, Left goes back, Esc closes. Tab and
 *   Shift+Tab stay inside the card (a focus trap); focus returns to where it was on close.
 * - With reduced motion asked for, the spotlight and the card jump; nothing slides or fades.
 * - Closing (Done, Skip or Esc) remembers that this browser has seen the tour, in
 *   localStorage, inside try/catch: a private window that blocks storage just offers the
 *   tour again next time. `tourSeen()` reads that flag, `forgetTour()` clears it.
 *
 * The words hold no numbers: every number the reader sees is on the tool's own screens.
 * `TOUR_STEPS` is the default path (pick a reform, read the range, why, where, trust,
 * limits, share). The shell decides where to mount it; see docs/modules/web.md.
 */
import "../../css/tour.css";
import { html, useEffect, useId, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { reducedMotion } from "./motion.js";

export const TOUR_KEY = "vh.tour.seen.v1";

/** Room kept between the element and its spotlight edge, and between spotlight and card. */
const PAD = 8;
const GAP = 14;
const EDGE = 12;

/** The default path through the State screen. Words only; no numbers. */
export const TOUR_STEPS = [
  {
    id: "pick",
    selector: ['[data-zone="left"] .switch', '[data-zone="left"]'],
    title: "Pick a reform",
    body: "Switch a reform on here. The map and the results change to show what it adds over five years, compared with today's rules.",
  },
  {
    id: "range",
    selector: ['[data-zone="right"] .lead', '[data-zone="right"] .headline', '[data-zone="right"] .dock-body'],
    title: "Read the range",
    body: "The answer is a likely range, not one number. The low and high ends come from many simulated futures, so both ends matter.",
  },
  {
    id: "why",
    selector: '[data-zone="right"] [role="tab"]',
    text: "Drivers",
    title: "See why",
    body: "Drivers shows which inputs move the answer most, and how far each one could push it.",
  },
  {
    id: "where",
    selector: [".hud-legend", '[data-zone="controls"]'],
    title: "See where",
    body: "The map shades each county by what the reform adds there. Press 3D to stand a column on each county; click a county to open it.",
  },
  {
    id: "trust",
    selector: ['[data-zone="right"] .explain-btn', '[data-zone="right"] .num-btn'],
    title: "Check any number",
    body: "Press Explain, or any underlined number, to see its formula, its sources, the assumptions behind it and how sure the evidence is.",
  },
  {
    id: "limits",
    selector: '[data-zone="right"] [role="tab"]',
    text: "Limits",
    title: "Know the limits",
    body: "Limits says plainly what these estimates cannot tell you. Read it before you quote a range.",
  },
  {
    id: "share",
    selector: '[data-zone="right"] .dock-actions button',
    text: "Share link",
    title: "Share it",
    body: "Share link copies an address that holds the whole scenario. Slides and Export give a deck, a one-page brief and a table.",
  },
];

/** True when this browser has finished or skipped the tour before. */
export function tourSeen() {
  try {
    return window.localStorage.getItem(TOUR_KEY) === "1";
  } catch {
    return false;
  }
}

/** Remember that the tour was seen (ignored where storage is blocked). */
export function markTourSeen() {
  try {
    window.localStorage.setItem(TOUR_KEY, "1");
  } catch {
    /* storage blocked: the tour is offered again next time */
  }
}

/** Forget the flag, so the tour is offered again. */
export function forgetTour() {
  try {
    window.localStorage.removeItem(TOUR_KEY);
  } catch {
    /* storage blocked: nothing to forget */
  }
}

/** True when an element takes up room on the screen. */
function visible(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
}

/** The first visible element a step names, or null. */
export function findTarget(step) {
  const sels = Array.isArray(step.selector) ? step.selector : [step.selector];
  for (const sel of sels) {
    if (!sel) continue;
    let list;
    try {
      list = [...document.querySelectorAll(sel)];
    } catch {
      continue; // a bad selector skips to the next fallback
    }
    const hit = list.find(
      (el) => visible(el) && (!step.text || (el.textContent || "").includes(step.text)),
    );
    if (hit) return hit;
  }
  return null;
}

/** True when two boxes ({left, top, right, bottom}) overlap. */
function overlaps(a, b) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

/**
 * Where the card goes: below, above, right or left of the spotlight, the first side where
 * the whole card fits inside the window without touching the spotlight. If no side fits,
 * the side with the most room, still kept off the spotlight where the window allows.
 */
export function placeCard(hole, card, view) {
  const clampX = (x) => Math.min(Math.max(x, EDGE), view.width - card.width - EDGE);
  const clampY = (y) => Math.min(Math.max(y, EDGE), view.height - card.height - EDGE);
  const cx = hole.left + hole.width / 2 - card.width / 2;
  const cy = hole.top + hole.height / 2 - card.height / 2;
  const sides = [
    { side: "bottom", left: clampX(cx), top: hole.top + hole.height + GAP, room: view.height - (hole.top + hole.height) },
    { side: "top", left: clampX(cx), top: hole.top - GAP - card.height, room: hole.top },
    { side: "right", left: hole.left + hole.width + GAP, top: clampY(cy), room: view.width - (hole.left + hole.width) },
    { side: "left", left: hole.left - GAP - card.width, top: clampY(cy), room: hole.left },
  ];
  const holeBox = { left: hole.left, top: hole.top, right: hole.left + hole.width, bottom: hole.top + hole.height };
  const fits = (p) =>
    p.left >= EDGE - 0.5 &&
    p.top >= EDGE - 0.5 &&
    p.left + card.width <= view.width - EDGE + 0.5 &&
    p.top + card.height <= view.height - EDGE + 0.5 &&
    !overlaps({ left: p.left, top: p.top, right: p.left + card.width, bottom: p.top + card.height }, holeBox);
  const best = sides.find(fits) || [...sides].sort((a, b) => b.room - a.room)[0];
  return { side: best.side, left: Math.round(clampX(best.left)), top: Math.round(clampY(best.top)) };
}

/** The coach-mark tour. `steps` defaults to TOUR_STEPS; `onClose(finished)` is called once. */
export function Tour({ steps = TOUR_STEPS, onClose, start = 0 }) {
  const [i, setI] = useState(Math.min(Math.max(start, 0), steps.length - 1));
  const [geo, setGeo] = useState(null);
  const card = useRef(null);
  const nextBtn = useRef(null);
  const returnTo = useRef(null);
  const closed = useRef(false);
  const id = useId();
  const still = reducedMotion();
  const step = steps[i];
  const last = i === steps.length - 1;

  const close = (finished) => {
    if (closed.current) return;
    closed.current = true;
    markTourSeen();
    onClose?.(finished);
  };
  const next = () => (last ? close(true) : setI((n) => Math.min(n + 1, steps.length - 1)));
  const back = () => setI((n) => Math.max(n - 1, 0));

  // Remember where focus was, and give it back on close.
  useEffect(() => {
    returnTo.current = document.activeElement;
    return () => {
      const el = returnTo.current;
      if (el && el.isConnected && typeof el.focus === "function") el.focus();
    };
  }, []);

  // Measure the step's element and place the spotlight and the card; again on resize/scroll.
  useLayoutEffect(() => {
    let frame = 0;
    const target = findTarget(step);
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "nearest", inline: "nearest", behavior: still ? "auto" : "smooth" });
    }
    const measure = () => {
      frame = 0;
      const view = { width: window.innerWidth, height: window.innerHeight };
      const box = card.current?.getBoundingClientRect();
      const size = { width: box?.width || 320, height: box?.height || 180 };
      const el = target && target.isConnected ? target : findTarget(step);
      if (!el) {
        setGeo({ hole: null, card: { side: "center", left: Math.round((view.width - size.width) / 2), top: Math.round((view.height - size.height) / 2) } });
        return;
      }
      const r = el.getBoundingClientRect();
      const hole = {
        left: Math.max(r.left - PAD, 0),
        top: Math.max(r.top - PAD, 0),
        width: Math.min(r.width + PAD * 2, view.width),
        height: Math.min(r.height + PAD * 2, view.height),
      };
      setGeo({ hole, card: placeCard(hole, size, view) });
    };
    const soon = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    measure();
    // Once more after a smooth scroll or a late layout settles.
    const late = setTimeout(measure, still ? 0 : 360);
    window.addEventListener("resize", soon);
    window.addEventListener("scroll", soon, true);
    return () => {
      clearTimeout(late);
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("resize", soon);
      window.removeEventListener("scroll", soon, true);
    };
  }, [i, steps]);

  // Each new step puts focus on Next (Done), so Enter keeps going.
  useEffect(() => {
    nextBtn.current?.focus({ preventScroll: true });
  }, [i]);

  // Keys: Enter / Right next, Left back, Esc close, Tab stays in the card.
  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented && e.key !== "Escape") return;
      const inCard = card.current?.contains(e.target);
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close(false);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        e.stopPropagation();
        next();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        e.stopPropagation();
        back();
      } else if (e.key === "Enter") {
        // A focused button in the card does its own job (Back, Skip); elsewhere Enter is Next.
        if (inCard && e.target instanceof HTMLButtonElement) return;
        e.preventDefault();
        e.stopPropagation();
        next();
      } else if (e.key === "Tab") {
        const f = [...(card.current?.querySelectorAll("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])") || [])];
        if (!f.length) return;
        const first = f[0];
        const end = f[f.length - 1];
        if (!inCard) {
          e.preventDefault();
          first.focus();
        } else if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          end.focus();
        } else if (!e.shiftKey && document.activeElement === end) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    // Focus that escapes the card (a click on the page) is brought back.
    const onFocus = (e) => {
      if (card.current && !card.current.contains(e.target)) nextBtn.current?.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocus, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", onFocus, true);
    };
  }, [i, steps]);

  if (!step) return null;
  const hole = geo?.hole;
  const pos = geo?.card;
  return html`<div class=${`tour ${still ? "tour--still" : ""}`} data-step=${step.id || i}>
    <div class="tour-blocker" aria-hidden="true"
      onMouseDown=${(e) => e.preventDefault()} onClick=${(e) => e.stopPropagation()}></div>
    ${hole
      ? html`<div class="tour-hole" aria-hidden="true"
          style=${`left:${hole.left}px;top:${hole.top}px;width:${hole.width}px;height:${hole.height}px`}></div>`
      : html`<div class="tour-dim" aria-hidden="true"></div>`}
    <div class=${`tour-card tour-card--${pos?.side || "center"}`} ref=${card} role="dialog" aria-modal="true"
      aria-labelledby=${`${id}-t`} aria-describedby=${`${id}-b`}
      style=${pos ? `left:${pos.left}px;top:${pos.top}px` : "opacity:0;left:0;top:0"}>
      <p class="tour-count">Step ${i + 1} of ${steps.length}</p>
      <h2 class="tour-title" id=${`${id}-t`}>${step.title}</h2>
      <p class="tour-body" id=${`${id}-b`}>${step.body}</p>
      <div class="tour-dots" aria-hidden="true">
        ${steps.map((_, k) => html`<span class=${`tour-dot ${k === i ? "is-on" : ""}`}></span>`)}
      </div>
      <div class="tour-actions">
        <button type="button" class="btn btn-ghost btn-xs tour-skip" onClick=${() => close(false)}>Skip tour</button>
        <span class="tour-spacer"></span>
        <button type="button" class="btn btn-quiet btn-xs tour-back" onClick=${back} disabled=${i === 0}>Back</button>
        <button type="button" class="btn btn-primary btn-xs tour-next" ref=${nextBtn} onClick=${next}>${last ? "Done" : "Next"}</button>
      </div>
      <p class="tour-keys">Enter or → next · ← back · Esc closes</p>
    </div>
  </div>`;
}
