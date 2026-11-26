/**
 * The district layer: state legislative districts over the map, for "what does this reform
 * mean where my voters live?".
 *
 * What it does
 *   - "Districts" in the map controls turns the layer on; a House / Senate switch in the map
 *     key picks the chamber (state house = lower chamber, state senate = upper chamber).
 *   - 2D: each district is filled by its class from the server (GET /states/ST/districts):
 *     homes added per 1,000 of its 2020 homes, middle estimate, by the fixed breaks of
 *     assumptions/decision.yaml; grey "not modelled" with no reform on. A district whose
 *     split share is above the ledger's `districts.split_caveat_share` is hatched (the
 *     server's `caveat` flag) and its hover card and panel say why.
 *   - 3D: one column per district at the middle of its largest part, the solid core up to
 *     the middle estimate (P50) and a glass cap to the likely high value (P90), the pattern of
 *     ui/columns.js. The county and tract columns step aside while the districts show.
 *   - Pointing at a district shows its card (name, the range text the server wrote, the split
 *     caveat). Clicking it opens the district panel (ui/district-panel.js).
 *   - "Find a district" (DistrictFind) searches by district number or name, and by county
 *     name: a county lists the districts that hold some of its homes (the server's county
 *     weights). Up / Down move through the list, Enter opens, Esc closes.
 *
 * Shapes come from GET /states/ST/district-outlines (the pack's outlines; made-up rectangles
 * for Testland). The static site has them only when its export says `districts: true`;
 * otherwise the layer says it needs the local app. No number is made here: the browser only
 * places the server's values (a column's height is its P50 and P90 on one scale).
 */
import { api, districtShapes, districtsReply, IS_STATIC } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { Icon } from "./icons.js";
import { mapInsets, MEASURE_COLOURS } from "./map.js";
import { districtView, mainMap, showRange, view3d } from "./map-state.js";
import { hoveredCounty, hoverPoint, useHoverFollow } from "./panel.js";
import { reducedMotion } from "./motion.js";

/** Chamber ids and their names on screen. */
export const CHAMBERS = [
  ["lower", "House"],
  ["upper", "Senate"],
];

/** Class id to colour token: the map's per-1,000 classes, plus grey for no reform / no data. */
export const DISTRICT_COLOURS = { ...MEASURE_COLOURS, none: "--not-modelled", nodata: "--no-data" };

/** The district pointed at (its id, or ""). */
const hotDistrict = { id: "" };

const SRC = "dist-shapes";
const COLS = "dist-cols";
const LAYERS_2D = ["dist-fill", "dist-hatch"];
const LAYERS_ALL = ["dist-fill", "dist-hatch", "dist-line", "dist-hover", "dist-sel", "dist-col-core", "dist-col-cap"];
const EMPTY = { type: "FeatureCollection", features: [] };
const DEG = Math.PI / 180;

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#cccccc";
}

export function setDistricts(patch) {
  districtView.value = { ...districtView.peek(), ...patch };
}

/** The cache key and loader of a chamber's reply under a scenario (shared with the panel). */
export function useDistrictReply(sc, chamber, on = true) {
  const key = on ? `districts:${chamber}:${sc.state}:${(sc.reforms || []).join(",")}:${JSON.stringify(sc.overrides || {})}` : "";
  return useJson(key, () => districtsReply(sc, chamber));
}

export function useDistrictShapes(state, chamber, on = true) {
  return useJson(on ? `district-shapes:${state}:${chamber}` : "", () => districtShapes(state, chamber));
}

/* ------------------------------------------------------------------ geometry (placing only) */

function rings(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return [geometry.coordinates];
  if (geometry.type === "MultiPolygon") return geometry.coordinates;
  return [];
}

/** The middle of a district's largest part ([lon, lat]) and that part's area in square metres. */
function middleOf(geometry) {
  let best = null;
  let bestA = 0;
  for (const poly of rings(geometry)) {
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
  if (!best) return null;
  const m2 = (bestA / 2) * 111320 * Math.cos(best[1] * DEG) * 110540;
  return { at: best, area: m2 };
}

/** The bounding box of one feature or a collection: [[west, south], [east, north]]. */
export function boxOf(features) {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const f of features) {
    for (const poly of rings(f.geometry)) {
      for (const [x, y] of poly[0] || []) {
        w = Math.min(w, x);
        e = Math.max(e, x);
        s = Math.min(s, y);
        n = Math.max(n, y);
      }
    }
  }
  return Number.isFinite(w) ? [[w, s], [e, n]] : null;
}

