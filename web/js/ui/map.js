/**
 * The county map, its basemap and its key.
 *
 * The map is drawn by MapLibre GL (BSD-3), bundled by Vite and loaded only when the map is
 * first shown. MapLibre draws in a Web Worker; its worker file is copied into the build as is
 * and named with `setWorkerUrl`, so it loads from this site. The county outlines come from the
 * state pack (GET /states/{st}/outlines).
 *
 * Basemap (optional, a third-party request): under the counties the map can show a quiet
 * street-and-place background from OpenFreeMap (https://openfreemap.org), a free public
 * tile service with no key, no account and no cookies, built from OpenStreetMap data. Its
 * light "positron" style is used (its "dark" style if the page is ever in a dark theme). Place
 * labels stay above the county fills; road shields and points of interest are dropped. The
 * credit "OpenFreeMap © OpenMapTiles Data from OpenStreetMap" shows on the map whenever the
 * basemap is on. The "Basemap" button in the key turns it off (the choice is kept in this
 * browser); a build made with VITE_BASEMAP=off starts with it off (the offline tests do).
 * If the style or its tiles do not load, the map goes back to the plain look by itself:
 * no basemap, no error. The Content Security Policy allows exactly this one host.
 *
 * Each county is filled by its class from the engine's map reply (GET /states/{st}/map):
 * "Little effect", "Adds some" or "Adds a lot", the verdict rule of the Assumption Ledger
 * applied to the county's added homes as a share of its own baseline. The breaks are fixed,
 * so colours mean the same thing whatever is switched on. Grey is "no data".
 * The shapes are set once per state (each county's geoid is its feature id); a new scenario
 * only pushes each county's class as feature state, so nothing re-tiles before the fill wave.
 * Pointing at a county raises it (a soft white glow under an accent outline, a lighter fill)
 * and lights its row in the lists (`hoveredCounty`); the hover card follows the pointer
 * (`hoverPoint`). Hover is feature state too, set from the signal outside rendering, so
 * pointing never re-renders the map view or the lists.
 * The basemap's layers are added into the map once; turning it off or on only hides or shows
 * them, so the map never goes blank. While the map library loads, a still drawing of the
 * counties stands in and fades out when the map has drawn; the library itself is fetched
 * ahead when the browser is idle. Clicking a county (or pressing Enter on its row in the table) opens the
 * county panel: a ring ripples out from the click, the rest of the state dims, and the view
 * eases to the county (and back to the whole state when the panel closes).
 * When the classes change (a reform switched on or off), the counties that change fill in
 * as a wave from west to east. Picking a class in the map key fades the others.
 * Every movement is skipped when the reader asks for less motion.
 * If the browser cannot draw WebGL, the page says so and the table still works.
 */
import { effect, signal } from "@preact/signals";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { Icon } from "./icons.js";
import { reducedMotion, useHeightMorph } from "./motion.js";
import { districtView, mainMap, view3d } from "./map-state.js";
import { focusClass, hoveredCounty, hoverPoint } from "./panel.js";

/** The one basemap host; the Content Security Policy names it (connect-src, img-src). */
export const BASEMAP_HOST = "https://tiles.openfreemap.org";
const BASEMAP_STYLES = { light: `${BASEMAP_HOST}/styles/positron`, dark: `${BASEMAP_HOST}/styles/dark` };
const PREF_KEY = "visionhousing.basemap";
const STYLE_TIMEOUT_MS = 8000;
const TILE_FAILURES_BEFORE_FALLBACK = 6;
/** Symbol layers left out of the basemap so the counties stay the subject. */
const DROP = /shield|poi|aeroway|airport|oneway|housenumber|highway-name|highway_name|road_label|building/i;

function readPref() {
  if ((import.meta.env || {}).VITE_BASEMAP === "off") {
    try {
      return localStorage.getItem(PREF_KEY) === "on";
    } catch {
      return false;
    }
  }
  try {
    return localStorage.getItem(PREF_KEY) !== "off";
  } catch {
    return true;
  }
}

/** Whether the basemap is wanted (the reader's choice), and how it is doing. */
export const basemapWanted = signal(typeof window === "undefined" ? false : readPref());
/** "off", "loading", "on" or "failed". */
export const basemapState = signal("off");

export function setBasemap(on) {
  basemapWanted.value = on;
  try {
    localStorage.setItem(PREF_KEY, on ? "on" : "off");
  } catch {
    // Storage blocked: the choice lasts for this page only.
  }
}

function darkTheme() {
  const root = document.documentElement;
  if (root.dataset.theme) return root.dataset.theme === "dark";
  const scheme = getComputedStyle(root).colorScheme || "";
  return scheme.includes("dark") && (!scheme.includes("light") || window.matchMedia("(prefers-color-scheme: dark)").matches);
}

const MESSAGES = {
  loading: "Loading the map…",
  missing: "The map library did not load. The county table below has the same values.",
  nogl: "This browser cannot draw the map. The county table has the same values.",
  noshapes: "No county outlines for this state. The county table has the values.",
};

/** Class id to the colour token it is filled with. */
export const CLASS_COLOURS = {
  none: "--not-modelled",
  nodata: "--no-data",
  little: "--seq-2",
  some: "--seq-4",
  a_lot: "--seq-6",
  // Homes added where the baseline is 0: no share to show (api/views.py `no_base`).
  no_base: "--seq-1",
};

