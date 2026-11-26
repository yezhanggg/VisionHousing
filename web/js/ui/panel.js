/**
 * Tabs for the side panels (the Results card on the State screen, the side column on the
 * Reform screen), and a shared "which county is under the pointer" signal.
 *
 * `Tabs` is the WAI-ARIA tab pattern: one tab stop for the whole row; Left / Right (and
 * Home / End) move between tabs and show each one at once. Each tab names its panel with
 * `aria-controls`; `panelProps` gives a panel the matching id, role and label.
 *
 * A white pill glides to the tab that is on, and the panel that opens slides in from the side
 * of the tab that was pressed (`data-dir` on the row's group; CSS plays it, and reduced
 * motion turns it off).
 *
 * `hoveredCounty` holds the FIPS code of the county the reader points at or focuses, on the
 * map or in a list. The map outlines it and the lists highlight its row, so the two stay
 * linked both ways. `hoverPoint` is where the hover card sits on the map: the pointer when
 * the reader points at the map, the county's middle when they point at a row in a list.
 * `focusClass` is a map class picked in the map key: the map fades every other class.
 *
 * Pointing must stay cheap, so no view reads these signals while it renders:
 *   - `useHotRows(ref)` lights the row of the county pointed at (`[data-fips]` inside `ref`)
 *     by moving one class from the previous row to the next; nothing re-renders.
 *   - `useHoverFollow(ref)` moves the hover card to `hoverPoint` with transform only, in
 *     animation frames, trailing the pointer by about a tenth of a second (a soft spring).
 *     It returns a signal with the card's placement classes ("is-at", "from-map" or
 *     "is-docked"); the card puts it in its rendered `class`, so a re-render (a new county
 *     picked with the keyboard, say) never drops them and leaves the card off its county.
 * A light highlight also follows the pointer along a tab row, under the tab it is over.
 */
import { effect, signal, useSignal } from "@preact/signals";
import { html, useEffect, useLayoutEffect, useRef } from "../lib/html.js";
import { reducedMotion } from "./motion.js";

/** Light the row of the county pointed at: `is-hot` moves from the previous row to the next. */
export function useHotRows(ref) {
  const lit = useRef(null);
  const apply = () => {
    const id = hoveredCounty.peek();
    const box = ref.current;
    const row = id && box ? box.querySelector(`[data-fips="${CSS.escape(id)}"]`) : null;
    if (lit.current === row) {
      row?.classList.add("is-hot"); // a re-render may have reset the class
      return;
    }
    lit.current?.classList.remove("is-hot");
    row?.classList.add("is-hot");
    lit.current = row;
  };
  useEffect(() => effect(() => {
    void hoveredCounty.value;
    apply();
  }), []);
  // Rows re-rendered (a new sort, a new scenario): keep the light on the right one.
  useLayoutEffect(apply);
}

/**
 * Move the hover card (`ref`) to `hoverPoint` in animation frames, with transform only. It
 * trails the pointer slightly (about 100 ms to settle), flips to stay on screen, and docks
 * when there is no point (or on a phone, where CSS places it above the sheet).
 *
 * Returns a signal holding the placement classes. The card must render it in its `class`
 * (`class=${`map-hover ${place.value}`}`): Preact rewrites the whole class attribute on every
 * re-render, so classes set only by hand would be lost. The hook also sets them by hand at
 * once, so the first frame of a move is already placed before the re-render lands.
 */
export function useHoverFollow(ref) {
  const place = useSignal("is-docked");
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const phone = window.matchMedia("(max-width: 767px)");
    let size = { w: 248, h: 120 };
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(([e]) => {
      const b = e.borderBoxSize?.[0];
      size = b ? { w: b.inlineSize, h: b.blockSize } : { w: el.offsetWidth, h: el.offsetHeight };
    });
    ro?.observe(el);
    // `hoverPoint` is in map-area pixels; the card is placed in its offset parent. `origin` is
    // where the map area's top-left corner sits in that parent (the map fills the window
    // under the top bar today, so it is usually 0, 0). Measured when a card first shows and
    // after a resize, never on every pointer move.
    let origin = null;
    const measure = () => {
      const area = document.querySelector(".map-area:not(.is-compact)");
      const a = area?.getBoundingClientRect();
      const p = (el.offsetParent || document.documentElement).getBoundingClientRect();
      origin = a ? { x: a.left - p.left, y: a.top - p.top } : { x: 0, y: 0 };
    };
    const forget = () => {
      origin = null;
    };
    window.addEventListener("resize", forget);
    let raf = 0;
    let x = null;
    let y = null;
    let tx = 0;
    let ty = 0;
    let last = 0;
    const tick = (now) => {
      raf = 0;
      const dt = Math.min(64, last ? now - last : 16);
      last = now;
      const k = reducedMotion() || x === null ? 1 : 1 - Math.exp(-dt / 30);
      x = x === null ? tx : x + (tx - x) * k;
      y = y === null ? ty : y + (ty - y) * k;
      el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
      if (Math.abs(tx - x) > 0.3 || Math.abs(ty - y) > 0.3) raf = requestAnimationFrame(tick);
      else last = 0;
    };
    const stop = effect(() => {
      const at = hoverPoint.value;
      if (!at || phone.matches) {
        cancelAnimationFrame(raf);
        raf = 0;
        x = null;
        y = null;
        el.classList.remove("is-at", "from-map");
        el.classList.add("is-docked");
        el.style.transform = "";
        place.value = "is-docked";
        return;
      }
      el.classList.add("is-at");
      el.classList.remove("is-docked");
      el.classList.toggle("from-map", at.from === "map");
      place.value = at.from === "map" ? "is-at from-map" : "is-at";
      const shown = el.classList.contains("is-on");
      if (!origin || !shown) measure();
      const right = origin.x + at.x > window.innerWidth - 300;
      const low = origin.y + at.y > window.innerHeight - 260;
      tx = origin.x + (right ? at.x - size.w - 16 : at.x + 16);
      ty = origin.y + (low ? at.y - size.h - 16 : at.y + 16);
      // A card that was hidden appears where it belongs; a shown one glides there.
      if (!shown) x = null;
      if (!raf) raf = requestAnimationFrame(tick);
    });
    return () => {
      stop();
      cancelAnimationFrame(raf);
      ro?.disconnect();
      window.removeEventListener("resize", forget);
    };
  }, []);
  return place;
}

