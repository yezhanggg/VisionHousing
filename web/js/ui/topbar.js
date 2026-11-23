/**
 * The one top bar, the same on every screen.
 *   left:   the product name (goes to the State screen)
 *   centre: State · Reform · Compare · Method & data
 *   right:  Slides · Export ▾ · Share link
 * Under 768 px wide the tabs and the buttons fold into a labelled "Menu".
 */
import { html, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { defaultReform, hrefFor, navigate, route, showToast } from "../state/store.js";
import { Icon, Mark } from "./icons.js";

export const TABS = [
  { name: "state", label: "State" },
  { name: "reform", label: "Reform" },
  { name: "compare", label: "Compare" },
  { name: "method", label: "Method & data" },
];

function tabHref(name) {
  return name === "reform" ? hrefFor("reform", defaultReform()) : hrefFor(name);
}

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

/** The three exports. Only the slides can be printed before the engine is connected. */
export const EXPORTS = [
  {
    id: "brief",
    label: "Brief (PDF)",
    hint: "Four-page summary",
    ready: false,
    why: "The brief needs results from the engine, which is not connected yet.",
  },
  { id: "slides", label: "Slides (PDF)", hint: "One slide per page", ready: true },
  {
    id: "csv",
    label: "Table (CSV)",
    hint: "Every result with its range",
    ready: false,
    why: "The table needs results from the engine, which is not connected yet.",
  },
];

/** Run an export. */
export function runExport(item) {
  if (!item.ready) {
    showToast(item.why, 3200);
    return;
  }
  if (item.id === "slides") {
    if (route.value.name !== "slides") navigate("slides");
    // Let the slides render before the print dialog opens.
    setTimeout(() => window.print(), 350);
  }
}

function ExportMenu() {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const button = useRef(null);
  const items = useRef([]);

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
    <button type="button" class="btn btn-quiet" ref=${button} aria-haspopup="menu"
      aria-expanded=${open ? "true" : "false"} aria-controls="export-menu"
      onClick=${() => setOpen(!open)} onKeyDown=${onButtonKey}>
      <${Icon} name="download" /><span>Export</span><${Icon} name="chevronDown" size=${16} />
    </button>
    ${open &&
    html`<div class="menu-panel" id="export-menu" role="menu" aria-label="Export" onKeyDown=${onMenuKey}>
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

function MobileMenu({ onClose }) {
  const current = route.value.name;
  const first = useRef(null);
  useLayoutEffect(() => {
    first.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onClose(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return html`<div class="mobile-menu" id="mobile-menu">
    <nav aria-label="Screens" class="mobile-nav">
      ${TABS.map(
        (t, i) => html`<a href=${tabHref(t.name)} class="mobile-link" ref=${i === 0 ? first : undefined}
            aria-current=${current === t.name ? "page" : undefined} onClick=${() => onClose(false)}>
          ${t.label}</a>`,
      )}
    </nav>
    <div class="mobile-actions">
      <a class="btn btn-quiet" href=${hrefFor("slides")} onClick=${() => onClose(false)}>
        <${Icon} name="slides" /><span>Slides</span></a>
      <button type="button" class="btn btn-quiet" onClick=${() => {
        onClose(false);
        shareLink();
      }}><${Icon} name="link" /><span>Share link</span></button>
    </div>
    <p class="mobile-heading">Export</p>
    <div class="mobile-exports">
      ${EXPORTS.map(
        (item) => html`<button type="button" class="menu-item"
            aria-disabled=${item.ready ? undefined : "true"}
            onClick=${() => {
              onClose(false);
              runExport(item);
            }}>
          <span class="menu-label">${item.label}</span>
          <span class="menu-hint">${item.ready ? item.hint : "Available once results exist"}</span>
        </button>`,
      )}
    </div>
  </div>`;
}

export function TopBar() {
  const current = route.value.name;
  const [menu, setMenu] = useState(false);
  const toggle = useRef(null);
  const closeMenu = (refocus) => {
    setMenu(false);
    if (refocus) toggle.current?.focus();
  };
  return html`<header class="topbar">
    <a class="brand" href=${hrefFor("state")} aria-label="VisionHousing, go to the State screen">
      <${Mark} /><span class="brand-name">VisionHousing</span>
    </a>
    <nav class="tabs" aria-label="Screens">
      ${TABS.map(
        (t) => html`<a class="tab" href=${tabHref(t.name)}
          aria-current=${current === t.name ? "page" : undefined}>${t.label}</a>`,
      )}
    </nav>
    <div class="actions">
      <a class="btn btn-quiet" href=${hrefFor("slides")}
        aria-current=${current === "slides" ? "page" : undefined}>
        <${Icon} name="slides" /><span>Slides</span></a>
      <${ExportMenu} />
      <button type="button" class="btn btn-primary" onClick=${shareLink}>
        <${Icon} name="link" /><span>Share link</span></button>
    </div>
    <button type="button" class="btn btn-quiet menu-toggle" ref=${toggle}
      aria-expanded=${menu ? "true" : "false"} aria-controls="mobile-menu"
      onClick=${() => setMenu(!menu)}>
      <${Icon} name=${menu ? "close" : "menu"} /><span>Menu</span>
    </button>
    ${menu && html`<${MobileMenu} onClose=${closeMenu} />`}
  </header>`;
}