/** Back-test error classes to colour tokens: blue where the baseline ran low, red where high. */
export const ERROR_COLOURS = {
  well_low: "--div-1",
  low: "--div-2",
  near: "--div-3",
  high: "--div-4",
  well_high: "--div-5",
  nodata: "--no-data",
};

/**
 * The map's other measures (api/decision.py): four fixed classes for homes added per 1,000
 * existing homes and for homes added (`q1`..`q4`, the sequential ramp), and three for
 * evidence coverage (lighter is thinner).
 */
export const MEASURE_COLOURS = {
  q1: "--seq-2",
  q2: "--seq-3",
  q3: "--seq-5",
  q4: "--seq-6",
  cov_good: "--seq-5",
  cov_partial: "--seq-3",
  cov_thin: "--seq-1",
  // "What this law changes" (ui/law-changes.js): the four answers, and outside a bill.
  law_already_allowed: "--seq-2",
  law_changed_condition: "--seq-4",
  law_newly_allowed: "--seq-6",
  law_unresolved: "--no-data",
  law_outside: "--not-modelled",
};

/** Every class id the map can show, in one palette (the ids never clash). */
export const MAP_COLOURS = { ...CLASS_COLOURS, ...ERROR_COLOURS, ...MEASURE_COLOURS };

/** The measures offered in the map key: id and short label. */
const MEASURE_LABELS = [
  ["change", "% of baseline"],
  ["per_1000", "Per 1,000 homes"],
  ["added", "Homes added"],
  ["coverage", "Coverage"],
];

/**
 * Each county's class under a measure of the map reply: "change" is the verdict class; the
 * others are the classes the API put each county in (`measure_class`, `coverage`).
 */
export function classesFor(map, measure) {
  const out = {};
  for (const c of map?.counties || []) {
    if (measure === "coverage") out[c.fips] = c.coverage?.class || "nodata";
    else if (measure === "per_1000" || measure === "added") out[c.fips] = c.measure_class?.[measure] || "nodata";
    else out[c.fips] = c.class;
  }
  return out;
}

/** The measures this map reply can show (with no reform on: the effect prompt and coverage). */
export function measuresOf(map) {
  const have = new Set((map?.measures || []).map((m) => m.id));
  if (!have.size) return [];
  const any = map.reforms?.length > 0;
  return MEASURE_LABELS.filter(([id]) => (id === "change" ? true : have.has(id) && (any || id === "coverage")));
}

export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#cccccc";
}

/** The bounding box of a feature collection, for fitting the view. */
function bounds(fc) {
  let w = 180;
  let s = 90;
  let e = -180;
  let n = -90;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]);
      e = Math.max(e, c[0]);
      s = Math.min(s, c[1]);
      n = Math.max(n, c[1]);
    } else c.forEach(walk);
  };
  fc.features.forEach((f) => f.geometry && walk(f.geometry.coordinates));
  return w <= e ? [[w, s], [e, n]] : null;
}

/** Each feature's bounding box by geoid, for the camera, the hover card and the fill wave. */
function boxesOf(fc) {
  const out = {};
  for (const f of fc?.features || []) {
    if (!f.geometry) continue;
    const b = bounds({ features: [f] });
    if (b) out[String(f.properties?.geoid)] = b;
  }
  return out;
}

/** The outlines with each county's class joined in (no numbers are added). */
function joined(outlines, classes) {
  return {
    type: "FeatureCollection",
    features: (outlines?.features || []).map((f) => ({
      ...f,
      properties: {
        ...f.properties,
        cls: classes[String(f.properties?.geoid)] || "nodata",
      },
    })),
  };
}

/**
 * A county's class as the map reads it: the `cls` feature state (pushed on every scenario
 * change, so the shapes never re-tile), else the class baked in when the shapes were set.
 */
const CLS = ["to-string", ["coalesce", ["feature-state", "cls"], ["get", "cls"]]];
const HOVER = ["boolean", ["feature-state", "hover"], false];

function fillColour(palette) {
  const fill = ["match", CLS];
  Object.entries(palette).forEach(([k, v]) => fill.push(k, token(v)));
  fill.push(token("--no-data"));
  return fill;
}

/** Picking a class in the map key: every other class is painted the page colour. */
function fadeColour(focus) {
  return ["case", ["==", CLS, focus || "__none__"], "rgba(0,0,0,0)", token("--surface")];
}

const fillOpacity = (withBase) => ["*", withBase ? 0.74 : 0.92, ["coalesce", ["feature-state", "k"], 1]];

