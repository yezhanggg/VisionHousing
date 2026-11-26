/**
 * The funnel on the map: step through a reform's stages (lots in scope → physically fit →
 * financially feasible → expected permits; for transit-oriented density the land steps are in
 * acres) and watch the map shade each stage, county by county and, for an open county, tract
 * by tract.
 *
 * Data: GET /states/ST/tract-funnel?r=KEY (`api.tractFunnel`): each stage's P10 / P50 / P90
 * per tract, per county (the county's tracts summed in each draw) and for the state, with the
 * engine's text for the state. Reforms that work by county (fees, parking) have no tract
 * funnel; for them the panel says so in one sentence and lists the run's own funnel steps.
 *
 * Shading: each place gets `fv`, its stage value as a share (0-1) of the largest value of any
 * stage in the same unit (lots with lots, homes with homes, acres with acres), so a later stage
 * of the same unit is never drawn bigger than it is, and units are never mixed. When the unit
 * changes, the key says so ("Scale changes: now homes, not lots."). `fv` is feature state,
 * tweened per place over 560 ms, starting west to east across 240 ms. The tract outlines come
 * from the open county's reply and are added to the map once per county.
 *
 * Every number shown is the API's: the state's stage text, and the county's and tracts' values
 * as the API rounded them (written with commas). The browser divides only to place a colour.
 */
import { reformById } from "../content/reforms.js";
import { api, scenarioQuery } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { plain } from "../lib/format.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { Icon } from "./icons.js";
import { dialView, mainMap, mapFunnel, view3d, yearView } from "./map-state.js";
import { reducedMotion, Ticker } from "./motion.js";
import { Tabs } from "./panel.js";
import { LAW_REFORMS, lawView } from "./law-changes.js";

const PLAY_MS = 1100;
const EACH_MS = 560;
const SPREAD_MS = 240;
const UNIT_WORDS = { lots: "lots", units: "homes", acres: "acres", homes: "homes" };

/** The tract funnel reply of a reform (loaded once per state and reform). */
export function useTractFunnel(state, reform, overrides = {}) {
  const key = reform ? `tract-funnel:${state}:${reform}:${JSON.stringify(overrides || {})}` : "";
  return useJson(key, () => api.tractFunnel(state, reform, overrides));
}

/** The stages of `reform` in a tract funnel reply, or []. */
export function stagesOf(reply, reform) {
  return reply?.funnel?.[reform]?.steps || [];
}

/** Each stage's scale: the largest P50 of any stage with the same unit, at `level`. */
function maxByUnit(steps, level, pick = null) {
  const out = {};
  for (const s of steps) {
    const vals = s[level]?.p50 || [];
    const list = pick ? pick.map((i) => vals[i]) : vals;
    const m = Math.max(0, ...list.filter(Number.isFinite));
    out[s.unit] = Math.max(out[s.unit] || 0, m);
  }
  return out;
}

const unitWord = (u) => UNIT_WORDS[u] || u;
/** How many step selectors show each reform (two can: the Reform panel and a county panel). */
const users = new Map();

/**
 * The map stage a row of the run's funnel card stands for: the land or lot rows are the
 * stages in order; every later row (homes) is the last stage, expected permits.
 */
export function stageOfRow(row, stages) {
  return stages > 0 ? Math.min(row, stages - 1) : -1;
}

/**
 * The step selector: tabs through the stages (Left / Right move), a Play button (1,100 ms a
 * stage; any key or press stops it; hidden when the reader asks for less motion), and the
 * stage's state and county counts (the engine's text, rolling when it changes).
 */
