/**
 * The 3D view: a column on every county (and, closer in, on every census tract) whose height
 * is the homes the scenario adds there.
 *
 * What is drawn
 *   - Each column stands on a square footprint around a point inside its place: a county's
 *     middle (8 km wide; 6 km in Vermont, whose counties are small) or a tract's interior
 *     point from GET /states/ST/tract-points. A tract's square is 0.86 x the distance to the
 *     nearest other tract point, kept between 300 m and 3 km, worked out once per state, so
 *     neighbouring squares never overlap and the dense city tracts stay thin.
 *   - The solid core runs from the ground to the middle estimate (P50); a glass cap at a
 *     quarter opacity runs from P50 to the likely high value (P90); pointing at a column adds a
 *     thin dark notch at the likely low value (P10). "Show range" hides the caps.
 *   - County columns show below zoom 7.6 and tract columns from 8.2; in between the county
 *     columns sink while the tract columns rise. With no reform on, the county columns show the
 *     baseline instead (the baseline has no tract split, so there are no tract columns).
 *   - Colours are the class tokens the 2D map uses (lighter is less); "no data" and "not
 *     modelled" places stand as flat grey squares. The county map underneath turns into a
 *     neutral floor with its county lines.
 *
 * Height
 *   Height is linear in homes. A column's height in metres is homes x k x (metres per pixel at
 *   the current zoom), so it keeps the same size on screen as the reader zooms. `k` (pixels
 *   per home) is fitted once per level and scenario: the 99th-percentile P90 stands about 32%
 *   of the map's height at a 50 degree tilt; the tallest column is kept between 25% and 80% of
 *   it. A new scale eases in over 400 ms.
 *
 * Motion (all skipped when the reader asks for less motion)
 *   Every animation runs through MapLibre global state (one number per frame), never by
 *   replacing the data per frame: `vh_t` runs from 0 to 1 and each column reads its own delay
 *   from its properties (west to east for a new scenario, north to south for the first 3D
 *   view), so all columns move with one value. A scenario change sets the data once, with each
 *   column's previous and next P10 / P50 / P90; colours switch at the start. Pointing at a
 *   column sets `vh_hover` (the column turns accent) and dims the others to 0.55 over 250 ms.
 *
 * Dial levels on the map
 *   When the reader moves a policy dial (ui/dials.js sends `vh:dial-level` with that level's
 *   county table), `dialView` holds the level and the map shows it instead of the scenario:
 *   the county columns grow or sink to the level's P50 (glass cap to P90) with the same `vh_t`
 *   motion, on one height scale for every level of that dial (fitted on each county's highest
 *   P90 over the levels), and the tract columns step aside (the dial is county-level). In 2D
 *   the county fills shade by the level's P50 on one light-to-dark ramp for the whole dial;
 *   each county's previous and next value is set once as feature state and `vh_dt` (global
 *   state) runs 0 to 1, so no data is set per frame. A chip on the map says what is shown,
 *   with "Back to the scenario"; leaving the Reform screen, a new scenario, the year slider or
 *   the district view also go back. Reduced motion: every change is instant.
 *
 * No number is made here: heights and texts are the API's values (county records with the
 * engine's text; tract values as the API rounded them; dial county values as the engine sent
 * them, written with `plain`). The browser only places them.
 */
import { effect, signal } from "@preact/signals";
import { reformById } from "../content/reforms.js";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { plain } from "../lib/format.js";
import { RangeBar } from "./bits.js";
import { mapInsets } from "./map.js";
import { bearing, columnPoint, dialView, districtView, hoveredTract, mainMap, mapZoom, showRange, view3d, yearView } from "./map-state.js";
import { reducedMotion } from "./motion.js";
import { hoveredCounty } from "./panel.js";

const DEG = Math.PI / 180;
/** Metres per screen pixel at zoom 0 on the equator (MapLibre's 512-pixel tiles). */
const MPP0 = 78271.517;
const SIN50 = Math.sin(50 * DEG);
const Z_COUNTY_OUT = 7.6;
const Z_TRACT_IN = 8.2;
const MAX_ZOOM_3D = 12.5;
const FOV_3D = 30;
const FOV_2D = 36.87;

/** Colour tokens of the column classes (counties: the map's classes; tracts: their five). */
export const COLUMN_COLOURS = {
  little: "--seq-2",
  some: "--seq-4",
  a_lot: "--seq-6",
  no_base: "--seq-1",
  few: "--seq-2",
  many: "--seq-5",
  most: "--seq-6",
  base: "--seq-3",
  q1: "--seq-2",
  q2: "--seq-3",
  q3: "--seq-5",
  q4: "--seq-6",
  cov_good: "--seq-5",
  cov_partial: "--seq-3",
  cov_thin: "--seq-1",
  dial: "--seq-5",
  none: "--not-modelled",
  nodata: "--no-data",
};

const gs = (k) => ["global-state", k];

/** The scales (pixels per home) the columns stand at, for the legend patch: {county, tract, height}. */
/** The zoom from which the legend describes the tract columns (half way through the swap). */
export const TRACT_LEGEND_ZOOM = (Z_COUNTY_OUT + Z_TRACT_IN) / 2;

export const columnScale = signal({ county: 0, tract: 0, height: 0 });

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#cccccc";
}

/* ------------------------------------------------------------------ geometry */

/** A square ring of side `side` metres around [lon, lat]. */
function square(lon, lat, side) {
  const h = side / 2;
  const dy = h / 110540;
  const dx = h / (111320 * Math.cos(lat * DEG));
  return [[[lon - dx, lat - dy], [lon + dx, lat - dy], [lon + dx, lat + dy], [lon - dx, lat + dy], [lon - dx, lat - dy]]];
}

/**
 * Each tract's square side in metres: 0.86 x the distance to its nearest neighbour, kept
 * between 300 m and 3 km. A grid of 5 km cells keeps the search near each point.
 */
export function tractSides(lon, lat) {
  const n = lon.length;
  if (!n) return [];
  const lat0 = lat.reduce((a, b) => a + b, 0) / n;
  const kx = 111320 * Math.cos(lat0 * DEG);
  const ky = 110540;
  const x = lon.map((v) => v * kx);
  const y = lat.map((v) => v * ky);
  const CELL = 5000;
  const grid = new Map();
  const key = (i, j) => `${i},${j}`;
  for (let p = 0; p < n; p += 1) {
    const k = key(Math.floor(x[p] / CELL), Math.floor(y[p] / CELL));
    if (!grid.has(k)) grid.set(k, []);
    grid.get(k).push(p);
  }
  const out = new Array(n);
  for (let p = 0; p < n; p += 1) {
    const ci = Math.floor(x[p] / CELL);
    const cj = Math.floor(y[p] / CELL);
    let best = Infinity;
    for (let r = 1; r <= 4 && best > (r - 1) * CELL; r += 1) {
      for (let i = ci - r; i <= ci + r; i += 1) {
        for (let j = cj - r; j <= cj + r; j += 1) {
          for (const q of grid.get(key(i, j)) || []) {
            if (q === p) continue;
            const d = Math.hypot(x[q] - x[p], y[q] - y[p]);
            if (d < best) best = d;
          }
        }
      }
    }
    out[p] = Math.min(3000, Math.max(300, 0.86 * (Number.isFinite(best) ? best : 3000)));
  }
  return out;
}

