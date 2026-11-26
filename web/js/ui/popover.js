/**
 * Popovers that work by keyboard, touch and mouse (not hover-only).
 *
 * - `Popover`: a button that opens a small panel. Enter or Space opens it, Esc closes it and
 *   returns focus to the button, a click outside closes it. A mouse hover also opens it,
 *   as a convenience.
 * - `Term`: a glossary word. The word is the button; the panel gives the plain definition.
 * - `Explain` and `Num`: the "Explain this number" panel: the formula, inputs, assumptions,
 *   sources and likely range the engine attaches to every result.
 *
 * The panel is drawn in a layer at the end of `<body>` (a portal), not next to its button.
 * The side panels are glass cards (backdrop-filter, transforms while they fold), and such a card
 * becomes the containing block of every `position: fixed` element inside it: a panel placed in
 * window coordinates there landed off screen (offset again by the card's own position) or was
 * clipped by the card's scroll box. In the body layer, window coordinates are window coordinates.
 * Keyboard order is kept by hand: Tab on the open button moves into the panel, Tab past the
 * panel's last control (or Shift+Tab before its first) goes back to the button, so the reading
 * order is the same as when the panel sat after the button.
 */
import { GLOSSARY } from "../content/glossary.js";
import { lookups } from "../lib/data.js";
import { createPortal } from "preact/compat";
import { html, useId, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { Icon } from "./icons.js";
import { closingProps, Ticker, usePresence } from "./motion.js";

export function Popover({ trigger, triggerClass, title, label, children, wide = false }) {
  const [open, setOpen] = useState(false);
  const [byHover, setByHover] = useState(false);
  const id = useId();
  const root = useRef(null);
  const button = useRef(null);
  const panel = useRef(null);
  const hoverTimer = useRef(0);
  // A closing panel stays for its short exit, hidden from assistive technology.
  const [shown, closing] = usePresence(open);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        button.current?.focus();
      }
    };
    const onDown = (e) => {
      if (root.current?.contains(e.target) || panel.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  // Place the panel in window coordinates, so a scrolling card never clips it: under the
  // trigger, kept inside the window, flipped above when there is no room below.
  // Scrolling or resizing closes it rather than leaving it adrift.
  useLayoutEffect(() => {
    const el = panel.current;
    if (!open || !el || !button.current) return undefined;
    const margin = 12;
    const t = button.current.getBoundingClientRect();
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const start = t.left + w > window.innerWidth - margin ? t.right - w : t.left;
    const left = Math.max(margin, Math.min(start, window.innerWidth - w - margin));
    // Below the trigger if it fits; else on the side with more room, kept clear of the top
    // bar and capped to the room there (the panel scrolls inside).
    const bar = document.querySelector(".topbar")?.getBoundingClientRect().bottom || 0;
    const roomBelow = window.innerHeight - margin - (t.bottom + 8);
    const roomAbove = t.top - 8 - Math.max(margin, bar + 4);
    let top;
    if (h <= roomBelow || roomBelow >= roomAbove) {
      top = t.bottom + 8;
      el.style.maxHeight = `${Math.max(120, roomBelow)}px`;
    } else {
      const room = Math.max(120, roomAbove);
      el.style.maxHeight = `${room}px`;
      top = t.top - 8 - Math.min(h, room);
    }
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    // Only a scroll that really moves the trigger closes it. A scroll elsewhere (another
    // panel), or the late scroll event of the scroll that brought the trigger into view just
    // before the click, leaves it open.
    const away = (e) => {
      if (e.type === "resize") return setOpen(false);
      if (el.contains(e.target) || !button.current) return;
      const r = button.current.getBoundingClientRect();
      if (Math.abs(r.top - t.top) > 2 || Math.abs(r.left - t.left) > 2) setOpen(false);
    };
    window.addEventListener("scroll", away, true);
    window.addEventListener("resize", away);
    return () => {
      window.removeEventListener("scroll", away, true);
      window.removeEventListener("resize", away);
    };
  }, [open]);

  const enter = (e) => {
    if (e.pointerType !== "mouse") return;
    clearTimeout(hoverTimer.current);
    if (open) return;
    hoverTimer.current = setTimeout(() => {
      setByHover(true);
      setOpen(true);
    }, 250);
  };
  const leave = (e) => {
    if (e.pointerType !== "mouse") return;
    clearTimeout(hoverTimer.current);
    if (byHover) hoverTimer.current = setTimeout(() => setOpen(false), 200);
  };
  const click = () => {
    clearTimeout(hoverTimer.current);
    setByHover(false);
    setOpen(byHover ? true : !open);
  };
  // Tab order across the portal (see the module note).
  const focusables = () =>
    [...(panel.current?.querySelectorAll("a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex='-1'])") || [])];
  const buttonKey = (e) => {
    if (e.key !== "Tab" || e.shiftKey || !open || !panel.current) return;
    e.preventDefault();
    const list = focusables();
    (list[0] || panel.current).focus();
  };
  const panelKey = (e) => {
    if (e.key !== "Tab" || !panel.current) return;
    const list = focusables();
    const first = list[0] || panel.current;
    const last = list[list.length - 1] || panel.current;
    const at = document.activeElement;
    if (e.shiftKey && (at === first || at === panel.current)) {
      e.preventDefault();
      button.current?.focus();
    } else if (!e.shiftKey && at === last) {
      // Leave the panel forward: back on the button, then on to whatever follows it.
      e.preventDefault();
      setOpen(false);
      button.current?.focus();
      const zone = button.current?.closest("[data-zone]") || document.body;
      const all = [...zone.querySelectorAll("a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex='-1'])")]
        .filter((el) => el.offsetParent !== null || el === button.current);
      const next = all[all.indexOf(button.current) + 1];
      if (next) next.focus();
    }
  };

  const layer = typeof document !== "undefined" ? document.body : null;
  const body =
    shown &&
    html`<div id=${closing ? undefined : id} ref=${panel} class=${`pop-panel ${wide ? "is-wide" : ""} ${closing ? "is-closing" : ""}`}
        role=${closing ? undefined : "dialog"} aria-label=${closing ? undefined : title} tabindex="-1"
        onKeyDown=${panelKey} onPointerEnter=${enter} onPointerLeave=${leave} ...${closingProps(closing)}>
      <p class="pop-title">${title}</p>
      ${children}
    </div>`;
  return html`<span class="pop" ref=${root} onPointerEnter=${enter} onPointerLeave=${leave}>
    <button type="button" ref=${button} class=${triggerClass} aria-expanded=${open ? "true" : "false"}
      aria-controls=${id} aria-label=${label} onClick=${click} onKeyDown=${buttonKey}>${trigger}</button>
    ${body && (layer ? createPortal(body, layer) : body)}
  </span>`;
}

/** A glossary word: `<${Term} id="baseline">baseline<//>`. */
export function Term({ id, children }) {
  const entry = GLOSSARY[id];
  if (!entry) return children;
  return html`<${Popover} trigger=${children} triggerClass="term" title=${entry.term}>
    <p class="pop-text">${entry.text}</p>
  <//>`;
}

/** "34 units to 170 units (middle 84 units)" from a payload's text forms. */
export function rangeText(n) {
  const sh = n?.shown;
  if (!sh) return "";
  if (typeof sh === "string") return sh;
  if (sh.p10 !== undefined && sh.p90 !== undefined) {
    return sh.p50 !== undefined ? `${sh.p10} to ${sh.p90} (middle ${sh.p50})` : `${sh.p10} to ${sh.p90}`;
  }
  return sh.value || "";
}

function ExplainBody({ n }) {
  const { sources, assumptions } = lookups.value;
  const ids = n.assumptions
    ? n.assumptions
    : (n.assumption_ids || []).map((id) => ({ id, label: assumptions[id]?.label || "" }));
  const srcIds = n.source_ids || [];
  const rows = [
    ["What it is", n.name],
    ["Likely range", rangeText(n)],
    ["Formula", n.formula],
  ];
  return html`<dl class="explain">
      ${rows.map(
        ([dt, dd]) => html`<div class="explain-row">
          <dt>${dt}</dt><dd class=${dd ? "" : "is-empty"}>${dd || "—"}</dd>
        </div>`,
      )}
      ${n.inputs?.length > 0 &&
      html`<div class="explain-row"><dt>Inputs</dt><dd><ul class="explain-list">
        ${n.inputs.slice(0, 12).map((i) => html`<li>${i.name}: ${i.shown}</li>`)}
        ${n.inputs.length > 12 && html`<li>and ${n.inputs.length - 12} more in the CSV export</li>`}
      </ul></dd></div>`}
      <div class="explain-row"><dt>Assumptions</dt><dd class=${ids.length ? "" : "is-empty"}>
        ${ids.length
          ? html`<ul class="explain-list">
              ${ids.slice(0, 10).map((a) => html`<li>${a.label ? `${a.label} ` : ""}<code>${a.id}</code></li>`)}
              ${ids.length > 10 && html`<li>and ${ids.length - 10} more (see the Assumption Ledger)</li>`}
            </ul>`
          : "—"}
      </dd></div>
      <div class="explain-row"><dt>Sources</dt><dd class=${srcIds.length ? "" : "is-empty"}>
        ${srcIds.length
          ? html`<ul class="explain-list">
              ${srcIds.map((id) => html`<li>${sources[id]?.citation || id}</li>`)}
            </ul>`
          : "—"}
      </dd></div>
      ${n.tier_label && html`<div class="explain-row"><dt>Data</dt><dd>${n.tier_label}</dd></div>`}
    </dl>`;
}

/**
 * "Explain this number": a labelled button that opens the engine's explanation of one
 * number (its formula, inputs, assumptions and sources, and its likely range). `n` is the
 * explanation payload of the number (`explanation.numbers[ref]` in a run, or a record).
 */
export function Explain({ of, n = null }) {
  return html`<${Popover}
      trigger=${html`<${Icon} name="info" size=${15} /><span>Explain</span>`}
      triggerClass="btn btn-ghost btn-xs explain-btn" label=${`Explain: ${of}`}
      title=${`Explain: ${of}`} wide=${true}>
    ${n ? html`<${ExplainBody} n=${n} />` : html`<p class="pop-note">The engine has not sent this number yet.</p>`}
  <//>`;
}

/**
 * A number that explains itself: the engine's text (for example "34 to 170 units") as a
 * button; pressing it opens the same panel as Explain. When the scenario changes, its changed
 * digits roll from the old text to the new one (`Ticker`) and end on the engine's exact text.
 */
export function Num({ of, n, text, size = "md" }) {
  // Never a live region: each screen's one lead number (`.lead-number`) announces the new
  // value, so a scenario change is read out once, not once per number.
  const shown = text ?? rangeText(n);
  if (!n || !shown) return html`<span class=${`value value-${size} is-empty`} aria-label="Not available">—</span>`;
  return html`<${Popover} trigger=${html`<${Ticker} text=${shown} />`} triggerClass=${`value value-${size} num-btn`}
      label=${`${of}: ${shown}. Explain this number`} title=${`Explain: ${of}`} wide=${true}>
    <${ExplainBody} n=${n} />
  <//>`;
}