/** The county source and layers. Hover and class live in feature state, not in filters. */
function countyLayers(data, picked, withBase, palette = CLASS_COLOURS, focus = "") {
  const surface = token("--surface");
  const accent = token("--accent");
  return {
    // promoteId: each county's geoid is its feature id, so hover, class and the fill wave
    // are feature state.
    source: { type: "geojson", data, promoteId: "geoid" },
    layers: [
      {
        id: "fill",
        type: "fill",
        source: "counties",
        paint: {
          "fill-color": fillColour(palette),
          // `k` (feature state) runs from low to 1 while a county fills in; 1 when at rest.
          "fill-opacity": fillOpacity(withBase),
        },
      },
      {
        id: "class-fade",
        type: "fill",
        source: "counties",
        paint: {
          "fill-color": fadeColour(focus),
          "fill-opacity": focus ? 0.72 : 0,
          "fill-opacity-transition": { duration: reducedMotion() ? 0 : 280, delay: 0 },
        },
      },
      {
        // A county is open: the rest of the state dims, so the open one stands out.
        id: "dim",
        type: "fill",
        source: "counties",
        filter: ["!=", ["get", "geoid"], picked || "__none__"],
        paint: {
          "fill-color": token("--text"),
          "fill-opacity": picked ? 0.16 : 0,
          "fill-opacity-transition": { duration: reducedMotion() ? 0 : 450, delay: 0 },
        },
      },
      {
        id: "line",
        type: "line",
        source: "counties",
        paint: { "line-color": surface, "line-width": withBase ? 0.8 : 1, "line-opacity": withBase ? 0.85 : 1 },
      },
      {
        id: "hover-fill",
        type: "fill",
        source: "counties",
        paint: { "fill-color": surface, "fill-opacity": ["case", HOVER, 0.2, 0] },
      },
      {
        id: "hover-glow",
        type: "line",
        source: "counties",
        paint: { "line-color": surface, "line-width": 7, "line-blur": 2, "line-opacity": ["case", HOVER, 0.9, 0] },
      },
      {
        id: "hover",
        type: "line",
        source: "counties",
        paint: { "line-color": accent, "line-width": 3, "line-opacity": ["case", HOVER, 1, 0] },
      },
      {
        id: "picked-glow",
        type: "line",
        source: "counties",
        filter: ["==", ["get", "geoid"], picked || ""],
        paint: { "line-color": surface, "line-width": 8, "line-blur": 2.5, "line-opacity": 0.95 },
      },
      {
        id: "picked",
        type: "line",
        source: "counties",
        filter: ["==", ["get", "geoid"], picked || ""],
        paint: { "line-color": token("--text"), "line-width": 2.5 },
      },
    ],
  };
}

/** The plain style: the counties on a clear background. The basemap is added into it later. */
function buildStyle(data, picked, palette, focus = "") {
  const own = countyLayers(data, picked, false, palette, focus);
  return {
    version: 8,
    sources: { counties: own.source },
    layers: [{ id: "background", type: "background", paint: { "background-opacity": 0 } }, ...own.layers],
  };
}

/**
 * Add the basemap's sources and layers into the running map, once: its ground layers under
 * the counties, its place labels above them. Ids get a "bm-" prefix so they never clash with
 * the county layers. Turning the basemap off or on later only flips their visibility, so the
 * map never goes blank while it restyles.
 */
function addBase(map, base) {
  if (base.glyphs) map.setGlyphs(base.glyphs);
  if (typeof base.sprite === "string") map.setSprite(base.sprite);
  else if (Array.isArray(base.sprite)) base.sprite.forEach((s) => map.addSprite(s.id, s.url));
  for (const [id, src] of Object.entries(base.sources)) {
    if (!map.getSource(`bm-${id}`)) map.addSource(`bm-${id}`, src);
  }
  const ids = [];
  for (const l of base.layers) {
    if (l.type === "symbol" && DROP.test(l.id)) continue;
    const layer = { ...l, id: `bm-${l.id}`, layout: { ...l.layout, visibility: "visible" } };
    if (l.source) layer.source = `bm-${l.source}`;
    if (l.type === "symbol") layer.paint = { ...l.paint, "text-opacity": 0.78, "icon-opacity": 0.6 };
    if (map.getLayer(layer.id)) continue;
    // Ground layers go under the county fill; labels go on top of everything.
    map.addLayer(layer, l.type === "symbol" ? undefined : "fill");
    ids.push(layer.id);
  }
  return ids;
}

/** Show or hide the basemap layers and restyle the counties for it. */
function showBase(map, ids, on) {
  for (const id of ids) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }
  map.setPaintProperty("fill", "fill-opacity", fillOpacity(on));
  map.setPaintProperty("line", "line-width", on ? 0.8 : 1);
  map.setPaintProperty("line", "line-opacity", on ? 0.85 : 1);
}

/** Fetch the basemap style (with a time limit). Rejects on any failure. */
async function loadBase(dark) {
  const url = dark ? BASEMAP_STYLES.dark : BASEMAP_STYLES.light;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), STYLE_TIMEOUT_MS);
  try {
    const reply = await fetch(url, { signal: ctl.signal, credentials: "omit", referrerPolicy: "no-referrer" });
    if (!reply.ok) throw new Error(`basemap style ${reply.status}`);
    const style = await reply.json();
    if (!Array.isArray(style?.layers) || !style.sources) throw new Error("basemap style is not a style");
    return style;
  } finally {
    clearTimeout(timer);
  }
}

/** A ring that grows and fades where the reader clicked (CSS `.map-ripple`). */
function ripple(container, point) {
  const area = container?.parentElement;
  if (!area || reducedMotion()) return;
  const dot = document.createElement("span");
  dot.className = "map-ripple";
  dot.setAttribute("aria-hidden", "true");
  dot.style.left = `${point.x}px`;
  dot.style.top = `${point.y}px`;
  dot.addEventListener("animationend", () => dot.remove());
  area.appendChild(dot);
  setTimeout(() => dot.remove(), 1200);
}

const WAVE_SPREAD = 520;
const WAVE_EACH = 460;
const WAVE_FROM = 0.12;

/**
 * Fill the counties whose class changed as a wave from west to east: each one's `k` feature
 * state runs from faint to 1, starting later the further east it lies. Only the drawing
 * changes; the colours are the engine's classes from the first frame.
 */