/** The area-weighted middle of a county's largest polygon part ([lon, lat]), or null. */
function middleOf(geometry) {
  if (!geometry) return null;
  const parts = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
  let best = null;
  let bestA = 0;
  for (const poly of parts) {
    const ring = poly[0] || [];
    let a = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
      a += f;
      cx += (ring[j][0] + ring[i][0]) * f;
      cy += (ring[j][1] + ring[i][1]) * f;
    }
    if (Math.abs(a) > bestA && a !== 0) {
      bestA = Math.abs(a);
      best = [cx / (3 * a), cy / (3 * a)];
    }
  }
  return best;
}

/** 0-1 positions west to east (`dx`) and north to south (`dn`) of a list of [lon, lat]. */
function spreads(pts) {
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const xr = Math.max(...xs) - x0 || 1;
  const y1 = Math.max(...ys);
  const yr = y1 - Math.min(...ys) || 1;
  return pts.map((p) => ({ dx: (p[0] - x0) / xr, dn: (y1 - p[1]) / yr }));
}

/**
 * The fixed part of each level's features (footprints and places), built once per state:
 * `county` from the outlines, `tract` from the tract points.
 */
export function footprints(state, outlines, points) {
  const out = { county: [], tract: [] };
  const cside = state === "VT" ? 6000 : 8000;
  const cf = (outlines?.features || [])
    .map((f) => ({ geoid: String(f.properties?.geoid), at: middleOf(f.geometry) }))
    .filter((f) => f.at);
  const cs = spreads(cf.map((f) => f.at));
  out.county = cf.map((f, i) => ({ geoid: f.geoid, county: f.geoid, at: f.at, side: cside, ...cs[i] }));
  if (points?.geoids?.length) {
    const lon = points.lon || [];
    const lat = points.lat || [];
    const sides = tractSides(lon, lat);
    const ts = spreads(points.geoids.map((_, i) => [lon[i], lat[i]]));
    out.tract = points.geoids.map((g, i) => ({
      geoid: String(g),
      county: String(points.counties?.[points.county_index?.[i]] ?? String(g).slice(0, 5)),
      at: [lon[i], lat[i]],
      side: sides[i],
      ...ts[i],
    }));
  }
  return out;
}

/* ------------------------------------------------------------------ values */

/**
 * The values a level stands on: geoid -> {cls, v10, v50, v90, rec} where `rec` is what the
 * hover card shows. Counties use the map reply (homes added, or the baseline with no reform
 * on); tracts use the tracts reply (the same `geoids` order as the points).
 */
export function levelValues(mapReply, classes, tracts) {
  const county = {};
  const any = (mapReply?.reforms?.length || 0) > 0;
  for (const c of mapReply?.counties || []) {
    const rec = any ? c.added : c.baseline;
    const ok = rec && Number.isFinite(rec.p50);
    county[c.fips] = {
      cls: ok ? (any ? classes[c.fips] || c.class || "nodata" : "base") : "nodata",
      v10: ok ? Math.max(0, rec.p10) : 0,
      v50: ok ? Math.max(0, rec.p50) : 0,
      v90: ok ? Math.max(0, rec.p90) : 0,
      rec: ok ? rec : null,
      name: c.name,
    };
  }
  const tract = {};
  const a = tracts?.added;
  if (a && tracts.geoids) {
    const ids = (tracts.classes || []).map((c) => c.id);
    tracts.geoids.forEach((g, i) => {
      const p50 = a.p50?.[i];
      const ok = Number.isFinite(p50);
      const cls = ok ? ids[tracts.class?.[i]] || "none" : "nodata";
      tract[g] = {
        cls,
        v10: ok ? Math.max(0, a.p10[i]) : 0,
        v50: ok ? Math.max(0, p50) : 0,
        v90: ok ? Math.max(0, a.p90[i]) : 0,
        rec: ok ? { p10: a.p10[i], p50, p90: a.p90[i] } : null,
      };
    });
  }
  return { county, tract, any };
}

/** The event a policy dial sends on `window` (ui/dials.js `DIAL_EVENT`). */
const DIAL_EVENT = "vh:dial-level";

/** A county table ({columns, rows}) as fips -> {p10, p50, p90} (the engine's values). */
function tableOf(t) {
  const cols = t?.columns || [];
  const at = (k) => cols.indexOf(k);
  const [ic, i10, i50, i90] = [at("county"), at("p10"), at("p50"), at("p90")];
  const out = {};
  if (ic < 0 || i50 < 0) return out;
  for (const r of t.rows || []) out[String(r[ic])] = { p10: r[i10], p50: r[i50], p90: r[i90] };
  return out;
}

/**
 * The map's view of one dial level (the `vh:dial-level` detail), or null: the level's county
 * values, each county's highest P90 over the dial's levels (one height scale for the dial) and
 * the highest county P50 over the levels (one 2D colour scale). Only the server's values are
 * compared; none is changed.
 */
export function dialOf(d) {
  const counties = tableOf(d?.counties);
  if (!Object.keys(counties).length) return null;
  const top = {};
  let scale2d = 0;
  const bench = tableOf((d.levels || []).find((lv) => lv.benchmark)?.counties);
  for (const lv of d.levels?.length ? d.levels : [{ counties: d.counties }]) {
    for (const [id, v] of Object.entries(tableOf(lv.counties))) {
      if (Number.isFinite(v.p90) && v.p90 > (top[id]?.v90 || 0)) top[id] = { v90: v.p90 };
      if (Number.isFinite(v.p50) && v.p50 > scale2d) scale2d = v.p50;
    }
  }
  return {
    state: d.state,
    reform: d.reform,
    reformName: reformById(d.reform)?.name || d.reform,
    dial: d.dial,
    level: d.level,
    label: d.label,
    counties,
    bench,
    top,
    scale2d,
  };
}

/** A dial level's values in the shape of `levelValues` (counties only: the dial is county-level). */
export function dialValues(dv, mapReply) {
  const names = Object.fromEntries((mapReply?.counties || []).map((c) => [c.fips, c.name]));
  const county = {};
  for (const [id, v] of Object.entries(dv.counties)) {
    const ok = Number.isFinite(v.p50);
    county[id] = {
      cls: ok ? "dial" : "nodata",
      v10: ok ? Math.max(0, v.p10) : 0,
      v50: ok ? Math.max(0, v.p50) : 0,
      v90: ok ? Math.max(0, v.p90) : 0,
      rec: ok ? v : null,
      name: names[id] || id,
    };
  }
  return { county, tract: {}, any: true, dial: dv };
}

