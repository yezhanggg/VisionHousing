/**
 * A small set of line icons drawn for this project (24 x 24 grid, stroke only).
 * Icons are always decorative: every button that shows one also shows its label.
 */
import { html } from "../lib/html.js";

const PATHS = {
  chevronDown: "M6 9l6 6 6-6",
  chevronUp: "M6 15l6-6 6 6",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5",
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
  minimise: "M5 12h14",
  panel: "M4 5h16v14H4zM10 5v14",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  rank: "M4 6h10M4 12h14M4 18h6",
  pin: "M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  book: "M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4zM5 16a4 4 0 0 1 4-4h10",
  compare: "M8 4v16M16 4v16M4 8h8M12 16h8",
  funnel: "M3 5h18l-7 8v6l-4 2v-8z",
  chat: "M4 5h16v11H9l-5 4z",
  plus: "M12 5v14M5 12h14",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01",
  key: "M4 6h4v4H4zM4 14h4v4H4zM12 8h8M12 16h8",
  cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  north: "M12 3l5 16-5-4-5 4z",
  play: "M8 5v14l11-7z",
  pause: "M8 5v14M16 5v14",
  calendar: "M4 6h16v14H4zM4 10h16M9 3v5M15 3v5",
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