function fillWave(map, ids, boxes, raf) {
  cancelAnimationFrame(raf.current);
  const list = ids.filter((id) => boxes[id]);
  if (!list.length) return;
  const xs = list.map((id) => (boxes[id][0][0] + boxes[id][1][0]) / 2);
  const x0 = Math.min(...xs);
  const span = Math.max(...xs) - x0 || 1;
  const delay = Object.fromEntries(list.map((id, i) => [id, ((xs[i] - x0) / span) * WAVE_SPREAD]));
  const set = (id, k) => {
    try {
      map.setFeatureState({ source: "counties", id }, { k });
    } catch {
      // The map is going away: nothing to draw.
    }
  };
  list.forEach((id) => set(id, WAVE_FROM));
  const t0 = performance.now();
  const step = (now) => {
    let busy = false;
    for (const id of list) {
      const t = Math.min(1, Math.max(0, (now - t0 - delay[id]) / WAVE_EACH));
      if (t < 1) busy = true;
      set(id, WAVE_FROM + (1 - WAVE_FROM) * (1 - (1 - t) ** 3));
    }
    if (busy) raf.current = requestAnimationFrame(step);
  };
  raf.current = requestAnimationFrame(step);
}

/**
 * The free part of the screen over the map, as insets in pixels from each edge: what the
 * floating panels, the mode switcher, the chat box and the legend leave clear. The shell
 * (ui/shell.js) measures its zones and writes this; the map fits the state inside it.
 */
export const mapInsets = signal(null);

/** The whole state's bounds and fit padding (the 3D view's pitch follows the zoom from there). */
export function stateFrame(outlines) {
  const b = outlines ? bounds(outlines) : null;
  return b ? { bounds: b, padding: fitPadding(null, false) } : null;
}

/** The main map's camera, for the map controls (zoom in / out, the whole state). */
export const camera = { zoomIn() {}, zoomOut() {}, home() {}, north() {} };

/**
 * The padding a view is fitted with. The whole state fits the free area between the panels.
 * A picked county (`centred`) sits at the true middle of the screen: the padding is the same
 * on both sides, so the county stays in the middle when a panel folds, opens or is resized.
 * At least 120 px stay free each way.
 */
function fitPadding(container, compact, centred = false) {
  if (compact) return 12;
  const w = container?.clientWidth || window.innerWidth;
  const h = container?.clientHeight || window.innerHeight;
  const ins = mapInsets.peek() || (window.innerWidth >= 768
    ? { top: 72, bottom: 72, left: 380, right: 440 }
    : { top: 64, bottom: Math.round(h * 0.45), left: 0, right: 0 });
  let p = { top: ins.top + 16, bottom: ins.bottom + 16, left: ins.left + 16, right: ins.right + 16 };
  if (centred && window.innerWidth >= 768) {
    const x = Math.max(p.left, p.right);
    const y = Math.max(p.top, p.bottom);
    p = { left: x, right: x, top: y, bottom: y };
  }
  const squeeze = (a, b, size) => {
    const over = p[a] + p[b] - (size - 120);
    if (over <= 0) return;
    const k = Math.max(0, (size - 120) / (p[a] + p[b]));
    p[a] = Math.floor(p[a] * k);
    p[b] = Math.floor(p[b] * k);
  };
  squeeze("left", "right", w);
  squeeze("top", "bottom", h);
  return p;
}

/**
 * A still drawing of the counties (Web Mercator, as the map draws them), shown while the map
 * library loads and faded out once the map has drawn. It is only a placeholder: no numbers,
 * hidden from screen readers.
 */
function silhouette(outlines) {
  const merc = ([x, y]) => [x, -Math.log(Math.tan(Math.PI / 4 + (y * Math.PI) / 360)) * (180 / Math.PI)];
  const b = bounds(outlines);
  if (!b) return null;
  const [x0, y1] = merc(b[0]);
  const [x1, y0] = merc(b[1]);
  const w = x1 - x0 || 1;
  const h = y1 - y0 || 1;
  const s = 1000 / w;
  const pt = (c) => {
    const [x, y] = merc(c);
    return `${((x - x0) * s).toFixed(1)},${((y - y0) * s).toFixed(1)}`;
  };
  const ring = (r) => `M${r.map(pt).join("L")}Z`;
  const paths = outlines.features
    .filter((f) => f.geometry)
    .map((f) => {
      const g = f.geometry;
      const d = g.type === "Polygon" ? g.coordinates.map(ring).join("") : g.coordinates.flatMap((p) => p.map(ring)).join("");
      return { id: String(f.properties?.geoid), d };
    });
  return { paths, vb: `0 0 1000 ${(h * s).toFixed(1)}` };
}

function MapSilhouette({ shape, classes, palette, pad, gone }) {
  if (!shape) return null;
  const p = typeof pad === "number" ? { top: pad, right: pad, bottom: pad, left: pad } : pad;
  return html`<svg class=${`map-silhouette ${gone ? "is-gone" : ""}`} viewBox=${shape.vb} aria-hidden="true"
      preserveAspectRatio="xMidYMid meet" style=${`inset: ${p.top}px ${p.right}px ${p.bottom}px ${p.left}px`}>
    ${shape.paths.map((x) => html`<path d=${x.d} fill=${`var(${palette[classes[x.id]] || "--no-data"})`} />`)}
  </svg>`;
}