/** "likely P10 to P90, middle P50" for a dial county (the engine's values, written plainly). */
export function dialText(v) {
  return v && Number.isFinite(v.p50) ? `likely ${plain(v.p10)} to ${plain(v.p90)}, middle ${plain(v.p50)}` : "no value";
}

/** Pixels per home that fit a level (see the module note), or 0 with nothing to show. */
export function fitScale(values, height) {
  const tops = Object.values(values).map((v) => v.v90).filter((v) => v > 0).sort((a, b) => a - b);
  if (!tops.length || !(height > 0)) return 0;
  const p99 = tops[Math.min(tops.length - 1, Math.floor(0.99 * (tops.length - 1)))];
  const max = tops[tops.length - 1];
  let k = (0.32 * height) / (p99 * SIN50);
  if (max * k * SIN50 > 0.8 * height) k = (0.8 * height) / (max * SIN50);
  if (max * k * SIN50 < 0.25 * height) k = (0.25 * height) / (max * SIN50);
  return k;
}

/* ------------------------------------------------------------------ expressions */

/** 0-1 progress of one column: global `vh_t` with the column's own delay, eased out. */
function progress() {
  const delay = ["+", gs("vh_dbase"), ["*", gs("vh_dspan"), ["case", ["==", gs("vh_dk"), "n"], ["get", "dn"], ["get", "dx"]]]];
  const u = ["max", 0, ["min", 1, ["/", ["-", ["*", gs("vh_t"), gs("vh_total")], delay], gs("vh_each")]]];
  return ["-", 1, ["^", ["-", 1, u], 3]];
}

/** The animated value of a field ("10", "50", "90"): previous to next by the progress. */
function valueOf(field) {
  const a = ["*", gs("vh_keep"), ["get", `a${field}`]];
  return ["+", a, ["*", ["-", ["get", `b${field}`], a], progress()]];
}

/** On-screen pixels (before the tilt) of a level's value expression. */
function pixels(level, value) {
  return ["*", value, gs(level === "tract" ? "vh_kt" : "vh_kc"), gs("vh_h3d")];
}

/**
 * Height in metres of a pixel expression: pixels x metres per pixel at the zoom. Exponential
 * interpolation with base 0.5 between stops is exact for a quantity that halves per zoom level.
 * County columns sink between zoom 7.6 and 8.2 where tract columns rise (`withTracts`).
 */
function metres(level, px, lat0, withTracts) {
  const mpp = (z) => (MPP0 * Math.cos(lat0 * DEG)) / 2 ** z;
  const at = (z) => ["*", px, mpp(z)];
  if (level === "tract") return ["interpolate", ["exponential", 0.5], ["zoom"], Z_COUNTY_OUT, 0, Z_TRACT_IN, at(Z_TRACT_IN), 16, at(16)];
  if (!withTracts) return ["interpolate", ["exponential", 0.5], ["zoom"], 0, at(0), 16, at(16)];
  return ["interpolate", ["exponential", 0.5], ["zoom"], 0, at(0), Z_COUNTY_OUT, at(Z_COUNTY_OUT), Z_TRACT_IN, 0];
}

/** A colour token mixed toward the floor colour (`k` = share of the floor), as rgb(). */
function mixed(name, k) {
  const parse = (c) => {
    const x = c.trim();
    if (x.startsWith("#")) {
      const h = x.length === 4 ? x.slice(1).split("").map((d) => d + d).join("") : x.slice(1, 7);
      return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
    }
    const m = x.match(/\d+(\.\d+)?/g);
    return m ? m.slice(0, 3).map(Number) : [200, 200, 200];
  };
  const a = parse(token(name));
  const b = parse(token("--surface-3"));
  const c = a.map((v, i) => Math.round(v * (1 - k) + b[i] * k));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/**
 * A column's colour: its class token; accent for the column pointed at; the others fade
 * toward the floor while `vh_dim` runs (to look 0.55 opaque: MapLibre takes one opacity per
 * extrusion layer, so the fade is a colour mix).
 */
function colourOf(palette) {
  const full = ["match", ["get", "cls"]];
  const dim = ["match", ["get", "cls"]];
  Object.entries(palette).forEach(([k, v]) => {
    full.push(k, token(v));
    dim.push(k, mixed(v, 0.45));
  });
  full.push(token("--no-data"));
  dim.push(mixed("--no-data", 0.45));
  const off = ["all", ["!=", gs("vh_dimof"), ""], ["!=", ["get", "geoid"], gs("vh_dimof")]];
  const amount = ["case", off, gs("vh_dim"), 0];
  return ["case", ["==", ["get", "geoid"], gs("vh_hover")], token("--accent"), ["interpolate", ["linear"], amount, 0, full, 1, dim]];
}

const GLOBALS = {
  vh_t: 1,
  vh_keep: 1,
  vh_dk: "x",
  vh_dbase: 0,
  vh_dspan: 0,
  vh_each: 1,
  vh_total: 1,
  vh_h3d: 0,
  vh_kc: 0,
  vh_kt: 0,
  vh_hover: "",
  vh_dimof: "",
  vh_dim: 0,
  vh_dt: 1,
  vh_dscale: 1,
};

const SOURCES = { county: "col-county", tract: "col-tract" };
const NOTCH = "col-notch";

/** The column layers of a level: core, glass cap, footprint edge. */
function layersOf(level, lat0, withTracts) {
  const src = SOURCES[level];
  const colour = colourOf(COLUMN_COLOURS);
  const v50 = pixels(level, valueOf("50"));
  const v90 = pixels(level, valueOf("90"));
  const zoomed = level === "tract" ? { minzoom: Z_COUNTY_OUT - 0.05 } : withTracts ? { maxzoom: Z_TRACT_IN + 0.05 } : {};
  return [
    {
      id: `${src}-edge`,
      type: "line",
      source: src,
      ...zoomed,
      layout: { visibility: "none" },
      paint: { "line-color": token("--surface"), "line-width": 0.5, "line-opacity": 0.9 },
    },
    {
      id: `${src}-core`,
      type: "fill-extrusion",
      source: src,
      ...zoomed,
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": colour,
        "fill-extrusion-base": 0,
        "fill-extrusion-height": metres(level, v50, lat0, withTracts),
        "fill-extrusion-opacity": 1,
        "fill-extrusion-vertical-gradient": true,
      },
    },
    {
      id: `${src}-cap`,
      type: "fill-extrusion",
      source: src,
      ...zoomed,
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": colour,
        "fill-extrusion-base": metres(level, v50, lat0, withTracts),
        "fill-extrusion-height": metres(level, v90, lat0, withTracts),
        "fill-extrusion-opacity": 0.25,
        "fill-extrusion-vertical-gradient": false,
      },
    },
  ];
}

