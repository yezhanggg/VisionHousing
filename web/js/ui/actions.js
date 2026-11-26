/**
 * The scenario's actions: open the slides, export (brief, hearing brief, slides, CSV), share the link.
 *
 * They used to sit in the top bar. The top bar now holds only the product name and the
 * screens, and these actions travel with the results instead: the State screen's Results
 * panel, the Reform screen's side panel, and the head of the Compare, Place and Method
 * pages. Every action works on the scenario in the address, so wherever the buttons sit they
 * do the same thing.
 *
 *   `ScenarioActions`  Slides (a link to the Slides view) · Export ▾ · Share link
 *                      `compact` draws them as small buttons for a panel header;
 *                      `slides={false}` leaves the Slides link out (the Slides view itself).
 *   `ExportMenu`       the WAI-ARIA menu button: Enter, Space or Down opens it, Up / Down /
 *                      Home / End move, Esc closes and returns focus, Tab leaves.
 */
import { html, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { downloadCsv, openBrief, openHearing } from "../lib/exports.js";
import { hrefFor, navigate, route, scenario, showToast } from "../state/store.js";
import { Icon } from "./icons.js";
import { closingProps, usePresence } from "./motion.js";

/** Copy the address (the full scenario) and say so. */
export async function shareLink() {
  const url = location.href;
  try {
    if (!navigator.clipboard) throw new Error("no clipboard");
    await navigator.clipboard.writeText(url);
    showToast("Link copied");
  } catch {
    // Fallback for browsers or contexts without the async clipboard.
    const box = document.createElement("textarea");
    box.value = url;
    box.setAttribute("readonly", "");
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    box.remove();
    showToast(ok ? "Link copied" : "Copy the address bar: it holds the full scenario", 3200);
  }
}

/** The three exports. Each is made from the current scenario's engine run. */
export const EXPORTS = [
  { id: "brief", label: "Brief (PDF)", hint: "Print-ready page", ready: true },
  { id: "slides", label: "Slides (PDF)", hint: "One slide per page", ready: true },
  { id: "csv", label: "Table (CSV)", hint: "Every result with its range", ready: true },
  { id: "hearing", label: "Hearing brief (PDF)", hint: "Two pages: question, range, baseline, limits", ready: true },
];

/** Run an export. */
export function runExport(item) {
  const sc = scenario.value;
  if (item.id === "brief") openBrief(sc);
  else if (item.id === "hearing") openHearing(sc);
  else if (item.id === "csv") downloadCsv(sc);
  else if (item.id === "slides") {
    if (route.value.name !== "slides") navigate("slides");
    // Let the slides render before the print dialog opens.
    setTimeout(() => window.print(), 600);
  }
}

export function ExportMenu({ compact = false }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const button = useRef(null);
  const items = useRef([]);
  const [shown, closing] = usePresence(open);

  const close = (refocus) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };

  useLayoutEffect(() => {
    if (!open) return undefined;
    items.current[0]?.focus();
    const onDown = (e) => {
      if (root.current && !root.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const onMenuKey = (e) => {
    const list = items.current.filter(Boolean);
    const at = list.indexOf(document.activeElement);
    const go = (i) => list[(i + list.length) % list.length]?.focus();
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") close(false);
    else return;
    if (e.key !== "Tab") e.preventDefault();
    e.stopPropagation();
  };

  const onButtonKey = (e) => {
    if (e.key === "ArrowDown" && !open) {
      e.preventDefault();
      setOpen(true);
    }
  };

  return html`<div class="menu" ref=${root}>
    <button type="button" class=${`btn btn-quiet ${compact ? "btn-xs" : ""}`} ref=${button} aria-haspopup="menu"
      aria-expanded=${open ? "true" : "false"} aria-controls="export-menu"
      onClick=${() => setOpen(!open)} onKeyDown=${onButtonKey}>
      <${Icon} name="download" size=${compact ? 15 : 18} /><span>Export</span><${Icon} name="chevronDown" size=${14} />
    </button>
    ${shown &&
    html`<div class=${`menu-panel ${closing ? "is-closing" : ""}`} id=${closing ? undefined : "export-menu"}
        role=${closing ? undefined : "menu"} aria-label=${closing ? undefined : "Export"} onKeyDown=${onMenuKey}
        ...${closingProps(closing)}>
      ${EXPORTS.map(
        (item, i) => html`<button type="button" role="menuitem" class="menu-item" tabindex="-1"
            ref=${(el) => {
              items.current[i] = el;
            }}
            aria-disabled=${item.ready ? undefined : "true"}
            onClick=${() => {
              close(true);
              runExport(item);
            }}>
          <span class="menu-label">${item.label}</span>
          <span class="menu-hint">${item.ready ? item.hint : "Available once results exist"}</span>
        </button>`,
      )}
    </div>`}
  </div>`;
}

/** Slides · Export · Share link, for a panel header or a page head. */
export function ScenarioActions({ compact = false, slides = true, label = "Share and export" }) {
  const xs = compact ? "btn-xs" : "";
  const size = compact ? 15 : 18;
  return html`<div class=${`scenario-actions ${compact ? "is-compact" : ""}`} role="group" aria-label=${label}>
    ${slides &&
    html`<a class=${`btn btn-quiet ${xs}`} href=${hrefFor("slides")}>
      <${Icon} name="slides" size=${size} /><span>Slides</span></a>`}
    <${ExportMenu} compact=${compact} />
    <button type="button" class=${`btn btn-primary ${xs}`} onClick=${shareLink}>
      <${Icon} name="link" size=${size} /><span>Share link</span></button>
  </div>`;
}