/** Warm the map library while the browser is idle, so the first map opens quickly. */
let warmed = false;
function warmMapLibrary() {
  if (warmed || typeof window === "undefined") return;
  warmed = true;
  const go = () => {
    import("maplibre-gl").catch(() => {});
    try {
      const link = document.createElement("link");
      link.rel = "prefetch";
      link.href = workerUrl;
      document.head.appendChild(link);
    } catch {
      // No prefetch: the worker loads when the map starts.
    }
  };
  if (window.requestIdleCallback) window.requestIdleCallback(go, { timeout: 2500 });
  else setTimeout(go, 1200);
}
warmMapLibrary();

/**
 * Optional props (the State screen uses none of them): `palette` maps class ids to colour
 * tokens (default CLASS_COLOURS); `compact` fits the view with small margins, for a map that
 * sits inside a card (the Method screen's back-test error map); `label` replaces the region's
 * accessible name.
 */
export function MapView({ stateName, outlines, classes = {}, selected = "", onSelect, palette = CLASS_COLOURS, compact = false, label = "", focusPoint = null }) {
  const box = useRef(null);
  const mapRef = useRef(null);
  const pick = useRef(onSelect);
  pick.current = onSelect;
  const [status, setStatus] = useState("loading");
  // 0 until the map has drawn, 1 while the still drawing fades out, 2 once it is gone.
  const [drawn, setDrawn] = useState(0);
  const sil = useRef({ of: null, shape: null });
  if (sil.current.of !== outlines) sil.current = { of: outlines, shape: outlines?.features?.length ? silhouette(outlines) : null };
  const hasShapes = Boolean(outlines?.features?.length);
  const wanted = basemapWanted.value;
  const baseState = basemapState.value;
  const focus = compact ? "" : focusClass.value;
  // The latest values, read when the map starts.
  const now = useRef({});
  now.current = { classes, selected, palette, focus, outlines };
  const failures = useRef(0);
  // Pointer on the map (true) or a row in a list (false) is what the reader points with.
  const fromMap = useRef(false);
  const boxes = useRef({});
  // Each county's class last drawn, to know which ones change (the fill wave).
  const lastCls = useRef(null);
  const wave = useRef(0);
  // The padding the whole state was fitted with, reused when the view eases back to it.
  const fitPad = useRef(12);
  // The basemap layers once added, and whether they are showing.
  const base = useRef({ ids: null, on: false });
  // True once the reader pans or zooms by hand (the view is then theirs until the next pick).
  const moved = useRef(false);
  const focusRef = useRef(focusPoint);
  focusRef.current = focusPoint;
  const sel = useRef(selected);
  sel.current = selected;
  /** Frame the picked county (at the true middle of the screen) or the whole state. */
  const frame = (map, duration) => {
    const picked = sel.current;
    const target = picked ? boxes.current[picked] : bounds(now.current.outlines || { features: [] });
    if (!target) return;
    const opts = { padding: fitPadding(box.current, compact, Boolean(picked)), duration: reducedMotion() ? 0 : duration, essential: true, pitch: map.getPitch(), bearing: map.getBearing() };
    try {
      map.fitBounds(target, picked ? { ...opts, maxZoom: 9.2 } : opts);
      moved.current = false;
    } catch {
      // A view the map cannot fit (an empty county): stay where it is.
    }
  };

  useEffect(() => {
    let map = null;
    let gone = false;
    import("maplibre-gl")
      .then(({ Map, setWorkerUrl }) => {
        if (gone || !box.current) return;
        setWorkerUrl(workerUrl);
        try {
          const c = now.current;
          map = new Map({
            container: box.current,
            style: buildStyle({ type: "FeatureCollection", features: [] }, c.selected, c.palette, c.focus),
            center: [-77.6, 40.9],
            zoom: 6,
            attributionControl: false,
            // Rotating and tilting are for the 3D view only (ui/columns.js turns them on).
            dragRotate: false,
            maxPitch: 60,
          });
          // The canvas is the map's keyboard stop: say what the keys do there.
          map.getCanvas().setAttribute("aria-label", compact
            ? "County map."
            : "County map. Left and Right step through the counties by rank; Enter opens the county named; Up and Down move the map; plus and minus zoom.");
          map.touchPitch.disable();
          map.keyboard.disableRotation();
          map.touchZoomRotate.disableRotation();
          map.on("error", (e) => {
            const fromBase = base.current.ids && (e?.sourceId ? e.sourceId !== "counties" : true);
            if (fromBase) {
              // A basemap tile, sprite or font failed: after a few, go back to the plain look.
              failures.current += 1;
              if (failures.current >= TILE_FAILURES_BEFORE_FALLBACK && basemapState.value !== "failed") {
                basemapState.value = "failed";
              }
              return;
            }
            if (!mapRef.current) setStatus("nogl");
          });
          map.on("click", "fill", (e) => {
            // In 3D the column layer answers the pointer (ui/columns.js); with the district
            // layer on, the districts do (ui/districts-layer.js).
            if (!compact && (view3d.peek() || districtView.peek().on)) return;
            const id = e.features?.[0]?.properties?.geoid;
            if (!id) return;
            if (!compact) ripple(box.current, e.point);
            pick.current?.(String(id));
          });
          map.on("mousemove", "fill", (e) => {
            if (!compact && (view3d.peek() || districtView.peek().on)) return;
            map.getCanvas().style.cursor = "pointer";
            const id = String(e.features?.[0]?.properties?.geoid || "");
            fromMap.current = true;
            if (!compact) hoverPoint.value = { x: e.point.x, y: e.point.y, from: "map" };
            if (id !== hoveredCounty.peek()) hoveredCounty.value = id;
          });
          map.on("mouseleave", "fill", () => {
            if (!compact && (view3d.peek() || districtView.peek().on)) return;
            map.getCanvas().style.cursor = "";
            fromMap.current = false;
            if (!compact) hoverPoint.value = null;
            hoveredCounty.value = "";
          });
          // A pan or zoom by the reader (not by the code) makes the view theirs.
          map.on("movestart", (e) => {
            if (e?.originalEvent) moved.current = true;
          });
          // Where the view rests (the middle of the screen), and where each county's middle
          // is drawn in map pixels (the point the hover card sits by), for the browser tests.
          map.on("moveend", () => {
            const c = map.getCenter();
            if (!box.current) return;
            box.current.dataset.center = `${c.lng.toFixed(5)},${c.lat.toFixed(5)}`;
            const pts = {};
            for (const [id, b] of Object.entries(boxes.current)) {
              const p = map.project([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2]);
              pts[id] = [Math.round(p.x), Math.round(p.y)];
            }
            box.current.dataset.countyPoints = JSON.stringify(pts);
          });
          if (!compact) {
            camera.zoomIn = () => map.zoomIn({ duration: reducedMotion() ? 0 : 300 });
            camera.zoomOut = () => map.zoomOut({ duration: reducedMotion() ? 0 : 300 });
            camera.home = () => frame(map, 800);
            camera.north = () => map.easeTo({ bearing: 0, duration: reducedMotion() ? 0 : 500 });
          }
          map.on("load", () => {
            mapRef.current = map;
            setStatus("ready");
            if (!compact) mainMap.value = map;
            let done = false;
            const show = () => {
              if (done || gone) return;
              done = true;
              setDrawn(1);
              setTimeout(() => !gone && setDrawn(2), 420);
            };
            map.once("idle", show);
            setTimeout(show, 1500);
          });
        } catch {
          setStatus("nogl");
        }
      })
      .catch(() => setStatus("missing"));
    return () => {
      gone = true;
      mapRef.current = null;
      if (!compact && mainMap.peek() === map) mainMap.value = null;
      if (map) map.remove();
    };
  }, []);

  // Basemap on or off: add its layers once, then only show or hide them.
  useEffect(() => {
    const map = mapRef.current;
    if (status !== "ready" || !map) return undefined;
    const b = base.current;
    const set = (on) => {
      b.on = on;
      try {
        if (b.ids) showBase(map, b.ids, on);
      } catch {
        // The map is going away.
      }
    };
    if (!wanted || baseState === "failed") {
      if (b.on) set(false);
      if (!wanted) basemapState.value = "off";
      return undefined;
    }
    if (b.ids) {
      failures.current = 0;
      set(true);
      basemapState.value = "on";
      return undefined;
    }
    let live = true;
    basemapState.value = "loading";
    failures.current = 0;
    loadBase(darkTheme())
      .then((style) => {
        if (!live || mapRef.current !== map) return;
        try {
          b.ids = addBase(map, style);
        } catch {
          basemapState.value = "failed";
          return;
        }
        set(true);
        basemapState.value = "on";
      })
      .catch(() => {
        if (live) basemapState.value = "failed";
      });
    return () => {
      live = false;
    };
  }, [status, wanted, baseState === "failed"]);

  // New outlines: set the shapes (once per state) and fit the view to them.
  useEffect(() => {
    const map = mapRef.current;
    if (status !== "ready" || !map) return;
    map.getSource("counties")?.setData(joined(outlines, now.current.classes));
    lastCls.current = null;
    if (!hasShapes) return;
    const b = bounds(outlines);
    const padding = fitPadding(box.current, compact);
    boxes.current = boxesOf(outlines);
    fitPad.current = padding;
    const picked = now.current.selected && boxesOf(outlines)[now.current.selected];
    try {
      if (picked) map.fitBounds(picked, { padding: fitPadding(box.current, compact, true), maxZoom: 9.2, animate: false });
      else if (b) map.fitBounds(b, { padding, animate: false });
    } catch {
      // A view the map cannot fit: stay where it is.
    }
    moved.current = false;
  }, [status, outlines]);

  // New classes, a picked county or a pointed-at county: update the map.
  const safely = (fn) => {
    const map = mapRef.current;
    if (status !== "ready" || !map) return;
    try {
      fn(map);
    } catch {
      // The map is going away.
    }
  };
  // The classes as one string, so a new object with the same content changes nothing.
  const clsKey = Object.entries(classes).sort().join(";");
  useEffect(
    () =>
      safely((map) => {
        // Only feature state changes: the shapes stay tiled, so the wave starts at once.
        const next = {};
        for (const f of outlines?.features || []) {
          const id = String(f.properties?.geoid);
          next[id] = classes[id] || "nodata";
        }
        const before = lastCls.current;
        lastCls.current = next;
        for (const [id, cls] of Object.entries(next)) {
          if (!before || before[id] !== cls) map.setFeatureState({ source: "counties", id }, { cls });
        }
        if (!before || compact || reducedMotion()) return;
        fillWave(map, Object.keys(next).filter((id) => before[id] !== next[id]), boxes.current, wave);
      }),
    [status, outlines, clsKey],
  );
  useEffect(
    () =>
      safely((map) => {
        const one = ["==", ["get", "geoid"], selected || ""];
        map.setFilter("picked", one);
        map.setFilter("picked-glow", one);
        map.setFilter("dim", ["!=", ["get", "geoid"], selected || "__none__"]);
        map.setPaintProperty("dim", "fill-opacity", selected ? 0.16 : 0);
      }),
    [status, selected],
  );
  // The camera follows the open county, and goes back to the whole state when it closes.
  const lastPicked = useRef(selected);
  useEffect(() => {
    const map = mapRef.current;
    const was = lastPicked.current;
    lastPicked.current = selected;
    if (compact || status !== "ready" || !map || was === selected) return;
    if (!selected && focusPoint) return;
    frame(map, 900);
  }, [status, selected]);
  // A tract (a point) to look at: it eases to the middle of the screen.
  const pointKey = focusPoint ? focusPoint.join(",") : "";
  useEffect(() => {
    const map = mapRef.current;
    if (compact || status !== "ready" || !map || !focusPoint) return;
    try {
      map.easeTo({
        center: focusPoint,
        zoom: Math.max(map.getZoom(), 10.5),
        padding: fitPadding(box.current, compact, true),
        duration: reducedMotion() ? 0 : 900,
        essential: true,
      });
      moved.current = false;
    } catch {
      // The map is going away.
    }
  }, [status, pointKey]);
  // The panels moved (folded, opened, resized, a new screen): fit the view again, unless the
  // reader has moved the map by hand since the last fit.
  useEffect(() => {
    if (compact || status !== "ready") return undefined;
    let t = 0;
    const stop = effect(() => {
      void mapInsets.value;
      clearTimeout(t);
      t = setTimeout(() => {
        const map = mapRef.current;
        if (!map || moved.current) return;
        if (focusRef.current) {
          try {
            map.easeTo({ center: focusRef.current, padding: fitPadding(box.current, compact, true), duration: reducedMotion() ? 0 : 600 });
          } catch {
            // The map is going away.
          }
        } else frame(map, 600);
      }, 140);
    });
    return () => {
      stop();
      clearTimeout(t);
    };
  }, [status]);
  // The county pointed at: hover feature state, set from the signal outside rendering, so
  // pointing never re-renders this view (or the lists).
  useEffect(() => {
    if (status !== "ready") return undefined;
    let was = "";
    const stop = effect(() => {
      const id = hoveredCounty.value;
      const map = mapRef.current;
      if (!map) return;
      try {
        if (was && was !== id) map.setFeatureState({ source: "counties", id: was }, { hover: false });
        if (id) map.setFeatureState({ source: "counties", id }, { hover: true });
      } catch {
        // The map is going away.
      }
      was = id;
      // A row pointed at in a list: the hover card sits on the county's middle.
      if (compact || fromMap.current) return;
      const b = id && boxes.current[id];
      if (!b) {
        hoverPoint.value = null;
        return;
      }
      const p = map.project([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2]);
      hoverPoint.value = { x: p.x, y: p.y, from: "list" };
    });
    return stop;
  }, [status]);
  useEffect(
    () =>
      safely((map) => {
        map.setPaintProperty("class-fade", "fill-color", fadeColour(focus));
        map.setPaintProperty("class-fade", "fill-opacity", focus ? 0.72 : 0);
      }),
    [status, focus],
  );
  useEffect(() => () => cancelAnimationFrame(wave.current), []);

  const shown = status === "ready" && !hasShapes ? "noshapes" : status;
  const message = shown === "ready" || (shown === "loading" && hasShapes) ? "" : MESSAGES[shown];
  return html`<div class=${compact ? "map-area is-compact" : "map-area"} data-map-status=${shown} data-basemap=${baseState}
      data-drawn=${drawn ? "true" : "false"}>
    ${hasShapes && drawn < 2 && (shown === "loading" || shown === "ready") &&
    html`<${MapSilhouette} shape=${sil.current.shape} classes=${classes} palette=${palette}
      pad=${fitPadding(box.current, compact)} gone=${drawn === 1} />`}
    <div class="map-canvas" ref=${box} role="region"
      aria-label=${label || `County map of ${stateName}. The county table has the same values.`}></div>
    ${message &&
    html`<div class="map-empty" role="note"><p class="map-empty-title">${message}</p></div>`}
    ${baseState === "on" && html`<${BasemapCredit} />`}
  </div>`;
}