/** The P10 notch: a thin dark slab on the column pointed at (one feature, its own source). */
function notchLayers(lat0, withTracts) {
  return ["county", "tract"].map((level) => {
    const px = ["*", ["get", "p10"], gs(level === "tract" ? "vh_kt" : "vh_kc"), gs("vh_h3d")];
    return {
      id: `${NOTCH}-${level}`,
      type: "fill-extrusion",
      source: NOTCH,
      filter: ["==", ["get", "lv"], level],
      ...(level === "tract" ? { minzoom: Z_COUNTY_OUT - 0.05 } : withTracts ? { maxzoom: Z_TRACT_IN + 0.05 } : {}),
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": token("--text"),
        "fill-extrusion-base": metres(level, ["max", 0, ["-", px, 1.2]], lat0, withTracts),
        "fill-extrusion-height": metres(level, ["+", px, 1.2], lat0, withTracts),
        "fill-extrusion-opacity": 0.9,
      },
    };
  });
}

const EMPTY = { type: "FeatureCollection", features: [] };

/* ------------------------------------------------------------------ the controller */

/** Ease-out cubic. */
const outCubic = (u) => 1 - (1 - u) ** 3;
let introPlayed = false;

/**
 * Global-state tweens on one animation frame loop. `tween(key, to, ms)` moves one value; a
 * new tween of the same key starts from where the old one was.
 */
function tweener(map) {
  const jobs = new Map();
  const now = { ...GLOBALS };
  let raf = 0;
  const set = (k, v) => {
    if (now[k] === v) return;
    now[k] = v;
    try {
      map.setGlobalStateProperty(k, v);
    } catch {
      // The map is going away.
    }
  };
  const frame = (ts) => {
    raf = 0;
    for (const [k, j] of jobs) {
      const u = Math.min(1, (ts - j.t0) / j.ms);
      set(k, j.from + (j.to - j.from) * (j.ease ? j.ease(u) : u));
      if (u >= 1) {
        jobs.delete(k);
        j.done?.();
      }
    }
    if (jobs.size) raf = requestAnimationFrame(frame);
  };
  return {
    now,
    set(k, v) {
      jobs.delete(k);
      set(k, v);
    },
    tween(k, to, ms, { ease = null, done = null } = {}) {
      if (ms <= 0 || reducedMotion()) {
        jobs.delete(k);
        set(k, to);
        done?.();
        return;
      }
      jobs.set(k, { from: now[k], to, ms, ease, done, t0: performance.now() });
      if (!raf) raf = requestAnimationFrame(frame);
    },
    busy: (k) => jobs.has(k),
    stop() {
      cancelAnimationFrame(raf);
      raf = 0;
      jobs.clear();
    },
  };
}

/** The value a column shows at this moment (to start the next change from there). */
function shownNow(f, g) {
  const total = g.vh_total || 1;
  const d = g.vh_dbase + g.vh_dspan * (g.vh_dk === "n" ? f.dn : f.dx);
  const u = Math.max(0, Math.min(1, (g.vh_t * total - d) / (g.vh_each || 1)));
  const e = outCubic(u);
  const at = (k) => {
    const a = g.vh_keep * (f[`a${k}`] || 0);
    return a + ((f[`b${k}`] || 0) - a) * e;
  };
  return { v10: at("10"), v50: at("50"), v90: at("90") };
}

/** The pitch that follows the zoom: 35 degrees at the state's fit, 50 at zoom 9, 55 from 11. */
function followPitch(z, zFit) {
  if (z <= zFit) return 35;
  if (z <= 9) return 35 + (15 * (z - zFit)) / Math.max(0.5, 9 - zFit);
  if (z <= 11) return 50 + (5 * (z - 9)) / 2;
  return 55;
}

/**
 * The column layers on the main map, the 2D / 3D camera and the column hover card. Rendered
 * by the shell once; it draws nothing in 2D except an empty card slot.
 */
