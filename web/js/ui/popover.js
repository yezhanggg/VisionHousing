/**
 * Popovers that work by keyboard, touch and mouse (not hover-only).
 *
 * - `Popover`: a button that opens a small panel. Enter or Space opens it, Esc closes it and
 *   returns focus to the button, a click outside closes it. A mouse hover also opens it,
 *   as a convenience.
 * - `Term`: a glossary word. The word is the button; the panel gives the plain definition.
 * - `Explain`: the "Explain this number" panel. It will show the formula, inputs, sources
 *   and likely range that the engine attaches to every result. Until then each row says "—".
 */
import { GLOSSARY } from "../content/glossary.js";
import { html, useId, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { Icon } from "./icons.js";

export function Popover({ trigger, triggerClass, title, label, children, wide = false }) {
  const [open, setOpen] = useState(false);
  const [byHover, setByHover] = useState(false);
  const id = useId();
  const root = useRef(null);
  const button = useRef(null);
  const panel = useRef(null);
  const hoverTimer = useRef(0);

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
      if (root.current && !root.current.contains(e.target)) setOpen(false);
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
    const below = t.bottom + 8;
    const top = below + h > window.innerHeight - margin && t.top - h - 8 > margin ? t.top - h - 8 : below;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    const away = (e) => {
      if (!el.contains(e.target)) setOpen(false);
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

  return html`<span class="pop" ref=${root} onPointerEnter=${enter} onPointerLeave=${leave}>
    <button type="button" ref=${button} class=${triggerClass} aria-expanded=${open ? "true" : "false"}
      aria-controls=${id} aria-label=${label} onClick=${click}>${trigger}</button>
    ${open &&
    html`<div id=${id} ref=${panel} class=${`pop-panel ${wide ? "is-wide" : ""}`} role="dialog"
        aria-label=${title}>
      <p class="pop-title">${title}</p>
      ${children}
    </div>`}
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

const EXPLAIN_ROWS = ["Formula", "Inputs", "Sources", "Likely range"];

/**
 * "Explain this number". `of` names the number. `record` will be the engine's result
 * record ({formula, inputs, sources, range}); while it is missing every row shows "—".
 */
export function Explain({ of, record = null }) {
  const fields = record || {};
  const keys = { Formula: "formula", Inputs: "inputs", Sources: "sources", "Likely range": "range" };
  return html`<${Popover}
      trigger=${html`<${Icon} name="info" size=${15} /><span>Explain</span>`}
      triggerClass="btn btn-ghost btn-xs explain-btn" label=${`Explain: ${of}`}
      title=${`Explain: ${of}`} wide=${true}>
    <dl class="explain">
      ${EXPLAIN_ROWS.map(
        (row) => html`<div class="explain-row">
          <dt>${row}</dt>
          <dd class=${fields[keys[row]] ? "" : "is-empty"}>${fields[keys[row]] || "—"}</dd>
        </div>`,
      )}
    </dl>
    ${!record && html`<p class="pop-note">Filled in by the engine once it is connected.</p>`}
  <//>`;
}