/** The basemap credit the tile service and OpenStreetMap ask for. */
export function BasemapCredit({ inline = false }) {
  const link = (href, text) => html`<a href=${href} rel="noopener noreferrer" target="_blank">${text}</a>`;
  return html`<p class=${inline ? "basemap-credit is-inline" : "basemap-credit"}>
    ${"Basemap "}${link("https://openfreemap.org", "OpenFreeMap")}${" "}${link("https://www.openmaptiles.org/", "© OpenMapTiles")}${" Data from "}${link("https://www.openstreetmap.org/copyright", "OpenStreetMap")}
  </p>`;
}

/** The "Basemap" on/off button, with a word when the basemap could not load. */
export function BasemapToggle() {
  const on = basemapWanted.value;
  const st = basemapState.value;
  return html`<button type="button" class="btn btn-quiet btn-xs basemap-btn" aria-pressed=${on && st !== "failed" ? "true" : "false"}
      title=${st === "failed" ? "The basemap did not load; the map shows the plain look." : "Show or hide the street and place background"}
      onClick=${() => {
        // Unavailable: the press switches it off (the plain map); a second press tries again.
        if (st === "failed") {
          basemapState.value = "off";
          setBasemap(false);
        } else setBasemap(!on);
      }}>
    <${Icon} name="layers" size=${14} /><span>${st === "failed" ? "Basemap unavailable" : on ? "Basemap on" : "Basemap off"}</span>
  </button>`;
}