export function MapColumns({ state, outlines, mapReply, classes, tracts, points, onPick, frameState }) {
  const ctl = useRef(null);
  const map = mainMap.value;
  const on = view3d.value;
  const [values, setValues] = useState(null);
  const latest = useRef({});
  latest.current = { mapReply, classes, tracts, points, outlines, state, onPick, frameState };

  // Footprints: once per state (and once the tract points arrive).
  const feet = useRef({ key: "", data: null });
  const fkey = `${state}|${outlines ? outlines.features?.length : 0}|${points?.count || points?.geoids?.length || 0}`;
  if (feet.current.key !== fkey) feet.current = { key: fkey, data: outlines ? footprints(state, outlines, points) : null };

  // Set the map up once it is there: global state, sources, layers, light, events.
  useEffect(() => {
    if (!map) return undefined;
    const lat0 = map.getCenter().lat;
    const tw = tweener(map);
    for (const [k, v] of Object.entries(GLOBALS)) {
      try {
        map.setGlobalStateProperty(k, v);
      } catch {
        // An older map without global state: the 3D view stays off.
      }
    }
    const c = { map, tw, lat0, feats: { county: [], tract: [] }, withTracts: false, layers: [], userTilt: false, zFit: 6.5, entered: false };
    ctl.current = c;
    try {
      map.addSource(SOURCES.county, { type: "geojson", data: EMPTY, promoteId: "geoid" });
      map.addSource(SOURCES.tract, { type: "geojson", data: EMPTY, promoteId: "geoid" });
      map.addSource(NOTCH, { type: "geojson", data: EMPTY });
      map.addLayer(
        { id: "floor3d", type: "fill", source: "counties", paint: { "fill-color": token("--surface-3"), "fill-opacity": 0, "fill-opacity-transition": { duration: reducedMotion() ? 0 : 400, delay: 0 } } },
        "dim",
      );
      placeLayers(c, false);
    } catch {
      ctl.current = null;
      return undefined;
    }
    const box = map.getContainer();
    const note = () => {
      box.dataset.pitch = map.getPitch().toFixed(1);
      box.dataset.bearing = map.getBearing().toFixed(1);
      bearing.value = map.getBearing();
      mapZoom.value = map.getZoom();
    };
    map.on("moveend", note);
    map.on("rotate", () => (bearing.value = map.getBearing()));
    note();
    // A tilt by hand makes the pitch the reader's own (it no longer follows the zoom).
    map.on("pitchstart", (e) => {
      if (e?.originalEvent) c.userTilt = true;
    });
    map.on("zoomend", (e) => {
      if (!view3d.peek() || c.userTilt || !e?.originalEvent) return;
      const p = followPitch(map.getZoom(), c.zFit);
      if (Math.abs(p - map.getPitch()) > 1) map.easeTo({ pitch: p, duration: reducedMotion() ? 0 : 300 });
    });
    const hit = (point) => {
      const ids = ["col-tract-core", "col-tract-cap", "col-county-core", "col-county-cap"].filter((id) => map.getLayer(id));
      const f = map.queryRenderedFeatures(point, { layers: ids })[0];
      if (f) return { level: f.layer.id.startsWith("col-tract") ? "tract" : "county", id: String(f.properties.geoid), county: String(f.properties.county) };
      const g = map.queryRenderedFeatures(point, { layers: ["fill"] })[0];
      return g ? { level: "floor", id: String(g.properties.geoid), county: String(g.properties.geoid) } : null;
    };
    const move = (e) => {
      if (!view3d.peek()) return;
      const h = hit(e.point);
      map.getCanvas().style.cursor = h ? "pointer" : "";
      c.fromMap = true;
      pointAt(c, h && h.level !== "floor" ? h : null);
      const county = h ? h.county : "";
      if (county !== hoveredCounty.peek()) hoveredCounty.value = county;
    };
    const leave = () => {
      if (!view3d.peek()) return;
      pointAt(c, null);
      hoveredCounty.value = "";
    };
    const click = (e) => {
      if (!view3d.peek()) return;
      const h = hit(e.point);
      if (h) latest.current.onPick?.(h.county);
    };
    map.on("mousemove", move);
    map.on("mouseout", leave);
    map.on("click", click);
    // Keep the card on its column while the camera moves.
    const follow = () => place(c);
    map.on("move", follow);
    return () => {
      tw.stop();
      ctl.current = null;
      try {
        map.off("moveend", note);
        map.off("mousemove", move);
        map.off("mouseout", leave);
        map.off("click", click);
        map.off("move", follow);
      } catch {
        // The map is gone.
      }
    };
  }, [map]);

  // A county pointed at in a list (not on the map): its column lights and the card sits on it.
  useEffect(() => {
    if (!map) return undefined;
    return effect(() => {
      const id = hoveredCounty.value;
      const c = ctl.current;
      if (!c || !view3d.peek()) return;
      if (c.fromMap) {
        c.fromMap = false;
        return;
      }
      pointAt(c, id ? { level: "county", id, county: id } : null);
    });
  }, [map]);

  // The values of the current scenario (both levels), computed when the replies change.
  useEffect(() => {
    if (!mapReply) return;
    setValues(levelValues(mapReply, classes || {}, tracts));
  }, [mapReply, tracts, Object.entries(classes || {}).sort().join(";")]);

  // A policy dial moved by the reader: show its level (see "Dial levels on the map").
  useEffect(() => {
    const on = (e) => {
      const d = e.detail || {};
      if (d.off) {
        if (dialView.peek()?.reform === d.reform) dialView.value = null;
        return;
      }
      if (!d.moved || d.state !== latest.current.state || yearView.peek().on || districtView.peek().on) return;
      const next = dialOf(d);
      if (next) dialView.value = next;
    };
    window.addEventListener(DIAL_EVENT, on);
    return () => {
      window.removeEventListener(DIAL_EVENT, on);
      dialView.value = null;
    };
  }, []);
  // A new state or scenario, the year slider or the district view: back to the scenario.
  const firstReply = useRef(true);
  useEffect(() => {
    if (firstReply.current) {
      firstReply.current = false;
      return;
    }
    dialView.value = null;
  }, [state, mapReply]);
  useEffect(
    () =>
      effect(() => {
        if ((yearView.value.on || districtView.value.on) && dialView.peek()) dialView.value = null;
      }),
    [],
  );

  // What the columns stand on: the dial level shown, else the scenario.
  const dv = dialView.value;
  const memo = useRef({ dv: null, reply: null, out: null });
  if (dv && (memo.current.dv !== dv || memo.current.reply !== mapReply)) memo.current = { dv, reply: mapReply, out: dialValues(dv, mapReply) };
  const shown = dv ? memo.current.out : values;

  // New footprints or values: set the data once (previous and next values), then animate.
  useEffect(() => {
    const c = ctl.current;
    if (!c || !shown || !feet.current.data) return;
    c.values = shown;
    c.fitOn = shown.dial ? shown.dial.top : null;
    c.data = feet.current.data;
    if (!view3d.peek()) {
      c.stale = true;
      return;
    }
    stage(c, { animate: true });
  }, [map, shown, fkey]);

  // The county fills in 2D follow the dial level too (one tween of `vh_dt`).
  useEffect(() => {
    const c = ctl.current;
    if (c) dialFill(c, dv);
  }, [map, dv]);

  // Into 3D and back.
  useEffect(() => {
    const c = ctl.current;
    if (!c) return;
    if (on) enter(c, latest.current);
    else if (c.entered) leaveView(c);
  }, [map, on]);

  // The range caps on or off.
  const range = showRange.value;
  useEffect(() => {
    const c = ctl.current;
    if (!c || !on) return;
    for (const src of Object.values(SOURCES)) {
      if (map.getLayer(`${src}-cap`)) map.setLayoutProperty(`${src}-cap`, "visibility", range ? "visible" : "none");
    }
  }, [map, on, range]);

  // Refit the scale when the map's height changes.
  useEffect(() => {
    if (!map) return undefined;
    const fit = () => ctl.current && view3d.peek() && fitAll(ctl.current, 400);
    map.on("resize", fit);
    return () => map.off("resize", fit);
  }, [map]);

  return html`<${DialChip} dv=${dv} /><${ColumnCard} values=${shown} />`;
}

/** The light-to-dark ramp the 2D fills take while a dial level is shown. */
const DIAL_RAMP = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6"];

/**
 * The 2D county fill while a dial level is shown: each county's previous (`da`) and next (`db`)
 * P50 are feature state, set once per level; `vh_dt` moves between them and `vh_dscale` (the
 * dial's highest county P50) places them on the ramp, square-root spaced so the many small
 * counties stay apart. Counties with no value take the "no data" grey.
 */
function dialFillColour() {
  const a = ["to-number", ["coalesce", ["feature-state", "da"], 0]];
  const b = ["to-number", ["coalesce", ["feature-state", "db"], 0]];
  const v = ["+", a, ["*", ["-", b, a], gs("vh_dt")]];
  const ramp = ["interpolate", ["linear"], ["sqrt", ["min", 1, ["/", ["max", 0, v], gs("vh_dscale")]]]];
  DIAL_RAMP.forEach((t, i) => ramp.push(i / (DIAL_RAMP.length - 1), token(t)));
  return ["case", ["==", ["typeof", ["feature-state", "db"]], "number"], ramp, token("--no-data")];
}

/** Clear a county's dial values (only those: its class and hover states stay). */
function unset(map, id) {
  map.removeFeatureState({ source: "counties", id }, "da");
  map.removeFeatureState({ source: "counties", id }, "db");
}