export const hoveredCounty = signal("");
/** {x, y} in map-area pixels, or null. */
export const hoverPoint = signal(null);
/** A class id ("little", "some", "a_lot") picked in the map key, or "". */
export const focusClass = signal("");
/**
 * What the county map shades (picked in the map key): "change" (the homes added as a share of
 * each county's baseline, the default), "per_1000" (homes added per 1,000 existing homes),
 * "added" (homes added) or "coverage" (evidence coverage). Per visit, never in the address.
 */
export const mapMeasure = signal("change");

/** Props that make an element the tab panel of tab `tab` in the group `group`. */
export function panelProps(group, tab, current) {
  return {
    id: `${group}-panel-${tab}`,
    role: "tabpanel",
    "aria-labelledby": `${group}-tab-${tab}`,
    hidden: tab !== current,
    tabindex: "0",
    class: "tab-panel",
  };
}

/** A row of tabs. `tabs` is [{id, label}]; `current` the shown tab's id. */
export function Tabs({ group, label, tabs, current, onChange }) {
  const row = useRef(null);
  const pill = useRef(null);
  const halo = useRef(null);
  const placed = useRef(false);
  const last = useRef(current);
  // Slide the pill under the tab that is on; the first placement is instant.
  const place = () => {
    const box = row.current;
    const el = box?.querySelector(`#${group}-tab-${current}`);
    if (!el || !pill.current) return;
    const before = tabs.findIndex((t) => t.id === last.current);
    const now = tabs.findIndex((t) => t.id === current);
    box.parentElement?.setAttribute("data-dir", now < before ? "back" : "fwd");
    last.current = current;
    pill.current.classList.toggle("is-instant", !placed.current || reducedMotion());
    pill.current.style.width = `${el.offsetWidth}px`;
    pill.current.style.transform = `translateX(${el.offsetLeft}px)`;
    placed.current = Boolean(el.offsetWidth);
  };
  useLayoutEffect(place, [current]);
  useEffect(() => {
    if (typeof ResizeObserver === "undefined" || !row.current) return undefined;
    const ro = new ResizeObserver(() => {
      placed.current = false;
      place();
    });
    ro.observe(row.current);
    return () => ro.disconnect();
  }, [current]);
  const move = (e) => {
    const i = tabs.findIndex((t) => t.id === current);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length];
    onChange(next.id);
    requestAnimationFrame(() => row.current?.querySelector(`#${group}-tab-${next.id}`)?.focus());
  };
  // The light highlight under the tab the pointer is over (first placement instant).
  const over = (e) => {
    const tab = e.target.closest?.(".seg-tab");
    const h = halo.current;
    if (!tab || !h) return;
    const fresh = !h.classList.contains("is-on");
    h.classList.toggle("is-instant", fresh || reducedMotion());
    h.style.width = `${tab.offsetWidth}px`;
    h.style.transform = `translateX(${tab.offsetLeft}px)`;
    h.classList.add("is-on");
  };
  const leave = () => halo.current?.classList.remove("is-on");
  return html`<div class="seg-tabs" role="tablist" aria-label=${label} ref=${row} onKeyDown=${move}
      onPointerOver=${over} onPointerLeave=${leave}>
    <span class="seg-hover" aria-hidden="true" ref=${halo}></span>
    <span class="seg-glide" aria-hidden="true" ref=${pill}></span>
    ${tabs.map(
      (t) => html`<button type="button" role="tab" class="seg-tab" id=${`${group}-tab-${t.id}`}
        aria-selected=${t.id === current ? "true" : "false"} aria-controls=${`${group}-panel-${t.id}`}
        tabindex=${t.id === current ? "0" : "-1"} onClick=${() => onChange(t.id)}>${t.label}</button>`,
    )}
  </div>`;
}