function square(lon, lat, side) {
  const h = side / 2;
  const dy = h / 110540;
  const dx = h / (111320 * Math.cos(lat * DEG));
  return [[[lon - dx, lat - dy], [lon + dx, lat - dy], [lon + dx, lat + dy], [lon - dx, lat + dy], [lon - dx, lat - dy]]];
}

/** The range a district shows: the headline (the scenario's reforms), else the baseline. */
export function shownRange(row) {
  if (!row) return null;
  return row.headline ? row.headline.homes : row.baseline;
}

/** Shapes with each district's class and caveat flag from the reply, for the map. */
function dressed(shapes, reply) {
  const rows = new Map((reply?.districts || []).map((d) => [d.id, d]));
  return {
    type: "FeatureCollection",
    features: (shapes?.features || []).map((f) => {
      const id = String(f.properties.geoid);
      const d = rows.get(id);
      return { type: "Feature", properties: { geoid: id, name: d?.name || f.properties.name, cls: d ? d.class : "nodata", caveat: d?.caveat ? 1 : 0 }, geometry: f.geometry };
    }),
  };
}

/** Column squares: P50 and P90 in metres on one scale (the tallest P90 = 30% of the state's height). */
function columns(shapes, reply) {
  const rows = new Map((reply?.districts || []).map((d) => [d.id, d]));
  const box = boxOf(shapes?.features || []);
  if (!box) return EMPTY;
  const span = (box[1][1] - box[0][1]) * 110540;
  const tops = [...rows.values()].map((d) => shownRange(d)?.p90).filter(Number.isFinite);
  const top = Math.max(1, ...tops);
  const k = (0.3 * span) / top;
  const features = [];
  for (const f of shapes.features) {
    const id = String(f.properties.geoid);
    const d = rows.get(id);
    const mid = middleOf(f.geometry);
    if (!d || !mid) continue;
    const r = shownRange(d);
    const side = Math.max(800, Math.min(9000, 0.3 * Math.sqrt(mid.area)));
    features.push({
      type: "Feature",
      properties: { geoid: id, cls: d.headline ? d.class : "base", h50: Math.max(0, r?.p50 || 0) * k, h90: Math.max(0, r?.p90 || 0) * k },
      geometry: { type: "Polygon", coordinates: square(mid.at[0], mid.at[1], side) },
    });
  }
  return { type: "FeatureCollection", features };
}

/** A small diagonal hatch, drawn once, for the districts that carry the split caveat. */
function hatchImage() {
  const n = 8;
  const c = document.createElement("canvas");
  c.width = n;
  c.height = n;
  const g = c.getContext("2d");
  g.strokeStyle = token("--text");
  g.globalAlpha = 0.55;
  g.lineWidth = 1.2;
  g.beginPath();
  g.moveTo(0, n);
  g.lineTo(n, 0);
  g.moveTo(-n / 2, n / 2);
  g.lineTo(n / 2, -n / 2);
  g.moveTo(n / 2, n + n / 2);
  g.lineTo(n + n / 2, n / 2);
  g.stroke();
  const img = g.getImageData(0, 0, n, n);
  return { width: n, height: n, data: new Uint8Array(img.data.buffer) };
}

function colourMatch() {
  const pairs = Object.entries({ ...DISTRICT_COLOURS, base: "--seq-3" }).flatMap(([id, t]) => [id, token(t)]);
  return ["match", ["get", "cls"], ...pairs, token("--no-data")];
}