export function MapFunnelSteps({ state, reform, overrides = {}, fips = "", countyName = "" }) {
  const reply = useTractFunnel(state, reform, overrides);
  const steps = stagesOf(reply.data, reform);
  const f = mapFunnel.value;
  // Turn the map funnel on for this reform while the selector is shown.
  useEffect(() => {
    if (!reform) return undefined;
    users.set(reform, (users.get(reform) || 0) + 1);
    if (mapFunnel.peek().reform !== reform) mapFunnel.value = { reform, step: 0, preview: -1 };
    return () => {
      const n = (users.get(reform) || 1) - 1;
      users.set(reform, n);
      if (n <= 0 && mapFunnel.peek().reform === reform) mapFunnel.value = { reform: "", step: 0, preview: -1 };
    };
  }, [reform]);
  const [playing, setPlaying] = useState(false);
  // A layout effect: the stop keys listen from the moment Play shows as pressed.
  useLayoutEffect(() => {
    if (!playing || !steps.length) return undefined;
    // Any key or press stops it, except on the Play button itself (see years.js).
    const stop = (e) => {
      const own = e.target?.closest?.(".map-funnel-play") && (e.key === " " || e.key === "Enter");
      if (!own) setPlaying(false);
    };
    const t = setInterval(() => {
      const now = mapFunnel.peek();
      if (now.step >= steps.length - 1) {
        setPlaying(false);
        return;
      }
      mapFunnel.value = { ...now, step: now.step + 1 };
    }, PLAY_MS);
    window.addEventListener("keydown", stop);
    window.addEventListener("pointerdown", stop);
    return () => {
      clearInterval(t);
      window.removeEventListener("keydown", stop);
      window.removeEventListener("pointerdown", stop);
    };
  }, [playing, steps.length]);

  if (!reform) return null;
  const countyOnly = reply.data?.county_only?.[reform];
  if (reply.error || countyOnly || (reply.data && !steps.length)) {
    return html`<div class="map-funnel is-county-only">
      <p class="card-sub">${reply.data?.county_only_note || (reply.localOnly ? reply.error : "This reform works county by county, so the map has no tract stages to show.")}</p>
    </div>`;
  }
  if (!steps.length) return html`<p class="card-sub map-funnel-wait" role="status">Loading the funnel on the map…</p>`;
  const i = Math.min(f.reform === reform ? f.step : 0, steps.length - 1);
  const step = steps[i];
  const tabs = steps.map((s, k) => ({ id: String(k), label: `${k + 1}. ${s.words}` }));
  const ci = fips ? (reply.data.counties || []).indexOf(fips) : -1;
  const cty = ci >= 0 ? step.counties : null;
  const say = (r, k) => (r ? `${plain(r.p10[k])} to ${plain(r.p90[k])} ${unitWord(step.unit)}` : "—");
  const changed = i > 0 && steps[i - 1].unit !== step.unit;
  return html`<div class="map-funnel" data-step=${i}>
    <div class="map-funnel-head">
      <p class="field-label">On the map: ${reformById(reform)?.short || reform}, stage by stage</p>
      ${!reducedMotion() &&
      html`<button type="button" class="btn btn-quiet btn-xs map-funnel-play" aria-pressed=${playing ? "true" : "false"}
          onClick=${(e) => {
            e.stopPropagation();
            if (!playing && i >= steps.length - 1) mapFunnel.value = { ...mapFunnel.peek(), step: 0 };
            setPlaying(!playing);
          }} onPointerDown=${(e) => e.stopPropagation()}>
        <${Icon} name=${playing ? "pause" : "play"} size=${13} /><span>${playing ? "Pause" : "Play"}</span></button>`}
    </div>
    <${Tabs} group=${`map-funnel-${fips || "state"}`} label="Funnel stage shown on the map" tabs=${tabs} current=${String(i)}
      onChange=${(id) => (mapFunnel.value = { ...mapFunnel.peek(), reform, step: Number(id) })} />
    <dl class="map-funnel-counts">
      <div><dt>${countyName || "County"}</dt><dd>${fips ? html`<${Ticker} text=${say(cty, ci)} />` : html`<span class="tick">Open a county to see its tracts</span>`}</dd></div>
      <div><dt>State</dt><dd><${Ticker} text=${step.state?.shown ? `${step.state.shown.p10} to ${step.state.shown.p90}` : "—"} live=${true} /></dd></div>
    </dl>
    <p class="card-sub map-funnel-label">${step.label}. Likely range (P10 to P90).${changed ? html` <strong class="map-funnel-unit">Scale changes: now ${unitWord(step.unit)}, not ${unitWord(steps[i - 1].unit)}.</strong>` : ""}</p>
  </div>`;
}

/** The map key while the funnel is on: the stage, its unit and the colour ramp. */
export function FunnelKey({ state, overrides = {} }) {
  const f = mapFunnel.value;
  const reply = useTractFunnel(state, f.reform, overrides);
  const steps = stagesOf(reply.data, f.reform);
  if (!f.reform || !steps.length) return null;
  const i = f.preview >= 0 ? f.preview : Math.min(f.step, steps.length - 1);
  const step = steps[i];
  const changed = i > 0 && steps[i - 1].unit !== step.unit;
  const top = maxByUnit(steps, "counties")[step.unit];
  return html`<div class="funnel-key" key=${step.unit}>
    <h2 class="key-title" id="map-key-title">Stage ${i + 1} of ${steps.length}: ${step.words} (${unitWord(step.unit)}, middle estimate)</h2>
    <div class="funnel-ramp" aria-hidden="true"></div>
    <p class="funnel-ramp-ends"><span>0</span><span>${plain(top)} ${unitWord(step.unit)} (largest county, any ${unitWord(step.unit)} stage)</span></p>
    ${changed && html`<p class="key-hint funnel-unit-note">Scale changes: now ${unitWord(step.unit)}, not ${unitWord(steps[i - 1].unit)}.</p>`}
  </div>`;
}

