/**
 * A small set of line icons drawn for this project (24 x 24 grid, stroke only).
 * Icons are always decorative: every button that shows one also shows its label.
 */
import { html } from "../lib/html.js";

const PATHS = {
  chevronDown: "M6 9l6 6 6-6",
  chevronLeft: "M15 6l-6 6 6 6",
  chevronRight: "M9 6l6 6-6 6",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  slides: "M3 5h18v11H3zM8 20h8M12 16v4",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  menu: "M4 7h16M4 12h16M4 17h16",
  close: "M6 6l12 12M18 6L6 18",
  info: "M12 11v6M12 7.5v.5M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  print: "M7 9V4h10v5M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2M7 14h10v6H7z",
  page: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
  reset: "M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
};

/** Render one icon by name. */
export function Icon({ name, size = 18 }) {
  return html`<svg class="icon" width=${size} height=${size} viewBox="0 0 24 24" aria-hidden="true"
    focusable="false" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round"><path d=${PATHS[name]} /></svg>`;
}

/** The product mark: a roof line over three rising bars. */
export function Mark() {
  return html`<svg class="mark" width="28" height="28" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
    <rect width="32" height="32" rx="8" fill="var(--accent)" />
    <path d="M7 15l9-7 9 7" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
    <path d="M11 24v-4M16 24v-7M21 24v-5" stroke="#fff" stroke-width="2.4" stroke-linecap="round" />
  </svg>`;
}