function addLayers(map) {
  if (map.getSource(SRC)) return;
  map.addSource(SRC, { type: "geojson", data: EMPTY, promoteId: "geoid" });
  map.addSource(COLS, { type: "geojson", data: EMPTY, promoteId: "geoid" });
  if (!map.hasImage("dist-hatch")) map.addImage("dist-hatch", hatchImage());
  const hidden = { visibility: "none" };
  map.addLayer({ id: "dist-fill", type: "fill", source: SRC, layout: hidden, paint: { "fill-color": colourMatch(), "fill-opacity": 0.86 } });
  map.addLayer({ id: "dist-hatch", type: "fill", source: SRC, layout: hidden, filter: ["==", ["get", "caveat"], 1], paint: { "fill-pattern": "dist-hatch", "fill-opacity": 0.9 } });
  map.addLayer({ id: "dist-line", type: "line", source: SRC, layout: hidden, paint: { "line-color": token("--surface"), "line-width": 1.1, "line-opacity": 0.95 } });
  map.addLayer({ id: "dist-hover", type: "line", source: SRC, layout: hidden, filter: ["==", ["get", "geoid"], ""], paint: { "line-color": token("--accent-strong"), "line-width": 2.2 } });
  map.addLayer({ id: "dist-sel", type: "line", source: SRC, layout: hidden, filter: ["==", ["get", "geoid"], ""], paint: { "line-color": token("--accent-strong"), "line-width": 3.2 } });
  map.addLayer({ id: "dist-col-core", type: "fill-extrusion", source: COLS, layout: hidden,
    paint: { "fill-extrusion-color": colourMatch(), "fill-extrusion-base": 0, "fill-extrusion-height": ["get", "h50"], "fill-extrusion-opacity": 1, "fill-extrusion-vertical-gradient": true } });
  map.addLayer({ id: "dist-col-cap", type: "fill-extrusion", source: COLS, layout: hidden,
    paint: { "fill-extrusion-color": colourMatch(), "fill-extrusion-base": ["get", "h50"], "fill-extrusion-height": ["get", "h90"], "fill-extrusion-opacity": 0.25 } });
}

/** Show the layers for the view (2D fill or 3D columns); the county columns step aside in 3D. */
function showLayers(map, on, three) {
  const vis = (id) => {
    if (!on) return false;
    if (id.startsWith("dist-col")) return three && (id !== "dist-col-cap" || showRange.peek());
    if (LAYERS_2D.includes(id)) return !three;
    return true;
  };
  for (const id of LAYERS_ALL) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis(id) ? "visible" : "none");
  // The county and tract columns (ui/columns.js) hide while the district columns stand.
  if (three) {
    for (const l of map.getStyle()?.layers || []) {
      if (!/^col-(county|tract|notch)/.test(l.id)) continue;
      const want = on ? "none" : l.id.endsWith("-cap") && !showRange.peek() ? "none" : "visible";
      if (map.getLayoutProperty(l.id, "visibility") !== want) map.setLayoutProperty(l.id, "visibility", want);
    }
  }
}

/** Ease the view to a district (its box, inside the free area between the panels). */
export function frameDistrict(map, feature) {
  const b = feature && boxOf([feature]);
  if (!map || !b) return;
  const ins = mapInsets.peek() || { top: 72, bottom: 72, left: 380, right: 440 };
  try {
    map.fitBounds(b, {
      padding: { top: ins.top + 24, bottom: ins.bottom + 24, left: ins.left + 24, right: ins.right + 24 },
      maxZoom: 10.5,
      duration: reducedMotion() ? 0 : 700,
      pitch: map.getPitch(),
      bearing: map.getBearing(),
      essential: true,
    });
  } catch {
    // A box the map cannot fit: stay where it is.
  }
}

/**
 * The layer controller: adds the sources and layers once, keeps their data and visibility in
 * step with the reply, answers the pointer, and shows the hover card. `sc` is the scenario the
 * map shades.
 */