/** Tween `fv` feature state of `ids` to `targets`, west to east (`order` 0-1 each). */
function tweenStates(map, source, ids, from, to, order, raf) {
  cancelAnimationFrame(raf.current);
  const set = (id, v) => {
    try {
      map.setFeatureState({ source, id }, { fv: v });
    } catch {
      // The map is going away.
    }
  };
  if (reducedMotion()) {
    ids.forEach((id) => set(id, to[id]));
    return;
  }
  const t0 = performance.now();
  const step = (now) => {
    let busy = false;
    for (const id of ids) {
      const u = Math.min(1, Math.max(0, (now - t0 - order[id] * SPREAD_MS) / EACH_MS));
      if (u < 1) busy = true;
      const e = 1 - (1 - u) ** 3;
      const a = from[id] ?? 0;
      const b = to[id] ?? 0;
      set(id, a + (b - a) * e);
    }
    if (busy) raf.current = requestAnimationFrame(step);
  };
  raf.current = requestAnimationFrame(step);
}

function tokenOf(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#cccccc";
}

/** The colour of `fv`: the sequential ramp from light to dark; no state, no colour. */
function rampColour() {
  const v = ["coalesce", ["feature-state", "fv"], -1];
  return ["case", ["<", v, 0], "rgba(0,0,0,0)",
    ["interpolate", ["linear"], v, 0, tokenOf("--seq-1"), 0.2, tokenOf("--seq-2"), 0.4, tokenOf("--seq-3"), 0.6, tokenOf("--seq-4"), 0.8, tokenOf("--seq-5"), 1, tokenOf("--seq-6")]];
}

/** West-to-east order (0-1) of features by their first coordinate's longitude. */
function westToEast(ids, xs) {
  const x0 = Math.min(...xs);
  const span = Math.max(...xs) - x0 || 1;
  return Object.fromEntries(ids.map((id, i) => [id, (xs[i] - x0) / span]));
}

function firstLon(geometry) {
  let c = geometry?.coordinates;
  while (Array.isArray(c) && Array.isArray(c[0])) c = c[0];
  return Array.isArray(c) ? c[0] : 0;
}

/**
 * The map side of the funnel: shades the counties by the stage (statewide) and, when a
 * county is open, its tracts (added once per county). Also the tract hover card with a small
 * bar per stage.
 */