/** Show a dial level on the 2D fills (or put the scenario's fills back with `dv` null). */
function dialFill(c, dv) {
  const { map, tw } = c;
  const f = c.fill || (c.fill = { saved: null, now: {}, ids: [] });
  try {
    if (!dv) {
      if (f.saved) map.setPaintProperty("fill", "fill-color", f.saved);
      for (const id of f.ids) unset(map, id);
      f.saved = null;
      f.now = {};
      f.ids = [];
      f.dialOn = false;
      return;
    }
    if (!f.saved) f.saved = map.getPaintProperty("fill", "fill-color");
    const t = tw.now.vh_dt ?? 1;
    const next = {};
    const ids = new Set([...f.ids, ...Object.keys(dv.counties)]);
    for (const id of ids) {
      const v = dv.counties[id];
      const b = v && Number.isFinite(v.p50) ? Math.max(0, v.p50) : null;
      const was = f.now[id];
      // Start from where the county is drawn now (the first time: from the dial's benchmark).
      const from = dv.bench[id];
      const a = was ? was.a + (was.b - was.a) * t : from && Number.isFinite(from.p50) ? Math.max(0, from.p50) : (b ?? 0);
      if (b === null) unset(map, id);
      else {
        map.setFeatureState({ source: "counties", id }, { da: a, db: b });
        next[id] = { a, b };
      }
    }
    f.now = next;
    f.ids = Object.keys(next);
    tw.set("vh_dscale", Math.max(dv.scale2d, 1e-9));
    if (!f.dialOn) map.setPaintProperty("fill", "fill-color", dialFillColour());
    tw.set("vh_dt", 0);
    tw.tween("vh_dt", 1, 700, { ease: outCubic });
  } catch {
    // The map is going away.
  }
  f.dialOn = Boolean(dv);
}

/**
 * The chip on the map while a dial level is shown: what the map shows and "Back to the
 * scenario". It is announced once, politely, when the map switches to the dial.
 */
function DialChip({ dv }) {
  const ins = mapInsets.value;
  const [said, setSaid] = useState("");
  const was = useRef(false);
  useEffect(() => {
    if (dv && !was.current) setSaid(`The map shows ${dv.reformName} at ${dv.label}, from the policy dial.`);
    if (!dv) setSaid("");
    was.current = Boolean(dv);
  }, [dv]);
  const left = ins ? ins.left : 0;
  const right = ins ? ins.right : 0;
  const style = `top: ${(ins?.top || 64) + 12}px; left: ${left}px; right: ${right}px`;
  return html`<p class="visually-hidden" role="status" aria-live="polite">${said}</p>
    ${dv &&
    html`<div class="dial-map-chip-row" style=${style}>
      <div class="dial-map-chip" data-testid="dial-map-chip">
        <span class="dial-map-chip-swatch" aria-hidden="true"></span>
        <span class="dial-map-chip-text">Showing: <strong>${dv.reformName}</strong> at <strong>${dv.label}</strong> (dial)</span>
        <button type="button" class="btn btn-quiet btn-xs dial-map-chip-back" onClick=${() => (dialView.value = null)}>Back to the scenario</button>
      </div>
    </div>`}`;
}

/** The map key's body while a dial level is shown (the shell shows it in place of the classes). */
export function DialKey({ dv }) {
  return html`<div class="dial-key">
    <h2 class="key-title" id="map-key-title">${dv.reformName} at ${dv.label}: homes added per county (middle estimate)</h2>
    <div class="dial-key-ramp" aria-hidden="true" style=${`background: linear-gradient(90deg, ${DIAL_RAMP.map((t) => `var(${t})`).join(", ")})`}></div>
    <p class="key-hint">Lighter: fewer homes; darker: more. One scale for every level of this dial, so the map darkens as the dial goes up. In 3D the columns stand on one scale too; tract columns step aside (the dial is county-level).</p>
  </div>`;
}

/** The hover card's lines for a county while a dial level is shown (the shell's 2D card). */
export function DialHoverLines({ dv, fips }) {
  const v = dv.counties[fips];
  return html`<span class="map-hover-class"><span class="key-swatch" style="background: var(--seq-5)"></span>${dv.reformName} at ${dv.label} (dial)</span>
    <span class="map-hover-value" data-testid="dial-hover-value">Homes added: ${dialText(v)}</span>`;
}

/** Add (or re-add) the column layers; `withTracts` sets whether counties sink near zoom 8. */
function placeLayers(c, withTracts) {
  const { map } = c;
  for (const id of c.layers) if (map.getLayer(id)) map.removeLayer(id);
  const specs = [...layersOf("county", c.lat0, withTracts), ...layersOf("tract", c.lat0, withTracts), ...notchLayers(c.lat0, withTracts)];
  const vis = view3d.peek() && c.entered ? "visible" : "none";
  for (const l of specs) {
    l.layout = { ...l.layout, visibility: l.id.endsWith("-cap") && !showRange.peek() ? "none" : vis };
    map.addLayer(l);
  }
  c.layers = specs.map((l) => l.id);
  c.withTracts = withTracts;
}

/** Build the features of a level: fixed footprint and places, previous (a) and next (b) values. */
function featuresOf(c, level, opts) {
  const before = new Map((c.feats[level] || []).map((f) => [f.geoid, f]));
  const vals = c.values[level] || {};
  const g = c.tw.now;
  return (c.data[level] || []).map((foot) => {
    const v = vals[foot.geoid];
    const prev = before.get(foot.geoid);
    const was = opts.fromZero || !prev ? { v10: 0, v50: 0, v90: 0 } : shownNow(prev, g);
    return {
      geoid: foot.geoid,
      county: foot.county,
      lv: level,
      cls: v?.cls || "nodata",
      dx: foot.dx,
      dn: foot.dn,
      at: foot.at,
      side: foot.side,
      a10: was.v10,
      a50: was.v50,
      a90: was.v90,
      b10: v?.v10 || 0,
      b50: v?.v50 || 0,
      b90: v?.v90 || 0,
    };
  });
}

function collection(list) {
  return {
    type: "FeatureCollection",
    features: list.map((f) => ({
      type: "Feature",
      properties: { geoid: f.geoid, county: f.county, lv: f.lv, cls: f.cls, dx: f.dx, dn: f.dn, a10: f.a10, a50: f.a50, a90: f.a90, b10: f.b10, b50: f.b50, b90: f.b90 },
      geometry: { type: "Polygon", coordinates: square(f.at[0], f.at[1], f.side) },
    })),
  };
}

/**
 * Set both levels' data once, then run `vh_t` from 0 to 1: a scenario change grows or sinks
 * each column from its previous height, west to east over 350 ms (700 ms each); the first 3D
 * view raises them from the ground north to south (150 ms + 600 ms spread, 650 ms each).
 */