/**
 * The map's legend, in the bottom-right corner of the map.
 *
 * With a reform on it shows the three classes with the engine's labels; each class is a
 * button that fades the other classes on the map (press again to show all). With no reform
 * on there is nothing to shade, so it says so. `classes` and `palette` can be given for
 * another map (the back-test error map on Method & data); then the classes are not buttons.
 * `onTable` adds the "County table" button (the map's twin as a table).
 */
export function MapLegend({ map, onTable = null, tableOpen = false, title = "", classes = null, palette = CLASS_COLOURS, grey = null, measure = "change", onMeasure = null, top = null, body = null, children = null }) {
  const options = !classes && onMeasure ? measuresOf(map) : [];
  const m = options.some(([id]) => id === measure) ? measure : "change";
  const spec = m !== "change" ? (map?.measures || []).find((x) => x.id === m) : null;
  if (spec) {
    classes = spec.classes;
    palette = MEASURE_COLOURS;
    title = spec.title;
  }
  const custom = Boolean(classes);
  const any = custom || map?.reforms?.length > 0;
  const focus = focusClass.value;
  const ref = useRef(null);
  useHeightMorph(ref, `${any}|${title}|${m}|${body ? "own" : ""}`);
  const list = custom ? classes : map?.classes || [];
  const pick = spec || !custom;
  return html`<section class=${`map-key map-legend card ${any ? "is-shaded" : "is-prompt"}`} aria-labelledby="map-key-title"
      ref=${ref} data-zone="legend">
    ${top}
    ${options.length > 1 && !body &&
    html`<label class="key-measure-select">
      <span class="visually-hidden">What the map shades</span>
      <select class="select select-xs" value=${m} onChange=${(e) => {
        focusClass.value = "";
        onMeasure(e.currentTarget.value);
      }}>
        ${options.map(([id, label]) => html`<option value=${id} selected=${m === id}>${label}</option>`)}
      </select>
    </label>`}
    ${options.length > 1 && !body &&
    html`<div class="key-measures" role="group" aria-label="What the map shades">
      ${options.map(
        ([id, label]) => html`<button type="button" class="seg seg-xs" aria-pressed=${m === id ? "true" : "false"}
          onClick=${() => {
            focusClass.value = "";
            onMeasure(id);
          }}>${label}</button>`,
      )}
    </div>`}
    ${body
      ? html`<div class="key-body" key="own">${body}</div>`
      : html`<div class="key-body" key=${any ? `${title || "shaded"}|${m}` : "prompt"}>
    ${any
      ? html`<h2 class="key-title" id="map-key-title">${title || "Added homes as a share of each county's baseline (middle estimate)"}</h2>
        <ol class=${`key-ramp key-ramp-${list.length}`} style=${`--cols: ${list.filter((c) => c.id !== "no_base").length || 1}`}
          aria-label=${!pick ? "Map key" : "Classes: press one to show only those counties"}>
          ${list.map(
            (c, i) => html`<li class="key-step" data-class=${c.id} style=${`--i: ${i}`}>
              ${!pick
                ? html`<span class="key-chip is-static"><span class="key-swatch" style=${`background: var(${palette[c.id]})`}></span>
                    <span class="key-label">${c.word ? html`<strong>${c.word}</strong> ` : null}<span class="key-range">${c.label}</span></span></span>`
                : html`<button type="button" class="key-chip" aria-pressed=${focus === c.id ? "true" : "false"}
                    onClick=${() => (focusClass.value = focus === c.id ? "" : c.id)}>
                    <span class="key-swatch" style=${`background: var(${palette[c.id]})`}></span>
                    <span class="key-label">${c.word ? html`<strong>${c.word}</strong> ` : null}<span class="key-range">${c.label}</span></span>
                  </button>`}
            </li>`,
          )}
        </ol>`
      : html`<h2 class="key-title" id="map-key-title">Map key</h2>
        <p class="key-hint">Switch a reform on to shade the counties by the homes it adds.</p>`}
    </div>`}
    ${children}
    ${m === "coverage" && spec?.rule && html`<details class="about key-about">
      <summary class="about-sum"><span class="about-i" aria-hidden="true">i</span>How coverage is counted</summary>
      <div class="about-body">${spec.rule}</div>
    </details>`}
    <div class="key-foot">
      <ul class="key-grey">
        ${(grey || [["is-nodata", "No data"]]).map(([cls, word]) => html`<li><span class=${`key-swatch ${cls}`}></span>${word}</li>`)}
        ${!any && html`<li><span class="key-swatch is-notmodelled"></span>No reform on</li>`}

      </ul>
      ${onTable &&
      html`<button type="button" class="btn btn-quiet btn-xs" aria-pressed=${tableOpen ? "true" : "false"}
          onClick=${onTable}>${tableOpen ? "Hide table" : "County table"}</button>`}
    </div>
    ${basemapState.value === "on" && html`<${BasemapCredit} inline=${true} />`}
  </section>`;
}