export function MapFunnelLayer({ sc, picked, outlines }) {
  const map = mainMap.value;
  const f = mapFunnel.value;
  const reply = useTractFunnel(sc.state, f.reform, sc.overrides);
  const steps = stagesOf(reply.data, f.reform);
  const query = scenarioQuery(sc);
  const county = useJson(f.reform && picked ? `county:${sc.state}:${picked}:${query}` : "", () => api.county(sc.state, picked, query));
  const raf = useRef({ c: { current: 0 }, t: { current: 0 } });
  const last = useRef({ c: {}, t: {} });
  const [hot, setHot] = useState(null);
  // The year slider, a policy dial level (ui/columns.js) and "What this law changes"
  // (ui/law-changes.js) have the map to themselves.
  const lawOn = lawView.value.on && LAW_REFORMS.includes(f.reform);
  const on = Boolean(map && f.reform && steps.length) && !yearView.value.on && !dialView.value && !lawOn;

  // Layers once per map: county stage fill, tract stage fill and its outline.
  useEffect(() => {
    if (!map) return;
    try {
      if (!map.getSource("fn-tracts")) map.addSource("fn-tracts", { type: "geojson", data: { type: "FeatureCollection", features: [] }, promoteId: "geoid" });
      if (!map.getLayer("fn-county")) map.addLayer({ id: "fn-county", type: "fill", source: "counties", layout: { visibility: "none" }, paint: { "fill-color": rampColour(), "fill-opacity": 0.9 } }, "dim");
      if (!map.getLayer("fn-tract")) map.addLayer({ id: "fn-tract", type: "fill", source: "fn-tracts", layout: { visibility: "none" }, paint: { "fill-color": rampColour(), "fill-opacity": 0.95 } }, "dim");
      if (!map.getLayer("fn-tract-line")) map.addLayer({ id: "fn-tract-line", type: "line", source: "fn-tracts", layout: { visibility: "none" }, paint: { "line-color": tokenOf("--surface"), "line-width": 0.6 } }, "dim");
    } catch {
      // The map is going away.
    }
    const move = (e) => {
      const g = e.features?.[0]?.properties?.geoid;
      setHot(g ? { id: String(g), x: e.originalEvent.clientX, y: e.originalEvent.clientY } : null);
    };
    const out = () => setHot(null);
    map.on("mousemove", "fn-tract", move);
    map.on("mouseleave", "fn-tract", out);
    return () => {
      try {
        map.off("mousemove", "fn-tract", move);
        map.off("mouseleave", "fn-tract", out);
      } catch {
        // Gone.
      }
    };
  }, [map]);

  // Show or hide the layers.
  useEffect(() => {
    if (!map) return;
    const vis = on ? "visible" : "none";
    for (const id of ["fn-county", "fn-tract", "fn-tract-line"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
    if (!on) {
      last.current = { c: {}, t: {} };
      setHot(null);
    }
  }, [map, on]);

  // The open county's tract outlines: set once per county.
  const tractFeatures = (county.data?.boundaries?.features || []).filter((x) => String(x.properties?.geoid).length === 11);
  const tkey = `${picked}|${tractFeatures.length}`;
  useEffect(() => {
    if (!map || !on) return;
    try {
      map.getSource("fn-tracts")?.setData({ type: "FeatureCollection", features: picked ? tractFeatures : [] });
      last.current.t = {};
    } catch {
      // Gone.
    }
  }, [map, on, tkey]);

  // The stage shown: tween every county (and the open county's tracts) to it.
  const i = steps.length ? Math.min(f.preview >= 0 ? f.preview : f.step, steps.length - 1) : -1;
  useEffect(() => {
    if (!on || i < 0) return;
    const step = steps[i];
    const cids = reply.data.counties || [];
    const cmax = maxByUnit(steps, "counties")[step.unit] || 1;
    const cto = Object.fromEntries(cids.map((id, k) => [id, Number.isFinite(step.counties.p50[k]) ? step.counties.p50[k] / cmax : 0]));
    const boxes = Object.fromEntries((outlines?.features || []).map((x) => [String(x.properties?.geoid), firstLon(x.geometry)]));
    const corder = westToEast(cids, cids.map((id) => boxes[id] ?? 0));
    tweenStates(map, "counties", cids, last.current.c, cto, corder, raf.current.c);
    last.current.c = cto;
    if (picked && tractFeatures.length) {
      const index = Object.fromEntries((reply.data.geoids || []).map((g, k) => [g, k]));
      const tids = tractFeatures.map((x) => String(x.properties.geoid)).filter((g) => g in index);
      const rows = tids.map((g) => index[g]);
      const tmax = maxByUnit(steps, "tracts", rows)[step.unit] || 1;
      const tto = Object.fromEntries(tids.map((g) => [g, (step.tracts.p50[index[g]] || 0) / tmax]));
      const torder = westToEast(tids, tractFeatures.filter((x) => String(x.properties.geoid) in index).map((x) => firstLon(x.geometry)));
      tweenStates(map, "fn-tracts", tids, last.current.t, tto, torder, raf.current.t);
      last.current.t = tto;
    }
  }, [on, i, tkey, f.reform]);

  useEffect(() => () => {
    cancelAnimationFrame(raf.current.c.current);
    cancelAnimationFrame(raf.current.t.current);
  }, []);

  if (!on || !hot || view3d.value) return null;
  return html`<${TractFunnelCard} reply=${reply.data} reform=${f.reform} hot=${hot} step=${i} />`;
}

/** The tract hover card on the funnel map: the tract and a small bar for each stage. */
function TractFunnelCard({ reply, reform, hot, step }) {
  const steps = stagesOf(reply, reform);
  const k = (reply.geoids || []).indexOf(hot.id);
  if (k < 0) return null;
  const maxes = maxByUnit(steps, "tracts", [k]);
  const flip = hot.x > window.innerWidth - 300;
  const style = `transform: translate3d(${Math.round(flip ? hot.x - 268 : hot.x + 16)}px, ${Math.round(hot.y + 16)}px, 0)`;
  return html`<div class="col-card is-on funnel-card" aria-hidden="true" style=${style}>
    <strong class="map-hover-name">Tract ${hot.id}</strong>
    <ol class="mini-funnel">
      ${steps.map((s, j) => {
        const v = s.tracts.p50[k];
        const w = maxes[s.unit] ? (100 * (v || 0)) / maxes[s.unit] : 0;
        return html`<li class=${j === step ? "is-on" : ""}>
          <span class="mini-funnel-label">${s.words}</span>
          <span class="mini-funnel-bar"><span style=${`width: ${Math.max(1, w).toFixed(1)}%`}></span></span>
          <span class="mini-funnel-value">${plain(v)} ${unitWord(s.unit)}</span>
        </li>`;
      })}
    </ol>
    <span class="map-hover-hint">Middle estimates. Bars compare stages of the same unit only.</span>
  </div>`;
}

/** Point at a row of the Reform screen's funnel card: that stage shows on the map. */
export function previewStage(reform, index) {
  const now = mapFunnel.peek();
  if (now.reform !== reform) return;
  mapFunnel.value = { ...now, preview: index };
}

/** Whether a stage is being previewed (the funnel card dims its other rows to 0.45). */
export function useFunnelPreview(reform) {
  const f = mapFunnel.value;
  return f.reform === reform ? f.preview : -1;
}

