/**
 * The mode switcher and the brand mark, floating over the map (there is no top bar).
 *
 *   ModeSwitcher  State · Reform · Compare · Place · Method & data, one pill at the top centre
 *                 of the map. A white pill glides under the current mode; a light highlight
 *                 follows the pointer. The map stays put when the mode changes; only the side
 *                 panels change (ui/shell.js). On a phone the icons hide and "Method & data"
 *                 shows as "Method" (its full name stays for screen readers), so all five fit.
 *   Brand         the product mark and name, small, in the top-left corner (it opens the State
 *                 mode). The name hides on narrow screens.
 *
 * The links are plain links to the routes (#/state, #/reform/adu ...), so share links, Back
 * and Forward keep working.
 */
import { html, useEffect, useLayoutEffect, useRef } from "../lib/html.js";
import { defaultReform, hrefFor, route } from "../state/store.js";
import { Icon, Mark } from "./icons.js";
import { reducedMotion } from "./motion.js";

export const TABS = [
  { name: "state", label: "State", icon: "sliders" },
  { name: "reform", label: "Reform", icon: "funnel" },
  { name: "compare", label: "Compare", icon: "compare" },
  { name: "place", label: "Place", icon: "pin" },
  { name: "method", label: "Method & data", short: "Method", icon: "book" },
];

export function tabHref(name) {
  return name === "reform" ? hrefFor("reform", defaultReform()) : hrefFor(name);
}

/** Slide `pill` under the element of `row` that matches `selector` (first placement instant). */
function useGlide(row, pill, current) {
  const placed = useRef(false);
  const place = () => {
    const box = row.current;
    const p = pill.current;
    if (!box || !p) return;
    const on = box.querySelector('[aria-current="page"]');
    if (!on || !on.offsetWidth) {
      p.style.opacity = "0";
      return;
    }
    p.classList.toggle("is-instant", !placed.current || reducedMotion());
    p.style.opacity = "1";
    p.style.width = `${on.offsetWidth}px`;
    p.style.transform = `translateX(${on.offsetLeft}px)`;
    placed.current = true;
    on.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  };
  useLayoutEffect(place, [current]);
  useEffect(() => {
    const again = () => {
      placed.current = false;
      place();
    };
    window.addEventListener("resize", again);
    document.fonts?.ready?.then(again).catch(() => {});
    return () => window.removeEventListener("resize", again);
  }, []);
}

export function Brand() {
  return html`<a class="brand" href=${hrefFor("state")} aria-label="VisionHousing, go to the State mode" data-zone="brand">
    <${Mark} /><span class="brand-name">VisionHousing</span>
  </a>`;
}

export function ModeSwitcher() {
  const current = route.value.name;
  const row = useRef(null);
  const glide = useRef(null);
  const halo = useRef(null);
  useGlide(row, glide, current);
  const over = (e) => {
    const tab = e.target.closest?.(".tab");
    const h = halo.current;
    if (!tab || !h) return;
    h.classList.toggle("is-instant", !h.classList.contains("is-on") || reducedMotion());
    h.style.width = `${tab.offsetWidth}px`;
    h.style.transform = `translateX(${tab.offsetLeft}px)`;
    h.classList.add("is-on");
  };
  return html`<nav class="switcher tabs" aria-label="Screens" ref=${row} data-zone="switch" onPointerOver=${over}
      onPointerLeave=${() => halo.current?.classList.remove("is-on")}>
    <span class="tabs-hover" aria-hidden="true" ref=${halo}></span>
    <span class="tabs-glide" aria-hidden="true" ref=${glide}></span>
    ${TABS.map(
      (t) => html`<a class="tab" href=${tabHref(t.name)} aria-current=${current === t.name ? "page" : undefined}>
        <${Icon} name=${t.icon} size=${15} /><span class="tab-label">${t.short
          ? html`${t.short}<span class="tab-more">${t.label.slice(t.short.length)}</span>`
          : t.label}</span></a>`,
    )}
  </nav>`;
}