function stage(c, { animate = true, intro = false } = {}) {
  const { map, tw } = c;
  const withTracts = Object.keys(c.values.tract || {}).length > 0 && (c.data.tract || []).length > 0;
  if (withTracts !== c.withTracts) placeLayers(c, withTracts);
  c.feats.county = featuresOf(c, "county", { fromZero: intro });
  c.feats.tract = withTracts ? featuresOf(c, "tract", { fromZero: intro }) : [];
  map.getSource(SOURCES.county)?.setData(collection(c.feats.county));
  map.getSource(SOURCES.tract)?.setData(collection(c.feats.tract));
  c.stale = false;
  fitAll(c, intro ? 0 : 400);
  const still = !animate || reducedMotion();
  if (intro) {
    tw.set("vh_keep", 0);
    tw.set("vh_dk", "n");
    tw.set("vh_dbase", 150);
    tw.set("vh_dspan", 600);
    tw.set("vh_each", 650);
    tw.set("vh_total", 1400);
  } else {
    tw.set("vh_keep", 1);
    tw.set("vh_dk", "x");
    tw.set("vh_dbase", 0);
    tw.set("vh_dspan", 350);
    tw.set("vh_each", 700);
    tw.set("vh_total", 1050);
  }
  tw.set("vh_t", still ? 1 : 0);
  if (!still) tw.tween("vh_t", 1, intro ? 1400 : 1050);
  // The hovered column may have changed height: the card follows.
  requestAnimationFrame(() => place(c));
}

/** Fit both levels' scales to the map's height (eased over `ms`). */
function fitAll(c, ms) {
  const h = c.map.getCanvas().clientHeight;
  const kc = fitScale(c.fitOn || c.values?.county || {}, h);
  const kt = fitScale(c.values?.tract || {}, h);
  c.tw.tween("vh_kc", kc, ms);
  c.tw.tween("vh_kt", kt, ms);
  c.scale = { county: kc, tract: kt };
  columnScale.value = { ...c.scale, height: h, at: Date.now() };
}


function setVisible(c, on) {
  for (const id of c.layers) {
    if (!c.map.getLayer(id)) continue;
    const show = on && !(id.endsWith("-cap") && !showRange.peek());
    c.map.setLayoutProperty(id, "visibility", show ? "visible" : "none");
  }
}

/** Into 3D: floor, camera, handlers; the columns rise (north to south the first time). */
function enter(c, now) {
  const { map, tw } = c;
  c.entered = true;
  const motion = !reducedMotion();
  try {
    map.setVerticalFieldOfView(FOV_3D);
  } catch {
    // An older map: the default field of view.
  }
  map.setMaxZoom(MAX_ZOOM_3D);
  map.dragRotate.enable();
  map.touchPitch.enable();
  map.keyboard.enableRotation();
  map.touchZoomRotate.enableRotation();
  map.setLight({ anchor: "map", position: [1.3, 315, 45], color: "#fff", intensity: 0.4 });
  map.setPaintProperty("floor3d", "fill-opacity", 0.9);
  map.setPaintProperty("line", "line-width", 0.8);
  c.userTilt = false;
  // The whole state, framed for the tilted camera (unless a county is open): the pitch
  // follows the zoom from this fit on.
  const intro = !introPlayed && motion;
  introPlayed = true;
  const pitch = intro ? 45 : 50;
  const turn = intro ? -12 : -15;
  let cam = null;
  try {
    const b = now.frameState?.();
    cam = b ? map.cameraForBounds(b.bounds, { padding: b.padding, pitch, bearing: turn }) : null;
    c.zFit = cam?.zoom ?? map.getZoom();
  } catch {
    c.zFit = map.getZoom();
  }
  if (c.values && c.data) stage(c, { animate: motion, intro });
  tw.tween("vh_h3d", 1, intro ? 0 : 450, { ease: outCubic });
  setVisible(c, true);
  const to = cam ? { center: cam.center, zoom: cam.zoom + (intro ? 0.25 : 0) } : intro ? { zoom: map.getZoom() + 0.25 } : {};
  map.easeTo({ ...to, pitch, bearing: turn, duration: motion ? (intro ? 1200 : 900) : 0, easing: outCubic, essential: true });
}

/** Back to 2D: the columns sink (450 ms), then the map flattens (600 ms). */
function leaveView(c) {
  const { map, tw } = c;
  c.entered = false;
  pointAt(c, null);
  const motion = !reducedMotion();
  map.dragRotate.disable();
  map.touchPitch.disable();
  map.keyboard.disableRotation();
  map.touchZoomRotate.disableRotation();
  const flatten = () => {
    if (view3d.peek()) return;
    setVisible(c, false);
    map.setPaintProperty("floor3d", "fill-opacity", 0);
    map.setPaintProperty("line", "line-width", 1);
    map.easeTo({ pitch: 0, bearing: 0, duration: motion ? 600 : 0, easing: outCubic, essential: true });
    map.once("moveend", () => {
      if (view3d.peek()) return;
      map.setMaxZoom(22);
      try {
        map.setVerticalFieldOfView(FOV_2D);
      } catch {
        // An older map.
      }
    });
  };
  tw.tween("vh_h3d", 0, motion ? 450 : 0, { ease: outCubic, done: flatten });
}

/**
 * Point at a column (or nothing): it turns accent, the others dim to 0.55 over 250 ms, the
 * P10 notch appears on it and the card moves to its top.
 */
function pointAt(c, h) {
  const { map, tw } = c;
  const id = h?.id || "";
  if ((c.hot?.id || "") === id && (c.hot?.level || "") === (h?.level || "")) return;
  c.hot = h;
  tw.set("vh_hover", id);
  if (id) {
    tw.set("vh_dimof", id);
    tw.tween("vh_dim", 1, 250);
  } else {
    tw.tween("vh_dim", 0, 250, { done: () => !c.hot && tw.set("vh_dimof", "") });
  }
  const f = id ? (c.feats[h.level] || []).find((x) => x.geoid === id) : null;
  map.getSource(NOTCH)?.setData(
    f
      ? {
          type: "FeatureCollection",
          features: [{ type: "Feature", properties: { lv: h.level, p10: f.b10 }, geometry: { type: "Polygon", coordinates: square(f.at[0], f.at[1], f.side * 1.18) } }],
        }
      : EMPTY,
  );
  hoveredTract.value = f ? { level: h.level, id, county: f.county } : null;
  place(c);
}