export function DistrictLayer({ sc }) {
  const view = districtView.value;
  const map = mainMap.value;
  const three = view3d.value;
  const range = showRange.value;
  const shapes = useDistrictShapes(sc.state, view.chamber, view.on);
  const reply = useDistrictReply(sc, view.chamber, view.on);
  const [hot, setHot] = useState("");
  const now = useRef({});
  now.current = { ...now.current, shapes: shapes.data, reply: reply.data };

  useEffect(() => {
    if (!map) return undefined;
    try {
      addLayers(map);
    } catch {
      return undefined;
    }
    const pick = (point) => {
      const v = districtView.peek();
      if (!v.on) return null;
      const ids = (view3d.peek() ? ["dist-col-core", "dist-col-cap"] : ["dist-fill"]).filter((id) => map.getLayer(id));
      const f = map.queryRenderedFeatures(point, { layers: ids })[0];
      return f ? String(f.properties.geoid) : null;
    };
    const move = (e) => {
      if (!districtView.peek().on) return;
      const id = pick(e.point) || "";
      map.getCanvas().style.cursor = id ? "pointer" : "";
      if (hoveredCounty.peek()) hoveredCounty.value = "";
      hoverPoint.value = id ? { x: e.point.x, y: e.point.y, from: "map" } : null;
      if (id !== hotDistrict.id) {
        hotDistrict.id = id;
        setHot(id);
        if (map.getLayer("dist-hover")) map.setFilter("dist-hover", ["==", ["get", "geoid"], id]);
      }
    };
    const leave = () => {
      if (!districtView.peek().on || !hotDistrict.id) return;
      hotDistrict.id = "";
      setHot("");
      hoverPoint.value = null;
      if (map.getLayer("dist-hover")) map.setFilter("dist-hover", ["==", ["get", "geoid"], ""]);
    };
    const click = (e) => {
      const id = pick(e.point);
      if (id) setDistricts({ picked: id });
    };
    // A layer of the 3D columns re-added by ui/columns.js comes back hidden.
    const keep = () => {
      const v = districtView.peek();
      if (v.on && view3d.peek()) showLayers(map, true, true);
    };
    // Where each district's middle sits on screen and what is drawn, once the map rests
    // (read by the browser tests, as the map's own `data-center`).
    const note = () => {
      const box = map.getContainer();
      const v = districtView.peek();
      if (!v.on) {
        box.dataset.districts = "off";
        return;
      }
      const pts = {};
      for (const f of now.current.shapes?.features || []) {
        const b = boxOf([f]);
        if (!b) continue;
        const p = map.project([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2]);
        pts[String(f.properties.geoid)] = [Math.round(p.x), Math.round(p.y)];
      }
      box.dataset.districts = view3d.peek() ? "3d" : "2d";
      box.dataset.districtPoints = JSON.stringify(pts);
      box.dataset.districtColumns = String(now.current.cols || 0);
      box.dataset.districtLayers = [...LAYERS_ALL, "col-county-core", "col-tract-core"].map((id) => `${id}:${map.getLayer(id) ? map.getLayoutProperty(id, "visibility") : "absent"}`).join(",");
    };
    map.on("idle", note);
    map.on("mousemove", move);
    map.on("mouseout", leave);
    map.on("click", click);
    map.on("styledata", keep);
    return () => {
      try {
        map.off("mousemove", move);
        map.off("mouseout", leave);
        map.off("click", click);
        map.off("styledata", keep);
        map.off("idle", note);
      } catch {
        // The map is gone.
      }
    };
  }, [map]);

  // New shapes or values: set the data once.
  useEffect(() => {
    if (!map || !map.getSource(SRC)) return;
    const data = view.on && shapes.data ? dressed(shapes.data, reply.data) : EMPTY;
    map.getSource(SRC).setData(data);
    const cols = view.on && shapes.data && reply.data ? columns(shapes.data, reply.data) : EMPTY;
    now.current.cols = cols.features.length;
    map.getSource(COLS).setData(cols);
  }, [map, view.on, shapes.data, reply.data]);

  // On / off, 2D / 3D, the range caps.
  useEffect(() => {
    if (!map || !map.getSource(SRC)) return;
    showLayers(map, view.on, three);
    if (!view.on) {
      hotDistrict.id = "";
      setHot("");
    }
  }, [map, view.on, three, range]);

  // The picked district: outline it and ease the view there.
  const picked = view.on ? view.picked : "";
  useEffect(() => {
    if (!map || !map.getLayer("dist-sel")) return;
    map.setFilter("dist-sel", ["==", ["get", "geoid"], picked]);
    if (picked) frameDistrict(map, (now.current.shapes?.features || []).find((f) => String(f.properties.geoid) === picked));
  }, [map, picked, shapes.data]);

  // Esc closes the picked district (a field or menu with focus takes Esc first).
  useEffect(() => {
    if (!picked) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (e.target?.closest?.("input, select, textarea, [role=menu], [role=listbox], .pop-panel")) return;
      setDistricts({ picked: "" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picked]);

  const row = hot && reply.data?.districts?.find((d) => d.id === hot);
  // The card is mounted only while the layer is on, so the county card stays the only one otherwise.
  return view.on ? html`<${DistrictHover} row=${row} reply=${reply.data} />` : null;
}

/** The hover card of the district pointed at: its name, the server's range text and the caveat. */
function DistrictHover({ row, reply }) {
  const card = useRef(null);
  const place = useHoverFollow(card);
  const r = shownRange(row);
  const cls = row && reply?.classes?.classes?.find((c) => c.id === row.class);
  return html`<div class=${`map-hover district-hover ${place.value} ${row ? "is-on" : ""}`} aria-hidden="true" ref=${card}>
    ${row &&
    html`<div class="map-hover-in" key=${row.id}>
      <strong class="map-hover-name">${row.name}</strong>
      ${cls && html`<span class="map-hover-class"><span class="key-swatch" style=${`background: var(${DISTRICT_COLOURS[row.class]})`}></span>${row.per_1000 && row.headline?.per_1000?.shown ? `${row.headline.per_1000.shown.p50} (middle)` : cls.label}</span>`}
      ${r?.shown && html`<span class="map-hover-value">${row.headline ? `Homes added (${row.headline.title})` : "Baseline homes"}: ${r.shown.p10} to ${r.shown.p90}</span>`}
      ${row.caveat && html`<span class="map-hover-value district-caveat-badge"><span class="key-swatch is-hatch"></span>Split-share caveat: ${row.split_share_shown} of its homes are in shared tracts</span>`}
      <span class="map-hover-hint">Click to open the district brief</span>
    </div>`}
  </div>`;
}

/**
 * The "Districts" button (the House / Senate switch sits in the map key). In the live app it
 * shows only when the state has district outlines; on the static site it always shows and,
 * when the export holds no district files, the map key says the view needs the local app.
 */
export function DistrictToggle({ state }) {
  const probe = useJson(IS_STATIC ? "" : `district-outlines:${state}`, () => api.districtOutlines(state, "lower"));
  const view = districtView.value;
  useEffect(() => setDistricts({ on: false, picked: "" }), [state]);
  const has = IS_STATIC || (probe.data?.features?.length || 0) > 0;
  if (!has) return null;
  return html`<button type="button" class="btn btn-quiet btn-xs district-btn" aria-pressed=${view.on ? "true" : "false"}
        onClick=${() => setDistricts({ on: !view.on, picked: "" })} title="Show the state legislative districts">
      <${Icon} name="layers" size=${14} /><span class="district-btn-label">Districts</span>
    </button>`;
}

/** The House / Senate switch (in the map key while the district layer is on). */
function ChamberSwitch() {
  const view = districtView.value;
  return html`<div class="seg-pair district-chamber" role="group" aria-label="Chamber">
    ${CHAMBERS.map(([id, label]) => html`<button type="button" class="seg seg-xs" aria-pressed=${view.chamber === id ? "true" : "false"}
        onClick=${() => setDistricts({ chamber: id, picked: "" })}>${label}</button>`)}
  </div>`;
}

/** The map key's district part: the classes, the caveat hatch, and any note from the reply. */
export function DistrictKey({ sc }) {
  const view = districtView.value;
  const reply = useDistrictReply(sc, view.chamber, view.on);
  const shapes = useDistrictShapes(sc.state, view.chamber, view.on);
  const r = reply.data;
  const word = CHAMBERS.find(([id]) => id === view.chamber)?.[1] || "";
  const note = shapes.error || reply.error;
  return html`<div class="district-key">
    <${ChamberSwitch} />
    <h2 class="key-title" id="map-key-title">${r && r.reforms?.length ? r.classes.title : `State ${word.toLowerCase()} districts`}</h2>
    ${note && html`<p class="key-hint district-note" role="note">${shapes.localOnly || reply.localOnly ? note : `The districts did not load: ${note}`}</p>`}
    ${reply.loading && !r && html`<p class="key-hint" role="status">Loading the districts…</p>`}
    ${r && html`<ul class="key-list">
      ${r.reforms?.length
        ? r.classes.classes.map((c) => html`<li><span class="key-swatch" style=${`background: var(${DISTRICT_COLOURS[c.id]})`}></span>${c.label}</li>`)
        : html`<li><span class="key-swatch is-notmodelled"></span>No reform on: columns and cards show the baseline</li>`}
      <li><span class="key-swatch is-hatch"></span>Split-share caveat: more than ${r.threshold_shown} of the district's homes are in tracts it shares</li>
    </ul>
    <p class="key-hint">${view3d.value ? "Column height: homes added, middle estimate; the glass cap reaches the likely high value. " : ""}${r.district_count} ${word} districts · ${r.vintage?.plans || ""}</p>`}
  </div>`;
}

/** Search entries: each district by number and name, and each county with the districts it touches. */
function entriesOf(reply) {
  const out = [];
  const byCounty = new Map();
  for (const d of reply?.districts || []) {
    out.push({ kind: "district", id: d.id, label: d.name, hint: shownRange(d)?.shown ? `${shownRange(d).shown.p10} to ${shownRange(d).shown.p90}` : "" });
    for (const c of d.counties_touched || []) {
      if (!byCounty.has(c.fips)) byCounty.set(c.fips, { name: c.name, list: [] });
      byCounty.get(c.fips).list.push({ id: d.id, name: d.name, shown: c.shown, weight: c.weight });
    }
  }
  return { districts: out, counties: byCounty };
}

/** The matches of a query: district numbers first, then names, then county names. */
export function matchesOf(reply, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q || !reply) return [];
  const { districts, counties } = entriesOf(reply);
  const num = q.replace(/^(district|house|senate|hd|sd)\s*/, "").replace(/^0+(?=\d)/, "");
  const exact = districts.filter((d) => d.id.replace(/^0+(?=\d)/, "").toLowerCase() === num);
  const named = districts.filter((d) => !exact.includes(d) && d.label.toLowerCase().includes(q));
  const out = [...exact, ...named].slice(0, 8);
  for (const [fips, c] of counties) {
    if (!c.name.toLowerCase().includes(q)) continue;
    for (const d of [...c.list].sort((a, b) => b.weight - a.weight)) {
      out.push({ kind: "county", id: d.id, label: d.name, county: c.name, fips, hint: d.shown });
    }
    if (out.length > 16) break;
  }
  return out.slice(0, 16);
}

/**
 * "Find a district": a combobox over the chamber's districts. Type a number ("91"), a name,
 * or a county name; Up / Down move through the list, Enter opens the district, Esc closes the
 * list. Press / to jump here while the district layer is on.
 */
export function DistrictFind({ sc }) {
  const view = districtView.value;
  const reply = useDistrictReply(sc, view.chamber, view.on);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const box = useRef(null);
  const closing = useRef(0);
  const list = matchesOf(reply.data, q);
  const show = () => {
    clearTimeout(closing.current);
    setOpen(true);
  };
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "/" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target?.closest?.("input, select, textarea, [contenteditable]")) return;
      e.preventDefault();
      box.current?.focus();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  const go = (m) => {
    if (!m) return;
    setDistricts({ picked: m.id });
    setQ("");
    setOpen(false);
    box.current?.blur();
  };
  const word = CHAMBERS.find(([id]) => id === view.chamber)?.[1] || "";
  const active = open && list.length ? `district-opt-${Math.min(at, list.length - 1)}` : undefined;
  return html`<div class="map-find district-find">
    <${Icon} name="search" size=${14} />
    <label class="visually-hidden" for="district-find-input">Find a ${word.toLowerCase()} district by number, name or county (press / to jump here)</label>
    <input ref=${box} id="district-find-input" type="search" class="map-find-input" role="combobox" autocomplete="off"
      aria-autocomplete="list" aria-expanded=${open && list.length ? "true" : "false"} aria-controls="district-find-list"
      aria-activedescendant=${active} placeholder="Find my district  /" value=${q}
      onInput=${(e) => {
        setQ(e.currentTarget.value);
        setAt(0);
        show();
      }}
      onFocus=${show}
      onBlur=${() => {
        clearTimeout(closing.current);
        closing.current = setTimeout(() => setOpen(false), 150);
      }}
      onKeyDown=${(e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          show();
          const n = list.length || 1;
          setAt((i) => (e.key === "ArrowDown" ? (i + 1) % n : (i - 1 + n) % n));
        } else if (e.key === "Enter") {
          e.preventDefault();
          go(list[Math.min(at, list.length - 1)]);
        } else if (e.key === "Escape") {
          if (open && q) {
            e.preventDefault();
            e.stopPropagation();
            setOpen(false);
          } else e.currentTarget.blur();
        }
      }} />
    ${open && q &&
    html`<ul class="district-find-list" id="district-find-list" role="listbox" aria-label="Matching districts">
      ${list.length === 0 && html`<li class="district-find-none" role="option" aria-disabled="true">No ${word.toLowerCase()} district matches</li>`}
      ${list.map((m, i) => html`<li id=${`district-opt-${i}`} role="option" aria-selected=${i === at ? "true" : "false"}
          class=${`district-find-opt ${i === at ? "is-on" : ""}`} onMouseDown=${(e) => {
            e.preventDefault();
            go(m);
          }} onMouseEnter=${() => setAt(i)}>
        <span class="district-find-name">${m.label}</span>
        <span class="district-find-hint">${m.kind === "county" ? `${m.county}: ${m.hint}` : m.hint}</span>
      </li>`)}
    </ul>`}
  </div>`;
}