/** Put the card on the top of the column pointed at (its base projected, lifted by its height). */
function place(c) {
  const h = c.hot;
  const f = h ? (c.feats[h.level] || []).find((x) => x.geoid === h.id) : null;
  if (!f) {
    if (columnPoint.peek()) columnPoint.value = null;
    return;
  }
  const { map } = c;
  const p = map.project(f.at);
  const k = (h.level === "tract" ? c.scale?.tract : c.scale?.county) || 0;
  const top = showRange.peek() ? f.b90 : f.b50;
  const lift = top * k * Math.sin(map.getPitch() * DEG) * (c.tw.now.vh_h3d || 0);
  const r = map.getContainer().getBoundingClientRect();
  columnPoint.value = { x: r.left + p.x, y: r.top + p.y - lift };
}

/* ------------------------------------------------------------------ card and legend */

/** The hover card on a column's top: place, class, likely range (P10 to P90, middle P50). */
function ColumnCard({ values }) {
  const hot = hoveredTract.value;
  const at = columnPoint.value;
  const ref = useRef(null);
  if (!view3d.value || !hot || !at || !values) return html`<div class="col-card" aria-hidden="true" ref=${ref}></div>`;
  const county = values.county[hot.county];
  const v = hot.level === "tract" ? values.tract[hot.id] : county;
  const rec = v?.rec;
  const isTract = hot.level === "tract";
  const text = rec
    ? isTract || !rec.shown
      ? `likely ${plain(rec.p10)} to ${plain(rec.p90)}, middle ${plain(rec.p50)}`
      : `likely ${rec.shown.p10} to ${rec.shown.p90}, middle ${rec.shown.p50}`
    : "no value";
  const flip = at.x > window.innerWidth - 300;
  const style = `transform: translate3d(${Math.round(flip ? at.x - 268 : at.x + 14)}px, ${Math.round(Math.max(8, at.y - 18))}px, 0)`;
  return html`<div class="col-card is-on" aria-hidden="true" ref=${ref} style=${style}>
    <strong class="map-hover-name">${isTract ? `Tract ${hot.id}` : county?.name || hot.id}</strong>
    ${isTract && html`<span class="map-hover-value">${county?.name || hot.county}</span>`}
    <span class="map-hover-class"><span class="key-swatch" style=${`background: var(${COLUMN_COLOURS[v?.cls] || "--no-data"})`}></span>${classWord(v?.cls)}</span>
    <span class="map-hover-value">${values.dial ? `Homes added at ${values.dial.label} (dial)` : values.any ? "Homes added" : "Baseline homes"}: ${text}</span>
    ${rec && html`<span class="map-hover-bar"><${RangeBar} label=${isTract ? `Tract ${hot.id}` : county?.name} n=${rec} lo=${0} hi=${Math.max(rec.p90, 1e-9)} /></span>`}
    <span class="map-hover-hint">Click to open the county</span>
  </div>`;
}

const WORDS = {
  little: "Little effect",
  some: "Adds some",
  a_lot: "Adds a lot",
  no_base: "No baseline to compare",
  few: "Under 1 home",
  many: "5 to under 25 homes",
  most: "25 homes or more",
  base: "Baseline (no reform on)",
  dial: "Policy dial level",
  none: "No added homes",
  nodata: "No data",
};
function classWord(cls) {
  return WORDS[cls] || "";
}

/**
 * The legend's mini patch: three columns in the class colours (light, middle, dark) at the
 * current scale, each the tallest of its class, with its glass cap and leader ticks naming
 * the tallest one's middle and high values (the API's text).
 */
export function ColumnPatch({ mapReply, values, level = "county" }) {
  const sc = columnScale.value;
  const k = level === "tract" ? sc.tract : sc.county;
  if (!values || !k) return null;
  const vals = values[level] || {};
  const order = level === "tract" ? [["few", "some"], ["many"], ["most"]] : values.any ? [["little"], ["some"], ["a_lot"]] : [["base"], ["base"], ["base"]];
  const picks = [];
  const used = new Set();
  for (const group of order) {
    let best = null;
    for (const [id, v] of Object.entries(vals)) {
      if (!group.includes(v.cls) || used.has(id)) continue;
      if (!best || v.v90 > best.v.v90) best = { id, v };
    }
    if (best) used.add(best.id);
    picks.push(best);
  }
  // Shrink the patch to 64 px for the tallest column, keeping the map's proportions.
  const tallest = Math.max(1e-9, ...picks.filter(Boolean).map((p) => p.v.v90));
  const s = 64 / tallest;
  const tokens = level === "tract" ? ["--seq-2", "--seq-5", "--seq-6"] : values.any ? ["--seq-2", "--seq-4", "--seq-6"] : ["--seq-3", "--seq-3", "--seq-3"];
  const top = picks.reduce((a, p) => (p && (!a || p.v.v90 > a.v.v90) ? p : a), null);
  const rec = top?.v.rec;
  const name = level === "tract" ? `Tract ${top?.id}` : top && mapReply?.counties?.find((c) => c.fips === top.id)?.name;
  const say = (field) => (rec ? (rec.shown ? rec.shown[field] : `${plain(rec[field])} homes`) : "");
  return html`<figure class="col-patch">
    <svg viewBox="0 0 168 86" width="168" height="86" role="img"
      aria-label=${top ? `Columns at the map's scale. Tallest: ${name}, middle ${say("p50")}, likely high ${say("p90")}.` : "Columns at the map's scale."}>
      <line x1="0" y1="80" x2="120" y2="80" stroke="var(--line-strong)" stroke-width="1" />
      ${picks.map((p, i) => {
        if (!p) return null;
        const x = 10 + i * 36;
        const h50 = p.v.v50 * s;
        const h90 = p.v.v90 * s;
        return html`<g>
          <rect x=${x} y=${80 - h90} width="20" height=${Math.max(0, h90 - h50)} fill=${`var(${tokens[i]})`} opacity="0.25" stroke=${`var(${tokens[i]})`} stroke-width="0.5" />
          <rect x=${x} y=${80 - h50} width="20" height=${h50} fill=${`var(${tokens[i]})`} />
          <path d=${`M${x} ${80 - h90}l5 -4h20l-5 4z`} fill=${`var(${tokens[i]})`} opacity="0.35" />
        </g>`;
      })}
      ${top &&
      html`<g class="col-patch-ticks" font-size="9" fill="var(--text-muted)">
        <line x1="112" y1=${80 - top.v.v90 * s} x2="124" y2=${80 - top.v.v90 * s} stroke="var(--text-muted)" />
        <line x1="112" y1=${80 - top.v.v50 * s} x2="124" y2=${80 - top.v.v50 * s} stroke="var(--text-muted)" />
        <text x="126" y=${83 - top.v.v90 * s}>P90</text>
        <text x="126" y=${Math.min(80, 92 - top.v.v50 * s)}>P50</text>
      </g>`}
    </svg>
    ${top && html`<figcaption class="col-patch-cap">Tallest: ${name}, middle ${say("p50")}, likely high ${say("p90")}. Glass cap: P50 to P90.</figcaption>`}
  </figure>`;
}
